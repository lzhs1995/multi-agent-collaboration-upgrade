"""Generated .84 journal/input integration with real file locks and fences.

Deployment, transport return and receipt observation are explicit fixtures.
No live callback/adoption or model consumption is established by these tests.
"""
import ast
import copy
import fcntl
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
import upgrade_task_callback_sender as sender
from upgrade_task_target_patch import task_target_paths
import test_upgrade_task_target as targets


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.f=targets.TargetTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f;self.root=f.f.root
        self.report=self.root/'report.md';self.report.write_text('original callback report')
        self.pack=dict(task_id='original-task',completion_nonce='callback',
            completion_callback='exact callback body',callback_target=targets.SURFACE,
            executor_uuid=targets.SENDER,report=str(self.report),
            completion_receipt=str(self.root/'callback.json'))
        self.pack_path=self.root/'pack.json';write_json(self.pack_path,self.pack)
        self.journal=self.root/'callback-attempts'
        bridge_classes={}
        tree=ast.parse((targets.SOURCE/'scripts/cmux_bridge.py').read_text())
        classes=[node for node in tree.body if isinstance(node,ast.ClassDef)
                 and node.name in ('TaskPackContractError','DispatchUnconfirmed')]
        exec(compile(ast.Module(body=classes,type_ignores=[]),'original-84-bridge-errors','exec'),bridge_classes)
        self.bridge=SimpleNamespace(validate_task_pack_contract=lambda p:json_ref(file_ref(p)),
            pin_workspace=lambda *a:copy.deepcopy(f.identity),
            _sha256_file=lambda p:file_ref(p)['sha256'],submit_text=self.submit,
            TaskPackContractError=bridge_classes['TaskPackContractError'],
            DispatchUnconfirmed=bridge_classes['DispatchUnconfirmed'])
        self.endpoint=dict(task_thread_id=targets.SID)
        self.endpoint_ref=write_json(self.root/'endpoint.json',self.endpoint)
        self.ingress_calls=[];self.observations=[];self.mode='accepted';self.replace_lock=False
        self.receipt_observation=dict(confirmed=False,state='NATIVE_NOT_YET_OBSERVED')
        for p in (patch.object(Path,'home',return_value=self.root),
                  patch('availability_contract.require_action'),
                  patch.dict(sys.modules,cmux_codex_input=f.input),
                  patch.object(sender.ingress_client,'descriptor',return_value=(self.endpoint_ref,self.endpoint)),
                  patch('upgrade_task_callback_contract.historical_descriptor',return_value=(self.endpoint_ref,self.endpoint)),
                  patch.object(sender.ingress_client,'submit_once',side_effect=self.ingress)):
            p.start();self.addCleanup(p.stop)
        name='cmux_callback_journal.py'
        rendered=task_target_paths(name,(targets.SOURCE/'scripts'/name).read_text())
        generated=self.root/name;generated.write_text(rendered)
        self.module=ModuleType('original_84_generated_callback');self.module.__file__=str(generated)
        exec(compile(rendered,str(generated),'exec'),self.module.__dict__)

    def assert_locked(self,owner):
        self.assertEqual(len(owner.locks),3)
        for held,path in owner.locks:
            self.assertFalse(held.closed)
            with Path(path).open('rb') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def submit(self,surface,text,**kw):
        owner=sender._current.get();self.assertIsInstance(owner,sender.CallbackSender)
        self.assert_locked(owner)
        if self.replace_lock:
            path=Path(owner.locks[2][1]);path.rename(path.with_suffix('.retired'));path.touch()
        bound=kw['native_binding']
        def observe():
            self.assert_locked(owner)
            self.observations.append(True)
            return copy.deepcopy(self.receipt_observation)
        return self.f.input.send_once(bound,text,
            recheck=lambda:self.f.native.require_bound(self.bridge,surface,bound,text),
            emit=kw['delivery_observer'],observe=observe,wait_seconds=0,
            fallback=lambda:(_ for _ in ()).throw(AssertionError('terminal fallback forbidden')))

    def ingress(self,binding,pack,attempt,*,process,recheck,record):
        owner=sender._current.get();self.assert_locked(owner);recheck()
        self.assertEqual(pack,file_ref(self.pack_path))
        value=json_ref(attempt)
        self.assertEqual(value['phase'],'NATIVE_INPUT_INTENT')
        self.assertEqual(len(value['events']),1)
        request=value['events'][0]['native_request']
        self.assertEqual(request['task_callback_route']['attempt'],attempt['path'])
        self.ingress_calls.append(copy.deepcopy(request))
        record('CALLBACK_INGRESS_SEND_INTENT',dict(fixture=True))
        if self.mode=='unknown':raise TimeoutError('fixture unknown original input')
        return dict(result=dict(turn_id=targets.TURN,callback_input=dict(fixture=True)))

    def deliver_unconfirmed(self):
        with self.assertRaises(self.bridge.DispatchUnconfirmed):
            self.module.deliver(self.bridge,self.pack_path,wait_seconds=0)
        self.assertIsNone(sender._current.get())
        self.assertFalse(Path(self.pack['completion_receipt']).exists())
        return json_ref(file_ref(self.journal/'attempt-0001.json'))

    def test_generated_journal_keeps_three_locks_one_intent_and_no_direct_start(self):
        attempt=self.deliver_unconfirmed()
        self.assertEqual(len(self.ingress_calls),1);self.assertFalse(self.f.inputs)
        self.assertEqual([e['phase'] for e in attempt['events']],
                         ['NATIVE_INPUT_INTENT','NATIVE_INPUT_RESULT'])
        self.assertEqual(attempt['events'][1]['native_result']['rpc_outcome'],'RPC_ACCEPTED')
        self.assertEqual(len(list(self.journal.glob('attempt-*.json'))),1)
        self.assertEqual(len(list(self.journal.glob('ingress-*.json'))),1)
        self.assertEqual(len(self.observations),1)

    def test_idle_original_input_gate_runs_before_intent_and_stop_sends_nothing(self):
        import upgrade_input_gate
        def stop(identity,*,native_binding):
            attempt=json_ref(file_ref(self.journal/'attempt-0001.json'))
            self.assertEqual(attempt['phase'],'PREPARED');self.assertEqual(attempt['events'],[])
            self.assertEqual(identity,native_binding['identity'])
            raise ContractError('EXPERIMENT_STOP_REQUESTED')
        with patch.object(upgrade_input_gate,'check_input',side_effect=stop) as gate:
            with self.assertRaisesRegex(ContractError,'EXPERIMENT_STOP_REQUESTED'):
                self.module.deliver(self.bridge,self.pack_path,wait_seconds=0)
        gate.assert_called_once()
        attempt=json_ref(file_ref(self.journal/'attempt-0001.json'))
        self.assertEqual(attempt['phase'],'NO_INPUT');self.assertEqual(attempt['events'],[])
        self.assertFalse(self.ingress_calls);self.assertFalse(self.f.inputs)
        self.assertIsNone(sender._current.get())

    def test_unknown_ingress_keeps_original_attempt_and_refuses_replay(self):
        self.mode='unknown';attempt=self.deliver_unconfirmed()
        before=file_ref(self.journal/'attempt-0001.json')
        with self.assertRaisesRegex(self.bridge.TaskPackContractError,'CALLBACK_ATTEMPT_EXISTS'):
            self.module.deliver(self.bridge,self.pack_path,wait_seconds=0)
        self.assertEqual(before,file_ref(before['path']))
        self.assertEqual(len(self.ingress_calls),1);self.assertFalse(self.f.inputs)
        self.assertEqual(attempt['events'][1]['native_result']['rpc_outcome'],'RPC_ERROR_OR_UNKNOWN')

    def test_replaced_held_lock_refuses_before_intent(self):
        self.replace_lock=True
        with self.assertRaisesRegex(ContractError,'ORIGINAL_LOCK_CHANGED'):
            self.module.deliver(self.bridge,self.pack_path,wait_seconds=0)
        attempt=json_ref(file_ref(self.journal/'attempt-0001.json'))
        self.assertEqual(attempt['phase'],'NO_INPUT');self.assertEqual(attempt['events'],[])
        self.assertFalse(self.ingress_calls);self.assertFalse(self.f.inputs)

    def test_generated_active_steer_keeps_original_rpc_path(self):
        self.f.f.thread['status']['type']='active';self.f.f.turns[0]['status']='inProgress'
        self.deliver_unconfirmed()
        self.assertEqual([method for method,_ in self.f.inputs],['turn/steer'])
        self.assertFalse(self.ingress_calls)

    def test_idle_outside_callback_scope_refuses_without_intent(self):
        with self.assertRaisesRegex(ContractError,'EXTERNAL_TURN_ENROLLMENT_REQUIRED'):
            self.f.input.send_once(self.f.bind(),'exact callback body',recheck=lambda:None,
                emit=lambda *a,**k:self.fail('unexpected intent'),observe=lambda:{},wait_seconds=0)
        self.assertFalse(self.ingress_calls);self.assertFalse(self.f.inputs)

    def test_legacy_submission_does_not_acquire_task_scope(self):
        observed=[]
        def legacy(*args,**kwargs):
            observed.append(sender._current.get());return 'original result'
        result=sender.submit_callback(SimpleNamespace(submit_text=legacy),None,None,(),
            'surface','text',native_binding=dict(schema='native-delivery-v1'))
        self.assertEqual(result,'original result');self.assertEqual(observed,[None])


if __name__=='__main__':unittest.main()
