"""One next input through real service/gate/SQLite with mocked deployment/RPC.

No live clients are started. RPC records carry the fixture's original peer;
these tests do not establish production adoption or model consumption.
"""
import copy
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
from upgrade_task_actor import TaskService
import test_upgrade_task_enrollment as fixtures


class NextInputTests(unittest.TestCase):
    def setUp(self):
        base=fixtures.EnrollmentTests();base.setUp();self.addCleanup(base.doCleanups)
        self.base=base;self.f=base.f;self.client=base.client
        self.gate=base.gate();self.f.actor.gate=self.gate
        self.client.poll_recorded=lambda **kwargs:self.f.queue.pop(0) if self.f.queue else None
        self.service=TaskService(self.f.actor)
        self.old=self.f.ledger.active_turn
        previous=dict(method='turn/completed',params=dict(threadId=self.f.registration['thread_id'],
            turn=dict(id=self.old,status='completed')))
        self.previous=self.f.enqueue(previous)
        self.assertEqual(self.service.pulse()['status'],'CLOSED')
        self.base.observed.update(thread_status='idle',latest_turn_status='completed',observed_input_method='turn/start')
        self.plan_value=dict(schema='upgrade-task-next-input-plan-v1',
            registration_sha256=self.f.ledger.registration_sha256,authority_ref=self.f.registration['authority_ref'],
            previous_turn_id=self.old,previous_completion=self.previous,
            operation_ids=['start','claim'],text='Continue the registered task; select only a declared operation.')
        self.plan=write_json(self.f.control/'next-input-plan.json',self.plan_value)
        self.inputs=[];self.turn=str(uuid.uuid4());self.failure=None;self.bad_exchange=False
        self.client.request=self.request

    def request(self,method,params):
        self.assertEqual(method,'turn/start')
        self.assertTrue((self.f.actor.directory/(self.old+'-next-turn-intent.json')).exists())
        self.assertTrue((self.f.control/(self.old+'-model-input-intent.json')).exists())
        self.inputs.append(copy.deepcopy(params))
        if self.failure:raise self.failure
        peer=dict(schema='upgrade-task-rpc-peer-v1',server=self.client.server,
            peer={k:self.client.server[k] for k in ('pid','uid')})
        sent=copy.deepcopy(params)
        if self.bad_exchange:sent['input'][0]['text']='different input'
        def record(name,direction,message):
            return write_json(self.f.control/(self.turn+'-'+name+'.json'),
                dict(direction=direction,message=message,peer=peer),exclusive=True)
        result=dict(turn=dict(id=self.turn))
        self.client.last_exchange=dict(
            intent=record('intent','send_intent',dict(id=350,method=method,params=sent)),
            response=record('response','received',dict(id=350,result=result)))
        self.base.observed.update(latest_turn_id=self.turn,thread_status='active',
            latest_turn_status='inProgress',observed_input_method='turn/steer')
        return result

    def changed_plan(self,**changes):
        return write_json(self.f.control/('changed-'+str(uuid.uuid4())+'.json'),dict(self.plan_value,**changes))

    def test_one_input_reopens_same_service_preserving_operations(self):
        before=self.f.ledger.identity
        result=self.service.start_next_turn(self.plan)
        self.assertEqual(result,dict(status='ACTIVE',turn_id=self.turn,operation_budget_reset=False,model_inputs_sent=1))
        self.assertEqual(len(self.inputs),1)
        self.assertIs(self.f.actor.service_owner,self.service)
        self.assertEqual(self.f.ledger.identity,before)
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM turns').fetchone()[0],2)
        registration=json_ref(json_ref(self.gate.proof)['registration'])
        self.assertEqual(registration['operations'],self.f.registration['operations'])
        with self.assertRaisesRegex(ContractError,'NORMAL_TERMINAL_REQUIRED'):
            self.service.start_next_turn(self.plan)
        self.assertEqual(len(self.inputs),1)

    def test_ambiguous_send_keeps_original_intent_and_cannot_retry_either_entry(self):
        self.failure=TimeoutError('fixture original turn/start outcome unknown')
        with self.assertRaisesRegex(TimeoutError,'outcome unknown'):self.service.start_next_turn(self.plan)
        self.assertTrue(self.f.actor.closed)
        unknown=json_ref(file_ref(self.f.actor.directory/(self.old+'-next-turn-unknown.json')))
        self.assertEqual(unknown['status'],'UNKNOWN_NO_REPLAY')
        self.assertFalse(unknown['retry_allowed'])
        with self.assertRaises(FileExistsError):self.service.start_next_turn(self.plan)
        grant,previous,turn,exchange=self.base.next_grant()
        value=json_ref(grant);value['previous_completion']=self.previous
        grant=write_json(self.f.control/'reuse-grant.json',value)
        with self.assertRaises(FileExistsError):self.service.continue_turn(grant)
        self.assertEqual(len(self.inputs),1)
        self.assertEqual(self.f.ledger.active_turn,self.old)

    def test_active_foreign_turn_stop_or_foreign_plan_never_send(self):
        for change in (dict(thread_status='active'),dict(latest_turn_status='failed'),
                       dict(latest_turn_id=str(uuid.uuid4()))):
            original=dict(self.base.observed);self.base.observed.update(change)
            with self.assertRaises(ContractError):self.service.start_next_turn(self.plan)
            self.base.observed=original
        for plan in (self.changed_plan(previous_completion={}),self.changed_plan(operation_ids=['unknown']),
                     self.changed_plan(text=''),self.changed_plan(registration_sha256='0'*64)):
            with self.assertRaises(ContractError):self.service.start_next_turn(plan)
        Path(self.base.spec['stop_path']).write_text('STOP')
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):self.service.start_next_turn(self.plan)
        self.assertFalse(self.inputs)
        self.assertFalse((self.f.actor.directory/(self.old+'-next-turn-intent.json')).exists())

    def test_last_moment_stop_preserves_intent_without_send(self):
        original=self.gate._observe
        def observe(**kwargs):
            result=original(**kwargs)
            Path(self.base.spec['stop_path']).write_text('STOP')
            return result
        with patch.object(self.gate,'_observe',side_effect=observe):
            with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):self.service.start_next_turn(self.plan)
        self.assertFalse(self.inputs)
        self.assertTrue(self.f.actor.closed)

    def test_changed_exchange_or_post_append_failure_never_replays(self):
        self.bad_exchange=True
        with self.assertRaisesRegex(ContractError,'INPUT_EXCHANGE_CHANGED'):self.service.start_next_turn(self.plan)
        self.assertTrue(self.f.actor.closed)
        self.assertEqual(self.f.ledger.active_turn,self.old)
        self.assertEqual(len(self.inputs),1)

    def test_live_failure_after_enrollment_keeps_new_turn_closed(self):
        original=self.gate._observe
        def observe(**kwargs):
            if self.f.ledger.active_turn==self.turn:raise ContractError('fixture live peer changed after append')
            return original(**kwargs)
        with patch.object(self.gate,'_observe',side_effect=observe):
            with self.assertRaisesRegex(ContractError,'after append'):self.service.start_next_turn(self.plan)
        self.assertTrue(self.f.actor.closed)
        self.assertEqual(self.f.ledger.active_turn,self.turn)
        with self.assertRaisesRegex(ContractError,'ORIGINAL_TERMINAL_CHANGED'):self.service.start_next_turn(self.plan)
        self.assertEqual(len(self.inputs),1)

    def test_service_scope_and_owner_cannot_be_widened_by_plan(self):
        self.f.actor.scope=frozenset(['start'])
        with self.assertRaisesRegex(ContractError,'OUTSIDE_SERVICE_SCOPE'):self.service.start_next_turn(self.plan)
        self.f.actor.scope=frozenset(self.f.registration['operations'])
        self.service.owner_pid+=1
        with self.assertRaisesRegex(ContractError,'ORIGINAL_OWNER_REQUIRED'):self.service.start_next_turn(self.plan)
        self.assertFalse(self.inputs)

    def test_missing_production_adoption_blocks_before_input_intent(self):
        with patch('upgrade_live_runtime.validate_adoption',side_effect=ContractError('fixture missing production adoption')):
            with self.assertRaisesRegex(ContractError,'missing production adoption'):self.service.start_next_turn(self.plan)
        self.assertFalse(self.inputs)
        self.assertFalse((self.f.actor.directory/(self.old+'-next-turn-intent.json')).exists())


if __name__=='__main__':unittest.main()
