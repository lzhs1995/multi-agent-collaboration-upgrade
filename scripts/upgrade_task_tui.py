"""Same-surface official TUI initialization and exact task-thread attachment.

Only a newly held/released lane uses this path. It never sends terminal text,
clears a conversation or restarts an existing task. The initial empty TUI owns
the real terminal; after the external controller has persisted the first turn,
that owned empty process exits before this launcher execs resume of the exact
created task. The parent PID remains the final task TUI's registered launch PID.
"""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import stat
import subprocess
import time
import uuid

from orchestration_common import canonical, checked_path, digest, file_ref, json_ref, require, write_json
from upgrade_task_creation import validate_creation
from upgrade_task_thread import journal_evidence

ORIGIN_READY_MARKER = 'tui startup initial frame scheduled '


def _origin_log(native_root):
    return checked_path(checked_path(native_root) / 'log/logs_2.sqlite')


def _read_origin_log(path, query, params=()):
    """Bounded SELECT from the original native log; never create or edit it."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
            and not before.st_mode & 0o022, 'TASK_TUI_ORIGIN_LOG_INVALID')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.1)) as database:
        database.row_factory = sqlite3.Row
        database.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + .5
        database.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        try:
            rows = [dict(row) for row in database.execute(query, params)]
        except sqlite3.OperationalError as exc:
            # A busy/unfinished observation is not a missing or exited client.
            if str(exc) in ('database is locked', 'database is busy', 'interrupted'):
                return None
            raise
        after = path.lstat()
        require((after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
                and stat.S_ISREG(after.st_mode),
                'TASK_TUI_ORIGIN_LOG_CHANGED')
        return before, rows


def origin_log_fence(native_root):
    """Capture the original log boundary before launching the empty TUI."""
    path = _origin_log(native_root)
    observed = _read_origin_log(path, "SELECT seq FROM sqlite_sequence WHERE name='logs'")
    require(observed is not None or not path.exists(), 'TASK_TUI_ORIGIN_FENCE_UNAVAILABLE')
    info, rows = observed if observed is not None else (None, [])
    return dict(path=str(path), device=info.st_dev if info else None,
                inode=info.st_ino if info else None, last_id=rows[0]['seq'] if rows else 0)


def observe_origin_ready(native_root, fence, process):
    """Wait for a fresh official TUI frame before a helper may initialize.

    In the pinned official client this frame follows the awaited app-server
    bootstrap. The log is only an ordering aid: peer, initialize userAgent and
    created-thread originator checks remain mandatory. It grants no task input.
    """
    path = _origin_log(native_root)
    require(isinstance(fence, dict) and set(fence) ==
            {'path', 'device', 'inode', 'last_id'}
            and fence['path'] == str(path) and type(fence['last_id']) is int
            and fence['last_id'] >= 0,
            'TASK_TUI_ORIGIN_FENCE_INVALID')
    born_ns = process['birth'][0] * 10**9 + process['birth'][1] * 1000
    observed = _read_origin_log(path,
        'SELECT id, ts, ts_nanos, level, target, feedback_log_body, process_uuid FROM logs '
        'WHERE id > ? AND process_uuid LIKE ? AND target = ? '
        'AND level = ? AND feedback_log_body LIKE ? AND ts*1000000000+ts_nanos >= ? '
        'ORDER BY id LIMIT 2',
        (fence['last_id'], f"pid:{process['pid']}:%", 'codex_tui::app::startup',
         'INFO', ORIGIN_READY_MARKER + '%', born_ns))
    if observed is None:
        return None
    info, rows = observed
    require((fence['device'] is None and fence['inode'] is None and fence['last_id'] == 0)
            or (info.st_dev, info.st_ino) == (fence['device'], fence['inode']),
            'TASK_TUI_ORIGIN_LOG_REPLACED')
    require(len(rows) <= 1, 'TASK_TUI_ORIGIN_LOG_AMBIGUOUS')
    if not rows:
        return None
    row = rows[0]
    require(str(uuid.UUID(row['process_uuid'].split(':', 2)[2])) == row['process_uuid'].split(':', 2)[2]
            and len(row['feedback_log_body'].encode()) <= 4096,
            'TASK_TUI_ORIGIN_RECORD_INVALID')
    return dict(path=str(path), device=info.st_dev, inode=info.st_ino,
                row=row, sha256=digest(canonical(row)),
                ordering_only=True, foreground_authentication=False, input_operations=0)


def wait_original_server(ref, spec, binding, *, timeout=120):
    """Observe the external one-shot launch; this TUI never starts a server."""
    import cmux_daemon_identity as identity
    require(0 < timeout <= 120, 'TASK_SERVER_WAIT_BUDGET_INVALID')
    startup = Path(binding['state_root'])/'startup'
    result_path = startup/'codex-server-result.json'
    deadline = time.monotonic()+timeout
    original_records = None
    while True:
        require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
        require(time.monotonic() < deadline, 'TASK_SERVER_WAIT_TIMEOUT_NO_REPLAY')
        if result_path.is_file():
            records = (file_ref(result_path), file_ref(startup/'codex-server.json'))
            if original_records is None:
                original_records = records
            require(records == original_records, 'TASK_SERVER_ORIGINAL_RECORD_CHANGED')
            result = json_ref(original_records[0])
            require(result.get('schema') == 'upgrade-server-result-v2' and result.get('startup') == ref,
                    'TASK_SERVER_ORIGINAL_STARTUP_CHANGED')
            require(result.get('status') == 'SOCKET_CREATED', 'TASK_SERVER_ORIGINAL_START_FAILED')
            launched = json_ref(original_records[1])
            try:
                live = identity.process(launched['pid'])
            except identity.IdentityError as exc:
                # A short-lived external launcher can exit between the two
                # strict kernel reads, changing only its child's parent. No
                # identity from that failed read is accepted. Observe the
                # same pinned launch again within the original wait budget;
                # never restart the server or relax the identity reader.
                if str(exc) != 'process changed during read':
                    raise
                require(time.monotonic() < deadline, 'TASK_SERVER_WAIT_TIMEOUT_NO_REPLAY')
                time.sleep(.05)
                continue
            require(launched['startup'] == ref and result['pid'] == launched['pid']
                    and live['birth'] == launched['birth']
                    and live['executable'] == spec['pins']['codex']['path']
                    and live['argv'] == spec['rpc']['argv']
                    and Path(spec['rpc']['path']).is_socket(), 'TASK_SERVER_ORIGINAL_PROCESS_CHANGED')
            return result
        require(time.monotonic() < deadline, 'TASK_SERVER_WAIT_TIMEOUT_NO_REPLAY')
        time.sleep(.05)


def task_tui_argv(ref, spec, binding, env, *, timeout=120):
    import cmux_daemon_identity as identity
    require(all(os.isatty(fd) for fd in (0, 1, 2)), "TASK_TUI_REAL_TERMINAL_REQUIRED")
    require(0 < timeout <= 120, "TASK_TUI_ATTACH_TIMEOUT_INVALID")
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    control = Path(binding["state_root"]) / "startup"
    external = Path(spec["adoption_path"]).parent
    client = spec["clients"]["codex"]
    log_fence = origin_log_fence(client['native_root'])
    write_json(control / "codex-origin-intent.json", dict(startup=ref, argv=client["argv"],
        terminal=os.ttyname(0), input_operations=0, own_empty_tui=True,
        log_fence=log_fence), exclusive=True)
    child = subprocess.Popen(client["argv"], env=env, cwd=spec["work_root"], close_fds=True)
    try:
        process = identity.process(child.pid, validate_argv=False)
        write_json(control / "codex-origin.json", dict(startup=ref, pid=child.pid, birth=process["birth"],
            input_operations=0, ready_proven=False, log_fence=log_fence), exclusive=True)
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
