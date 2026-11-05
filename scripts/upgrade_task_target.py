"""Explicit logical Codex targets for newly generated TASK controllers.

The displayed TUI and journal-writing server are different roles. This module
selects only the startup's original task, validates both roles and reuses the
original native delivery journal. It grants no caller or input authority.
"""
from pathlib import Path
import uuid

from orchestration_common import file_ref, json_ref, require
from upgrade_native_roots import configuration, process_root
from upgrade_rpc_peer import verify_peer
from upgrade_task_readiness import task_startup_result
from upgrade_task_thread import observe, socket_evidence, validate_tui_target
from upgrade_task_target_contract import SCHEMA, TARGET_SCHEMA, validate_value


def _source():
    binding, _ = configuration()
    if "task_startup" not in binding:
        return None
    startup = file_ref(Path(binding["config_root"]) / "startup-v2.json")
    spec = json_ref(startup)
    require(spec.get("schema") == "upgrade-startup-task-v1"
            and spec.get("task_startup") == binding["task_startup"]
            and spec.get("controller") == binding["controller_root"],
            "TASK_TARGET_ORIGINAL_STARTUP_REQUIRED")
    result_ref, result, anchor = task_startup_result(spec)
    launch_ref = file_ref(Path(binding["state_root"]) / "startup/codex.json")
    require(json_ref(launch_ref).get("startup") == startup, "TASK_TARGET_ORIGINAL_LAUNCH_REQUIRED")
    target = dict(schema=TARGET_SCHEMA, startup=startup, startup_result=result_ref,
                  anchor=result["anchor"], launch=launch_ref,
                  server=anchor["server"], socket=anchor["socket"])
    return spec, anchor, target


def _display(target, process):
    launch = json_ref(target["launch"])
    require(process["pid"] == launch["pid"] and process["birth"] == launch["birth"]
            and str(uuid.UUID(process["env"]["CMUX_SURFACE_ID"])) == str(uuid.UUID(launch["surface_uuid"]))
            and str(uuid.UUID(process["env"]["CMUX_WORKSPACE_ID"])) == str(uuid.UUID(launch["workspace_uuid"])),
            "TASK_TARGET_ORIGINAL_DISPLAY_CHANGED")


def _observe(spec, anchor, target, process):
    import cmux_daemon_identity as identity
    from cmux_codex_rpc import DaemonRPC
    _display(target, process)
    validate_tui_target(process, process_root("codex", process), spec, anchor)
    # This connection only reads the exact task. It never receives ownership of
    # dynamic tools, responds to a model operation, or creates/resumes a thread.
    with DaemonRPC(anchor["socket"]["path"], timeout=8) as client:
        return observe(client, anchor, identity.process)


def selected(process):
    """A new task target or None for an explicitly legacy startup."""
    source = _source()
    if source is None:
        return None
    spec, anchor, target = source
    proof = _observe(spec, anchor, target, process)
    require(proof["thread_id"] == anchor["thread_id"], "TASK_TARGET_THREAD_CHANGED")
    return dict(session_id=anchor["thread_id"], journal=anchor["journal"], target=target)


def validate_saved(binding):
    """Check the versioned producer/reader contract without selecting a thread."""
    require(binding.get("schema") == SCHEMA and binding.get("provider") == "codex",
            "TASK_TARGET_EXPLICIT_BINDING_REQUIRED")
    source = _source()
    require(source is not None, "TASK_TARGET_LEGACY_CONTROLLER_REFUSED")
    spec, anchor, target = source
    validate_value(binding, anchor, target)
    return spec, anchor


def require_live(binding, process):
    spec, anchor = validate_saved(binding)
    proof = _observe(spec, anchor, binding["task_target"], process)
    require(proof["thread_id"] == binding["session_id"], "TASK_TARGET_THREAD_CHANGED")


def require_client(client, binding):
    """Bind the actual original input connection to the selected server peer."""
    if binding.get("schema") != SCHEMA:
        require_legacy(binding)
        return
    _, anchor = validate_saved(binding)
    import cmux_daemon_identity as identity
    socket_evidence(client, anchor["socket"])
    verify_peer(client.sock, anchor["server"], identity.process)


def require_request(request, binding):
    if binding.get("schema") != SCHEMA:
        require_legacy(binding)
        return
    _, anchor = validate_saved(binding)
    require(request.get("socket") == anchor["socket"], "TASK_TARGET_REQUEST_SOCKET_CHANGED")
    # A third-party turn/start would be outside the retained service's enrolled
    # turn ledger. Keep it closed until original external-turn enrollment is
    # implemented; never replace it with terminal input or a new task.
    require(request.get("method") == "turn/steer", "TASK_TARGET_EXTERNAL_TURN_ENROLLMENT_REQUIRED")


def native_fallback(binding, fallback):
    """TASK delivery never types into the display TUI as a recovery route."""
    if binding.get("schema") == SCHEMA:
        return None
    require_legacy(binding)
    return fallback


def require_legacy(binding):
    require("task_target" not in binding, "TASK_TARGET_DOWNGRADE_REFUSED")
    if binding.get("provider") == "codex":
        original, _ = configuration()
        require("task_startup" not in original, "TASK_TARGET_DOWNGRADE_REFUSED")
