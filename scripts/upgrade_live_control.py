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


def task_lifecycle(runtime_ref, client):
    """Retain the original observer, RPC and ledger through all task phases.

    This explicit entry is used only after an independently authorized launch.
    Construction sends no model input; bootstrap/adoption/attachment remain
    separate guarded actions on the returned same owner.
    """
    from upgrade_task_lifecycle import TaskLifecycle
    return TaskLifecycle(runtime_ref, client)


def bootstrap_task_thread(runtime_ref, client):
    """New topology only: one task creation on a TUI-first pinned connection.

    The caller retains this TaskRPC to process buffered requests after actual
    TUI attachment and independently verified actor enrollment. This returns
    creation evidence, never live adoption or authority for campaign actions.
    Legacy runtime requests are refused before any mutation.
    """
    from upgrade_startup_contract import TASK_SCHEMA
    from upgrade_task_startup import bootstrap_task
    from upgrade_rpc_peer import server_pin, verify_peer
    import cmux_daemon_identity as identity
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    require(spec.get("schema") == TASK_SCHEMA, "TASK_BOOTSTRAP_EXPLICIT_NEW_TOPOLOGY_REQUIRED")
    external_publisher(spec)
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    binding = json_ref(file_ref(Path(spec["controller"]) / "upgrade-binding.json"))
    startup = Path(binding["state_root"]) / "startup"
    release = json_ref(file_ref(startup / "release.json"))
    require(release.get("startup") == runtime["startup"], "TASK_BOOTSTRAP_HELD_RELEASE_REQUIRED")
    launched = json_ref(file_ref(startup / "codex-server.json"))
    row = identity.process(launched["pid"])
    require(launched["startup"] == runtime["startup"] and row["birth"] == launched["birth"]
            and row["executable"] == spec["pins"]["codex"]["path"] and row["argv"] == spec["rpc"]["argv"]
            and client.server == server_pin(row, uid=os.getuid()), "TASK_BOOTSTRAP_SERVER_CHANGED")
    origin = json_ref(file_ref(startup / "codex-origin.json"))
    tui = identity.process(origin["pid"])
    require(origin["startup"] == runtime["startup"] and tui["birth"] == origin["birth"]
            and tui["executable"] == spec["pins"]["codex"]["path"], "TASK_BOOTSTRAP_OFFICIAL_TUI_REQUIRED")
    verify_peer(client.sock, client.server, client.process)
    control = Path(spec["adoption_path"]).parent
    result = bootstrap_task(client, spec["task_startup"], control / "task-startup")
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    attach = write_json(control / "task-attach.json", dict(schema="upgrade-task-attach-v1",
        startup=runtime["startup"], result=result), exclusive=True)
    return dict(status="TASK_CREATED_AWAITING_TUI_AND_ADOPTION", result=result, attach=attach,
                model_turns_started=1, operations_executed=0, adoption_proven=False)


