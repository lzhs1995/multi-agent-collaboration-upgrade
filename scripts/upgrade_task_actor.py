"""One-shot execution of registered task operations on a pinned TaskRPC.

The owning launcher supplies its independently verified enrollment gate and a
fixed kind-to-handler table. This module supplies no foreground replacement,
shell launcher or ambient authority. Handlers receive only frozen payloads;
the native model can select only an existing operation_id. The same ledger
governs delivery, cancellation and read-only recovery after a disconnect.
"""
from __future__ import annotations

from pathlib import Path

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
            if self.completion_observer is not None:
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
        self._recorded(message, original_ref)
        require(message.get("method") == "item/completed", "TASK_ACTOR_NATIVE_COMPLETION_REQUIRED")
        call=message["params"]["item"]["id"]
        row=self.ledger.inspect(call)
        from orchestration_common import strict_json
        events=self.ledger.db.execute("SELECT body FROM events WHERE call_id=? AND kind='NATIVE_COMPLETED'",(call,)).fetchall()
        require(row["phase"]=="NATIVE_COMPLETED" and len(events)==1
                and strict_json(events[0][0])["native"]==message, "TASK_ACTOR_ORIGINAL_COMPLETION_REQUIRED")
        try:
            self.gate(self.ledger.registration, message, original_ref)
            verify_peer(self.client.sock, self.client.server, self.client.process)
            return self.completion_observer(message, original_ref)
        except BaseException:
            self.closed = True
            raise

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
