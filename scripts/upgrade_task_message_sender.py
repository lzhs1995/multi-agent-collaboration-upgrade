"""Send the exact registered Claude message through its original helper attempt.

The helper still owns delivery/pane/target locks, the durable input intent and
receipt. A file claim alone cannot call this route outside its live helper
scope, and the server separately verifies the actual connected Claude ancestry.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import os
from pathlib import Path
import time

from orchestration_common import file_ref, json_ref, require, write_json
from upgrade_task_streams import execution_owner
from upgrade_task_message_wire import read_source as validate_source, helper_request, ROUTE_SCHEMA, REQUEST_SCHEMA, original_journal

_active = ContextVar('original_claude_message_scope', default=None)


def descriptor(binding):
    from upgrade_task_callback_client import descriptor as original
    from upgrade_task_message_contract import _require
    ref,value=original(binding)
    _require(value)
    return ref,value


class MessageSender:
    def __init__(self, helper, source, surface, mode, message, request_id):
        self.helper = helper; self.source = source
        self.pid = os.getpid(); self.owner = execution_owner(); self.active = True
        self.value, self.campaign, self.sample = validate_source(source)
        self.spec = helper_request(self.campaign, self.sample, self.helper.root.parent)
        require(mode == 'send' and surface.upper() == self.sample['receiver_surface_uuid'].upper()
                and message == self.sample['body'] and request_id == self.sample['request_id'],
                'TASK_MESSAGE_EXACT_HELPER_CALL_REQUIRED')
        self.request = None; self.sent = False; self.attempt_path = None
        self.check()

    def check(self):
        require(self.active and self.pid == os.getpid() and self.owner == execution_owner(),
                'TASK_MESSAGE_OWNER_CHANGED_OR_CLOSED')
        require(validate_source(self.source) == (self.value, self.campaign, self.sample),
                'TASK_MESSAGE_SOURCE_CHANGED')
        identity, _ = self.helper.live(self.sample['receiver_surface_uuid'], self.spec['identity'])
        require(identity == self.spec['identity'] and self.helper.request(identity, 'send',
                self.sample['body'], self.sample['request_id']) == self.spec,
                'TASK_MESSAGE_ORIGINAL_HELPER_CHANGED')
        # The exact sample/run is checked by the original sender command and
        # again by the receiving service. This private helper claims no task
        # authority and does not import the external business controller.


@contextmanager
def scope(helper, source, surface, mode, message, request_id):
    require(_active.get() is None, 'TASK_MESSAGE_NESTED_SCOPE_REFUSED')
    owner = MessageSender(helper, source, surface, mode, message, request_id)
    token = _active.set(owner)
    try:
        yield owner
    finally:
        owner.active = False
        _active.reset(token)


def registered_deliver(helper, surface, mode, message, request_id, source_path, source_sha):
    if source_path is None and source_sha is None:
        return helper.deliver(surface, mode, message, request_id)
    require(source_path is not None and source_sha is not None, 'TASK_MESSAGE_SOURCE_PIN_REQUIRED')
    ref = file_ref(Path(source_path))
    require(ref['sha256'] == source_sha, 'TASK_MESSAGE_SOURCE_PIN_CHANGED')
    with scope(helper, ref, surface, mode, message, request_id) as owner:
        return helper.deliver(surface, mode, message, request_id,
                              expected_identity=owner.spec['identity'], outer_check=owner.check)


def route_request(binding, request, text):
    owner = _active.get()
    require(type(owner) is MessageSender and owner.request is None and text == owner.spec['payload']
            and request.get('method') in ('turn/start', 'turn/steer'), 'TASK_MESSAGE_LIVE_SCOPE_REQUIRED')
    owner.check()
    root = original_journal(owner.helper.root.parent, owner.spec['identity'], owner.spec['marker'])
    attempts = sorted(root.glob('attempt-*.json'))
    require(len(attempts) == 1, 'TASK_MESSAGE_ONE_ORIGINAL_ATTEMPT_REQUIRED')
    old = json_ref(file_ref(attempts[0]))
    require(old['native_binding'] == binding and old['phase'] == 'PREPARED' and old['events'] == [],
            'TASK_MESSAGE_FRESH_ATTEMPT_REQUIRED')
    endpoint, _ = descriptor(binding)
    result = copy.deepcopy(request)
    result['task_message_route'] = dict(schema=ROUTE_SCHEMA, ingress=endpoint,
                                      source=owner.source, attempt=str(attempts[0]))
    owner.attempt_path = attempts[0]; owner.request = result
    return result


def send_once(binding, request, text, *, recheck, emit, observe, wait_seconds):
    from cmux_codex_input import validate_request, rpc_error_details
    from cmux_confirmation import observe_receipt, annotate_error
    from upgrade_input_gate import check_input
    from upgrade_task_callback_client import submit_request_once
    import cmux_daemon_identity as identity
    owner = _active.get()
    require(type(owner) is MessageSender and not owner.sent and owner.request == request,
            'TASK_MESSAGE_ORIGINAL_REQUEST_REQUIRED')
    validate_request(request, binding, text); recheck(); owner.check()
    check_input(binding['identity'], native_binding=binding)
    emit('NATIVE_INPUT_INTENT', evidence=dict(native_request=request))
    owner.sent = True
    attempt = file_ref(owner.attempt_path); intended = json_ref(attempt)
    require(intended['phase'] == 'NATIVE_INPUT_INTENT' and len(intended['events']) == 1
            and intended['events'][0]['native_request'] == request, 'TASK_MESSAGE_ORIGINAL_INTENT_REQUIRED')
    sequence = 0
    def record(phase, value):
        nonlocal sequence
        sequence += 1
        write_json(owner.attempt_path.with_name(f'message-ingress-{sequence:02d}.json'),
                   dict(phase=phase, value=value, attempt=attempt), exclusive=True)
    def check():
        recheck(); owner.check()
        require(file_ref(owner.attempt_path) == attempt, 'TASK_MESSAGE_INTENT_CHANGED')
        check_input(binding['identity'], native_binding=binding)
    began = time.monotonic(); outcome = 'RPC_ERROR_OR_UNKNOWN'; turn = result_ref = None
    try:
        response = submit_request_once(binding, dict(schema=REQUEST_SCHEMA, source=owner.source, attempt=attempt),
            descriptor_fn=descriptor, response_schema='upgrade-task-message-response-v1',
            record_prefix='MESSAGE_INGRESS', process=identity.process, recheck=check, record=record)
        turn = response['result']['turn_id']; result_ref = response['result']['message_input']
        if request['method'] == 'turn/steer':
            require(turn == request['params']['expectedTurnId'], 'TASK_MESSAGE_BUSY_RESPONSE_TURN_CHANGED')
        outcome = 'RPC_ACCEPTED'
        diagnostic = dict(method=request['method'], turn_id=turn, rpc_outcome=outcome, message_input=result_ref)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        diagnostic = dict(rpc_error_details(exc), method=request['method'], rpc_outcome=outcome)
    journal_error = None
    try:
        emit('NATIVE_INPUT_RESULT', evidence=diagnostic)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        journal_error = dict(error_type=type(exc).__name__, error=str(exc)[:1000])
    fields = dict(transport='codex-native-input-v1', native_method=request['method'],
        native_rpc_outcome=outcome, native_rpc_diagnostic=diagnostic, native_metadata=request['native_metadata'],
        native_turn_id=turn, native_result_journal_error=journal_error, terminal_input_operations=0,
        input_operations=1, submission_key=None, retries=0, message_input=result_ref)
    try:
        proof = observe_receipt(observe, wait_seconds)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        raise annotate_error(exc, **fields)
    return dict(proof, **fields, elapsed_ms=round((time.monotonic()-began)*1000, 3))
