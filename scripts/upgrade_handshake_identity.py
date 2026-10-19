"""Resolve legacy receipt refs only through the original pinned handshake gate.

No current cmux lookup, evidence rewrite, fuzzy UUID match or transport operation.
This module is copied into the private controller; production MAC is untouched.
"""
from __future__ import annotations

import uuid
import re


def canonical_uuid(value):
    if not isinstance(value, str):
        raise ValueError("ACK_CANONICAL_UUID_REQUIRED")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError("ACK_CANONICAL_UUID_REQUIRED") from exc


def _selector(value):
    if isinstance(value, str) and re.fullmatch(r"surface:[0-9]+", value):
        return value
    return canonical_uuid(value)


def _register_alias(aliases, value, mid):
    if not isinstance(value, str) or not value:
        raise ValueError("ACK_ORIGINAL_GATE_ALIAS_REQUIRED")
    alias = _selector(value)
    if ((not alias.startswith("surface:") and alias != mid)
            or (alias in aliases and aliases[alias] != mid)):
        raise ValueError("ACK_ORIGINAL_GATE_ALIAS_AMBIGUOUS")
    aliases[alias] = mid


def bound_rows(rows, members, gate, binding):
    """Return original row objects with canonical IDs; do not mutate either input."""
    identity = binding.get("identity", {})
    if (gate.get("status") != "PASS" or not isinstance(rows, list) or not rows
            or not isinstance(members, list) or len(rows) != len(members)):
        raise ValueError("ACK_ORIGINAL_PANEL_OR_GATE_CHANGED")
    if (canonical_uuid(gate.get("supervisor_surface_uuid")) != canonical_uuid(identity.get("caller_surface_uuid"))
            or canonical_uuid(gate.get("workspace_uuid")) != canonical_uuid(identity.get("workspace_uuid"))):
        raise ValueError("ACK_ORIGINAL_PANEL_OR_GATE_CHANGED")
    aliases, seen_members = {}, set()
    for member in members:
        mid = canonical_uuid(member.get("surface_uuid"))
        if mid in seen_members:
            raise ValueError("ACK_ORIGINAL_CANONICAL_EXECUTOR_DUPLICATE")
        seen_members.add(mid)
        for alias in (member.get("surface_ref"), mid):
            _register_alias(aliases, alias, mid)
    # Bind aliases only from the original gate, including UUID-keyed proofs
    # whose target is represented by a UI ref. Never consult today's inventory.
    for member in members:
        mid = canonical_uuid(member["surface_uuid"])
        proof = gate.get("workspace_proofs", {}).get(member["surface_ref"])
        if not isinstance(proof, dict):
            raise ValueError("ACK_ORIGINAL_WORKSPACE_PROOF_REQUIRED")
        if canonical_uuid(proof.get("target_surface_uuid")) != mid:
            raise ValueError("ACK_ORIGINAL_WORKSPACE_PROOF_CHANGED")
        source, target_ref = _selector(member["surface_ref"]), _selector(proof.get("target_surface_ref"))
        if source.startswith("surface:") and target_ref != source:
            raise ValueError("ACK_ORIGINAL_WORKSPACE_PROOF_CHANGED")
        _register_alias(aliases, target_ref, mid)
    resolved, seen = [], set()
    for row, member in zip(rows, members):
        if not isinstance(row, dict):
            raise ValueError("ACK_ORIGINAL_PANEL_ORDER_OR_BINDING_CHANGED")
        value = row.get("executor")
        if isinstance(value, str) and value.startswith("surface:"):
            actual = aliases.get(value)
        else:
            actual = canonical_uuid(value)
            if actual not in seen_members:
                actual = None
        expected = canonical_uuid(member["surface_uuid"])
        if (actual != expected or actual in seen or type(row.get("ordinal")) is not int
                or row["ordinal"] != member.get("ordinal")
                or row.get("task_id") != gate.get("task_id")
                or row.get("supervisor") != gate.get("supervisor")
                or row.get("executor_provider") != member.get("provider")
                or row.get("workspace_ref") != gate.get("workspace_ref")
                or member.get("workspace_ref") != gate.get("workspace_ref")):
            raise ValueError("ACK_ORIGINAL_PANEL_ORDER_OR_BINDING_CHANGED")
        seen.add(actual)
        resolved.append((row, actual, member))
    target = canonical_uuid(identity.get("target_surface_uuid"))
    selected = [item for item in resolved if item[1] == target]
    if len(selected) != 1:
        raise ValueError("ACK_ORIGINAL_EXECUTOR_NOT_UNIQUE")
    row, _, member = selected[0]
    participants = [p for p in gate.get("native_participants", [])
                    if isinstance(p, dict) and isinstance(p.get("surface_uuid"), str)
                    and canonical_uuid(p["surface_uuid"]) == target]
    if (len(participants) != 1 or participants[0].get("role") != "executor"
            or canonical_uuid(participants[0].get("native_session_id")) != canonical_uuid(binding.get("session_id"))
            or participants[0].get("provider") != binding.get("provider")
            or row.get("executor_provider") != binding.get("provider")):
        raise ValueError("ACK_ORIGINAL_EXECUTOR_SESSION_CHANGED")
    # C34 records panes in workspace_proofs, not native_participants. Resolve
    # through the original ref, never today's cmux inventory. A ref-only match
    # without the original UUID/pane proof must remain unverified.
    proof = gate.get("workspace_proofs", {}).get(member.get("surface_ref"))
    if not isinstance(proof, dict):
        raise ValueError("ACK_ORIGINAL_WORKSPACE_PROOF_REQUIRED")
    for field in ("workspace_uuid", "caller_surface_uuid", "target_surface_uuid", "target_pane_uuid"):
        if canonical_uuid(proof.get(field)) != canonical_uuid(identity.get(field)):
            raise ValueError("ACK_ORIGINAL_WORKSPACE_PROOF_CHANGED")
    if (aliases.get(_selector(proof.get("target_surface_ref"))) != target
            or proof.get("workspace_ref") != gate.get("workspace_ref")):
        raise ValueError("ACK_ORIGINAL_WORKSPACE_PROOF_CHANGED")
    if "caller_pane_uuid" in identity and canonical_uuid(proof.get("caller_pane_uuid")) != canonical_uuid(identity["caller_pane_uuid"]):
        raise ValueError("ACK_ORIGINAL_WORKSPACE_PROOF_CHANGED")
    # Additional recorded pane fields narrow, never widen, that original proof.
    for field in ("pane_uuid", "pane_id"):
        if field in participants[0] and canonical_uuid(participants[0][field]) != canonical_uuid(identity.get("target_pane_uuid")):
            raise ValueError("ACK_ORIGINAL_EXECUTOR_PANE_CHANGED")
    if "target_pane_ref" in identity and identity["target_pane_ref"] != member.get("pane_ref"):
        raise ValueError("ACK_ORIGINAL_EXECUTOR_PANE_CHANGED")
    return resolved, row
