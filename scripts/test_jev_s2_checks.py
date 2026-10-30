"""New synthetic negative controls, not a rerun of S0/S1 or a live S2 claim.

The 27 cases in ``ConsumerJoinTests`` and ``PairedAccountingTests`` are ROOT's
R32 tests (`test_jev_s2_checks.py` sha256
`e35c9b6d6cdba991b90ad24182e4c3a8888615e8e2f9dd872692b089e7df069d`) ported
verbatim: every body, every fixture and every expected verdict is byte-identical,
including ``median_paired_improvement == 0.3`` and the exact-boundary case. Only
this header differs. The ported checker takes its provider, model and arm labels
from the frozen registration (design Q44), so the two call shims below supply
exactly R32's own literals; a test that wants another registration passes it
explicitly. ``R62Negatives`` and ``RegistrationTests`` are new.
"""
import unittest

import jev_s2_checks
from jev_s2_checks import EvidenceError

# Exactly the labels R32 hardcoded, so every ported verdict is unchanged.
REGISTRATION = dict(provider="typesafe", model="jev-1.14.0",
                    off_arm="advisory-off", on_arm="advisory-on")


def consumption_join(trace, *, registration=REGISTRATION):
    """R32's own call shape, with its literals supplied as the registration."""
    return jev_s2_checks.consumption_join(trace, registration=registration)


def estimate_jev_increment(pairs, expected_pair_ids, *, registration=REGISTRATION):
    return jev_s2_checks.estimate_jev_increment(pairs, expected_pair_ids, registration=registration)


def trace():
    sha = "a" * 64
    request = dict(run_id="new-run", status="HTTP_200", model="jev-1.14.0", input_sha256=sha,
                   questions_sha256=sha, request_sha256=sha, finished_at=1)
    advice = dict(request, provider="typesafe", advice_sha256=sha, route="claude_b", finished_at=2)
    read = dict(run_id="new-run", advice_sha256=sha, complete=True, actor_session="original", read_at=3)
    decision = dict(run_id="new-run", advice_sha256=sha, actor_session="original", decided_at=4,
                    task_id="new-task", write_scope_sha256=sha, actual_route="claude_b",
                    action="accepted", reason="Independent review belongs to the nonauthor lane")
    binding = dict(run_id="new-run", supervisor_session="original", task_id="new-task",
                   write_scope_sha256=sha, actual_route="claude_b", bound_at=5,
                   new_binding=True, eligible_lanes=["claude_b"])
    return dict(run_id="new-run", mode="advisory-on", request=request, advice=advice,
                read=read, decision=decision, binding=binding)


def pair(pair_id="one", workload="fan_in", elapsed=70):
    off = dict(mode="advisory-off", phase="warm", workload=workload, input_sha256="a" * 64,
               main_models=["gpt-6.1-sol", "same-claude"], strategy="omo",
               resource_policy_sha256="b" * 64, response_observer_sha256=None,
               write_scope_policy_sha256="c" * 64, model_requests=0, outcome="SUCCESS",
               all_costs_observed=True, full_elapsed_seconds=100, total_main_child_tokens=1000,
               human_prompts=0, human_interventions=1, total_cost_usd=0.01)
    on = dict(off, mode="advisory-on", model_requests=1, full_elapsed_seconds=elapsed,
              total_cost_usd=0.01001, advice_consumption_authenticated=True)
    return dict(pair_id=pair_id, off=off, on=on)


