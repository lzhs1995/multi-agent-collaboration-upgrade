"""Live campaign identity for the retained explicit task actor.

This adapter owns no model input or launch path. Construction requires the
original production Runner and complete adoption; TASK adoption still refuses
until its independent deployment contract is implemented. A JSON receipt or
environment variable cannot install an active actor context.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import sys

from orchestration_common import file_ref, json_ref, object_sha, read_ref, require, write_json
from upgrade_rpc_peer import verify_peer

SCHEMA="upgrade-task-campaign-enrollment-v1"
IDENTITY_SOURCE="live_kernel_and_original_task_rpc"
_active=ContextVar("upgrade_original_campaign_actor",default=None)


class TaskCampaignGate:
    def __init__(self,runner,client,ledger):
        from orchestration_run import Runner
        from upgrade_task_operations import OperationLedger
        from upgrade_task_campaign import validate_controls
        from upgrade_task_readiness import task_startup_result
        require(type(runner) is Runner and not runner.fixtures and type(ledger) is OperationLedger,
                "TASK_ENROLLMENT_PRODUCTION_OWNER_REQUIRED")
        self.runner,self.client,self.ledger=runner,client,ledger
        self.campaign=runner.campaign
        self.registration,self.operations=validate_controls(self.campaign)
        require(self.registration==ledger.registration and object_sha(self.registration)==ledger.registration_sha256,
                "TASK_ENROLLMENT_REGISTRATION_CHANGED")
        self.runtime=json_ref(self.campaign["runtime"])
        self.spec=json_ref(self.runtime["startup"])
        _,startup,self.anchor=task_startup_result(self.spec)
        require(startup["operations"]==self.campaign["task_controls"]["registration"]
                and startup["ledger"]==str(ledger.path),"TASK_ENROLLMENT_ORIGINAL_LEDGER_REQUIRED")
        self.pending=None
        self.owner_pid=os.getpid()
        self.proof_path=Path(startup["operations"]["path"]).parent/"campaign-enrollment.json"
        sessions=self._observe()
        self.proof=write_json(self.proof_path,dict(schema=SCHEMA,campaign=runner.ref,
            registration=self.campaign["task_controls"]["registration"],
            authority=self.campaign["authorization"],runtime=self.campaign["runtime"],
            adoption=self.campaign["client_isolation"],startup=self.runtime["startup"],
            server=self.anchor["server"],journal=self.anchor["journal"],sessions=sessions,
            controller_pid=self.owner_pid,ledger=dict(path=str(ledger.path),device=ledger.identity[0],inode=ledger.identity[1]),
            foreground_authentication=False,input_operations=0),exclusive=True)

    def _observe(self):
        from orchestration_campaign import (canonical_live_uuids,controller_modules,validate_campaign)
        from upgrade_live_control import external_publisher
        from upgrade_live_runtime import validate_adoption
        from upgrade_task_thread import observe
        require(os.getpid()==self.owner_pid,"TASK_ENROLLMENT_OWNER_PROCESS_CHANGED")
        require(validate_campaign(self.runner.ref)==self.campaign,"TASK_ENROLLMENT_CAMPAIGN_CHANGED")
        external_publisher(self.spec)
        validate_adoption(self.campaign["runtime"],self.campaign["client_isolation"])
        require(not os.path.lexists(self.spec["stop_path"]),"EXPERIMENT_STOP_REQUESTED")
        require(self.client.server==self.anchor["server"]==self.registration["server"],
                "TASK_ENROLLMENT_ORIGINAL_SERVER_REQUIRED")
        verify_peer(self.client.sock,self.client.server,self.client.process)
        proof=observe(self.client,self.anchor,self.client.process)
        require(proof["thread_id"]==self.registration["thread_id"]
                and proof["latest_turn_id"]==self.ledger.active_turn,
                "TASK_ENROLLMENT_ACTIVE_THREAD_OR_TURN_CHANGED")
        st=self.ledger.path.lstat()
        require((st.st_dev,st.st_ino)==self.ledger.identity,"TASK_ENROLLMENT_LEDGER_REPLACED")
        require(json_ref(self.campaign["task_controls"]["registration"])==self.ledger.registration,
                "TASK_ENROLLMENT_REGISTRATION_CHANGED")
        bridge,native,_,_=controller_modules(self.campaign)
        root=str(Path(self.campaign["controller"]["path"])/"scripts")
        sys.path.insert(0,root)
        try:
            observed={}
            for lane,expected in self.campaign["sessions"].items():
                if lane=="codex":
                    row=native._tree_row(bridge,expected["surface_uuid"])
                    process=native._target_process(row)
                    require(native._provider(process)=="codex"
                            and process["argv"][-2:]==["resume",self.registration["thread_id"]],
                            "TASK_ENROLLMENT_TUI_NOT_ATTACHED")
                    actual=dict(provider="codex",session_id=proof["thread_id"],tty=row["tty"],
                        process=native._public_process(process),workspace_uuid=row["workspace_uuid"],
                        surface_uuid=row["target_surface_uuid"],pane_uuid=row["target_pane_uuid"],
                        transcript=dict(self.anchor["journal"]),
                        native_config_root=str(native.private_roots.process_root("codex",process)))
                else:
                    bound=native.bind_target(bridge,expected["surface_uuid"],"TASK_CAMPAIGN_IDENTITY_READ_ONLY")
                    actual={k:bound[k] for k in ("provider","session_id","tty","process","native_config_root")}
                    actual.update(workspace_uuid=bound["identity"]["workspace_uuid"],
                        surface_uuid=bound["identity"]["target_surface_uuid"],pane_uuid=bound["identity"]["target_pane_uuid"],
                        transcript={k:bound["transcript"][k] for k in ("path","device","inode")})
                canonical_live_uuids(actual)
                require(all(expected.get(k)==v for k,v in actual.items()),
                        "TASK_ENROLLMENT_ORIGINAL_LANE_CHANGED:"+lane)
                observed[lane]=actual
            verify_peer(self.client.sock,self.client.server,self.client.process)
            return observed
        finally:
            sys.path.pop(0)

    def __call__(self,registration,message,record):
        self.pending=None
        require(registration==self.registration,"TASK_ENROLLMENT_REGISTRATION_CHANGED")
        read_ref(self.proof,"original actor enrollment")
        value=json_ref(record)
        require(value.get("direction")=="received" and value.get("message")==message
                and value.get("peer",{}).get("server")==self.client.server
                and value["peer"].get("peer")=={k:self.client.server[k] for k in ("pid","uid")},
                "TASK_ENROLLMENT_ORIGINAL_RPC_REQUIRED")
        params=message.get("params",{})
        require(params.get("threadId")==registration["thread_id"]
                and params.get("turnId")==self.ledger.active_turn,"TASK_ENROLLMENT_FOREIGN_THREAD_OR_TURN")
        if message.get("method")=="item/tool/call":
            operation=params.get("arguments",{}).get("operation_id")
        elif message.get("method")=="item/completed":
            row=self.ledger.inspect(params.get("item",{}).get("id"))
            require(row["phase"]=="NATIVE_COMPLETED","TASK_ENROLLMENT_ORIGINAL_COMPLETION_REQUIRED")
            operation=row["operation_id"]
        else:
            require(False,"TASK_ENROLLMENT_UNOWNED_RPC")
        require(operation in self.operations,"TASK_ENROLLMENT_OPERATION_OUTSIDE_SCOPE")
        self._observe()
        self.pending=(message,record)

    @contextmanager
    def activate(self):
        require(self.pending is not None and _active.get() is None,"TASK_ENROLLMENT_CONTEXT_UNAVAILABLE")
        message,record=self.pending
        self(self.registration,message,record)
        token=_active.set((self,message,record))
        try:yield
        finally:_active.reset(token)


def current_identity(campaign):
    context=_active.get()
    require(context is not None and type(context[0]) is TaskCampaignGate,
            "TASK_CAMPAIGN_LIVE_ACTOR_REQUIRED")
    owner,message,record=context
    require(owner.campaign==campaign,"TASK_ENROLLMENT_FOREIGN_CAMPAIGN")
    owner(owner.registration,message,record)
    return owner._observe()


def claim_provenance(campaign):
    context=_active.get()
    require(context is not None and type(context[0]) is TaskCampaignGate
            and context[0].campaign==campaign,"TASK_CAMPAIGN_LIVE_ACTOR_REQUIRED")
    owner,message,record=context
    require(message.get("method")=="item/tool/call","TASK_ENROLLMENT_CLAIM_CALL_REQUIRED")
    operation=message["params"]["arguments"]["operation_id"]
    require(owner.operations[operation]["action"]=="claim","TASK_ENROLLMENT_CLAIM_OPERATION_REQUIRED")
    invocation=file_ref(Path(campaign["task_controls"]["evidence_directory"])/(operation+".json"))
    return dict(enrollment=owner.proof,received=record,invocation=invocation)


def verify_claim_provenance(campaign,campaign_ref,proof,*,run_id,node_id):
    """Read original actor provenance; this historical evidence grants no input."""
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    require(isinstance(proof,dict) and set(proof)=={"enrollment","received","invocation"},
            "TASK_CLAIM_ACTOR_PROOF_REQUIRED")
    enrollment=json_ref(proof["enrollment"])
    require(proof["enrollment"]["path"]==str(Path(campaign["task_controls"]["registration"]["path"]).parent/
            "campaign-enrollment.json"),"TASK_CLAIM_ENROLLMENT_PATH_CHANGED")
    require(enrollment.get("schema")==SCHEMA and enrollment.get("campaign")==campaign_ref
            and enrollment.get("registration")==campaign["task_controls"]["registration"]
            and enrollment.get("authority")==campaign["authorization"]
            and enrollment.get("runtime")==campaign["runtime"]
            and enrollment.get("adoption")==campaign["client_isolation"],"TASK_CLAIM_ENROLLMENT_CHANGED")
    invocation=json_ref(proof["invocation"])
    payload=invocation["operation"]["payload"]
    require(invocation.get("received")==proof["received"] and payload["action"]=="claim"
            and payload["run_id"]==run_id and payload["node_id"]==node_id
            and invocation["request"]["registration"]==enrollment["registration"]
            and enrollment.get("ledger")==invocation.get("ledger"),
            "TASK_CLAIM_ORIGINAL_INVOCATION_CHANGED")
    native_invocation(NativeReader(campaign["sessions"]["codex"]),proof["invocation"],invocation["request"],allow_intended=True)
