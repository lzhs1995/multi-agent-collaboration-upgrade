"""Missing preparation files and misleading UI rows must fail before dispatch.

All clients here are offline fixtures; these tests do not measure model speed.
"""
import copy
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
from orchestration_evidence import task_notice, verify_prepared
from orchestration_pack_inputs import verify_inputs
from orchestration_test_support import CampaignFixture
from orchestration_transport import PendingOperation, Transport
from test_orchestration_transport import Crash, FakeRuntime


class PreparationInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, version=3)
        self.co = self.f.start()
        self.info = self.co.claim("review")
        self.node = self.co.nodes["review"]

    def check(self, proof):
        return verify_prepared(self.f.campaign, self.co.header, self.node, self.info, proof, fixtures=True)

    def repack(self, proof, pack):
        changed = dict(proof, pack=write_json(proof["pack"]["path"], pack))
        Path(proof["notice"]["path"]).write_text(task_notice(changed["pack"], pack))
        changed["notice"] = file_ref(proof["notice"]["path"])
        return changed

    def test_missing_changed_and_relocated_files_are_rejected_but_future_outputs_may_be_absent(self):
        proof = self.f.prepare(self.co, "review")
        pack = json_ref(proof["pack"])
        self.check(proof)
        self.assertFalse(Path(pack["report"]).exists())
        self.assertFalse(Path(pack["completion_receipt"]).exists())
        for key in ("role_map", "pane_inventory"):
            path = Path(pack[key])
            original = path.read_bytes()
            for payload in (None, original + b" "):
                with self.subTest(key=key, payload="missing" if payload is None else "changed"):
                    if payload is None:
                        path.unlink()
                    else:
                        path.write_bytes(payload)
                    with self.assertRaises(ContractError):
                        self.check(proof)
                    path.write_bytes(original)
            other = path.with_name("copied-" + path.name)
            other.write_bytes(original)
            bad = copy.deepcopy(pack)
            bad[key] = str(other)
            bad["preparation_inputs"][key] = file_ref(other)
            with self.assertRaisesRegex(ContractError, "input paths changed"):
                self.check(self.repack(proof, bad))
            self.repack(proof, pack)

    def test_rehashed_false_task_role_workspace_or_inventory_is_rejected(self):
        proof = self.f.prepare(self.co, "review")
        pack = json_ref(proof["pack"])
        wrong = str(uuid.uuid4())
        cases = [
            ("role_map", "task", lambda v: v.update(task_id="other-task")),
            ("role_map", "workspace", lambda v: v.update(workspace_ref="workspace:999")),
            ("role_map", "other_lane", lambda v: v["executor"].update(surface_ref=self.f.clients["claude_b"].session["surface_uuid"])),
            ("role_map", "role", lambda v: v["supervisor"].update(provider="claude")),
            ("role_map", "extra_role", lambda v: v.update(executor2=copy.deepcopy(v["executor"]))),
            ("role_map", "stale", lambda v: v.update(updated_at="2000-01-01T00:00:00Z")),
            ("pane_inventory", "task", lambda v: v.update(task_id="other-task")),
            ("pane_inventory", "workspace", lambda v: v.update(workspace_ref="workspace:999")),
            ("pane_inventory", "missing_receiver", lambda v: v["surfaces_same_workspace"].pop(1)),
            ("pane_inventory", "duplicate_ref", lambda v: v["surfaces_same_workspace"][1].update(ref=v["surfaces_same_workspace"][0]["ref"])),
            ("pane_inventory", "duplicate_uuid", lambda v: v["surfaces_same_workspace"][1].update(surface_id=v["surfaces_same_workspace"][0]["surface_id"])),
            ("pane_inventory", "wrong_uuid", lambda v: v["surfaces_same_workspace"][1].update(surface_id=wrong)),
            ("pane_inventory", "wrong_ref", lambda v: v["surfaces_same_workspace"][1].update(ref="surface:999")),
            ("pane_inventory", "cross_workspace", lambda v: v["surfaces_same_workspace"][1].update(workspace_id=wrong)),
            ("pane_inventory", "pane", lambda v: v["surfaces_same_workspace"][1].update(pane_ref="pane:999")),
            ("pane_inventory", "not_terminal", lambda v: v["surfaces_same_workspace"][1].update(is_terminal=False)),
            ("pane_inventory", "browser", lambda v: v["surfaces_same_workspace"][1].update(surface_type="browser")),
        ]
        for key, label, mutate in cases:
            with self.subTest(key=key, mutation=label):
                original = json_ref(pack["preparation_inputs"][key])
                value = copy.deepcopy(original)
                mutate(value)
                bad = copy.deepcopy(pack)
                bad["preparation_inputs"][key] = write_json(pack[key], value)
                with self.assertRaises(ContractError):
                    self.check(self.repack(proof, bad))
                write_json(pack[key], original)
                self.repack(proof, pack)

    def test_title_provider_and_selected_tab_are_not_native_identity_authority(self):
        proof = self.f.prepare(self.co, "review")
        pack = json_ref(proof["pack"])
        inventory = json_ref(pack["preparation_inputs"]["pane_inventory"])
        for row in inventory["surfaces_same_workspace"]:
            row.update(provider="unknown", title="renamed terminal", selected=False)
        pack["preparation_inputs"]["pane_inventory"] = write_json(pack["pane_inventory"], inventory)
        self.check(self.repack(proof, pack))

    def test_fixture_cannot_be_used_as_live_startup_proof(self):
        proof = self.f.prepare(self.co, "review")
        pack, gate = json_ref(proof["pack"]), json_ref(proof["identity_gate"])
        with self.assertRaisesRegex(ContractError, "live preparation requires external adoption"):
            verify_inputs(self.f.campaign, self.co.header, self.node, self.info, pack, gate)
        for adoption in ({"verified": True}, {"runtime": self.f.campaign["runtime"], "proof": None, "executor_id": "claude_b"}):
            bad = copy.deepcopy(pack)
            bad["preparation_inputs"]["client_adoption"] = adoption
            with self.assertRaisesRegex(ContractError, "adoption differs"):
                self.check(self.repack(proof, bad))

    def test_gate_singular_and_executor_list_aliases_must_agree(self):
        proof = self.f.prepare(self.co, "review")
        original = json_ref(proof["identity_gate"])
        for field, value in (("pane_ref", "pane:999"), ("workspace_ref", "workspace:999"),
                             ("provider", "codex"), ("surface_ref", "surface:999")):
            gate = copy.deepcopy(original)
            gate["executors"][0][field] = value
            bad = dict(proof, identity_gate=write_json(proof["identity_gate"]["path"], gate))
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, "aliases disagree"):
                self.check(bad)
        write_json(proof["identity_gate"]["path"], original)

    def test_all_original_stages_run_once_and_both_inputs_are_pinned(self):
        runtime = FakeRuntime(self.f, self.co)
        transport = Transport(self.co, "review", runtime=runtime)
        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root):
            proof = transport.prepare()
            self.assertEqual(transport.prepare(), proof)
        self.assertEqual(runtime.calls, ["preflight", "map", "surface-inventory", "task-pack", "finalize-pack", "notice"])
        pack = json_ref(proof["pack"])
        for key in ("role_map", "pane_inventory"):
            self.assertEqual(pack["preparation_inputs"][key], file_ref(pack[key]))
            self.assertIn(pack[key], pack["source"])
        self.assertIsNone(pack["preparation_inputs"]["client_adoption"])

    def test_missing_inventory_stops_before_finalization_or_dispatch_and_does_not_replay(self):
        runtime = FakeRuntime(self.f, self.co)
        transport = Transport(self.co, "review", runtime=runtime)
        original = runtime.harness

        def missing(argv, root):
            result = original(argv, root)
            if argv[3] == "surface-inventory":
                (transport.root / "preflight/surface-inventory.json").unlink()
            return result

        with patch.object(runtime, "harness", side_effect=missing), \
             patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root):
            with self.assertRaises(PendingOperation):
                transport.prepare()
            calls = list(runtime.calls)
            with self.assertRaises(PendingOperation):
                transport.prepare()
        self.assertEqual(runtime.calls, calls)
        self.assertNotIn("finalize-pack", runtime.calls)
        self.assertNotIn(("submit", False), runtime.calls)
        self.assertFalse((transport.root / "dispatch-intent.json").exists())

    def test_crash_in_new_inventory_stage_preserves_original_intent_and_no_replay(self):
        runtime = FakeRuntime(self.f, self.co)
        transport = Transport(self.co, "review", runtime=runtime)
        original = runtime.harness

        def crash(argv, root):
            result = original(argv, root)
            if argv[3] == "surface-inventory":
                raise Crash()
            return result

        with patch.object(runtime, "harness", side_effect=crash):
            with self.assertRaises(Crash):
                transport.prepare()
            intent = file_ref(transport.root / "operations/surface-inventory/intent.json")
            with self.assertRaises(PendingOperation):
                transport.prepare()
        self.assertEqual(file_ref(intent["path"]), intent)
        self.assertEqual(runtime.calls, ["preflight", "map", "surface-inventory"])
        self.assertFalse((transport.root / "dispatch-intent.json").exists())


if __name__ == "__main__":
    unittest.main()
