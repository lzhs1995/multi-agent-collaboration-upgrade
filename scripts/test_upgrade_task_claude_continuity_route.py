"""Real Unix sockets/process ancestry with explicitly synthetic model journals.

No models/terminals are launched. Fixture Claude parents are ordinary Python
processes; each launches the real submit_once API in a descendant. Kernel peer
PID and ancestry readers are not mocked. Adoption/authority and model journals
are fixture-only, not live qualification claims.
"""
from pathlib import Path
from unittest.mock import patch
import copy
import json
import multiprocessing
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from orchestration_common import ContractError, canonical, file_ref, json_ref, write_json
from orchestration_native import NativeReader
from test_orchestration_native import NativeFixture
from cmux_daemon_identity import process as native_process
import upgrade_task_continuity_capability as core
import upgrade_task_claude_continuity_route as route

CLIENT = r'''
import json,sys
sys.path.insert(0,sys.argv[1])
import upgrade_task_claude_continuity_route as r
from orchestration_native import NativeReader
from orchestration_common import canonical
r._reader=lambda s: NativeReader(s,fixtures=True)
try:
 if len(sys.argv)>6 and sys.argv[6]=='drop_ack':
  import socket
  ref=json.loads(sys.argv[2]);lane,verb,op=sys.argv[3:6]
  route,endpoint=r._endpoint(ref);reader=r._reader(route['registration']['sessions'][lane])
  expected=r.command(ref,lane,verb,op);calls=[]
  for record_ref,record in reader.records(reader.verify_pin(route['fences'][lane]),reader.snapshot(complete_records=True)['offset']):
   try:r._call(reader,record_ref,expected)
   except ValueError:continue
   calls.append(record_ref)
  assert len(calls)==1
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
   conn.connect(endpoint['socket']['path'])
   conn.sendall(canonical(dict(schema=r.REQUEST,route=ref,lane=lane,verb=verb,operation_id=op,native_call=calls[0])))
   r._frame(conn,r._LIMIT,10)
  sys.exit(7)
 result=r.submit_once(json.loads(sys.argv[2]),sys.argv[3],sys.argv[4],sys.argv[5])
 sys.stdout.buffer.write(canonical(result));sys.stdout.buffer.flush()
except Exception as e:
 print(type(e).__name__+':'+str(e),file=sys.stderr);sys.exit(2)
'''


def lane_parent(pipe):
    pipe.send(os.getpid())
    while True:
        args=pipe.recv()
        if args is None:break
        result=subprocess.run([sys.executable,'-B','-c',CLIENT,*args],capture_output=True,text=True,timeout=20)
        pipe.send(dict(code=result.returncode,stdout=result.stdout,stderr=result.stderr))


def foreign_server(pipe,path):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
        listener.bind(path);os.chmod(path,0o600);listener.listen(1);pipe.send(os.getpid())
        conn,_=listener.accept()
        with conn:
            conn.settimeout(5);pipe.send(conn.recv(16384))


