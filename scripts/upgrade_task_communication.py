"""Original ordinary-message operations on the retained TASK controller.

The fixed 192 slots remain independent of semantic inputs and business packs.
Only the original supervisor's outgoing slots become typed send operations;
Claude keeps its original native commands. Reads require the original sender
invocation and native receiver record, then return the entire referenced body.
"""
from pathlib import Path

from orchestration_common import file_ref, json_ref, require, utc_now

ACTIONS = frozenset({'communication-send', 'communication-read'})
READ_SCHEMA = 'upgrade-task-communication-read-v1'


def samples(campaign):
    from upgrade_communication_plan import WARM_RUNS
    if 'registration' not in campaign or not any(r['run_id'] in WARM_RUNS for r in campaign['runs']):
        return []
    from upgrade_communication_plan import registration_plan
    registration = json_ref(campaign['registration'])
    selected = [s for s in registration_plan(registration)['samples']
                if s['group'] == campaign['session_group']]
    require(all(s['run_id'] in {r['run_id'] for r in campaign['runs']} for s in selected),
            'TASK_COMMUNICATION_ORIGINAL_RUN_REQUIRED')
    return selected


def validate_payload(campaign, action, run, node, request_id):
    found = [s for s in samples(campaign) if s['request_id'] == request_id
             and s['run_id'] == run and s['node_id'] == node]
    require(len(found) == 1, 'TASK_COMMUNICATION_ORIGINAL_SLOT_REQUIRED')
    require((found[0]['sender'] == 'codex') == (action == 'communication-send'),
            'TASK_COMMUNICATION_ORIGINAL_DIRECTION_REQUIRED')
    return dict(communication_request=request_id)


def operation(registration, action, request_id):
    found = [(k, v['payload']) for k, v in registration['operations'].items()
             if v['kind'] == 'campaign-control' and v['payload'].get('action') == action
             and v['payload'].get('communication_request') == request_id]
    require(len(found) == 1, 'TASK_COMMUNICATION_ONE_ORIGINAL_OPERATION_REQUIRED')
    return found[0]


def validate_controls(campaign, registration, operations):
    selected = [p for p in operations.values() if p['action'] in ACTIONS]
    original = samples(campaign)
    require(len(selected) == len(original), 'TASK_COMMUNICATION_COMPLETE_ORIGINAL_CATALOG_REQUIRED')
    for s in original:
        action = 'communication-send' if s['sender'] == 'codex' else 'communication-read'
        operation(registration, action, s['request_id'])


def control(campaign, action, request_id):
    from upgrade_task_control import control_request
    controls = campaign['task_controls']
    name, _ = operation(json_ref(controls['registration']), action, request_id)
    return control_request(controls['registration'], name, controls['evidence_directory'])


def original_sample(campaign, payload):
    from upgrade_communication_plan import campaign_plan, resolve_sample
    found = [s for s in campaign_plan(campaign) if s['request_id'] == payload['communication_request']]
    require(len(found) == 1, 'TASK_COMMUNICATION_ORIGINAL_SLOT_REQUIRED')
    sample = resolve_sample(campaign, found[0])
    require(sample['run_id'] == payload['run_id'] and sample['node_id'] == payload['node_id'],
            'TASK_COMMUNICATION_ORIGINAL_SCOPE_CHANGED')
    return sample


