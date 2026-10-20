"""Authenticate a private native Codex ancestor without a managed daemon.

The current process, full ancestry and native foreground record are independent
evidence. A selector supplied in an environment dictionary or hook payload is
never enough. This module performs no input and changes no process environment.
"""
from __future__ import annotations

import os
from pathlib import Path
import uuid


def collect(env, chain, *, payload=None):
    import cmux_daemon_identity as native
    import cmux_foreground_thread as foreground
    import upgrade_process_inventory as inventory

    candidates = [(i, p) for i, p in enumerate(chain)
                  if Path(p.get("executable", "")).name == "codex"
                  and p.get("argv", [])[1:2] != ["app-server"]]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise native.IdentityError("ambiguous standalone native ancestry")
    index, client = candidates[0]
    current = native.process(os.getpid())
    if (current["pid"] != os.getpid() or current["ppid"] != chain[0]["pid"]
            or os.getppid() != chain[0]["pid"]):
        raise native.IdentityError("standalone current process parent changed")
    try:
        selector = env.get("CODEX_THREAD_ID") if payload is None else payload["session_id"]
        session = str(uuid.UUID(selector))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise native.IdentityError("standalone caller lacks a valid session selector") from exc
    if payload is not None and payload.get("hook_event_name") not in (
            "PreToolUse", "PostToolUse", "Stop", "SubagentStop", "SessionStart", "UserPromptSubmit"):
        raise native.IdentityError("standalone hook event is missing or unsupported")
    if payload is None and current["env"].get("CODEX_THREAD_ID") != session:
        raise native.IdentityError("standalone kernel thread selector differs")
    if env.get("CODEX_THREAD_ID") and env["CODEX_THREAD_ID"] != session:
        raise native.IdentityError("standalone hook session differs from tool selector")
    for key in ("CMUX_SURFACE_ID", "CMUX_WORKSPACE_ID"):
        try:
            uuid.UUID(client["env"][key])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise native.IdentityError("standalone client lacks a valid scope UUID") from exc
        if env.get(key) != client["env"][key]:
            raise native.IdentityError("standalone supplied scope differs from native client")
    for item in [current] + chain[:index]:
        selected = item["env"].get("CODEX_THREAD_ID")
        if selected and selected != session:
            raise native.IdentityError("standalone ancestor thread differs")
        if any(item["env"].get(key) != client["env"][key]
               for key in ("CMUX_SURFACE_ID", "CMUX_WORKSPACE_ID")):
            raise native.IdentityError("standalone kernel scope differs")
    selected = foreground.read(client)
    if not isinstance(selected, dict) or selected.get("thread_id") != session:
        # Inherited root selectors never grant a child the foreground inbox.
        raise native.IdentityError("standalone native foreground thread missing or changed")
    tty = inventory.terminal(client)
    if not tty or tty in ("?", "??", "-"):
        raise native.IdentityError("standalone client has no terminal")
    if native.process(current["pid"]) != current:
        raise native.IdentityError("standalone current process identity drift")
    again, daemon = native._ancestry()
    if again != chain or daemon is not None or native.process(client["pid"]) != client:
        raise native.IdentityError("standalone native ancestry drift")
    if foreground.read(client) != selected:
        raise native.IdentityError("standalone native foreground changed during observation")
    return dict(session=session, daemon=None, client=dict(client, tty=tty),
                chain=[current] + chain, foreground=selected, caller_kind="standalone_native",
                source="private-standalone-native-client-v1")
