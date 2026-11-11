"""Real observer receipts; deployment and native hook origin remain fixtures."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_task_observer_evidence import verify, hook
from upgrade_readiness_evidence import completed_probe
import test_upgrade_task_observer_transport as transport


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = transport.ObserverTransportTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.binding = self.f.binding
        self.binding['state_root'] = str(self.f.root/'state')
        self.startup = self.f.source.runtime['startup']
        write_json(self.f.root/'state/startup/claude_a.json',
                   dict(self.f.launch, startup=self.startup))
        self.service = self.f.start()

    def capture(self):
        child = self.f.child("from upgrade_task_observer_evidence import capture;"
            "from orchestration_common import file_ref;from pathlib import Path;"
            "s=file_ref(Path(binding['config_root'])/'startup-v2.json');"
            "print(json.dumps(capture(binding,s,'claude_a')))")
        self.assertEqual((child.returncode, child.stderr), (0, ''))
        return json.loads(child.stdout)

    def test_original_receipt_reads_after_close_but_grants_no_live_authority(self):
        ref = self.capture(); self.service.stop()
        value = verify(ref, self.binding, self.startup, 'claude_a')
        self.assertFalse(value['adoption_proven'])
        self.assertFalse(value['requester_direct_rpc_peer'])
        who = value['caller']
        result = hook(dict(direct_client='claude_a', task_observer=ref,
            pid=who['peer']['pid'], parents=who['ancestry'][1:]), self.binding, self.startup)
        self.assertEqual(result, value)

    def test_foreign_hook_pid_ancestry_lane_or_startup_refuses(self):
        ref = self.capture(); value = verify(ref, self.binding, self.startup, 'claude_a')
        who = value['caller']
        for kwargs in (dict(pid=who['peer']['pid']+1), dict(parents=[])):
            with self.assertRaises(ContractError): verify(ref, self.binding, self.startup, 'claude_a', **kwargs)
        with self.assertRaises(ContractError): verify(ref, self.binding, self.startup, 'claude_b')
        with self.assertRaises(ContractError):
            verify(ref, self.binding, dict(self.startup, sha256='0'*64), 'claude_a')

    def test_changed_response_nonce_and_copied_receipt_refuse(self):
        ref = self.capture(); original = json_ref(ref)
        copied = write_json(self.f.root/'copy.json', original)
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_PATH_REQUIRED'):
            verify(copied, self.binding, self.startup, 'claude_a')
        bad = copy.deepcopy(original); bad['response']['nonce'] = 'a'*48
        write_json(Path(ref['path']), bad)
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_RESPONSE_CHANGED'):
            verify(file_ref(ref['path']), self.binding, self.startup, 'claude_a')

    def test_probe_result_requires_original_tree_receipt_only_in_explicit_new_contract(self):
        ref = self.capture()
        spec = dict(controller=self.binding['controller_root'], clients={'claude_a': {'native_root': '/fixture/native'}})
        legacy = dict(status='UPGRADE_READINESS_TOOL_COMPLETED', lane='claude_a',
            native_root='/fixture/native', controller=spec['controller'], adoption_proven=False, input_operations=0)
        current = dict(legacy, task_observer=ref)
        self.assertTrue(completed_probe(json.dumps(current), 'claude_a', spec, observer_binding=self.binding))
        self.assertFalse(completed_probe(json.dumps(legacy), 'claude_a', spec, observer_binding=self.binding))
        self.assertTrue(completed_probe(json.dumps(legacy), 'claude_a', spec))
        self.assertFalse(completed_probe(json.dumps(current), 'claude_a', spec))
        self.assertFalse(completed_probe(dict(exit_code=1, output=json.dumps(current)),
            'claude_a', spec, observer_binding=self.binding))

    def test_actual_probe_produces_read_before_success_output(self):
        self.binding['native_roots'] = {'claude_a': '/fixture/native'}
        child = self.f.child("""
import os
from pathlib import Path
from unittest.mock import patch
import upgrade_client_probe as probe
os.environ['UPGRADE_CLIENT_LANE']='claude_a'
with patch.object(probe,'configuration',return_value=(binding,{})), patch.object(probe,'configured_root',return_value=Path('/fixture/native')), patch.object(sys,'argv',['probe','--lane','claude_a']):
 probe.main()
