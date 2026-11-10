"""Complete inquiry exposure and native model-authored mailbox replies.

This module never starts/rearms an executor episode, sends terminal input,
changes its state, consumes its reply, or dispatches work. TASK replies are
published only to the original canonical mailbox, once per original marker.
"""
import json
import hashlib
import hmac
import math
import os
from pathlib import Path
import re
import time

from orchestration_common import (checked_path, digest, file_ref, json_ref,
    read_json, read_ref, require, write_json)

READ_SCHEMA = 'upgrade-task-inquiry-read-v1'
ANSWER_SCHEMA = 'upgrade-task-inquiry-answer-v1'
CHANNEL_SCHEMA = 'upgrade-task-inquiry-channel-v2'
SNAPSHOT_SCHEMA = 'upgrade-task-inquiry-snapshot-v2'


def _hash(text):
    return digest(text.encode())


def _episode(payload, registration, *, require_waiting=True):
    runtime = json_ref(registration['runtime'])
    root = checked_path(runtime['state_root'])
    lane = payload['lane']; executor = registration['sessions'][lane]
    caller = executor['surface_uuid'].upper()
    supervisor = registration['sessions']['codex']['surface_uuid'].upper()
    descriptor = payload['episode']
    require(set(descriptor) == {'path','episode_id','registration_token'}, 'INQUIRY_EPISODE_DESCRIPTOR_INVALID')
    episode = read_json(descriptor['path'])
    enrollment = json_ref(payload['enrollment'])
    channel=root/'supervisor-inbox-v1'/_hash(supervisor)/(_hash(caller)+'.json')
    supplied_channel=payload['channel'] if payload.get('schema')==CHANNEL_SCHEMA else payload['request']['path']
    require(payload['episode']['path'] == str(root/'executor-reask-v1'/(_hash(caller)+'.json'))
            and payload['enrollment']['path'] == str(root/'executor-reask-scheduler-v1'/'enrolled'/(_hash(caller)+'.json'))
            and supplied_channel == str(channel),
            'INQUIRY_ORIGINAL_PATH_REQUIRED')
    mailbox = root/'executor-idle-v1'/'mailbox'/_hash(caller)[:16]
    require(episode['caller_surface_uuid'] == caller and episode['supervisor'] == supervisor
            and episode['task_id'] == registration['task_id']
            and episode['workspace_uuid'].upper() == executor['workspace_uuid'].upper()
            and (not require_waiting or episode['state'] == 'WAITING_REPLY') and episode['mailbox'] == str(mailbox)
            and episode.get('file_only_preference') is True
            and episode['episode_id'] == descriptor['episode_id']
            and episode.get('registration', {}).get('token') == descriptor['registration_token'],
            'INQUIRY_ORIGINAL_EPISODE_REQUIRED')
    continuation = episode.get('continuation', {})
    require(continuation.get('enabled') is True and continuation.get('status') == 'ACTIVE'
            and continuation.get('reservation_inquiries') is True
            and continuation.get('proactive_hook') is True, 'INQUIRY_CONTINUING_SCOPE_REQUIRED')
    read_ref(continuation['authorization'])
    if require_waiting:
        until = continuation.get('until_epoch')
        require(until is None or type(until) in (int,float) and math.isfinite(until) and until>time.time(),
                'INQUIRY_AUTHORITY_EXPIRED')
        require(not (root/'executor-reask-v1'/(_hash(caller)+'.stop.json')).exists(),
                'INQUIRY_ORIGINAL_EPISODE_STOPPED')
    binding_keys = ('caller_surface_uuid','supervisor','workspace_uuid','task_id','purpose',
                    'episode_id','channels','mailbox')
    expected_binding={k:episode.get(k) for k in binding_keys}
    for key in ('executor_identity','successor_registration'):
        if key in episode: expected_binding[key]=episode[key]
    # Mirror the original scheduler's immutable continuation binding; ACTIVE
    # is mutable lifecycle state, but the grant and capabilities are not.
    continuation_keys=('enabled','id','overall_task','owner','authorization',
                       'authorized_epoch','until_epoch','controller',
                       'reservation_inquiries','proactive_hook')
    require(all(k in continuation for k in continuation_keys), 'INQUIRY_CONTINUATION_BINDING_REQUIRED')
    expected_binding['continuation']={k:continuation[k] for k in continuation_keys}
    require(enrollment.get('schema') == 'executor-reask-enrollment-v2'
            and enrollment.get('status') == 'ENROLLED'
            and enrollment.get('authorization') in ('EXPLICIT_REASK_START','EXPLICIT_REASK_ENROLL_EXISTING')
            and enrollment.get('uid') == os.geteuid()
            and enrollment.get('registration') == episode.get('registration')
            and episode['registration'].get('enrollment') == payload['enrollment']['path']
            and re.fullmatch(r'[0-9a-f]{32}',descriptor['registration_token'])
            and enrollment.get('binding') == expected_binding,
            'INQUIRY_COMMITTED_ENROLLMENT_REQUIRED')
    live = enrollment.get('live_binding', {})
    require(live.get('caller_surface_uuid') == caller and live.get('target_surface_uuid') == supervisor
            and live.get('workspace_uuid') == episode['workspace_uuid'], 'INQUIRY_ENROLLED_PAIR_CHANGED')
    return episode,mailbox,channel


