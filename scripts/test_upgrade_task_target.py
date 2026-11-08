"""Original .84 delivery readers with real local socket/journal boundaries.

Startup authority, TUI/kernel process rows and RPC replies are fixtures. No
client or model is launched and no live adoption or delivery is claimed.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, write_json
from upgrade_live_patch import native_readers, live_gates
from upgrade_task_target_patch import task_target_paths
from upgrade_task_bridge_patch import task_bridge_paths
import upgrade_task_target as target
import upgrade_task_sender as sender
import test_upgrade_task_thread as thread_fixture

SOURCE = Path(__file__).resolve().parents[1] / 'tests/fixtures/original-controller-084'
MANIFEST = json.loads((SOURCE / 'manifest.json').read_text())
FULL_SOURCE = Path(MANIFEST['original_source'])
for relative, pin in MANIFEST['files'].items():
    raw = (SOURCE / relative).read_bytes()
    if len(raw) != pin['bytes'] or hashlib.sha256(raw).hexdigest() != pin['sha256']:
        raise RuntimeError('original controller fixture changed: ' + relative)
SID = thread_fixture.SID
TURN = thread_fixture.TURN
WS = '44444444-4444-4444-8444-444444444444'
SURFACE = '55555555-5555-4555-8555-555555555555'
SENDER = '66666666-6666-4666-8666-666666666666'
PANE = '77777777-7777-4777-8777-777777777777'


@unittest.skipUnless(SOURCE.is_dir(), 'original .84 controller required')
class TargetTests(unittest.TestCase):
    def setUp(self):
        try:
            self.prepare_fixture()
        except BaseException:
            # Other suites borrow this fixture directly, bypassing unittest's
            # normal cleanup-on-setUp-failure path.
            self.doCleanups()
            raise

    def prepare_fixture(self):
        f = thread_fixture.TaskThreadTests(); self.addCleanup(f.doCleanups);f.setUp(); self.f = f
        path = f.path.with_name('rollout-' + SID + '.jsonl')
        f.path.rename(path); f.path = path
        for key in ('journal', 'metadata', 'startup_input'):
            f.anchor[key]['path'] = str(path)
        f.thread['path'] = str(path)
        self.anchor = f.anchor
        self.control = f.root / 'config'; self.control.mkdir()
        self.binding = dict(config_root=str(self.control), state_root=str(f.root/'state'),
            controller_root=str(f.root/'controller'), task_startup=write_json(f.root/'plan.json', {'fixture': True}))
        self.spec = dict(schema='upgrade-startup-task-v1', controller=self.binding['controller_root'],
            task_startup=self.binding['task_startup'], clients=dict(codex=dict(native_root=str(f.root))),
            rpc=dict(path=str(f.socket_path),argv=f.row['argv']), pins=dict(codex=dict(path=f.row['executable'])))
        startup = write_json(self.control/'startup-v2.json',self.spec)
        write_json(f.root/'state/startup/codex.json',dict(startup=startup,pid=1010,birth=[2,3],
            surface_uuid=SURFACE,workspace_uuid=WS))
        self.result = dict(anchor=write_json(f.root/'anchor.json',self.anchor))
        self.result_ref = write_json(f.root/'result.json',self.result)
        self.process = dict(pid=1010,birth=[2,3],executable=f.row['executable'],file_identity=[1,2,3,4],
            argv=[f.row['executable'],'--remote','unix://'+str(f.socket_path),'resume',SID],
            env=dict(CMUX_SURFACE_ID=SURFACE,CMUX_WORKSPACE_ID=WS))
        self.identity = dict(workspace_uuid=WS,caller_surface_uuid=SENDER,target_surface_uuid=SURFACE,target_pane_uuid=PANE)
        self.row = dict(self.identity,tty='ttys-fixture')
        self.bridge = types.SimpleNamespace(pin_workspace=lambda *a:copy.deepcopy(self.identity))
        self.inputs=[]; self.events=[]
        outer=self
        class RPC:
            def __init__(self,*a,**k):
                self.socket_identity=copy.deepcopy(outer.anchor['socket']);self.sock=f.client.sock
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def request(self,method,params):
                if method.startswith('turn/') and method!='thread/turns/list':
                    outer.inputs.append((method,params))
                    if getattr(outer,'input_failure',None):raise outer.input_failure
                    return dict(turnId=TURN)
                return f.client.request(method,params)
        self.RPC=RPC
        fake_rpc=types.ModuleType('cmux_codex_rpc');fake_rpc.DaemonRPC=RPC
        fake_rpc.RPCError=type('RPCError',(RuntimeError,),{})
        confirmation=types.ModuleType('cmux_confirmation')
        exec(compile((SOURCE/'scripts/cmux_confirmation.py').read_text(),'original-84-confirmation','exec'),
             confirmation.__dict__)
        user_record=types.ModuleType('native_user_record')
        exec(compile((SOURCE/'scripts/native_user_record.py').read_text(),'original-84-user-record','exec'),
             user_record.__dict__)
        # Execute original reader functions, preserving their source and fences.
        # Native process discovery is replaced only by explicit test doubles.
        raw=(SOURCE/'scripts/cmux_native_delivery.py').read_text()
        rendered=task_bridge_paths('cmux_native_delivery.py',task_target_paths(
            'cmux_native_delivery.py',native_readers('cmux_native_delivery.py',raw)))
        import cmux_native_delivery as base_native
        self.native=types.ModuleType('fixture_task_delivery')
        self.native.__dict__.update(base_native.__dict__)
        self.native.__dict__.update(task_target=target,observation_budget=types.SimpleNamespace(),
            private_roots=types.SimpleNamespace(process_root=lambda *a:f.root,binding_root=lambda *a:f.root,
                transcript_root=lambda *a,**k:f.root/'sessions'))
        tree=ast.parse(rendered)
        functions=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))]
        exec(compile(ast.Module(body=functions,type_ignores=[]),'original-84-task-target','exec'),self.native.__dict__)
        self.native._tree_row=lambda *a:copy.deepcopy(self.row)
        self.native._target_process=lambda *a:copy.deepcopy(self.process)
        self.native._provider=lambda p:'codex'
        self.native._live_session=lambda *a:(_ for _ in ()).throw(AssertionError('foreground must not select task'))
        self.native.private_process=types.SimpleNamespace(terminal=lambda p:'ttys-fixture')
        import cmux_daemon_identity
        for p in (
            patch.object(target,'configuration',return_value=(self.binding,{})),
            patch.object(sender,'configuration',return_value=(self.binding,{})),
            patch.object(target,'task_startup_result',side_effect=lambda *a:(self.result_ref,self.result,self.anchor)),
            patch.object(target,'process_root',return_value=f.root),
            patch.object(target,'observe',side_effect=lambda client,anchor,process:thread_fixture.observe(
                client,anchor,process,descriptors=f.descriptors)),
            patch.object(cmux_daemon_identity,'process',side_effect=lambda pid:copy.deepcopy(self.process if pid==1010 else f.row)),
            patch.dict(sys.modules,cmux_codex_rpc=fake_rpc,cmux_native_delivery=self.native,
                       cmux_confirmation=confirmation,native_user_record=user_record),
        ):
            p.start();self.addCleanup(p.stop)
        input_raw=(SOURCE/'scripts/cmux_codex_input.py').read_text()
        self.input=types.ModuleType('fixture_task_input')
        p=patch.dict(sys.modules,upgrade_input_gate=types.SimpleNamespace(check_input=lambda *a,**k:None))
        p.start();self.addCleanup(p.stop)
        exec(compile(task_target_paths('cmux_codex_input.py',live_gates('cmux_codex_input.py',input_raw)),
                     'original-84-task-input','exec'),self.input.__dict__)

    def bind(self):return self.native.bind_target(self.bridge,SURFACE,'exact callback body')

    def test_binding_keeps_display_and_server_distinct_without_foreground(self):
        bound=self.bind()
        self.assertEqual(bound['schema'],target.SCHEMA)
        self.assertEqual(bound['process']['pid'],1010)
        self.assertEqual(bound['task_target']['server']['pid'],os.getpid())
        self.assertEqual(bound['session_id'],SID)
        self.assertEqual(bound['transcript']['path'],str(self.f.path))
        self.assertTrue(all(m in ('thread/read','thread/turns/list') for m,_ in self.f.calls))
        self.assertFalse(self.inputs)

    def test_other_tui_process_or_remote_cannot_bind_original_task(self):
        self.process['birth']=[2,4]
        with self.assertRaisesRegex(ContractError,'ORIGINAL_DISPLAY_CHANGED'):self.bind()
        self.process['birth']=[2,3];self.process['argv'][2]='unix:///foreign'
        with self.assertRaisesRegex(ContractError,'REMOTE_TARGET_CHANGED'):self.bind()
        self.assertFalse(self.inputs)

    def test_saved_binding_cannot_select_other_thread_server_or_schema(self):
        bound=self.bind()
        for key,value in (('session_id',thread_fixture.OTHER),('task_target',{}),('schema',self.native.SCHEMA)):
            with self.subTest(key=key),self.assertRaises((ContractError,self.native.NativeDeliveryError)):
                self.native._validate_binding(dict(bound,**{key:value}),'exact callback body')
        downgraded=dict(bound,schema=self.native.SCHEMA);downgraded.pop('task_target')
        with self.assertRaisesRegex(ContractError,'DOWNGRADE_REFUSED'):
            self.native._validate_binding(downgraded,'exact callback body')

    def test_legacy_topology_selection_and_fallback_remain_explicit(self):
        legacy=dict(self.binding);legacy.pop('task_startup')
        with patch.object(target,'configuration',return_value=(legacy,{})):
            self.assertIsNone(target.selected(self.process))
            fallback=object()
            self.assertIs(target.native_fallback(dict(provider='codex',schema=self.native.SCHEMA),fallback),fallback)
        self.assertFalse(self.f.calls);self.assertFalse(self.inputs)

    def test_active_input_uses_original_one_shot_path_and_never_falls_back(self):
        self.f.thread['status']['type']='active';self.f.turns[0]['status']='inProgress'
        bound=self.bind();self.input_failure=TimeoutError('original input unknown')
        fallback=[]
        result=self.input.send_once(bound,'exact callback body',
            recheck=lambda:self.native.require_bound(self.bridge,SURFACE,bound,'exact callback body'),
            emit=lambda phase,**kw:self.events.append((phase,kw)),
            observe=lambda:dict(confirmed=False),wait_seconds=0,fallback=lambda:fallback.append(True))
        self.assertEqual(self.inputs,[('turn/steer',dict(threadId=SID,
            input=[dict(type='text',text='exact callback body')],expectedTurnId=TURN))])
        self.assertEqual([e[0] for e in self.events],['NATIVE_INPUT_INTENT','NATIVE_INPUT_RESULT'])
        self.assertEqual(result['input_operations'],1)
        self.assertEqual(result['retries'],0)
        self.assertFalse(result['confirmed']);self.assertFalse(fallback)

    def test_idle_requires_original_external_turn_enrollment_not_untracked_start(self):
        bound=self.bind();fallback=[]
        with self.assertRaisesRegex(ContractError,'EXTERNAL_TURN_ENROLLMENT_REQUIRED'):
            self.input.send_once(bound,'exact callback body',recheck=lambda:None,
                emit=lambda *a,**kw:self.events.append(a),observe=lambda:None,wait_seconds=0,
                fallback=lambda:fallback.append(True))
        self.assertFalse(self.inputs);self.assertFalse(self.events);self.assertFalse(fallback)

    def test_actual_input_connection_and_request_socket_must_match_original(self):
        bound=self.bind();client=self.RPC()
        client.socket_identity['inode']+=1
        with self.assertRaisesRegex(ContractError,'SOCKET_CHANGED'):target.require_client(client,bound)
        with self.assertRaisesRegex(ContractError,'REQUEST_SOCKET_CHANGED'):
            target.require_request(dict(method='turn/steer',socket={}),bound)
        self.assertFalse(self.inputs)

    def test_original_native_fence_still_rejects_old_or_foreign_user_record(self):
        bound=self.bind()
        marker='exact callback body'
        fence=self.native.capture_paste_fence(bound)
        import time
        from datetime import datetime,timezone
        at=time.time()
        raw=(json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(),type='response_item',
            payload=dict(type='message',role='user',content=[dict(type='input_text',text=marker)])))+'\n').encode()
        offset=self.f.path.stat().st_size
        with self.f.path.open('ab') as f:f.write(raw)
        proof=dict(schema=self.native.SCHEMA,session_id=SID,path=str(self.f.path),
            device=bound['transcript']['device'],inode=bound['transcript']['inode'],offset=offset,
            length=len(raw),sha256=hashlib.sha256(raw).hexdigest(),reception_kind='native_user_message',
            payload_sha256=bound['payload_sha256'])
        self.assertTrue(self.native.validate_proof(bound,marker,proof,not_before=at,paste_fence=fence))
        self.assertFalse(self.native.validate_proof(bound,marker,dict(proof,session_id=thread_fixture.OTHER),
            not_before=at,paste_fence=fence))
        self.assertFalse(self.native.validate_proof(bound,marker,dict(proof,offset=0),not_before=at,paste_fence=fence))

    def test_campaign_reader_joins_original_typed_target_fence_and_receipt(self):
        from orchestration_native import verify_delivery
        import time
        from datetime import datetime,timezone
        text='exact callback body'
        self.f.thread['status']['type']='active';self.f.turns[0]['status']='inProgress'
        bound=self.bind();request=self.input.prepare(self.RPC(),bound,text)
        with patch.dict(sys.modules,cmux_codex_input=self.input):
            fields=self.native.input_event_fields(bound,text,'NATIVE_INPUT_INTENT',dict(native_request=request))
        event=dict(phase='NATIVE_INPUT_INTENT',at_epoch=time.time(),**fields)
        raw=(json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(),type='response_item',
            payload=dict(type='message',role='user',content=[dict(type='input_text',text=text)])))+'\n').encode()
        offset=self.f.path.stat().st_size
        with self.f.path.open('ab') as stream:stream.write(raw)
        proof=dict(schema=self.native.SCHEMA,session_id=SID,path=str(self.f.path),
            device=bound['transcript']['device'],inode=bound['transcript']['inode'],offset=offset,
            length=len(raw),sha256=hashlib.sha256(raw).hexdigest(),reception_kind='native_user_message',
            payload_sha256=bound['payload_sha256'])
        receiver=dict(provider='codex',session_id=SID,process=bound['process'],tty=bound['tty'],
            workspace_uuid=WS,surface_uuid=SURFACE,pane_uuid=PANE,native_config_root=str(self.f.root),
            transcript={k:bound['transcript'][k] for k in ('path','device','inode')},
            private_native=dict(runtime={'fixture':True},adoption={'fixture':True}))
        sender=dict(workspace_uuid=WS,surface_uuid=SENDER,pane_uuid='sender-pane')
        runtime=dict(startup=bound['task_target']['startup'],state_root=self.binding['state_root'])
        attempt=dict(phase='CONFIRMED',binding=dict(identity=self.identity),native_binding=bound,events=[event])
        directory=self.f.root/'original-callback';directory.mkdir()
        attempt_path=directory/'attempt-0001.json'
        receipt=dict(attempt=str(attempt_path),confirmed=True,binding=attempt['binding'],native_binding=bound,
            native_proof=proof,confirmation_source='native_user_message_v1')
        def save():
            return dict(attempts=[write_json(attempt_path,attempt)],receipt=write_json(directory/'receipt.json',receipt))
        with patch('upgrade_live_runtime.session_authority',return_value=({},runtime,dict(sessions=dict(codex=receiver)))), \
             patch('upgrade_task_readiness.task_startup_result',return_value=(self.result_ref,self.result,self.anchor)):
            result=verify_delivery(save(),receiver,sender,text,fixtures=True)
            self.assertEqual(result['transport_retries'],0)
            wrong=copy.deepcopy(receiver);wrong['private_native']={}
            with self.assertRaisesRegex(ContractError,'ADOPTED_RECEIVER_REQUIRED'):
                verify_delivery(save(),wrong,sender,text,fixtures=True)
            bound['task_target']=copy.deepcopy(bound['task_target'])
            bound['task_target']['server']['birth']=[9,9]
            with self.assertRaisesRegex(ContractError,'ORIGINAL_BINDING_CHANGED'):
                verify_delivery(save(),receiver,sender,text,fixtures=True)

    def test_original_084_materialization_contains_complete_target_support(self):
        import test_upgrade_task_topology as topology
        if not all(p.is_dir() for p in (FULL_SOURCE, topology.WRAPPERS, topology.PACKAGE)):
            self.skipTest('full original controller and local client packages required')
        class Candidate(topology.TaskTopologyTests):pass
        with patch.object(topology,'SOURCE',FULL_SOURCE):
            Candidate.setUpClass()
        self.addCleanup(Candidate.tearDownClass)
        root=Path(Candidate.spec['controller'])/'scripts'
        text=(root/'cmux_native_delivery.py').read_text()
        self.assertIn('import mac_upgrade_task_target as task_target',text)
        self.assertIn('task_target.require_live(b, p)',text)
        self.assertTrue((root/'mac_upgrade_task_target.py').is_file())
        input_text=(root/'cmux_codex_input.py').read_text()
        self.assertIn('task_target.require_request(value, binding)',input_text)
        self.assertIn('task_target.native_fallback(binding, fallback)',input_text)
        self.assertIn('task_target.require_client(client, binding)',input_text)
        self.assertFalse((Path(Candidate.spec['adoption_path'])).exists())


if __name__=='__main__':unittest.main()
