"""Static original ingress contract shared by controller and historical reader.

Reading this contract is not live caller/peer authentication or input authority.
"""
from pathlib import Path

from orchestration_common import file_ref,json_ref,require

SCHEMA='upgrade-task-callback-ingress-v1'
BUSY_SCHEMA='upgrade-task-callback-ingress-v2'
MESSAGE_SCHEMA='upgrade-task-registered-input-ingress-v3'
REQUEST_SCHEMA='upgrade-task-callback-request-v1'


def descriptor_value(binding,anchor):
    startup=json_ref(binding['task_target']['startup_result'])
    parent=Path(startup['operations']['path']).parent
    ref=file_ref(parent/'callback-ingress/descriptor.json');value=json_ref(ref)
    enrollment_ref=file_ref(parent/'campaign-enrollment.json')
    enrollment=json_ref(enrollment_ref)
    owner=json_ref(value['runtime_owner'])
    require(value.get('schema') in (SCHEMA,BUSY_SCHEMA,MESSAGE_SCHEMA) and value['enrollment']==enrollment_ref
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
    if value['schema'] in (BUSY_SCHEMA,MESSAGE_SCHEMA):
        require(value.get('allowed_input_methods')==['turn/start','turn/steer'],
                'TASK_CALLBACK_CLIENT_BUSY_CONTRACT_CHANGED')
    else:
        require('allowed_input_methods' not in value,'TASK_CALLBACK_CLIENT_BUSY_DOWNGRADE')
    if value['schema']==MESSAGE_SCHEMA:
        from upgrade_task_message_wire import REQUEST_SCHEMA as message_schema
        require(value.get('allowed_message_request')==message_schema,
                'TASK_MESSAGE_INGRESS_CONTRACT_CHANGED')
    else:
        require('allowed_message_request' not in value,'TASK_MESSAGE_INGRESS_DOWNGRADE')
    return ref,value


def historical_descriptor(binding):
    from upgrade_task_target_contract import validate_value
    anchor=json_ref(binding['task_target']['anchor'])
    validate_value(binding,anchor,binding['task_target'])
    startup=json_ref(binding['task_target']['startup_result'])
    require(startup['anchor']==binding['task_target']['anchor'],
            'TASK_CALLBACK_CLIENT_ORIGINAL_ANCHOR_CHANGED')
    return descriptor_value(binding,anchor)
