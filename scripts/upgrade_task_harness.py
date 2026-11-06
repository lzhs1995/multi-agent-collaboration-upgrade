"""Exact original preparation phases in the retained task actor process.

The operation/phase context is explicit, never environment or foreground
identity. Original native guards remain active; this module alone does not
enable actor-aware sending or TASK launch.
"""
from contextvars import ContextVar
import os
from pathlib import Path

from orchestration_common import file_ref,json_ref,object_sha,read_ref,require,write_json
from orchestration_preparation import HARNESS_STAGES
from upgrade_task_streams import capture,execution_owner

SCHEMA="upgrade-task-harness-operation-v1"
_active=ContextVar("upgrade_original_harness_operation",default=None)


def _scope(runtime,argv,operation_root,owner,message,record):
    from orchestration_binding import claim_lane
    from orchestration_coordinator import State
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    campaign=runtime.campaign
    require(owner.campaign==campaign and message.get("method")=="item/tool/call",
            "TASK_HARNESS_ORIGINAL_CALL_REQUIRED")
    operation=message["params"]["arguments"]["operation_id"]
    payload=owner.operations[operation]
    require(payload["action"]=="prepare","TASK_HARNESS_PREPARE_OPERATION_REQUIRED")
    row=owner.ledger.inspect(message["params"]["callId"])
    require(row["phase"]=="INTENDED" and row["request"]==message,"TASK_HARNESS_ORIGINAL_INTENT_REQUIRED")
    co=owner.runner.co(payload["run_id"]);co._gate()
    states,data=co.state();node=payload["node_id"];info=data[node]
    require(states[node]==State.PREPARING and info.get("mode")=="delegated_cli",
            "TASK_HARNESS_ORIGINAL_PREPARATION_REQUIRED")
    lane=claim_lane(co.nodes[node],info)
    root=co.root/"nodes"/node
    require(isinstance(argv,list) and len(argv)>3 and argv[3] in HARNESS_STAGES,
            "TASK_HARNESS_PHASE_NOT_ALLOWED")
    phase=argv[3]
    require(argv==runtime.harness_argv(phase,info["task_id"],root/"preflight",executor_id=lane)
            and Path(operation_root)==root/"operations"/phase,"TASK_HARNESS_EXACT_ARGUMENTS_REQUIRED")
    intent=file_ref(Path(operation_root)/"intent.json")
    value=json_ref(intent)
    expected=dict(version=2,campaign=owner.runner.ref,task_id=info["task_id"],
        controller_sha256=object_sha(campaign["controller"]),operation=phase,arguments={"argv":argv})
    require(set(value)==set(expected)|{"created_at"} and all(value[k]==v for k,v in expected.items())
            and not os.path.lexists(Path(operation_root)/"result.json"),"TASK_HARNESS_ORIGINAL_STAGE_INTENT_REQUIRED")
    invocation=file_ref(Path(campaign["task_controls"]["evidence_directory"])/(operation+".json"))
    call=json_ref(invocation)
    require(call["received"]==record and call["operation"]["payload"]==payload,
            "TASK_HARNESS_ORIGINAL_INVOCATION_CHANGED")
    native_invocation(NativeReader(campaign["sessions"]["codex"]),invocation,call["request"],allow_intended=True)
    return dict(schema=SCHEMA,campaign=owner.runner.ref,enrollment=owner.proof,received=record,
        invocation=invocation,stage_intent=intent,phase=phase,run_id=payload["run_id"],node_id=node,
        task_id=info["task_id"],lane=lane,argv=argv,controller_pid=os.getpid())


def _context(runtime,argv,operation_root):
    from upgrade_task_enrollment import _active as actor_context,TaskCampaignGate
    context=actor_context.get()
    require(context is not None and type(context[0]) is TaskCampaignGate,"TASK_HARNESS_LIVE_ACTOR_REQUIRED")
    owner,message,record=context
    owner(owner.registration,message,record)
    return _scope(runtime,argv,operation_root,owner,message,record)


def current_scope():
    """Recheck this exact active phase; saved files alone cannot activate it."""
    value=_active.get()
    require(value is not None,"TASK_HARNESS_ACTIVE_PHASE_REQUIRED")
    runtime,argv,root,proof,lease=value
    require(lease["active"] and lease["pid"]==os.getpid() and lease["owner"]==execution_owner(),
            "TASK_HARNESS_PHASE_OWNER_CHANGED_OR_CLOSED")
    require(_context(runtime,argv,root)==json_ref(proof),"TASK_HARNESS_ACTIVE_SCOPE_CHANGED")
    return proof


def invoke(runtime,argv,operation_root):
    from orchestration_transport import PinnedRuntime
    from upgrade_task_bridge import activate as bridge_actor
    require(type(runtime) is PinnedRuntime and "task_controls" in runtime.campaign,
            "TASK_HARNESS_ORIGINAL_RUNTIME_REQUIRED")
    require(_active.get() is None,"TASK_HARNESS_NESTED_PHASE_REFUSED")
    scope=_context(runtime,argv,operation_root)
    proof=write_json(Path(operation_root)/"task-actor.json",scope,exclusive=True)
    stdout,stderr=(Path(operation_root)/(name+".txt") for name in ("stdout","stderr"))
    with runtime.modules("mac_harness","cmux_workspace_guard","mac_upgrade_task_bridge_context") as (harness,guard,adapter):
        require(getattr(harness,"_UPGRADE_TASK_BRIDGE_PARTICIPANTS",False) is True,
                "TASK_HARNESS_PARTICIPANT_CONTRACT_REQUIRED")
        with stdout.open("x",encoding="utf-8") as out,stderr.open("x",encoding="utf-8") as err:
            lease=dict(active=True,pid=os.getpid(),owner=execution_owner())
            token=_active.set((runtime,list(argv),Path(operation_root),proof,lease))
            try:
                with capture(out,err):
                    current_scope()
                    with bridge_actor(runtime,guard,adapter):
                        try:
                            harness.main(argv[3:])
                            code=0
                        except SystemExit as exc:
                            code=exc.code if type(exc.code) is int else 0 if exc.code is None else 1
                            if exc.code is not None and type(exc.code) is not int:print(exc.code,file=err)
                    current_scope()
            finally:
                lease["active"]=False
                _active.reset(token)
                for stream in (out,err):stream.flush();os.fsync(stream.fileno())
    return dict(returncode=code,stdout=file_ref(stdout),stderr=file_ref(stderr),task_actor=proof)


