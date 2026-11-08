"""Historical evidence for one idle callback sent by the original service.

Descriptor validation permits reading a proposed route, not sending a model
input. Completed proof joins the original pre-send snapshot, retained RPC,
service result and append-only ledger; missing evidence remains unconfirmed.
"""
from pathlib import Path
import sqlite3
import uuid

from orchestration_common import file_ref, json_ref, object_sha, require, strict_json

SCHEMA='upgrade-task-callback-native-route-v1'


def proposed(binding,request):
    from upgrade_task_callback_contract import historical_descriptor
    route=request.get('task_callback_route')
    require(isinstance(route,dict) and set(route)=={'schema','ingress','pack','attempt'}
            and route['schema']==SCHEMA,'TASK_CALLBACK_ROUTE_REQUIRED')
    # The enclosing native binding/adopted receiver validates its own source.
    # This reader never selects a live configuration from process environment.
    ref,value=historical_descriptor(binding)
    require(route['ingress']==ref,'TASK_CALLBACK_ROUTE_ORIGINAL_INGRESS_CHANGED')
    pack=json_ref(route['pack'])
    receipt=Path(pack['completion_receipt'])
    attempt=Path(route['attempt'])
    previous=request.get('observed_turn_id')
    try:
        valid_previous=isinstance(previous,str) and str(uuid.UUID(previous))==previous
    except ValueError:
        valid_previous=False
    busy=request.get('method')=='turn/steer'
    params=dict(threadId=binding['session_id'],input=[dict(type='text',text=pack['completion_callback'])])
    if busy:
        from upgrade_task_callback_contract import BUSY_SCHEMA, MESSAGE_SCHEMA
        require(value['schema'] in (BUSY_SCHEMA,MESSAGE_SCHEMA) and value['allowed_input_methods']==['turn/start','turn/steer'],
                'TASK_CALLBACK_BUSY_DESCRIPTOR_REQUIRED')
        params['expectedTurnId']=previous
    require(valid_previous
            and request.get('method') in ('turn/start','turn/steer')
            and request.get('observed_status')==('active' if busy else 'idle')
            and request.get('socket')==binding['task_target']['socket']
            and request.get('params')==params
            and pack['executor_uuid'].upper()==binding['identity']['caller_surface_uuid'].upper()
            and pack['callback_target'].upper()==binding['identity']['target_surface_uuid'].upper()
            and attempt.parent==receipt.with_name(receipt.stem+'-attempts')
            and attempt.name.startswith('attempt-') and attempt.suffix=='.json',
            'TASK_CALLBACK_ROUTE_ORIGINAL_REQUEST_CHANGED')
    return route,value,pack


def verify_input(binding,request,attempt):
    """Read an existing service result. Never manufacture receipt or authority."""
    return verify_original_input(binding,request,attempt)


