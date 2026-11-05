"""One immutable binding for an explicitly predeclared generated-artifact slot.

This module validates structure and persists evidence in the original operation
ledger. The owner must authenticate the live actor and verify the original
artifact with the corresponding business resolver before binding. A file pin,
this binding, or the fact a producer completed is not execution authority.
"""
from __future__ import annotations

import copy
from orchestration_common import checked_path, json_ref, object_sha, read_ref, require, strict_json

REGISTRATION_SCHEMA = "upgrade-task-operations-v3"
SLOT_SCHEMA = "upgrade-task-artifact-slot-v1"
BINDING_SCHEMA = "upgrade-task-artifact-binding-v1"
RESOLVERS = {
    "campaign-accept-marker-v1": ["marker"],
    "campaign-settle-marker-v1": ["marker"],
    "campaign-advice-sha-v1": ["decision_fields", "advice_sha256"],
}


def _value(payload, path):
    value=payload
    for name in path:
        require(isinstance(value,dict) and name in value, "TASK_SLOT_FIELD_MISSING")
        value=value[name]
    return value


def validate_slots(operations, slots):
    require(isinstance(slots,dict) and slots and set(slots)<=set(operations), "TASK_SLOTS_INVALID")
    for operation, slot in slots.items():
        require(isinstance(slot,dict) and set(slot)=={"schema","resolver","source_path","producer_operation"}
                and slot["schema"]==SLOT_SCHEMA and slot["resolver"] in RESOLVERS,
                "TASK_SLOT_DECLARATION_INVALID")
        require(slot["producer_operation"] in operations and slot["producer_operation"]!=operation
                and slot["producer_operation"] not in slots, "TASK_SLOT_PRODUCER_INVALID")
        require(str(checked_path(slot["source_path"]))==slot["source_path"], "TASK_SLOT_SOURCE_PATH_INVALID")
        require(_value(operations[operation]["payload"],RESOLVERS[slot["resolver"]]) is None,
                "TASK_SLOT_BOUND_FIELD_MUST_START_NULL")
    return slots


def validate_binding(registration, operation_id, ref):
    require(registration.get("schema")==REGISTRATION_SCHEMA, "TASK_SLOT_EXPLICIT_V3_REQUIRED")
    slots=validate_slots(registration["operations"],registration["binding_slots"])
    require(operation_id in slots, "TASK_SLOT_NOT_DECLARED")
    slot=slots[operation_id];value=json_ref(ref)
    require(isinstance(value,dict) and set(value)=={"schema","registration_sha256","operation_id","slot",
            "source","producer_call_id","producer_result","producer_native","replacement"}
            and value["schema"]==BINDING_SCHEMA and value["registration_sha256"]==object_sha(registration)
            and value["operation_id"]==operation_id and value["slot"]==slot,
            "TASK_SLOT_BINDING_CHANGED")
    require(isinstance(value["source"],dict) and value["source"].get("path")==slot["source_path"],
            "TASK_SLOT_SOURCE_CHANGED")
    read_ref(value["source"],"original generated artifact")
    read_ref(value["producer_result"],"original producing operation result")
    require(isinstance(value["producer_native"],dict) and set(value["producer_native"])=={"invocation","fence"}
            and isinstance(value["producer_native"]["fence"],dict), "TASK_SLOT_PRODUCER_NATIVE_REQUIRED")
    read_ref(value["producer_native"]["invocation"], "original producing native invocation")
    require(isinstance(value["producer_call_id"],str) and value["producer_call_id"]
            and isinstance(value["replacement"],str) and 0<len(value["replacement"].encode())<=8192,
            "TASK_SLOT_REPLACEMENT_INVALID")
    if slot["resolver"]=="campaign-advice-sha-v1":
        require(value["replacement"]==value["source"]["sha256"], "TASK_SLOT_ADVICE_SHA_CHANGED")
    resolved=resolve_replacement(registration, operation_id, value["replacement"])
    return value,resolved