def _source(payload, registration, *, require_waiting=True, captured=None):
    episode,mailbox,channel=_episode(payload,registration,require_waiting=require_waiting)
    version2=payload.get('schema')==CHANNEL_SCHEMA
    observed_ref=None
    if captured is not None:
        request=captured['request']
    else:
        observed_ref=file_ref(channel) if version2 else payload['request']
        request=json_ref(observed_ref)
    marker=request['marker']
    require(isinstance(marker,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}',marker),
            'INQUIRY_MARKER_INVALID')
    caller,supervisor=episode['caller_surface_uuid'],episode['supervisor']
    require(request.get('schema') == 'executor-reask-current-v2'
            and request.get('status') == 'WAITING_REPLY'
            and request['caller_surface_uuid'] == caller
            and request['supervisor'] == supervisor and request['task_id'] == registration['task_id']
            and request['episode_id'] == episode['episode_id']
            and request['workspace_uuid'] == episode['workspace_uuid']
            and request['reply_mailbox'] == str(mailbox/(marker+'.json')),
            'INQUIRY_ORIGINAL_ASK_REQUIRED')
    asks=[a for a in episode['asks'] if a.get('marker')==marker]
    if captured is None:
        require(len(asks)==1,'INQUIRY_ONE_ORIGINAL_ASK_REQUIRED')
        ask=asks[0]
    else:
        ask=captured['ask']
        require(len(asks)<=1 and (not asks or asks[0]==ask), 'INQUIRY_SAVED_ASK_CHANGED')
    if version2:
        ordinal=ask.get('attempt')
        require(type(ordinal) is int and 0<ordinal<=episode.get('attempt_count',0)
                and ordinal>episode.get('replies_closed_through',0)
                and isinstance(episode.get('marker_secret'),str),'INQUIRY_ORIGINAL_ASK_CLOSED')
        signature=hmac.new(episode['marker_secret'].encode(),str(ordinal).encode(),hashlib.sha256).hexdigest()[:16]
        require(hmac.compare_digest(marker,f'EXECUTOR_READY_{ordinal:x}_{signature}'),
                'INQUIRY_ORIGINAL_ISSUED_MARKER_REQUIRED')
    body=request.get('request')
    require(isinstance(body, str) and 0 < len(body.encode()) <= 256*1024, 'INQUIRY_BODY_BUDGET')
    require(ask.get('text_sha256') == _hash(body)
            and request.get('at_epoch') == ask.get('at_epoch')
            and str(channel) in ask.get('channels',{}).get('written',[])
            and ask.get('outcome') == 'FILE_ONLY_SCHEDULED'
            and ask.get('terminal') is False and ask.get('input_operations') == 0,
            'INQUIRY_ORIGINAL_FILE_CHANNEL_REQUIRED')
    require(type(ask.get('attempt')) is int and ask['attempt']>=1
            and all(type(ask.get(k)) in (int,float) and 0<=ask[k]<=253402300799.0
                    for k in ('at_epoch','due_epoch'))
            and ask['at_epoch']>=ask['due_epoch'], 'INQUIRY_BEFORE_ORIGINAL_DUE')
    previous=[a for a in episode['asks'] if a.get('attempt')==ask['attempt']-1]
    if ask['attempt']>1 and captured is None:
        require(len(previous)==1 and ask['at_epoch']-previous[0]['at_epoch']>=60,
                'INQUIRY_ORIGINAL_60_SECOND_INTERVAL_REQUIRED')
    if version2:
        return episode,request,body,mailbox/(marker+'.json'),ask,observed_ref
    return episode, request, body, mailbox/ (marker+'.json')


