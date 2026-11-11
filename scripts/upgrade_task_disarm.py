"""Original terminal-task release under one retained typed operation.

This context supplies workspace observations, never native send authority.
The original disarm journal and bounded recovery retain all their budgets.
"""
from contextvars import ContextVar
import os
from pathlib import Path

from orchestration_common import file_ref,json_ref,parse_time,require,write_json
from upgrade_task_streams import execution_owner

SCHEMA="upgrade-task-disarm-operation-v1"
_active=ContextVar("upgrade_original_disarm_operation",default=None)


def terminal(transport):
    from orchestration_binding import claim_lane
    from orchestration_coordinator import State
    co=transport.co;states,data=co.state();info=data[transport.key]
    require(states[transport.key] in (State.ACCEPTED,State.FAILED_PRESERVED),
            "TASK_DISARM_VERIFIED_TERMINAL_REQUIRED")
    lane=claim_lane(transport.node,info)
    require(co.campaign["sessions"][lane]["provider"]=="claude","TASK_DISARM_DELEGATED_LANE_REQUIRED")
    event=next(e for e in reversed(co.events()) if e["node_id"]==transport.key)
    ref=file_ref(co.root/"events"/f"{event['seq']:06d}.json")
    lease=co._lease(transport.key,info)
    require(lease["released_at"] is not None and lease["released_by"]==ref,
            "TASK_DISARM_ORIGINAL_RELEASED_LEASE_REQUIRED")
    return info,lane,ref


def _scope(transport,owner,message,record):
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    campaign=transport.campaign
    require(owner.campaign==campaign and message.get("method")=="item/tool/call",
            "TASK_DISARM_ORIGINAL_CALL_REQUIRED")
    operation=message["params"]["arguments"]["operation_id"];payload=owner.operations[operation]
    require(payload["action"]=="disarm" and payload["run_id"]==transport.co.spec["run_id"]
            and payload["node_id"]==transport.key,"TASK_DISARM_EXACT_NODE_REQUIRED")
    row=owner.ledger.inspect(message["params"]["callId"])
    require(row["phase"]=="INTENDED" and row["request"]==message,"TASK_DISARM_ORIGINAL_INTENT_REQUIRED")
    info,lane,ref=terminal(transport)
    invocation=file_ref(Path(campaign["task_controls"]["evidence_directory"])/(operation+".json"))
    call=json_ref(invocation)
    require(call["received"]==record and call["operation"]["payload"]==payload,
            "TASK_DISARM_ORIGINAL_INVOCATION_CHANGED")
    native=native_invocation(NativeReader(campaign["sessions"]["codex"]),invocation,call["request"],allow_intended=True)
    require(parse_time(json_ref(ref)["at"])<=native["at"],"TASK_DISARM_CALL_PRECEDES_TERMINAL")
    return dict(schema=SCHEMA,campaign=owner.runner.ref,enrollment=owner.proof,received=record,
        invocation=invocation,run_id=payload["run_id"],node_id=transport.key,task_id=info["task_id"],
        lane=lane,terminal=ref,controller_pid=os.getpid(),input_operations=0)


def _context(transport):
    from upgrade_task_enrollment import _active as actor_context,TaskCampaignGate
    value=actor_context.get()
    require(value is not None and type(value[0]) is TaskCampaignGate,"TASK_DISARM_LIVE_ACTOR_REQUIRED")
    owner,message,record=value
    owner(owner.registration,message,record)
    return _scope(transport,owner,message,record)


def current_scope():
    value=_active.get()
    require(value is not None,"TASK_DISARM_ACTIVE_OPERATION_REQUIRED")
    transport,proof,lease=value
    require(lease["active"] and lease["pid"]==os.getpid() and lease["owner"]==execution_owner(),
            "TASK_DISARM_OWNER_CHANGED_OR_CLOSED")
    require(_context(transport)==json_ref(proof),"TASK_DISARM_ACTIVE_SCOPE_CHANGED")
    return proof


def run(transport):
    from orchestration_transport import Transport,PinnedRuntime
    from upgrade_task_bridge import activate
    require(type(transport) is Transport and type(transport.runtime) is PinnedRuntime
            and "task_controls" in transport.campaign,"TASK_DISARM_ORIGINAL_RUNTIME_REQUIRED")
    require(_active.get() is None,"TASK_DISARM_NESTED_OPERATION_REFUSED")
    # The caller holds original node locks before publishing the actor source.
    # A previous interrupted call is retained, not granted a second budget.
    scope=_context(transport)
    proof=write_json(transport.root/"disarm-task-actor.json",scope,exclusive=True)
    lease=dict(active=True,pid=os.getpid(),owner=execution_owner())
    token=_active.set((transport,proof,lease))
    try:
        with transport.runtime.modules("cmux_workspace_guard","mac_upgrade_task_bridge_context") as (guard,adapter):
            with activate(transport.runtime,guard,adapter,current_scope):
                current_scope()
                result=transport._disarm_locked()
                current_scope()
                return result
    finally:
        lease["active"]=False
        _active.reset(token)


