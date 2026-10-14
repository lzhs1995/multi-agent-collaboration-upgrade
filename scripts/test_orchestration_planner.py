#!/usr/bin/env python3
"""Focused tests for the CLI-owned orchestration adapter."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from orchestration_planner import (  # noqa: E402
    PlanError,
    benchmark,
    build_schedule,
    materialize,
    validate_plan,
)


FIXTURE = ROOT / "tests" / "fixtures" / "orchestration-plan.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class OrchestrationPlannerTests(unittest.TestCase):
    def test_valid_graph_is_acyclic_and_keeps_two_cli_lanes(self):
        plan = validate_plan(load_fixture())
        waves = build_schedule(plan)
        self.assertEqual([wave["nodes"] for wave in waves], [
            ["intake"],
            ["protocol-inspection", "policy-review"],
            ["implementation", "documentation"],
            ["tests"],
            ["closeout-audit"],
        ])
        self.assertEqual(waves[1]["executors"], ["claude", "codex"])
        self.assertEqual(waves[2]["parallelism"], 2)

    def test_benchmark_is_explicitly_an_estimate_and_clears_target(self):
        result = benchmark(load_fixture())
        self.assertEqual(result["evidence_class"], "offline_schedule_estimate")
        self.assertEqual(result["serial_seconds"], 790.0)
        self.assertEqual(result["scheduled_seconds"], 600.0)
        self.assertGreaterEqual(result["wall_time_reduction_percent"], 15.0)
        self.assertTrue(result["meets_fifteen_percent_target"])
        self.assertIn("offline", result["claim"].lower())
        self.assertNotIn("unless", result["claim"])

    def test_estimate_cannot_launder_an_input_evidence_label(self):
        plan = load_fixture()
        plan["evidence_class"] = "measured_cli_replay"
        self.assertEqual(benchmark(plan)["evidence_class"], "offline_schedule_estimate")

    def test_nonfinite_estimates_and_multiple_tasks_per_session_are_rejected(self):
        for field in ("expected_seconds", "retry_seconds"):
            for value in (float("nan"), float("inf"), True):
                plan = load_fixture()
                plan["nodes"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(PlanError):
                    validate_plan(plan)
        with self.assertRaises(PlanError):
            build_schedule(load_fixture(), max_parallel_per_executor=2)

    def test_nested_and_symlink_alias_scopes_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            for other in (root / "real" / "nested", root / "alias" / "nested"):
                plan = load_fixture()
                plan["nodes"][3]["write_scope"] = str(root / "real")
                plan["nodes"][4]["write_scope"] = str(other)
                self.assertEqual(build_schedule(plan)[2]["nodes"], ["implementation"])

    def test_run_identity_is_unique_and_existing_artifacts_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = materialize(load_fixture(), root / "one")
            second = materialize(load_fixture(), root / "two")
            self.assertNotEqual(first["run_id"], second["run_id"])
            seed1 = json.loads((root / "one/task-pack-seeds/intake.json").read_text())
            seed2 = json.loads((root / "two/task-pack-seeds/intake.json").read_text())
            self.assertNotEqual(seed1["task_id"], seed2["task_id"])
            self.assertNotEqual(seed1["attempt_id"], seed2["attempt_id"])
            with self.assertRaisesRegex(PlanError, "empty output root"):
                materialize(load_fixture(), root / "one")

    def test_retries_are_counted_without_changing_dependency_order(self):
        plan = load_fixture()
        plan["nodes"][0]["observed_retries"] = 1
        plan["nodes"][0]["retry_seconds"] = 30
        result = benchmark(plan)
        self.assertEqual(result["serial_seconds"], 820.0)
        self.assertEqual(result["scheduled_seconds"], 630.0)

    def test_cycle_is_rejected(self):
        plan = load_fixture()
        plan["nodes"][0]["depends_on"] = ["closeout-audit"]
        with self.assertRaisesRegex(PlanError, "cycle"):
            validate_plan(plan)

    def test_third_party_api_is_rejected(self):
        plan = load_fixture()
        plan["api_policy"]["third_party_api"] = True
        with self.assertRaisesRegex(PlanError, "third_party_api"):
            validate_plan(plan)

    def test_replacement_session_and_mismatched_transport_are_rejected(self):
        plan = load_fixture()
        plan["nodes"][0]["session_policy"] = "spawn_replacement"
        with self.assertRaisesRegex(PlanError, "session_policy"):
            validate_plan(plan)
        plan = load_fixture()
        plan["nodes"][0]["transport"] = "typesafe_api"
        with self.assertRaisesRegex(PlanError, "transport"):
            validate_plan(plan)

    def test_shared_write_scope_is_serialized_even_when_dependencies_allow_parallelism(self):
        plan = load_fixture()
        plan["nodes"][4]["write_scope"] = plan["nodes"][3]["write_scope"]
        waves = build_schedule(plan)
        self.assertNotIn("documentation", waves[2]["nodes"])
        self.assertIn("implementation", waves[2]["nodes"])
        self.assertEqual(waves[3]["nodes"], ["documentation"])

    def test_materialize_writes_hash_bound_manifest_and_seeds(self):
        with tempfile.TemporaryDirectory() as directory:
            result = materialize(load_fixture(), Path(directory))
            root = Path(directory)
            self.assertTrue(Path(result["manifest"]).is_file())
            self.assertTrue((root / "orchestration-plan.json").is_file())
            self.assertTrue((root / "orchestration-schedule.json").is_file())
            seed = json.loads((root / "task-pack-seeds" / "implementation.json").read_text(encoding="utf-8"))
            self.assertEqual(seed["transport"], "claude_code_cli")
            self.assertFalse(seed["api_policy"]["third_party_api"])
            self.assertFalse(seed["dispatch"]["replacement_session"])
            manifest = json.loads((root / "orchestration-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["plan_sha256"], result["plan_sha256"])
            self.assertEqual(manifest["seed_sha256"]["implementation"], result["seed_sha256"]["implementation"])

    def test_materialize_refuses_output_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "real"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(PlanError, "symlink"):
                materialize(load_fixture(), link)


if __name__ == "__main__":
    unittest.main(verbosity=2)
