"""Historical idle proof with real service/ledger and fixture original ingress.

The descriptor's outer startup/TUI contract and live dispatch are mocked here;
the retained input exchange, immutable snapshot/result and ledger are real.
Separate target/ingress suites cover those descriptor and transport boundaries.
"""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
import upgrade_task_callback_route as route
import test_upgrade_task_callback_turn as fixture


class RouteTests(unittest.TestCase):
    def setUp(self):
        from orchestration_test_support import CampaignFixture
        original=CampaignFixture.__init__
        def complete_process_rows(campaign,*args,**kwargs):
            original(campaign,*args,**kwargs)
            for session in campaign.campaign['sessions'].values():
                session['process']['file_identity']=[1,2,3,4]
            campaign.save()
        with patch.object(CampaignFixture,'__init__',complete_process_rows):
            self.s=fixture.ServiceTests();self.s.setUp()
        self.addCleanup(self.s.doCleanups)
        n=self.s.f;self.n=n;self.gate=n.gate
        parent=Path(n.f.reg_ref['path']).parent
        directory=parent/'campaign-actor-events';directory.mkdir();n.f.actor.directory=directory
        self.sender=n.base.campaign['sessions']['claude_a']
        receiver=n.base.campaign['sessions']['codex']
        report=parent/'report.md';report.write_text('original report')
        pack=dict(task_id='task',completion_nonce='nonce',completion_callback='exact callback body',
                  completion_receipt=str(parent/'callback.json'),report=str(report),
                  executor_uuid=self.sender['surface_uuid'],callback_target=receiver['surface_uuid'])
        self.pack_ref=write_json(parent/'pack.json',pack)
        journal=parent/'callback-attempts';journal.mkdir()
        self.attempt_path=journal/'attempt-0001.json'
        self.endpoint=dict(schema='upgrade-task-callback-ingress-v1',registration=n.f.reg_ref,
                           enrollment=self.gate.proof,campaign=self.gate.runner.ref,owner=dict(pid=os.getpid()))
        self.endpoint_ref=write_json(parent/'descriptor.json',self.endpoint)
        self.binding=dict(schema='native-delivery-task-v1',session_id=n.f.registration['thread_id'],
            task_target=dict(socket=n.base.anchor['socket']),
            identity=dict(caller_surface_uuid=self.sender['surface_uuid'],target_surface_uuid=receiver['surface_uuid']))
        self.request=dict(method='turn/start',observed_status='idle',observed_turn_id=n.old,
            socket=n.base.anchor['socket'],params=self.s.initial['request']['params'],
            task_callback_route=dict(schema=route.SCHEMA,ingress=self.endpoint_ref,
                                     pack=self.pack_ref,attempt=str(self.attempt_path)))
        self.attempt=dict(phase='NATIVE_INPUT_INTENT',native_binding=self.binding,
                          binding=dict(task_id='task'),events=[dict(phase='NATIVE_INPUT_INTENT',native_request=self.request)])
        self.attempt_ref=write_json(self.attempt_path,self.attempt)
        sender_row=dict(self.sender['process'],ppid=1)
        tool=dict(sender_row,pid=sender_row['pid']+1000,ppid=sender_row['pid'])
        self.s.initial.update(pack=self.pack_ref,attempt=self.attempt_ref,request=self.request,
            caller=dict(peer=dict(pid=tool['pid'],uid=os.getuid()),ancestry=[tool,sender_row]))
        self.s.request=dict(pack=self.pack_ref,attempt=self.attempt_ref)
        p=patch('upgrade_task_callback_contract.historical_descriptor',return_value=(self.endpoint_ref,self.endpoint))
        p.start();self.addCleanup(p.stop)
        self.result=n.service.start_callback_turn(object(),self.s.request)

    def check(self,attempt=None):
        return route.verify_input(self.binding,self.request,self.attempt if attempt is None else attempt)

    def test_original_service_result_and_enrolled_turn_join_exact_request(self):
        self.assertEqual(self.check(),self.result['callback_input'])
        final=copy.deepcopy(self.attempt);final['phase']='CONFIRMED'
        final['events'].append(dict(phase='NATIVE_INPUT_RESULT',callback_input=self.result['callback_input']))
        self.assertEqual(self.check(final),self.result['callback_input'])
        self.assertEqual(len(self.n.inputs),1)

    def test_removed_service_result_never_becomes_sender_confirmed(self):
        Path(self.result['callback_input']['path']).unlink()
        with self.assertRaises((ContractError,OSError)):self.check()
        self.assertEqual(len(self.n.inputs),1)

    def test_original_snapshot_or_current_input_change_refuses(self):
        changed=copy.deepcopy(self.attempt);changed['events'][0]['native_request']['params']['input'][0]['text']='foreign'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_SOURCE_CHANGED'):self.check(changed)
        source=json_ref(json_ref(self.result['callback_input'])['source'])
        write_json(source['original_attempt_snapshot']['path'],{'changed':True})
        with self.assertRaises(ContractError):self.check()

    def test_missing_original_ledger_turn_refuses_without_enrollment(self):
        self.n.f.ledger.db.execute('DELETE FROM turns WHERE turn_id=?',(self.n.turn,))
        with self.assertRaisesRegex(ContractError,'ORIGINAL_TURN_NOT_ENROLLED'):self.check()
        self.assertEqual(len(self.n.inputs),1)

    def test_original_route_cannot_select_other_pack_or_endpoint(self):
        original=copy.deepcopy(self.request)
        self.request['task_callback_route']['ingress']={}
        with self.assertRaisesRegex(ContractError,'ORIGINAL_INGRESS_CHANGED'):self.check()
        self.request.clear();self.request.update(original)
        self.request['params']['threadId']='foreign'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_REQUEST_CHANGED'):self.check()

    def test_malformed_previous_turn_refuses_as_contract_error(self):
        self.request['observed_turn_id']='not-a-uuid'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_REQUEST_CHANGED'):self.check()


if __name__=='__main__':unittest.main()
