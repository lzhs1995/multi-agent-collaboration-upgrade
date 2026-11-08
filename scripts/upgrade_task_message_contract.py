"""Explicit ordinary-message permission on the original retained input socket."""
from orchestration_common import require
from upgrade_task_callback_contract import MESSAGE_SCHEMA
from upgrade_task_message_wire import REQUEST_SCHEMA


def _require(value):
    require(value.get('schema') == MESSAGE_SCHEMA and value.get('allowed_message_request') == REQUEST_SCHEMA
            and value.get('allowed_input_methods') == ['turn/start', 'turn/steer'],
            'TASK_MESSAGE_EXPLICIT_INGRESS_REQUIRED')


def historical_descriptor(binding):
    from upgrade_task_callback_contract import historical_descriptor as original
    ref, value = original(binding)
    _require(value)
    return ref, value
