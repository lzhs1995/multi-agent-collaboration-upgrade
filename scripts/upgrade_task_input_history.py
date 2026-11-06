"""Read exact typed task calls when proving no business dispatch occurred.

Operation names alone convey no authority. The registered campaign, original
native invocation and ledger identify each call; this reader never invokes it.
"""
from pathlib import Path

from orchestration_common import file_ref,json_ref,require

# These original handlers cannot dispatch a business task pack. Preparation
# can send its separately verified handshake. A future action is not exempt.
NO_BUSINESS_DISPATCH=frozenset({"start","claim","candidate","accept","block","failure-candidate",
    "settle","publish-decision","bind-unbound","status","recover","finish","verify",
    "advice","read-advice","callback","prepare","disarm","reconcile"})


def verify_unrelated(reader,ref,call,scope):
    from upgrade_task_campaign import validate_controls
    from upgrade_task_control import native_invocation
    require(isinstance(scope,dict) and set(scope)=={"campaign","run_id","node_id"},
            "TASK_INPUT_ORIGINAL_CAMPAIGN_REQUIRED")
    campaign=scope["campaign"];controls=campaign["task_controls"]
    require(reader.session==campaign["sessions"]["codex"],"TASK_INPUT_ORIGINAL_SENDER_REQUIRED")
    _,operations=validate_controls(campaign,check_startup=False)
    operation=call["operation_id"]
    require(operation in operations,"TASK_INPUT_REGISTERED_CAMPAIGN_CALL_REQUIRED")
    evidence=file_ref(Path(controls["evidence_directory"])/(operation+".json"));value=json_ref(evidence)
    native=native_invocation(reader,evidence,value["request"],allow_intended=True)
    require(native["native"]==ref and value["request"]["registration"]==controls["registration"]
        and value["request"]["evidence_directory"]==controls["evidence_directory"]
        and value["request"]["operation_id"]==operation,"TASK_INPUT_ORIGINAL_CALL_CHANGED")
    payload=native["operation"]["payload"];action=payload["action"]
    # Native verification resolves any original marker slot. Scope/action are
    # fixed by the registration, while the marker may have been bound later.
    declared=operations[operation]
    require(all(payload[k]==declared[k] for k in ("action","run_id","node_id","campaign_id","campaign_root")),
            "TASK_INPUT_ORIGINAL_OPERATION_CHANGED")
    require(action in NO_BUSINESS_DISPATCH or action=="dispatch","TASK_INPUT_ACTION_NOT_CLASSIFIED")
    same=payload["run_id"]==scope["run_id"] and payload["node_id"]==scope["node_id"]
    require(action!="dispatch" or not same,"original business pack has an attempted typed dispatch")
    return dict(invocation=evidence,action=action,run_id=payload["run_id"],node_id=payload["node_id"],
                classification="different_task" if action=="dispatch" else "no_business_dispatch",
                execution_authority=False)
