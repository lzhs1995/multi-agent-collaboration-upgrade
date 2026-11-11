"""S2 binding-dependent communication controls. All clients and sends are fixtures.

These are offline wiring/refusal tests, never efficiency or live receipt evidence.
The unchanged v3 communication tests run separately against the same candidate.
"""
import collections
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from orchestration_binding import BINDING_FIELDS, SCHEMA as BINDING_SCHEMA
from orchestration_common import ContractError, file_ref, write_json
from orchestration_freeze_v3 import registered_corpus_plan
import test_upgrade_communication_plan as fixtures
from test_upgrade_communication_attribution import OriginalFixture
from test_upgrade_jev_benchmark import PLAN, factors, OPPS
import upgrade_communication_attribution as attribution
import upgrade_communication_observer as observer
import upgrade_communication_plan as communication
import upgrade_jev_benchmark as jev_benchmark
import upgrade_supervisor_instructions as instructions


@unittest.skipUnless(PLAN.exists(), "frozen S2 plan is not available")
class BoundCommunicationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CommunicationPlanTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        groups, pairs = jev_benchmark.layout(self.f.root / "s2", file_ref(PLAN))
        self.f.registration_ref = jev_benchmark.register(self.f.root / "s2", "offline-bound-comm", PLAN,
            factors=factors(pairs), opportunities=OPPS, compact=False)
        self.f.registration = jev_benchmark.validate_registration(self.f.registration_ref)
        self.campaign = self.f.make_campaign("fan_in-on")
        self.campaign["lease_root"] = str(self.f.root / "lease-registry")
        self.root = Path(self.campaign["root"])
        self.plan = registered_corpus_plan(self.f.root / "corpus", "offline-bound-comm", "fan_in",
                                           self.f.registration)
        self.campaign["plan"] = write_json(self.root / "plan.json", self.plan)
        workspace = str(uuid.uuid4())
        for index, session in enumerate(self.campaign["sessions"].values()):
            session.update(workspace_uuid=workspace, pane_uuid=str(uuid.uuid4()),
                process=dict(pid=100000 + index, birth=[1, index]), tty=f"ttys-fixture-{index}")
        self.campaign_ref = write_json(self.root / "campaign.json", self.campaign)
        self.samples = communication.campaign_plan(self.campaign)

    def sample(self, node="inventory", stage="after_dispatch"):
        return next(s for s in self.samples if s["run_id"] == "warm-01"
                    and s["node_id"] == node and s["stage"] == stage)

    def claim(self, lane="claude_b", node="inventory", **binding_changes):
        root = self.root / "runs/warm-01"
        spec = next(run for run in self.campaign["runs"] if run["run_id"] == "warm-01")
        write_json(root / "run.json", dict(campaign=self.campaign_ref, run=spec))
        binding = {key: None for key in BINDING_FIELDS}
        binding.update(schema=BINDING_SCHEMA, campaign=self.campaign_ref, run_id="warm-01", node_id=node,
            lane=lane, execution_kind="local_cli" if lane == "codex" else "delegated_cli",
            transport="codex_cli" if lane == "codex" else "claude_code_cli", arm="B",
            supervisor_session=self.campaign["sessions"]["codex"]["session_id"])
        binding.update(binding_changes)
        binding_pin = write_json(root / "bindings" / (node + ".json"), binding)
        task_id = f"{self.plan['plan_id']}--warm-01--{node}--a0001"
        claim = dict(task_id=task_id, executor_binding=binding_pin)
        claim_pin = write_json(root / "claims" / (node + ".json"), claim)
        write_json(Path(self.campaign["lease_root"]) / "leases.json", {task_id: dict(
            session_id=self.campaign["sessions"][lane]["session_id"], claim=claim_pin,
            released_at=None, released_by=None)})
        return binding_pin, claim_pin, task_id

    def test_denominator_freezes_roles_and_balances_advisory_not_native_lanes(self):
        plan = communication.registration_plan(self.f.registration)
        self.assertEqual(plan["schema"], communication.S2_PLAN)
        self.assertEqual(len(plan["samples"]), 192)
        self.assertEqual(len({s["request_id"] for s in plan["samples"]}), 192)
        self.assertEqual(collections.Counter(s["advisory"] for s in plan["samples"]),
                         {"advisory-off": 96, "advisory-on": 96})
        self.assertEqual(collections.Counter(s["sender"] for s in plan["samples"]),
                         {"codex": 96, communication.EXECUTOR_ROLE: 96})
        self.assertTrue(all(s["body"] is None and s["binding_policy"] == communication.CLAIMED_PEER
                            for s in plan["samples"]))
        shape = lambda arm: collections.Counter((s["workload_class"], s["run_id"], s["node_id"],
            s["sender"], s["receiver"], s["stage"]) for s in plan["samples"] if s["advisory"] == arm)
        self.assertEqual(shape("advisory-off"), shape("advisory-on"))

    def test_bootstrap_before_claim_has_only_pinned_dynamic_commands(self):
        self.assertEqual(len(self.samples), 32)
        for sample in self.samples:
            argv = shlex.split(sample["command"])
            self.assertEqual(argv[:4], ["rtk", "proxy", self.campaign["python"]["path"], "-BE"])
            self.assertEqual(argv[5:], ["send-sample", "--campaign", self.campaign_ref["path"],
                "--campaign-sha256", self.campaign_ref["sha256"], "--request-id", sample["request_id"]])
            self.assertEqual(sample["resolution"], "AWAITING_ORIGINAL_CLAIM")
            self.assertIsNone(sample["body"])
        with patch.object(instructions, "validate_campaign", return_value=self.campaign):
            text = instructions.render(self.campaign_ref)
        self.assertEqual(text.count(" --request-id "), 16)
        self.assertLess(len(text), 24000)
        self.assertIn("S2 freezes roles, not Claude A/B", text)
        scope = communication.executor_messages(self.campaign, "warm-01", "inventory")
        self.assertEqual(scope.count(" --request-id "), 2)

    def test_all_original_192_slot_ids_and_order_are_preserved(self):
        # Run the unchanged pre-binding branch on the same registered layout;
        # only the S2 roles/body resolution may differ, never the opaque IDs.
        registration = self.f.registration
        legacy = dict(registration, schema="upgrade-paired-registration-v1")
        from upgrade_benchmark import registered_layout
        original_layout = registered_layout(registration)
        with patch("upgrade_benchmark.registered_layout", return_value=original_layout), \
             patch("upgrade_benchmark.pilot_group", return_value="pilot-on"):
            original = communication.registration_plan(legacy)
        current = communication.registration_plan(registration)
        self.assertEqual([s["request_id"] for s in current["samples"]],
                         [s["request_id"] for s in original["samples"]])

    def test_same_slot_resolves_either_real_claimed_claude_in_both_directions(self):
        for lane in ("claude_a", "claude_b"):
            with self.subTest(lane=lane):
                binding, claim, _ = self.claim(lane)
                for stage in ("after_dispatch", "before_accept", "before_business", "after_business"):
                    sample = self.sample(stage=stage)
                    value = communication.resolve_sample(self.campaign, sample)
                    outgoing = stage in ("after_dispatch", "before_accept")
                    self.assertEqual((value["sender"], value["receiver"]),
                                     ("codex", lane) if outgoing else (lane, "codex"))
                    self.assertEqual(value["request_id"], sample["request_id"])
                    self.assertEqual(value["command"], sample["command"])
                    self.assertEqual(value["binding_resolution_pins"]["claim"], claim)
                    self.assertIn("binding=" + binding["sha256"], value["body"])
                    self.assertLess(len(value["body"]) + 160, 700)
                    argv = shlex.split(value["helper_command"])
                    self.assertEqual(argv[:4], ["rtk", "proxy", str(self.f.helper), "send"])
                    self.assertEqual(argv[4], self.campaign["sessions"][value["receiver"]]["surface_uuid"])
                    self.assertEqual(argv[5:], ["--request-id", sample["request_id"], value["body"]])

    def test_binding_alone_never_resolves_an_unreached_claim(self):
        binding, claim, _ = self.claim()
        Path(claim["path"]).unlink()
        self.assertTrue(Path(binding["path"]).exists())
        with self.assertRaises((OSError, ContractError)):
            communication.resolve_sample(self.campaign, self.sample())

    def test_changed_binding_or_claim_is_refused_not_rerouted(self):
        for target in ("binding", "claim"):
            with self.subTest(target=target):
                binding, claim, _ = self.claim()
                ref = binding if target == "binding" else claim
                path = Path(ref["path"])
                value = json.loads(path.read_text())
                value["lane" if target == "binding" else "task_id"] = "changed"
                path.write_text(json.dumps(value))
                with self.assertRaises(ContractError):
                    communication.resolve_sample(self.campaign, self.sample())

    def test_wrong_binding_run_campaign_node_or_supervisor_refuses(self):
        for field, value in (("run_id", "warm-02"), ("campaign", dict(self.campaign_ref, sha256="0" * 64)),
                             ("node_id", "verify_inputs"), ("supervisor_session", "another-supervisor")):
            with self.subTest(field=field):
                self.claim(**{field: value})
                with self.assertRaises(ContractError):
                    communication.resolve_sample(self.campaign, self.sample())

    def test_released_original_lease_keeps_same_peer_but_wrong_session_refuses(self):
        _, _, task_id = self.claim()
        path = Path(self.campaign["lease_root"]) / "leases.json"
        before = communication.resolve_sample(self.campaign, self.sample())
        leases = json.loads(path.read_text())
        leases[task_id].update(released_at="fixture-later", released_by={"fixture": True})
        write_json(path, leases)
        self.assertEqual(communication.resolve_sample(self.campaign, self.sample()), before)
        leases[task_id]["session_id"] = "different-original-session"
        write_json(path, leases)
        with self.assertRaises(ContractError):
            communication.resolve_sample(self.campaign, self.sample())

    def test_codex_peer_never_generates_a_self_send(self):
        self.claim("codex")
        with self.assertRaisesRegex(ContractError, "never send to self"):
            communication.resolve_sample(self.campaign, self.sample())

    def test_send_invokes_only_one_original_helper_and_keeps_exit_75(self):
        self.claim()
        sample = self.sample()
        resolved = communication.resolve_sample(self.campaign, sample)
        with patch.object(communication, "_require_original_sender") as guard, \
             patch.object(communication.subprocess, "run", return_value=SimpleNamespace(returncode=75)) as send:
            self.assertEqual(communication.send_sample(self.campaign, sample["request_id"]), 75)
        guard.assert_called_once_with(self.campaign, resolved)
        send.assert_called_once_with(shlex.split(resolved["helper_command"]), check=False)

    def test_pre_send_refusal_has_zero_helper_calls_and_no_retry(self):
        self.claim()
        for request_id in (self.sample()["request_id"], "unregistered-slot"):
            with self.subTest(request=request_id), redirect_stderr(io.StringIO()) as output, \
                 patch.object(communication, "_require_original_sender", side_effect=ContractError("wrong sender")), \
                 patch.object(communication.subprocess, "run") as send:
                self.assertEqual(communication.send_sample(self.campaign, request_id), 75)
                send.assert_not_called()
                self.assertEqual(json.loads(output.getvalue())["status"], "REFUSED_NO_HELPER_CALL")

    def test_exact_cli_campaign_and_adapter_pins_reach_one_original_helper(self):
        self.campaign["adapter"]["path"] = str(Path(communication.__file__).resolve().parents[1])
        self.campaign_ref = write_json(self.root / "campaign.json", self.campaign)
        self.samples = communication.campaign_plan(self.campaign)
        self.claim()
        sample = self.sample()
        argv = shlex.split(sample["command"])[5:]
        with patch("orchestration_campaign.validate_campaign", return_value=self.campaign) as validate, \
             patch.object(communication, "_require_original_sender"), \
             patch.object(communication.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as send:
            self.assertEqual(communication.main(argv), 0)
        validate.assert_called_once_with(self.campaign_ref)
        send.assert_called_once_with(shlex.split(communication.resolve_sample(self.campaign, sample)["helper_command"]),
                                     check=False)
        bad = list(argv)
        bad[bad.index("--campaign-sha256") + 1] = "0" * 64
        with redirect_stderr(io.StringIO()), patch.object(communication.subprocess, "run") as send:
            self.assertEqual(communication.main(bad), 75)
            send.assert_not_called()

    def test_helper_exception_is_not_mislabeled_as_a_no_input_refusal(self):
        self.claim()
        with patch.object(communication, "_require_original_sender"), \
             patch.object(communication.subprocess, "run", side_effect=OSError("original process failed")), \
             redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(OSError):
                communication.send_sample(self.campaign, self.sample()["request_id"])
            self.assertEqual(output.getvalue(), "")

    def test_original_sender_guard_uses_native_session_process_and_foreground(self):
        self.claim()
        sample = communication.resolve_sample(self.campaign, self.sample())
        session = self.campaign["sessions"][sample["sender"]]
        public = dict(session=session["session_id"], tty=session["tty"],
                      foreground=dict(thread_id=session["session_id"]))
        caller = {key: session[field].upper() for key, field in
                  (("workspace_id", "workspace_uuid"), ("surface_id", "surface_uuid"), ("pane_id", "pane_uuid"))}
        native = SimpleNamespace(_public_process=lambda proof: session["process"])
        guard = Mock()
        daemon = SimpleNamespace(collect=lambda env: {"client": {"fixture": True}})
        for changed in (public, dict(public, session="wrong-session"), dict(public, foreground={"thread_id": "wrong"})):
            guard.caller_snapshot.return_value = (dict(caller=caller), None, None, changed)
            with patch("orchestration_campaign.controller_modules", return_value=(None, native, guard, daemon)):
                if changed == public:
                    communication._require_original_sender(self.campaign, sample)
                else:
                    with self.assertRaises(ContractError):
                        communication._require_original_sender(self.campaign, sample)

    def test_observer_resolves_changed_peer_and_retains_unreached_slots(self):
        self.claim("claude_b")
        info = dict(campaign=self.campaign, state_root=str(self.f.root / "empty-native-state"))
        row = observer.observe_sample(observer.Observer(), self.sample(), info)
        self.assertEqual(row["receiver"], "claude_b")
        self.assertEqual(row["attempt"], "MISSING")
        missing = observer.observe_sample(observer.Observer(), self.sample(node="verify_inputs"), info)
        self.assertEqual(missing["attempt"], "UNKNOWN")
        self.assertEqual(missing["request_id"], self.sample(node="verify_inputs")["request_id"])
        self.assertTrue(missing["reasons"][0].startswith("CLAIMED_PEER_UNRESOLVED"))

    def test_full_observation_retains_all_192_and_separate_advisory_counts(self):
        plan_pin = write_json(self.f.root / "communication-plan.json",
                             communication.registration_plan(self.f.registration))
        candidate = write_json(self.f.root / "candidate.json", dict(root=self.f.registration["root"],
            communication_plan=plan_pin, registration=self.f.registration_ref, groups={}))
        with patch.object(observer, "prepare_group", return_value={}), \
             patch.object(communication.subprocess, "run", side_effect=AssertionError("file-only observer")):
            result = observer.collect(candidate["path"])
        self.assertEqual(result["total_samples"], len(result["samples"]))
        self.assertEqual(result["by_advisory"]["advisory-off"]["total"], 96)
        self.assertEqual(result["by_advisory"]["advisory-on"]["total"], 96)
        self.assertEqual(result["counts"]["native_reception"], {"UNKNOWN": 192})
        self.assertEqual(result["input_operations"], 0)

    def test_attribution_resolves_role_before_join_and_keeps_failure_unknown(self):
        path = self.f.root / "attribution-fixture"
        path.mkdir()
        f = OriginalFixture(path, "claude")
        f.sender_append(f.call())
        f.append(f.inbound())
        role = dict(f.sample, receiver=communication.EXECUTOR_ROLE, body=None,
                    binding_policy=communication.CLAIMED_PEER)
        with patch.object(attribution, "campaign_plan", return_value=[role]), \
             patch.object(attribution, "state_root", return_value=f.state), \
             patch.object(attribution, "resolve_sample", return_value=f.sample) as resolve:
            result = attribution.attribute_inputs(f.campaign, f.header, f.ends(), f.readers, not_after=f.stamp(5))
        resolve.assert_called_once_with(f.campaign, role)
        self.assertEqual(len(result["inputs"]), 1, result)
        self.assertEqual(result["unresolved"], [])
        with patch.object(attribution, "campaign_plan", return_value=[role]), \
             patch.object(attribution, "state_root", return_value=f.state), \
             patch.object(attribution, "resolve_sample", side_effect=ContractError("claim changed")):
            failed = attribution.attribute_inputs(f.campaign, f.header, f.ends(), f.readers, not_after=f.stamp(5))
        self.assertEqual(failed["inputs"], [])
        self.assertEqual(failed["unresolved"], [dict(request_id=role["request_id"], reason="ContractError")])


if __name__ == "__main__":
    unittest.main()