def send_boundary(co, sample):
    from orchestration_coordinator import State
    from orchestration_native import verify_delivery
    from orchestration_transport import journal_bundle
    states, data = co.state(); node = sample['node_id']; info = data[node]
    require(sample['sender'] == 'codex' and sample['receiver'] in ('claude_a', 'claude_b')
            and states[node] == State.DISPATCHED and info.get('mode') == 'delegated_cli'
            and 'delivery' in info and not (co.root/'nodes'/node/'candidate.json').exists(),
            'TASK_COMMUNICATION_ORIGINAL_DISPATCH_BOUNDARY_REQUIRED')
    pack = json_ref(info['prepared']['pack'])
    receipt = Path(pack['completion_receipt'])
    if sample['stage'] == 'after_dispatch':
        # A fast callback may already exist when dispatch returns. The model
        # must send before its callback-consumption operation, not race the
        # executor's filesystem write.
        from upgrade_task_campaign import campaign_control
        callback_control = campaign_control(co.campaign, co.campaign_ref, 'callback', co.spec['run_id'], node_id=node)
        require(not (Path(callback_control['evidence_directory'])/(callback_control['operation_id']+'.json')).exists(),
                'TASK_COMMUNICATION_AFTER_DISPATCH_BOUNDARY_PASSED')
    else:
        require(sample['stage'] == 'before_accept', 'TASK_COMMUNICATION_UNKNOWN_SEND_STAGE')
        bundle = journal_bundle(receipt.with_name(receipt.stem+'-attempts'), receipt)
        verify_delivery(bundle, co.campaign['sessions']['codex'], co.campaign['sessions'][sample['receiver']],
                        pack['completion_callback'], fixtures=co.fixtures, uuid_semantics=True)
        from orchestration_campaign import find_control
        from orchestration_native import NativeReader
        from upgrade_task_campaign import campaign_control
        from upgrade_task_control import native_result
        reader = NativeReader(co.campaign['sessions']['codex'], fixtures=co.fixtures)
        after = reader.verify_pin(co.header['native_start']['codex'])
        current = find_control(reader, control(co.campaign, 'communication-send', sample['request_id']), after=after)
        callback = campaign_control(co.campaign, co.campaign_ref, 'callback', co.spec['run_id'], node_id=node)
        previous = find_control(reader, callback, after=after)
        result = native_result(reader, previous['task_invocation'], callback, until=current['offset'])
        require(result is not None and result['success'] and result['value'] == bundle,
                'TASK_COMMUNICATION_CALLBACK_FULL_RESULT_REQUIRED')
    return info


def handle(runner, registration, payload):
    co = runner.co(payload['run_id'])
    with co.node_locks(payload['node_id']):
        co._gate(); co._work_budget()
        try:
            sample = original_sample(runner.campaign, payload)
            if payload['action'] == 'communication-send':
                send_boundary(co, sample)
                from upgrade_task_message_send import send
                return send(runner, payload, sample)
            from orchestration_native import NativeReader
            from upgrade_communication_attribution import attribute_samples
            from upgrade_body_read import read_result
            readers = {lane: NativeReader(s, fixtures=runner.fixtures)
                       for lane, s in runner.campaign['sessions'].items()}
            ends = {lane: r.snapshot(complete_records=True) for lane, r in readers.items()}
            attribution = attribute_samples(runner.campaign, co.header, ends, readers,
                                           samples=[sample], not_after=utc_now(), fixtures=runner.fixtures)
            require(len(attribution['inputs']) == 1 and not attribution['unresolved'],
                    'TASK_COMMUNICATION_ORIGINAL_NATIVE_RECEIPT_REQUIRED')
            original = attribution['inputs'][0]
            notice = json_ref(original['intent'])['payload']
            return dict(schema=READ_SCHEMA, run_id=payload['run_id'], request_id=sample['request_id'],
                        original=original, body_read=read_result(notice), business_accepted=False)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            # Communication samples must not terminate independent business.
            # The original typed attempt and any helper pending/intent remain
            # consumed. Returning a diagnostic neither retries nor settles it.
            return dict(schema='upgrade-task-communication-unconfirmed-v1',
                status='UNCONFIRMED_NO_REPLAY', run_id=payload['run_id'],
                request_id=payload['communication_request'], action=payload['action'],
                error_type=type(exc).__name__, error=str(exc)[:1000], retries=0,
                native_user_receipt=False, model_consumption=False, business_accepted=False)


def typed_read_evidence(reader, *, fence, until, notice, command, not_before, not_after, request_id):
    from orchestration_campaign import find_control
    from orchestration_common import parse_time
    from upgrade_task_control import native_result
    from upgrade_body_read import read_result, SCOPE
    if not (Path(command['evidence_directory'])/(command['operation_id']+'.json')).exists():
        return []
    start = reader.verify_pin(fence) if isinstance(fence, dict) else fence
    call = find_control(reader, command, after=start)
    result = native_result(reader, call['task_invocation'], command, until=until)
    if result is None:
        return []
    value = result['value']
    require(result['success'] and value.get('schema') == READ_SCHEMA
            and value.get('request_id') == request_id and value.get('body_read') == read_result(notice)
            and value.get('business_accepted') is False,
            'TASK_COMMUNICATION_NATIVE_COMPLETE_READ_REQUIRED')
    require(parse_time(result['call_timestamp']).timestamp() >= not_before - .001
            and parse_time(result['timestamp']).timestamp() <= not_after,
            'TASK_COMMUNICATION_READ_TIME_OUTSIDE_WINDOW')
    return [dict(kind='BODY_READ_OBSERVED', scope=SCOPE, session_id=reader.session['session_id'],
        **{k: result[k] for k in ('call_id', 'call_record', 'result_record', 'call_timestamp', 'timestamp')},
        reference=value['body_read']['reference'])]
