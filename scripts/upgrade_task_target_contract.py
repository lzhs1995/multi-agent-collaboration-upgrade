"""Shared static TASK target contract; no runtime RPC or caller authority."""
import uuid
from orchestration_common import json_ref, require

SCHEMA = "native-delivery-task-v1"
TARGET_SCHEMA = "upgrade-task-native-target-v1"


def validate_value(binding, anchor, target):
    """Callers independently derive sources from their original authority."""
    require(binding.get("schema") == SCHEMA and binding.get("provider") == "codex",
            "TASK_TARGET_EXPLICIT_BINDING_REQUIRED")
    require(binding.get("task_target") == target
            and binding.get("session_id") == anchor["thread_id"]
            and binding.get("native_config_root") == anchor["native_root"]
            and all(binding.get("transcript", {}).get(k) == anchor["journal"][k]
                    for k in ("path", "device", "inode"))
            and binding.get("process", {}).get("executable") == anchor["server"]["executable"],
            "TASK_TARGET_ORIGINAL_BINDING_CHANGED")
    launch = json_ref(target["launch"])
    require(binding["process"]["pid"] == launch["pid"] and binding["process"]["birth"] == launch["birth"]
            and str(uuid.UUID(binding["identity"]["target_surface_uuid"])) == str(uuid.UUID(launch["surface_uuid"]))
            and str(uuid.UUID(binding["identity"]["workspace_uuid"])) == str(uuid.UUID(launch["workspace_uuid"])),
            "TASK_TARGET_ORIGINAL_DISPLAY_CHANGED")
