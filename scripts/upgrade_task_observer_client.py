"""One authenticated read request to the fixed original task observer.

No caller-chosen endpoint, command, thread, RPC method or input payload. A new
read gets a new nonce; a timed-out response is never promoted into authority.
"""
import os
from pathlib import Path
import secrets
import socket
import re
import time

from orchestration_common import canonical, file_ref, json_ref, require
from upgrade_rpc_peer import kernel_peer
from upgrade_task_observer_protocol import (SCHEMA, REQUEST, RESPONSE, MAX_REPLY, OBSERVATION,
    PROCESS_FIELDS, socket_pin, frame, request_value)
from upgrade_task_network import OBSERVER_SCHEMA, observer_path


def descriptor(binding):
    require(binding.get('task_model_network',{}).get('schema')==OBSERVER_SCHEMA,
            'TASK_OBSERVER_EXPLICIT_CONTRACT_REQUIRED')
    path=Path(observer_path(binding['evidence_root']))
    require(binding['task_model_network']['observer']==str(path),'TASK_OBSERVER_PATH_CHANGED')
    startup=file_ref(Path(binding['config_root'])/'startup-v2.json')
    runtime=file_ref(Path(binding['controller_root']).parents[2]/'runtime-v2.json')
    ref=file_ref(path.parent/'descriptor.json');value=json_ref(ref)
    require(value.get('schema')==SCHEMA and value['runtime']==runtime and value['startup']==startup
            and value['socket']['path']==str(path) and value['allowed_kinds']==['tree','task']
            and value['input_operations']==0 and value['restart_allowed'] is False
            and value['adoption_proven'] is False
            and type(value['maximum_seconds']) in (int,float) and 0<value['maximum_seconds']<=1800
            and type(value['maximum_requests']) is int and 0<value['maximum_requests']<=2048
            and value['maximum_observation_seconds']==30
            and set(value['owner'])==set(PROCESS_FIELDS),
            'TASK_OBSERVER_DESCRIPTOR_CHANGED')
    return ref,value


def peer(connection, value, process):
    require(socket_pin(value['socket']['path'])==value['socket'],'TASK_OBSERVER_SOCKET_CHANGED')
    observed=kernel_peer(connection)
    require(observed==dict(pid=value['owner']['pid'],uid=os.geteuid()),'TASK_OBSERVER_PEER_CHANGED')
    def row():
        current=process(observed['pid'])
        return {k:current[k] for k in PROCESS_FIELDS}
    require(row()==value['owner'] and kernel_peer(connection)==observed
            and row()==value['owner'],'TASK_OBSERVER_OWNER_CHANGED')


def read(binding, kind, *, process, timeout=35):
    require(type(timeout) in (int,float) and 0<timeout<=35,'TASK_OBSERVER_TIMEOUT_INVALID')
    request=request_value(dict(schema=REQUEST,kind=kind,nonce=secrets.token_hex(24)))
    ref,value=descriptor(binding)
    deadline=time.monotonic()+timeout
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.set_inheritable(False);connection.settimeout(timeout);connection.connect(value['socket']['path'])
        peer(connection,value,process)
        require(descriptor(binding)==(ref,value),'TASK_OBSERVER_DESCRIPTOR_CHANGED')
        connection.sendall(canonical(request))
        require(time.monotonic()<deadline,'TASK_OBSERVER_TIMEOUT')
        response=frame(connection,limit=MAX_REPLY,seconds=deadline-time.monotonic())
        peer(connection,value,process)
        connection.settimeout(max(.001,deadline-time.monotonic()))
        connection.sendall(b'ACK\n')
        require(descriptor(binding)==(ref,value) and response.get('schema')==RESPONSE
                and response.get('nonce')==request['nonce'] and response.get('status')=='OBSERVED'
                and response.get('kind')==kind and response.get('descriptor')==ref
                and response.get('input_operations')==0,'TASK_OBSERVER_RESPONSE_UNAVAILABLE_OR_CHANGED')
        observation=response['observation']
        require(observation.get('schema')==OBSERVATION and observation['runtime']==value['runtime']
                and observation['startup']==value['startup']
                and observation['caller']['peer']==dict(pid=os.getpid(),uid=os.geteuid())
                and observation['input_operations']==0 and observation['adoption_proven'] is False
                and observation['requester_direct_rpc_peer'] is False,
                'TASK_OBSERVER_ORIGINAL_QUERY_CHANGED')
        evidence=response['evidence'];path=Path(evidence['path'])
        require(path.name=='observation.json' and re.fullmatch(r'[0-9]{6}',path.parent.name)
                and path.parent.parent==Path(ref['path']).parent and path==path.resolve(),
                'TASK_OBSERVER_EVIDENCE_SCOPE_CHANGED')
        saved=json_ref(evidence)
        require(saved['request']['path']==str(path.parent/'request.json')
                and json_ref(saved['request'])==request
                and saved['response']=={k:v for k,v in response.items() if k!='evidence'},
                'TASK_OBSERVER_ORIGINAL_EVIDENCE_CHANGED')
        require(time.monotonic()<deadline,'TASK_OBSERVER_TIMEOUT')
        return response
