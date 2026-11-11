"""Historical TASK target verification from adopted receiver authority.

Unlike the generated controller helper, this reader derives the original
startup from the receiver's enrolled runtime. A binding cannot choose its own
root or authority. Reading it never authenticates a current sender.
"""
from pathlib import Path

from orchestration_common import file_ref, json_ref, require
from upgrade_task_target_contract import SCHEMA, TARGET_SCHEMA, validate_value


def check_target(binding, receiver):
    schema = binding.get("schema")
    require(schema in ("native-delivery-v1", SCHEMA), "original native binding required")
    if schema == "native-delivery-v1":
        require("task_target" not in binding, "TASK_TARGET_DOWNGRADE_REFUSED")
        if receiver.get("provider") != "codex" or "private_native" not in receiver:
            return
    from upgrade_live_runtime import session_authority, identity_matches
    authority = receiver.get("private_native")
    require(isinstance(authority, dict) and set(authority) == {"runtime", "adoption"},
            "TASK_TARGET_ADOPTED_RECEIVER_REQUIRED")
    _, runtime, adoption = session_authority(authority["runtime"], authority["adoption"], allow_stopped=True)
    require(identity_matches(receiver, adoption["sessions"]["codex"]), "TASK_TARGET_ADOPTED_RECEIVER_CHANGED")
    spec = json_ref(runtime["startup"])
    if schema == "native-delivery-v1":
        require(spec.get("schema") != "upgrade-startup-task-v1", "TASK_TARGET_DOWNGRADE_REFUSED")
        return
    require(spec.get("schema") == "upgrade-startup-task-v1", "TASK_TARGET_ORIGINAL_STARTUP_REQUIRED")
    from upgrade_task_readiness import task_startup_result
    result_ref, result, anchor = task_startup_result(spec)
    launch = file_ref(Path(runtime["state_root"]) / "startup/codex.json")
    require(json_ref(launch).get("startup") == runtime["startup"], "TASK_TARGET_ORIGINAL_LAUNCH_REQUIRED")
    target = dict(schema=TARGET_SCHEMA, startup=runtime["startup"], startup_result=result_ref,
        anchor=result["anchor"], launch=launch, server=anchor["server"], socket=anchor["socket"])
    validate_value(binding, anchor, target)
