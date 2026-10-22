import unittest

from decision_provider import Decision, OfflineMockProvider, Outcome, RulesProvider, decide
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
        self.assertEqual(decide(context).outcome, Outcome.ESCALATE)

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
