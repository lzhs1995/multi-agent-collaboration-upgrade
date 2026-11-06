"""Original actor observer passed explicitly into the generated bridge API."""
from contextlib import contextmanager
from pathlib import Path

from orchestration_common import json_ref,require
from upgrade_task_harness import current_scope
from upgrade_task_enrollment import _active,TaskCampaignGate

SOURCE="original_task_rpc_and_live_tree"


def caller_in_tree(tree,session):
    rows=[]
    for window in tree.get("windows",[]):
        for ws in window.get("workspaces",[]):
            for pane in ws.get("panes",[]):
                for surface in pane.get("surfaces",[]):
                    if surface.get("id","").upper()==session["surface_uuid"].upper():
                        require(ws["id"].upper()==session["workspace_uuid"].upper()
                                and pane["id"].upper()==session["pane_uuid"].upper()
                                and surface.get("type")=="terminal" and surface.get("dock_scope")!="global",
                                "TASK_BRIDGE_DISPLAY_CHANGED")
                        rows.append(dict(workspace_ref=ws["ref"],surface_ref=surface["ref"],
                            surface_type="terminal",pane_ref=pane["ref"],window_ref=window["ref"],
                            workspace_id=ws["id"],surface_id=surface["id"]))
    require(len(rows)==1,"TASK_BRIDGE_DISPLAY_MISSING_OR_AMBIGUOUS")
    return rows[0]


def observe(runtime,guard):
    proof=current_scope();scope=json_ref(proof)
    context=_active.get()
    require(context is not None and type(context[0]) is TaskCampaignGate,
            "TASK_BRIDGE_ORIGINAL_ACTOR_REQUIRED")
    owner=context[0]
    require(owner.campaign==runtime.campaign and scope["campaign"]==owner.runner.ref,
            "TASK_BRIDGE_ORIGINAL_CAMPAIGN_REQUIRED")
    tree=guard._read_json_command("tree","--all","--json","--id-format","both")
    sender=owner.campaign["sessions"]["codex"];target=owner.campaign["sessions"][scope["lane"]]
    caller=caller_in_tree(tree,sender)
    caller_in_tree(tree,target)
    # Rechecking the scope rechecks live kernel/RPC/adoption/STOP after the tree
    # read. Sequential observations do not claim atomic exclusion of user input.
    require(current_scope()==proof,"TASK_BRIDGE_OPERATION_CHANGED_DURING_OBSERVATION")
    return dict(identity_source=SOURCE,foreground_authentication=False,task_actor=proof,
                caller=caller,tree=tree,supervisor=sender,target=target)


@contextmanager
def activate(runtime,guard,adapter):
    require(Path(guard.__file__).resolve().parent==runtime.scripts
            and Path(adapter.__file__).resolve().parent==runtime.scripts,
            "TASK_BRIDGE_MIXED_CONTROLLER")
    with adapter.activate(lambda:observe(runtime,guard)):
        yield


def verify_gate(campaign,header,node,info,gate,*,fixtures=False):
    """Read the exact preflight actor source; this never activates a bridge."""
    from orchestration_common import file_ref
    from orchestration_transport import PinnedRuntime
    from orchestration_binding import claim_lane
    from upgrade_task_harness import verify_provenance
    root=Path(campaign["root"])/"runs"/header["run"]["run_id"]/"nodes"/node["id"]
    stage=root/"operations/preflight"
    proof=file_ref(stage/"task-actor.json")
    argv=PinnedRuntime(campaign).harness_argv("preflight",info["task_id"],root/"preflight",
        executor_id=claim_lane(node,info))
    verify_provenance(campaign,header,node,info,"preflight",proof,argv,file_ref(stage/"intent.json"),fixtures=fixtures)
    require(gate.get("task_actor")==proof and gate.get("identity_source")==SOURCE,
            "TASK_BRIDGE_GATE_PROVENANCE_CHANGED")
    participants=gate.get("native_participants");proofs=gate.get("workspace_proofs")
    require(isinstance(participants,list) and len(participants)==2
            and isinstance(proofs,dict) and len(proofs)==1,"TASK_BRIDGE_GATE_MEMBERS_CHANGED")
    for value in [*participants,*proofs.values()]:
        require(value.get("task_actor")==proof and value.get("identity_source")==SOURCE,
                "TASK_BRIDGE_MEMBER_PROVENANCE_CHANGED")
