"""Bounded v3 shared-state acquisition; node action locks remain fail-fast.

Only flock acquisition repeats, never the transaction or an external operation.
The historical cap is an acquisition limit, not a business/closeout duration promise.
"""
from __future__ import annotations

import fcntl
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from orchestration_common import ContractError, checked_path, finite, json_ref, read_json, require

HISTORICAL_LOCK_ACQUIRE_SECONDS = 300
LOCK_POLL_SECONDS = 0.05


def new_work_guard(co):
    """Cheap pinned records only; the full current action gate runs after locking.

    There is no live registration in offline fixtures. Their v3 flock wait is
    nevertheless real, with the bounded historical acquisition cap.
    """
    if co.fixtures:
        return None
    from orchestration_permit import elapsed
    registration_ref = co.campaign["registration"]
    registration = json_ref(registration_ref)
    runtime_ref = co.campaign["runtime"]
    runtime = json_ref(runtime_ref)
    startup_ref = runtime["startup"]
    startup = json_ref(startup_ref)
    permit_ref = co.header["run_permit"]
    permit = json_ref(permit_ref)
    limit = finite(registration["contract"]["run_timeout_seconds"], "registered run cap")
    require(limit > 0, "registered run cap must be positive")
    schedule = Path(registration["root"]) / "schedule"
    absent = [co.root / "finish.json", schedule / "halt.json", Path(startup["stop_path"]),
              schedule / "closures" / f"{permit['step']['ordinal']:04d}.json"]
    refs = (registration_ref, runtime_ref, startup_ref, permit_ref)

    def remaining():
        require(read_json(co.root / "run.json") == co.header, "original run header changed while waiting")
        # These are small pinned JSON records, never recursive runtime/campaign validation.
        for ref in refs:
            json_ref(ref)
        require(not any(os.path.lexists(path) for path in absent),
                "new work stopped or closed while waiting; preserve the original evidence")
        measured = elapsed(co.header)
        require(measured["seconds"] is not None, "run clock is unknown; no new work")
        left = limit - measured["seconds"]
        require(left > 0, "registered time cap reached while waiting; no new work")
        return left

    return remaining


@contextmanager
def coordinator_lock(path, *, remaining=None):
    """Wait on one original inode; never unlink/reopen it or repeat the body."""
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    budget = finite(remaining() if remaining else HISTORICAL_LOCK_ACQUIRE_SECONDS,
                    "coordinator acquisition cap")
    require(budget > 0, "coordinator lock acquisition timed out")
    deadline = time.monotonic() + budget
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        original = os.fstat(fd)
        require(stat.S_ISREG(original.st_mode) and original.st_uid == os.getuid(), "lock owner/type mismatch")
        signature = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid)

        def check():
            nonlocal deadline
            try:
                current = checked_path(path).lstat()
            except (OSError, ContractError) as exc:
                raise ContractError("coordinator lock path changed during acquisition") from exc
            require(signature(current) == signature(original), "coordinator lock path changed during acquisition")
            if remaining:
                deadline = min(deadline, time.monotonic() + remaining())
            require(time.monotonic() < deadline, "coordinator lock acquisition timed out; preserve original evidence")

        while True:
            check()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(LOCK_POLL_SECONDS, max(0, deadline - time.monotonic())))
        check()
        yield
    finally:
        os.close(fd)
