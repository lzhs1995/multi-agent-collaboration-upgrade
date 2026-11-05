"""Actor context boundaries, using explicit fake deployment observations only."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from orchestration_common import ContractError,json_ref,object_sha,write_json
from orchestration_campaign import require_live_identity
from upgrade_task_enrollment import TaskCampaignGate,current_identity
import test_upgrade_task_campaign as fixtures


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.CampaignActorTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        if self._testMethodName=='test_claim_producer_reader_binds_exact_original_node_and_invocation':
            f.call('start');f.complete('start');f.call('claim')
        self.campaign=f.c.campaign;self.client=f.f.client
        self.registration=f.registration
        self.spec=dict(stop_path=str(f.control/'STOP'))
        self.runtime=dict(startup=write_json(f.control/'startup.json',self.spec))
        self.campaign['runtime']=write_json(f.control/'runtime.json',self.runtime)
        self.campaign['client_isolation']=write_json(f.control/'adoption.json',{'fixture':True})
        for lane,session in self.campaign['sessions'].items():session['native_config_root']=str(f.control/lane)
        self.anchor=dict(server=self.client.server,journal={k:f.native.session['transcript'][k] for k in ('path','device','inode')})
        self.startup=dict(operations=f.reg_ref,ledger=str(f.ledger.path),registration_sha256=f.ledger.registration_sha256)
        self.session=self.campaign['sessions']['codex']
        self.row=dict(workspace_uuid=self.session['workspace_uuid'],target_surface_uuid=self.session['surface_uuid'],
                      target_pane_uuid=self.session['pane_uuid'],tty=self.session['tty'])
        self.process=dict(self.session['process'],argv=['/fixture/codex','resume',self.registration['thread_id']])
        self.observed=dict(thread_id=self.registration['thread_id'],latest_turn_id=f.ledger.active_turn)
        self.native=SimpleNamespace(_tree_row=lambda *a:copy.deepcopy(self.row),_target_process=lambda *a:copy.deepcopy(self.process),
            _provider=lambda p:'codex',_public_process=lambda p:{k:p[k] for k in self.session['process']},
            private_roots=SimpleNamespace(process_root=lambda *a:self.session['native_config_root']),
            bind_target=self.bound)
        self.validation_calls=[]
        for target,kwargs in (
            ('upgrade_task_campaign.validate_controls',dict(return_value=(f.registration,{k:v['payload'] for k,v in f.registration['operations'].items()}))),
            ('upgrade_task_readiness.task_startup_result',dict(return_value=(None,self.startup,self.anchor))),
            ('orchestration_campaign.validate_campaign',dict(side_effect=lambda ref:self.campaign)),
            ('upgrade_live_control.external_publisher',dict(side_effect=lambda *a:self.validation_calls.append('external'))),
            ('upgrade_live_runtime.validate_adoption',dict(side_effect=lambda *a:self.validation_calls.append('adoption'))),
            ('upgrade_task_thread.observe',dict(side_effect=lambda *a:copy.deepcopy(self.observed))),
            ('orchestration_campaign.controller_modules',dict(return_value=(None,self.native,None,None)))):
            p=patch(target,**kwargs);p.start();self.addCleanup(p.stop)
        f.runner.campaign=self.campaign;f.runner.fixtures=False

    def bound(self,bridge,surface,*args):
        s=next(s for s in self.campaign['sessions'].values() if s['surface_uuid']==surface)
        return dict(**{k:s[k] for k in ('provider','session_id','tty','process','native_config_root','transcript')},
            identity=dict(workspace_uuid=s['workspace_uuid'],target_surface_uuid=s['surface_uuid'],target_pane_uuid=s['pane_uuid']))

    def gate(self):return TaskCampaignGate(self.f.runner,self.client,self.f.ledger)

    def record(self,op='start'):
        message=dict(id=1,method='item/tool/call',params=dict(threadId=self.registration['thread_id'],
            turnId=self.f.ledger.active_turn,callId='inner-'+op,namespace='upgrade_collaboration',tool='execute',arguments={'operation_id':op}))
        ref=self.f.enqueue(message)
        self.f.queue.clear()
        return message,ref

    def next_grant(self):
        f=self.f;turn=str(uuid.uuid4())
        peer=dict(schema='upgrade-task-rpc-peer-v1',server=self.client.server,
            peer={k:self.client.server[k] for k in ('pid','uid')})
        def record(direction,name,message):
            return write_json(f.control/(turn+'-'+name+'.json'),dict(direction=direction,message=message,peer=peer))
        previous=record('received','terminal',dict(method='turn/completed',params=dict(
            threadId=self.registration['thread_id'],turn=dict(id=f.ledger.active_turn,status='completed'))))
        exchange=dict(intent=record('send_intent','start',dict(id=991,method='turn/start',params=dict(
            threadId=self.registration['thread_id'],input=[dict(type='text',text='Next exact task operation')]))),
            response=record('received','result',dict(id=991,result=dict(turn=dict(id=turn)))))
        grant=write_json(f.control/(turn+'-grant.json'),dict(schema='upgrade-task-operation-turn-v1',
            registration_sha256=object_sha(self.registration),authority_ref=self.registration['authority_ref'],
            previous_turn_id=f.ledger.active_turn,turn_id=turn,operation_ids=['claim'],
            previous_completion=previous,turn_start=exchange))
        return grant,previous,turn,exchange

    def test_next_turn_requires_live_observation_and_original_same_connection_exchange(self):
        gate=self.gate();grant,previous,turn,exchange=self.next_grant()
        self.client.last_exchange=exchange
        self.observed['latest_turn_id']=turn
        receipt=gate.enroll_next_turn(grant,previous)
        self.assertEqual(self.f.ledger.active_turn,turn)
        self.assertEqual(json_ref(receipt)['enrollment'],gate.proof)
        self.assertEqual(json_ref(receipt)['grant'],grant)
        self.assertFalse(json_ref(receipt)['operation_budget_reset'])
        message,ref=self.record('claim');gate(self.registration,message,ref)
        with self.assertRaisesRegex(ContractError,'NORMAL_PREVIOUS_TURN_REQUIRED'):
            gate.enroll_next_turn(grant,previous)

    def test_file_grant_stale_turn_and_stop_cannot_append_scope(self):
        gate=self.gate();grant,previous,turn,exchange=self.next_grant()
        old=self.f.ledger.active_turn
        self.client.last_exchange=None
        with self.assertRaisesRegex(ContractError,'ORIGINAL_NEXT_TURN_EXCHANGE_REQUIRED'):
            gate.enroll_next_turn(grant,previous)
        self.client.last_exchange=exchange
        with self.assertRaisesRegex(ContractError,'ACTIVE_THREAD_OR_TURN_CHANGED'):
            gate.enroll_next_turn(grant,previous)
        self.observed['latest_turn_id']=turn
        Path(self.spec['stop_path']).write_text('stop')
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):gate.enroll_next_turn(grant,previous)
        self.assertEqual(self.f.ledger.active_turn,old)
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM turns').fetchone()[0],1)

    def test_post_append_live_failure_does_not_recreate_a_turn(self):
        gate=self.gate();grant,previous,turn,exchange=self.next_grant()
        self.client.last_exchange=exchange;self.observed['latest_turn_id']=turn
        with patch.object(gate,'_observe',side_effect=[self.campaign['sessions'],ContractError('fixture live change')]):
            with self.assertRaisesRegex(ContractError,'fixture live change'):gate.enroll_next_turn(grant,previous)
        self.assertEqual(self.f.ledger.active_turn,turn)
        self.assertFalse((self.f.control/(turn+'-campaign-enrollment.json')).exists())
        with self.assertRaisesRegex(ContractError,'NORMAL_PREVIOUS_TURN_REQUIRED'):
            gate.enroll_next_turn(grant,previous)

    def test_original_production_entry_requires_adoption_and_scoped_context(self):
        gate=self.gate();message,ref=self.record()
        with self.assertRaisesRegex(ContractError,'LIVE_ACTOR_REQUIRED'):require_live_identity(self.campaign)
        gate(self.registration,message,ref)
        with gate.activate():
            actual=require_live_identity(self.campaign)
            self.assertEqual(set(actual),set(self.campaign['sessions']))
            with self.assertRaisesRegex(ContractError,'CONTEXT_UNAVAILABLE'):
                with gate.activate():pass
        with self.assertRaisesRegex(ContractError,'LIVE_ACTOR_REQUIRED'):current_identity(self.campaign)
        self.assertIn('adoption',self.validation_calls)
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM calls').fetchone()[0],0)

    def test_context_cleanup_after_failure_and_foreign_campaign_refusal(self):
        gate=self.gate();gate(self.registration,*self.record())
        with self.assertRaisesRegex(RuntimeError,'fixture interruption'):
            with gate.activate():
                other=copy.deepcopy(self.campaign);other['campaign_id']='foreign'
                with self.assertRaisesRegex(ContractError,'FOREIGN_CAMPAIGN'):current_identity(other)
                raise RuntimeError('fixture interruption')
        with self.assertRaisesRegex(ContractError,'LIVE_ACTOR_REQUIRED'):current_identity(self.campaign)

    def test_stop_wrong_tui_and_foreign_turn_refuse_without_operation(self):
        gate=self.gate();message,ref=self.record()
        self.process['argv'][-1]='foreign'
        with self.assertRaisesRegex(ContractError,'TUI_NOT_ATTACHED'):gate(self.registration,message,ref)
        self.process['argv'][-1]=self.registration['thread_id']
        self.observed['latest_turn_id']='foreign'
        with self.assertRaisesRegex(ContractError,'ACTIVE_THREAD_OR_TURN'):gate(self.registration,message,ref)
        self.observed['latest_turn_id']=self.f.ledger.active_turn
        Path(self.spec['stop_path']).write_text('stop')
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):gate(self.registration,message,ref)
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM calls').fetchone()[0],0)

    def test_actual_adoption_refusal_precedes_enrollment_file(self):
        with patch('upgrade_live_runtime.validate_adoption',side_effect=ContractError('fixture adoption refused')):
            with self.assertRaisesRegex(ContractError,'adoption refused'):self.gate()
        self.assertFalse((self.f.control/'campaign-enrollment.json').exists())

    def test_offline_runner_cannot_be_enrolled_as_live(self):
        self.f.runner.fixtures=True
        with self.assertRaisesRegex(ContractError,'PRODUCTION_OWNER_REQUIRED'):self.gate()

    def test_unbound_callable_cannot_create_production_service(self):
        from upgrade_task_campaign import campaign_task_service
        self.client.poll_recorded=lambda **kw:None
        with self.assertRaisesRegex(ContractError,'LIVE_GATE_REQUIRED'):
            campaign_task_service(self.f.runner,self.client,self.f.ledger,lambda *a:None)
        self.assertFalse((self.f.control/'campaign-actor-events').exists())

    def test_record_mismatch_and_unregistered_operation_cannot_activate(self):
        gate=self.gate();message,ref=self.record()
        changed=copy.deepcopy(message);changed['params']['arguments']['operation_id']='arbitrary'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_RPC_REQUIRED'):gate(self.registration,changed,ref)
        message,ref=self.record('unregistered')
        with self.assertRaisesRegex(ContractError,'OUTSIDE_SCOPE'):gate(self.registration,message,ref)
        with self.assertRaisesRegex(ContractError,'CONTEXT_UNAVAILABLE'):
            with gate.activate():pass

    def test_claim_producer_reader_binds_exact_original_node_and_invocation(self):
        from orchestration_common import json_ref,file_ref
        from orchestration_native import NativeReader
        from upgrade_task_enrollment import claim_provenance,verify_claim_provenance
        gate=self.gate();f=self.f
        # Business operations remain explicitly offline. Only the provenance
        # producer/reader use the mocked live gate; no production run is claimed.
        evidence=json_ref(file_ref(f.directory/'claim.json'))
        message=f.ledger.inspect('inner-claim')['request'];record=evidence['received']
        gate(self.registration,message,record)
        with gate.activate():proof=claim_provenance(self.campaign)
        with patch('orchestration_native.NativeReader',side_effect=lambda s:NativeReader(s,fixtures=True)):
            # Enrollment references the same file path and frozen hash that
            # existed at construction; use that exact reference for replay.
            enrolled=json_ref(proof['enrollment'])
            verify_claim_provenance(self.campaign,enrolled['campaign'],proof,run_id='run-01',node_id='local')
            with self.assertRaisesRegex(ContractError,'ORIGINAL_INVOCATION_CHANGED'):
                verify_claim_provenance(self.campaign,enrolled['campaign'],proof,run_id='run-01',node_id='other')


if __name__=='__main__':unittest.main()
