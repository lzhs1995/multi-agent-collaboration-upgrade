"""Explicit v3 campaign advice with original worker budgets and native reads.

Only the trusted retained controller supplies in-memory on-arm connections.
The existing worker runs inside the original scoped actor context; there is no
new broker, socket or ambient credential lookup. This explicit mode adds the
worker to the new adapter's frozen dependency closure. Old frozen adapters and
their separately owned worker processes are never migrated.
"""
from pathlib import Path
from copy import deepcopy
from hashlib import sha256

from orchestration_common import file_ref,json_ref,parse_time,require
from upgrade_task_control import control_request,native_result
from upgrade_task_bindings import receipt_advice_sha

RESOLVER="campaign-advice-receipt-sha-v1"
READ_SCHEMA="upgrade-task-advice-read-v1"


def _operations(registration,action,run,node):
    return [(k,v["payload"]) for k,v in registration["operations"].items()
        if v["kind"]=="campaign-control" and v["payload"].get("action")==action
        and v["payload"].get("run_id")==run and v["payload"].get("node_id")==node]


def one_operation(registration,action,run,node):
    found=_operations(registration,action,run,node)
    require(len(found)==1,"TASK_ADVICE_ONE_ORIGINAL_OPERATION_REQUIRED:"+action)
    return found[0]


def receipt_path(campaign,operation):
    return Path(campaign["task_controls"]["registration"]["path"]).parent/"campaign-results"/(operation+".json")


def validate_advice_slot(campaign,registration,name):
    from upgrade_task_campaign import ADVICE_SCHEMA
    require(campaign["task_controls"]["schema"]==ADVICE_SCHEMA,"TASK_CAMPAIGN_ADVICE_OPT_IN_REQUIRED")
    value=registration["operations"][name]["payload"];slot=registration["binding_slots"][name]
    require(value["action"]=="publish-decision","TASK_ADVICE_DECISION_SLOT_REQUIRED")
    producer,_=one_operation(registration,"read-advice",value["run_id"],value["node_id"])
    require(slot["producer_operation"]==producer and slot["source_path"]==str(receipt_path(campaign,producer)),
            "TASK_ADVICE_ORIGINAL_READ_RECEIPT_REQUIRED")


def validate_advice_controls(campaign,registration,operations):
    from jev_registration import load
    keys={(p["run_id"],p["node_id"]) for p in operations.values()
          if p["action"] in ("advice","read-advice","publish-decision")}
    require(keys,"TASK_ADVICE_NO_REGISTERED_NODES")
    for run,node in keys:
        _,producer=one_operation(registration,"advice",run,node)
        reg=load(producer["advisory_registration"],run_id=run,node_id=node)
        reads=_operations(registration,"read-advice",run,node)
        decisions=_operations(registration,"publish-decision",run,node)
        require(decisions,"TASK_ADVICE_NO_MODEL_DECISIONS")
        on=reg["arm"]=="advisory-on"
        require(len(reads)==(1 if on else 0),"TASK_ADVICE_ARM_READ_SCOPE_CHANGED")
        for name,p in decisions:
            slot=registration.get("binding_slots",{}).get(name)
            if on:require(slot is not None and slot["resolver"]==RESOLVER,"TASK_ADVICE_MODEL_CHOICE_NOT_BOUND_TO_READ")
            else:
                from orchestration_campaign import NO_ADVICE
                require(slot is None and p["decision_fields"]["advice_sha256"]==NO_ADVICE,
                        "TASK_ADVICE_OFF_ARM_DECISION_CHANGED")


def completed_result(campaign,campaign_ref,registration,name,reader,*,until):
    """Require the exact original producer and its full model-visible return."""
    controls=campaign["task_controls"]
    request=control_request(controls["registration"],name,controls["evidence_directory"])
    invocation=file_ref(Path(controls["evidence_directory"])/(name+".json"))
    result=native_result(reader,invocation,request,until=until)
    require(result is not None and result["success"],"TASK_ADVICE_ORIGINAL_RESULT_PENDING_OR_FAILED")
    ref=file_ref(receipt_path(campaign,name));receipt=json_ref(ref)
    require(receipt.get("schema")=="upgrade-task-campaign-result-v1"
            and receipt.get("campaign")==campaign_ref and receipt.get("request")==request
            and receipt.get("operation")==registration["operations"][name]["payload"]
            and receipt.get("result")==result["value"],"TASK_ADVICE_ORIGINAL_RECEIPT_CHANGED")
    return ref,result