def readiness_task_actor(runtime_ref, client):
    """Serve only the new startup's fixed readiness operation, never business.

    This is the explicit pre-adoption exception: one nonce receipt in the
    protected control directory. Every business kind is refused. The retained
    original RPC stream and ledger feed the normal TaskActor implementation.
    """
    import secrets
    from upgrade_startup_contract import TASK_SCHEMA
    from upgrade_task_readiness import SCHEMA, task_startup_result
    from upgrade_task_operations import OperationLedger
    from upgrade_task_startup import plan_value, registration_for_plan
    from upgrade_task_actor import TaskActor
    from upgrade_task_thread import observe
    import cmux_daemon_identity as identity
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    require(spec.get("schema") == TASK_SCHEMA, "TASK_READINESS_NEW_TOPOLOGY_REQUIRED")
    external_publisher(spec)
    _, startup, anchor = task_startup_result(spec)
    registration = json_ref(startup["operations"])
    require(registration == registration_for_plan(plan_value(spec["task_startup"]),
                startup["thread_id"], startup["turn_id"], anchor["server"]),
            "TASK_READINESS_REGISTRATION_CHANGED")
    ready = {"ready":{"kind":"readiness","payload":{}}}
    multi = registration["schema"] in ("upgrade-task-operations-v2", "upgrade-task-operations-v3")
    require((not multi and registration["operations"] == ready) or
            (multi and registration["initial_operations"] == ["ready"]
             and registration["operations"].get("ready") == ready["ready"]
             and registration["server"] == client.server), "TASK_READINESS_BUSINESS_OPERATIONS_REFUSED")
    control = Path(spec["adoption_path"]).parent
    binding = json_ref(file_ref(Path(spec["controller"]) / "upgrade-binding.json"))
    launched = json_ref(file_ref(Path(binding["state_root"]) / "startup/codex.json"))

    def gate(original, message, record):
        external_publisher(spec)
        require(not os.path.lexists(spec["stop_path"]) and original == registration,
                "TASK_READINESS_STOPPED_OR_CHANGED")
        current = identity.process(launched["pid"])
        require(current["birth"] == launched["birth"]
                and current["executable"] == spec["pins"]["codex"]["path"]
                and current["argv"][-2:] == ["resume",startup["thread_id"]]
                and client.server == anchor["server"], "TASK_READINESS_TUI_OR_SERVER_CHANGED")
        read_ref(spec["task_startup"])
        proof = observe(client,anchor,identity.process)
        require(proof["thread_id"] == registration["thread_id"]
                and proof["latest_turn_id"] == registration["turn_id"], "TASK_READINESS_ACTIVE_TURN_CHANGED")

    def readiness(payload):
        require(payload == {}, "TASK_READINESS_PAYLOAD_CHANGED")
        value = dict(schema=SCHEMA,plan=spec["task_startup"],thread_id=startup["thread_id"],
                     turn_id=startup["turn_id"],receipt_nonce=secrets.token_hex(24))
        receipt = write_json(control / "task-readiness-receipt.json",value,exclusive=True)
        return dict(success=True,contentItems=[dict(type="inputText",text=json.dumps(value,sort_keys=True))]),receipt

    ledger = OperationLedger(startup["ledger"],startup["registration_sha256"])
    try:
        require(ledger.active_turn == startup["turn_id"], "TASK_READINESS_INITIAL_TURN_CLOSED")
        return TaskActor(client,ledger,{"readiness":readiness},gate,control / "task-readiness-events",
                         operation_scope={"ready"})
    except BaseException:
        ledger.close()
        raise


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


def campaign_task_service(runtime_ref, campaign_ref, client, ledger, *, advice_connections=None):
    """Attach the original post-adoption business actor; never launch a task.

    Requires complete TASK adoption. A fixture or callback gate cannot grant
    permission to drive live campaign operations.
    """
    from upgrade_startup_contract import TASK_SCHEMA
    _,runtime=validate_live_runtime(runtime_ref)
    spec=json_ref(runtime["startup"])
    require(spec.get("schema")==TASK_SCHEMA, "TASK_SERVICE_EXPLICIT_TOPOLOGY_REQUIRED")
    external_publisher(spec)
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    adoption=file_ref(spec["adoption_path"])
    validate_adoption(runtime_ref,adoption,runtime=runtime)
    # This entry belongs to the orchestration adapter, not the private startup
    # support import closure. Resolve its owner only after actual adoption.
    from upgrade_task_campaign import campaign_task_service as attach
    from upgrade_task_enrollment import TaskCampaignGate
    from orchestration_run import Runner
    runner=Runner(campaign_ref)
    require(runner.campaign["runtime"]==runtime_ref and runner.campaign["client_isolation"]==adoption,
            "TASK_SERVICE_CAMPAIGN_ADOPTION_CHANGED")
    return attach(runner,client,ledger,TaskCampaignGate(runner,client,ledger),
                  advice_connections=advice_connections)


