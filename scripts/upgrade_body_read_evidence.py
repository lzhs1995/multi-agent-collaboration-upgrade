"""Pure matching inside an already authenticated, bounded receiver journal read.

The caller authenticates session metadata, original input intent/EOF fence,
native file identity and every record pin. This module never reads files, runs
commands, or turns a tool result into a native notice/business receipt.
Unrecognized wrappers and yielded results remain UNKNOWN, not successful reads.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re

from cmux_prompt_reference import wire_reference
from upgrade_body_read import SCHEMA, SCOPE

RTK_NOTICE = "[rtk] /!\\ No hook installed — run `rtk init -g` for automatic token savings\n"


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))


def stamp(record):
    value = record.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        date = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def valid_command(name, arguments, command):
    """Admit only a direct tool invocation, never arbitrary JavaScript/shell."""
    if name in ("exec", "functions.exec"):
        if not isinstance(arguments, str):
            return False
        match = re.fullmatch(r"\s*text\(\s*await tools\.exec_command\((\{.*\})\)\s*\);?\s*",
                             arguments, re.S)
        if match is None:
            return False
        arguments = strict_json(match[1])
        name = "exec_command"
    elif isinstance(arguments, str):
        arguments = strict_json(arguments)
    if not isinstance(arguments, dict):
        return False
    if name in ("exec_command", "functions.exec_command"):
        allowed = {"cmd", "login", "workdir", "yield_time_ms", "max_output_tokens"}
        return (set(arguments) <= allowed and arguments.get("cmd") == command
                and arguments.get("login") is False)
    if name == "Bash":
        return (set(arguments) <= {"command", "description", "timeout", "run_in_background"}
                and arguments.get("command") == command
                and arguments.get("run_in_background", False) is False)
    return False


def calls_and_results(record, provider):
    if provider == "codex" and record.get("type") == "response_item":
        p = record.get("payload", {})
        if not isinstance(p, dict):
            return [], []
        if p.get("type") in ("function_call", "custom_tool_call"):
            return [(p.get("call_id"), p.get("name"), p.get("arguments", p.get("input")))], []
        if p.get("type") in ("function_call_output", "custom_tool_call_output"):
            return [], [(p.get("call_id"), p.get("output"), False)]
    if provider == "claude" and record.get("type") in ("assistant", "user"):
        msg = record.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != record["type"]:
            return [], []
        blocks = msg.get("content", [])
        if not isinstance(blocks, list):
            return [], []
        calls, results = [], []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if record["type"] == "assistant" and b.get("type") == "tool_use":
                calls.append((b.get("id"), b.get("name"), b.get("input")))
            if record["type"] == "user" and b.get("type") == "tool_result":
                results.append((b.get("tool_use_id"), b.get("content"), b.get("is_error") is not False))
        return calls, results
    return [], []


def codex_stdout(value):
    if isinstance(value, list):
        # Measured functions.exec envelope: a completion header and one text(r).
        if (len(value) != 2 or any(not isinstance(v, dict) or v.get("type") != "input_text"
                                  or set(v) != {"type", "text"} for v in value)
                or re.fullmatch(r"Script completed\nWall time [0-9.]+ seconds\nOutput:\n",
                                value[0]["text"]) is None):
            return None
        value = value[1]["text"]
    if isinstance(value, str):
        try:
            value = strict_json(value)
        except (ValueError, TypeError):
            # Direct exec_command has a separate, measured terminal envelope.
            match = re.fullmatch(r"(?:Chunk ID: [^\n]+\n)?Wall time: [0-9.]+ seconds\n"
                r"Process exited with code 0\n(?:Final output|Output):\n([\s\S]*)", value)
            return match[1] if match else None
    if not isinstance(value, dict) or type(value.get("exit_code")) is not int or value["exit_code"] != 0:
        return None
    if value.get("session_id") is not None or value.get("cell_id") is not None:
        return None
    if set(value) - {"exit_code", "output", "chunk_id", "wall_time_seconds", "original_token_count", "session_id"}:
        return None
    return value.get("output") if isinstance(value.get("output"), str) else None


def exact_body_output(value, provider, notice, reference):
    stdout = codex_stdout(value) if provider == "codex" else value
    if isinstance(stdout, list) and provider == "claude":
        if len(stdout) != 1 or not isinstance(stdout[0], dict) or set(stdout[0]) != {"type", "text"} or stdout[0]["type"] != "text":
            return False
        stdout = stdout[0]["text"]
    if not isinstance(stdout, str):
        return False
    if stdout.startswith(RTK_NOTICE):
        stdout = stdout[len(RTK_NOTICE):]
    result = strict_json(stdout)
    if (not isinstance(result, dict) or set(result) != {"schema", "scope", "notice", "reference", "body"}
            or result.get("schema") != SCHEMA or result.get("scope") != SCOPE
            or result.get("notice") != notice or result.get("reference") != reference
            or not isinstance(result.get("body"), str)):
        return False
    raw = result["body"].encode("utf-8")
    return len(raw) == reference["bytes"] and hashlib.sha256(raw).hexdigest() == reference["sha256"]


def body_read_evidence(records, *, provider, session_id, notice, command, not_before, not_after=None):
    """Return paired exact records; expose bytes without asserting understanding."""
    if (provider not in ("codex", "claude") or not isinstance(session_id, str) or not session_id
            or type(not_before) not in (int, float) or not math.isfinite(not_before)
            or (not_after is not None and (type(not_after) not in (int, float)
                or not math.isfinite(not_after) or not_after < not_before))):
        return []
    try:
        reference = wire_reference(notice)
    except (ValueError, TypeError):
        return []
    if reference is None:
        return []
    calls, results = {}, {}
    threshold = math.floor(not_before * 1000) / 1000
    previous_end = -1
    for pin, record in records:
        if not isinstance(pin, dict) or type(pin.get("offset")) is not int or type(pin.get("length")) is not int or pin["length"] <= 0 or pin["offset"] < previous_end:
            return []
        previous_end = pin["offset"] + pin["length"]
        if not isinstance(record, dict):
            continue
        if provider == "claude" and (record.get("sessionId") != session_id or record.get("isSidechain") is True):
            continue
        when = stamp(record)
        time_valid = when is not None and when >= threshold and (not_after is None or when <= not_after)
        cs, rs = calls_and_results(record, provider)
        for call_id, name, arguments in cs:
            if isinstance(call_id, str) and call_id:
                try:
                    valid = valid_command(name, arguments, command)
                except (ValueError, TypeError):
                    valid = False
                kind = record.get("payload", {}).get("type") if provider == "codex" else "tool_use"
                calls.setdefault(call_id, []).append((pin, record.get("timestamp"), when, valid and time_valid, kind))
        for call_id, value, error in rs:
            if isinstance(call_id, str) and call_id:
                try:
                    valid = not error and exact_body_output(value, provider, notice, reference)
                except (ValueError, TypeError, UnicodeError):
                    valid = False
                kind = record.get("payload", {}).get("type") if provider == "codex" else "tool_result"
                results.setdefault(call_id, []).append((pin, record.get("timestamp"), when, valid and time_valid, kind))
    evidence = []
    for call_id, call_list in calls.items():
        result_list = results.get(call_id, [])
        if len(call_list) != 1 or len(result_list) != 1:
            continue
        call, result = call_list[0], result_list[0]
        expected_kind = {"function_call": "function_call_output", "custom_tool_call": "custom_tool_call_output",
                         "tool_use": "tool_result"}.get(call[4])
        if not (call[3] and result[3] and result[4] == expected_kind
                and result[0]["offset"] >= call[0]["offset"] + call[0]["length"]
                and result[2] >= call[2]):
            continue
        evidence.append(dict(kind="BODY_READ_OBSERVED", scope=SCOPE, session_id=session_id,
            call_id=call_id, call_record=call[0], result_record=result[0],
            call_timestamp=call[1], timestamp=result[1], reference=reference))
    return evidence
