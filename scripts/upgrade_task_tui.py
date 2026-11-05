"""Same-surface official TUI initialization and exact task-thread attachment.

Only a newly held/released lane uses this path. It never sends terminal text,
clears a conversation or restarts an existing task. The initial empty TUI owns
the real terminal; after the external controller has persisted the first turn,
that owned empty process exits before this launcher execs resume of the exact
created task. The parent PID remains the final task TUI's registered launch PID.
"""
import os
from pathlib import Path
import subprocess
import time

from orchestration_common import file_ref, json_ref, require, write_json
from upgrade_task_creation import validate_creation
from upgrade_task_thread import journal_evidence


def task_tui_argv(ref, spec, binding, env, *, timeout=120):
    import cmux_daemon_identity as identity
    require(all(os.isatty(fd) for fd in (0, 1, 2)), "TASK_TUI_REAL_TERMINAL_REQUIRED")
    require(0 < timeout <= 120, "TASK_TUI_ATTACH_TIMEOUT_INVALID")
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    control = Path(binding["state_root"]) / "startup"
    external = Path(spec["adoption_path"]).parent
    client = spec["clients"]["codex"]
    write_json(control / "codex-origin-intent.json", dict(startup=ref, argv=client["argv"],
        terminal=os.ttyname(0), input_operations=0, own_empty_tui=True), exclusive=True)
    child = subprocess.Popen(client["argv"], env=env, cwd=spec["work_root"], close_fds=True)
    try:
        process = identity.process(child.pid, validate_argv=False)
        write_json(control / "codex-origin.json", dict(startup=ref, pid=child.pid, birth=process["birth"],
            input_operations=0, ready_proven=False), exclusive=True)
        deadline = time.monotonic() + timeout
        while True:
            require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
            require(child.poll() is None, "TASK_TUI_ORIGIN_EXITED_NO_REPLACEMENT")
            signal = external / "task-attach.json"
            if signal.is_file():
                attach = json_ref(file_ref(signal))
                require(set(attach) == {"schema", "startup", "result"}
                        and attach["schema"] == "upgrade-task-attach-v1" and attach["startup"] == ref
                        and attach["result"]["path"] == str(external / "task-startup/result.json"),
                        "TASK_TUI_ATTACH_CONTRACT_CHANGED")
                result = json_ref(attach["result"])
                require(result["schema"] == "upgrade-task-startup-result-v1"
                        and result["plan"] == spec["task_startup"], "TASK_TUI_TASK_PLAN_CHANGED")
                anchor = json_ref(result["anchor"])
                journal_evidence(anchor)
                plan = json_ref(spec["task_startup"])
                creation = validate_creation(result["creation"], server=anchor["server"],
                    thread_id=result["thread_id"], journal_path=anchor["journal"]["path"],
                    native_root=client["native_root"], startup_text=plan["startup_text"])
                require(anchor["thread_id"] == result["thread_id"]
                        and creation["turn_id"] == result["turn_id"], "TASK_TUI_THREAD_CHANGED")
                argv = [*client["argv"], "resume", result["thread_id"]]
                write_json(control / "codex-attach-intent.json", dict(startup=ref, attach=file_ref(signal),
                    argv=argv, new_task_only=True, terminal_input_operations=0), exclusive=True)
                return argv
            require(time.monotonic() < deadline, "TASK_TUI_ATTACH_TIMEOUT_NO_REPLAY")
            time.sleep(0.1)
    finally:
        # Only our own just-created empty TUI. Never signal the original user
        # supervisor, server or another surface, and never force-kill a stall.
        if child.poll() is None:
            write_json(control / "codex-origin-stop-intent.json", dict(pid=child.pid,
                startup=ref, signal="SIGTERM", own_empty_tui=True), exclusive=True)
            child.terminate()
            child.wait(timeout=5)
        write_json(control / "codex-origin-exited.json", dict(pid=child.pid,
            startup=ref, returncode=child.returncode), exclusive=True)
