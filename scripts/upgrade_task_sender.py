"""Recorded TASK sender source for generated native delivery bindings.

The original binding identity remains unchanged. This separate provenance
field survives target binding; files alone never authorize a live sender.
"""
from pathlib import Path

from orchestration_common import file_ref,json_ref,require
from upgrade_native_roots import configuration
from upgrade_task_bridge_context import SOURCE,snapshot

SCHEMA="upgrade-task-native-sender-v1"


def _task_caller(binding):
    original,_=configuration()
    if "task_startup" not in original:return False
    launch=json_ref(file_ref(Path(original["state_root"])/"startup/codex.json"))
    startup=file_ref(Path(original["config_root"])/"startup-v2.json")
    require(launch.get("startup")==startup,"TASK_SENDER_ORIGINAL_LAUNCH_REQUIRED")
    return binding["identity"]["caller_surface_uuid"].upper()==launch["surface_uuid"].upper()


def validate(binding):
    source=binding.get("task_sender")
    required=_task_caller(binding)
    require((source is not None)==required,"TASK_SENDER_DOWNGRADE_OR_FOREIGN_SOURCE")
    if not required:return
    require(isinstance(source,dict) and set(source)=={"schema","identity_source","task_actor"}
            and source["schema"]==SCHEMA and source["identity_source"]==SOURCE,
            "TASK_SENDER_SOURCE_CHANGED")
    proof=json_ref(source["task_actor"])
    require(proof.get("schema") in ("upgrade-task-harness-operation-v1","upgrade-task-dispatch-operation-v1"),
            "TASK_SENDER_ORIGINAL_OPERATION_REQUIRED")
    require(proof["schema"]!="upgrade-task-harness-operation-v1" or proof.get("phase")=="preflight",
            "TASK_SENDER_PHASE_CANNOT_SEND")
    enrollment=json_ref(proof["enrollment"]);campaign=json_ref(proof["campaign"])
    require(enrollment.get("campaign")==proof["campaign"] and proof["controller_pid"]==enrollment["controller_pid"]
            and campaign["sessions"]["codex"]["surface_uuid"].upper()==binding["identity"]["caller_surface_uuid"].upper()
            and campaign["sessions"][proof["lane"]]["surface_uuid"].upper()==binding["identity"]["target_surface_uuid"].upper(),
            "TASK_SENDER_ORIGINAL_PARTICIPANTS_CHANGED")


def bind(binding,live):
    value=snapshot()
    if value is not None:
        require(live.get("task_actor")==value["task_actor"] and live.get("identity_source")==SOURCE,
                "TASK_SENDER_CURRENT_SOURCE_CHANGED")
        binding["task_sender"]=dict(schema=SCHEMA,identity_source=SOURCE,task_actor=value["task_actor"])
    validate(binding)


def require_live(binding):
    validate(binding)
    value=snapshot()
    source=binding.get("task_sender")
    if source is None:
        require(value is None,"TASK_SENDER_CURRENT_SOURCE_MISSING")
        return
    require(value is not None and value["task_actor"]==source["task_actor"],
            "TASK_SENDER_ACTIVE_OPERATION_REQUIRED")


def check(binding,*,read_only=False):
    if read_only:validate(binding)
    else:require_live(binding)