class ConsumerJoinTests(unittest.TestCase):
    def assert_rejected(self, value, expected):
        with self.assertRaisesRegex(EvidenceError, expected):
            consumption_join(value)

    def test_valid_join_never_claims_native_or_upgrade_acceptance(self):
        result = consumption_join(trace())
        self.assertEqual(result["structural_join"], "VALID")
        self.assertFalse(result["runtime_consumption_proven"])
        self.assertFalse(result["native_authenticated"])
        self.assertFalse(result["upgrade_accepted"])

    def test_shadow_label_is_not_consumption(self):
        value = trace(); value["mode"] = "shadow"
        self.assert_rejected(value, "not actual")

    def test_missing_read_does_not_become_consumed(self):
        value = trace(); value.pop("read")
        self.assert_rejected(value, "complete read")

    def test_reading_notice_or_partial_body_is_not_full_advice(self):
        value = trace(); value["read"]["complete"] = False
        self.assert_rejected(value, "partial/notice-only")

    def test_cross_request_input_questions_and_advice_digest_all_refused(self):
        for section, field in (("advice", "input_sha256"), ("advice", "questions_sha256"),
                               ("advice", "request_sha256"), ("read", "advice_sha256"),
                               ("decision", "advice_sha256")):
            value = trace(); value[section][field] = "b" * 64
            with self.subTest(section=section, field=field):
                self.assert_rejected(value, "does not match|exact same")

    def test_cross_run_or_supervisor_cannot_consume(self):
        for section, field in (("read", "run_id"), ("read", "actor_session"),
                               ("decision", "actor_session")):
            value = trace(); value[section][field] = "foreign"
            with self.subTest(section=section, field=field):
                self.assert_rejected(value, "cross-run|original supervisor")

    def test_post_binding_read_or_decision_refused(self):
        for section, field in (("read", "read_at"), ("decision", "decided_at")):
            value = trace(); value[section][field] = 6
            with self.subTest(section=section):
                self.assert_rejected(value, "precede fresh")

    def test_existing_task_cannot_be_rebound(self):
        value = trace(); value["binding"]["new_binding"] = False
        self.assert_rejected(value, "may not be rebound")

    def test_write_domain_and_task_identity_preserved(self):
        for field in ("task_id", "write_scope_sha256"):
            value = trace(); value["binding"][field] = "different"
            with self.subTest(field=field):
                self.assert_rejected(value, "scope changed")

    def test_cannot_adopt_an_unavailable_or_author_lane(self):
        value = trace(); value["binding"]["eligible_lanes"] = []
        self.assert_rejected(value, "eligible write domain")

    def test_rejection_allowed_with_reason_and_code_valid_route(self):
        value = trace(); value["decision"].update(action="rejected", reason="Current writer is unavailable")
        value["decision"]["actual_route"] = value["binding"]["actual_route"] = "codex"
        value["binding"]["eligible_lanes"] = ["codex"]
        self.assertEqual(consumption_join(value)["structural_join"], "VALID")

    def test_auth_error_cannot_be_relabelled_negative(self):
        for status in ("HTTP_401", "HTTP_403", "TIMEOUT", "HTTP_429", "HTTP_502"):
            value = trace(); value["request"]["status"] = status
            with self.subTest(status=status):
                self.assert_rejected(value, "failed inference")

    def test_boolean_and_nonfinite_times_are_not_evidence(self):
        for bad in (True, float("nan"), float("inf"), "4", -1):
            value = trace(); value["decision"]["decided_at"] = bad
            with self.subTest(bad=bad):
                self.assert_rejected(value, "finite numeric")

    def test_missing_run_identity_and_string_eligibility_refused(self):
        value = trace()
        value.pop("run_id")
        for part in ("request", "advice", "read", "decision", "binding"):
            value[part].pop("run_id")
        self.assert_rejected(value, "run identity")
        value = trace(); value["binding"]["eligible_lanes"] = "claude_b"
        self.assert_rejected(value, "eligible write domain")


