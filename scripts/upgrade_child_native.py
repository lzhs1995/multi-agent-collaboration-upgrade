"""Source-bound original native child observations; never launches a child.

Production callers supply an already authenticated original-parent enrollment.
The hook's session label alone is never enrollment or native tool authority.
Synthetic mode is confined to the Python test boundary, not a hook/CLI option.
"""
from __future__ import annotations

import re
import time

from orchestration_common import checked_path, contract_errors, digest, object_sha, parse_time, require
from orchestration_native import NativeReader, native_text
from upgrade_child_budget_protocol import child_prompt, updated_agent_input
from upgrade_native_outcomes import claude_outcomes

MAX_RECORDS = 65536
MAX_BYTES = 32 * 1024 * 1024


@contract_errors("original native Agent call")
def original_agent_call(session, payload, *, fixtures=False):
    """Bind one actual post-enrollment parent tool_use to its PreToolUse hook.

    Read the fixed original interval again before returning. Copied journals,
    sidechain records, quoted calls, ambiguous IDs and changed tool inputs are
    not authority to reserve whole-task capacity.
    """
    require(isinstance(payload, dict) and session["provider"] == "claude"
            and payload.get("hook_event_name") == "PreToolUse"
            and payload.get("session_id") == session["session_id"]
            and payload.get("transcript_path") == session["transcript"]["path"]
            and payload.get("tool_name") == "Agent"
            and payload.get("agent_id") is None,
            "ORIGINAL_NATIVE_AGENT_CALL_REQUIRED: original root parent only")
    tool_id, tool_input = payload.get("tool_use_id"), payload.get("tool_input")
    require(isinstance(tool_id, str) and 0 < len(tool_id) <= 256
            and not any(ord(c) < 32 for c in tool_id), "CHILD_TOOL_USE_ID_INVALID")
    # Validate the complete supported native input, without changing it here.
    updated_agent_input(tool_input, "0" * 64)
    reader = NativeReader(session, fixtures=fixtures)
    start = reader.verify_pin(session["freeze_fence"])
    end = reader.snapshot()
    require(end["offset"] - start <= MAX_BYTES,
            "CHILD_NATIVE_CALL_WINDOW_PENDING: original interval exceeds bound")
    matches, scanned = [], 0
    for ref, record in reader.records(start, end["offset"]):
        scanned += 1
        require(scanned <= MAX_RECORDS, "CHILD_NATIVE_CALL_WINDOW_PENDING: too many records")
        if record.get("type") != "assistant" or record.get("isSidechain") is True:
            continue
        message = record.get("message", {})
        require(isinstance(message, dict), "CHILD_NATIVE_ASSISTANT_CHANGED")
        content = message.get("content", [])
        require(isinstance(content, list), "CHILD_NATIVE_ASSISTANT_CONTENT_CHANGED")
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("id") != tool_id:
                continue
            require(record.get("sessionId") == session["session_id"]
                    and message.get("role") == "assistant" and block.get("name") == "Agent"
                    and block.get("input") == tool_input,
                    "ORIGINAL_NATIVE_AGENT_CALL_REQUIRED: native invocation differs")
            require(parse_time(record["timestamp"]).timestamp() <= time.time(),
                    "ORIGINAL_NATIVE_AGENT_CALL_REQUIRED: future native invocation")
            matches.append(dict(call_record=ref, tool_use_id=tool_id, tool_input=block["input"]))
    require(len(matches) == 1, "ORIGINAL_NATIVE_AGENT_CALL_REQUIRED: missing or duplicate native invocation")
    reader.record(matches[0]["call_record"])
    reader.verify_pin(session["freeze_fence"])
    reader.verify_pin(end)
    return matches[0]


