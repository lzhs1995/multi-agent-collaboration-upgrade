"""No model calls: durable launch failures, real files/socket, held ordering.

Native process lookup and registration are explicit fixtures here. The separate
entry test exercises the actual fork/parent lifetime without any installed CLI.
"""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import socket
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from orchestration_common import ContractError, file_ref, json_ref, tree_manifest, write_json
from upgrade_task_observer_protocol import PROCESS_FIELDS, socket_pin
import upgrade_task_launcher as launcher
import upgrade_live_control as control
import test_upgrade_batch_hold as hold_fixture


class LaunchFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.plan = dict(runtime={'path':'/fixture/runtime','sha256':'a'},
            driver={'path':'/fixture/driver','sha256':'b'}, startup={},
            group='original', root=str(self.root/'launch'),
            python={'path':'/fixture/python'}, execution_python={'path':'/fixture/python'},
            entry={'path':'/fixture/entry.py'},
            campaign_path='/fixture/campaign.json', work_root=str(self.root),
            stop_path=str(self.root/'STOP'))
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(launcher, 'launch_plan', return_value=self.plan))

    def test_popen_failure_preserves_intent_and_forbids_second_start(self):
        with patch.object(launcher.subprocess, 'Popen', side_effect=OSError('fixture refused')) as spawn:
            with self.assertRaises(OSError): launcher.launch_owner(self.plan)
            with self.assertRaises(FileExistsError): launcher.launch_owner(self.plan)
        self.assertEqual(spawn.call_count, 1)
        value = json_ref(file_ref(self.root/'launch/unknown.json'))
        self.assertFalse(value['retry_allowed'])
        self.assertTrue((self.root/'launch/intent.json').exists())

    def test_stop_before_popen_is_recorded_without_start(self):
        Path(self.plan['stop_path']).touch()
        with patch.object(launcher.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'): launcher.launch_owner(self.plan)
        spawn.assert_not_called()
        self.assertTrue((self.root/'launch/unknown.json').exists())

    def test_unknown_wait_does_not_kill_or_restart_and_inherits_only_pipe(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, writer)
        child = Mock(pid=4000); child.poll.return_value = None
        def row(pid):
            intent = json_ref(file_ref(self.root/'launch/intent.json'))
            return dict(pid=pid, birth=[1,2], executable='/fixture/python',
                        file_identity={'fixture':True}, argv=intent['argv'])
        with patch.object(launcher.subprocess,'Popen',return_value=child) as spawn, \
             patch.object(launcher,'process_row',side_effect=row), \
             self.assertRaisesRegex(ContractError,'TIMEOUT_NO_REPLAY'):
            launcher.launch_owner(self.plan, credential_fd=reader, timeout=.01)
        self.assertTrue(spawn.call_args.kwargs['close_fds'])
        self.assertEqual(spawn.call_args.kwargs['pass_fds'],(reader,))
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        with self.assertRaises(OSError): os.fstat(reader)
        child.kill.assert_not_called(); child.terminate.assert_not_called()
        self.assertFalse((self.root/'launch/result.json').exists())


class PreflightTests(unittest.TestCase):
    """Real pin/tree/path checks; the full experiment registration is a fixture."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.source=self.root/'source';(self.source/'scripts').mkdir(parents=True)
        (self.source/'scripts/upgrade_task_owner_entry.py').write_text('# fixed fixture entry\n')
        self.campaigns=self.root/'campaigns';self.campaigns.mkdir()
        context=write_json(self.root/'context.json',dict(paths=dict(campaigns=str(self.campaigns))))
        self.spec=dict(schema='upgrade-startup-task-v1',controller=str(self.root/'controller'),
            adoption_path=str(self.root/'control/adoption.json'),stop_path=str(self.root/'STOP'),
            work_root=str(self.root),task_startup={'fixture':'plan'})
        startup=write_json(self.root/'startup.json',self.spec)
        self.runtime=dict(startup=startup,state_root=str(self.root/'state'),context=context,arm='B',
                          python=file_ref('/usr/bin/true'))
        self.ref=write_json(self.root/'runtime.json',self.runtime)
        from upgrade_task_network import OBSERVER_SCHEMA
        write_json(self.root/'controller/upgrade-binding.json',dict(campaign_root=str(self.campaigns/'B'),
            task_model_network=dict(schema=OBSERVER_SCHEMA)))
        self.config=dict(runtimes={'g0':self.ref},source=tree_manifest(self.source))
        self.driver=SimpleNamespace(config=self.config,registration_ref={'fixture':'registration'},
            registration={'fixture':True},_check_overall_deadline=lambda:None)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for target,kw in (
            ('upgrade_live_runtime.validate_live_runtime',dict(return_value=(None,self.runtime))),
            ('upgrade_live_control.external_publisher',{}),
            ('upgrade_live_control.held_launch',dict(return_value=True)),
            ('upgrade_driver.Driver',dict(return_value=self.driver)),
            ('upgrade_schedule.status',dict(return_value=dict(status='REGISTERED'))),
            ('upgrade_benchmark.group_registration',dict(return_value=dict(campaign_id='original'))),
            ('upgrade_benchmark.runtime_observation',{}),
            ('upgrade_task_startup.plan_value',dict(return_value={'operations':{}}))):
            self.stack.enter_context(patch(target,**kw))
        self.stack.enter_context(patch.object(launcher,'__file__',str(self.source/'scripts/upgrade_task_launcher.py')))

    def plan(self,**kw):return launcher.launch_plan(self.ref,{'fixture':'driver'},**kw)

    def test_exact_original_campaign_source_and_off_arm_are_bound_without_writes(self):
        plan=self.plan()
        self.assertEqual(plan['campaign_path'],str(self.campaigns/'B/original/campaign.json'))
        self.assertEqual(plan['group'],'g0')
        self.assertFalse(plan['credential_pipe_required'])
        self.assertFalse(Path(plan['root']).exists())

    def test_reused_attempts_refuse_even_without_a_result(self):
        for rel in ('control/task-owner-launch','control/task-owner','control/task-lifecycle',
                    'control/launch-request.json','control/held-request.json','state/startup/intent.json'):
            path=self.root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            with self.subTest(path=rel),self.assertRaisesRegex(ContractError,'NO_REPLAY'):self.plan()
            path.unlink()

    def test_wrong_runtime_ambiguous_group_and_changed_source_refuse(self):
        self.config['runtimes']={}
        with self.assertRaisesRegex(ContractError,'ORIGINAL_RUNTIME_REQUIRED'):self.plan()
        self.config['runtimes']={'g0':self.ref,'g1':self.ref}
        with self.assertRaisesRegex(ContractError,'ORIGINAL_RUNTIME_REQUIRED'):self.plan()
        self.config['runtimes']={'g0':self.ref}
        (self.source/'scripts/upgrade_task_owner_entry.py').write_text('# changed\n')
        with self.assertRaisesRegex(ContractError,'tree changed'):self.plan()

    def test_off_arm_cannot_receive_credentials_and_on_arm_requires_read_pipe(self):
        reader,writer=os.pipe();self.addCleanup(os.close,reader);self.addCleanup(os.close,writer)
        with self.assertRaisesRegex(ContractError,'EXACT_CREDENTIAL_PIPE_REQUIRED'):self.plan(credential_fd=reader)
        with patch('upgrade_task_owner_contract.advice_requirements',return_value=({'op':True},{})):
            with self.assertRaisesRegex(ContractError,'EXACT_CREDENTIAL_PIPE_REQUIRED'):self.plan()
            with self.assertRaisesRegex(ContractError,'READ_ONLY_CREDENTIAL_PIPE_REQUIRED'):self.plan(credential_fd=writer)
            self.assertTrue(self.plan(credential_fd=reader)['credential_pipe_required'])
        self.assertFalse((self.root/'control/task-owner-launch').exists())

    def test_missing_observer_contract_refuses_before_creating_owner(self):
        write_json(self.root/'controller/upgrade-binding.json',dict(campaign_root=str(self.campaigns/'B')))
        with self.assertRaisesRegex(ContractError,'OBSERVER_CONTRACT_REQUIRED'):self.plan()
        self.assertFalse((self.root/'control/task-owner-launch').exists())


class ObserveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tl-',dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.control = self.root/'control'; self.directory = self.control/'task-owner-launch'
        self.spec = dict(adoption_path=str(self.control/'adoption.json'),
                        stop_path=str(self.root/'STOP'),controller=str(self.root/'controller'))
        self.startup = write_json(self.root/'startup.json',self.spec)
        self.runtime = write_json(self.root/'runtime.json',dict(startup=self.startup))
        self.driver = {'fixture':'original driver'}
        self.argv = ['/fixture/python','-I','-B','/fixture/owner-entry.py']
        self.intent_value = dict(schema='upgrade-task-owner-launch-intent-v1',runtime=self.runtime,
            startup=self.startup,driver=self.driver,max_process_starts=1,reconnect_allowed=False,
            argv=self.argv,group='g0',campaign_path=str(self.root/'campaign.json'))
        self.intent = write_json(self.directory/'intent.json',self.intent_value)
        parent = dict(pid=4000,ppid=10,birth=[1,1],executable='/fixture/python',
                      file_identity={'fixture':'binary'},argv=self.argv)
        child = dict(parent,pid=4001,ppid=4000,birth=[1,2])
        self.rows = {4000:parent,4001:child}
        self.launch = write_json(self.directory/'process.json',dict(intent=self.intent,
            process={k:parent[k] for k in launcher.LAUNCH_PROCESS_FIELDS}))
        self.owner = write_json(self.control/'task-owner/owner.json',dict(
            schema='upgrade-task-external-owner-v1',runtime=self.runtime,driver=self.driver,
            campaign_path=self.intent_value['campaign_path'],process={k:child[k] for k in PROCESS_FIELDS},
            reconnect_allowed=False,model_operations_by_operator=0))
        observer_root = self.root/'observer'; observer_root.mkdir()
        self.socket = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.addCleanup(self.socket.close)
        endpoint = observer_root/'s'; self.socket.bind(str(endpoint)); endpoint.chmod(0o600)
        self.observer = dict(owner={k:child[k] for k in PROCESS_FIELDS},
            schedule_budget=dict(driver=self.driver,group='g0'), socket=socket_pin(endpoint))
        self.observer_ref = write_json(observer_root/'descriptor.json',self.observer)
        self.started = write_json(observer_root/'started.json',dict(descriptor=self.observer_ref,owner_pid=4001))
        self.ready = write_json(self.control/'task-owner/launch-ready.json',dict(
            schema='upgrade-task-owner-launch-ready-v1',intent=self.intent,launcher=self.launch,
            owner=self.owner,credential_bound=True,observer=self.observer_ref,observer_started=self.started))
        write_json(self.root/'controller/upgrade-binding.json',{})
        self.patcher = patch('upgrade_task_observer_client.descriptor',return_value=(self.observer_ref,self.observer))
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def observe(self, group='g0'):
        return launcher.observe_launch(self.runtime,self.driver,expected_group=group,
                                       process=lambda pid:copy.deepcopy(self.rows[pid]))

    def test_original_live_owner_accepts_reparented_launcher(self):
        self.rows[4000]['ppid'] = 1
        result = self.observe()
        self.assertEqual(result['owner'],self.owner)
        self.assertEqual(result['input_operations'],0)

    def test_owner_reparenting_and_pid_reuse_are_rejected(self):
        for field, value in (('ppid',1),('birth',[4,5]),('executable','/other/python')):
            with self.subTest(field=field):
                old = self.rows[4001][field]; self.rows[4001][field] = value
                with self.assertRaisesRegex(ContractError,'PROCESS_CHANGED'): self.observe()
                self.rows[4001][field] = old

    def test_partial_owner_publication_does_not_authorize_release(self):
        Path(self.started['path']).unlink()
        with self.assertRaises((ContractError,OSError)): self.observe()

    def test_foreign_group_stopped_or_unknown_owner_cannot_release(self):
        with self.assertRaisesRegex(ContractError,'GROUP_CHANGED'): self.observe('other')
        for path in (self.directory/'unknown.json', self.control/'task-owner/closed.json',
                     self.root/'observer/closed.json',self.root/'observer/terminal.json',Path(self.spec['stop_path'])):
            with self.subTest(path=path.name):
                write_json(path,{})
                with self.assertRaises(ContractError): self.observe()
                path.unlink()


class TaskHoldTests(unittest.TestCase):
    def setUp(self):
        self.f = hold_fixture.BatchHoldTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        spec = json_ref(self.f.startup); spec['schema'] = 'upgrade-startup-task-v1'
        self.f.startup = write_json(self.f.base/'startup.json',spec)
        self.f.runtime['startup'] = self.f.startup
        self.f.ref = write_json(self.f.base/'runtime.json',self.f.runtime)
        self.driver = {'fixture':'driver'}
        self.intent = write_json(self.f.base/'fixture-owner-intent.json',dict(
            runtime=self.f.ref,driver=self.driver,registration={'registration':True},group='pilot-B'))
        self.owner = dict(status='ORIGINAL_TASK_OWNER_READY',intent=self.intent,owner={'fixture':'owner'})
        self.driver_object=SimpleNamespace(registration_ref={'registration':True},
            config={'runtimes':{'pilot-B':self.f.ref}},_check_overall_deadline=lambda:None)
        p=patch('upgrade_driver.Driver',return_value=self.driver_object)
        p.start();self.addCleanup(p.stop)

    def test_task_direct_launch_and_unbound_hold_refuse_before_any_spawn(self):
        with patch.object(control,'invocation') as invoke:
            with self.assertRaisesRegex(ContractError,'HOLD_AND_RELEASE'): control.control(self.f.ref,'launch')
            with self.assertRaisesRegex(ContractError,'ORIGINAL_DRIVER_REQUIRED'): control.control(self.f.ref,'hold')
        invoke.assert_not_called()

    def test_owner_ready_precedes_held_harness_and_launch_clock_stays_absent(self):
        order=[]
        def owner(*args,**kwargs):
            self.assertFalse((self.f.base/'live-control/held-request.json').exists())
            order.append('owner'); return self.owner
        def harness(*args,**kwargs):
            request=json_ref(file_ref(self.f.base/'live-control/held-request.json'))
            self.assertEqual(request['task_owner'],self.owner)
            order.append('harness'); return None,dict(returncode=0)
        with patch.object(launcher,'launch_plan',return_value={'fixture':'plan'}), \
             patch.object(launcher,'launch_owner',side_effect=owner), \
             patch.object(control,'invocation',side_effect=harness):
            result=control.control(self.f.ref,'hold',driver_ref=self.driver)
        self.assertEqual(order,['owner','harness'])
        self.assertEqual(result['status'],'HELD_LAUNCH_RETURNED')
        self.assertFalse((self.f.base/'live-control/launch-request.json').exists())

    def test_unknown_held_launch_preserves_request_and_closes_owner_gate(self):
        with patch.object(launcher,'launch_plan',return_value={'fixture':'plan'}), \
             patch.object(launcher,'launch_owner',return_value=self.owner), \
             patch.object(control,'invocation',return_value=(None,dict(returncode=None))):
            self.assertEqual(control.control(self.f.ref,'hold',driver_ref=self.driver)['status'],'HELD_LAUNCH_UNCONFIRMED')
        self.assertTrue((self.f.base/'live-control/held-request.json').exists())
        self.assertTrue((self.f.base/'live-control/task-owner-launch/unknown.json').exists())

    def held(self):
        self.f.held_spawn()
        write_json(self.f.base/'live-control/held-request.json',dict(
            runtime=self.f.ref,startup=self.f.startup,task_owner=self.owner))

    def test_release_rechecks_original_owner_before_original_private_release(self):
        self.held()
        with patch.object(launcher,'observe_launch',return_value=self.owner) as observe:
            self.assertEqual(self.f.release()['status'],'RELEASED')
        observe.assert_called_once_with(self.f.ref,self.driver,expected_group='pilot-B')
        self.assertEqual(len(self.f.calls),1)

    def test_unready_owner_blocks_release_without_consuming_release_intent(self):
        self.held()
        with patch.object(launcher,'observe_launch',side_effect=ContractError('original observer stopped')):
            with self.assertRaisesRegex(ContractError,'observer stopped'): self.f.release()
        self.assertEqual(self.f.calls,[])
        self.assertFalse((self.f.base/'live-control/launch-request.json').exists())

    def test_original_deadline_blocks_release_without_resetting_it(self):
        self.held()
        def expired():raise ContractError('original overall deadline reached')
        self.driver_object._check_overall_deadline=expired
        with patch.object(launcher,'observe_launch') as observe:
            with self.assertRaisesRegex(ContractError,'original overall deadline'):self.f.release()
        observe.assert_not_called()
        self.assertEqual(self.f.calls,[])


class CredentialTests(unittest.TestCase):
    def pipe(self, payload=b'', eof=False):
        reader, writer = os.pipe()
        if payload: os.write(writer,payload)
        if eof: os.close(writer)
        else: self.addCleanup(os.close,writer)
        return reader

    def test_empty_or_unclosed_pipe_times_out_closes_fd_without_echoing_key(self):
        for payload in (b'',b'fixture-private-sentinel'):
            reader=self.pipe(payload)
            began=time.monotonic()
            with self.assertRaisesRegex(ContractError,'EOF_TIMEOUT_NO_REPLAY') as error:
                launcher.read_launch_credential(SimpleNamespace(_verify=lambda:None),reader,timeout=.02)
            self.assertLess(time.monotonic()-began,.5)
            self.assertNotIn('fixture-private-sentinel',str(error.exception))
            with self.assertRaises(OSError): os.fstat(reader)

    def test_stop_or_unknown_interrupts_read_before_eof(self):
        def stop():raise ContractError('original stop or unknown')
        reader=self.pipe()
        with self.assertRaisesRegex(ContractError,'original stop'):
            launcher.read_launch_credential(SimpleNamespace(_verify=stop),reader)
        with self.assertRaises(OSError): os.fstat(reader)

    def test_bounded_eof_success_and_oversize_refusal(self):
        owner=SimpleNamespace(_verify=lambda:None)
        reader=self.pipe(b'fixture-single-key\n',eof=True)
        self.assertEqual(launcher.read_launch_credential(owner,reader),'fixture-single-key')
        reader=self.pipe(b'x'*4097,eof=True)
        with self.assertRaisesRegex(ContractError,'TOO_LARGE'):
            launcher.read_launch_credential(owner,reader)


if __name__ == '__main__': unittest.main()
