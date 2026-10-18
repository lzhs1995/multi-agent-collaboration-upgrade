"""Fail-closed inventory and the real materialized Darwin execution boundary."""
import ctypes
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import cmux_daemon_identity as identity
import upgrade_process_inventory as inventory


class InventoryTests(unittest.TestCase):
    def test_truncation_grows_and_duplicate_or_bad_length_refuses(self):
        calls = []
        def listing(kind, uid, buf, size):
            self.assertEqual((kind, uid), (4, os.getuid()))
            calls.append(size)
            if len(calls) == 1:
                return size
            buf[0], buf[1] = 12, 13
            return 8
        self.assertEqual(inventory._pids(types.SimpleNamespace(proc_listpids=listing)), [12, 13])
        self.assertEqual(len(calls), 2)
        for result in (-1, 0, 3):
            with self.assertRaises(identity.IdentityError):
                inventory._pids(types.SimpleNamespace(proc_listpids=lambda *a: result))
        def duplicates(kind, uid, buf, size):
            buf[0] = buf[1] = 12
            return 8
        with self.assertRaisesRegex(identity.IdentityError, "duplicate"):
            inventory._pids(types.SimpleNamespace(proc_listpids=duplicates))
        with self.assertRaisesRegex(identity.IdentityError, "exceeds bound"):
            inventory._pids(types.SimpleNamespace(proc_listpids=lambda *a: a[-1]))


    def test_terminal_requires_birth_owner_and_stability(self):
        expected = dict(pid=23, birth=[100, 200])
        def read(pid, kind, arg, buf, size):
            b = ctypes.cast(buf, ctypes.POINTER(identity.BsdInfo)).contents
            b.pid, b.uid, b.start_sec, b.start_usec = 23, os.getuid(), 100, 200
            b.misc[3] = 0xFFFFFFFF
            return size
        lib = types.SimpleNamespace(proc_pidinfo=read)
        self.assertEqual(inventory._tty_device(lib, expected), 0xFFFFFFFF)
        with self.assertRaisesRegex(identity.IdentityError, "changed"):
            inventory._tty_device(lib, dict(expected, birth=[100, 201]))
        with patch.object(inventory, "_lib", return_value=lib), \
                patch.object(identity, "process", return_value=expected):
            self.assertEqual(inventory.terminal(expected), "?")
            with patch.object(inventory, "_tty_device", side_effect=[0xFFFFFFFF, 1]):
                with self.assertRaisesRegex(identity.IdentityError, "changed during"):
                    inventory.terminal(expected)
        with patch.object(identity, "process", return_value=dict(expected, birth=[2, 3])):
            with self.assertRaisesRegex(identity.IdentityError, "changed before"):
                inventory.terminal(expected)


