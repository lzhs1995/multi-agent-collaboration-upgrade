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
import re
import socket
import stat
import sys
import threading
import time
import uuid

from cmux_prompt_reference import plan, persist
from orchestration_common import (checked_path, contract_errors, digest, json_ref, file_ref, object_sha,
    parse_time, read_ref, require, strict_json, tree_manifest, utc_now, verify_tree, write_json)
from upgrade_body_read import read_command
from upgrade_child_budget import (authority_value, budget_status, initialize_ledger,
    consume_child_report, record_child_report, reserve_child)
from upgrade_child_budget_protocol import (POLICY, SCHEMA, child_socket, consume_command,
    MAX_PACKET, encode_packet, parse_consumption, updated_agent_input)
from upgrade_child_native import (child_report, native_agent_completion, original_agent_call,
                                  current_original_agent_call, original_consumption_call)
from upgrade_child_peer import authenticate_hook_peer, kernel_peer, measured_python_executable

REGISTRATION_SCHEMA = "upgrade-native-child-service-registration-v1"
REGISTRATION_FIELDS = {"schema", "policy", "task_id", "runtime", "startup", "adoption", "source",
    "native_authorizations", "sessions", "authority", "output_root", "report_root", "socket",
    "account_home", "python", "controller", "rtk", "input_operations", "child_budget_authorized"}
MAX_SERVICE_EVENTS = 256
MAX_SUCCESSORS = 8
# The installed hook waits 10 s for a reply. A full registration check took
# 8.5-11 s under live load, so it runs beside the accept loop, not inside it.
# One private native-root authentication took 2.8-6.7 s at load 160-200 and an
# Agent hook needs two, so events reuse the reverifier's completed one.
REVERIFY_INTERVAL_SECONDS = 30
MAX_VERIFIED_AGE_SECONDS = 120
REPLY_BUDGET_SECONDS = 6
CALL_WAIT_MARGIN_SECONDS = 1.5


def service_root(runtime):
    # One registration per frozen runtime. Changing task_id cannot create a
    # parallel empty ledger beneath the same two original native parents.
    startup = json_ref(runtime["startup"])
    return checked_path(Path(startup["adoption_path"]).parent / "native-child-budget")


def registration_path(root, predecessor):
    # A successor sits beside its one predecessor and shares its ledger.
    if predecessor is None:
        return root / "registration.json"
    return root / "successors" / predecessor["sha256"] / "registration.json"


def _predecessor_chain(value, root):
    """Every link keeps authority, ledger, task, parents and sessions."""
    current = value
    for _ in range(MAX_SUCCESSORS):
        previous_ref = current.get("predecessor")
        if previous_ref is None:
            return
        previous = json_ref(previous_ref, "original child service predecessor")
        require(previous_ref["path"] == str(registration_path(root, previous.get("predecessor")))
                and {k: v for k, v in previous.items() if k not in ("source", "predecessor")}
                == {k: v for k, v in value.items() if k not in ("source", "predecessor")},
                "CHILD_SERVICE_PREDECESSOR_CHANGED")
        current = previous
    require(current.get("predecessor") is None, "CHILD_SERVICE_SUCCESSOR_BOUND")


