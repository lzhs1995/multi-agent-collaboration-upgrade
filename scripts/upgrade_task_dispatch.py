"""One original task-pack submission in the retained typed actor context.

The original transport keeps its locks, intent and delivery budget. This entry
supplies its own exact source to the bridge, never a foreground substitute.
"""
from contextvars import ContextVar
import os
from pathlib import Path

from orchestration_common import file_ref,json_ref,object_sha,read_ref,require,write_json
from upgrade_task_streams import execution_owner

SCHEMA="upgrade-task-dispatch-operation-v1"
_active=ContextVar("upgrade_original_dispatch_operation",default=None)


def _scope(runtime,surface,notice,pack,owner,message,record):
    from orchestration_binding import claim_lane
    from orchestration_coordinator import State
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    campaign=runtime.campaign
    require(owner.campaign==campaign and message.get("method")=="item/tool/call",
            "TASK_DISPATCH_ORIGINAL_CALL_REQUIRED")
    operation=message["params"]["arguments"]["operation_id"];payload=owner.operations[operation]
    require(payload["action"]=="dispatch","TASK_DISPATCH_OPERATION_REQUIRED")
    row=owner.ledger.inspect(message["params"]["callId"])
    require(row["phase"]=="INTENDED" and row["request"]==message,"TASK_DISPATCH_ORIGINAL_INTENT_REQUIRED")
    co=owner.runner.co(payload["run_id"]);co._gate(new_work=True)
    states,data=co.state();node=payload["node_id"];info=data[node]
    require(states[node]==State.DISPATCH_INTENT and info.get("mode")=="delegated_cli",
            "TASK_DISPATCH_ORIGINAL_STATE_REQUIRED")
    lane=claim_lane(co.nodes[node],info);root=co.root/"nodes"/node;prepared=info["prepared"]
    require(surface==campaign["sessions"][lane]["surface_uuid"] and pack==prepared["pack"]["path"]
            and file_ref(pack)==prepared["pack"] and read_ref(prepared["notice"]).decode()==notice,
            "TASK_DISPATCH_EXACT_PACK_NOTICE_TARGET_REQUIRED")
    stage=root/"operations/submit-task-pack";intent=file_ref(stage/"intent.json");value=json_ref(intent)
    args=dict(surface=surface,notice=prepared["notice"],pack=prepared["pack"],reconcile_only=False)
    expected=dict(version=2,campaign=owner.runner.ref,task_id=info["task_id"],
        controller_sha256=object_sha(campaign["controller"]),operation="submit-task-pack",arguments=args)
    require(set(value)==set(expected)|{"created_at"} and all(value[k]==v for k,v in expected.items())
            and not os.path.lexists(stage/"result.json"),"TASK_DISPATCH_ORIGINAL_STAGE_INTENT_REQUIRED")
    dispatch=file_ref(root/"dispatch-intent.json")
    require(dispatch==info["dispatch_intent"] and json_ref(dispatch)["prepared"]==prepared,
            "TASK_DISPATCH_ORIGINAL_BOUNDARY_CHANGED")
    invocation=file_ref(Path(campaign["task_controls"]["evidence_directory"])/(operation+".json"))
    call=json_ref(invocation)
    require(call["received"]==record and call["operation"]["payload"]==payload,
            "TASK_DISPATCH_ORIGINAL_INVOCATION_CHANGED")
    native_invocation(NativeReader(campaign["sessions"]["codex"]),invocation,call["request"],allow_intended=True)
    return dict(schema=SCHEMA,campaign=owner.runner.ref,enrollment=owner.proof,received=record,
        invocation=invocation,stage_intent=intent,dispatch_intent=dispatch,run_id=payload["run_id"],
        node_id=node,task_id=info["task_id"],lane=lane,arguments=args,controller_pid=os.getpid())


def _context(runtime,surface,notice,pack):
    from upgrade_task_enrollment import _active as actor_context,TaskCampaignGate
    context=actor_context.get()
    require(context is not None and type(context[0]) is TaskCampaignGate,"TASK_DISPATCH_LIVE_ACTOR_REQUIRED")
    owner,message,record=context
    owner(owner.registration,message,record)
    return _scope(runtime,surface,notice,pack,owner,message,record)


