"""Private Darwin discovery/TTY reads without launching the setuid ps tool.

Discovery is never authentication. The original native reader still verifies
argv, owner, executable, birth and cmux UUIDs for every selected process.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
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


def _pids(lib, *, tty_device=None):
    from cmux_daemon_identity import IdentityError
    # Darwin sys/proc_info.h: PROC_UID_ONLY=4, PROC_TTY_ONLY=3.
    # Grow a bounded buffer
    # rather than accepting a possibly truncated inventory as unique evidence.
    for capacity in (1024, 4096, 16384, 65536):
        budget.check()
        buf = (ctypes.c_int * capacity)()
        kind, selector = (4, os.getuid()) if tty_device is None else (3, tty_device)
        count = lib.proc_listpids(kind, selector, buf, ctypes.sizeof(buf))
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


def _kernel_bsd(lib, pid, stage, *, expected=None, tty_device=None, absence=None,
                lifecycle=False):
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
            if absence is not None:
                absence.update(pid=pid, stage=stage, count=count, errno=error)
            return None
        raise _discovery_error("kernel process identity inaccessible", pid, stage,
                               count=count, error=error)
    if b.pid != pid or b.uid != os.getuid() or not b.start_sec:
        raise _discovery_error("kernel process identity changed, foreign, or invalid", pid, stage)
    if lifecycle and b.status not in (1, 2, 3, 4, 5):
        raise _discovery_error("kernel process status unknown", pid, stage,
                               count=count, error=error)
    current = dict(identity=dict(pid=pid, ppid=b.ppid, birth=[b.start_sec, b.start_usec]),
                   name=bytes(b.name), zombie=b.status == 5,
                   uid=int(b.uid), tty_device=int(b.misc[3]))
    if lifecycle:
        current['status'] = int(b.status)
    if tty_device is not None:
        current['tty_device'] = int(b.misc[3])
        if current['tty_device'] != tty_device:
            raise _discovery_error("kernel terminal inventory changed", pid, stage)
    # Validate before considering zombie/exit. A reused PID cannot erase drift.
    if expected is not None and (current['identity'] != expected['identity']
                                 or current['name'] != expected['name']):
        raise _discovery_error("kernel executable inventory changed", pid, stage)
    return current


def observe_original_process(expected):
    """Two bounded kernel reads of an already pinned PID/birth, not API health.

    An absent PID is not SESSION_LOST, a healthy server or permission to resume.
    Callers must authenticate original membership separately. No inventory,
    substitute process selection, input, signal or task management occurs.
    """
    import copy
    from cmux_daemon_identity import BsdInfo
    from orchestration_common import require
    require(isinstance(expected, dict) and type(expected.get('pid')) is int
            and expected['pid'] > 1 and isinstance(expected.get('birth'), list)
            and len(expected['birth']) == 2
            and all(type(item) is int for item in expected['birth'])
            and expected['birth'][0] > 0 and 0 <= expected['birth'][1] < 1000000
            and isinstance(expected.get('executable'), str)
            and Path(expected['executable']).is_absolute(),
            'ORIGINAL_PROCESS_BINDING_REQUIRED')
    lib, pid, reads = _lib(), expected['pid'], []

    def read(stage):
        absent = {}
        value = _kernel_bsd(lib, pid, stage, absence=absent, lifecycle=True)
        error = ctypes.get_errno()
        if value is None:
            reads.append(dict(absent))
            return None
        require(error == 0, 'ORIGINAL_PROCESS_KERNEL_STATUS_UNKNOWN')
        require(value['identity']['pid'] == pid
                and value['identity']['birth'] == expected['birth']
                and ('ppid' not in expected or value['identity']['ppid'] == expected['ppid']),
                'ORIGINAL_PROCESS_IDENTITY_CHANGED')
        reads.append(dict(pid=pid, stage=stage, count=ctypes.sizeof(BsdInfo), errno=error,
            snapshot=dict(value['identity'], uid=value['uid'], tty_device=value['tty_device'],
                name_hex=value['name'].hex(), zombie=value['zombie'], status=value['status'])))
        return value

    def executable():
        budget.check()
        path = ctypes.create_string_buffer(4096)
        ctypes.set_errno(0)
        count = lib.proc_pidpath(pid, path, len(path))
        error = ctypes.get_errno()
        require(0 < count <= len(path) and error == 0
                and path.value.decode('utf-8') == expected['executable'],
                'ORIGINAL_PROCESS_EXECUTABLE_CHANGED_OR_UNKNOWN')
        return dict(count=count, errno=error, path=expected['executable'])

    first = read('original_process_bsd_1')
    path_reads = []
    if first is not None and not first['zombie']:
        path_reads.append(executable())
    second = read('original_process_bsd_2')
    require((first is None) == (second is None)
            and (first is None or first == second),
            'ORIGINAL_PROCESS_CHANGED_DURING_OBSERVATION')
    if first is None:
        state = 'ORIGINAL_PID_ABSENT'
    elif first['zombie']:
        state = 'ORIGINAL_PROCESS_ZOMBIE'
    else:
        path_reads.append(executable())
        require(path_reads[0] == path_reads[1],
                'ORIGINAL_PROCESS_CHANGED_DURING_OBSERVATION')
        state = 'ORIGINAL_PROCESS_PRESENT'
    budget.check()
    return dict(state=state, original_process=copy.deepcopy(expected),
                kernel_reads=reads, executable_reads=path_reads)


class _KinfoProc(ctypes.Structure):
    """SDK-checked Darwin 25.5 arm64 LP64 layout, never a BSD replacement.

    Opaque regions intentionally omit kernel pointers and the truncated comm.
    The platform gate and a fresh self BSD/sysctl/BSD comparison are mandatory.
    """
    _fields_ = [('start_sec', ctypes.c_int64), ('start_usec', ctypes.c_int32),
                ('_to_status', ctypes.c_ubyte * 24), ('status', ctypes.c_uint8),
                ('_to_pid', ctypes.c_ubyte * 3), ('pid', ctypes.c_int32),
                ('_to_uid', ctypes.c_ubyte * 376), ('uid', ctypes.c_uint32),
                ('_to_ppid', ctypes.c_ubyte * 136), ('ppid', ctypes.c_int32),
                ('_to_tty', ctypes.c_ubyte * 8), ('tty_device', ctypes.c_uint32),
                ('_tail', ctypes.c_ubyte * 72)]


def _kinfo_lib():
    from cmux_daemon_identity import IdentityError
    known = dict(start_sec=0, start_usec=8, status=36, pid=40, uid=420,
                 ppid=560, tty_device=572)
    if (sys.platform != 'darwin' or os.uname().machine != 'arm64'
            or os.uname().release != '25.5.0' or sys.byteorder != 'little'
            or ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(ctypes.c_long) != 8
            or ctypes.sizeof(ctypes.c_int) != 4 or ctypes.sizeof(_KinfoProc) != 648
            or ctypes.alignment(_KinfoProc) != 8
            or any(getattr(_KinfoProc, k).offset != v for k, v in known.items())):
        raise IdentityError('kernel zombie layout is not verified on this platform')
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    lib.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    lib.sysctl.restype = ctypes.c_int
    return lib


def _kernel_kinfo(lib, pid, stage):
    budget.check()
    info = _KinfoProc()
    size = ctypes.c_size_t(ctypes.sizeof(info))
    # CTL_KERN=1, KERN_PROC=14, KERN_PROC_PID=1, from the measured SDK.
    mib = (ctypes.c_int * 4)(1, 14, 1, pid)
    ctypes.set_errno(0)
    result = lib.sysctl(mib, 4, ctypes.byref(info), ctypes.byref(size), None, 0)
    error = ctypes.get_errno()
    if result != 0 or error != 0 or size.value != ctypes.sizeof(info):
        exc = _discovery_error('kernel zombie identity inaccessible', pid, stage,
                               count=size.value, error=error)
        exc.discovery['return'] = result
        raise exc
    if (info.pid != pid or info.uid != os.getuid() or info.ppid < 1
            or info.start_sec <= 0 or not 0 <= info.start_usec < 1000000):
        raise _discovery_error('kernel zombie identity changed, foreign, or invalid', pid, stage)
    return dict(identity=dict(pid=pid, ppid=info.ppid, birth=[info.start_sec, info.start_usec]),
                uid=int(info.uid), tty_device=int(info.tty_device), zombie=info.status == 5,
                status=int(info.status))


def _verify_kinfo_self(bsd_lib, kinfo_lib):
    """Fresh field-level ABI check, not a cached grant or a receiver binding."""
    pid = os.getpid()
    first = _kernel_bsd(bsd_lib, pid, 'zombie_layout_self_bsd_1')
    middle = _kernel_kinfo(kinfo_lib, pid, 'zombie_layout_self_sysctl')
    last = _kernel_bsd(bsd_lib, pid, 'zombie_layout_self_bsd_2', expected=first)
    keys = ('identity', 'uid', 'tty_device')
    if (first is None or last is None or first['zombie'] or middle['zombie'] or last['zombie']
            or middle['status'] not in (1, 2, 3, 4)
            or any(first[k] != middle[k] or first[k] != last[k] for k in keys)):
        raise _discovery_error('kernel zombie layout self identity changed', pid, 'zombie_layout')


def _sysctl_zombie_pair(lib, pid, stage, tty_device, *, previous=None, absence=None):
    """Exclude only an initial dead TTY member using its own narrow proof."""
    try:
        kinfo_lib = _kinfo_lib()
        _verify_kinfo_self(lib, kinfo_lib)
        first = _kernel_kinfo(kinfo_lib, pid, stage + '_sysctl_1')
        if not first['zombie'] or first['tty_device'] != tty_device:
            raise _discovery_error('kernel terminal zombie not stable', pid, stage + '_sysctl_1')
        if previous is not None and first != previous['snapshot']:
            raise _discovery_error('kernel terminal zombie changed', pid, stage + '_sysctl_1')
        second = _kernel_kinfo(kinfo_lib, pid, stage + '_sysctl_2')
        if second != first:
            raise _discovery_error('kernel terminal zombie changed', pid, stage + '_sysctl_2')
    except Exception as exc:
        # Preserve the exact original BSD absence alongside the failed proof.
        exc.bsd_absence = previous['bsd_absence'] if previous is not None else absence
        raise
    return dict(zombie=True, proof='initial_tty_sysctl_zombie_v1', snapshot=second,
                bsd_absence=previous['bsd_absence'] if previous is not None else absence)


def _unrelated_name(snapshot, candidate):
    raw = snapshot['name']
    if not raw or len(raw) >= 31:
        return False
    try:
        name = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        return False
    return not candidate(name)


def _discovery_pair(lib, pid, candidate, *, stage, previous=None, known_noncandidate=None,
                    tty_device=None):
    """Pair identity reads; scoped initial zombies need a separate closed proof.

    A live scoped member exiting still refuses. Unscoped discovery retains its
    original known-unrelated-row exclusion, without retries.
    """
    if previous is not None and previous.get('proof') == 'initial_tty_sysctl_zombie_v1':
        if tty_device is None or stage != 'closed_member':
            raise _discovery_error('kernel zombie proof outside terminal closure', pid, stage)
        return _sysctl_zombie_pair(lib, pid, stage, tty_device, previous=previous)
    absence = {}
    first = _kernel_bsd(lib, pid, stage + '_bsd_1', expected=previous,
                        tty_device=tty_device, absence=absence)
    if first is None and tty_device is not None and previous is None and stage == 'before_path':
        return _sysctl_zombie_pair(lib, pid, stage, tty_device, absence=absence)
    if tty_device is not None and first is not None:
        # A process already dead at the first observation cannot be a live
        # receiver. Prove that fact with two complete matching BSD reads, then
        # retain the snapshot for the final scope closure. Do not infer it from
        # ESRCH, a name, or a later ps observation. A formerly live row dying
        # during this scan must still refuse, including a selected candidate.
        if previous is not None and previous['zombie'] and not first['zombie']:
            raise _discovery_error("kernel terminal zombie changed", pid, stage + '_bsd_1')
        if first['zombie'] and ((previous is None and stage == 'before_path')
                                or (previous is not None and previous['zombie'])):
            second = _kernel_bsd(lib, pid, stage + '_bsd_2', expected=first,
                                 tty_device=tty_device)
            if second is None or not second['zombie']:
                raise _discovery_error("kernel terminal zombie not stable", pid, stage + '_bsd_2')
            return second
    if first is None or first['zombie']:
        if tty_device is not None:
            raise _discovery_error("kernel terminal member exited", pid, stage + '_bsd_1')
        if previous is None or known_noncandidate is True:
            return None
        raise _discovery_error("candidate or unknown process exited", pid, stage + '_bsd_1')
    second = _kernel_bsd(lib, pid, stage + '_bsd_2', expected=first, tty_device=tty_device)
    if second is None or second['zombie']:
        if tty_device is not None:
            raise _discovery_error("kernel terminal member exited", pid, stage + '_bsd_2')
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
    lib = _lib()
    rows, _ = _inventory(lib, _pids(lib), candidate)
    return rows


def _inventory(lib, pids, candidate, *, tty_device=None):
    import errno
    rows, snapshots = [], {}
    for pid in pids:
        before = _discovery_pair(lib, pid, candidate, stage='before_path', tty_device=tty_device)
        if before is None:
            continue
        if before['zombie']:
            # Only scoped initial-zombie branches return this. Keep their own
            # proof type for closure; neither grants a live receiver row.
            snapshots[pid] = before
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
                if tty_device is None and _unrelated_name(before, candidate):
                    continue
                raise _discovery_error("candidate executable inventory inaccessible", pid,
                                       'path', count=count, error=error)
            fallback = _kernel_bsd(lib, pid, 'path_fallback_bsd', expected=before, tty_device=tty_device)
            if fallback is None or fallback['zombie']:
                if tty_device is None and _unrelated_name(before, candidate):
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
                                known_noncandidate=not is_candidate, tty_device=tty_device)
        if after is not None:
            snapshots[pid] = after
        if after is not None and is_candidate:
            rows.append([str(pid), path])
    budget.check()
    return rows, snapshots


def _terminal_selector(tty):
    """Pin a real /dev character device without opening or reading its TTY."""
    from cmux_daemon_identity import IdentityError
    budget.check()
    if not isinstance(tty, str) or re.fullmatch(r'tty[A-Za-z0-9]+', tty) is None:
        raise IdentityError("terminal inventory selector invalid")
    path = Path('/dev') / tty
    info = path.lstat()
    if not stat.S_ISCHR(info.st_mode) or path.resolve(strict=True) != path:
        raise IdentityError("terminal inventory selector is not a canonical character device")
    device = info.st_rdev & 0xFFFFFFFF
    if device == 0xFFFFFFFF:
        raise IdentityError("terminal inventory selector has no device")
    # Darwin devfs updates ctime on ordinary terminal output. Pin stable device
    # identity and access metadata; activity timestamps cannot authenticate it.
    return dict(path=str(path), device=device, dev=info.st_dev, inode=info.st_ino,
                mode=info.st_mode, uid=info.st_uid, gid=info.st_gid)


def _tty_scope_pids(lib, device):
    """Bracket a UID membership sample with the complete controlling TTY set."""
    from cmux_daemon_identity import IdentityError
    pids = _pids(lib, tty_device=device)
    owned = set(_pids(lib))
    if set(_pids(lib, tty_device=device)) != set(pids):
        raise IdentityError("kernel terminal member set changed")
    return pids, [pid for pid in pids if pid in owned]


def inventory_for_tty(candidate, tty):
    """Discover this UID inside the exact TTY, not global env uniqueness.

    No fallback or retry. Kernel UID enumeration excludes foreign login parents
    before any identity read; an EPERM from an owned member still refuses. Both
    the complete TTY set and its UID intersection close against the start, and
    initially live owned members survive their original BSD/path/identity checks.
    An initially dead member needs complete matching zombie pairs at discovery
    and closure (BSD, or separately verified sysctl after initial BSD ESRCH).
    Its PID must remain in both scope sets; sysctl never authenticates a client.
    Reaping, reuse, resurrection, unknown reads and live-to-dead changes refuse.
    Samples are not atomic: transient UID changes can evade discrete reads.
    Selected clients still require every original strict native binding check.
    """
    from cmux_daemon_identity import IdentityError
    pin = _terminal_selector(tty)
    lib, device = _lib(), pin['device']
    pids, owned = _tty_scope_pids(lib, device)
    rows, snapshots = _inventory(lib, owned, candidate, tty_device=device)
    for pid in owned:
        _discovery_pair(lib, pid, candidate, stage='closed_member',
                        previous=snapshots[pid], tty_device=device)
    final_pids, final_owned = _tty_scope_pids(lib, device)
    if set(final_pids) != set(pids):
        raise IdentityError("kernel terminal member set changed")
    if set(final_owned) != set(owned):
        raise IdentityError("kernel terminal UID membership changed")
    if _terminal_selector(tty) != pin:
        raise IdentityError("terminal inventory device changed")
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