@contract_errors("register original child service")
def _process_present(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contract_errors("register original child service successor")
def register_successor(predecessor_ref, *, source, process_present=_process_present):
    """Continue an exited service from a fixed source; never a new or reset ledger.

    Authority, ledger, task, parents, sessions and native authorizations stay
    those of the predecessor. Its socket is renamed, never unlinked, and only
    after its recorded process is gone and the socket refuses connections.
    Each predecessor has at most one successor.
    """
    from upgrade_live_runtime import native_authority_observation
    previous = json_ref(predecessor_ref, "original child service predecessor")
    with native_authority_observation():
        _, runtime = validate_service_registration(predecessor_ref, running_source=False)
    root = service_root(runtime)
    here = registration_path(root, predecessor_ref).parent
    require(not os.path.lexists(here), "CHILD_SERVICE_SUCCESSOR_EXISTS: one successor per predecessor")
    verify_tree(source)
    require(Path(source["path"]).resolve() == Path(__file__).resolve().parent.parent,
            "ORIGINAL_CHILD_SERVICE_SOURCE_REQUIRED")
    intent_ref = file_ref(Path(predecessor_ref["path"]).parent / "serve-intent.json")
    intent = json_ref(intent_ref, "original child service predecessor intent")
    require(intent.get("registration") == predecessor_ref and type(intent.get("pid")) is int,
            "CHILD_SERVICE_PREDECESSOR_INTENT_CHANGED")
    require(not process_present(intent["pid"]), "CHILD_SERVICE_PREDECESSOR_STILL_RUNNING")
    endpoint = Path(previous["socket"])
    socket_identity = None
    if os.path.lexists(endpoint):
        socket_identity = endpoint.lstat()
        require(stat.S_ISSOCK(socket_identity.st_mode) and socket_identity.st_uid == os.getuid(),
                "CHILD_SERVICE_SOCKET_IDENTITY_CHANGED")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            try:
                probe.connect(str(endpoint))
                accepting = True
            except ConnectionRefusedError:
                accepting = False
        require(not accepting, "CHILD_SERVICE_PREDECESSOR_SOCKET_ACCEPTING")
    value = dict(previous, source=source, predecessor=predecessor_ref)
    here.mkdir(parents=True, mode=0o700, exist_ok=False)
    retired = None
    if socket_identity is not None:
        retired = endpoint.with_name(endpoint.name + ".retired-" + predecessor_ref["sha256"][:16])
        require(not os.path.lexists(retired), "CHILD_SERVICE_RETIRED_SOCKET_EXISTS")
        current = endpoint.lstat()
        require((current.st_dev, current.st_ino) == (socket_identity.st_dev, socket_identity.st_ino),
                "CHILD_SERVICE_SOCKET_REPLACED")
        os.rename(endpoint, retired)
    exit_ref = write_json(here / "predecessor-exit.json", dict(schema="upgrade-original-child-service-predecessor-exit-v1",
        predecessor=predecessor_ref, intent=intent_ref, process_absent=intent["pid"],
        socket_refused=socket_identity is not None, retired_socket=str(retired) if retired else None,
        observed_at=utc_now(), input_operations=0), exclusive=True)
    Path(exit_ref["path"]).chmod(0o444)
    ref = write_json(here / "registration.json", value, exclusive=True)
    Path(ref["path"]).chmod(0o444)
    return ref


def register_service(runtime_ref, adoption_ref, *, task_id, native_authorizations, source,
                     child_budget_authorized):
    from orchestration_freeze_v3 import authorize
    from upgrade_continuity_native import registered_sessions
    from upgrade_live_control import external_publisher
    from upgrade_live_runtime import session_authority, session_controller, validate_session_authority
    from upgrade_startup_contract import child_budget_enabled
    require(child_budget_authorized is True, "ORIGINAL_NATIVE_CHILD_AUTHORIZATION_REQUIRED")
    _, runtime, proof = session_authority(runtime_ref, adoption_ref)
    startup = json_ref(runtime["startup"])
    controller = session_controller(runtime, proof)
    binding = json_ref(file_ref(Path(controller) / "upgrade-binding.json"))
    require(child_budget_enabled(binding), "CHILD_NATIVE_HOOK_NOT_REGISTERED")
    external_publisher(startup)
    root = service_root(runtime)
    require(not os.path.lexists(root), "ORIGINAL_CHILD_SERVICE_REGISTRATION_EXISTS: do not reset it")
    records = proof["native_authorizations"] if proof.get("schema") == "upgrade-startup-continuity-v1" else authorize(native_authorizations)
    if proof.get("schema") == "upgrade-startup-continuity-v1":
        require(task_id == proof["task_id"] and native_authorizations == records,
                "STARTUP_CONTINUITY_CHILD_AUTHORIZATION_CHANGED")
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
    if proof.get("schema") == "upgrade-startup-continuity-v1":
        require(validate_session_authority(runtime_ref, adoption_ref) == proof, "STARTUP_CONTINUITY_CHANGED")
    else:
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
        python=runtime["python"]["path"], controller=controller,
        rtk=startup["pins"]["rtk"]["path"], input_operations=0, child_budget_authorized=True)
    ref = write_json(root / "registration.json", value, exclusive=True)
    Path(ref["path"]).chmod(0o444)
    return ref