def resolve_replacement(registration, operation_id, replacement):
    """Only the declared leaf may differ; this grants no binding or authority."""
    slot=registration["binding_slots"][operation_id]
    require(isinstance(replacement,str) and 0<len(replacement.encode())<=8192,
            "TASK_SLOT_REPLACEMENT_INVALID")
    resolved=copy.deepcopy(registration["operations"][operation_id])
    field=RESOLVERS[slot["resolver"]];parent=resolved["payload"]
    for name in field[:-1]:parent=parent[name]
    parent[field[-1]]=replacement
    return resolved


def validate_resolved(registration, operation_id, operation):
    if operation_id not in registration.get("binding_slots", {}):
        require(operation==registration["operations"][operation_id], "TASK_SLOT_FIXED_OPERATION_CHANGED")
    else:
        slot=registration["binding_slots"][operation_id]
        replacement=_value(operation["payload"],RESOLVERS[slot["resolver"]])
        require(operation==resolve_replacement(registration,operation_id,replacement),
                "TASK_SLOT_RESOLVED_SCOPE_CHANGED")
    return operation


def bound_operation(db, registration, operation_id):
    """Read the original binding and its matching immutable SQLite row."""
    require(registration.get("schema")==REGISTRATION_SCHEMA, "TASK_SLOT_EXPLICIT_V3_REQUIRED")
    row=db.execute("SELECT body FROM bindings WHERE operation_id=?",(operation_id,)).fetchone()
    require(row is not None,"TASK_SLOT_NOT_BOUND")
    ref=strict_json(row[0]);binding,operation=validate_binding(registration,operation_id,ref)
    verify_producer(db, binding)
    events=db.execute("SELECT body FROM events WHERE call_id=? AND kind='SLOT_BOUND'",
                      ("slot:"+operation_id,)).fetchall()
    require(len(events)==1 and strict_json(events[0][0])["binding"]==ref,
            "TASK_SLOT_BINDING_EVENT_CHANGED")
    return operation,ref


def verify_producer(db, binding):
    """A phase label alone cannot replace the original successful result chain."""
    producer=db.execute("SELECT operation_id,phase,receipt,result,request FROM calls WHERE call_id=?",
                        (binding["producer_call_id"],)).fetchone()
    require(producer is not None and producer[0]==binding["slot"]["producer_operation"]
            and producer[1]=="NATIVE_COMPLETED" and strict_json(producer[2])==binding["producer_result"]
            and strict_json(producer[3])["success"] is True, "TASK_SLOT_PRODUCER_NOT_COMPLETED")
    result,receipt,request=map(strict_json, (producer[3],producer[2],producer[4]))
    events={}
    for kind,body in db.execute("SELECT kind,body FROM events WHERE call_id=? ORDER BY sequence",
                                (binding["producer_call_id"],)):
        events.setdefault(kind,[]).append(strict_json(body))
    require(all(len(events.get(k,[]))==1 for k in ("INTENDED","RESULT_RECORDED","RESPONSE_INTENDED","NATIVE_COMPLETED")),
            "TASK_SLOT_PRODUCER_HISTORY_CHANGED")
    require(events["INTENDED"][0]["request"]==request
            and events["RESULT_RECORDED"][0]["result"]==result and events["RESULT_RECORDED"][0]["receipt"]==receipt
            and events["RESPONSE_INTENDED"][0]["response"]=={"id":request["id"],"result":result},
            "TASK_SLOT_PRODUCER_HISTORY_CHANGED")
    completed=events["NATIVE_COMPLETED"][0];native=json_ref(completed["evidence"])
    require(completed["native"]==native and native.get("method")=="item/completed",
            "TASK_SLOT_PRODUCER_COMPLETION_CHANGED")
    params=native.get("params",{});item=params.get("item",{});original=request["params"]
    expected=dict(id=original["callId"],type="dynamicToolCall",namespace=original["namespace"],
                  tool=original["tool"],arguments=original["arguments"],status="completed",**result)
    require(params.get("threadId")==original["threadId"] and params.get("turnId")==original["turnId"]
            and all(item.get(k)==v for k,v in expected.items()) and item.get("success") is True,
            "TASK_SLOT_PRODUCER_COMPLETION_CHANGED")
