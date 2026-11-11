"""Bounded continuity handoff and immutable result publication.

No provider call, command execution, dispatch, lease release or original-file
rewrite is offered. The loader authenticates the original task and each call;
this module additionally preserves one shared scope claim across extensions.
"""
from pathlib import Path
import re
import time

from orchestration_common import (checked_path, digest, exclusive_lock,
    file_ref, json_ref, object_sha, read_ref, require, write_bytes_once, write_json)

ACTIONS = frozenset({'continuity-work', 'continuity-result'})
CONTRACT = 'upgrade-task-continuity-contract-v1'
BUDGET = 'upgrade-task-continuity-budget-v1'
CHECKPOINT = 'upgrade-task-continuity-checkpoint-v1'
WORK = 'upgrade-task-continuity-work-v1'
SUBMISSION = 'upgrade-task-continuity-submission-v1'
REVIEW = 'upgrade-task-continuity-review-v1'
RESULT = 'upgrade-task-continuity-result-v1'
LANES = frozenset({'codex', 'claude_a', 'claude_b'})
MAX_TEXT = 128 * 1024


def _fields(value, fields, reason):
    require(isinstance(value, dict) and set(value) == set(fields.split()), reason)


def _name(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value),
            'CONTINUITY_NAME_INVALID')
    return value


def _budget(ref, task_id):
    value = json_ref(ref)
    _fields(value, 'schema task_id max_subagents child_budget_scope children_used review_limit review_rounds_used review_history',
            'CONTINUITY_BUDGET_FIELDS')
    children, used, history = value['children_used'], value['review_rounds_used'], value['review_history']
    require(value['schema'] == BUDGET and value['task_id'] == task_id
            and type(value['max_subagents']) is int and value['max_subagents'] == 2
            and value['child_budget_scope'] == 'WHOLE_TASK'
            and isinstance(children, list) and len(children) <= 2
            and all(isinstance(x, str) and x for x in children) and len(set(children)) == len(children)
            and type(value['review_limit']) is int and value['review_limit'] == 3
            and type(used) is int and 0 <= used <= 3
            and isinstance(history, list) and len(history) == used,
            'CONTINUITY_ORIGINAL_WHOLE_TASK_BUDGET_REQUIRED')
    for item in history:
        read_ref(item, 'original consumed review')
    require(len({object_sha(item) for item in history}) == len(history), 'CONTINUITY_DUPLICATE_REVIEW')
    return value


def _contract(ref, registration):
    value = json_ref(ref)
    _fields(value, 'schema task_id base_registration original_authority continuity_registration budget checkpoint',
            'CONTINUITY_CONTRACT_FIELDS')
    require(value['schema'] == CONTRACT and value['task_id'] == registration['task_id']
            and value['base_registration'] == registration['base_registration']
            and value['budget'] == registration['budget'], 'CONTINUITY_ORIGINAL_TASK_CHANGED')
    base = json_ref(value['base_registration'])
    require(value['original_authority'] == base['authority_ref'], 'CONTINUITY_ORIGINAL_AUTHORITY_CHANGED')
    read_ref(value['original_authority'])
    budget = _budget(value['budget'], value['task_id'])
    checkpoint = json_ref(value['checkpoint'])
    _fields(checkpoint, 'schema task_id base_registration budget scopes', 'CONTINUITY_CHECKPOINT_FIELDS')
    require(checkpoint['schema'] == CHECKPOINT and checkpoint['task_id'] == value['task_id']
            and checkpoint['base_registration'] == value['base_registration']
            and checkpoint['budget'] == value['budget'] and isinstance(checkpoint['scopes'], list)
            and 0 < len(checkpoint['scopes']) <= 32, 'CONTINUITY_CHECKPOINT_CHANGED')
    scopes = {}
    for scope in checkpoint['scopes']:
        _fields(scope, 'scope_id mode writer_lane output_name inputs handoff nonce_state lease_state',
                'CONTINUITY_SCOPE_FIELDS')
        key = _name(scope['scope_id'])
        require(key not in scopes and scope['mode'] in ('CODEX_SOLO', 'CLAUDE_PEER'), 'CONTINUITY_SCOPE_CHANGED')
        require(scope['writer_lane'] == 'codex' if scope['mode'] == 'CODEX_SOLO'
                else scope['writer_lane'] in ('claude_a', 'claude_b'), 'CONTINUITY_SINGLE_WRITER_REQUIRED')
        name = scope['output_name']
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', name)
                and '..' not in name and name not in ('work.json', 'result.json', 'result-intent.json'),
                'CONTINUITY_FIXED_ARTIFACT_NAME_REQUIRED')
        require(isinstance(scope['inputs'], list) and 0 < len(scope['inputs']) <= 32,
                'CONTINUITY_ORIGINAL_INPUTS_REQUIRED')
        for item in [*scope['inputs'], scope['handoff']]:
            read_ref(item, 'original scope input/handoff')
        # These pinned controller observations are a bounded prerequisite, not a
        # replacement resource manager. Drift fails; no release/repair exists.
        for key_, expected in (('nonce_state', 'CLEAR'), ('lease_state', 'NONE')):
            guard = json_ref(scope[key_])
            require(guard == dict(schema='upgrade-task-continuity-guard-v1', task_id=value['task_id'],
                    scope_id=key, kind=key_, state=expected, pending=[]),
                    'CONTINUITY_PENDING_OR_UNKNOWN_NO_RELEASE')
        scopes[key] = scope
    return value, budget, scopes


