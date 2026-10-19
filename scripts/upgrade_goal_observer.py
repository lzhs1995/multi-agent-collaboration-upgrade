#!/usr/bin/env python3
"""Observe Claude Goal records in a bounded original-journal interval.

No input, Goal control, receipt publication or business acceptance is performed.
Only native attachment.goal_status is interpreted. Binary format discovery is
not runtime evidence; even repeated checks cannot prove an unobserved interval
had no pause. Production CLI has no fixture/copy-transcript bypass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import itertools
from pathlib import Path

from orchestration_common import ContractError, count, digest, parse_time, read_json, require
from orchestration_native import NativeReader

SCHEMA = "upgrade-goal-observation-v1"
MAX_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_EVENTS = 4096


def goal_event(record):
    """Inspect native structure only; quoted JSON/prompt/tool text is not state."""
    if record.get("isSidechain") is True or record.get("type") != "attachment":
        return None
    body = record.get("attachment")
    if not isinstance(body, dict) or body.get("type") != "goal_status":
        return None
    require(type(body.get("met")) is bool, "Goal met must be boolean")
    for key in ("failed", "sentinel"):
        require(key not in body or type(body[key]) is bool, "Goal flag must be boolean")
    require(not (body["met"] and body.get("failed")), "Goal cannot be met and failed")
    condition = body.get("condition")
    # Native clear may have an absent/null condition; active state may not.
    require(condition is None or isinstance(condition, str), "Goal condition must be text")
    inactive = body["met"] or body.get("failed") is True
    require(inactive or bool(condition), "Active Goal condition missing")
    require("reason" not in body or isinstance(body["reason"], str), "Goal reason must be text")
    if "iterations" in body:
        count(body["iterations"], "Goal iterations")
    parse_time(record.get("timestamp"), "Goal timestamp")
    if body.get("sentinel") is True:
        kind = "CLEARED_OR_MET" if inactive else "SET"
    elif body.get("failed") is True:
        kind = "FAILED"
    elif body["met"]:
        kind = "MET"
    elif isinstance(body.get("reason"), str) and body["reason"]:
        kind = "UNMET_CHECK"
    else:
        kind = "ACTIVE_STATUS"
    return dict(kind=kind, condition_sha256=digest(condition.encode()) if condition is not None else None,
                timestamp=record["timestamp"], inactive=inactive)


def assistant_activity(record):
    """A real model record after an unmet check; does not infer wake causation."""
    message = record.get("message")
    return (record.get("type") == "assistant" and record.get("isSidechain") is not True
            and record.get("isApiErrorMessage") is not True and isinstance(message, dict)
            and message.get("role") == "assistant" and isinstance(message.get("model"), str)
            and message["model"] not in ("", "<synthetic>"))


def observe(reader, start, end, condition, *, max_bytes=MAX_BYTES, max_records=MAX_RECORDS,
            max_events=MAX_EVENTS):
    require(reader.provider == "claude", "Only Claude native Goal format is supported")
    require(isinstance(condition, str) and bool(condition), "Expected exact Goal condition required")
    for value, maximum in ((max_bytes, MAX_BYTES), (max_records, MAX_RECORDS), (max_events, MAX_EVENTS)):
        require(0 < count(value, "observation limit") <= maximum, "Observation limit out of range")
    lo, hi = reader.verify_pin(start), reader.verify_pin(end)
    require(lo <= hi, "Goal interval moved backwards")
    expected = digest(condition.encode())
    events, after_checks, uncertainties = [], [], []
    state, pending_check, previous_time = "UNKNOWN", None, None
    counts = dict(records=0, bytes=0, sets=0, unmet_checks=0, model_records_after_check=0)
    through, seen_ids = lo, set()
    evidence_digest = hashlib.sha256()
    # Refuse an oversized interval before parsing any of its records. islice
    # also avoids fetching an extra record beyond the record-count budget.
    if hi - lo > max_bytes:
        uncertainties.append("SCAN_BUDGET_EXHAUSTED")
        records = iter(())
    else:
        records = itertools.islice(reader.records(lo, hi), max_records)
    for ref, record in records:
        counts["records"] += 1
        counts["bytes"] += ref["length"]
        through = ref["offset"] + ref["length"]
        evidence_digest.update(bytes.fromhex(ref["sha256"]))
        # Wire active_goal is a different channel/schema, not a JSONL attachment.
        if record.get("type") == "active_goal":
            uncertainties.append("UNSUPPORTED_ACTIVE_GOAL_TRANSPORT_RECORD")
            state, pending_check = "UNKNOWN", None
            continue
        event = goal_event(record)
        if event is not None:
            stamp = parse_time(event["timestamp"])
            require(previous_time is None or stamp >= previous_time, "Goal chronology moved backwards")
            previous_time = stamp
            event_id = record.get("uuid")
            require(isinstance(event_id, str) and bool(event_id), "Goal native record UUID missing")
            require(event_id not in seen_ids, "Duplicate Goal native record UUID")
            seen_ids.add(event_id)
            same = event["condition_sha256"] == expected
            event.update(record=ref, expected_condition=same)
            events.append(event)
            pending_check = None
            if event["inactive"]:
                state = "INACTIVE_OBSERVED"
            elif not same:
                state = "OTHER_GOAL_OBSERVED"
            else:
                state = "EXPECTED_GOAL_OBSERVED"
                if event["kind"] == "SET":
                    counts["sets"] += 1
                if event["kind"] == "UNMET_CHECK":
                    counts["unmet_checks"] += 1
                    pending_check = event
        elif pending_check is not None and assistant_activity(record):
            stamp = parse_time(record.get("timestamp"), "model record timestamp")
            require(stamp >= parse_time(pending_check["timestamp"]), "Model activity precedes Goal check")
            after_checks.append(dict(check=pending_check["record"], model_record=ref,
                                     timestamp=record["timestamp"]))
            counts["model_records_after_check"] += 1
            previous_time = stamp
            pending_check = None
        if len(events) + len(after_checks) >= max_events:
            break
    if through != hi and "SCAN_BUDGET_EXHAUSTED" not in uncertainties:
        uncertainties.append("SCAN_BUDGET_EXHAUSTED")
    # Appends beyond the chosen EOF are allowed; original fence changes are not.
    reader.verify_metadata()
    reader.verify_pin(start)
    reader.verify_pin(end)
    complete = through == hi and not uncertainties
    return dict(schema=SCHEMA, observation_only=True, provider="claude",
                session_id=reader.session["session_id"], condition_sha256=expected,
                start=start, end=end, scanned_through=through, coverage_complete=complete,
                scanned_record_hashes_sha256=evidence_digest.hexdigest(), counts=counts,
                events=events, model_activity_after_unmet_check=after_checks,
                last_observed_state=state, state_at_interval_end="UNKNOWN",
                uncertainties=uncertainties, input_operations=0, model_calls=0,
                continuous_goal_proven=False, communication_acceptance="NOT_EVALUATED",
                business_acceptance="NOT_EVALUATED", limitations=[
                    "Goal attachment observations are discrete state evidence, not proof of uninterrupted activity.",
                    "A later model record does not prove automatic wake causation or useful business work.",
                    "No command delivery, pause absence, current live process, or continuous-goal acceptance is inferred.",
                    "Unsupported, incomplete and unobserved intervals remain UNKNOWN; no copied journal is native evidence.",
                ])


def collect(request):
    require(request.get("schema") == "upgrade-goal-observation-request-v1", "Goal request schema mismatch")
    # Authentication retains the original NativeReader's private-runtime checks.
    reader = NativeReader(request["session"])
    return observe(reader, request["start"], request["end"], request["condition"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = collect(read_json(args.request))
    except (ContractError, KeyError, TypeError, OSError) as exc:
        print(json.dumps(dict(schema=SCHEMA, observation_only=True, status="UNKNOWN",
                              error=str(exc), input_operations=0, model_calls=0)))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
