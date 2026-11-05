"""Read proof for an explicitly selected remote task thread.

This is a prerequisite for future enrollment, not an enrollment API. In
particular it grants no caller, foreground, task-owner or input authority.
The controller must supply the original startup input and exact server pin.
Other open rollouts neither select nor invalidate this explicitly named thread.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

from orchestration_common import (checked_path, contract_errors, digest, require,
                                  strict_json)
from upgrade_rpc_peer import verify_peer

MAX_RECORD = 2 * 1024 * 1024


def validate_tui_target(process, native_root, spec, anchor):
    """Match the observed TUI's declared destination to the original task.

    A resume suffix alone can name the right UUID on a different app-server.
    This check is shared by inspection and live enrollment. It does not prove
    an established TUI connection, caller authority, or permission to send.
    The caller must still authenticate the process and the live server peer.
    """
    require(spec.get("schema") == "upgrade-startup-task-v1"
            and anchor.get("schema") == "upgrade-task-thread-read-anchor-v1",
            "TASK_TUI_EXPLICIT_TOPOLOGY_REQUIRED")
    thread = _uuid(anchor["thread_id"])
    require(str(native_root) == spec["clients"]["codex"]["native_root"] == anchor["native_root"],
            "TASK_TUI_NATIVE_ROOT_CHANGED")
    require(process["executable"] == spec["pins"]["codex"]["path"]
            == anchor["server"]["executable"]
            and anchor["server"]["argv_sha256"] == digest(json.dumps(
                spec["rpc"]["argv"], ensure_ascii=False, separators=(",", ":")).encode())
            and anchor["socket"]["path"] == spec["rpc"]["path"],
            "TASK_TUI_ORIGINAL_SERVER_CHANGED")
    argv = process.get("argv")
    require(isinstance(argv, list) and all(isinstance(a, str) for a in argv)
            and argv[:1] == [process["executable"]]
            and argv[-2:] == ["resume", thread]
            and argv.count("resume") == 1 and "--" not in argv,
            "TASK_TUI_NOT_ATTACHED")
    selectors = [i for i, arg in enumerate(argv)
                 if arg == "--remote" or arg.startswith("--remote=")]
    require(len(selectors) == 1 and argv[selectors[0]] == "--remote"
            and selectors[0] + 1 < len(argv) - 2
            and argv[selectors[0] + 1] == "unix://" + anchor["socket"]["path"],
            "TASK_TUI_REMOTE_TARGET_CHANGED")


def _uuid(value):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value,
            "TASK_THREAD_CANONICAL_UUID_REQUIRED")
    return value


def parse_descriptor_output(raw, pid, path, device, inode):
    """Select only the pre-named file; never choose the only/latest rollout."""
    require(isinstance(raw, str) and len(raw.encode()) <= 512 * 1024,
            "TASK_THREAD_DESCRIPTOR_OUTPUT_INVALID")
    frames, frame, seen_process = [], None, False
    for line in raw.splitlines():
        if line.startswith("p"):
            require(not seen_process and line[1:] == str(pid),
                    "TASK_THREAD_DESCRIPTOR_PROCESS_CHANGED")
            seen_process = True
        elif line.startswith("f"):
            require(seen_process, "TASK_THREAD_DESCRIPTOR_PROCESS_MISSING")
            if frame is not None:
                frames.append(frame)
            frame = {"f": line[1:]}
        elif line and line[0] in "aitnD":
            require(frame is not None and line[0] not in frame,
                    "TASK_THREAD_DESCRIPTOR_FIELDS_AMBIGUOUS")
            frame[line[0]] = line[1:]
    if frame is not None:
        frames.append(frame)
    candidates = [f for f in frames if f.get("n") == str(path)
                  and f.get("a") in ("w", "u")]
    require(seen_process and len(candidates) == 1,
            "TASK_THREAD_EXACT_WRITABLE_JOURNAL_REQUIRED")
    item = candidates[0]
    require(item["f"].isdigit() and item.get("t") == "REG"
            and item.get("i", "").isdigit()
            and int(item["i"]) == inode and int(item.get("D", "-1"), 16) == device,
            "TASK_THREAD_DESCRIPTOR_FILE_CHANGED")
    return {"fd": int(item["f"]), "access": item["a"], "device": device,
            "inode": inode, "path": str(path)}


def writable_descriptor(pid, path, device, inode):
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-FpfaitnD"],
        capture_output=True, text=True, timeout=5, check=False)
    require(result.returncode == 0 and not result.stderr,
            "TASK_THREAD_DESCRIPTOR_READ_FAILED")
    return parse_descriptor_output(result.stdout, pid, path, device, inode)


def _signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink]


def _slice(stream, ref, size):
    require(type(ref.get("offset")) is int and ref["offset"] >= 0
            and type(ref.get("length")) is int and 0 < ref["length"] <= MAX_RECORD
            and ref["offset"] + ref["length"] <= size,
            "TASK_THREAD_RECORD_BOUNDS_INVALID")
    stream.seek(ref["offset"])
    raw = stream.read(ref["length"])
    require(raw.endswith(b"\n") and raw.count(b"\n") == 1
            and digest(raw) == ref.get("sha256"), "TASK_THREAD_RECORD_CHANGED")
    if ref["offset"]:
        stream.seek(ref["offset"] - 1)
        require(stream.read(1) == b"\n", "TASK_THREAD_RECORD_BOUNDARY_INVALID")
    return strict_json(raw, "native task-thread record")


def journal_evidence(anchor):
    root = checked_path(anchor["native_root"])
    path = checked_path(anchor["journal"]["path"])
    require(path.is_relative_to(root / "sessions"), "TASK_THREAD_JOURNAL_OUTSIDE_ROOT")
    thread = _uuid(anchor["thread_id"])
    expected = anchor["journal"]
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
            and not before.st_mode & 0o022
            and (before.st_dev, before.st_ino) == (expected["device"], expected["inode"]),
            "TASK_THREAD_JOURNAL_IDENTITY_CHANGED")
    refs = [anchor["metadata"], anchor["startup_input"]]
    require(refs[0]["offset"] == 0 and refs[1]["offset"] >= refs[0]["length"],
            "TASK_THREAD_ORIGINAL_INPUT_REQUIRED")
    for ref in refs:
        require(all(ref[k] == expected[k] for k in ("path", "device", "inode")),
                "TASK_THREAD_RECORD_FROM_OTHER_JOURNAL")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(_signature(opened) == _signature(before), "TASK_THREAD_JOURNAL_REPLACED")
        meta, user = [_slice(stream, r, opened.st_size) for r in refs]
        payload = meta.get("payload", {})
        aliases = [payload[k] for k in ("id", "session_id") if k in payload]
        # Official remote TUI uses app-server's VSCode session-source default.
        # The source label is metadata, not proof of TUI or caller identity.
        require(meta.get("type") == "session_meta" and aliases
                and all(x == thread for x in aliases) and payload.get("source") in ("cli", "vscode")
                and payload.get("thread_source") == "user",
                "TASK_THREAD_ORIGINAL_USER_SESSION_REQUIRED")
        payload = user.get("payload", {})
        content = payload.get("content")
        require(user.get("type") == "response_item" and payload.get("type") == "message"
                and payload.get("role") == "user" and isinstance(content, list)
                and content and all(isinstance(x, dict) and x.get("type") == "input_text"
                                    and isinstance(x.get("text"), str) for x in content),
                "TASK_THREAD_NATIVE_STARTUP_INPUT_REQUIRED")
        text = "".join(x["text"] for x in content)
        require(isinstance(anchor.get("startup_text"), str) and text
                and text == anchor["startup_text"], "TASK_THREAD_STARTUP_INPUT_CHANGED")
        turn = _uuid(payload["internal_chat_message_metadata_passthrough"]["turn_id"])
        after = os.fstat(stream.fileno())
    fresh = path.lstat()
    require(_signature(before) == _signature(after) == _signature(fresh)
            and opened.st_size <= after.st_size <= fresh.st_size,
            "TASK_THREAD_JOURNAL_CHANGED_DURING_READ")
    return {"path": str(path), "signature": _signature(before),
            "metadata": refs[0], "startup_input": refs[1], "startup_turn": turn,
            "session_source": meta["payload"]["source"]}


def socket_evidence(client, expected):
    require(client.socket_identity == expected, "TASK_THREAD_SOCKET_CHANGED")
    path = checked_path(expected["path"])
    info = path.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600
            and (info.st_dev, info.st_ino) == (expected["device"], expected["inode"])
            and expected["uid"] == expected["peer_uid"] == os.geteuid()
            and expected["mode"] == 0o600, "TASK_THREAD_SOCKET_IDENTITY_CHANGED")


@contract_errors("TASK_THREAD_OBSERVATION_REFUSED")
def observe(client, anchor, process, *, descriptors=writable_descriptor):
    """Read original journal + live same-connection metadata; input count is zero.

    This intentionally cannot publish enrollment or call turn/start/turn/steer.
    It does not prove startup-probe completion, hook provenance or invoking-tool
    identity. The full enrollment layer must require those separate proofs.
    """
    require(anchor.get("schema") == "upgrade-task-thread-read-anchor-v1",
            "TASK_THREAD_EXPLICIT_READ_ANCHOR_REQUIRED")
    thread = _uuid(anchor["thread_id"])
    socket_evidence(client, anchor["socket"])
    peer = verify_peer(client.sock, anchor["server"], process)
    journal = journal_evidence(anchor)
    desc_args = (peer["peer"]["pid"], journal["path"],
                 anchor["journal"]["device"], anchor["journal"]["inode"])
    descriptor = descriptors(*desc_args)
    data = client.request("thread/read", {"threadId": thread, "includeTurns": False})["thread"]
    require(verify_peer(client.sock, anchor["server"], process) == peer,
            "TASK_THREAD_RESPONSE_PEER_CHANGED")
    require(isinstance(data, dict) and data.get("id") == thread
            and data.get("path") == journal["path"], "TASK_THREAD_RPC_IDENTITY_CHANGED")
    require(data.get("canAcceptDirectInput") is True, "TASK_THREAD_DIRECT_INPUT_UNAVAILABLE")
    turns = client.request("thread/turns/list", {"threadId": thread, "limit": 1,
                           "sortDirection": "desc", "itemsView": "notLoaded"})["data"]
    require(verify_peer(client.sock, anchor["server"], process) == peer,
            "TASK_THREAD_RESPONSE_PEER_CHANGED")
    require(isinstance(turns, list) and len(turns) <= 1
            and all(isinstance(t, dict) for t in turns), "TASK_THREAD_TURNS_INVALID")
    latest = turns[0] if turns else None
    if latest is not None:
        _uuid(latest["id"])
    status = data.get("status", {}).get("type")
    method = None
    if status == "active" and latest and latest.get("status") == "inProgress":
        method = "turn/steer"
    elif status == "idle" and (latest is None or latest.get("status") in
                               ("completed", "interrupted", "failed")):
        method = "turn/start"
    # Metadata is a momentary observation; do not turn it into an input permit.
    require(verify_peer(client.sock, anchor["server"], process) == peer
            and journal_evidence(anchor) == journal and descriptors(*desc_args) == descriptor,
            "TASK_THREAD_CHANGED_DURING_OBSERVATION")
    socket_evidence(client, anchor["socket"])
    return {"schema": "upgrade-task-thread-read-proof-v1", "thread_id": thread,
            "server_peer": peer, "socket": dict(anchor["socket"]),
            "journal": journal, "descriptor": descriptor,
            "can_accept_direct_input": True, "thread_status": status,
            "latest_turn_id": latest.get("id") if latest else None,
            "latest_turn_status": latest.get("status") if latest else None,
            "observed_input_method": method, "foreground_authentication": False,
            "caller_authentication": False, "enrollment_proven": False, "input_operations": 0}