def validate_operation(payload, registration):
    require(payload.get('action') in ('inquiry-read','inquiry-answer'), 'INQUIRY_ACTION_INVALID')
    if payload.get('schema')==CHANNEL_SCHEMA:
        fields={'action','schema','lane','episode','enrollment','channel','slot'}
        if payload.get('slot')==2: fields.add('previous_read_operation_id')
        if payload['action']=='inquiry-answer': fields.add('read_operation_id')
        require(set(payload)==fields and payload['lane'] in ('claude_a','claude_b')
                and type(payload['slot']) is int and payload['slot'] in (1,2),
                'INQUIRY_CHANNEL_PAYLOAD_INVALID')
        _episode(payload,registration,require_waiting=False)
        reads={};answers={}
        for name,op in registration['operations'].items():
            if not op['action'].startswith('inquiry-'): continue
            value=op['payload']
            require(value.get('schema')==CHANNEL_SCHEMA,'INQUIRY_MIXED_SOURCE_VERSIONS')
            key=(value.get('lane'),value.get('slot'))
            target=reads if op['action']=='inquiry-read' else answers
            require(key not in target,'INQUIRY_DUPLICATE_CYCLE_SLOT')
            target[key]=(name,value)
        require(0<len(reads)<=4 and set(reads)==set(answers), 'INQUIRY_FIXED_CYCLE_PAIRS_REQUIRED')
        for key,(name,value) in reads.items():
            answer_name,answer=answers[key]
            require(answer==dict(value,read_operation_id=name),'INQUIRY_ORIGINAL_READ_REQUIRED')
            if key[1]==2:
                prior=reads.get((key[0],1))
                require(prior is not None and value.get('previous_read_operation_id')==prior[0]
                        and {k:v for k,v in value.items() if k not in ('slot','previous_read_operation_id')}
                            =={k:v for k,v in prior[1].items() if k!='slot'},
                        'INQUIRY_ORIGINAL_PREVIOUS_CYCLE_REQUIRED')
        return
    fields = {'action','lane','episode','enrollment','request'}
    if payload['action'] == 'inquiry-answer': fields.add('read_operation_id')
    require(set(payload) == fields and payload['lane'] in ('claude_a','claude_b'), 'INQUIRY_PAYLOAD_INVALID')
    _source(payload, registration, require_waiting=False)
    if payload['action'] == 'inquiry-answer':
        op = registration['operations'].get(payload['read_operation_id'])
        require(op == dict(action='inquiry-read', payload={k:v for k,v in payload.items()
                if k not in ('action','read_operation_id')}), 'INQUIRY_ORIGINAL_READ_REQUIRED')