@contract_errors("original child service registration")
def validate_service_registration(ref, *, running_source=True):
    # running_source=False checks an exited predecessor's own pinned tree, not
    # that it is this running source; every other authority check is unchanged.
    from orchestration_freeze_v3 import authorize
    from orchestration_native import NativeReader
    from upgrade_live_runtime import identity_matches, session_authority, session_controller
    from upgrade_startup_contract import child_budget_enabled
    value = json_ref(ref, "original native child service registration")
    require(set(value) in (REGISTRATION_FIELDS, REGISTRATION_FIELDS | {"predecessor"})
            and value["schema"] == REGISTRATION_SCHEMA
            and value["policy"] == POLICY and value["child_budget_authorized"] is True
            and type(value["input_operations"]) is int and value["input_operations"] == 0,
            "CHILD_SERVICE_REGISTRATION_FIELDS_CHANGED")
    _, runtime, proof = session_authority(value["runtime"], value["adoption"], allow_stopped=True)
    startup = json_ref(runtime["startup"])
    controller = session_controller(runtime, proof)
    binding = json_ref(file_ref(Path(controller) / "upgrade-binding.json"))
    require(child_budget_enabled(binding), "CHILD_NATIVE_HOOK_NOT_REGISTERED")
    root = service_root(runtime)
    _predecessor_chain(value, root)
    require(ref["path"] == str(registration_path(root, value.get("predecessor"))) and value["output_root"] == str(root)
            and value["report_root"] == str(root / "reports") and value["startup"] == runtime["startup"]
            and value["controller"] == controller
            and value["python"] == runtime["python"]["path"]
            and value["rtk"] == startup["pins"]["rtk"]["path"]
            and value["socket"] == str(child_socket({"tmp": binding["tmp_root"]}))
            and value["account_home"] == str(Path.home().resolve()), "CHILD_SERVICE_SCOPE_CHANGED")
    verify_tree(value["source"])
    require(not running_source or Path(value["source"]["path"]).resolve() == Path(__file__).resolve().parent.parent,
            "CHILD_SERVICE_SOURCE_CHANGED")
    require(set(value["sessions"]) == {"codex", "claude_a", "claude_b"}, "CHILD_SERVICE_ORIGINAL_LANES_CHANGED")
    for lane, session in value["sessions"].items():
        require(identity_matches(session, proof["sessions"][lane])
                and session.get("private_native") == dict(runtime=value["runtime"], adoption=value["adoption"]),
                "CHILD_SERVICE_ORIGINAL_SESSION_CHANGED")
        reader = NativeReader(session)
        require(reader.verify_pin(session["freeze_fence"]) >= reader.verify_pin(proof["sessions"][lane]["freeze_fence"]),
                "CHILD_SERVICE_ORIGINAL_FENCE_CHANGED")
    require(0 < len(value["native_authorizations"]) <= 8, "ORIGINAL_NATIVE_CHILD_AUTHORIZATION_BOUND")
    if proof.get("schema") == "upgrade-startup-continuity-v1":
        require(value["task_id"] == proof["task_id"]
                and value["native_authorizations"] == proof["native_authorizations"],
                "STARTUP_CONTINUITY_CHILD_AUTHORIZATION_CHANGED")
    else:
        authorize(value["native_authorizations"])
    require(value["authority"]["path"] == str(root / "authority.json") and authority_value(value["authority"]) == dict(
        schema="upgrade-child-budget-authority-v1", task_id=value["task_id"], ledger_root=str(root / "ledger"),
        max_inflight=2, budget_scope="WHOLE_TASK", parents={lane: value["sessions"][lane]["session_id"]
                                                        for lane in ("claude_a", "claude_b")}),
        "CHILD_SERVICE_AUTHORITY_CHANGED")
    budget_status(value["authority"])
    return value, runtime


def _completed_child_report(session, call, claim_id, agent_id, *, fixtures=False):
    child_path = (Path(session["transcript"]["path"]).parent / session["session_id"] / "subagents"
                  / ("agent-" + agent_id + ".jsonl"))
    return child_report(session, call, claim_id=claim_id, agent_id=agent_id,
                        transcript_path=str(child_path), fixtures=fixtures)


def _report_context(registration, claim_id, prepared):
    command = read_command(registration["python"], registration["controller"], prepared["text"], registration["rtk"])
    return ("Original child report is available but its slot remains occupied. Read its complete body with: "
            + command + "\nThen consume this exact SHA only after the original parent native read: "
            + consume_command(registration["python"], registration["controller"], claim_id,
                              prepared["reference"]["sha256"], registration["rtk"]))


