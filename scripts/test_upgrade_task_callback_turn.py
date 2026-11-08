"""Idle callback components; no live sender, ingress listener or adoption.

Intent cases use local native journals and the original .84 fixture. Service
cases use the real retained service/gate/SQLite with explicit mocked dispatch
and ingress observations and RPC. The kernel-peer test uses a real Unix socket
with fixture process rows. None of these is a live callback acceptance.
"""
import copy
import hashlib
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
import upgrade_task_callback_turn as cb
import test_upgrade_task_callback as settlement_fixture
import test_upgrade_task_next_input as next_input_fixture


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.f=settlement_fixture.SettlementTests();self.addCleanup(self.f.doCleanups);self.f.setUp()
        self.attempt=copy.deepcopy(self.f.attempt)
        self.attempt['phase']='NATIVE_INPUT_INTENT'
        self.event=self.attempt['events'][0]
        self.request=self.event['native_request']
        self.turn=self.request['observed_turn_id']
        self.request.update(method='turn/start',observed_status='idle')
        self.request['native_metadata'].update(thread_status='idle',latest_turn_status='completed',
                                              available_input_method='turn/start')
        self.request['params'].pop('expectedTurnId')
        self.rehash()

    def rehash(self):
        self.event['native_input_fence']['native_request_sha256']=hashlib.sha256(
            json.dumps(self.request,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

    def check(self):
        return cb.idle_intent(self.attempt,self.f.bound,self.f.text,self.f.receiver,
            self.f.sender,self.turn,self.f.bound['task_target']['socket'])

    def test_exact_proposed_idle_intent_is_read_without_input(self):
        self.assertEqual(self.check(),self.request)
        self.assertFalse(self.f.f.inputs)

    def test_changed_params_phase_and_foreign_socket_refuse(self):
        for field,value in (('method','turn/steer'),('observed_turn_id','foreign'),
                            ('socket',{}),('params',dict(self.request['params'],model='override'))):
            original=copy.deepcopy(self.request);self.request[field]=value;self.rehash()
            with self.assertRaisesRegex(ContractError,'ORIGINAL_IDLE_INPUT_CHANGED'):self.check()
            self.request.clear();self.request.update(original);self.rehash()
        self.attempt['phase']='UNKNOWN'
        with self.assertRaisesRegex(ContractError,'FRESH_CONSUMED_INTENT_REQUIRED'):self.check()

    def test_fence_hash_and_native_prefix_changes_refuse(self):
        self.event['native_input_fence']['native_request_sha256']='0'*64
        with self.assertRaisesRegex(ContractError,'ORIGINAL_INPUT_FENCE_CHANGED'):self.check()
        self.rehash()
        self.event['native_input_fence']['captured_at_epoch']=self.event['at_epoch']+1
        with self.assertRaisesRegex(ContractError,'CHRONOLOGY_CHANGED'):self.check()

    def test_duplicate_or_terminal_input_event_cannot_become_fresh_intent(self):
        self.attempt['events'].insert(0,copy.deepcopy(self.event))
        with self.assertRaisesRegex(ContractError,'FRESH_CONSUMED_INTENT_REQUIRED'):self.check()
        self.attempt['events'].pop(0)
        self.attempt['events'].insert(0,dict(phase='ENTER_SENT',at_epoch=self.event['at_epoch']))
        with self.assertRaisesRegex(ContractError,'FRESH_CONSUMED_INTENT_REQUIRED'):self.check()


class IngressTests(unittest.TestCase):
    def test_kernel_peer_must_reach_original_parent_and_no_environment_is_saved(self):
        a,b=socket.socketpair();self.addCleanup(a.close);self.addCleanup(b.close)
        b.sendall(b'x');self.assertEqual(a.recv(1),b'x')
        pid=os.getpid();parent=os.getppid()
        rows={pid:dict(pid=pid,ppid=parent,birth=[1,2],executable='/fixture/tool',
                       file_identity=[1,2,3,4],env=dict(secret='not-persisted')),
              parent:dict(pid=parent,ppid=1,birth=[3,4],executable='/fixture/claude',
                          file_identity=[4,3,2,1],env={})}
        sender=dict(process={k:v for k,v in rows[parent].items() if k not in ('ppid','env')})
        result=cb.caller_ancestry(a,sender,lambda p:copy.deepcopy(rows[p]))
        self.assertEqual(result['peer']['pid'],pid)
        self.assertNotIn('not-persisted',json.dumps(result))
        sender['process']['birth']=[4,5]
        with self.assertRaisesRegex(ContractError,'ORIGINAL_CLAUDE_CHANGED'):
            cb.caller_ancestry(a,sender,lambda p:copy.deepcopy(rows[p]))

    def test_same_pid_with_changed_incarnation_and_ancestry_loop_refuse(self):
        row=dict(pid=401,ppid=402,birth=[1,2],executable='/fixture/tool',file_identity=[1,2,3,4])
        parent=dict(row,pid=402,ppid=1,birth=[3,4])
        sender=dict(process={k:v for k,v in parent.items() if k!='ppid'})
        calls=[]
        def process(pid):
            calls.append(pid)
            if pid==402:return copy.deepcopy(parent)
            return dict(row,birth=[9,9]) if len(calls)>2 else copy.deepcopy(row)
        with patch.object(cb,'kernel_peer',return_value=dict(pid=401,uid=os.getuid())):
            with self.assertRaisesRegex(ContractError,'CALLER_CHANGED_DURING_READ'):
                cb.caller_ancestry(object(),sender,process)
            row['ppid']=401
            with self.assertRaisesRegex(ContractError,'ANCESTRY_INVALID'):
                cb.caller_ancestry(object(),sender,lambda p:copy.deepcopy(row))


class ObservationTests(unittest.TestCase):
    """Real admission predicates; dispatch state, ancestry and fence are fixtures."""
    def setUp(self):
        from orchestration_coordinator import State
        f=next_input_fixture.NextInputTests();self.addCleanup(f.doCleanups);f.setUp();self.f=f
        self.sender=f.base.campaign['sessions']['claude_a']
        receipt=f.f.control/'callback.json';report=f.f.control/'report.md';report.write_text('original report')
        self.pack=dict(task_id='original-callback',completion_nonce='nonce',completion_callback='body',
                       completion_receipt=str(receipt),report=str(report),executor_uuid=self.sender['surface_uuid'])
        self.pack_ref=write_json(f.f.control/'pack.json',self.pack)
        directory=receipt.with_name(receipt.stem+'-attempts');directory.mkdir()
        self.attempt_path=directory/'attempt-0001.json'
        self.attempt=dict(phase='NATIVE_INPUT_INTENT',events=[],native_binding=dict(schema='native-delivery-task-v1'),
            binding=dict(task_pack_sha256=self.pack_ref['sha256'],report_sha256=file_ref(report)['sha256'],
                report_bytes=file_ref(report)['bytes'],completion_nonce='nonce',completion_callback='body'))
        self.request=dict(pack=self.pack_ref,attempt=write_json(self.attempt_path,self.attempt))
        run=f.base.campaign['runs'][0]['run_id'];self.run=run
        write_json(Path(f.base.campaign['root'])/'runs'/run/'run.json',{'fixture':'dispatch'})
        self.info=dict(mode='delegated_cli',delivery={'fixture':'dispatch'},prepared=dict(pack=self.pack_ref),
                       task_id='original-callback')
        self.states=dict(local=State.DISPATCHED)
        self.co=SimpleNamespace(state=lambda:(self.states,dict(local=self.info)),
            nodes=dict(local=dict(id='local',executor='claude_a')),_work_budget=lambda:None)
        self.params=dict(method='turn/start',params=dict(threadId=f.f.registration['thread_id'],
                            input=[dict(type='text',text='body')]))
        for p in (patch.object(f.gate.runner,'co',return_value=self.co),
                  patch('orchestration_campaign.controller_modules',return_value=(
                      SimpleNamespace(validate_task_pack_contract=lambda p:json_ref(file_ref(p))),f.base.native,None,None)),
                  patch.object(cb,'caller_ancestry',return_value={'fixture':'original caller'}),
                  patch.object(cb,'idle_intent',return_value=self.params)):
            p.start();self.addCleanup(p.stop)

    def create(self):return cb.CallbackInput(self.f.service,object(),self.request)

    def test_original_dispatched_pack_selects_exact_claude_lane(self):
        incoming=self.create()
        self.assertEqual(incoming.initial['lane'],'claude_a')
        self.assertEqual(incoming.initial['run_id'],self.run)
        self.assertFalse(self.f.inputs)

    def test_completed_or_undispatched_node_cannot_wake_receiver(self):
        from orchestration_coordinator import State
        self.states['local']=State.ACCEPTED
        with self.assertRaisesRegex(ContractError,'ORIGINAL_DISPATCH_REQUIRED'):self.create()
        self.states['local']=State.DISPATCHED;self.info.pop('delivery')
        with self.assertRaisesRegex(ContractError,'ORIGINAL_DISPATCH_REQUIRED'):self.create()
        self.assertFalse(self.f.inputs)

    def test_changed_report_and_nonlatest_attempt_refuse(self):
        Path(self.pack['report']).write_text('altered report')
        with self.assertRaisesRegex(ContractError,'ORIGINAL_REPORT_CHANGED'):self.create()
        Path(self.pack['report']).write_text('original report')
        write_json(self.attempt_path.with_name('attempt-0002.json'),self.attempt)
        with self.assertRaisesRegex(ContractError,'ORIGINAL_INPUT_ATTEMPT_CHANGED'):self.create()
        self.assertFalse(self.f.inputs)

    def test_uncertain_prior_attempt_and_changed_lane_refuse(self):
        self.request['attempt']=write_json(self.attempt_path.with_name('attempt-0002.json'),self.attempt)
        with self.assertRaisesRegex(ContractError,'UNCERTAIN_INPUT_WAS_RETRIED'):self.create()
        self.attempt_path.with_name('attempt-0002.json').unlink()
        self.request['attempt']=file_ref(self.attempt_path)
        self.pack['executor_uuid']='different'
        self.pack_ref=write_json(self.pack_ref['path'],self.pack)
        self.info['prepared']['pack']=self.pack_ref;self.request['pack']=self.pack_ref
        with self.assertRaisesRegex(ContractError,'ORIGINAL_SENDER_CHANGED'):self.create()
        self.assertFalse(self.f.inputs)

    def test_runtime_busy_and_work_budget_exhaustion_refuse(self):
        self.f.service.runtime_owner=SimpleNamespace(running=True,closed=False)
        with self.assertRaisesRegex(ContractError,'RUNTIME_BUSY_OR_CLOSED'):self.create()
        self.f.service.runtime_owner=None
        self.co._work_budget=lambda:(_ for _ in ()).throw(ContractError('fixture work budget ended'))
        with self.assertRaisesRegex(ContractError,'work budget ended'):self.create()
        self.assertFalse(self.f.inputs)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.f=next_input_fixture.NextInputTests();self.addCleanup(self.f.doCleanups);self.f.setUp()
        self.service=self.f.service
        self.attempt=write_json(self.f.f.control/'callback-attempt.json',{'fixture':'original'})
        self.pack=write_json(self.f.f.control/'callback-pack.json',{'fixture':'original'})
        self.request=dict(pack=self.pack,attempt=self.attempt)
        self.initial=dict(pack=self.pack,attempt=self.attempt,report=self.pack,
            caller=dict(fixture='dispatch-and-ingress-explicitly-mocked'),run_id='run-01',node_id='local',
            lane='claude_a',text='exact callback body',request=dict(method='turn/start',
                params=dict(threadId=self.f.f.registration['thread_id'],
                            input=[dict(type='text',text='exact callback body')])))
        p=patch.object(cb.CallbackInput,'observe',side_effect=lambda:copy.deepcopy(self.initial))
        self.observed=p.start();self.addCleanup(p.stop)

    def test_one_callback_uses_same_service_and_exact_input_then_enrolls(self):
        before=self.f.f.ledger.identity
        result=self.service.start_callback_turn(object(),self.request)
        self.assertEqual(result['model_inputs_sent'],1)
        self.assertEqual(self.f.inputs,[self.initial['request']['params']])
        self.assertEqual(self.f.f.ledger.identity,before)
        self.assertEqual(self.f.f.ledger.active_turn,self.f.turn)
        proof=json_ref(result['callback_input'])
        self.assertFalse(proof['native_user_receipt']);self.assertFalse(proof['model_consumption'])
        source=json_ref(proof['source'])
        self.assertEqual(json_ref(source['original_attempt_snapshot']),json_ref(self.attempt))
        with self.assertRaisesRegex(ContractError,'NORMAL_TERMINAL_REQUIRED'):
            self.service.start_callback_turn(object(),self.request)
        self.assertEqual(len(self.f.inputs),1)

    def test_file_only_plan_cannot_activate_ingress_capability(self):
        incoming=cb.CallbackInput(self.service,object(),self.request);plan=incoming.publish()
        with self.assertRaisesRegex(ContractError,'LIVE_INPUT_CONTEXT_REQUIRED'):
            self.service.start_next_turn(plan)
        self.assertFalse(self.f.inputs)

    def test_original_source_or_snapshot_tampering_prevents_send(self):
        incoming=cb.CallbackInput(self.service,object(),self.request);plan=incoming.publish()
        write_json(incoming.snapshot['path'],{'fixture':'modified'})
        with incoming.activate():
            with self.assertRaises(ContractError):self.service.start_next_turn(plan)
        self.assertFalse(self.f.inputs)

    def test_unknown_send_consumes_shared_budget_without_retry(self):
        self.f.failure=TimeoutError('fixture original callback outcome unknown')
        with self.assertRaisesRegex(TimeoutError,'outcome unknown'):
            self.service.start_callback_turn(object(),self.request)
        with self.assertRaisesRegex(ContractError,'TRANSITION_ALREADY_ATTEMPTED'):
            self.service.start_callback_turn(object(),self.request)
        with self.assertRaises(FileExistsError):self.service.start_next_turn(self.f.plan)
        self.assertEqual(len(self.f.inputs),1)
        self.assertEqual(self.f.f.ledger.active_turn,self.f.old)

    def test_changed_ingress_before_input_preserves_no_send(self):
        incoming=cb.CallbackInput(self.service,object(),self.request);plan=incoming.publish()
        self.initial['caller']={'fixture':'different connected process'}
        with incoming.activate():
            with self.assertRaisesRegex(ContractError,'INPUT_CHANGED'):self.service.start_next_turn(plan)
        self.assertFalse(self.f.inputs)


if __name__=='__main__':unittest.main()
