"""Bounded driving of one retained campaign service and its original RPC.

No reconnect, replacement process, operation replay or new response writer.
A yielded invocation retains the same owner. Loss of that owner needs separate
recovery; the exclusive runtime directory cannot be opened as a new runtime.
"""
import os
import time

from orchestration_common import file_ref,require,write_json
from upgrade_task_actor import TaskService
from upgrade_task_enrollment import TaskCampaignGate
from upgrade_task_instructions import publish_next_input,remaining_operations


class CampaignRuntime:
    def __init__(self,service):
        require(type(service) is TaskService and type(service.actor.gate) is TaskCampaignGate,
                "TASK_RUNTIME_ORIGINAL_PRODUCTION_SERVICE_REQUIRED")
        require(getattr(service,"runtime_owner",None) is None,"TASK_RUNTIME_ALREADY_OWNED")
        self.service=service;self.actor=service.actor;self.gate=self.actor.gate
        self.client=self.actor.client;self.ledger=self.actor.ledger
        self.scope=self.actor.scope
        require(self.gate.client is self.client and self.gate.ledger is self.ledger
                and self.actor.service_owner is service,"TASK_RUNTIME_OWNER_CHANGED")
        self.pid=os.getpid();self.closed=False;self.running=False;self.sequence=0
        self.callback_ingress=None
        self.gate._observe()
        self.root=self.actor.directory/"runtime-owner"
        self.root.mkdir(mode=0o700,exist_ok=False)
        self.proof=write_json(self.root/"owner.json",dict(schema="upgrade-task-runtime-owner-v1",
            owner_pid=self.pid,enrollment=self.gate.proof,campaign=self.gate.runner.ref,
            registration_sha256=self.ledger.registration_sha256,ledger=dict(path=str(self.ledger.path),
                device=self.ledger.identity[0],inode=self.ledger.identity[1]),
            operation_scope=sorted(self.scope),reconnect_allowed=False,operation_budget_reset=False),exclusive=True)
        service.runtime_owner=self

    def enable_callback_ingress(self):
        """Explicit endpoint on this existing runtime; never a second owner."""
        from upgrade_task_callback_ingress import CallbackIngress
        return CallbackIngress(self)

    def _verify(self):
        require(os.getpid()==self.pid==self.service.owner_pid==self.gate.owner_pid,
                "TASK_RUNTIME_OWNER_PROCESS_CHANGED")
        require(self.service.runtime_owner is self and self.service.actor is self.actor
                and self.actor.service_owner is self.service and self.actor.gate is self.gate
                and self.actor.client is self.client and self.actor.ledger is self.ledger
                and self.actor.scope==self.scope
                and self.gate.client is self.client and self.gate.ledger is self.ledger,
                "TASK_RUNTIME_OWNER_CHANGED")
        require(file_ref(self.root/"owner.json")==self.proof,"TASK_RUNTIME_OWNER_PROOF_CHANGED")
        require(not os.path.lexists(self.gate.spec["stop_path"]),"EXPERIMENT_STOP_REQUESTED")
        with self.ledger._transaction():pass

    def _remaining(self):
        with self.ledger._transaction():
            used={row[0] for row in self.ledger.db.execute("SELECT operation_id FROM calls")}
            pending=set(self.actor.scope)-used
            for members in self.ledger.registration["choice_groups"].values():
                if used.intersection(members):pending.difference_update(members)
            if not pending:return []
            return remaining_operations(self.ledger.registration,self.actor.scope,used)

    def run(self,*,seconds=30,max_messages=64,max_pulses=32,max_new_turns=1):
        """Drive original events and one normal continuation, then yield.

        Limits apply between original synchronous operations; a running original
        handler is collected, never killed and resubmitted at this time bound.
        Caller repeats run on THIS object only. Exhausted scope is not business
        completion, and an idle pulse is not actor termination.
        """
        require(not self.closed,"TASK_RUNTIME_CLOSED_NO_REPLAY")
        require(not self.running,"TASK_RUNTIME_REENTRANT_CALL")
        require(type(seconds) in (int,float) and 0<seconds<=30
                and type(max_messages) is int and 0<max_messages<=1024
                and type(max_pulses) is int and 0<max_pulses<=64
                and type(max_new_turns) is int and 0<=max_new_turns<=1,
                "TASK_RUNTIME_INVOCATION_BUDGET_INVALID")
        self._verify()
        self.running=True;self.sequence+=1
        path=self.root/(f"{self.sequence:06d}-intent.json")
        # Durable before the first operation/continuation. A failed persistence
        # cannot be worked around with a different runtime or invocation ID.
        try:
            intent=write_json(path,dict(owner=self.proof,turn_id=self.ledger.active_turn,
                seconds=seconds,max_messages=max_messages,max_pulses=max_pulses,
                max_new_turns=max_new_turns),exclusive=True)
            began=time.monotonic();pulses=messages=inputs=0;status="YIELDED";continuations=[];callbacks=[]
            while pulses<max_pulses and messages<max_messages and time.monotonic()-began<seconds:
                self._verify()
                if self.actor.closed:
                    self.service._normal_terminal()
                    if not self._remaining():
                        status="SCOPE_EXHAUSTED";self.closed=True;break
                    if inputs>=max_new_turns:break
                    if self.callback_ingress is not None:
                        callback=self.callback_ingress.poll(timeout=0)
                        if callback is not None:
                            callbacks.append(callback);pulses+=1
                            inputs+=callback['model_inputs_sent']
                            continue
                    plan=publish_next_input(self.service)
                    next_turn=self.service.start_next_turn(plan)
                    inputs+=next_turn["model_inputs_sent"]
                    continuations.append(dict(plan=plan,result=next_turn))
                    continue
                remaining=seconds-(time.monotonic()-began)
                if remaining<=0:break
                observed=self.service.pulse(seconds=min(1,remaining),max_messages=max_messages-messages)
                pulses+=1;messages+=observed["messages_observed"]
                # Do not label interrupted/failed/unresolved completion as a
                # normal yield when a finite pulse limit ended this invocation.
                if self.actor.closed:self.service._normal_terminal()
            result=dict(schema="upgrade-task-runtime-invocation-v1",owner=self.proof,intent=intent,
                status=status,turn_id=self.ledger.active_turn,pulses=pulses,messages_observed=messages,
                model_inputs_sent=inputs,continuations=continuations,
                callback_inputs=callbacks,
                pending_binding_calls=list(self.service.pending),business_accepted=False,
                operation_budget_reset=False,reconnects=0,elapsed_seconds=time.monotonic()-began)
            receipt=write_json(path.with_name(path.name.replace("intent","result")),result,exclusive=True)
            return dict(result,receipt=receipt)
        except BaseException as exc:
            self.closed=True
            if self.callback_ingress is not None:self.callback_ingress.close()
            try:self.actor.close("UNKNOWN")
            except Exception:self.actor.closed=True
            # Preserve the first failure, including a failed diagnostic write.
            try:write_json(path.with_name(path.name.replace("intent","unknown")),dict(
                status="UNKNOWN_NO_REPLAY",owner=self.proof,error_type=type(exc).__name__,
                error=str(exc),reconnect_allowed=False),exclusive=True)
            except (OSError,ValueError):pass
            raise
        finally:
            self.running=False
            if self.closed and self.callback_ingress is not None:self.callback_ingress.close()
