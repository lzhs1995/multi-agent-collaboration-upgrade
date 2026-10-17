"""Final experiment membership/stop gate inside the existing MAC send paths.

MAC still owns draft stability, binding, intent, one input and native receipt.
This module never sends, retries, chooses a replacement, or accepts a task.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from orchestration_common import file_ref, read_regular, require
from upgrade_client_isolation import require_kernel_boundary
from upgrade_native_roots import configuration, process_root
from upgrade_socket_boundary import binding_boundary, verify_kernel


def canonical(value):
    return str(uuid.UUID(value))


def check_input(proof, *, native_binding=None):
    binding, _ = configuration()
    control = Path(binding["evidence_root"]) / "live-control"
    require(not os.path.lexists(control / "stop.json"), "EXPERIMENT_STOP_REQUESTED")
    adoption = json.loads(read_regular(control / "adoption.json"))
    startup = file_ref(Path(binding["config_root"]) / "startup-v2.json")
    require(adoption.get("schema") == "upgrade-live-adoption-v2"
            and adoption.get("startup") == startup and adoption.get("verified") is True,
            "EXPERIMENT_AUTOMATIC_ADOPTION_REQUIRED")
    sessions = adoption["sessions"]
    require(set(sessions) == {"codex", "claude_a", "claude_b"}, "EXPERIMENT_ADOPTION_LANES_CHANGED")
    by_surface = {canonical(s["surface_uuid"]): (lane, s) for lane, s in sessions.items()}
    caller, target = canonical(proof["caller_surface_uuid"]), canonical(proof["target_surface_uuid"])
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
    require(not os.path.lexists(control / "stop.json"), "EXPERIMENT_STOP_REQUESTED")
    return adoption
