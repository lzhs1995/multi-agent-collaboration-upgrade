"""Typed plans through real service/ledger; deployment and RPC are fixtures."""
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from orchestration_common import ContractError,json_ref,write_json
from upgrade_task_actor import TaskService
from upgrade_task_control import invocation_script
from upgrade_task_instructions import (PLAN_SCHEMA,initial_text,publish_next_input,
                                       remaining_operations,render)
import test_upgrade_task_enrollment as enrollment
import test_upgrade_task_next_input as next_input_fixture


class InstructionTests(unittest.TestCase):
    request=next_input_fixture.NextInputTests.request

    def setUp(self):
        base=enrollment.EnrollmentTests();base.setUp();self.addCleanup(base.doCleanups)
        self.base=base;self.f=base.f;self.client=base.client
        base.campaign.update(session_group='fixture-group',arm='B')
        # Freeze the fixture's explicit fake deployment fields before creating
        # the gate. Do not patch the new renderer or plan-validation path.
        self.f.c.save();self.f.runner.ref=self.f.c.ref
        self.gate=base.gate();self.f.actor.gate=self.gate
        self.client.poll_recorded=lambda **kwargs:self.f.queue.pop(0) if self.f.queue else None
        self.service=TaskService(self.f.actor);self.old=self.f.ledger.active_turn
        if self._testMethodName=='test_consumed_operation_is_not_renewed_by_plan':
            self.f.runner.fixtures=True
            with patch('orchestration_coordinator.validate_campaign',return_value=base.campaign):
                # The original fixture operations retain their offline gate;
                # actual typed worker/native joins are covered by their suite.
                original=self.f.actor.gate;self.f.actor.gate=lambda *args:None
                try:self.f.call('start');self.f.complete('start')
                finally:self.f.actor.gate=original;self.f.runner.fixtures=False
        previous=dict(method='turn/completed',params=dict(threadId=self.f.registration['thread_id'],
            turn=dict(id=self.old,status='completed')))
        self.previous=self.f.enqueue(previous)
        self.assertEqual(self.service.pulse()['status'],'CLOSED')
        base.observed.update(thread_status='idle',latest_turn_status='completed',observed_input_method='turn/start')
        self.inputs=[];self.turn=str(uuid.uuid4());self.failure=None;self.bad_exchange=False
        self.client.request=self.request

    def test_published_typed_plan_joins_original_one_input_and_same_ledger(self):
        ledger=self.f.ledger.identity
        ref=publish_next_input(self.service);value=json_ref(ref)
        self.assertEqual(value['schema'],PLAN_SCHEMA)
        self.assertEqual(value['operation_ids'],['claim','start'])
        self.assertEqual(value['campaign'],self.f.runner.ref)
        self.assertFalse(self.inputs)
        with self.assertRaises(FileExistsError):publish_next_input(self.service)
        self.service.start_next_turn(ref)
        self.assertEqual(len(self.inputs),1)
        self.assertEqual(self.inputs[0]['input'][0]['text'],value['text'])
        self.assertEqual(self.f.ledger.identity,ledger)
        self.assertEqual(self.f.ledger.active_turn,self.turn)

    def test_edited_text_omitted_action_or_foreign_campaign_never_sends(self):
        ref=publish_next_input(self.service);original=json_ref(ref)
        for change in (dict(text=original['text']+'Choose an unregistered route.'),
                       dict(operation_ids=['claim']),dict(campaign=self.f.reg_ref)):
            bad=write_json(self.f.control/(str(uuid.uuid4())+'.json'),dict(original,**change))
            with self.assertRaisesRegex(ContractError,'TYPED_INPUT_PLAN_CHANGED'):
                self.service.start_next_turn(bad)
        self.assertFalse(self.inputs)
        self.assertFalse((self.f.actor.directory/(self.old+'-next-turn-intent.json')).exists())

    def test_consumed_operation_is_not_renewed_by_plan(self):
        value=json_ref(publish_next_input(self.service))
        self.assertEqual(value['operation_ids'],['claim'])
        self.assertNotIn(invocation_script('start'),value['text'])
        self.assertEqual(self.f.ledger.inspect('inner-start')['phase'],'NATIVE_COMPLETED')

    def test_stopped_or_missing_adoption_never_publishes_or_sends(self):
        with patch('upgrade_live_runtime.validate_adoption',side_effect=ContractError('fixture missing adoption')):
            with self.assertRaisesRegex(ContractError,'missing adoption'):publish_next_input(self.service)
        Path(self.base.spec['stop_path']).write_text('STOP')
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):publish_next_input(self.service)
        self.assertFalse(list(self.f.actor.directory.glob('*-next-input-plan.json')))
        self.assertFalse(self.inputs)

    def test_original_instruction_entry_uses_typed_catalog_and_exact_scripts(self):
        import upgrade_supervisor_instructions
        with patch('upgrade_supervisor_instructions.validate_campaign',
                   return_value=dict(self.base.campaign,evidence_class='cli_campaign_v3')):
            text=upgrade_supervisor_instructions.render(self.f.runner.ref)
        self.assertEqual(text,initial_text(self.f.runner.ref))
        for name in self.f.registration['initial_operations']:
            # Catalog serializes each exact script as JSON, preserving bytes.
            import json
            self.assertIn(json.dumps(invocation_script(name)),text)
        self.assertNotIn('rtk proxy',text)
        self.assertNotIn('orchestration_run.py',text)
        self.assertFalse(self.inputs)

    def test_unknown_send_keeps_v2_intent_and_cannot_republish(self):
        ref=publish_next_input(self.service)
        self.failure=TimeoutError('fixture unknown input')
        with self.assertRaisesRegex(TimeoutError,'unknown input'):self.service.start_next_turn(ref)
        with self.assertRaisesRegex(ContractError,'TRANSITION_ALREADY_ATTEMPTED'):publish_next_input(self.service)
        self.assertEqual(len(self.inputs),1)


class CatalogTests(unittest.TestCase):
    def test_complete_choices_survive_until_any_original_choice_is_consumed(self):
        reg=dict(operations={k:{} for k in ('left','right','read')},choice_groups=dict(route=['left','right']))
        self.assertEqual(remaining_operations(reg,reg['operations'],[]),['left','read','right'])
        self.assertEqual(remaining_operations(reg,reg['operations'],['left']),['read'])
        with self.assertRaisesRegex(ContractError,'CHOICE_HIDDEN'):
            remaining_operations(reg,['left','read'],[])
        with self.assertRaisesRegex(ContractError,'NO_REMAINING'):
            remaining_operations(reg,reg['operations'],['left','read'])

    def test_advice_catalog_preserves_both_routes_and_unresolved_read_slot(self):
        from test_upgrade_task_advice import AdviceTests
        fixture=AdviceTests('test_on_full_body_choices_publish_bind_original_chain')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.c.campaign.update(session_group='fixture-group',arm='B')
        fixture.c.save();fixture.runner.ref=fixture.c.ref
        reg=fixture.registration
        ids=remaining_operations(reg,reg['operations'],['start'])
        text=render(fixture.runner.ref,reg,ids)
        for name in ('advice','read','choose-codex','choose-claude','bind','claim'):
            self.assertIn('"operation_id": "'+name+'"',text)
        self.assertIn('"advice_sha256": null',text)
        self.assertNotIn('fixture-only',text)
        with self.assertRaisesRegex(ContractError,'CHOICE_HIDDEN'):
            render(fixture.runner.ref,reg,[name for name in ids if name!='choose-claude'])
        self.assertFalse(fixture.calls)


if __name__=='__main__':unittest.main()
