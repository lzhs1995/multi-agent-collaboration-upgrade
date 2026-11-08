"""Original S2 messages through the retained TASK controller.

The model still decides each disposition after the full native body read. Its
own assistant JSON record supplies the reason and existing work nodes; neither
the catalog nor the controller invents a decision. Every mutating operation has
one existing ledger attempt. Ordinary-message send authority is unchanged.
"""
import json
from pathlib import Path

from orchestration_common import file_ref, json_ref, parse_time, read_ref, require, utc_now

ACTIONS = frozenset({'semantic-status', 'semantic-order', 'semantic-read', 'semantic-disposition'})
READ_SCHEMA = 'upgrade-task-semantic-read-v1'
STATEMENT_SCHEMA = 'upgrade-task-semantic-decision-v1'
DISPOSITION_SCHEMA = 'upgrade-task-semantic-disposition-result-v1'


def samples(campaign):
    """Original slots also usable before native campaign adoption/freezing."""
    if 'registration' not in campaign:
        return []
    registration = json_ref(campaign['registration'])
    if 'semantic_input' not in registration:
        return []
    from upgrade_semantic_inputs import registration_slots
    if 'semantic_input' in campaign:
        require(read_ref(campaign['semantic_input']) == read_ref(registration['semantic_input']),
                'TASK_SEMANTIC_ORIGINAL_BUNDLE_CHANGED')
    selected = [s for s in registration_slots(registration) if s['group'] == campaign['session_group']]
    require(len(selected) == 3 * len(campaign['runs'])
            and {s['run_id'] for s in selected} == {r['run_id'] for r in campaign['runs']},
            'TASK_SEMANTIC_ORIGINAL_GROUP_REQUIRED')
    return selected


def node_for(campaign):
    from orchestration_binding import unbound_nodes
    nodes = unbound_nodes(json_ref(campaign['plan']))
    require(nodes, 'TASK_SEMANTIC_ORIGINAL_UNBOUND_NODE_REQUIRED')
    return nodes[0]


def validate_payload(campaign, action, run, node, request_id):
    selected = [s for s in samples(campaign) if s['run_id'] == run]
    require(len(selected) == 3 and node == node_for(campaign), 'TASK_SEMANTIC_ORIGINAL_SCOPE_REQUIRED')
    if action in ('semantic-read', 'semantic-disposition'):
        require(request_id in {s['request_id'] for s in selected}, 'TASK_SEMANTIC_UNKNOWN_MESSAGE')
        return {'semantic_request': request_id}
    require(request_id is None, 'TASK_SEMANTIC_UNEXPECTED_MESSAGE')
    return {}


def operation(registration, action, run, request_id=None):
    found = [(k, v['payload']) for k, v in registration['operations'].items()
             if v['kind'] == 'campaign-control' and v['payload'].get('action') == action
             and v['payload'].get('run_id') == run and v['payload'].get('semantic_request') == request_id]
    require(len(found) == 1, 'TASK_SEMANTIC_ONE_ORIGINAL_OPERATION_REQUIRED:' + action)
    return found[0]


def validate_controls(campaign, registration, operations):
    original = samples(campaign)
    selected = [p for p in operations.values() if p['action'] in ACTIONS]
    if not original:
        require(not selected, 'TASK_SEMANTIC_UNREGISTERED_INPUT')
        return
    from upgrade_task_advice import one_operation
    for run in {s['run_id'] for s in original}:
        _, order = operation(registration, 'semantic-order', run)
        require(order['node_id'] == node_for(campaign), 'TASK_SEMANTIC_ORDER_NODE_CHANGED')
        one_operation(registration, 'advice', run, order['node_id'])
        require(any(p['action'] == 'semantic-status' and p['run_id'] == run for p in selected),
                'TASK_SEMANTIC_READINESS_OBSERVATION_REQUIRED')
        for sample in (s for s in original if s['run_id'] == run):
            for action in ('semantic-read', 'semantic-disposition'):
                operation(registration, action, run, sample['request_id'])


def control(campaign, action, run, request_id):
    from upgrade_task_control import control_request
    controls = campaign['task_controls']
    name, _ = operation(json_ref(controls['registration']), action, run, request_id)
    return control_request(controls['registration'], name, controls['evidence_directory'])


