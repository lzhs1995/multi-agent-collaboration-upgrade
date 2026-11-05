"""Retained runtime with actual service/SQLite and explicit deployment/RPC mocks."""
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref
from upgrade_task_runtime import CampaignRuntime
import test_upgrade_task_instructions as fixtures


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        name=('test_consumed_operation_is_not_renewed_by_plan'
              if self._testMethodName=='test_exhausted_scope_never_claims_business_acceptance'
              else 'test_published_typed_plan_joins_original_one_input_and_same_ledger')
        f=fixtures.InstructionTests(name);f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.service=f.service;self.actor=f.service.actor
        if self._testMethodName=='test_exhausted_scope_never_claims_business_acceptance':
            self.actor.scope=frozenset(['start'])
        self.runtime=CampaignRuntime(self.service)

    def run_runtime(self,**kwargs):
        return self.runtime.run(seconds=0.1,max_pulses=1,**kwargs)

    def test_same_owner_drives_one_input_then_idle_without_reconnect(self):
        old_ledger=self.f.f.ledger.identity
        first=self.run_runtime()
        self.assertEqual(first['status'],'YIELDED')
        self.assertEqual(first['model_inputs_sent'],1)
        self.assertEqual(len(self.f.inputs),1)
        self.assertIs(self.runtime.client,self.f.client)
        self.assertEqual(self.f.f.ledger.identity,old_ledger)
        later=self.run_runtime()
        self.assertEqual(later['model_inputs_sent'],0)
        self.assertEqual(later['reconnects'],0)
        self.assertFalse(later['business_accepted'])
        self.assertFalse(self.runtime.closed)
        self.assertEqual(len(self.f.inputs),1)

    def test_existing_service_cannot_gain_second_runtime_owner(self):
        with self.assertRaisesRegex(ContractError,'ALREADY_OWNED'):CampaignRuntime(self.service)
        self.assertFalse(self.f.inputs)

    def test_unknown_send_closes_runtime_and_never_replays(self):
        self.f.failure=TimeoutError('original runtime input unknown')
        with self.assertRaisesRegex(TimeoutError,'input unknown'):self.run_runtime()
        self.assertEqual(len(self.f.inputs),1)
        self.assertTrue(self.runtime.closed)
        with self.assertRaisesRegex(ContractError,'CLOSED_NO_REPLAY'):self.run_runtime()
        self.assertEqual(len(self.f.inputs),1)
        self.assertEqual(json_ref(file_ref(
            self.runtime.root/'000001-unknown.json'))['status'],'UNKNOWN_NO_REPLAY')

    def test_stop_and_replaced_owner_refuse_before_any_new_input(self):
        Path(self.runtime.gate.spec['stop_path']).write_text('STOP')
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):self.run_runtime()
        self.assertFalse(self.f.inputs)
        self.service.runtime_owner=object()
        with self.assertRaisesRegex(ContractError,'OWNER_CHANGED'):self.run_runtime()
        self.assertFalse(list(self.runtime.root.glob('*-intent.json')))

    def test_zero_new_turn_budget_yields_without_publishing_plan(self):
        result=self.run_runtime(max_new_turns=0)
        self.assertEqual(result['status'],'YIELDED')
        self.assertEqual(result['model_inputs_sent'],0)
        self.assertFalse(list(self.actor.directory.glob('*-next-input-plan.json')))
        self.assertFalse(self.f.inputs)
        result=self.run_runtime()
        self.assertEqual(result['model_inputs_sent'],1)

    def test_interrupted_turn_is_terminal_even_at_pulse_boundary(self):
        self.run_runtime()
        self.f.f.enqueue(dict(method='turn/completed',params=dict(
            threadId=self.f.f.registration['thread_id'],turn=dict(id=self.f.turn,status='interrupted'))))
        with self.assertRaisesRegex(ContractError,'NORMAL_TERMINAL_REQUIRED'):self.run_runtime(max_messages=1)
        self.assertTrue(self.runtime.closed)
        with self.assertRaisesRegex(ContractError,'CLOSED_NO_REPLAY'):self.run_runtime()
        self.assertEqual(len(self.f.inputs),1)

    def test_partial_frame_failure_stays_on_original_connection(self):
        self.run_runtime()
        def fail(**kwargs):raise TimeoutError('original partial frame')
        self.f.client.poll_recorded=fail
        with self.assertRaisesRegex(TimeoutError,'partial frame'):self.run_runtime()
        self.assertIs(self.runtime.client,self.f.client)
        self.assertTrue(self.actor.closed)
        self.assertEqual(len(self.f.inputs),1)

    def test_exhausted_scope_never_claims_business_acceptance(self):
        # A supported finite actor stage may own only the already-completed
        # start operation. This is not whole-campaign completion.
        result=self.run_runtime()
        self.assertEqual(result['status'],'SCOPE_EXHAUSTED')
        self.assertTrue(self.runtime.closed)
        self.assertFalse(result['business_accepted'])
        self.assertFalse(self.f.inputs)

    def test_runtime_scope_cannot_change_after_owner_registration(self):
        self.actor.scope=frozenset(['start'])
        with self.assertRaisesRegex(ContractError,'OWNER_CHANGED'):self.run_runtime()
        self.assertFalse(self.f.inputs)
        self.assertFalse(list(self.runtime.root.glob('*-intent.json')))

    def test_production_attachment_never_bypasses_existing_adoption_gate(self):
        import upgrade_live_control as control
        with patch.object(control,'campaign_task_service',side_effect=ContractError('original adoption refused')) as attach:
            with self.assertRaisesRegex(ContractError,'adoption refused'):
                control.campaign_task_runtime({}, {}, self.f.client,self.f.f.ledger,
                                              advice_connections={'fixture':'held'})
            attach.assert_called_once_with({}, {}, self.f.client,self.f.f.ledger,
                                            advice_connections={'fixture':'held'})
        self.assertFalse(self.f.inputs)


if __name__=='__main__':unittest.main()