def _publish_child_report(registration, lane, call, claim_id, report):
    """Pin one complete child body; availability never releases its slot."""
    body = "STATUS: child-" + claim_id + "\n" + report["body"]
    prepared = plan(body, "child-" + claim_id, Path(registration["report_root"]))
    persist(prepared["reference"], body)
    result = record_child_report(registration["authority"], claim_id, parent=lane, notice=prepared["text"])
    evidence_path = Path(registration["report_root"]) / (claim_id + "-native.json")
    evidence = dict(schema="upgrade-original-native-child-report-v2", claim_id=claim_id, parent=lane,
        call=call, report=report, reference=prepared["reference"], input_operations=0)
    if not evidence_path.exists():
        write_json(evidence_path, evidence, exclusive=True)
        evidence_path.chmod(0o444)
    return prepared, result


def _bound_completion(completion, report):
    """The parent's own native result names this child and carries its report."""
    require(completion["agent_id"] == report["agent_id"]
            and completion["content"] == [{"type": "text", "text": report["body"]}],
            "ORIGINAL_CHILD_REPORT_BODY_DIFFERENT: note is not the complete child result")
    require(parse_time(report["terminal_timestamp"]) <= parse_time(completion["completion_timestamp"]),
            "ORIGINAL_NATIVE_CHILD_COMPLETION_CHRONOLOGY_CHANGED")
    return completion


@contract_errors("recover original child reports")
def recover_child_reports(registration, *, fixtures=False):
    """Publish reports whose PostToolUse ran before the parent result existed.

    Only a RESERVED claim with its original call record, a completed native
    parent result and the child's own terminal report is published. Capacity is
    not released; a late-report file names the same read and consume commands.
    """
    recovered, pending = [], {}
    for claim_id, claim in sorted(budget_status(registration["authority"])["claims"].items()):
        if claim["state"] != "RESERVED" or "report" in claim:
            continue
        lane = claim["parent"]
        session = registration["sessions"][lane]
        try:
            call = json_ref(file_ref(Path(registration["report_root"]) / (claim_id + "-call.json")),
                            "original native child call")
            require(claim_id == object_sha(dict(session_id=session["session_id"], tool_use_id=call["tool_use_id"]))
                    and claim["tool_input_sha256"] == object_sha(call["tool_input"]),
                    "ORIGINAL_CHILD_RESERVATION_REQUIRED")
            payload = dict(hook_event_name="PreToolUse", session_id=session["session_id"],
                           transcript_path=session["transcript"]["path"], tool_name="Agent",
                           tool_use_id=call["tool_use_id"], tool_input=call["tool_input"])
            require(original_agent_call(session, payload, fixtures=fixtures) == call, "ORIGINAL_CHILD_NATIVE_CALL_CHANGED")
            completion = native_agent_completion(session, call, fixtures=fixtures)
            report = _completed_child_report(session, call, claim_id, completion["agent_id"], fixtures=fixtures)
            _bound_completion(completion, report)
            prepared, _ = _publish_child_report(registration, lane, call, claim_id, report)
        except (ValueError, KeyError, OSError) as exc:
            pending[claim_id] = str(exc)
            continue
        late_path = Path(registration["report_root"]) / (claim_id + "-late-report.json")
        if not late_path.exists():
            write_json(late_path, dict(schema="upgrade-original-child-late-report-v1", claim_id=claim_id,
                parent=lane, reference=prepared["reference"], completion=completion,
                context=_report_context(registration, claim_id, prepared), input_operations=0), exclusive=True)
            late_path.chmod(0o444)
        recovered.append(dict(claim_id=claim_id, parent=lane, late_report=str(late_path)))
    return dict(recovered=recovered, pending=pending)


