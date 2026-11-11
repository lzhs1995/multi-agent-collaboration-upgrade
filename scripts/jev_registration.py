"""Separate, versioned JEV advisory registration (R486).

The frozen registration keeps ``third_party_api=False`` in all seven places;
this file never relaxes it. An on arm needs its own pinned registration that
names the external-advice exception, the original run/node, the arm, the
model/provider, the transport source and the authorization/budget pins. The
arm is read from here, never derived afterwards from an advice file.

Absent registration means the off arm with zero calls. Nothing here reads the
environment or shared configuration to supply an implicit authorization, and
an on registration without a positive budget is refused, not silently turned
into an off record.
"""
from __future__ import annotations

from orchestration_common import ContractError, json_ref, read_ref

SCHEMA = "jev-advisory-registration-v1"
EXCEPTION = "EXTERNAL_ADVICE_EXCEPTION_SEPARATE_FROM_FROZEN_THIRD_PARTY_API_FALSE"
ARMS = ("advisory-off", "advisory-on")


def off(run_id: str, node_id: str) -> dict:
    return dict(schema=SCHEMA, run_id=run_id, node_id=node_id, arm="advisory-off", registration=None,
                provider="none", model=None, budget=None, transport_source=None, authorization=None)


def _require(value, message):
    if not value:
        raise ContractError("JEV_REGISTRATION: " + message)


def load(ref, *, run_id: str, node_id: str) -> dict:
    if ref is None:
        return off(run_id, node_id)
    value = json_ref(ref, "jev registration")
    _require(value.get("schema") == SCHEMA, "schema mismatch")
    _require(value.get("run_id") == run_id and value.get("node_id") == node_id, "bound to another run/node")
    arm = value.get("arm")
    _require(arm in ARMS, "unknown arm")
    if arm == "advisory-off":
        _require(all(value.get(k) in (None, "none") for k in ("transport_source", "authorization", "budget", "model", "provider")),
                 "an off arm names no transport, authorization, budget, model or provider")
        return dict(off(run_id, node_id), registration=ref)
    _require(value.get("external_advice_exception") == EXCEPTION, "the external-advice exception must be explicit")
    for key in ("model", "provider"):
        _require(isinstance(value.get(key), str) and value[key], key + " required")
    budget = value.get("budget")
    limit = budget.get("max_model_requests") if isinstance(budget, dict) else None
    _require(isinstance(limit, int) and not isinstance(limit, bool) and limit > 0, "an on arm needs a positive budget")
    read_ref(value.get("transport_source"), "jev registration transport_source")
    # Narrow, not a permission system: the authorization file must name this
    # exact run/node/arm/provider/model and the complete frozen budget. Whether
    # a person granted it stays outside code and is never claimed here.
    granted = json_ref(value.get("authorization"), "jev registration authorization")
    _require(all(granted.get(k) == value[k] for k in ("run_id", "node_id", "arm", "provider", "model", "budget")),
             "authorization does not name this run/node/arm/provider/model and complete budget")
    return dict(schema=SCHEMA, run_id=run_id, node_id=node_id, arm=arm, registration=ref, provider=value["provider"],
                model=value["model"], budget=dict(budget), transport_source=value["transport_source"],
                authorization=value["authorization"])


RECORD_SCHEMA = "jev-advisory-registration-record-v1"
RECORD_NAME = "jev-registration-record.json"


def record_path(run_root, node_id):
    """Beside the worker journal, outside nodes/<id>/advice, so it is never an advice body."""
    from pathlib import Path
    return Path(run_root) / "nodes" / str(node_id) / RECORD_NAME


def registered_arm(run_root, run_id: str, node_id: str):
    """The arm this node was registered under, re-measured; ``(arm, record_ref)``.

    No record is the off arm. A record is re-validated through ``load``, so an
    edited registration, transport source or authorization pin fails here
    instead of being trusted from the record.
    """
    from orchestration_common import file_ref
    path = record_path(run_root, node_id)
    if not (path.exists() or path.is_symlink()):
        return "advisory-off", None
    ref = file_ref(path)
    value = json_ref(ref, "jev registration record")
    _require(value.get("schema") == RECORD_SCHEMA and value.get("run_id") == run_id and value.get("node_id") == node_id,
             "record belongs to another run/node")
    _require(load(value.get("registration"), run_id=run_id, node_id=node_id)["arm"] == value.get("arm"),
             "record arm differs from its registration")
    return value["arm"], ref
