#!/usr/bin/env python3
"""Build, plan, apply or roll back one fenced validation stage release.

build only writes a new staging directory. plan/apply/rollback read a frozen
PLAN.json: every target is compare-and-swapped under one exclusive lock, the
three original lane hook files are backed up first, and only their hooks field
names the new controller. Nothing here starts, stops, inputs or replaces a
client, changes a model, or touches the original release, startup or failures.
New Claude settings carry provider secrets: they are recomputed at apply time
from the live file and only their SHA256 is ever written to staging.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys

from orchestration_common import (ContractError, canonical, checked_path, digest, exclusive_lock, file_ref,
                                  json_ref, read_regular, require, tree_manifest, utc_now, write_bytes_once)
from orchestration_campaign import anchored_ref
from upgrade_child_budget_protocol import registered_hooks
from upgrade_experiment import arm_paths
from upgrade_live_integrity import artifact_ref
from upgrade_live_materialize import immutable_bytes, render_private_support
from upgrade_live_patch import transform_live
from upgrade_live_runtime import validate_live_runtime
from upgrade_materialize import hook_config, rendered_helper
from upgrade_stage_fence import ADOPTION_SCHEMA, HOOK_PINS, LANES, SCHEMA, control_directory, stage_prompt
from upgrade_startup_contract import child_budget_enabled

PLAN_SCHEMA = "upgrade-stage-install-plan-v1"
MANIFEST_SCHEMA = "upgrade-stage-controller-v1"
# MAC efficiency-and-closeout: 10 GB is the hard floor after completion; below
# 20 GB only triggers active management/backup and is not an install gate.
FREE_FLOOR_BYTES = 10 * 10**9
LANE_FORMAT = {"codex/hooks.json": "codex", "claude_a/settings.json": "claude", "claude_b/settings.json": "claude"}


def lane_document(raw, provider):
    """Parse one lane file and prove it is exactly the materializer's serialization."""
    document = json.loads(raw)
    rendered = (json.dumps(document, sort_keys=True) if provider == "codex"
                else json.dumps(document, sort_keys=True, indent=2)) + "\n"
    require(rendered.encode() == raw, "STAGE_LANE_FILE_NOT_ORIGINAL_SERIALIZATION")
    return document


def provider_hooks(source, controller, python, binding):
    hooks = hook_config(source, controller, python)
    return {provider: (registered_hooks(hooks, python, controller, provider=provider)
                       if child_budget_enabled(binding) else hooks) for provider in ("codex", "claude")}


def rewritten_hook_file(key, raw, original_hooks, stage_hooks):
    """Only the hooks field changes, from the exact original wiring to the stage's."""
    provider = LANE_FORMAT[key]
    document = lane_document(raw, provider)
    require(document.get("hooks") == original_hooks[provider]["hooks"], "STAGE_LANE_HOOKS_NOT_ORIGINAL:" + key)
    changed = dict(document, hooks=copy.deepcopy(stage_hooks[provider]["hooks"]))
    require(set(changed) == set(document) and all(changed[k] == document[k] for k in document if k != "hooks"),
            "STAGE_NON_HOOK_FIELD_CHANGED:" + key)
    rendered = (json.dumps(changed, sort_keys=True) if provider == "codex"
                else json.dumps(changed, sort_keys=True, indent=2)) + "\n"
    return rendered.encode()


def stage_paths(paths, release):
    """Original arm paths; only bin/config templates move into the new release."""
    return dict(paths, bin=release / "bin", config=release / "config")


def original_scope(runtime_ref):
    """The original runtime, still exactly as frozen; the stage never edits it."""
    context, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"], "original startup")
    source = Path(runtime["controller"]["path"])
    binding = json.loads(read_regular(source / "upgrade-binding.json"))
    require("stage_fence" not in binding, "ORIGINAL_BINDING_ALREADY_STAGED")
    return context, runtime, spec, binding


def new_hook_bytes(runtime, spec, binding, controller):
    """Recompute the three new lane files from the current, pinned originals."""
    source = Path(runtime["source_controller"]["path"])
    python = spec["pins"]["python"]["path"]
    original = provider_hooks(source, Path(spec["controller"]), python, binding)
    staged = provider_hooks(source, Path(controller), python, binding)
    result = {}
    for key in HOOK_PINS:
        pin = spec["pins"][key]
        raw = read_regular(pin["path"])
        require(digest(raw) == pin["sha256"], "STAGE_LANE_FILE_NOT_ORIGINAL:" + key)
        result[key] = rewritten_hook_file(key, raw, original, staged)
    return result


