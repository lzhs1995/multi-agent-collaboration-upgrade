"""Observe one original unknown task dispatch under a typed read operation.

No original sender capability is reactivated. Original locks and native EOF
readers own receipt publication; attempts and their one input stay unchanged.
"""
from contextvars import ContextVar
import os
from pathlib import Path
import re

from orchestration_common import file_ref,json_ref,object_sha,parse_time,read_ref,require,write_json
from upgrade_task_streams import execution_owner

SCHEMA="upgrade-task-reconcile-operation-v1"
_active=ContextVar("upgrade_original_dispatch_observation",default=None)


def _scope(runtime,surface,notice,pack,operation_root,owner,message,record):
    from orchestration_binding import claim_lane
    from orchestration_native import NativeReader,original_attempts
    from orchestration_transport import dispatch_journal
    from upgrade_task_control import native_invocation
    from upgrade_task_dispatch import verify_provenance
    campaign=runtime.campaign
    require(owner.campaign==campaign and message.get("method")=="item/tool/call",
            "TASK_RECONCILE_ORIGINAL_CALL_REQUIRED")
    operation=message["params"]["arguments"]["operation_id"];payload=owner.operations[operation]
    require(payload["action"]=="reconcile","TASK_RECONCILE_OBSERVATION_REQUIRED")
    row=owner.ledger.inspect(message["params"]["callId"])
    require(row["phase"]=="INTENDED" and row["request"]==message,"TASK_RECONCILE_ORIGINAL_INTENT_REQUIRED")
    co=owner.runner.co(payload["run_id"]);co._gate();states,data=co.state();node=payload["node_id"];info=data[node]
    require(states[node].value in ("DISPATCH_INTENT","UNCERTAIN") and info.get("mode")=="delegated_cli",
            "TASK_RECONCILE_ORIGINAL_UNCERTAINTY_REQUIRED")
    lane=claim_lane(co.nodes[node],info);root=co.root/"nodes"/node;prepared=info["prepared"]
    require(surface==campaign["sessions"][lane]["surface_uuid"] and file_ref(pack)==prepared["pack"]
        and read_ref(prepared["notice"]).decode()==notice,"TASK_RECONCILE_PACK_NOTICE_TARGET_CHANGED")
    stage=Path(operation_root)
    require(stage.parent==root/"operations" and re.fullmatch(r"reconcile-task-[0-9]{4}",stage.name),
            "TASK_RECONCILE_ORIGINAL_STAGE_REQUIRED")
    intent=file_ref(stage/"intent.json");entry=json_ref(intent)
    args=dict(pack=prepared["pack"],notice=prepared["notice"],reconcile_only=True)
    expected=dict(version=2,campaign=owner.runner.ref,task_id=info["task_id"],
        controller_sha256=object_sha(campaign["controller"]),operation=stage.name,arguments=args)
    require(set(entry)==set(expected)|{"created_at"} and all(entry[k]==v for k,v in expected.items())
        and not os.path.lexists(stage/"result.json"),"TASK_RECONCILE_ORIGINAL_STAGE_CHANGED")
    dispatch=file_ref(root/"operations/submit-task-pack/task-actor.json")
    verify_provenance(campaign,co.header,co.nodes[node],info,dispatch)
    paths=sorted(dispatch_journal(campaign,info).glob("attempt-*.json"));refs=[file_ref(p) for p in paths]
    attempts=original_attempts(refs)
    require(attempts[-1].get("phase") not in ("PREPARED","NO_INPUT")
        and attempts[-1].get("native_binding",{}).get("task_sender",{}).get("task_actor")==dispatch,
        "TASK_RECONCILE_ORIGINAL_SUBMITTED_ATTEMPT_REQUIRED")
    invocation=file_ref(Path(campaign["task_controls"]["evidence_directory"])/(operation+".json"))
    call=json_ref(invocation)
    require(call["received"]==record and call["operation"]["payload"]==payload,
            "TASK_RECONCILE_ORIGINAL_INVOCATION_CHANGED")
    native=native_invocation(NativeReader(campaign["sessions"]["codex"]),invocation,call["request"],allow_intended=True)
    require(native["at"]<=parse_time(entry["created_at"]),"TASK_RECONCILE_CALL_AFTER_INTENT")
    return dict(schema=SCHEMA,campaign=owner.runner.ref,enrollment=owner.proof,received=record,
        invocation=invocation,stage_intent=intent,original_dispatch=dispatch,attempts=refs,
        run_id=payload["run_id"],node_id=node,task_id=info["task_id"],lane=lane,arguments=args,
        controller_pid=os.getpid(),input_operations=0)


