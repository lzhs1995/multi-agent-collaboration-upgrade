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
    limit = (value.get("budget") or {}).get("max_model_requests") if isinstance(value.get("budget"), dict) else None
    _require(isinstance(limit, int) and not isinstance(limit, bool) and limit > 0, "an on arm needs a positive budget")
    for key in ("transport_source", "authorization"):
        read_ref(value.get(key), "jev registration " + key)
    return dict(schema=SCHEMA, run_id=run_id, node_id=node_id, arm=arm, registration=ref, provider=value["provider"],
                model=value["model"], budget=dict(max_model_requests=limit), transport_source=value["transport_source"],
                authorization=value["authorization"])