@contract_errors("current original native Agent creation")
def current_original_agent_call(session, payload, *, fixtures=False):
    """Require the current API tool-use response, not a call before failure.

    Only new creation uses this freshness gate. Historical reports and exact
    parent consumption continue to authenticate their original call separately.
    A hook label or stale tool-call record cannot prove current API execution.
    """
    call = original_agent_call(session, payload, fixtures=fixtures)
    reader = NativeReader(session, fixtures=fixtures)
    end = reader.snapshot()
    # Reused original parents can have a large pre-enrollment history. Read
    # only a bounded current tail, aligned to an original whole-record edge.
    # Do not manufacture ancestry at that edge: the same parser must still
    # find the actual user root and qualify the exact Agent content block.
    start = max(0, end["offset"] - MAX_BYTES)
    if start:
        with reader.open() as stream:
            stream.seek(start - 1)
            if stream.read(1) != b"\n":
                partial_prefix = stream.readline(end["offset"] - start)
                require(partial_prefix.endswith(b"\n"), "CHILD_NATIVE_CURRENT_INTERVAL_PENDING")
                start = stream.tell()
    records = []
    for pair in reader.records(start, end["offset"]):
        require(len(records) < MAX_RECORDS, "CHILD_NATIVE_CURRENT_INTERVAL_PENDING")
        records.append(pair)
    now = time.time()
    outcomes = claude_outcomes(session, records, "current-original-agent-call", now)
    blocks = claude_outcomes(session, records, "current-original-agent-call", now,
                             deduplicate_messages=False)
    message_id = reader.record(call["call_record"]).get("message", {}).get("id")
    require(outcomes and outcomes[-1]["kind"] == "API_SUCCESS"
            and isinstance(message_id, str) and message_id.startswith("msg_")
            and outcomes[-1]["attempt_id"] == "claude-message:" + message_id
            and any(event["native_record"] == call["call_record"] and event["kind"] == "API_SUCCESS"
                    for event in blocks),
            "ORIGINAL_NATIVE_CURRENT_AGENT_CALL_REQUIRED: latest native API response must own this call")
    reader.record(call["call_record"])
    reader.verify_pin(session["freeze_fence"])
    reader.verify_pin(end)
    return call


class _ChildReader(NativeReader):
    """Only a child file immediately under an authenticated original parent.

    Reuse the original bounded/no-follow descriptor and record-pin methods.
    The exact child filename is not a UUID parent-session filename, so its
    constructor cannot use the root-parent NativeReader filename rule.
    """
    def __init__(self, parent, agent_id, path):
        require(re.fullmatch(r"a[0-9a-f]{16}", agent_id) is not None,
                "ORIGINAL_CHILD_NATIVE_ID_REQUIRED")
        expected = parent.path.parent / parent.session["session_id"] / "subagents" / ("agent-" + agent_id + ".jsonl")
        self.path = checked_path(path)
        require(self.path == expected, "ORIGINAL_CHILD_NATIVE_PATH_REQUIRED")
        info = self.path.lstat()
        self.pin = dict(path=str(self.path), device=info.st_dev, inode=info.st_ino)
        self.session, self.provider = parent.session, "claude"
        self.agent_id = agent_id
        # open() verifies native file type/owner and the captured inode.
        with self.open():
            pass


