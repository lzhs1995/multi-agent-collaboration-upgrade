"""Existing-workspace held allocation (R511 option b): side panes in a pinned workspace, never a new one.

Pure argv builders and set checks only; nothing here calls cmux. The one real
allocation is a separate explicit step that snapshots the workspace first,
runs at most three of these argv lists, and then proves with a second snapshot
that every old surface survived and the new surfaces are exactly the created,
disjoint trio. Whether ``new-pane`` honours ``--id-format uuids`` in its reply
is NOT_VERIFIED until that real call.
"""
from __future__ import annotations

import uuid

LANES = ("codex", "claude_a", "claude_b")
DIRECTION = {"codex": "right", "claude_a": "down", "claude_b": "down"}
MAX_NEW = 3


def _uuid(value, label):
    try:
        return str(uuid.UUID(str(value))).upper()
    except ValueError as exc:
        raise ValueError(label + " must be a UUID") from exc


def normalize_split_targets(workspace_uuid, targets):
    """Pin an explicit original surface for each lane; shared layout targets are allowed."""
    if workspace_uuid is None:
        raise ValueError("split targets require an existing workspace")
    _uuid(workspace_uuid, "workspace")
    if not isinstance(targets, dict) or set(targets) != set(LANES):
        raise ValueError("one split target per lane required")
    return {lane: _uuid(targets[lane], "split target").lower() for lane in LANES}


def verify_split_targets(tree, workspace_uuid, targets):
    """Resolve the pinned UUIDs in the live workspace before any creation intent."""
    targets = normalize_split_targets(workspace_uuid, targets)
    workspace = _uuid(workspace_uuid, "workspace")
    workspaces = [ws for window in tree["windows"] for ws in window["workspaces"]]
    selected = [ws for ws in workspaces if _uuid(ws["id"], "workspace") == workspace]
    if len(selected) != 1:
        raise ValueError("split workspace is not unique")
    for target in set(targets.values()):
        rows = [(ws, surface) for ws in workspaces for pane in ws["panes"]
                for surface in pane["surfaces"] if _uuid(surface["id"], "surface").lower() == target]
        if (len(rows) != 1 or _uuid(rows[0][0]["id"], "workspace") != workspace
                or rows[0][1].get("type") != "terminal"):
            raise ValueError("split target is not a unique terminal in the pinned workspace")
    return targets


def pane_argv(cmux, workspace_uuid, lane, command, *, surface_uuid=None):
    """One lane's creation-time held entry command in a new side pane of the pinned workspace."""
    if lane not in LANES:
        raise ValueError("unknown lane")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("held entry command required")
    if surface_uuid is not None:
        # The default new-pane target follows focus and can be too small even
        # when a different original pane has enough room. Pin the actual target.
        return [str(cmux), "--json", "--id-format", "uuids", "new-split", DIRECTION[lane],
                "--workspace", _uuid(workspace_uuid, "workspace"),
                "--surface", _uuid(surface_uuid, "split target"),
                "--command", command, "--focus", "false"]
    return [str(cmux), "--json", "--id-format", "uuids", "new-pane", "--type", "terminal",
            "--direction", DIRECTION[lane], "--workspace", _uuid(workspace_uuid, "workspace"),
            "--command", command, "--focus", "false"]


def surfaces(tree_text):
    """Surface UUIDs listed by ``cmux tree --workspace <id> --id-format both``."""
    found = set()
    for line in tree_text.splitlines():
        parts = line.split()
        if "surface" not in parts:
            continue
        index = parts.index("surface")
        for token in parts[index + 1:index + 3]:
            try:
                found.add(str(uuid.UUID(token)).upper())
            except ValueError:
                pass
    return found


def create_panes(argvs, run, write_intent, write_result, *, timeout=45):
    """Write one exclusive intent, then make each lane's one creation call; never retry or replace.

    ``run`` is the subprocess runner and ``write_intent``/``write_result`` the
    exclusive writers, all injected so tests make no UI. A timeout records
    UNKNOWN and stops; a non-zero return stops before the next lane.
    """
    if set(argvs) != set(LANES):
        raise ValueError("one creation argv per lane required")
    write_intent(dict(argv={lane: argvs[lane] for lane in LANES}, pane_creation_attempts=0))
    results = {}
    for lane in LANES:
        try:
            done = run(argvs[lane], timeout)
        except TimeoutError:
            write_result(dict(lanes=results, failed_lane=lane, status="UNKNOWN",
                              pane_creation_attempts=len(results) + 1))
            raise
        results[lane] = dict(returncode=done.returncode, stdout=done.stdout, stderr=done.stderr)
        if done.returncode != 0:
            write_result(dict(lanes=results, failed_lane=lane, status="UNCONFIRMED",
                              pane_creation_attempts=len(results)))
            raise ValueError("PANE_CREATION_UNCONFIRMED: " + lane + "; inspect the original attempt")
    write_result(dict(lanes=results, status="RETURNED", pane_creation_attempts=len(results)))
    return results


def require_disjoint(before, after, created):
    """Every old surface survives; the new surfaces are exactly the created set of one to three."""
    created = {_uuid(c, "created surface") for c in created}
    if not before <= after:
        raise ValueError("an old surface disappeared")
    if created & before or after - before != created or not 0 < len(created) <= MAX_NEW:
        raise ValueError("new surfaces are not exactly the created, disjoint set")
    return sorted(created)
