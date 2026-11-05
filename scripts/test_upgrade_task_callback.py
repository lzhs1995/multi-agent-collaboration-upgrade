"""Original .84 receipt/fence/lock tests with explicitly mocked live enrollment.

Uses local socket, native journal, original controller validation/publication.
No actual client, model, production adoption or business run is claimed.
"""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, utc_now, write_json
from orchestration_native import NativeReader, delivery_identity
import upgrade_task_callback as callback
import test_upgrade_task_target as targets


class SettlementTests(unittest.TestCase):
    def setUp(self):
        f = targets.TargetTests(); f.setUp(); self.addCleanup(f.doCleanups); self.f = f
        self.root = f.f.root; self.text = 'exact callback body'
        f.f.thread['status']['type'] = 'active'; f.f.turns[0]['status'] = 'inProgress'
        self.bound = f.bind(); self.receipt = self.root/'callback.json'
        self.journal = self.root/'callback-attempts'; self.journal.mkdir()
        self.lock = self.journal/'delivery.lock'; self.lock.touch()
        report = self.root/'report.md'; report.write_text('original complete report')
        gate = write_json(self.root/'identity-gate.json',dict(status='PASS',task_id='original-task',
            workspace_uuid=targets.WS,supervisor_surface_uuid=targets.SURFACE))
        self.pack = dict(task_id='original-task',completion_nonce='original-nonce',
            completion_callback=self.text,callback_target=targets.SURFACE,executor_uuid=targets.SENDER,
            completion_receipt=str(self.receipt),report=str(report),identity_gate=gate['path'],
            finalized_at='2026-01-01T00:00:00Z')
        self.pack_ref = write_json(self.root/'pack.json',self.pack)
        self.attempt_path = self.journal/'attempt-0001.json'
        request = f.input.prepare(f.RPC(),self.bound,self.text)
        with patch.dict(sys.modules,cmux_codex_input=f.input):
            fields = f.native.input_event_fields(self.bound,self.text,'NATIVE_INPUT_INTENT',dict(native_request=request))
        import time
        event = dict(phase='NATIVE_INPUT_INTENT',at_epoch=time.time(),**fields)
        self.attempt = dict(phase='UNKNOWN',native_binding=self.bound,events=[event],binding=dict(
            task_id=self.pack['task_id'],completion_nonce=self.pack['completion_nonce'],
            completion_callback=self.text,callback_target=targets.SURFACE,task_pack_sha256=self.pack_ref['sha256'],
            report=str(report),report_sha256=file_ref(report)['sha256'],report_bytes=report.stat().st_size,
            identity=f.identity))
        write_json(self.attempt_path,self.attempt)
        self.raw = (json.dumps(dict(timestamp=utc_now(),type='response_item',payload=dict(type='message',role='user',
            content=[dict(type='input_text',text=self.text)])))+'\n').encode()
        self.line = len(f.f.path.read_bytes().splitlines()) + 1
        with f.f.path.open('ab') as stream:stream.write(self.raw)
        self.receiver = dict(provider='codex',session_id=targets.SID,process=self.bound['process'],tty=self.bound['tty'],
            workspace_uuid=targets.WS,surface_uuid=targets.SURFACE,pane_uuid=targets.PANE,native_config_root=str(f.f.root),
            transcript={k:self.bound['transcript'][k] for k in ('path','device','inode')},
            private_native=dict(runtime={'fixture':True},adoption={'fixture':True}))
        self.sender = dict(provider='claude',workspace_uuid=targets.WS,surface_uuid=targets.SENDER,pane_uuid='sender-pane')
        import delivery_receipts,cmux_evidence_io
        original_receipts = types.ModuleType('fixture_original_receipts')
        # Original bounded file primitives and no-overwrite publisher.
        exec(compile((targets.SOURCE/'scripts/delivery_receipts.py').read_text(),'original84-receipts','exec'),original_receipts.__dict__)
        self.receipts = original_receipts
        p = patch.dict(sys.modules,delivery_receipts=original_receipts); p.start(); self.addCleanup(p.stop)
        p = patch.dict(sys.modules,cmux_codex_input=f.input); p.start(); self.addCleanup(p.stop)
        self.original = types.ModuleType('fixture_original_callback')
        exec(compile((targets.SOURCE/'scripts/callback_native_evidence.py').read_text(),
            'original84-callback','exec'),self.original.__dict__)
        self.bridge = types.SimpleNamespace(validate_task_pack_contract=lambda path:json_ref(file_ref(path)))
        self.availability = types.SimpleNamespace(require_action=lambda *args:None)
        self.owner = object(); self.proof = dict(schema=callback.SCHEMA,fixture=True)
        self.context = (self.owner,self.proof,self.sender,self.receiver)
        p = patch.object(callback,'_context',return_value=self.context);self.context_check=p.start();self.addCleanup(p.stop)
        p = patch.object(callback,'verify_receiver');self.provenance_check=p.start();self.addCleanup(p.stop)
        runtime=dict(startup=self.bound['task_target']['startup'],state_root=f.binding['state_root'])
        for p in (patch('upgrade_live_runtime.session_authority',return_value=({},runtime,dict(sessions=dict(codex=self.receiver)))),
                  patch('orchestration_native.NativeReader',side_effect=lambda s,**kw:NativeReader(s,fixtures=True)),
                  patch('upgrade_task_readiness.task_startup_result',return_value=(f.result_ref,f.result,f.anchor))):
            p.start();self.addCleanup(p.stop)

    def settle(self):
        return callback.reconcile({},self.pack_ref['path'],self.bridge,self.original,self.receipts,
            self.availability,transcript=str(self.f.f.path),line=self.line)

    def test_original_unknown_attempt_receipt_preserves_lock_and_sends_nothing(self):
        before=file_ref(self.attempt_path);lock=(self.lock.stat().st_dev,self.lock.stat().st_ino)
        with patch.dict(os.environ,CODEX_THREAD_ID='wrong-display-session'):
            result=self.settle()
        self.assertEqual(result['native_evidence']['status'],'EXACT_NATIVE_RECEPTION')
        self.assertEqual(result['receiver_authentication'],callback.SCHEMA)
        self.assertEqual(result['input_operations'],0)
        self.assertEqual(result['receiver_identity'],delivery_identity(self.sender,self.receiver))
        self.assertEqual(file_ref(self.attempt_path),before)
        self.assertEqual((self.lock.stat().st_dev,self.lock.stat().st_ino),lock)
        self.assertEqual(self.context_check.call_count,2)
        self.assertFalse(self.f.inputs)
        with self.assertRaisesRegex(ContractError,'ALREADY_EXISTS'):self.settle()

    def test_original_active_sender_lock_prevents_publication(self):
        with self.lock.open('rb') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):self.settle()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.f.inputs)

    def test_changed_report_and_foreign_body_never_publish(self):
        report=Path(self.pack['report']);report.write_text('changed report')
        with self.assertRaisesRegex(self.receipts.ReceiptError,'binding changed'):self.settle()
        report.write_text('original complete report')
        data=self.f.f.path.read_bytes();self.f.f.path.write_bytes(data[:-len(self.raw)]+self.raw.replace(b'exact callback body',b'foreign callback'))
        with self.assertRaisesRegex(self.receipts.ReceiptError,'exact received user'):self.settle()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.f.inputs)

    def test_changed_live_gate_or_replaced_original_lock_never_publish(self):
        self.context_check.side_effect=[self.context,ContractError('fixture STOP')]
        with self.assertRaisesRegex(ContractError,'STOP'):self.settle()
        self.assertFalse(self.receipt.exists())
        self.context_check.side_effect=None
        def replace(*args):
            self.lock.rename(self.lock.with_suffix('.old'));self.lock.touch()
        self.provenance_check.side_effect=replace
        with self.assertRaisesRegex(ContractError,'ORIGINAL_LOCK_CHANGED'):self.settle()
        self.assertFalse(self.receipt.exists());self.assertFalse(self.f.inputs)

    def test_no_enrolled_context_has_no_receiver_authority(self):
        with patch.object(callback,'_context',wraps=REAL_CONTEXT):
            with self.assertRaisesRegex(ContractError,'LIVE_ACTOR_REQUIRED'):self.settle()
        self.assertFalse(self.receipt.exists())


