"""Explicit new remote task creation evidence, separate from foreground proof.

These validators consume controller-pinned original RPC exchanges. They do not
authenticate the caller, grant task ownership or authorize a side effect. A new
task's external publisher must bind these references to its own registration.
New tasks explicitly use the supported threadSource=user classification and
retain the existing reader's requirement. The classification grants no power.
"""
from __future__ import annotations

from orchestration_common import json_ref, require
from upgrade_task_operations import tool_definition

CREATION_SCHEMA = "upgrade-task-creation-v1"


def exchange(value, method, server):
    require(isinstance(value, dict) and set(value) == {"intent", "response"},
            "TASK_CREATION_EXCHANGE_REQUIRED")
    sent, received = (json_ref(value[key]) for key in ("intent", "response"))
    for item in (sent, received):
        require(isinstance(item, dict) and set(item) == {"direction", "message", "peer"},
                "TASK_CREATION_RPC_RECORD_INVALID")
        peer = item["peer"]
        require(peer.get("schema") == "upgrade-task-rpc-peer-v1"
                and peer.get("server") == server
                and peer.get("peer") == {k: server[k] for k in ("pid", "uid")},
                "TASK_CREATION_RPC_PEER_CHANGED")
    request, response = sent["message"], received["message"]
    require(sent["direction"] == "send_intent" and received["direction"] == "received"
            and set(request) == {"id", "method", "params"} and request["method"] == method
            and type(request["id"]) is int and request["id"] > 0
            and set(response) == {"id", "result"} and type(response["id"]) is int
            and request["id"] == response["id"], "TASK_CREATION_RPC_PAIR_CHANGED")
    return request["params"], response["result"]


def validate_creation(ref, *, server, thread_id, journal_path, native_root, startup_text):
    value = json_ref(ref, "original task creation")
    require(set(value) == {"schema", "initialize", "thread_start", "turn_start"}
            and value["schema"] == CREATION_SCHEMA, "TASK_CREATION_SCHEMA_REQUIRED")
    init, initialized = exchange(value["initialize"], "initialize", server)
    require(init.get("clientInfo", {}).get("name") == "cmux_native_input"
            and init.get("capabilities", {}).get("experimentalApi") is True
            and isinstance(initialized.get("userAgent"), str)
            and initialized["userAgent"].startswith("codex-tui/")
            and initialized.get("codexHome") == native_root, "TASK_CREATION_ORIGINAL_INITIALIZATION_REQUIRED")
    params, result = exchange(value["thread_start"], "thread/start", server)
    thread = result.get("thread", {})
    require(params.get("dynamicTools") == [tool_definition()] and params.get("threadSource") == "user"
            and thread.get("id") == thread_id and thread.get("path") == journal_path
            and thread.get("originator") == "codex-tui", "TASK_CREATION_THREAD_CHANGED")
    turn_params, turn_result = exchange(value["turn_start"], "turn/start", server)
    require(turn_params == {"threadId": thread_id,
                "input": [{"type": "text", "text": startup_text, "text_elements": []}]}
            and isinstance(turn_result.get("turn"), dict), "TASK_CREATION_FIRST_INPUT_CHANGED")
    return {"turn_id": turn_result["turn"]["id"], "thread_parameters": params,
            "creation": ref}
