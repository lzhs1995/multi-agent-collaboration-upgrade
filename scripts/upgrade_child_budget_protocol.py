"""Minimal native-hook IPC contract; never a model or process manager."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shlex
import re
import subprocess
import os

POLICY = "whole-task-pre-agent-v1"
SCHEMA = "upgrade-native-child-hook-request-v1"
MAX_PACKET = 256 * 1024
EVENTS = ("PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop")


def verify_child_hook_binary(ref):
    """Read-only prerequisite, not active native hook adoption or model proof.

    Use the exact pinned program, never a PATH lookup, provider call or client
    launch. The new private launch refuses an old/unknown native hook contract.
    Offline materialization may still use explicit non-model executable fixtures.
    """
    from upgrade_client_isolation import clean_child_environment
    from upgrade_live_integrity import verify_artifact
    program = verify_artifact(ref)
    argv = [program["path"], "--version"]
    # An empty stdin pipe wraps a write descriptor that the strict controller
    # audit correctly cannot bind to private state. A read-only EOF descriptor
    # needs neither a new write authority nor a broader kernel profile.
    with open(os.devnull, "rb") as null_input:
        result = subprocess.run(argv, stdin=null_input, capture_output=True, text=True,
                                env=clean_child_environment({}), timeout=10, check=False)
    verify_artifact(ref)
    match = re.fullmatch(r"([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,6}) \(Claude Code\)\n?", result.stdout)
    if (result.returncode != 0 or result.stderr or match is None
            or tuple(int(v) for v in match.groups()) < (2, 1, 295)):
        raise ValueError("CHILD_NATIVE_HOOK_VERSION_UNSUPPORTED: blocking hook failures require native Claude >=2.1.295")
    return dict(program=program, version=".".join(match.groups()), argv=argv,
                model_requests=0, input_operations=0)


def hook_command(python, controller):
    return shlex.join([str(python), "-I", "-B",
                      str(Path(controller) / "scripts/mac_upgrade_child_hook.py"), "hook"])


def hook_group(python, controller, *, provider, event):
    handler = {"type": "command", "command": hook_command(python, controller), "timeout": 15}
    matcher = "*"
    if provider == "claude" and event == "PreToolUse":
        # Current native Claude explicitly blocks hook startup errors, invalid
        # JSON and timeouts only with this field. Keep ordinary non-Bash tools
        # outside the permission gate, preserving original guards separately.
        handler["onFailure"] = "block"
        matcher = "Agent|Bash"
    return {"matcher": matcher, "hooks": [handler]}


def registered_hooks(original, python, controller, *, provider):
    """Keep original guards. A success never sets permissionDecision=allow."""
    if provider not in ("codex", "claude"):
        raise ValueError("CHILD_HOOK_PROVIDER_UNKNOWN")
    result = copy.deepcopy(original)
    if set(result) != {"hooks"} or not isinstance(result["hooks"], dict):
        raise ValueError("CHILD_HOOK_CONFIGURATION_CHANGED")
    command = hook_command(python, controller)
    for event in EVENTS if provider == "claude" else ("PreToolUse",):
        entries = result["hooks"].setdefault(event, [])
        if not isinstance(entries, list) or any(h.get("command") == command
                for group in entries for h in group.get("hooks", [])):
            raise ValueError("CHILD_HOOK_ALREADY_REGISTERED_OR_CHANGED")
        # Run before native tool execution; SubagentStart cannot limit creation.
        entries.insert(0, hook_group(python, controller, provider=provider, event=event))
    return result


def validate_registered_hooks(document, python, controller, *, provider, enabled):
    """Check the actual native-client hook declaration, not a caller's boolean."""
    command = hook_command(python, controller)
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("STARTUP_CHILD_HOOK_CHANGED")
    expected = set(EVENTS if provider == "claude" else ("PreToolUse",)) if enabled else set()
    found = set()
    for event, groups in hooks.items():
        matches = []
        if not isinstance(groups, list):
            raise ValueError("STARTUP_CHILD_HOOK_CHANGED")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("STARTUP_CHILD_HOOK_CHANGED")
            matches += [(group, h) for h in group["hooks"] if isinstance(h, dict) and h.get("command") == command]
        if matches:
            expected_group = hook_group(python, controller, provider=provider, event=event)
            if (len(matches) != 1 or event not in expected
                    or matches[0] != (expected_group, expected_group["hooks"][0])
                    or groups[0] != matches[0][0]):
                raise ValueError("STARTUP_CHILD_HOOK_CHANGED")
            found.add(event)
    if found != expected:
        raise ValueError("STARTUP_CHILD_HOOK_CHANGED")


def child_socket(paths):
    path = Path(paths["tmp"]) / "child-budget.sock"
    if path != path.resolve() or len(str(path).encode()) > 103:
        raise ValueError("STARTUP_CHILD_SOCKET_CHANGED")
    return path


def consume_command(python, controller, claim_id, sha256, rtk="rtk"):
    if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
           for value in (claim_id, sha256)):
        raise ValueError("CHILD_CONSUMPTION_PIN_REQUIRED")
    return shlex.join([str(rtk), "proxy", str(python), "-I", "-B",
        str(Path(controller) / "scripts/mac_upgrade_child_hook.py"), "consume",
        "--claim", claim_id, "--reply-sha256", sha256])


def parse_consumption(command, python, controller, rtk):
    if not isinstance(command, str):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    prefix = [str(rtk), "proxy", str(python), "-I", "-B",
              str(Path(controller) / "scripts/mac_upgrade_child_hook.py"), "consume"]
    if argv[:len(prefix)] != prefix:
        return None
    if (len(argv) != len(prefix) + 4 or argv[-4] != "--claim" or argv[-2] != "--reply-sha256"
            or command != consume_command(python, controller, argv[-3], argv[-1], rtk)):
        raise ValueError("CHILD_CONSUMPTION_COMMAND_CHANGED")
    return dict(claim_id=argv[-3], reply_sha256=argv[-1])


def child_prompt(claim_id, original):
    if not isinstance(original, str) or not original.strip():
        raise ValueError("CHILD_PROMPT_REQUIRED")
    return ("UPGRADE_ORIGINAL_CHILD_CLAIM=" + claim_id + "\n"
            "This is one original native child of the registered whole task. "
            "Do not create or resume any nested agent. Keep the assigned readonly scope. "
            "Return a complete self-contained report in your final native message; "
            "do not substitute a file path, launch note, quote, or incomplete checkpoint.\n\n"
            + original)


def updated_agent_input(original, claim_id):
    if not isinstance(original, dict):
        raise ValueError("CHILD_TOOL_INPUT_REQUIRED")
    allowed = {"description", "prompt", "subagent_type", "run_in_background"}
    if (set(original) - allowed or original.get("subagent_type") != "general-purpose"
            or not isinstance(original.get("description"), str) or not original["description"].strip()
            or type(original.get("run_in_background", False)) is not bool):
        raise ValueError("CHILD_NATIVE_INPUT_UNSUPPORTED: no model/team/resume/nested override")
    result = copy.deepcopy(original)
    result["prompt"] = child_prompt(claim_id, original.get("prompt"))
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": result}}


def encode_packet(value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")) + "\n").encode()
    if len(raw) > MAX_PACKET:
        raise ValueError("CHILD_IPC_PACKET_BOUND_EXCEEDED")
    return raw