REAL_CONTEXT=callback._context
REAL_VERIFY_RECEIVER=callback.verify_receiver


class DeliveryAuthorityTests(unittest.TestCase):
    """Actual reader with original .84 fixture settlement, no live deployment."""
    def setUp(self):
        self.f=SettlementTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.receipt=self.f.settle()

    def read(self):
        from orchestration_native import verify_delivery
        bundle=dict(receipt=write_json(self.f.root/'readback.json',self.receipt),
                    attempts=[file_ref(self.f.attempt_path)])
        with patch.object(callback,'verify_receiver',wraps=REAL_VERIFY_RECEIVER):
            return verify_delivery(bundle,self.f.receiver,self.f.sender,self.f.text,
                                   fixtures=True,uuid_semantics=True)

    def strip_authority(self):
        for key in ('receiver_task_actor','receiver_authentication','reconciled_read_only',
                    'native_evidence','receiver_identity','input_operations'):
            self.receipt.pop(key,None)

    def test_removing_receipt_flags_cannot_downgrade_unknown_attempt(self):
        self.strip_authority()
        with self.assertRaisesRegex(ContractError,'RECEIVER_PROOF_REQUIRED'):self.read()

    def test_unknown_attempt_cannot_claim_sender_confirmation_in_receipt(self):
        self.strip_authority();self.receipt['reconciled_read_only']=False
        self.receipt['phase']='CONFIRMED'
        with self.assertRaisesRegex(ContractError,'RECEIVER_PROOF_REQUIRED'):self.read()

    def test_confirmed_phase_without_original_result_cannot_downgrade(self):
        self.strip_authority();self.f.attempt['phase']='CONFIRMED'
        write_json(self.f.attempt_path,self.f.attempt)
        with self.assertRaisesRegex(ContractError,'RECEIVER_PROOF_REQUIRED'):self.read()

    def test_original_sender_result_remains_accepted_without_receiver_proof(self):
        self.strip_authority();self.f.attempt.update(phase='CONFIRMED',result=dict(
            confirmed=True,confirmation_source='native_user_message_v1',
            native_binding=self.f.bound,native_proof=self.receipt['native_proof']))
        write_json(self.f.attempt_path,self.f.attempt)
        self.assertEqual(self.read()['transport_retries'],0)

    def test_sender_result_with_different_native_proof_requires_receiver(self):
        self.strip_authority();self.f.attempt.update(phase='CONFIRMED',result=dict(
            confirmed=True,confirmation_source='native_user_message_v1',
            native_binding=self.f.bound,native_proof=dict(self.receipt['native_proof'],offset=0)))
        write_json(self.f.attempt_path,self.f.attempt)
        with self.assertRaisesRegex(ContractError,'RECEIVER_PROOF_REQUIRED'):self.read()


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        import test_upgrade_task_campaign as campaign_fixtures
        import test_upgrade_task_enrollment as enrollment_fixtures
        class Case(campaign_fixtures.CampaignActorTests):
            failure_mode=True
            failure_boundary='callback_boundary'
        with patch.object(campaign_fixtures,'CampaignActorTests',Case):
            self.base=enrollment_fixtures.EnrollmentTests();self.base.setUp()
        self.addCleanup(self.base.doCleanups);self.f=self.base.f
        f=self.f;self.campaign=self.base.campaign
        self.receiver=self.campaign['sessions']['codex'];self.sender=self.campaign['sessions']['claude_a']
        self.receiver['private_native']=dict(runtime=self.campaign['runtime'],adoption=self.campaign['client_isolation'])
        f.c.save();f.runner.ref=f.c.ref
        self.gate=self.base.gate()
        payload=f.registration['operations']['failure-candidate']['payload']
        path=callback._pack_path(self.campaign,payload)
        pack=dict(task_id='fixture-task',completion_callback='original-callback',executor_uuid=self.sender['surface_uuid'])
        pack_ref=write_json(path,pack)
        from upgrade_task_control import control_request
        descriptor=control_request(f.reg_ref,'failure-candidate',f.directory,operation=f.registration['operations']['failure-candidate'])
        f.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call',
            name='exec',call_id='outer-callback',input=descriptor['script'],
            internal_chat_message_metadata_passthrough=dict(turn_id=f.ledger.active_turn))))
        message,record=self.base.record('failure-candidate')
        self.message,self.record=message,record
        f.ledger.receive(message)
        invocation=f.actor.invocation_recorder(f.ledger,message,record)
        f.ledger.record_native_invocation(message['params']['callId'],invocation)
        self.proof=dict(schema=callback.SCHEMA,enrollment=self.gate.proof,received=record,invocation=invocation,pack=pack_ref)
        self.receipt=dict(receiver_task_actor=self.proof,receiver_authentication=callback.SCHEMA,
            reconciled_read_only=True,input_operations=0,task_pack_sha256=pack_ref['sha256'],
            task_id=pack['task_id'],completion_callback=pack['completion_callback'],
            receiver_identity=delivery_identity(self.sender,self.receiver))
        p=patch.object(callback,'NativeReader',side_effect=lambda s:NativeReader(s,fixtures=True));p.start();self.addCleanup(p.stop)

    def test_original_typed_invocation_reader_and_foreign_proof_refusal(self):
        callback.verify_receiver(self.receipt,self.receiver,self.sender)
        for key,value in (('received',{}),('pack',{}),('enrollment',{})):
            changed=copy.deepcopy(self.receipt);changed['receiver_task_actor'][key]=value
            with self.assertRaises((ContractError,KeyError)):
                callback.verify_receiver(changed,self.receiver,self.sender)
        changed=copy.deepcopy(self.receipt);changed['receiver_identity']['target_surface_uuid']='foreign'
        with self.assertRaisesRegex(ContractError,'PARTICIPANTS_CHANGED'):
            callback.verify_receiver(changed,self.receiver,self.sender)

    def test_missing_explicit_receiver_proof_never_downgrades(self):
        changed=copy.deepcopy(self.receipt);changed.pop('receiver_task_actor')
        with self.assertRaisesRegex(ContractError,'RECEIVER_PROOF_REQUIRED'):
            callback.verify_receiver(changed,self.receiver,self.sender)

    def test_live_context_requires_original_dispatched_pack_and_scoped_operation(self):
        info=dict(mode='delegated_cli',delivery={'original':'fixture'},prepared=dict(pack=self.proof['pack']))
        co=types.SimpleNamespace(_gate=lambda:None,state=lambda:({},dict(local=info)),
            nodes=dict(local=dict(id='local',executor='claude_a')))
        self.gate(self.gate.registration,self.message,self.record)
        with patch.object(self.gate.runner,'co',return_value=co), self.gate.activate():
            owner,proof,sender,receiver=callback._context(self.campaign,self.proof['pack']['path'])
            self.assertIs(owner,self.gate);self.assertEqual(proof,self.proof)
            self.assertEqual(sender,self.sender);self.assertEqual(receiver,self.receiver)
            with self.assertRaisesRegex(ContractError,'SCOPE_CHANGED'):
                callback._context(self.campaign,self.proof['pack']['path']+'.foreign')
            info.pop('delivery')
            with self.assertRaisesRegex(ContractError,'DISPATCH_REQUIRED'):
                callback._context(self.campaign,self.proof['pack']['path'])
            Path(self.base.spec['stop_path']).write_text('STOP')
            with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):
                callback._context(self.campaign,self.proof['pack']['path'])


class TypedEntryTests(unittest.TestCase):
    def test_callback_operation_runs_through_original_actor_budget(self):
        import test_upgrade_task_campaign as fixtures
        original=fixtures.operation_payload
        def payload(campaign,action,run_id,**kwargs):
            if action=='claim':return original(campaign,'callback',run_id,node_id='local')
            return original(campaign,action,run_id,**kwargs)
        with patch.object(fixtures,'operation_payload',side_effect=payload):
            f=fixtures.CampaignActorTests();f.setUp()
        self.addCleanup(f.doCleanups)
        f.call('start');f.complete('start')
        runtime=types.SimpleNamespace(callback=lambda:dict(fixture_only=True,input_operations=0))
        with patch.object(f.runner,'transport',return_value=runtime) as entry:
            result=f.call('claim')
            self.assertEqual(result['phase'],'RESPONSE_INTENDED')
            self.assertEqual(entry.call_count,1)
            self.assertEqual(f.ledger.inspect('inner-claim')['phase'],'RESPONSE_INTENDED')
            with self.assertRaises(ContractError):f.call('claim')
            self.assertEqual(entry.call_count,1)


if __name__=='__main__':unittest.main()
