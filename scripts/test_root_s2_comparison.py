"""S2 reporting shape controls only: no registration, native proof, client or API.

Positive numeric cases stub the original observation checker explicitly. They
are not measured runs. The negative reader control uses the real checker.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import upgrade_jev_benchmark as s2
import upgrade_measurements as measurements
import upgrade_schedule as schedule
from orchestration_common import ContractError, file_ref
from orchestration_permit import steps
from test_upgrade_jev_benchmark import PLAN, PLAN_SHA
from upgrade_benchmark import CONTRACT, observation_policy


@unittest.skipUnless(PLAN.exists(), "original frozen S2 plan unavailable")
class S2ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="s2-report-only-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve() / "layout-only"
        self.assertEqual(file_ref(PLAN)["sha256"], PLAN_SHA)
        groups, pairs = s2.layout(root, file_ref(PLAN), compact=False)
        # Pure in-memory layout, not register(); no opportunity counts or
        # eligibility claims are created by these tests.
        self.registration = dict(schema=s2.SCHEMA, benchmark_id="numeric-fixture-only", root=str(root),
                                 groups=groups, pairs=pairs, contract=CONTRACT,
                                 response_observation=observation_policy(False))
        self.reference = dict(path=str(root / "not-a-registration.json"), bytes=0, sha256="0" * 64)
        self.rows = []
        for step in steps(self.registration):
            group = groups[step["group"]]
            on = group["advisory"] == "advisory-on"
            self.rows.append(dict(**step, arm="B", workload_class=group["workload_class"], status="PASS",
                                  contract_pass=True, within_cap=True, elapsed_seconds=80 if on else 100,
                                  human_interventions=0, usage=dict(tokens=None, cost=1.2 if on else 1),
                                  evidence_class=measurements.LIVE_MEASUREMENTS[1],
                                  observer_configuration={}))

    def numeric_fixture(self, rows):
        checks = {(r["group"], r["run_id"]): dict(verified=r.get("evidence_class") == measurements.LIVE_MEASUREMENTS[1],
                                                reason="explicit synthetic observation control") for r in rows}
        with patch.object(measurements, "observation_checks", return_value=checks):
            return measurements.compare(self.registration, rows, registration_ref=self.reference)

    def test_unstarted_report_retains_all_slots_without_crash(self):
        with patch.object(measurements, "observation_checks", return_value={}):
            result = measurements.compare(self.registration, [], registration_ref=self.reference)
        self.assertEqual((result["expected_runs"], len(result["pairs"]), result["observed_runs"]), (33, 15, 0))
        self.assertEqual(result["conclusion"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result["timing_criterion_met"])
        self.assertTrue(all(r["status"] == "MISSING" and r["arm"] == "B" for r in result["runs"]))

    def test_original_schedule_report_uses_the_s2_consumer(self):
        with patch.object(schedule, "ledger", return_value=(self.registration, [], [])), \
                patch.object(schedule, "read_halt", return_value=None), \
                patch.object(measurements, "observation_checks", return_value={}):
            result = schedule.report(self.reference)
        self.assertEqual((result["expected_runs"], result["input_operations"]), (33, 0))
        self.assertEqual(result["schema"], "upgrade-jev-paired-report-v1")
        self.assertFalse(result["production_adoption"])

    def test_off_on_members_are_compared_without_relabeling_registration(self):
        before = copy.deepcopy(self.registration)
        result = self.numeric_fixture(self.rows)
        self.assertEqual(self.registration, before)
        self.assertEqual((result["expected_runs"], result["pilot_passes"], result["available_pairs"]), (33, 3, 12))
        self.assertEqual(result["paired_warm_median_improvement_percent"], 20)
        self.assertTrue(result["timing_criterion_met"])
        self.assertTrue(all(set(p["members"]) == {"advisory-off", "advisory-on"} for p in result["pairs"]))
        self.assertEqual([p["pair_id"] for p in result["pairs"]], [p["pair_id"] for p in self.registration["pairs"]])
        self.assertFalse(result["production_adoption"])
        self.assertFalse(result["native_authenticated_by_this_report"])

    def test_unknown_cost_cannot_meet_s2_numeric_gate(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["usage"]["cost"] = None
        result = self.numeric_fixture(rows)
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertFalse(result["timing_criterion_met"])

    def test_observed_zero_prices_are_not_unknown(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["usage"]["cost"] = 0
        result = self.numeric_fixture(rows)
        self.assertEqual(result["cost_status"], "MEASURED")
        self.assertTrue(result["timing_criterion_met"])
        self.assertEqual(result["zero_base_cost_increases"], [])

    def test_known_zero_base_increase_is_disclosed_not_divided_by_zero(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["usage"]["cost"] = (1 if self.registration["groups"][row["group"]]["advisory"] == "advisory-on" else 0)
        result = self.numeric_fixture(rows)
        self.assertEqual(result["cost_status"], "MEASURED")
        self.assertEqual(len(result["zero_base_cost_increases"]), 15)

    def test_cold_effects_are_separate_and_warm_15_percent_is_inclusive(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            if self.registration["groups"][row["group"]]["advisory"] == "advisory-on":
                row["elapsed_seconds"] = 85 if row["phase"] == "warm" else 500
        result = self.numeric_fixture(rows)
        self.assertEqual(result["paired_warm_median_improvement_percent"], 15)
        self.assertTrue(result["timing_criterion_met"])
        self.assertTrue(all(p["improvement_percent"] == -400 for p in result["pairs"] if p["phase"] == "cold"))

    def test_missing_failed_unknown_samples_stay_in_original_denominator(self):
        rows = copy.deepcopy(self.rows[:-1])
        rows[4].update(status="FAILED", contract_pass=False)
        rows[7].update(status="UNKNOWN", elapsed_seconds=None, evidence_class="unverified_original_attempt")
        result = self.numeric_fixture(rows)
        self.assertEqual((result["expected_runs"], result["observed_runs"], len(result["pairs"])), (33, 32, 15))
        self.assertEqual(result["runs"][-1]["status"], "MISSING")
        self.assertFalse(result["timing_criterion_met"])
        self.assertIsNone(result["paired_warm_median_improvement_percent"])

    def test_one_class_regression_blocks_positive_overall_median(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            if row["phase"] == "warm" and row["workload_class"] == "fan_in" and \
                    self.registration["groups"][row["group"]]["advisory"] == "advisory-on":
                row["elapsed_seconds"] = 106
        result = self.numeric_fixture(rows)
        self.assertEqual(result["paired_warm_median_improvement_percent"], 20)
        self.assertEqual(result["classes_exceeding_regression_limit"], ["fan_in"])
        self.assertFalse(result["timing_criterion_met"])

    def test_both_omo_arms_require_zero_human_interventions(self):
        for advisory in ("advisory-off", "advisory-on"):
            rows = copy.deepcopy(self.rows)
            changed = next(r for r in rows if self.registration["groups"][r["group"]]["advisory"] == advisory)
            changed["human_interventions"] = 1
            with self.subTest(advisory=advisory):
                self.assertFalse(self.numeric_fixture(rows)["timing_criterion_met"])

    def test_duplicate_extra_or_wrong_runtime_arm_is_refused(self):
        for rows in (self.rows + [self.rows[0]], self.rows + [dict(self.rows[0], run_id="extra")],
                     [dict(self.rows[0], arm="A1")] + self.rows[1:]):
            with self.assertRaises(ContractError):
                self.numeric_fixture(rows)

    def test_invalid_elapsed_and_legacy_evidence_never_qualify_s2(self):
        for bad in (True, float("nan"), float("inf"), 0, -1):
            rows = copy.deepcopy(self.rows)
            rows[6]["elapsed_seconds"] = bad
            with self.subTest(elapsed=bad):
                self.assertFalse(self.numeric_fixture(rows)["timing_criterion_met"])
        rows = copy.deepcopy(self.rows)
        rows[6]["evidence_class"] = measurements.LIVE_MEASUREMENTS[0]
        self.assertFalse(self.numeric_fixture(rows)["timing_criterion_met"])

    def test_real_observation_checker_rejects_self_labeled_native_rows(self):
        # No fake native record is supplied. Missing campaigns/closures are
        # rejected by the real observer; only registration loading is stubbed.
        with patch.object(measurements, "validate_registration", return_value=self.registration):
            result = measurements.compare(self.registration, self.rows, registration_ref=self.reference)
        self.assertEqual(result["verified_runs"], 0)
        self.assertFalse(result["timing_criterion_met"])
        self.assertEqual(result["conclusion"], "INSUFFICIENT_EVIDENCE")

    def test_s2_requires_original_registration_reference(self):
        with self.assertRaises(ContractError):
            measurements.compare(self.registration, self.rows)


if __name__ == "__main__":
    unittest.main()