class PairedAccountingTests(unittest.TestCase):
    def evaluate(self, value=None, expected=None):
        return estimate_jev_increment([pair()] if value is None else value,
                                      ["one"] if expected is None else expected)

    def test_paired_effect_only_and_not_final_acceptance(self):
        result = self.evaluate()
        self.assertAlmostEqual(result["median_paired_improvement"], 0.3)
        self.assertTrue(result["numeric_gate_only"])
        self.assertFalse(result["upgrade_accepted"])

    def test_changed_strategy_model_resource_or_observer_is_confounding(self):
        for field in ("phase", "strategy", "main_models", "resource_policy_sha256",
                      "response_observer_sha256", "input_sha256", "write_scope_policy_sha256"):
            value = pair(); value["on"][field] = "changed"
            with self.subTest(field=field), self.assertRaisesRegex(EvidenceError, "confounded by"):
                self.evaluate([value])

    def test_missing_duplicate_and_extra_pairs_never_shrink_failure_denominator(self):
        for values, expected in (([pair()], ["one", "two"]),
                                 ([pair(), pair()], ["one"]),
                                 ([pair(), pair("extra")], ["one"])):
            with self.subTest(expected=expected), self.assertRaisesRegex(EvidenceError, "denominator"):
                self.evaluate(values, expected)

    def test_failed_unknown_timeout_incomplete_pairs_are_retained(self):
        for outcome in ("FAILED", "UNKNOWN", "TIMEOUT", "INCOMPLETE"):
            value = pair("two"); value["on"]["outcome"] = outcome
            with self.subTest(outcome=outcome):
                result = self.evaluate([pair(), value], ["one", "two"])
                self.assertEqual(result["registered_pairs"], 2)
                self.assertEqual(result["performance_incomplete_pairs"], ["two"])
                self.assertFalse(result["numeric_gate_only"])

    def test_shadow_and_unproven_native_consumption_cannot_be_on_treatment(self):
        for key, new in (("mode", "shadow"), ("advice_consumption_authenticated", False)):
            value = pair(); value["on"][key] = new
            with self.subTest(key=key), self.assertRaisesRegex(EvidenceError, "treatment|consumption"):
                self.evaluate([value])

    def test_exact_15_percent_boundary_and_no_workload_over_5_percent_regression(self):
        self.assertTrue(self.evaluate([pair(elapsed=85)])["numeric_gate_only"])
        values = [pair("one", "fan_in", 50), pair("two", "write_conflict", 106)]
        result = self.evaluate(values, ["one", "two"])
        self.assertGreater(result["median_paired_improvement"], 0.15)
        self.assertFalse(result["numeric_gate_only"])

    def test_full_cost_unknown_and_missing_latency_cannot_promote(self):
        for key in ("total_cost_usd", "full_elapsed_seconds"):
            value = pair(); value["on"][key] = None
            result = self.evaluate([value])
            self.assertEqual(result["performance_incomplete_pairs"], ["one"])
            self.assertFalse(result["numeric_gate_only"])

    def test_cost_increase_is_disclosed_not_hidden_by_latency(self):
        value = pair(); value["on"]["total_cost_usd"] = 0.012
        self.assertEqual(self.evaluate([value])["cost_increase_over_10_percent_or_zero_base"], ["one"])

    def test_zero_baseline_and_missing_tokens_refused(self):
        for key, val in (("full_elapsed_seconds", 0), ("total_main_child_tokens", None)):
            value = pair(); value["off"][key] = val
            with self.subTest(key=key), self.assertRaisesRegex(EvidenceError, "zero baseline|missing full"):
                self.evaluate([value])

    def test_human_prompt_and_intervention_regressions_refused(self):
        for key, val in (("human_prompts", 1), ("human_interventions", 2)):
            value = pair(); value["on"][key] = val
            with self.subTest(key=key), self.assertRaisesRegex(EvidenceError, "human prompt|intervention"):
                self.evaluate([value])

    def test_use_median_of_paired_effects_not_ratio_of_unpaired_medians(self):
        values = [pair("one", elapsed=40), pair("two", elapsed=45), pair("three", elapsed=480)]
        values[1]["off"]["full_elapsed_seconds"] = 50
        values[2]["off"]["full_elapsed_seconds"] = 800
        result = self.evaluate(values, ["one", "two", "three"])
        self.assertAlmostEqual(result["median_paired_improvement"], 0.4)
        unpaired = 1 - 45 / 100
        self.assertNotAlmostEqual(result["median_paired_improvement"], unpaired)

    def test_cold_pairs_cannot_make_failed_warm_threshold_pass(self):
        values = [pair("one", elapsed=90), pair("two", elapsed=10), pair("three", elapsed=10)]
        for value in values[1:]:
            value["off"]["phase"] = value["on"]["phase"] = "cold"
        result = self.evaluate(values, ["one", "two", "three"])
        self.assertEqual(result["registered_pairs"], 3)
        self.assertEqual(result["observed_warm_pairs"], 1)
        self.assertAlmostEqual(result["median_paired_improvement"], 0.1)
        self.assertFalse(result["numeric_gate_only"])
        self.assertEqual(len(result["cold_paired_improvements_disclosed_separately"]), 2)

    def test_empty_denominator_no_warm_or_boolean_request_count_refused(self):
        with self.assertRaisesRegex(EvidenceError, "denominator"):
            self.evaluate([], [])
        value = pair(); value["off"]["model_requests"] = False
        with self.assertRaisesRegex(EvidenceError, "silently query"):
            self.evaluate([value])
        value = pair(); value["off"]["phase"] = value["on"]["phase"] = "cold"
        self.assertFalse(self.evaluate([value])["numeric_gate_only"])


