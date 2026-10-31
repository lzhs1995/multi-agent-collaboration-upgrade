"""Default-off, single-attempt transport entry for the JEV advisory worker.

``jev_advice_worker.advise`` takes its transport as an argument. This module
only builds that argument. With no ``send`` there is no transport, so the
default is zero calls and no network. With an injected ``send`` each request
makes exactly one attempt: nothing here retries, sleeps or falls back, and an
exception from ``send`` propagates unchanged so ``jev_request_ledger`` records
it as UNKNOWN.

Nothing is read from a file or an environment variable: no key, model,
endpoint, budget or route. A caller that adopts an SDK client inside ``send``
must disable that client's own retries (the TypeSafe SDK defaults to
``max_retries=2``) and reuse one client rather than building one per request.
"""
from __future__ import annotations

from typing import Callable

from jev_request_ledger import AUTH_STATUSES
from orchestration_common import ContractError


def build_transport(send: Callable[[dict], dict] | None, *, model: str,
                    parse: Callable[[dict], dict]) -> Callable[[dict], dict] | None:
    """Return a one-attempt transport, or None when no sender was injected."""
    if send is None:
        return None
    if not callable(send) or not callable(parse):
        raise ContractError("TRANSPORT_SEND_AND_PARSE_MUST_BE_CALLABLE")
    if not isinstance(model, str) or not model.strip():
        raise ContractError("TRANSPORT_MODEL_REQUIRED")

    def transport(request: dict) -> dict:
        response = send(dict(request, model=model))
        if not isinstance(response, dict):
            return response
        status = response.get("http_status")
        if isinstance(status, int) and not isinstance(status, bool) and status in AUTH_STATUSES:
            # The sender's known 401/403 reaches the ledger's STOP before, and
            # regardless of, any parsing of its error body.
            return dict(answers=None, error="AUTH_REFUSED", usage=response.get("usage"), http_status=status)
        # parse maps the provider's reply onto the worker's shape. A parse
        # failure on any other reply propagates and is recorded as UNKNOWN.
        value = parse(response)
        if not isinstance(value, dict):
            return value
        return dict(answers=value.get("answers"), error=value.get("error"), usage=value.get("usage"),
                    http_status=response.get("http_status", value.get("http_status")))

    return transport
