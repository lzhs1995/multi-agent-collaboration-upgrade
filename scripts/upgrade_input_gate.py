"""Final experiment membership/stop gate inside the existing MAC send paths.

MAC still owns draft stability, binding, intent, one input and native receipt.
This module never sends, retries, chooses a replacement, or accepts a task.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from orchestration_common import file_ref, json_ref, read_regular, require
from upgrade_client_isolation import require_kernel_boundary
from upgrade_native_roots import configuration, process_root
from upgrade_socket_boundary import binding_boundary, verify_kernel


def canonical(value):
    return str(uuid.UUID(value))


def check_input(proof, *, native_binding=None):
    binding, _ = configuration()
    control = Path(binding["evidence_root"]) / "live-control"
    from upgrade_stage_fence import stage_adoption, stop_paths
    stops = stop_paths(binding)
    require(not any(map(os.path.lexists, stops)), "EXPERIMENT_STOP_REQUESTED")
    startup = file_ref(Path(binding["config_root"]) / "startup-v2.json")
    startup_authority = False
    if "stage_fence" in binding:
        # A fenced stage accepts only its own external adoption, never the
        # original adoption.json or startup-continuity authority.
        adoption = stage_adoption(binding)
        allowed = {"codex", "claude_a", "claude_b"}
    elif os.path.lexists(control / "adoption.json"):
        adoption = json.loads(read_regular(control / "adoption.json"))
        require(adoption.get("schema") == "upgrade-live-adoption-v2"
                and adoption.get("startup") == startup and adoption.get("verified") is True,
                "EXPERIMENT_AUTOMATIC_ADOPTION_REQUIRED")
        allowed = {"codex", "claude_a", "claude_b"}
    else:
        from upgrade_startup_contract import startup_continuity_enabled, validate_startup
        from upgrade_startup_continuity import validate_source_contract, current_ready_claude_lanes
        require(startup_continuity_enabled(binding), "EXPERIMENT_AUTOMATIC_ADOPTION_REQUIRED")
        spec, checked_binding = validate_startup(startup, controller=binding["controller_root"])
        require(checked_binding == binding, "STARTUP_CONTINUITY_BINDING_CHANGED")
        runtime_ref = file_ref(Path(binding["controller_root"]).parents[2] / "runtime-v2.json")
        runtime = json_ref(runtime_ref)
        adoption = json_ref(file_ref(control / "startup-continuity.json"))
        import cmux_native_delivery as native
        validate_source_contract(adoption, runtime_ref, runtime, spec, binding, native)
        startup_authority = True
        allowed = set(current_ready_claude_lanes(adoption, native))
    sessions = adoption["sessions"]
    require(set(sessions) == {"codex", "claude_a", "claude_b"}, "EXPERIMENT_ADOPTION_LANES_CHANGED")
    by_surface = {canonical(s["surface_uuid"]): (lane, s) for lane, s in sessions.items()}
    caller, target = canonical(proof["caller_surface_uuid"]), canonical(proof["target_surface_uuid"])
    if allowed != {"codex", "claude_a", "claude_b"}:
        require(caller in by_surface and target in by_surface
                and by_surface[caller][0] in allowed and by_surface[target][0] in allowed,
                "STARTUP_CONTINUITY_ENDPOINT_NOT_READY")
    require(len(by_surface) == 3 and caller in by_surface and target in by_surface and caller != target,
            "EXPERIMENT_ENDPOINT_NOT_ENROLLED")
    import cmux_daemon_identity as identity
    for endpoint in (caller, target):
        lane, session = by_surface[endpoint]
        require(canonical(proof["workspace_uuid"]) == canonical(session["workspace_uuid"]),
                "EXPERIMENT_WORKSPACE_CHANGED")
        row = identity.process(session["process"]["pid"])
        require({k: row[k] for k in session["process"]} == session["process"], "EXPERIMENT_CLIENT_CHANGED")
        require(canonical(row["env"]["CMUX_SURFACE_ID"]) == endpoint
                and canonical(row["env"]["CMUX_WORKSPACE_ID"]) == canonical(session["workspace_uuid"])
                and str(process_root(session["provider"], row)) == binding["native_roots"][lane],
                "EXPERIMENT_CLIENT_ENDPOINT_CHANGED")
        require_kernel_boundary(row["pid"], denied=binding["isolation"]["denied_probes"],
                                writable=binding["isolation"]["write_probes"])
        boundary = binding_boundary(binding, live=True)
        if boundary is not None:
            verify_kernel(row["pid"], boundary, writer=lane == "codex")
    receiver = by_surface[target][1]
    if "target_pane_uuid" in proof:
        require(canonical(proof["target_pane_uuid"]) == canonical(receiver["pane_uuid"]),
                "EXPERIMENT_TARGET_PANE_CHANGED")
    if native_binding is not None:
        require(native_binding["session_id"] == receiver["session_id"]
                and native_binding["provider"] == receiver["provider"]
                and native_binding["process"] == receiver["process"]
                and all(native_binding["transcript"][k] == receiver["transcript"][k]
                        for k in ("path", "device", "inode")), "EXPERIMENT_NATIVE_RECEIVER_CHANGED")
    if startup_authority:
        fresh_allowed = set(current_ready_claude_lanes(adoption, native))
        require(by_surface[caller][0] in fresh_allowed and by_surface[target][0] in fresh_allowed,
                "STARTUP_CONTINUITY_ENDPOINT_NOT_READY")
    require(not any(map(os.path.lexists, stops)), "EXPERIMENT_STOP_REQUESTED")
    return adoption
