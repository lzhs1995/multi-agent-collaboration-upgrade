"""One-shot execution of registered task operations on a pinned TaskRPC.

The owning launcher supplies its independently verified enrollment gate and a
fixed kind-to-handler table. This module supplies no foreground replacement,
shell launcher or ambient authority. Handlers receive only frozen payloads;
the native model can select only an existing operation_id. The same ledger
governs delivery, cancellation and read-only recovery after a disconnect.
"""
from __future__ import annotations

from pathlib import Path
import os
import time

from orchestration_common import checked_path, json_ref, require, write_json
from upgrade_rpc_peer import verify_peer


class TaskActor:
    def __init__(self, client, ledger, handlers, enrollment_gate, evidence_directory, *, operation_scope=None,
                 invocation_recorder=None, completion_observer=None):
        self.client, self.ledger = client, ledger
        self.handlers, self.gate = dict(handlers), enrollment_gate
        self.directory = checked_path(evidence_directory)
        self.closed = False
        require(invocation_recorder is None or callable(invocation_recorder), "TASK_ACTOR_INVOCATION_RECORDER_INVALID")
        self.invocation_recorder = invocation_recorder
        require(completion_observer is None or callable(completion_observer), "TASK_ACTOR_COMPLETION_OBSERVER_INVALID")
        self.completion_observer = completion_observer
        operations = ledger.registration["operations"]
        require(operation_scope is None or (isinstance(operation_scope, (list, tuple, set, frozenset))
                and all(isinstance(name, str) for name in operation_scope)), "TASK_ACTOR_OPERATION_SCOPE_INVALID")
        self.scope = frozenset(operations if operation_scope is None else operation_scope)
        require(self.scope and self.scope <= set(operations), "TASK_ACTOR_OPERATION_SCOPE_INVALID")
        kinds = {operations[name]["kind"] for name in self.scope}
        require(set(self.handlers) == kinds and all(callable(x) for x in self.handlers.values()),
                "TASK_ACTOR_EXACT_HANDLERS_REQUIRED")
        self.directory.mkdir(mode=0o700, exist_ok=False)

    def _recorded(self, message, ref):
        verify_peer(self.client.sock, self.client.server, self.client.process)
        record = json_ref(ref, "original received task RPC")
        require(record.get("direction") == "received" and record.get("message") == message
                and record.get("peer", {}).get("server") == self.client.server
                and record["peer"].get("peer") == {k: self.client.server[k] for k in ("pid", "uid")},
                "TASK_ACTOR_ORIGINAL_RECEIVE_REQUIRED")

    def step(self, *, timeout=20):
        require(not self.closed, "TASK_ACTOR_CLOSED_NO_REPLAY")
        message, ref = self.client.receive_recorded(timeout)
        return self.process_recorded(message,ref)

    def process_recorded(self, message, ref):
        """Process exactly one original recorded receive; never invent input."""
        require(not self.closed, "TASK_ACTOR_CLOSED_NO_REPLAY")
        self._recorded(message, ref)
        method = message.get("method")
        if method == "item/tool/call":
            args = message.get("params", {}).get("arguments", {})
            require(isinstance(args, dict) and isinstance(args.get("operation_id"), str)
                    and args["operation_id"] in self.scope, "TASK_ACTOR_OPERATION_OUTSIDE_STAGE")
            # Gate implementation belongs to the exact enrolled topology. It
            # must freshly verify task ownership, deployment, stop, endpoints
            # and the operation's scope. No boolean env flag supplies it.
            registration = self.ledger.registration
            self.gate(registration, message, ref)
            verify_peer(self.client.sock, self.client.server, self.client.process)
            operation = self.ledger.receive(message)
            call = message["params"]["callId"]
            try:
                if self.invocation_recorder is not None:
                    evidence = self.invocation_recorder(self.ledger,message,ref)
                    self.ledger.record_native_invocation(call,evidence)
                result, receipt = self.handlers[operation["kind"]](operation["payload"])
                self.ledger.operation_result(call, result, receipt)
                self.gate(registration, message, ref)
                self.client.respond(self.ledger.response_intent(call))
                return {"phase": "RESPONSE_INTENDED", "call_id": call,
                        "native_completion": False, "business_accepted": False}
            except BaseException:
                state = self.ledger.inspect(call)
                if state["phase"] in ("INTENDED", "RESULT_RECORDED", "RESPONSE_INTENDED"):
                    self.ledger.stop(call, "UNKNOWN")
                self.closed = True
                raise
        if method == "item/completed" and message.get("params", {}).get("item", {}).get("type") == "dynamicToolCall":
            call = message["params"]["item"]["id"]
            # inspect() rejects an unrelated call before using it as a path.
            self.ledger.inspect(call)
            evidence = write_json(self.directory / (call + "-completed.json"), message, exclusive=True)
            result = self.ledger.complete(message, evidence)
            if self.completion_observer is not None and result["phase"]=="NATIVE_COMPLETED":
                result["artifact_binding_observation"] = self.bind_completed(message, ref)
            return result
        if method == "turn/completed":
            params = message.get("params", {})
            require(params.get("threadId") == self.ledger.registration["thread_id"]
                    and params.get("turn", {}).get("id") == self.ledger.active_turn,
                    "TASK_ACTOR_FOREIGN_TURN_COMPLETION")
            self.close("CANCELLED" if params["turn"].get("status") == "interrupted" else "UNKNOWN")
        elif "id" in message:
            self.close("UNKNOWN")
            require(False, "TASK_ACTOR_UNOWNED_SERVER_REQUEST_NO_APPROVAL")
        return {"phase": "OBSERVED", "method": method, "record": ref, "business_accepted": False}

    def bind_completed(self, message, original_ref):
        """Observe the original result later without executing or responding.

        A native inner completion may precede its outer model-visible result.
        The owner can revisit that original completion under the live gate;
        successful bindings remain append-once and cannot be rebound.
        """
        require(not self.closed and self.completion_observer is not None,
                "TASK_ACTOR_BINDING_OBSERVER_UNAVAILABLE")
        self._original_completion(message,original_ref)
        try:
            self.gate(self.ledger.registration, message, original_ref)
            verify_peer(self.client.sock, self.client.server, self.client.process)
            return self.completion_observer(message, original_ref)
        except BaseException:
            self.closed = True
            raise

    def _original_completion(self,message,original_ref):
        self._recorded(message, original_ref)
        require(message.get("method") == "item/completed", "TASK_ACTOR_NATIVE_COMPLETION_REQUIRED")
        call=message["params"]["item"]["id"]
        row=self.ledger.inspect(call)
        from orchestration_common import strict_json
        events=self.ledger.db.execute("SELECT body FROM events WHERE call_id=? AND kind='NATIVE_COMPLETED'",(call,)).fetchall()
        require(row["phase"]=="NATIVE_COMPLETED" and len(events)==1
                and strict_json(events[0][0])["native"]==message, "TASK_ACTOR_ORIGINAL_COMPLETION_REQUIRED")

    def reconcile_binding(self,message,original_ref):
        """Attach only a previously published original artifact after failure.

        The same closed actor and live gate are required. This never reopens
        service processing, runs an operation or resends a response. Recovery
        after losing this in-memory owner remains a separate enrollment task.
        """
        reconciler=getattr(self.completion_observer,"reconcile",None)
        require(self.closed and callable(reconciler),"TASK_ACTOR_BINDING_RECOVERY_UNAVAILABLE")
        self._original_completion(message,original_ref)
        self.gate(self.ledger.registration,message,original_ref)
        verify_peer(self.client.sock,self.client.server,self.client.process)
        return reconciler(message,original_ref)

    def close(self, reason="UNKNOWN"):
        """Seal pending ledger rows; never send an interrupt or replay input."""
        require(reason in ("UNKNOWN", "CANCELLED"), "TASK_ACTOR_CLOSE_REASON_INVALID")
        self.closed = True
        rows = self.ledger.db.execute("SELECT call_id FROM calls WHERE phase IN "
            "('INTENDED','RESULT_RECORDED','RESPONSE_INTENDED')").fetchall()
        for (call,) in rows:
            self.ledger.stop(call, reason)

    def recover_completion(self, message, original_ref):
        """Exact original native completion can resolve UNKNOWN without execution."""
        self._recorded(message, original_ref)
        require(message.get("method") == "item/completed", "TASK_ACTOR_NATIVE_COMPLETION_REQUIRED")
        call = message["params"]["item"]["id"]
        self.ledger.inspect(call)
        evidence = write_json(self.directory / (call + "-recovered.json"), message, exclusive=True)
        return self.ledger.complete(message, evidence)


