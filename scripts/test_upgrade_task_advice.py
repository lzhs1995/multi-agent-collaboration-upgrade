"""Real worker/Runner/ledger/typed joins; fake transport and native model only."""
import copy
import json
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,parse_time,tree_manifest,utc_now,write_json
from orchestration_campaign import NO_ADVICE
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture
from upgrade_task_operations import OperationLedger
from upgrade_task_campaign import ADVICE_SCHEMA,KIND,operation_payload,campaign_task_service,validate_controls,ARGUMENTS
from upgrade_task_bindings import SLOT_SCHEMA
from upgrade_task_control import invocation_script
from upgrade_task_advice import RESOLVER,receipt_advice_sha
import test_upgrade_task_startup as fixtures


class AdviceTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.TaskStartupTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.c=CampaignFixture(f.root,version=3,strategies=('omo',),arm='B',unbound=True)
        adapter=Path(self.c.campaign['adapter']['path'])
        shutil.copyfile(Path(__file__).with_name('orchestration_run.py'),adapter/'scripts/orchestration_run.py')
        self.c.campaign['adapter']=tree_manifest(adapter)
        self.native=self.c.clients['codex'];self.control=f.root/'task-advice';self.control.mkdir(mode=0o700)
        self.calls=[];self.reply=dict(answers=dict(work_kind='coding',work_kind_applies=0.9),http_status=200)
        self.failure=None
        self.on=not self._testMethodName.startswith('test_off_')
        transport=self.control/'transport.py'
        transport.write_text('def bind(connection,credential,*,model):\n    return connection, lambda value: value\n')
        from jev_registration import SCHEMA,EXCEPTION
        values=dict(schema=SCHEMA,run_id='run-01',node_id='unbound',arm='advisory-on',
            provider='fixture',model='fixture-model',budget=dict(max_model_requests=3))
        values.update(external_advice_exception=EXCEPTION,transport_source=file_ref(transport),
                      authorization=write_json(self.control/'jev-authority.json',values))
        self.jev=write_json(self.control/'jev-registration.json',values) if self.on else None
        def payload(action,**kwargs):return operation_payload(self.c.campaign,action,'run-01',**kwargs)
        ops=dict(start=dict(kind=KIND,payload=payload('start')),
            advice=dict(kind=KIND,payload=payload('advice',node_id='unbound',advisory_registration=self.jev)))
        slots={}
        if self.on:ops['read']=dict(kind=KIND,payload=payload('read-advice',node_id='unbound'))
        for name,lane in (('choose-codex','codex'),('choose-claude','claude')):
            fields=dict(work_kind='coding',actual_route=lane,action='supervisor_review',
                reason='Fixture model selects the original available lane',advice_sha256='0'*64 if self.on else NO_ADVICE)
            p=payload('publish-decision',node_id='unbound',decision_fields=fields)
            if self.on:
                p['decision_fields']['advice_sha256']=None
                slots[name]=dict(schema=SLOT_SCHEMA,resolver=RESOLVER,
                    source_path=str(self.control/'campaign-results/read.json'),producer_operation='read')
            ops[name]=dict(kind=KIND,payload=p)
        ops['bind']=dict(kind=KIND,payload=payload('bind-unbound',node_id='unbound',
            decision=str(self.c.root/'runs/run-01/decisions/unbound.json')))
        ops['claim']=dict(kind=KIND,payload=payload('claim',node_id='unbound'))
        # v3 requires at least one slot. Off-arm fixtures use an unrelated
        # original marker slot for the separate local node, never an advice read.
        if not self.on:
            ops['candidate']=dict(kind=KIND,payload=payload('candidate',node_id='local'))
            accept=payload('accept',node_id='local',marker='template');accept['marker']=None
            ops['accept']=dict(kind=KIND,payload=accept)
            slots['accept']=dict(schema=SLOT_SCHEMA,resolver='campaign-accept-marker-v1',
                source_path=str(self.c.root/'runs/run-01/nodes/local/candidate.json'),producer_operation='candidate')
        self.registration=dict(schema='upgrade-task-operations-v3',task_id='fixture-advice',controller_id='fixture',
            thread_id=self.native.session['session_id'],turn_id=fixtures.TURN,server=f.client.server,
            authority_ref=self.c.campaign['authorization'],operations=ops,initial_operations=list(ops),
            binding_slots=slots,choice_groups={'route':['choose-codex','choose-claude']})
        self.reg=write_json(self.control/'operations.json',self.registration)
        self.ledger=OperationLedger.create(self.control/'ledger.sqlite',self.registration);self.addCleanup(self.ledger.close)
        self.c.campaign['task_controls']=dict(schema=ADVICE_SCHEMA,registration=self.reg,
            evidence_directory=str(self.control/'campaign-invocations'))
        self.c.save();self.runner=Runner(self.c.ref,fixtures=True)
        self.queue=[];self.sent=[];self.sequence=0
        f.client.receive_recorded=lambda timeout:self.queue.pop(0)
        f.client.poll_recorded=lambda **kwargs:self.queue.pop(0) if self.queue else None
        f.client.respond=self.sent.append
        self.held={'advice':dict(registration=self.jev,connection=self.transport,credential='fixture-only')} if self.on else None
        self.service=campaign_task_service(self.runner,f.client,self.ledger,lambda *a:None,advice_connections=self.held)
        self.actor=self.service.actor
        self.call('start');self.complete('start')

    def transport(self,request):
        self.calls.append(copy.deepcopy(request))
        if self.failure:raise self.failure
        return copy.deepcopy(self.reply)

    def enqueue(self,message):
        self.sequence+=1
        ref=write_json(self.control/f'received-{self.sequence}.json',dict(direction='received',message=message,
            peer=dict(server=self.f.client.server,peer={k:self.f.client.server[k] for k in ('pid','uid')})))
        self.queue.append((message,ref));return ref

    def call(self,name):
        self.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call',
            name='exec',call_id='outer-'+name,input=invocation_script(name),
            internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.TURN))))
        self.enqueue(dict(id=50+self.sequence,method='item/tool/call',params=dict(threadId=self.registration['thread_id'],
            turnId=fixtures.TURN,callId='inner-'+name,namespace='upgrade_collaboration',tool='execute',arguments={'operation_id':name})))
        return self.actor.step()

    def complete(self,name,*,defer=False,change=None):
        item=dict(id='inner-'+name,type='dynamicToolCall',namespace='upgrade_collaboration',tool='execute',
            arguments={'operation_id':name},status='completed',**self.sent[-1]['result'])
        message=dict(method='item/completed',params=dict(threadId=self.registration['thread_id'],turnId=fixtures.TURN,item=item))
        ref=self.enqueue(message);result=self.actor.step()
        if defer:return message,ref,result
        self.native_output(name,message,change=change)
        if isinstance(result.get('artifact_binding_observation'),dict):self.actor.bind_completed(message,ref)
        return message,ref,result

    def native_output(self,name,message,*,change=None):
        item=message['params']['item']
        self.native.append(dict(timestamp=utc_now(),type='event_msg',payload=dict(type='item_completed',
            thread_id=self.registration['thread_id'],turn_id=fixtures.TURN,item=dict(type='DynamicToolCall',
                content_items=item['contentItems'],**{k:item[k] for k in ('id','namespace','tool','arguments','status','success')}))))
        text=item['contentItems'][0]['text']
        if change:text=change(text)
        self.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call_output',
            call_id='outer-'+name,output=[dict(type='input_text',text='Script completed\nWall time 0.1 seconds\nOutput:\n'),
                dict(type='input_text',text=text)],internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.TURN))))

    def produce_read(self):
        self.call('advice');self.complete('advice');self.call('read');self.complete('read')

    def test_on_full_body_choices_publish_bind_original_chain(self):
        self.produce_read()
        sha=receipt_advice_sha(file_ref(self.control/'campaign-results/read.json'))
        for name in ('choose-codex','choose-claude'):
            self.assertEqual(self.ledger.resolve_operation(name)[0]['payload']['decision_fields']['advice_sha256'],sha)
        self.assertEqual(len(self.calls),1)
        self.call('choose-codex');self.complete('choose-codex');self.call('bind');self.complete('bind')
        binding=json_ref(json.loads(self.sent[-1]['result']['contentItems'][0]['text']))
        self.assertEqual(binding['lane'],'codex')
        self.assertTrue(binding['advice']['counted_as_advisory_consumption'])
        self.assertEqual(binding['advice']['read']['kind'],'BODY_READ_OBSERVED')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM bindings').fetchone()[0],2)
        self.call('claim');self.complete('claim')
        self.assertEqual(self.runner.status('run-01')['states']['unbound'],'LOCAL_RUNNING')

    def test_model_alternative_cannot_replace_already_chosen_decision(self):
        self.produce_read();self.call('choose-codex');self.complete('choose-codex')
        before=file_ref(self.c.root/'runs/run-01/decisions/unbound.json')
        with self.assertRaisesRegex(ContractError,'CHOICE_ALREADY_INTENDED_NO_REPLAY'):self.call('choose-claude')
        self.assertEqual(before,file_ref(before['path']))
        self.assertEqual(len(self.calls),1)

    def test_read_without_complete_producer_result_refuses(self):
        self.call('advice');self.complete('advice',defer=True)
        with self.assertRaisesRegex(ContractError,'OUTER_AMBIGUOUS'):self.call('read')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.ledger.inspect('inner-read')['phase'],'UNKNOWN')

    def test_native_read_truncation_cannot_open_decisions(self):
        self.call('advice');self.complete('advice');self.call('read')
        with self.assertRaises(ContractError):self.complete('read',change=lambda text:text[:-8])
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM bindings').fetchone()[0],0)

    def test_decision_before_full_read_and_unknown_choice_do_not_replay(self):
        self.call('advice');self.complete('advice')
        with self.assertRaisesRegex(ContractError,'NOT_BOUND'):self.call('choose-codex')
        self.assertEqual(self.ledger.db.execute('SELECT count(*) FROM bindings').fetchone()[0],0)

    def test_changed_body_is_refused_by_existing_read_binding(self):
        self.produce_read();ref=json_ref(file_ref(self.control/'campaign-results/read.json'))['result']['publication']
        path=Path(ref['path']);path.chmod(0o600);path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(ContractError):self.ledger.resolve_operation('choose-codex')

    def test_transport_unknown_and_auth_stop_preserve_original_worker_budget(self):
        self.failure=TimeoutError('fixture transport outcome unknown')
        with self.assertRaisesRegex(TimeoutError,'outcome unknown'):self.call('advice')
        self.assertEqual(len(self.calls),1);self.assertTrue(self.actor.closed)
        with self.assertRaisesRegex(ContractError,'CLOSED'):self.call('advice')
        self.assertEqual(len(self.calls),1)

    def test_auth_refusal_releases_held_connection_and_key(self):
        self.reply=dict(http_status=403)
        with self.assertRaisesRegex(ContractError,'STOP_AFFECTED'):self.call('advice')
        self.assertIsNone(self.held['advice']['connection']);self.assertIsNone(self.held['advice']['credential'])
        self.assertEqual(len(self.calls),1)

    def assert_substitution_refused_before_worker(self):
        with patch('jev_advice_run.record_advice') as worker:
            with self.assertRaisesRegex(ContractError,'HELD_CAPABILITY_SUBSTITUTED'):
                self.call('advice')
            worker.assert_not_called()
        self.assertFalse(self.calls)
        self.assertFalse((self.control/'campaign-results/advice.json').exists())

    def test_connection_substitution_after_factory_never_enters_worker(self):
        substituted=[]
        self.held['advice']['connection']=lambda request:substituted.append(request)
        self.assert_substitution_refused_before_worker()
        self.assertFalse(substituted)

    def test_credential_substitution_after_factory_never_enters_worker(self):
        self.held['advice']['credential']='different-fixture-only'
        self.assert_substitution_refused_before_worker()

    def test_capability_holder_replacement_never_enters_worker(self):
        self.held['advice']=dict(self.held['advice'])
        self.assert_substitution_refused_before_worker()

    def test_equal_connection_cannot_replace_original_identity(self):
        class EqualConnection:
            def __eq__(self,other):return True
            def __call__(self,request):raise AssertionError('replacement invoked')
        self.held['advice']['connection']=EqualConnection()
        self.assert_substitution_refused_before_worker()

    def test_auth_refusal_clears_original_holder_even_after_mapping_replacement(self):
        from upgrade_task_advice import advice_handler
        original=self.held['advice'];handler=advice_handler(self.runner,self.registration,self.held)
        from jev_advice_run import STOP_REASON
        def refused(*args,**kwargs):
            self.assertIs(kwargs['connection'],original['connection'])
            self.held.clear()
            raise ContractError(STOP_REASON)
        with patch('jev_advice_run.record_advice',side_effect=refused) as worker:
            payload=self.registration['operations']['advice']['payload']
            with self.assertRaisesRegex(ContractError,'STOP_AFFECTED'):handler(payload)
            self.assertIsNone(original['connection']);self.assertIsNone(original['credential'])
            self.held['advice']=dict(registration=self.jev,connection=self.transport,credential='fixture-only')
            with self.assertRaisesRegex(ContractError,'HELD_CAPABILITY_UNAVAILABLE'):handler(payload)
            self.assertEqual(worker.call_count,1)

    def test_off_zero_requests_no_body_and_native_decision_binding(self):
        self.call('advice');self.complete('advice')
        self.call('choose-codex');self.complete('choose-codex');self.call('bind');self.complete('bind')
        self.assertFalse(self.calls)
        self.assertFalse((self.c.root/'runs/run-01/nodes/unbound/advice').exists())
        binding=json_ref(json.loads(self.sent[-1]['result']['contentItems'][0]['text']))
        self.assertEqual(binding['advice']['arm'],'advisory-off')

    def test_off_unknown_dynamic_input_cannot_be_zero_exposure(self):
        self.call('advice');self.complete('advice');self.call('choose-codex');self.complete('choose-codex')
        self.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call',
            name='exec',call_id='unregistered',input=invocation_script('foreign-advice-read'))))
        self.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call_output',
            call_id='unregistered',output='opaque original result')))
        with self.assertRaisesRegex(ContractError,'TYPED_INPUT_UNKNOWN'):self.call('bind')

    def test_off_registration_cannot_declare_advice_read(self):
        changed=copy.deepcopy(self.registration)
        changed['operations']['bad-read']=dict(kind=KIND,payload=operation_payload(
            self.c.campaign,'read-advice','run-01',node_id='unbound'))
        campaign=copy.deepcopy(self.c.campaign)
        campaign['task_controls']['registration']=write_json(self.control/'changed-operations.json',changed)
        with self.assertRaisesRegex(ContractError,'ARM_READ_SCOPE_CHANGED'):
            validate_controls(campaign,fixtures=True,check_startup=False)

    def test_registration_change_after_freeze_never_calls_advisor(self):
        Path(self.jev['path']).write_bytes(Path(self.jev['path']).read_bytes()+b'\n')
        with self.assertRaises(ContractError):self.call('advice')
        self.assertFalse(self.calls)

    def test_no_ambient_connection_or_wrong_capability_can_supply_on_arm(self):
        from upgrade_task_advice import advice_handler
        for held in (None,{},dict(advice=dict(self.held['advice'],registration={})),
                     dict(advice=dict(self.held['advice'],credential=''))):
            with self.assertRaises(ContractError):advice_handler(self.runner,self.registration,held)
        self.assertFalse(self.calls)

    def test_foreign_read_slot_and_legacy_direct_sha_resolver_refuse(self):
        for change in (dict(source_path=str(self.control/'foreign-receipt.json')),
                       dict(resolver='campaign-advice-sha-v1')):
            registration=copy.deepcopy(self.registration)
            registration['binding_slots']['choose-codex'].update(change)
            campaign=copy.deepcopy(self.c.campaign)
            path=self.control/('modified-'+str(len(list(self.control.glob('modified-*'))))+'.json')
            campaign['task_controls']['registration']=write_json(path,registration)
            with self.assertRaises(ContractError):validate_controls(campaign,fixtures=True,check_startup=False)
        self.assertFalse(self.calls)


if __name__=='__main__':unittest.main()
