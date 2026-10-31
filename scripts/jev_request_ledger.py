"""Local, auditable ledger around the one injected JEV advisory transport.

``jev_advice_worker.advise`` already takes its transport as an argument; this
module only wraps that callable. One ledger serves a whole run and arm: the
budget and any stop are shared by every node, and each intent names its
decision and marker. Every real request first leaves an exclusive intent, then
exactly one separate result: RESPONDED, AUTH_REFUSED, MALFORMED or UNKNOWN.
Under the lock each call rebuilds its refusal from what is on disk, not from
STOP.json alone: an AUTH_REFUSED result, an intent with no result, or an
earlier intent for the same decision all refuse before any new intent. So a
dead key, a crash window or an UNKNOWN never becomes a second request.

The counts here are the worker side's own records. They are not the gateway's
billing ledger: ``ledger_summary`` reports that authority as NOT_VERIFIED and
no input can change it. No key, model, budget or route is read from a file or
an environment variable; the caller passes every value.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
import uuid

from orchestration_common import (ContractError, canonical, digest, exclusive_lock, read_regular, utc_now,
                                  write_json)

SCHEMA = "jev-request-ledger-v2"
AUTH_STATUSES = (401, 403)


def require(value, message):
    if not value:
        raise ContractError(message)


def _bind(root: Path, binding: dict) -> None:
    """One ledger directory belongs to exactly one run and arm."""
    path = root / "binding.json"
    try:
        write_json(path, binding, exclusive=True)
    except FileExistsError:
        pass
    require(json.loads(read_regular(path)) == binding, "LEDGER_BOUND_TO_ANOTHER_RUN_OR_ARM")


def _intents(root: Path) -> list[Path]:
    return sorted(root.glob("*.intent.json"))


def record_result(root: Path, key: str, value: dict) -> dict:
    """Exactly one result per request; a second write raises and keeps the first."""
    return write_json(Path(root) / (key + ".result.json"), value, exclusive=True)


def _sha256_or_none(value) -> str | None:
    try:
        return digest(canonical(value))
    except (TypeError, ValueError):
        return None


def _auth_status(exc: BaseException):
    """A 401/403 an adapter raised instead of returned still stops the arm."""
    for owner in (exc, getattr(exc, "response", None)):
        for name in ("status_code", "status", "http_status"):
            value = getattr(owner, name, None)
            if isinstance(value, int) and not isinstance(value, bool) and value in AUTH_STATUSES:
                return value
    return None


def _stop(root: Path, request_id: str, status: int) -> None:
    try:
        write_json(root / "STOP.json", dict(schema=SCHEMA, request_id=request_id, http_status=status,
                                            reason="STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER",
                                            at=utc_now()), exclusive=True)
    except FileExistsError:
        pass


def _refusal(root: Path, decision: str) -> str | None:
    """Rebuild the refusal from every persisted fact, strongest reason first."""
    stop = (root / "STOP.json").exists()
    unresolved = repeated = False
    for path in _intents(root):
        result = root / (path.name[:-len(".intent.json")] + ".result.json")
        if not result.exists():
            unresolved = True
        elif json.loads(read_regular(result)).get("status") == "AUTH_REFUSED":
            stop = True
        repeated = repeated or json.loads(read_regular(path)).get("decision") == decision
    if stop:
        return "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"
    if unresolved:
        return "UNRESOLVED_INTENT_REFUSES_NEW_REQUESTS"
    return "DECISION_ALREADY_ATTEMPTED" if repeated else None


def decision_attempted(ledger_root, decision: str) -> bool:
    """Read-only: has this decision ever reserved a request in this ledger?"""
    root = Path(ledger_root)
    return root.is_dir() and any(json.loads(read_regular(path)).get("decision") == decision
                                 for path in _intents(root))


def ledgered_transport(transport: Callable[[dict], dict], *, ledger_root, run_id: str, arm: str,
                       decision: str, marker: str, provider: str, budget: dict) -> Callable[[dict], dict]:
    """Wrap ``transport`` for one decision on the run's shared ledger."""
    require(callable(transport), "TRANSPORT_REQUIRED")
    require(arm == "advisory-on", "LEDGER_ONLY_FOR_THE_ON_ARM")
    limit = budget.get("max_model_requests") if isinstance(budget, dict) else None
    require(isinstance(limit, int) and not isinstance(limit, bool) and limit > 0, "BUDGET_NOT_POSITIVE")
    for name, value in (("run_id", run_id), ("decision", decision), ("marker", marker),
                        ("provider", provider)):
        require(isinstance(value, str) and value.strip(), name.upper() + "_REQUIRED")
    root = Path(ledger_root)
    # Run and arm wide, so every node shares one budget and one stop; the
    # decision and its marker belong to each intent instead.
    binding = dict(schema=SCHEMA, run_id=run_id, arm=arm, provider=provider, max_model_requests=limit)
    _bind(root, binding)

    def call(request: dict) -> dict:
        # One fail-fast lock spans the STOP and budget checks, the reservation,
        # the transport call and its result, so requests on one ledger are
        # serial and no call can start after another call's 401/403.
        with exclusive_lock(root / "ledger.lock"):
            return _locked_call(request)

    def _locked_call(request: dict) -> dict:
        require(json.loads(read_regular(root / "binding.json")) == binding,
                "LEDGER_BOUND_TO_ANOTHER_RUN_OR_ARM")
        refusal = _refusal(root, decision)
        if refusal is not None:
            raise ContractError(refusal)
        sequence = len(_intents(root)) + 1
        require(sequence <= limit, "MODEL_REQUEST_BUDGET_EXHAUSTED")
        # Named by sequence: even outside the lock a second writer of the same
        # reservation is refused by the exclusive write.
        key, request_id = "%06d" % sequence, uuid.uuid4().hex
        try:
            write_json(root / (key + ".intent.json"), dict(
                schema=SCHEMA, request_id=request_id, sequence=sequence, binding=binding,
                decision=decision, marker=marker,
                model=request.get("model") if isinstance(request, dict) else None,
                request_sha256=_sha256_or_none(request), created_at=utc_now()), exclusive=True)
        except FileExistsError as exc:
            raise ContractError("CONCURRENT_REQUEST_SEQUENCE_TAKEN") from exc
        try:
            response = transport(request)
        except Exception as exc:
            status = _auth_status(exc)
            record_result(root, key, dict(schema=SCHEMA, request_id=request_id, raised=True,
                                          status="UNKNOWN" if status is None else "AUTH_REFUSED",
                                          http_status=status, error=type(exc).__name__, observed_at=utc_now()))
            if status is not None:
                _stop(root, request_id, status)
            raise
        if not isinstance(response, dict):
            record_result(root, key, dict(schema=SCHEMA, request_id=request_id, status="MALFORMED",
                                          response_type=type(response).__name__, observed_at=utc_now()))
            return response
        status = response.get("http_status")
        refused = isinstance(status, int) and not isinstance(status, bool) and status in AUTH_STATUSES
        record_result(root, key, dict(
            schema=SCHEMA, request_id=request_id, status="AUTH_REFUSED" if refused else "RESPONDED",
            http_status=status, usage=response.get("usage"), response_sha256=_sha256_or_none(response),
            observed_at=utc_now()))
        if refused:
            _stop(root, request_id, status)
        return response

    return call


