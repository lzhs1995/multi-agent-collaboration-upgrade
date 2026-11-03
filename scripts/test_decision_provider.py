import unittest

from decision_provider import (UNBOUND_PENDING_BIND, Decision, OfflineMockProvider, Outcome, RulesProvider,
                               decide, deliberately_unbound)
from orchestration_common import ContractError


class DecisionProviderTests(unittest.TestCase):
    def context(self, **changes):
        return dict(authorized=True, identity_verified=True, scope_valid=True,
                    evidence_verified=True, purpose="route", executor="claude",
                    available_executors=["claude", "codex"], **changes)

    def test_routes_only_to_assigned_existing_session(self):
        self.assertEqual(decide(self.context()).route, "claude")
        context = self.context()
        context["available_executors"] = ["codex"]
        unavailable = decide(context)
        self.assertEqual(unavailable.outcome, Outcome.ESCALATE)
        # Both branches escalate, so the reason is the only distinguishing
        # assertion: an outcome-only check keeps a removed unbound branch green.
        self.assertEqual(unavailable.reason, "the assigned existing session is unavailable")

        unbound = self.context()
        unbound.update(executor=None, executor_binding=UNBOUND_PENDING_BIND)
        verdict = decide(unbound)
        self.assertEqual(verdict.outcome, Outcome.ESCALATE)
        self.assertEqual(verdict.reason, "the frozen node is deliberately unbound; bind it before claiming")
        self.assertIsNone(verdict.route)

        missing = self.context()
        del missing["executor"]
        absent = decide(missing)
        self.assertEqual(absent.outcome, Outcome.ESCALATE)
        self.assertEqual(absent.reason, "the assigned existing session is unavailable")

        bad = self.context()
        bad.update(executor=None, executor_binding="not_a_lane")
        wrong = decide(bad)
        self.assertEqual(wrong.outcome, Outcome.DENY)
        self.assertEqual(wrong.reason, "unsupported executor binding fact")

    def test_a_bound_binding_fact_routes_to_that_lane(self):
        verdict = decide(self.context(executor_binding="claude"))
        self.assertEqual(verdict.outcome, Outcome.ALLOW)
        self.assertEqual(verdict.route, "claude")

    def test_deliberately_unbound_reads_only_the_explicit_fact(self):
        self.assertTrue(deliberately_unbound({"executor_binding": UNBOUND_PENDING_BIND}))
        for context in ({}, {"executor": None}, {"executor_binding": None},
                        {"executor_binding": "claude"}, {"executor_binding": "unbound_pending_bind"}):
            with self.subTest(context=context):
                self.assertFalse(deliberately_unbound(context))

    def test_advisory_cannot_override_any_hard_gate(self):
        mock = OfflineMockProvider({"route": Decision(Outcome.ALLOW, "mock", 1)})
        for gate in ("authorized", "identity_verified", "scope_valid", "evidence_verified"):
            for value in (False, None, "true", 1):
                context = self.context()
                context[gate] = value
                with self.subTest(gate=gate, value=value):
                    self.assertEqual(decide(context, mock).outcome, Outcome.DENY)

    def test_low_confidence_and_exception_have_different_outcomes(self):
        low = OfflineMockProvider({"route": Decision(Outcome.ALLOW, "uncertain", 0.3)})
        self.assertEqual(decide(self.context(), low).outcome, Outcome.ESCALATE)
        broken = OfflineMockProvider({"route": RuntimeError("offline failure")})
        fallback = decide(self.context(), broken)
        self.assertEqual(fallback.outcome, Outcome.ALLOW)
        self.assertEqual(fallback.provider, "rules_fallback")

    def test_completion_and_scope_need_evidence(self):
        for purpose in ("completion", "scope"):
            context = self.context()
            context.update(purpose=purpose, evidence_verified=False)
            self.assertEqual(RulesProvider().decide(context).outcome, Outcome.DENY)

    def test_dangerous_operation_needs_specific_authority(self):
        context = self.context()
        context.update(purpose="dangerous_operation", dangerous=True)
        self.assertEqual(decide(context).outcome, Outcome.DENY)
        context["operation_authorized"] = True
        self.assertEqual(decide(context).outcome, Outcome.ALLOW)

    def test_mock_cannot_reroute_or_hide_deny(self):
        reroute = OfflineMockProvider({"route": Decision(Outcome.ALLOW, "switch", 1, route="codex")})
        self.assertEqual(decide(self.context(), reroute).outcome, Outcome.DENY)
        deny = OfflineMockProvider({"route": Decision(Outcome.DENY, "review finding", 1)})
        self.assertEqual(decide(self.context(), deny).outcome, Outcome.DENY)

    def test_confidence_rejects_nonfinite_or_bool(self):
        for value in (float("nan"), float("inf"), True, -1, 2):
            with self.subTest(value=value), self.assertRaises(ContractError):
                Decision(Outcome.ALLOW, "invalid", value)


if __name__ == "__main__":
    unittest.main()