""")
        self.assertEqual((child.returncode, child.stderr), (0, ''))
        value = json.loads(child.stdout)
        self.assertEqual(value['status'], 'UPGRADE_READINESS_TOOL_COMPLETED')
        observed = verify(value['task_observer'], self.binding, self.startup, 'claude_a')
        self.assertEqual(observed['caller']['lane'], 'claude_a')

    def test_generated_private_reader_validates_original_receipt_without_source_fallback(self):
        from upgrade_live_materialize import render_private_support
        ref = self.capture()
        private = self.f.root/'private'
        buffers, _ = render_private_support(Path(__file__).parent)
        for name, (raw, _) in buffers.items():
            path = private/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        code = '''
import json,sys
sys.path.insert(0,sys.argv[1])
from mac_upgrade_task_observer_evidence import verify
from mac_upgrade_readiness_evidence import completed_probe
from mac_upgrade_common import ContractError
ref,binding,startup=json.loads(sys.argv[2])
observed=verify(ref,binding,startup,'claude_a')
assert not observed['adoption_proven']
try:
 verify(ref,binding,startup,'claude_b')
except ContractError:
 pass
else:
 raise AssertionError('foreign lane accepted')
spec={'controller':binding['controller_root'],'clients':{'claude_a':{'native_root':'/fixture/native'}}}
value=dict(status='UPGRADE_READINESS_TOOL_COMPLETED',lane='claude_a',native_root='/fixture/native',
 controller=spec['controller'],adoption_proven=False,input_operations=0,task_observer=ref)
assert completed_probe(json.dumps(value),'claude_a',spec,observer_binding=binding)
assert not any(n.startswith('upgrade_') for n in sys.modules)
print('PRIVATE_ORIGINAL_OBSERVER_RECEIPT_VERIFIED')
'''
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code,
            str(private/'scripts'), json.dumps([ref,self.binding,self.startup])],
            cwd=private, capture_output=True, text=True, timeout=15, close_fds=True)
        self.assertEqual((result.returncode,result.stderr),(0,''))
        self.assertEqual(result.stdout.strip(),'PRIVATE_ORIGINAL_OBSERVER_RECEIPT_VERIFIED')


class HookJoinTests(unittest.TestCase):
    def setUp(self):
        import test_upgrade_task_hooks as hooks
        self.f = hooks.TaskHookTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.binding['task_model_network'] = {'schema': 'upgrade-task-observer-network-v3'}

    def test_reader_requires_hook_receipt_for_both_original_events(self):
        with self.assertRaises(KeyError): self.f.evaluate()
        for event, value in self.f.values.items(): value['task_observer'] = {'fixture': event}
        with patch('upgrade_task_observer_evidence.hook') as verify_hook:
            self.assertTrue(self.f.evaluate()['hooks_proven'])
        self.assertEqual(verify_hook.call_count, 2)
        self.assertEqual({c.args[0]['payload']['hook_event_name'] for c in verify_hook.call_args_list},
                         {'PreToolUse', 'PostToolUse'})

    def test_hook_producer_calls_same_receipt_capture_with_actual_pid_ancestry(self):
        import upgrade_hook_observer as observer
        import __main__
        f = self.f
        binding = dict(f.binding, config_root=str(f.root/'config'),
            controller_root=f.spec['controller'], native_roots={'codex': str(f.root)})
        f.startup = write_json(f.root/'config/startup-v2.json', f.spec)
        main = str(Path(f.spec['controller'])/'scripts/cmux_workflow_advisory.py')
        parents = f.values['PreToolUse']['parents']
        with patch.object(observer, 'configuration', return_value=(binding, {'python': {'path': '/fixture/python'}})), \
             patch.object(observer, 'hook_parents', return_value=(parents, 'codex-server')), \
             patch.object(__main__, '__file__', main, create=True), \
             patch.object(observer.sys, 'argv', [main, '--hook', f.hook]), \
             patch.dict(os.environ, {'UPGRADE_CLIENT_LANE': 'codex'}), \
             patch('upgrade_task_observer_evidence.capture', return_value={'fixture': 'receipt'}) as capture:
            observer.record(f.values['PreToolUse']['payload'], original_hook=f.hook)
        capture.assert_called_once_with(binding, f.startup, 'codex-server', parents=parents)
        from upgrade_hook_observer import task_call_directory
        files = list(task_call_directory(binding, f.startup, f.f.session['session_id'] if hasattr(f.f,'session')
            else '11111111-1111-4111-8111-111111111111', 'call-ready').glob('*.json'))
        self.assertEqual(len(files), 1)
        value = json_ref(file_ref(files[0]))
        self.assertEqual(value['task_observer'], {'fixture': 'receipt'})
        self.assertEqual(value['pid'], os.getpid())


if __name__ == '__main__': unittest.main()
