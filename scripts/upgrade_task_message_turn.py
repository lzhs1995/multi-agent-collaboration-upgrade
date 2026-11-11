"""Registered Claude input to the same retained task, idle or busy.

This accepts neither arbitrary message text nor a completion pack. The source
slot, live original sender, exact helper journal and original run must agree.
"""
import copy
import os
from pathlib import Path

from orchestration_common import file_ref, json_ref, require, write_json
from upgrade_task_callback_turn import CallbackInput, caller_ancestry, idle_intent, _incoming
from upgrade_task_message_source import validate_source, helper_request, boundary


class MessageInput(CallbackInput):
    REQUEST_KEYS={'source','attempt'}
    PLAN_SCHEMA='upgrade-task-message-input-plan-v1'
    SOURCE_SCHEMA='upgrade-task-message-input-source-v1'
    SOURCE_FIELD='message'
    LABEL='message'

    def observe(self):
        from upgrade_task_callback_ingress import active_context
        from upgrade_task_thread import observe
        from orchestration_contracts import state_root
        from upgrade_communication_observer import original_journal, original_dispatch_binding, expected_intent
        from cmux_prompt_reference import validate_wire_body
        service, gate = self.service, self.gate
        runtime = getattr(service, 'runtime_owner', None)
        require(os.getpid() == self.owner_pid == service.owner_pid == gate.owner_pid
                and service.actor.gate is gate and service.actor.client is gate.client
                and service.actor.ledger is gate.ledger and gate.ledger.active_turn == self.turn
                and runtime is not None and runtime.callback_ingress.messages_enabled
                and active_context(service, self.connection, self.request),
                'TASK_MESSAGE_ORIGINAL_LIVE_OWNER_REQUIRED')
        require((not service.actor.closed and service.terminal is None and service.normal_completion is None)
                if self.busy else service._normal_terminal() == self.previous,
                'TASK_MESSAGE_ORIGINAL_TURN_CHANGED')
        gate._observe(require_normal_idle=not self.busy)
        if self.busy:
            live = observe(gate.client, gate.anchor, gate.client.process)
            require(live['latest_turn_id'] == self.turn and live['thread_status'] == 'active'
                    and live['latest_turn_status'] == 'inProgress'
                    and live['observed_input_method'] == 'turn/steer', 'TASK_MESSAGE_ACTIVE_TURN_CHANGED')
        value, campaign, sample = validate_source(self.request['source'])
        require(value['campaign'] == gate.runner.ref and campaign == gate.campaign,
                'TASK_MESSAGE_ORIGINAL_CAMPAIGN_CHANGED')
        sender, receiver = (campaign['sessions'][sample[k]] for k in ('sender', 'receiver'))
        caller = caller_ancestry(self.connection, sender, gate.client.process)
        boundary(campaign, sample, value['kind'])
        spec = helper_request(campaign, sample)
        root = state_root(campaign)
        _, marker, channel = expected_intent(sample, spec['identity'])
        intent_ref = file_ref(root/'cmux-agent-adapter-v1/targets'/channel/'intents'/(marker+'.json'))
        require(json_ref(intent_ref) == spec, 'TASK_MESSAGE_ORIGINAL_HELPER_INTENT_CHANGED')
        validate_wire_body(spec['payload'])
        journal = original_journal(root, spec['identity'], marker)
        attempts = sorted(journal.glob('attempt-*.json'))
        require(len(attempts) == 1 and file_ref(attempts[0]) == self.request['attempt'],
                'TASK_MESSAGE_ONE_ORIGINAL_ATTEMPT_REQUIRED')
        attempt = json_ref(self.request['attempt']); binding = attempt['native_binding']
        original_dispatch_binding(attempt, journal, spec['identity'], marker, spec['payload'])
        require(binding['schema'] == 'native-delivery-task-v1', 'TASK_MESSAGE_EXPLICIT_TARGET_REQUIRED')
        request = idle_intent(attempt, binding, spec['payload'], receiver, sender,
            self.turn, gate.anchor['socket'], busy=self.busy)
        route = request['task_message_route']
        require(route['source'] == self.request['source'] and route['attempt'] == self.request['attempt']['path'],
                'TASK_MESSAGE_ORIGINAL_ROUTE_CHANGED')
        return dict(source=self.request['source'], kind=value['kind'], sample=sample,
            attempt=self.request['attempt'], helper_intent=intent_ref, caller=caller,
            run_id=sample['run_id'], lane=sample['sender'], text=spec['payload'], request=request)


def validate_plan(service, plan_ref, previous):
    incoming = _incoming.get()
    require(type(incoming) is MessageInput and incoming.service is service
            and incoming.plan == plan_ref and incoming.previous == previous,
            'TASK_MESSAGE_LIVE_INPUT_CONTEXT_REQUIRED')
    require(json_ref(plan_ref) == incoming.value(), 'TASK_MESSAGE_INPUT_PLAN_CHANGED')


def input_parameters(service, plan_ref):
    incoming = _incoming.get()
    require(type(incoming) is MessageInput and incoming.service is service and incoming.plan == plan_ref,
            'TASK_MESSAGE_LIVE_INPUT_CONTEXT_REQUIRED')
    require(incoming.observe() == incoming.initial, 'TASK_MESSAGE_INPUT_CHANGED')
    return copy.deepcopy(incoming.initial['request']['params'])


def deliver(service, connection, request, *, busy=False):
    return deliver_prepared(MessageInput(service,connection,request,busy=busy))


def deliver_prepared(incoming):
    require(type(incoming) is MessageInput,'TASK_MESSAGE_ORIGINAL_PREPARED_INPUT_REQUIRED')
    service=incoming.service
    if incoming.busy:
        from upgrade_task_callback_busy import deliver_input_busy
        return deliver_input_busy(service,incoming.connection,incoming.request,message=True,prepared=incoming)
    plan = incoming.publish(); previous = service.actor.ledger.active_turn
    with incoming.activate():
        result = service.start_next_turn(plan)
    evidence = write_json(service.actor.directory/(previous+'-message-input-result.json'),
        dict(schema='upgrade-task-message-input-result-v1', plan=plan, source=incoming.origin,
            transition=file_ref(service.actor.directory/(previous+'-next-turn-result.json')),
            result=result, native_user_receipt=False, model_consumption=False), exclusive=True)
    return dict(result, message_input=evidence)
