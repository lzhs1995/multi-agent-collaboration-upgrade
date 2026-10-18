"""Read-only initial handshake checks for newly materialized private controllers.

This evidence is not a bridge-clear proof, input intent, native receipt or ACK.
The existing sender still owns fresh input gates, its journal and one challenge.
"""
from __future__ import annotations

import hashlib
import json
import time

from upgrade_handshake_identity import bound_rows
from upgrade_input_gate import check_input

SCHEMA = "upgrade-readonly-handshake-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def panel_rows(gate, members):
    return [dict(task_id=gate["task_id"], supervisor=gate["supervisor"],
                 workspace_ref=gate["workspace_ref"], executor=m["surface_ref"],
                 ordinal=m["ordinal"], executor_provider=m["provider"]) for m in members]


def observe(bridge, native, gate, members, item, text, *, lines=200):
    """Bind the actual challenge payload, inspect capability, then recheck.

    No paste, key, focus, turn mutation, draft deletion or native goal operation.
    Codex uses metadata only. Claude permits activity with a complete empty
    composer stable across two reads. Actual input rechecks all of this again.
    """
    surface = item["surface_ref"]
    binding = native.bind_target(bridge, surface, text)
    _, row = bound_rows(panel_rows(gate, members), members, gate, binding)
    if row["executor"] != surface or row["ordinal"] != item["ordinal"]:
        raise ValueError("HANDSHAKE_CAPABILITY_EXECUTOR_CHANGED")
    check_input(binding["identity"], native_binding=binding)
    if binding["provider"] == "codex":
        from cmux_codex_input import DaemonRPC, prepare, validate_request
        with DaemonRPC(timeout=8) as client:
            request = prepare(client, binding, text)
            validate_request(request, binding, text)
        capability = dict(route="codex-native-metadata", request=request)
    elif binding["provider"] == "claude":
        screens, structures = [], []
        for _ in range(2):
            screen = bridge.read_screen(surface, lines=lines)
            block = bridge._claude_bordered_compose(screen)
            if not block or block["body"].strip():
                raise ValueError("HANDSHAKE_CAPABILITY_DRAFT_OR_UNKNOWN_COMPOSER")
            structures.append({k: block[k] for k in ("body", "footer")})
            screens.append(dict(screen=screen,
                                screen_sha256=hashlib.sha256(screen.encode()).hexdigest(),
                                active_or_queued=bridge._queued_or_active_input(screen)))
        if structures[0] != structures[1]:
            raise ValueError("HANDSHAKE_CAPABILITY_COMPOSER_CHANGED")
        capability = dict(route="claude-guarded-composer", observations=screens,
                          structure=structures[0])
    else:
        raise ValueError("HANDSHAKE_CAPABILITY_PROVIDER_UNSUPPORTED")
    native.require_bound(bridge, surface, binding, text)
    check_input(binding["identity"], native_binding=binding)
    return dict(schema=SCHEMA, task_id=gate["task_id"], gate_sha256=digest(gate),
                executor=surface, ordinal=item["ordinal"], payload_sha256=binding["payload_sha256"],
                native_binding=binding, capability=capability, input_operations=0,
                observed_at_epoch=time.time())


def require_original(selected, gate, binding, text, native):
    """Reconcile only the original capability and the original actual input.

    Historical bridge-clear receipts retain their original contract. New proof
    never grants a resend or pretends that a write-and-clear probe took place.
    """
    if "handshake_precondition" not in selected:
        if selected.get("bridge_clear_confirmed") is not True:
            raise ValueError("ACK_ORIGINAL_BRIDGE_CLEAR_REQUIRED")
        return
    proof = selected["handshake_precondition"]
    if (not isinstance(proof, dict) or proof.get("schema") != SCHEMA
            or proof.get("input_operations") != 0 or type(proof.get("input_operations")) is not int
            or proof.get("task_id") != selected.get("task_id")
            or proof.get("gate_sha256") != digest(gate)
            or proof.get("executor") != selected.get("executor")
            or proof.get("ordinal") != selected.get("ordinal")
            or proof.get("payload_sha256") != hashlib.sha256(text.encode()).hexdigest()
            or selected.get("bridge_clear_confirmed") is not None):
        raise ValueError("ACK_ORIGINAL_CAPABILITY_REQUIRED")
    original = proof["native_binding"]
    native._validate_binding(original, text)
    for key in ("identity", "tty", "process", "provider", "session_id", "native_config_root"):
        if original.get(key) != binding.get(key):
            raise ValueError("ACK_ORIGINAL_CAPABILITY_BINDING_CHANGED")
    old, new = original["transcript"], binding["transcript"]
    if (any(old[k] != new[k] for k in ("path", "device", "inode"))
            or old["offset"] > new["offset"]
            or not original["bound_at_epoch"] <= proof["observed_at_epoch"] <= binding["bound_at_epoch"]):
        raise ValueError("ACK_ORIGINAL_CAPABILITY_FENCE_CHANGED")
    with native._open(old["path"]) as stream:
        native._check_file(stream, original)
    cap = proof["capability"]
    if original["provider"] == "codex":
        from cmux_codex_input import validate_request
        if cap.get("route") != "codex-native-metadata":
            raise ValueError("ACK_ORIGINAL_CAPABILITY_ROUTE_CHANGED")
        validate_request(cap["request"], original, text)
    elif original["provider"] == "claude":
        import cmux_bridge as bridge
        observations = cap.get("observations")
        if (cap.get("route") != "claude-guarded-composer" or not isinstance(observations, list)
                or len(observations) != 2):
            raise ValueError("ACK_ORIGINAL_CAPABILITY_ROUTE_CHANGED")
        for observation in observations:
            screen = observation["screen"]
            block = bridge._claude_bordered_compose(screen)
            if (hashlib.sha256(screen.encode()).hexdigest() != observation["screen_sha256"]
                    or not block or block["body"].strip()
                    or {k: block[k] for k in ("body", "footer")} != cap.get("structure")):
                raise ValueError("ACK_ORIGINAL_CAPABILITY_COMPOSER_CHANGED")
    else:
        raise ValueError("ACK_ORIGINAL_CAPABILITY_ROUTE_CHANGED")
