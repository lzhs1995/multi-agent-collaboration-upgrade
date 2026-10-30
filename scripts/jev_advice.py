"""Offline JEV triage truth table (S0). Pure code: no network, no SDK, no key.

JEV may only judge language before a new task pack is frozen and bound. Code
decides everything else, and advice never binds or changes an executor. The
question wording, options and thresholds live here so they are audited in one
place (OWNER43-JEV-SELECTION-SPEC-v1 questions_v1/thresholds).
"""
from __future__ import annotations

import math
from typing import Any

YES, NO = 0.7, 0.3  # Noul starting thresholds; confidence is never a gate.

WORK_KINDS = ("coding", "debugging", "review", "literature")
MESSAGE_KINDS = ("result", "request", "dependency_update")
QUESTIONS = (
    dict(id="work_kind", type="Choice", options=WORK_KINDS,
         companion=dict(id="work_kind_applies", type="Noul",
                        statement="`task` 描述的是一项需要分配给执行者的具体工作")),
    dict(id="message_kind", type="Choice", options=MESSAGE_KINDS,
         companion=dict(id="message_actionable", type="Noul",
                        statement="`message` 包含收件人现在可以执行的具体新内容")),
)
CODEX_KINDS, CLAUDE_KINDS = {"coding", "debugging"}, {"review", "literature"}
STATUSES = {"NOT_CALLED", "NOT_CONFIGURED", "JEV_ERROR", "NOT_APPLICABLE",
            "UNSETTLED", "SUGGEST", "NO_IDLE_LANE"}


def _probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if 0 <= value <= 1 else None


def _advice(status, reason, candidates=(), fallback=None):
    return dict(status=status, reason=reason, candidates=tuple(candidates), fallback=fallback)


def _failure(answers, error, top, top_options, companion):
    """Any failed or malformed answer is an error, never a 'no'."""
    if error is not None:
        status = "NOT_CONFIGURED" if error == "NOT_CONFIGURED" else "JEV_ERROR"
        return _advice(status, f"JEV unavailable ({error}); rules decide", fallback="rules")
    if not isinstance(answers, dict):
        return _advice("JEV_ERROR", "JEV answers missing; rules decide", fallback="rules")
    if answers.get(top) not in top_options:
        return _advice("JEV_ERROR", f"JEV answer {top} missing or not an offered option; rules decide",
                       fallback="rules")
    if _probability(answers.get(companion)) is None:
        return _advice("JEV_ERROR", f"JEV answer {companion} is not a probability; rules decide",
                       fallback="rules")
    return None


def route_advice(*, bound: bool, answers: dict | None = None, error: str | None = None,
                 lanes: dict[str, dict] | None = None) -> dict:
    """Routing suggestion for an unbound task; the supervisor still chooses.

    lanes maps an existing CLI lane to scheduler facts measured by code:
    kind ("codex"/"claude"), verified, idle_write_domain and author.
    """
    if bound:
        return _advice("NOT_CALLED", "task already bound; JEV is not asked")
    failed = _failure(answers, error, "work_kind", WORK_KINDS, "work_kind_applies")
    if failed:
        return failed
    applies = _probability(answers["work_kind_applies"])
    if applies < NO:
        return _advice("NOT_APPLICABLE", "not a concrete work item; rules decide", fallback="rules")
    if applies <= YES:
        return _advice("UNSETTLED", "work-item judgment unsettled; supervisor decides", fallback="supervisor")
    # Every candidate must be verified available, not only codex as v1 row 4
    # states; no candidate at all is an explicit supervisor row.
    lanes = lanes or {}
    idle = lambda kind, **extra: sorted(
        name for name, lane in lanes.items()
        if lane.get("kind") == kind and lane.get("verified") is True and lane.get("idle_write_domain") is True
        and all(lane.get(k) is v for k, v in extra.items()))
    kind = answers["work_kind"]
    if kind in CODEX_KINDS:
        candidates = idle("codex") or idle("claude")
    else:
        candidates = idle("claude", author=False)
    if not candidates:
        return _advice("NO_IDLE_LANE", f"{kind}: no verified lane with an idle write domain", fallback="supervisor")
    return _advice("SUGGEST", f"{kind}: verified idle lane(s)", candidates)


def inbox_advice(*, answers: dict | None = None, error: str | None = None) -> dict:
    """Inbox ordering only; every message body is still read in full."""
    failed = _failure(answers, error, "message_kind", MESSAGE_KINDS, "message_actionable")
    if failed:
        return dict(failed, front=False, full_read_required=True)
    actionable = _probability(answers["message_actionable"])
    front = actionable > YES
    status = "SUGGEST" if front else "UNSETTLED" if actionable >= NO else "NOT_APPLICABLE"
    return dict(_advice(status, "actionable now" if front else "original order kept"),
                front=front, full_read_required=True)