def advice_handler(runner,registration,connections):
    from upgrade_task_campaign import ADVICE_SCHEMA
    selected=runner.campaign["task_controls"]["schema"]==ADVICE_SCHEMA
    require(connections is None or selected,"TASK_ADVICE_CONNECTION_WITHOUT_OPT_IN")
    if not selected:return None
    from jev_registration import load
    producers={name:p for name,v in registration["operations"].items()
               if v["kind"]=="campaign-control" and (p:=v["payload"])["action"]=="advice"}
    on={name for name,p in producers.items() if load(p["advisory_registration"],
        run_id=p["run_id"],node_id=p["node_id"])["arm"]=="advisory-on"}
    held={} if connections is None else connections
    require(isinstance(held,dict) and set(held)==on,"TASK_ADVICE_EXACT_HELD_CONNECTIONS_REQUIRED")
    run_owners={};retained={}
    for name,value in held.items():
        require(isinstance(value,dict) and set(value)=={"registration","connection","credential"}
                and value["registration"]==producers[name]["advisory_registration"]
                and value["connection"] is not None and isinstance(value["credential"],str)
                and value["credential"],"TASK_ADVICE_HELD_CAPABILITY_CHANGED")
        producer=producers[name]
        reg=load(producer["advisory_registration"],run_id=producer["run_id"],node_id=producer["node_id"])
        owner=(value["connection"],value["credential"],reg["transport_source"],reg["provider"],reg["model"])
        prior=run_owners.setdefault(producer["run_id"],owner)
        require(prior[0] is owner[0] and prior[1:]==owner[1:],"TASK_ADVICE_ORIGINAL_RUN_CONNECTION_REQUIRED")
        # Keep the original holder/connection, but no second plaintext key in
        # the closure. Later owner mutation must fail before worker loading or
        # request intent; equality alone cannot pin a connection's identity.
        retained[name]=(value,value["connection"],sha256(value["credential"].encode()).digest(),
                        deepcopy(value["registration"]))

    def capability_for(name):
        if name not in on:return None,None
        require(name in retained,"TASK_ADVICE_HELD_CAPABILITY_UNAVAILABLE")
        original,connection,key_digest,registration_ref=retained[name]
        capability=held.get(name)
        require(capability is original and set(capability)=={"registration","connection","credential"},
                "TASK_ADVICE_HELD_CAPABILITY_SUBSTITUTED")
        credential=capability["credential"]
        require(capability["registration"]==registration_ref and capability["connection"] is connection
                and isinstance(credential,str) and sha256(credential.encode()).digest()==key_digest,
                "TASK_ADVICE_HELD_CAPABILITY_SUBSTITUTED")
        return connection,credential

    def release_capabilities():
        # Clear the original holders even if the caller replaced its mapping
        # while a request was in flight. Then release retained connection refs.
        for original,_,_,_ in retained.values():original.update(connection=None,credential=None)
        retained.clear()

    def handle(payload):
        from orchestration_native import NativeReader
        run,node=payload["run_id"],payload["node_id"]
        name,producer=one_operation(registration,"advice",run,node)
        co=runner.co(run)
        if payload["action"] in ("advice", "semantic-order"):
            # Same-process scoped gate is retained by the campaign handler.
            # Credentials never enter registration, operation or result files.
            from jev_advice_run import record_advice,STOP_REASON
            from orchestration_common import ContractError
            connection,credential=capability_for(name)
            try:
                if payload['action'] == 'semantic-order':
                    from jev_advice_run import record_inbox_order
                    return record_inbox_order(co, node, 'semantic-' + run, None,
                        registration=producer['advisory_registration'], connection=connection, credential=credential)
                return record_advice(co,node,registration=producer["advisory_registration"],
                    connection=connection,credential=credential)
            except ContractError as exc:
                if str(exc)==STOP_REASON:
                    release_capabilities()
                raise
        require(payload["action"]=="read-advice","TASK_ADVICE_UNKNOWN_HANDLER_ACTION")
        from jev_registration import registered_arm
        require(registered_arm(co.root,run,node)[0]=="advisory-on","TASK_ADVICE_OFF_ARM_READ_REFUSED")
        reader=NativeReader(runner.campaign["sessions"]["codex"],fixtures=runner.fixtures)
        ref,result=completed_result(runner.campaign,runner.ref,registration,name,reader,
            until=reader.snapshot(complete_records=True)["offset"])
        original=result["value"]
        with co.node_locks(node):
            co._gate()
            require(original.get("schema")=="jev-worker-advice-record-v1" and original.get("run_id")==run
                    and original.get("node_id")==node and original.get("arm")=="advisory-on"
                    and original.get("registration")==producer["advisory_registration"],
                    "TASK_ADVICE_ORIGINAL_PUBLICATION_CHANGED")
            advice,_=co._bind_advice(node,dict(advice_sha256=original["advice_sha256"]))
            require(advice["notice"]==original.get("notice") and advice["worker_status"]==original["worker_status"],
                    "TASK_ADVICE_ORIGINAL_NOTICE_CHANGED")
            from upgrade_body_read import read_result
            body=read_result(original["notice"])
            publication=file_ref(body["reference"]["path"])
            require(publication["sha256"]==original["advice_sha256"],"TASK_ADVICE_BODY_SHA_CHANGED")
            return dict(schema=READ_SCHEMA,producer=ref,publication=publication,body_read=body)
    return handle