def _context(*args):
    from upgrade_task_enrollment import _active as actor_context,TaskCampaignGate
    value=actor_context.get()
    require(value is not None and type(value[0]) is TaskCampaignGate,"TASK_RECONCILE_LIVE_ACTOR_REQUIRED")
    owner,message,record=value
    owner(owner.registration,message,record)
    return _scope(*args,owner,message,record)


def current_scope():
    value=_active.get()
    require(value is not None,"TASK_RECONCILE_ACTIVE_OPERATION_REQUIRED")
    args,proof,lease=value
    require(lease["active"] and lease["pid"]==os.getpid() and lease["owner"]==execution_owner(),
            "TASK_RECONCILE_OWNER_CHANGED_OR_CLOSED")
    require(_context(*args)==json_ref(proof),"TASK_RECONCILE_ACTIVE_SCOPE_CHANGED")
    return proof


def reconcile(runtime,bridge,guard,adapter,surface,notice,pack,operation_root):
    from orchestration_transport import PinnedRuntime
    from upgrade_task_bridge import activate
    require(type(runtime) is PinnedRuntime and "task_controls" in runtime.campaign,
            "TASK_RECONCILE_ORIGINAL_RUNTIME_REQUIRED")
    require(_active.get() is None,"TASK_RECONCILE_NESTED_OPERATION_REFUSED")
    args=(runtime,surface,notice,pack,operation_root);value=_context(*args)
    proof=write_json(Path(operation_root)/"task-actor.json",value,exclusive=True)
    lease=dict(active=True,pid=os.getpid(),owner=execution_owner());token=_active.set((args,proof,lease))
    try:
        with activate(runtime,guard,adapter,current_scope):
            current_scope()
            result=bridge.submit_task_pack(surface,notice,pack,reconcile_only=True,force_compose=False,
                                          confirm_lines=200,wait_seconds=0)
            current_scope()
            require(result.get("task_reconciler")==proof and result.get("input_operations")==0,
                    "TASK_RECONCILE_RECEIPT_PROVENANCE_REQUIRED")
            return result
    finally:
        lease["active"]=False
        _active.reset(token)


