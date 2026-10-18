"""Private Darwin discovery/TTY reads without launching the setuid ps tool.

Discovery is never authentication. The original native reader still verifies
argv, owner, executable, birth and cmux UUIDs for every selected process.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat
import sys

import cmux_identity_budget as budget


def _lib():
    from cmux_daemon_identity import IdentityError
    if sys.platform != "darwin":
        raise IdentityError("live Darwin inventory required")
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.c_void_p, ctypes.c_int]
    lib.proc_listpids.restype = ctypes.c_int
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    return lib


def _pids(lib):
    from cmux_daemon_identity import IdentityError
    # PROC_UID_ONLY=4, as declared by Darwin libproc.h. Grow a bounded buffer
    # rather than accepting a possibly truncated inventory as unique evidence.
    for capacity in (1024, 4096, 16384, 65536):
        budget.check()
        buf = (ctypes.c_int * capacity)()
        count = lib.proc_listpids(4, os.getuid(), buf, ctypes.sizeof(buf))
        if count <= 0 or count % ctypes.sizeof(ctypes.c_int):
            raise IdentityError("kernel client inventory unavailable")
        if count >= ctypes.sizeof(buf):
            continue
        rows = [pid for pid in buf[:count // ctypes.sizeof(ctypes.c_int)] if pid > 1]
        if len(rows) != len(set(rows)):
            raise IdentityError("duplicate kernel client inventory")
        return rows
    raise IdentityError("kernel client inventory exceeds bound")


def _discovery_error(message, pid, stage, *, count=None, error=None):
    from cmux_daemon_identity import IdentityError
    details = dict(pid=pid, stage=stage, count=count, errno=error)
    exc = IdentityError(message + f" (pid={pid}, stage={stage}, count={count}, errno={error})")
    exc.discovery = details
    return exc


def _kernel_bsd(lib, pid, stage, *, expected=None):
    """One discovery-only read; retain the original errno and negative evidence."""
    import errno
    from cmux_daemon_identity import BsdInfo
    budget.check()
    b = BsdInfo()
    ctypes.set_errno(0)
    count = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(b), ctypes.sizeof(b))
    error = ctypes.get_errno()
    if count != ctypes.sizeof(b):
        if count <= 0 and error == errno.ESRCH:
            return None
        raise _discovery_error("kernel process identity inaccessible", pid, stage,
                               count=count, error=error)
    if b.pid != pid or b.uid != os.getuid() or not b.start_sec:
        raise _discovery_error("kernel process identity changed, foreign, or invalid", pid, stage)
    current = dict(identity=dict(pid=pid, ppid=b.ppid, birth=[b.start_sec, b.start_usec]),
                   name=bytes(b.name), zombie=b.status == 5)
    # Validate before considering zombie/exit. A reused PID cannot erase drift.
    if expected is not None and (current['identity'] != expected['identity']
                                 or current['name'] != expected['name']):
        raise _discovery_error("kernel executable inventory changed", pid, stage)
    return current


def _unrelated_name(snapshot, candidate):
    raw = snapshot['name']
    if not raw or len(raw) >= 31:
        return False
    try:
        name = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        return False
    return not candidate(name)


def _discovery_pair(lib, pid, candidate, *, stage, previous=None, known_noncandidate=None):
    """No retries. An exit can exclude only an original known unrelated row."""
    first = _kernel_bsd(lib, pid, stage + '_bsd_1', expected=previous)
    if first is None or first['zombie']:
        if previous is None or known_noncandidate is True:
            return None
        raise _discovery_error("candidate or unknown process exited", pid, stage + '_bsd_1')
    second = _kernel_bsd(lib, pid, stage + '_bsd_2', expected=first)
    if second is None or second['zombie']:
        may_exclude = (_unrelated_name(first, candidate) if known_noncandidate is None
                       else known_noncandidate)
        if may_exclude:
            return None
        raise _discovery_error("candidate or unknown process exited", pid, stage + '_bsd_2')
    return second


def inventory(candidate):
    """Return discovery-only [PID string, kernel executable path] rows.

    A complete BSD basename is negative discovery evidence only. Selected native
    clients still pass the original strict process()/argv/TTY/session checks.
    """
    import errno
    lib, rows = _lib(), []
    for pid in _pids(lib):
        before = _discovery_pair(lib, pid, candidate, stage='before_path')
        if before is None:
            continue
        buf = ctypes.create_string_buffer(4096)
        ctypes.set_errno(0)
        count = lib.proc_pidpath(pid, buf, len(buf))
        error = ctypes.get_errno()
        if count <= 0:
            # EPERM/EACCES/unknown reads must never turn into successful scans.
            # ENOENT permits the original stable-name unlinked-file fallback.
            if error not in (errno.ESRCH, errno.ENOENT):
                raise _discovery_error("kernel executable inventory inaccessible", pid,
                                       'path', count=count, error=error)
            if error == errno.ESRCH:
                if _unrelated_name(before, candidate):
                    continue
                raise _discovery_error("candidate executable inventory inaccessible", pid,
                                       'path', count=count, error=error)
            fallback = _kernel_bsd(lib, pid, 'path_fallback_bsd', expected=before)
            if fallback is None or fallback['zombie']:
                if _unrelated_name(before, candidate):
                    continue
                raise _discovery_error("candidate executable inventory inaccessible", pid,
                                       'path_fallback_bsd')
            if not _unrelated_name(fallback, candidate):
                raise _discovery_error("candidate executable inventory inaccessible", pid,
                                       'path_fallback_bsd')
            before, path, is_candidate = fallback, None, False
        else:
            try:
                path = buf.value.decode('utf-8', errors='strict')
            except UnicodeDecodeError:
                raise _discovery_error("kernel executable inventory invalid", pid, 'path')
            if not Path(path).is_absolute() or count >= len(buf):
                raise _discovery_error("kernel executable inventory invalid", pid, 'path')
            is_candidate = candidate(path)
        after = _discovery_pair(lib, pid, candidate, stage='after_path', previous=before,
                                known_noncandidate=not is_candidate)
        if after is not None and is_candidate:
            rows.append([str(pid), path])
    budget.check()
    return rows




def _tty_device(lib, expected):
    from cmux_daemon_identity import BsdInfo, IdentityError
    b = BsdInfo()
    if lib.proc_pidinfo(expected["pid"], 3, 0, ctypes.byref(b), ctypes.sizeof(b)) != ctypes.sizeof(b):
        raise IdentityError("terminal process identity inaccessible")
    if (b.pid != expected["pid"] or b.uid != os.getuid() or b.status == 5
            or [b.start_sec, b.start_usec] != expected["birth"]):
        raise IdentityError("terminal process identity changed")
    # proc_bsdinfo: nfiles, pgid, pjobc, e_tdev, e_tpgid, nice.
    return int(b.misc[3])


def terminal(expected):
    """Read a stable controlling terminal; return '?' only for NODEV.

    Device names are resolved from actual character devices, never from argv
    or a file descriptor which may point at a different terminal.
    """
    from cmux_daemon_identity import IdentityError, process
    budget.check()
    if process(expected["pid"]) != expected:
        raise IdentityError("terminal client changed before read")
    lib = _lib()
    device = _tty_device(lib, expected)
    matches = []
    if device != 0xFFFFFFFF:  # Darwin NODEV, represented by uint32_t e_tdev.
        for path in Path("/dev").glob("tty*"):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISCHR(info.st_mode) and (info.st_rdev & 0xFFFFFFFF) == device:
                matches.append(path.name)
        if len(matches) != 1:
            raise IdentityError("controlling terminal is not unique")
    if _tty_device(lib, expected) != device or process(expected["pid"]) != expected:
        raise IdentityError("terminal client changed during read")
    budget.check()
    return matches[0] if matches else "?"
