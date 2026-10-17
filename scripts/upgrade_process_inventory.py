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


def inventory(candidate):
    """Return discovery-only [PID string, kernel executable path] rows."""
    from cmux_daemon_identity import IdentityError, ProcessExited, process
    lib, rows = _lib(), []
    for pid in _pids(lib):
        budget.check()
        try:
            before = process(pid, arguments=False)
        except ProcessExited:
            continue  # Only initial ESRCH/zombie is harmless, never EPERM.
        buf = ctypes.create_string_buffer(4096)
        ctypes.set_errno(0)
        if lib.proc_pidpath(pid, buf, len(buf)) <= 0:
            # A still-live unrelated program may have an unlinked executable.
            # Fall back only to a stable, untruncated BSD name proving that it
            # cannot be a candidate. Never hide an unreadable candidate.
            from cmux_daemon_identity import BsdInfo
            b = BsdInfo()
            if (lib.proc_pidinfo(pid, 3, 0, ctypes.byref(b), ctypes.sizeof(b)) != ctypes.sizeof(b)
                    or b.pid != pid or b.uid != os.getuid() or b.status == 5
                    or [b.start_sec, b.start_usec] != before["birth"]):
                raise IdentityError("kernel executable inventory inaccessible")
            name = b.name.decode()
            if not name or len(b.name) >= 31 or candidate(name):
                raise IdentityError("candidate executable inventory inaccessible")
            path = None
        else:
            path = buf.value.decode()
            if not Path(path).is_absolute():
                raise IdentityError("kernel executable inventory invalid")
        if process(pid, arguments=False) != before:
            raise IdentityError("kernel executable inventory changed")
        if path is not None and candidate(path):
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
