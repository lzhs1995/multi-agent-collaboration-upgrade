"""Actual helper and original .84 journal/input; model/ancestry are fixtures."""
import ast
from contextvars import copy_context
import copy
import fcntl
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
import cmux_agent_adapter as helper_module
import upgrade_task_message_sender as sender
import upgrade_task_message_source as source
import test_upgrade_task_target as targets


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.f=targets.TargetTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        root=self.f.f.root;self.state=root/'.local/state/multi-agent-collaboration'
        self.sample=dict(run_id='run-01',node_id='review',stage='before_business',
            request_id='original-ordinary',sender='claude_a',receiver='codex',
            sender_surface_uuid=targets.SENDER,receiver_surface_uuid=targets.SURFACE,
            body='STATUS: registered ordinary body; neither callback nor new business acceptance.')
        self.campaign=dict(root=str(root),sessions={
            'claude_a':dict(provider='claude',workspace_uuid=targets.WS,surface_uuid=targets.SENDER),
            'codex':dict(provider='codex',workspace_uuid=targets.WS,surface_uuid=targets.SURFACE,pane_uuid=targets.PANE)})
        self.value=dict(schema=source.SCHEMA,kind='ordinary',sample=self.sample)
        self.ref=write_json(root/'source.json',self.value)
        classes={};tree=ast.parse((targets.SOURCE/'scripts/cmux_bridge.py').read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name in ('TaskPackContractError','DispatchUnconfirmed')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'original-errors','exec'),classes)
        row=dict(surface_id=targets.SURFACE,workspace_id=targets.WS,surface_type='terminal',ref='surface:42')
        self.bridge=SimpleNamespace(pin_workspace=lambda *a:copy.deepcopy(self.f.identity),
            list_surfaces=lambda:[row],TaskPackContractError=classes['TaskPackContractError'],
            DispatchUnconfirmed=classes['DispatchUnconfirmed'],_looks_like_task_dispatch=lambda text:False,
            native_submission_state=lambda result:result.get('state','UNKNOWN'),
            _submit_text_once=self.native_submit,submit_text=self.bridge_submit)
        self.saved=[];self.calls=[];self.mode='accepted'
        self.endpoint=write_json(root/'message-endpoint.json',{'fixture':'retained ingress'})
        for p in (patch.object(Path,'home',return_value=root),
                  patch('orchestration_contracts.state_root',return_value=self.state),
                  patch('cmux_agent_adapter.bridge',self.bridge),
                  patch('cmux_agent_adapter.verified_receipt',return_value=None),
                  patch('upgrade_task_message_sender.validate_source',return_value=(self.value,self.campaign,self.sample)),
                  patch('upgrade_task_message_sender.descriptor',return_value=(self.endpoint,{})),
                  patch('upgrade_task_message_route.proposed',return_value=({}, {}, {})),
                  patch('upgrade_task_callback_client.submit_request_once',side_effect=self.ingress),
                  patch.dict(sys.modules,cmux_codex_input=self.f.input)):
            p.start();self.addCleanup(p.stop)
        text=(targets.SOURCE/'scripts/cmux_message_journal.py').read_text()
        self.journal=ModuleType('original_message_journal');self.journal.__file__=str(targets.SOURCE/'scripts/cmux_message_journal.py')
        exec(compile(text,self.journal.__file__,'exec'),self.journal.__dict__)
        self.helper=helper_module.Adapter(home=root)

    def native_submit(self,surface,text,**kwargs):
        self.saved.append(copy_context())
        owner=sender._active.get();self.assertIsInstance(owner,sender.MessageSender)
        def check():
            self.f.native.require_bound(self.bridge,surface,kwargs['native_binding'],text)
        return self.f.input.send_once(kwargs['native_binding'],text,recheck=check,
            emit=kwargs['delivery_observer'],observe=lambda:dict(confirmed=False,state='NATIVE_NOT_YET_OBSERVED'),
            wait_seconds=0,fallback=lambda:self.fail('TASK terminal fallback forbidden'))

    def bridge_submit(self,surface,text,**kwargs):
        return self.journal.deliver(self.bridge,surface,text,kwargs['marker'],
                                    reconcile_only=kwargs['reconcile_only'],wait_seconds=0)

    def ingress(self,binding,request,**kwargs):
        owner=sender._active.get();owner.check();kwargs['recheck']()
        attempt=json_ref(request['attempt'])
        self.assertEqual(request['source'],self.ref)
        self.assertEqual(attempt['phase'],'NATIVE_INPUT_INTENT');self.assertEqual(len(attempt['events']),1)
        self.calls.append(copy.deepcopy(attempt['events'][0]['native_request']))
        # Both the helper's locks and the original bridge's pane/target/journal
        # locks are still held while the one ingress frame is issued.
        locks=list(self.state.rglob('*.lock'))+list(self.state.rglob('*.lock.flock'))
        self.assertGreaterEqual(len(locks),5)
        for path in locks:
            with path.open('rb') as lock:
                with self.assertRaises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        kwargs['record']('MESSAGE_INGRESS_SEND_INTENT',dict(fixture=True))
        if self.mode=='unknown':raise TimeoutError('fixture ingress UNKNOWN')
        return dict(result=dict(turn_id=targets.TURN,message_input=dict(fixture=True)))

    def deliver(self):
        return sender.registered_deliver(self.helper,targets.SURFACE,'send',self.sample['body'],
            self.sample['request_id'],self.ref['path'],self.ref['sha256'])

    def test_actual_helper_locks_original_fence_and_no_direct_model_rpc(self):
        with self.assertRaises(self.bridge.DispatchUnconfirmed):self.deliver()
        self.assertEqual(len(self.calls),1);self.assertFalse(self.f.inputs)
        attempts=list(self.state.glob('message-dispatch-v1/*/attempt-*.json'))
        self.assertEqual(len(attempts),1)
        value=json_ref(file_ref(attempts[0]))
        self.assertEqual([e['phase'] for e in value['events']],['NATIVE_INPUT_INTENT','NATIVE_INPUT_RESULT'])
        self.assertEqual(value['events'][1]['native_result']['rpc_outcome'],'RPC_ACCEPTED')
        self.assertIn('task_message_route',self.calls[0]);self.assertNotIn('task_callback_route',self.calls[0])
        self.assertIsNone(sender._active.get())
        owner=self.saved[0].get(sender._active)
        with self.assertRaisesRegex(ContractError,'OWNER_CHANGED_OR_CLOSED'):self.saved[0].run(owner.check)

    def test_unknown_keeps_helper_pending_and_never_reissues_frame(self):
        self.mode='unknown'
        with self.assertRaises(self.bridge.DispatchUnconfirmed):self.deliver()
        self.assertEqual(len(list(self.state.glob('cmux-agent-adapter-v1/targets/*/pending.json'))),1)
        with self.assertRaises(Exception):self.deliver()
        self.assertEqual(len(self.calls),1);self.assertFalse(self.f.inputs)

    def test_busy_message_uses_registered_ingress_and_original_turn(self):
        self.f.f.thread['status']={'type':'active'};self.f.f.turns[0]['status']='inProgress'
        original=self.f.input.read_metadata
        def metadata(*a,**k):
            value=original(*a,**k);value['requester_direct_rpc_peer']=False;return value
        with patch.object(self.f.input,'read_metadata',side_effect=metadata):
            with self.assertRaises(self.bridge.DispatchUnconfirmed):self.deliver()
        self.assertEqual(len(self.calls),1);self.assertFalse(self.f.inputs)
        self.assertEqual(self.calls[0]['method'],'turn/steer')
        self.assertEqual(self.calls[0]['params']['expectedTurnId'],targets.TURN)


if __name__=='__main__':unittest.main()
