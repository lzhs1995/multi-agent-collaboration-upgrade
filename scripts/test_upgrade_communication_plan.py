"""Offline instruction/denominator checks; these are not native delivery evidence."""
import copy
import shlex
import sys
import tempfile
import unittest
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from cmux_prompt_reference import helper_request
from orchestration_common import ContractError, file_ref, utc_now, write_json
from orchestration_evidence import task_scope
from render_cmux_agent import BASELINE_SHA256, render
from upgrade_benchmark import ARM_STRATEGIES, CONTRACT, layout
import upgrade_communication_plan as communication
import upgrade_supervisor_instructions as instructions


class CommunicationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        groups, pairs = layout(self.root)
        self.registration = dict(schema="upgrade-paired-registration-v1", benchmark_id="comm-test",
            root=str(self.root), created_at=utc_now(), contract=CONTRACT, groups=groups, pairs=pairs)
        self.registration_ref = write_json(self.root / "registration.json", self.registration)
        self.controller = self.root / "controller"
        (self.controller / "scripts").mkdir(parents=True)
        (self.controller / "tests/fixtures").mkdir(parents=True)
        self.adapter = self.controller / "scripts/cmux_agent_adapter.py"
        self.adapter.write_text("# Offline test route; never invoked.\n")
        baseline = Path(__file__).resolve().parents[1] / "tests/fixtures/cmux-agent-legacy.sh"
        self.original = baseline.read_bytes()
        (self.controller / "tests/fixtures/cmux-agent-legacy.sh").write_bytes(self.original)
        self.python = file_ref(sys.executable)
        self.helper = self.root / "bin/cmux-agent"
        self.helper.parent.mkdir()
        self.helper.write_bytes(render(self.original, expected_sha256=BASELINE_SHA256,
                                      python=self.python["path"], adapter=self.adapter))
        self.helper.chmod(0o755)
        self.controller_pin = {"path": str(self.controller)}
        self.startup_path = self.root / "startup.json"
        self.startup = dict(controller=str(self.controller), pins={"helper": file_ref(self.helper)})
        self.runtime_path = self.root / "runtime.json"
        self.runtime = dict(controller=self.controller_pin, python=self.python,
                            startup=write_json(self.startup_path, self.startup))
        self.runtime_ref = write_json(self.runtime_path, self.runtime)
        self.campaign = self.make_campaign("uneven_successor-A1")

    def make_campaign(self, group_id):
        group = self.registration["groups"][group_id]
        return dict(version=3, evidence_class="cli_campaign_v3", root=str(self.root / group_id),
            campaign_id=group["campaign_id"], session_group=group_id, arm=group["arm"],
            workload_class=group["workload_class"], registration=self.registration_ref,
            experiment={"path": group["context_path"]}, runtime=self.runtime_ref,
            runs=[dict(run_id=r["run_id"], strategy=ARM_STRATEGIES[group["arm"]]) for r in group["runs"]],
            controller=self.controller_pin, python=self.python, adapter={"path": str(self.root / "source")},
            workload={"path": str(self.root / "workload.py")},
            sessions={lane: dict(provider="codex" if lane == "codex" else "claude",
                                session_id=lane + "-original", surface_uuid=str(uuid.uuid4()))
                      for lane in ("codex", "claude_a", "claude_b")})

    def repin_helper(self):
        self.startup["pins"]["helper"] = file_ref(self.helper)
        self.runtime["startup"] = write_json(self.startup_path, self.startup)
        self.campaign["runtime"] = write_json(self.runtime_path, self.runtime)

    def test_exact_192_independent_messages_and_balanced_load(self):
        value = communication.registration_plan(self.registration)
        samples = value["samples"]
        self.assertEqual(value["total_samples"], 192)
        self.assertEqual(len(samples), 192)
        self.assertEqual(len({s["request_id"] for s in samples}), 192)
        self.assertEqual(Counter(s["arm"] for s in samples), {"A1": 96, "B": 96})
        self.assertEqual(set(Counter((s["group"], s["run_id"]) for s in samples).values()), {8})
        self.assertEqual(Counter(s["sender"] for s in samples), {"codex": 96, "claude_a": 48, "claude_b": 48})
        self.assertTrue(all(s["run_id"] in communication.WARM_RUNS for s in samples))
        self.assertNotIn("inventory_successor", {s["node_id"] for s in samples})
        shape = lambda arm: Counter((s["workload_class"], s["run_id"], s["node_id"],
                                     s["sender"], s["receiver"], s["stage"]) for s in samples if s["arm"] == arm)
        self.assertEqual(shape("A1"), shape("B"))

    def test_deterministic_ids_bound_to_registration_and_group(self):
        first = communication.registration_plan(self.registration)
        self.assertEqual(first, communication.registration_plan(copy.deepcopy(self.registration)))
        changed = copy.deepcopy(self.registration)
        changed["benchmark_id"] = "another-experiment"
        second = communication.registration_plan(changed)
        self.assertFalse({s["request_id"] for s in first["samples"]} & {s["request_id"] for s in second["samples"]})

    def test_all_commands_use_same_pinned_route_and_uuid_with_short_ascii_body(self):
        for group_id in self.registration["groups"]:
            campaign = self.make_campaign(group_id)
            values = communication.campaign_plan(campaign)
            self.assertEqual(len(values), 0 if group_id == "pilot-B" else 32)
            for sample in values:
                argv = shlex.split(sample["command"])
                self.assertEqual(argv[:4], ["rtk", "proxy", str(self.helper), "send"])
                self.assertEqual(argv[4], campaign["sessions"][sample["receiver"]]["surface_uuid"])
                self.assertEqual(argv[5:], ["--request-id", sample["request_id"], sample["body"]])
                self.assertTrue(sample["body"].isascii())
                self.assertLess(len(sample["body"]) + 160, 700)
                self.assertNotIn("TASK:", sample["body"])
                self.assertNotIn("\n", sample["command"])
                identity = dict(workspace_uuid=str(uuid.uuid4()), target_pane_uuid=str(uuid.uuid4()),
                    caller_surface_uuid=sample["sender_surface_uuid"], target_surface_uuid=sample["receiver_surface_uuid"])
                wire = helper_request(identity, "send", sample["body"], sample["request_id"], home=self.root)
                self.assertLessEqual(len(wire["payload"].encode("ascii")), 700)
                self.assertNotIn("\n", wire["payload"])
                self.assertEqual(wire["body_reference"]["format"], "cmux-message-reference-v2")

    def test_v2_cold_pilot_and_successor_do_not_require_or_add_helper(self):
        self.assertEqual(communication.campaign_plan({"version": 2}), [])
        self.assertEqual(communication.campaign_plan({"version": 3, "runs": [{"run_id": "cold-01"}]}), [])
        for version, run_id, node in ((2, "warm-01", "inventory"), (3, "cold-01", "inventory"),
                                      (3, "pilot-01", "inventory"), (3, "warm-01", "inventory_successor")):
            self.assertEqual(communication.executor_messages({"version": version,
                "workload_class": "uneven_successor"}, run_id, node), "")

    def test_helper_tampering_refused_even_after_repinning(self):
        with self.helper.open("ab") as stream:
            stream.write(b"\n# altered route\n")
        with self.assertRaises(ContractError):
            communication.campaign_plan(self.campaign)
        self.repin_helper()
        with self.assertRaisesRegex(ValueError, "HELPER_RENDERED_BYTES_CHANGED"):
            communication.campaign_plan(self.campaign)

    def test_foreign_release_route_refused(self):
        foreign = self.root / "foreign/scripts/cmux_agent_adapter.py"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("# foreign\n")
        self.helper.write_bytes(render(self.original, expected_sha256=BASELINE_SHA256,
                                      python=self.python["path"], adapter=foreign))
        self.repin_helper()
        with self.assertRaisesRegex(ValueError, "HELPER_FIXED_RELEASE_ROUTE_REQUIRED"):
            communication.campaign_plan(self.campaign)

    def test_invalid_duplicate_or_wrong_provider_identity_refused(self):
        for value in ("surface:1000020040", "not-a-uuid"):
            changed = copy.deepcopy(self.campaign)
            changed["sessions"]["claude_a"]["surface_uuid"] = value
            with self.assertRaises(ValueError):
                communication.campaign_plan(changed)
        changed = copy.deepcopy(self.campaign)
        changed["sessions"]["claude_a"]["surface_uuid"] = changed["sessions"]["codex"]["surface_uuid"]
        with self.assertRaisesRegex(ContractError, "distinct native UUIDs"):
            communication.campaign_plan(changed)
        changed["sessions"]["claude_a"]["provider"] = "codex"
        with self.assertRaisesRegex(ContractError, "provider changed"):
            communication.campaign_plan(changed)

    def test_runtime_and_python_route_mismatch_refused(self):
        self.runtime["python"] = {"path": "/unrelated/python"}
        self.campaign["runtime"] = write_json(self.runtime_path, self.runtime)
        with self.assertRaisesRegex(ContractError, "controller/Python changed"):
            communication.campaign_plan(self.campaign)
        self.campaign["python"] = self.runtime["python"]
        with self.assertRaisesRegex(ContractError, "helper Python changed"):
            communication.campaign_plan(self.campaign)

    def test_supervisor_has_exact_16_own_commands_and_existing_protocol(self):
        with patch.object(instructions, "validate_campaign", return_value=self.campaign):
            result = instructions.render({"path": "/campaign.json", "sha256": "a" * 64})
        self.assertEqual(result.count(" --request-id "), 16)
        self.assertLess(len(result), 24000)
        for text in ("1800 seconds", "callback --run RUN", "candidate --run RUN", "disarm",
                     "192 denominator", "UNKNOWN runtime remains UNKNOWN", "never resend"):
            self.assertIn(text, result)
        self.assertNotIn("before_business:", result)

    def test_task_scope_retains_business_and_callback_and_only_two_own_messages(self):
        info = dict(work_request={"path": "/request.json", "sha256": "b" * 64}, output_root="/output")
        node = dict(id="inventory", executor="claude_a", scope="Read the corpus.", deliverables=["report.json"])
        result = task_scope(self.campaign, {"run": {"run_id": "warm-01"}}, node, info)
        self.assertEqual(result.count(" --request-id "), 2)
        self.assertIn("before_business:", result)
        self.assertIn("after_business:", result)
        self.assertIn("--request /request.json --request-sha256 " + "b" * 64, result)
        self.assertIn("submit-completion-callback --task-pack", result)
        self.assertIn("No reply or additional ACK is requested", result)
        self.assertIn("Do not delay independent business work", result)
        self.assertNotIn("after_dispatch:", result)


if __name__ == "__main__":
    unittest.main()
