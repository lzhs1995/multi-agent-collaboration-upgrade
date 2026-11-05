"""Original Claude callback lock scope for a new generated TASK sender.

New .84 generation enters this scope from the original callback journal while
its delivery/pane/target locks are held. Ordinary messages and task dispatches
do not acquire this callback-only capability.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import os
from pathlib import Path
import stat
import time

from orchestration_common import file_ref, json_ref, require, write_json
import upgrade_task_callback_client as ingress_client

ROUTE_SCHEMA='upgrade-task-callback-native-route-v1'
_current=ContextVar('original_claude_callback_scope',default=None)


class CallbackSender:
    def __init__(self,bridge,pack_path,attempt_path,locks):
        self.bridge=bridge;self.pid=os.getpid()
        self.pack=file_ref(Path(pack_path));self.value=bridge.validate_task_pack_contract(Path(pack_path))
        self.attempt_path=Path(attempt_path)
        receipt=Path(self.value['completion_receipt'])
        self.journal=receipt.with_name(receipt.stem+'-attempts')
        require(self.attempt_path.parent==self.journal and self.attempt_path.name.startswith('attempt-'),
                'TASK_CALLBACK_SENDER_ORIGINAL_ATTEMPT_REQUIRED')
        require(len(locks)==3,'TASK_CALLBACK_SENDER_ORIGINAL_LOCKS_REQUIRED')
        self.locks=tuple(locks)
        self.identity=bridge.pin_workspace(self.value['callback_target'])
        require(self.identity['caller_surface_uuid'].upper()==self.value['executor_uuid'].upper(),
                'TASK_CALLBACK_SENDER_ORIGINAL_EXECUTOR_REQUIRED')
        self.report=file_ref(self.value['report'])
        self.request=None;self.sent=False
        self.check()

    def check(self):
        require(self.pid==os.getpid() and file_ref(self.pack['path'])==self.pack
                and file_ref(self.report['path'])==self.report
                and self.bridge.pin_workspace(self.value['callback_target'])==self.identity,
                'TASK_CALLBACK_SENDER_ORIGINAL_SCOPE_CHANGED')
        for held,path in self.locks:
            opened,current=os.fstat(held.fileno()),Path(path).lstat()
            require(stat.S_ISREG(current.st_mode) and current.st_nlink==1
                    and (opened.st_dev,opened.st_ino)==(current.st_dev,current.st_ino),
                    'TASK_CALLBACK_SENDER_ORIGINAL_LOCK_CHANGED')
        require(Path(self.locks[0][1])==self.journal/'delivery.lock',
                'TASK_CALLBACK_SENDER_DELIVERY_LOCK_CHANGED')
        require(sorted(self.journal.glob('attempt-*.json'))[-1]==self.attempt_path,
                'TASK_CALLBACK_SENDER_LATEST_ATTEMPT_CHANGED')
        return json_ref(file_ref(self.attempt_path))


@contextmanager
def scope(bridge,pack_path,attempt_path,locks):
    require(_current.get() is None,'TASK_CALLBACK_SENDER_SCOPE_ALREADY_ACTIVE')
    owner=CallbackSender(bridge,pack_path,attempt_path,locks)
    token=_current.set(owner)
    try:yield owner
    finally:_current.reset(token)


def route_request(binding,request,text):
    """Describe the exact callback route without sending or consuming intent."""
    owner=_current.get()
    require(type(owner) is CallbackSender and request.get('method')=='turn/start'
            and request.get('observed_status')=='idle' and owner.value['completion_callback']==text,
            'TASK_TARGET_EXTERNAL_TURN_ENROLLMENT_REQUIRED')
    old=owner.check()
    require(old['native_binding']==binding and old['phase']=='PREPARED' and old['events']==[],
            'TASK_CALLBACK_SENDER_FRESH_ATTEMPT_REQUIRED')
    ref,_=ingress_client.descriptor(binding)
    result=copy.deepcopy(request)
    result['task_callback_route']=dict(schema=ROUTE_SCHEMA,ingress=ref,pack=owner.pack,
                                      attempt=str(owner.attempt_path))
    require(owner.request is None,'TASK_CALLBACK_SENDER_REQUEST_ALREADY_PREPARED')
    owner.request=result
    return result


def submit_callback(bridge,pack_path,attempt_path,locks,*args,**kwargs):
    """Preserve the original bridge call and lock objects for TASK callbacks."""
    binding=kwargs.get('native_binding',{})
    if binding.get('schema')!='native-delivery-task-v1':
        return bridge.submit_text(*args,**kwargs)
    with scope(bridge,pack_path,attempt_path,locks):
        return bridge.submit_text(*args,**kwargs)


def send_idle_once(binding,request,text,*,recheck,emit,observe,wait_seconds):
    """Called only by the new generated sender while original locks are held."""
    owner=_current.get()
    require(type(owner) is CallbackSender and not owner.sent and owner.request==request,
            'TASK_CALLBACK_SENDER_ORIGINAL_REQUEST_REQUIRED')
    from cmux_codex_input import validate_request,rpc_error_details
    from cmux_confirmation import observe_receipt,annotate_error
    from upgrade_input_gate import check_input
    import cmux_daemon_identity as identity
    validate_request(request,binding,text)
    recheck();owner.check()
    # This idle route returns before the original direct sender's input gate.
    # Keep that same membership/kernel/STOP gate before consuming its intent.
    check_input(binding['identity'],native_binding=binding)
    emit('NATIVE_INPUT_INTENT',evidence=dict(native_request=request))
    owner.sent=True
    attempt=file_ref(owner.attempt_path)
    intended=json_ref(attempt)
    require(intended['phase']=='NATIVE_INPUT_INTENT'
            and intended['events'][-1]['native_request']==request,
            'TASK_CALLBACK_SENDER_ORIGINAL_INTENT_REQUIRED')
    sequence=0
    def record(phase,value):
        nonlocal sequence
        sequence+=1
        write_json(owner.attempt_path.with_name('ingress-'+owner.attempt_path.stem+f'-{sequence:02d}.json'),
            dict(phase=phase,value=value,attempt=attempt),exclusive=True)
    def check():
        recheck();owner.check()
        require(file_ref(owner.attempt_path)==attempt,'TASK_CALLBACK_SENDER_INTENT_CHANGED')
    began=time.monotonic();outcome='RPC_ERROR_OR_UNKNOWN';turn=None;result_ref=None
    try:
        check()
        response=ingress_client.submit_once(binding,owner.pack,attempt,
                    process=identity.process,recheck=check,record=record)
        turn=response['result']['turn_id'];result_ref=response['result']['callback_input']
        outcome='RPC_ACCEPTED'
        diagnostic=dict(method='turn/start',turn_id=turn,rpc_outcome=outcome,
                        callback_input=result_ref)
    except (OSError,RuntimeError,ValueError,KeyError,TypeError) as exc:
        diagnostic=dict(rpc_error_details(exc),method='turn/start',rpc_outcome=outcome)
    journal_error=None
    try:emit('NATIVE_INPUT_RESULT',evidence=diagnostic)
    except (OSError,RuntimeError,ValueError,TypeError) as exc:
        journal_error=dict(error_type=type(exc).__name__,error=str(exc)[:1000])
    fields=dict(transport='codex-native-input-v1',native_method='turn/start',
        native_rpc_outcome=outcome,native_rpc_diagnostic=diagnostic,
        native_metadata=request['native_metadata'],native_turn_id=turn,
        native_result_journal_error=journal_error,terminal_input_operations=0,
        input_operations=1,submission_key=None,retries=0,callback_input=result_ref)
    try:proof=observe_receipt(observe,wait_seconds)
    except (OSError,RuntimeError,ValueError,KeyError,TypeError) as exc:
        raise annotate_error(exc,**fields)
    return dict(proof,**fields,elapsed_ms=round((time.monotonic()-began)*1000,3))