def _result(campaign, campaign_ref, registration, action, run, request_id, reader, until):
    from upgrade_task_advice import completed_result
    name, _ = operation(registration, action, run, request_id)
    return completed_result(campaign, campaign_ref, registration, name, reader, until=until)


def _batch(co):
    from orchestration_native import NativeReader
    from upgrade_semantic_inputs import native_batch
    readers = {lane: NativeReader(session, fixtures=co.fixtures)
               for lane, session in co.campaign['sessions'].items()}
    ends = {lane: reader.snapshot(complete_records=True) for lane, reader in readers.items()}
    return native_batch(co.campaign, co.header, ends, readers, not_after=utc_now())


def _order(co, registration, reader, until):
    ref, result = _result(co.campaign, co.campaign_ref, registration, 'semantic-order',
                         co.spec['run_id'], None, reader, until)
    value = json_ref(file_ref(co.root/'jev-inbox'/('semantic-' + co.spec['run_id'] + '.json')))
    require(result['value'] == value, 'TASK_SEMANTIC_ORIGINAL_ORDER_CHANGED')
    return ref, value


def declaration(reader, *, after, until, run, request_id, body_sha256, nodes, not_after=None):
    """One actual assistant statement, never user input, tool output or echo."""
    found = []
    for ref, record in reader.records(after, until):
        p = record.get('payload', {})
        if record.get('type') != 'response_item' or p.get('type') != 'message' or p.get('role') != 'assistant':
            continue
        content = p.get('content')
        if not isinstance(content, list) or len(content) != 1 or content[0].get('type') != 'output_text':
            continue
        text = content[0].get('text')
        if not isinstance(text, str) or len(text.encode()) > 16000:
            continue
        try:
            from orchestration_common import strict_json
            value = strict_json(text)
        except (ValueError, TypeError, RuntimeError):
            continue
        if not isinstance(value, dict) or value.get('schema') != STATEMENT_SCHEMA or value.get('request_id') != request_id:
            continue
        require(set(value) == {'schema', 'run_id', 'request_id', 'inner_body_sha256', 'disposition', 'reason', 'work'}
                and value['run_id'] == run and value['inner_body_sha256'] == body_sha256,
                'TASK_SEMANTIC_DECLARATION_SCOPE_CHANGED')
        require(value['disposition'] in ('adopt', 'defer', 'refuse')
                and isinstance(value['reason'], str) and value['reason'].strip()
                and len(value['reason']) <= 2000 and '\0' not in value['reason']
                and isinstance(value['work'], list) and all(isinstance(n, str) and n in nodes for n in value['work'])
                and len(set(value['work'])) == len(value['work']), 'TASK_SEMANTIC_DECLARATION_INVALID')
        if not_after is not None:
            require(parse_time(record['timestamp']).timestamp() <= not_after, 'TASK_SEMANTIC_DECLARATION_TOO_LATE')
        found.append(dict(native_record=ref, value=value))
    require(len(found) == 1, 'TASK_SEMANTIC_ONE_MODEL_DECLARATION_REQUIRED')
    return found[0]