class DiscoveryKernelTests(unittest.TestCase):
    """Exercise syscall boundaries, including the two real inner-read races."""

    def scan(self, events=(), *, name=b'sleep', path=b'/bin/sleep', path_errno=0,
             path_count=None, sentinel=True):
        import errno
        events = list(events)
        self.reads, self.paths = [], []
        counts = {}
        def bsd(pid, kind, arg, pointer, size):
            index = counts.get(pid, 0)
            counts[pid] = index + 1
            self.reads.append((pid, index))
            spec = (events[index] if pid == 23 and index < len(events) else {})
            ctypes.set_errno(spec.get('errno', 0))
            if 'count' in spec:
                return spec['count']
            b = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
            b.pid, b.uid, b.ppid, b.status = pid, os.getuid(), 7, 1
            b.start_sec, b.start_usec = 100, 200
            b.name = name if pid == 23 else b'codex'
            for key, value in spec.items():
                if key != 'errno':
                    setattr(b, key, value)
            return size
        def executable(pid, buf, size):
            self.paths.append(pid)
            if pid == 24:
                buf.value = b'/opt/bin/codex'
                return len(buf.value) + 1
            ctypes.set_errno(path_errno)
            if path_count is not None:
                return path_count
            buf.value = path
            return len(path) + 1
        lib = types.SimpleNamespace(proc_pidinfo=bsd, proc_pidpath=executable)
        with patch.object(inventory, '_lib', return_value=lib), \
                patch.object(inventory, '_pids', return_value=[23, 24] if sentinel else [23]), \
                patch.object(identity, 'process', side_effect=AssertionError('strict reader must remain separate')):
            return inventory.inventory(lambda value: Path(value).name in ('codex', 'claude'))

    def test_noncandidate_exit_at_each_bsd_boundary_preserves_live_candidate(self):
        import errno
        for index in range(4):
            for terminal in ({'count': 0, 'errno': errno.ESRCH}, {'status': 5}):
                with self.subTest(index=index, terminal=terminal):
                    result = self.scan([{}] * index + [terminal])
                    self.assertEqual(result, [['24', '/opt/bin/codex']])
                    self.assertEqual(sum(pid == 23 for pid, _ in self.reads), index + 1)

    def test_candidate_and_unknown_inner_exits_refuse_without_path_or_rescan(self):
        import errno
        for name in (b'codex', b'claude', b'', b'x' * 31, b'\xff'):
            for terminal in ({'count': 0, 'errno': errno.ESRCH}, {'status': 5}):
                with self.subTest(name=name, terminal=terminal):
                    with self.assertRaisesRegex(identity.IdentityError, 'candidate or unknown'):
                        self.scan([{}, terminal], name=name)
                    self.assertEqual(self.reads, [(23, 0), (23, 1)])
                    self.assertEqual(self.paths, [])

    def test_candidate_kernel_path_overrides_earlier_unrelated_bsd_name(self):
        import errno
        for index in (2, 3):
            for terminal in ({'count': 0, 'errno': errno.ESRCH}, {'status': 5}):
                with self.subTest(index=index, terminal=terminal):
                    with self.assertRaisesRegex(identity.IdentityError, 'candidate or unknown'):
                        self.scan([{}] * index + [terminal], path=b'/opt/bin/codex')
                    self.assertEqual(sum(pid == 23 for pid, _ in self.reads), index + 1)

    def test_bsd_permission_unknown_and_partial_reads_always_refuse(self):
        import errno
        failures = [{'count': 0, 'errno': value}
                    for value in (errno.EPERM, errno.EACCES, errno.EIO, 0)]
        failures += [{'count': ctypes.sizeof(identity.BsdInfo) - 1, 'errno': errno.ESRCH}]
        for index in range(4):
            for failure in failures:
                with self.subTest(index=index, failure=failure):
                    with self.assertRaisesRegex(identity.IdentityError, 'inaccessible') as result:
                        self.scan([{}] * index + [failure])
                    self.assertEqual(result.exception.discovery['errno'], failure['errno'])
                    self.assertEqual(result.exception.discovery['pid'], 23)
                    self.assertEqual(sum(pid == 23 for pid, _ in self.reads), index + 1)

    def test_identity_or_name_drift_is_never_erased_by_zombie_or_later_exit(self):
        import errno
        variants = ({'pid': 999}, {'uid': os.getuid() + 1}, {'ppid': 8},
                    {'start_sec': 101}, {'start_usec': 201}, {'name': b'changed'},
                    {'name': b'codex'}, {'start_sec': 0})
        for index in (1, 2, 3):
            for variant in variants:
                for zombie in (False, True):
                    entry = dict(variant, **({'status': 5} if zombie else {}))
                    with self.subTest(index=index, entry=entry):
                        with self.assertRaisesRegex(identity.IdentityError, 'changed'):
                            self.scan([{}] * index + [entry, {'count': 0, 'errno': errno.ESRCH}])
                        self.assertEqual(sum(pid == 23 for pid, _ in self.reads), index + 1)

    def test_path_esrch_requires_prior_complete_unrelated_name(self):
        import errno
        self.assertEqual(self.scan(path_count=0, path_errno=errno.ESRCH), [['24', '/opt/bin/codex']])
        self.assertEqual(sum(pid == 23 for pid, _ in self.reads), 2)
        for name in (b'codex', b'claude', b'', b'x' * 31, b'\xff'):
            with self.subTest(name=name), self.assertRaisesRegex(identity.IdentityError, 'candidate executable'):
                self.scan(name=name, path_count=0, path_errno=errno.ESRCH)

    def test_path_permission_and_unknown_fail_before_fallback(self):
        import errno
        for error in (errno.EPERM, errno.EACCES, errno.EIO, 0):
            with self.subTest(errno=error):
                with self.assertRaisesRegex(identity.IdentityError, 'inaccessible') as result:
                    self.scan([{}, {}, {'count': 0, 'errno': errno.ESRCH}], path_count=0, path_errno=error)
                self.assertEqual(result.exception.discovery['errno'], error)
                self.assertEqual(self.reads, [(23, 0), (23, 1)])

    def test_unlinked_executable_requires_stable_complete_unrelated_name(self):
        import errno
        self.assertEqual(self.scan(path_count=0, path_errno=errno.ENOENT), [['24', '/opt/bin/codex']])
        self.assertEqual(sum(pid == 23 for pid, _ in self.reads), 5)
        for name in (b'codex', b'', b'x' * 31, b'\xff'):
            with self.subTest(name=name), self.assertRaisesRegex(identity.IdentityError, 'candidate executable'):
                self.scan(name=name, path_count=0, path_errno=errno.ENOENT)
        for variant in ({'ppid': 8}, {'start_sec': 101}, {'name': b'codex'},
                        {'start_sec': 101, 'status': 5}):
            with self.subTest(variant=variant), self.assertRaisesRegex(identity.IdentityError, 'changed'):
                self.scan([{}, {}, variant], path_count=0, path_errno=errno.ENOENT)

    def test_unlinked_fallback_exit_does_not_hide_live_candidate(self):
        import errno
        for index in (2, 3, 4):
            for terminal in ({'count': 0, 'errno': errno.ESRCH}, {'status': 5}):
                with self.subTest(index=index, terminal=terminal):
                    self.assertEqual(self.scan([{}] * index + [terminal], path_count=0,
                                               path_errno=errno.ENOENT), [['24', '/opt/bin/codex']])

    def test_absolute_candidate_path_survives_unknown_bsd_name_when_all_reads_stable(self):
        for name in (b'codex', b'', b'x' * 31, b'\xff'):
            with self.subTest(name=name):
                self.assertEqual(self.scan(name=name, path=b'/opt/bin/codex'),
                                 [['23', '/opt/bin/codex'], ['24', '/opt/bin/codex']])
        for path in (b'relative', b'', b'/bad/\xff'):
            with self.subTest(path=path), self.assertRaisesRegex(identity.IdentityError, 'invalid'):
                self.scan(path=path)
        with self.assertRaisesRegex(identity.IdentityError, 'invalid'):
            self.scan(path_count=4096)
    def test_unlinked_fallback_permission_unknown_and_short_reads_at_all_boundaries(self):
        import errno
        failures = [{'count': 0, 'errno': e} for e in (errno.EPERM, errno.EACCES, errno.EIO, 0)]
        failures += [{'count': ctypes.sizeof(identity.BsdInfo)-1, 'errno': errno.ESRCH}]
        for index in (2, 3, 4):
            for failure in failures:
                with self.subTest(index=index, failure=failure):
                    with self.assertRaisesRegex(identity.IdentityError, 'inaccessible') as caught:
                        self.scan([{}]*index+[failure], path_count=0, path_errno=errno.ENOENT)
                    self.assertEqual(caught.exception.discovery['errno'], failure['errno'])
                    self.assertEqual(sum(pid==23 for pid,_ in self.reads), index+1)

    def test_unlinked_full_identity_and_name_drift_at_all_boundaries(self):
        import errno
        variants=({'pid':999}, {'uid':os.getuid()+1}, {'ppid':8}, {'start_sec':101}, {'start_usec':201}, {'name':b'changed'}, {'start_sec':0})
        for index in (2,3,4):
            for variant in variants:
                for zombie in (False,True):
                    entry=dict(variant, **({'status':5} if zombie else {}))
                    with self.subTest(index=index, entry=entry):
                        with self.assertRaisesRegex(identity.IdentityError,'changed'):
                            self.scan([{}]*index+[entry], path_count=0, path_errno=errno.ENOENT)
                        self.assertEqual(sum(pid==23 for pid,_ in self.reads),index+1)

    def test_initial_full_zombie_still_validates_basic_identity(self):
        for entry in ({'pid':999}, {'uid':os.getuid()+1}, {'start_sec':0}):
            with self.subTest(entry=entry), self.assertRaisesRegex(identity.IdentityError,'changed'):
                self.scan([dict(entry,status=5)])


