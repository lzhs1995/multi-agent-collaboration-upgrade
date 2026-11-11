"""Real Unix endpoint, helper files and SQLite; model/identity are fixtures.

These checks prove routing, one-shot budgets and rejection boundaries, not
native client adoption, model consumption or any of the 33 business runs.
"""
import copy
import json
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, canonical, file_ref, json_ref, write_json
from upgrade_task_runtime import CampaignRuntime
from upgrade_task_callback_contract import MESSAGE_SCHEMA
from upgrade_task_message_source import REQUEST_SCHEMA
import upgrade_task_message_source as source
import upgrade_task_message_turn as incoming
import upgrade_task_message_sender as sender
import test_upgrade_task_callback_busy as busy_fixture
import test_upgrade_task_callback_turn as idle_fixture


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.sample=dict(run_id='run-01',request_id='original-message',sender='claude_a',receiver='codex',
            stage='before_business',node_id='review',body='STATUS: original body',
            helper_command='/original/helper send SURFACE --request-id original-message '+repr('STATUS: original body'))
        self.campaign=dict(root=str(self.root),task_controls={},sessions={'claude_a':{'provider':'claude'}})
        self.campaign_ref=write_json(self.root/'campaign.json',self.campaign)
        for p in (patch('upgrade_communication_plan._campaign_pin',return_value=self.campaign_ref),
                  patch('upgrade_communication_plan.campaign_plan',side_effect=lambda c:[copy.deepcopy(self.sample)]),
                  patch('upgrade_communication_plan.resolve_sample',side_effect=lambda c,s:s)):
            p.start();self.addCleanup(p.stop)

    def test_source_retains_original_helper_and_repeated_slot_cannot_spawn_again(self):
        with patch.object(source,'boundary'), patch('upgrade_communication_plan._require_original_sender'), \
             patch.object(source.subprocess,'run',return_value=SimpleNamespace(returncode=75)) as run:
            self.assertEqual(source.send(self.campaign,self.sample,'ordinary'),75)
            argv=run.call_args.args[0]
            self.assertEqual(argv[0:2],['/original/helper','send'])
            self.assertEqual(argv[-1],self.sample['body'])
            ref=file_ref(source.source_path(self.campaign,self.sample))
            self.assertIn(ref['path'],argv);self.assertIn(ref['sha256'],argv)
            self.assertEqual(source.validate_source(ref)[2],self.sample)
            with self.assertRaises(FileExistsError):source.send(self.campaign,self.sample,'ordinary')
            self.assertEqual(run.call_count,1)

    def test_changed_or_supervisor_authored_slot_cannot_borrow_ordinary_route(self):
        self.sample['sender']='codex'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_CLAUDE_REQUIRED'):
            source.sample_value(self.campaign,'ordinary','run-01','original-message')
        self.sample['sender']='claude_a'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_SLOT_REQUIRED'):
            source.sample_value(self.campaign,'ordinary','run-02','original-message')

    def test_missing_original_sender_refuses_before_source_and_subprocess(self):
        with patch.object(source,'boundary'), \
             patch('upgrade_communication_plan._require_original_sender',side_effect=ContractError('wrong live sender')), \
             patch.object(source.subprocess,'run') as run:
            with self.assertRaisesRegex(ContractError,'wrong live sender'):
                source.send(self.campaign,self.sample,'ordinary')
            run.assert_not_called()
            self.assertFalse(source.source_path(self.campaign,self.sample).exists())