def verify_receipt(receipt,bundle,sender,receiver,*,fixtures=False):
    """Original receipt and original reconciliation source, never execution."""
    from orchestration_native import NativeReader,original_attempts
    from upgrade_task_control import native_invocation
    from upgrade_task_sender_evidence import verify_source
    proof=receipt.get("task_reconciler")
    require(isinstance(proof,dict) and receipt.get("reconciled_read_only") is True
        and type(receipt.get("input_operations")) is int and receipt["input_operations"]==0,
        "TASK_RECONCILE_RECEIPT_SOURCE_REQUIRED")
    value=json_ref(proof);campaign=json_ref(value["campaign"]);controls=campaign["task_controls"]
    enrollment=json_ref(value["enrollment"]);invocation=json_ref(value["invocation"]);payload=invocation["operation"]["payload"]
    stage=Path(proof["path"]).parent
    root=Path(campaign["root"])/"runs"/value["run_id"]/"nodes"/value["node_id"]
    require(set(value)=={"schema","campaign","enrollment","received","invocation","stage_intent",
        "original_dispatch","attempts","run_id","node_id","task_id","lane","arguments","controller_pid","input_operations"}
        and value["schema"]==SCHEMA and stage.parent==root/"operations"
        and re.fullmatch(r"reconcile-task-[0-9]{4}",stage.name) and Path(proof["path"]).name=="task-actor.json"
        and type(value["input_operations"]) is int and value["input_operations"]==0
        and value["controller_pid"]==enrollment["controller_pid"]
        and sender==campaign["sessions"]["codex"] and receiver==campaign["sessions"][value["lane"]],
        "TASK_RECONCILE_ORIGINAL_SCOPE_CHANGED")
    require(enrollment["schema"]=="upgrade-task-campaign-enrollment-v1"
        and value["enrollment"]["path"]==str(Path(controls["registration"]["path"]).parent/"campaign-enrollment.json")
        and enrollment["campaign"]==value["campaign"] and enrollment["registration"]==controls["registration"]
        and enrollment["authority"]==campaign["authorization"] and enrollment["runtime"]==campaign["runtime"]
        and enrollment["adoption"]==campaign["client_isolation"],"TASK_RECONCILE_ENROLLMENT_CHANGED")
    require(payload["action"]=="reconcile" and payload["run_id"]==value["run_id"]
        and payload["campaign_id"]==campaign["campaign_id"] and payload["campaign_root"]==campaign["root"]
        and payload["node_id"]==value["node_id"] and invocation["received"]==value["received"]
        and invocation["ledger"]==enrollment["ledger"] and invocation["request"]["registration"]==controls["registration"]
        and invocation["request"]["evidence_directory"]==controls["evidence_directory"],
        "TASK_RECONCILE_INVOCATION_CHANGED")
    native=native_invocation(NativeReader(sender,fixtures=fixtures),value["invocation"],invocation["request"],allow_intended=True)
    require(value["stage_intent"]==file_ref(stage/"intent.json") and value["attempts"]==bundle["attempts"],
            "TASK_RECONCILE_ORIGINAL_EVIDENCE_CHANGED")
    intent=json_ref(value["stage_intent"]);args=value["arguments"]
    require(set(intent)=={"version","campaign","task_id","controller_sha256","operation","arguments","created_at"}
        and intent["version"]==2 and intent["campaign"]==value["campaign"] and intent["task_id"]==value["task_id"]
        and intent["operation"]==stage.name and intent["controller_sha256"]==object_sha(campaign["controller"])
        and intent["arguments"]==args and set(args)=={"pack","notice","reconcile_only"}
        and args["reconcile_only"] is True,
        "TASK_RECONCILE_ORIGINAL_INTENT_CHANGED")
    old=original_attempts(value["attempts"])[-1];binding=old["native_binding"]
    require(value["original_dispatch"]==file_ref(root/"operations/submit-task-pack/task-actor.json")
        and binding.get("task_sender",{}).get("task_actor")==value["original_dispatch"]
        and receipt["native_binding"]==binding and receipt["attempt"]==value["attempts"][-1]["path"],
        "TASK_RECONCILE_ORIGINAL_DISPATCH_CHANGED")
    dispatch=json_ref(value["original_dispatch"])
    require(all(dispatch[k]==value[k] for k in ("campaign","enrollment","run_id","node_id","task_id","lane","controller_pid"))
        and args["pack"]==dispatch["arguments"]["pack"] and args["notice"]==dispatch["arguments"]["notice"],
        "TASK_RECONCILE_DISPATCH_SCOPE_CHANGED")
    require(parse_time(json_ref(dispatch["stage_intent"])["created_at"])<=native["at"]<=parse_time(intent["created_at"]),
        "TASK_RECONCILE_ORIGINAL_CHRONOLOGY_CHANGED")
    verify_source(binding,sender,receiver,fixtures=fixtures)
