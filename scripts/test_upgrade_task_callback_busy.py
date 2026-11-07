"""Busy input on real retained runtime/SQLite and Unix ingress with RPC fixtures.

Original dispatch/adoption identity is fixture supplied. This is not live model
callback consumption or acceptance. All sends retain original intent/budget.
"""
import copy
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,canonical,file_ref,json_ref,write_json
from upgrade_task_callback_contract import BUSY_SCHEMA
from upgrade_task_callback_ingress import REQUEST_SCHEMA
from upgrade_task_runtime import CampaignRuntime
from upgrade_task_network import OBSERVER_SCHEMA
import upgrade_task_callback_busy as busy
import upgrade_task_callback_turn as cb
import test_upgrade_task_callback_turn as fixture
import test_upgrade_task_callback_sender as sender_fixture


class BusyIntentTests(fixture.IntentTests):
    def setUp(self):
        super().setUp()
        self.request.update(method='turn/steer',observed_status='active')
        self.request['params']['expectedTurnId']=self.turn
        self.request['native_metadata'].update(thread_status='active',latest_turn_status='inProgress',
                                               available_input_method='turn/steer')
        self.rehash()

    def check(self):
        return cb.idle_intent(self.attempt,self.f.bound,self.f.text,self.f.receiver,self.f.sender,
                             self.turn,self.f.bound['task_target']['socket'],busy=True)

    def test_changed_params_phase_and_foreign_socket_refuse(self):
        for field,value in (('method','turn/start'),('observed_turn_id','foreign'),('socket',{}),
                            ('params',dict(self.request['params'],expectedTurnId='foreign'))):
            original=copy.deepcopy(self.request);self.request[field]=value;self.rehash()
            with self.assertRaisesRegex(ContractError,'ORIGINAL_BUSY_INPUT_CHANGED'):self.check()
            self.request.clear();self.request.update(original);self.rehash()


