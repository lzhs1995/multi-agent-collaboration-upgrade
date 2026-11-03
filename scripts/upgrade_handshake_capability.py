"""Read-only initial handshake checks for newly materialized private controllers.

This evidence is not a bridge-clear proof, input intent, native receipt or ACK.
The existing sender still owns fresh input gates, its journal and one challenge.
"""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path

from upgrade_handshake_identity import bound_rows
from upgrade_input_gate import check_input

SCHEMA = "upgrade-readonly-handshake-v1"
REFUSAL_SCHEMA = "upgrade-readonly-handshake-refusal-v1"


def record_dispatch_failure(receipt, details):
    """Preserve returned evidence only; never discover or modify an attempt.

    This is a diagnostic pin, not native receipt, controller authentication or
    proof that no input occurred. Missing sender details stay unavailable.
    """
    from cmux_evidence_io import snapshot

    receipt["dispatch_error_details"] = deepcopy(details)
    receipt["dispatch_evidence_status"] = "UNAVAILABLE"
    try:
        if not isinstance(details, dict) or not isinstance(details.get("attempt"), str):
            raise ValueError("sender did not return an original attempt path")
        path = Path(details["attempt"])
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise ValueError("sender attempt must be an absolute original path")
        raw, identity = snapshot(path)
        if not isinstance(json.loads(raw), dict):
            raise ValueError("original sender attempt must be an object")
        if snapshot(path) != (raw, identity):
            raise ValueError("original attempt changed while saving its pin")
        receipt.update(
            dispatch_attempt=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                                  bytes=len(raw), identity=list(identity)),
            dispatch_evidence_status="ORIGINAL_ATTEMPT_PINNED",
            dispatch_controller_evidence_status="UNVERIFIED",
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        receipt["dispatch_evidence_unavailable_reason"] = str(exc)


class CapabilityRefused(ValueError):
    """An observation refusal, never a successful capability or input proof."""

    def __init__(self, error, diagnostic):
        super().__init__(str(error))
        self.diagnostic = diagnostic


def _error_info(error):
    return dict(type=type(error).__name__, message=str(error))


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
    diagnostic = dict(schema=REFUSAL_SCHEMA, executor=item.get("surface_ref"),
                      ordinal=item.get("ordinal"), provider=None, lines=lines,
                      input_operations=0, stage="bind_target", read_index=None,
                      observations=[])
    try:
        return _observe(bridge, native, gate, members, item, text, lines, diagnostic)
    except Exception as error:
        diagnostic["error"] = _error_info(error)
        raise CapabilityRefused(error, diagnostic) from error


def _observe(bridge, native, gate, members, item, text, lines, diagnostic):
    surface = item["surface_ref"]
    binding = native.bind_target(bridge, surface, text)
    diagnostic["provider"] = binding.get("provider")
    diagnostic["stage"] = "bound_rows"
    _, row = bound_rows(panel_rows(gate, members), members, gate, binding)
    diagnostic["stage"] = "executor_check"
    if row["executor"] != surface or row["ordinal"] != item["ordinal"]:
        raise ValueError("HANDSHAKE_CAPABILITY_EXECUTOR_CHANGED")
    diagnostic["stage"] = "initial_input_check"
    check_input(binding["identity"], native_binding=binding)
    if binding["provider"] == "codex":
        diagnostic["stage"] = "codex_metadata"
        from cmux_codex_input import DaemonRPC, prepare, validate_request
        with DaemonRPC(timeout=8) as client:
            request = prepare(client, binding, text)
            validate_request(request, binding, text)
        capability = dict(route="codex-native-metadata", request=request)
    elif binding["provider"] == "claude":
        screens, structures = [], []
        for read_index in (1, 2):
            diagnostic.update(stage="claude_read_screen", read_index=read_index)
            observation = dict(read_index=read_index, read_status="STARTED", screen=None,
                               screen_sha256=None, parser_status="NOT_RUN", parser_block=None)
            diagnostic["observations"].append(observation)
            try:
                screen = bridge.read_screen(surface, lines=lines)
            except Exception as error:
                observation.update(read_status="ERROR", error=_error_info(error))
                raise
            observation.update(read_status="READ", screen=screen,
                               screen_sha256=hashlib.sha256(screen.encode()).hexdigest())
            diagnostic["stage"] = "claude_parse_composer"
            try:
                block = bridge._claude_bordered_compose(screen)
            except Exception as error:
                observation.update(parser_status="ERROR", error=_error_info(error))
                raise
            observation.update(parser_status="PARSED" if block else "MISSING",
                               parser_block=deepcopy(block))
            diagnostic["stage"] = "claude_check_composer"
            if not block or block["body"].strip():
                raise ValueError("HANDSHAKE_CAPABILITY_DRAFT_OR_UNKNOWN_COMPOSER")
            structures.append({k: block[k] for k in ("body", "footer")})
            diagnostic["stage"] = "claude_activity"
            screens.append(dict(screen=screen,
                                screen_sha256=hashlib.sha256(screen.encode()).hexdigest(),
                                active_or_queued=bridge._queued_or_active_input(screen)))
        diagnostic["stage"] = "claude_stability"
        if structures[0] != structures[1]:
            raise ValueError("HANDSHAKE_CAPABILITY_COMPOSER_CHANGED")
        capability = dict(route="claude-guarded-composer", observations=screens,
                          structure=structures[0])
    else:
        diagnostic["stage"] = "provider_check"
        raise ValueError("HANDSHAKE_CAPABILITY_PROVIDER_UNSUPPORTED")
    diagnostic.update(stage="final_binding_check", read_index=None)
    native.require_bound(bridge, surface, binding, text)
    diagnostic["stage"] = "final_input_check"
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