def _slot_root(payload,registration):
    session=registration['sessions'][payload['lane']]
    key=_hash(json.dumps([session['surface_uuid'].upper(),payload['episode']['episode_id']]))
    return Path(registration['output_root']).parent/'inquiry-cycle-slots'/key


def _read_value(context,payload,snapshot_ref,snapshot):
    body=snapshot['request']['request']
    return dict(schema='upgrade-task-inquiry-read-v2',task_id=context.task_id,lane=payload['lane'],
        slot=payload['slot'],snapshot=snapshot_ref,marker=snapshot['request']['marker'],
        episode_id=payload['episode']['episode_id'],body=body,body_sha256=_hash(body),
        model_consumption_proven=False,business_accepted=False)


def _load_snapshot(context,payload,read_operation):
    result_ref,read,native_output=context.completed_operation(read_operation)
    snapshot_ref=read.get('snapshot')
    expected=Path(context.registration['output_root'])/'operations'/read_operation/'inquiry-snapshot.json'
    require(isinstance(snapshot_ref,dict) and snapshot_ref.get('path')==str(expected),
            'INQUIRY_ORIGINAL_SNAPSHOT_PATH_REQUIRED')
    snapshot=json_ref(snapshot_ref)
    require(snapshot.get('schema')==SNAPSHOT_SCHEMA and snapshot['registration']==context.registration_ref
            and snapshot['read_operation_id']==read_operation and snapshot['lane']==payload['lane']
            and snapshot['slot']==payload['slot'] and snapshot['channel']==payload['channel']
            and snapshot['enrollment']==payload['enrollment'] and snapshot['episode']==payload['episode']
            and read==_read_value(context,payload,snapshot_ref,snapshot),
            'INQUIRY_COMPLETE_SNAPSHOT_CHANGED')
    slot=_slot_root(payload,context.registration)/str(payload['slot'])
    require(json_ref(file_ref(slot/'snapshot.json'))==dict(snapshot=snapshot_ref),
            'INQUIRY_ORIGINAL_SLOT_SNAPSHOT_REQUIRED')
    return result_ref,read,native_output,snapshot


def _previous_consumed(context,payload,episode,ask):
    if payload['slot']==1:return None
    previous_id=payload['previous_read_operation_id']
    previous_payload=context.registration['operations'][previous_id]['payload']
    _,previous,_,snapshot=_load_snapshot(context,previous_payload,previous_id)
    previous_slot=_slot_root(payload,context.registration)/'1'
    answer=json_ref(file_ref(previous_slot/'answer.json'))
    answer_result=json_ref(answer['result'])
    answer_op=answer['operation_id']
    expected=context.registration['operations'].get(answer_op)
    require(expected==dict(action='inquiry-answer',payload=dict(previous_payload,read_operation_id=previous_id)),
            'INQUIRY_ORIGINAL_PREVIOUS_ANSWER_REQUIRED')
    _,native_answer,_=context.completed_operation(answer_op)
    require(native_answer==answer_result,'INQUIRY_PREVIOUS_ANSWER_OUTPUT_CHANGED')
    original_root=checked_path(json_ref(context.registration['runtime'])['state_root'])
    receipt_path=original_root/'executor-reask-v1'/'consumed'/_hash(episode['caller_surface_uuid'])/(
        _hash(episode['episode_id']+':'+previous['marker'])+'.json')
    receipt_ref=file_ref(receipt_path);receipt=json_ref(receipt_ref)
    reply=json_ref(answer_result['reply'])
    require(receipt.get('schema')=='executor-reply-consumed-v1'
            and all(receipt.get(k)==v for k,v in dict(caller_surface_uuid=episode['caller_surface_uuid'],
                supervisor=episode['supervisor'],task_id=context.task_id,episode_id=episode['episode_id'],
                marker=previous['marker'],body=reply,body_sha256=answer_result['reply_body_sha256'],
                consumption='EXPLICIT_FOREGROUND_EXECUTOR_ACK').items())
            and receipt.get('executor_authentication',{}).get('session_id')
                ==context.sessions[payload['lane']]['session_id']
            and any(h.get('receipt')==str(receipt_path) and h.get('marker')==previous['marker']
                and h.get('body_sha256')==answer_result['reply_body_sha256'] for h in episode.get('reply_history',[]))
            and episode.get('replies_closed_through',0)>=snapshot['ask']['attempt'],
            'INQUIRY_PREVIOUS_ORIGINAL_CONSUMPTION_REQUIRED')
    require(ask['attempt']>snapshot['ask']['attempt']
            and ask['at_epoch']-snapshot['ask']['at_epoch']>=60
            and type(receipt.get('consumed_epoch')) in (int,float)
            and snapshot['ask']['at_epoch']<=receipt['consumed_epoch']<=ask['at_epoch'],
            'INQUIRY_SECOND_CYCLE_60_SECONDS_REQUIRED')
    return receipt_ref


