"""Protocol routing for a future single-upstream task-tool relay.

The official server broadcasts dynamic calls to attached clients, so a direct
TUI and an independent responder can race. This router reserves one namespace
for the controller on the SAME upstream connection and forwards other traffic.
It does not open sockets, authenticate callers, execute operations or publish
enrollment. Its owner must establish peer identity, task authority, durable
intents, cancellation and recovery before using a returned task-call action.
"""
from __future__ import annotations

import copy
import uuid

from orchestration_common import canonical, contract_errors, require

NAMESPACE = "upgrade_collaboration"
METHOD = "item/tool/call"
MAX_MESSAGE = 1024 * 1024


def request_key(value):
    require(type(value) in (int, str) and (type(value) is not str or 0 < len(value) <= 256),
            "TASK_RELAY_REQUEST_ID_INVALID")
    # JSON integer and string IDs are different namespaces. bool is not an ID.
    return type(value).__name__, value


def _thread(value):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value,
            "TASK_RELAY_THREAD_INVALID")
    return value


def envelope(value):
    require(isinstance(value, dict) and len(canonical(value)) <= MAX_MESSAGE,
            "TASK_RELAY_ENVELOPE_INVALID")
    if "jsonrpc" in value:
        require(value["jsonrpc"] == "2.0", "TASK_RELAY_PROTOCOL_INVALID")
    if "method" in value:
        require(isinstance(value["method"], str) and value["method"]
                and "result" not in value and "error" not in value,
                "TASK_RELAY_REQUEST_INVALID")
        if "id" in value:
            request_key(value["id"])
    else:
        request_key(value.get("id"))
        require(("result" in value) != ("error" in value), "TASK_RELAY_RESPONSE_INVALID")
    return copy.deepcopy(value)


class TaskToolRoute:
    """In-memory routing state; a disconnect permanently seals this instance.

    A new object must NOT be used to replay pending calls. The caller persists
    the original request and operation before any side effect and reconstructs
    only via a separately verified original-attempt recovery process.
    """

    def __init__(self, thread_id, tools, *, limit=1024):
        self.thread = _thread(thread_id)
        require(isinstance(tools, (list, tuple)) and tools and len(tools) == len(set(tools))
                and all(isinstance(t, str) and t and len(t) <= 128 for t in tools),
                "TASK_RELAY_TOOL_SET_INVALID")
        require(type(limit) is int and 1 <= limit <= 10000, "TASK_RELAY_LIMIT_INVALID")
        self.tools, self.limit = frozenset(tools), limit
        self.requests, self.calls = {}, set()
        self.closed = False

    def _open(self):
        require(not self.closed, "TASK_RELAY_CONNECTION_CLOSED_NO_REPLAY")

    @contract_errors("TASK_RELAY_SERVER_MESSAGE_REFUSED")
    def from_server(self, message):
        self._open()
        value = envelope(message)
        if "method" not in value or "id" not in value:
            return {"route": "tui", "message": value}
        key = request_key(value["id"])
        require(key not in self.requests, "TASK_RELAY_SERVER_REQUEST_REUSED")
        require(len(self.requests) < self.limit, "TASK_RELAY_REQUEST_BUDGET_EXHAUSTED")
        params = value.get("params")
        owned = (value["method"] == METHOD and isinstance(params, dict)
                 and params.get("namespace") == NAMESPACE)
        if not owned:
            self.requests[key] = {"owner": "tui", "state": "forwarded"}
            return {"route": "tui", "message": value}
        require(set(params) == {"threadId", "turnId", "callId", "namespace", "tool", "arguments"},
                "TASK_RELAY_TASK_CALL_FIELDS_INVALID")
        require(_thread(params["threadId"]) == self.thread, "TASK_RELAY_FOREIGN_THREAD")
        _thread(params["turnId"])
        require(params["tool"] in self.tools and isinstance(params["arguments"], dict),
                "TASK_RELAY_TASK_TOOL_INVALID")
        call = params["callId"]
        require(isinstance(call, str) and 0 < len(call) <= 256, "TASK_RELAY_CALL_ID_INVALID")
        require(call not in self.calls, "TASK_RELAY_CALL_REUSED")
        self.requests[key] = {"owner": "controller", "state": "received", "message": value}
        self.calls.add(call)
        return {"route": "controller", "message": copy.deepcopy(value),
                "task_authorization_proven": False, "caller_authentication": False}

    @contract_errors("TASK_RELAY_CLIENT_MESSAGE_REFUSED")
    def from_tui(self, message):
        self._open()
        value = envelope(message)
        if "method" in value:
            return {"route": "server", "message": value}
        key = request_key(value["id"])
        entry = self.requests.get(key)
        require(entry is not None and entry["owner"] == "tui" and entry["state"] == "forwarded",
                "TASK_RELAY_TUI_RESPONSE_NOT_OWNED")
        # Mark before write: a failed/unknown write cannot permit a second send.
        entry["state"] = "response_intended"
        return {"route": "server", "message": value}

    @contract_errors("TASK_RELAY_CONTROLLER_RESPONSE_REFUSED")
    def controller_response(self, request_id, result):
        self._open()
        entry = self.requests.get(request_key(request_id))
        require(entry is not None and entry["owner"] == "controller" and entry["state"] == "received",
                "TASK_RELAY_CONTROLLER_RESPONSE_NOT_OWNED")
        require(isinstance(result, dict) and set(result) == {"success", "contentItems"}
                and type(result["success"]) is bool and isinstance(result["contentItems"], list)
                and result["contentItems"], "TASK_RELAY_RESULT_INVALID")
        # Fixed task operations return text/structured JSON. Media and arbitrary
        # server requests are outside this adapter's task contract.
        require(all(isinstance(item, dict) and set(item) == {"type", "text"}
                    and item["type"] == "inputText" and isinstance(item["text"], str)
                    for item in result["contentItems"]), "TASK_RELAY_RESULT_CONTENT_INVALID")
        value = envelope({"id": request_id, "result": result})
        entry["state"] = "response_intended"
        return {"route": "server", "message": value, "native_completion_proven": False}

    def disconnect(self):
        """Return unresolved request IDs; never invent operation outcomes."""
        self.closed = True
        return [{"request_id": key[1], "owner": entry["owner"], "state": "UNKNOWN",
                 "last_local_state": entry["state"]}
                for key, entry in self.requests.items()]
