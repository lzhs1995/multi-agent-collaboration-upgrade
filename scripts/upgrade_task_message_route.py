"""Historical provenance for registered ordinary/semantic TASK messages."""
from pathlib import Path
import uuid

from orchestration_common import json_ref, require
from upgrade_task_message_wire import (ROUTE_SCHEMA,read_source as validate_source,helper_request,
    campaign_state,canonical_identity,original_journal,helper_intent_path)


def proposed(binding, request):
    from upgrade_task_message_contract import historical_descriptor
    route = request.get('task_message_route')
    require(isinstance(route, dict) and set(route) == {'schema','ingress','source','attempt'}
            and route['schema'] == ROUTE_SCHEMA and 'task_callback_route' not in request,
            'TASK_MESSAGE_ROUTE_REQUIRED')
    ref, endpoint = historical_descriptor(binding)
    value, campaign, sample = validate_source(route['source'])
    state=campaign_state(campaign)
    spec = helper_request(campaign, sample, state)
    journal = original_journal(state, spec['identity'], spec['marker'])
    previous = request.get('observed_turn_id')
    require(isinstance(previous, str) and str(uuid.UUID(previous)) == previous,
            'TASK_MESSAGE_PREVIOUS_TURN_REQUIRED')
    busy = request.get('method') == 'turn/steer'
    params = dict(threadId=binding['session_id'], input=[dict(type='text',text=spec['payload'])])
    if busy:params['expectedTurnId'] = previous
    require(route['ingress'] == ref and value['campaign'] == endpoint['campaign']
            and canonical_identity(binding['identity']) == spec['identity']
            and route['attempt'] == str(journal/'attempt-0001.json')
            and request.get('method') in ('turn/start','turn/steer')
            and request.get('observed_status') == ('active' if busy else 'idle')
            and request.get('socket') == binding['task_target']['socket']
            and request.get('params') == params, 'TASK_MESSAGE_ORIGINAL_REQUEST_CHANGED')
    details = dict(text=spec['payload'],sender_surface=sample['sender_surface_uuid'])
    return route, endpoint, details


def verify_registered_source(original, route, endpoint):
    value, campaign, sample = validate_source(route['source'])
    state=campaign_state(campaign);spec=helper_request(campaign,sample,state)
    expected=helper_intent_path(state,spec)
    require(value['campaign'] == endpoint['campaign'] and original['source'] == route['source']
            and original['kind'] == value['kind'] and original['sample'] == sample
            and original['lane'] == sample['sender'] and original['run_id'] == sample['run_id']
            and original['helper_intent']['path']==str(expected)
            and json_ref(original['helper_intent']) == spec,
            'TASK_MESSAGE_HISTORICAL_SOURCE_CHANGED')


def verify_input(binding, request, attempt):
    from upgrade_task_callback_route import verify_original_input
    return verify_original_input(binding,request,attempt,message=True)
