"""One idle callback turn on the original retained campaign connection.

An ingress owner must pass its actual accepted Unix socket. File references and
declared lane names cannot substitute for the peer's original Claude ancestry.
This module does not open a listener or enable generated sender routing. That
separate adapter must preserve the sender's existing locks and consumed intent.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import hashlib
import json
import os
from pathlib import Path

from orchestration_common import file_ref, finite, json_ref, read_ref, require, write_bytes_once, write_json
from upgrade_rpc_peer import kernel_peer

SCHEMA = 'upgrade-task-callback-input-plan-v1'
_incoming = ContextVar('original_task_callback_input', default=None)


def idle_intent(attempt, binding, text, receiver, sender, previous_turn, socket_pin, *, busy=False):
    """Read a proposed idle intent; this grants no generated-sender capability.

    The existing generated sender continues to refuse idle input. This reader
    belongs only to the original retained service, after live ingress checks.
    Its params are subsequently sent unchanged through that service's RPC.
    """
    from orchestration_native import NativeReader, check_native_binding
    original_offset = check_native_binding(binding, receiver, sender, text, uuid_semantics=True)
    require(attempt.get('native_binding') == binding
            and attempt.get('binding', {}).get('identity') == binding['identity'],
            'TASK_CALLBACK_ORIGINAL_IDENTITY_CHANGED')
    events = attempt.get('events')
    require(isinstance(events, list) and events and all(isinstance(e, dict) for e in events),
            'TASK_CALLBACK_ORIGINAL_EVENTS_REQUIRED')
    inputs = [e for e in events if e.get('phase') in ('NATIVE_INPUT_INTENT', 'PASTE_INTENT')]
    require(len(events) == len(inputs) == 1 and events[-1] == inputs[0]
            and inputs[0]['phase'] == attempt.get('phase') == 'NATIVE_INPUT_INTENT'
            and not any(e.get('phase') in ('PASTED','ENTER_INTENT','ENTER_SENT',
                'QUEUE_TAB_INTENT','KEY_SENT','NATIVE_INPUT_SENT','NATIVE_INPUT_UNKNOWN') for e in events),
            'TASK_CALLBACK_FRESH_CONSUMED_INTENT_REQUIRED')
    event = inputs[0]; request = event.get('native_request', {})
    fields={'transport','method','params','socket','observed_status',
            'observed_turn_id','observed_at_epoch','native_metadata'}
    if 'task_callback_route' in request:
        from upgrade_task_callback_route import proposed
        proposed(binding,request)
        fields.add('task_callback_route')
    if 'task_message_route' in request:
        from upgrade_task_message_route import proposed
        proposed(binding,request)
        fields.add('task_message_route')
    method,status,turn_status=('turn/steer','active','inProgress') if busy else ('turn/start','idle','completed')
    params=dict(threadId=receiver['session_id'],input=[dict(type='text',text=text)])
    if busy:params['expectedTurnId']=previous_turn
    require(set(request) == fields
            and request['transport'] == 'codex-native-input-v1'
            and request['method'] == method and request['observed_status'] == status
            and request['observed_turn_id'] == previous_turn and request['socket'] == socket_pin
            and request['params'] == params,
            'TASK_CALLBACK_ORIGINAL_BUSY_INPUT_CHANGED' if busy else 'TASK_CALLBACK_ORIGINAL_IDLE_INPUT_CHANGED')
    metadata=request['native_metadata']
    require(isinstance(metadata,dict) and all(metadata.get(k) == v for k,v in dict(
                thread_id=receiver['session_id'],thread_status=status,can_accept_direct_input=True,
                latest_turn_id=previous_turn,latest_turn_status=turn_status,
                latest_turn_interrupted=False,latest_turn_error=None,provider_error_source=None,
                metadata_rpc_status='AVAILABLE',available_input_method=method).items()),
            'TASK_CALLBACK_ORIGINAL_IDLE_METADATA_CHANGED')
    fence = event.get('native_input_fence', {})
    require(fence.get('schema') == 'native-input-fence-v1'
            and fence.get('binding_sha256') == hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
            and fence.get('native_request_sha256') == hashlib.sha256(json.dumps(request, sort_keys=True,
                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'TASK_CALLBACK_ORIGINAL_INPUT_FENCE_CHANGED')
    bound = finite(binding['bound_at_epoch'], 'callback bound time')
    observed = finite(request['observed_at_epoch'], 'callback observation time')
    captured = finite(fence.get('captured_at_epoch'), 'callback fence time')
    times = [finite(e.get('at_epoch'), 'callback event time') for e in events]
    require(bound <= finite(metadata.get('observed_at_epoch'),'callback metadata time')
            <= observed <= captured <= times[-1]
            and times == sorted(times) and all(t >= bound for t in times),
            'TASK_CALLBACK_ORIGINAL_INPUT_CHRONOLOGY_CHANGED')
    require(NativeReader(receiver).verify_pin(fence.get('transcript')) >= original_offset,
            'TASK_CALLBACK_ORIGINAL_INPUT_FENCE_CHANGED')
    return request


def caller_ancestry(connection, sender, process):
    """Observe the actual connected process twice; never trust an env selector."""
    peer = kernel_peer(connection)
    expected = sender['process']
    require(peer['pid'] != expected['pid'], 'TASK_CALLBACK_TOOL_DESCENDANT_REQUIRED')
    rows, seen, pid = [], set(), peer['pid']
    for _ in range(32):
        require(pid > 1 and pid not in seen, 'TASK_CALLBACK_CALLER_ANCESTRY_INVALID')
        seen.add(pid)
        row = process(pid)
        require(row['pid'] == pid, 'TASK_CALLBACK_CALLER_PROCESS_CHANGED')
        rows.append(row)
        if pid == expected['pid']:
            require(all(row.get(k) == v for k, v in expected.items()),
                    'TASK_CALLBACK_ORIGINAL_CLAUDE_CHANGED')
            break
        pid = row['ppid']
    else:
        require(False, 'TASK_CALLBACK_CALLER_ANCESTRY_LIMIT')
    require(rows[-1]['pid'] == expected['pid'], 'TASK_CALLBACK_FOREIGN_CALLER')
    require(kernel_peer(connection) == peer
            and all(process(row['pid']) == row for row in rows),
            'TASK_CALLBACK_CALLER_CHANGED_DURING_READ')
    # Environment contents may contain credentials; never persist them.
    fields = ('pid', 'ppid', 'birth', 'executable', 'file_identity')
    return dict(peer=peer, ancestry=[{k:r[k] for k in fields} for r in rows])


class CallbackInput:
    """In-memory request bound to one original service and live sender socket."""
    REQUEST_KEYS={'pack','attempt'}
    PLAN_SCHEMA=SCHEMA
    SOURCE_SCHEMA='upgrade-task-callback-input-source-v1'
    SOURCE_FIELD='callback'
    LABEL='callback'
    def __init__(self, service, connection, request, *, busy=False):
        from upgrade_task_actor import TaskService
        from upgrade_task_enrollment import TaskCampaignGate
        require(type(service) is TaskService and type(service.actor.gate) is TaskCampaignGate,
                'TASK_CALLBACK_ORIGINAL_SERVICE_REQUIRED')
        require(isinstance(request, dict) and set(request) == self.REQUEST_KEYS,
                'TASK_CALLBACK_INPUT_REQUEST_INVALID')
        self.service, self.connection, self.request = service, connection, copy.deepcopy(request)
        self.gate = service.actor.gate
        self.busy=busy
        self.previous = None if busy else service._normal_terminal()
        self.turn=service.actor.ledger.active_turn
        self.owner_pid = os.getpid()
        self.initial = self.observe()
        self.plan = None

    def observe(self):
        from orchestration_binding import claim_lane
        from orchestration_campaign import controller_modules
        from orchestration_coordinator import State
        from orchestration_native import original_attempts, check_native_binding
        service, gate = self.service, self.gate
        require(os.getpid() == self.owner_pid == service.owner_pid == gate.owner_pid
                and service.actor.gate is gate and service.actor.client is gate.client
                and service.actor.ledger is gate.ledger and gate.ledger.active_turn==self.turn
                and ((not service.actor.closed and service.terminal is None and service.normal_completion is None)
                     if self.busy else service._normal_terminal() == self.previous),
                'TASK_CALLBACK_ORIGINAL_OWNER_CHANGED')
        runtime = getattr(service, 'runtime_owner', None)
        from upgrade_task_callback_ingress import active_context
        require(runtime is None or not runtime.closed and (not runtime.running
                or active_context(service,self.connection,self.request)),
                'TASK_CALLBACK_RUNTIME_BUSY_OR_CLOSED')
        gate._observe(require_normal_idle=not self.busy)
        if self.busy:
            from upgrade_task_thread import observe
            live=observe(gate.client,gate.anchor,gate.client.process)
            require(live['latest_turn_id']==self.turn and live['thread_status']=='active'
                    and live['latest_turn_status']=='inProgress'
                    and live['observed_input_method']=='turn/steer',
                    'TASK_CALLBACK_ORIGINAL_ACTIVE_TURN_REQUIRED')
        campaign = gate.campaign
        pack = json_ref(self.request['pack'])
        # Resolve only an originally claimed, dispatched node in this campaign.
        found = []
        for run in campaign['runs']:
            run_root = Path(campaign['root']) / 'runs' / run['run_id']
            if not (run_root/'run.json').exists():
                continue
            co = gate.runner.co(run['run_id']); states, data = co.state()
            for key, info in data.items():
                if (states[key] == State.DISPATCHED and info and info.get('mode') == 'delegated_cli' and 'delivery' in info
                        and info.get('prepared', {}).get('pack') == self.request['pack']):
                    co._work_budget()
                    lane = claim_lane(co.nodes[key], info)
                    found.append((run['run_id'], key, lane, info))
        require(len(found) == 1, 'TASK_CALLBACK_ORIGINAL_DISPATCH_REQUIRED')
        run, node, lane, info = found[0]
        sender, receiver = campaign['sessions'][lane], campaign['sessions']['codex']
        require(sender['provider'] == 'claude' and pack['task_id'] == info['task_id']
                and sender['surface_uuid'].upper() == pack['executor_uuid'].upper(),
                'TASK_CALLBACK_ORIGINAL_SENDER_CHANGED')
        caller = caller_ancestry(self.connection, sender, gate.client.process)
        receipt = Path(pack['completion_receipt'])
        journal = receipt.with_name(receipt.stem+'-attempts')
        require(not receipt.exists() and not Path(str(receipt)+'.pending.json').exists(),
                'TASK_CALLBACK_ALREADY_RECEIVED_OR_LEGACY_PENDING')
        attempts = sorted(journal.glob('attempt-*.json'))
        require(attempts and str(attempts[-1]) == self.request['attempt']['path']
                and file_ref(attempts[-1]) == self.request['attempt'],
                'TASK_CALLBACK_ORIGINAL_INPUT_ATTEMPT_CHANGED')
        history = original_attempts([file_ref(p) for p in attempts])
        attempt = history[-1]
        require(all(old.get('phase') == 'NO_INPUT' and old.get('events') == []
                    and old.get('binding') == attempt.get('binding') for old in history[:-1]),
                'TASK_CALLBACK_UNCERTAIN_INPUT_WAS_RETRIED')
        for old in history[:-1]:
            check_native_binding(old.get('native_binding', {}),receiver,sender,
                                 pack['completion_callback'],uuid_semantics=True)
        bound = attempt['native_binding']
        require(bound.get('schema') == 'native-delivery-task-v1', 'TASK_CALLBACK_EXPLICIT_TARGET_REQUIRED')
        bridge, _, _, _ = controller_modules(campaign)
        require(bridge.validate_task_pack_contract(Path(self.request['pack']['path'])) == pack,
                'TASK_CALLBACK_ORIGINAL_PACK_CHANGED')
        request = idle_intent(attempt, bound, pack['completion_callback'], receiver, sender,
                              gate.ledger.active_turn, gate.anchor['socket'],
                              **({'busy':True} if self.busy else {}))
        if 'task_callback_route' in request:
            route=request['task_callback_route']
            require(route['pack']==self.request['pack'] and route['attempt']==self.request['attempt']['path'],
                    'TASK_CALLBACK_ORIGINAL_INGRESS_ROUTE_CHANGED')
        original = attempt['binding']
        report = file_ref(pack['report'])
        require(original['task_pack_sha256'] == self.request['pack']['sha256']
                and original['report_sha256'] == report['sha256'] and original['report_bytes'] == report['bytes']
                and original['completion_nonce'] == pack['completion_nonce']
                and original['completion_callback'] == pack['completion_callback'],
                'TASK_CALLBACK_ORIGINAL_REPORT_CHANGED')
        return dict(pack=self.request['pack'], attempt=self.request['attempt'], report=report,
                    caller=caller, run_id=run, node_id=node, lane=lane,
                    text=pack['completion_callback'], request=request)

    def value(self):
        require(not self.busy,'TASK_CALLBACK_BUSY_CANNOT_ENROLL_NEW_TURN')
        from upgrade_task_instructions import remaining_operations
        require(self.observe() == self.initial, 'TASK_CALLBACK_INPUT_CHANGED')
        require(json_ref(self.origin) == dict(schema=self.SOURCE_SCHEMA,
                    enrollment=self.gate.proof, original=self.initial,
                    original_attempt_snapshot=self.snapshot,
                    native_user_receipt=False, model_consumption=False)
                and read_ref(self.snapshot) == read_ref(self.request['attempt']),
                'TASK_CALLBACK_ORIGINAL_SOURCE_CHANGED')
        ledger = self.gate.ledger
        with ledger._transaction():
            used = [r[0] for r in ledger.db.execute('SELECT operation_id FROM calls')]
            ids = remaining_operations(ledger.registration, self.service.actor.scope, used)
        return dict(schema=self.PLAN_SCHEMA, campaign=self.gate.runner.ref,
            registration_sha256=ledger.registration_sha256, authority_ref=ledger.registration['authority_ref'],
            previous_turn_id=ledger.active_turn, previous_completion=self.previous,
            operation_ids=ids, text=self.initial['text'], **{self.SOURCE_FIELD:self.origin})

    def publish(self):
        require(not self.busy,'TASK_CALLBACK_BUSY_CANNOT_ENROLL_NEW_TURN')
        require(self.plan is None, 'TASK_CALLBACK_PLAN_ALREADY_PUBLISHED')
        ledger = self.gate.ledger; directory = self.service.actor.directory
        # Same transition budget as the ordinary next turn; no replacement ID.
        require(not (directory/(ledger.active_turn+'-next-turn-intent.json')).exists(),
                'TASK_CALLBACK_TRANSITION_ALREADY_ATTEMPTED')
        raw = read_ref(self.request['attempt'])
        self.snapshot = write_bytes_once(directory/(ledger.active_turn+'-'+self.LABEL+'-attempt.json'), raw)
        self.origin = write_json(directory/(ledger.active_turn+'-'+self.LABEL+'-source.json'),
            dict(schema=self.SOURCE_SCHEMA, enrollment=self.gate.proof,
                original=self.initial, original_attempt_snapshot=self.snapshot,
                native_user_receipt=False, model_consumption=False), exclusive=True)
        self.plan = write_json(directory/(ledger.active_turn+'-'+self.LABEL+'-plan.json'), self.value(), exclusive=True)
        return self.plan

    @contextmanager
    def activate(self):
        require(_incoming.get() is None and self.plan is not None, 'TASK_CALLBACK_INPUT_CONTEXT_UNAVAILABLE')
        token = _incoming.set(self)
        try:
            yield
        finally:
            _incoming.reset(token)


def validate_plan(service, plan_ref, previous):
    incoming = _incoming.get()
    require(type(incoming) is CallbackInput and incoming.service is service
            and incoming.plan == plan_ref and incoming.previous == previous,
            'TASK_CALLBACK_LIVE_INPUT_CONTEXT_REQUIRED')
    require(json_ref(plan_ref) == incoming.value(), 'TASK_CALLBACK_INPUT_PLAN_CHANGED')


def input_parameters(service, plan_ref):
    incoming = _incoming.get()
    require(type(incoming) is CallbackInput and incoming.service is service
            and incoming.plan == plan_ref, 'TASK_CALLBACK_LIVE_INPUT_CONTEXT_REQUIRED')
    require(incoming.observe() == incoming.initial, 'TASK_CALLBACK_INPUT_CHANGED')
    return copy.deepcopy(incoming.initial['request']['params'])


def deliver(service, connection, request):
    """Original ingress calls once; a reply timeout never permits resubmission."""
    incoming = CallbackInput(service, connection, request)
    plan = incoming.publish()
    previous_turn = service.actor.ledger.active_turn
    with incoming.activate():
        result = service.start_next_turn(plan)
    evidence = write_json(service.actor.directory/(previous_turn+'-callback-input-result.json'),
        dict(schema='upgrade-task-callback-input-result-v1', plan=plan, source=incoming.origin,
            transition=file_ref(service.actor.directory/(previous_turn+'-next-turn-result.json')),
            result=result, native_user_receipt=False, model_consumption=False), exclusive=True)
    return dict(result, callback_input=evidence)
