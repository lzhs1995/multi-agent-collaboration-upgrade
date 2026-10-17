"""Accounting and paired-report semantics on synthetic records, never live proof."""
import copy
import tempfile
import unittest
from pathlib import Path

from orchestration_common import ContractError
from orchestration_permit import steps
from upgrade_benchmark import register, validate_registration
from upgrade_measurements import compare, native_usage, next_allowed


def codex_count(inputs, outputs, **changes):
    usage = dict(input_tokens=inputs, output_tokens=outputs, cached_input_tokens=0,
                 reasoning_output_tokens=0, total_tokens=inputs + outputs)
    usage.update(changes)
    return dict(type="event_msg", payload=dict(type="token_count", info=dict(total_token_usage=usage)))


def claude_message(key, inputs=10, outputs=2, stop="end_turn"):
    return dict(type="assistant", message=dict(id=key, stop_reason=stop,
                usage=dict(input_tokens=inputs, output_tokens=outputs, cache_read_input_tokens=3,
                           cache_creation_input_tokens=4)))


def records(*values):
    return [(dict(offset=i * 10), value) for i, value in enumerate(values)]


class AccountingTests(unittest.TestCase):
    def test_codex_uses_cumulative_differences_and_does_not_double_count(self):
        data = records(codex_count(100, 10), codex_count(120, 20), codex_count(120, 20), codex_count(150, 30))
        result = native_usage(data, "codex", 10, 40)
        self.assertEqual(result["status"], "MEASURED")
        self.assertEqual(result["tokens"]["total_tokens"], 70)
        self.assertEqual(result["tokens"]["input_tokens"], 50)

    def test_codex_missing_start_reset_or_final_accounting_stays_unknown(self):
        generated = dict(type="response_item", payload=dict(type="message", role="assistant"))
        cases = [(records(generated, codex_count(10, 2)), 10, "missing_counter_at_start_boundary"),
                 (records(codex_count(100, 10), codex_count(2, 1)), 10, "native_token_counter_reset"),
                 (records(codex_count(100, 10), generated), 10, "generation_without_final_accounting_at_end_boundary"),
                 (records(codex_count(0, 0), codex_count(2, 1, total_tokens=99)), 10, "inconsistent_total_token_semantics")]
        for data, start, reason in cases:
            with self.subTest(reason=reason):
                result = native_usage(data, "codex", start, 30)
                self.assertIsNone(result["tokens"])
                self.assertIn(reason, result["reasons"])

    def test_claude_deduplicates_message_ids_and_uses_final_usage(self):
        data = records(claude_message("old"), claude_message("one", outputs=1, stop=None),
                       claude_message("one", outputs=2), claude_message("two"), claude_message("two"))
        result = native_usage(data, "claude", 10, 50)
        self.assertEqual(result["status"], "MEASURED")
        self.assertEqual(result["tokens"]["total_tokens"], 38)
        self.assertEqual(result["tokens"]["output_tokens"], 4)

    def test_claude_incomplete_regressed_missing_and_crossed_boundaries_are_unknown(self):
        missing = claude_message("one")
        missing["message"].pop("usage")
        cases = [(records(claude_message("one", stop=None)), 0, "missing_final_message_accounting"),
                 (records(claude_message("one", outputs=3), claude_message("one", outputs=2)), 0, "message_usage_regressed"),
                 (records(missing), 0, "missing_native_message_usage"),
                 (records(claude_message("one"), claude_message("one")), 10, "message_crosses_start_boundary")]
        for data, start, reason in cases:
            with self.subTest(reason=reason):
                result = native_usage(data, "claude", start, 30)
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertIsNone(result["tokens"])
                self.assertIn(reason, result["reasons"])

    def test_accounting_excludes_records_after_end_and_rejects_boolean_counts(self):
        data = records(codex_count(100, 10), codex_count(110, 11), codex_count(999, 99))
        self.assertEqual(native_usage(data, "codex", 10, 20)["tokens"]["total_tokens"], 11)
        bad = claude_message("bad")
        bad["message"]["usage"]["output_tokens"] = True
        self.assertIsNone(native_usage(records(bad), "claude", 0, 10)["tokens"])


class PairReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registration = validate_registration(register(Path(self.tmp.name).resolve() / "report", "offline-report"))
        self.rows = []
        for step in steps(self.registration):
            group = self.registration["groups"][step["group"]]
            self.rows.append(dict(**step, arm=group["arm"], workload_class=group["workload_class"], status="PASS",
                contract_pass=True, within_cap=True, elapsed_seconds=100 if group["arm"] == "A1" else 80,
                human_interventions=0, usage=dict(tokens=None, cost=None), evidence_class="registered_live_measurement_v1"))

    def test_full_denominator_median_threshold_and_unknown_cost(self):
        result = compare(self.registration, self.rows)
        self.assertEqual((len(result["runs"]), len(result["pairs"]), result["pilot_passes"]), (33, 15, 3))
        self.assertEqual(result["paired_warm_median_improvement_percent"], 20)
        self.assertEqual((result["observed_runs"], result["verified_runs"], result["measured_runs"]), (33, 33, 33))
        self.assertTrue(result["timing_criterion_met"])
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertFalse(result["production_adoption"])

    def test_A1_interventions_are_retained_and_B_must_be_zero(self):
        for row in self.rows:
            if row["arm"] == "A1":
                row["human_interventions"] = 2
                self.assertTrue(next_allowed(row))
        result = compare(self.registration, self.rows)
        self.assertTrue(result["timing_criterion_met"])
        self.assertTrue(result["complete_correct_and_intervention_contract"])
        self.assertFalse(result["complete_correct_unattended"])
        self.rows[0]["human_interventions"] = 1
        self.assertFalse(compare(self.registration, self.rows)["timing_criterion_met"])

    def test_missing_failed_and_unknown_slots_never_shrink_denominator(self):
        rows = copy.deepcopy(self.rows[:-1])
        rows[5].update(status="FAILED", contract_pass=False)
        rows[7].update(status="UNKNOWN", evidence_class="unverified_original_attempt", elapsed_seconds=None)
        result = compare(self.registration, rows)
        self.assertEqual((result["expected_runs"], result["observed_runs"], result["verified_runs"]), (33, 32, 31))
        self.assertEqual((len(result["pairs"]), result["runs"][-1]["status"]), (15, "MISSING"))
        self.assertIsNone(result["paired_warm_median_improvement_percent"])
        self.assertEqual(result["conclusion"], "INSUFFICIENT_EVIDENCE")

    def test_class_regression_blocks_general_replacement_despite_favorable_median(self):
        for row in self.rows:
            if row["phase"] == "warm" and row["arm"] == "B" and row["workload_class"] == "fan_in":
                row["elapsed_seconds"] = 106
        result = compare(self.registration, self.rows)
        self.assertEqual(result["paired_warm_median_improvement_percent"], 20)
        self.assertEqual(result["classes_exceeding_regression_limit"], ["fan_in"])
        self.assertFalse(result["timing_criterion_met"])

    def test_cold_is_separate_and_15_percent_boundary_is_inclusive(self):
        for row in self.rows:
            if row["arm"] == "B":
                row["elapsed_seconds"] = 85 if row["phase"] == "warm" else 500
        result = compare(self.registration, self.rows)
        self.assertEqual(result["paired_warm_median_improvement_percent"], 15)
        self.assertTrue(result["timing_criterion_met"])
        self.assertTrue(all(p["improvement_percent"] == -400 for p in result["pairs"] if p["phase"] == "cold"))

    def test_nan_infinity_boolean_and_fixture_timing_are_ineligible(self):
        for bad in (float("nan"), float("inf"), True, -1, 0):
            rows = copy.deepcopy(self.rows)
            rows[6]["elapsed_seconds"] = bad
            rows[6]["usage"]["cost"] = bad
            with self.subTest(bad=bad):
                result = compare(self.registration, rows)
                self.assertFalse(result["timing_criterion_met"])
                self.assertIsNone(result["paired_warm_median_improvement_percent"])
        self.rows[6]["evidence_class"] = "offline_fixture"
        self.assertFalse(compare(self.registration, self.rows)["timing_criterion_met"])

    def test_duplicate_or_unregistered_run_cannot_affect_pairs(self):
        for rows in (self.rows + [self.rows[0]], self.rows + [dict(self.rows[0], run_id="extra")]):
            with self.assertRaises(ContractError):
                compare(self.registration, rows)

    def test_known_cost_increase_is_disclosed_without_inventing_unknown_bills(self):
        for row in self.rows:
            row["usage"]["cost"] = 1 if row["arm"] == "A1" else 1.2
        result = compare(self.registration, self.rows)
        self.assertEqual((result["cost_status"], len(result["cost_increase_over_10_percent"])), ("MEASURED", 15))


if __name__ == "__main__":
    unittest.main()
