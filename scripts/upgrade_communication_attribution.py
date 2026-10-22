"""Read-only attribution of exact registered inputs inside a closed native run.

This proves automatic input provenance, not delivery success, body reading,
business acceptance or efficiency. Never exempts a prefix or a text value: only
one original, pinned native record may be excluded from unknown user inputs.
"""
from __future__ import annotations

from collections import Counter
import math
from uuid import UUID

from cmux_prompt_reference import verify, wire_reference
from orchestration_common import parse_time
from orchestration_contracts import state_root
from orchestration_native import native_text
from upgrade_body_read_evidence import calls_and_results, stamp, valid_command
from upgrade_communication_observer import (Uncertain, canonical, canonical_identity, expected_intent,
    json_file, need, original_dispatch_binding, original_event, original_journal, raw_file, sha)
from upgrade_communication_plan import WARM_RUNS, campaign_plan

ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError,
          OverflowError, RecursionError, Uncertain)


def _original(sample, campaign, state, readers, starts, ends, not_before, not_after):
    sender, receiver = (campaign["sessions"][sample[k]] for k in ("sender", "receiver"))
    identity = canonical_identity(dict(workspace_uuid=receiver["workspace_uuid"], caller_surface_uuid=sender["surface_uuid"],
                    target_surface_uuid=receiver["surface_uuid"], target_pane_uuid=receiver["pane_uuid"]))
    need(str(UUID(sender["workspace_uuid"])) == identity["workspace_uuid"], "WORKSPACE_MISMATCH")
    spec, marker, channel = expected_intent(sample, identity)
    intent_path = state / "cmux-agent-adapter-v1/targets" / channel / "intents" / (marker + ".json")
    intent, intent_pin = json_file(intent_path)
    need(all(intent.get(k) == v for k, v in spec.items()) and intent.get("marker") == marker,
         "EXACT_REQUEST_OR_IDENTITY_CHANGED")
    payload = intent["payload"]
    need(isinstance(payload, str) and sha(payload) == intent["payload_sha256"], "PAYLOAD_CHANGED")
    full = "[CMUX-AGENT][delivery:" + marker + "][from:" + identity["caller_surface_uuid"] + "]\n" + sample["body"]
    reference = intent["body_reference"]
    need(reference == dict(path=str(state / "message-bodies-v1" / (sha(full) + ".txt")),
                           sha256=sha(full), bytes=len(full.encode()), format="cmux-message-reference-v2")
         and intent.get("original_payload_sha256") == sha(full), "BODY_REFERENCE_CHANGED")
    body, body_pin = raw_file(reference["path"])
    need(body == full.encode(), "BODY_CONTENT_CHANGED")
    need(wire_reference(payload) == reference and payload.split(" ", 3)[2] == marker, "NOTICE_CHANGED")
    verify(reference, full)
    journal = original_journal(state, identity, marker)
    need(sorted(p.name for p in journal.glob("attempt-*.json")) == ["attempt-0001.json"],
         "ONE_ORIGINAL_ATTEMPT_REQUIRED")
    attempt, attempt_pin = json_file(journal / "attempt-0001.json")
    expected_binding = original_dispatch_binding(attempt, journal, identity, marker, payload)
    binding = attempt["native_binding"]
    need(binding.get("schema") == "native-delivery-v1" and binding.get("identity") == expected_binding["identity"]
         and binding.get("payload_sha256") == sha(payload)
         and all(binding.get(k) == receiver[k] for k in ("provider", "session_id", "process", "tty")),
         "ORIGINAL_RECEIVER_BINDING_CHANGED")
    event, fence = original_event(attempt, binding, payload)
    reader, lane = readers[sample["receiver"]], sample["receiver"]
    bound_offset = reader.verify_pin(binding["transcript"])
    fence_offset = reader.verify_pin(fence)
    need(starts[lane] <= bound_offset <= fence_offset <= ends[lane], "ORIGINAL_FENCE_OUTSIDE_RUN")
    need(not_before <= binding["bound_at_epoch"] <= event["at_epoch"] <= not_after,
         "ORIGINAL_INPUT_OUTSIDE_RUN")
    return dict(sample=sample, payload=payload, marker=marker, journal=journal, event=event,
                fence=fence, binding=binding, pins=[intent_pin, attempt_pin, body_pin],
                calls=[], notices=[])


def _session_valid(record, reader):
    if reader.provider == "claude":
        return record.get("sessionId") == reader.session["session_id"] and record.get("isSidechain") is not True
    if record.get("type") == "session_meta":
        payload = record.get("payload", {})
        need(isinstance(payload, dict) and all(payload[k] == reader.session["session_id"]
             for k in ("id", "session_id") if k in payload), "CONTRADICTORY_NATIVE_SESSION")
    return True