@contract_errors("original native child report")
def child_report(session, call, *, claim_id, agent_id, transcript_path, fixtures=False):
    """Return source-pinned full terminal text, never a launch/handback note.

    Report availability is separate from native PostToolUse completion and
    original-parent consumption. Callers may not release capacity from this
    observation. Every ancestry vertex (including attachment) stays bound to
    the original parent session and exact native child.
    """
    payload = dict(hook_event_name="PreToolUse", session_id=session["session_id"],
        transcript_path=session["transcript"]["path"], tool_name="Agent",
        tool_use_id=call["tool_use_id"], tool_input=call["tool_input"])
    original = original_agent_call(session, payload, fixtures=fixtures)
    require(original == call and claim_id == object_sha(dict(session_id=session["session_id"],
        tool_use_id=call["tool_use_id"])), "ORIGINAL_CHILD_CLAIM_BINDING_CHANGED")
    parent = NativeReader(session, fixtures=fixtures)
    reader = _ChildReader(parent, agent_id, transcript_path)
    end = reader.snapshot()
    require(0 < end["offset"] <= MAX_BYTES, "ORIGINAL_CHILD_REPORT_PENDING: bounded child interval required")
    records = []
    for pair in reader.records(0, end["offset"]):
        require(len(records) < MAX_RECORDS, "ORIGINAL_CHILD_REPORT_PENDING: child record bound")
        records.append(pair)
    first_ref, first = records[0]
    root_text = child_prompt(claim_id, call["tool_input"]["prompt"])
    public_first = dict(first, isSidechain=False)
    require(first.get("type") == "user" and first.get("parentUuid") is None
            and first.get("sessionId") == session["session_id"]
            and first.get("agentId") == agent_id and first.get("isSidechain") is True
            and native_text(public_first, "claude", "user") == root_text,
            "ORIGINAL_CHILD_ROOT_PROMPT_REQUIRED: a quoted marker is not a child binding")
    call_time = parse_time(parent.record(call["call_record"])["timestamp"]).timestamp()
    require(parse_time(first["timestamp"]).timestamp() >= call_time,
            "ORIGINAL_CHILD_NATIVE_CHRONOLOGY_CHANGED")
    linked, candidates, last_message = {}, [], None
    tool_calls, pending_tools = set(), set()
    previous_time = call_time
    for ref, record in records:
        # Non-conversation telemetry cannot establish a parent link or a result.
        if record.get("type") not in {"assistant", "user", "system", "attachment"}:
            continue
        require(record.get("sessionId") == session["session_id"]
                and record.get("agentId") == agent_id and record.get("isSidechain") is True,
                "ORIGINAL_CHILD_NATIVE_IDENTITY_CHANGED")
        key, parent_key = record.get("uuid"), record.get("parentUuid")
        require(isinstance(key, str) and key and key not in linked
                and (ref == first_ref or parent_key in linked),
                "ORIGINAL_CHILD_NATIVE_PARENT_CHAIN_CHANGED")
        stamp = parse_time(record["timestamp"]).timestamp()
        require(previous_time <= stamp <= time.time(), "ORIGINAL_CHILD_NATIVE_CHRONOLOGY_CHANGED")
        previous_time = stamp
        linked[key] = ref
        if record["type"] in {"assistant", "user"}:
            last_message = key
            message = record.get("message", {})
            require(isinstance(message, dict) and message.get("role") == record["type"],
                    "ORIGINAL_CHILD_NATIVE_MESSAGE_CHANGED")
            blocks = message.get("content", [])
            for block in blocks if isinstance(blocks, list) else []:
                if not isinstance(block, dict):
                    continue
                if record["type"] == "assistant" and block.get("type") == "tool_use":
                    tool_id = block.get("id")
                    require(isinstance(tool_id, str) and tool_id and tool_id not in tool_calls,
                            "ORIGINAL_CHILD_TOOL_RESULTS_PENDING: changed or duplicate child tool call")
                    tool_calls.add(tool_id)
                    pending_tools.add(tool_id)
                if record["type"] == "user" and block.get("type") == "tool_result":
                    tool_id = block.get("tool_use_id")
                    require(isinstance(tool_id, str) and tool_id in pending_tools,
                            "ORIGINAL_CHILD_TOOL_RESULTS_PENDING: unrelated or duplicate child tool result")
                    pending_tools.remove(tool_id)
        if record["type"] == "assistant":
            message = record.get("message", {})
            require(isinstance(message, dict) and record.get("isApiErrorMessage") is not True,
                    "ORIGINAL_CHILD_REPORT_PENDING: native API error")
            if message.get("stop_reason") == "end_turn":
                require(not pending_tools,
                        "ORIGINAL_CHILD_TOOL_RESULTS_PENDING: terminal text cannot close pending child tools")
                # native_text ignores reasoning blocks, never publishes them.
                body = native_text(dict(record, isSidechain=False), "claude", "assistant")
                require(isinstance(body, str) and bool(body.strip())
                        and len(body.encode()) <= 256 * 1024,
                        "ORIGINAL_CHILD_REPORT_PENDING: complete bounded terminal text required")
                candidates.append(dict(body=body, terminal_uuid=key, terminal_record=ref,
                                       terminal_timestamp=record["timestamp"]))
    require(len(candidates) == 1 and candidates[0]["terminal_uuid"] == last_message,
            "ORIGINAL_CHILD_REPORT_PENDING: no unique current end_turn report")
    for ref in (first_ref, candidates[0]["terminal_record"]):
        reader.record(ref)
    require([pin for pin, _ in reader.records(0, end["offset"])] == [pin for pin, _ in records],
            "ORIGINAL_CHILD_NATIVE_INTERVAL_CHANGED")
    reader.verify_pin(end)
    parent.verify_pin(session["freeze_fence"])
    return dict(candidates[0], claim_id=claim_id, agent_id=agent_id,
                body_sha256=digest(candidates[0]["body"].encode()),
                root_record=first_ref, native_fence=end, input_operations=0)


