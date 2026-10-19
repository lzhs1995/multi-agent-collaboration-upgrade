"""Append observations from real MAC hook entrypoints; never emit adoption.

The external adoption verifier correlates these with native tool calls/results
and independently bound live clients. A manually invoked hook is insufficient.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import time
import uuid

from orchestration_common import file_ref, read_regular, write_json
from upgrade_native_roots import configuration


def hook_parents(expected_command, launches):
    import cmux_daemon_identity as identity
    parents, direct = [], True
    pid = os.getppid()
    for _ in range(8):
        row = identity.process(pid, validate_argv=False)
        public = {k: row[k] for k in ("pid", "ppid", "birth", "executable", "file_identity")}
        matched = next((name for name, launch in launches.items()
                        if row["pid"] == launch["pid"] and row["birth"] == launch["birth"]), None)
        if matched:
            public["launch"] = matched
            parents.append(public)
            return parents, matched if direct else None
        argv = row["argv"]
        shell = Path(row["executable"]).name in {"sh", "bash", "zsh"}
        try:
            at = next(i for i, value in enumerate(argv) if i and value.startswith("-") and "c" in value[1:])
            hook_shell = shell and shlex.split(argv[at + 1]) == shlex.split(expected_command)
        except (StopIteration, IndexError, ValueError):
            hook_shell = False
        public["exact_hook_shell"] = hook_shell
        parents.append(public)
        direct = direct and hook_shell
        pid = row["ppid"]
        if pid <= 1:
            break
    return parents, None


def record(payload, *, original_hook):
    """Called inside the original automatically registered advisory hook."""
    if payload.get("hook_event_name") not in {"PreToolUse", "PostToolUse"}:
        return
    binding, context = configuration()
    lane = os.environ.get("UPGRADE_CLIENT_LANE")
    if lane not in binding["native_roots"]:
        return
    startup_path = Path(binding["config_root"]) / "startup-v2.json"
    startup_ref = file_ref(startup_path)
    spec = json.loads(read_regular(startup_path))
    main = Path(getattr(sys.modules.get("__main__"), "__file__", "")).resolve()
    if (main != Path(binding["controller_root"]) / "scripts/cmux_workflow_advisory.py"
            or sys.argv[1:] != ["--hook", original_hook]):
        return
    expected = shlex.join([context["python"]["path"], "-I", "-B", str(main), "--hook", original_hook])
    hooks_ref = spec["pins"]["codex/hooks.json" if lane == "codex" else lane + "/settings.json"]
    hooks = json.loads(read_regular(Path(hooks_ref["path"]))).get("hooks", {})
    commands = [hook.get("command") for group in hooks.get(payload["hook_event_name"], [])
                for hook in group.get("hooks", []) if hook.get("type") == "command"]
    if commands.count(expected) != 1:
        return
    launches = {}
    for name in (lane, "codex-server") if lane == "codex" else (lane,):
        path = Path(binding["state_root"]) / "startup" / (name + ".json")
        if path.exists():
            launches[name] = json.loads(read_regular(path))
    parents, client = hook_parents(expected, launches)
    selected = {key: payload[key] for key in (
        "session_id", "sessionId", "transcript_path", "transcriptPath", "hook_event_name",
        "tool_name", "tool_input", "tool_use_id", "call_id", "tool_call_id") if key in payload}
    value = dict(schema="upgrade-automatic-hook-observation-v2", startup=startup_ref,
        lane=lane, hook=original_hook, command=expected, configuration=hooks_ref,
        observed_at_ns=time.time_ns(), pid=os.getpid(), parents=parents, direct_client=client,
        payload=selected, adoption_proven=False, native_reception=False)
    directory = Path(binding["state_root"]) / "hook-observations" / lane
    ref = write_json(directory / (str(value["observed_at_ns"]) + "-" + uuid.uuid4().hex + ".json"),
                     value, exclusive=True)
    Path(ref["path"]).chmod(0o400)
