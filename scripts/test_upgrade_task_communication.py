"""Real actor, ledger, helper locks/files and source joins; model/CLI fixtures.

The bridge's input and receipt are explicit fixtures. These tests do not claim
live cmux delivery, TASK ordinary ingress or actual model consumption.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import copy
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, digest, file_ref, json_ref, utc_now, write_json
from orchestration_native import NativeReader
from orchestration_transport import PinnedRuntime
from upgrade_task_campaign import KIND, operation_payload
import upgrade_task_communication as communication
import upgrade_task_message_send as sending
import upgrade_task_bridge_context as adapter
import upgrade_task_sender as sender
import cmux_agent_adapter as helper_module
import cmux_prompt_reference as reference
import test_upgrade_task_campaign as fixture
from test_orchestration_transport import FakeRuntime


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        f = fixture.CampaignActorTests(); self.f = f; self.addCleanup(f.doCleanups)
        self.samples = [dict(run_id='run-01', node_id='review', request_id='ordinary-'+stage,
            sender='codex' if stage in ('after_dispatch','before_accept') else 'claude_a',
            receiver='claude_a' if stage in ('after_dispatch','before_accept') else 'codex',
            stage=stage, body='STATUS: original ordinary sample '+stage)
            for stage in ('after_dispatch','before_accept','before_business')]
        self.by_id = {s['request_id']: s for s in self.samples}
        def extend(operations):
            for s in self.samples:
                action = 'communication-send' if s['sender']=='codex' else 'communication-read'
                operations[s['stage']] = dict(kind=KIND, payload=operation_payload(f.c.campaign, action,
                    'run-01', node_id='review', communication_request=s['request_id']))
        self.patch('upgrade_task_communication.samples', return_value=self.samples)
        self.patch('upgrade_task_communication.original_sample',
                   side_effect=lambda campaign,payload: copy.deepcopy(self.by_id[payload['communication_request']]))
        f.transport_node='review'; f.service_mode=True; f.transport_actions=('prepare','dispatch','callback')
        f.extend_operations=extend; f.setUp()
        f.call('start'); f.complete('start')
        self.fake=FakeRuntime(f.c,f.runner.co('run-01'),node_id='review'); f.runner.runtime=self.fake
        self.patch('orchestration_transport.handshake_journal',return_value=self.fake.handshake_root)
        self.patch('orchestration_transport.dispatch_journal',return_value=self.fake.dispatch_root)
        f.call('claim'); f.complete('claim'); f.call('prepare'); f.complete('prepare')
        f.call('dispatch'); f.complete('dispatch')
        self.state=f.f.root/'ordinary-state'
        self.patch('orchestration_contracts.state_root',return_value=self.state)
        self.patch('cmux_prompt_reference.body_root',return_value=self.state/'message-bodies-v1')
        actual_helper=helper_module.Adapter(home=f.f.root)
        actual_helper.root=self.state/'cmux-agent-adapter-v1'
        self.helper=actual_helper
        self.runtime=PinnedRuntime(f.c.campaign)
        self.guard=SimpleNamespace(__file__=str(self.runtime.scripts/'cmux_workspace_guard.py'))
        self.adapt=SimpleNamespace(__file__=str(self.runtime.scripts/'mac_upgrade_task_bridge_context.py'),activate=adapter.activate)
        enrollment=dict(schema='upgrade-task-campaign-enrollment-v1', campaign=f.runner.ref,
            registration=f.reg_ref, authority=f.c.campaign['authorization'], runtime=f.c.campaign['runtime'],
            adoption=f.c.campaign['client_isolation'],controller_pid=os.getpid(),
            ledger=dict(path=str(f.ledger.path),device=f.ledger.identity[0],inode=f.ledger.identity[1]))
        self.owner=SimpleNamespace(runner=f.runner,campaign=f.c.campaign,ledger=f.ledger,
            operations={k:v['payload'] for k,v in f.registration['operations'].items()},
            proof=write_json(f.control/'campaign-enrollment.json',enrollment))
        def context(runner,payload,sample,request):
            name=sample['stage']; value=json_ref(file_ref(f.directory/(name+'.json')))
            call=f.ledger.inspect('inner-'+name)['request']
            return sending._scope(runner,payload,sample,request,self.owner,call,value['received'])
        self.patch('upgrade_task_message_send._context',side_effect=context)
        self.patch('orchestration_native.NativeReader',side_effect=lambda s,**k:NativeReader(s,fixtures=True))
        self.patch('upgrade_task_sender._task_caller',return_value=True)
        self.patch('upgrade_task_bridge.observe',side_effect=lambda runtime,guard,reader:
            dict(identity_source=adapter.SOURCE,foreground_authentication=False,task_actor=reader()))
        @contextmanager
        def modules(runtime,*names):
            self.assertEqual(names,('cmux_workspace_guard','mac_upgrade_task_bridge_context','cmux_agent_adapter'))
            yield self.guard,self.adapt,SimpleNamespace(Adapter=lambda:self.helper)
        self.patch('orchestration_transport.PinnedRuntime.modules',modules)
        receiver=f.c.campaign['sessions']['claude_a']; supervisor=f.c.campaign['sessions']['codex']
        self.identity=dict(workspace_uuid=receiver['workspace_uuid'],caller_surface_uuid=supervisor['surface_uuid'],
            target_surface_uuid=receiver['surface_uuid'],target_pane_uuid=receiver['pane_uuid'])
        self.identity={k:v.lower() for k,v in self.identity.items()}
        row=dict(surface_id=receiver['surface_uuid'],workspace_id=receiver['workspace_uuid'],surface_type='terminal',ref='surface:42')
        self.patch('cmux_agent_adapter.bridge.pin_workspace',return_value=self.identity)
        self.patch('cmux_agent_adapter.bridge.list_surfaces',return_value=[row])
        self.receipt=None;self.sends=[];self.saved=[];self.binding=None
        self.patch('cmux_agent_adapter.verified_receipt',side_effect=lambda *a:self.receipt)
        self.patch('cmux_agent_adapter.bridge.submit_text',side_effect=self.submit)

    def patch(self,*a,**kw):
        p=patch(*a,**kw);result=p.start();self.addCleanup(p.stop);return result

    def submit(self,surface,text,**kwargs):
        self.sends.append((surface,text,kwargs));self.saved.append(copy_context())
        proof=sending.current_scope();self.assertEqual(adapter.snapshot()['task_actor'],proof)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(ContractError,'OWNER_CHANGED_OR_CLOSED'):
                pool.submit(copy_context().run,sending.current_scope).result(timeout=5)
        channel=self.helper.channel(self.identity)
        self.assertTrue((channel/'pending.json').is_file())
        self.assertTrue((channel/'intents'/(kwargs['marker']+'.json')).is_file())
        for path in [channel/'delivery.lock',*self.helper.legacy('surface:42',surface.lower())]:
            with path.open('r+') as lock:
                with self.assertRaises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if getattr(self,'fail_send',False):raise OSError('explicit fixture UNKNOWN after input')
        self.binding=dict(identity=self.identity,payload_sha256=digest(text.encode()),bound_at_epoch=__import__('time').time())
        sender.bind(self.binding,dict(identity_source=adapter.SOURCE,task_actor=proof))
        sender.require_live(self.binding)
        self.receipt=dict(source='revalidated_message_dispatch_v1',identity=self.identity,
                          attempt='explicit-fixture-attempt',receipt='explicit-fixture-receipt')
        return dict(confirmed=True)

    def test_one_model_operation_uses_original_helper_locks_and_historical_sender_source(self):
        f=self.f;f.call('after_dispatch');f.complete('after_dispatch')
        result=json.loads(f.sent[-1]['result']['contentItems'][0]['text'])
        self.assertTrue(result['helper_result']['confirmed']);self.assertFalse(result['business_accepted'])
        self.assertEqual(len(self.sends),1)
        from upgrade_task_sender_evidence import verify_source
        verify_source(self.binding,f.c.campaign['sessions']['codex'],f.c.campaign['sessions']['claude_a'],fixtures=True)
        with self.assertRaisesRegex(ContractError,'OWNER_CHANGED_OR_CLOSED'):
            self.saved[0].run(sending.current_scope)
        with self.assertRaises(ContractError):f.call('after_dispatch')
        self.assertEqual(len(self.sends),1)

    def test_unknown_preserves_helper_pending_and_consumed_model_attempt(self):
        self.fail_send=True
        self.f.call('after_dispatch');self.f.complete('after_dispatch')
        result=json.loads(self.f.sent[-1]['result']['contentItems'][0]['text'])
        self.assertEqual(result['status'],'UNCONFIRMED_NO_REPLAY');self.assertFalse(result['native_user_receipt'])
        self.assertFalse(self.f.actor.closed)
        self.assertEqual(len(self.sends),1)
        pending=self.helper.channel(self.identity)/'pending.json';pin=file_ref(pending)
        self.assertEqual(self.f.ledger.inspect('inner-after_dispatch')['phase'],'NATIVE_COMPLETED')
        with self.assertRaises(ContractError):self.f.call('after_dispatch')
        self.assertEqual(len(self.sends),1);self.assertEqual(file_ref(pending),pin)

    def test_before_accept_without_callback_refuses_before_helper(self):
        self.f.call('before_accept');self.f.complete('before_accept')
        result=json.loads(self.f.sent[-1]['result']['contentItems'][0]['text'])
        self.assertEqual(result['status'],'UNCONFIRMED_NO_REPLAY');self.assertFalse(self.f.actor.closed)
        self.assertFalse(self.sends)
        self.assertFalse(self.helper.channel(self.identity).exists())

    def test_before_accept_after_original_callback_result_can_send_once(self):
        f=self.f;co=f.runner.co('run-01');info=co.state()[1]['review']
        f.c.work(info,'claude_a');f.c.callback(co,'review')
        f.call('callback');f.complete('callback')
        f.call('before_accept');f.complete('before_accept')
        self.assertEqual(len(self.sends),1)
        self.assertEqual(f.ledger.inspect('inner-before_accept')['phase'],'NATIVE_COMPLETED')

    def test_sender_attribution_scans_original_typed_invocation_and_detects_duplicates(self):
        f=self.f;f.call('after_dispatch');f.complete('after_dispatch')
        from upgrade_communication_attribution import _scan
        sample=dict(self.samples[0], command=communication.control(f.c.campaign,'communication-send',self.samples[0]['request_id']))
        readers={k:NativeReader(v,fixtures=True) for k,v in f.c.campaign['sessions'].items()}
        start={k:r.verify_pin(f.runner.co('run-01').header['native_start'][k]) for k,r in readers.items()}
        def scan():
            item=dict(sample=sample,payload=self.sends[0][1],calls=[],notices=[])
            _scan([item],readers,start,{k:r.snapshot()['offset'] for k,r in readers.items()})
            return item
        item=scan();self.assertEqual(len(item['calls']),1)
        self.assertEqual(item['calls'][0][0],item['typed_call'])
        self.assertEqual(item['call_id_counts'][item['calls'][0][1]],1)
        original=f.native.reader.record(item['typed_call']);original['timestamp']=None
        f.native.append(original)
        item=scan();self.assertEqual(len(item['calls']),2)
        self.assertEqual(item['call_id_counts'][item['calls'][0][1]],2)

    def test_original_wire_or_sender_cannot_change(self):
        self.f.call('after_dispatch');self.f.complete('after_dispatch')
        from upgrade_task_sender_evidence import verify_source
        for change in ('payload','lane'):
            binding=copy.deepcopy(self.binding)
            proof=json_ref(binding['task_sender']['task_actor'])
            if change=='payload':proof['helper_request']['payload']+=' extra'
            else:proof['lane']='claude_b'
            binding['task_sender']['task_actor']=write_json(self.f.f.root/(change+'.json'),proof)
            with self.assertRaises(ContractError):
                verify_source(binding,self.f.c.campaign['sessions']['codex'],self.f.c.campaign['sessions']['claude_a'],fixtures=True)

    def prepare_read(self):
        f=self.f;sample=self.samples[-1]
        full='[CMUX-AGENT][delivery:ORIGINAL_READ_FIXTURE]\n'+sample['body']
        wire=reference.plan(full,'ORIGINAL_READ_FIXTURE',root=self.state/'message-bodies-v1')
        reference.persist(wire['reference'],full)
        intent=write_json(f.f.root/'read-intent.json',dict(payload=wire['text']))
        original=dict(request_id=sample['request_id'],intent=intent,explicit_native_arrival_fixture=True)
        self.patch('upgrade_communication_attribution.attribute_samples',return_value=dict(inputs=[original],unresolved=[]))
        f.call('before_business')
        return wire, sample

    def test_full_body_read_returns_original_and_native_complete_output_qualifies(self):
        wire,sample=self.prepare_read();f=self.f;f.complete('before_business')
        result=json.loads(f.sent[-1]['result']['contentItems'][0]['text'])
        self.assertTrue(result['body_read']['body'].endswith(sample['body']))
        command=communication.control(f.c.campaign,'communication-read',sample['request_id'])
        reader=NativeReader(f.c.campaign['sessions']['codex'],fixtures=True)
        evidence=communication.typed_read_evidence(reader,fence=f.runner.co('run-01').header['native_start']['codex'],
            until=reader.snapshot()['offset'],notice=wire['text'],command=command,not_before=0,
            not_after=__import__('time').time()+1,request_id=sample['request_id'])
        self.assertEqual(len(evidence),1);self.assertFalse(self.sends)

    def test_partial_or_missing_native_body_output_never_qualifies(self):
        wire,sample=self.prepare_read();f=self.f
        message,_,_=f.complete('before_business',defer_outer=True)
        command=communication.control(f.c.campaign,'communication-read',sample['request_id'])
        reader=NativeReader(f.c.campaign['sessions']['codex'],fixtures=True)
        def evidence():
            return communication.typed_read_evidence(reader,fence=f.runner.co('run-01').header['native_start']['codex'],
                until=reader.snapshot()['offset'],notice=wire['text'],command=command,not_before=0,
                not_after=__import__('time').time()+1,request_id=sample['request_id'])
        self.assertEqual(evidence(),[])
        append=f.native.append
        def truncated(record):
            if record.get('payload',{}).get('type')=='custom_tool_call_output':
                record=copy.deepcopy(record);record['payload']['output'][-1]['text']=record['payload']['output'][-1]['text'][:-8]
            return append(record)
        with patch.object(f.native,'append',side_effect=truncated):f.native_result('before_business',message)
        with self.assertRaises(ContractError):evidence()


class CallerTests(unittest.TestCase):
    def test_files_alone_cannot_activate_an_outgoing_operation(self):
        with self.assertRaisesRegex(ContractError,'LIVE_ACTOR_REQUIRED'):
            sending._context(None,None,None,None)


if __name__=='__main__':unittest.main()