class TtyInventoryTests(unittest.TestCase):
    """Scoped discovery must close the full kernel set, never select the first."""

    def scan(self, *, events=(), name=b'sleep', path=b'/bin/sleep', path_errno=0,
             path_count=None, listings=((23, 24), (23, 24)), final_pin=None):
        events, listings = list(events), list(listings)
        self.reads, self.listings, self.paths = [], [], []
        counts = {}
        device = 0x12000007
        def listing(kind, selector, buf, size):
            self.assertEqual((kind, selector), (3, device))
            members = listings[len(self.listings)]
            self.listings.append(members)
            for index, pid in enumerate(members):
                buf[index] = pid
            return len(members) * ctypes.sizeof(ctypes.c_int)
        def bsd(pid, kind, arg, pointer, size):
            self.assertIn(pid, (23, 24), 'unrelated TTY processes must not be read')
            index = counts.get(pid, 0)
            counts[pid] = index + 1
            self.reads.append((pid, index))
            spec = events[index] if pid == 23 and index < len(events) else {}
            ctypes.set_errno(spec.get('errno', 0))
            if 'count' in spec:
                return spec['count']
            b = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
            b.pid, b.uid, b.ppid, b.status = pid, os.getuid(), 7, 1
            b.start_sec, b.start_usec, b.misc[3] = 100, 200, device
            b.name = name if pid == 23 else b'codex'
            for key, value in spec.items():
                if key == 'tty_device':
                    b.misc[3] = value
                elif key != 'errno':
                    setattr(b, key, value)
            return size
        def executable(pid, buf, size):
            self.paths.append(pid)
            ctypes.set_errno(path_errno if pid == 23 else 0)
            if pid == 23 and path_count is not None:
                return path_count
            buf.value = path if pid == 23 else b'/opt/bin/codex'
            return len(buf.value) + 1
        lib = types.SimpleNamespace(proc_listpids=listing, proc_pidinfo=bsd, proc_pidpath=executable)
        pin = dict(path='/dev/ttys007', device=device, inode=12)
        with patch.object(inventory, '_lib', return_value=lib), \
                patch.object(inventory, '_terminal_selector', side_effect=[pin, final_pin or pin]), \
                patch.object(identity, 'process', side_effect=AssertionError('strict reader is separate')):
            return inventory.inventory_for_tty(lambda value: Path(value).name in ('codex', 'claude'), 'ttys007')

    def test_only_exact_tty_members_are_read_and_closed(self):
        self.assertEqual(self.scan(), [['24', '/opt/bin/codex']])
        self.assertEqual(self.listings, [(23, 24), (23, 24)])
        self.assertEqual(set(self.reads), {(pid, read) for pid in (23, 24) for read in range(6)})
        # More than one candidate must survive discovery for the native gate
        # to reject ambiguity; discovery must never return only the first.
        self.assertEqual(self.scan(path=b'/opt/bin/claude'),
                         [['23', '/opt/bin/claude'], ['24', '/opt/bin/codex']])

    def test_member_set_expansion_removal_reuse_and_duplicates_refuse(self):
        for members in ((23, 24, 25), (24,), (23, 25), (23, 23)):
            with self.subTest(members=members), self.assertRaises(identity.IdentityError):
                self.scan(listings=((23, 24), members))
        with self.assertRaisesRegex(identity.IdentityError, 'device changed'):
            self.scan(final_pin={'device': 0x12000007, 'inode': 13})

    def test_every_scoped_bsd_error_exit_or_drift_refuses_even_for_unrelated_name(self):
        import errno
        faults = [{'count': 0, 'errno': error} for error in
                  (errno.ESRCH, errno.EPERM, errno.EACCES, errno.EIO, 0)]
        faults += [{'count': ctypes.sizeof(identity.BsdInfo) - 1, 'errno': errno.ESRCH},
                   {'status': 5}, {'pid': 99}, {'uid': os.getuid() + 1},
                   {'start_sec': 0}, {'tty_device': 0x12000008}]
        for index in range(6):
            for fault in faults:
                with self.subTest(index=index, fault=fault), self.assertRaises(identity.IdentityError):
                    self.scan(events=[{}] * index + [fault])
                self.assertEqual(sum(pid == 23 for pid, _ in self.reads), index + 1)
        for index in range(1, 6):
            for fault in ({'ppid': 8}, {'start_sec': 101}, {'start_usec': 201}, {'name': b'changed'}):
                with self.subTest(index=index, fault=fault), self.assertRaisesRegex(identity.IdentityError, 'changed'):
                    self.scan(events=[{}] * index + [fault])

    def test_path_errors_and_unknown_candidates_cannot_disappear(self):
        import errno
        for error in (errno.ESRCH, errno.EPERM, errno.EACCES, errno.EIO, 0):
            with self.subTest(error=error), self.assertRaisesRegex(identity.IdentityError, 'inaccessible'):
                self.scan(path_count=0, path_errno=error)
        for name in (b'codex', b'claude', b'', b'x' * 31, b'\xff'):
            with self.subTest(name=name), self.assertRaises(identity.IdentityError):
                self.scan(name=name, path_count=0, path_errno=errno.ENOENT)

    def test_selector_refuses_aliases_symlinks_nondevices_and_nodev(self):
        for tty in (None, '', '?', '??', '-', 'tty', '../ttys007', '/dev/ttys007', 'ttys007\x00'):
            with self.subTest(tty=tty), self.assertRaisesRegex(identity.IdentityError, 'selector invalid'):
                inventory._terminal_selector(tty)
        expected = Path('/dev/ttys007')
        info = dict(st_mode=stat.S_IFCHR | 0o620, st_rdev=0x12000007, st_dev=1, st_ino=2,
                    st_uid=os.getuid(), st_gid=os.getgid(), st_ctime_ns=123)
        with patch.object(Path, 'lstat', return_value=types.SimpleNamespace(**info)), \
                patch.object(Path, 'resolve', return_value=expected):
            self.assertEqual(inventory._terminal_selector('ttys007')['device'], info['st_rdev'])
        for changed in ({'st_mode': stat.S_IFREG | 0o600}, {'st_mode': stat.S_IFLNK | 0o777},
                        {'st_rdev': 0xFFFFFFFF}):
            with self.subTest(changed=changed), \
                    patch.object(Path, 'lstat', return_value=types.SimpleNamespace(**dict(info, **changed))), \
                    patch.object(Path, 'resolve', return_value=expected), self.assertRaises(identity.IdentityError):
                inventory._terminal_selector('ttys007')
        with patch.object(Path, 'lstat', return_value=types.SimpleNamespace(**info)), \
                patch.object(Path, 'resolve', return_value=Path('/other/ttys007')), self.assertRaises(identity.IdentityError):
            inventory._terminal_selector('ttys007')

    def test_tty_enumeration_keeps_bounded_grow_and_error_checks(self):
        calls = []
        def listing(kind, device, buf, size):
            self.assertEqual((kind, device), (3, 99))
            calls.append(size)
            if len(calls) == 1:
                return size
            buf[0] = 23
            return 4
        self.assertEqual(inventory._pids(types.SimpleNamespace(proc_listpids=listing), tty_device=99), [23])
        self.assertEqual(len(calls), 2)
        for count in (-1, 0, 3):
            with self.subTest(count=count), self.assertRaises(identity.IdentityError):
                inventory._pids(types.SimpleNamespace(proc_listpids=lambda *args: count), tty_device=99)
        with self.assertRaisesRegex(identity.IdentityError, 'exceeds bound'):
            inventory._pids(types.SimpleNamespace(proc_listpids=lambda *args: args[-1]), tty_device=99)

    def test_scoped_unlinked_fallback_still_rejects_every_late_read_failure(self):
        import errno
        self.assertEqual(self.scan(path_count=0, path_errno=errno.ENOENT), [['24', '/opt/bin/codex']])
        for index in range(2, 7):
            for fault in ({'tty_device': 0x12000008}, {'count': 0, 'errno': errno.ESRCH},
                          {'count': 0, 'errno': errno.EPERM}, {'start_sec': 101}):
                with self.subTest(index=index, fault=fault), self.assertRaises(identity.IdentityError):
                    self.scan(events=[{}] * index + [fault], path_count=0, path_errno=errno.ENOENT)


if __name__ == "__main__":
    unittest.main()
