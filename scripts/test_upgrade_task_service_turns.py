"""Same service/ledger across normal turns; deployment gate is an explicit fake."""
import copy
import uuid
import unittest

from orchestration_common import ContractError,json_ref,object_sha,utc_now,write_json
from upgrade_task_control import invocation_script
import test_upgrade_task_service as fixtures


class ServiceTurnTests(unittest.TestCase):
    def setUp(self):
        base=fixtures.ServiceTests();base.setUp();self.addCleanup(base.doCleanups)
        self.base=base;self.f=base.f;self.service=base.service

    def closed(self):
        message=self.base.complete_candidate()
        self.f.native_result('candidate',message)
        self.base.terminal()
        self.assertEqual(self.service.pulse()['status'],'CLOSED')
        self.assertIsNotNone(self.service.normal_completion)

    def grant(self,*,operations=None,previous=None):
        f=self.f;turn=str(uuid.uuid4())
        terminal,terminal_ref=self.service.normal_completion
        if previous is not None:terminal_ref=previous
        peer=dict(schema='upgrade-task-rpc-peer-v1',server=f.f.client.server,
            peer={k:f.f.client.server[k] for k in ('pid','uid')})
        def record(kind,msg):return write_json(f.control/(turn+'-'+kind+'.json'),
            dict(direction=kind,message=msg,peer=peer),exclusive=True)
        exchange=dict(intent=record('send_intent',dict(id=100,method='turn/start',params=dict(
            threadId=f.registration['thread_id'],input=[dict(type='text',text='Continue exact original task')]))),
            response=record('received',dict(id=100,result=dict(turn=dict(id=turn)))))
        value=dict(schema='upgrade-task-operation-turn-v1',registration_sha256=object_sha(f.registration),
            authority_ref=f.registration['authority_ref'],previous_turn_id=f.ledger.active_turn,
            turn_id=turn,operation_ids=operations or ['accept'],previous_completion=terminal_ref,turn_start=exchange)
        return write_json(f.control/(turn+'-grant.json'),value,exclusive=True),turn

    def gate(self,*,fail_after_append=False):
        f=self.f;old=f.actor.gate
        class Gate:
            def __call__(self,*args):return old(*args)
            def enroll_next_turn(self,grant,previous):
                result=f.ledger.enroll_turn(grant)
                if fail_after_append:raise ContractError('fixture post-enrollment failure')
                return write_json(f.control/(result['turn_id']+'-enrollment.json'),result,exclusive=True)
        f.actor.gate=Gate()

    def test_same_owner_accepts_next_turn_with_old_bindings_and_budgets(self):
        self.closed();self.gate();f=self.f
        before=f.ledger.path.stat();grant,turn=self.grant()
        result=self.service.continue_turn(grant)
        self.assertFalse(result['operation_budget_reset']);self.assertFalse(f.actor.closed)
        self.assertIs(f.actor.service_owner,self.service)
        self.assertEqual((before.st_dev,before.st_ino),f.ledger.identity)
        self.assertEqual(f.ledger.db.execute('SELECT count(*) FROM calls').fetchone()[0],3)
        self.assertEqual(f.ledger.db.execute('SELECT count(*) FROM bindings').fetchone()[0],1)
        f.native.append(dict(timestamp=utc_now(),type='response_item',payload=dict(type='custom_tool_call',
            name='exec',call_id='outer-accept',input=invocation_script('accept'),
            internal_chat_message_metadata_passthrough=dict(turn_id=turn))))
        f.enqueue(dict(id=900,method='item/tool/call',params=dict(threadId=f.registration['thread_id'],
            turnId=turn,callId='inner-accept',namespace='upgrade_collaboration',tool='execute',arguments={'operation_id':'accept'})))
        self.assertEqual(self.service.pulse(max_messages=1)['status'],'ACTIVE')
        self.assertEqual(f.runner.status('run-01')['states']['local'],'ACCEPTED')
        self.assertEqual(len(f.sent),4)
        self.assertEqual(f.ledger.db.execute('SELECT count(*) FROM turns').fetchone()[0],2)

    def test_new_turn_does_not_renew_an_already_completed_operation(self):
        self.closed();self.gate();grant,turn=self.grant(operations=['start'])
        self.service.continue_turn(grant)
        f=self.f;request=copy.deepcopy(f.ledger.inspect('inner-start')['request'])
        request['id']=901;request['params'].update(turnId=turn,callId='replay-start')
        f.enqueue(request)
        with self.assertRaisesRegex(ContractError,'NO_REPLAY'):self.service.pulse(max_messages=1)
        self.assertEqual(len(f.sent),3);self.assertTrue(f.actor.closed)

    def test_pending_bindings_unknown_and_interrupted_cannot_continue(self):
        self.base.complete_candidate();self.base.terminal('interrupted');self.service.pulse()
        with self.assertRaisesRegex(ContractError,'NORMAL_TERMINAL_REQUIRED'):
            self.service.continue_turn({})
        self.assertEqual(len(self.f.sent),3)

    def test_unfinished_operation_cannot_be_promoted_by_normal_turn_status(self):
        self.base.terminal();self.service.pulse()
        self.assertEqual(self.f.ledger.inspect('inner-candidate')['phase'],'UNKNOWN')
        self.assertIsNone(self.service.normal_completion)
        with self.assertRaisesRegex(ContractError,'NORMAL_TERMINAL_REQUIRED'):self.service.continue_turn({})

    def test_plain_gate_does_not_grant_multi_turn_authority(self):
        self.closed();grant,_=self.grant()
        with self.assertRaisesRegex(ContractError,'LIVE_TURN_GATE_REQUIRED'):self.service.continue_turn(grant)
        self.assertEqual(self.f.ledger.db.execute('SELECT count(*) FROM turns').fetchone()[0],1)

    def test_foreign_terminal_and_post_commit_failure_stay_sealed(self):
        self.closed();self.gate(fail_after_append=True)
        grant,_=self.grant(previous=write_json(self.f.control/'unrelated.json',{}))
        with self.assertRaisesRegex(ContractError,'ORIGINAL_TERMINAL_CHANGED'):self.service.continue_turn(grant)
        grant,_=self.grant()
        with self.assertRaisesRegex(ContractError,'post-enrollment failure'):self.service.continue_turn(grant)
        self.assertTrue(self.f.actor.closed)
        self.assertEqual(len(self.f.sent),3)
        with self.assertRaisesRegex(ContractError,'ORIGINAL_TERMINAL_CHANGED'):self.service.continue_turn(grant)


if __name__=='__main__':unittest.main()