class BusyMessageTests(unittest.TestCase):
    def setUp(self):
        original_enable=CampaignRuntime.enable_callback_ingress
        original_write=busy_fixture.write_json
        def write(path,value,*args,**kwargs):
            if Path(path).name=='upgrade-binding.json':
                value=copy.deepcopy(value);value['task_model_network']['registered_messages']=True
            return original_write(path,value,*args,**kwargs)
        def enable(runtime,**kwargs):
            return original_enable(runtime,**kwargs,allow_messages=True)
        self.f=busy_fixture.BusyRuntimeTests()
        with patch.object(busy_fixture,'write_json',side_effect=write), \
             patch.object(CampaignRuntime,'enable_callback_ingress',enable):
            self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f=self.f
        self.request=dict(source=f.f.request['pack'],attempt=f.f.request['attempt'])
        self.initial=copy.deepcopy(f.f.initial)
        self.initial['source']=self.initial.pop('pack')
        p=patch.object(incoming.MessageInput,'observe',side_effect=lambda:copy.deepcopy(self.initial))
        self.observation=p.start();self.addCleanup(p.stop)

    def send(self,value=None):
        self.f.client.sendall(canonical(value or dict(schema=REQUEST_SCHEMA,**self.request)))

    def run_once(self):
        return self.f.runtime.run(seconds=.2,max_pulses=1,max_new_turns=1)

    def test_busy_message_uses_same_owner_turn_ledger_and_separate_source(self):
        from upgrade_task_callback_busy import ledger_turn
        before=ledger_turn(self.f.runtime.ledger)
        self.send();result=self.run_once();response=self.f.receive()
        self.assertEqual(response['schema'],'upgrade-task-message-response-v1')
        self.assertEqual(response['status'],'ENROLLED')
        self.assertEqual(result['model_inputs_sent'],1);self.assertEqual(len(self.f.sent),1)
        self.assertFalse(result['callback_inputs']);self.assertEqual(len(result['message_inputs']),1)
        self.assertEqual(ledger_turn(self.f.runtime.ledger),before)
        self.assertIn('message_input',response['result']);self.assertNotIn('callback_input',response['result'])
        self.assertEqual(json_ref(self.f.endpoint.proof)['schema'],MESSAGE_SCHEMA)
        self.assertEqual(len(list(self.f.actor.directory.glob('message-steers/*/result.json'))),1)
        self.assertFalse(list(self.f.actor.directory.glob('callback-steers/*')))
        self.assertFalse(response['native_user_receipt'])

    def test_invalid_message_is_refused_without_stopping_independent_business(self):
        self.observation.side_effect=ContractError('TASK_MESSAGE_ORIGINAL_CLAUDE_CHANGED')
        self.send();result=self.run_once();response=self.f.receive()
        self.assertEqual(response['status'],'REFUSED_BEFORE_SERVICE')
        self.assertEqual(result['model_inputs_sent'],0)
        self.assertFalse(self.f.runtime.closed);self.assertFalse(self.f.sent)

    def test_arbitrary_text_and_callback_pack_cannot_enter_message_route(self):
        self.send(dict(schema=REQUEST_SCHEMA,**self.request,text='unregistered task'))
        result=self.run_once();response=self.f.receive()
        self.assertEqual(response['status'],'REFUSED_BEFORE_SERVICE')
        self.assertEqual(result['model_inputs_sent'],0);self.assertFalse(self.f.sent)

    def test_ambiguous_rpc_keeps_original_intent_and_no_idle_fallback(self):
        self.f.failure=TimeoutError('original message outcome unknown')
        self.send()
        with self.assertRaisesRegex(TimeoutError,'outcome unknown'):self.run_once()
        self.assertEqual(self.f.receive()['status'],'UNKNOWN_NO_REPLAY')
        self.assertEqual(len(self.f.sent),1);self.assertTrue(self.f.runtime.closed)
        self.assertEqual(len(list(self.f.actor.directory.glob('message-steers/*/unknown.json'))),1)
        with self.assertRaisesRegex(ContractError,'CLOSED_NO_REPLAY'):self.run_once()
        self.assertEqual(len(self.f.sent),1)

    def test_duplicate_original_attempt_never_sends_second_input(self):
        self.send();self.run_once();self.f.receive()
        with socket.socket(socket.AF_UNIX) as again:
            again.settimeout(2);again.connect(str(self.f.endpoint.path))
            again.sendall(canonical(dict(schema=REQUEST_SCHEMA,**self.request)))
            with self.assertRaises(FileExistsError):self.run_once()
            self.assertEqual(json.loads(again.recv(32768))['status'],'UNKNOWN_NO_REPLAY')
        self.assertEqual(len(self.f.sent),1)

    def test_historical_message_joins_registered_body_original_rpc_and_ledger(self):
        from upgrade_task_message_route import verify_input
        from upgrade_communication_observer import expected_intent,original_journal
        from cmux_prompt_reference import persist,original_helper_payload
        f=self.f;gate=f.runtime.gate;campaign=gate.campaign
        claude=campaign['sessions']['claude_a'];codex=campaign['sessions']['codex']
        sample=dict(run_id='run-01',request_id='registered-original',sender='claude_a',receiver='codex',
            sender_surface_uuid=claude['surface_uuid'],body='STATUS: registered ordinary historical sample')
        claimed=dict(schema=source.SCHEMA,campaign=gate.runner.ref,kind='ordinary',sample=sample)
        src=write_json(f.actor.directory/'source-claim.json',claimed)
        state=f.actor.directory/'helper-state'
        with patch('orchestration_contracts.state_root',return_value=state):
            spec=source.helper_request(campaign,sample)
            persist(spec.get('body_reference'),original_helper_payload(spec))
            _,marker,channel=expected_intent(sample,spec['identity'])
            helper_ref=write_json(state/'cmux-agent-adapter-v1/targets'/channel/'intents'/(marker+'.json'),spec)
            journal=original_journal(state,spec['identity'],marker)
            path=journal/'attempt-0001.json'
            binding=dict(schema='native-delivery-task-v1',session_id=codex['session_id'],
                identity=spec['identity'],task_target=dict(socket=gate.anchor['socket']))
            request=dict(method='turn/steer',observed_status='active',observed_turn_id=f.runtime.ledger.active_turn,
                socket=gate.anchor['socket'],params=dict(threadId=codex['session_id'],
                    expectedTurnId=f.runtime.ledger.active_turn,input=[dict(type='text',text=spec['payload'])]),
                task_message_route=dict(schema=source.ROUTE_SCHEMA,ingress=f.endpoint.proof,source=src,attempt=str(path)))
            attempt=dict(phase='NATIVE_INPUT_INTENT',native_binding=binding,binding={'fixture':'ordinary'},
                events=[dict(phase='NATIVE_INPUT_INTENT',native_request=request)])
            ref=write_json(path,attempt)
            native=dict(claude['process'],ppid=1);tool=dict(native,pid=native['pid']+1000,ppid=native['pid'])
            self.request=dict(source=src,attempt=ref)
            self.initial.update(source=src,attempt=ref,request=request,text=spec['payload'],
                kind='ordinary',sample=sample,helper_intent=helper_ref,run_id='run-01',
                caller=dict(peer=dict(pid=tool['pid'],uid=__import__('os').getuid()),ancestry=[tool,native]))
            self.send();result=self.run_once();f.receive()
            with patch('upgrade_task_message_route.validate_source',return_value=(claimed,campaign,sample)), \
                 patch('upgrade_task_message_route.campaign_state',return_value=state), \
                 patch('upgrade_task_message_contract.historical_descriptor',return_value=(f.endpoint.proof,json_ref(f.endpoint.proof))):
                actual=verify_input(binding,request,attempt)
                self.assertEqual(actual,result['message_inputs'][0]['message_input'])
                proof=json_ref(actual);Path(proof['exchange']['response']['path']).unlink()
                with self.assertRaises(ContractError):verify_input(binding,request,attempt)
        self.assertEqual(len(f.sent),1)


