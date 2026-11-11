"""Offline 1+2 campaigns: distinct lanes, original evidence and complete cleanup.

These synthetic clients cannot qualify for live adoption or paired speed claims.
"""
import copy
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from orchestration_campaign import METRICS, validate_campaign
from orchestration_common import ContractError, file_ref, json_ref, parse_time, read_json, read_ref, utc_now, write_json
from orchestration_coordinator import State
from orchestration_evidence import verify_dispatched, verify_prepared, verify_protocol
from orchestration_native import verify_delivery
from orchestration_preparation import collect_preparation_exit, verify_preparation_exit
from orchestration_test_support import CampaignFixture
from orchestration_transport import Transport
from orchestration_verify import derive_run, finish_run, verify_campaign, verify_run
from test_orchestration_transport import FakeRuntime


class ThreeLaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, version=3)

    def only(self, *keys):
        self.f.plan["nodes"] = [n for n in self.f.plan["nodes"] if n["id"] in keys]
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()

    def local(self, co, key="local"):
        info = co.claim(key)
        self.f.work(info)
        return self.f.accept(co, key, self.f.candidate(co, key))

    def begin(self, co, key):
        info = co.claim(key)
        self.f.prepare(co, key)
        self.f.intent(co, key)
        self.f.dispatch(co, key)
        return info

    def complete(self, co, key, info, *, disarm=True):
        self.f.work(info, co.nodes[key]["executor"])
        protocol = self.f.callback(co, key)
        result = self.f.accept(co, key, self.f.candidate(co, key, protocol))
        if disarm:
            self.f.disarm(co, key)
        return result

    def test_uuid_only_sessions_prepare_through_original_gate_and_close_both_lanes(self):
        for session in self.f.campaign["sessions"].values():
            self.assertNotIn("surface_ref", session)
            self.assertNotIn("pane_ref", session)
        co = self.f.start()
        for key in ("review", "parallel"):
            info = co.claim(key)
            runtime = FakeRuntime(self.f, co, node_id=key)
            transport = Transport(co, key, runtime=runtime)
            with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root), \
                 patch("orchestration_transport.dispatch_journal", return_value=runtime.dispatch_root):
                proof = transport.prepare()
                pack, gate = json_ref(proof["pack"]), json_ref(proof["identity_gate"])
                self.assertEqual(pack["supervisor"], gate["supervisor"])
                self.assertEqual(pack["callback_target"], gate["supervisor"])
                self.assertNotEqual(pack["callback_target"], self.f.clients["codex"].session["surface_uuid"])
                transport.dispatch()
                self.assertEqual(co.state()[0][key], State.DISPATCHED)
            self.complete(co, key, info)
        self.assertEqual(co.state()[0]["review"], State.ACCEPTED)
        self.assertEqual(co.state()[0]["parallel"], State.ACCEPTED)

    def test_original_gate_must_bind_ref_uuid_workspace_panes_roles_and_sessions(self):
        co = self.f.start()
        co.claim("review")
        proof = self.f.prepare(co, "review")
        info = co.state()[1]["review"]
        original = json_ref(proof["identity_gate"])
        selector = self.f.clients["claude_a"].session["surface_uuid"]
        wrong_uuid = str(uuid.uuid4())
        mutations = [
            ("supervisor_ref", lambda gate: gate.update(supervisor="surface:999")),
            ("malformed_ref", lambda gate: gate.update(supervisor=wrong_uuid)),
            ("supervisor_provider", lambda gate: gate.update(supervisor_provider="claude")),
            ("supervisor_uuid", lambda gate: gate.update(supervisor_surface_uuid=wrong_uuid)),
            ("workspace", lambda gate: gate.update(workspace_uuid=wrong_uuid)),
            ("caller_ref", lambda gate: gate["workspace_proofs"][selector].update(caller_surface_ref="surface:999")),
            ("target_ref_format", lambda gate: gate["workspace_proofs"][selector].update(target_surface_ref=wrong_uuid)),
            ("missing_proof", lambda gate: gate.update(workspace_proofs={})),
            ("extra_proof", lambda gate: gate["workspace_proofs"].update({wrong_uuid: copy.deepcopy(gate["workspace_proofs"][selector])})),
            ("wrong_proof_selector", lambda gate: gate.update(workspace_proofs={wrong_uuid: gate["workspace_proofs"][selector]})),
            ("role", lambda gate: gate["native_participants"][0].update(role="executor")),
            ("native_session", lambda gate: gate["native_participants"][1].update(native_session_id=wrong_uuid)),
            ("duplicate_participant", lambda gate: gate["native_participants"].append(copy.deepcopy(gate["native_participants"][0]))),
        ]
        for field in ("caller_surface_uuid", "target_surface_uuid", "workspace_uuid", "caller_pane_uuid", "target_pane_uuid"):
            mutations.append((field, lambda gate, field=field: gate["workspace_proofs"][selector].update({field: wrong_uuid})))
        for label, mutate in mutations:
            with self.subTest(mutation=label):
                gate = copy.deepcopy(original)
                mutate(gate)
                changed = dict(proof, identity_gate=write_json(proof["identity_gate"]["path"], gate))
                with self.assertRaises(ContractError):
                    verify_prepared(self.f.campaign, co.header, co.nodes["review"], info, changed, fixtures=True)
        write_json(proof["identity_gate"]["path"], original)

    def test_v3_callback_must_retain_the_original_pack_ref(self):
        co = self.f.start()
        info = self.begin(co, "review")
        self.f.work(info, "claude_a")
        protocol = self.f.callback(co, "review")
        pack = json_ref(protocol["prepared"]["pack"])
        evidence = dict(protocol=protocol, report=file_ref(pack["report"]))
        current = co.state()[1]["review"]
        ends = {lane: client.reader.snapshot()["offset"] for lane, client in self.f.clients.items()}
        verify_protocol(self.f.campaign, co.header, co.nodes["review"], current, evidence,
                        ends["claude_a"], ends["codex"], fixtures=True)
        callback = protocol["callback"]
        refs = [callback["receipt"], *callback["attempts"]]
        originals = {ref["path"]: json_ref(ref) for ref in refs}
        for target in ("surface:999", self.f.clients["codex"].session["surface_uuid"]):
            with self.subTest(target=target):
                replacements = {}
                for ref in refs:
                    value = copy.deepcopy(originals[ref["path"]])
                    if "binding" in value:
                        value["binding"]["callback_target"] = target
                    replacements[ref["path"]] = write_json(ref["path"], value)
                changed = dict(callback, receipt=replacements[callback["receipt"]["path"]],
                               attempts=[replacements[ref["path"]] for ref in callback["attempts"]])
                bad = dict(evidence, protocol=dict(protocol, callback=changed))
                with self.assertRaisesRegex(ContractError, "callback pins a different report/pack/nonce"):
                    verify_protocol(self.f.campaign, co.header, co.nodes["review"], current, bad,
                                    ends["claude_a"], ends["codex"], fixtures=True)
        for path, value in originals.items():
            write_json(path, value)

    def test_both_claudes_close_with_successor_while_other_lane_is_active(self):
        co = self.f.start()
        a, b = self.begin(co, "review"), self.begin(co, "parallel")
        self.local(co)
        self.complete(co, "review", a)
        self.assertEqual(co.state()[0]["parallel"], State.DISPATCHED)
        self.assertIn("followup", [n["id"] for n in co.ready()])
        followup = self.begin(co, "followup")
        self.complete(co, "followup", followup)
        self.complete(co, "parallel", b)
        self.local(co, "closeout")
        result = finish_run(co)
        self.assertEqual((result["status"], result["nodes_accepted"]), ("PASS", 5))
        self.assertEqual(len(result["disarms"]), 3)
        self.assertEqual(result["metrics"]["human_interventions"], 0)
        self.assertEqual(result["metrics"]["transport_retries"], 0)
        last = co.events()[-1]
        self.assertGreater(parse_time(result["closeout_clock"]["at"]), parse_time(last["at"]))
        self.assertEqual(result["metrics"]["elapsed_wall_seconds"],
            (parse_time(result["closeout_clock"]["at"]) - parse_time(co.header["started_at"])).total_seconds())
        verification = verify_campaign(self.f.ref, fixtures=True)
        self.assertFalse(verification["promotion"]["promoted"])
        self.assertEqual(verification["promotion"]["reason"], "separate_registered_paired_experiment_required")

    def test_wrong_lane_cannot_supply_exact_dispatch_or_callback(self):
        co = self.f.start()
        info = co.claim("review")
        proof = self.f.prepare(co, "review")
        self.f.intent(co, "review")
        info = co.state()[1]["review"]
        wrong = self.f.clients["claude_b"].delivery(read_ref(proof["notice"]).decode(),
            sender=self.f.clients["codex"].session, journal=co.root / "wrong-dispatch", at=utc_now())
        with self.assertRaises(ContractError):
            verify_dispatched(self.f.campaign, co.header, co.nodes["review"], info, wrong, fixtures=True)
        self.assertEqual(co.state()[0]["review"], State.DISPATCH_INTENT)
        pack = json_ref(proof["pack"])
        callback = self.f.clients["codex"].delivery(pack["completion_callback"],
            sender=self.f.clients["claude_b"].session, journal=co.root / "wrong-callback", at=utc_now())
        with self.assertRaises(ContractError):
            verify_delivery(callback, self.f.clients["codex"].session, self.f.clients["claude_a"].session,
                            pack["completion_callback"], fixtures=True)
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_cross_arm_runtime_and_lease_cannot_be_relabelled(self):
        original = copy.deepcopy(self.f.campaign)
        for changed, reason in (({"arm": "A1"}, "arm differs"),
                ({"lease_root": str(self.f.root / "other-leases")}, "private registry"),
                ({"metrics": METRICS}, "measurement definitions")):
            with self.subTest(changed=changed):
                self.f.campaign = dict(original, **changed)
                self.f.save()
                with self.assertRaisesRegex(ContractError, reason):
                    validate_campaign(self.f.ref, fixtures=True)

    def test_same_provider_does_not_allow_duplicate_session_or_pane(self):
        original = copy.deepcopy(self.f.campaign)
        for field in ("session_id", "surface_uuid", "pane_uuid"):
            with self.subTest(field=field):
                self.f.campaign = copy.deepcopy(original)
                self.f.campaign["sessions"]["claude_b"][field] = self.f.campaign["sessions"]["claude_a"][field]
                self.f.save()
                with self.assertRaisesRegex(ContractError, "distinct"):
                    validate_campaign(self.f.ref, fixtures=True)

    def test_conflicting_writes_retain_one_writer_and_active_lease(self):
        self.f.plan["nodes"][2]["write_scope"] = "review/nested"
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()
        co = self.f.start()
        info = co.claim("review")
        self.assertNotIn("parallel", [n["id"] for n in co.ready()])
        with self.assertRaisesRegex(ContractError, "not ready"):
            co.claim("parallel")
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])
        with self.assertRaisesRegex(ContractError, "active/uncertain"):
            finish_run(co)

    def test_complete_closeout_cannot_be_shortened_to_last_acceptance(self):
        self.only("review")
        co = self.f.start()
        self.complete(co, "review", self.begin(co, "review"))
        result = finish_run(co)
        last = co.events()[-1]
        end = {k: c.reader.snapshot() for k, c in self.f.clients.items()}
        early = {k: last[k] for k in ("at", "monotonic_ns", "boot_id")}
        with self.assertRaisesRegex(ContractError, "complete lease/disarm cleanup"):
            derive_run(co, end, closeout_clock=early)
        value = read_json(co.root / "finish.json")
        value.update(closeout_clock=early, closed_at=early["at"])
        write_json(co.root / "finish.json", value)
        with self.assertRaisesRegex(ContractError, "complete lease/disarm cleanup"):
            verify_run(self.f.ref, co.spec["run_id"], fixtures=True)
        self.assertGreater(result["metrics"]["elapsed_wall_seconds"],
                           (parse_time(last["at"]) - parse_time(co.header["started_at"])).total_seconds())

    def test_two_lane_transport_preserves_unknown_input_without_replay(self):
        co = self.f.start()
        co.claim("parallel")
        runtime = FakeRuntime(self.f, co, node_id="parallel")
        transport = Transport(co, "parallel", runtime=runtime)
        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root), \
             patch("orchestration_transport.dispatch_journal", return_value=runtime.dispatch_root):
            transport.prepare()
            runtime.fail_submit = True
            self.assertEqual(transport.dispatch()["status"], "UNCERTAIN")
            before = file_ref(transport.root / "dispatch-intent.json")
            self.assertEqual(transport.dispatch()["status"], "UNCERTAIN")
            self.assertEqual(file_ref(transport.root / "dispatch-intent.json"), before)
            self.assertEqual(runtime.calls.count(("submit", False)), 1)
        self.assertEqual(co.state()[0]["parallel"], State.UNCERTAIN)

    def test_other_lane_progress_does_not_invalidate_original_preparation_exit(self):
        self.only("review", "parallel")
        co = self.f.start()
        b = self.begin(co, "parallel")
        co.claim("review")
        runtime = FakeRuntime(self.f, co)
        transport = Transport(co, "review", runtime=runtime)
        transition = co.transition

        def fail_at_final_gate(key, state, data):
            if key == "review" and state == State.PREPARED:
                raise ContractError("fixture final gate rejected")
            return transition(key, state, data)

        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root), \
             patch.object(co, "transition", side_effect=fail_at_final_gate):
            with self.assertRaisesRegex(ContractError, "final gate rejected"):
                transport.prepare()
        self.f.block(co, "review")
        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root):
            proof = collect_preparation_exit(transport)
        info = co.state()[1]["review"]
        self.complete(co, "parallel", b)
        # No new claim follows a run failure. Already started work may finish.
        self.assertEqual(co.ready(), [])
        ends = {k: c.reader.snapshot() for k, c in self.f.clients.items()}
        result = verify_preparation_exit(self.f.campaign, co.header, co.nodes["review"], info, proof,
                                          fixtures=True, native_end=ends)
        self.assertEqual(result["observed_at"], proof["observed_at"])
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])
        self.assertEqual(co.state()[0]["parallel"], State.ACCEPTED)


if __name__ == "__main__":
    unittest.main()
