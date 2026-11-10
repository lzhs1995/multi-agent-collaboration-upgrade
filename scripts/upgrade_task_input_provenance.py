"""Attribute an exact original TASK dispatch without a second send journal.

The index is written after receipt. Authority comes from the pre-input native
fence, original typed RPC and sender source, never the time of indexing.
"""
from pathlib import Path

from orchestration_common import canonical,digest,json_ref,parse_time,read_ref,require


def validate_transport(value):
    from upgrade_task_operations import validate_registration
    require(set(value)=={"kind","campaign_id","campaign_root","controls"}
        and value["kind"]=="task_dispatch" and isinstance(value["campaign_id"],str)
        and isinstance(value["campaign_root"],str) and Path(value["campaign_root"]).is_absolute(),
        "TASK_INPUT_TRANSPORT_CHANGED")
    controls=value["controls"]
    require(set(controls)=={"registration","evidence_directory"}
        and Path(controls["evidence_directory"])==Path(controls["registration"]["path"]).parent/"campaign-invocations",
        "TASK_INPUT_CONTROLS_CHANGED")
    validate_registration(json_ref(controls["registration"]))
    return value


def validate_campaign_producers(campaign,producers):
    """Require one frozen source before starting a TASK run with input metrics."""
    typed=[p for p in producers.values() if p["transport"]["kind"]=="task_dispatch"]
    if "task_controls" not in campaign:
        require(not typed,"TASK_INPUT_TRANSPORT_REQUIRES_TASK_CAMPAIGN")
        return
    sender=campaign["sessions"]["codex"]
    wanted=dict(kind="task_dispatch",campaign_id=campaign["campaign_id"],campaign_root=campaign["root"],
        controls={k:campaign["task_controls"][k] for k in ("registration","evidence_directory")})
    require(len(typed)==1 and typed[0]["transport"]==wanted
        and typed[0]["session"]=={k:sender[k] for k in ("provider","session_id","transcript")},
        "TASK_INPUT_ONE_REGISTERED_DISPATCH_PRODUCER_REQUIRED")


def publish(transport,bundle):
    from orchestration_campaign import input_provenance_registration
    from upgrade_input_provenance import ProvenanceJournal
    campaign=transport.campaign
    if "task_controls" not in campaign:return
    run=transport.co.spec["run_id"];registration=input_provenance_registration(campaign,run)
    if registration is None:return
    journal=ProvenanceJournal(transport.co.root/"input-provenance.jsonl",run,registration)
    wanted=dict(kind="task_dispatch",campaign_id=campaign["campaign_id"],campaign_root=campaign["root"],
        controls={k:campaign["task_controls"][k] for k in ("registration","evidence_directory")})
    sender=campaign["sessions"]["codex"]
    producers=[p for p in journal.producers.values() if p["transport"]==wanted
        and p["session"]=={k:sender[k] for k in ("provider","session_id","transcript")}]
    require(len(producers)==1,"TASK_INPUT_ONE_REGISTERED_DISPATCH_PRODUCER_REQUIRED")
    return journal.task_delivery(producer_id=producers[0]["id"],lane=transport.lane,bundle=bundle)


def claim_delivery(entry,registration,run_id,readers,units,bounds,window,producers,producer_sessions,*,fixtures=False):
    from orchestration_native import verify_delivery
    from upgrade_input_provenance import record_key
    require(entry.get("kind")=="task_delivery" and set(entry)=={"kind","intent_id","producer","lane","bundle",
        "at","schema","run_id","seq","previous_sha256"},"TASK_INPUT_INDEX_CHANGED")
    ident="typed-dispatch-"+digest(canonical(entry["bundle"]))
    require(entry["intent_id"]==ident,"TASK_INPUT_INDEX_ID_CHANGED")
    producer=producers.get(entry["producer"]["id"])
    require(producer is not None and entry["producer"]==dict(id=producer["id"],kind="lane_agent")
        and producer["transport"]["kind"]=="task_dispatch","TASK_INPUT_REGISTERED_DISPATCH_REQUIRED")
    supplied=producer_sessions.get(producer["id"])
    require(supplied is not None,"TASK_INPUT_PRODUCER_SESSION_REQUIRED")
    sender_reader=supplied["reader"];sender=sender_reader.session
    require(producer["session"]=={k:sender[k] for k in ("provider","session_id","transcript")},
            "TASK_INPUT_PRODUCER_IDENTITY_CHANGED")
    receipt=json_ref(entry["bundle"]["receipt"]);source=receipt["native_binding"]["task_sender"]["task_actor"]
    actor=json_ref(source);campaign=json_ref(actor["campaign"])
    require(actor["schema"]=="upgrade-task-dispatch-operation-v1" and actor["run_id"]==run_id
        and actor["lane"]==entry["lane"] and entry["lane"] in readers
        and campaign["input_provenance"]["registrations"][run_id]==registration
        and producer["transport"]==dict(kind="task_dispatch",campaign_id=campaign["campaign_id"],campaign_root=campaign["root"],
            controls={k:campaign["task_controls"][k] for k in ("registration","evidence_directory")})
        and sender==campaign["sessions"]["codex"] and readers[entry["lane"]].session==campaign["sessions"][entry["lane"]],
        "TASK_INPUT_ORIGINAL_CAMPAIGN_CHANGED")
    delivery=verify_delivery(entry["bundle"],readers[entry["lane"]].session,sender,
        read_ref(actor["arguments"]["notice"]).decode(),fixtures=fixtures)
    invocation=json_ref(actor["invocation"]);call=invocation["native"]
    start=sender_reader.verify_pin(supplied["start"]);end=sender_reader.verify_pin(supplied["end"])
    require(start<=call["offset"] and call["offset"]+call["length"]<=end,"TASK_INPUT_CALL_OUTSIDE_RUN")
    original=sender_reader.record(call)
    key=record_key(delivery["native_proof"]);unit=units.get(key)
    require(unit is not None and unit["lane"]==entry["lane"] and unit["kind"] in ("user","message")
        and unit["payload_sha256"]==receipt["native_binding"]["payload_sha256"],"TASK_INPUT_ORIGINAL_LANDING_REQUIRED")
    at=parse_time(unit["at"]).timestamp();indexed=parse_time(entry["at"]).timestamp()
    require(window[0]<=parse_time(original["timestamp"]).timestamp()<=at<=indexed<=window[1]
        and bounds[entry["lane"]][0]<=unit["ref"]["offset"]<bounds[entry["lane"]][1],
        "TASK_INPUT_LANDING_OUTSIDE_RUN")
    return key,call
