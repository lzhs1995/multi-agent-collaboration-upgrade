"""Read-only dynamic readiness, not a shell/hook readiness surrogate.

The caller supplies the already process-bound native session. This checks the
original task creation, operation ledger, native DynamicToolCall completion,
model identity and exact final result consumption. It never publishes adoption.
"""
import json
from pathlib import Path
import re
import sqlite3

from orchestration_common import file_ref, json_ref, object_sha, read_ref, require, strict_json
from upgrade_task_thread import journal_evidence
from upgrade_task_creation import validate_creation

SCHEMA = "upgrade-task-dynamic-readiness-v1"
# Official core::tools::flat_tool_name at the pinned 0.164.0 source concatenates
# the non-default namespace and name. This is the hook name, not the JS export.
DYNAMIC_HOOK_NAME = "upgrade_collaborationexecute"

READY_CALL = 'const result = await tools.upgrade_collaboration__execute({operation_id:"ready"}); text(result);'
READY_INSTRUCTION = (
    "Startup readiness: execute exactly this one code-mode call, once:\n" + READY_CALL +
    "\nRead the returned JSON and output only its receipt_nonce as your final answer. "
    "Do not guess the nonce, run another tool, or repeat the operation. "
    "This proves startup only, not business acceptance."
)


def readiness_text(instructions):
    return instructions + "\n\n" + READY_INSTRUCTION


def _contains_nonce(value, nonce):
    if isinstance(value, str):
        return nonce in value
    if isinstance(value, list):
        # Native content can split text across adjacent blocks.
        text = "".join(x.get("text", "") for x in value if isinstance(x, dict)
                       and isinstance(x.get("text"), str))
        return nonce in text or any(_contains_nonce(x, nonce) for x in value)
    if isinstance(value, dict):
        return any(_contains_nonce(x, nonce) for x in value.values())
    return False


def consumption_source(records, startup, completed, consumed, call, output, nonce):
    """Join one closed-form outer call to its exact output; reject other sources.

    The operation ID is unique in the ledger and the whole initial turn has
    exactly one matching outer invocation. An unrelated tool, comment/string
    containing a call, additional invocation, input or compacted history cannot
    stand in for this chain. Later turns are outside this readiness window.
    """
    turn = startup["turn_id"]
    window = [(ref, record) for ref, record in records if ref["offset"] <= consumed["offset"]]
    calls = [(ref, record["payload"]) for ref, record in window
             if record.get("type") == "response_item"
             and record.get("payload", {}).get("type") in ("custom_tool_call", "function_call")]
    require(len(calls) == 1, "TASK_READINESS_OUTER_CALL_AMBIGUOUS")
    ref, outer = calls[0]
    require(outer.get("type") == "custom_tool_call" and outer.get("name") == "exec"
            and isinstance(outer.get("input"), str) and outer["input"].strip() == READY_CALL
            and outer.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") == turn
            and isinstance(outer.get("call_id"), str) and outer["call_id"],
            "TASK_READINESS_OUTER_CALL_UNBOUND")
    returns = [(r, rec["payload"]) for r, rec in window
               if rec.get("type") == "response_item"
               and rec.get("payload", {}).get("type") == "custom_tool_call_output"
               and rec["payload"].get("call_id") == outer["call_id"]]
    require(len(returns) == 1, "TASK_READINESS_OUTER_OUTPUT_AMBIGUOUS")
    returned, payload = returns[0]
    content = payload.get("output")
    require(payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id") == turn
            and isinstance(content, list) and len(content) == 2
            and all(isinstance(x, dict) and set(x) == {"type", "text"}
                    and x["type"] == "input_text" and isinstance(x["text"], str) for x in content)
            and re.fullmatch(r"Script completed\nWall time [0-9.]+ seconds\nOutput:\n", content[0]["text"])
            and strict_json(content[1]["text"]) == strict_json(output["contentItems"][0]["text"])
            and startup["input_offset"] < ref["offset"] < completed["offset"]
            < returned["offset"] < consumed["offset"], "TASK_READINESS_OUTER_OUTPUT_CHANGED")
    final = next(record["payload"] for r, record in window if r["offset"] == consumed["offset"])
    for r, record in window:
        if not _contains_nonce(record, nonce):
            continue
        p = record.get("payload", {})
        if r["offset"] in (completed["offset"], returned["offset"]):
            continue  # Both exact results were independently matched above.
        assistant = (record.get("type") == "response_item"
                     and (p.get("type") == "reasoning" or
                          (p.get("type") == "message" and p.get("role") == "assistant")))
        assistant_event = record.get("type") == "event_msg" and p.get("type") in (
            "agent_message", "agent_reasoning")
        item = p.get("item", {})
        native_final = (record.get("type") == "event_msg" and p.get("type") == "item_completed"
                        and p.get("thread_id") == startup["thread_id"] and p.get("turn_id") == turn
                        and item.get("type") == "AgentMessage" and item.get("phase") == "final_answer"
                        and isinstance(final.get("id"), str) and item.get("id") == final["id"]
                        and item.get("content") == [{"type":"Text","text":nonce}])
        require((assistant or assistant_event or native_final) and r["offset"] > returned["offset"],
                "TASK_READINESS_NONCE_OTHER_SOURCE")
    return dict(outer_call=ref, outer_output=returned, outer_call_id=outer["call_id"],
                inner_call_id=call, source_window_end=consumed, other_nonce_sources=False)


