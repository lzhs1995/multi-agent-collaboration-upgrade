"""Offline advisory decisions. Hard protocol gates never delegate authority."""
from __future__ import annotations

from dataclasses import dataclass, replace, asdict
from enum import Enum
from typing import Any, Protocol

from orchestration_common import ContractError, finite
from orchestration_planner import ISOLATED_EXECUTORS


class Outcome(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    confidence: float
    evidence_refs: tuple[str, ...] = ()
    route: str | None = None
    provider: str = "rules"

    def __post_init__(self):
        if not isinstance(self.outcome, Outcome) or not isinstance(self.reason, str) or not self.reason.strip():
            raise ContractError("decision requires a typed outcome and reason")
        if not 0 <= finite(self.confidence, "confidence") <= 1:
            raise ContractError("confidence must be between zero and one")
        if self.route not in (None, "claude", "codex", "claude_a", "claude_b"):
            raise ContractError("route must name an existing CLI lane")
        if not isinstance(self.evidence_refs, tuple) or any(not isinstance(x, str) or not x for x in self.evidence_refs):
            raise ContractError("evidence_refs must be a tuple of references")

    def record(self):
        value = asdict(self)
        value["outcome"] = self.outcome.value
        value["evidence_refs"] = list(self.evidence_refs)
        return value


class DecisionProvider(Protocol):
    def decide(self, context: dict[str, Any]) -> Decision: ...


HARD_GATES = ("authorized", "identity_verified", "scope_valid", "evidence_verified")
PURPOSES = {"route", "completion", "scope", "dangerous_operation"}
UNBOUND_PENDING_BIND = "UNBOUND_PENDING_BIND"


def deliberately_unbound(context: dict[str, Any]) -> bool:
    """A frozen node that deliberately carries no executor, said explicitly.

    The fact is an explicit context key, never a missing or None ``executor``:
    a context-assembly bug must not become the benign unbound path.
    """
    return context.get("executor_binding") == UNBOUND_PENDING_BIND


class RulesProvider:
    def decide(self, context: dict[str, Any]) -> Decision:
        refs = tuple(context.get("evidence_refs", ()))
        for gate in HARD_GATES:
            if context.get(gate) is not True:
                return Decision(Outcome.DENY, f"hard gate not verified: {gate}", 1, refs)
        if context.get("purpose") not in PURPOSES:
            return Decision(Outcome.DENY, "unknown decision purpose", 1, refs)
        if context.get("dangerous") and context.get("operation_authorized") is not True:
            return Decision(Outcome.DENY, "dangerous operation lacks specific authority", 1, refs)
        if context.get("purpose") == "route":
            available = context.get("available_executors", [])
            preferred = context.get("executor")
            providers = context.get("executor_providers", {"codex": "codex", "claude": "claude"})
            if providers not in ({"codex": "codex", "claude": "claude"}, ISOLATED_EXECUTORS):
                return Decision(Outcome.DENY, "unsupported executor/provider binding", 1, refs)
            if not isinstance(available, (list, tuple)) or any(x not in providers for x in available):
                return Decision(Outcome.DENY, "route includes an unsupported executor", 1, refs)
            binding = context.get("executor_binding")
            if deliberately_unbound(context):
                return Decision(Outcome.ESCALATE,
                                "the frozen node is deliberately unbound; bind it before claiming", 1, refs)
            if binding is not None and binding not in providers:
                return Decision(Outcome.DENY, "unsupported executor binding fact", 1, refs)
            if preferred not in available:
                return Decision(Outcome.ESCALATE, "the assigned existing session is unavailable", 1, refs)
            return Decision(Outcome.ALLOW, "assigned existing CLI session is ready", 1, refs, preferred)
        return Decision(Outcome.ALLOW, "all required evidence gates verified", 1, refs)


class OfflineMockProvider:
    """Deterministic test input, never a TypeSafe/JEV measurement or network call."""
    def __init__(self, decisions: dict[str, Decision | Exception]):
        self.decisions = dict(decisions)

    def decide(self, context: dict[str, Any]) -> Decision:
        value = self.decisions[context["purpose"]]
        if isinstance(value, Exception):
            raise value
        return value


def decide(context: dict[str, Any], provider: DecisionProvider | None = None,
           *, minimum_confidence: float = 0.8) -> Decision:
    """Advisory exceptions fall back to rules; gates and low confidence do not."""
    if not 0 <= finite(minimum_confidence, "minimum_confidence") <= 1:
        raise ContractError("minimum_confidence must be between zero and one")
    rules = RulesProvider().decide(context)
    if rules.outcome is not Outcome.ALLOW or provider is None:
        return rules
    try:
        result = provider.decide(dict(context))
        if not isinstance(result, Decision):
            raise ContractError("advisory returned an invalid decision")
    except Exception as exc:
        return Decision(rules.outcome, f"advisory fallback ({type(exc).__name__}); {rules.reason}",
                        rules.confidence, rules.evidence_refs, rules.route, "rules_fallback")
    # The scheduler labels the actual provider. Advisory data cannot impersonate
    # rules by leaving Decision.provider at its default (or supplying a lie).
    provenance = "offline_mock" if isinstance(provider, OfflineMockProvider) else "advisory"
    result = replace(result, provider=provenance)
    if result.outcome is Outcome.ALLOW and result.confidence < minimum_confidence:
        return Decision(Outcome.ESCALATE, "advisory confidence below threshold", result.confidence,
                        result.evidence_refs, provider=result.provider)
    if result.route is not None and result.route != rules.route:
        return Decision(Outcome.DENY, "advisory cannot change the bound executor", 1, rules.evidence_refs)
    return replace(result, route=rules.route if result.outcome is Outcome.ALLOW and result.route is None else result.route)
