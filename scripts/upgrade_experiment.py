#!/usr/bin/env python3
"""Explicit private experiment scope. Initialization never starts or contacts agents."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path

from orchestration_common import (ContractError, checked_path, contract_errors, file_ref,
                                  json_ref, read_ref, tree_manifest, verify_tree,
                                  within, write_json, utc_now)
from orchestration_campaign import anchored_ref
from orchestration_native import require

NAME = "multi-agent-collaboration-upgrade"
PROTECTED_REFS = ("surface:1000020040", "surface:1000020058",
                  "surface:1000020061", "surface:1000020062")
DIRECTORIES = ("state", "config", "controllers", "campaigns", "evidence", "tmp", "bin")
ARMS = ("A0", "A1", "B")


def uuid_value(value, label):
    require(isinstance(value, str), label + " must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ContractError(label + " must be a UUID") from exc


def protected_paths():
    home = Path.home().resolve()
    return [home / suffix for suffix in (
        ".agents/skills/multi-agent-collaboration", ".codex/skills/multi-agent-collaboration",
        ".local/share/multi-agent-collaboration", ".local/state/multi-agent-collaboration",
        ".local/state/cmux-agent", ".local/bin/cmux-agent",
        ".codex/hooks.json", ".codex/config.toml", ".claude/settings.json",
        "Library/LaunchAgents", ".cmuxterm")] + [Path("/private/tmp/multi-agent-collaboration")]


def private_root(path):
    root = checked_path(path)
    require(root != Path.home().resolve() and root != Path("/"), "dedicated experiment root required")
    for protected in protected_paths():
        for target in (protected, protected.resolve()):
            require(root != target and root not in target.parents and target not in root.parents,
                    "experiment overlaps a protected production path")
    return root


def init_experiment(root, experiment_id, source_controller):
    root, source = private_root(root), checked_path(source_controller)
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", experiment_id) is not None,
            "invalid experiment id")
    require(not root.exists(), "experiment root already exists; observe it, do not reinitialize")
    source_pin = tree_manifest(source)
    require("SKILL.md" in source_pin["files"] and "scripts/mac_harness.py" in source_pin["files"],
            "complete MAC controller source required")
    require(not any("__pycache__" in Path(n).parts or n.endswith((".pyc", ".pyo"))
                    for n in source_pin["files"]), "controller contains loadable bytecode")
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name in DIRECTORIES:
        (root / name).mkdir(mode=0o700)
    context = dict(version=1, skill=NAME, experiment_id=experiment_id, root=str(root),
                   created_at=utc_now(), account_uid=os.getuid(), account_home=str(Path.home().resolve()),
                   paths={name: str(root / name) for name in DIRECTORIES},
                   protected_surface_refs=list(PROTECTED_REFS), source_controller=source_pin,
                   python=file_ref(Path(sys.executable).resolve()),
                   session_policy="explicit_dedicated_1_plus_2", production_adoption=False)
    return write_json(root / "experiment.json", context, exclusive=True)


@contract_errors("experiment context")
def validate_context(ref):
    context = json_ref(ref, "pinned experiment")
    require(set(context) == {"version", "skill", "experiment_id", "root", "created_at", "account_uid",
                             "account_home", "paths", "protected_surface_refs", "source_controller",
                             "python", "session_policy", "production_adoption"}, "context fields changed")
    require(context["version"] == 1 and context["skill"] == NAME and
            context["production_adoption"] is False and
            context["session_policy"] == "explicit_dedicated_1_plus_2", "unsupported experiment contract")
    root = private_root(context["root"])
    require(Path(ref["path"]) == root / "experiment.json", "experiment context moved")
    require(context["paths"] == {name: str(root / name) for name in DIRECTORIES}, "private paths changed")
    require(context["protected_surface_refs"] == list(PROTECTED_REFS), "production protection changed")
    require(context["account_uid"] == os.getuid() and
            context["account_home"] == str(Path.home().resolve()), "experiment account changed")
    for path in [root, *map(Path, context["paths"].values())]:
        checked_path(path)
        st = path.stat()
        require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid(), "private directory owner/type changed")
    read_ref(context["python"], "pinned Python")
    return context


def arm_paths(context, arm):
    require(arm in ARMS, "unknown experiment arm")
    return {name: within(Path(path) / arm, context["root"]) for name, path in context["paths"].items()}


def validate_sessions(sessions, *, protected=()):
    """Addresses are frozen identities, never titles, focus or remembered refs."""
    require(isinstance(sessions, dict) and set(sessions) == {"codex", "claude_a", "claude_b"},
            "one Codex supervisor and two distinct Claude executor IDs required")
    ids, surfaces, panes, workspaces = set(), set(), set(), set()
    for lane, session in sessions.items():
        require(isinstance(session, dict) and session.get("provider") ==
                ("codex" if lane == "codex" else "claude"), "provider is separate from executor ID")
        sid = uuid_value(session.get("session_id"), "session_id")
        surface = uuid_value(session.get("surface_uuid"), "surface_uuid")
        pane = uuid_value(session.get("pane_uuid"), "pane_uuid")
        workspace = uuid_value(session.get("workspace_uuid"), "workspace_uuid")
        require(sid not in ids and surface not in surfaces and pane not in panes,
                "experiment sessions, surfaces and panes must be distinct")
        require(session.get("surface_ref") not in PROTECTED_REFS and
                surface not in {uuid_value(x, "protected surface") for x in protected},
                "production session cannot be enrolled")
        ids.add(sid); surfaces.add(surface); panes.add(pane); workspaces.add(workspace)
    require(len(workspaces) == 1, "dedicated same-workspace endpoints required")
    return sessions


def validate_runtime(ref, *, context_ref=None):
    runtime = json_ref(ref, "private runtime")
    require(set(runtime) == {"version", "skill", "context", "arm", "execution_mode",
                             "source_controller", "controller", "release_manifest", "patches",
                             "helper", "configs", "state_root", "config_root", "python",
                             "production_adoption"}, "runtime fields changed")
    require(runtime.get("version") == 1 and runtime.get("skill") == NAME,
            "private runtime format required")
    if context_ref is not None:
        require(runtime.get("context") == context_ref, "runtime belongs to another experiment")
    context = validate_context(runtime["context"])
    paths = arm_paths(context, runtime["arm"])
    require(Path(ref["path"]) == paths["controllers"] / "runtime.json", "runtime manifest moved")
    within(runtime["controller"]["path"], paths["controllers"])
    require(runtime["state_root"] == str(paths["state"]) and runtime["config_root"] == str(paths["config"]),
            "runtime uses nonprivate state/configuration")
    require(runtime["source_controller"] == context["source_controller"], "runtime source changed")
    require(runtime["python"] == context["python"] and runtime["production_adoption"] is False,
            "runtime Python or adoption scope changed")
    require(runtime["execution_mode"] == "offline", "unverified runtime execution mode")
    require(runtime["controller"]["path"] == str(paths["controllers"] / "releases/private-v1/source"),
            "private release location changed")
    verify_tree(runtime["source_controller"])
    verify_tree(runtime["controller"])
    read_ref(runtime["patches"], "private controller patch manifest")
    read_ref(runtime["release_manifest"], "private release manifest")
    require(runtime["helper"]["path"] == str(paths["bin"] / "cmux-agent"), "private helper moved")
    read_ref(runtime["helper"], "private helper")
    require(set(runtime["configs"]) == {"codex-hooks.json", "claude-settings.json"},
            "private hook configuration missing")
    for name, pin in runtime["configs"].items():
        require(pin["path"] == str(paths["config"] / name), "private configuration moved")
        read_ref(pin, "private hook configuration")
    return context, runtime


def live_gate(context_ref, runtime_ref, sessions, proof_ref=None):
    """Refuse live input until a supported private client boundary exists.

    Runtime v1 is an offline format. Keep this explicit instead of importing
    an unimplemented adoption verifier: the missing import also invalidates
    the offline adapter's complete dependency closure. A supplied proof file
    cannot upgrade this runtime or authorize input.
    """
    validate_runtime(runtime_ref, context_ref=context_ref)
    validate_sessions(sessions)
    raise ContractError(
        "LIVE_ISOLATION_UNVERIFIED: runtime v1 is offline; private client launch, "
        "automatic hook adoption and protected-path write isolation are not implemented. "
        "A proof file or passing offline tests cannot authorize client input")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--root", required=True)
    init.add_argument("--id", required=True)
    init.add_argument("--source-controller", required=True)
    show = sub.add_parser("inspect")
    show.add_argument("--experiment", required=True)
    show.add_argument("--experiment-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        value = (init_experiment(args.root, args.id, args.source_controller) if args.command == "init" else
                 validate_context(anchored_ref(args.experiment, args.experiment_sha256)))
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ContractError, OSError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
