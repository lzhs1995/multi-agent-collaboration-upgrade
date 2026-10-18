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
    def test_zombie_layout_is_explicitly_gated_before_loading_native_library(self):
        known = types.SimpleNamespace(machine='arm64', release='25.5.0')
        library = types.SimpleNamespace(sysctl=types.SimpleNamespace())
        with patch.object(inventory.sys, 'platform', 'darwin'), \
                patch.object(inventory.sys, 'byteorder', 'little'), \
                patch.object(inventory.os, 'uname', return_value=known), \
                patch.object(inventory.ctypes, 'CDLL', return_value=library) as load:
            self.assertIs(inventory._kinfo_lib(), library)
            self.assertEqual(load.call_count, 1)
            for machine, release in (('x86_64', '25.5.0'), ('arm64', '25.6.0'), ('arm64', '24.0.0')):
                load.reset_mock()
                with patch.object(inventory.os, 'uname', return_value=types.SimpleNamespace(
                        machine=machine, release=release)), self.assertRaises(identity.IdentityError):
                    inventory._kinfo_lib()
                load.assert_not_called()
            for module, key, value in ((inventory.sys, 'platform', 'linux'),
                                       (inventory.sys, 'byteorder', 'big')):
                load.reset_mock()
                with patch.object(module, key, value), self.assertRaises(identity.IdentityError):
                    inventory._kinfo_lib()
                load.assert_not_called()

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
             path_count=None, listings=((23, 24),) * 4, final_pin=None,
             uid_listings=((23, 24),) * 2, selector_stats=None,
             sysctl_events=None, self_sysctl_events=(), self_bsd_events=()):
        events, listings, uid_listings = list(events), list(listings), list(uid_listings)
        self.reads, self.listings, self.paths, self.uid_listings = [], [], [], []
        self.enumerations = []
        self.sysctl_reads, self.self_bsd_reads, self.self_sysctl_reads = [], [], []
        counts = {}
        device = 0x12000007
        def listing(kind, selector, buf, size):
            self.enumerations.append(kind)
            if kind == 3:
                self.assertEqual(selector, device)
                members = listings[len(self.listings)]
                self.listings.append(members)
            else:
                self.assertEqual((kind, selector), (4, os.getuid()))
                members = uid_listings[len(self.uid_listings)]
                self.uid_listings.append(members)
            for index, pid in enumerate(members):
                buf[index] = pid
            return len(members) * ctypes.sizeof(ctypes.c_int)
        def bsd(pid, kind, arg, pointer, size):
            self.assertIn(pid, (23, 24, os.getpid()), 'unrelated TTY processes must not be read')
            index = counts.get(pid, 0)
            counts[pid] = index + 1
            if pid == os.getpid():
                self.self_bsd_reads.append(index)
                spec = self_bsd_events[index] if index < len(self_bsd_events) else {}
            else:
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
        def sysctl(mib, count, pointer, length, new, newlen):
            self.assertEqual((list(mib)[:3], count, new, newlen), ([1, 14, 1], 4, None, 0))
            pid = mib[3]
            self.assertIn(pid, (23, os.getpid()))
            reads = self.self_sysctl_reads if pid == os.getpid() else self.sysctl_reads
            variants = self_sysctl_events if pid == os.getpid() else sysctl_events
            index = len(reads)
            reads.append(pid)
            spec = variants[index] if index < len(variants) else {}
            b = ctypes.cast(pointer, ctypes.POINTER(inventory._KinfoProc)).contents
            b.pid, b.ppid, b.uid = pid, 7, os.getuid()
            b.start_sec, b.start_usec, b.tty_device = 100, 200, device
            b.status = 2 if pid == os.getpid() else 5
            for key, value in spec.items():
                if key not in ('errno', 'return', 'length'):
                    setattr(b, key, value)
            ctypes.cast(length, ctypes.POINTER(ctypes.c_size_t)).contents.value = spec.get('length', 648)
            ctypes.set_errno(spec.get('errno', 0))
            return spec.get('return', 0)
        def kinfo_lib():
            if sysctl_events is None:
                raise identity.IdentityError('no verified zombie proof in this fixture')
            return types.SimpleNamespace(sysctl=sysctl)
        pin = dict(path='/dev/ttys007', device=device, inode=12)
        original_selector = inventory._terminal_selector
        stats = iter(selector_stats or ())
        pins = iter((pin, final_pin or pin))
        def selector(tty):
            if selector_stats is None:
                return next(pins)
            with patch.object(Path, 'lstat', return_value=types.SimpleNamespace(**next(stats))), \
                    patch.object(Path, 'resolve', return_value=Path('/dev/ttys007')):
                return original_selector(tty)
        with patch.object(inventory, '_lib', return_value=lib), \
                patch.object(inventory, '_kinfo_lib', side_effect=kinfo_lib), \
                patch.object(inventory, '_terminal_selector', side_effect=selector), \
                patch.object(identity, 'process', side_effect=AssertionError('strict reader is separate')):
            return inventory.inventory_for_tty(lambda value: Path(value).name in ('codex', 'claude'), 'ttys007')

    def test_only_exact_tty_members_are_read_and_closed(self):
        self.assertEqual(self.scan(), [['24', '/opt/bin/codex']])
        self.assertEqual(self.listings, [(23, 24)] * 4)
        self.assertEqual(self.enumerations, [3, 4, 3, 3, 4, 3])
        self.assertEqual(set(self.reads), {(pid, read) for pid in (23, 24) for read in range(6)})
        # More than one candidate must survive discovery for the native gate
        # to reject ambiguity; discovery must never return only the first.
        self.assertEqual(self.scan(path=b'/opt/bin/claude'),
                         [['23', '/opt/bin/claude'], ['24', '/opt/bin/codex']])

    def test_foreign_login_parent_is_excluded_by_uid_kernel_membership_before_reads(self):
        # Reading PID 22 would raise in the fixture. It is excluded by an
        # independent kernel UID selector, never by swallowing an EPERM.
        self.assertEqual(self.scan(listings=((22, 23, 24),) * 4,
                                   uid_listings=((23, 24, 999),) * 2),
                         [['24', '/opt/bin/codex']])
        self.assertEqual(self.paths, [23, 24])
        self.assertEqual({pid for pid, _ in self.reads}, {23, 24})

    def test_unrelated_uid_churn_does_not_change_tty_intersection(self):
        self.assertEqual(self.scan(uid_listings=((23, 24, 991), (23, 24, 992, 993))),
                         [['24', '/opt/bin/codex']])
        self.assertEqual({pid for pid, _ in self.reads}, {23, 24})

    def test_uid_migration_in_either_direction_refuses_with_same_complete_tty_set(self):
        for final_owned in ((24,), (22, 23, 24)):
            with self.subTest(final_owned=final_owned), self.assertRaisesRegex(
                    identity.IdentityError, 'UID membership changed'):
                self.scan(listings=((22, 23, 24),) * 4,
                          uid_listings=((23, 24), final_owned))
            self.assertEqual(self.enumerations, [3, 4, 3, 3, 4, 3])

    def test_foreign_member_set_change_refuses_even_when_uid_intersection_is_equal(self):
        for final_members in ((23, 24), (22, 23, 24, 25), (23, 24, 25)):
            with self.subTest(final_members=final_members), self.assertRaisesRegex(
                    identity.IdentityError, 'terminal member set changed'):
                self.scan(listings=((22, 23, 24),) * 2 + (final_members,) * 2)

    def test_tty_change_around_either_uid_sample_refuses_without_resampling(self):
        for listing_index in (1, 3):
            samples = [(22, 23, 24)] * 4
            samples[listing_index] = (23, 24)
            with self.subTest(listing_index=listing_index), self.assertRaisesRegex(
                    identity.IdentityError, 'terminal member set changed'):
                self.scan(listings=samples)
            self.assertEqual(self.enumerations, [3, 4, 3] * (1 if listing_index == 1 else 2))
            if listing_index == 1:
                self.assertEqual(self.reads, [])

    def test_uid_enumeration_failure_is_not_a_foreign_process_exclusion(self):
        # A malformed UID list must fail even when the complete TTY list is valid.
        with self.assertRaisesRegex(identity.IdentityError, 'duplicate'):
            self.scan(uid_listings=((23, 23),))
        self.assertEqual(self.reads, [])
        self.assertEqual(self.enumerations, [3, 4])

    def test_foreign_only_tty_returns_no_candidate_and_still_closes_both_sets(self):
        self.assertEqual(self.scan(listings=((22,),) * 4, uid_listings=((99,),) * 2), [])
        self.assertEqual(self.reads, [])
        self.assertEqual(self.enumerations, [3, 4, 3, 3, 4, 3])

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
                expected_reads = 2 if index == 0 and fault == {'status': 5} else index + 1
                self.assertEqual(sum(pid == 23 for pid, _ in self.reads), expected_reads)
        for index in range(1, 6):
            for fault in ({'ppid': 8}, {'start_sec': 101}, {'start_usec': 201}, {'name': b'changed'}):
                with self.subTest(index=index, fault=fault), self.assertRaisesRegex(identity.IdentityError, 'changed'):
                    self.scan(events=[{}] * index + [fault])

    def test_initial_stable_zombie_is_not_a_live_receiver_even_with_candidate_name(self):
        for name in (b'sleep', b'codex', b'claude', b'', b'x' * 31, b'\xff'):
            with self.subTest(name=name):
                self.assertEqual(self.scan(name=name, events=[{'status': 5}] * 4),
                                 [['24', '/opt/bin/codex']])
                self.assertEqual(self.paths, [24])
                self.assertEqual([n for pid, n in self.reads if pid == 23], list(range(4)))
                self.assertEqual(self.enumerations, [3, 4, 3, 3, 4, 3])

    def test_initial_zombie_pairs_preserve_all_identity_errors_and_close_without_retry(self):
        import errno
        faults = [{'count': 0, 'errno': error} for error in
                  (errno.ESRCH, errno.EPERM, errno.EACCES, errno.EIO, 0)]
        faults += [{'count': ctypes.sizeof(identity.BsdInfo)-1, 'errno': errno.ESRCH},
                   {'pid': 99}, {'uid': os.getuid()+1}, {'start_sec': 0},
                   {'tty_device': 0x12000008}]
        for index in range(4):
            for fault in faults:
                events = [{'status': 5}] * 4
                events[index] = dict(status=5, **fault)
                with self.subTest(index=index, fault=fault), self.assertRaises(identity.IdentityError):
                    self.scan(events=events)
                self.assertEqual(sum(pid == 23 for pid, _ in self.reads), index + 1)
                self.assertNotIn(23, self.paths)
        for index in range(1, 4):
            for drift in ({'ppid': 8}, {'start_sec': 101}, {'start_usec': 201},
                          {'name': b'changed'}, {'status': 1}):
                events = [{'status': 5}] * 4
                events[index] = dict(events[index], **drift)
                with self.subTest(index=index, drift=drift), self.assertRaises(identity.IdentityError):
                    self.scan(events=events)
                self.assertNotIn(23, self.paths)

    def test_initial_zombie_reaping_or_scope_change_is_not_ignored(self):
        for final_members in ((24,), (24, 25), (23, 24, 25)):
            with self.subTest(final_members=final_members), self.assertRaisesRegex(
                    identity.IdentityError, 'terminal member set changed'):
                self.scan(events=[{'status': 5}] * 4,
                          listings=((23, 24),) * 2 + (final_members,) * 2)
        with self.assertRaisesRegex(identity.IdentityError, 'UID membership changed'):
            self.scan(events=[{'status': 5}] * 4, uid_listings=((23, 24), (24,)))

    def test_live_candidate_becoming_zombie_is_never_excluded(self):
        for index in range(1, 6):
            with self.subTest(index=index), self.assertRaises(identity.IdentityError):
                self.scan(name=b'claude', path=b'/opt/bin/claude',
                          events=[{}] * index + [{'status': 5}] * (6-index))

    def test_initial_esrch_requires_four_complete_sysctl_zombies_and_fresh_self_checks(self):
        import errno
        for name in (b'claude', b'codex', b'', b'\xff', b'x' * 31):
            with self.subTest(name=name):
                self.assertEqual(self.scan(name=name, events=[{'count': 0, 'errno': errno.ESRCH}],
                                           sysctl_events=[]), [['24', '/opt/bin/codex']])
                self.assertEqual(self.paths, [24])
                self.assertEqual([n for p, n in self.reads if p == 23], [0])
                self.assertEqual(self.sysctl_reads, [23] * 4)
                self.assertEqual(len(self.self_bsd_reads), 4)
                self.assertEqual(len(self.self_sysctl_reads), 2)
                self.assertEqual(self.enumerations, [3, 4, 3, 3, 4, 3])

    def test_sysctl_zombie_every_read_refuses_missing_partial_errno_identity_and_live(self):
        import errno
        faults = [{'return': -1, 'errno': e} for e in (errno.ESRCH, errno.EPERM, errno.EACCES, 0)]
        faults += [{'length': n} for n in (0, 1, 647, 649)]
        faults += [{'errno': errno.EIO}, {'status': 0}, {'status': 2}, {'status': 6},
                   {'pid': 99}, {'uid': os.getuid()+1}, {'start_sec': 0},
                   {'start_usec': -1}, {'start_usec': 1000000}, {'ppid': 0},
                   {'tty_device': 0x12000008}]
        for index in range(4):
            for fault in faults:
                with self.subTest(index=index, fault=fault), self.assertRaises(identity.IdentityError) as caught:
                    self.scan(events=[{'count': 0, 'errno': errno.ESRCH}],
                              sysctl_events=[{}]*index+[fault])
                self.assertEqual(caught.exception.bsd_absence,
                                 dict(pid=23, stage='before_path_bsd_1', count=0, errno=errno.ESRCH))
                self.assertEqual(len(self.sysctl_reads), index+1)
                self.assertNotIn(23, self.paths)
        for index in (1, 2, 3):
            for drift in ({'ppid': 8}, {'start_sec': 101}, {'start_usec': 201}):
                with self.subTest(index=index, drift=drift), self.assertRaisesRegex(identity.IdentityError, 'changed'):
                    self.scan(events=[{'count': 0, 'errno': errno.ESRCH}],
                              sysctl_events=[{}]*index+[drift])

    def test_sysctl_never_converts_late_bsd_exit_or_error_to_success(self):
        import errno
        for index in range(6):
            for error in (errno.ESRCH, errno.EPERM, errno.EACCES, errno.EIO, 0):
                if index == 0 and error == errno.ESRCH:
                    continue
                with self.subTest(index=index, error=error), self.assertRaises(identity.IdentityError):
                    self.scan(events=[{}]*index+[{'count': 0, 'errno': error}], sysctl_events=[])
                self.assertEqual(self.sysctl_reads, [])

    def test_sysctl_zombie_keeps_complete_scope_closure(self):
        import errno
        for kwargs in (dict(listings=((23,24),)*2+((24,),)*2),
                       dict(uid_listings=((23,24),(24,)))):
            with self.subTest(kwargs=kwargs), self.assertRaises(identity.IdentityError):
                self.scan(events=[{'count': 0, 'errno': errno.ESRCH}], sysctl_events=[], **kwargs)
            self.assertEqual(len(self.sysctl_reads), 4)

    def test_sysctl_layout_self_check_rejects_drift_on_each_pair(self):
        import errno
        for index in range(2):
            for fault in ({'ppid': 8}, {'start_sec': 101}, {'start_usec': 201},
                          {'tty_device': 0xFFFFFFFF}, {'status': 5}, {'status': 0},
                          {'length': 647}, {'pid': 99}, {'uid': os.getuid()+1}):
                with self.subTest(index=index, fault=fault), self.assertRaises(identity.IdentityError):
                    self.scan(events=[{'count': 0, 'errno': errno.ESRCH}], sysctl_events=[],
                              self_sysctl_events=[{}]*index+[fault])
                self.assertEqual(len(self.sysctl_reads), index*2)
        for index in range(4):
            with self.subTest(index=index), self.assertRaises(identity.IdentityError):
                self.scan(events=[{'count': 0, 'errno': errno.ESRCH}], sysctl_events=[],
                          self_bsd_events=[{}]*index+[{'count': 0, 'errno': errno.ESRCH}])

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

    def test_terminal_activity_timestamps_do_not_refuse_stable_device_identity(self):
        info = dict(st_mode=stat.S_IFCHR | 0o620, st_rdev=0x12000007,
                    st_dev=1, st_ino=2, st_uid=os.getuid(), st_gid=os.getgid(),
                    st_atime_ns=100, st_mtime_ns=200, st_ctime_ns=300)
        # Measured devfs behavior: slave output changes mtime and ctime; reads
        # can change atime. The complete UID/TTY/BSD checks must still run.
        after = dict(info, st_atime_ns=101, st_mtime_ns=201, st_ctime_ns=301)
        self.assertEqual(self.scan(selector_stats=(info, after)), [['24', '/opt/bin/codex']])
        self.assertEqual(self.enumerations, [3, 4, 3, 3, 4, 3])
        self.assertEqual(set(self.reads), {(pid, n) for pid in (23, 24) for n in range(6)})

    def test_device_identity_and_access_metadata_changes_still_refuse(self):
        info = dict(st_mode=stat.S_IFCHR | 0o620, st_rdev=0x12000007,
                    st_dev=1, st_ino=2, st_uid=os.getuid(), st_gid=os.getgid(),
                    st_ctime_ns=300)
        changes = ({'st_rdev': 0x12000008}, {'st_dev': 2}, {'st_ino': 3},
                   {'st_mode': stat.S_IFCHR | 0o600}, {'st_uid': os.getuid()+1},
                   {'st_gid': os.getgid()+1})
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaisesRegex(
                    identity.IdentityError, 'device changed'):
                self.scan(selector_stats=(info, dict(info, **changed)))
            self.assertEqual(self.enumerations, [3, 4, 3, 3, 4, 3])

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