def verify_original_input(binding,request,attempt,*,message=False):
    require(type(message) is bool,'TASK_INPUT_KIND_REQUIRED')
    if message:
        from upgrade_task_message_route import proposed as message_route
        route,endpoint,details=message_route(binding,request)
    else:
        route,endpoint,pack=proposed(binding,request)
        details=dict(text=pack['completion_callback'],sender_surface=pack['executor_uuid'])
    label='message' if message else 'callback'
    source_key='source' if message else 'pack'
    if request['method']=='turn/steer':
        from upgrade_task_busy_evidence import verify_busy_input
        return verify_busy_input(binding,request,attempt,route,endpoint,details,message=message)
    parent=Path(endpoint['registration']['path']).parent
    directory=parent/'campaign-actor-events'
    previous=request['observed_turn_id']
    result_ref=file_ref(directory/(previous+'-'+label+'-input-result.json'))
    result=json_ref(result_ref);plan=json_ref(result['plan']);source=json_ref(result['source'])
    snapshot=json_ref(source['original_attempt_snapshot'])
    initial=source['original']
    enrollment=json_ref(endpoint['enrollment']);campaign=json_ref(endpoint['campaign'])
    registration=json_ref(endpoint['registration'])
    require(result['schema']=='upgrade-task-'+label+'-input-result-v1'
            and result['native_user_receipt'] is False and result['model_consumption'] is False
            and plan['schema']=='upgrade-task-'+label+'-input-plan-v1'
            and plan['campaign']==endpoint['campaign'] and plan[label]==result['source']
            and source['schema']=='upgrade-task-'+label+'-input-source-v1'
            and source['enrollment']==endpoint['enrollment']
            and source['native_user_receipt'] is False and source['model_consumption'] is False
            and plan['previous_turn_id']==previous
            and plan['registration_sha256']==object_sha(registration)
            and plan['authority_ref']==registration['authority_ref']
            and initial[source_key]==route[source_key] and initial['attempt']['path']==route['attempt']
            and initial['attempt']['sha256']==source['original_attempt_snapshot']['sha256']
            and initial['attempt']['bytes']==source['original_attempt_snapshot']['bytes']
            and initial['request']==request and plan['text']==initial['text']==details['text']
            and snapshot['phase']=='NATIVE_INPUT_INTENT'
            and snapshot['native_binding']==binding==attempt['native_binding']
            and snapshot['binding']==attempt['binding']
            and len(snapshot['events'])==1 and attempt['events'][:1]==snapshot['events']
            and snapshot['events'][0]['native_request']==request,
            'TASK_CALLBACK_ROUTE_ORIGINAL_SOURCE_CHANGED')
    # Require provenance from the enrolled original Claude lane, not arbitrary
    # process ancestry supplied by a file consumer.
    sender=campaign['sessions'][initial['lane']]
    caller=initial['caller'];ancestry=caller['ancestry']
    if message:
        from upgrade_task_message_route import verify_registered_source
        verify_registered_source(initial,route,endpoint)
    require(sender['provider']=='claude' and sender['surface_uuid'].upper()==details['sender_surface'].upper()
            and ancestry and caller['peer']['pid']==ancestry[0]['pid']
            and all(k in sender['process'] and ancestry[-1].get(k)==sender['process'][k]
                    for k in ('pid','birth','executable','file_identity'))
            and all(a['ppid']==b['pid'] for a,b in zip(ancestry,ancestry[1:])),
            'TASK_CALLBACK_ROUTE_ORIGINAL_CALLER_CHANGED')
    transition=json_ref(result['transition']);grant_ref=transition['grant'];grant=json_ref(grant_ref)
    enrollment_result=json_ref(transition['enrollment'])
    from upgrade_task_creation import exchange
    sent,received=exchange(grant['turn_start'],'turn/start',registration['server'])
    turn=grant['turn_id']
    require(sent==request['params'] and received.get('turn',{}).get('id')==turn
            and grant['schema']=='upgrade-task-operation-turn-v1'
            and grant['registration_sha256']==object_sha(registration)
            and grant['authority_ref']==registration['authority_ref']
            and grant['previous_turn_id']==previous and grant['operation_ids']==plan['operation_ids']
            and grant['previous_completion']==plan['previous_completion']
            and transition['turn_id']==turn and transition['model_inputs_sent']==1
            and transition['operation_budget_reset'] is False
            and result['result']==dict(status='ACTIVE',turn_id=turn,
                operation_budget_reset=False,model_inputs_sent=1)
            and enrollment_result['schema']=='upgrade-task-campaign-turn-enrollment-v1'
            and enrollment_result['enrollment']==endpoint['enrollment']
            and enrollment_result['grant']==grant_ref and enrollment_result['turn_id']==turn
            and enrollment_result['previous_completion']==plan['previous_completion']
            and enrollment_result['controller_pid']==endpoint['owner']['pid'],
            'TASK_CALLBACK_ROUTE_ORIGINAL_ENROLLMENT_CHANGED')
    prior=json_ref(plan['previous_completion'])
    require(prior.get('direction')=='received'
            and prior.get('peer',{}).get('server')==registration['server']
            and prior.get('message',{}).get('method')=='turn/completed'
            and prior['message'].get('params',{}).get('threadId')==registration['thread_id']
            and prior['message']['params']['turn']['id']==previous
            and prior['message']['params']['turn']['status']=='completed',
            'TASK_CALLBACK_ROUTE_ORIGINAL_PREVIOUS_TURN_CHANGED')
    ledger=enrollment['ledger'];path=Path(ledger['path']);st=path.stat()
    require((st.st_dev,st.st_ino)==(ledger['device'],ledger['inode']),
            'TASK_CALLBACK_ROUTE_ORIGINAL_LEDGER_CHANGED')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        db.execute('BEGIN')
        registered=db.execute('SELECT body FROM registration WHERE id=1').fetchone()
        row=db.execute('SELECT sequence,scope,grant FROM turns WHERE turn_id=?',(turn,)).fetchone()
        require(registered and strict_json(registered[0])==registration and row
                and strict_json(row[1])==plan['operation_ids'] and strict_json(row[2])==grant_ref
                and db.execute('SELECT turn_id FROM turns WHERE sequence=?',(row[0]-1,)).fetchone()==(previous,),
                'TASK_CALLBACK_ROUTE_ORIGINAL_TURN_NOT_ENROLLED')
    finally:db.close()
    require(path.stat().st_ino==st.st_ino and path.stat().st_dev==st.st_dev,
            'TASK_CALLBACK_ROUTE_ORIGINAL_LEDGER_CHANGED')
    return result_ref
