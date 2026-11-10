"""Existing actor/Runner driven by original RPC events and late native output."""
import unittest

from orchestration_common import ContractError, utc_now
from upgrade_task_actor import TaskService
from upgrade_task_control import invocation_script
import test_upgrade_task_campaign as fixtures


class ServiceTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.CampaignActorTests();f.binding_mode=True;f.service_mode=True
        f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.client=f.f.client
        self.client.poll_recorded=lambda **kwargs:f.queue.pop(0) if f.queue else None
        self.service=f.service
        f.call('start');f.complete('start');f.call('claim');f.complete('claim')
        f.c.work(f.runner.co('run-01').state()[1]['local'])
        f.call('candidate')

    def complete_candidate(self):
        f=self.f
        message=dict(method='item/completed',params=dict(threadId=f.registration['thread_id'],
            turnId=fixtures.fixtures.TURN,item=dict(id='inner-candidate',type='dynamicToolCall',
                namespace='upgrade_collaboration',tool='execute',arguments={'operation_id':'candidate'},
                status='completed',**f.sent[-1]['result'])))
        f.enqueue(message)
        result=self.service.pulse(max_messages=1)
        self.assertEqual(result['pending_binding_calls'],['inner-candidate'])
        return message

    def terminal(self,status='completed'):
        self.f.enqueue(dict(method='turn/completed',params=dict(threadId=self.f.registration['thread_id'],
            turn=dict(id=fixtures.fixtures.TURN,status=status))))

    def test_late_output_then_already_queued_acceptance_is_bound_before_execution(self):
        f=self.f;message=self.complete_candidate()
        f.native_result('candidate',message)
        f.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call',
            name='exec',call_id='outer-accept',input=invocation_script('accept'),
            internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.fixtures.TURN))))
        f.enqueue(dict(id=999,method='item/tool/call',params=dict(threadId=f.registration['thread_id'],
            turnId=fixtures.fixtures.TURN,callId='inner-accept',namespace='upgrade_collaboration',tool='execute',
            arguments={'operation_id':'accept'})))
        result=self.service.pulse(max_messages=1)
        self.assertEqual(result['pending_binding_calls'],[])
        self.assertEqual(f.runner.status('run-01')['states']['local'],'ACCEPTED')
        self.assertEqual(len(f.sent),4)
        self.assertEqual(f.ledger.inspect('inner-accept')['phase'],'RESPONSE_INTENDED')
        self.assertFalse(result['operation_budget_reset'])

    def test_completed_turn_waits_for_original_result_without_repeating_effect(self):
        f=self.f;message=self.complete_candidate();self.terminal()
        result=self.service.pulse(max_messages=1)
        self.assertTrue(result['terminal_received']);self.assertFalse(f.actor.closed)
        f.native_result('candidate',message)
        result=self.service.pulse()
        self.assertEqual(result['status'],'CLOSED')
        self.assertEqual(f.ledger.resolve_operation('accept')[0]['payload']['action'],'accept')
        self.assertEqual(len(f.sent),3)

    def test_idle_pulse_is_not_task_termination_or_a_new_attempt(self):
        self.complete_candidate()
        result=self.service.pulse(seconds=0.01)
        self.assertEqual(result['status'],'ACTIVE')
        self.assertEqual(result['pending_binding_calls'],['inner-candidate'])
        self.assertEqual(len(self.f.sent),3)
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM calls').fetchone()[0],3)

    def test_partial_native_line_waits_then_same_original_output_binds(self):
        f=self.f;message=self.complete_candidate();path=f.native.reader.path
        before=path.stat().st_size
        f.native_result('candidate',message)
        with path.open('rb') as stream:stream.seek(before);raw=stream.read()
        # Keep the complete inner event, append only part of its outer output.
        first=raw.index(b'\n')+1
        cut=first+(len(raw)-first)//2
        with path.open('r+b') as stream:stream.truncate(before);stream.seek(before);stream.write(raw[:cut])
        result=self.service.pulse(seconds=0.01)
        self.assertEqual(result['pending_binding_calls'],['inner-candidate'])
        self.assertFalse(f.actor.closed)
        with self.assertRaisesRegex(ContractError,'not a record boundary'):f.native.reader.snapshot()
        with path.open('ab') as stream:stream.write(raw[cut:])
        self.terminal()
        self.assertEqual(self.service.pulse()['status'],'CLOSED')
        self.assertEqual(f.ledger.db.execute('SELECT count(*) FROM bindings').fetchone()[0],1)
        self.assertEqual(len(f.sent),3)

    def test_existing_actor_cannot_gain_a_second_service_owner(self):
        with self.assertRaisesRegex(ContractError,'ALREADY_OWNED'):TaskService(self.f.actor)

    def test_partial_frame_timeout_seals_pending_and_cannot_restart_actor(self):
        def fail(**kwargs):raise TimeoutError('original partial frame')
        self.client.poll_recorded=fail
        with self.assertRaisesRegex(TimeoutError,'partial frame'):self.service.pulse()
        self.assertTrue(self.f.actor.closed)
        self.assertEqual(self.f.ledger.inspect('inner-candidate')['phase'],'UNKNOWN')
        with self.assertRaisesRegex(ContractError,'CLOSED'):self.service.pulse()
        self.assertEqual(len(self.f.sent),3)

    def test_interrupted_turn_and_revoked_gate_never_bind_late_result(self):
        self.complete_candidate();self.terminal('interrupted')
        self.assertEqual(self.service.pulse(max_messages=1)['status'],'CLOSED')
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM bindings').fetchone()[0],0)

    def test_revoked_gate_closes_without_replaying_completed_candidate(self):
        message=self.complete_candidate();self.f.native_result('candidate',message)
        def stop(*args):raise ContractError('fixture enrollment expired')
        self.f.actor.gate=stop
        with self.assertRaisesRegex(ContractError,'expired'):self.service.pulse()
        self.assertEqual(self.f.ledger.inspect('inner-candidate')['phase'],'NATIVE_COMPLETED')
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM bindings').fetchone()[0],0)
        self.assertEqual(len(self.f.sent),3)

    def test_consumer_before_source_result_remains_rejected(self):
        f=self.f;message=self.complete_candidate()
        f.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call',
            name='exec',call_id='early-accept',input=invocation_script('accept'),
            internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.fixtures.TURN))))
        f.native_result('candidate',message)
        f.enqueue(dict(id=999,method='item/tool/call',params=dict(threadId=f.registration['thread_id'],
            turnId=fixtures.fixtures.TURN,callId='early-inner',namespace='upgrade_collaboration',tool='execute',
            arguments={'operation_id':'accept'})))
        with self.assertRaisesRegex(ContractError,'PRODUCER_RETURN_AFTER_CONSUMER'):
            self.service.pulse(max_messages=1)
        self.assertEqual(f.runner.status('run-01')['states']['local'],'EVIDENCE_READY')
        self.assertEqual(f.ledger.inspect('early-inner')['phase'],'UNKNOWN')
        self.assertEqual(len(f.sent),3)


if __name__=='__main__':unittest.main()
