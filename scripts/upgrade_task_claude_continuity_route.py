"""Preaccepted, one-request Claude peer route; no Codex live RPC dependency.

The retained controller owns this endpoint. Two existing Claude Bash children
are authenticated by kernel peer/ancestry and original native tool records.
Importing/registering starts no listener, process, session or model request.
"""
from pathlib import Path
import argparse
import copy
import os
import shlex
import socket
import stat
import sys
import time

from orchestration_common import (canonical, checked_path, digest, exclusive_lock,
    file_ref, json_ref, object_sha, read_ref, require, strict_json, utc_now, write_json)
from orchestration_native import NativeReader
from upgrade_rpc_peer import kernel_peer
from upgrade_task_callback_turn import caller_ancestry
from upgrade_task_callback_ingress import socket_pin, process_pin
from upgrade_task_capability_extension import _assistant
import upgrade_task_continuity_capability as continuity

SCHEMA = 'upgrade-task-claude-continuity-route-v1'
REQUEST = 'upgrade-task-claude-continuity-request-v1'
RESPONSE = 'upgrade-task-claude-continuity-response-v1'
APPROVAL = 'upgrade-task-claude-continuity-preapproval-v1'
VERBS = frozenset({'execute', 'read-work', 'read-submission', 'read-result'})
_KEY = object()
_LIMIT = 1024 * 1024


def _frame(connection, limit, seconds):
    """One whole frame deadline, including slow partial reads."""
    deadline=time.monotonic()+seconds;raw=bytearray()
    while b'\n' not in raw:
        remaining=deadline-time.monotonic()
        require(remaining>0,'PEER_FRAME_DEADLINE')
        connection.settimeout(remaining)
        part=connection.recv(min(65536,limit+1-len(raw)))
        require(part,'PEER_INCOMPLETE_FRAME');raw.extend(part)
        require(len(raw)<=limit,'PEER_FRAME_LIMIT')
    require(raw.endswith(b'\n') and raw.count(b'\n')==1,'PEER_ONE_FRAME_REQUIRED')
    return bytes(raw)


def _process(pid):
    from cmux_daemon_identity import process
    return process(pid)


def _reader(session):
    return NativeReader(session)


def _execution_stop_paths(registration):
    """Original immutable authority paths; observing them needs no model RPC."""
    from upgrade_live_runtime import _stage_routed
    from upgrade_stage_fence import ADOPTION_SCHEMA
    runtime=json_ref(registration['runtime']);startup=json_ref(runtime['startup'])
    paths=[str(checked_path(startup['stop_path']))]
    adoption=json_ref(registration['adoption'])
    staged=_stage_routed(runtime,registration['adoption'])
    require(staged==(adoption.get('schema')==ADOPTION_SCHEMA),'PEER_STOP_AUTHORITY_CHANGED')
    if staged:
        stage_ref=adoption['stage'];stage=json_ref(stage_ref);control=checked_path(stage['control'])
        require(adoption['runtime']==registration['runtime'] and adoption['startup']==runtime['startup']
            and stage['original_startup']==runtime['startup'] and Path(stage_ref['path']).parent==control
            and Path(registration['adoption']['path'])==control/'adoption.json','PEER_STAGE_STOP_AUTHORITY_CHANGED')
        paths.append(str(control/'stop.json'))
    return paths


def scope_value(extension_ref, scope_id):
    """Exact extra permission accepted BEFORE a normal original turn ends."""
    return dict(schema=APPROVAL, extension=extension_ref, scope_id=scope_id,
        independent_claude_peer_authorized=True, max_requests_per_lane_verb_operation=1,
        codex_live_rpc_required=False, new_sessions=0, resource_releases=0)