class IdleMessageTests(unittest.TestCase):
    def setUp(self):
        self.f=idle_fixture.ServiceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.initial=copy.deepcopy(self.f.initial)
        self.initial['source']=self.initial.pop('pack')
        self.request=dict(source=self.f.pack,attempt=self.f.attempt)
        p=patch.object(incoming.MessageInput,'observe',side_effect=lambda:copy.deepcopy(self.initial))
        p.start();self.addCleanup(p.stop)

    def test_idle_message_grants_only_remaining_original_operations(self):
        ledger=self.f.f.f.ledger;before=ledger.identity
        result=incoming.deliver(self.f.service,object(),self.request)
        self.assertEqual(result['model_inputs_sent'],1);self.assertEqual(ledger.identity,before)
        self.assertEqual(self.f.f.inputs,[self.initial['request']['params']])
        value=json_ref(result['message_input']);plan=json_ref(value['plan'])
        self.assertEqual(plan['schema'],'upgrade-task-message-input-plan-v1')
        self.assertEqual(plan['message'],value['source']);self.assertNotIn('callback',plan)
        self.assertFalse(value['native_user_receipt']);self.assertFalse(value['model_consumption'])

    def test_file_plan_alone_cannot_acquire_message_input_authority(self):
        value=incoming.MessageInput(self.f.service,object(),self.request)
        plan=value.publish()
        with self.assertRaisesRegex(ContractError,'LIVE_INPUT_CONTEXT_REQUIRED'):
            self.f.service.start_next_turn(plan)
        self.assertFalse(self.f.f.inputs)

    def test_idle_historical_message_requires_actual_transition_and_original_ledger(self):
        from upgrade_task_message_route import verify_input
        from upgrade_task_message_wire import helper_intent_path,original_journal
        from orchestration_common import object_sha
        f=self.f.f;gate=f.gate;campaign=gate.campaign
        # The original process rows in this fixture omit file identity; it is
        # supplied explicitly here, not inferred from a live native client.
        campaign=copy.deepcopy(campaign)
        for row in campaign['sessions'].values():row['process']['file_identity']=[1,2,3,4]
        campaign_ref=write_json(f.f.control/'historical-campaign.json',campaign)
        parent=Path(f.f.reg_ref['path']).parent;directory=parent/'campaign-actor-events'
        directory.mkdir();f.f.actor.directory=directory
        endpoint=dict(registration=f.f.reg_ref,enrollment=gate.proof,campaign=campaign_ref,owner=dict(pid=__import__('os').getpid()))
        endpoint_ref=write_json(parent/'message-descriptor.json',endpoint)
        claude=campaign['sessions']['claude_a'];codex=campaign['sessions']['codex']
        sample=dict(run_id='run-01',request_id='idle-original',sender='claude_a',receiver='codex',
            sender_surface_uuid=claude['surface_uuid'],body='STATUS: original idle message')
        state=directory/'message-state'
        with patch('orchestration_contracts.state_root',return_value=state):spec=source.helper_request(campaign,sample)
        helper_ref=write_json(helper_intent_path(state,spec),spec)
        claimed=dict(schema=source.SCHEMA,campaign=campaign_ref,kind='ordinary',sample=sample)
        src=write_json(directory/'message-claim.json',claimed)
        path=original_journal(state,spec['identity'],spec['marker'])/'attempt-0001.json'
        binding=dict(schema='native-delivery-task-v1',session_id=codex['session_id'],
            task_target=dict(socket=gate.anchor['socket']),identity=spec['identity'])
        request=dict(method='turn/start',observed_status='idle',observed_turn_id=f.old,socket=gate.anchor['socket'],
            params=dict(threadId=codex['session_id'],input=[dict(type='text',text=spec['payload'])]),
            task_message_route=dict(schema=source.ROUTE_SCHEMA,ingress=endpoint_ref,source=src,attempt=str(path)))
        attempt=dict(phase='NATIVE_INPUT_INTENT',native_binding=binding,binding={'fixture':'ordinary'},
            events=[dict(phase='NATIVE_INPUT_INTENT',native_request=request)])
        ref=write_json(path,attempt);native=dict(claude['process'],ppid=1)
        tool=dict(native,pid=native['pid']+1000,ppid=native['pid'])
        self.initial.update(source=src,attempt=ref,request=request,text=spec['payload'],kind='ordinary',
            sample=sample,helper_intent=helper_ref,run_id='run-01',
            caller=dict(peer=dict(pid=tool['pid'],uid=__import__('os').getuid()),ancestry=[tool,native]))
        with patch.object(gate.runner,'ref',campaign_ref):
            result=incoming.deliver(self.f.service,object(),dict(source=src,attempt=ref))
        with patch('upgrade_task_message_route.validate_source',return_value=(claimed,campaign,sample)), \
             patch('upgrade_task_message_route.campaign_state',return_value=state), \
             patch('upgrade_task_message_contract.historical_descriptor',return_value=(endpoint_ref,endpoint)):
            self.assertEqual(verify_input(binding,request,attempt),result['message_input'])
            f.f.ledger.db.execute('DELETE FROM turns WHERE turn_id=?',(f.turn,))
            with self.assertRaisesRegex(ContractError,'ORIGINAL_TURN_NOT_ENROLLED'):
                verify_input(binding,request,attempt)
        self.assertEqual(len(f.inputs),1)