def validate_operation(payload, registration):
    action = payload.get('action')
    fields = 'action contract scope_id' + (' work_operation_id statement review_statement' if action == 'continuity-result' else '')
    _fields(payload, fields, 'CONTINUITY_OPERATION_FIELDS')
    require(action in ACTIONS, 'CONTINUITY_ACTION_INVALID')
    _, _, scopes = _contract(payload['contract'], registration)
    require(payload['scope_id'] in scopes, 'CONTINUITY_UNKNOWN_SCOPE')
    if action == 'continuity-result':
        work_id = payload['work_operation_id']
        work = registration['operations'].get(work_id)
        require(work == dict(action='continuity-work', payload=dict(contract=payload['contract'],
                scope_id=payload['scope_id'])), 'CONTINUITY_EXACT_ORIGINAL_WORK_REQUIRED')
        require(payload['statement'] is None or isinstance(payload['statement'], dict), 'CONTINUITY_STATEMENT_PIN_INVALID')
        require(payload['review_statement'] is None or isinstance(payload['review_statement'], dict), 'CONTINUITY_REVIEW_PIN_INVALID')
    for selected in ACTIONS:
        matches = [op for op in registration['operations'].values() if op['action'] == selected
                   and op['payload'].get('scope_id') == payload['scope_id']]
        require(len(matches) <= 1, 'CONTINUITY_SCOPE_OPERATION_DUPLICATE')


def _input_bodies(scope):
    """Expose all original UTF-8 text; pin-only output is not a body read."""
    result = []
    total = 0
    for kind, refs in (('input', scope['inputs']), ('handoff', [scope['handoff']])):
        for ref in refs:
            raw = read_ref(ref, 'complete continuity body')
            total += len(raw)
            require(total <= 128 * 1024, 'CONTINUITY_INPUT_BODY_BUDGET')
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValueError('CONTINUITY_UTF8_TEXT_INPUT_REQUIRED') from exc
            require('\0' not in text, 'CONTINUITY_TEXT_INPUT_REQUIRED')
            result.append(dict(kind=kind, reference=ref, encoding='utf-8', full_text=text))
    return result


def _evaluate(context, contract):
    """Read original native terminals and run only the pinned original planner."""
    from upgrade_continuity_native import (validate_native_registration, collect_native_outcomes,
        invoke_original_plan, _retry_delay_pending_lanes)
    from upgrade_live_runtime import identity_matches
    registered, runtime = validate_native_registration(contract['continuity_registration'])
    require(registered['task_id'] == context.task_id and registered['runtime'] == context.registration['runtime']
            and registered['adoption'] == context.registration['adoption'], 'CONTINUITY_NATIVE_REGISTRATION_CHANGED')
    for lane in LANES:
        require(identity_matches(registered['sessions'][lane], context.sessions[lane]), 'CONTINUITY_ORIGINAL_SESSION_CHANGED')
    now = time.time()
    collection = collect_native_outcomes(registered['sessions'], task_id=context.task_id, now=now)
    require(not collection['pending_lanes'] and not _retry_delay_pending_lanes(registered['sessions'], collection),
            'CONTINUITY_NATIVE_PENDING_NO_TAKEOVER')
    result = invoke_original_plan(runtime['controller'], runtime['python'], registered['sessions'], collection,
        registered['authorization'], output=context.directory/'native-plan', now=now,
        runtime_ref=registered['runtime'], adoption_ref=registered['adoption'])
    require(result['plan']['mode'] in ('CODEX_SOLO', 'CLAUDE_PEER')
            and result['plan']['max_subagents'] == 2 and result['plan']['child_budget_scope'] == 'WHOLE_TASK'
            and result['plan']['resource_releases'] == result['plan']['input_operations'] == 0,
            'CONTINUITY_ORIGINAL_FALLBACK_PLAN_REQUIRED')
    return dict(collection=collection, original=result)


