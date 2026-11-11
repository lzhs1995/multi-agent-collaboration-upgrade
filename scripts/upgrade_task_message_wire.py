"""Read-only message claim/wire format shared with the private controller.

This is not the business admission check. The external retained owner validates
the original registered sample, live run and connected Claude before input.
"""
import json
from pathlib import Path
import re
import uuid

from orchestration_common import digest, json_ref, require

SCHEMA='upgrade-task-registered-message-v1'
REQUEST_SCHEMA='upgrade-task-message-request-v1'
ROUTE_SCHEMA='upgrade-task-message-native-route-v1'


def canonical_identity(identity):
    return {k:str(uuid.UUID(identity[k])) for k in (
        'workspace_uuid','caller_surface_uuid','target_surface_uuid','target_pane_uuid')}


def read_source(ref):
    value=json_ref(ref);campaign=json_ref(value['campaign']);sample=value['sample']
    require(set(value)=={'schema','campaign','kind','sample'} and value['schema']==SCHEMA
            and value['kind'] in ('ordinary','semantic') and 'task_controls' in campaign
            and sample['sender'] in ('claude_a','claude_b') and sample['receiver']=='codex'
            and campaign['sessions'][sample['sender']]['provider']=='claude'
            and all(isinstance(sample[k],str) and re.fullmatch(r'[A-Za-z0-9_.:-]+',sample[k])
                    and sample[k] not in ('.','..') for k in ('run_id','request_id')),
            'TASK_MESSAGE_CLAIM_CHANGED')
    expected=Path(campaign['root'])/'runs'/sample['run_id']/'message-inputs'/sample['request_id']/'source.json'
    require(expected.is_absolute() and ref['path']==str(expected),'TASK_MESSAGE_CLAIM_PATH_CHANGED')
    return value,campaign,sample


def campaign_state(campaign):
    runtime=json_ref(campaign['runtime'])
    require(campaign.get('version')==3 and runtime.get('version')==2
            and runtime['context']==campaign['experiment'] and runtime['controller']==campaign['controller'],
            'TASK_MESSAGE_ORIGINAL_RUNTIME_CHANGED')
    return Path(runtime['state_root'])


def helper_request(campaign,sample,state):
    from cmux_prompt_reference import helper_request as request,plan
    sender,receiver=(campaign['sessions'][sample[k]] for k in ('sender','receiver'))
    identity=canonical_identity(dict(workspace_uuid=sender['workspace_uuid'],
        caller_surface_uuid=sender['surface_uuid'],target_surface_uuid=receiver['surface_uuid'],
        target_pane_uuid=receiver['pane_uuid']))
    require(str(uuid.UUID(receiver['workspace_uuid']))==identity['workspace_uuid'],
            'TASK_MESSAGE_ORIGINAL_WORKSPACE_CHANGED')
    value=request(identity,'send',sample['body'],sample['request_id'],reference=False)
    full=value['payload'];prepared=plan(full,value['marker'],root=Path(state)/'message-bodies-v1')
    if prepared['reference']:
        value.update(payload=prepared['text'],payload_sha256=digest(prepared['text'].encode()),
            body_reference=prepared['reference'],original_payload_sha256=digest(full.encode()))
    return value


def helper_intent_path(state,spec):
    from cmux_prompt_reference import encoded
    identity=canonical_identity(spec['identity'])
    channel=digest(encoded([identity['workspace_uuid'],identity['target_surface_uuid']]).encode())
    return Path(state)/'cmux-agent-adapter-v1/targets'/channel/'intents'/(spec['marker']+'.json')


def original_journal(state,identity,marker):
    caller=canonical_identity(identity)['caller_surface_uuid']
    candidates=[Path(state)/'message-dispatch-v1'/digest(json.dumps([v,marker],sort_keys=True).encode())
                for v in sorted({caller,caller.upper()})]
    present=[p for p in candidates if p.exists() or p.is_symlink()]
    require(len(present)<=1,'TASK_MESSAGE_AMBIGUOUS_JOURNAL_UUID_CASE')
    return present[0] if present else Path(state)/'message-dispatch-v1'/digest(json.dumps([caller,marker],sort_keys=True).encode())
