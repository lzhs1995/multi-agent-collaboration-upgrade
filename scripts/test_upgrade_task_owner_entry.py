"""Real entry fork/waitpid tests with a tiny, explicitly synthetic owner.

The entry is copied byte-for-byte. Only upgrade_task_owner is replaced in a
temporary directory. No Codex, Claude, harness, permit, release or model runs.
These tests do not establish real TaskOwner startup or business acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ENTRY = Path(__file__).with_name('upgrade_task_owner_entry.py')
SENTINEL = b'entry-test-only-credential-71f20ac9'
FIXTURE_OWNER = r'''
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

def publish(root, name, value):
    temporary = root / (name + '.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.rename(root / name)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture-root', required=True)
    parser.add_argument('--credential-fd', type=int)
    parser.add_argument('--hold', action='store_true')
    parser.add_argument('--exit-code', type=int, default=0)
    args = parser.parse_args(argv)
    root = Path(args.fixture_root)
    initial_parent = os.getppid()
    with (root / 'invocations.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'pid': os.getpid(), 'ppid': initial_parent}) + '\n')
    credential = None
    if args.credential_fd is not None:
        try:
            credential = os.read(args.credential_fd, 4096)
        finally:
            os.close(args.credential_fd)
    publish(root, 'ready.json', {
        'pid': os.getpid(), 'ppid': initial_parent,
        'argv': sys.argv, 'environment': dict(os.environ),
        'credential_bytes': len(credential) if credential is not None else None,
        'credential_sha256': hashlib.sha256(credential).hexdigest() if credential is not None else None,
        'credential_read_closed': args.credential_fd is not None,
    })
    credential = None
    deadline = time.monotonic() + 12
    while args.hold and not (root / 'STOP').exists():
        if time.monotonic() >= deadline:
            return 91
        if (root / 'observe-request').exists() and not (root / 'observed.json').exists():
            publish(root, 'observed.json', {'pid': os.getpid(), 'ppid': os.getppid()})
        time.sleep(.01)
    publish(root, 'closed.json', {'pid': os.getpid(), 'ppid': os.getppid(), 'exit_code': args.exit_code})
    return args.exit_code
'''

SHORT_CALLER = r'''
import json
import os
from pathlib import Path
import subprocess
import sys
import time
root = Path(sys.argv[1])
with (root / 'stdout.log').open('wb') as out, (root / 'stderr.log').open('wb') as err:
    reaper = subprocess.Popen([sys.executable, '-I', '-B', str(root / 'upgrade_task_owner_entry.py'),
        '--fixture-root', str(root), '--hold'], stdin=subprocess.DEVNULL, stdout=out, stderr=err,
        close_fds=True, start_new_session=True, env=dict(os.environ))
deadline = time.monotonic() + 8
while not (root / 'ready.json').exists():
    if reaper.poll() is not None or time.monotonic() >= deadline:
        raise RuntimeError('fixture entry failed to become ready')
    time.sleep(.01)
(root / 'caller.json').write_text(json.dumps({'pid': os.getpid(), 'reaper_pid': reaper.pid}), encoding='utf-8')
# Exit normally while the entry's waitpid parent and its single owner survive.
'''


@unittest.skipUnless(hasattr(os, 'fork') and hasattr(os, 'waitstatus_to_exitcode'),
                     'requires POSIX fork and waitpid')
class OwnerEntryProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='owner-entry-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        original = ENTRY.read_bytes()
        self.entry = self.root / ENTRY.name
        self.entry.write_bytes(original)
        self.assertEqual(self.entry.read_bytes(), original)
        (self.root / 'upgrade_task_owner.py').write_text(FIXTURE_OWNER, encoding='utf-8')
        self.env = {'PATH': os.defpath, 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}

    def await_file(self, name, seconds=10):
        path = self.root / name
        deadline = time.monotonic() + seconds
        while not path.exists():
            if time.monotonic() >= deadline:
                self.fail('timed out waiting for fixture ' + name)
            time.sleep(.01)
        return json.loads(path.read_text(encoding='utf-8'))

    @staticmethod
    def exists(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def stop(self, process=None):
        (self.root / 'STOP').touch()
        if process is not None:
            # No signal or restart. The fixture also has its own 12 s ceiling.
            process.wait(timeout=20)
        elif (self.root / 'ready.json').exists():
            self.await_file('closed.json', seconds=20)

    def start(self, *args, pass_fds=()):
        out = (self.root / 'stdout.log').open('wb')
        err = (self.root / 'stderr.log').open('wb')
        try:
            process = subprocess.Popen([sys.executable, '-I', '-B', str(self.entry),
                '--fixture-root', str(self.root), *args], stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, close_fds=True, pass_fds=pass_fds,
                start_new_session=True, env=self.env)
        finally:
            out.close()
            err.close()
        self.addCleanup(self.stop, process)
        return process

    def assert_one_owner(self):
        rows = (self.root / 'invocations.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows), 1)
        return json.loads(rows[0])

    def test_short_lived_caller_exit_keeps_original_owner_parent(self):
        self.addCleanup(self.stop)
        caller = subprocess.run([sys.executable, '-I', '-B', '-c', SHORT_CALLER, str(self.root)],
            capture_output=True, text=True, timeout=10, close_fds=True, env=self.env)
        self.assertEqual(caller.returncode, 0, caller.stderr)
        caller_row = self.await_file('caller.json')
        ready = self.await_file('ready.json')
        self.assertFalse(self.exists(caller_row['pid']))
        self.assertTrue(self.exists(caller_row['reaper_pid']))
        self.assertNotEqual(ready['pid'], caller_row['reaper_pid'])
        self.assertEqual(ready['ppid'], caller_row['reaper_pid'])
        (self.root / 'observe-request').touch()
        observed = self.await_file('observed.json')
        self.assertEqual(observed, {'pid': ready['pid'], 'ppid': ready['ppid']})
        self.assertEqual(self.assert_one_owner(), observed)
        self.stop()
        self.assertEqual(self.await_file('closed.json')['exit_code'], 0)
        deadline = time.monotonic() + 10
        while self.exists(caller_row['reaper_pid']) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.exists(caller_row['reaper_pid']))
        self.assertFalse(self.exists(ready['pid']))

    def test_only_child_reads_pipe_and_parent_closes_reader_without_leaking_sentinel(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, SENTINEL)
            process = self.start('--hold', '--credential-fd', str(read_fd), pass_fds=(read_fd,))
            os.close(read_fd)
            read_fd = None
            ready = self.await_file('ready.json')
            self.assertEqual(ready['ppid'], process.pid)
            self.assertEqual(ready['credential_bytes'], len(SENTINEL))
            self.assertEqual(ready['credential_sha256'], hashlib.sha256(SENTINEL).hexdigest())
            self.assertTrue(ready['credential_read_closed'])
            self.assertIsNone(process.poll())
            self.assertTrue(self.exists(ready['pid']))
            # Child has closed its reader but is still alive. EPIPE proves the
            # waiting entry parent did not retain another credential reader.
            with self.assertRaises(BrokenPipeError):
                os.write(write_fd, b'reader-closure-check')
            self.assertFalse(SENTINEL.decode() in json.dumps(ready['argv']))
            self.assertFalse(SENTINEL.decode() in json.dumps(ready['environment']))
            self.assert_one_owner()
            self.stop(process)
            self.assertEqual(process.returncode, 0)
            for name in ('stdout.log', 'stderr.log'):
                self.assertFalse(SENTINEL in (self.root / name).read_bytes())
        finally:
            if read_fd is not None:
                os.close(read_fd)
            os.close(write_fd)

    def test_waitpid_propagates_exact_child_failure_and_reaps_it(self):
        process = self.start('--exit-code', '37')
        ready = self.await_file('ready.json')
        self.assertEqual(ready['ppid'], process.pid)
        self.assertEqual(process.wait(timeout=10), 37)
        self.assertEqual(self.await_file('closed.json')['exit_code'], 37)
        self.assert_one_owner()
        self.assertFalse(self.exists(ready['pid']))
        self.assertEqual((self.root / 'stdout.log').read_bytes(), b'')
        self.assertEqual((self.root / 'stderr.log').read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