class NetworkTests(unittest.TestCase):
    def test_message_permission_is_explicit_and_does_not_add_network_destinations(self):
        from upgrade_task_network import network_contract,model_only_profile
        root=Path(tempfile.mkdtemp(prefix='msg-',dir='/tmp')).resolve()
        self.addCleanup(__import__('shutil').rmtree,root)
        station={'codex':{'base_url':'http://127.0.0.1:3465'}}
        claude={'env':{'ANTHROPIC_BASE_URL':'http://127.0.0.1:3445'}}
        old=network_contract(station,claude,evidence_root=root,observer=True)
        new=network_contract(station,claude,evidence_root=root,observer=True,messages=True)
        self.assertNotIn('registered_messages',old);self.assertIs(new['registered_messages'],True)
        for lane in ('codex','claude_a','claude_b'):
            self.assertEqual(model_only_profile(b'(version 1)\n',old,lane),
                             model_only_profile(b'(version 1)\n',new,lane))
        with self.assertRaises(ContractError):network_contract(station,claude,messages=True)

    def test_no_helper_scope_cannot_call_registered_route(self):
        with self.assertRaisesRegex(ContractError,'LIVE_SCOPE_REQUIRED'):
            sender.route_request({},dict(method='turn/start'),'arbitrary body')


if __name__=='__main__':unittest.main()
