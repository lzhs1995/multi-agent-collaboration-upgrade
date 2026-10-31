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


def pane_argv(cmux, workspace_uuid, lane, command):
    """One lane's creation-time held entry command in a new side pane of the pinned workspace."""
    if lane not in LANES:
        raise ValueError("unknown lane")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("held entry command required")
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


def require_disjoint(before, after, created):
    """Every old surface survives; the new surfaces are exactly the created set of one to three."""
    created = {_uuid(c, "created surface") for c in created}
    if not before <= after:
        raise ValueError("an old surface disappeared")
    if created & before or after - before != created or not 0 < len(created) <= MAX_NEW:
        raise ValueError("new surfaces are not exactly the created, disjoint set")
    return sorted(created)
