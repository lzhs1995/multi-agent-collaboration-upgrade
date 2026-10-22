#!/usr/bin/env python3
"""External adopt/status/stop for one installed fenced validation stage.

Runs outside every client sandbox. It invokes only the stage release's
read-only inspector, publishes the stage's own adoption.json once, and never
sends input, accepts results, rewrites history or touches the original
adoption.json, startup record, failures or controller.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from orchestration_common import (ContractError, file_ref, json_ref, require, strict_json, utc_now, within,
                                  write_json)
from orchestration_campaign import anchored_ref
from upgrade_client_isolation import LANES, SANDBOX, clean_child_environment
from upgrade_experiment import validate_sessions
from upgrade_live_control import external_publisher
from upgrade_live_runtime import validate_live_runtime
from upgrade_stage_fence import ADOPTION_SCHEMA, load_stage, stop_paths

INSPECTION_SCHEMA = "upgrade-live-inspection-v2"


def scope(runtime_ref, stage_ref):
    """The original runtime and the exact stage it is installed for."""
    stage = json_ref(stage_ref, "validation stage")
    startup_ref, spec, binding, checked = load_stage(Path(stage["controller"]))
    require(checked == stage and binding["stage_fence"]["path"] == stage_ref["path"]
            and binding["stage_fence"]["sha256"] == stage_ref["sha256"], "STAGE_SCOPE_CHANGED")
    _, runtime = validate_live_runtime(runtime_ref, stage_controller=stage["controller"])
    require(runtime["startup"] == startup_ref, "STAGE_RUNTIME_CHANGED")
    return runtime, spec, binding, stage


def harness_argv(runtime, spec, stage):
    ref = runtime["startup"]
    return [str(SANDBOX), "-f", spec["pins"]["control_sandbox"]["path"], runtime["python"]["path"], "-I", "-B",
            str(Path(stage["controller"]) / "scripts/mac_harness.py"),
            "experiment-inspect", "--startup", ref["path"], "--startup-sha256", ref["sha256"]]


def observe(runtime_ref, stage_ref, runtime, spec, stage, *, persist):
    argv = harness_argv(runtime, spec, stage)
    directory = Path(stage["control"]) / "observations" / str(uuid.uuid4())
    if persist:
        external_publisher(dict(spec, controller=stage["controller"]))
        write_json(directory / "intent.json", dict(runtime=runtime_ref, stage=stage_ref, argv=argv,
                   command="experiment-inspect", created_at=utc_now()), exclusive=True)
    started_ns, started_at = time.monotonic_ns(), utc_now()
    base = dict(runtime=runtime_ref, stage=stage_ref, startup=runtime["startup"], command="experiment-inspect",
                argv=argv, started_at=started_at, input_operations=0)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=spec["work_root"],
                                env=clean_child_environment({}), check=False)
        value = dict(base, completed_at=utc_now(), elapsed_ns=time.monotonic_ns() - started_ns,
                     returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        try:
            value["result"] = strict_json(result.stdout, "stage inspection result")
        except ContractError:
            value["result"] = None
    except subprocess.TimeoutExpired:
        value = dict(base, completed_at=utc_now(), elapsed_ns=time.monotonic_ns() - started_ns,
                     returncode=None, result=None, status="UNKNOWN")
    ref = write_json(directory / "result.json", value, exclusive=True) if persist else None
    if ref:
        Path(ref["path"]).chmod(0o444)
    return ref, value


def validate_stage_adoption(runtime_ref, stage_ref, runtime, spec, binding, stage, proof):
    """All inspection evidence for this stage, before or after publication."""
    control = Path(stage["control"])
    require(not any(map(os.path.lexists, stop_paths(binding))), "EXPERIMENT_STOP_REQUESTED")
    require(proof.get("schema") == ADOPTION_SCHEMA and proof.get("runtime") == runtime_ref
            and proof.get("stage") == stage_ref and proof.get("startup") == runtime["startup"]
            and proof.get("verified") is True and proof.get("input_operations") == 0, "STAGE_ADOPTION_UNVERIFIED")
    within(proof["inspection"]["path"], control / "observations")
    observation = json_ref(proof["inspection"], "stage inspector invocation")
    require(observation["runtime"] == runtime_ref and observation["stage"] == stage_ref
            and observation["argv"] == harness_argv(runtime, spec, stage) and observation["returncode"] == 0
            and observation["input_operations"] == 0, "STAGE_INSPECTION_INVOCATION_CHANGED")
    value = observation["result"]
    require(value["schema"] == INSPECTION_SCHEMA and value["startup"] == runtime["startup"]
            and value.get("stage") == stage_ref and value["ready"] is True and value["errors"] == {}
            and value["stopped"] is False and value["input_operations"] == 0
            and value["sessions"] == proof["sessions"] and "startup_failures" not in value,
            "STAGE_INSPECTION_NOT_READY")
    require(value["inspector"]["path"] == str(Path(stage["controller"]) / "scripts/mac_upgrade_live_inspect.py")
            and file_ref(value["inspector"]["path"]) == value["inspector"], "STAGE_INSPECTOR_CHANGED")
    validate_sessions(proof["sessions"], protected=spec["protected_surface_uuids"])
    require(set(value["readiness"]) == set(LANES) and set(value["kernel"]) == {*LANES, "codex-server"},
            "STAGE_CLIENT_PROOF_MISSING")
    codex, capability = proof["sessions"]["codex"], value["codex_capability"]
    require(capability["thread_id"] == codex["session_id"] and capability["path"] == codex["transcript"]["path"]
            and capability["can_accept_direct_input"] is True and capability["input_operations"] == 0,
            "STAGE_CODEX_CAPABILITY_CHANGED")
    for lane, session in proof["sessions"].items():
        item = value["readiness"][lane]
        require(session["native_config_root"] == spec["clients"][lane]["native_root"]
                and item["stage"] == dict(stage_id=stage["stage_id"], window_offset=item["stage"]["window_offset"],
                                          controller=stage["controller"])
                and item["model"] == stage["models"][lane] == session["model"]
                and item["model_record"] == session["model_record"]
                and item["hooks"]["PreToolUse"] and item["hooks"]["PostToolUse"]
                and value["kernel"][lane]["pid"] == session["process"]["pid"]
                and value["kernel"][lane]["kernel_checked"] is True, "STAGE_READINESS_OR_KERNEL_CHANGED")
    require(value["kernel"]["codex-server"]["kernel_checked"] is True, "STAGE_SERVER_KERNEL_UNVERIFIED")
    return proof


def adopt(runtime_ref, stage_ref):
    runtime, spec, binding, stage = scope(runtime_ref, stage_ref)
    path = Path(stage["control"]) / "adoption.json"
    external_publisher(dict(spec, controller=stage["controller"]))
    if os.path.lexists(path):
        ref = file_ref(path)
        validate_stage_adoption(runtime_ref, stage_ref, runtime, spec, binding, stage, json_ref(ref))
        return dict(status="ALREADY_ADOPTED", adoption=ref, input_operations=0)
    require(not any(map(os.path.lexists, stop_paths(binding))), "EXPERIMENT_STOP_REQUESTED")
    ref, value = observe(runtime_ref, stage_ref, runtime, spec, stage, persist=True)
    result = value["result"]
    if (value["returncode"] != 0 or not isinstance(result, dict) or result.get("ready") is not True
            or result.get("errors") or result.get("stopped") or result.get("input_operations") != 0):
        return dict(status="NOT_READY", evidence=ref, observation=value, input_operations=0)
    require(0 <= time.time_ns() - result["observed_at_ns"] < 30 * 10**9, "INSPECTION_TOO_OLD_FOR_ADOPTION")
    proof = dict(schema=ADOPTION_SCHEMA, runtime=runtime_ref, stage=stage_ref, startup=runtime["startup"],
                 verified=True, observed_at=utc_now(), inspection=ref, sessions=result["sessions"],
                 input_operations=0)
    validate_stage_adoption(runtime_ref, stage_ref, runtime, spec, binding, stage, proof)
    require(not any(map(os.path.lexists, stop_paths(binding))), "EXPERIMENT_STOP_REQUESTED")
    published = write_json(path, proof, exclusive=True)
    path.chmod(0o444)
    return dict(status="STAGE_ADOPTED", adoption=published, sessions=proof["sessions"], input_operations=0)


def stop(stage_ref):
    """Derived from the pinned stage record only; a broken release never prevents stopping."""
    stage = json_ref(stage_ref, "validation stage stop binding")
    control = Path(stage["control"])
    require(stage_ref["path"] == str(control / "stage.json") and control.parent.name == "live-control",
            "STAGE_STOP_SCOPE_CHANGED")
    path = control / "stop.json"
    if not os.path.lexists(path):
        write_json(path, dict(schema="upgrade-stage-stop-v1", stage=stage_ref, requested_at=utc_now(),
                              reason="explicit stage stop", input_operations=0, terminate_clients=False),
                   exclusive=True)
        path.chmod(0o444)
    return dict(status="STOPPED_INPUT", stop=file_ref(path), clients_preserved=True, input_operations=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("adopt", "status", "stop"))
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--stage-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        runtime_ref, stage_ref = anchored_ref(args.runtime, args.runtime_sha256), anchored_ref(args.stage,
                                                                                              args.stage_sha256)
        if args.command == "stop":
            value = stop(stage_ref)
        elif args.command == "adopt":
            value = adopt(runtime_ref, stage_ref)
        else:
            runtime, spec, binding, stage = scope(runtime_ref, stage_ref)
            _, observation = observe(runtime_ref, stage_ref, runtime, spec, stage, persist=False)
            adoption = Path(stage["control"]) / "adoption.json"
            value = dict(status="STOPPED_INPUT" if any(map(os.path.lexists, stop_paths(binding))) else "OBSERVED",
                         observation=observation, adoption=file_ref(adoption) if adoption.exists() else None,
                         input_operations=0)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if value["status"] != "NOT_READY" else 2
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