def _paths(registration, scope_id):
    root = checked_path(Path(registration['base_registration']['path']).parent/'task-capability-extensions')
    require(checked_path(registration['output_root']).parent == root, 'CONTINUITY_FIXED_CONTROLLER_ROOT_REQUIRED')
    shared = root/'continuity-ledger'
    return shared, shared/('scope-' + object_sha([registration['task_id'], scope_id]))


def _anchor(shared, contract_ref, budget_ref, *, create):
    expected = dict(schema='upgrade-task-continuity-anchor-v1', contract=contract_ref, budget=budget_ref)
    path = shared/'anchor.json'
    if path.exists():
        require(json_ref(file_ref(path)) == expected, 'CONTINUITY_NO_CONTRACT_OR_BUDGET_RESET')
    else:
        require(create, 'CONTINUITY_ORIGINAL_ANCHOR_MISSING')
        write_json(path, expected, exclusive=True)
    for scope in shared.glob('scope-*'):
        intent, result = scope/'result-intent.json', scope/'result.json'
        require(not intent.exists() or result.exists(),
                'CONTINUITY_UNKNOWN_RESULT_READ_ORIGINAL_NO_REPLAY')
        if result.exists():
            receipt = json_ref(file_ref(result))
            marker = shared / f"review-{receipt['review_round']:03d}.json"
            require(marker.exists() and json_ref(file_ref(marker)) ==
                    dict(review_round=receipt['review_round'], result=file_ref(result)),
                    'CONTINUITY_REVIEW_COMMIT_UNKNOWN_NO_REPLAY')


def _rounds(shared, budget):
    reviews = sorted(shared.glob('review-*.json'))
    expected = list(range(budget['review_rounds_used'] + 1, budget['review_rounds_used'] + len(reviews) + 1))
    require([int(p.stem.removeprefix('review-')) for p in reviews] == expected,
            'CONTINUITY_REVIEW_HISTORY_GAP')
    for path in reviews:
        value = json_ref(file_ref(path))
        require(value['review_round'] == int(path.stem.removeprefix('review-')), 'CONTINUITY_REVIEW_HISTORY_CHANGED')
        receipt = json_ref(value['result'])
        require(receipt['review_round'] == value['review_round'], 'CONTINUITY_REVIEW_RECEIPT_CHANGED')
    return budget['review_rounds_used'] + len(reviews)