class R62Negatives(unittest.TestCase):
    """R62 ``checks.new_negatives``, at the level this ported checker decides.

    Each case is a shape R32's own 27 do not already cover, and each is asserted
    against the ported checker rather than restated in prose. The run-level
    halves of these same negatives live in ``test_orchestration_advice_chain.py``
    and ``test_orchestration_bind_unbound.py`` (design 4.3).
    """

    def assert_rejected(self, value, expected):
        with self.assertRaisesRegex(EvidenceError, expected):
            consumption_join(value)

    def test_off_arm_with_advice_requests_is_not_the_off_arm(self):
        # R32 covers model_requests=False; a real positive count is the shape
        # R62 names: the off arm must not silently query the advisor at all.
        for count in (1, 2):
            value = pair()
            value["off"]["model_requests"] = count
            with self.subTest(count=count), self.assertRaisesRegex(EvidenceError, "silently query"):
                estimate_jev_increment([value], ["one"])

    def test_a_truthy_but_incomplete_read_is_not_a_full_read(self):
        # R32 covers complete=False. A truthy non-True value is the partial read
        # R62 names: only the literal True is a complete original read.
        for value in (1, "true", "yes", [1], {"complete": True}):
            trace_value = trace()
            trace_value["read"]["complete"] = value
            with self.subTest(value=value):
                self.assert_rejected(trace_value, "partial/notice-only")

    def test_a_binding_without_any_decision_is_refused(self):
        # R32 pops "read"; popping the decision is the binding-without-decision
        # shape, and an empty decision is refused for the same reason.
        value = trace()
        value.pop("decision")
        self.assert_rejected(value, "decision and new binding")
        value = trace()
        value["binding"]["new_binding"] = True
        for missing in ("advice_sha256", "actor_session", "task_id"):
            broken = trace()
            del broken["decision"][missing]
            with self.subTest(missing=missing):
                self.assert_rejected(broken, "exact same advice|original supervisor|scope changed")

    def test_a_decision_and_read_citing_another_advice_together_are_refused(self):
        # R32 changes one digest at a time. Changing read and decision to the
        # same other digest is the agreed-but-wrong advice R62 names.
        value = trace()
        value["read"]["advice_sha256"] = value["decision"]["advice_sha256"] = "b" * 64
        self.assert_rejected(value, "exact same advice")

    def test_a_missing_or_truthy_new_binding_cannot_rebind(self):
        # R32 covers new_binding=False; absent or truthy-not-True is the same
        # refusal, so an already bound task is never rebound through advice.
        for value in (None, 1, "yes", "True"):
            trace_value = trace()
            if value is None:
                del trace_value["binding"]["new_binding"]
            else:
                trace_value["binding"]["new_binding"] = value
            with self.subTest(value=value):
                self.assert_rejected(trace_value, "may not be rebound")

    def test_a_fallback_or_stopped_advisor_is_not_counted_as_consumption(self):
        # R32 covers HTTP/timeout statuses. These are the fallback labels R62
        # names, and none of them is an answer anybody consumed.
        for status in ("JEV_FALLBACK", "STOPPED_AUTH", "NOT_CONFIGURED", "JEV_ERROR"):
            value = trace()
            value["request"]["status"] = status
            with self.subTest(status=status):
                self.assert_rejected(value, "failed inference")
        value = pair()
        value["on"]["advice_consumption_authenticated"] = None
        with self.assertRaisesRegex(EvidenceError, "authenticated native advice consumption"):
            estimate_jev_increment([value], ["one"])

    def test_a_route_copied_from_advice_is_not_a_recomputed_route(self):
        # The advised lane is only admissible if code recomputed it. R32 covers
        # an empty eligible list; these two are the copied-advice shapes.
        with self.subTest(case="advised lane outside the recomputed domain"):
            value = trace()
            value["binding"]["eligible_lanes"] = ["codex"]
            self.assert_rejected(value, "eligible write domain")
        with self.subTest(case="accepted advice differs from the actual route"):
            value = trace()
            value["advice"]["route"] = "codex"
            self.assert_rejected(value, "accepted advice differs")

    def test_a_decision_without_a_reason_is_refused(self):
        # v1 synthetic_self_check empty_reason: "record why advice was adopted
        # or rejected". R32's own tests never exercised it, so a mutant that
        # deleted the check survived; blank, whitespace and absent all refuse.
        for reason in ("", "  ", None):
            value = trace()
            value["decision"]["reason"] = reason
            with self.subTest(reason=reason):
                self.assert_rejected(value, "record why advice was adopted or rejected")


