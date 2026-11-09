"""A transient kernel observation must not replace or rebind the original server."""
from pathlib import Path
import socket
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cmux_daemon_identity as identity
from orchestration_common import ContractError, write_json
from upgrade_task_tui import wait_original_server

KERNEL_PROCESS = identity.process


class OriginalServerWaitTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='task-wait-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        startup = self.root / 'startup'
        startup.mkdir()
        self.ref = write_json(self.root / 'spec.json', {'fixture': True})
        self.socket = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.socket.close)
        endpoint = self.root / 'c.sock'
        self.socket.bind(str(endpoint))
        self.spec = dict(stop_path=str(self.root / 'STOP'),
                        pins={'codex': {'path': '/fixture/codex'}},
                        rpc={'argv': ['/fixture/codex', 'app-server'], 'path': str(endpoint)})
        self.binding = {'state_root': str(self.root)}
        self.live = dict(pid=123, birth=[12, 34], executable='/fixture/codex',
                         argv=self.spec['rpc']['argv'])
        self.launched = dict(startup=self.ref, pid=123, birth=[12, 34])
        self.launch_path = startup / 'codex-server.json'
        write_json(self.launch_path, self.launched)
        self.result = dict(schema='upgrade-server-result-v2', startup=self.ref,
                           status='SOCKET_CREATED', pid=123)
        write_json(startup / 'codex-server-result.json', self.result)
        self.spawn_patch = patch('upgrade_task_tui.subprocess.Popen')
        self.spawn = self.spawn_patch.start()
        self.addCleanup(self.spawn_patch.stop)

    def wait(self, **kwargs):
        return wait_original_server(self.ref, self.spec, self.binding, **kwargs)

    def test_transient_read_rechecks_the_same_pid_without_starting_a_server(self):
        error = identity.IdentityError('process changed during read')
        with patch.object(identity, 'process', side_effect=[error, self.live]) as read:
            self.assertEqual(self.wait(), self.result)
        self.assertEqual([c.args for c in read.call_args_list], [(123,), (123,)])
        self.spawn.assert_not_called()

    def test_repeated_transient_read_expires_with_original_budget(self):
        error = identity.IdentityError('process changed during read')
        with patch.object(identity, 'process', side_effect=error), \
                patch('upgrade_task_tui.time.sleep'), \
                patch('upgrade_task_tui.time.monotonic', side_effect=[0, .1, .2, 1]):
            with self.assertRaisesRegex(ContractError, 'WAIT_TIMEOUT_NO_REPLAY'):
                self.wait(timeout=1)
        self.spawn.assert_not_called()

    def test_other_identity_errors_and_exit_are_not_hidden(self):
        for error in (identity.IdentityError('process arguments inaccessible'),
                      identity.ProcessExited('process absent at initial kernel read')):
            with self.subTest(error=str(error)), patch.object(identity, 'process', side_effect=error) as read:
                with self.assertRaises(type(error)):
                    self.wait()
                self.assertEqual(read.call_count, 1)

    def test_stop_during_uncertain_read_prevents_another_probe(self):
        def uncertain(_):
            Path(self.spec['stop_path']).touch()
            raise identity.IdentityError('process changed during read')
        with patch.object(identity, 'process', side_effect=uncertain) as read:
            with self.assertRaisesRegex(ContractError, 'STOP_REQUESTED'):
                self.wait()
        self.assertEqual(read.call_count, 1)

    def test_changed_original_launch_is_not_adopted_after_uncertain_read(self):
        def uncertain(_):
            write_json(self.launch_path, dict(self.launched, pid=456))
            raise identity.IdentityError('process changed during read')
        with patch.object(identity, 'process', side_effect=uncertain) as read:
            with self.assertRaisesRegex(ContractError, 'ORIGINAL_RECORD_CHANGED'):
                self.wait()
        self.assertEqual(read.call_count, 1)

    def test_stable_but_wrong_process_is_rejected_after_uncertain_read(self):
        for key, value in (('birth', [99, 1]), ('executable', '/foreign/codex'),
                           ('argv', ['/fixture/codex', 'other'])):
            wrong = dict(self.live, **{key: value})
            with self.subTest(key=key), patch.object(identity, 'process', side_effect=[
                    identity.IdentityError('process changed during read'), wrong]):
                with self.assertRaisesRegex(ContractError, 'ORIGINAL_PROCESS_CHANGED'):
                    self.wait()

    def test_successful_retry_cannot_cross_the_original_deadline(self):
        clock = [0]
        reads = []
        def observe(_):
            reads.append(True)
            if len(reads) == 1:
                raise identity.IdentityError('process changed during read')
            clock[0] = 1
            return self.live
        with patch.object(identity, 'process', side_effect=observe), \
                patch('upgrade_task_tui.time.monotonic', side_effect=lambda: clock[0]), \
                patch('upgrade_task_tui.time.sleep'):
            with self.assertRaisesRegex(ContractError, 'WAIT_TIMEOUT_NO_REPLAY'):
                self.wait(timeout=1)
        self.assertEqual(len(reads), 2)

    def test_stop_arriving_during_successful_retry_prevents_readiness(self):
        reads = []
        def observe(_):
            reads.append(True)
            if len(reads) == 1:
                raise identity.IdentityError('process changed during read')
            Path(self.spec['stop_path']).touch()
            return self.live
        with patch.object(identity, 'process', side_effect=observe):
            with self.assertRaisesRegex(ContractError, 'STOP_REQUESTED'):
                self.wait()
        self.assertEqual(len(reads), 2)

    def test_record_replacement_during_successful_read_prevents_readiness(self):
        def observe(_):
            write_json(self.launch_path, dict(self.launched, pid=456))
            return self.live
        with patch.object(identity, 'process', side_effect=observe):
            with self.assertRaisesRegex(ContractError, 'ORIGINAL_RECORD_CHANGED'):
                self.wait()

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin native process observation')
    def test_real_parent_exit_between_kernel_reads_retains_original_process(self):
        # The original identity reader is used unchanged. The fixture releases
        # a real parent between its kernel snapshots; this is not an agent or
        # a replacement app-server, and only this fixture process is cleaned up.
        self.spawn_patch.stop()
        native_reader = patch.object(identity, 'process', KERNEL_PROCESS)
        native_reader.start()
        self.addCleanup(native_reader.stop)
        stop = self.root / 'fixture-stop'
        ready = self.root / 'fixture-ready'
        child_code = ('import pathlib,time; p=pathlib.Path(' + repr(str(stop)) + '); '
                      'pathlib.Path(' + repr(str(ready)) + ').touch(); '
                      'end=time.monotonic()+30; '
                      'exec("while not p.exists() and time.monotonic()<end: time.sleep(.01)")')
        parent_code = ('import subprocess,sys; p=subprocess.Popen([sys.executable,"-c",'
                       + repr(child_code) + '],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,'
                       'stderr=subprocess.DEVNULL); print(p.pid,flush=True); sys.stdin.readline()')
        parent = subprocess.Popen([sys.executable, '-c', parent_code], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(parent.stderr.close)
        self.addCleanup(parent.stdout.close)
        self.addCleanup(parent.stdin.close)
        self.addCleanup(lambda: parent.wait(timeout=5))
        self.addCleanup(lambda: parent.terminate() if parent.poll() is None else None)
        pid = int(parent.stdout.readline())
        def stop_fixture():
            stop.touch()
            end = time.monotonic() + 5
            while time.monotonic() < end:
                try:
                    KERNEL_PROCESS(pid, arguments=False)
                except identity.ProcessExited:
                    return
                except identity.IdentityError:
                    # Exit can race the second kernel sample. Confirm absence
                    # on a subsequent read; no signal or replacement is used.
                    pass
                time.sleep(.01)
            self.fail('fixture child did not exit after its stop file')
        self.addCleanup(stop_fixture)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(ready.exists())
        before = identity.process(pid)
        self.assertEqual(before['ppid'], parent.pid)
        self.spec['pins']['codex']['path'] = before['executable']
        self.spec['rpc']['argv'] = before['argv']
        self.launched.update(pid=pid, birth=before['birth'])
        write_json(self.launch_path, self.launched)
        self.result['pid'] = pid
        write_json(self.root / 'startup/codex-server-result.json', self.result)
        original_args = identity._args
        calls = []
        def release_parent(raw):
            args = original_args(raw)
            if not calls:
                parent.stdin.write('\n'); parent.stdin.flush(); parent.wait(timeout=5)
            calls.append(True)
            return args
        with patch.object(identity, '_args', side_effect=release_parent):
            self.assertEqual(self.wait(timeout=5), self.result)
        self.assertGreaterEqual(len(calls), 2)
        after = identity.process(pid)
        self.assertEqual(after['birth'], before['birth'])
        self.assertEqual(after['argv'], before['argv'])
        self.assertEqual(after['ppid'], 1)


if __name__ == '__main__':
    unittest.main()