@contract_errors("native child hook transaction")
def handle_event(registration, packet, *, peer, process_reader=None, fixtures=False, reply_deadline=None,
                 native_python=None):
    """Python-only transaction boundary; live service authenticates registration.

    Offline fixtures use an explicit seam here. Production never accepts a
    supplied registration or process reader from the client packet. After
    reply_deadline the waiting hook has already denied its tool call, so no
    reservation or consumption may still be recorded for it.
    """
    def in_reply_budget():
        require(reply_deadline is None or time.monotonic() < reply_deadline,
                "CHILD_SERVICE_REPLY_BUDGET_EXCEEDED: the hook stopped waiting; no slot changed")
    require(set(packet) == {"schema", "lane", "payload"} and packet["schema"] == SCHEMA,
            "CHILD_NATIVE_IPC_FIELDS_CHANGED")
    lane, payload = packet["lane"], packet["payload"]
    require(lane in registration["sessions"] and isinstance(payload, dict),
            "CHILD_ORIGINAL_PARENT_NOT_REGISTERED")
    session = registration["sessions"][lane]
    authenticate_hook_peer(peer, session=session, account_home=registration["account_home"],
        python=registration["python"], controller=registration["controller"], process_reader=process_reader,
        native_python=native_python)
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
        # The parent's native result exists only after its PostToolUse returned;
        # no slot is released before it names this child and carries this body.
        call = json_ref(file_ref(Path(registration["report_root"]) / (claim_id + "-call.json")),
                        "original native child call")
        completion = native_agent_completion(session, call, fixtures=fixtures)
        report = _completed_child_report(session, call, claim_id, completion["agent_id"], fixtures=fixtures)
        _bound_completion(completion, report)
        require(digest(("STATUS: child-" + claim_id + "\n" + report["body"]).encode())
                == claim["report"]["reference"]["sha256"],
                "ORIGINAL_CHILD_REPORT_BODY_DIFFERENT: note is not the complete child result")
        reader_command = read_command(registration["python"], registration["controller"],
                                      claim["report"]["notice"], registration["rtk"])
        in_reply_budget()
        result = consume_child_report(authority, claim_id, parent=lane, parent_session=session,
            command=reader_command, reply_sha256=consumption["reply_sha256"],
            consumption_call=call_record, fixtures=fixtures)
        receipt_path = Path(registration["report_root"]) / (claim_id + "-consumed.json")
        receipt = dict(schema="upgrade-original-child-consumed-v1", claim_id=claim_id, parent=lane,
            reply_sha256=consumption["reply_sha256"], consumption_call=call_record, completion=completion,
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
        # Wait for a not-yet-appended original call only while the hook still
        # waits, leaving the reservation and its reply inside the same budget.
        call = current_original_agent_call(session, payload, fixtures=fixtures, wait_until=(
            None if reply_deadline is None else reply_deadline - CALL_WAIT_MARGIN_SECONDS))
        in_reply_budget()
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
    # The parent's native result is not persisted until this hook returns. The
    # hook response only names the child; its own transcript is the report, and
    # consumption later binds the parent result before any slot is released.
    response = payload.get("tool_response")
    require(isinstance(response, dict) and response.get("status") == "completed"
            and re.fullmatch(r"a[0-9a-f]{16}", response.get("agentId", "")) is not None,
            "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED")
    report = _completed_child_report(session, call, claim_id, response["agentId"], fixtures=fixtures)
    prepared, result = _publish_child_report(registration, lane, call, claim_id, report)
    return dict(status="OBSERVED", claim_id=claim_id, inflight=result["inflight"],
                hook_output=dict(hookSpecificOutput=dict(hookEventName=event,
                    additionalContext=_report_context(registration, claim_id, prepared))))


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


class _Reverifier:
    """Repeat the full registration check beside the accept loop.

    The first failure latches and refuses every later event. Each event still
    rereads the pinned registration bytes and its own ledger authority, and
    authenticates native roots only from the latest completed check.
    """

    def __init__(self, registration_ref, registration, stopped, snapshot):
        self.ref, self.registration, self.stopped = registration_ref, registration, stopped
        self.lock, self.done = threading.Lock(), threading.Event()
        self.verified_at, self.failure, self.snapshot = time.monotonic(), None, snapshot
        self.thread = threading.Thread(target=self.run, name="child-service-reverifier", daemon=True)

    def run(self):
        from upgrade_live_runtime import native_authority_observation
        while not self.done.wait(REVERIFY_INTERVAL_SECONDS):
            try:
                published = []
                with native_authority_observation(publish=published.append):
                    latest, _ = validate_service_registration(self.ref)
                require(latest == self.registration, "CHILD_SERVICE_REGISTRATION_CHANGED")
                require(not self.stopped(), "EXPERIMENT_STOP_REQUESTED")
                with self.lock:
                    self.verified_at, self.snapshot = time.monotonic(), published[0]
            except Exception as exc:  # any failure must close the service, never pass silently
                with self.lock:
                    self.failure = str(exc) or type(exc).__name__
                return

    def require_current(self):
        with self.lock:
            failure, verified_at, snapshot = self.failure, self.verified_at, self.snapshot
        require(failure is None, "CHILD_SERVICE_REVERIFICATION_FAILED: " + str(failure))
        require(time.monotonic() - verified_at < MAX_VERIFIED_AGE_SECONDS, "CHILD_SERVICE_VERIFICATION_STALE")
        require(json_ref(self.ref, "original native child service registration") == self.registration,
                "CHILD_SERVICE_REGISTRATION_CHANGED")
        # Pinned authority files are small; reread them for every grant.
        for key in ("runtime", "startup", "adoption", "authority"):
            read_ref(self.registration[key], "original child service " + key)
        return snapshot


def serve(registration_ref, *, timeout_seconds):
    """One bounded external allocator invocation; no automatic restart/reset.

    The socket and serve-intent are create-only. A crash leaves both and the
    original occupied ledger for diagnosis; it never authorizes a replacement
    service or new native child. Stop/timeout closes IPC without releasing slots.
    """
    from upgrade_live_runtime import native_authority_observation, verified_native_authority
    from upgrade_live_control import external_publisher
    require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 3600,
            "CHILD_SERVICE_BOUNDED_DURATION_REQUIRED")
    published = []
    with native_authority_observation(publish=published.append):
        registration, runtime = validate_service_registration(registration_ref)
    startup = json_ref(runtime["startup"])
    external_publisher(startup)
    # A fenced stage adds its own stop beside the original experiment stop.
    from upgrade_stage_fence import stop_paths
    stops = [startup["stop_path"], *map(str, stop_paths(json_ref(file_ref(
        Path(registration["controller"]) / "upgrade-binding.json"))))]
    stopped = lambda: any(map(os.path.lexists, stops))
    # Hooks are started as the pinned Python; record what the kernel reports.
    native_python = measured_python_executable(registration["python"])
    require(not stopped(), "EXPERIMENT_STOP_REQUESTED")
    # Reports whose PostToolUse ran before the parent result existed; this
    # publishes availability only and releases no slot.
    with verified_native_authority(published[0]):
        recovery = recover_child_reports(registration)
    endpoint = checked_path(registration["socket"])
    require(not os.path.lexists(endpoint), "ORIGINAL_CHILD_SERVICE_SOCKET_EXISTS: do not replace it")
    # Intent and events sit beside this registration; a successor keeps its
    # predecessor's records unchanged.
    root = Path(registration_ref["path"]).parent
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
                              socket=str(endpoint), pid=os.getpid(), recovery=recovery,
                              model_requests=0, input_operations=0)), flush=True)
        reverifier = _Reverifier(registration_ref, registration, stopped, published[0])
        reverifier.thread.start()
        try:
            while (time.monotonic() < deadline and handled < MAX_SERVICE_EVENTS
                   and not stopped()):
                current = endpoint.lstat()
                require((current.st_dev, current.st_ino, current.st_mode, current.st_uid) == (
                    socket_identity.st_dev, socket_identity.st_ino, socket_identity.st_mode, socket_identity.st_uid),
                    "CHILD_SERVICE_SOCKET_REPLACED")
                try:
                    channel, _ = listener.accept()
                except TimeoutError:
                    continue
                accepted = time.monotonic()
                with channel:
                    channel.settimeout(min(8, max(0.001, deadline - time.monotonic())))
                    peer, packet, result = None, None, None
                    try:
                        peer = kernel_peer(channel)
                        packet = read_packet(channel)
                        # The full registration check runs in the reverifier;
                        # each event rereads the pinned registration bytes.
                        # Native roots are authenticated from its latest
                        # completed check; ledger writes reread their authority.
                        snapshot = reverifier.require_current()
                        require(not stopped(), "EXPERIMENT_STOP_REQUESTED")
                        require(time.monotonic() < deadline, "CHILD_SERVICE_DURATION_EXPIRED")
                        with verified_native_authority(snapshot):
                            result = handle_event(registration, packet, peer=peer, native_python=native_python,
                                                  reply_deadline=accepted + REPLY_BUDGET_SECONDS)
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
        finally:
            reverifier.done.set()
            reverifier.thread.join()
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
    successor = commands.add_parser("register-successor", allow_abbrev=False)
    successor.add_argument("--predecessor", required=True)
    successor.add_argument("--predecessor-sha256", required=True)
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
        elif args.command == "register-successor":
            ref = register_successor(anchored_ref(args.predecessor, args.predecessor_sha256),
                                     source=tree_manifest(Path(__file__).resolve().parent.parent))
            value = dict(status="CHILD_SERVICE_SUCCESSOR_REGISTERED_NOT_STARTED", registration=ref, input_operations=0)
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
