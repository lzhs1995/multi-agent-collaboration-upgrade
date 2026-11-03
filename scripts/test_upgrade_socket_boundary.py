"""Exact native alias and real kernel isolation tests; no model startup."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import upgrade_socket_boundary as boundary
from orchestration_common import ContractError, checked_path
from upgrade_client_isolation import sandbox_profile


class SocketBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.shared, self.private = self.root / 's', self.root / 'p'
        self.shared.mkdir(mode=0o700)
        self.private.mkdir(mode=0o700)
        self.patch = patch.object(boundary, 'shared_directory', return_value=self.shared)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.alias = self.private / 'c.sock'
        self.pin = dict(sha256=boundary.CODEX_SHA256, bytes=boundary.CODEX_BYTES)
        self.plan = boundary.boundary_plan(self.alias, self.pin)

    def endpoint(self):
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(self.plan['physical'])
        Path(self.plan['physical']).chmod(0o600)
        Path(self.plan['lock']).touch(mode=0o600)
        self.alias.symlink_to(self.plan['physical'])
        return server

    def test_native_alias_works_without_relaxing_generic_symlink_check(self):
        self.endpoint()
        self.assertEqual(boundary.validate_boundary(self.plan, self.alias, live=True), self.alias)
        with self.assertRaises(ContractError):
            checked_path(self.alias)
        with self.assertRaisesRegex(ContractError, 'ALREADY_EXISTS'):
            boundary.boundary_plan(self.alias, self.pin)

    def test_missing_endpoints_and_changed_plan_or_binary_refuse(self):
        with self.assertRaisesRegex(ContractError, 'NOT_READY'):
            boundary.validate_boundary(self.plan, self.alias, live=True)
        with self.assertRaisesRegex(ContractError, 'NOT_READY'):
            boundary.verify_kernel(os.getpid(), self.plan, writer=True)
        for key in self.plan:
            bad = copy.deepcopy(self.plan)
            bad[key] = None
            with self.subTest(key=key), self.assertRaises(ContractError):
                boundary.validate_boundary(bad, self.alias)
        for pin in ({}, dict(self.pin, sha256='0' * 64), dict(self.pin, bytes=1)):
            with self.assertRaisesRegex(ContractError, 'LAYOUT_UNVERIFIED'):
                boundary.boundary_plan(self.alias, pin)

    def test_wrong_alias_regular_socket_symlink_lock_hardlink_and_mode_refuse(self):
        self.endpoint()
        self.alias.unlink()
        self.alias.symlink_to(self.plan['lock'])
        with self.assertRaisesRegex(ContractError, 'TARGET_CHANGED'):
            boundary.validate_boundary(self.plan, self.alias)
        self.alias.unlink()
        self.alias.symlink_to(self.plan['physical'])
        physical, lock = map(Path, (self.plan['physical'], self.plan['lock']))
        physical.chmod(0o666)
        with self.assertRaisesRegex(ContractError, 'TYPE_OWNER_OR_MODE'):
            boundary.validate_boundary(self.plan, self.alias)
        physical.chmod(0o600)
        os.link(lock, self.private / 'lock-alias')
        with self.assertRaisesRegex(ContractError, 'TYPE_OWNER_OR_MODE'):
            boundary.validate_boundary(self.plan, self.alias)
        (self.private / 'lock-alias').unlink()
        lock.unlink()
        lock.symlink_to(self.private / 'elsewhere')
        with self.assertRaisesRegex(ContractError, 'TYPE_OWNER_OR_MODE'):
            boundary.validate_boundary(self.plan, self.alias)
        lock.unlink()
        lock.touch(mode=0o600)
        physical.unlink()
        physical.write_text('not a socket')
        with self.assertRaisesRegex(ContractError, 'TYPE_OWNER_OR_MODE'):
            boundary.validate_boundary(self.plan, self.alias)

    def test_shared_directory_replacement_and_symlink_parent_refuse(self):
        self.shared.chmod(0o755)
        with self.assertRaisesRegex(ContractError, 'DIRECTORY_OWNER_OR_MODE'):
            boundary.validate_boundary(self.plan, self.alias)
        self.shared.chmod(0o700)
        self.shared.rename(self.root / 'old')
        self.shared.mkdir(mode=0o700)
        with self.assertRaisesRegex(ContractError, 'BOUNDARY_CHANGED'):
            boundary.validate_boundary(self.plan, self.alias)
        alias_parent = self.root / 'alias'
        alias_parent.symlink_to(self.private)
        with self.assertRaises(ContractError):
            boundary.boundary_plan(alias_parent / 'c.sock', self.pin)

    def test_undeclared_or_mismatched_policy_cannot_expand_private_scope(self):
        binding = dict(native_policy='private-cli-roots-v1', tmp_root=str(self.private),
                       codex_control_socket=str(self.alias))
        self.assertIsNone(boundary.binding_boundary(binding))
        with self.assertRaisesRegex(ContractError, 'UNDECLARED_EXCEPTION'):
            boundary.binding_boundary(dict(binding, socket_boundary=self.plan))
        binding['native_policy'] = boundary.SOCKET_POLICY
        with self.assertRaisesRegex(ContractError, 'BOUNDARY_REQUIRED'):
            boundary.binding_boundary(binding)
        binding['socket_boundary'] = self.plan
        self.assertEqual(boundary.binding_boundary(binding), self.plan)
        with self.assertRaisesRegex(ContractError, 'BINDING_CHANGED'):
            boundary.binding_boundary(dict(binding, codex_control_socket='/wrong'))

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Darwin Seatbelt')
    def test_kernel_exact_writes_and_nonwriter_deny_all_external_endpoints(self):
        self.endpoint()
        module_path = str(Path(boundary.__file__).resolve())
        for writer in (False, True):
            profile = self.root / ('writer.sb' if writer else 'reader.sb')
            raw = sandbox_profile([self.private])
            if writer:
                raw = boundary.extend_profile(raw, self.plan)
            profile.write_bytes(raw)
            code = f'''
import importlib.util, os, sys
from pathlib import Path
sys.path.insert(0, {str(Path(sandbox_profile.__code__.co_filename).resolve().parent)!r})
spec = importlib.util.spec_from_file_location('boundary', {module_path!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.shared_directory = lambda: Path({str(self.shared)!r})
print(m.verify_kernel(os.getpid(), {self.plan!r}, writer={writer!r}))
'''
            result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile),
                sys.executable, '-I', '-B', '-c', code], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("'kernel_checked': True", result.stdout)

    @unittest.skipUnless(sys.platform == 'darwin', 'requires Darwin Seatbelt')
    def test_real_socket_lifecycle_and_descendant_negative_controls(self):
        sentinel = self.shared / 'sentinel'
        sentinel.write_text('unchanged')
        (self.private / 'outside-link').symlink_to(sentinel)
        profile = self.root / 'lifecycle.sb'
        profile.write_bytes(boundary.extend_profile(sandbox_profile([self.private]), self.plan))
        code = r'''
import fcntl, json, os, socket, subprocess, sys
from pathlib import Path
private, shared, physical, lock, sentinel = map(Path, sys.argv[1:])
denials = []
def denied(name, operation):
    try:
        operation()
    except PermissionError:
        denials.append(name)
    else:
        raise AssertionError('unexpected permission: ' + name)
fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'r+b') as held:
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(physical))
        physical.chmod(0o600)
        server.listen(1)
        (private / 'c.sock').symlink_to(physical)
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(private / 'c.sock'))
            peer, _ = server.accept()
            with peer:
                client.sendall(b'fixture')
                assert peer.recv(16) == b'fixture'
        denied('unrelated-write', lambda: sentinel.write_text('bad'))
        denied('unrelated-unlink', sentinel.unlink)
        denied('unrelated-rename', lambda: sentinel.rename(private / 'stolen'))
        denied('neighbor-create', lambda: (shared / 'neighbor').write_text('bad'))
        denied('neighbor-lock', lambda: (shared / 'neighbor.lock').write_text('bad'))
        denied('parent-chmod', lambda: shared.chmod(0o777))
        denied('parent-rename', lambda: shared.rename(private / 'moved'))
        denied('symlink-write', lambda: (private / 'outside-link').write_text('bad'))
        with socket.socket(socket.AF_UNIX) as other:
            denied('neighbor-bind', lambda: other.bind(str(shared / 'neighbor.sock')))
        child = subprocess.run([sys.executable, '-I', '-B', '-c',
            'from pathlib import Path; Path(' + repr(str(sentinel)) + ').write_text("bad")'],
            capture_output=True, text=True, timeout=10)
        assert child.returncode != 0 and 'PermissionError' in child.stderr
        denials.append('descendant-write')
    physical.unlink()
lock.unlink()
print(json.dumps(denials))
'''
        result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile),
            sys.executable, '-I', '-B', '-c', code, str(self.private), str(self.shared),
            self.plan['physical'], self.plan['lock'], str(sentinel)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 10)
        self.assertEqual(sentinel.read_text(), 'unchanged')
        self.assertFalse(Path(self.plan['physical']).exists())
        self.assertFalse(Path(self.plan['lock']).exists())


if __name__ == '__main__':
    unittest.main()
