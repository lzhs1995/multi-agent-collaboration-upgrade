#!/usr/bin/env python3
"""Freeze an isolated MAC copy; never install it or start a client.

A0 has isolation changes only. A1 and B additionally repair original-gate ACK
matching. Orchestration belongs to the existing upgrade coordinator, not a fork
of the MAC task state machine. All copies are offline until live adoption passes.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import shlex
import sys

from orchestration_common import (ContractError, digest, file_ref, read_ref, read_regular,
                                  tree_manifest, verify_tree, write_bytes_once, write_json)
from orchestration_campaign import anchored_ref
from orchestration_native import require
from upgrade_experiment import NAME, ARMS, arm_paths, validate_context
from upgrade_controller_patch import transform_python


def literal_assignment(raw, name):
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
                require(len(value.args) == 1 and not value.keywords, "changed source constant")
                value = value.args[0]
            return ast.literal_eval(value)
    raise ContractError("missing source constant: " + name)


def hook_config(source, controller, python):
    # Read literal wiring from the pinned original. Do not import controller
    # modules into this materializer or execute any old install/doctor action.
    guards = literal_assignment(read_regular(source / "scripts/manage_install.py"), "GUARDS")
    advisory = literal_assignment(read_regular(source / "scripts/cmux_workflow_advisory.py"), "WORKFLOW_HOOKS")
    hooks = {}
    for name, event in guards.items():
        filename = name + ".py"
        command = [python, "-I", "-B"]
        if filename in advisory:
            command += [str(controller / "scripts/cmux_workflow_advisory.py"), "--hook", filename]
        else:
            command += [str(controller / "scripts" / filename)]
        hooks.setdefault(event, []).append({"matcher": "*", "hooks": [
            {"type": "command", "command": shlex.join(command)}]})
    return {"hooks": hooks}


def rendered_helper(source, controller, python):
    # The original renderer is pure: compile a separately named copy with no
    # __main__ execution. The inputs and every output byte are manifest-bound.
    filename = source / "scripts/render_cmux_agent.py"
    namespace = {"__name__": "_private_materializer_renderer", "__file__": str(filename)}
    exec(compile(read_regular(filename), str(filename), "exec"), namespace)
    return namespace["render"](
        read_regular(source / "tests/fixtures/cmux-agent-legacy.sh"),
        expected_sha256=namespace["BASELINE_SHA256"], python=python,
        adapter=controller / "scripts/cmux_agent_adapter.py")


def materialize(context_ref, arm):
    context = validate_context(context_ref)
    paths = arm_paths(context, arm)
    require(not any(p.exists() for p in paths.values()),
            "arm already exists; inspect the original manifest instead of overwriting")
    source_pin = context["source_controller"]
    verify_tree(source_pin)
    read_ref(context["python"], "materializer Python")
    source = Path(source_pin["path"])
    release = paths["controllers"] / "releases" / "private-v1"
    controller = release / "source"
    script_dir = Path(__file__).resolve().parent
    additions = ["upgrade_controller_scope.py"]
    if arm != "A0":
        additions.append("upgrade_handshake_identity.py")
    buffers, changes = {}, []
    for name, old in source_pin["files"].items():
        require(name not in ("upgrade-binding.json", *["scripts/" + n for n in additions]),
                "source is already a private controller")
        raw = read_regular(source / name)
        kinds = []
        if Path(name).parent == Path("scripts") and name.endswith(".py"):
            text, kinds = transform_python(Path(name).name, raw.decode("utf-8"), paths,
                                           controller, compatibility=arm != "A0")
            raw = text.encode("utf-8")
        buffers[name] = (raw, bool(old["mode"] & 0o111))
        if raw != read_regular(source / name):
            changes.append(dict(path=name, transformations=kinds,
                                before={"sha256": old["sha256"], "bytes": old["bytes"]},
                                after={"sha256": digest(raw), "bytes": len(raw)}))
    for name in additions:
        raw = read_regular(script_dir / name)
        buffers["scripts/" + name] = (raw, False)
        changes.append(dict(path="scripts/" + name, transformations=["private_support_module"],
                            source=file_ref(script_dir / name), before=None,
                            after=dict(sha256=digest(raw), bytes=len(raw))))
    binding = dict(version=1, context=context_ref, arm=arm, controller_root=str(controller),
                   state_root=str(paths["state"]), config_root=str(paths["config"]),
                   campaign_root=str(paths["campaigns"]), evidence_root=str(paths["evidence"]),
                   tmp_root=str(paths["tmp"]), execution_mode="offline")
    config = hook_config(source, controller, context["python"]["path"])
    # Publish only after every transformation has compiled and all source pins
    # still match. A partial filesystem publication is retained, never resumed
    # as a completed runtime or silently overwritten.
    verify_tree(source_pin)
    for path in paths.values():
        path.mkdir(mode=0o700, exist_ok=False)
    controller.mkdir(parents=True, mode=0o700)
    for name in source_pin["directories"]:
        (controller / name).mkdir(parents=True, exist_ok=True)
    for name, (raw, executable) in buffers.items():
        path = controller / name
        write_bytes_once(path, raw)
        path.chmod(0o555 if executable else 0o444)
    write_json(controller / "upgrade-binding.json", binding, exclusive=True)
    (controller / "upgrade-binding.json").chmod(0o444)
    private_pin = tree_manifest(controller)
    manifest_ref = write_json(release / "MANIFEST.json", {
        "schema": "upgrade-private-controller-v1", "arm": arm, "source": source_pin,
        "files": {n: dict(sha256=p["sha256"], bytes=p["bytes"], executable=bool(p["mode"] & 0o111))
                  for n, p in private_pin["files"].items()}}, exclusive=True)
    Path(manifest_ref["path"]).chmod(0o444)
    config_refs = {}
    for filename in ("codex-hooks.json", "claude-settings.json"):
        config_refs[filename] = write_json(paths["config"] / filename, config, exclusive=True)
        Path(config_refs[filename]["path"]).chmod(0o444)
    helper = write_bytes_once(paths["bin"] / "cmux-agent",
                              rendered_helper(source, controller, context["python"]["path"]))
    Path(helper["path"]).chmod(0o555)
    patches = write_json(paths["controllers"] / "patches.json", dict(
        version=1, arm=arm, source=source_pin, changes=changes,
        compatibility_fixes=[] if arm == "A0" else ["original_gate_canonical_ack"],
        excluded_claims=["active_client_adoption", "native_delivery", "measured_efficiency"]), exclusive=True)
    Path(patches["path"]).chmod(0o444)
    runtime = dict(version=1, skill=NAME, context=context_ref, arm=arm,
                   execution_mode="offline", source_controller=source_pin, controller=private_pin,
                   release_manifest=manifest_ref, patches=patches, helper=helper, configs=config_refs,
                   state_root=str(paths["state"]), config_root=str(paths["config"]),
                   python=context["python"], production_adoption=False)
    verify_tree(source_pin)
    verify_tree(private_pin)
    ref = write_json(paths["controllers"] / "runtime.json", runtime, exclusive=True)
    Path(ref["path"]).chmod(0o444)
    return ref


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--experiment-sha256", required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    args = parser.parse_args(argv)
    try:
        result = materialize(anchored_ref(args.experiment, args.experiment_sha256), args.arm)
        print(json.dumps(result, indent=2))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