STAGE_SCOPE = ("# Fenced validation stage {stage_id} for multi-agent-collaboration-upgrade\n\n"
    "This release serves only the three original sessions of this experiment under the user's explicit "
    "stage authorization. The original startup, its failed probes, the original private-live-v2 controller "
    "and all history stay unchanged evidence; never redo, replay or overwrite them. This stage's own "
    "readiness prompt and its externally published stage adoption are required before task input; the "
    "original adoption.json or startup continuity is never authority here. Never create replacement "
    "sessions or change models.\n\n")


def build(runtime_ref, stage_id, models, authority_ref, staging, backup):
    """Write a new staging directory and its PLAN.json; no live path is written."""
    context, runtime, spec, binding = original_scope(runtime_ref)
    require(isinstance(stage_id, str) and stage_id and "/" not in stage_id and not stage_id.startswith(".")
            and isinstance(models, dict) and set(models) == set(LANES)
            and all(isinstance(m, str) and m for m in models.values()), "STAGE_IDENTITY_REQUIRED")
    paths = arm_paths(context, runtime["arm"])
    release = paths["controllers"] / "releases" / stage_id
    controller, control = release / "source", control_directory(binding, stage_id)
    staging, backup = checked_path(staging), checked_path(backup)
    for path in (release, control, staging, backup):
        require(not os.path.lexists(path), "STAGE_DESTINATION_EXISTS:" + str(path))
    from upgrade_startup_contract import RESPONSE_OBSERVER_FILE, response_observation_enabled
    source_pin = runtime["source_controller"]
    source, support = Path(source_pin["path"]), Path(__file__).resolve().parent
    relocated = stage_paths(paths, release)
    original_skill = read_regular(Path(spec["controller"]) / "SKILL.md")
    require(original_skill.endswith(read_regular(source / "SKILL.md")), "ORIGINAL_SKILL_CHANGED")
    buffers = {}
    for name, old in source_pin["files"].items():
        raw = read_regular(source / name)
        if Path(name).parent == Path("scripts") and name.endswith(".py"):
            raw = transform_live(Path(name).name, raw.decode(), relocated, controller, compatibility=True)[0].encode()
        elif name == "SKILL.md":
            raw = STAGE_SCOPE.format(stage_id=stage_id).encode() + original_skill
        buffers[name] = (raw, bool(old["mode"] & 0o111))
    private, _ = render_private_support(support)
    if response_observation_enabled(binding):
        private["scripts/" + RESPONSE_OBSERVER_FILE] = (read_regular(support / RESPONSE_OBSERVER_FILE), False)
    require(not set(private) & set(buffers), "STAGE_SOURCE_COLLISION")
    buffers.update(private)
    python, rtk = spec["pins"]["python"]["path"], spec["pins"]["rtk"]["path"]
    hooks = new_hook_bytes(runtime, spec, binding, controller)
    authority_raw = read_regular(authority_ref["path"])
    require(digest(authority_raw) == authority_ref["sha256"], "STAGE_AUTHORITY_CHANGED")
    record = dict(schema=SCHEMA, stage_id=stage_id, models=models, original_startup=runtime["startup"],
        authority=dict(path=str(control / "AUTHORITY.json"), sha256=digest(authority_raw)),
        controller=str(controller), control=str(control),
        prompts={lane: stage_prompt(stage_id, models[lane], str(controller), python, rtk, lane) for lane in LANES},
        hooks={key: dict(path=spec["pins"][key]["path"], sha256=digest(raw), bytes=len(raw))
               for key, raw in hooks.items()})
    record_raw = canonical(record)
    staged = dict(binding, controller_root=str(controller), stage_fence=dict(path=str(control / "stage.json"),
                  sha256=digest(record_raw), stage_id=stage_id, models=models))
    staging.mkdir(mode=0o700)
    root, ctl, src = staging / "release", staging / "control", staging / "release/source"
    src.mkdir(parents=True, mode=0o700)
    for directory in source_pin["directories"]:
        (src / directory).mkdir(parents=True, exist_ok=True)
    for name, (raw, executable) in buffers.items():
        immutable_bytes(src / name, raw, executable=executable)
    binding_raw = canonical(staged)
    immutable_bytes(src / "upgrade-binding.json", binding_raw)
    manifest_raw = canonical(dict(schema=MANIFEST_SCHEMA, arm=runtime["arm"], stage_id=stage_id,
                                  source=source_pin, files=tree_manifest(src)["files"]))
    immutable_bytes(root / "MANIFEST.json", manifest_raw)
    # The renderer needs an existing adapter: render against staging, then name
    # the destination. apply re-renders at the destination and compares bytes.
    staged_adapter, adapter = str(src / "scripts/cmux_agent_adapter.py"), str(controller / "scripts/cmux_agent_adapter.py")
    helper = rendered_helper(source, src, python).decode()
    require(helper.count(staged_adapter) == 1, "STAGE_HELPER_SHAPE_CHANGED")
    helper = helper.replace(staged_adapter, adapter)
    require(str(staging) not in helper, "STAGE_HELPER_NAMES_STAGING")
    immutable_bytes(root / "bin/cmux-agent", helper.encode(), executable=True)
    templates = provider_hooks(source, controller, python, binding)
    for name, provider in (("codex-hooks.json", "codex"), ("claude-settings.json", "claude")):
        immutable_bytes(root / "config" / name, canonical(templates[provider]))
    immutable_bytes(ctl / "stage.json", record_raw)
    immutable_bytes(ctl / "AUTHORITY.json", authority_raw)
    ref = lambda path, raw: dict(path=str(path), sha256=digest(raw), bytes=len(raw))
    plan = dict(schema=PLAN_SCHEMA, stage_id=stage_id, models=models, built_at=utc_now(), runtime=runtime_ref,
        startup=runtime["startup"], builder=file_ref(Path(__file__).resolve()), staging=str(staging),
        release=str(release), control=str(control), backup=str(backup),
        release_tree=dict(tree_manifest(root), path=str(release)),
        control_tree=dict(tree_manifest(ctl, kind="output"), path=str(control)),
        stage=ref(control / "stage.json", record_raw), binding=ref(controller / "upgrade-binding.json", binding_raw),
        manifest=ref(release / "MANIFEST.json", manifest_raw), authority=authority_ref,
        hooks={key: dict(before=spec["pins"][key], after=dict(path=spec["pins"][key]["path"],
               sha256=digest(raw), bytes=len(raw), mode=spec["pins"][key]["mode"])) for key, raw in hooks.items()},
        unchanged=dict({k: v for k, v in spec["pins"].items() if k not in HOOK_PINS}, startup=runtime["startup"],
                       runtime=runtime_ref, release_manifest=runtime["release_manifest"], patches=runtime["patches"]),
        absent=[spec["adoption_path"], spec["stop_path"]], free_floor_bytes=FREE_FLOOR_BYTES,
        adoption_schema=ADOPTION_SCHEMA, input_operations=0, clients_started=0)
    immutable_bytes(staging / "PLAN.json", canonical(plan))
    return file_ref(staging / "PLAN.json")


