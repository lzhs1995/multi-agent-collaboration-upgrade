"""Frozen exact-socket startup wiring; never executes Codex or creates peers."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref
from upgrade_experiment import init_experiment
from upgrade_live_materialize import materialize_live
from upgrade_startup_contract import validate_startup
import upgrade_socket_boundary as boundary
import upgrade_live_entry as entry

SOURCE = Path.home() / '.local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source'
WRAPPERS = Path('/Applications/cmux.app/Contents/Resources/bin')
BINARY = Path('/Users/lzhs/Documents/cnm/tasks/06_Mintransition/verification/'
    'ccc-collaboration-efficiency-20261004-01/post-submit10/native-package194/package/bin/codex')


@unittest.skipUnless(sys.platform == 'darwin' and SOURCE.is_dir() and BINARY.is_file(),
                     'requires fixed local artifacts; executes no native client')
class SocketStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='', dir='/tmp')
        cls.root = Path(cls.temp.name).resolve()
        cls.shared = cls.root / 's'
        cls.shared.mkdir(mode=0o700)
        cls.shared_patch = patch.object(boundary, 'shared_directory', return_value=cls.shared)
        cls.shared_patch.start()
        cls.addClassCleanup(cls.shared_patch.stop)
        cls.addClassCleanup(cls.temp.cleanup)
        context = init_experiment(cls.root / 'l', 'exact-socket-startup', SOURCE)
        cls.binaries = {k: Path('/usr/bin/true') for k in ('claude', 'cmux', 'rtk', 'node')}
        cls.binaries.update(codex=BINARY, **{p + '_wrapper': WRAPPERS / ('cmux-' + p + '-wrapper')
                                           for p in ('codex', 'claude')})
        runtime = materialize_live(context, 'B', binaries=cls.binaries,
            provider_configuration=({}, {}), native_policy=boundary.SOCKET_POLICY)
        cls.ref = json_ref(runtime)['startup']
        cls.spec, cls.binding = validate_startup(cls.ref)

    def endpoint(self):
        plan = self.spec['socket_boundary']
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        physical, lock, alias = (Path(plan[k]) for k in ('physical', 'lock', 'advertised'))
        server.bind(str(physical))
        physical.chmod(0o600)
        lock.touch(mode=0o600)
        alias.symlink_to(physical)
        for path in (physical, lock, alias):
            self.addCleanup(path.unlink, missing_ok=True)
        return plan

    def test_occupied_endpoint_refuses_before_workspace_or_server_intent(self):
        plan = self.endpoint()
        self.assertEqual(validate_startup(self.ref)[0]['socket_boundary'], plan)
        with patch.object(entry.subprocess, 'run') as create:
            with self.assertRaisesRegex(ContractError, 'ALREADY_EXISTS'):
                entry.launch(self.ref, self.spec, self.binding, spawn=True, spawn_authorized=True)
            create.assert_not_called()
        self.assertFalse((Path(self.binding['state_root']) / 'startup/intent.json').exists())

    def test_rehashed_broader_profile_still_refuses(self):
        path = Path(self.spec['pins']['codex_sandbox']['path'])
        original = path.read_bytes()
        spec_path = Path(self.ref['path'])
        original_spec = spec_path.read_bytes()
        try:
            path.chmod(0o600)
            path.write_bytes(original + ('(allow file-write* (subpath ' + json.dumps(str(self.shared)) + '))\n').encode())
            changed = copy.deepcopy(self.spec)
            from upgrade_live_integrity import artifact_ref
            changed['pins']['codex_sandbox'] = artifact_ref(path)
            spec_path.chmod(0o600)
            spec_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ContractError, 'SOCKET_PROFILE_CHANGED'):
                validate_startup(file_ref(spec_path))
        finally:
            path.write_bytes(original)
            path.chmod(0o444)
            spec_path.write_bytes(original_spec)
            spec_path.chmod(0o444)

    def test_materialized_alias_readers_scope_and_all_lane_kernel_permissions(self):
        self.endpoint()
        controller = Path(self.spec['controller']) / 'scripts'
        # The only substituted dependency is the shared root: all filesystem
        # operations and Seatbelt profiles are real, in disposable directories.
        for lane in ('codex', 'claude_a', 'claude_b', 'control'):
            code = f'''
import os, sys
from pathlib import Path
sys.path.insert(0, {str(controller)!r})
import mac_upgrade_socket_boundary as boundary
boundary.shared_directory = lambda: Path({str(self.shared)!r})
from mac_upgrade_startup_contract import validate_startup
spec, binding = validate_startup({self.ref!r})
from mac_upgrade_native_roots import codex_control_socket
assert str(codex_control_socket()) == spec['rpc']['path']
boundary.verify_kernel(os.getpid(), binding['socket_boundary'], writer={lane == 'codex'!r})
from mac_upgrade_common import checked_path, ContractError
try:
    checked_path(spec['rpc']['path'])
except ContractError:
    pass
else:
    raise AssertionError('generic symlink restriction was weakened')
from mac_upgrade_controller_scope import install_scope
install_scope(live=True)
assert str(codex_control_socket()) == spec['rpc']['path']
print('MATERIALIZED_EXACT_SOCKET_PASS', flush=True)
'''
            result = subprocess.run(['/usr/bin/sandbox-exec', '-f', self.spec['pins'][lane + '_sandbox']['path'],
                sys.executable, '-I', '-B', '-c', code], capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, lane + ': ' + result.stderr)
            self.assertIn('MATERIALIZED_EXACT_SOCKET_PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