def _verify_preapproval(extension_ref, scope_id, acceptance, terminal, registration):
    reader = _reader(registration['sessions']['codex'])
    value, record = _assistant(reader, acceptance)
    require(value == scope_value(extension_ref, scope_id), 'PEER_EXACT_PREACCEPTANCE_REQUIRED')
    turn = record['payload'].get('internal_chat_message_metadata_passthrough', {}).get('turn_id')
    end = reader.record(terminal); payload = end.get('payload', {})
    require(isinstance(turn,str) and end.get('type') == 'event_msg'
        and payload.get('type') == 'task_complete' and payload.get('turn_id') == turn
        and not payload.get('error') and terminal['offset'] > acceptance['offset'],
        'PEER_NORMAL_PREFAULT_TERMINAL_REQUIRED')
    latest = reader.snapshot(complete_records=True)
    for _, following in reader.records(terminal['offset'] + terminal['length'], latest['offset']):
        require(following.get('type') == 'event_msg' and following.get('payload', {}).get('type') == 'token_count',
                'PEER_REGISTER_BEFORE_NEW_WORK_OR_FAILURE')
    return dict(acceptance=acceptance, terminal=terminal, native_fence=latest)


def register_peer_route(extension_ref, scope_id, *, acceptance, terminal):
    """Inert fixed registration; original owner approval cannot occur post-fault."""
    from upgrade_task_capability_extension import load_extension
    from upgrade_live_control import external_publisher
    registration = load_extension(extension_ref)
    runtime = json_ref(registration['runtime']); external_publisher(json_ref(runtime['startup']))
    selected = {name:op for name,op in registration['operations'].items()
                if op['action'] in continuity.ACTIONS and op['payload']['scope_id'] == scope_id}
    require(len(selected) == 2 and {op['action'] for op in selected.values()} == continuity.ACTIONS,
            'PEER_FIXED_WORK_RESULT_PAIR_REQUIRED')
    work_id = next(name for name,op in selected.items() if op['action'] == 'continuity-work')
    result_id = next(name for name,op in selected.items() if op['action'] == 'continuity-result')
    contract, _, scopes = continuity._contract(selected[work_id]['payload']['contract'], registration)
    scope = scopes[scope_id]
    require(scope['mode'] == 'CLAUDE_PEER' and selected[result_id]['payload']['work_operation_id'] == work_id,
            'PEER_FIXED_ORIGINAL_CLAUDE_SCOPE_REQUIRED')
    sessions = registration['sessions']
    require(sessions['claude_a']['session_id'] != sessions['claude_b']['session_id']
        and sessions['claude_a']['process']['pid'] != sessions['claude_b']['process']['pid'],
        'PEER_TWO_DISTINCT_ORIGINAL_CLAUDES_REQUIRED')
    for lane in ('claude_a','claude_b'):
        expected = sessions[lane]['process']; current = _process(expected['pid'])
        require(all(current.get(k) == v for k,v in expected.items()), 'PEER_ORIGINAL_CLAUDE_CHANGED')
    proof = _verify_preapproval(extension_ref, scope_id, acceptance, terminal, registration)
    root = checked_path(registration['output_root'])/'claude-peer'
    root.mkdir(mode=0o700, exist_ok=False)
    paths = [Path(__file__).resolve(), Path(continuity.__file__).resolve()]
    value = dict(schema=SCHEMA, extension=extension_ref, registration=registration,
        scope_id=scope_id, work_operation=work_id, result_operation=result_id,
        writer=scope['writer_lane'], reviewer='claude_b' if scope['writer_lane']=='claude_a' else 'claude_a',
        preapproval=proof, fences={lane:_reader(sessions[lane]).snapshot(complete_records=True)
            for lane in ('claude_a','claude_b')},
        owner=process_pin(_process,os.getpid()), python=file_ref(Path(sys.executable).resolve()),
        sources=[file_ref(p) for p in paths], stop_paths=_execution_stop_paths(registration),
        root=str(root), registered_at=utc_now(),
        input_operations=0, codex_live_rpc_required=False)
    return write_json(root/'registration.json',value,exclusive=True)


def load_route(ref):
    value = json_ref(ref)
    require(value.get('schema') == SCHEMA and ref['path'] == str(Path(value['root'])/'registration.json'),
            'PEER_ROUTE_REGISTRATION_CHANGED')
    registration = json_ref(value['extension'])
    require(registration == value['registration'] and Path(value['root']) ==
            checked_path(registration['output_root'])/'claude-peer', 'PEER_ORIGINAL_EXTENSION_CHANGED')
    # Re-read fixed original authority/source proofs without contacting Codex.
    read_ref(registration['base_registration']); read_ref(registration['authority']); read_ref(registration['budget'])
    for pin in [value['python'], *value['sources']]: read_ref(pin)
    continuity._contract(registration['operations'][value['work_operation']]['payload']['contract'],registration)
    require(value['stop_paths']==_execution_stop_paths(registration),'PEER_STOP_PATHS_CHANGED')
    pre=value['preapproval'];reader=_reader(registration['sessions']['codex'])
    accepted,_=_assistant(reader,pre['acceptance'])
    require(accepted==scope_value(value['extension'],value['scope_id']),'PEER_PREACCEPTANCE_CHANGED')
    reader.record(pre['terminal']);reader.verify_pin(pre['native_fence'])
    return value


