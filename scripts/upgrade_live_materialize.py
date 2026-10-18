"""Freeze a new live-v2 candidate without starting a client or installing MAC."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from orchestration_common import (ContractError, checked_path, digest, file_ref, read_ref,
                                  read_regular, tree_manifest, verify_tree, write_bytes_once, write_json)
from orchestration_native import require
from upgrade_client_isolation import (LANES, NATIVE_POLICY, claude_config, client_argv, client_temp_base,
    claude_ps_wrapper, codex_config, control_socket, patch_cmux_wrapper, private_native_roots,
    prepare_client_temp, read_provider_configuration, sandbox_profile)
from upgrade_controller_patch import add_bootstrap
from upgrade_experiment import NAME, arm_paths, protected_paths, validate_context
from upgrade_live_integrity import artifact_ref
from upgrade_live_patch import PRIVATE_MODULES, private_imports, transform_live
from upgrade_materialize import hook_config, rendered_helper
from upgrade_startup_contract import client_environment, readiness_prompt, validate_startup
from upgrade_socket_boundary import SOCKET_POLICY, boundary_plan, extend_profile
from upgrade_body_read import reader_instructions

SUPPORT = ("upgrade_controller_scope.py", "upgrade_handshake_identity.py",
           "upgrade_native_roots.py", "upgrade_client_isolation.py", "upgrade_live_entry.py",
           "upgrade_live_integrity.py", "upgrade_client_probe.py", "upgrade_startup_contract.py",
           "upgrade_hook_observer.py", "upgrade_input_gate.py", "upgrade_live_inspect.py", "upgrade_bootstrap.py",
           "orchestration_common.py", "upgrade_process_inventory.py", "upgrade_socket_boundary.py",
           "upgrade_claude_ps.py", "upgrade_standalone_identity.py", "upgrade_claude_registration.py",
           "upgrade_handshake_capability.py", "upgrade_body_read.py")


def immutable_bytes(path, raw, *, executable=False, secret=False):
    ref = write_bytes_once(path, raw)
    Path(ref["path"]).chmod(0o400 if secret else (0o555 if executable else 0o444))
    return artifact_ref(Path(ref["path"]))


def materialize_live(context_ref, arm, *, binaries, protected_surface_uuids=(),
                     provider_configuration=None, credential_files=None, native_policy=NATIVE_POLICY):
    context = validate_context(context_ref)
    require(arm in ("A1", "B"), "A0 is a retained historical control, not a new live lane")
    paths = arm_paths(context, arm)
    require(native_policy in (NATIVE_POLICY, SOCKET_POLICY), "SOCKET_POLICY_UNKNOWN")
    require(not any(p.exists() for p in paths.values()),
            "arm exists; preserve it and inspect its original runtime")
    source_pin = context["source_controller"]
    verify_tree(source_pin)
    source, support = Path(source_pin["path"]), Path(__file__).resolve().parent
    release = paths["controllers"] / "releases/private-live-v2"
    controller, roots = release / "source", private_native_roots(paths)
    socket = control_socket(paths)
    # Reject incompatible real paths before creating any arm artifacts.
    for lane in LANES:
        client_temp_base(paths, lane)
    require(set(binaries) == {"codex", "claude", "codex_wrapper", "claude_wrapper", "cmux", "rtk", "node"},
            "explicit native programs and wrappers required")
    executable_pins = {}
    for name, value in binaries.items():
        path = checked_path(value)
        require(path.is_file() and os.access(path, os.X_OK), "startup executable missing: " + name)
        executable_pins[name] = artifact_ref(path)
    socket_boundary = (boundary_plan(socket, executable_pins["codex"])
                       if native_policy == SOCKET_POLICY else None)
    read_ref(context["python"], "startup Python")
    codex_original, claude_original = (read_provider_configuration()
        if provider_configuration is None else provider_configuration)
    credential_files = {} if credential_files is None else credential_files
    require(set(credential_files) <= {"codex", "claude"}, "unknown credential source")
    secrets = {name: read_regular(checked_path(path)) for name, path in credential_files.items()}
    # Keep account HOME. Only these newly created client processes receive
    # private CLI roots; no shell profile, global variable or installed alias changes.
    common_immutable = [paths["controllers"], paths["config"], paths["bin"],
                        paths["evidence"] / "live-control", paths["state"] / "work/AGENTS.md",
                        paths["state"] / "work/CLAUDE.md"]
    for lane, root in roots.items():
        common_immutable += [root / n for n in (("config.toml", "hooks.json", "auth.json", "AGENTS.md",
                                                "credential-observations/enabled-v1")
            if lane == "codex" else ("settings.json", ".credentials.json", "CLAUDE.md"))]
    existing_protected = sorted({str(p.resolve()) for p in protected_paths() if p.exists()})
    denied_probes = existing_protected + [str(controller / "SKILL.md")]
    isolation = {"immutable": list(map(str, common_immutable)), "denied_probes": denied_probes,
                 "write_probes": [str(paths["state"] / "kernel-write-probe")]}
    buffers, changes = {}, []
    for name, old in source_pin["files"].items():
        require(name != "upgrade-binding.json" and name not in {"scripts/" + n for n in SUPPORT},
                "source is already an upgrade controller")
        raw = read_regular(source / name)
        kinds = []
        if Path(name).parent == Path("scripts") and name.endswith(".py"):
            text, kinds = transform_live(Path(name).name, raw.decode(), paths, controller, compatibility=True)
            raw = text.encode()
        elif name == "SKILL.md":
            # The original full contract is retained, with an explicit private
            # scope at its front. Never install this tree as production MAC.
            raw = ("# Private compatibility controller for multi-agent-collaboration-upgrade\n\n"
                   "Use only this frozen controller, its private helper/configuration/state and the "
                   "three dedicated experiment sessions. Never install globally, contact production "
                   "surfaces, create replacements or replay old attempts. HOME stays unchanged; "
                   "the new clients have separately pinned CODEX_HOME/CLAUDE_CONFIG_DIR. "
                   "Actual automatic hooks and native identities must pass adoption before task input. "
                   "Initial handshake uses a read-only bound capability check followed by one real "
                   "challenge/ACK; it does not run the legacy write-and-clear probe. Busy work or "
                   "native /goal alone does not block communication. Preserve drafts and original "
                   "attempts; never repeat a pending or failed challenge. The explicit bridge-test "
                   "remains a separate diagnostic. No bridge-clear success is claimed for read-only checks. "
                   "Original MAC native reception, ACK, callback and acceptance gates below still apply.\n\n"
                   + raw.decode()).encode()
            kinds = ["explicit_private_controller_scope"]
        buffers[name] = (raw, bool(old["mode"] & 0o111))
        if digest(raw) != old["sha256"]:
            changes.append({"path": name, "transformations": kinds,
                "before": {"sha256": old["sha256"], "bytes": old["bytes"]},
                "after": {"sha256": digest(raw), "bytes": len(raw)}})
    for name in SUPPORT:
        raw = read_regular(support / name)
        if name in ("upgrade_live_entry.py", "upgrade_client_probe.py", "upgrade_bootstrap.py", "upgrade_claude_ps.py", "upgrade_body_read.py"):
            raw = add_bootstrap(raw.decode()).replace("_upgrade_install_scope()",
                                                     "_upgrade_install_scope(live=True)").encode()
        raw = private_imports(raw.decode()).encode()
        private_name = PRIVATE_MODULES[Path(name).stem] + ".py"
        compile(raw, private_name, "exec", dont_inherit=True)
        buffers["scripts/" + private_name] = (raw, False)
        changes.append({"path": "scripts/" + private_name, "source": file_ref(support / name),
                        "transformations": ["private_live_support", "distinct_module_namespace"],
                        "after": {"sha256": digest(raw), "bytes": len(raw)}})
    hooks = hook_config(source, controller, context["python"]["path"])
    wrappers = {provider: patch_cmux_wrapper(provider,
        read_regular(binaries[provider + "_wrapper"]), binaries[provider], paths, node=binaries["node"])
        for provider in ("codex", "claude")}
    binding = dict(version=2, context=context_ref, arm=arm, controller_root=str(controller),
                   state_root=str(paths["state"]), config_root=str(paths["config"]),
                   campaign_root=str(paths["campaigns"]), evidence_root=str(paths["evidence"]),
                   tmp_root=str(paths["tmp"]), execution_mode="live-isolated-v2",
                   native_policy=native_policy, native_roots={k: str(v) for k, v in roots.items()},
                   codex_control_socket=str(socket),
                   isolation=isolation)
    if socket_boundary is not None:
        binding["socket_boundary"] = socket_boundary
    verify_tree(source_pin)
    for path in paths.values():
        path.mkdir(mode=0o700, exist_ok=False)
    controller.mkdir(parents=True, mode=0o700)
    for directory in source_pin["directories"]:
        (controller / directory).mkdir(parents=True, exist_ok=True)
    for name, (raw, executable) in buffers.items():
        immutable_bytes(controller / name, raw, executable=executable)
    write_json(controller / "upgrade-binding.json", binding, exclusive=True)
    (controller / "upgrade-binding.json").chmod(0o444)
    private_pin = tree_manifest(controller)
    manifest = write_json(release / "MANIFEST.json", dict(schema="upgrade-private-controller-v2",
        arm=arm, source=source_pin, files=private_pin["files"]), exclusive=True)
    Path(manifest["path"]).chmod(0o444)
    pins = {k: v for k, v in executable_pins.items() if not k.endswith("_wrapper")}
    pins["python"] = context["python"]
    (paths["bin"] / "claude-tools").mkdir(mode=0o700)
    pins["claude_ps"] = immutable_bytes(paths["bin"] / "claude-tools/ps",
        claude_ps_wrapper(controller, context["python"]["path"]), executable=True)
    for provider in ("codex", "claude"):
        pins[provider + "_original_wrapper"] = executable_pins[provider + "_wrapper"]
        pins[provider + "_wrapper"] = immutable_bytes(paths["bin"] / ("cmux-" + provider + "-wrapper"),
                                                      wrappers[provider], executable=True)
    helper = immutable_bytes(paths["bin"] / "cmux-agent",
                             rendered_helper(source, controller, context["python"]["path"]), executable=True)
    pins["helper"] = helper
    for name in ("codex-hooks.json", "claude-settings.json"):
        ref = write_json(paths["config"] / name, hooks, exclusive=True)
        Path(ref["path"]).chmod(0o444)
        pins[name] = artifact_ref(Path(ref["path"]))
    (paths["state"] / "kernel-write-probe").write_text("private write probe\n")
    work = paths["state"] / "work"
    work.mkdir(mode=0o700)
    socket_notice = ("The native Codex server additionally has the two exact physical socket/lock "
                     "write paths pinned in upgrade-binding.json; this grants no shared-directory "
                     "or neighboring-daemon writes, and grants neither Claude lane those writes. "
                     if socket_boundary is not None else "")
    policy = ("# Independent upgrade experiment\n\nUse this experiment's private paths and dedicated sessions. "
              + socket_notice + "Read " + str(controller / "SKILL.md") + ". "
              "Use the pinned private cmux-agent/harness for all collaboration. "
              "Never install skills/hooks, contact production sessions, create replacement clients, "
              "change HOME or invoke another message sender. Do not claim adoption from a readiness probe.\n"
              + reader_instructions(context["python"]["path"], controller, executable_pins["rtk"]["path"]))
    for name in ("AGENTS.md", "CLAUDE.md"):
        pins["work_" + name] = immutable_bytes(work / name, policy.encode())
    # This is an explicit startup choice for the newly authorized, OS-sandboxed
    # clients. It contains no imported projects, global MCPs, hooks or identity.
    # The native .claude.json is mutable client state; pin its seed separately.
    claude_seed = dict(hasCompletedOnboarding=True, theme="dark", autoUpdates=False,
        bypassPermissionsModeAccepted=True,
        projects={str(work): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}})
    pins["claude-startup-state.json"] = immutable_bytes(paths["config"] / "claude-startup-state.json",
        (json.dumps(claude_seed, sort_keys=True) + "\n").encode())
    clients = {}
    for lane, root in roots.items():
        root.mkdir(parents=True, mode=0o700)
        for directory in ("log", "cache", "tmp", "sessions"):
            (root / directory).mkdir(mode=0o700)
        prepare_client_temp(paths, lane)
        provider = "codex" if lane == "codex" else "claude"
        if provider == "codex":
            (root / "credential-observations").mkdir(mode=0o700)
            config_files = {"config.toml": codex_config(codex_original, root, hooks),
                            "hooks.json": (json.dumps(hooks, sort_keys=True) + "\n").encode(),
                            "AGENTS.md": policy.encode(),
                            "credential-observations/enabled-v1": b"ccc-request-credentials-v1\n"}
        else:
            config_files = {"settings.json": claude_config(claude_original, hooks), "CLAUDE.md": policy.encode()}
            write_json(root / ".claude.json", claude_seed, exclusive=True)
            (root / ".claude.json").chmod(0o600)
        for name, raw in config_files.items():
            pins[lane + "/" + name] = immutable_bytes(root / name, raw,
                secret=name in ("settings.json", "credential-observations/enabled-v1"))
        if provider in secrets:
            name = "auth.json" if provider == "codex" else ".credentials.json"
            pins[lane + "/" + name] = immutable_bytes(root / name, secrets[provider], secret=True)
        session = None if provider == "codex" else str(uuid.uuid4())
        argv = client_argv(provider, paths["bin"] / ("cmux-" + provider + "-wrapper"), work, root,
                           session_id=session, remote_socket=socket if provider == "codex" else None,
                           socket_boundary=socket_boundary if provider == "codex" else None)
        argv.append(readiness_prompt(controller, context["python"]["path"], binaries["rtk"], lane))
        env = client_environment(roots, paths, lane, binaries["rtk"])
        clients[lane] = dict(provider=provider, native_root=str(root), session_id=session,
                             argv=argv, environment=env)
    writable = [paths[name] for name in ("state", "campaigns", "evidence", "tmp")]
    for lane in ("control", *LANES):
        denied = list(common_immutable)
        if lane != "control":
            denied += [p for key, p in roots.items() if key != lane]
        profile = sandbox_profile(writable, denied)
        if lane == "codex" and socket_boundary is not None:
            profile = extend_profile(profile, socket_boundary)
        pins[lane + "_sandbox"] = immutable_bytes(paths["config"] / (lane + ".sb"), profile)
    patch_ref = write_json(paths["controllers"] / "patches-v2.json", dict(version=2,
        source=source_pin, changes=changes, compatibility_fixes=["original_gate_canonical_ack"],
        live_adoption=False, native_reception=False, measured_efficiency=False), exclusive=True)
    Path(patch_ref["path"]).chmod(0o444)
    startup = dict(schema="upgrade-startup-v2", context=context_ref, arm=arm,
        controller=str(controller), native_policy=native_policy, pins=pins,
        rpc={"path": str(socket), "argv": [executable_pins["codex"]["path"], "app-server", "--listen",
                                          "unix://" + str(socket)]},
        clients=clients, work_root=str(work), workspace_name="upgrade-lab-" + context["experiment_id"] + "-" + arm,
        protected_surface_uuids=sorted({str(uuid.UUID(x)) for x in protected_surface_uuids}),
        stop_path=str(paths["evidence"] / "live-control/stop.json"),
        adoption_path=str(paths["evidence"] / "live-control/adoption.json"),
        authorization={"source": "user: Implement the independent experiment plan",
                       "dedicated_clients": True, "replace_failed_sessions": False,
                       "production_adoption": False})
    if socket_boundary is not None:
        startup["socket_boundary"] = socket_boundary
    startup_ref = write_json(paths["config"] / "startup-v2.json", startup, exclusive=True)
    Path(startup_ref["path"]).chmod(0o444)
    validate_startup(startup_ref, controller=controller)
    runtime = dict(version=2, skill=NAME, context=context_ref, arm=arm, execution_mode="live-isolated-v2",
        source_controller=source_pin, controller=private_pin, release_manifest=manifest, patches=patch_ref,
        startup=startup_ref, state_root=str(paths["state"]), config_root=str(paths["config"]),
        python=context["python"], production_adoption=False)
    verify_tree(source_pin)
    verify_tree(private_pin)
    ref = write_json(paths["controllers"] / "runtime-v2.json", runtime, exclusive=True)
    Path(ref["path"]).chmod(0o444)
    return ref


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--experiment-sha256", required=True)
    parser.add_argument("--arm", choices=("A1", "B"), required=True)
    for name in ("codex", "claude", "codex-wrapper", "claude-wrapper", "cmux", "rtk", "node"):
        parser.add_argument("--" + name, required=True, help="absolute native executable path")
    parser.add_argument("--codex-credentials", help="copy this credential file into the private client root")
    parser.add_argument("--claude-credentials", help="copy this credential file into both private Claude roots")
    parser.add_argument("--protected-surface-uuid", action="append", default=[])
    parser.add_argument("--native-policy", choices=(NATIVE_POLICY, SOCKET_POLICY), default=NATIVE_POLICY,
                        help="prepare an explicit candidate policy; does not authorize client startup")
    args = parser.parse_args(argv)
    try:
        from orchestration_campaign import anchored_ref
        context = anchored_ref(args.experiment, args.experiment_sha256)
        binaries = {name: getattr(args, name) for name in
                    ("codex", "claude", "codex_wrapper", "claude_wrapper", "cmux", "rtk", "node")}
        credentials = {name: getattr(args, name + "_credentials") for name in ("codex", "claude")
                       if getattr(args, name + "_credentials")}
        ref = materialize_live(context, args.arm, binaries=binaries, credential_files=credentials,
                               protected_surface_uuids=args.protected_surface_uuid, native_policy=args.native_policy)
        print(json.dumps(dict(status="MATERIALIZED_NOT_LAUNCHED", runtime=ref, input_operations=0),
                         ensure_ascii=False, indent=2))
        return 0
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="MATERIALIZATION_REFUSED_PRESERVED", error=str(exc),
                              input_operations=0), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