def _scan(items, readers, starts, ends):
    """Stream pinned intervals twice; retain at most two candidates per slot.

    The second pass counts every occurrence of relevant call IDs, including an
    invalid timestamp or unrelated command. Filtering those first hides replay.
    """
    for lane, reader in readers.items():
        outgoing = [item for item in items if item["sample"]["sender"] == lane]
        incoming = [item for item in items if item["sample"]["receiver"] == lane]
        for ref, record in reader.records(starts[lane], ends[lane]):
            session_valid = _session_valid(record, reader)
            text = native_text(record, reader.provider, "user")
            for item in incoming:
                if text == item["payload"] and len(item["notices"]) < 2:
                    item["notices"].append((ref, stamp(record), session_valid))
            calls, _ = calls_and_results(record, reader.provider)
            for call_id, name, args in calls:
                for item in outgoing:
                    try:
                        matches = valid_command(name, args, item["sample"]["command"])
                    except (ValueError, TypeError):
                        matches = False
                    if matches and len(item["calls"]) < 2:
                        item["calls"].append((ref, call_id, stamp(record), session_valid))
        ids = {call[1] for item in outgoing for call in item["calls"]
               if isinstance(call[1], str) and call[1]}
        counts = Counter()
        if ids:
            for _, record in reader.records(starts[lane], ends[lane]):
                _session_valid(record, reader)
                calls, _ = calls_and_results(record, reader.provider)
                for call_id, _, _ in calls:
                    if isinstance(call_id, str) and call_id in ids:
                        counts[call_id] += 1
        for item in outgoing:
            item["call_id_counts"] = counts


def attribute_inputs(campaign, header, native_end, readers, *, not_after):
    """Return deterministic evidence; any unverifiable input remains unknown."""
    result = dict(schema="upgrade-communication-attribution-v1", inputs=[], unresolved=[])
    run_id = header["run"]["run_id"]
    if run_id not in WARM_RUNS:
        return result
    items = []
    try:
        samples = [s for s in campaign_plan(campaign) if s["run_id"] == run_id]
        need(len({s["request_id"] for s in samples}) == len(samples), "DUPLICATE_REGISTERED_REQUEST")
        starts = {lane: reader.verify_pin(header["native_start"][lane]) for lane, reader in readers.items()}
        ends = {lane: reader.verify_pin(native_end[lane]) for lane, reader in readers.items()}
        first, last = parse_time(header["started_at"]).timestamp(), parse_time(not_after).timestamp()
        state = state_root(campaign)
        for sample in samples:
            try:
                items.append(_original(sample, campaign, state, readers, starts, ends, first, last))
            except ERRORS as exc:
                result["unresolved"].append(dict(request_id=sample["request_id"], reason=type(exc).__name__))
        _scan(items, readers, starts, ends)
        for item in items:
            sample = item["sample"]
            try:
                need(len(item["calls"]) == len(item["notices"]) == 1, "ONE_CALL_AND_NATIVE_NOTICE_REQUIRED")
                call, call_id, call_at, call_session = item["calls"][0]
                notice, notice_at, notice_session = item["notices"][0]
                need(isinstance(call_id, str) and call_id and item["call_id_counts"][call_id] == 1,
                     "ONE_ORIGINAL_CALL_ID_REQUIRED")
                # Native CLIs use millisecond timestamps; intent clocks have
                # finer precision. Admit that precision only, never live time.
                earliest = math.floor(item["event"]["at_epoch"] * 1000) / 1000
                need(call_session and notice_session and call_at is not None and notice_at is not None
                     and first <= call_at <= item["binding"]["bound_at_epoch"]
                     and earliest <= notice_at <= last
                     and notice["offset"] >= item["fence"]["offset"], "NATIVE_CHRONOLOGY_OR_SESSION_CHANGED")
                need(sorted(p.name for p in item["journal"].glob("attempt-*.json")) == ["attempt-0001.json"],
                     "ORIGINAL_ATTEMPT_SET_CHANGED")
                need(original_journal(state, item["binding"]["identity"], item["marker"]) == item["journal"],
                     "ORIGINAL_JOURNAL_CHANGED")
                for pin in item["pins"]:
                    _, current = raw_file(pin["path"])
                    need(current == pin, "ORIGINAL_SUPPORT_CHANGED")
                readers[sample["sender"]].record(call)
                readers[sample["receiver"]].record(notice)
                result["inputs"].append(dict(request_id=sample["request_id"], sender=sample["sender"],
                    receiver=sample["receiver"], native_record=notice, sender_call=call, call_id=call_id,
                    marker=item["marker"], original_input_sha256=sha(canonical(item["event"])),
                    original_binding_sha256=sha(canonical(item["binding"])),
                    intent=item["pins"][0], attempt=item["pins"][1], body=item["pins"][2]))
            except ERRORS as exc:
                result["unresolved"].append(dict(request_id=sample["request_id"], reason=type(exc).__name__))
        for lane, reader in readers.items():
            reader.verify_pin(header["native_start"][lane])
            reader.verify_pin(native_end[lane])
    except ERRORS as exc:
        # A partial or foreign native interval cannot support any exemption.
        result["inputs"] = []
        result["unresolved"].append(dict(request_id=None, reason=type(exc).__name__))
    result["inputs"].sort(key=lambda item: item["request_id"])
    result["unresolved"].sort(key=lambda item: item["request_id"] or "")
    return result