def load_plan(path, sha256):
    plan = json_ref(anchored_ref(path, sha256), "stage install plan")
    require(plan.get("schema") == PLAN_SCHEMA and plan.get("input_operations") == 0, "STAGE_PLAN_CHANGED")
    return plan


def tree_at(manifest, path):
    """A frozen tree manifest evaluated at another location (staging or destination)."""
    return dict(manifest, path=str(path))


def mode_ref(path):
    value = artifact_ref(Path(path))
    return {k: value[k] for k in ("path", "sha256", "bytes", "mode")}


def check_original(plan):
    """Every precondition before any write; returns the recomputed new hook bytes."""
    _, runtime, spec, binding = original_scope(plan["runtime"])
    require(runtime["startup"] == plan["startup"], "STAGE_ORIGINAL_STARTUP_CHANGED")
    for key, value in plan["unchanged"].items():
        if "mode" in value:
            require(mode_ref(value["path"]) == {k: value[k] for k in ("path", "sha256", "bytes", "mode")},
                    "STAGE_UNCHANGED_FILE_CHANGED:" + key)
        else:
            require(file_ref(value["path"]) == value, "STAGE_UNCHANGED_FILE_CHANGED:" + key)
    for key, item in plan["hooks"].items():
        before = item["before"]
        require(mode_ref(before["path"]) == {k: before[k] for k in ("path", "sha256", "bytes", "mode")},
                "STAGE_HOOK_TARGET_CHANGED:" + key)
    hooks = new_hook_bytes(runtime, spec, binding, Path(plan["release"]) / "source")
    for key, raw in hooks.items():
        require(digest(raw) == plan["hooks"][key]["after"]["sha256"], "STAGE_NEW_HOOK_BYTES_CHANGED:" + key)
    for path in plan["absent"]:
        require(not os.path.lexists(path), "STAGE_PRECONDITION_PRESENT:" + path)
    return hooks


