"""Kernel peer proof for a separately enrolled remote task-thread contract.

These functions perform no input, enrollment or foreground selection. The
caller must supply a controller-owned server pin; a returned proof identifies
the connected server process, not the TUI selection or the invoking agent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import sys

from orchestration_common import ContractError, contract_errors, require

SERVER_KEYS = {"pid", "birth", "uid", "executable", "file_identity", "argv_sha256"}


@contract_errors("TASK_RPC_KERNEL_PEER_UNAVAILABLE")
def kernel_peer(sock):
    """Read AF_UNIX peer credentials from the kernel, never a file.

    Darwin LOCAL_PEERPID reflects the peer's most recent I/O process, whereas
    getpeereid retains connection credentials. Linux SO_PEERCRED is captured at
    connection time. Neither authenticates a socket passed to other processes.
    The supported server must perform all socket I/O in its pinned process.
    """
    require(sock.family == socket.AF_UNIX
            and sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM,
            "TASK_RPC_UNIX_STREAM_REQUIRED")
    if sys.platform == "darwin":
        # <sys/un.h>: SOL_LOCAL=0, LOCAL_PEERCRED=1, LOCAL_PEERPID=2.
        pid = sock.getsockopt(0, 2)
        import ctypes
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        fn = ctypes.CDLL(None, use_errno=True).getpeereid
        fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        if fn(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise OSError(ctypes.get_errno(), "getpeereid failed")
        uid = uid.value
    elif sys.platform.startswith("linux"):
        pid, uid, _ = struct.unpack("3i", sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    else:
        raise ValueError("TASK_RPC_KERNEL_PEER_UNSUPPORTED")
    require(type(pid) is int and pid > 1 and uid == os.geteuid(),
            "TASK_RPC_KERNEL_PEER_INVALID")
    return {"pid": pid, "uid": uid}


@contract_errors("TASK_RPC_SERVER_ROW_INVALID")
def server_pin(row, *, uid):
    """Normalize a kernel-read process; this does not enroll it.

    file_identity is the reader's path stat (dev/ino/size/mtime), not a content
    digest or a proof about the mapped text vnode. Package verification belongs
    to the controller. pid+birth binds the live process incarnation.
    """
    argv = row.get("argv")
    require(isinstance(argv, list) and len(argv) >= 2
            and argv[1] == "app-server" and all(isinstance(x, str) for x in argv),
            "TASK_RPC_APP_SERVER_REQUIRED")
    require(type(uid) is int and uid >= 0, "TASK_RPC_SERVER_UID_REQUIRED")
    return {**{k: row[k] for k in SERVER_KEYS - {"argv_sha256", "uid"}}, "uid": uid,
            "argv_sha256": hashlib.sha256(json.dumps(argv, ensure_ascii=False,
                separators=(",", ":")).encode()).hexdigest()}


def verify_peer(sock, expected, process):
    """Bind this connection to the exact server incarnation before a request.

    process is the original kernel process reader, supplied by the controller.
    Repeat immediately after EACH response, before using it. On Darwin the
    responding process may differ from the previously observed peer after FD
    transfer. FD transfer/socket activation/multiprocess I/O are unsupported;
    the Linux credential cannot detect that transfer. Every exception refuses
    proof and never grants retry of an unknown input operation.
    This function does not assert that expected came from a valid enrollment.
    """
    try:
        return _verify_peer(sock, expected, process)
    except ContractError:
        raise
    except Exception as exc:
        # Process readers have their own error hierarchy. An inaccessible,
        # disappeared or malformed process never becomes a retryable identity.
        raise ContractError("TASK_RPC_PEER_OBSERVATION_REFUSED: " + type(exc).__name__) from exc


def _verify_peer(sock, expected, process):
    require(isinstance(expected, dict) and set(expected) == SERVER_KEYS,
            "TASK_RPC_SERVER_PIN_INVALID")
    require(type(expected["pid"]) is int and expected["pid"] > 1
            and type(expected["uid"]) is int and expected["uid"] == os.geteuid(),
            "TASK_RPC_SERVER_PIN_INVALID")
    require(isinstance(expected["birth"], list) and len(expected["birth"]) == 2
            and all(type(x) is int and x >= 0 for x in expected["birth"]),
            "TASK_RPC_SERVER_PIN_INVALID")
    require(expected["birth"][0] > 0 and expected["birth"][1] < 1000000
            and isinstance(expected["executable"], str) and Path(expected["executable"]).is_absolute()
            and isinstance(expected["file_identity"], list) and len(expected["file_identity"]) == 4
            and all(type(x) is int and x >= 0 for x in expected["file_identity"])
            and isinstance(expected["argv_sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", expected["argv_sha256"]) is not None,
            "TASK_RPC_SERVER_PIN_INVALID")
    peer = kernel_peer(sock)
    require(peer == {k: expected[k] for k in ("pid", "uid")}, "TASK_RPC_SERVER_PEER_CHANGED")
    first = server_pin(process(peer["pid"]), uid=peer["uid"])
    require(first == expected, "TASK_RPC_SERVER_INSTANCE_CHANGED")
    require(kernel_peer(sock) == peer and server_pin(process(peer["pid"]), uid=peer["uid"]) == first,
            "TASK_RPC_SERVER_CHANGED_DURING_READ")
    return dict(schema="upgrade-task-rpc-peer-v1", server=first, peer=peer,
                foreground_authentication=False, enrollment_proven=False, input_operations=0)
