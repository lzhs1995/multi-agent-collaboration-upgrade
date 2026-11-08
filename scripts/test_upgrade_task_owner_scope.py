"""Real generated-controller import and Darwin kernel refusal, no CLI launch."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from orchestration_common import json_ref
from upgrade_client_isolation import clean_child_environment
from test_upgrade_task_topology import TaskTopologyTests, SOURCE, PACKAGE


@unittest.skipUnless(sys.platform == 'darwin' and SOURCE.is_dir() and PACKAGE.is_dir(),
                     'requires original local macOS packages')
class OwnerScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = type('OwnerScopeFixture', (TaskTopologyTests,), dict(model_network=True,
            callback_network=True, observer_network=True, mach_boundary=True,
            native_descriptors=True, runtime_policy=True))
        cls.fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def child(self, code, *, lane=None):
        f = self.fixture
        prefix = ([] if lane is None else ['/usr/bin/sandbox-exec', '-f',
                  f.spec['pins'][lane+'_sandbox']['path']])
        payload = ('import sys,json\nfrom pathlib import Path\n'
                   'sys.path.insert(0,sys.argv[1])\n'
                   'runtime_ref=json.loads(sys.argv[2])\n'+code)
        return subprocess.run([*prefix, f.runtime['python']['path'], '-I', '-B', '-c', payload,
            str(Path(__file__).resolve().parent), json.dumps(f.runtime_ref)],
            capture_output=True, text=True, timeout=60, env=clean_child_environment({}))

    def test_actual_external_scope_loads_private_readers_and_can_publish_only_private_state(self):
        result = self.child('''from upgrade_task_owner import install_owner_scope
runtime,spec=install_owner_scope(runtime_ref)
from orchestration_campaign import controller_modules
modules=controller_modules(runtime)
from orchestration_common import write_json
root=Path(spec['adoption_path']).parent
proof=write_json(root/'external-scope-proof.json',{'fixture':'external owner'},exclusive=True)
refused=[]
for path in (Path(spec['controller'])/'SKILL.md',root.parent.parent.parent/'outside-owner-scope.txt'):
 try:
  with path.open('wb') as f:f.write(b'must not write')
 except PermissionError:refused.append(str(path))
 else:raise AssertionError('write audit did not refuse')
print(json.dumps({'loaded':[m.__name__ for m in modules],'proof':proof,'refused':refused}))
''')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(len(value['loaded']), 4)
        self.assertEqual(len(value['refused']), 2)
        self.assertEqual(json_ref(value['proof']), {'fixture': 'external owner'})

    def test_actual_client_sandboxes_cannot_acquire_external_owner_scope(self):
        for lane in ('codex', 'claude_a', 'claude_b', 'control'):
            with self.subTest(lane=lane):
                result = self.child('''from upgrade_task_owner import install_owner_scope
install_owner_scope(runtime_ref)
raise AssertionError('sandboxed client acquired external publication authority')
''', lane=lane)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('CLIENT_CANNOT_PUBLISH_ADOPTION', result.stderr)

    def test_direct_guard_entry_is_also_refused_by_the_real_client_kernel_boundary(self):
        f = self.fixture
        Path(f.spec['adoption_path']).parent.mkdir(mode=0o700, exist_ok=True)
        code = '''import importlib
sys.path.insert(0,sys.argv[3])
scope=importlib.import_module('mac_upgrade_controller_scope')
scope.install_scope(live=True,external_startup=json.loads(sys.argv[4]))
raise AssertionError('direct scope escalation')
'''
        for lane in ('codex', 'claude_a'):
            result = subprocess.run(['/usr/bin/sandbox-exec', '-f',
                f.spec['pins'][lane+'_sandbox']['path'], f.runtime['python']['path'], '-I', '-B',
                '-c', 'import sys,json\n'+code, 'unused', 'unused',
                str(Path(f.spec['controller'])/'scripts'), json.dumps(f.runtime['startup'])],
                capture_output=True, text=True, timeout=60, env=clean_child_environment({}))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('UPGRADE_EXTERNAL_OWNER_KERNEL_DENIED', result.stderr)

    def test_actual_owner_starts_observer_before_clients_and_waits_without_launching(self):
        result = self.child('''from upgrade_task_owner import TaskOwner
from orchestration_common import json_ref,file_ref
from types import SimpleNamespace
from unittest.mock import patch
from test_upgrade_task_observer_budget import fixture_driver
runtime=json_ref(runtime_ref)
binding=json_ref(file_ref(Path(runtime['controller']['path'])/'upgrade-binding.json'))
# Only the absent benchmark registration is a fixture. Private imports,
# observer, process identity, kernel boundaries and shutdown are real.
driver_ref=fixture_driver(Path(binding['tmp_root'])/'benchmark',runtime_ref)
config=json_ref(driver_ref)
driver=SimpleNamespace(config=config,registration_ref=config['registration'],_check_overall_deadline=lambda:None)
with patch('upgrade_driver.Driver',return_value=driver), patch('upgrade_benchmark.group_registration',return_value={'campaign_id':'original'}):
 owner=TaskOwner(runtime_ref, str(Path(binding['campaign_root'])/'original/campaign.json'),driver_ref=driver_ref)
owner._schedule=lambda:dict(status='ACTIVE',active=dict(group='g0',run_id='fixture-run'))
try:
 from mac_upgrade_task_observer_client import descriptor
 actual_descriptor=descriptor(binding)[1]
 assert actual_descriptor['schema']=='upgrade-task-observer-endpoint-v2'
 assert actual_descriptor['maximum_requests']==8192
 value=owner.tick()
 assert value['status']=='WAITING_ORIGINAL_SERVER',value
 assert owner.observer.thread.is_alive()
 assert owner.client is None and owner.lifecycle is None
 assert not (Path(binding['state_root'])/'startup').exists()
finally:closed=owner.close()
print(json.dumps({'result':value,'closed':closed,'observer_closed':owner.observer.closed}))
''')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['observer_closed'])
        self.assertFalse(value['closed']['business_accepted'])


if __name__ == '__main__':
    unittest.main()
