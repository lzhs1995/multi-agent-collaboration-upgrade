"""One-shot client for the original retained callback ingress.

This module is not wired into generated native senders yet. Callers must hold
the original callback delivery/pane/target locks and persist NATIVE_INPUT_INTENT
before submit_once. The server remains responsible for original sender ancestry,
pack/attempt validation, input and ledger enrollment.
"""
import os
from pathlib import Path
import socket
import stat
import time

from orchestration_common import canonical, file_ref, json_ref, require, strict_json
from upgrade_rpc_peer import kernel_peer

SCHEMA='upgrade-task-callback-ingress-v1'
REQUEST_SCHEMA='upgrade-task-callback-request-v1'
LIMIT=32768


def descriptor(binding):
    """Resolve from original startup operations, never a caller-chosen path."""
    from upgrade_task_target import validate_saved
    _, anchor=validate_saved(binding)
    startup=json_ref(binding['task_target']['startup_result'])
    parent=Path(startup['operations']['path']).parent
    ref=file_ref(parent/'callback-ingress/descriptor.json');value=json_ref(ref)
    enrollment_ref=file_ref(parent/'campaign-enrollment.json')
    enrollment=json_ref(enrollment_ref)
    owner=json_ref(value['runtime_owner'])
    require(value.get('schema')==SCHEMA and value['enrollment']==enrollment_ref
            and value['registration']==startup['operations']==enrollment['registration']
            and value['campaign']==enrollment['campaign']==owner['campaign']
            and value['task_thread_id']==anchor['thread_id']
            and value['allowed_request']==REQUEST_SCHEMA and value['reconnect_allowed'] is False
            and value['owner']['pid']==enrollment['controller_pid']==owner['owner_pid']
            and owner['schema']=='upgrade-task-runtime-owner-v1'
            and owner['enrollment']==enrollment_ref and owner['ledger']==enrollment['ledger']
            and owner['reconnect_allowed'] is False and owner['operation_budget_reset'] is False
            and enrollment['server']==anchor['server'] and enrollment['journal']==anchor['journal']
            and enrollment['startup']==binding['task_target']['startup']
            and value['socket']['path']==str(parent/'callback-ingress/s'),
            'TASK_CALLBACK_CLIENT_ORIGINAL_DESCRIPTOR_REQUIRED')
    return ref,value


def _peer(connection,value,process):
    pin=value['socket'];path=Path(pin['path']);st=path.lstat()
    require(path.is_absolute() and path.parent==path.parent.resolve()
            and stat.S_ISSOCK(st.st_mode) and (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))
                ==(pin['device'],pin['inode'],pin['uid'],pin['mode'])
            and pin['uid']==os.geteuid() and pin['mode']==0o600,
            'TASK_CALLBACK_CLIENT_SOCKET_CHANGED')
    peer=kernel_peer(connection);expected=value['owner']
    require(peer==dict(pid=expected['pid'],uid=os.geteuid()),'TASK_CALLBACK_CLIENT_PEER_CHANGED')
    def row():
        observed=process(peer['pid'])
        return {k:observed[k] for k in ('pid','birth','executable','file_identity')}
    require(row()==expected and kernel_peer(connection)==peer and row()==expected,
            'TASK_CALLBACK_CLIENT_OWNER_CHANGED')


def submit_once(binding,pack,attempt,*,process,recheck,record,timeout=8):
    """Send one frame; every exception leaves the caller's input intent consumed.

    The sole connection is closed on return/error. No retry or fallback exists.
    An ENROLLED response proves service return only, never native user reception.
    record() writes separate immutable transport evidence. The pinned sender
    attempt remains unchanged until the original service returns or times out.
    """
    require(type(timeout) in (int,float) and 0<timeout<=30,'TASK_CALLBACK_CLIENT_TIMEOUT_INVALID')
    original,json_descriptor=descriptor(binding)
    request=dict(schema=REQUEST_SCHEMA,pack=pack,attempt=attempt)
    raw=canonical(request);require(len(raw)<=16384,'TASK_CALLBACK_CLIENT_REQUEST_TOO_LARGE')
    recheck();deadline=time.monotonic()+timeout
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.set_inheritable(False);connection.settimeout(timeout)
        connection.connect(json_descriptor['socket']['path'])
        _peer(connection,json_descriptor,process)
        require(descriptor(binding)==(original,json_descriptor),'TASK_CALLBACK_CLIENT_DESCRIPTOR_CHANGED')
        recheck()
        record('CALLBACK_INGRESS_SEND_INTENT',dict(ingress=original,request=request,retry_allowed=False))
        _peer(connection,json_descriptor,process)
        connection.sendall(raw)
        data=bytearray()
        while b'\n' not in data:
            require(time.monotonic()<deadline,'TASK_CALLBACK_CLIENT_RESPONSE_TIMEOUT')
            connection.settimeout(max(.001,deadline-time.monotonic()))
            part=connection.recv(min(4096,LIMIT+1-len(data)))
            require(part,'TASK_CALLBACK_CLIENT_INCOMPLETE_RESPONSE')
            data.extend(part);require(len(data)<=LIMIT,'TASK_CALLBACK_CLIENT_RESPONSE_TOO_LARGE')
        _peer(connection,json_descriptor,process)
        require(descriptor(binding)==(original,json_descriptor)
                and data.endswith(b'\n') and data.count(b'\n')==1,
                'TASK_CALLBACK_CLIENT_ORIGINAL_RESPONSE_CHANGED')
        response=strict_json(bytes(data),'callback response')
        require(isinstance(response,dict) and response.get('schema')=='upgrade-task-callback-response-v1'
                and response.get('retry_allowed') is False,'TASK_CALLBACK_CLIENT_RESPONSE_INVALID')
        # Permit the original server to close only after this response's peer
        # was rechecked. This acknowledges transport bytes, not model input.
        connection.sendall(b'ACK\n')
        record('CALLBACK_INGRESS_RESPONSE',dict(ingress=original,response=response))
        require(response.get('status')=='ENROLLED','TASK_CALLBACK_CLIENT_NOT_ENROLLED_NO_REPLAY')
        evidence=json_ref(response['evidence']);incoming=json_ref(evidence['request'])
        require(incoming['request']==request and incoming['ingress']==original
                and incoming['peer']==dict(pid=os.getpid(),uid=os.geteuid())
                and evidence['result']==response['result'] and evidence['retry_allowed'] is False
                and response['native_user_receipt'] is False
                and response['result']['model_inputs_sent']==1,
                'TASK_CALLBACK_CLIENT_ORIGINAL_RESULT_CHANGED')
        return response
