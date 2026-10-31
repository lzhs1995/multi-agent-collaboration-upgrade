"""DP2 inbox priority for the JEV advisory on arm (R505).

Advice only reorders code-verified eligible NEW messages; it never drops,
merges or skips a body. Code owns eligibility, identity, permission and the
full-read proof: each body must match its pinned sha256 and byte count before
it is judged. The off arm, an unconfigured on arm, an uncertain or malformed
answer, a stop and an UNKNOWN all keep the original arrival order. Each new
body is asked once (deduplicated by sha256) and both inbox questions travel in
that one request; a body already judged is reused, never asked again.
"""
from __future__ import annotations

import hashlib
from typing import Callable

import jev_advice

SCHEMA = "jev-inbox-order-v1"
REQUEST_SCHEMA = "jev-inbox-request-v1"


def _require(value, message):
    if not value:
        raise ValueError("JEV_INBOX: " + message)


def build_request(body: str, *, model: str) -> dict:
    """The exact DP2 question from jev_advice.QUESTIONS[1], copied field by field."""
    _require(isinstance(body, str) and body.strip(), "full message body required")
    _require(isinstance(model, str) and model.strip(), "advisory model must be named")
    question = jev_advice.QUESTIONS[1]
    companion = question["companion"]
    return dict(schema=REQUEST_SCHEMA, model=model, state=body,
                question=dict(id=question["id"], type=question["type"], options=tuple(question["options"])),
                companion=dict(id=companion["id"], type=companion["type"], statement=companion["statement"]))


def _verified(message: dict) -> str:
    """Full-read proof: the body read is exactly the pinned bytes."""
    body = message.get("body")
    _require(isinstance(body, str), "message body must be read in full: %s" % message.get("id"))
    raw = body.encode()
    digest = hashlib.sha256(raw).hexdigest()
    _require(digest == message.get("sha256") and len(raw) == message.get("bytes"),
             "body does not match its pinned sha256/bytes: %s" % message.get("id"))
    return digest


def order_inbox(messages: list, *, arm: str, transport_for: Callable[[str], Callable] | None = None,
                model: str | None = None, judged: dict | None = None) -> dict:
    """Return the reading order and one decision record per eligible message.

    ``messages`` are in original arrival order; each has id, body, sha256,
    bytes and eligible (decided by code). ``transport_for(decision)`` returns
    the one-attempt ledgered transport for that decision. ``judged`` maps a
    body sha256 to advice already published, which is reused, not re-asked.
    ``model_requests`` counts replies received; the ledger stays the
    authority for reserved attempts, including UNKNOWN ones.
    """
    _require(arm in ("advisory-off", "advisory-on"), "unknown arm")
    ids = [m.get("id") for m in messages]
    _require(all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids), "unique message ids required")
    digests = [_verified(m) for m in messages]
    eligible = [i for i, m in enumerate(messages) if m.get("eligible") is True]
    configured = arm == "advisory-on" and transport_for is not None and isinstance(model, str) and bool(model.strip())
    judged, advice_by_sha, records = dict(judged or {}), {}, {}
    requests, stopped, usage = 0, None, dict(input_tokens=0, output_tokens=0)
    for i in eligible:
        digest = digests[i]
        record = dict(id=ids[i], sha256=digest, bytes=messages[i]["bytes"], requests=0)
        if digest in advice_by_sha:
            record.update(status="DUPLICATE_BODY", front=advice_by_sha[digest].get("front") is True)
        elif digest in judged:
            advice_by_sha[digest] = judged[digest]
            record.update(status="REUSED", front=judged[digest].get("front") is True)
        elif not configured or stopped is not None:
            advice_by_sha[digest] = dict(front=False)
            status = "OFF_ARM" if arm == "advisory-off" else "NOT_CONFIGURED" if not configured else "NOT_ASKED_AFTER_STOP"
            record.update(status=status, front=False)
        else:
            try:
                response = transport_for("dp2:" + digest)(build_request(messages[i]["body"], model=model))
            except Exception as exc:  # the ledger has recorded UNKNOWN/AUTH, or refused before sending
                stopped = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                advice_by_sha[digest] = dict(front=False)
                record.update(status="UNKNOWN_OR_REFUSED", front=False, error=stopped)
                records[i] = record
                continue
            requests += 1
            if isinstance(response, dict):
                advice = jev_advice.inbox_advice(answers=response.get("answers"), error=response.get("error"))
                for key in usage:
                    value = (response.get("usage") or {}).get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage[key] += value
                if response.get("error") == "AUTH_REFUSED":
                    stopped = "AUTH_REFUSED_%s" % response.get("http_status")
                record["http_status"] = response.get("http_status")
            else:
                advice = jev_advice.inbox_advice(error="malformed_response")
            advice_by_sha[digest] = advice
            record.update(status=advice["status"], front=advice["front"] is True, requests=1)
        records[i] = record
    front = [i for i in eligible if records[i]["front"]]
    slots = iter(front + [i for i in eligible if i not in front])
    order = [next(slots) if i in eligible else i for i in range(len(messages))]
    after = {index: position for position, index in enumerate(order)}
    decisions = [dict(records[i], position_before=i, position_after=after[i]) for i in eligible]
    return dict(schema=SCHEMA, arm=arm, original_order=ids, order=[ids[i] for i in order],
                reordered=order != list(range(len(messages))), decisions=decisions,
                model_requests=requests, usage=usage, stopped=stopped, full_read_required=True)