def _capture_read(context,payload):
    episode,request,body,reply_path,ask,observed_ref=_source(payload,context.registration)
    prior=_previous_consumed(context,payload,episode,ask)
    slot=_slot_root(payload,context.registration)/str(payload['slot'])
    marker_key=_hash(json.dumps([episode['caller_surface_uuid'],episode['episode_id'],request['marker']]))
    marker_path=Path(context.registration['output_root']).parent/'inquiry-read-markers'/(marker_key+'.json')
    require(not slot.exists() and not marker_path.exists(),'INQUIRY_CYCLE_OR_MARKER_ALREADY_INTENDED')
    slot.parent.mkdir(mode=0o700,parents=True,exist_ok=True);slot.mkdir(mode=0o700)
    write_json(slot/'intent.json',dict(registration=context.registration_ref,operation_id=context.operation_id,
        slot=payload['slot'],marker=request['marker']),exclusive=True)
    marker_path.parent.mkdir(mode=0o700,exist_ok=True)
    write_json(marker_path,dict(registration=context.registration_ref,operation_id=context.operation_id,
        slot=str(slot),marker=request['marker']),exclusive=True)
    snapshot=dict(schema=SNAPSHOT_SCHEMA,registration=context.registration_ref,
        read_operation_id=context.operation_id,lane=payload['lane'],slot=payload['slot'],
        channel=payload['channel'],channel_observation=observed_ref,request=request,ask=ask,
        enrollment=payload['enrollment'],episode=payload['episode'],previous_consumption=prior)
    snapshot_ref=write_json(context.directory/'inquiry-snapshot.json',snapshot,exclusive=True)
    write_json(slot/'snapshot.json',dict(snapshot=snapshot_ref),exclusive=True)
    return _read_value(context,payload,snapshot_ref,snapshot)


