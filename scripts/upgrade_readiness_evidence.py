"""Pure original readiness evidence; no discovery, RPC, input or publication.

The original inspector and source-contract verifier share these exact parsers.
Native/root authority remains with the supplied authenticated native reader.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import uuid

from orchestration_common import (ContractError, digest, file_ref, json_ref, parse_time, read_regular,
                                  require, strict_json)
from upgrade_startup_contract import GOAL_SCHEMA, pressure_startup_prompt, readiness_command, readiness_prompt


def native_records(native, session):
    """Stream only the exact process-bound journal up to its captured EOF."""
    pin = session["transcript"]
    require(pin["at_record_boundary"], "NATIVE_EOF_INCOMPLETE")
    with native._open(pin["path"]) as stream:
        native._check_file(stream, session)
        stream.seek(0)
        while stream.tell() < pin["offset"]:
            at = stream.tell()
            raw = stream.readline(min(native.MAX_RECORD + 1, pin["offset"] - at))
            require(raw.endswith(b"\n") and len(raw) <= native.MAX_RECORD,
                    "NATIVE_RECORD_INCOMPLETE_OR_OVERSIZED")
            record = strict_json(raw, "startup native record")
            require(isinstance(record, dict), "NATIVE_RECORD_NOT_OBJECT")
            if session["provider"] == "claude" and record.get("sessionId"):
                require(record["sessionId"] == session["session_id"], "NATIVE_SESSION_CHANGED")
            ref = {k: pin[k] for k in ("path", "device", "inode")}
            ref.update(offset=at, length=len(raw), sha256=digest(raw))
            yield ref, record


def command_in_tool(arguments, command):
    """Match executed shell arguments; assistant prose is never an input here."""
    if isinstance(arguments, dict):
        return arguments.get("cmd", arguments.get("command")) == command
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except ValueError:
            # Only the single complete shell invocation is admitted. A comment,
            # quoted example or a second call must not become readiness evidence.
            match = re.fullmatch(r"\s*text\(\s*await\s+tools\.exec_command\((\{.*\})\)\s*\);?\s*",
                                 arguments, re.DOTALL)
            if match is None:
                return False
            try:
                parsed = json.loads(match[1])
            except ValueError:
                return False
        # A JSON string containing example code is data, not an executed call.
        return isinstance(parsed, dict) and command_in_tool(parsed, command)
    return False


def completed_probe(value, lane, spec):
    """Recognize the actual probe result inside supported native tool envelopes."""
    expected = dict(status="UPGRADE_READINESS_TOOL_COMPLETED", lane=lane,
                    native_root=spec["clients"][lane]["native_root"],
                    controller=spec["controller"], adoption_proven=False, input_operations=0)
    found, failed = [], []

    def walk(item):
        if isinstance(item, dict):
            if (item.get("isError") is True or item.get("is_error") is True
                    or item.get("exit_code") not in (None, 0)
                    or item.get("status") in ("running", "pending", "rejected")
                    or (item.get("session_id") is not None and item.get("exit_code") is None)):
                failed.append(True)
            if item == expected:
                found.append(item)
            for key in ("content", "output", "value", "text"):
                if key in item:
                    walk(item[key])
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            if re.search(r"Script running with cell ID|Process running with session ID|"
                         r"(?:Process exited with code|exit_code[\"']?\s*:)\s*-?[1-9][0-9]*", item):
                failed.append(True)
            # Native tools can wrap JSON in a short textual status envelope.
            # Decode complete objects; never accept the marker as a substring.
            cursor, decoder = 0, json.JSONDecoder()
            while cursor < len(item):
                at = item.find("{", cursor)
                if at < 0:
                    break
                try:
                    child, size = decoder.raw_decode(item[at:])
                except ValueError:
                    cursor = at + 1
                    continue
                walk(child)
                cursor = at + size
    walk(value)
    return len(found) == 1 and not failed


def hook_matches(value, call, session, lane, spec, launches):
    if (value.get("schema") != "upgrade-automatic-hook-observation-v2"
            or value.get("lane") != lane or value.get("startup") != call["startup"]
            or value.get("adoption_proven") is not False
            or value.get("direct_client") not in ((lane, "codex-server") if lane == "codex" else (lane,))):
        return False
    selected = launches[value["direct_client"]]
    parents = value.get("parents", [])
    if not parents or any(p.get("exact_hook_shell") is not True for p in parents[:-1]):
        return False
    parent = parents[-1]
    if any(earlier.get("ppid") != later.get("pid") for earlier, later in zip(parents, parents[1:])):
        return False
    if (parent.get("pid") != selected["pid"] or parent.get("birth") != selected["birth"]
            or parent.get("executable") != spec["pins"][session["provider"]]["path"]):
        return False
    payload = value.get("payload", {})
    sid = payload.get("session_id", payload.get("sessionId"))
    path = payload.get("transcript_path", payload.get("transcriptPath"))
    if sid != session["session_id"] or path != session["transcript"]["path"]:
        return False
    if payload.get("hook_event_name") not in ("PreToolUse", "PostToolUse"):
        return False
    call_id = payload.get("tool_use_id", payload.get("call_id", payload.get("tool_call_id")))
    if call_id != call["id"]:
        return False
    # Native names can have their functions. namespace, while hook names don't.
    if str(payload.get("tool_name", "")).split(".")[-1] != call["name"].split(".")[-1]:
        return False
    inputs = payload.get("tool_input")
    arguments = call["arguments"]
    try:
        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        pass
    if inputs != arguments and inputs not in ({"input": arguments}, {"code": arguments}):
        return False
    key = "codex/hooks.json" if lane == "codex" else lane + "/settings.json"
    if value.get("configuration") != spec["pins"][key]:
        return False
    hooks = json.loads(read_regular(spec["pins"][key]["path"]))["hooks"]
    registered = [h.get("command") for group in hooks[payload["hook_event_name"]]
                  for h in group["hooks"] if h.get("type") == "command"]
    expected = shlex.join([spec["pins"]["python"]["path"], "-I", "-B",
        str(Path(spec["controller"]) / "scripts/cmux_workflow_advisory.py"),
        "--hook", value["hook"]])
    return value.get("command") == expected and registered.count(expected) == 1


def nested_readiness_command(records, call, result_ref, session, command, spec):
    """Bind a nested Bash hook to its native CommandExecution, never by text alone.

    Unified exec emits its own ID inside the outer functions.exec call/result
    interval. The exact one-command wrapper, one native completion, same thread
    and turn, and successful probe output jointly establish this mapping.
    """
    if call["name"].split(".")[-1] != "exec" or not call.get("turn_id"):
        return None
    # command_in_tool also admits a direct JSON tool argument; that is not a
    # JavaScript wrapper and cannot establish a nested execution relationship.
    args = call["arguments"]
    if not isinstance(args, str) or re.fullmatch(
            r"\s*text\(\s*await\s+tools\.exec_command\((\{.*\})\)\s*\);?\s*",
            args, re.DOTALL) is None:
        return None
    inside = [(ref, row) for ref, row in records
              if call["record"]["offset"] < ref["offset"] < result_ref["offset"]]
    if any(row.get("type") == "response_item" and row.get("payload", {}).get("type")
           in ("function_call", "custom_tool_call") for _, row in inside):
        return None
    completions = [(ref, row["payload"]) for ref, row in inside
                   if row.get("type") == "event_msg"
                   and row.get("payload", {}).get("type") == "item_completed"
                   and row["payload"].get("item", {}).get("type") == "CommandExecution"]
    if len(completions) != 1:
        return None
    ref, event = completions[0]
    item = event["item"]
    argv = item.get("command")
    if (event.get("thread_id") != session["session_id"]
            or event.get("turn_id") != call["turn_id"]
            or item.get("source") != "unified_exec_startup"
            or item.get("status") != "completed"
            or type(item.get("exit_code")) is not int or item["exit_code"] != 0
            or not isinstance(item.get("id"), str) or not item["id"].startswith("exec-")
            or not isinstance(argv, list) or len(argv) != 3
            or argv[0] not in ("/bin/zsh", "/bin/bash", "/bin/sh")
            or argv[1:] != ["-c", command]
            or not completed_probe(item.get("aggregated_output"), "codex", spec)):
        return None
    return dict(id=item["id"], name="Bash", arguments={"command": command},
                startup=call["startup"], record=ref, turn_id=call["turn_id"])


def readiness_evidence(native, session, lane, spec, startup_ref, binding, launches):
    command = readiness_command(spec["controller"], spec["pins"]["python"]["path"],
                                spec["pins"]["rtk"]["path"], lane)
    goal_mode = spec.get("schema") == GOAL_SCHEMA
    prompt = (pressure_startup_prompt(lane, spec["lane_instructions"], spec["goal_startup"])
              if goal_mode else readiness_prompt(spec["controller"], spec["pins"]["python"]["path"],
                                                 spec["pins"]["rtk"]["path"], lane))
    calls, results, model_records, prompts, execution_records = {}, {}, [], [], []
    goals = []
    call_count = 0
    for ref, record in native_records(native, session):
        if lane == "codex":
            payload = record.get("payload", {})
            # Retain execution structure only, not reasoning or large tool output.
            if (record.get("type") == "event_msg" and payload.get("type") == "item_completed"
                    or record.get("type") == "response_item" and payload.get("type")
                    in ("function_call", "custom_tool_call")):
                execution_records.append((ref, record))
            if record.get("type") == "turn_context" and payload.get("model"):
                model_records.append((ref, payload["model"]))
            if record.get("type") != "response_item":
                continue
            if payload.get("type") == "message" and payload.get("role") == "user":
                text = "".join(x.get("text", "") for x in payload.get("content", [])
                               if x.get("type") == "input_text")
                if text == prompt:
                    prompts.append(ref)
            if payload.get("type") in ("function_call", "custom_tool_call"):
                args = payload.get("arguments", payload.get("input"))
                name = payload.get("name", "")
                if name.split(".")[-1] in ("exec", "exec_command") and command_in_tool(args, command):
                    call_count += 1
                    calls[payload["call_id"]] = dict(id=payload["call_id"], name=name,
                        arguments=args, record=ref, startup=startup_ref,
                        turn_id=payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id"))
            if payload.get("type") in ("function_call_output", "custom_tool_call_output"):
                results.setdefault(payload.get("call_id"), []).append((ref, payload.get("output")))
        else:
            if goal_mode and record.get("isSidechain") is True:
                continue
            attachment = record.get("attachment")
            if (goal_mode and record.get("type") == "attachment"
                    and isinstance(attachment, dict) and attachment.get("type") == "goal_status"):
                goals.append((ref, record))
                if (attachment.get("met") is False and attachment.get("sentinel") is True
                        and attachment.get("failed") is not True
                        and attachment.get("condition") == spec["goal_startup"][lane]):
                    # Native /goal enters through local-jsx, so a quoted raw
                    # command is not sufficient. Bind the persisted SET event.
                    try:
                        require(isinstance(record.get("uuid"), str), "STARTUP_GOAL_UUID_MISSING")
                        uuid.UUID(record["uuid"])
                        parse_time(record.get("timestamp"), "startup Goal timestamp")
                    except (ValueError, TypeError, AttributeError) as exc:
                        raise ContractError("STARTUP_GOAL_IDENTITY_INVALID") from exc
                    prompts.append(ref)
            message = record.get("message", {})
            if (record.get("type") == "assistant" and message.get("model")
                    and record.get("isApiErrorMessage") is not True and message["model"] != "<synthetic>"):
                model_records.append((ref, message["model"]))
            content = message.get("content", [])
            text = content if isinstance(content, str) else "".join(
                x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
            if not goal_mode and record.get("type") == "user" and text == prompt:
                prompts.append(ref)
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if (record.get("type") == "assistant" and block.get("type") == "tool_use"
                        and block.get("name") == "Bash" and command_in_tool(block.get("input"), command)):
                    call_count += 1
                    calls[block["id"]] = dict(id=block["id"], name="Bash", arguments=block["input"],
                                              record=ref, startup=startup_ref,
                                              timestamp=record.get("timestamp"))
                if record.get("type") == "user" and block.get("type") == "tool_result":
                    if block.get("is_error") is not True:
                        results.setdefault(block.get("tool_use_id"), []).append((ref, block.get("content")))
    require(len(prompts) == 1, "ORIGINAL_STARTUP_PROMPT_NOT_OBSERVED")
    require(len(calls) == call_count == 1, "READINESS_TOOL_NOT_UNIQUE_OR_NOT_OBSERVED")
    call = next(iter(calls.values()))
    completed = [(ref, value) for ref, value in results.get(call["id"], [])
                 if completed_probe(value, lane, spec)
                 and ref["offset"] > call["record"]["offset"]]
    require(len(completed) == len(results.get(call["id"], [])) == 1, "READINESS_TOOL_RESULT_NOT_OBSERVED")
    require(prompts[0]["offset"] < call["record"]["offset"], "READINESS_PRECEDES_STARTUP_INPUT")
    if goal_mode and lane != "codex":
        before_result = [(ref, row) for ref, row in goals
                         if ref["offset"] <= completed[0][0]["offset"]]
        require(before_result and all(row["attachment"].get("met") is False
                and ("failed" not in row["attachment"] or row["attachment"]["failed"] is False)
                and ("sentinel" not in row["attachment"] or type(row["attachment"]["sentinel"]) is bool)
                and row["attachment"].get("condition") == spec["goal_startup"][lane]
                for _, row in before_result), "STARTUP_GOAL_CHANGED_BEFORE_READINESS")
        initial = next(row for ref, row in goals if ref == prompts[0])
        require(parse_time(initial["timestamp"]) <= parse_time(call["timestamp"], "probe timestamp"),
                "STARTUP_GOAL_TIME_AFTER_PROBE")
    models = [(ref, model) for ref, model in model_records if ref["offset"] <= completed[0][0]["offset"]]
    require(models and len({model for _, model in models}) == 1, "STARTUP_MODEL_UNCONFIRMED_OR_CHANGED")
    nested = (nested_readiness_command(execution_records, call, completed[0][0], session, command, spec)
              if lane == "codex" else None)
    matched = {"PreToolUse": [], "PostToolUse": []}
    nested_matched = {"PreToolUse": [], "PostToolUse": []}
    directory = Path(binding["state_root"]) / "hook-observations" / lane
    for path in sorted(directory.glob("*.json")):
        ref = file_ref(path)
        value = json_ref(ref, "automatic hook observation")
        if hook_matches(value, call, session, lane, spec, launches):
            matched[value["payload"]["hook_event_name"]].append(ref)
        if nested is not None and hook_matches(value, nested, session, lane, spec, launches):
            nested_matched[value["payload"]["hook_event_name"]].append(ref)
    used_nested = not all(matched.values()) and all(nested_matched.values())
    if used_nested:
        matched = nested_matched
    require(all(matched.values()), "AUTOMATIC_PRE_POST_HOOKS_NOT_CORRELATED")
    proof = dict(prompt=prompts[0], call=call["record"], result=completed[0][0],
                 call_id=call["id"], hooks=matched, model=models[-1][1], model_record=models[-1][0])
    if used_nested:
        proof["native_command"] = {key: nested[key] for key in ("id", "record", "turn_id")}
    if goal_mode:
        proof["startup_kind"] = "pinned_lane_prompt" if lane == "codex" else "native_goal_set"
        if lane != "codex":
            proof["goal_condition_sha256"] = digest(spec["goal_startup"][lane].encode())
    return proof