class TaskService:
    """Bounded pulses on the existing actor, including late native results.

    The same object and RPC connection persist between pulses. A pulse ending
    is not a stopped actor, a new attempt, enrollment or permission to restart.
    Runtime enrollment and fixed operation handlers are supplied by the owner.
    There is no new socket, model input, reconnect, retry or response writer.
    """
    def __init__(self, actor):
        require(type(actor) is TaskActor and not actor.closed, "TASK_SERVICE_ORIGINAL_ACTOR_REQUIRED")
        require(callable(getattr(actor.client,"poll_recorded",None)), "TASK_SERVICE_POLL_REQUIRED")
        require(getattr(actor,"service_owner",None) is None,"TASK_SERVICE_ALREADY_OWNED")
        actor.service_owner=self
        self.actor=actor
        self.pending={}
        self.terminal=None
        self.normal_completion=None
        self.owner_pid=os.getpid()

    def _complete_turn(self,record):
        message,ref=record
        self.actor.process_recorded(message,ref)
        turn=message.get("params",{}).get("turn",{})
        if turn.get("status")=="completed" and not self.pending:
            unresolved=self.actor.ledger.db.execute(
                "SELECT 1 FROM calls WHERE phase!='NATIVE_COMPLETED'").fetchone()
            if unresolved is None:
                self.normal_completion=record

    def _normal_terminal(self):
        actor=self.actor
        require(os.getpid()==self.owner_pid and actor.service_owner is self,
                "TASK_SERVICE_ORIGINAL_OWNER_REQUIRED")
        require(actor.closed and self.normal_completion is not None and not self.pending,
                "TASK_SERVICE_NORMAL_TERMINAL_REQUIRED")
        message,previous_ref=self.normal_completion
        actor._recorded(message,previous_ref)
        require(message["params"]["turn"]["id"]==actor.ledger.active_turn,
                "TASK_SERVICE_ORIGINAL_TERMINAL_CHANGED")
        require(actor.ledger.db.execute("SELECT 1 FROM calls WHERE phase!='NATIVE_COMPLETED'").fetchone() is None,
                "TASK_SERVICE_UNRESOLVED_PRIOR_OPERATION")
        return previous_ref

    def _enroll_next_turn(self,grant_ref,previous_ref,path,*,model_inputs_sent):
        actor=self.actor
        value=json_ref(grant_ref)
        require(value.get("previous_completion")==previous_ref
                and value.get("previous_turn_id")==actor.ledger.active_turn,
                "TASK_SERVICE_ORIGINAL_TERMINAL_CHANGED")
        try:
            enrollment=actor.gate.enroll_next_turn(grant_ref,previous_ref)
            require(actor.ledger.active_turn==value["turn_id"],"TASK_SERVICE_NEXT_TURN_NOT_ENROLLED")
            write_json(path.with_name(path.name.replace("intent","result")),
                dict(grant=grant_ref,enrollment=enrollment,turn_id=actor.ledger.active_turn,
                    operation_budget_reset=False,model_inputs_sent=model_inputs_sent),exclusive=True)
        except BaseException:
            actor.close("UNKNOWN")
            raise
        self.normal_completion=None
        self.terminal=None
        actor.closed=False
        return dict(status="ACTIVE",turn_id=actor.ledger.active_turn,
                    operation_budget_reset=False,model_inputs_sent=model_inputs_sent)

    def continue_turn(self,grant_ref):
        """Enroll an already-created turn; sends no model input. No replay."""
        previous_ref=self._normal_terminal()
        actor=self.actor
        require(callable(getattr(actor.gate,"enroll_next_turn",None)),
                "TASK_SERVICE_LIVE_TURN_GATE_REQUIRED")
        value=json_ref(grant_ref)
        require(value.get("previous_completion")==previous_ref
                and value.get("previous_turn_id")==actor.ledger.active_turn,
                "TASK_SERVICE_ORIGINAL_TERMINAL_CHANGED")
        path=actor.directory/(actor.ledger.active_turn+"-next-turn-intent.json")
        write_json(path,dict(grant=grant_ref,previous_completion=previous_ref,
            owner_pid=self.owner_pid,model_inputs_sent=0),exclusive=True)
        return self._enroll_next_turn(grant_ref,previous_ref,path,model_inputs_sent=0)

    def start_next_turn(self,plan_ref):
        """One planned input through the original live gate and retained RPC.

        Shares the transition budget with continue_turn. An uncertain send or
        failed enrollment keeps that budget consumed and the actor closed.
        This is neither a reconnect nor process-death recovery.
        """
        previous_ref=self._normal_terminal()
        actor=self.actor
        from upgrade_task_enrollment import TaskCampaignGate
        require(type(actor.gate) is TaskCampaignGate,"TASK_SERVICE_PRODUCTION_INPUT_GATE_REQUIRED")
        actor.gate.validate_next_input(self,plan_ref,previous_ref)
        path=actor.directory/(actor.ledger.active_turn+"-next-turn-intent.json")
        intent=write_json(path,dict(plan=plan_ref,previous_completion=previous_ref,
            owner_pid=self.owner_pid,max_model_inputs=1),exclusive=True)
        try:
            grant=actor.gate.start_next_input(self,plan_ref,previous_ref,intent)
            return self._enroll_next_turn(grant,previous_ref,path,model_inputs_sent=1)
        except BaseException as exc:
            actor.close("UNKNOWN")
            write_json(path.with_name(path.name.replace("intent","unknown")),dict(
                status="UNKNOWN_NO_REPLAY",intent=intent,error_type=type(exc).__name__,
                error=str(exc),retry_allowed=False),exclusive=True)
            raise

    def start_callback_turn(self, connection, request):
        """One ingress-authenticated idle callback on this same retained owner."""
        from upgrade_task_callback_turn import deliver
        return deliver(self, connection, request)

    def _bindings(self):
        for call,(message,ref) in list(self.pending.items()):
            result=self.actor.bind_completed(message,ref)
            if not (isinstance(result,dict) and result.get("status")=="WAITING_ORIGINAL_NATIVE_RESULT"):
                del self.pending[call]

    def pulse(self, *, seconds=1, max_messages=64):
        require(type(seconds) in (int,float) and 0 < seconds <= 30
                and type(max_messages) is int and 0 < max_messages <= 1024,
                "TASK_SERVICE_PULSE_BUDGET_INVALID")
        require(not self.actor.closed,"TASK_SERVICE_CLOSED_NO_REPLAY")
        require(os.getpid()==self.owner_pid,"TASK_SERVICE_ORIGINAL_OWNER_REQUIRED")
        deadline=time.monotonic()+seconds
        count=0
        try:
            while time.monotonic()<deadline and count<max_messages:
                self._bindings()
                if self.terminal is not None and not self.pending:
                    self._complete_turn(self.terminal)
                    break
                record=self.actor.client.poll_recorded(timeout=min(0.25,max(0,deadline-time.monotonic())))
                if record is None:
                    continue
                message,ref=record
                self.actor._recorded(message,ref)
                count+=1
                method=message.get("method")
                if method=="turn/completed" and message.get("params",{}).get("turn",{}).get("status") in ("interrupted","failed"):
                    self.actor.process_recorded(message,ref)
                    break
                # A result may have reached disk while waiting for this RPC.
                # Bind it before a dependent model choice reaches the ledger.
                self._bindings()
                if self.terminal is not None:
                    require("id" not in message and method!="turn/completed",
                            "TASK_SERVICE_INPUT_AFTER_TERMINAL")
                    continue
                if method=="turn/completed":
                    params=message.get("params",{});turn=params.get("turn",{})
                    require(params.get("threadId")==self.actor.ledger.registration["thread_id"]
                            and turn.get("id")==self.actor.ledger.active_turn,
                            "TASK_ACTOR_FOREIGN_TURN_COMPLETION")
                    if turn.get("status")=="completed" and self.pending:
                        write_json(self.actor.directory/(turn["id"]+"-terminal-binding-wait.json"),
                                   dict(received=ref,original_completion=message),exclusive=True)
                        self.terminal=record
                        continue
                    self._complete_turn(record)
                    break
                result=self.actor.process_recorded(message,ref)
                require(result.get("phase")!="CONFLICT","TASK_SERVICE_NATIVE_COMPLETION_CONFLICT")
                observation=result.get("artifact_binding_observation")
                if isinstance(observation,dict) and observation.get("status")=="WAITING_ORIGINAL_NATIVE_RESULT":
                    call=message["params"]["item"]["id"]
                    require(call not in self.pending,"TASK_SERVICE_DUPLICATE_COMPLETION")
                    write_json(self.actor.directory/(call+"-binding-wait.json"),
                        dict(received=ref,call_id=call,original_completion=message),exclusive=True)
                    self.pending[call]=record
                if self.actor.closed:
                    break
        except BaseException:
            # In particular, a partial-frame timeout is not a safe idle wait.
            self.actor.close("UNKNOWN")
            raise
        return dict(status="CLOSED" if self.actor.closed else "ACTIVE",messages_observed=count,
            pending_binding_calls=list(self.pending),terminal_received=self.terminal is not None,
            operation_budget_reset=False,model_inputs_sent=0,business_accepted=False)