@unittest.skipUnless(sys.platform=='darwin','real kernel process ancestry fixture uses macOS native reader')
class PeerRouteTests(unittest.TestCase):
    def setUp(self):
        # test_all supplies a synthetic ordinary-client process by default.
        # This fixture owns real Python parents and needs their kernel identity;
        # override only the peer route, leaving other offline readers isolated.
        process_patch=patch.object(route,'_process',native_process)
        process_patch.start();self.addCleanup(process_patch.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.pipes={};self.parents={}
        ctx=multiprocessing.get_context('spawn')
        for lane in ('claude_a','claude_b'):
            parent,child=ctx.Pipe();process=ctx.Process(target=lane_parent,args=(child,));process.start()
            self.pipes[lane]=parent;self.parents[lane]=process
            self.assertEqual(parent.recv(),process.pid)
        self.addCleanup(self.stop_parents)
        self.journals={};self.sessions={}
        for lane in ('codex','claude_a','claude_b'):
            directory=self.root/lane;directory.mkdir()
            f=NativeFixture(directory,'codex' if lane=='codex' else 'claude',session_id=str(uuid.uuid4()))
            f.session.update(workspace_uuid=str(uuid.uuid4()),surface_uuid=str(uuid.uuid4()),pane_uuid=str(uuid.uuid4()))
            if lane!='codex':f.session['process']=route._process(self.parents[lane].pid)
            f.session['freeze_fence']=f.reader.snapshot()
            self.journals[lane]=f;self.sessions[lane]=f.session
        self.authority=self.put('authority.json',{'fixture':'original fallback authorization'})
        self.base=self.put('base.json',{'task_id':'task','authority_ref':self.authority})
        self.budget=self.put('budget.json',dict(schema=core.BUDGET,task_id='task',max_subagents=2,
            child_budget_scope='WHOLE_TASK',children_used=['old-child-1','old-child-2'],review_limit=3,
            review_rounds_used=1,review_history=[self.put('review-old.json',{'status':'failed-preserved'})]))
        scope=dict(scope_id='chapter',mode='CLAUDE_PEER',writer_lane='claude_a',output_name='chapter.md',
            inputs=[self.put('input.json',{'scientific':'original complete body'})],
            handoff=self.put('handoff.json',{'limitation':'do not invent history'}))
        for kind,state in [('nonce_state','CLEAR'),('lease_state','NONE')]:
            scope[kind]=self.put(kind+'.json',dict(schema='upgrade-task-continuity-guard-v1',task_id='task',
                scope_id='chapter',kind=kind,state=state,pending=[]))
        checkpoint=self.put('checkpoint.json',dict(schema=core.CHECKPOINT,task_id='task',base_registration=self.base,
            budget=self.budget,scopes=[scope]))
        contract=self.put('contract.json',dict(schema=core.CONTRACT,task_id='task',base_registration=self.base,
            original_authority=self.authority,budget=self.budget,checkpoint=checkpoint,continuity_registration={'fixture':True}))
        self.reg=dict(task_id='task',extension_id='peer-original',base_registration=self.base,authority=self.authority,
            budget=self.budget,runtime=self.put('runtime.json',dict(startup=self.put('startup.json',dict(
                stop_path=str(self.root/'original-stop.json'),adoption_path=str(self.root/'adoption.json'))))),
            adoption=self.put('adoption.json',{'fixture':True}),sessions=self.sessions,
            output_root=str(self.root/'task-capability-extensions/peer-original'),operations={
            'work':dict(action='continuity-work',payload=dict(contract=contract,scope_id='chapter')),
            'result':dict(action='continuity-result',payload=dict(contract=contract,scope_id='chapter',
                work_operation_id='work',statement=None,review_statement=None))})
        Path(self.reg['output_root']).mkdir(parents=True)
        self.extension=write_json(Path(self.reg['output_root'])/'registration.json',self.reg,exclusive=True)
        self.reader_patch=patch.object(route,'_reader',lambda s:NativeReader(s,fixtures=True));self.reader_patch.start();self.addCleanup(self.reader_patch.stop)
        f=self.journals['codex'];self.turn=str(uuid.uuid4())
        self.acceptance=f.append(dict(type='response_item',payload=dict(type='message',role='assistant',
            internal_chat_message_metadata_passthrough=dict(turn_id=self.turn),
            content=[dict(type='output_text',text=canonical(route.scope_value(self.extension,'chapter')).decode())])))
        self.terminal=f.append(dict(type='event_msg',payload=dict(type='task_complete',turn_id=self.turn,last_agent_message='Preaccepted.')))
        with patch('upgrade_task_capability_extension.load_extension',return_value=self.reg), patch('upgrade_live_control.external_publisher'):
            self.ref=route.register_peer_route(self.extension,'chapter',acceptance=self.acceptance,terminal=self.terminal)
        self.owner=route.PeerRoute(self.ref);self.addCleanup(self.close_owner)
        # A new failure after pre-registration must not require another Codex approval.
        f.append(dict(type='event_msg',payload=dict(type='error',message='fixture Codex model disconnected')))
        self.codex_after_failure=f.path.read_bytes()
        self.eval_patch=patch.object(core,'_evaluate',return_value={'collection':{'fixture_native_terminal':'Codex unavailable'},
            'original':{'plan':{'mode':'CLAUDE_PEER'}}});self.eval_patch.start();self.addCleanup(self.eval_patch.stop)
    def close_owner(self):
        self.owner.close();shutil.rmtree(Path(self.owner.endpoint['path']).parent)
    def stop_parents(self):
        for lane,pipe in self.pipes.items():
            if self.parents[lane].is_alive():pipe.send(None)
        for p in self.parents.values():p.join(5)
    def put(self,name,value):return write_json(self.root/name,value,exclusive=True)
    def call(self,lane,verb,op,*,record=True,drop_ack=False):
        f=self.journals[lane];tool_id='tool-'+uuid.uuid4().hex
        cmd=route.command(self.ref,lane,verb,op)
        call=f.append(dict(type='assistant',sessionId=f.session['session_id'],message=dict(role='assistant',
            content=[dict(type='tool_use',id=tool_id,name='Bash',input={'command':cmd})])))
        args=[str(Path(route.__file__).parent),json.dumps(self.ref),lane,verb,op]
        if drop_ack:args.append('drop_ack')
        self.pipes[lane].send(args)
        response=self.owner.serve_once(timeout=10)
        answer=self.pipes[lane].recv();self.assertEqual(answer['code'],0,answer['stderr'])
        self.assertEqual(json.loads(answer['stdout']),response)
        if record:
            f.append(dict(type='user',sessionId=f.session['session_id'],message=dict(role='user',content=[
                dict(type='tool_result',tool_use_id=tool_id,content=answer['stdout'],is_error=False)])))
        return response
    def statement(self,lane,value):
        return self.journals[lane].append(self.journals[lane].message('assistant',canonical(value).decode()))
    def prepare_review(self,*,read_submission=True):
        work=self.call('claude_a','execute','work')['value']
        observed=self.call('claude_b','read-work','work')['value'];self.assertEqual(work,observed)
        self.sub=dict(schema=core.SUBMISSION,task_id='task',extension_id='peer-original',operation_id='result',
            work=work['work'],text='A peer-produced scoped research result.\n',self_review='Preserved scope.',limitations=['Original evidence gap remains.'])
        self.statement('claude_a',self.sub)
        if read_submission:self.assertEqual(self.call('claude_b','read-submission','result')['value'],self.sub)
        self.statement('claude_b',dict(schema=core.REVIEW,task_id='task',extension_id='peer-original',operation_id='result',
            work=work['work'],artifact_sha256=core.digest(self.sub['text'].encode()),verdict='accept',reason='Read the complete original text and its limitation.'))
        return work
    def test_codex_model_disconnected_two_real_peers_complete_file_and_native_roundtrip(self):
        work=self.prepare_review();response=self.call('claude_a','execute','result');value=response['value']['value']
        self.assertEqual(value['validation'],'claude_peer_review');self.assertTrue(value['peer_accepted'])
        self.assertEqual(value['review_round'],2);self.assertEqual(value['children_used'],['old-child-1','old-child-2'])
        self.assertEqual(Path(value['artifact']['path']).read_text(),self.sub['text'])
        returned=self.call('claude_b','read-result','result')
        self.assertEqual(returned['value'],response['value'])
        self.assertEqual(self.journals['codex'].path.read_bytes(),self.codex_after_failure)
        consumed,_,native=self.owner.consumed('claude_b','read-result','result')
        self.assertEqual(consumed,response['value']);self.assertGreater(native['length'],0)
        self.assertEqual(len(work['value']['input_bodies']),2)
        intents=list((self.owner.root/'requests').glob('*/intent.json'))
        peers=[json_ref(file_ref(p))['peer']['peer']['pid'] for p in intents]
        self.assertEqual(len(set(peers)),5);self.assertNotIn(os.getpid(),peers)
    def test_missing_reviewer_full_submission_read_refuses_result(self):
        self.prepare_review(read_submission=False)
        with self.assertRaises((ContractError,FileNotFoundError)):
            self.call('claude_a','execute','result')
        answer=self.pipes['claude_a'].recv();self.assertNotEqual(answer['code'],0)
        self.assertTrue((_dir(self.owner,'claude_a','execute','result')/'unknown.json').exists())
    def test_review_before_full_submission_read_cannot_be_backfilled(self):
        work=self.prepare_review(read_submission=False)
        self.assertEqual(self.call('claude_b','read-submission','result')['value'],self.sub)
        with self.assertRaisesRegex(ContractError,'ONE_ORIGINAL_STATEMENT'):
            self.call('claude_a','execute','result')
        self.assertNotEqual(self.pipes['claude_a'].recv()['code'],0)
        self.assertTrue((_dir(self.owner,'claude_a','execute','result')/'unknown.json').exists())
        artifact=Path(work['value']['output_path'])
        self.assertFalse(artifact.exists())
        self.assertFalse((artifact.parent/'result-intent.json').exists())
    def test_original_stop_refuses_work_before_request_intent(self):
        Path(self.owner.value['stop_paths'][0]).touch()
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):
            self.call('claude_a','execute','work')
        self.assertNotEqual(self.pipes['claude_a'].recv()['code'],0)
        self.assertFalse(_dir(self.owner,'claude_a','execute','work').exists())
    def test_stop_after_work_refuses_result_but_keeps_original_readonly_consumption(self):
        work=self.prepare_review();Path(self.owner.value['stop_paths'][0]).touch()
        observed,_,_=self.owner.consumed('claude_a','execute','work')
        self.assertEqual(observed,work)
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):
            self.call('claude_a','execute','result')
        self.assertNotEqual(self.pipes['claude_a'].recv()['code'],0)
        self.assertFalse(_dir(self.owner,'claude_a','execute','result').exists())
        self.assertFalse(Path(work['value']['output_path']).exists())
    def test_stop_after_result_intent_preserves_unknown_without_artifact(self):
        work=self.prepare_review();original_write=core.write_json
        def stop_after_intent(path,value,**kwargs):
            result=original_write(path,value,**kwargs)
            if Path(path).name=='result-intent.json':Path(self.owner.value['stop_paths'][0]).touch()
            return result
        with patch.object(core,'write_json',side_effect=stop_after_intent):
            with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):
                self.call('claude_a','execute','result')
        self.assertNotEqual(self.pipes['claude_a'].recv()['code'],0)
        artifact=Path(work['value']['output_path'])
        self.assertFalse(artifact.exists());self.assertTrue((artifact.parent/'result-intent.json').exists())
        self.assertTrue((_dir(self.owner,'claude_a','execute','result')/'unknown.json').exists())
    def test_stage_stop_derives_from_original_pinned_stage_authority(self):
        from upgrade_stage_fence import ADOPTION_SCHEMA
        registration=copy.deepcopy(self.reg);runtime=json_ref(registration['runtime'])
        control=self.root/'stage';control.mkdir()
        stage=self.put('stage/stage.json',dict(control=str(control),original_startup=runtime['startup']))
        registration['adoption']=self.put('stage/adoption.json',dict(schema=ADOPTION_SCHEMA,
            runtime=registration['runtime'],startup=runtime['startup'],stage=stage))
        self.assertEqual(route._execution_stop_paths(registration),[str(self.root/'original-stop.json'),str(control/'stop.json')])
    def test_changed_original_approval_cannot_be_used_after_registration(self):
        p=self.journals['codex'].path;raw=p.read_bytes();p.write_bytes(raw.replace(b'Preaccepted.',b'Not accepted'))
        with self.assertRaises(ContractError):self.owner.verify()
    def test_original_attempt_is_not_resubmitted_from_same_native_call(self):
        self.call('claude_a','execute','work')
        self.pipes['claude_a'].send([str(Path(route.__file__).parent),json.dumps(self.ref),'claude_a','execute','work'])
        with self.assertRaisesRegex(ContractError,'CONSUMED_NO_REPLAY'):self.owner.serve_once(timeout=10)
        self.assertNotEqual(self.pipes['claude_a'].recv()['code'],0)
    def test_peer_scope_cannot_be_claimed_by_controller_or_unrelated_process(self):
        # An actual local socket with the controller PID is not a Claude descendant.
        import threading
        errors=[]
        def serve():
            try:self.owner.serve_once(timeout=5)
            except Exception as exc:errors.append(exc)
        f=self.journals['claude_a'];cmd=route.command(self.ref,'claude_a','execute','work')
        call=f.append(dict(type='assistant',sessionId=f.session['session_id'],message=dict(role='assistant',
            content=[dict(type='tool_use',id='foreign',name='Bash',input={'command':cmd})])))
        thread=threading.Thread(target=serve);thread.start()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.connect(self.owner.endpoint['path']);conn.sendall(canonical(dict(schema=route.REQUEST,route=self.ref,
                lane='claude_a',verb='execute',operation_id='work',native_call=call)))
            conn.recv(32)
        thread.join(5);self.assertTrue(errors);self.assertFalse((_dir(self.owner,'claude_a','execute','work')).exists())
    def test_preapproval_after_fault_or_new_work_cannot_register(self):
        with patch('upgrade_task_capability_extension.load_extension',return_value=self.reg),patch('upgrade_live_control.external_publisher'):
            with self.assertRaisesRegex(ContractError,'REGISTER_BEFORE'):
                route.register_peer_route(self.extension,'chapter',acceptance=self.acceptance,terminal=self.terminal)
    def test_client_rejects_real_foreign_server_before_disclosing_request(self):
        f=self.journals['claude_a'];cmd=route.command(self.ref,'claude_a','execute','work')
        f.append(dict(type='assistant',sessionId=f.session['session_id'],message=dict(role='assistant',
            content=[dict(type='tool_use',id='request',name='Bash',input={'command':cmd})])))
        endpoint=Path(self.owner.endpoint['path']);endpoint.unlink()
        parent,child=multiprocessing.get_context('spawn').Pipe()
        proc=multiprocessing.get_context('spawn').Process(target=foreign_server,args=(child,str(endpoint)))
        proc.start();self.assertNotEqual(parent.recv(),os.getpid())
        descriptor=dict(route=self.ref,owner=self.owner.value['owner'],socket=route.socket_pin(endpoint))
        Path(self.owner.descriptor['path']).write_bytes(canonical(descriptor))
        with self.assertRaisesRegex(ContractError,'REAL_OWNER_REQUIRED'):
            route.submit_once(self.ref,'claude_a','execute','work')
        self.assertEqual(parent.recv(),b'');proc.join(5);self.assertEqual(proc.exitcode,0)
    def test_slow_partial_frame_obeys_total_one_second_deadline(self):
        errors=[]
        def serve():
            try:self.owner.serve_once(timeout=5)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=serve);thread.start();start=time.monotonic()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.connect(self.owner.endpoint['path'])
            for _ in range(8):
                try:conn.sendall(b'{')
                except BrokenPipeError:break
                time.sleep(.2)
        thread.join(3)
        self.assertFalse(thread.is_alive());self.assertTrue(errors)
        self.assertLess(time.monotonic()-start,2.5)
        self.assertFalse((self.owner.root/'requests').exists())
    def test_lost_result_ack_blocks_read_result_without_original_native_consumption(self):
        self.prepare_review()
        with self.assertRaises(ContractError):self.call('claude_a','execute','result',drop_ack=True)
        self.assertEqual(self.pipes['claude_a'].recv()['code'],7)
        directory=_dir(self.owner,'claude_a','execute','result')
        self.assertTrue((directory/'response.json').exists());self.assertTrue((directory/'unknown.json').exists())
        with self.assertRaisesRegex(ContractError,'ONE_COMPLETE_NATIVE_RESULT'):
            self.call('claude_b','read-result','result')
        self.assertNotEqual(self.pipes['claude_b'].recv()['code'],0)
        self.assertFalse((_dir(self.owner,'claude_b','read-result','result')/'response.json').exists())
    def test_any_unknown_attempt_blocks_further_mutation(self):
        self.call('claude_a','execute','work',record=False)
        with self.assertRaisesRegex(ContractError,'ONE_COMPLETE_NATIVE_RESULT'):
            self.call('claude_b','read-work','work')
        self.assertNotEqual(self.pipes['claude_b'].recv()['code'],0)
        with self.assertRaisesRegex(ContractError,'UNKNOWN_READONLY'):
            self.call('claude_a','execute','result')
        self.assertNotEqual(self.pipes['claude_a'].recv()['code'],0)


def _dir(owner,lane,verb,op):return route._directory(owner.root,lane,verb,op)
if __name__=='__main__':unittest.main()
