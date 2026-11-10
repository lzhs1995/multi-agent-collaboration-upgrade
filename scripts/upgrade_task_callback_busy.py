"""One busy callback at a safe boundary on the retained input owner's thread.

The existing task turn and operation scope are retained. No extra reader thread
writes RPC, and a timeout or a turn race consumes the original attempt without
falling back to start or granting additional operations.
"""
import os
from pathlib import Path

from orchestration_common import (file_ref,json_ref,object_sha,read_ref,require,
    strict_json,write_bytes_once,write_json)
from upgrade_task_callback_turn import CallbackInput


def ledger_turn(ledger):
    with ledger._transaction():
        row=ledger.db.execute('SELECT sequence,scope,grant FROM turns WHERE turn_id=?',
                              (ledger.active_turn,)).fetchone()
        require(row is not None,'TASK_BUSY_CALLBACK_TURN_NOT_ENROLLED')
        return dict(turn_id=ledger.active_turn,sequence=row[0],scope=strict_json(row[1]),
                    grant=None if row[2] is None else strict_json(row[2]),
                    registration_sha256=ledger.registration_sha256)


def deliver_busy(service,connection,request):
    return deliver_input_busy(service,connection,request)


def deliver_input_busy(service,connection,request,*,message=False,prepared=None):
    from upgrade_task_enrollment import _active
    require(_active.get() is None,'TASK_BUSY_CALLBACK_DURING_OPERATION')
    runtime=getattr(service,'runtime_owner',None)
    from upgrade_task_callback_ingress import CallbackIngress,active_context
    require(runtime is not None and type(runtime.callback_ingress) is CallbackIngress
            and runtime.callback_ingress.busy_enabled
            and active_context(service,connection,request), 'TASK_BUSY_CALLBACK_ORIGINAL_INGRESS_REQUIRED')
    require(type(message) is bool,'TASK_INPUT_KIND_REQUIRED')
    if message:
        require(runtime.callback_ingress.messages_enabled,'TASK_MESSAGE_INGRESS_NOT_ENABLED')
        from upgrade_task_message_turn import MessageInput
        incoming=prepared if prepared is not None else MessageInput(service,connection,request,busy=True)
        require(type(incoming) is MessageInput and incoming.service is service
                and incoming.connection is connection and incoming.request==request and incoming.busy,
                'TASK_MESSAGE_ORIGINAL_PREPARED_INPUT_REQUIRED')
        require(incoming.observe()==incoming.initial,'TASK_MESSAGE_INPUT_CHANGED')
    else:
        require(prepared is None,'TASK_CALLBACK_NO_FOREIGN_PREPARED_INPUT')
        incoming=CallbackInput(service,connection,request,busy=True)
    label='message' if message else 'callback'
    initial=incoming.initial;actor=service.actor;ledger=actor.ledger;client=actor.client
    before=ledger_turn(ledger)
    require(set(before['scope'])==set(actor.scope),'TASK_BUSY_CALLBACK_SCOPE_CHANGED')
    source_key='source' if message else 'pack'
    key=object_sha({source_key:initial[source_key],'attempt':initial['attempt']['path']})
    directory=actor.directory/(label+'-steers')/key
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    snapshot=write_bytes_once(directory/'attempt.json',read_ref(initial['attempt']))
    source=write_json(directory/'source.json',dict(schema='upgrade-task-busy-'+label+'-source-v1',
        enrollment=incoming.gate.proof,original=initial,original_attempt_snapshot=snapshot,
        ledger_turn=before,owner_pid=service.owner_pid,input_operations=0,
        native_user_receipt=False,model_consumption=False),exclusive=True)
    require(incoming.observe()==initial and ledger_turn(ledger)==before,
            'TASK_BUSY_CALLBACK_CHANGED_BEFORE_INTENT')
    intent=write_json(directory/'input-intent.json',dict(source=source,
        method='turn/steer',params=initial['request']['params'],maximum_inputs=1,
        retry_allowed=False),exclusive=True)
    try:
        require(incoming.observe()==initial and ledger_turn(ledger)==before,
                'TASK_BUSY_CALLBACK_CHANGED_AFTER_INTENT')
        result=client.request('turn/steer',initial['request']['params'])
        from upgrade_task_creation import exchange
        sent,received=exchange(client.last_exchange,'turn/steer',incoming.gate.registration['server'])
        require(sent==initial['request']['params'] and received==result
                and result.get('turnId')==before['turn_id'] and ledger_turn(ledger)==before,
                'TASK_BUSY_CALLBACK_ORIGINAL_RESPONSE_CHANGED')
        outcome=dict(status='ACTIVE',turn_id=before['turn_id'],model_inputs_sent=1,
                     operation_budget_reset=False,operation_scope_changed=False)
        evidence=write_json(directory/'result.json',dict(schema='upgrade-task-busy-'+label+'-result-v1',
            source=source,intent=intent,exchange=client.last_exchange,result=outcome,
            ledger_turn=before,native_user_receipt=False,model_consumption=False),exclusive=True)
        return dict(outcome,**{label+'_input':evidence})
    except BaseException as exc:
        write_json(directory/'unknown.json',dict(status='UNKNOWN_NO_REPLAY',intent=intent,
            error_type=type(exc).__name__,error=str(exc),retry_allowed=False),exclusive=True)
        raise
