"""Versioned live runtime and externally verified private native-session roots.

The offline-v1 validator remains closed to live use. A live-v2 configuration
alone never authorizes input: adoption is published outside the client sandboxes
after the pinned, read-only inspector observes all three actual clients.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from orchestration_common import (checked_path, contract_errors, json_ref, read_ref,
                                  require, verify_tree, within)
from upgrade_client_isolation import LANES, private_native_roots
from upgrade_experiment import NAME, arm_paths, validate_context, validate_sessions
from upgrade_startup_contract import validate_startup

RUNTIME_FIELDS = {"version", "skill", "context", "arm", "execution_mode", "source_controller",
                  "controller", "release_manifest", "patches", "startup", "state_root",
                  "config_root", "python", "production_adoption"}
SESSION_FIELDS = ("provider", "session_id", "workspace_uuid", "surface_uuid", "pane_uuid",
                  "tty", "process", "native_config_root", "model", "model_record")
TRANSCRIPT_FIELDS = ("path", "device", "inode")


@contract_errors("live runtime")
def validate_live_runtime(ref, *, context_ref=None):
    runtime = json_ref(ref, "live runtime")
    require(set(runtime) == RUNTIME_FIELDS and runtime["version"] == 2
            and runtime["skill"] == NAME and runtime["execution_mode"] == "live-isolated-v2"
            and runtime["production_adoption"] is False, "LIVE_RUNTIME_FORMAT_CHANGED")
    if context_ref is not None:
        require(runtime["context"] == context_ref, "LIVE_RUNTIME_EXPERIMENT_CHANGED")
    context = validate_context(runtime["context"])
    require(runtime["arm"] in ("A1", "B"), "LIVE_RUNTIME_ARM_CHANGED")
    paths = arm_paths(context, runtime["arm"])
    release = paths["controllers"] / "releases/private-live-v2"
    require(Path(ref["path"]) == paths["controllers"] / "runtime-v2.json"
            and runtime["controller"]["path"] == str(release / "source")
            and runtime["release_manifest"]["path"] == str(release / "MANIFEST.json")
            and runtime["patches"]["path"] == str(paths["controllers"] / "patches-v2.json"),
            "LIVE_RUNTIME_LOCATION_CHANGED")
    require(runtime["source_controller"] == context["source_controller"]
            and runtime["python"] == context["python"]
            and runtime["state_root"] == str(paths["state"])
            and runtime["config_root"] == str(paths["config"]), "LIVE_RUNTIME_SCOPE_CHANGED")
    verify_tree(runtime["source_controller"])
    verify_tree(runtime["controller"])
    manifest = json_ref(runtime["release_manifest"], "private release manifest")
    require(manifest == dict(schema="upgrade-private-controller-v2", arm=runtime["arm"],
            source=runtime["source_controller"], files=runtime["controller"]["files"]),
            "LIVE_RELEASE_MANIFEST_CHANGED")
    patches = json_ref(runtime["patches"], "private patches")
    require(patches["source"] == runtime["source_controller"] and patches["version"] == 2
            and patches["compatibility_fixes"] == ["original_gate_canonical_ack"]
            and patches["live_adoption"] is False, "LIVE_PATCH_CONTRACT_CHANGED")
    spec, _ = validate_startup(runtime["startup"], controller=runtime["controller"]["path"])
    require(spec["context"] == runtime["context"] and spec["arm"] == runtime["arm"],
            "LIVE_STARTUP_RUNTIME_CHANGED")
    return context, runtime


def validate_versioned_runtime(ref, *, context_ref=None):
    runtime = json_ref(ref, "versioned runtime")
    if runtime.get("version") == 1:
        from upgrade_experiment import validate_runtime
        return validate_runtime(ref, context_ref=context_ref)
    return validate_live_runtime(ref, context_ref=context_ref)


def identity_matches(actual, enrolled):
    """A growing EOF is not a new session; path/device/inode must stay fixed."""
    return (all(actual.get(key) == enrolled.get(key) for key in SESSION_FIELDS)
            and all(actual.get("transcript", {}).get(key) == enrolled["transcript"].get(key)
                    for key in TRANSCRIPT_FIELDS))


@contract_errors("client adoption")
def validate_adoption(runtime_ref, proof_ref, *, runtime=None, allow_stopped=False):
    if runtime is None:
        _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    require(isinstance(proof_ref, dict) and proof_ref.get("path") == spec["adoption_path"],
            "EXTERNAL_ADOPTION_REQUIRED")
    proof = json_ref(proof_ref, "external adoption")
    return validate_adoption_candidate(runtime_ref, proof, runtime=runtime, allow_stopped=allow_stopped)


@contract_errors("client adoption candidate")
def validate_adoption_candidate(runtime_ref, proof, *, runtime=None, allow_stopped=False):
    """Check all evidence before the external publisher creates adoption.json.

    This accepts an unpublished value only for validation. Native readers and
    input gates still require the immutable published file at the fixed path.
    """
    if runtime is None:
        _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    if not allow_stopped:
        require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    require(proof.get("schema") == "upgrade-live-adoption-v2"
            and proof.get("runtime") == runtime_ref and proof.get("startup") == runtime["startup"]
            and proof.get("verified") is True and proof.get("input_operations") == 0,
            "EXTERNAL_ADOPTION_UNVERIFIED")
    control = Path(spec["adoption_path"]).parent
    inspection_ref = proof["inspection"]
    within(inspection_ref["path"], control / "observations")
    observation = json_ref(inspection_ref, "external inspector invocation")
    require(observation["runtime"] == runtime_ref and observation["startup"] == runtime["startup"]
            and observation["command"] == "experiment-inspect" and observation["returncode"] == 0
            and observation["input_operations"] == 0,
            "ADOPTION_INSPECTION_INVOCATION_CHANGED")
    from upgrade_live_control import harness_argv
    require(observation["argv"] == harness_argv(runtime, spec, "experiment-inspect"),
            "ADOPTION_INSPECTION_COMMAND_CHANGED")
    value = observation["result"]
    require(value["schema"] == "upgrade-live-inspection-v2" and value["startup"] == runtime["startup"]
            and value["ready"] is True and value["errors"] == {} and value["stopped"] is False
            and value["input_operations"] == 0 and value["sessions"] == proof["sessions"],
            "ADOPTION_INSPECTION_NOT_READY")
    inspector_path = Path(runtime["controller"]["path"]) / "scripts/mac_upgrade_live_inspect.py"
    require(value["inspector"]["path"] == str(inspector_path), "ADOPTION_INSPECTOR_CHANGED")
    read_ref(value["inspector"], "frozen inspector")
    validate_sessions(proof["sessions"], protected=spec["protected_surface_uuids"])
    require(set(value["readiness"]) == set(LANES) and set(value["kernel"]) == {*LANES, "codex-server"},
            "ADOPTION_CLIENT_PROOF_MISSING")
    capability = value["codex_capability"]
    codex = proof["sessions"]["codex"]
    require(capability["thread_id"] == codex["session_id"]
            and capability["path"] == codex["transcript"]["path"]
            and capability["can_accept_direct_input"] is True
            and capability["methods"] == ["thread/read", "thread/turns/list"]
            and capability["input_operations"] == 0, "ADOPTION_CODEX_CAPABILITY_CHANGED")
    for lane, session in proof["sessions"].items():
        expected = spec["clients"][lane]["native_root"]
        require(session["native_config_root"] == expected, "ADOPTION_NATIVE_ROOT_CHANGED")
        within(session["transcript"]["path"], Path(expected) / ("sessions" if lane == "codex" else "projects"))
        item = value["readiness"][lane]
        require(item["model"] == session["model"] and item["model_record"] == session["model_record"]
                and item["hooks"]["PreToolUse"] and item["hooks"]["PostToolUse"]
                and value["kernel"][lane]["pid"] == session["process"]["pid"]
                and value["kernel"][lane]["kernel_checked"] is True,
                "ADOPTION_READINESS_OR_KERNEL_CHANGED")
    require(value["kernel"]["codex-server"]["kernel_checked"] is True,
            "ADOPTION_SERVER_KERNEL_UNVERIFIED")
    return proof


def authenticated_sessions(runtime_ref, proof_ref):
    """Attach authority references, never a caller-supplied filesystem root."""
    proof = validate_adoption(runtime_ref, proof_ref)
    sessions = copy.deepcopy(proof["sessions"])
    for session in sessions.values():
        session["private_native"] = dict(runtime=runtime_ref, adoption=proof_ref)
        session["transcript"] = {key: session["transcript"][key] for key in TRANSCRIPT_FIELDS}
    return sessions


def authenticated_native_root(session):
    authority = session.get("private_native")
    require(isinstance(authority, dict) and set(authority) == {"runtime", "adoption"},
            "copied/non-native transcript cannot prove a CLI run; private adoption required")
    context, runtime = validate_live_runtime(authority["runtime"])
    # Read-only evidence remains inspectable after stop. Every new business input
    # separately checks the stop marker in the original MAC input gate.
    proof = validate_adoption(authority["runtime"], authority["adoption"], runtime=runtime, allow_stopped=True)
    matches = [lane for lane, enrolled in proof["sessions"].items() if identity_matches(session, enrolled)]
    require(len(matches) == 1, "PRIVATE_NATIVE_SESSION_NOT_ENROLLED")
    lane = matches[0]
    root = private_native_roots(arm_paths(context, runtime["arm"]))[lane]
    return checked_path(root / ("sessions" if session["provider"] == "codex" else "projects"))


def live_gate_v2(context_ref, runtime_ref, sessions, proof_ref):
    _, runtime = validate_live_runtime(runtime_ref, context_ref=context_ref)
    proof = validate_adoption(runtime_ref, proof_ref, runtime=runtime)
    validate_sessions(sessions)
    for lane, session in sessions.items():
        require(identity_matches(session, proof["sessions"][lane])
                and session.get("private_native") == dict(runtime=runtime_ref, adoption=proof_ref),
                "CAMPAIGN_SESSION_NOT_ADOPTED")
    return proof
