"""New task-thread bootstrap over an already bound official server connection.

The trusted external launcher supplies the frozen new-task plan and the actual
TUI-first TaskRPC connection. This does not launch a client, bypass held/CAS/
release, publish adoption or authorize old campaigns. A new attempt owns one
exclusive directory; any uncertain thread/turn request consumes that attempt.
The visible TUI may resume the returned ID only after the journal is pinned.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import time
import uuid

from orchestration_common import (checked_path, contract_errors, digest, json_ref,
    object_sha, read_ref, require, strict_json, write_json)
from upgrade_rpc_peer import verify_peer
from upgrade_task_creation import CREATION_SCHEMA, exchange, validate_creation
from upgrade_task_operations import OperationLedger, tool_definition
from upgrade_task_thread import journal_evidence

PLAN_SCHEMA = "upgrade-task-startup-plan-v1"


def plan_value(ref):
    value = json_ref(ref, "new task bootstrap plan")
    require(set(value) == {"schema", "task_id", "controller_id", "authority_ref", "native_root",
            "work_root", "model", "model_provider", "startup_text", "base_instructions", "operations"}
            and value["schema"] == PLAN_SCHEMA, "TASK_STARTUP_PLAN_INVALID")
    read_ref(value["authority_ref"], "original new task authority")
    for key in ("task_id", "controller_id", "model", "model_provider", "startup_text", "base_instructions"):
        require(isinstance(value[key], str) and 0 < len(value[key].encode()) <= 256 * 1024,
                "TASK_STARTUP_TEXT_INVALID")
    for key in ("native_root", "work_root"):
        require(checked_path(value[key]).is_dir(), "TASK_STARTUP_ROOT_MISSING")
    # Reuse the same spec validation before any thread or model turn starts.
    from upgrade_task_operations import validate_registration
    validate_registration(dict(schema="upgrade-task-operations-v1", task_id=value["task_id"],
        controller_id=value["controller_id"], authority_ref=value["authority_ref"],
        thread_id="00000000-0000-4000-8000-000000000001",
        turn_id="00000000-0000-4000-8000-000000000002", operations=value["operations"]))
    return value


def _journal_anchor(client, plan, created, creation, *, timeout):
    """Pin only the journal named by the original thread/start response."""
    path = checked_path(created["thread"]["path"])
    root = checked_path(plan["native_root"])
    require(path.is_relative_to(root / "sessions"), "TASK_STARTUP_JOURNAL_OUTSIDE_ROOT")
    deadline = time.monotonic() + timeout
    while True:
        try:
            before = path.lstat()
        except FileNotFoundError:
            before = None
        if before is not None:
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                    and not before.st_mode & 0o022, "TASK_STARTUP_JOURNAL_IDENTITY_INVALID")
            journal = dict(path=str(path), device=before.st_dev, inode=before.st_ino)
            refs, offset, metadata = [], 0, None
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
                opened = os.fstat(stream.fileno())
                require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                        "TASK_STARTUP_JOURNAL_REPLACED")
                while offset < min(opened.st_size, 16 * 1024 * 1024):
                    raw = stream.readline(2 * 1024 * 1024 + 1)
                    if not raw.endswith(b"\n"):
                        break  # A still-appending original record is not an input.
                    require(len(raw) <= 2 * 1024 * 1024, "TASK_STARTUP_JOURNAL_RECORD_TOO_LARGE")
                    item = strict_json(raw)
                    ref = dict(journal, offset=offset, length=len(raw), sha256=digest(raw))
                    if offset == 0:
                        metadata = ref
                    payload = item.get("payload", {})
                    if item.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "user":
                        content = payload.get("content", [])
                        if (isinstance(content, list) and content
                                and all(isinstance(x, dict) and x.get("type") == "input_text" for x in content)
                                and "".join(x.get("text", "") for x in content) == plan["startup_text"]):
                            refs.append(ref)
                    offset += len(raw)
            require(len(refs) <= 1, "TASK_STARTUP_INPUT_AMBIGUOUS")
            if metadata and refs:
                anchor = dict(schema="upgrade-task-thread-read-anchor-v1", thread_id=created["thread"]["id"],
                    native_root=str(root), journal=journal, metadata=metadata, startup_input=refs[0],
                    startup_text=plan["startup_text"], server=client.server,
                    socket=client.socket_identity)
                observed = journal_evidence(anchor)
                original = validate_creation(creation, server=client.server, thread_id=anchor["thread_id"],
                    journal_path=str(path), native_root=str(root), startup_text=plan["startup_text"])
                require(observed["startup_turn"] == original["turn_id"], "TASK_STARTUP_TURN_CHANGED")
                return anchor
        require(time.monotonic() < deadline, "TASK_STARTUP_JOURNAL_TIMEOUT_NO_REPLAY")
        time.sleep(min(0.05, max(0, deadline-time.monotonic())))


@contract_errors("TASK_STARTUP_REFUSED_NO_REPLAY")
def bootstrap_task(client, plan_ref, directory, *, journal_timeout=10):
    """One new thread and first turn; no handler executes during bootstrap.

    Pending native requests remain in TaskRPC until the caller has attached
    the exact TUI, proved deployment boundaries and enrolled the actor. The
    returned ledger is closed; reopening it does not grant another execution.
    """
    require(type(journal_timeout) in (int, float) and 0 < journal_timeout <= 20,
            "TASK_STARTUP_TIMEOUT_INVALID")
    plan = plan_value(plan_ref)
    verify_peer(client.sock, client.server, client.process)
    initialize = client.initialize_exchange
    init, result = exchange(initialize, "initialize", client.server)
    require(init.get("clientInfo", {}).get("name") == "cmux_native_input"
            and init.get("capabilities", {}).get("experimentalApi") is True
            and result.get("codexHome") == plan["native_root"]
            and isinstance(result.get("userAgent"), str) and result["userAgent"].startswith("codex-tui/"),
            "TASK_STARTUP_REAL_TUI_FIRST_REQUIRED")
    directory = checked_path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    write_json(directory / "intent.json", dict(plan=plan_ref, server=client.server,
        socket=client.socket_identity, initialize=initialize, max_thread_starts=1, max_turn_starts=1), exclusive=True)
    try:
        params = dict(model=plan["model"], modelProvider=plan["model_provider"], cwd=plan["work_root"],
            approvalPolicy="never", sandbox="read-only", dynamicTools=[tool_definition()],
            baseInstructions=plan["base_instructions"], threadSource="user")
        created = client.request("thread/start", params)
        thread_exchange = client.last_exchange
        sent, received = exchange(thread_exchange, "thread/start", client.server)
        require(sent == params and received == created, "TASK_STARTUP_THREAD_EXCHANGE_CHANGED")
        thread = created["thread"]
        require(str(uuid.UUID(thread["id"])) == thread["id"] and thread.get("originator") == "codex-tui",
                "TASK_STARTUP_CREATED_THREAD_INVALID")
        write_json(directory / "thread.json", dict(exchange=thread_exchange, result=created), exclusive=True)
        params = dict(threadId=thread["id"], input=[dict(type="text", text=plan["startup_text"], text_elements=[])])
        started = client.request("turn/start", params)
        turn_exchange = client.last_exchange
        sent, received = exchange(turn_exchange, "turn/start", client.server)
        require(sent == params and received == started, "TASK_STARTUP_TURN_EXCHANGE_CHANGED")
        turn = started["turn"]["id"]
        require(str(uuid.UUID(turn)) == turn, "TASK_STARTUP_CREATED_TURN_INVALID")
        creation = write_json(directory / "creation.json", dict(schema=CREATION_SCHEMA,
            initialize=initialize, thread_start=thread_exchange, turn_start=turn_exchange), exclusive=True)
        registration = dict(schema="upgrade-task-operations-v1", task_id=plan["task_id"],
            thread_id=thread["id"], turn_id=turn, controller_id=plan["controller_id"],
            authority_ref=plan["authority_ref"], operations=plan["operations"])
        registration_ref = write_json(directory / "operations.json", registration, exclusive=True)
        ledger = OperationLedger.create(directory / "operations.sqlite", registration)
        ledger.close()
        anchor = _journal_anchor(client, plan, created, creation, timeout=journal_timeout)
        verify_peer(client.sock, client.server, client.process)
        result = dict(schema="upgrade-task-startup-result-v1", plan=plan_ref, creation=creation,
            anchor=write_json(directory / "anchor.json", anchor, exclusive=True),
            operations=registration_ref, ledger=str(directory / "operations.sqlite"),
            registration_sha256=object_sha(registration), thread_id=thread["id"], turn_id=turn,
            foreground_authentication=False, adoption_proven=False, operations_executed=0)
        return write_json(directory / "result.json", result, exclusive=True)
    except BaseException as exc:
        write_json(directory / "unknown.json", dict(status="UNKNOWN_NO_REPLAY", error_type=type(exc).__name__,
            error=str(exc), plan=plan_ref, retry_allowed=False), exclusive=True)
        raise
