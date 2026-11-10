"""Explicit closed-turn handoff to additive operations on the original tool.

Codex 0.164.0 exposes dynamicTools only on thread/start. This adapter uses the
already installed upgrade_collaboration.execute tool with a registered prefix;
it does not attempt thread/resume tool injection or create a replacement TASK.
The original TaskService's transition-intent path serializes the handoff with
its normal continuation. The old ledger is never enrolled or edited here.
"""
import json
import os
from pathlib import Path
import time

from orchestration_common import (file_ref, json_ref, require, write_json)
from upgrade_task_capability_extension import ExtensionLoader, invocation_script, routed_operation


class CapabilityService:
    @classmethod
    def activate(cls, original_service, registration_ref):
        from upgrade_task_actor import TaskService
        from upgrade_task_enrollment import TaskCampaignGate
        require(type(original_service) is TaskService
                and type(original_service.actor.gate) is TaskCampaignGate,
                'CAPABILITY_ORIGINAL_SERVICE_REQUIRED')
        previous = original_service._normal_terminal()
        actor = original_service.actor
        loader = ExtensionLoader(registration_ref, actor.client)
        reg = loader.registration
        request = json_ref(reg['request'])
        require(request['previous_completion'] == previous
                and actor.ledger.registration == json_ref(reg['base_registration'])
                and actor.ledger.path == Path(reg['base_ledger']['path'])
                and actor.gate.campaign['runtime'] == reg['runtime']
                and actor.gate.campaign['client_isolation'] == reg['adoption'],
                'CAPABILITY_SERVICE_BASE_CHANGED')
        actor.gate._observe(require_normal_idle=True)
        # Same exclusive transition name used by TaskService.start_next_turn /
        # continue_turn. A second owner or lost start cannot get another input.
        intent_path = actor.directory/(actor.ledger.active_turn+'-next-turn-intent.json')
        intent = write_json(intent_path, dict(schema='upgrade-task-capability-handoff-v1',
            registration=registration_ref, previous_completion=previous,
            owner_pid=os.getpid(), max_model_inputs=1, base_ledger_unchanged=True), exclusive=True)
        root = Path(reg['output_root'])
        try:
            catalog = [dict(operation_id=name, action=op['action'], payload=op['payload'],
                           exact_exec_script=invocation_script(reg['extension_id'], name))
                       for name,op in reg['operations'].items()]
            text = ('Continue the same task under its explicitly accepted capability extension. '
                'Use only the exact original-tool calls below; do not invoke old campaign operations '
                'during this auxiliary turn. Every operation has one attempt. A lost result is not '
                'permission to repeat. Read complete inquiry-read results, then emit one assistant '
                'JSON with schema upgrade-task-inquiry-answer-v1, task_id, extension_id, operation_id '
                '(the answer operation), read_operation_id, body_sha256 (from the full read) and reply '
                '(marker, caller_surface_uuid, supervisor_uuid, task_id, episode_id, status, trigger; '
                'WAITING_DEPENDENCY, SOLO or BLOCKED also require owner equal to supervisor_uuid '
                'and finite UTC next_due_epoch/deadline_epoch with 0 < due <= deadline). '
                'Do not claim a task dispatch in an ACK/status reply. For continuity use its exact '
                'returned contract and expected native statement. Never invent receipt pins.\n'
                +json.dumps(dict(registration=registration_ref, task_id=reg['task_id'],
                    extension_id=reg['extension_id'], operations=catalog),ensure_ascii=False))
            # Last unchanged boundary check immediately before the only send.
            actor.gate._observe(require_normal_idle=True)
            require(original_service._normal_terminal() == previous, 'CAPABILITY_BOUNDARY_CHANGED')
            created = actor.client.request('turn/start', dict(threadId=actor.ledger.registration['thread_id'],
                input=[dict(type='text',text=text,text_elements=[])]))
            turn = created.get('turn', {}).get('id')
            require(isinstance(turn,str) and turn, 'CAPABILITY_NEW_TURN_MISSING')
            from upgrade_task_creation import exchange
            sent, response = exchange(actor.client.last_exchange, 'turn/start', actor.client.server)
            require(response == created and sent == dict(threadId=actor.ledger.registration['thread_id'],
                input=[dict(type='text',text=text,text_elements=[])]), 'CAPABILITY_START_EXCHANGE_CHANGED')
            loader.recheck(turn)
            activation = write_json(root/'activation.json', dict(registration=registration_ref,
                handoff_intent=intent, turn_id=turn, turn_start=actor.client.last_exchange,
                original_controller_ledger_unchanged=True, max_model_inputs=1), exclusive=True)
            return cls(loader, turn, activation)
        except BaseException as exc:
            write_json(root/'activation-unknown.json', dict(intent=intent, error_type=type(exc).__name__,
                retry_allowed=False, same_task_preserved=True), exclusive=True)
            raise

    def __init__(self, loader, turn_id, activation_ref):
        require(type(loader) is ExtensionLoader, 'CAPABILITY_LOADER_REQUIRED')
        activation = json_ref(activation_ref)
        require(activation['registration'] == loader.ref and activation['turn_id'] == turn_id,
                'CAPABILITY_ACTIVATION_CHANGED')
        require(Path(activation_ref['path']) == Path(loader.registration['output_root'])/'activation.json',
                'CAPABILITY_ORIGINAL_ACTIVATION_PATH_REQUIRED')
        from upgrade_task_creation import exchange
        sent, response = exchange(activation['turn_start'], 'turn/start', loader.client.server)
        base = json_ref(loader.registration['base_registration'])
        require(sent.get('threadId') == base['thread_id'] and response.get('turn', {}).get('id') == turn_id
                and json_ref(activation['handoff_intent'])['registration'] == loader.ref,
                'CAPABILITY_ORIGINAL_ACTIVATION_EXCHANGE_REQUIRED')
        root=Path(loader.registration['output_root'])
        require(not any((root/name).exists() for name in (
            'activation-unknown.json','service-unknown.json','normal-completion.json','service-owner.json'))
            and not (root/'operations').exists() and not (root/'call-intents').exists(),
            'CAPABILITY_SERVICE_ALREADY_STARTED_NO_RECONSTRUCTION')
        # The in-memory pending map is not a recovery journal. One durable
        # ownership claim per activation forbids a fresh object from losing
        # pending/UNKNOWN state or pretending an earlier completion is new.
        owner_ref=write_json(root/'service-owner.json',dict(activation=activation_ref,
            registration=loader.ref,turn_id=turn_id,owner_pid=os.getpid(),
            reconstruction_allowed=False),exclusive=True)
        self.loader, self.turn, self.activation = loader, turn_id, activation_ref
        self.client, self.root, self.owner_ref = loader.client, root, owner_ref
        self.owner_pid, self.closed, self.pending = os.getpid(), False, {}

    def _native_call(self, operation_id):
        from orchestration_native import NativeReader
        reader = NativeReader(self.loader.registration['sessions']['codex'])
        # The transcript is bounded by the actual turn's current records. The
        # exact script+turn+unreturned call must be unique, not a substring.
        end = reader.snapshot()['offset']
        # Start at the fixed original assistant acceptance boundary, not a
        # guessed/latest rollout offset or an arbitrary byte near EOF.
        acceptance=json_ref(self.loader.registration['authority'])['owner_acceptance']
        after=acceptance['offset']+acceptance['length']
        require(end-after <= 32*1024*1024, 'CAPABILITY_NATIVE_WINDOW_TOO_LARGE')
        found, returned = [], set()
        for ref, record in reader.records(after,end):
            p = record.get('payload',{})
            if p.get('type') == 'custom_tool_call_output': returned.add(p.get('call_id'))
            if (record.get('type') == 'response_item' and p.get('type') == 'custom_tool_call'
                    and p.get('name') == 'exec' and p.get('input') == invocation_script(
                        self.loader.registration['extension_id'],operation_id)
                    and p.get('internal_chat_message_metadata_passthrough',{}).get('turn_id') == self.turn):
                found.append((ref,p.get('call_id')))
        found = [ref for ref, call in found if call not in returned]
        require(len(found)==1, 'CAPABILITY_ONE_PENDING_NATIVE_CALL_REQUIRED')
        return found[0]

    def process_recorded(self, message, received_ref):
        require(not self.closed and os.getpid()==self.owner_pid, 'CAPABILITY_SERVICE_CLOSED_OR_OWNER_CHANGED')
        from upgrade_rpc_peer import verify_peer
        verify_peer(self.client.sock,self.client.server,self.client.process)
        record=json_ref(received_ref)
        require(record.get('message') == message and self.client.last_received_record==received_ref,
                'CAPABILITY_ORIGINAL_RECEIVED_RECORD_REQUIRED')
        reg=self.loader.registration; base=json_ref(reg['base_registration']);p=message.get('params',{})
        require(record.get('direction')=='received' and record.get('peer',{}).get('server')==base['server']
                and record['peer'].get('peer')=={k:base['server'][k] for k in ('pid','uid')},
                'CAPABILITY_FOREIGN_SERVER')
        try:
            method=message.get('method')
            if method not in ('item/tool/call','item/completed','turn/completed'):
                require('id' not in message,'CAPABILITY_UNSUPPORTED_SERVER_REQUEST')
                return dict(phase='OBSERVED')
            if method=='item/completed' and p.get('item',{}).get('type')!='dynamicToolCall':
                # Reasoning / agentMessage / other ordinary completions do not
                # consume a dynamic operation or close the original actor.
                return dict(phase='OBSERVED')
            require(p.get('threadId')==base['thread_id']
                    and p.get('turnId',p.get('turn',{}).get('id'))==self.turn,
                    'CAPABILITY_FOREIGN_THREAD_OR_TURN')
            if message.get('method')=='item/tool/call':
                require(isinstance(p.get('callId'),str) and p['callId']
                        and p['callId'] not in self.pending,'CAPABILITY_DUPLICATE_CALL_ID')
                call_intents=self.root/'call-intents';call_intents.mkdir(mode=0o700,exist_ok=True)
                from orchestration_common import object_sha
                call_path=call_intents/(object_sha(p['callId'])+'.json')
                require(not call_path.exists(),'CAPABILITY_DUPLICATE_CALL_ID')
                prefix='extension:'+reg['extension_id']+':'
                name=p.get('arguments',{}).get('operation_id','')
                require(name.startswith(prefix),'CAPABILITY_FOREIGN_OR_BASE_OPERATION')
                operation=name[len(prefix):]
                write_json(call_path,dict(received=received_ref,call_id=p['callId']),exclusive=True)
                result,receipt=self.loader.dispatch(operation,received_ref=received_ref,
                    native_call_ref=self._native_call(operation))
                directory=self.root/'operations'/operation
                response=dict(id=message['id'],result=result)
                response_intent=write_json(directory/'response-intent.json',dict(response=response,
                    result=receipt, activation=self.activation),exclusive=True)
                self.loader.recheck(self.turn)
                # Intent is durable before send. Failure closes this service;
                # neither response nor operation can be sent again.
                self.client.respond(response)
                self.pending[p['callId']]=dict(operation_id=operation,request=message,
                    result=result,response_intent=response_intent)
                return dict(phase='RESPONSE_INTENDED',operation_id=operation,model_consumption_proven=False)
            if message.get('method')=='item/completed':
                item=p.get('item',{}); row=self.pending.get(item.get('id'))
                require(row is not None,'CAPABILITY_UNKNOWN_COMPLETION')
                expected=dict(type='dynamicToolCall',id=item['id'],namespace='upgrade_collaboration',tool='execute',
                    arguments=row['request']['params']['arguments'],status='completed',**row['result'])
                require(item==expected,'CAPABILITY_COMPLETION_CONFLICT')
                write_json(self.root/'operations'/row['operation_id']/'native-completion.json',
                    dict(received=received_ref,response_intent=row['response_intent'],
                        model_consumption_proven=False,business_accepted=False),exclusive=True)
                del self.pending[item['id']]
                return dict(phase='NATIVE_COMPLETED',model_consumption_proven=False)
            if message.get('method')=='turn/completed':
                require(p['turn']['status']=='completed' and not self.pending,'CAPABILITY_TURN_INCOMPLETE_UNKNOWN')
                write_json(self.root/'normal-completion.json',dict(received=received_ref,
                    activation=self.activation,old_attempts_reset=False),exclusive=True)
                self.closed=True
                return dict(phase='CLOSED',old_attempts_reset=False)
            require('id' not in message,'CAPABILITY_UNSUPPORTED_SERVER_REQUEST')
            return dict(phase='OBSERVED')
        except BaseException as exc:
            self.closed=True
            path=self.root/'service-unknown.json'
            if not path.exists():write_json(path,dict(received=received_ref,error_type=type(exc).__name__,
                retry_allowed=False,pending=list(self.pending)),exclusive=True)
            raise

    def pulse(self, *, seconds=1):
        require(type(seconds) in (int,float) and 0<seconds<=30,'CAPABILITY_PULSE_BUDGET')
        deadline=time.monotonic()+seconds; count=0
        while not self.closed and time.monotonic()<deadline:
            record=self.client.poll_recorded(timeout=min(.25,max(0,deadline-time.monotonic())))
            if record is not None:
                self.process_recorded(*record);count+=1
        return dict(status='CLOSED' if self.closed else 'ACTIVE',messages=count,
            pending=list(self.pending),old_attempts_reset=False)
