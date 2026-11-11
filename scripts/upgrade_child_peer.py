"""Kernel peer and exact native-hook ancestry, not caller-supplied labels.

This service boundary never runs a model or creates a child. A same-user socket
is necessary but insufficient: the peer must be the frozen hook beneath the
fresh original enrolled CLI. Process-reader injection is a Python test seam,
not an IPC/CLI override.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shlex
import socket
import struct
import subprocess
import sys

from orchestration_common import contract_errors, require
from upgrade_child_budget_protocol import hook_command


@contract_errors("native child kernel peer")
def kernel_peer(connection):
    require(connection.family == socket.AF_UNIX, "CHILD_IPC_UNIX_PEER_REQUIRED")
    if sys.platform == "darwin":
        # Apple bsd/sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=0x002.
        # getpeereid validates effective credentials independently of the PID.
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        lib.getpeereid.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        lib.getpeereid.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        require(lib.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0,
                "CHILD_IPC_KERNEL_CREDENTIALS_UNAVAILABLE")
        pid = connection.getsockopt(0, 0x002)
        result = dict(pid=pid, uid=uid.value)
    elif sys.platform.startswith("linux"):
        # Linux SO_PEERCRED is the kernel struct ucred: pid_t/uid_t/gid_t.
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, _ = struct.unpack("3i", raw)
        result = dict(pid=pid, uid=uid)
    else:
        raise ValueError("CHILD_IPC_KERNEL_PEER_PLATFORM_UNSUPPORTED")
    require(type(result["pid"]) is int and result["pid"] > 1
            and result["uid"] == os.getuid(), "CHILD_IPC_FOREIGN_OR_INVALID_KERNEL_PEER")
    return result


PYTHON_PROBE = "import sys; sys.stdout.write('r'); sys.stdout.flush(); sys.stdin.read()"


@contract_errors("pinned Python kernel executable")
def measured_python_executable(python):
    """The binary the kernel reports for a process started as the pinned Python.

    A macOS framework bin/python3.14 re-execs Resources/Python.app, so every
    hook it starts shows that executable and argv[0]. Measure it, never infer it.
    """
    from cmux_daemon_identity import process
    argv = [str(python), "-I", "-B", "-c", PYTHON_PROBE]
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          env={"PATH": "/usr/bin:/bin"}, close_fds=True) as probe:
        try:
            require(probe.stdout.read(1) == b"r", "PINNED_PYTHON_PROBE_FAILED")
            row = process(probe.pid, validate_argv=False)
        finally:
            probe.stdin.close()
            probe.wait(10)
    require(row["pid"] == probe.pid and row["argv"][1:] == argv[1:]
            and row["argv"][0] in (row["executable"], argv[0]), "PINNED_PYTHON_PROBE_IDENTITY_CHANGED")
    return row["executable"]


@contract_errors("original automatic child hook peer")
def authenticate_hook_peer(peer, *, session, account_home, python, controller, process_reader=None,
                           native_python=None):
    require(set(peer) == {"pid", "uid"} and type(peer["pid"]) is int and peer["pid"] > 1
            and peer["uid"] == os.getuid(), "CHILD_IPC_KERNEL_PEER_REQUIRED")
    require(session.get("provider") in {"codex", "claude"}, "CHILD_ORIGINAL_PROVIDER_REQUIRED")
    if process_reader is None:
        from cmux_daemon_identity import process
        process_reader = lambda pid: process(pid, validate_argv=False, native_roots=True)
    command = hook_command(python, controller)
    expected_argv = shlex.split(command)
    # The hook process itself is either the pinned path or, after a measured
    # launcher re-exec, that target with the same arguments.
    accepted = [(str(Path(python).resolve()), expected_argv)]
    if native_python is not None:
        accepted.append((native_python, [native_python] + expected_argv[1:]))
    originals, pid, reached = [], peer["pid"], False
    for index in range(8):
        row = process_reader(pid)
        require(row["pid"] == pid, "CHILD_HOOK_PROCESS_IDENTITY_CHANGED")
        originals.append(row)
        if index == 0:
            require((row["executable"], row["argv"]) in accepted, "CHILD_AUTOMATIC_HOOK_COMMAND_REQUIRED")
        elif row["pid"] == session["process"]["pid"]:
            require(all(row.get(key) == value for key, value in session["process"].items()),
                    "CHILD_ORIGINAL_PARENT_PROCESS_CHANGED")
            env = row.get("env", {})
            root_key = "CODEX_HOME" if session["provider"] == "codex" else "CLAUDE_CONFIG_DIR"
            require(env.get("HOME") == account_home and env.get(root_key) == session["native_config_root"]
                    and env.get("CMUX_SURFACE_ID", "").lower() == session["surface_uuid"].lower()
                    and env.get("CMUX_WORKSPACE_ID", "").lower() == session["workspace_uuid"].lower(),
                    "CHILD_ORIGINAL_PARENT_ENVIRONMENT_CHANGED")
            reached = True
            break
        else:
            argv = row["argv"]
            shell = Path(row["executable"]).name in {"sh", "bash", "zsh"}
            flags = [i for i, value in enumerate(argv) if i and value.startswith("-") and "c" in value[1:]]
            require(shell and len(flags) == 1 and flags[0] + 1 < len(argv)
                    and shlex.split(argv[flags[0] + 1]) == expected_argv,
                    "CHILD_MANUAL_OR_UNRECOGNIZED_HOOK_ANCESTRY")
        pid = row["ppid"]
        require(pid > 1 and pid not in {item["pid"] for item in originals},
                "CHILD_ORIGINAL_PARENT_ANCESTRY_REQUIRED")
    require(reached, "CHILD_ORIGINAL_PARENT_ANCESTRY_REQUIRED")
    # Every row is fresh again after complete selection; no process cache grant.
    require(all(process_reader(row["pid"]) == row for row in originals),
            "CHILD_HOOK_PROCESS_CHANGED_DURING_AUTHENTICATION")
    return dict(peer_pid=peer["pid"], original_parent_pid=session["process"]["pid"],
                command=command, parents=[{key: row[key] for key in (
                    "pid", "ppid", "birth", "executable", "file_identity")} for row in originals],
                input_operations=0)