@contract_errors("original native Agent completion")
def native_agent_completion(session, call, response, *, fixtures=False):
    """Bind a foreground completed response to its unique actual parent result.

    A background launch, a hook payload or a quoted result cannot prove native
    completion. Exact source equality and a completed structured native result
    are mandatory even when the tool block omits the optional is_error flag.
    """
    require(isinstance(response, dict) and response.get("status") == "completed"
            and re.fullmatch(r"a[0-9a-f]{16}", response.get("agentId", "")) is not None,
            "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED")
    reader = NativeReader(session, fixtures=fixtures)
    start, end = reader.verify_pin(session["freeze_fence"]), reader.snapshot()
    call_time = parse_time(reader.record(call["call_record"])["timestamp"]).timestamp()
    require(end["offset"] - start <= MAX_BYTES, "CHILD_NATIVE_RESULT_WINDOW_PENDING")
    results, scanned = [], 0
    for ref, record in reader.records(start, end["offset"]):
        scanned += 1
        require(scanned <= MAX_RECORDS, "CHILD_NATIVE_RESULT_WINDOW_PENDING")
        if record.get("type") != "user" or record.get("isSidechain") is True:
            continue
        message = record.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result" or block.get("tool_use_id") != call["tool_use_id"]:
                continue
            require(ref["offset"] >= call["call_record"]["offset"] + call["call_record"]["length"]
                    and block.get("is_error") is not True and record.get("toolUseResult") == response
                    and block.get("content") == response.get("content") and record.get("sessionId") == session["session_id"],
                    "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED: native result differs")
            require(call_time <= parse_time(record["timestamp"]).timestamp() <= time.time(),
                    "ORIGINAL_NATIVE_CHILD_COMPLETION_CHRONOLOGY_CHANGED")
            results.append(ref)
    require(len(results) == 1, "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED: missing or duplicate result")
    result = reader.record(results[0])
    reader.verify_pin(session["freeze_fence"])
    reader.verify_pin(end)
    return dict(result_record=results[0], completion_timestamp=result["timestamp"],
                agent_id=response["agentId"], input_operations=0)


@contract_errors("original native child consumption call")
def original_consumption_call(session, payload, command, *, fixtures=False):
    require(payload.get("hook_event_name") == "PreToolUse" and payload.get("tool_name") == "Bash"
            and payload.get("agent_id") is None and payload.get("tool_input", {}).get("command") == command
            and payload["tool_input"].get("run_in_background", False) is False,
            "ORIGINAL_NATIVE_CHILD_CONSUMPTION_REQUIRED")
    reader = NativeReader(session, fixtures=fixtures)
    start, end = reader.verify_pin(session["freeze_fence"]), reader.snapshot()
    require(end["offset"] - start <= MAX_BYTES, "CHILD_NATIVE_CONSUMPTION_WINDOW_PENDING")
    matches = []
    scanned = 0
    for ref, record in reader.records(start, end["offset"]):
        scanned += 1
        require(scanned <= MAX_RECORDS, "CHILD_NATIVE_CONSUMPTION_WINDOW_PENDING")
        if record.get("type") != "assistant" or record.get("isSidechain") is True:
            continue
        for block in record.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("id") != payload["tool_use_id"]:
                continue
            require(block.get("name") == "Bash" and block.get("input") == payload["tool_input"]
                    and record.get("sessionId") == session["session_id"]
                    and record.get("message", {}).get("role") == "assistant"
                    and parse_time(record["timestamp"]).timestamp() <= time.time(),
                    "ORIGINAL_NATIVE_CHILD_CONSUMPTION_REQUIRED: native Bash differs")
            matches.append(ref)
    require(len(matches) == 1, "ORIGINAL_NATIVE_CHILD_CONSUMPTION_REQUIRED: missing or duplicate Bash")
    reader.record(matches[0])
    reader.verify_pin(end)
    return matches[0]
