"""External source-authenticated native child allocator; never calls a model.

Only the original native Agent tool may create a child. This process manages
whole-task reservations and evidence, not model sessions, retries or dispatch.
No fixture mode is accepted by its CLI or Unix socket protocol.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time
import uuid

from cmux_prompt_reference import plan, persist
from orchestration_common import (checked_path, contract_errors, json_ref, file_ref, object_sha,
    parse_time, require, strict_json, tree_manifest, utc_now, verify_tree, write_json)
from upgrade_body_read import read_command
from upgrade_child_budget import (authority_value, budget_status, initialize_ledger,
    consume_child_report, record_child_report, reserve_child)
from upgrade_child_budget_protocol import (POLICY, SCHEMA, child_socket, consume_command,
    MAX_PACKET, encode_packet, parse_consumption, updated_agent_input)
from upgrade_child_native import (child_report, native_agent_completion, original_agent_call,
                                  original_consumption_call)
from upgrade_child_peer import authenticate_hook_peer, kernel_peer

REGISTRATION_SCHEMA = "upgrade-native-child-service-registration-v1"
REGISTRATION_FIELDS = {"schema", "policy", "task_id", "runtime", "startup", "adoption", "source",
    "native_authorizations", "sessions", "authority", "output_root", "report_root", "socket",
    "account_home", "python", "controller", "rtk", "input_operations", "child_budget_authorized"}
MAX_SERVICE_EVENTS = 256


def service_root(runtime):
    # One registration per frozen runtime. Changing task_id cannot create a
    # parallel empty ledger beneath the same two original native parents.
    startup = json_ref(runtime["startup"])
    return checked_path(Path(startup["adoption_path"]).parent / "native-child-budget")


@contract_errors("register original child service")
def register_service(runtime_ref, adoption_ref, *, task_id, native_authorizations, source,
                     child_budget_authorized):
    from orchestration_freeze_v3 import authorize
    from upgrade_continuity_native import registered_sessions
    from upgrade_live_control import external_publisher
    from upgrade_live_runtime import validate_live_runtime
    from upgrade_startup_contract import child_budget_enabled
    require(child_budget_authorized is True, "ORIGINAL_NATIVE_CHILD_AUTHORIZATION_REQUIRED")
    _, runtime = validate_live_runtime(runtime_ref)
    startup = json_ref(runtime["startup"])
    binding = json_ref(file_ref(Path(runtime["controller"]["path"]) / "upgrade-binding.json"))
    require(child_budget_enabled(binding), "CHILD_NATIVE_HOOK_NOT_REGISTERED")
    external_publisher(startup)
    root = service_root(runtime)
    require(not os.path.lexists(root), "ORIGINAL_CHILD_SERVICE_REGISTRATION_EXISTS: do not reset it")
    records = authorize(native_authorizations)
    require(0 < len(records) <= 8, "ORIGINAL_NATIVE_CHILD_AUTHORIZATION_BOUND")
    verify_tree(source)
    require(Path(source["path"]).resolve() == Path(__file__).resolve().parent.parent
            and all("scripts/" + name in source["files"] for name in (
                "upgrade_child_service.py", "upgrade_child_budget.py", "upgrade_child_native.py",
                "upgrade_child_peer.py", "upgrade_child_budget_protocol.py")),
            "ORIGINAL_CHILD_SERVICE_SOURCE_REQUIRED")
    sessions = registered_sessions(runtime_ref, adoption_ref)
    authority = dict(schema="upgrade-child-budget-authority-v1", task_id=task_id,
        ledger_root=str(root / "ledger"), max_inflight=2, budget_scope="WHOLE_TASK",
        parents={lane: sessions[lane]["session_id"] for lane in ("claude_a", "claude_b")})
    # Validate task/parents before creating the irreversible enrollment root.
    require(isinstance(task_id, str) and 0 < len(task_id) <= 128 and task_id == task_id.strip()
            and not any(ord(c) < 32 for c in task_id), "CHILD_TASK_ID_INVALID")
    authorize(records)
    verify_tree(source)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    authority_ref = write_json(root / "authority.json", authority, exclusive=True)
    Path(authority_ref["path"]).chmod(0o444)
    initialize_ledger(authority_ref)
    value = dict(schema=REGISTRATION_SCHEMA, policy=POLICY, task_id=task_id, runtime=runtime_ref,
        startup=runtime["startup"], adoption=adoption_ref, source=source, native_authorizations=records,
        sessions=sessions, authority=authority_ref, output_root=str(root), report_root=str(root / "reports"),
        socket=str(child_socket({"tmp": binding["tmp_root"]})), account_home=str(Path.home().resolve()),
        python=runtime["python"]["path"], controller=runtime["controller"]["path"],
        rtk=startup["pins"]["rtk"]["path"], input_operations=0, child_budget_authorized=True)
    ref = write_json(root / "registration.json", value, exclusive=True)
    Path(ref["path"]).chmod(0o444)
    return ref


@contract_errors("original child service registration")
def validate_service_registration(ref):
    from orchestration_freeze_v3 import authorize
    from orchestration_native import NativeReader
    from upgrade_live_runtime import identity_matches, validate_adoption, validate_live_runtime
    from upgrade_startup_contract import child_budget_enabled
    value = json_ref(ref, "original native child service registration")
    require(set(value) == REGISTRATION_FIELDS and value["schema"] == REGISTRATION_SCHEMA
            and value["policy"] == POLICY and value["child_budget_authorized"] is True
            and type(value["input_operations"]) is int and value["input_operations"] == 0,
            "CHILD_SERVICE_REGISTRATION_FIELDS_CHANGED")
    _, runtime = validate_live_runtime(value["runtime"])
    startup = json_ref(runtime["startup"])
    binding = json_ref(file_ref(Path(runtime["controller"]["path"]) / "upgrade-binding.json"))
    require(child_budget_enabled(binding), "CHILD_NATIVE_HOOK_NOT_REGISTERED")
    root = service_root(runtime)
    require(ref["path"] == str(root / "registration.json") and value["output_root"] == str(root)
            and value["report_root"] == str(root / "reports") and value["startup"] == runtime["startup"]
            and value["controller"] == runtime["controller"]["path"]
            and value["python"] == runtime["python"]["path"]
            and value["rtk"] == startup["pins"]["rtk"]["path"]
            and value["socket"] == str(child_socket({"tmp": binding["tmp_root"]}))
            and value["account_home"] == str(Path.home().resolve()), "CHILD_SERVICE_SCOPE_CHANGED")
    verify_tree(value["source"])
    require(Path(value["source"]["path"]).resolve() == Path(__file__).resolve().parent.parent,
            "CHILD_SERVICE_SOURCE_CHANGED")
    proof = validate_adoption(value["runtime"], value["adoption"], runtime=runtime, allow_stopped=True)
    require(set(value["sessions"]) == {"codex", "claude_a", "claude_b"}, "CHILD_SERVICE_ORIGINAL_LANES_CHANGED")
    for lane, session in value["sessions"].items():
        require(identity_matches(session, proof["sessions"][lane])
                and session.get("private_native") == dict(runtime=value["runtime"], adoption=value["adoption"]),
                "CHILD_SERVICE_ORIGINAL_SESSION_CHANGED")
        reader = NativeReader(session)
        require(reader.verify_pin(session["freeze_fence"]) >= reader.verify_pin(proof["sessions"][lane]["freeze_fence"]),
                "CHILD_SERVICE_ORIGINAL_FENCE_CHANGED")
    require(0 < len(value["native_authorizations"]) <= 8, "ORIGINAL_NATIVE_CHILD_AUTHORIZATION_BOUND")
    authorize(value["native_authorizations"])
    require(value["authority"]["path"] == str(root / "authority.json") and authority_value(value["authority"]) == dict(
        schema="upgrade-child-budget-authority-v1", task_id=value["task_id"], ledger_root=str(root / "ledger"),
        max_inflight=2, budget_scope="WHOLE_TASK", parents={lane: value["sessions"][lane]["session_id"]
                                                        for lane in ("claude_a", "claude_b")}),
        "CHILD_SERVICE_AUTHORITY_CHANGED")
    budget_status(value["authority"])
    return value, runtime


@contract_errors("native child hook transaction")
def handle_event(registration, packet, *, peer, process_reader=None, fixtures=False):
    """Python-only transaction boundary; live service authenticates registration.

    Offline fixtures use an explicit seam here. Production never accepts a
    supplied registration or process reader from the client packet.
    """
    require(set(packet) == {"schema", "lane", "payload"} and packet["schema"] == SCHEMA,
            "CHILD_NATIVE_IPC_FIELDS_CHANGED")
    lane, payload = packet["lane"], packet["payload"]
    require(lane in registration["sessions"] and isinstance(payload, dict),
            "CHILD_ORIGINAL_PARENT_NOT_REGISTERED")
    session = registration["sessions"][lane]
    authenticate_hook_peer(peer, session=session, account_home=registration["account_home"],
        python=registration["python"], controller=registration["controller"], process_reader=process_reader)
    require(payload.get("session_id") == session["session_id"]
            and payload.get("transcript_path") == session["transcript"]["path"],
            "CHILD_NATIVE_HOOK_ORIGINAL_SESSION_REQUIRED")
    event = payload.get("hook_event_name")
    require(event in {"PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop"},
            "CHILD_NATIVE_HOOK_EVENT_UNKNOWN")
    authority = registration["authority"]
    if event == "PreToolUse" and payload.get("tool_name") == "Bash":
        require(lane in {"claude_a", "claude_b"}, "CHILD_ORIGINAL_CLAUDE_ROOT_PARENT_REQUIRED")
        command = payload.get("tool_input", {}).get("command")
        consumption = parse_consumption(command, registration["python"], registration["controller"], registration["rtk"])
        require(consumption is not None, "CHILD_CONSUMPTION_COMMAND_REQUIRED")
        call_record = original_consumption_call(session, payload, command, fixtures=fixtures)
        claim_id = consumption["claim_id"]
        state = budget_status(authority)
        claim = state["claims"].get(claim_id)
        require(claim is not None and claim["parent"] == lane and "report" in claim,
                "CHILD_REPORT_NOT_READY_FOR_ORIGINAL_PARENT")
        reader_command = read_command(registration["python"], registration["controller"],
                                      claim["report"]["notice"], registration["rtk"])
        result = consume_child_report(authority, claim_id, parent=lane, parent_session=session,
            command=reader_command, reply_sha256=consumption["reply_sha256"],
            consumption_call=call_record, fixtures=fixtures)
        receipt_path = Path(registration["report_root"]) / (claim_id + "-consumed.json")
        receipt = dict(schema="upgrade-original-child-consumed-v1", claim_id=claim_id, parent=lane,
            reply_sha256=consumption["reply_sha256"], consumption_call=call_record,
            full_read=budget_status(authority)["claims"][claim_id]["consumption"], input_operations=0)
        if not receipt_path.exists():
            write_json(receipt_path, receipt, exclusive=True)
            receipt_path.chmod(0o444)
        return dict(status="OBSERVED", claim_id=claim_id, consumption_status=result["status"],
                    inflight=result["inflight"], hook_output=dict(hookSpecificOutput=dict(
                        hookEventName=event, additionalContext=
                        "Original child complete-body read and exact-SHA consumption verified; one slot released.")))
    if event in {"SubagentStart", "SubagentStop"}:
        # This observation cannot create/release capacity. Completion support
        # is added only with separately tested original child-source binding.
        return dict(status="OBSERVED", inflight=budget_status(authority)["inflight"], hook_output={})
    require(payload.get("tool_name") == "Agent" and lane in {"claude_a", "claude_b"}
            and payload.get("agent_id") is None, "CHILD_ORIGINAL_CLAUDE_ROOT_PARENT_REQUIRED")
    claim_id = object_sha(dict(session_id=session["session_id"], tool_use_id=payload["tool_use_id"]))
    call_path = Path(registration["report_root"]) / (claim_id + "-call.json")
    if event == "PreToolUse":
        require(payload.get("tool_input", {}).get("run_in_background", False) is False,
                "CHILD_NATIVE_BACKGROUND_UNSUPPORTED: completion binding is not proven")
        call = original_agent_call(session, payload, fixtures=fixtures)
        reservation = reserve_child(authority, parent=lane, tool_use_id=call["tool_use_id"], tool_input=call["tool_input"])
        require(reservation["status"] == "RESERVED",
                "ORIGINAL_CHILD_RESERVATION_ALREADY_ISSUED: preserve it; never replay Agent")
        write_json(call_path, call, exclusive=True)
        call_path.chmod(0o444)
        return dict(reservation, hook_output=updated_agent_input(call["tool_input"], claim_id))
    call = json_ref(file_ref(call_path), "original native child call")
    require(payload["tool_input"] in (call["tool_input"], updated_agent_input(call["tool_input"], claim_id)
            ["hookSpecificOutput"]["updatedInput"]), "ORIGINAL_CHILD_POST_INPUT_CHANGED")
    require(original_agent_call(session, dict(payload, hook_event_name="PreToolUse", tool_input=call["tool_input"]),
            fixtures=fixtures) == call, "ORIGINAL_CHILD_NATIVE_CALL_CHANGED")
    state = budget_status(authority)
    claim = state["claims"].get(claim_id)
    require(claim is not None and claim["parent"] == lane and claim["tool_input_sha256"] == object_sha(call["tool_input"]),
            "ORIGINAL_CHILD_RESERVATION_REQUIRED")
    completion = native_agent_completion(session, call, payload.get("tool_response"), fixtures=fixtures)
    child_path = (Path(session["transcript"]["path"]).parent / session["session_id"] / "subagents"
                  / ("agent-" + completion["agent_id"] + ".jsonl"))
    report = child_report(session, call, claim_id=claim_id, agent_id=completion["agent_id"],
                          transcript_path=str(child_path), fixtures=fixtures)
    require(parse_time(report["terminal_timestamp"]) <= parse_time(completion["completion_timestamp"]),
            "ORIGINAL_NATIVE_CHILD_COMPLETION_CHRONOLOGY_CHANGED")
    response = payload["tool_response"]
    require(response.get("content") == [{"type": "text", "text": report["body"]}],
            "ORIGINAL_CHILD_REPORT_BODY_DIFFERENT: note is not the complete child result")
    body = "STATUS: child-" + claim_id + "\n" + report["body"]
    prepared = plan(body, "child-" + claim_id, Path(registration["report_root"]))
    persist(prepared["reference"], body)
    result = record_child_report(authority, claim_id, parent=lane, notice=prepared["text"])
    evidence_path = Path(registration["report_root"]) / (claim_id + "-native.json")
    evidence = dict(schema="upgrade-original-native-child-report-v1", claim_id=claim_id,
        parent=lane, call=call, completion=completion, report=report, reference=prepared["reference"], input_operations=0)
    if not evidence_path.exists():
        write_json(evidence_path, evidence, exclusive=True)
        evidence_path.chmod(0o444)
    command = read_command(registration["python"], registration["controller"], prepared["text"], registration["rtk"])
    context = ("Original child report is available but its slot remains occupied. Read its complete body with: "
               + command + "\nThen consume this exact SHA only after the original parent native read: "
               + consume_command(registration["python"], registration["controller"], claim_id,
                                 prepared["reference"]["sha256"], registration["rtk"]))
    return dict(status="OBSERVED", claim_id=claim_id, inflight=result["inflight"],
                hook_output=dict(hookSpecificOutput=dict(hookEventName=event, additionalContext=context)))


@contract_errors("native child IPC packet")
def read_packet(channel):
    raw = bytearray()
    while True:
        chunk = channel.recv(min(16384, MAX_PACKET + 1 - len(raw)))
        if not chunk:
            break
        raw += chunk
        require(len(raw) <= MAX_PACKET, "CHILD_IPC_PACKET_BOUND_EXCEEDED")
    require(raw.endswith(b"\n") and raw.count(b"\n") == 1, "CHILD_IPC_EXACT_COMPLETE_PACKET_REQUIRED")
    value = strict_json(raw, "native child packet")
    require(isinstance(value, dict) and set(value) == {"schema", "lane", "payload"}
            and value["schema"] == SCHEMA and value["lane"] in {"codex", "claude_a", "claude_b"}
            and isinstance(value["payload"], dict), "CHILD_NATIVE_IPC_FIELDS_CHANGED")
    return value


def serve(registration_ref, *, timeout_seconds):
    """One bounded external allocator invocation; no automatic restart/reset.

    The socket and serve-intent are create-only. A crash leaves both and the
    original occupied ledger for diagnosis; it never authorizes a replacement
    service or new native child. Stop/timeout closes IPC without releasing slots.
    """
    from upgrade_live_runtime import native_authority_observation
    from upgrade_live_control import external_publisher
    require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 3600,
            "CHILD_SERVICE_BOUNDED_DURATION_REQUIRED")
    with native_authority_observation():
        registration, runtime = validate_service_registration(registration_ref)
    startup = json_ref(runtime["startup"])
    external_publisher(startup)
    require(not os.path.lexists(startup["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    endpoint = checked_path(registration["socket"])
    require(not os.path.lexists(endpoint), "ORIGINAL_CHILD_SERVICE_SOCKET_EXISTS: do not replace it")
    root = Path(registration["output_root"])
    intent = write_json(root / "serve-intent.json", dict(schema="upgrade-original-child-service-intent-v1",
        registration=registration_ref, pid=os.getpid(), started_at=utc_now(), input_operations=0,
        timeout_seconds=timeout_seconds), exclusive=True)
    Path(intent["path"]).chmod(0o444)
    deadline = time.monotonic() + timeout_seconds
    handled = 0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        endpoint.chmod(0o600)
        socket_identity = endpoint.lstat()
        require(stat.S_ISSOCK(socket_identity.st_mode) and socket_identity.st_uid == os.getuid(),
                "CHILD_SERVICE_SOCKET_IDENTITY_CHANGED")
        listener.listen(16)
        listener.settimeout(0.5)
        print(json.dumps(dict(status="CHILD_SERVICE_READY_NOT_ADOPTED", registration=registration_ref,
                              socket=str(endpoint), pid=os.getpid(), model_requests=0, input_operations=0)), flush=True)
        while (time.monotonic() < deadline and handled < MAX_SERVICE_EVENTS
               and not os.path.lexists(startup["stop_path"])):
            current = endpoint.lstat()
            require((current.st_dev, current.st_ino, current.st_mode, current.st_uid) == (
                socket_identity.st_dev, socket_identity.st_ino, socket_identity.st_mode, socket_identity.st_uid),
                "CHILD_SERVICE_SOCKET_REPLACED")
            try:
                channel, _ = listener.accept()
            except TimeoutError:
                continue
            with channel:
                channel.settimeout(min(8, max(0.001, deadline - time.monotonic())))
                peer, packet, result = None, None, None
                try:
                    peer = kernel_peer(channel)
                    packet = read_packet(channel)
                    with native_authority_observation():
                        latest, _ = validate_service_registration(registration_ref)
                        require(latest == registration, "CHILD_SERVICE_REGISTRATION_CHANGED")
                    # The verification cache is read-only. Native permission
                    # decisions and ledger writes must independently reread
                    # their original authority outside that scope.
                    require(not os.path.lexists(startup["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
                    require(time.monotonic() < deadline, "CHILD_SERVICE_DURATION_EXPIRED")
                    result = handle_event(registration, packet, peer=peer)
                except (ValueError, KeyError, OSError, RuntimeError) as exc:
                    result = dict(status="REFUSED", error=str(exc), model_requests=0, input_operations=0)
                handled += 1
                observation = dict(schema="upgrade-native-child-service-event-v1", registration=registration_ref,
                    peer=peer, payload_sha256=object_sha(packet) if packet is not None else None,
                    result=result, observed_at=utc_now(), input_operations=0)
                ref = write_json(root / "events" / (f"{handled:06d}-" + uuid.uuid4().hex + ".json"),
                                 observation, exclusive=True)
                Path(ref["path"]).chmod(0o444)
                try:
                    channel.sendall(encode_packet(result))
                except (OSError, ValueError):
                    # The reservation remains even when its response is lost.
                    pass
    # Never unlink the create-only endpoint or reset intent/ledger on exit.
    return dict(status="CHILD_SERVICE_STOPPED_PRESERVED", handled=handled,
                registration=registration_ref, input_operations=0, model_requests=0,
                inflight=budget_status(registration["authority"])["inflight"])


def main(argv=None):
    from orchestration_campaign import anchored_ref
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register", allow_abbrev=False)
    register.add_argument("--request", required=True)
    register.add_argument("--request-sha256", required=True)
    for action in ("serve", "status"):
        command = commands.add_parser(action, allow_abbrev=False)
        command.add_argument("--registration", required=True)
        command.add_argument("--registration-sha256", required=True)
        if action == "serve":
            command.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            request = json_ref(anchored_ref(args.request, args.request_sha256))
            require(set(request) == {"schema", "runtime", "adoption", "task_id", "native_authorizations",
                                     "child_budget_authorized"}
                    and request["schema"] == "upgrade-native-child-service-register-request-v1",
                    "CHILD_SERVICE_REGISTER_REQUEST_CHANGED")
            ref = register_service(request["runtime"], request["adoption"], task_id=request["task_id"],
                native_authorizations=request["native_authorizations"], child_budget_authorized=request["child_budget_authorized"],
                source=tree_manifest(Path(__file__).resolve().parent.parent))
            value = dict(status="CHILD_SERVICE_REGISTERED_NOT_STARTED", registration=ref, input_operations=0)
        else:
            ref = anchored_ref(args.registration, args.registration_sha256)
            if args.command == "serve":
                value = serve(ref, timeout_seconds=args.timeout_seconds)
            else:
                registration, _ = validate_service_registration(ref)
                value = budget_status(registration["authority"])
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print(json.dumps(dict(status="CHILD_SERVICE_REFUSED_PRESERVED", error=str(exc), input_operations=0)), flush=True)
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