def command(route_ref,lane,verb,operation_id):
    route=load_route(route_ref)
    return shlex.join([route['python']['path'],'-B',route['sources'][0]['path'],'request',
        '--route',route_ref['path'],'--route-sha256',route_ref['sha256'],
        '--lane',lane,'--verb',verb,'--operation',operation_id])


def _call(reader,ref,expected):
    record=reader.record(ref);message=record.get('message',{});blocks=message.get('content',[])
    require(record.get('type')=='assistant' and record.get('sessionId')==reader.session['session_id']
        and record.get('isSidechain') is not True and message.get('role')=='assistant', 'PEER_ORIGINAL_BASH_CALL_REQUIRED')
    found=[x for x in blocks if x.get('type')=='tool_use' and x.get('name')=='Bash'
           and x.get('input',{}).get('command')==expected]
    require(len(found)==1 and isinstance(found[0].get('id'),str)
        and set(found[0]['input']) <= {'command','description','timeout'}, 'PEER_FIXED_BASH_CALL_REQUIRED')
    return found[0]['id']


def _native_output(reader,call_ref,call_id,response,until):
    found=[]
    for ref,record in reader.records(call_ref['offset']+call_ref['length'],until):
        if record.get('type')!='user' or record.get('sessionId')!=reader.session['session_id'] or record.get('isSidechain') is True:continue
        message=record.get('message',{})
        for block in message.get('content',[]):
            if block.get('type')!='tool_result' or block.get('tool_use_id')!=call_id:continue
            raw=block.get('content')
            if isinstance(raw,list) and len(raw)==1 and raw[0].get('type')=='text':raw=raw[0].get('text')
            require(not block.get('is_error') and isinstance(raw,str) and strict_json(raw)==response,
                    'PEER_FULL_NATIVE_BASH_RESULT_REQUIRED')
            found.append(ref)
    require(len(found)==1,'PEER_ONE_COMPLETE_NATIVE_RESULT_REQUIRED')
    return found[0]


def _directory(root,lane,verb,op):return Path(root)/'requests'/object_sha([lane,verb,op])


