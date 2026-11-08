"""Startup joins and private-reader closure; native deployment remains a fixture."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, write_json
import upgrade_task_adoption_evidence as subject
from upgrade_task_fd_contract import contract
from upgrade_task_mach_policy import contract as mach_contract
from upgrade_live_materialize import render_private_support
from test_upgrade_task_roles import RoleTests


class AdoptionStartupEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = RoleTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        f = self.f
        self.roles = f.check()
        f.binding.update(task_native_descriptors=contract(), task_mach_boundary=mach_contract(),
                         task_model_network={'schema': 'upgrade-task-observer-network-v3'})
        self.sessions = copy.deepcopy(f.sessions)
        self.readiness = {}
        for lane, row in self.sessions.items():
            row.update(model='fixture-'+lane, model_record={'fixture': lane})
            self.readiness[lane] = dict(model=row['model'], model_record=row['model_record'],
                                       hooks={'PreToolUse': [{'fixture': 'pre'}],
                                              'PostToolUse': [{'fixture': 'post'}]})
        self.journal = dict(f.anchor['journal'], metadata={'fixture': 'metadata'})
        peer = dict(schema='upgrade-task-rpc-peer-v1', server=f.anchor['server'],
                    peer={k: f.anchor['server'][k] for k in ('pid', 'uid')},
                    foreground_authentication=False, enrollment_proven=False, input_operations=0)
        thread = dict(schema='upgrade-task-thread-read-proof-v1', thread_id=f.sid,
            server_peer=peer, socket=f.anchor['socket'], journal=self.journal,
            descriptor=dict(f.anchor['journal'], fd=12, access='w'),
            can_accept_direct_input=True, input_operations=0,
            foreground_authentication=False, caller_authentication=False, enrollment_proven=False)
        self.value = dict(schema='upgrade-live-inspection-task-v1', startup=f.startup,
            errors={}, stopped=False, input_operations=0, sessions=self.sessions,
            readiness=self.readiness, launches=self.roles['launches'], task_roles=self.roles,
            kernel=copy.deepcopy(self.roles['kernels']),
            codex_capability=dict(thread_id=f.sid, path=f.anchor['journal']['path'],
                can_accept_direct_input=True, methods=['thread/read', 'thread/turns/list'],
                input_operations=0, socket=f.anchor['socket'], task_thread=thread))
        for name, kwargs in (
            ('task_startup_result', dict(return_value=f.initial)),
            ('journal_evidence', dict(return_value=self.journal)),
            ('task_readiness', dict(return_value=self.readiness['codex'])),
            ('readiness_evidence', dict(side_effect=lambda n, s, lane, *a: self.readiness[lane]))):
            p = patch.object(subject, name, **kwargs)
            setattr(self, name, p.start()); self.addCleanup(p.stop)

    def collect(self):
        f = self.f
        return subject.collect(f.startup, f.spec, f.binding, self.value, native=object())

    def test_all_three_native_readers_are_required_and_success_grants_no_authority(self):
        result = self.collect()
        self.assertEqual(self.task_readiness.call_count, 1)
        self.assertEqual([c.args[2] for c in self.readiness_evidence.call_args_list],
                         ['claude_a', 'claude_b'])
        self.assertFalse(result['live_processes_proven'])
        self.assertFalse(result['business_adoption_proven'])
        self.assertFalse(result['whole_client_isolation_proven'])
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(subject.verify(result, self.f.startup, self.f.spec,
            self.f.binding, self.value, native=object()), result)

    def test_actual_readiness_refusal_is_not_replaced_by_inspector_ready_flags(self):
        self.value.update(ready=True, startup_ready=True, business_adoption_supported=True)
        self.readiness_evidence.side_effect = ContractError('AUTOMATIC_PRE_POST_HOOKS_NOT_CORRELATED')
        with self.assertRaisesRegex(ContractError, 'PRE_POST_HOOKS_NOT_CORRELATED'):
            self.collect()

    def test_server_role_writer_and_rpc_peer_cannot_use_display_identity(self):
        original = copy.deepcopy(self.value)
        for change in (
            lambda v: v['task_roles'].update(display=dict(pid=103, execution_owner=False)),
            lambda v: v['codex_capability']['task_thread']['server_peer']['server'].update(pid=100),
            lambda v: v['codex_capability']['task_thread']['descriptor'].update(inode=99),
            lambda v: v['kernel']['claude_a'].update(pid=100)):
            self.value = copy.deepcopy(original); change(self.value)
            with self.assertRaises(ContractError): self.collect()

    def test_repinning_another_launch_or_startup_does_not_move_authority(self):
        self.value['launches'] = dict(self.value['launches'])
        self.value['launches']['claude_a'] = write_json(self.f.root/'copied-launch.json',
            json.loads(Path(self.roles['launches']['claude_a']['path']).read_text()))
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_LAUNCHES_CHANGED'): self.collect()
        self.value['launches'] = self.roles['launches']
        self.value['startup'] = dict(self.f.startup, sha256='0'*64)
        with self.assertRaisesRegex(ContractError, 'INSPECTION_CHANGED'): self.collect()

    def test_uuid_case_is_not_a_new_endpoint_but_foreign_workspace_refuses(self):
        for session in self.sessions.values():
            session['surface_uuid'] = session['surface_uuid'].upper()
            session['workspace_uuid'] = session['workspace_uuid'].upper()
        self.assertFalse(self.collect()['business_adoption_proven'])
        self.sessions['claude_a']['workspace_uuid'] = '00000000-0000-4000-8000-000000000000'
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_SESSION_CHANGED'): self.collect()

    def test_changed_readiness_and_changed_launch_during_read_refuse(self):
        self.task_readiness.return_value = dict(self.readiness['codex'], model='changed')
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_READINESS_CHANGED'): self.collect()
        self.task_readiness.return_value = self.readiness['codex']
        def change(n, s, lane, *args):
            path = Path(self.roles['launches']['claude_a']['path'])
            write_json(path, dict(json.loads(path.read_text()), birth=[200, 0]))
            return self.readiness[lane]
        self.readiness_evidence.side_effect = change
        with self.assertRaisesRegex(ContractError, 'STARTUP_CHANGED_DURING_READ'): self.collect()

    def test_generated_private_reader_rechecks_same_evidence_without_public_imports(self):
        expected = self.collect()
        private = self.f.root/'private'
        for name, (raw, _) in render_private_support(Path(__file__).parent)[0].items():
            p = private/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
        fixture = write_json(self.f.root/'fixture.json', dict(startup=self.f.startup,
            spec=self.f.spec, binding=self.f.binding, value=self.value, initial=self.f.initial,
            journal=self.journal, expected=expected))
        code = '''import sys,json
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
import mac_upgrade_task_adoption_evidence as s
from mac_upgrade_common import ContractError
v=json.load(open(sys.argv[2]));r=v['value']['readiness']
with patch.object(s,'task_startup_result',return_value=tuple(v['initial'])), \\
     patch.object(s,'journal_evidence',return_value=v['journal']), \\
     patch.object(s,'task_readiness',return_value=r['codex']), \\
     patch.object(s,'readiness_evidence',side_effect=lambda n,t,lane,*a:r[lane]):
 result=s.verify(v['expected'],v['startup'],v['spec'],v['binding'],v['value'],native=object())
 assert result==v['expected'] and not result['business_adoption_proven']
 v['expected']['server']['pid']+=1
 try:s.verify(v['expected'],v['startup'],v['spec'],v['binding'],v['value'],native=object())
 except ContractError:pass
 else:raise AssertionError('changed evidence accepted')
assert not any(n.startswith('upgrade_') for n in sys.modules)
assert 'mac_upgrade_live_control' not in sys.modules
print('PRIVATE_STARTUP_EVIDENCE_VERIFIED_NO_ADOPTION')
'''
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code,
            str(private/'scripts'), fixture['path']], cwd=private,
            capture_output=True, text=True, timeout=25, close_fds=True)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(result.stdout.strip(), 'PRIVATE_STARTUP_EVIDENCE_VERIFIED_NO_ADOPTION')


class InspectorJoinTests(unittest.TestCase):
    def setUp(self):
        from test_upgrade_live_inspect import InspectorFlowTests
        self.f = InspectorFlowTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.spec['schema'] = 'upgrade-startup-task-v1'
        self.f.binding.update(task_startup={}, task_native_descriptors=contract(),
            task_mach_boundary=mach_contract(),
            task_model_network={'schema': 'upgrade-task-observer-network-v3'})

    def inspect(self, error=None):
        f = self.f; sid = f.spec['clients']['codex']['session_id']
        anchor = dict(journal={'path': str(f.root/(sid+'.jsonl'))})
        with ExitStack() as stack:
            for target, kwargs in (
                ('upgrade_task_readiness.task_startup_result', dict(return_value=(None, dict(thread_id=sid), anchor))),
                ('upgrade_task_thread.validate_tui_target', {}),
                ('upgrade_task_readiness.task_readiness', dict(return_value=dict(model='fixture-model', model_record={'offline_fixture': True}))),
                ('upgrade_rpc_peer.server_pin', dict(return_value={})),
                ('upgrade_task_rpc.task_rpc_type', dict(side_effect=lambda base: lambda *a, **k: base(a[0], timeout=15))),
                ('upgrade_task_thread.observe', dict(return_value={'offline_fixture': True})),
                ('upgrade_task_roles.verify_roles', dict(return_value={'offline_fixture': True}))):
                stack.enter_context(patch(target, **kwargs))
            collect = stack.enter_context(patch.object(subject, 'collect',
                side_effect=error, return_value={'fixture': 'joined-original-startup'}))
            result, _ = f.inspect_fixture()
            collect.assert_called_once()
            self.assertEqual(collect.call_args.args[:3], (f.startup, f.spec, f.binding))
        return result

    def test_inspector_includes_original_join_without_claiming_business_adoption(self):
        value = self.inspect()
        self.assertTrue(value['startup_ready'], value['errors'])
        self.assertEqual(value['task_startup_evidence'], {'fixture': 'joined-original-startup'})
        self.assertFalse(value['ready'])
        self.assertFalse(value['business_adoption_supported'])

    def test_failed_original_join_makes_startup_not_ready_with_actual_error(self):
        value = self.inspect(ContractError('TASK_ADOPTION_ORIGINAL_WRITER_CHANGED'))
        self.assertFalse(value['startup_ready'])
        self.assertEqual(value['errors']['task-startup-evidence'], 'TASK_ADOPTION_ORIGINAL_WRITER_CHANGED')
        self.assertNotIn('task_startup_evidence', value)
        self.assertFalse(value['ready'])


if __name__ == '__main__': unittest.main()