def verify_provenance(campaign,header,node,info,phase,ref,argv,intent,*,fixtures=False):
    """Historical verification only; cannot install an actor or execute a phase."""
    from orchestration_binding import claim_lane
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    require(phase in HARNESS_STAGES,"TASK_HARNESS_PHASE_NOT_ALLOWED")
    value=json_ref(ref);root=Path(campaign["root"])/"runs"/header["run"]["run_id"]/"nodes"/node["id"]
    require(ref["path"]==str(root/"operations"/phase/"task-actor.json"),"TASK_HARNESS_PROVENANCE_PATH_CHANGED")
    controls=campaign["task_controls"];enrollment=json_ref(value["enrollment"])
    require(value["enrollment"]["path"]==str(Path(controls["registration"]["path"]).parent/"campaign-enrollment.json")
        and enrollment["schema"]=="upgrade-task-campaign-enrollment-v1"
        and enrollment["campaign"]==header["campaign"] and enrollment["registration"]==controls["registration"]
        and enrollment["authority"]==campaign["authorization"] and enrollment["runtime"]==campaign["runtime"]
        and enrollment["adoption"]==campaign["client_isolation"],"TASK_HARNESS_ENROLLMENT_CHANGED")
    expected=dict(schema=SCHEMA,campaign=header["campaign"],enrollment=value["enrollment"],
        received=value["received"],invocation=value["invocation"],stage_intent=intent,phase=phase,
        run_id=header["run"]["run_id"],node_id=node["id"],task_id=info["task_id"],
        lane=claim_lane(node,info),argv=argv,controller_pid=enrollment["controller_pid"])
    require(value==expected,"TASK_HARNESS_PROVENANCE_CHANGED")
    invocation=json_ref(value["invocation"]);payload=invocation["operation"]["payload"]
    require(invocation["received"]==value["received"] and invocation["ledger"]==enrollment["ledger"]
        and invocation["request"]["registration"]==controls["registration"]
        and payload["action"]=="prepare" and payload["run_id"]==value["run_id"] and payload["node_id"]==value["node_id"],
        "TASK_HARNESS_INVOCATION_CHANGED")
    native_invocation(NativeReader(campaign["sessions"]["codex"],fixtures=fixtures),value["invocation"],
        invocation["request"],allow_intended=True)
    return value


def verify_prepared_harness(campaign,header,node,info,*,fixtures=False):
    """Every original harness result must carry its actual actor provenance."""
    from orchestration_binding import claim_lane
    from orchestration_common import evidence_names,parse_time
    from orchestration_preparation import preparation_stages
    from orchestration_transport import PinnedRuntime
    runtime=PinnedRuntime(campaign)
    root=Path(campaign["root"])/"runs"/header["run"]["run_id"]/"nodes"/node["id"]
    lane=claim_lane(node,info);previous=parse_time(info["started_at"])
    for phase in preparation_stages(campaign):
        if phase not in HARNESS_STAGES:continue
        stage=root/"operations"/phase;intent=file_ref(stage/"intent.json");entry=json_ref(intent)
        result=json_ref(file_ref(stage/"result.json"))
        argv=runtime.harness_argv(phase,info["task_id"],root/"preflight",executor_id=lane)
        expected=dict(version=2,campaign=header["campaign"],task_id=info["task_id"],
            controller_sha256=object_sha(campaign["controller"]),operation=phase,arguments={"argv":argv})
        require(set(entry)==set(expected)|{"created_at"} and all(entry[k]==v for k,v in expected.items()),
                "TASK_HARNESS_HISTORICAL_STAGE_CHANGED")
        require(set(result)=={"intent","status","value","completed_at"} and result["intent"]==intent
                and result["status"]=="RETURNED","TASK_HARNESS_RETURNED_STAGE_REQUIRED")
        value=result["value"]
        require(set(value)=={"returncode","stdout","stderr","task_actor"}
                and type(value["returncode"]) is int and value["returncode"]==0,"TASK_HARNESS_PROVEN_SUCCESS_REQUIRED")
        verify_provenance(campaign,header,node,info,phase,value["task_actor"],argv,intent,fixtures=fixtures)
        expected_files={"intent.json","result.json","task-actor.json","stdout.txt","stderr.txt"}
        require(evidence_names(stage,expected_files)==expected_files,"TASK_HARNESS_EVIDENCE_FILES_CHANGED")
        for stream in ("stdout","stderr"):
            require(value[stream]["path"]==str(stage/(stream+".txt")),"TASK_HARNESS_STREAM_PATH_CHANGED")
            read_ref(value[stream])
        started,finished=parse_time(entry["created_at"]),parse_time(result["completed_at"])
        require(previous<=started<=finished,"TASK_HARNESS_STAGE_CHRONOLOGY_CHANGED")
        previous=finished