class PeerContext:
    def __init__(self,key,owner,connection,request,directory):
        require(key is _KEY,'PEER_LIVE_CONTEXT_REQUIRED')
        self.owner,self.connection,self.request,self.directory=owner,connection,request,directory
        self.registration=owner.value['registration'];self.registration_ref=owner.value['extension']
        self.sessions=self.registration['sessions'];self.task_id=self.registration['task_id']
        self.operation_id=request['operation_id'];self._active=True
    def reader(self,lane):return _reader(self.sessions[lane])
    def recheck(self):
        require_context(self);self.owner.verify_sender(self.connection,self.request)
        if self.request['verb']=='execute':self.owner.verify_execution()
    def assert_operation(self,action,payload):
        self.recheck();require(self.registration['operations'][self.operation_id]==dict(action=action,payload=payload),
                              'PEER_OPERATION_CHANGED')
    def native_statement(self,ref,schema,lane='codex',*,after=0):
        require(lane in (self.owner.value['writer'],self.owner.value['reviewer']),'PEER_CLAUDE_STATEMENT_REQUIRED')
        if schema==continuity.REVIEW:
            require(lane==self.owner.value['reviewer'],'PEER_ORIGINAL_REVIEWER_REQUIRED')
            _,_,read_native=self.owner.consumed(lane,'read-submission',self.owner.value['result_operation'])
            after=max(after,read_native['offset']+read_native['length'])
        reader=self.reader(lane);until=self.request['native_call']['offset'] if lane==self.request['lane'] else reader.snapshot(complete_records=True)['offset']
        refs=[ref] if ref is not None else [r for r,_ in reader.records(after,until)]
        found=[]
        for item in refs:
            require(item['offset']>=after and item['offset']+item['length']<=until,'PEER_STATEMENT_WINDOW_CHANGED')
            try:value,record=_assistant(reader,item)
            except ValueError:
                if ref is not None:raise
                continue
            if not isinstance(value,dict) or value.get('schema')!=schema:continue
            require(record.get('isSidechain') is not True and value.get('task_id')==self.task_id
                and value.get('extension_id')==self.registration['extension_id']
                and value.get('operation_id')==self.owner.value['result_operation'],'PEER_STATEMENT_SCOPE_CHANGED')
            found.append(value)
        require(len(found)==1,'PEER_ONE_ORIGINAL_STATEMENT_REQUIRED');return found[0]
    def completed_operation(self,operation_id):
        require(operation_id==self.owner.value['work_operation'],'PEER_ORIGINAL_WORK_REQUIRED')
        writer=self.owner.value['writer'];reviewer=self.owner.value['reviewer']
        value,receipt,native=self.owner.consumed(writer,'execute',operation_id)
        reviewed,_,_=self.owner.consumed(reviewer,'read-work',operation_id)
        require(reviewed==value,'PEER_BOTH_FULL_WORK_READS_REQUIRED')
        submission,_,_=self.owner.consumed(reviewer,'read-submission',self.owner.value['result_operation'])
        work=json_ref(value['work'])
        current=self.native_statement(None,continuity.SUBMISSION,lane=writer,
            after=self.reader(writer).verify_pin(work['statements_after'][writer]))
        require(submission==current,'PEER_REVIEWER_FULL_SUBMISSION_READ_REQUIRED')
        return receipt,value,native


def require_context(context):
    require(type(context) is PeerContext and context._active and type(context.owner) is PeerRoute
        and context.owner.active is context and context.registration is context.owner.value['registration'],
        'PEER_LIVE_CONTEXT_REQUIRED')
    context.owner.verify();return context