def task_startup_result(spec):
    from upgrade_task_startup import plan_value, thread_parameters
    directory = Path(spec["adoption_path"]).parent / "task-startup"
    result_ref = file_ref(directory / "result.json")
    result = json_ref(result_ref)
    require(result.get("schema") == "upgrade-task-startup-result-v1"
            and result.get("plan") == spec["task_startup"]
            and result.get("ledger") == str(directory / "operations.sqlite")
            and result.get("adoption_proven") is False, "TASK_READINESS_STARTUP_CHANGED")
    anchor = json_ref(result["anchor"])
    plan = plan_value(spec["task_startup"])
    require(anchor["thread_id"] == result["thread_id"] and anchor["native_root"] == plan["native_root"]
            and anchor["startup_text"] == plan["startup_text"], "TASK_READINESS_ANCHOR_CHANGED")
    original = validate_creation(result["creation"], server=anchor["server"], thread_id=result["thread_id"],
        journal_path=anchor["journal"]["path"], native_root=anchor["native_root"], startup_text=anchor["startup_text"])
    require(original["thread_parameters"] == thread_parameters(plan),
            "TASK_READINESS_CREATION_PARAMETERS_CHANGED")
    require(journal_evidence(anchor)["startup_turn"] == result["turn_id"] == original["turn_id"],
            "TASK_READINESS_STARTUP_TURN_CHANGED")
    return result_ref, result, anchor


def dynamic_readiness(native, session, spec):
    from upgrade_readiness_evidence import native_records
    from upgrade_task_startup import plan_value, registration_for_plan
    result_ref, startup, anchor = task_startup_result(spec)
    require(session["session_id"] == startup["thread_id"]
            and all(session["transcript"][k] == anchor["journal"][k] for k in ("path","device","inode")),
            "TASK_READINESS_ORIGINAL_JOURNAL_REQUIRED")
    registration = json_ref(startup["operations"])
    require(registration == registration_for_plan(plan_value(spec["task_startup"]),
                startup["thread_id"], startup["turn_id"], anchor["server"])
            and object_sha(registration) == startup["registration_sha256"]
            and registration["thread_id"] == startup["thread_id"] and registration["turn_id"] == startup["turn_id"]
            and registration["operations"].get("ready") == {"kind":"readiness","payload":{}},
            "TASK_READINESS_REGISTRATION_CHANGED")
    db = sqlite3.connect(Path(startup["ledger"]).as_uri()+"?mode=ro",uri=True)
    try:
        db.execute("BEGIN")
        persisted = strict_json(db.execute("SELECT body FROM registration WHERE id=1").fetchone()[0])
        require(persisted == registration, "TASK_READINESS_LEDGER_CHANGED")
        rows = db.execute("SELECT call_id,request,phase,result,receipt FROM calls WHERE operation_id='ready'").fetchall()
        require(len(rows)==1 and rows[0][2]=="NATIVE_COMPLETED", "TASK_READINESS_NATIVE_OPERATION_REQUIRED")
        call, request, _, output, receipt = rows[0]
        request, output, receipt = map(strict_json,(request,output,receipt))
        events=[(kind,strict_json(body)) for kind,body in db.execute(
            "SELECT kind,body FROM events WHERE call_id=? ORDER BY sequence",(call,))]
    finally:
        db.close()
    require([x[0] for x in events]==["INTENDED","RESULT_RECORDED","RESPONSE_INTENDED","NATIVE_COMPLETED"],
            "TASK_READINESS_OPERATION_HISTORY_CHANGED")
    require(request["params"]["threadId"]==startup["thread_id"]
            and request["params"]["turnId"]==startup["turn_id"]
            and request["params"]["arguments"]=={"operation_id":"ready"}
            and events[0][1]["request"]==request and events[1][1]["result"]==output
            and events[1][1]["receipt"]==receipt
            and events[2][1]["response"]=={"id":request["id"],"result":output},
            "TASK_READINESS_OPERATION_BINDING_CHANGED")
    data=json_ref(receipt)
    nonce=data.get("receipt_nonce")
    require(data.get("schema")==SCHEMA and isinstance(nonce,str) and re.fullmatch(r"[0-9a-f]{48}",nonce)
            and data.get("thread_id")==startup["thread_id"] and data.get("turn_id")==startup["turn_id"]
            and data.get("plan")==spec["task_startup"]
            and output=={"success":True,"contentItems":[{"type":"inputText","text":json.dumps(data,sort_keys=True)}]}
            and nonce not in anchor["startup_text"], "TASK_READINESS_RECEIPT_CHANGED")
    completion=json_ref(events[3][1]["evidence"])
    require(completion==events[3][1]["native"], "TASK_READINESS_COMPLETION_CHANGED")
    item=completion["params"]["item"]
    require(completion["method"]=="item/completed" and completion["params"]["threadId"]==startup["thread_id"]
            and completion["params"]["turnId"]==startup["turn_id"] and item["id"]==call
            and item["type"]=="dynamicToolCall" and item["namespace"]=="upgrade_collaboration"
            and item["tool"]=="execute" and item["arguments"]=={"operation_id":"ready"}
            and item["status"]=="completed" and item["success"] is True
            and item["contentItems"]==output["contentItems"], "TASK_READINESS_COMPLETION_MISMATCH")
    models, completed, consumed=[],[],[]
    records = list(native_records(native,session))
    for ref,record in records:
        payload=record.get("payload",{})
        if record.get("type")=="turn_context" and payload.get("turn_id")==startup["turn_id"]:
            models.append((ref,payload.get("model")))
        if record.get("type")=="event_msg" and payload.get("type")=="item_completed" and payload.get("item",{}).get("id")==call:
            native_item=payload["item"]
            require(payload.get("thread_id")==startup["thread_id"] and payload.get("turn_id")==startup["turn_id"]
                    and native_item.get("type")=="DynamicToolCall"
                    and all(native_item.get(k)==item[k] for k in ("namespace","tool","arguments","status","success"))
                    and native_item.get("content_items")==item["contentItems"], "TASK_READINESS_NATIVE_RESULT_CHANGED")
            completed.append(ref)
        if (record.get("type")=="response_item" and payload.get("type")=="message"
                and payload.get("role")=="assistant" and payload.get("phase")=="final_answer"
                and payload.get("internal_chat_message_metadata_passthrough",{}).get("turn_id")==startup["turn_id"]
                and payload.get("content") and "".join(x.get("text","") for x in payload["content"])==nonce):
            consumed.append(ref)
    require(len(models)==len(completed)==len(consumed)==1
            and models[0][1]==json_ref(spec["task_startup"])["model"]
            and anchor["startup_input"]["offset"] < completed[0]["offset"] < consumed[0]["offset"],
            "TASK_READINESS_MODEL_OR_CONSUMPTION_MISSING")
    source = consumption_source(records, dict(startup, input_offset=anchor["startup_input"]["offset"]),
                                completed[0], consumed[0], call, output, nonce)
    return dict(schema=SCHEMA,startup_result=result_ref,call_id=call,receipt=receipt,
        model=models[0][1],model_record=models[0][0],native_completion=completed[0],
        model_consumption=consumed[0],consumption_source=source,hooks_proven=False,adoption_proven=False)