def handle(runner, registration, payload):
    """Non-advisory operations; ordering uses the existing advice capability."""
    from orchestration_native import NativeReader
    from orchestration_campaign import find_control
    from upgrade_task_campaign import campaign_control, ARGUMENTS
    from upgrade_body_read import read_result
    co = runner.co(payload['run_id'])
    reader = NativeReader(runner.campaign['sessions']['codex'], fixtures=runner.fixtures)
    script = campaign_control(runner.campaign, runner.ref, payload['action'], payload['run_id'],
                              **{k: payload.get(k) for k in ARGUMENTS})
    current = find_control(reader, script, after=reader.verify_pin(co.header['native_start']['codex']))
    until = current['offset']
    with co.node_locks(payload['node_id']):
        co._gate()
        if payload['action'] == 'semantic-status':
            batch = _batch(co)
            # Readiness must not expose bodies ahead of the chosen read order.
            return dict(schema='upgrade-task-semantic-status-v1', ready=batch['ready'],
                        attribution=batch['attribution'], business_accepted=False)
        order_ref, ordered = _order(co, registration, reader, until)
        request_id = payload['semantic_request']
        reads = {r['request_id']: r for r in ordered['read_requests']}
        request = reads[request_id]
        if payload['action'] == 'semantic-read':
            # Require complete native outputs of every preceding ordered body.
            for preceding in ordered['order']['order'][:ordered['order']['order'].index(request_id)]:
                _, prior = _result(runner.campaign, runner.ref, registration, 'semantic-read',
                                   payload['run_id'], preceding, reader, until)
                require(prior['value']['body_read'] == read_result(reads[preceding]['notice']),
                        'TASK_SEMANTIC_PRECEDING_BODY_NOT_EXPOSED')
            return dict(schema=READ_SCHEMA, request_id=request_id, order=order_ref,
                        inner_body_sha256=request['inner_body_sha256'], body_read=read_result(request['notice']))
        require(payload['action'] == 'semantic-disposition', 'TASK_SEMANTIC_UNKNOWN_HANDLER_ACTION')
        _, read = _result(runner.campaign, runner.ref, registration, 'semantic-read',
                          payload['run_id'], request_id, reader, until)
        require(read['value']['body_read'] == read_result(request['notice'])
                and read['value']['inner_body_sha256'] == request['inner_body_sha256'],
                'TASK_SEMANTIC_NATIVE_COMPLETE_READ_REQUIRED')
        end = read['result_record']['offset'] + read['result_record']['length']
        decision = declaration(reader, after=end, until=until, run=payload['run_id'], request_id=request_id,
                               body_sha256=request['inner_body_sha256'], nodes=co.nodes)
        from jev_advice_run import record_disposition
        value = decision['value']
        ref = record_disposition(co, request_id, value['disposition'], value['reason'], value['work'])
        return dict(schema=DISPOSITION_SCHEMA, disposition=ref, declaration=decision)


def typed_read_evidence(reader, *, fence, until, notice, command, not_before, not_after, request_id, body_sha256):
    from orchestration_campaign import find_control
    from upgrade_task_control import native_result
    from upgrade_body_read import read_result, SCOPE
    start = reader.verify_pin(fence) if isinstance(fence, dict) else fence
    path = Path(command['evidence_directory']) / (command['operation_id'] + '.json')
    if not path.exists():
        return []
    ref = find_control(reader, command, after=start)
    result = native_result(reader, ref['task_invocation'], command, until=until)
    if result is None:
        return []
    value = result['value']
    require(result['success'] and value.get('schema') == READ_SCHEMA
            and value.get('request_id') == request_id and value.get('inner_body_sha256') == body_sha256
            and value.get('body_read') == read_result(notice), 'TASK_SEMANTIC_NATIVE_COMPLETE_READ_REQUIRED')
    require(parse_time(result['call_timestamp']).timestamp() >= not_before - 0.001
            and parse_time(result['timestamp']).timestamp() <= not_after,
            'TASK_SEMANTIC_READ_TIME_OUTSIDE_WINDOW')
    return [dict(kind='BODY_READ_OBSERVED', scope=SCOPE, session_id=reader.session['session_id'],
                 **{k: result[k] for k in ('call_id', 'call_record', 'result_record', 'call_timestamp', 'timestamp')},
                 reference=value['body_read']['reference'])]


def disposition_pair(co, reader, run, request_id, ref, read, *, until, not_after):
    registration = json_ref(co.campaign['task_controls']['registration'])
    _, result = _result(co.campaign, co.campaign_ref, registration, 'semantic-disposition', run, request_id, reader, until)
    value = result['value']
    require(value.get('schema') == DISPOSITION_SCHEMA and value.get('disposition') == ref
            and read is not None, 'TASK_SEMANTIC_DISPOSITION_RESULT_CHANGED')
    original = json_ref(ref)
    decision = declaration(reader, after=read['result_record']['offset'] + read['result_record']['length'],
        until=result['call_record']['offset'], run=run, request_id=request_id,
        body_sha256=original['inner_body_sha256'], nodes=co.nodes, not_after=not_after)
    require(value['declaration'] == decision
            and all(decision['value'][k] == original[k] for k in ('disposition', 'reason', 'work'))
            and parse_time(result['timestamp']).timestamp() <= not_after,
            'TASK_SEMANTIC_ORIGINAL_MODEL_DISPOSITION_CHANGED')
    return {k: result[k] for k in ('call_id', 'call_record', 'result_record')}