class PeerRoute:
    """Explicit synchronous endpoint in the retained owner; no model RPC calls."""
    def __init__(self,route_ref):
        self.ref=route_ref;self.value=load_route(route_ref);self.root=Path(self.value['root'])
        self.active=None;self.closed=False
        require(process_pin(_process,os.getpid())==self.value['owner'],'PEER_ORIGINAL_CONTROLLER_REQUIRED')
        endpoint=Path('/tmp').resolve()/('ucp-'+str(os.geteuid())+'-'+object_sha(route_ref)[:20])
        endpoint.mkdir(mode=0o700,exist_ok=False)
        self.listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.listener.set_inheritable(False)
        try:
            self.listener.bind(str(endpoint/'s'));(endpoint/'s').chmod(0o600);self.listener.listen(2)
            self.endpoint=socket_pin(endpoint/'s')
            self.descriptor=write_json(self.root/'endpoint.json',dict(route=route_ref,owner=self.value['owner'],
                socket=self.endpoint),exclusive=True)
        except BaseException:self.listener.close();raise
    def verify(self):
        require(not self.closed and process_pin(_process,os.getpid())==self.value['owner']
            and load_route(self.ref)==self.value and socket_pin(self.endpoint['path'])==self.endpoint
            and json_ref(self.descriptor)==dict(route=self.ref,owner=self.value['owner'],socket=self.endpoint),
            'PEER_ENDPOINT_OR_OWNER_CHANGED')
    def verify_execution(self):
        self.verify()
        require(not any(os.path.lexists(path) for path in self.value['stop_paths']),
                'EXPERIMENT_STOP_REQUESTED')
    def verify_sender(self,connection,request):
        self.verify();require(set(request)=={'schema','route','lane','verb','operation_id','native_call'}
            and request['schema']==REQUEST and request['route']==self.ref,'PEER_FIXED_REQUEST_REQUIRED')
        lane,verb,op=request['lane'],request['verb'],request['operation_id']
        require(lane in ('claude_a','claude_b') and verb in VERBS,'PEER_REQUEST_SCOPE_INVALID')
        writer,reviewer=self.value['writer'],self.value['reviewer']
        allowed={(writer,'execute',self.value['work_operation']),(writer,'execute',self.value['result_operation']),
                 (reviewer,'read-work',self.value['work_operation']),
                 (reviewer,'read-submission',self.value['result_operation']),
                 (writer,'read-result',self.value['result_operation']),
                 (reviewer,'read-result',self.value['result_operation'])}
        require((lane,verb,op) in allowed,'PEER_LANE_OPERATION_NOT_AUTHORIZED')
        proof=caller_ancestry(connection,self.value['registration']['sessions'][lane],_process)
        reader=_reader(self.value['registration']['sessions'][lane]);ref=request['native_call']
        require(ref['offset']>=reader.verify_pin(self.value['fences'][lane]),'PEER_CALL_PREDATES_ENROLLMENT')
        call_id=_call(reader,ref,command(self.ref,lane,verb,op))
        return proof,call_id
    def consumed(self,lane,verb,op):
        directory=_directory(self.root,lane,verb,op);response=json_ref(file_ref(directory/'response.json'))
        require(response['status']=='RECORDED' and response['retry_allowed'] is False,'PEER_ORIGINAL_RESPONSE_REQUIRED')
        receipt=response['receipt'];record=json_ref(receipt);request=json_ref(record['intent'])['request']
        reader=_reader(self.value['registration']['sessions'][lane]);call_id=_call(reader,request['native_call'],command(self.ref,lane,verb,op))
        native=_native_output(reader,request['native_call'],call_id,response,reader.snapshot(complete_records=True)['offset'])
        return record['value'],receipt,native
    def _value(self,context):
        request=context.request;verb=request['verb'];op=request['operation_id']
        if verb=='execute':
            require(not list((self.root/'requests').glob('*/unknown.json')),
                    'PEER_UNKNOWN_READONLY_RECONCILIATION_REQUIRED')
            selected=context.registration['operations'][op]
            return continuity.dispatch(selected['action'],selected['payload'],context)
        work_value,_,_=self.consumed(self.value['writer'],'execute',self.value['work_operation'])
        if verb=='read-work':return work_value
        if verb=='read-submission':
            self.consumed(self.value['reviewer'],'read-work',self.value['work_operation'])
            work=json_ref(work_value['work']);writer=self.value['writer']
            return context.native_statement(None,continuity.SUBMISSION,lane=writer,
                after=context.reader(writer).verify_pin(work['statements_after'][writer]))
        require(verb=='read-result','PEER_FIXED_VERB_REQUIRED')
        value,_,_=self.consumed(self.value['writer'],'execute',self.value['result_operation'])
        return value
    def serve_once(self,*,timeout=5):
        """One accept, frame, intent and response; uncertainty never retries."""
        self.verify();require(self.active is None,'PEER_NO_REENTRANT_DISPATCH')
        require(type(timeout) in (int,float) and 0<timeout<=60,'PEER_ACCEPT_TIMEOUT_REQUIRED')
        self.listener.settimeout(timeout);connection,_=self.listener.accept()
        with connection:
            request=strict_json(_frame(connection,16384,1));proof,call_id=self.verify_sender(connection,request)
            directory=_directory(self.root,request['lane'],request['verb'],request['operation_id'])
            with exclusive_lock(self.root/'route.lock'):
                if request['verb']=='execute':self.verify_execution()
                require(not directory.exists(),'PEER_ORIGINAL_ATTEMPT_CONSUMED_NO_REPLAY')
                directory.mkdir(mode=0o700,parents=True)
                intent=write_json(directory/'intent.json',dict(request=request,peer=proof,call_id=call_id,
                    route=self.ref,retry_allowed=False),exclusive=True)
                context=PeerContext(_KEY,self,connection,request,directory);self.active=context
                try:
                    value=self._value(context);context.recheck()
                    receipt=write_json(directory/'result.json',dict(intent=intent,value=value),exclusive=True)
                    response=dict(schema=RESPONSE,status='RECORDED',route=self.ref,receipt=receipt,value=value,
                        retry_allowed=False,native_consumption_verified=False,
                        readonly_original_evidence=request['verb']!='execute',
                        prior_unknown_attempts=[file_ref(p) for p in sorted((self.root/'requests').glob('*/unknown.json'))])
                    require(len(canonical(response))<=_LIMIT,'PEER_RESPONSE_LIMIT')
                    write_json(directory/'response.json',response,exclusive=True)
                    self.verify_sender(connection,request);connection.settimeout(5);connection.sendall(canonical(response))
                    ack=_frame(connection,4,1);require(ack==b'ACK\n','PEER_RESPONSE_ACK_UNKNOWN')
                    self.verify_sender(connection,request)
                    write_json(directory/'host-ack.json',dict(ack=True,native_consumption_verified=False),exclusive=True)
                    connection.sendall(b'DONE\n')
                    return response
                except BaseException as exc:
                    write_json(directory/'unknown.json',dict(intent=intent,error=type(exc).__name__,retry_allowed=False),exclusive=True)
                    raise
                finally:context._active=False;self.active=None
    def close(self):
        self.closed=True;self.listener.close()  # Directory/socket evidence remains.