class BusyRuntimeTests(unittest.TestCase):
    def setUp(self):
        from orchestration_test_support import CampaignFixture
        original=CampaignFixture.__init__
        def complete_rows(campaign,*args,**kwargs):
            original(campaign,*args,**kwargs)
            for session in campaign.campaign['sessions'].values():
                session['process']['file_identity']=[1,2,3,4]
            campaign.save()
        with patch.object(CampaignFixture,'__init__',complete_rows):
            self.f=fixture.ServiceTests();self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f=self.f.f;self.runtime=CampaignRuntime(self.f.service)
        # Native startup/adoption are explicit fixtures. This new endpoint is
        # opt-in and can only be enabled with the frozen observer contract.
        source=f.f.control/'controller';source.mkdir()
        write_json(source/'upgrade-binding.json',dict(task_model_network={'schema':OBSERVER_SCHEMA}))
        self.runtime.gate.spec['controller']=str(source)
        self.endpoint=self.runtime.enable_callback_ingress(allow_busy=True);self.addCleanup(self.endpoint.close)
        self.actor=f.f.actor;self.actor.closed=False;self.f.service.normal_completion=None
        self.actor.directory=Path(f.f.reg_ref['path']).parent/'campaign-actor-events'
        self.actor.directory.mkdir()
        self.f.initial['request']=dict(method='turn/steer',observed_turn_id=self.runtime.ledger.active_turn,
            params=dict(threadId=f.f.registration['thread_id'],expectedTurnId=self.runtime.ledger.active_turn,
                input=[dict(type='text',text='callback')]))
        self.sent=[];self.failure=None;self.before=busy.ledger_turn(self.runtime.ledger)
        original=f.client.request
        def request(method,params):
            if method!='turn/steer':return original(method,params)
            self.sent.append(copy.deepcopy(params))
            if self.failure:raise self.failure
            result={'turnId':self.runtime.ledger.active_turn}
            server=f.gate.registration['server']
            peer=dict(schema='upgrade-task-rpc-peer-v1',server=server,
                      peer={k:server[k] for k in ('pid','uid')})
            f.client.last_exchange={
                'intent':write_json(f.f.control/'busy-rpc-intent.json',dict(direction='send_intent',
                    peer=peer,message=dict(id=17,method=method,params=params))),
                'response':write_json(f.f.control/'busy-rpc-response.json',dict(direction='received',
                    peer=peer,message=dict(id=17,result=result)))}
            return result
        for p in (patch.object(f.client,'request',side_effect=request),):
            p.start();self.addCleanup(p.stop)
        self.client=socket.socket(socket.AF_UNIX);self.addCleanup(self.client.close)
        self.client.settimeout(2);self.client.connect(str(self.endpoint.path))

    def send(self):self.client.sendall(canonical(dict(schema=REQUEST_SCHEMA,**self.f.request)))
    def receive(self):
        raw=b''
        while b'\n' not in raw:raw+=self.client.recv(4096)
        return json.loads(raw)

    def test_busy_callback_uses_same_turn_scope_connection_and_one_budget(self):
        self.send()
        result=self.runtime.run(seconds=.2,max_pulses=1,max_new_turns=1)
        response=self.receive()
        self.assertEqual(response['status'],'ENROLLED')
        self.assertEqual(result['model_inputs_sent'],1);self.assertEqual(len(self.sent),1)
        self.assertEqual(busy.ledger_turn(self.runtime.ledger),self.before)
        self.assertFalse(response['result']['operation_scope_changed'])
        self.assertEqual(json_ref(self.endpoint.proof)['schema'],BUSY_SCHEMA)
        self.assertFalse(list(self.actor.directory.glob('*-next-turn-intent.json')))
        self.assertFalse(response['native_user_receipt'])

    def test_unknown_steer_closes_original_runtime_without_idle_fallback(self):
        self.failure=TimeoutError('busy original unknown');self.send()
        with self.assertRaisesRegex(TimeoutError,'busy original unknown'):
            self.runtime.run(seconds=.2,max_pulses=1)
        self.assertEqual(self.receive()['status'],'UNKNOWN_NO_REPLAY')
        self.assertEqual(len(self.sent),1);self.assertTrue(self.runtime.closed)
        with self.assertRaisesRegex(ContractError,'CLOSED_NO_REPLAY'):self.runtime.run()
        self.assertEqual(len(self.sent),1)
        self.assertEqual(len(list(self.actor.directory.glob('callback-steers/*/unknown.json'))),1)

    def test_zero_budget_does_not_accept_busy_request(self):
        self.send();result=self.runtime.run(seconds=.05,max_pulses=1,max_new_turns=0)
        self.assertFalse(result['callback_inputs']);self.assertFalse(self.sent)
        self.assertEqual(self.endpoint.sequence,0)

    def test_same_original_attempt_cannot_steer_twice(self):
        self.send();self.runtime.run(seconds=.2,max_pulses=1);self.receive()
        with socket.socket(socket.AF_UNIX) as again:
            again.settimeout(2);again.connect(str(self.endpoint.path))
            again.sendall(canonical(dict(schema=REQUEST_SCHEMA,**self.f.request)))
            with self.assertRaises(FileExistsError):self.runtime.run(seconds=.2,max_pulses=1)
            response=json.loads(again.recv(32768))
            self.assertEqual(response['status'],'UNKNOWN_NO_REPLAY')
        self.assertEqual(len(self.sent),1);self.assertTrue(self.runtime.closed)

    def test_busy_result_reader_joins_original_source_exchange_and_unchanged_ledger(self):
        from upgrade_task_callback_route import SCHEMA,verify_input
        f=self.f.f;sender=f.base.campaign['sessions']['claude_a'];receiver=f.base.campaign['sessions']['codex']
        pack=dict(completion_callback='callback',completion_receipt=str(f.f.control/'receipt.json'),
                  executor_uuid=sender['surface_uuid'],callback_target=receiver['surface_uuid'])
        pack_ref=write_json(f.f.control/'reader-pack.json',pack)
        attempt_path=f.f.control/'receipt-attempts/attempt-0001.json'
        binding=dict(session_id=receiver['session_id'],task_target=dict(socket=f.gate.anchor['socket']),
            identity=dict(caller_surface_uuid=sender['surface_uuid'],target_surface_uuid=receiver['surface_uuid']))
        request=dict(self.f.initial['request'],observed_status='active',socket=f.gate.anchor['socket'],
            task_callback_route=dict(schema=SCHEMA,ingress=self.endpoint.proof,pack=pack_ref,attempt=str(attempt_path)))
        attempt=dict(phase='NATIVE_INPUT_INTENT',native_binding=binding,binding={'fixture':'callback'},
                     events=[dict(phase='NATIVE_INPUT_INTENT',native_request=request)])
        attempt_ref=write_json(attempt_path,attempt)
        native=dict(sender['process'],ppid=1);tool=dict(native,pid=native['pid']+1000,ppid=native['pid'])
        self.f.request=dict(pack=pack_ref,attempt=attempt_ref)
        self.f.initial.update(pack=pack_ref,attempt=attempt_ref,request=request,text='callback',
            caller=dict(peer=dict(pid=tool['pid'],uid=__import__('os').getuid()),ancestry=[tool,native]))
        self.send();result=self.runtime.run(seconds=.2,max_pulses=1);self.receive()
        with patch('upgrade_task_callback_contract.historical_descriptor',return_value=(
                self.endpoint.proof,json_ref(self.endpoint.proof))):
            ref=verify_input(binding,request,attempt)
            self.assertEqual(ref,result['callback_inputs'][0]['callback_input'])
            original=file_ref(ref['path']);Path(ref['path']).unlink()
            with self.assertRaises(ContractError):verify_input(binding,request,attempt)
        self.assertEqual(len(self.sent),1)


class BusyObservationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ObservationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        service=self.f.f.service;service.actor.closed=False;service.normal_completion=None
        self.service=service
        self.live=dict(thread_id=service.actor.ledger.registration['thread_id'],
                       latest_turn_id=service.actor.ledger.active_turn,thread_status='active',
                       latest_turn_status='inProgress',observed_input_method='turn/steer')
        p=patch('upgrade_task_thread.observe',side_effect=lambda *a:copy.deepcopy(self.live))
        p.start();self.addCleanup(p.stop)

    def test_busy_original_dispatch_observation_rejects_turn_race(self):
        value=cb.CallbackInput(self.service,object(),self.f.request,busy=True)
        self.assertEqual(value.initial['lane'],'claude_a')
        self.live['thread_status']='idle'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_ACTIVE_TURN_REQUIRED'):value.observe()
        self.assertFalse(self.f.f.inputs)

    def test_busy_observation_cannot_use_idle_turn_enrollment(self):
        value=cb.CallbackInput(self.service,object(),self.f.request,busy=True)
        with self.assertRaisesRegex(ContractError,'BUSY_CANNOT_ENROLL_NEW_TURN'):value.value()
        self.service.terminal=object()
        with self.assertRaisesRegex(ContractError,'ORIGINAL_OWNER_CHANGED'):value.observe()


class BusySenderTests(unittest.TestCase):
    def setUp(self):
        self.f=sender_fixture.SenderTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.endpoint.update(schema=BUSY_SCHEMA,allowed_input_methods=['turn/start','turn/steer'])
        self.f.f.f.thread['status']={'type':'active'}
        self.f.f.f.turns=[dict(id=__import__('test_upgrade_task_target').TURN,status='inProgress')]
        old=self.f.f.input.read_metadata
        def metadata(*a,**k):
            value=old(*a,**k);value['requester_direct_rpc_peer']=False;return value
        p=patch.object(self.f.f.input,'read_metadata',side_effect=metadata);p.start();self.addCleanup(p.stop)

    def test_busy_sender_retains_locks_and_never_uses_direct_rpc(self):
        attempt=self.f.deliver_unconfirmed()
        self.assertEqual(len(self.f.ingress_calls),1);self.assertFalse(self.f.f.inputs)
        request=attempt['events'][0]['native_request']
        self.assertEqual(request['method'],'turn/steer')
        self.assertEqual(request['params']['expectedTurnId'],request['observed_turn_id'])

    def test_foreign_steer_response_is_unknown_and_never_retried(self):
        original=self.f.ingress
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            result['result']['turn_id']='99999999-9999-4999-8999-999999999999'
            return result
        import upgrade_task_callback_sender as sender
        with patch.object(sender.ingress_client,'submit_once',side_effect=changed):
            attempt=self.f.deliver_unconfirmed()
        result=next(e for e in attempt['events'] if e['phase']=='NATIVE_INPUT_RESULT')
        self.assertEqual(result['native_result']['rpc_outcome'],'RPC_ERROR_OR_UNKNOWN')
        self.assertEqual(len(self.f.ingress_calls),1);self.assertFalse(self.f.f.inputs)


if __name__=='__main__':unittest.main()
