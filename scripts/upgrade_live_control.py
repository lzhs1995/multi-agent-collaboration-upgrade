#!/usr/bin/env python3
"""External check/launch/adopt/bootstrap/status/stop for a frozen experiment.

Only launch creates a workspace, once through the private MAC harness. Adoption
uses a read-only sandboxed inspector. This process never sends agent messages,
accepts business results, rewrites native logs, or replaces a failed session.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from orchestration_common import (ContractError, boot_id, checked_path, exclusive_lock, file_ref, json_ref, read_ref, require,
                                  strict_json, utc_now, within, write_json)
from orchestration_campaign import anchored_ref
from upgrade_client_isolation import SANDBOX, clean_child_environment, sandbox_check
from upgrade_experiment import NAME, arm_paths, private_root
from upgrade_live_runtime import (validate_adoption, validate_adoption_candidate,
                                  validate_live_runtime)


def harness_argv(runtime, spec, command, *, spawn=False):
    require(command in ("experiment-launch", "experiment-inspect"), "CONTROL_COMMAND_REFUSED")
    ref = runtime["startup"]
    argv = [str(SANDBOX), "-f", spec["pins"]["control_sandbox"]["path"],
            runtime["python"]["path"], "-I", "-B",
            str(Path(runtime["controller"]["path"]) / "scripts/mac_harness.py"),
            command, "--startup", ref["path"], "--startup-sha256", ref["sha256"]]
    if command == "experiment-launch":
        require(spawn, "EXPLICIT_EXPERIMENT_LAUNCH_REQUIRED")
        argv.extend(["--spawn", "--spawn-authorized"])
    return argv


def external_publisher(spec):
    # Adoption does not exist before first publication. Darwin's kernel query
    # denies nonexistent paths even for an external process; the complete
    # controller is an existing authority denied to every experimental client.
    authority = Path(spec["controller"])
    require(authority.is_dir(), "publisher controller authority is missing")
    require(not sandbox_check(os.getpid(), str(authority), "file-write-create"),
            "CLIENT_CANNOT_PUBLISH_ADOPTION")


def publish_bootstrap(runtime_ref, campaign_ref, instructions_ref):
    """Publish one immutable first instruction; never send or wake a client.

    The original startup turn is already waiting on this directory. All clients
    are denied writes here by the kernel. A publisher lock and atomic rename
    publish complete fsynced bytes without changing inode metadata afterward.
    """
    from orchestration_campaign import validate_campaign
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    external_publisher(spec)
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    adoption_ref = file_ref(spec["adoption_path"])
    validate_adoption(runtime_ref, adoption_ref, runtime=runtime)
    campaign = validate_campaign(campaign_ref)
    require(campaign.get("version") == 3 and campaign.get("evidence_class") == "cli_campaign_v3"
            and campaign["runtime"] == runtime_ref and campaign["experiment"] == runtime["context"]
            and campaign["arm"] == runtime["arm"] and campaign["client_isolation"] == adoption_ref,
            "BOOTSTRAP_CAMPAIGN_CHANGED")
    within(instructions_ref["path"], campaign["root"])
    instructions = read_ref(instructions_ref, "frozen supervisor instructions").decode("utf-8")
    require(0 < len(instructions) < 24000, "BOOTSTRAP_INSTRUCTIONS_MISSING")
    value = dict(schema="upgrade-bootstrap-v1", runtime=runtime_ref, startup=runtime["startup"],
                 adoption=adoption_ref, campaign=campaign_ref, instructions=instructions)
    path = Path(spec["adoption_path"]).parent / "bootstrap.json"
    with exclusive_lock(path.parent / "bootstrap-publisher.lock"):
        require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
        published = not os.path.lexists(path)
        if published:
            # Do not hard-link then unlink/chmod a just-published inode: an
            # already-waiting reader correctly rejects its changing ctime.
            # All publishers hold this lock; an existing final file is never
            # replaced. The entire directory is kernel-denied to clients.
            write_json(path, value)
        ref = file_ref(path)
        require(json_ref(ref, "original bootstrap") == value,
                "BOOTSTRAP_ALREADY_PUBLISHED_DIFFERENT_INSTRUCTION")
    # A simultaneous stop wins at the waiting reader. Preserve this publication
    # as evidence instead of deleting it or authorizing another first message.
    return dict(status="STOPPED_INPUT" if os.path.lexists(spec["stop_path"]) else
                ("BOOTSTRAP_PUBLISHED" if published else "BOOTSTRAP_ALREADY_PUBLISHED"),
                bootstrap=ref, input_operations=0, native_reception_verified=False)


def invocation(runtime_ref, runtime, spec, command, *, spawn=False, persist=True):
    argv = harness_argv(runtime, spec, command, spawn=spawn)
    control = Path(spec["adoption_path"]).parent
    if persist:
        external_publisher(spec)
        directory = control / "observations" / str(uuid.uuid4())
        write_json(directory / "intent.json", dict(runtime=runtime_ref, startup=runtime["startup"],
            argv=argv, command=command, created_at=utc_now()), exclusive=True)
    start_ns = time.monotonic_ns()
    started_at = utc_now()
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
            cwd=spec["work_root"], env=clean_child_environment({}), check=False)
        value = dict(runtime=runtime_ref, startup=runtime["startup"], command=command, argv=argv,
            started_at=started_at, completed_at=utc_now(), elapsed_ns=time.monotonic_ns() - start_ns,
            returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
            input_operations=0)
        try:
            value["result"] = strict_json(result.stdout, "private harness result")
        except ContractError:
            value["result"] = None
    except subprocess.TimeoutExpired as exc:
        # Preserve the attempt. A launch timeout cannot create replacement clients.
        value = dict(runtime=runtime_ref, startup=runtime["startup"], command=command, argv=argv,
            started_at=started_at, completed_at=utc_now(), elapsed_ns=time.monotonic_ns() - start_ns,
            returncode=None, result=None, status="UNKNOWN", input_operations=0,
            stdout=(exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout,
            stderr=(exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr)
    ref = write_json(directory / "result.json", value, exclusive=True) if persist else None
    if ref:
        Path(ref["path"]).chmod(0o444)
    return ref, value


def stop_scope(runtime_ref):
    """Derive the stop location from the pinned context, without executing code.

    A changed executable, hook configuration or inspector must never prevent
    stopping input. Only the immutable runtime/context and private path/account
    binding are needed; full validation is a later diagnostic.
    """
    runtime = json_ref(runtime_ref, "runtime stop binding")
    require(runtime.get("version") == 2 and runtime.get("skill") == NAME
            and runtime.get("execution_mode") == "live-isolated-v2"
            and runtime.get("production_adoption") is False and runtime.get("arm") in ("A1", "B"),
            "STOP_RUNTIME_SCOPE_CHANGED")
    context = json_ref(runtime["context"], "experiment stop binding")
    root = private_root(context["root"])
    names = ("state", "config", "controllers", "campaigns", "evidence", "tmp", "bin")
    require(context.get("skill") == NAME and context.get("version") == 1
            and context.get("production_adoption") is False
            and context.get("account_uid") == os.getuid()
            and context.get("account_home") == str(Path.home().resolve())
            and Path(runtime["context"]["path"]) == root / "experiment.json"
            and context["paths"] == {n: str(root / n) for n in names}, "STOP_CONTEXT_SCOPE_CHANGED")
    paths = arm_paths(context, runtime["arm"])
    require(Path(runtime_ref["path"]) == paths["controllers"] / "runtime-v2.json"
            and runtime["startup"]["path"] == str(paths["config"] / "startup-v2.json"),
            "STOP_RUNTIME_LOCATION_CHANGED")
    directory = within(paths["evidence"] / "live-control", root)
    # external_publisher() proves this process is outside every client against
    # the controller release; it is only the pinned path, never executed here.
    controller = within(runtime["controller"]["path"], paths["controllers"] / "releases")
    return runtime, dict(stop_path=str(directory / "stop.json"),
                         adoption_path=str(directory / "adoption.json"), controller=str(controller))


def stop(runtime_ref):
    runtime, spec = stop_scope(runtime_ref)
    external_publisher(spec)
    path = checked_path(spec["stop_path"])
    if not os.path.lexists(path):
        write_json(path, dict(schema="upgrade-stop-v2", runtime=runtime_ref,
            startup=runtime["startup"], requested_at=utc_now(), reason="explicit experiment stop",
            input_operations=0, terminate_clients=False), exclusive=True)
        path.chmod(0o444)
    else:
        old = json_ref(file_ref(path), "original stop marker")
        require(old.get("runtime") == runtime_ref and old.get("schema") == "upgrade-stop-v2",
                "STOP_MARKER_SCOPE_CHANGED")
    result = dict(status="STOPPED_INPUT", stop=file_ref(path), clients_preserved=True,
                  evidence_preserved=True, input_operations=0)
    try:
        validate_live_runtime(runtime_ref)
        result["runtime_diagnostic"] = "VERIFIED"
    except (ContractError, OSError, ValueError, KeyError) as exc:
        result["runtime_diagnostic"] = str(exc)
    return result


def control(runtime_ref, command):
    if command == "stop":
        return stop(runtime_ref)
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    startup_dir = Path(runtime["state_root"]) / "startup"
    launched = (startup_dir / "intent.json").exists()
    stopped = os.path.lexists(spec["stop_path"])
    adoption = Path(spec["adoption_path"])
    if command == "check":
        return dict(status="CONFIGURATION_VERIFIED", runtime=runtime_ref, startup=runtime["startup"],
                    launched=launched, stopped=stopped, adoption_published=adoption.exists(), input_operations=0)
    if command == "status":
        if not launched:
            return dict(status="NOT_LAUNCHED", stopped=stopped, input_operations=0)
        _, value = invocation(runtime_ref, runtime, spec, "experiment-inspect", persist=False)
        return dict(status="STOPPED_INPUT" if stopped else "OBSERVED", observation=value,
                    adoption=file_ref(adoption) if adoption.exists() else None, input_operations=0)
    if command == "held-observe":
        # Read only: the owner's permit-scoped release already wrote both files.
        request_ref = file_ref(adoption.parent / "launch-request.json")
        request = json_ref(request_ref)
        require(held_launch(runtime) and launched and request.get("runtime") == runtime_ref
                and request.get("startup") == runtime["startup"] and isinstance(request.get("held_release"), dict)
                and (startup_dir / "release.json").exists(), "HELD_RELEASE_NOT_OBSERVED")
        return dict(status="HELD_RELEASE_OBSERVED", launch_request=request_ref, input_operations=0)
    require(not stopped, "EXPERIMENT_STOP_REQUESTED")
    if command in ("launch", "hold"):
        require(not launched, "ORIGINAL_LAUNCH_ALREADY_EXISTS: inspect it; no retry or replacement")
        # A batch hold creates the held processes without the launch clock;
        # only the later permit-scoped release writes launch-request.json.
        record = "launch-request.json" if command == "launch" else "held-request.json"
        require(command == "launch" or (held_launch(runtime)
                and not os.path.lexists(adoption.parent / "launch-request.json")), "BATCH_HOLD_REQUIRES_PRE_EXEC_HOLD")
        from upgrade_startup_contract import child_budget_enabled
        from upgrade_child_budget_protocol import verify_child_hook_binary
        binding = json_ref(file_ref(Path(runtime["controller"]["path"]) / "upgrade-binding.json"))
        child_capability = (verify_child_hook_binary(spec["pins"]["claude"])
                            if child_budget_enabled(binding) else None)
        # The private harness also writes its exclusive intent before cmux. This
        # external intent is an additional crash record, never a retry budget.
        external_publisher(spec)
        write_json(adoption.parent / record, dict(runtime=runtime_ref,
            requested_at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id(),
            startup=runtime["startup"], **({"child_hook_capability": child_capability}
                                         if child_capability is not None else {})), exclusive=True)
        ref, value = invocation(runtime_ref, runtime, spec, "experiment-launch", spawn=True)
        prefix = "LAUNCH" if command == "launch" else "HELD_LAUNCH"
        return dict(status=prefix + ("_RETURNED" if value["returncode"] == 0 else "_UNCONFIRMED"),
                    evidence=ref, observation=value, input_operations=0)
    require(command == "adopt", "UNKNOWN_CONTROL_COMMAND")
    external_publisher(spec)
    if adoption.exists():
        ref = file_ref(adoption)
        validate_adoption(runtime_ref, ref, runtime=runtime)
        return dict(status="ALREADY_ADOPTED", adoption=ref, input_operations=0)
    require(launched, "ORIGINAL_LAUNCH_REQUIRED")
    ref, value = invocation(runtime_ref, runtime, spec, "experiment-inspect")
    result = value["result"]
    if (value["returncode"] != 0 or not isinstance(result, dict) or result.get("ready") is not True
            or result.get("errors") or result.get("stopped") or result.get("input_operations") != 0):
        return dict(status="NOT_READY", evidence=ref, observation=value, input_operations=0)
    require(result["startup"] == runtime["startup"] and result["schema"] == "upgrade-live-inspection-v2",
            "INSPECTION_SCOPE_CHANGED")
    require(0 <= time.time_ns() - result["observed_at_ns"] < 30 * 10**9,
            "INSPECTION_TOO_OLD_FOR_ADOPTION")
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    proof = dict(schema="upgrade-live-adoption-v2", runtime=runtime_ref, startup=runtime["startup"],
                 verified=True, observed_at=utc_now(), inspection=ref,
                 sessions=result["sessions"], input_operations=0)
    validate_adoption_candidate(runtime_ref, proof, runtime=runtime)
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    # Publication is only performed by this external process; all clients have
    # kernel write denial over the entire live-control directory and ancestors.
    proof_ref = write_json(adoption, proof, exclusive=True)
    adoption.chmod(0o444)
    return dict(status="ADOPTED", adoption=proof_ref, sessions=proof["sessions"], input_operations=0)


def held_launch(runtime):
    """The materialized binding, not a caller flag, decides a batch-held launch."""
    from upgrade_startup_contract import pre_exec_hold_enabled
    return pre_exec_hold_enabled(json_ref(file_ref(Path(runtime["controller"]["path"]) / "upgrade-binding.json")))


def release_held(runtime_ref, registration_ref, group, receipt_path, receipt_sha256):
    """Owner release of one batch-held group under its own active permit.

    launch-request.json is written here, immediately before the one private
    release, so the driver's original 900-second startup clock starts when the
    held clients may first exec. Never launches, retries or releases another
    group, and never rewrites an earlier request.
    """
    import cmux_daemon_identity as identity
    import upgrade_schedule as schedule
    from orchestration_permit import read_permit
    from upgrade_client_isolation import LANES
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    control_dir, startup_dir = Path(spec["adoption_path"]).parent, Path(runtime["state_root"]) / "startup"
    require(held_launch(runtime), "PRE_EXEC_HOLD_NOT_BOUND")
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    require((control_dir / "held-request.json").exists() and (startup_dir / "intent.json").exists(),
            "BATCH_HELD_LAUNCH_REQUIRED")
    for path in (control_dir / "launch-request.json", startup_dir / "release.json", Path(spec["adoption_path"])):
        require(not os.path.lexists(path), "HELD_GROUP_ALREADY_RELEASED: " + path.name)
    active = schedule.status(registration_ref).get("active")
    require(active is not None and active.get("group") == group, "RELEASE_REQUIRES_THIS_GROUP_ACTIVE_PERMIT")
    _, permit = read_permit(registration_ref, active["ordinal"], active=True)
    require(permit["runtime"] == runtime_ref, "ACTIVE_PERMIT_RUNTIME_DIFFERS")
    held = {}
    for lane in LANES:
        value = json_ref(file_ref(startup_dir / (lane + "-held.json")))
        launched = json_ref(value["launched"])
        require(value.get("native_exec") == 0 and launched.get("surface_uuid") == value.get("surface_uuid"),
                "HELD_RECORD_CHANGED")
        try:
            live = identity.process(launched["pid"])
        except Exception as exc:
            raise ContractError("HELD_PROCESS_CHANGED: " + lane) from exc
        require(list(live["birth"]) == list(launched["birth"]), "HELD_PROCESS_CHANGED: " + lane)
        held[lane] = dict(pid=launched["pid"], birth=launched["birth"], surface_uuid=value["surface_uuid"])
    receipt = file_ref(checked_path(receipt_path))
    require(receipt["sha256"] == receipt_sha256, "EXCLUSION_RECEIPT_PIN_CHANGED")
    external_publisher(spec)
    request = write_json(control_dir / "launch-request.json", dict(runtime=runtime_ref,
        requested_at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id(), startup=runtime["startup"],
        held_release=dict(permit=permit["step"], group=group, receipt=receipt, held=held)), exclusive=True)
    ref = runtime["startup"]
    argv = [str(SANDBOX), "-f", spec["pins"]["control_sandbox"]["path"], runtime["python"]["path"], "-I", "-B",
            str(Path(runtime["controller"]["path"]) / "scripts/mac_upgrade_live_entry.py"), "experiment-release",
            "--startup", ref["path"], "--startup-sha256", ref["sha256"],
            "--receipt", receipt["path"], "--receipt-sha256", receipt["sha256"]]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=180, cwd=spec["work_root"],
                            env=clean_child_environment({}), check=False)
    released = (startup_dir / "release.json").exists()
    return dict(status="RELEASED" if result.returncode == 0 and released else "RELEASE_UNCONFIRMED",
                launch_request=request, argv=argv, returncode=result.returncode, stdout=result.stdout,
                stderr=result.stderr, input_operations=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "launch", "adopt", "bootstrap", "status", "stop"))
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--campaign")
    parser.add_argument("--campaign-sha256")
    parser.add_argument("--instructions")
    parser.add_argument("--instructions-sha256")
    args = parser.parse_args(argv)
    try:
        runtime_ref = anchored_ref(args.runtime, args.runtime_sha256)
        if args.command == "bootstrap":
            require(all((args.campaign, args.campaign_sha256, args.instructions, args.instructions_sha256)),
                    "PINNED_CAMPAIGN_AND_INSTRUCTIONS_REQUIRED")
            value = publish_bootstrap(runtime_ref, anchored_ref(args.campaign, args.campaign_sha256),
                                      anchored_ref(args.instructions, args.instructions_sha256))
        else:
            require(not any((args.campaign, args.campaign_sha256, args.instructions, args.instructions_sha256)),
                    "BOOTSTRAP_ARGUMENTS_FOR_BOOTSTRAP_ONLY")
            value = control(runtime_ref, args.command)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if value["status"] not in ("NOT_READY", "LAUNCH_UNCONFIRMED") else 2
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
