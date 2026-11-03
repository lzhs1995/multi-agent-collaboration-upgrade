"""Real OS lock contention, with no model/transport input."""
import concurrent.futures
import fcntl
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import NonCallableMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchestration_common import ContractError, exclusive_lock, write_json
from orchestration_coordinator import LeaseRegistry


class LockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'registry.lock'

    def test_default_action_lock_still_refuses_immediately(self):
        with exclusive_lock(self.path):
            started = time.monotonic()
            with self.assertRaisesRegex(ContractError, 'already active'):
                with exclusive_lock(self.path):
                    self.fail('reentered active action')
            self.assertLess(time.monotonic() - started, 1)

    def test_real_held_lock_deadline_does_not_enter_body(self):
        with exclusive_lock(self.path):
            started = time.monotonic()
            with self.assertRaisesRegex(ContractError, 'timed out'):
                with exclusive_lock(self.path, wait_seconds=0.1):
                    self.fail('entered without ownership')
            self.assertGreaterEqual(time.monotonic() - started, 0.09)
            self.assertLess(time.monotonic() - started, 1)

    def wait_then_release(self, operation, mutation=None):
        """Signal only after a real flock contention; never fake lock outcomes."""
        held = exclusive_lock(self.path)
        held.__enter__()
        contended = threading.Event()
        original = fcntl.flock

        def observe(fd, flags):
            try:
                return original(fd, flags)
            except BlockingIOError:
                contended.set()
                raise

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            # Observe the real OS call directly. MagicMock call recording takes
            # a process-wide RLock before observe can signal the main thread.
            with patch('orchestration_common.fcntl.flock', new=observe):
                future = pool.submit(operation)
                try:
                    self.assertTrue(contended.wait(3), 'worker did not contend')
                    self.assertFalse(future.done())
                    if mutation:
                        mutation()
                finally:
                    held.__exit__(None, None, None)
                return future.result(timeout=6)

    def enter_once(self):
        with exclusive_lock(self.path, wait_seconds=5):
            return 'entered'

    def test_eventual_acquisition_runs_body_once(self):
        self.assertEqual(self.wait_then_release(self.enter_once), 'entered')

    def test_observer_does_not_wait_for_mock_recording_lock(self):
        with NonCallableMock._lock:
            self.assertEqual(self.wait_then_release(self.enter_once), 'entered')

    def test_replaced_or_deleted_path_cannot_admit_old_inode(self):
        for replacement in ('file', 'missing', 'symlink'):
            with self.subTest(replacement=replacement):
                if self.path.is_symlink():
                    self.path.unlink()
                def mutate():
                    self.path.unlink()
                    if replacement == 'file':
                        self.path.write_text('new inode')
                    elif replacement == 'symlink':
                        target = self.root / 'target'
                        target.write_text('target')
                        self.path.symlink_to(target)
                with self.assertRaisesRegex(ContractError, 'lock path changed'):
                    self.wait_then_release(self.enter_once, mutate)

    def test_invalid_or_unbounded_wait_refuses_before_creating_lock(self):
        for value in (-1, 5.01, True, None, '1', float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ContractError):
                with exclusive_lock(self.path, wait_seconds=value):
                    self.fail('invalid wait accepted')
            self.assertFalse(self.path.exists())

    def test_registry_waits_then_validates_reserve_and_release(self):
        registry = LeaseRegistry(self.root)
        claim = write_json(self.root / 'claim.json', {'task_id': 'owner'})
        scope = str(self.root / 'output')
        self.wait_then_release(lambda: registry.reserve('owner', 'session', scope, claim))
        reservation = registry.read()['owner']
        terminal = write_json(self.root / 'terminal.json', dict(
            state='ACCEPTED', data={'verification': {'task_id': 'owner'}}))
        self.wait_then_release(lambda: registry.release('owner', session='session',
            scope=scope, claim=claim, terminal=terminal,
            reserved_at=reservation['reserved_at']))
        self.assertEqual(registry.read()['owner']['released_by'], terminal)

    def test_registry_timeout_preserves_lease_bytes(self):
        registry = LeaseRegistry(self.root)
        claim = write_json(self.root / 'claim.json', {'task_id': 'owner'})
        scope = str(self.root / 'output')
        registry.reserve('owner', 'session', scope, claim)
        original = (self.root / 'leases.json').read_bytes()
        terminal = write_json(self.root / 'terminal.json', dict(
            state='ACCEPTED', data={'verification': {'task_id': 'owner'}}))
        reserved_at = registry.read()['owner']['reserved_at']
        with exclusive_lock(self.path):
            with self.assertRaisesRegex(ContractError, 'timed out'):
                registry.release('owner', session='session', scope=scope, claim=claim,
                    terminal=terminal, reserved_at=reserved_at)
            self.assertEqual((self.root / 'leases.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