def ledger_summary(ledger_root) -> dict:
    """Count what this ledger itself recorded; never a gateway receipt.

    ``reserved_attempts`` is a conservative upper bound: a crash after the
    intent and before the transport may have sent nothing. Only a recorded
    transport return is an observed call, and even that is not the gateway's
    own count, which stays NOT_VERIFIED.
    """
    root = Path(ledger_root)
    statuses: dict[str, int] = {}
    raised: dict[str, int] = {}
    unresolved = []
    for path in _intents(root):
        key = path.name[:-len(".intent.json")]
        result = root / (key + ".result.json")
        if not result.exists():
            unresolved.append(key)
            continue
        value = json.loads(read_regular(result))
        status = value.get("status")
        statuses[status] = statuses.get(status, 0) + 1
        if value.get("raised") is True or status == "UNKNOWN":
            raised[status] = raised.get(status, 0) + 1
    # A returned call and a raised one are separate facts: a raised 401/403
    # stops the arm exactly like a returned one but is not a returned call.
    returned = sum(statuses.values()) - sum(raised.values())
    return dict(schema=SCHEMA + "-summary", reserved_attempts=len(_intents(root)),
                transport_returned=returned, transport_raised_auth=raised.get("AUTH_REFUSED", 0),
                transport_raised_unknown=raised.get("UNKNOWN", 0),
                intents_without_result_unknown=unresolved, results=statuses,
                stopped=(root / "STOP.json").exists() or statuses.get("AUTH_REFUSED", 0) > 0,
                stop_file_present=(root / "STOP.json").exists(), gateway_authoritative_ledger="NOT_VERIFIED")
