"""Historical TASK sender source from the adopted sender and original RPC."""
from pathlib import Path

from orchestration_common import digest,file_ref,json_ref,object_sha,parse_time,read_ref,require
from upgrade_task_sender import SCHEMA
from upgrade_task_bridge import SOURCE


def verify_source(binding,sender,receiver,*,fixtures=False):
    """No live actor installation or new native input can result from this read."""
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    source=binding.get("task_sender")
    required=False
    if sender.get("provider")=="codex" and "private_native" in sender:
        from upgrade_live_runtime import session_authority,identity_matches
        auth=sender["private_native"]
        _,runtime,adoption=session_authority(auth["runtime"],auth["adoption"],allow_stopped=True)
        require(identity_matches(sender,adoption["sessions"]["codex"]),"TASK_SENDER_ADOPTED_IDENTITY_CHANGED")
        required=json_ref(runtime["startup"]).get("schema")=="upgrade-startup-task-v1"
    require(source is not None or not required,"TASK_SENDER_PROVENANCE_REQUIRED")
    if source is None:return
    require(required or fixtures,"TASK_SENDER_ADOPTED_AUTHORITY_REQUIRED")
    require(set(source)=={"schema","identity_source","task_actor"} and source["schema"]==SCHEMA
            and source["identity_source"]==SOURCE,"TASK_SENDER_SOURCE_CHANGED")
    proof=json_ref(source["task_actor"]);campaign=json_ref(proof["campaign"])
    enrollment=json_ref(proof["enrollment"]);controls=campaign["task_controls"]
    require(sender==campaign["sessions"]["codex"] and receiver==campaign["sessions"][proof["lane"]]
        and (fixtures or sender["private_native"]==dict(runtime=campaign["runtime"],adoption=campaign["client_isolation"]))
        and enrollment["schema"]=="upgrade-task-campaign-enrollment-v1" and enrollment["campaign"]==proof["campaign"]
        and enrollment["registration"]==controls["registration"] and enrollment["authority"]==campaign["authorization"]
        and enrollment["runtime"]==campaign["runtime"] and enrollment["adoption"]==campaign["client_isolation"]
        and proof["enrollment"]["path"]==str(Path(controls["registration"]["path"]).parent/"campaign-enrollment.json")
        and proof["controller_pid"]==enrollment["controller_pid"],"TASK_SENDER_ORIGINAL_ENROLLMENT_CHANGED")
    invocation=json_ref(proof["invocation"]);payload=invocation["operation"]["payload"]
    schema=proof.get("schema")
    action={"upgrade-task-harness-operation-v1":"prepare","upgrade-task-dispatch-operation-v1":"dispatch"}.get(schema)
    require(action is not None and payload["action"]==action and payload["run_id"]==proof["run_id"]
        and payload["node_id"]==proof["node_id"] and payload["campaign_id"]==campaign["campaign_id"]
        and payload["campaign_root"]==campaign["root"] and invocation["received"]==proof["received"]
        and invocation["ledger"]==enrollment["ledger"] and invocation["request"]["registration"]==controls["registration"]
        and invocation["request"]["evidence_directory"]==controls["evidence_directory"],
        "TASK_SENDER_ORIGINAL_INVOCATION_CHANGED")
    native_invocation(NativeReader(sender,fixtures=fixtures),proof["invocation"],invocation["request"],allow_intended=True)
    phase=proof.get("phase") if action=="prepare" else "submit-task-pack"
    require(action!="prepare" or phase=="preflight","TASK_SENDER_PHASE_CHANGED")
    stage=Path(campaign["root"])/"runs"/proof["run_id"]/"nodes"/proof["node_id"]/"operations"/phase
    require(source["task_actor"]["path"]==str(stage/"task-actor.json")
        and proof["stage_intent"]==file_ref(stage/"intent.json"),"TASK_SENDER_ORIGINAL_STAGE_CHANGED")
    intent=json_ref(proof["stage_intent"])
    require(intent["campaign"]==proof["campaign"] and intent["task_id"]==proof["task_id"]
        and intent["controller_sha256"]==object_sha(campaign["controller"]) and intent["operation"]==phase
        and intent["arguments"]==({"argv":proof["argv"]} if action=="prepare" else proof["arguments"])
        and parse_time(intent["created_at"]).timestamp()<=binding["bound_at_epoch"],
        "TASK_SENDER_ORIGINAL_STAGE_CHANGED")
    if action=="dispatch":
        args=proof["arguments"]
        boundary=json_ref(proof["dispatch_intent"])
        require(set(args)=={"surface","notice","pack","reconcile_only"} and args["reconcile_only"] is False
            and args["surface"]==receiver["surface_uuid"]
            and boundary["prepared"]["pack"]==args["pack"] and boundary["prepared"]["notice"]==args["notice"]
            and digest(read_ref(args["notice"]))==binding["payload_sha256"],
            "TASK_SENDER_ORIGINAL_DISPATCH_CHANGED")


def verify_delivery_source(delivery,expected):
    """A valid source from another original operation cannot explain this receipt."""
    binding=delivery["attempt"]["native_binding"]
    require(binding.get("task_sender")==dict(schema=SCHEMA,identity_source=SOURCE,task_actor=expected),
            "TASK_SENDER_DELIVERY_OPERATION_CHANGED")