def dispatch(action, payload, context):
    from upgrade_task_claude_continuity_route import PeerContext, require_context as require_peer
    if type(context) is PeerContext:
        require_peer(context)
    else:
        from upgrade_task_capability_extension import require_context
        require_context(context)
    context.assert_operation(action, payload)
    validate_operation(dict(action=action, **payload), context.registration)
    contract, budget, scopes = _contract(payload['contract'], context.registration)
    scope = scopes[payload['scope_id']]
    shared, directory = _paths(context.registration, payload['scope_id'])
    shared.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive_lock(shared/'continuity.lock'):
        context.recheck()
        _anchor(shared, payload['contract'], contract['budget'], create=action == 'continuity-work')
        used = _rounds(shared, budget)
        require(used < 3, 'CONTINUITY_REVIEW_BUDGET_EXHAUSTED')
        if action == 'continuity-work':
            require(not directory.exists(), 'CONTINUITY_SCOPE_ALREADY_CLAIMED_NO_REPLAY')
            observed = _evaluate(context, contract)
            require(observed['original']['plan']['mode'] == scope['mode'], 'CONTINUITY_MODE_CHANGED')
            inputs = _input_bodies(scope)
            context.recheck(); _contract(payload['contract'], context.registration)
            directory.mkdir(mode=0o700)
            value = dict(schema=WORK, task_id=context.task_id, extension=context.registration_ref,
                operation_id=context.operation_id, contract=payload['contract'], scope=scope,
                original_budget=contract['budget'], consumed_review_rounds=used, input_bodies=inputs,
                output_path=str(directory/scope['output_name']), native_plan=observed,
                statements_after={lane:context.reader(lane).snapshot(complete_records=True) for lane in LANES},
                child_creation_authorized=False, resource_releases=0, input_operations=0,
                lease_transfer_authorized=False, business_accepted=False)
            context.recheck()
            ref = write_json(directory/'work.json', value, exclusive=True)
            return dict(schema=WORK, work=ref, value=value)
        work_ref = file_ref(directory/'work.json'); work = json_ref(work_ref)
        completed_ref, completed, native_output = context.completed_operation(payload['work_operation_id'])
        require(work['input_bodies'] == _input_bodies(scope), 'CONTINUITY_ORIGINAL_INPUT_BODIES_CHANGED')
        require(completed == dict(schema=WORK, work=work_ref, value=work)
                and work['extension'] == context.registration_ref
                and work['operation_id'] == payload['work_operation_id']
                and work['contract'] == payload['contract'] and work['scope'] == scope
                and work['original_budget'] == contract['budget'], 'CONTINUITY_ORIGINAL_WORK_NOT_CONSUMED')
        require(not (directory/'result-intent.json').exists() and not (directory/'result.json').exists(),
                'CONTINUITY_RESULT_ALREADY_ATTEMPTED_NO_REPLAY')
        writer = scope['writer_lane']
        start = context.reader(writer).verify_pin(work['statements_after'][writer])
        statement = context.native_statement(payload['statement'], SUBMISSION, lane=writer, after=start)
        common = dict(schema=SUBMISSION, task_id=context.task_id, extension_id=context.registration['extension_id'],
                      operation_id=context.operation_id, work=work_ref)
        require(set(statement) == set(common) | {'text', 'self_review', 'limitations'}
                and all(statement[k] == v for k,v in common.items()), 'CONTINUITY_SUBMISSION_BINDING_CHANGED')
        text = statement['text']; review = statement['self_review']; limitations = statement['limitations']
        require(isinstance(text, str) and 0 < len(text.encode()) <= MAX_TEXT and '\0' not in text
                and isinstance(review, str) and 0 < len(review.encode()) <= 8192
                and isinstance(limitations, list) and len(limitations) <= 32
                and all(isinstance(x,str) and 0 < len(x.encode()) <= 4096 for x in limitations),
                'CONTINUITY_BOUNDED_RESULT_REQUIRED')
        sha = digest(text.encode())
        peer = None
        if scope['mode'] == 'CLAUDE_PEER':
            reviewer = 'claude_b' if writer == 'claude_a' else 'claude_a'
            peer = context.native_statement(payload['review_statement'], REVIEW, lane=reviewer,
                after=context.reader(reviewer).verify_pin(work['statements_after'][reviewer]))
            expected = dict(schema=REVIEW, task_id=context.task_id, extension_id=context.registration['extension_id'],
                operation_id=context.operation_id, work=work_ref, artifact_sha256=sha)
            require(set(peer) == set(expected) | {'reason', 'verdict'} and all(peer[k] == v for k,v in expected.items())
                    and peer['verdict'] in ('accept', 'revise', 'reject')
                    and isinstance(peer['reason'],str) and 0 < len(peer['reason'].encode()) <= 8192,
                    'CONTINUITY_INDEPENDENT_CLAUDE_REVIEW_REQUIRED')
        else:
            require(payload['review_statement'] is None, 'CONTINUITY_SOLO_CANNOT_CLAIM_PEER_REVIEW')
        context.recheck(); _contract(payload['contract'], context.registration)
        # From this fsynced intent onward, any failure stays consumed. Neither
        # another operation nor another extension may overwrite/release it.
        intent = write_json(directory/'result-intent.json', dict(schema='upgrade-task-continuity-result-intent-v1',
            extension=context.registration_ref, operation_id=context.operation_id, work=work_ref,
            completed_work=completed_ref, native_output=native_output, statement=statement,
            peer_review=peer, artifact_sha256=sha, review_round=used+1), exclusive=True)
        context.recheck()
        artifact = write_bytes_once(directory/scope['output_name'], text.encode())
        context.recheck(); _contract(payload['contract'], context.registration)
        result = dict(schema=RESULT, status='SCOPED_ARTIFACT_RECORDED', task_id=context.task_id,
            work=work_ref, intent=intent, artifact=artifact, writer_session=context.sessions[writer]['session_id'],
            validation='solo_self_review' if peer is None else 'claude_peer_review',
            review_verdict='self_assessed' if peer is None else peer['verdict'],
            peer_accepted=peer is not None and peer['verdict'] == 'accept',
            peer_session=None if peer is None else context.sessions[reviewer]['session_id'],
            limitations=limitations, original_budget=contract['budget'], children_used=budget['children_used'],
            child_budget_scope='WHOLE_TASK', max_subagents=2, new_children=0,
            review_round=used+1, review_limit=3, input_operations=0, resource_releases=0,
            formal_business_accepted=False, live_transport_verified=False)
        context.recheck()
        ref = write_json(directory/'result.json', result, exclusive=True)
        write_json(shared/f'review-{used+1:03d}.json', dict(review_round=used+1, result=ref), exclusive=True)
        return dict(schema=RESULT, result=ref, value=result)