def verify_provenance(co,key,info,terminal_ref,*,fixtures=False):
    from orchestration_binding import claim_lane
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    campaign=co.campaign;controls=campaign["task_controls"]
    ref=file_ref(co.root/"nodes"/key/"disarm-task-actor.json");value=json_ref(ref)
    enrollment=json_ref(value["enrollment"])
    require(value==dict(schema=SCHEMA,campaign=co.campaign_ref,enrollment=value["enrollment"],
        received=value["received"],invocation=value["invocation"],run_id=co.spec["run_id"],node_id=key,
        task_id=info["task_id"],lane=claim_lane(co.nodes[key],info),terminal=terminal_ref,
        controller_pid=enrollment["controller_pid"],input_operations=0),"TASK_DISARM_PROVENANCE_CHANGED")
    require(value["enrollment"]["path"]==str(Path(controls["registration"]["path"]).parent/"campaign-enrollment.json")
        and enrollment["schema"]=="upgrade-task-campaign-enrollment-v1"
        and enrollment["campaign"]==co.campaign_ref and enrollment["registration"]==controls["registration"]
        and enrollment["authority"]==campaign["authorization"] and enrollment["runtime"]==campaign["runtime"]
        and enrollment["adoption"]==campaign["client_isolation"],"TASK_DISARM_ENROLLMENT_CHANGED")
    invocation=json_ref(value["invocation"]);payload=invocation["operation"]["payload"]
    require(invocation["received"]==value["received"] and invocation["ledger"]==enrollment["ledger"]
        and invocation["request"]["registration"]==controls["registration"] and payload["action"]=="disarm"
        and payload["run_id"]==value["run_id"] and payload["node_id"]==key,"TASK_DISARM_INVOCATION_CHANGED")
    native=native_invocation(NativeReader(campaign["sessions"]["codex"],fixtures=fixtures),value["invocation"],
        invocation["request"],allow_intended=True)
    require(parse_time(json_ref(terminal_ref)["at"])<=native["at"],"TASK_DISARM_CALL_PRECEDES_TERMINAL")
    intent_path=co.root/"nodes"/key/"operations/scoped-disarm/intent.json"
    require(native["at"]<=parse_time(json_ref(file_ref(intent_path))["created_at"]),
            "TASK_DISARM_CALL_FOLLOWS_ORIGINAL_INTENT")
    return ref


def verify_marker(co,key,info,frozen,*,fixtures=False):
    from orchestration_binding import claim_lane
    from orchestration_disarm import snapshot_value
    from orchestration_transport import PinnedRuntime
    from upgrade_task_bridge_context import SOURCE
    from upgrade_task_harness import verify_provenance as verify_preflight
    root=co.root/"nodes"/key;lane=claim_lane(co.nodes[key],info);campaign=co.campaign
    stage=root/"operations/preflight";actor=file_ref(stage/"task-actor.json")
    argv=PinnedRuntime(campaign).harness_argv("preflight",info["task_id"],root/"preflight",executor_id=lane)
    verify_preflight(campaign,co.header,co.nodes[key],info,"preflight",actor,argv,file_ref(stage/"intent.json"),fixtures=fixtures)
    marker=snapshot_value(frozen["marker"],frozen["marker"]["path"])
    require(marker.get("task_actor")==actor and marker.get("identity_source")==SOURCE,
            "TASK_DISARM_ORIGINAL_MARKER_SOURCE_CHANGED")
    expected={(role,s["provider"],s["surface_uuid"].upper(),s["session_id"])
              for role,s in (("supervisor",campaign["sessions"]["codex"]),("executor",campaign["sessions"][lane]))}
    actual={(p["role"],p["provider"],p["surface_uuid"].upper(),p.get("native_session_id")) for p in marker["participants"]}
    require(len(marker["participants"])==2 and actual==expected and marker["artifact_root"]==str(root/"preflight"),
            "TASK_DISARM_ORIGINAL_MARKER_PARTICIPANTS_CHANGED")


def check_frozen(runtime,frozen):
    value=_active.get()
    require(value is not None and value[0].runtime is runtime,"TASK_DISARM_ORIGINAL_RUNTIME_REQUIRED")
    transport=value[0];current_scope();info,_,_=terminal(transport)
    verify_marker(transport.co,transport.key,info,frozen)
