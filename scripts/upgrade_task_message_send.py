"""Send one registered ordinary sample through the original helper and locks.

The caller is the retained, currently executing model operation. This does not
borrow a foreground identity, spawn a helper with inherited authority, or send
on behalf of Claude. The sole source record is joined to the existing ledger.
"""
from contextvars import ContextVar
import os
from pathlib import Path

from orchestration_common import file_ref, json_ref, require, write_json
from upgrade_task_streams import execution_owner

SCHEMA = 'upgrade-task-communication-send-v1'
_active = ContextVar('original_task_communication_sender', default=None)


def _scope(runner, payload, sample, request, owner, message, received):
    from orchestration_native import NativeReader
    from upgrade_task_communication import original_sample, send_boundary
    from upgrade_task_control import native_invocation
    require(owner.runner is runner and owner.campaign == runner.campaign
            and message.get('method') == 'item/tool/call', 'TASK_COMMUNICATION_ORIGINAL_CALL_REQUIRED')
    operation = message['params']['arguments']['operation_id']
    row = owner.ledger.inspect(message['params']['callId'])
    require(owner.operations[operation] == payload and payload['action'] == 'communication-send'
            and row['phase'] == 'INTENDED' and row['request'] == message,
            'TASK_COMMUNICATION_ORIGINAL_INTENT_REQUIRED')
    require(original_sample(runner.campaign, payload) == sample, 'TASK_COMMUNICATION_ORIGINAL_SAMPLE_CHANGED')
    co = runner.co(payload['run_id']); co._gate(); co._work_budget(); send_boundary(co, sample)
    invocation = file_ref(Path(runner.campaign['task_controls']['evidence_directory'])/(operation+'.json'))
    value = json_ref(invocation)
    require(value['received'] == received and value['operation']['payload'] == payload,
            'TASK_COMMUNICATION_ORIGINAL_INVOCATION_CHANGED')
    native_invocation(NativeReader(runner.campaign['sessions']['codex']), invocation,
                      value['request'], allow_intended=True)
    validate_request(runner.campaign, sample, request)
    return dict(schema=SCHEMA, campaign=runner.ref, enrollment=owner.proof, invocation=invocation,
                received=received, controller_pid=os.getpid(), run_id=payload['run_id'],
                node_id=payload['node_id'], lane=sample['receiver'], sample=sample, helper_request=request)


def validate_request(campaign, sample, request):
    from cmux_prompt_reference import original_helper_payload, plan
    from upgrade_communication_observer import canonical_identity, expected_intent
    from orchestration_contracts import state_root
    sender, receiver = campaign['sessions']['codex'], campaign['sessions'][sample['receiver']]
    identity = canonical_identity(dict(workspace_uuid=sender['workspace_uuid'],
        caller_surface_uuid=sender['surface_uuid'], target_surface_uuid=receiver['surface_uuid'],
        target_pane_uuid=receiver['pane_uuid']))
    spec, marker, _ = expected_intent(sample, identity)
    require(all(request.get(k) == v for k, v in spec.items()) and request['marker'] == marker,
            'TASK_COMMUNICATION_EXACT_HELPER_REQUEST_REQUIRED')
    full = original_helper_payload(request)
    wire = plan(full, marker, root=state_root(campaign)/'message-bodies-v1')
    from orchestration_common import digest
    require(request.get('body_reference') == wire['reference'] and request.get('payload') == wire['text']
            and request.get('payload_sha256') == digest(wire['text'].encode())
            and request.get('original_payload_sha256') == digest(full.encode()),
            'TASK_COMMUNICATION_ORIGINAL_WIRE_CHANGED')


def _context(runner, payload, sample, request):
    from upgrade_task_enrollment import _active as actor_context, TaskCampaignGate
    context = actor_context.get()
    require(context is not None and type(context[0]) is TaskCampaignGate,
            'TASK_COMMUNICATION_LIVE_ACTOR_REQUIRED')
    owner, message, received = context
    owner(owner.registration, message, received)
    return _scope(runner, payload, sample, request, owner, message, received)


def current_scope():
    context = _active.get()
    require(context is not None, 'TASK_COMMUNICATION_ACTIVE_OPERATION_REQUIRED')
    runner, payload, sample, request, proof, lease = context
    require(lease['active'] and lease['owner'] == execution_owner() and lease['pid'] == os.getpid(),
            'TASK_COMMUNICATION_OWNER_CHANGED_OR_CLOSED')
    require(_context(runner, payload, sample, request) == json_ref(proof),
            'TASK_COMMUNICATION_ACTIVE_SCOPE_CHANGED')
    return proof


def send(runner, payload, sample):
    from orchestration_transport import PinnedRuntime
    from upgrade_task_bridge import activate
    from upgrade_communication_observer import canonical_identity
    require(_active.get() is None, 'TASK_COMMUNICATION_NESTED_SEND_REFUSED')
    runtime = PinnedRuntime(runner.campaign)
    with runtime.modules('cmux_workspace_guard', 'mac_upgrade_task_bridge_context', 'cmux_agent_adapter') as modules:
        guard, adapter, helper_module = modules
        helper = helper_module.Adapter()
        sender = runner.campaign['sessions']['codex']; receiver = runner.campaign['sessions'][sample['receiver']]
        identity = canonical_identity(dict(workspace_uuid=sender['workspace_uuid'],
            caller_surface_uuid=sender['surface_uuid'], target_surface_uuid=receiver['surface_uuid'],
            target_pane_uuid=receiver['pane_uuid']))
        request = helper.request(identity, 'send', sample['body'], sample['request_id'])
        scope = _context(runner, payload, sample, request)
        path = Path(runner.campaign['root'])/'runs'/payload['run_id']/'communication'/sample['request_id']/'task-actor.json'
        proof = write_json(path, scope, exclusive=True)
        lease = dict(active=True, owner=execution_owner(), pid=os.getpid())
        token = _active.set((runner, payload, sample, request, proof, lease))
        try:
            with activate(runtime, guard, adapter, current_scope):
                current_scope()
                result = helper.deliver(receiver['surface_uuid'], 'send', sample['body'], sample['request_id'],
                                        expected_identity=identity, outer_check=current_scope)
                current_scope()
                return dict(schema='upgrade-task-communication-send-result-v1', request_id=sample['request_id'],
                            original_sender=proof, helper_result=result, business_accepted=False)
        finally:
            lease['active'] = False
            _active.reset(token)


def verify_source(binding, proof_ref, proof, campaign, invocation, *, fixtures=False):
    """The common sender reader already checked enrollment and native invocation."""
    from upgrade_task_communication import original_sample
    from orchestration_common import digest, parse_time
    payload = invocation['operation']['payload']
    sample = original_sample(campaign, payload)
    require(proof.get('schema') == SCHEMA and proof['sample'] == sample and proof['lane'] == sample['receiver']
            and sample['sender'] == 'codex' and payload['communication_request'] == sample['request_id']
            and proof_ref['path'] == str(Path(campaign['root'])/'runs'/proof['run_id']/'communication'/sample['request_id']/'task-actor.json'),
            'TASK_COMMUNICATION_ORIGINAL_SOURCE_CHANGED')
    validate_request(campaign, sample, proof['helper_request'])
    from orchestration_native import NativeReader
    original = NativeReader(campaign['sessions']['codex'], fixtures=fixtures).record(invocation['native'])
    require(binding['payload_sha256'] == digest(proof['helper_request']['payload'].encode())
            and parse_time(original['timestamp']).timestamp() <= binding['bound_at_epoch'],
            'TASK_COMMUNICATION_ORIGINAL_INPUT_CHANGED')