def dispatch(action, payload, context):
    from upgrade_task_capability_extension import require_context
    require_context(context); context.assert_operation(action, payload)
    validate_operation(dict(action=action, **payload), context.registration)
    version2=payload.get('schema')==CHANNEL_SCHEMA
    captured=None
    if version2:
        if action=='inquiry-read':return _capture_read(context,payload)
        read_payload=context.registration['operations'][payload['read_operation_id']]['payload']
        result_ref,read,native_output,captured=_load_snapshot(context,read_payload,payload['read_operation_id'])
        episode,request,body,reply_path,_,_=_source(payload,context.registration,captured=captured)
    else:
        episode, request, body, reply_path = _source(payload, context.registration)
        read = dict(schema=READ_SCHEMA, task_id=context.task_id, lane=payload['lane'],
            request=payload['request'], marker=request['marker'], episode_id=episode['episode_id'],
            body=body, body_sha256=_hash(body), model_consumption_proven=False, business_accepted=False)
        if action == 'inquiry-read': return read
        result_ref, original, native_output = context.completed_operation(payload['read_operation_id'])
        require(original == read, 'INQUIRY_COMPLETE_BODY_RESULT_CHANGED')
    statement = context.native_statement(None, ANSWER_SCHEMA,
        after=native_output['offset']+native_output['length'])
    require(set(statement) == {'schema','task_id','extension_id','operation_id','read_operation_id',
                              'body_sha256','reply'}
            and statement['read_operation_id'] == payload['read_operation_id']
            and statement['body_sha256'] == read['body_sha256'], 'INQUIRY_REPLY_READ_BINDING_CHANGED')
    reply = statement['reply']
    fields={'marker','caller_surface_uuid','supervisor_uuid','task_id','episode_id','status','trigger'}
    if isinstance(reply,dict) and reply.get('status') in ('BLOCKED','WAITING_DEPENDENCY','SOLO'):
        fields |= {'owner','next_due_epoch','deadline_epoch'}
    if isinstance(reply,dict) and 'overall_task' in reply:
        fields.add('overall_task')
        require(reply['overall_task']==episode['continuation']['overall_task'],
                'INQUIRY_ORIGINAL_OVERALL_TASK_REQUIRED')
    require(isinstance(reply, dict) and set(reply) == fields
            and all(reply[k] == v for k,v in dict(marker=request['marker'],
                caller_surface_uuid=episode['caller_surface_uuid'], supervisor_uuid=episode['supervisor'],
                task_id=context.task_id, episode_id=episode['episode_id']).items())
            and reply['status'] in ('ACK','BLOCKED','WAITING_DEPENDENCY','SOLO')
            and isinstance(reply['trigger'], str) and 0 < len(reply['trigger']) <= 2000
            and reply['trigger'].strip() and '\0' not in reply['trigger'], 'INQUIRY_REPLY_SCOPE_INVALID')
    if reply['status'] != 'ACK':
        due,deadline=reply['next_due_epoch'],reply['deadline_epoch']
        require(reply['owner']==episode['supervisor']
                and all(type(v) in (int,float) and math.isfinite(v) and 0<v<=253402300799.0
                        for v in (due,deadline)) and due<=deadline,
                'INQUIRY_ORIGINAL_WAIT_CONTRACT_REQUIRED')
    # ACK/status only: TASK or DISPATCHED would need the separate work/dispatch proof.
    root = Path(context.registration['output_root']).parent/'inquiry-effects'
    root.mkdir(mode=0o700, exist_ok=True)
    key = _hash(json.dumps([episode['caller_surface_uuid'],episode['episode_id'],request['marker']]))
    directory = root/key
    require(not directory.exists() and not reply_path.exists(), 'INQUIRY_REPLY_ALREADY_INTENDED_NO_REPLAY')
    directory.mkdir(mode=0o700)
    intent = write_json(directory/'intent.json', dict(registration=context.registration_ref,
        operation_id=context.operation_id, request=read['snapshot'] if version2 else payload['request'], read_result=result_ref,
        reply=reply, reply_path=str(reply_path)), exclusive=True)
    context.recheck()
    _source(payload, context.registration,captured=captured)
    require(reply_path.parent.is_dir(), 'INQUIRY_ORIGINAL_MAILBOX_MISSING')
    receipt = write_json(reply_path, reply, exclusive=True)
    result = dict(schema='upgrade-task-inquiry-answer-result-v1', intent=intent, reply=receipt,
        reply_body_sha256=_hash(json.dumps(reply,ensure_ascii=False,sort_keys=True,separators=(',',':'))),
        reply_model_authored=True, executor_read_proven=False, executor_consumed=False,
        business_accepted=False, input_operations=0)
    effect_ref=write_json(directory/'result.json', result, exclusive=True)
    if version2:
        write_json(_slot_root(payload,context.registration)/str(payload['slot'])/'answer.json',
            dict(operation_id=context.operation_id,result=effect_ref),exclusive=True)
    return result