def task_readiness(native, session, spec, startup_ref, binding, launches):
    """Require real original observer evidence separately from dynamic success.

    The inspector supplies process-bound sessions/launches and validated config.
    This reader performs no hook execution, model input or adoption publication.
    A successful dynamic call alone cannot satisfy either automatic hook event.
    """
    from upgrade_readiness_evidence import hook_matches
    from upgrade_startup_contract import TASK_SCHEMA
    require(spec.get("schema") == TASK_SCHEMA and session.get("provider") == "codex",
            "TASK_HOOK_EXPLICIT_TOPOLOGY_REQUIRED")
    read_ref(spec["pins"]["codex/hooks.json"], "frozen automatic hooks")
    proof = dynamic_readiness(native, session, spec)
    call = dict(startup=startup_ref, id=proof["call_id"], name=DYNAMIC_HOOK_NAME,
                arguments={"operation_id":"ready"})
    from upgrade_hook_observer import task_call_directory
    directory = task_call_directory(binding,startup_ref,session["session_id"],proof["call_id"])
    matched = {"PreToolUse":[], "PostToolUse":[]}
    times = {event:[] for event in matched}
    # Bound this exact startup/session/call. Old or unrelated calls can grow
    # indefinitely without changing its lookup or requiring a global scan.
    for index,path in enumerate(directory.glob("*.json")):
        require(index<4096, "TASK_HOOK_OBSERVATION_WINDOW_TOO_LARGE")
        ref = file_ref(path)
        value = json_ref(ref, "original automatic dynamic hook observation")
        payload = value.get("payload", {})
        if (value.get("direct_client") != "codex-server"
                or not isinstance(payload, dict)
                or payload.get("tool_name") != DYNAMIC_HOOK_NAME
                or payload.get("tool_input") != {"operation_id":"ready"}
                or not hook_matches(value, call, session, "codex", spec, launches)):
            continue
        event = payload["hook_event_name"]
        observed = value.get("observed_at_ns")
        require(type(observed) is int and observed > 0, "TASK_HOOK_OBSERVATION_TIME_INVALID")
        matched[event].append(ref)
        times[event].append(observed)
    require(all(matched.values()), "TASK_AUTOMATIC_PRE_POST_HOOKS_NOT_CORRELATED")
    require(max(times["PreToolUse"]) < min(times["PostToolUse"]),
            "TASK_AUTOMATIC_HOOK_EVENT_ORDER_CHANGED")
    return dict(proof, hooks_proven=True, hooks=matched, hook_call_id=proof["call_id"],
                hook_tool_name=DYNAMIC_HOOK_NAME, adoption_proven=False)
