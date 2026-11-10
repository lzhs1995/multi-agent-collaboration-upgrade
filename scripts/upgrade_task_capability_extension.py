"""Versioned, additive task capabilities; never amend the original TASK ledger.

Registration is inert. CapabilityService explicitly activates a closed-turn
handoff on the SAME adopted thread, using the original execute tool's registered
extension prefix and its live recorded RPC. This library exposes no launcher,
model input, socket response or generic command execution. Importing it alone
does not give an old TASK559 new operations.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from orchestration_common import (canonical, checked_path, contract_errors, digest,
    exclusive_lock, file_ref, json_ref, object_sha, read_ref, require, strict_json,
    utc_now, write_json)
from orchestration_native import NativeReader
from upgrade_task_operations import validate_registration

REQUEST_SCHEMA = 'upgrade-task-capability-request-v1'
SCHEMA = 'upgrade-task-capability-extension-v1'
NAMESPACE = 'upgrade_collaboration'
ACTIONS = frozenset({'inquiry-read', 'inquiry-answer', 'continuity-work', 'continuity-result'})
_CONTEXT_KEY = object()


def _name(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value),
            'CAPABILITY_NAME_INVALID')
    return value


def invocation_script(extension_id, operation_id):
    from upgrade_task_control import invocation_script as original_script
    return original_script(routed_operation(extension_id, operation_id))


def routed_operation(extension_id, operation_id):
    _name(extension_id); _name(operation_id)
    return _name('extension:' + extension_id + ':' + operation_id)


def tool_definition():
    from upgrade_task_operations import tool_definition as original_tool
    return original_tool()


def _ledger_snapshot(descriptor, registration):
    """Read original attempts, including UNKNOWN, without opening a writer."""
    require(set(descriptor) == {'path', 'device', 'inode'}, 'CAPABILITY_LEDGER_PIN_INVALID')
    path = checked_path(descriptor['path']); info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.geteuid() and info.st_nlink == 1
            and (info.st_dev, info.st_ino) == (descriptor['device'], descriptor['inode']),
            'CAPABILITY_ORIGINAL_LEDGER_REPLACED')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        body = db.execute('SELECT body FROM registration WHERE id=1').fetchone()
        require(body is not None and strict_json(body[0]) == registration,
                'CAPABILITY_BASE_REGISTRATION_CHANGED')
        calls = db.execute('SELECT call_id,operation_id,request_key,request,phase,result,receipt '
                           'FROM calls ORDER BY call_id').fetchall()
        turns = db.execute('SELECT sequence,turn_id,scope,grant FROM turns ORDER BY sequence').fetchall()
        events = db.execute('SELECT sequence,call_id,kind,body FROM events ORDER BY sequence').fetchall()
        bindings = db.execute('SELECT operation_id,body FROM bindings ORDER BY operation_id').fetchall()
        def decode(rows):
            return [[x.decode() if isinstance(x, bytes) else x for x in row] for row in rows]
        require(turns and not any(r[4] in ('INTENDED', 'RESULT_RECORDED', 'RESPONSE_INTENDED')
                                  for r in calls), 'CAPABILITY_BASE_PENDING_NO_TAKEOVER')
        return dict(registration_sha256=object_sha(registration), active_turn=turns[-1][1],
            calls=decode(calls), turns=decode(turns), events=decode(events), bindings=decode(bindings))
    finally:
        db.close()


def _runtime_binding(request):
    from upgrade_live_runtime import validate_session_authority
    from upgrade_task_readiness import task_startup_result
    runtime = json_ref(request['runtime'])
    proof = validate_session_authority(request['runtime'], request['adoption'], allow_stopped=True)
    spec = json_ref(runtime['startup'])
    _, startup, anchor = task_startup_result(spec)
    require(startup['operations'] == request['base_registration']
            and startup['ledger'] == request['base_ledger']['path'],
            'CAPABILITY_ORIGINAL_STARTUP_REQUIRED')
    sessions = copy.deepcopy(proof['sessions'])
    for lane, session in sessions.items():
        session['private_native'] = dict(runtime=request['runtime'], adoption=request['adoption'])
    return runtime, spec, sessions, anchor


def _assistant(reader, ref):
    record = reader.record(ref)
    if reader.provider == 'codex':
        p = record.get('payload', {})
        require(record.get('type') == 'response_item' and p.get('type') == 'message'
                and p.get('role') == 'assistant', 'CAPABILITY_NATIVE_ASSISTANT_REQUIRED')
        blocks = p.get('content')
        require(isinstance(blocks, list) and len(blocks) == 1
                and blocks[0].get('type') == 'output_text', 'CAPABILITY_COMPLETE_STATEMENT_REQUIRED')
        text = blocks[0].get('text')
    else:
        p = record.get('message', {})
        require(record.get('type') == 'assistant' and p.get('role') == 'assistant'
                and record.get('sessionId') == reader.session['session_id'],
                'CAPABILITY_NATIVE_ASSISTANT_REQUIRED')
        blocks = p.get('content')
        require(isinstance(blocks, list) and len(blocks) == 1
                and blocks[0].get('type') == 'text', 'CAPABILITY_COMPLETE_STATEMENT_REQUIRED')
        text = blocks[0].get('text')
    require(isinstance(text, str) and 0 < len(text.encode()) <= 256*1024,
            'CAPABILITY_STATEMENT_BUDGET')
    return strict_json(text), record


def scope_digest(request):
    """The model accepts this exact scope; authority cannot accept itself."""
    return object_sha({k:v for k,v in request.items() if k != 'authority'})


def _authority(request, sessions, base):
    from orchestration_freeze_v3 import authorize
    authority = json_ref(request['authority'])
    goal = authority.get('schema') == 'upgrade-task-capability-authority-v2'
    source_field = 'goal_authorization' if goal else 'native_authorizations'
    require(set(authority) == {'schema', 'task_id', 'scope_sha256', source_field,
                              'owner_acceptance', 'budget'}
            and authority['schema'] in ('upgrade-task-capability-authority-v1','upgrade-task-capability-authority-v2')
            and authority['task_id'] == base['task_id']
            and authority['scope_sha256'] == scope_digest(request), 'CAPABILITY_AUTHORITY_SCOPE_CHANGED')
    if goal:
        from upgrade_task_goal_authority import verify_goal_authority
        source = authority[source_field]
        require(set(source) == {'evidence','context'}, 'CAPABILITY_GOAL_AUTHORITY_INVALID')
        verify_goal_authority(source['evidence'], expected_context=source['context'])
    else:
        authorize(authority[source_field])
    read_ref(authority['budget'], 'retained whole-task budget')
    accepted, _ = _assistant(NativeReader(sessions['codex']), authority['owner_acceptance'])
    require(accepted == dict(schema='upgrade-task-capability-acceptance-v1',
            task_id=base['task_id'], extension_id=request['extension_id'],
            scope_sha256=scope_digest(request),
            **{source_field+'_sha256':object_sha(authority[source_field])},
            budget_sha256=authority['budget']['sha256']), 'CAPABILITY_ORIGINAL_OWNER_ACCEPTANCE_REQUIRED')
    return authority


def _boundary(request, base, snapshot):
    value = json_ref(request['previous_completion'])
    p = value.get('message', {}).get('params', {})
    require(value.get('direction') == 'received' and value.get('peer', {}).get('server') == base['server']
            and value['peer'].get('peer') == {k:base['server'][k] for k in ('pid', 'uid')}
            and value.get('message', {}).get('method') == 'turn/completed'
            and p.get('threadId') == base['thread_id']
            and p.get('turn', {}).get('id') == snapshot['active_turn']
            and p['turn'].get('status') == 'completed', 'CAPABILITY_NORMAL_ORIGINAL_BOUNDARY_REQUIRED')


@contract_errors('capability request')
def inspect_request(request_ref):
    request = json_ref(request_ref)
    require(set(request) == {'schema', 'extension_id', 'base_registration', 'base_ledger', 'runtime',
            'adoption', 'previous_completion', 'authority', 'operations'}
            and request['schema'] == REQUEST_SCHEMA, 'CAPABILITY_REQUEST_SCHEMA_INVALID')
    _name(request['extension_id'])
    base = validate_registration(json_ref(request['base_registration']))
    require(base['schema'] == 'upgrade-task-operations-v3', 'CAPABILITY_VERSIONED_BASE_REQUIRED')
    runtime, spec, sessions, anchor = _runtime_binding(request)
    require(set(sessions) == {'codex', 'claude_a', 'claude_b'}
            and sessions['codex']['session_id'] == base['thread_id']
            and anchor['server'] == base['server'], 'CAPABILITY_ORIGINAL_TASK_CHANGED')
    snapshot = _ledger_snapshot(request['base_ledger'], base)
    _boundary(request, base, snapshot)
    authority = _authority(request, sessions, base)
    ops = request['operations']
    require(isinstance(ops, dict) and 0 < len(ops) <= 64
            and not set(ops).intersection(base['operations']), 'CAPABILITY_SCOPE_OR_BUDGET_INVALID')
    directory = checked_path(Path(request['base_registration']['path']).parent
                             / 'task-capability-extensions' / request['extension_id'])
    registration = dict(schema=SCHEMA, request=request_ref, extension_id=request['extension_id'],
        task_id=base['task_id'], base_registration=request['base_registration'],
        base_ledger=request['base_ledger'], base_snapshot=snapshot, runtime=request['runtime'],
        adoption=request['adoption'], authority=request['authority'], sessions=sessions,
        operations=ops, output_root=str(directory), budget=authority['budget'])
    for name, op in ops.items():
        _name(name)
        require(routed_operation(request['extension_id'], name) not in base['operations'],
                'CAPABILITY_BASE_OPERATION_COLLISION')
        require(isinstance(op, dict) and set(op) == {'action', 'payload'}
                and op['action'] in ACTIONS and isinstance(op['payload'], dict),
                'CAPABILITY_UNSUPPORTED_OPERATION')
        if op['action'].startswith('inquiry-'):
            from upgrade_task_inquiry_capability import validate_operation
        else:
            from upgrade_task_continuity_capability import validate_operation
        validate_operation(dict(action=op['action'], **op['payload']), registration)
    return registration, runtime, spec, anchor


def register_extension(request_ref):
    """One inert registration. No model input and no old-ledger mutation."""
    from upgrade_live_control import external_publisher
    registration, _, spec, _ = inspect_request(request_ref)
    external_publisher(spec)
    directory = Path(registration['output_root'])
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive_lock(directory.parent/'registration.lock'):
        require(not directory.exists(), 'CAPABILITY_REGISTRATION_EXISTS_NO_RESET')
        directory.mkdir(mode=0o700)
        return write_json(directory/'registration.json', registration, exclusive=True)


def load_extension(ref):
    registration = json_ref(ref)
    require(registration.get('schema') == SCHEMA, 'CAPABILITY_REGISTRATION_SCHEMA_INVALID')
    expected, _, _, _ = inspect_request(registration['request'])
    require(registration == expected and checked_path(ref['path']) ==
            Path(expected['output_root'])/'registration.json', 'CAPABILITY_BASE_OR_REGISTRATION_CHANGED')
    return registration


class ExtensionContext:
    """Handler context issued after a real original RPC and durable intent.

    This is an internal controller boundary, not a Python sandbox. Experimental
    clients must remain kernel-denied writes to the controller/ledger tree.
    """
    def __init__(self, key, loader, operation_id, directory, call_ref, message):
        require(key is _CONTEXT_KEY, 'CAPABILITY_LIVE_CONTEXT_REQUIRED')
        self.loader, self.operation_id, self.directory = loader, operation_id, directory
        self.registration, self.registration_ref = loader.registration, loader.ref
        self.sessions, self.task_id = self.registration['sessions'], self.registration['task_id']
        self.call_ref, self.message = call_ref, message
        self.turn_id = message['params']['turnId']
        self._active = True

    def reader(self, lane):
        require(lane in self.sessions, 'CAPABILITY_UNKNOWN_LANE')
        return NativeReader(self.sessions[lane])

    def recheck(self):
        require(self._active, 'CAPABILITY_CONTEXT_ENDED')
        self.loader.recheck(self.turn_id)

    def assert_operation(self, action, payload):
        require_context(self)
        require(self.registration['operations'][self.operation_id] == dict(action=action, payload=payload),
                'CAPABILITY_CONTEXT_OPERATION_CHANGED')

    def native_statement(self, record_ref, expected_schema, lane='codex', *, after=0):
        reader = self.reader(lane)
        until = self.call_ref['offset'] if lane == 'codex' else reader.snapshot()['offset']
        refs = [record_ref] if record_ref is not None else [r for r, _ in reader.records(after, until)]
        found = []
        for ref in refs:
            require(ref['offset'] >= after and ref['offset'] + ref['length'] <= until,
                    'CAPABILITY_STATEMENT_OUTSIDE_WINDOW')
            try:
                value, record = _assistant(reader, ref)
            except ValueError:
                if record_ref is not None: raise
                continue
            if not isinstance(value, dict) or value.get('schema') != expected_schema:
                continue
            require(value.get('task_id') == self.task_id
                    and value.get('extension_id') == self.registration['extension_id']
                    and value.get('operation_id') == self.operation_id, 'CAPABILITY_STATEMENT_SCOPE_CHANGED')
            if lane == 'codex':
                require(record['payload'].get('internal_chat_message_metadata_passthrough', {}).get('turn_id')
                        == self.turn_id, 'CAPABILITY_STATEMENT_FOREIGN_TURN')
            found.append(value)
        require(len(found) == 1, 'CAPABILITY_ONE_NATIVE_STATEMENT_REQUIRED')
        return found[0]

    def completed_operation(self, operation_id):
        """Exact complete native output; stored controller result alone is insufficient."""
        directory = Path(self.registration['output_root'])/'operations'/operation_id
        result_ref = file_ref(directory/'result.json'); value = json_ref(result_ref)
        intent = json_ref(value['intent'])
        require(intent['registration'] == self.registration_ref and intent['operation_id'] == operation_id,
                'CAPABILITY_READ_ORIGINAL_OPERATION_REQUIRED')
        reader = self.reader('codex'); call = reader.record(intent['native_call'])['payload']
        found = []
        start = intent['native_call']['offset'] + intent['native_call']['length']
        for ref, record in reader.records(start, self.call_ref['offset']):
            p = record.get('payload', {})
            if (record.get('type') == 'response_item' and p.get('type') == 'custom_tool_call_output'
                    and p.get('call_id') == call['call_id']):
                require(p.get('internal_chat_message_metadata_passthrough', {}).get('turn_id')
                        == intent['message']['params']['turnId'], 'CAPABILITY_RESULT_TURN_CHANGED')
                output = p.get('output')
                require(isinstance(output, list) and len(output) == 2
                        and all(x.get('type') == 'input_text' for x in output)
                        and re.fullmatch(r'Script completed\nWall time [0-9.]+ seconds\nOutput:\n', output[0]['text'])
                        and strict_json(output[1]['text']) == value['result'],
                        'CAPABILITY_COMPLETE_NATIVE_RESULT_REQUIRED')
                found.append(ref)
        require(len(found) == 1, 'CAPABILITY_ONE_NATIVE_RESULT_REQUIRED')
        return result_ref, value['result'], found[0]


def require_context(context):
    require(type(context) is ExtensionContext and context._active
            and type(context.loader) is ExtensionLoader
            and context.registration is context.loader.registration,
            'CAPABILITY_LIVE_CONTEXT_REQUIRED')
    context.recheck()
    return context


class ExtensionLoader:
    """Fixed dispatch for a future explicitly deployed same-thread tool route."""
    def __init__(self, registration_ref, client):
        self.ref, self.client = registration_ref, client
        self.registration = load_extension(registration_ref)
        self.owner_pid = os.getpid()
        from upgrade_task_streams import execution_owner
        self.owner_execution = execution_owner()
        _, _, _, self.anchor = inspect_request(self.registration['request'])

    def recheck(self, turn):
        from upgrade_rpc_peer import verify_peer
        from upgrade_task_thread import observe
        from upgrade_task_streams import execution_owner
        require(os.getpid() == self.owner_pid and execution_owner() == self.owner_execution,
                'CAPABILITY_OWNER_CHANGED')
        require(load_extension(self.ref) == self.registration, 'CAPABILITY_REGISTRATION_CHANGED')
        base = json_ref(self.registration['base_registration'])
        require(self.client.server == base['server'], 'CAPABILITY_ORIGINAL_SERVER_REQUIRED')
        verify_peer(self.client.sock, base['server'], self.client.process)
        observed = observe(self.client, self.anchor, self.client.process)
        require(observed['thread_id'] == base['thread_id'] and observed['latest_turn_id'] == turn
                and observed.get('latest_turn_status') == 'inProgress', 'CAPABILITY_ACTIVE_ORIGINAL_TURN_REQUIRED')
        verify_peer(self.client.sock, base['server'], self.client.process)

    def dispatch(self, operation_id, *, received_ref, native_call_ref):
        reg = self.registration; _name(operation_id)
        require(operation_id in reg['operations'], 'CAPABILITY_UNREGISTERED_OPERATION')
        received = json_ref(received_ref); msg = received.get('message', {}); p = msg.get('params', {})
        base = json_ref(reg['base_registration'])
        require(self.client.last_received_record == received_ref
                and received.get('direction') == 'received' and received.get('peer', {}).get('server') == base['server']
                and received['peer'].get('peer') == {k:base['server'][k] for k in ('pid', 'uid')}
                and msg.get('method') == 'item/tool/call' and 'id' in msg
                and set(p) == {'threadId','turnId','callId','namespace','tool','arguments'}
                and p['threadId'] == base['thread_id'] and p['namespace'] == NAMESPACE and p['tool'] == 'execute'
                and p['arguments'] == {'operation_id':routed_operation(reg['extension_id'],operation_id)},
                'CAPABILITY_ORIGINAL_RPC_REQUIRED')
        reader = NativeReader(reg['sessions']['codex'])
        call = reader.record(native_call_ref); body = call.get('payload', {})
        require(call.get('type') == 'response_item' and body.get('type') == 'custom_tool_call'
                and body.get('name') == 'exec' and body.get('input') == invocation_script(reg['extension_id'], operation_id)
                and body.get('internal_chat_message_metadata_passthrough', {}).get('turn_id') == p['turnId']
                and isinstance(body.get('call_id'), str) and body['call_id'], 'CAPABILITY_ORIGINAL_NATIVE_CALL_REQUIRED')
        self.recheck(p['turnId'])
        root = Path(reg['output_root']); directory = root/'operations'/operation_id
        with exclusive_lock(root.parent/'execution.lock'):
            self.recheck(p['turnId'])
            directory.parent.mkdir(mode=0o700, exist_ok=True)
            require(not directory.exists(), 'CAPABILITY_OPERATION_CONSUMED_NO_REPLAY')
            directory.mkdir(mode=0o700)
            intent = write_json(directory/'intent.json', dict(schema='upgrade-task-capability-intent-v1',
                registration=self.ref, operation_id=operation_id, message=msg,
                received=received_ref, native_call=native_call_ref, at=utc_now()), exclusive=True)
            # Any later failure leaves the original intent consumed. No cleanup or retry.
            context = ExtensionContext(_CONTEXT_KEY, self, operation_id, directory, native_call_ref, msg)
            op = reg['operations'][operation_id]
            if op['action'].startswith('inquiry-'):
                from upgrade_task_inquiry_capability import dispatch
            else:
                from upgrade_task_continuity_capability import dispatch
            try:
                result = dispatch(op['action'], op['payload'], context)
                self.recheck(p['turnId'])
                require(isinstance(result, dict) and len(canonical(result)) <= 1024*1024,
                        'CAPABILITY_RESULT_BUDGET')
                receipt = write_json(directory/'result.json', dict(intent=intent, result=result,
                    model_consumption_proven=False, business_accepted=False), exclusive=True)
                return dict(success=True, contentItems=[dict(type='inputText', text=json.dumps(result,
                    ensure_ascii=False))]), receipt
            finally:
                context._active = False