def _endpoint(route_ref):
    route=load_route(route_ref);descriptor=json_ref(file_ref(Path(route['root'])/'endpoint.json'))
    require(descriptor['route']==route_ref and descriptor['owner']==route['owner'],'PEER_CLIENT_ENDPOINT_CHANGED')
    require(socket_pin(descriptor['socket']['path'])==descriptor['socket'],'PEER_CLIENT_SOCKET_CHANGED')
    return route,descriptor


def submit_once(route_ref,lane,verb,operation_id):
    """Original Claude Bash-only client; stdout is the complete single result."""
    route,endpoint=_endpoint(route_ref);reader=_reader(route['registration']['sessions'][lane])
    expected=command(route_ref,lane,verb,operation_id);found=[]
    for ref,record in reader.records(reader.verify_pin(route['fences'][lane]),reader.snapshot(complete_records=True)['offset']):
        try:_call(reader,ref,expected)
        except ValueError:continue
        found.append(ref)
    require(len(found)==1,'PEER_ONE_ORIGINAL_BASH_INVOCATION_REQUIRED')
    request=dict(schema=REQUEST,route=route_ref,lane=lane,verb=verb,operation_id=operation_id,native_call=found[0])
    def peer_check(connection):
        require(_endpoint(route_ref)==(route,endpoint),'PEER_CLIENT_DESCRIPTOR_CHANGED')
        peer=kernel_peer(connection)
        require(peer==dict(pid=endpoint['owner']['pid'],uid=os.geteuid()) and
                process_pin(_process,peer['pid'])==endpoint['owner'],'PEER_CLIENT_REAL_OWNER_REQUIRED')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.set_inheritable(False);connection.settimeout(10);connection.connect(endpoint['socket']['path'])
        peer_check(connection)  # Never disclose the request to an unverified endpoint.
        connection.sendall(canonical(request));raw=_frame(connection,_LIMIT,10)
        peer_check(connection)
        response=strict_json(raw);require(response.get('schema')==RESPONSE and response.get('status')=='RECORDED'
            and response.get('route')==route_ref and response.get('retry_allowed') is False,'PEER_RESPONSE_CHANGED')
        receipt=json_ref(response['receipt']);require(json_ref(receipt['intent'])['request']==request
            and receipt['value']==response['value'],'PEER_EXACT_RESULT_REQUIRED')
        connection.sendall(b'ACK\n');require(_frame(connection,5,1)==b'DONE\n','PEER_FINAL_ACK_UNKNOWN')
        # The verified server may close immediately after DONE; macOS can no
        # longer query LOCAL_PEERPID on that closed connection. Recheck the
        # immutable descriptor and process, not a disconnected socket.
        require(_endpoint(route_ref)==(route,endpoint) and
            process_pin(_process,endpoint['owner']['pid'])==endpoint['owner'], 'PEER_FINAL_OWNER_CHANGED')
        return response


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['request'])
    parser.add_argument('--route',required=True);parser.add_argument('--route-sha256',required=True)
    parser.add_argument('--lane',choices=['claude_a','claude_b'],required=True)
    parser.add_argument('--verb',choices=sorted(VERBS),required=True);parser.add_argument('--operation',required=True)
    args=parser.parse_args(argv);ref=file_ref(args.route);require(ref['sha256']==args.route_sha256,'PEER_ROUTE_PIN_CHANGED')
    response=submit_once(ref,args.lane,args.verb,args.operation)
    sys.stdout.buffer.write(canonical(response));sys.stdout.buffer.flush()

if __name__=='__main__':main()