def campaign_task_runtime(runtime_ref,campaign_ref,client,ledger,*,advice_connections=None):
    """Attach one retained owner after the original complete adoption gates.

    The returned runtime drives its original service in bounded run() calls.
    Construction sends no model input and cannot replace an existing owner.
    """
    service=campaign_task_service(runtime_ref,campaign_ref,client,ledger,
                                  advice_connections=advice_connections)
    from upgrade_task_runtime import CampaignRuntime
    return CampaignRuntime(service)


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


def control(runtime_ref, command, *, driver_ref=None, credential_fd=None):
    require(command == 'hold' or (driver_ref is None and credential_fd is None),
            'TASK_OWNER_ARGUMENTS_FOR_HOLD_ONLY')
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
        from upgrade_startup_contract import TASK_SCHEMA
        task_plan = None
        if spec.get("schema") == TASK_SCHEMA:
            require(command == 'hold', 'TASK_LAUNCH_REQUIRES_ORIGINAL_HOLD_AND_RELEASE')
            from upgrade_task_launcher import launch_plan, launch_owner
            task_plan = launch_plan(runtime_ref, driver_ref, credential_fd=credential_fd)
        else:
            require(driver_ref is None and credential_fd is None, 'TASK_OWNER_ARGUMENTS_REQUIRE_TASK')
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
        owner = launch_owner(task_plan, credential_fd=credential_fd) if task_plan is not None else None
        try:
            require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
            write_json(adoption.parent / record, dict(runtime=runtime_ref,
                requested_at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id(),
                startup=runtime["startup"], **({"child_hook_capability": child_capability}
                                             if child_capability is not None else {}),
                **({'task_owner':owner} if owner is not None else {})), exclusive=True)
            ref, value = invocation(runtime_ref, runtime, spec, "experiment-launch", spawn=True)
        except BaseException as exc:
            if owner is not None:
                write_json(adoption.parent/'task-owner-launch/unknown.json',
                    dict(intent=owner['intent'], error_type=type(exc).__name__, error=str(exc),
                         retry_allowed=False, clients_preserved=True), exclusive=True)
            raise
        if owner is not None and value['returncode'] != 0:
            write_json(adoption.parent/'task-owner-launch/unknown.json',
                dict(intent=owner['intent'], reason='ORIGINAL_HELD_LAUNCH_UNCONFIRMED',
                     evidence=ref, retry_allowed=False, clients_preserved=True), exclusive=True)
        prefix = "LAUNCH" if command == "launch" else "HELD_LAUNCH"
        return dict(status=prefix + ("_RETURNED" if value["returncode"] == 0 else "_UNCONFIRMED"),
                    evidence=ref, observation=value, input_operations=0,
                    **({'task_owner':owner} if owner is not None else {}))
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
    task_mode = spec.get('schema') == 'upgrade-startup-task-v1'
    require(result["startup"] == runtime["startup"] and result["schema"] ==
            ('upgrade-live-inspection-task-v1' if task_mode else 'upgrade-live-inspection-v2'),
            "INSPECTION_SCOPE_CHANGED")
    require(0 <= time.time_ns() - result["observed_at_ns"] < 30 * 10**9,
            "INSPECTION_TOO_OLD_FOR_ADOPTION")
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    proof = dict(schema='upgrade-live-adoption-task-v1' if task_mode else 'upgrade-live-adoption-v2',
                 runtime=runtime_ref, startup=runtime["startup"],
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
    held clients may first exec. TASK starts its server here with one complete
    sandbox, outside the already sandboxed TUI. Never retries or releases
    another group, and never rewrites an earlier request.
    """
    import cmux_daemon_identity as identity
    import upgrade_schedule as schedule
    from orchestration_permit import read_permit, require_running
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
    state = schedule.status(registration_ref)
    # A halt keeps the active permit for closeout only; it is never release authority.
    require(state.get("status") != "HALTED" and state.get("halt") is None, "RELEASE_REFUSED_SCHEDULE_HALTED")
    active = state.get("active")
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
    task_owner = None
    from upgrade_startup_contract import TASK_SCHEMA
    if spec.get('schema') == TASK_SCHEMA:
        from upgrade_task_launcher import observe_launch
        held_request = json_ref(file_ref(control_dir/'held-request.json'))
        original_owner = held_request['task_owner']
        intent = json_ref(original_owner['intent'])
        from upgrade_driver import Driver
        driver = Driver(intent['driver'])
        driver._check_overall_deadline()
        require(held_request['runtime'] == runtime_ref and held_request['startup'] == runtime['startup']
                and intent['registration'] == registration_ref and intent['group'] == group
                and driver.registration_ref == registration_ref
                and driver.config['runtimes'].get(group) == runtime_ref,
                'TASK_RELEASE_ORIGINAL_OWNER_REQUIRED')
        task_owner = observe_launch(runtime_ref, intent['driver'], expected_group=group)
        require(task_owner == original_owner, 'TASK_RELEASE_OWNER_CHANGED')
    require_running(registration_ref)
    external_publisher(spec)
    request = write_json(control_dir / "launch-request.json", dict(runtime=runtime_ref,
        requested_at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id(), startup=runtime["startup"],
        held_release=dict(permit=permit["step"], group=group, receipt=receipt, held=held),
        **({'task_owner':task_owner} if task_owner is not None else {})), exclusive=True)
    ref = runtime["startup"]
    argv = [str(SANDBOX), "-f", spec["pins"]["control_sandbox"]["path"], runtime["python"]["path"], "-I", "-B",
            str(Path(runtime["controller"]["path"]) / "scripts/mac_upgrade_live_entry.py"), "experiment-release",
            "--startup", ref["path"], "--startup-sha256", ref["sha256"],
            "--receipt", receipt["path"], "--receipt-sha256", receipt["sha256"]]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=180, cwd=spec["work_root"],
                            env=clean_child_environment({}), check=False)
    released = (startup_dir / "release.json").exists()
    server_result = None
    if result.returncode == 0 and released and task_owner is not None:
        from upgrade_task_server import launch as launch_task_server
        server_result = launch_task_server(runtime_ref, registration_ref, group, request, receipt)
    return dict(status="RELEASED" if result.returncode == 0 and released else "RELEASE_UNCONFIRMED",
                launch_request=request, argv=argv, returncode=result.returncode, stdout=result.stdout,
                stderr=result.stderr, input_operations=0,
                **({'server_result': server_result} if server_result is not None else {}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "launch", "hold", "adopt", "bootstrap", "status", "stop"))
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--campaign")
    parser.add_argument("--campaign-sha256")
    parser.add_argument("--instructions")
    parser.add_argument("--instructions-sha256")
    parser.add_argument('--driver')
    parser.add_argument('--driver-sha256')
    parser.add_argument('--credential-fd', type=int)
    args = parser.parse_args(argv)
    try:
        runtime_ref = anchored_ref(args.runtime, args.runtime_sha256)
        if args.command == "bootstrap":
            require(not any((args.driver, args.driver_sha256)) and args.credential_fd is None,
                    'TASK_OWNER_ARGUMENTS_FOR_HOLD_ONLY')
            require(all((args.campaign, args.campaign_sha256, args.instructions, args.instructions_sha256)),
                    "PINNED_CAMPAIGN_AND_INSTRUCTIONS_REQUIRED")
            value = publish_bootstrap(runtime_ref, anchored_ref(args.campaign, args.campaign_sha256),
                                      anchored_ref(args.instructions, args.instructions_sha256))
        else:
            require(not any((args.campaign, args.campaign_sha256, args.instructions, args.instructions_sha256)),
                    "BOOTSTRAP_ARGUMENTS_FOR_BOOTSTRAP_ONLY")
            require(bool(args.driver) == bool(args.driver_sha256), 'TASK_OWNER_DRIVER_PIN_REQUIRED')
            value = control(runtime_ref, args.command,
                driver_ref=anchored_ref(args.driver, args.driver_sha256) if args.driver else None,
                credential_fd=args.credential_fd)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if value["status"] not in ("NOT_READY", "LAUNCH_UNCONFIRMED", "HELD_LAUNCH_UNCONFIRMED") else 2
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