def check_staging(plan):
    staging = Path(plan["staging"])
    require(tree_manifest(staging / "release") == tree_at(plan["release_tree"], staging / "release")
            and tree_manifest(staging / "control", kind="output") == tree_at(plan["control_tree"],
                                                                            staging / "control"),
            "STAGE_STAGING_CHANGED")
    for name, rel in (("stage", "control/stage.json"), ("binding", "release/source/upgrade-binding.json"),
                      ("manifest", "release/MANIFEST.json")):
        raw = read_regular(staging / rel)
        require(digest(raw) == plan[name]["sha256"] and len(raw) == plan[name]["bytes"], "STAGE_STAGING_CHANGED:" + name)


def check_space(plan):
    # New trees, backups, one temporary per hook write and one per rollback.
    needed = sum(item["bytes"] for tree in (plan["release_tree"], plan["control_tree"])
                 for item in tree["files"].values()) + sum(
        h["before"]["bytes"] + h["after"]["bytes"] + h["before"]["bytes"] for h in plan["hooks"].values())
    free = shutil.disk_usage(Path(plan["release"]).parent).free
    require(free - needed >= plan["free_floor_bytes"], "STAGE_FREE_SPACE_BELOW_FLOOR")
    return free


def plan_check(plan):
    check_staging(plan)
    check_original(plan)
    for path in (plan["release"], plan["control"], plan["backup"]):
        require(not os.path.lexists(path), "STAGE_DESTINATION_EXISTS:" + path)
    free = check_space(plan)
    return dict(status="PLAN_VERIFIED_NOT_INSTALLED", stage_id=plan["stage_id"], free_bytes=free,
                writes=0, input_operations=0)


