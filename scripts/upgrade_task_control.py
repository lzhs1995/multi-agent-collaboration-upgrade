"""Typed native invocation evidence, never foreground or execution authority.

The actor writes this only after its external enrollment gate and durable
operation intent. Readers bind the original native outer call, inner received
RPC and unchanged ledger. Existing campaign identity/adoption gates still apply;
this module cannot authorize a handler, supply a campaign or send any input.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from orchestration_common import (checked_path, count, file_ref, json_ref, object_sha,
                                  parse_time, read_ref, require, strict_json, write_json)

REQUEST_SCHEMA = "upgrade-task-control-request-v1"
EVIDENCE_SCHEMA = "upgrade-task-control-invocation-v1"


def invocation_script(operation_id):
    require(isinstance(operation_id,str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",operation_id),
            "TASK_CONTROL_OPERATION_ID_INVALID")
    return ('const result = await tools.upgrade_collaboration__execute({operation_id:'
            +json.dumps(operation_id)+'}); text(result);')


def control_request(registration_ref, operation_id, evidence_directory):
    """Create a descriptor for readers/instructions, not an execution permit."""
    from upgrade_task_operations import validate_registration
    registration=validate_registration(json_ref(registration_ref))
    require(registration["schema"]=="upgrade-task-operations-v2", "TASK_CONTROL_EXPLICIT_V2_REQUIRED")
    invocation_script(operation_id)
    require(operation_id in registration["operations"], "TASK_CONTROL_UNREGISTERED_OPERATION")
    directory=checked_path(evidence_directory)
    return dict(schema=REQUEST_SCHEMA,registration=registration_ref,operation_id=operation_id,
                evidence_directory=str(directory),script=invocation_script(operation_id))


def _descriptor(value):
    require(isinstance(value,dict) and set(value)=={
        "schema","registration","operation_id","evidence_directory","script"}
        and value["schema"]==REQUEST_SCHEMA, "TASK_CONTROL_REQUEST_INVALID")
    require(value==control_request(value["registration"],value["operation_id"],value["evidence_directory"]),
            "TASK_CONTROL_REQUEST_CHANGED")
    return json_ref(value["registration"])


def _original_rpc(value,registration,operation):
    message=value.get("message",{});params=message.get("params",{})
    require(value.get("direction")=="received" and value.get("peer",{}).get("server")==registration["server"]
            and value["peer"].get("peer")=={k:registration["server"][k] for k in ("pid","uid")}
            and message.get("method")=="item/tool/call" and "id" in message
            and set(params)=={"threadId","turnId","callId","namespace","tool","arguments"}
            and params["threadId"]==registration["thread_id"]
            and params["namespace"]=="upgrade_collaboration" and params["tool"]=="execute"
            and params["arguments"]=={"operation_id":operation}, "TASK_CONTROL_ORIGINAL_RPC_CHANGED")
    return message


def _outer(reader,ref,request,turn):
    record=reader.record(ref);payload=record.get("payload",{})
    registration=json_ref(request["registration"])
    require(reader.provider=="codex" and reader.session["session_id"]==registration["thread_id"]
            and record.get("type")=="response_item" and payload.get("type")=="custom_tool_call"
            and payload.get("name")=="exec" and payload.get("input")==request["script"]
            and payload.get("internal_chat_message_metadata_passthrough",{}).get("turn_id")==turn
            and isinstance(payload.get("call_id"),str) and payload["call_id"],
            "TASK_CONTROL_NATIVE_OUTER_UNBOUND")
    return parse_time(record.get("timestamp")),payload["call_id"]


def _pending_outer(reader,request,turn,after,end):
    after,end=count(after,"typed invocation start"),count(end,"typed invocation end")
    require(after<=end and end-after<=32*1024*1024, "TASK_CONTROL_NATIVE_WINDOW_TOO_LARGE")
    calls=[];returned=set()
    for ref,record in reader.records(after,end):
        _session_consistent(reader,record)
        p=record.get("payload",{})
        if record.get("type")!="response_item":continue
        if p.get("type") in ("custom_tool_call","function_call"):
            calls.append((ref,p))
        elif p.get("type") in ("custom_tool_call_output","function_call_output"):
            returned.add(p.get("call_id"))
    pending=[(ref,p) for ref,p in calls if p.get("call_id") not in returned]
    require(len(pending)==1, "TASK_CONTROL_OUTER_AMBIGUOUS")
    ref,p=pending[0]
    require(sum(item.get("call_id")==p.get("call_id") for _,item in calls)==1,
            "TASK_CONTROL_OUTER_AMBIGUOUS")
    _,outer_id=_outer(reader,ref,request,turn)
    return ref,outer_id


def _session_consistent(reader,record):
    if record.get("type")=="session_meta":
        payload=record.get("payload",{})
        require(isinstance(payload,dict) and all(payload[k]==reader.session["session_id"]
                for k in ("id","session_id") if k in payload), "TASK_CONTROL_NATIVE_SESSION_CHANGED")


def capture_invocation(reader,request,ledger,message,received_ref,directory,*,after):
    """Persist one model invocation after intent, before its fixed handler.

    `reader` must already be authenticated by the owning deployment. Exact
    same-turn outer invocation is required; unknown official shapes fail closed.
    Neither this artifact nor a caller-supplied reader authenticates the actor.
    """
    registration=_descriptor(request)
    require(registration==ledger.registration and object_sha(registration)==ledger.registration_sha256,
            "TASK_CONTROL_REGISTRATION_CHANGED")
    original=_original_rpc(json_ref(received_ref),registration,request["operation_id"])
    require(original==message, "TASK_CONTROL_RECEIVED_MESSAGE_CHANGED")
    call=message["params"]["callId"]
    row=ledger.inspect(call)
    require(row["phase"]=="INTENDED" and row["request"]==message
            and row["operation_id"]==request["operation_id"], "TASK_CONTROL_ORIGINAL_INTENT_REQUIRED")
    directory=checked_path(directory)
    require(str(directory)==request["evidence_directory"] and directory.is_dir(),
            "TASK_CONTROL_EVIDENCE_DIRECTORY_CHANGED")
    fence=reader.snapshot()
    native,outer_id=_pending_outer(reader,request,message["params"]["turnId"],after,fence["offset"])
    st=ledger.path.lstat()
    require((st.st_dev,st.st_ino)==ledger.identity, "TASK_CONTROL_LEDGER_REPLACED")
    value=dict(schema=EVIDENCE_SCHEMA,request=request,native=native,outer_call_id=outer_id,
        inner_call_id=call,received=received_ref,ledger=dict(path=str(ledger.path),device=st.st_dev,inode=st.st_ino),
        thread_id=registration["thread_id"],turn_id=message["params"]["turnId"],
        operation=registration["operations"][request["operation_id"]],
        native_window=dict(after=after,fence=fence),execution_authority=False,business_accepted=False)
    return write_json(directory/(request["operation_id"]+".json"),value,exclusive=True)


def invocation_recorder(reader,registration_ref,directory,*,after):
    """Connect the original actor's pre-effect producer to this native reader.

    This supplies evidence only. TaskActor still invokes its separate live
    enrollment gate before recording intent and again before any response.
    """
    def capture(ledger,message,received_ref):
        request=control_request(registration_ref,message["params"]["arguments"]["operation_id"],directory)
        return capture_invocation(reader,request,ledger,message,received_ref,directory,after=after)
    return capture


def native_invocation(reader,evidence_ref,request,*,allow_intended=False):
    """Read the original intent; execution and native completion are separate.

    Completed/unknown business effects remain the job of existing acceptance
    readers. This reader cannot turn CANCELLED/CONFLICT into successful control.
    """
    registration=_descriptor(request)
    require(evidence_ref["path"]==str(Path(request["evidence_directory"])/(request["operation_id"]+".json")),
            "TASK_CONTROL_ORIGINAL_EVIDENCE_PATH_REQUIRED")
    value=json_ref(evidence_ref)
    require(set(value)=={"schema","request","native","outer_call_id","inner_call_id","received","ledger",
                         "thread_id","turn_id","operation","native_window","execution_authority","business_accepted"}
            and value["schema"]==EVIDENCE_SCHEMA and value["request"]==request
            and value["thread_id"]==registration["thread_id"]
            and value["operation"]==registration["operations"][request["operation_id"]]
            and value["execution_authority"] is False and value["business_accepted"] is False,
            "TASK_CONTROL_EVIDENCE_CHANGED")
    message=_original_rpc(json_ref(value["received"]),registration,request["operation_id"])
    call=value["inner_call_id"]
    require(message["params"]["callId"]==call and message["params"]["turnId"]==value["turn_id"],
            "TASK_CONTROL_INNER_CALL_CHANGED")
    when,outer_id=_outer(reader,value["native"],request,value["turn_id"])
    require(outer_id==value["outer_call_id"], "TASK_CONTROL_OUTER_CALL_CHANGED")
    window=value["native_window"]
    require(set(window)=={"after","fence"}, "TASK_CONTROL_NATIVE_WINDOW_CHANGED")
    end=reader.verify_pin(window["fence"])
    require(_pending_outer(reader,request,value["turn_id"],window["after"],end)==(value["native"],outer_id),
            "TASK_CONTROL_NATIVE_WINDOW_CHANGED")
    path=checked_path(value["ledger"]["path"]);st=path.lstat()
    require(stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode)==0o600
            and st.st_uid==os.geteuid() and st.st_nlink==1
            and (st.st_dev,st.st_ino)==(value["ledger"]["device"],value["ledger"]["inode"]),
            "TASK_CONTROL_LEDGER_REPLACED")
    db=sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
    try:
        db.execute("BEGIN")
        require(strict_json(db.execute("SELECT body FROM registration WHERE id=1").fetchone()[0])==registration,
                "TASK_CONTROL_REGISTRATION_CHANGED")
        row=db.execute("SELECT operation_id,request,phase,result,receipt FROM calls WHERE call_id=?",(call,)).fetchone()
        require(row is not None and row[0]==request["operation_id"] and strict_json(row[1])==message,
                "TASK_CONTROL_LEDGER_CALL_CHANGED")
        phases=("INTENDED","RESULT_RECORDED","RESPONSE_INTENDED","NATIVE_COMPLETED","UNKNOWN") if allow_intended else ("NATIVE_COMPLETED",)
        require(row[2] in phases, "TASK_CONTROL_NATIVE_COMPLETION_REQUIRED")
        events=[(kind,strict_json(body)) for kind,body in db.execute(
            "SELECT kind,body FROM events WHERE call_id=? ORDER BY sequence",(call,))]
        require(events and events[0][0]=="INTENDED" and events[0][1]["request"]==message
                and events[0][1]["operation"]==value["operation"], "TASK_CONTROL_INTENT_CHANGED")
        # This artifact binds one native call, never merely the current turn.
        count=db.execute("SELECT count(*) FROM events WHERE kind='NATIVE_INVOCATION' AND call_id=?",(call,)).fetchone()[0]
        refs=[event["evidence"] for kind,event in events if kind=="NATIVE_INVOCATION"]
        require(count==1 and refs==[evidence_ref], "TASK_CONTROL_LEDGER_PROVENANCE_MISSING")
        completion=None
        if row[2]=="NATIVE_COMPLETED":
            result,receipt=strict_json(row[3]),strict_json(row[4])
            read_ref(receipt,"original operation result receipt")
            result_events=[event for kind,event in events if kind=="RESULT_RECORDED"]
            response_events=[event for kind,event in events if kind=="RESPONSE_INTENDED"]
            completed=[event for kind,event in events if kind=="NATIVE_COMPLETED"]
            require(len(result_events)==len(response_events)==len(completed)==1
                    and result_events[0]["result"]==result and result_events[0]["receipt"]==receipt
                    and response_events[0]["response"]=={"id":message["id"],"result":result},
                    "TASK_CONTROL_RESULT_HISTORY_CHANGED")
            completion=json_ref(completed[0]["evidence"])
            require(completion==completed[0]["native"], "TASK_CONTROL_COMPLETION_CHANGED")
            params=completion.get("params",{});item=params.get("item",{})
            require(completion.get("method")=="item/completed"
                    and params.get("threadId")==value["thread_id"] and params.get("turnId")==value["turn_id"]
                    and item.get("id")==call and item.get("type")=="dynamicToolCall"
                    and item.get("namespace")=="upgrade_collaboration" and item.get("tool")=="execute"
                    and item.get("arguments")==message["params"]["arguments"]
                    and item.get("success")==result["success"] and type(item.get("success")) is bool
                    and item.get("status")==("completed" if result["success"] else "failed")
                    and item.get("contentItems")==result["contentItems"], "TASK_CONTROL_COMPLETION_CHANGED")
    finally:db.close()
    fresh=path.lstat()
    require((fresh.st_dev,fresh.st_ino)==(st.st_dev,st.st_ino), "TASK_CONTROL_LEDGER_REPLACED")
    read_ref(evidence_ref)
    return dict(native=value["native"],at=when,outer_call_id=outer_id,inner_call_id=call,
                operation=value["operation"],phase=row[2],execution_authority=False,
                rpc_completion=completion,model_consumption_proven=False)


def native_result(reader, evidence_ref, request, *, until):
    """Exact RPC completion plus original inner event and outer JSON output.

    None means the original native result is not yet fully visible. Malformed,
    duplicate and contradictory results refuse. This proves returned bytes,
    never semantic model consumption. The caller supplies its original upper
    fence so a later append cannot rewrite an earlier decision window.
    """
    invocation = native_invocation(reader,evidence_ref,request,allow_intended=True)
    if invocation["phase"] != "NATIVE_COMPLETED":
        return None
    start = invocation["native"]["offset"]+invocation["native"]["length"]
    end = count(until,"typed result window end")
    require(start <= end and end-start <= 32*1024*1024, "TASK_CONTROL_RESULT_WINDOW_INVALID")
    original = json_ref(evidence_ref)
    item = invocation["rpc_completion"]["params"]["item"]
    require(len(item["contentItems"]) == 1 and item["contentItems"][0]["type"] == "inputText",
            "TASK_CONTROL_ONE_JSON_RESULT_REQUIRED")
    value = strict_json(item["contentItems"][0]["text"])
    completed, outputs = [], []
    for ref, record in reader.records(start,end):
        _session_consistent(reader,record)
        p=record.get("payload",{})
        if record.get("type")=="response_item":
            require(not (p.get("type") in ("custom_tool_call","function_call")
                         and p.get("call_id")==invocation["outer_call_id"]),
                    "TASK_CONTROL_RESULT_DUPLICATE_CALL")
            if p.get("type") in ("custom_tool_call_output","function_call_output") and p.get("call_id")==invocation["outer_call_id"]:
                content=p.get("output")
                require(p.get("type")=="custom_tool_call_output"
                        and p.get("internal_chat_message_metadata_passthrough",{}).get("turn_id")==original["turn_id"]
                        and isinstance(content,list) and len(content)==2
                        and all(isinstance(x,dict) and set(x)=={"type","text"} and x["type"]=="input_text"
                                and isinstance(x["text"],str) for x in content)
                        and re.fullmatch(r"Script completed\nWall time [0-9.]+ seconds\nOutput:\n",content[0]["text"])
                        and strict_json(content[1]["text"])==value, "TASK_CONTROL_NATIVE_RESULT_CHANGED")
                outputs.append((ref,record))
        if record.get("type")=="event_msg" and p.get("type")=="item_completed" and p.get("item",{}).get("id")==invocation["inner_call_id"]:
            native=p["item"]
            require(p.get("thread_id")==original["thread_id"] and p.get("turn_id")==original["turn_id"]
                    and native.get("type")=="DynamicToolCall"
                    and all(native.get(k)==item[k] for k in ("namespace","tool","arguments","status","success"))
                    and native.get("content_items")==item["contentItems"], "TASK_CONTROL_NATIVE_INNER_CHANGED")
            completed.append((ref,record))
    require(len(completed)<=1 and len(outputs)<=1, "TASK_CONTROL_NATIVE_RESULT_AMBIGUOUS")
    if not completed or not outputs:
        return None
    inner,outer=completed[0],outputs[0]
    require(inner[0]["offset"]<outer[0]["offset"]
            and invocation["at"]<=parse_time(inner[1].get("timestamp"))<=parse_time(outer[1].get("timestamp")),
            "TASK_CONTROL_NATIVE_RESULT_ORDER_CHANGED")
    reader.record(inner[0]);reader.record(outer[0])
    return dict(call_id=invocation["outer_call_id"],call_record=invocation["native"],
        inner_call_id=invocation["inner_call_id"],inner_completion=inner[0],result_record=outer[0],
        call_timestamp=invocation["at"].isoformat(),timestamp=outer[1]["timestamp"],
        value=value,success=item["success"],model_consumption_proven=False)
