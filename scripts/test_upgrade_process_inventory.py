"""Fail-closed inventory and the real materialized Darwin execution boundary."""
import ctypes
import os
from pathlib import Path
import subprocess
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

    def test_only_initial_absence_can_be_ignored(self):
        lib = types.SimpleNamespace(proc_pidpath=lambda *a: 0, proc_pidinfo=lambda *a: 0)
        with patch.object(inventory, "_lib", return_value=lib), \
                patch.object(inventory, "_pids", return_value=[23]):
            with patch.object(identity, "process", side_effect=identity.ProcessExited("absent")):
                self.assertEqual(inventory.inventory(lambda name: True), [])
            with patch.object(identity, "process", side_effect=identity.IdentityError("EPERM")):
                with self.assertRaisesRegex(identity.IdentityError, "EPERM"):
                    inventory.inventory(lambda name: True)
            with patch.object(identity, "process", return_value={"pid": 23}):
                with self.assertRaisesRegex(identity.IdentityError, "inaccessible"):
                    inventory.inventory(lambda name: True)

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

    def test_missing_executable_excludes_only_stable_unrelated_bsd_name(self):
        expected = dict(pid=23, birth=[100, 200])
        name = b'unrelated'
        def read(pid, kind, arg, buf, size):
            b = ctypes.cast(buf, ctypes.POINTER(identity.BsdInfo)).contents
            b.pid, b.uid, b.start_sec, b.start_usec = 23, os.getuid(), 100, 200
            b.name = name
            return size
        lib = types.SimpleNamespace(proc_pidpath=lambda *a: 0, proc_pidinfo=read)
        with patch.object(inventory, "_lib", return_value=lib), \
                patch.object(inventory, "_pids", return_value=[23]), \
                patch.object(identity, "process", return_value=expected):
            candidate = lambda path: Path(path).name == 'codex'
            self.assertEqual(inventory.inventory(candidate), [])
            for name in (b'codex', b'', b'x' * 31):
                with self.assertRaises(identity.IdentityError):
                    inventory.inventory(candidate)
            name = b'unrelated'
            with patch.object(identity, "process", side_effect=[expected, dict(expected, birth=[2, 3])]):
                with self.assertRaisesRegex(identity.IdentityError, "changed"):
                    inventory.inventory(candidate)


if __name__ == "__main__":
    unittest.main()