def replace_bytes(path, raw, mode):
    """Same-directory fsynced temporary, then one atomic rename; never a torn file."""
    path = checked_path(path)
    temporary = path.parent / ("." + path.name + ".stage-install.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def copy_tree(source, destination, manifest):
    """Exclusive new directory; every file checked against the frozen manifest."""
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    for name in sorted(manifest["directories"]):
        (destination / name).mkdir(mode=0o700)
    for name, pin in manifest["files"].items():
        raw = read_regular(Path(source) / name)
        require(digest(raw) == pin["sha256"] and len(raw) == pin["bytes"], "STAGE_STAGING_CHANGED:" + name)
        write_bytes_once(destination / name, raw)
        (destination / name).chmod(pin["mode"])
    require(tree_manifest(destination, kind=manifest["kind"]) == tree_at(manifest, destination),
            "STAGE_INSTALLED_TREE_CHANGED:" + str(destination))


def check_helper(plan):
    """The installed helper equals a fresh render against the installed adapter."""
    runtime = json_ref(plan["runtime"], "original runtime")
    python = json_ref(runtime["startup"], "original startup")["pins"]["python"]["path"]
    expected = rendered_helper(Path(runtime["source_controller"]["path"]), Path(plan["release"]) / "source", python)
    require(read_regular(Path(plan["release"]) / "bin/cmux-agent") == expected, "STAGE_HELPER_CHANGED")


def backup_name(key):
    return key.replace("/", "-") + ".orig"


def restore(plan):
    """Put back each original lane file that currently holds the stage bytes."""
    restored = []
    for key, item in plan["hooks"].items():
        current = mode_ref(item["before"]["path"])
        if current == item["before"]:
            continue
        require(current == item["after"], "STAGE_HOOK_TARGET_CHANGED_BY_OTHER:" + key)
        raw = read_regular(Path(plan["backup"]) / backup_name(key))
        require(digest(raw) == item["before"]["sha256"], "STAGE_BACKUP_CHANGED:" + key)
        replace_bytes(item["before"]["path"], raw, item["before"]["mode"])
        require(mode_ref(item["before"]["path"]) == item["before"], "STAGE_RESTORE_UNCONFIRMED:" + key)
        restored.append(key)
    return restored


def apply(plan):
    """One installation: backup, new release/control, then exactly three hook files."""
    backup = Path(plan["backup"])
    require(not os.path.lexists(backup), "STAGE_INSTALL_ALREADY_ATTEMPTED: inspect the original backup")
    check_staging(plan)
    check_original(plan)
    for path in (plan["release"], plan["control"]):
        require(not os.path.lexists(path), "STAGE_DESTINATION_EXISTS:" + path)
    check_space(plan)
    backup.mkdir(mode=0o700)  # exclusive: a second installer stops here
    with exclusive_lock(backup / "INSTALL.lock"):
        hooks = check_original(plan)
        for key, item in plan["hooks"].items():
            raw = read_regular(item["before"]["path"])
            require(digest(raw) == item["before"]["sha256"], "STAGE_HOOK_TARGET_CHANGED:" + key)
            write_bytes_once(backup / backup_name(key), raw)
            (backup / backup_name(key)).chmod(0o400)
            require(digest(read_regular(backup / backup_name(key))) == item["before"]["sha256"],
                    "STAGE_BACKUP_UNCONFIRMED:" + key)
        # From here any failure, at any write, restores every lane file that
        # holds exactly the stage bytes; a foreign change is never overwritten.
        try:
            staging = Path(plan["staging"])
            copy_tree(staging / "release", plan["release"], plan["release_tree"])
            check_helper(plan)
            copy_tree(staging / "control", plan["control"], plan["control_tree"])
            for key, item in plan["hooks"].items():
                replace_bytes(item["after"]["path"], hooks[key], item["after"]["mode"])
                require(mode_ref(item["after"]["path"]) == item["after"], "STAGE_HOOK_WRITE_UNCONFIRMED:" + key)
            from upgrade_stage_fence import load_stage
            _, _, _, stage = load_stage(Path(plan["release"]) / "source")
            validate_live_runtime(plan["runtime"], stage_controller=stage["controller"])
        except (ContractError, OSError, ValueError, KeyError) as exc:
            try:
                restored, restore_error = restore(plan), None
            except (ContractError, OSError, ValueError, KeyError) as failure:
                restored, restore_error = None, str(failure)
            return dict(status="INSTALL_FAILED_HOOKS_RESTORED" if restore_error is None
                        else "INSTALL_FAILED_RESTORE_INCOMPLETE", error=str(exc), restored=restored,
                        restore_error=restore_error, hooks={k: mode_ref(i["before"]["path"])
                                                             for k, i in plan["hooks"].items()},
                        release_preserved=plan["release"], control_preserved=plan["control"],
                        backup=str(backup), input_operations=0)
    return dict(status="INSTALLED_NOT_ADOPTED", stage=file_ref(Path(plan["control"]) / "stage.json"),
                hooks={key: mode_ref(item["after"]["path"]) for key, item in plan["hooks"].items()},
                backup=str(backup), input_operations=0, clients_started=0, native_reload_verified=False)


def rollback(plan):
    """Restore only the three lane files; the release and stage stay as evidence."""
    backup = checked_path(plan["backup"])
    require(backup.is_dir(), "STAGE_BACKUP_MISSING")
    with exclusive_lock(backup / "INSTALL.lock"):
        restored = restore(plan)
    return dict(status="HOOKS_RESTORED", restored=restored, release_preserved=plan["release"],
                control_preserved=plan["control"], input_operations=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "plan", "apply", "rollback"))
    parser.add_argument("--runtime")
    parser.add_argument("--runtime-sha256")
    parser.add_argument("--stage-id")
    for lane in LANES:
        parser.add_argument("--model-" + lane.replace("_", "-"))
    parser.add_argument("--authority")
    parser.add_argument("--authority-sha256")
    parser.add_argument("--staging")
    parser.add_argument("--backup")
    parser.add_argument("--plan")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            models = {lane: getattr(args, "model_" + lane) for lane in LANES}
            require(all((args.runtime, args.runtime_sha256, args.stage_id, args.authority, args.authority_sha256,
                         args.staging, args.backup, *models.values())) and not (args.plan or args.plan_sha256),
                    "STAGE_BUILD_ARGUMENTS_REQUIRED")
            ref = build(anchored_ref(args.runtime, args.runtime_sha256), args.stage_id, models,
                        anchored_ref(args.authority, args.authority_sha256), args.staging, args.backup)
            value = dict(status="STAGED_NOT_INSTALLED", plan=ref, input_operations=0)
        else:
            require(args.plan and args.plan_sha256 and not any((args.runtime, args.stage_id, args.staging)),
                    "STAGE_PLAN_ARGUMENTS_REQUIRED")
            plan = load_plan(args.plan, args.plan_sha256)
            value = {"plan": plan_check, "apply": apply, "rollback": rollback}[args.command](plan)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if value["status"] in ("STAGED_NOT_INSTALLED", "PLAN_VERIFIED_NOT_INSTALLED",
                                        "INSTALLED_NOT_ADOPTED", "HOOKS_RESTORED") else 2
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
