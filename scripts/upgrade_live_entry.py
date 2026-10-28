"""Private harness startup extension; not a second message sender.

The only creation operation is one cmux new-workspace with an initial layout.
Each client inherits Seatbelt before reaching this entrypoint. Existing agent
input, handshake, tasks and callbacks remain on the original MAC bridge.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid

from orchestration_common import (ContractError, checked_path, digest, file_ref, json_ref,
                                  read_ref, read_regular, utc_now, write_json)
from upgrade_client_isolation import LANES, SANDBOX, clean_child_environment
from upgrade_startup_contract import (child_budget_enabled, pre_exec_hold_enabled,
                                      response_observation_enabled, validate_startup)
from upgrade_socket_boundary import binding_boundary

HOLD_POLL_SECONDS = 0.5
HOLD_DEADLINE_SECONDS = 24 * 3600


def require(value, message):
    if not value:
        raise ContractError(message)


def hold(ref, spec, binding, lane, launched):
    """Wait before any native exec or server start until one exact release names this lane.

    The held record is exclusive and stays as the hold artifact. A release that
    names another lane's record, another startup or another workspace never
    releases this one; stop and the deadline end the hold without exec.
    """
    control = Path(binding["state_root"]) / "startup"
    record = json_ref(launched, "client launch record")
    held = write_json(control / (lane + "-held.json"), dict(
        schema="upgrade-client-held-v1", startup=ref, lane=lane, held_at=utc_now(), launched=launched,
        workspace_uuid=record["workspace_uuid"], surface_uuid=record["surface_uuid"],
        tty=os.ttyname(0) if os.isatty(0) else None, native_exec=0, server_starts=0,
        input_operations=0), exclusive=True)
    path, deadline = control / "release.json", time.monotonic() + HOLD_DEADLINE_SECONDS
    while True:
        require(not Path(spec["stop_path"]).exists(), "EXPERIMENT_STOP_REQUESTED")
        if path.exists():
            # One read: the digest and the parsed value come from the same bytes.
            raw = read_regular(path)
            release = json.loads(raw)
            require(release.get("schema") == "upgrade-client-release-v1" and release.get("startup") == ref
                    and release.get("workspace_uuid") == record["workspace_uuid"]
                    and (release.get("held") or {}).get(lane) == held, "RELEASE_DOES_NOT_NAME_THIS_LANE")
            return dict(path=str(path), sha256=digest(raw), bytes=len(raw))
        require(time.monotonic() < deadline, "PRE_EXEC_HOLD_DEADLINE: no release, no exec")
        time.sleep(HOLD_POLL_SECONDS)


def release(ref, spec, binding, receipt_path, receipt_sha256):
    """Write the one release after the exact exclusion receipt names every held lane.

    The receipt is the maintainer's frozen exclusion evidence: it must name the
    workspace and every held surface in the uppercase form written to the
    shared configuration. Releasing twice is refused by the exclusive write.
    """
    require(pre_exec_hold_enabled(binding), "PRE_EXEC_HOLD_NOT_BOUND")
    require(not Path(spec["stop_path"]).exists(), "EXPERIMENT_STOP_REQUESTED")
    receipt = file_ref(checked_path(receipt_path))
    require(receipt["sha256"] == receipt_sha256, "EXCLUSION_RECEIPT_PIN_CHANGED")
    text = read_ref(receipt, "exclusion receipt").decode("utf-8")
    control = Path(binding["state_root"]) / "startup"
    held, workspaces = {}, set()
    for lane in LANES:
        ref_held = file_ref(control / (lane + "-held.json"))
        value = json_ref(ref_held, lane + " held record")
        record = json_ref(value["launched"], lane + " launch record")
        require(value.get("schema") == "upgrade-client-held-v1" and value.get("startup") == ref
                and value.get("lane") == lane and value.get("native_exec") == 0
                and record.get("lane") == lane and record.get("surface_uuid") == value.get("surface_uuid")
                and record.get("workspace_uuid") == value.get("workspace_uuid"), "HELD_RECORD_CHANGED")
        require(value["surface_uuid"].upper() in text, "EXCLUSION_RECEIPT_OMITS_SURFACE")
        held[lane] = ref_held
        workspaces.add(value["workspace_uuid"])
    require(len(workspaces) == 1, "HELD_LANES_SPAN_WORKSPACES")
    workspace = workspaces.pop()
    require(workspace.upper() in text, "EXCLUSION_RECEIPT_OMITS_WORKSPACE")
    return write_json(control / "release.json", dict(
        schema="upgrade-client-release-v1", startup=ref, workspace_uuid=workspace, held=held,
        exclusion_receipt=receipt, released_at=utc_now(), input_operations=0), exclusive=True)


def load_startup(path, sha256):
    ref = file_ref(checked_path(path))
    require(ref["sha256"] == sha256, "STARTUP_PIN_CHANGED")
    source = Path(__file__).resolve().parent.parent
    spec, binding = validate_startup(ref, controller=source)
    return ref, spec, binding


def stage_bound(source):
    """A stage release names its fence in its own binding. Without a binding,
    load_startup refuses exactly as before."""
    path = source / "upgrade-binding.json"
    return path.is_file() and "stage_fence" in json.loads(read_regular(path))


def start_server(ref, spec, binding, env):
    """Start the one pinned private server, with no automatic restart."""
    import cmux_daemon_identity as identity
    control = Path(binding["state_root"]) / "startup"
    socket = Path(spec["rpc"]["path"])
    binding_boundary(binding, binary_pin=spec["pins"]["codex"], fresh=True)
    require(not os.path.lexists(socket), "ORIGINAL_CONTROL_SOCKET_ALREADY_EXISTS")
    require(not Path(spec["stop_path"]).exists(), "EXPERIMENT_STOP_REQUESTED")
    # With station launchers mg execs the pinned binary in place (same PID);
    # spec["rpc"]["argv"] is then the final native argv the verifier compares.
    launch_argv = spec["rpc"].get("launch_argv", spec["rpc"]["argv"])
    env = dict(env, **spec["rpc"].get("launch_environment", {}))
    write_json(control / "codex-server-intent.json", dict(
        schema="upgrade-server-intent-v2", startup=ref, created_at=utc_now(),
        argv=spec["rpc"]["argv"], input_operations=0,
        **({"launch_argv": launch_argv} if "launch_argv" in spec["rpc"] else {})), exclusive=True)
    # umask restricts the socket and server-created observations. The server
    # inherits the same kernel sandbox as its TUI and all descendant tools.
    # subprocess.DEVNULL opens the device O_RDWR, which violates the private
    # controller's write audit. Stdin only needs a read-only EOF descriptor.
    with open(os.devnull, "rb") as null_input, (control / "codex-server.log").open("xb") as log:
        child = subprocess.Popen(launch_argv, stdin=null_input,
            stdout=log, stderr=subprocess.STDOUT, cwd=spec["work_root"], env=env,
            close_fds=True, start_new_session=True, umask=0o077)
    row = identity.process(child.pid, validate_argv=False)
    write_json(control / "codex-server.json", dict(
        schema="upgrade-server-launch-v2", startup=ref, created_at=utc_now(),
        pid=row["pid"], birth=row["birth"], argv=spec["rpc"]["argv"],
        surface_uuid=str(uuid.UUID(env["CMUX_SURFACE_ID"])),
        workspace_uuid=str(uuid.UUID(env["CMUX_WORKSPACE_ID"])), input_operations=0), exclusive=True)
    deadline = time.monotonic() + 20
    while child.poll() is None and not socket.exists() and time.monotonic() < deadline:
        require(not Path(spec["stop_path"]).exists(), "EXPERIMENT_STOP_REQUESTED")
        time.sleep(0.05)
    status = "SOCKET_CREATED" if socket.is_socket() and child.poll() is None else "UNKNOWN"
    write_json(control / "codex-server-result.json", dict(
        schema="upgrade-server-result-v2", startup=ref, observed_at=utc_now(),
        status=status, returncode=child.poll(), pid=child.pid, input_operations=0), exclusive=True)
    require(status == "SOCKET_CREATED", "PRIVATE_SERVER_START_UNCONFIRMED: preserve original intent")


def layout_for(ref, spec):
    panes = []
    for lane in LANES:
        command = [str(SANDBOX), "-f", spec["pins"][lane + "_sandbox"]["path"],
                   spec["pins"]["python"]["path"], "-I", "-B",
                   str(Path(spec["controller"]) / "scripts/mac_harness.py"),
                   "experiment-client", "--startup", ref["path"], "--startup-sha256", ref["sha256"],
                   "--lane", lane]
        panes.append({"pane": {"surfaces": [{"type": "terminal", "command": shlex.join(command)}]}})
    return {"direction": "horizontal", "split": 0.42, "children": [panes[0],
            {"direction": "vertical", "split": 0.5, "children": panes[1:]}]}


def launch(ref, spec, binding, *, spawn=False, spawn_authorized=False):
    require(spawn and spawn_authorized, "SPAWN_REQUIRES_BOTH_FLAGS")
    binding_boundary(binding, binary_pin=spec["pins"]["codex"], fresh=True)
    control = Path(binding["state_root"]) / "startup"
    require(not Path(spec["stop_path"]).exists(), "EXPERIMENT_STOP_REQUESTED")
    from upgrade_child_budget_protocol import verify_child_hook_binary
    child_capability = (verify_child_hook_binary(spec["pins"]["claude"])
                        if child_budget_enabled(binding) else None)
    layout = layout_for(ref, spec)
    command = [spec["pins"]["cmux"]["path"], "--json", "new-workspace", "--name", spec["workspace_name"],
               "--cwd", spec["work_root"], "--focus", "false", "--layout",
               json.dumps(layout, ensure_ascii=False, separators=(",", ":"))]
    intent = dict(schema="upgrade-startup-intent-v2", startup=ref, created_at=utc_now(),
                  argv=command, spawn=True, spawn_authorized=True, input_operations=0,
                  **({"child_hook_capability": child_capability} if child_capability is not None else {}))
    # Exclusive/fsynced before the single creation request. A timeout or crash
    # leaves this intent in place; never retry creation or choose replacements.
    write_json(control / "intent.json", intent, exclusive=True)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        value = dict(schema="upgrade-startup-result-v2", startup=ref, observed_at=utc_now(),
                     returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
                     workspace_creation_attempts=1, input_operations=0)
        write_json(control / "result.json", value, exclusive=True)
        require(result.returncode == 0, "WORKSPACE_CREATION_UNCONFIRMED: inspect the original attempt")
        return value
    except subprocess.TimeoutExpired as exc:
        write_json(control / "timeout.json", dict(startup=ref, observed_at=utc_now(),
            status="UNKNOWN", workspace_creation_attempts=1, input_operations=0), exclusive=True)
        raise ContractError("WORKSPACE_CREATION_UNKNOWN: no retry or replacement") from exc


def start_client(ref, spec, binding, lane):
    require(lane in LANES, "STARTUP_LANE_UNKNOWN")
    intent = json.loads(read_regular(Path(binding["state_root"]) / "startup/intent.json"))
    require(intent.get("startup") == ref and intent.get("spawn") is True
            and intent.get("spawn_authorized") is True, "ORIGINAL_STARTUP_INTENT_REQUIRED")
    require(not Path(spec["stop_path"]).exists(), "EXPERIMENT_STOP_REQUESTED")
    # cmux sets these for the newly created pane; the launcher never invents
    # selectors or inherits a root thread identity from the controlling client.
    surface = str(uuid.UUID(os.environ["CMUX_SURFACE_ID"]))
    workspace = str(uuid.UUID(os.environ["CMUX_WORKSPACE_ID"]))
    require(surface not in spec["protected_surface_uuids"], "PRODUCTION_SURFACE_REFUSED")
    client = spec["clients"][lane]
    env = clean_child_environment(client["environment"])
    # Only the validated opt-in contract can introduce observer state. A parent
    # process's observer identity/directory must never leak into these clients.
    for name in tuple(env):
        if name.startswith("UPGRADE_RESPONSE_"):
            del env[name]
    if response_observation_enabled(binding) and lane != "codex":
        env["UPGRADE_RESPONSE_OBSERVATIONS_DIR"] = client["environment"]["UPGRADE_RESPONSE_OBSERVATIONS_DIR"]
        # execve through the pinned native wrapper preserves this PID. The
        # preload independently checks it before installing any fetch observer.
        env["UPGRADE_RESPONSE_OBSERVER_ROOT_PID"] = str(os.getpid())
    import cmux_daemon_identity as identity
    process = identity.process(os.getpid())
    record = dict(schema="upgrade-client-launch-v2", startup=ref, lane=lane, created_at=utc_now(),
                  pid=process["pid"], birth=process["birth"], surface_uuid=surface,
                  workspace_uuid=workspace, native_root=client["native_root"],
                  planned_session_id=client.get("session_id"), provider=client["provider"])
    launched = write_json(Path(binding["state_root"]) / "startup" / (lane + ".json"), record, exclusive=True)
    if pre_exec_hold_enabled(binding):
        # Held before chdir, the Codex server and exec: the pane's exact UUIDs
        # exist, but no native client, provider request or input does yet.
        hold(ref, spec, binding, lane, launched)
    os.chdir(spec["work_root"])
    os.umask(0o077)
    if lane == "codex":
        start_server(ref, spec, binding, env)
    require(not Path(spec["stop_path"]).exists(), "EXPERIMENT_STOP_REQUESTED")
    # Native CLI positional input is the pinned initial readiness instruction,
    # not an existing-session sender or an invented receipt.
    os.execve(client["argv"][0], client["argv"], env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("experiment-launch", "experiment-client", "experiment-inspect",
                                            "experiment-release"))
    parser.add_argument("--startup", required=True)
    parser.add_argument("--startup-sha256", required=True)
    parser.add_argument("--spawn", action="store_true")
    parser.add_argument("--spawn-authorized", action="store_true")
    parser.add_argument("--lane", choices=LANES)
    parser.add_argument("--receipt")
    parser.add_argument("--receipt-sha256")
    args = parser.parse_args(argv)
    try:
        source = Path(__file__).resolve().parent.parent
        if stage_bound(source):
            # A fenced stage only inspects the original clients; it never
            # launches, starts or replaces a client.
            from upgrade_stage_fence import load_stage
            require(args.command == "experiment-inspect", "STAGE_INSPECT_ONLY")
            ref, spec, binding, stage = load_stage(source)
            require(str(checked_path(args.startup)) == ref["path"] and args.startup_sha256 == ref["sha256"],
                    "STARTUP_PIN_CHANGED")
            from upgrade_live_inspect import inspect
            print(json.dumps(inspect(ref, spec, binding, stage=stage), ensure_ascii=False, indent=2))
            return 0
        ref, spec, binding = load_startup(args.startup, args.startup_sha256)
        if args.command == "experiment-launch":
            value = launch(ref, spec, binding, spawn=args.spawn, spawn_authorized=args.spawn_authorized)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.command == "experiment-inspect":
            from upgrade_live_inspect import inspect
            value = inspect(ref, spec, binding)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        elif args.command == "experiment-release":
            require(args.receipt and args.receipt_sha256, "EXCLUSION_RECEIPT_REQUIRED")
            value = release(ref, spec, binding, args.receipt, args.receipt_sha256)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            require(args.lane is not None, "STARTUP_LANE_REQUIRED")
            start_client(ref, spec, binding, args.lane)
        return 0
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
