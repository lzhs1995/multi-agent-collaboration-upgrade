"""Read-only private-harness inspection before/after client adoption.

This entrypoint never imports a sender or publishes adoption. It authenticates
the original startup PID/birth/TTY and reads metadata from its native journal.
The external controller, outside all three client sandboxes, owns publication.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from orchestration_common import (ContractError, digest, file_ref, json_ref, read_regular,
                                  require, strict_json, utc_now)
from upgrade_client_isolation import LANES, require_kernel_boundary
from upgrade_native_roots import process_root
from upgrade_startup_contract import readiness_command, readiness_prompt
from upgrade_socket_boundary import binding_boundary, verify_kernel


class ReadOnlyCmux:
    def __init__(self, executable):
        self.executable = executable

    def _run(self, *args):
        require(args == ("tree", "--all", "--json", "--id-format", "both"),
                "INSPECTOR_METADATA_ONLY")
        return subprocess.run([self.executable, *args], capture_output=True,
                              text=True, timeout=10, check=True).stdout


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


def readiness_evidence(native, session, lane, spec, startup_ref, binding, launches):
    command = readiness_command(spec["controller"], spec["pins"]["python"]["path"],
                                spec["pins"]["rtk"]["path"], lane)
    prompt = readiness_prompt(spec["controller"], spec["pins"]["python"]["path"],
                              spec["pins"]["rtk"]["path"], lane)
    calls, results, model_records, prompts = {}, {}, [], []
    for ref, record in native_records(native, session):
        if lane == "codex":
            payload = record.get("payload", {})
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
                    calls[payload["call_id"]] = dict(id=payload["call_id"], name=name,
                        arguments=args, record=ref, startup=startup_ref)
            if payload.get("type") in ("function_call_output", "custom_tool_call_output"):
                results.setdefault(payload.get("call_id"), []).append((ref, payload.get("output")))
        else:
            message = record.get("message", {})
            if (record.get("type") == "assistant" and message.get("model")
                    and record.get("isApiErrorMessage") is not True and message["model"] != "<synthetic>"):
                model_records.append((ref, message["model"]))
            content = message.get("content", [])
            text = content if isinstance(content, str) else "".join(
                x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
            if record.get("type") == "user" and text == prompt:
                prompts.append(ref)
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if (record.get("type") == "assistant" and block.get("type") == "tool_use"
                        and block.get("name") == "Bash" and command_in_tool(block.get("input"), command)):
                    calls[block["id"]] = dict(id=block["id"], name="Bash", arguments=block["input"],
                                              record=ref, startup=startup_ref)
                if record.get("type") == "user" and block.get("type") == "tool_result":
                    if block.get("is_error") is not True:
                        results.setdefault(block.get("tool_use_id"), []).append((ref, block.get("content")))
    require(len(prompts) == 1, "ORIGINAL_STARTUP_PROMPT_NOT_OBSERVED")
    require(len(calls) == 1, "READINESS_TOOL_NOT_UNIQUE_OR_NOT_OBSERVED")
    call = next(iter(calls.values()))
    completed = [(ref, value) for ref, value in results.get(call["id"], [])
                 if completed_probe(value, lane, spec)
                 and ref["offset"] > call["record"]["offset"]]
    require(len(completed) == 1, "READINESS_TOOL_RESULT_NOT_OBSERVED")
    require(prompts[0]["offset"] < call["record"]["offset"], "READINESS_PRECEDES_STARTUP_INPUT")
    models = [(ref, model) for ref, model in model_records if ref["offset"] <= completed[0][0]["offset"]]
    require(models and len({model for _, model in models}) == 1, "STARTUP_MODEL_UNCONFIRMED_OR_CHANGED")
    matched = {"PreToolUse": [], "PostToolUse": []}
    directory = Path(binding["state_root"]) / "hook-observations" / lane
    for path in sorted(directory.glob("*.json")):
        ref = file_ref(path)
        value = json_ref(ref, "automatic hook observation")
        if hook_matches(value, call, session, lane, spec, launches):
            matched[value["payload"]["hook_event_name"]].append(ref)
    require(all(matched.values()), "AUTOMATIC_PRE_POST_HOOKS_NOT_CORRELATED")
    return dict(prompt=prompts[0], call=call["record"], result=completed[0][0],
                call_id=call["id"], hooks=matched, model=models[-1][1], model_record=models[-1][0])


def inspect(startup_ref, spec, binding):
    import cmux_daemon_identity as identity
    import cmux_native_delivery as native
    from cmux_codex_rpc import DaemonRPC
    control = Path(binding["state_root"]) / "startup"
    intent_ref, result_ref = file_ref(control / "intent.json"), file_ref(control / "result.json")
    intent, result = json_ref(intent_ref), json_ref(result_ref)
    require(intent["startup"] == result["startup"] == startup_ref
            and intent.get("spawn") is True and intent.get("spawn_authorized") is True
            and result["returncode"] == 0 and result["workspace_creation_attempts"] == 1,
            "ORIGINAL_LAUNCH_UNCONFIRMED")
    bridge = ReadOnlyCmux(spec["pins"]["cmux"]["path"])
    launches, launch_refs = {}, {}
    for lane in (*LANES, "codex-server"):
        launch_refs[lane] = file_ref(control / (lane + ".json"))
        launches[lane] = json_ref(launch_refs[lane])
        require(launches[lane]["startup"] == startup_ref, "LAUNCH_STARTUP_CHANGED")
    sessions, evidence, kernels, errors = {}, {}, {}, {}
    for lane in LANES:
        try:
            launch = launches[lane]
            target = str(uuid.UUID(launch["surface_uuid"]))
            require(target not in spec["protected_surface_uuids"], "PRODUCTION_SURFACE_REFUSED")
            row = native._tree_row(bridge, target)
            client = native._target_process(row)
            require(client["pid"] == launch["pid"] and client["birth"] == launch["birth"],
                    "ORIGINAL_CLIENT_PID_OR_BIRTH_CHANGED")
            workspace = str(uuid.UUID(row["workspace_uuid"]))
            require(workspace == str(uuid.UUID(launch["workspace_uuid"]))
                    and workspace.lower() in result["stdout"].lower(), "LAUNCH_WORKSPACE_CHANGED")
            provider = "codex" if lane == "codex" else "claude"
            require(native._provider(client) == provider
                    and str(process_root(provider, client)) == spec["clients"][lane]["native_root"],
                    "CLIENT_PROVIDER_OR_NATIVE_ROOT_CHANGED")
            sid = native._live_session(client, provider)
            if provider == "claude":
                require(sid == spec["clients"][lane]["session_id"], "ORIGINAL_CLAUDE_SESSION_CHANGED")
            path = native._transcript_path(provider, sid, client)
            session = dict(provider=provider, session_id=sid, workspace_uuid=workspace,
                surface_uuid=target, pane_uuid=str(uuid.UUID(row["target_pane_uuid"])), tty=row["tty"],
                process=native._public_process(client), native_config_root=str(process_root(provider, client)),
                transcript=native._pin_file(path, provider, sid))
            kernels[lane] = require_kernel_boundary(client["pid"],
                denied=binding["isolation"]["denied_probes"], writable=binding["isolation"]["write_probes"])
            boundary = binding_boundary(binding, binary_pin=spec["pins"]["codex"], live=True)
            if boundary is not None:
                kernels[lane]["socket_boundary"] = verify_kernel(client["pid"], boundary, writer=lane == "codex")
            sessions[lane] = session
            evidence[lane] = readiness_evidence(native, session, lane, spec, startup_ref, binding, launches)
            session.update(model=evidence[lane]["model"], model_record=evidence[lane]["model_record"],
                           freeze_fence=session["transcript"])
        except (ValueError, OSError, KeyError, native.NativeDeliveryError, identity.IdentityError) as exc:
            errors[lane] = str(exc)
    capability = None
    if "codex" in sessions:
        try:
            server = identity.process(launches["codex-server"]["pid"])
            require(server["birth"] == launches["codex-server"]["birth"]
                    and server["executable"] == spec["pins"]["codex"]["path"]
                    and server["argv"] == spec["rpc"]["argv"], "ORIGINAL_CODEX_SERVER_CHANGED")
            kernels["codex-server"] = require_kernel_boundary(server["pid"],
                denied=binding["isolation"]["denied_probes"], writable=binding["isolation"]["write_probes"])
            boundary = binding_boundary(binding, binary_pin=spec["pins"]["codex"], live=True)
            if boundary is not None:
                kernels["codex-server"]["socket_boundary"] = verify_kernel(server["pid"], boundary, writer=True)
            client = DaemonRPC(spec["rpc"]["path"], timeout=15)
            try:
                session = sessions["codex"]
                metadata = client.request("thread/read", {"threadId": session["session_id"],
                                                          "includeTurns": False})["thread"]
                turns = client.request("thread/turns/list", {"threadId": session["session_id"],
                    "limit": 1, "sortDirection": "desc", "itemsView": "notLoaded"})["data"]
                require(metadata.get("id") == session["session_id"]
                        and metadata.get("path") == session["transcript"]["path"]
                        and metadata.get("canAcceptDirectInput") is True
                        and isinstance(turns, list) and len(turns) <= 1, "CODEX_DIRECT_INPUT_CAPABILITY_UNVERIFIED")
                capability = dict(thread_id=session["session_id"], path=metadata["path"],
                    can_accept_direct_input=True, status=metadata.get("status"), socket=client.socket_identity,
                    last_turn=[{k: t.get(k) for k in ("id", "status")} for t in turns],
                    methods=["thread/read", "thread/turns/list"], input_operations=0)
            finally:
                client.close()
        except Exception as exc:
            errors["codex-rpc"] = str(exc)
    if len(sessions) == 3:
        require(len({s["workspace_uuid"] for s in sessions.values()}) == 1
                and all(len({s[k] for s in sessions.values()}) == 3
                        for k in ("surface_uuid", "pane_uuid", "session_id")), "CLIENT_ENDPOINTS_NOT_DISTINCT")
    return dict(schema="upgrade-live-inspection-v2", startup=startup_ref, observed_at=utc_now(),
                observed_at_ns=time.time_ns(), inspector=file_ref(Path(__file__).resolve()),
                launch_intent=intent_ref, launch_result=result_ref, launches=launch_refs,
                sessions=sessions, readiness=evidence, kernel=kernels, codex_capability=capability,
                errors=errors, ready=not errors and len(evidence) == 3 and capability is not None,
                stopped=os.path.lexists(spec["stop_path"]), input_operations=0)