def read_control(campaign,run,node):
    from upgrade_task_campaign import validate_controls
    registration,_=validate_controls(campaign,check_startup=False)
    name,_=one_operation(registration,"read-advice",run,node)
    c=campaign["task_controls"]
    return control_request(c["registration"],name,c["evidence_directory"])


def typed_read_evidence(reader,*,fence,until,notice,command,not_before,not_after):
    from upgrade_body_read import read_result,SCOPE
    from orchestration_campaign import find_control
    start=reader.verify_pin(fence) if isinstance(fence,dict) else fence
    ref=find_control(reader,command,after=start)
    result=native_result(reader,ref["task_invocation"],command,until=until)
    if result is None:return []
    require(result["success"] and result["value"].get("schema")==READ_SCHEMA
            and result["value"].get("body_read")==read_result(notice),"TASK_ADVICE_NATIVE_FULL_BODY_REQUIRED")
    call_at=parse_time(result["call_timestamp"]).timestamp();at=parse_time(result["timestamp"]).timestamp()
    require(call_at>=not_before-0.001 and (not_after is None or at<=not_after),"TASK_ADVICE_READ_TIME_OUTSIDE_WINDOW")
    return [dict(kind="BODY_READ_OBSERVED",scope=SCOPE,session_id=reader.session["session_id"],
        **{k:result[k] for k in ("call_id","call_record","result_record","call_timestamp","timestamp")},
        reference=result["value"]["body_read"]["reference"])]


def typed_read_attempts(campaign,records):
    """Off-arm unknown dynamic calls cannot silently count as zero reads."""
    from upgrade_task_control import invocation_script
    registration=json_ref(campaign["task_controls"]["registration"])
    known={invocation_script(k):v for k,v in registration["operations"].items()}
    found=[]
    for ref,record in records:
        p=record.get("payload",{})
        if record.get("type")!="response_item" or p.get("type")!="custom_tool_call":continue
        text=p.get("input","")
        if not isinstance(text,str) or "upgrade_collaboration__execute" not in text:continue
        require(text in known,"ADVICE_ARM_TYPED_INPUT_UNKNOWN")
        operation=known[text]
        if operation["kind"]=="campaign-control" and operation["payload"].get("action")=="read-advice":
            found.append(dict(kind="ADVICE_READ_ATTEMPT",call_id=p.get("call_id"),name=p.get("name"),call_record=ref))
    return found
