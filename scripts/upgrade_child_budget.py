"""Atomic whole-task accounting behind a separately authenticated native hook.

This file does not create model clients or grant action permission. Its Python
primitives require an already trusted authority reference; a caller-authored
reference is not native authorization. The production hook must independently
authenticate its immutable private configuration and actual original parent.
No CLI, TTL expiry, process-disappearance release or per-parent quota exists.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import time
import uuid

from orchestration_common import (ContractError, checked_path, contract_errors, exclusive_lock,
    json_ref, object_sha, parse_time, read_json, require, write_json)
from cmux_prompt_reference import read_body, wire_reference
from orchestration_native import NativeReader
from upgrade_body_read_evidence import body_read_evidence

AUTHORITY_SCHEMA = "upgrade-child-budget-authority-v1"
AUTHORITY_FIELDS = {"schema", "task_id", "ledger_root", "max_inflight", "budget_scope", "parents"}
LEDGER_SCHEMA = "upgrade-child-budget-ledger-v1"
MAX_CLAIMS = 256


def authority_value(ref):
    value = json_ref(ref, "whole-task child authority")
    info = checked_path(ref["path"]).lstat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o444,
            "CHILD_AUTHORITY_NOT_IMMUTABLE")
    require(set(value) == AUTHORITY_FIELDS and value["schema"] == AUTHORITY_SCHEMA
            and type(value["max_inflight"]) is int and value["max_inflight"] == 2
            and value["budget_scope"] == "WHOLE_TASK", "CHILD_AUTHORITY_FIELDS_CHANGED")
    require(isinstance(value["task_id"], str) and 0 < len(value["task_id"]) <= 128
            and not any(ord(c) < 32 for c in value["task_id"]), "CHILD_TASK_ID_INVALID")
    checked_path(value["ledger_root"])
    parents = value["parents"]
    require(isinstance(parents, dict) and set(parents) == {"claude_a", "claude_b"}
            and len(set(parents.values())) == 2, "CHILD_ORIGINAL_PARENTS_CHANGED")
    for session in parents.values():
        require(isinstance(session, str) and str(uuid.UUID(session)) == session,
                "CHILD_ORIGINAL_SESSION_INVALID")
    return value


def paths(value):
    root = checked_path(value["ledger_root"])
    return root / "ledger.json", root / "transaction.lock"


def lock_identity(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600, "CHILD_LOCK_IDENTITY_CHANGED")
    return [info.st_dev, info.st_ino]


def load_ledger(ref, value, path, lock):
    ledger = read_json(path)
    require(set(ledger) == {"schema", "authority", "lock_identity", "claims"}
            and ledger["schema"] == LEDGER_SCHEMA and ledger["authority"] == ref
            and ledger["lock_identity"] == lock_identity(lock), "CHILD_LEDGER_AUTHORITY_CHANGED")
    claims = ledger["claims"]
    require(isinstance(claims, dict) and len(claims) <= MAX_CLAIMS, "CHILD_LEDGER_CLAIMS_CHANGED")
    for claim_id, claim in claims.items():
        require(isinstance(claim, dict), "CHILD_CLAIM_CHANGED")
        base = {"parent", "session_id", "tool_use_id", "tool_input_sha256", "state"}
        extra = {"report"} if claim.get("state") in {"RESULT_READY", "CONSUMED"} else set()
        if claim.get("state") == "CONSUMED":
            extra.add("consumption")
        require(set(claim) == base | extra and claim["parent"] in value["parents"]
                and claim["session_id"] == value["parents"][claim["parent"]]
                and isinstance(claim["tool_use_id"], str) and claim["tool_use_id"]
                and isinstance(claim["tool_input_sha256"], str)
                and re.fullmatch(r"[0-9a-f]{64}", claim["tool_input_sha256"])
                and claim["state"] in {"RESERVED", "RESULT_READY", "CONSUMED"}
                and claim_id == object_sha(dict(session_id=claim["session_id"],
                                               tool_use_id=claim["tool_use_id"])),
                "CHILD_CLAIM_CHANGED")
        if "report" in claim:
            require(set(claim["report"]) == {"notice", "reference", "not_before_epoch"}
                    and wire_reference(claim["report"]["notice"]) == claim["report"]["reference"],
                    "CHILD_REPORT_BINDING_CHANGED")
            read_body(claim["report"]["reference"])
    require(inflight(ledger) <= 2, "WHOLE_TASK_CHILD_LIMIT: preserve corrupt ledger")
    require(authority_value(ref) == value, "CHILD_AUTHORITY_CHANGED_DURING_TRANSACTION")
    return ledger


def inflight(ledger):
    return sum(claim["state"] != "CONSUMED" for claim in ledger["claims"].values())


@contract_errors("initialize child budget")
def initialize_ledger(authority_ref):
    """One create-only ledger. Native publication authorization is external."""
    value = authority_value(authority_ref)
    path, lock = paths(value)
    with exclusive_lock(lock, wait_seconds=5):
        require(not os.path.lexists(path), "ORIGINAL_CHILD_LEDGER_EXISTS: never reset it")
        ledger = dict(schema=LEDGER_SCHEMA, authority=authority_ref,
                      lock_identity=lock_identity(lock), claims={})
        write_json(path, ledger, exclusive=True)
    return budget_status(authority_ref)


@contract_errors("reserve original child")
def reserve_child(authority_ref, *, parent, tool_use_id, tool_input):
    """Persist capacity before native Agent execution, not after SubagentStart."""
    value = authority_value(authority_ref)
    require(parent in value["parents"], "CHILD_PARENT_NOT_ENROLLED")
    require(isinstance(tool_use_id, str) and 0 < len(tool_use_id) <= 256
            and not any(ord(c) < 32 for c in tool_use_id), "CHILD_TOOL_USE_ID_INVALID")
    require(isinstance(tool_input, dict) and isinstance(tool_input.get("prompt"), str)
            and bool(tool_input["prompt"].strip()) and len(tool_input["prompt"].encode()) <= 65536
            and "\x00" not in tool_input["prompt"], "CHILD_NATIVE_INPUT_INVALID")
    claim_id = object_sha(dict(session_id=value["parents"][parent], tool_use_id=tool_use_id))
    claim = dict(parent=parent, session_id=value["parents"][parent], tool_use_id=tool_use_id,
                 tool_input_sha256=object_sha(tool_input), state="RESERVED")
    path, lock = paths(value)
    with exclusive_lock(lock, wait_seconds=5):
        ledger = load_ledger(authority_ref, value, path, lock)
        original = ledger["claims"].get(claim_id)
        if original is not None:
            require(all(original[key] == claim[key] for key in claim if key != "state"),
                    "ORIGINAL_CHILD_CLAIM_DIFFERENT: do not replay")
            return dict(claim_id=claim_id, status="ORIGINAL_RESERVATION", inflight=inflight(ledger))
        require(inflight(ledger) < 2, "WHOLE_TASK_CHILD_LIMIT: third unconsumed child refused")
        require(len(ledger["claims"]) < MAX_CLAIMS, "CHILD_HISTORY_BOUND_EXCEEDED")
        ledger["claims"][claim_id] = claim
        write_json(path, ledger)
        return dict(claim_id=claim_id, status="RESERVED", inflight=inflight(ledger))


@contract_errors("record original child report")
def record_child_report(authority_ref, claim_id, *, parent, notice):
    """Record pinned complete report availability; never release capacity."""
    value = authority_value(authority_ref)
    reference = wire_reference(notice)
    require(reference is not None, "CHILD_FULL_REPORT_REFERENCE_REQUIRED")
    read_body(reference)
    path, lock = paths(value)
    report = dict(notice=notice, reference=reference)
    with exclusive_lock(lock, wait_seconds=5):
        ledger = load_ledger(authority_ref, value, path, lock)
        claim = ledger["claims"].get(claim_id)
        require(claim is not None and claim["parent"] == parent, "CHILD_REPORT_PARENT_MISMATCH")
        if "report" in claim:
            require(all(claim["report"][key] == item for key, item in report.items()),
                    "ORIGINAL_CHILD_REPORT_DIFFERENT")
        else:
            report["not_before_epoch"] = time.time()
            claim.update(state="RESULT_READY", report=report)
            write_json(path, ledger)
        return dict(claim_id=claim_id, status="RESULT_READY_NOT_CONSUMED", report=reference,
                    inflight=inflight(ledger))


@contract_errors("consume original child report")
def consume_child_report(authority_ref, claim_id, *, parent, parent_session, command,
                         reply_sha256, consumption_call=None, fixtures=False):
    """Release once after a real source-bound complete original-parent read.

    The native service supplies the enrolled session and exact frozen reader
    command. Synthetic journal mode exists only at this Python test boundary;
    no publishing or automatic hook CLI may expose it.
    """
    value = authority_value(authority_ref)
    require(parent in value["parents"] and parent_session.get("provider") == "claude"
            and parent_session.get("session_id") == value["parents"][parent],
            "CHILD_CONSUMER_NOT_ORIGINAL_PARENT")
    path, lock = paths(value)
    with exclusive_lock(lock, wait_seconds=5):
        ledger = load_ledger(authority_ref, value, path, lock)
        claim = ledger["claims"].get(claim_id)
        require(claim is not None and claim["parent"] == parent and "report" in claim,
                "CHILD_REPORT_NOT_READY_FOR_ORIGINAL_PARENT")
        report = claim["report"]
        require(reply_sha256 == report["reference"]["sha256"], "CHILD_REPORT_SHA_MISMATCH")
        reader = NativeReader(parent_session, fixtures=fixtures)
        end = reader.snapshot()
        read_end, not_after = end["offset"], time.time()
        if consumption_call is not None:
            # The service already authenticated this unique original Bash
            # invocation. A correct SHA must not borrow a later native read,
            # even when its timestamp has been copied or rounded backward.
            native_call = reader.record(consumption_call)
            not_after = parse_time(native_call["timestamp"]).timestamp()
            require(not_after <= time.time()
                    and consumption_call["offset"] + consumption_call["length"] <= end["offset"],
                    "ORIGINAL_NATIVE_CHILD_CONSUMPTION_REQUIRED: future or incomplete call")
            read_end = consumption_call["offset"]
        records, scanned = [], 0
        for ref, record in reader.records(0, read_end):
            scanned += ref["length"]
            require(len(records) < 65536 and scanned <= 32 * 1024 * 1024,
                    "CHILD_PARENT_READ_WINDOW_PENDING: original interval exceeds bound")
            records.append((ref, record))
        evidence = body_read_evidence(records, provider="claude", session_id=parent_session["session_id"],
            notice=report["notice"], command=command, not_before=report["not_before_epoch"],
            not_after=not_after)
        require(evidence, "ORIGINAL_PARENT_FULL_READ_REQUIRED: correct SHA alone cannot consume")
        observed = evidence[0]
        reader.record(observed["call_record"])
        reader.record(observed["result_record"])
        reader.verify_pin(end)
        if consumption_call is not None:
            reader.record(consumption_call)
        read_body(report["reference"])
        require(authority_value(authority_ref) == value, "CHILD_AUTHORITY_CHANGED_DURING_TRANSACTION")
        if claim["state"] == "CONSUMED":
            return dict(claim_id=claim_id, status="ORIGINAL_CONSUMPTION", inflight=inflight(ledger))
        claim.update(state="CONSUMED", consumption=observed)
        write_json(path, ledger)
        return dict(claim_id=claim_id, status="CONSUMED", inflight=inflight(ledger))


@contract_errors("observe child budget")
def budget_status(authority_ref):
    value = authority_value(authority_ref)
    path, lock = paths(value)
    with exclusive_lock(lock, wait_seconds=5):
        ledger = load_ledger(authority_ref, value, path, lock)
        return dict(task_id=value["task_id"], budget_scope="WHOLE_TASK", max_inflight=2,
                    inflight=inflight(ledger), claims=ledger["claims"],
                    model_requests=0, input_operations=0, native_adoption_proven=False)