def current_scope():
    value=_active.get()
    require(value is not None,"TASK_DISPATCH_ACTIVE_OPERATION_REQUIRED")
    runtime,surface,notice,pack,proof,lease=value
    require(lease["active"] and lease["pid"]==os.getpid() and lease["owner"]==execution_owner(),
            "TASK_DISPATCH_OWNER_CHANGED_OR_CLOSED")
    require(_context(runtime,surface,notice,pack)==json_ref(proof),"TASK_DISPATCH_ACTIVE_SCOPE_CHANGED")
    return proof


def submit(runtime,bridge,guard,adapter,surface,notice,pack,*,reconcile_only):
    from orchestration_transport import PinnedRuntime
    from upgrade_task_bridge import activate
    require(type(runtime) is PinnedRuntime and "task_controls" in runtime.campaign,
            "TASK_DISPATCH_ORIGINAL_RUNTIME_REQUIRED")
    require(reconcile_only is False,"TASK_DISPATCH_RECONCILIATION_NEEDS_ORIGINAL_SCOPE")
    require(_active.get() is None,"TASK_DISPATCH_NESTED_OPERATION_REFUSED")
    scope=_context(runtime,surface,notice,pack)
    stage=Path(scope["stage_intent"]["path"]).parent
    proof=write_json(stage/"task-actor.json",scope,exclusive=True)
    lease=dict(active=True,pid=os.getpid(),owner=execution_owner())
    token=_active.set((runtime,surface,notice,pack,proof,lease))
    try:
        with activate(runtime,guard,adapter,current_scope):
            current_scope()
            result=bridge.submit_task_pack(surface,notice,pack,confirm_lines=200,
                                          force_compose=False,reconcile_only=False)
            current_scope()
            return result
    finally:
        lease["active"]=False
        _active.reset(token)


def verify_provenance(campaign,header,node,info,ref,*,fixtures=False):
    """Historical dispatch source, independent of a live actor or node replay."""
    from orchestration_binding import claim_lane
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    value=json_ref(ref);controls=campaign["task_controls"];enrollment=json_ref(value["enrollment"])
    root=Path(campaign["root"])/"runs"/header["run"]["run_id"]/"nodes"/node["id"]
    stage=root/"operations/submit-task-pack";intent=file_ref(stage/"intent.json")
    lane=claim_lane(node,info)
    args=dict(surface=campaign["sessions"][lane]["surface_uuid"],notice=info["prepared"]["notice"],
              pack=info["prepared"]["pack"],reconcile_only=False)
    require(ref["path"]==str(stage/"task-actor.json") and value==dict(schema=SCHEMA,
        campaign=header["campaign"],enrollment=value["enrollment"],received=value["received"],
        invocation=value["invocation"],stage_intent=intent,dispatch_intent=info["dispatch_intent"],
        run_id=header["run"]["run_id"],node_id=node["id"],task_id=info["task_id"],lane=lane,
        arguments=args,controller_pid=enrollment["controller_pid"]),"TASK_DISPATCH_PROVENANCE_CHANGED")
    require(value["enrollment"]["path"]==str(Path(controls["registration"]["path"]).parent/"campaign-enrollment.json")
        and enrollment["schema"]=="upgrade-task-campaign-enrollment-v1"
        and enrollment["campaign"]==header["campaign"] and enrollment["registration"]==controls["registration"]
        and enrollment["authority"]==campaign["authorization"] and enrollment["runtime"]==campaign["runtime"]
        and enrollment["adoption"]==campaign["client_isolation"],"TASK_DISPATCH_ENROLLMENT_CHANGED")
    entry=json_ref(intent);expected=dict(version=2,campaign=header["campaign"],task_id=info["task_id"],
        controller_sha256=object_sha(campaign["controller"]),operation="submit-task-pack",arguments=args)
    require(set(entry)==set(expected)|{"created_at"} and all(entry[k]==v for k,v in expected.items()),
            "TASK_DISPATCH_HISTORICAL_STAGE_CHANGED")
    invocation=json_ref(value["invocation"]);payload=invocation["operation"]["payload"]
    require(invocation["received"]==value["received"] and invocation["ledger"]==enrollment["ledger"]
        and invocation["request"]["registration"]==controls["registration"] and payload["action"]=="dispatch"
        and payload["run_id"]==value["run_id"] and payload["node_id"]==value["node_id"],
        "TASK_DISPATCH_INVOCATION_CHANGED")
    native_invocation(NativeReader(campaign["sessions"]["codex"],fixtures=fixtures),value["invocation"],
        invocation["request"],allow_intended=True)
    return value
