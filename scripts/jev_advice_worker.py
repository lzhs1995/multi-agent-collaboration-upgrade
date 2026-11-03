"""Build the one S0 advisory request; answer it only through an injected transport.

The worker owns no client. Its transport is a plain callable supplied by the
caller, so a deployment without one can only answer with the single offline
status ``jev_advice`` already defines for an unavailable advisor. No budget,
limit, key or model name is ever read from a file or an environment variable:
every input arrives as an argument, which is why this module imports nothing
but ``jev_advice`` and typing names.

The request wording, its options and the companion statement are taken verbatim
from ``jev_advice.QUESTIONS[0]``; this module never rephrases a question, adds a
status or reinterprets a failure as a semantic "no". Advice binds nothing: the
supervisor still decides, and code still recomputes every candidate lane.
"""
from __future__ import annotations

from typing import Callable

import jev_advice

SCHEMA = "jev-advice-worker-v1"
JOURNAL_SCHEMA = "jev-advice-worker-journal-v1"


def build_request(state_text: str, *, model: str) -> dict:
    """The exact S0 work-kind question, copied field by field, never rephrased."""
    if not isinstance(state_text, str) or not state_text.strip():
        raise ValueError("ORIGINAL_STATE_TEXT_REQUIRED")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("ADVISORY_MODEL_MUST_BE_NAMED")
    question = jev_advice.QUESTIONS[0]
    companion = question["companion"]
    return dict(schema=SCHEMA, model=model, state=state_text,
                question=dict(id=question["id"], type=question["type"],
                              options=tuple(question["options"])),
                companion=dict(id=companion["id"], type=companion["type"],
                               statement=companion["statement"]))


def journal_entry(request: dict | None, response: dict | None, *, elapsed_seconds: float | None,
                  http_status: int | None, model_requests: int) -> dict:
    """One self-authored worker record; it is never a server receipt.

    ``authority`` states that plainly: the worker wrote this, no service
    acknowledged it, and nothing here may be promoted into a native join. The
    pinning sha256 of the request body is added by whichever step publishes the
    journal file, because this module takes no hashing dependency.
    """
    if not isinstance(model_requests, int) or isinstance(model_requests, bool) or model_requests < 0:
        raise ValueError("MODEL_REQUEST_COUNT_REQUIRED")
    return dict(schema=JOURNAL_SCHEMA, authority="WORKER_SELF_AUTHORED",
                model=request.get("model") if isinstance(request, dict) else None,
                request=request, response=response, elapsed_seconds=elapsed_seconds,
                http_status=http_status, model_requests=model_requests)


def _answer(advice: dict, request: dict | None, response: dict | None, *,
            elapsed_seconds: float | None, http_status: int | None, model_requests: int) -> dict:
    return dict(advice=advice, request=request, model_requests=model_requests,
                journal=[journal_entry(request, response, elapsed_seconds=elapsed_seconds,
                                       http_status=http_status, model_requests=model_requests)])


def advise(*, state_text: str, lanes: dict[str, dict] | None, bound: bool,
           budget: dict | None, transport: Callable[[dict], dict] | None = None,
           model: str | None = None, now: Callable[[], float] | None = None) -> dict:
    """Advise on an unbound task, or state offline that no advisor is configured.

    ``budget`` has no default and ``transport`` defaults to ``None``: either
    missing value short-circuits before any request object is built, so a
    deployment that pinned no budget cannot accidentally reach a transport.
    """
    if bound:
        return _answer(jev_advice.route_advice(bound=True), None, None,
                       elapsed_seconds=None, http_status=None, model_requests=0)
    allowed = budget.get("max_model_requests", 0) if isinstance(budget, dict) else 0
    if transport is None or not isinstance(allowed, int) or isinstance(allowed, bool) or allowed <= 0:
        return _answer(jev_advice.route_advice(bound=False, error="NOT_CONFIGURED", lanes=lanes),
                       None, None, elapsed_seconds=None, http_status=None, model_requests=0)
    request = build_request(state_text, model=model)
    started = now() if now is not None else None
    response = transport(request)
    finished = now() if now is not None else None
    elapsed = None if started is None or finished is None else finished - started
    if not isinstance(response, dict):
        return _answer(jev_advice.route_advice(bound=False, error="malformed_response", lanes=lanes),
                       request, None, elapsed_seconds=elapsed, http_status=None, model_requests=1)
    status = response.get("http_status")
    return _answer(jev_advice.route_advice(bound=False, answers=response.get("answers"),
                                           error=response.get("error"), lanes=lanes),
                   request, response, elapsed_seconds=elapsed,
                   http_status=status if isinstance(status, int) and not isinstance(status, bool) else None,
                   model_requests=1)