class RegistrationTests(unittest.TestCase):
    """The ported checker reads its labels from the registration, not literals."""

    def test_provider_and_model_come_from_the_registration(self):
        for field, other in (("provider", "deepseek"), ("model", "jev-1.13.0")):
            with self.subTest(field=field), self.assertRaisesRegex(EvidenceError, "provider/model provenance"):
                consumption_join(trace(), registration=dict(REGISTRATION, **{field: other}))
        # The same trace passes under its own registration, so the label decided it.
        self.assertEqual(consumption_join(trace())["structural_join"], "VALID")
        # The join reads the on-arm label only; the paired estimator reads both.
        with self.assertRaisesRegex(EvidenceError, "not actual on-arm consumption"):
            consumption_join(trace(), registration=dict(REGISTRATION, on_arm="shadow"))
        for field in ("off_arm", "on_arm"):
            with self.subTest(field=field), self.assertRaisesRegex(EvidenceError, "not the actual JEV treatment"):
                estimate_jev_increment([pair()], ["one"], registration=dict(REGISTRATION, **{field: "shadow"}))

    def test_registration_is_required(self):
        for registration in (None, {}, "typesafe", dict(REGISTRATION, provider=""),
                             {k: v for k, v in REGISTRATION.items() if k != "model"}):
            with self.subTest(registration=registration):
                with self.assertRaises(EvidenceError):
                    jev_s2_checks.consumption_join(trace(), registration=registration)
                with self.assertRaises(EvidenceError):
                    jev_s2_checks.estimate_jev_increment([pair()], ["one"], registration=registration)
        with self.assertRaisesRegex(EvidenceError, "registered arms must differ"):
            jev_s2_checks.consumption_join(trace(), registration=dict(REGISTRATION, off_arm="advisory-on"))


if __name__ == "__main__":
    unittest.main()
