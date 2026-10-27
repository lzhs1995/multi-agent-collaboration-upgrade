"""Run-scoped producer journal joined exactly to every native input unit.

A unit is any input-bearing native record inside a pinned lane interval:
Claude user records with a non-tool_result block, queued_command attachments
and queue enqueues; Codex role=user messages. Multi-block and queued inputs are
units too, so an input that native_text cannot read never counts as zero.

Only an intent written behind a pre-send native fence plus a landed entry that
names the exact native records attributes a unit. Its producer id and kind must
match the registration pinned before the run, and that producer's registered
native session must hold exactly one helper send, decoded by the shared
invocation reader, made no later than the intent: the registered helper path,
this receiver's surface, --request-id equal to the intent id, and a message
whose recomputed helper notice hashes to the journaled and landed payload.
A call that only prints, searches, reads or reconciles the id is not a send.
The registered kind decides MACHINE or
HUMAN_REGISTERED; the latter is a registered operator session, not a proved
person. Everything else, including text-only harness injections and
unjournaled metadata records, stays UNKNOWN. The journal proves what a
registered session invoked and recorded inside this run, not when it wrote it,
nor the sender's exit status: closeout must pin it, and payloads need a
run-unique marker because identical text cannot be told apart.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path
from uuid import UUID

from cmux_prompt_reference import helper_request
from orchestration_common import (ContractError, canonical, checked_path, digest, exclusive_lock, json_ref,
                                  parse_time, read_ref, read_regular, require, strict_json, utc_now)
from orchestration_inputs import invocation_calls
from upgrade_body_read_evidence import calls_and_results, valid_command

JOURNAL_SCHEMA = "upgrade-input-provenance-journal-v1"
REGISTRATION_SCHEMA = "upgrade-input-producers-v1"
RESULT_SCHEMA = "upgrade-input-provenance-v1"
PRODUCER_CLASS = {"upgrade_driver": "MACHINE", "mac_bridge": "MACHINE", "lane_agent": "MACHINE",
                  "operator_console": "HUMAN_REGISTERED"}
TRANSPORTS = ("cmux_helper",)
HELPER_NAMES = ("cmux-agent", "cmux_agent_adapter.py")
HELPER_MODES = ("send", "ask")
CLASSES = ("HUMAN_REGISTERED", "MACHINE", "UNKNOWN")
INTENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}")
GENESIS = "0" * 64
LANDING = ("user", "queued_command", "message")
ERRORS = (ContractError, KeyError, TypeError, ValueError, AttributeError)


def _exact(value, kind):
    """One text block is its text; any other shape is its canonical JSON."""
    if isinstance(value, str):
        return value
    if (isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict)
            and value[0].get("type") == kind and isinstance(value[0].get("text"), str)):
        return value[0]["text"]
    return canonical(value).decode()


def unit_payload(record, provider):
    """(kind, exact input) carried by one native record, or None for a non-input record."""
    if provider == "codex":
        message = record.get("payload")
        # Developer/system messages are harness instructions no terminal input can produce.
        if (record.get("type") == "response_item" and isinstance(message, dict)
                and message.get("type") == "message" and message.get("role") == "user"):
            return "message", _exact(message.get("content"), "input_text")
        return None
    if record.get("isSidechain") is True:
        # Sidechain records are the lane's own subagent traffic, not input to the lane.
        return None
    kind = record.get("type")
    if kind == "queue-operation":
        return ("enqueue", _exact(record.get("content"), "text")) if record.get("operation") == "enqueue" else None
    if kind == "attachment":
        attachment = record.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
            return "queued_command", _exact(attachment.get("prompt", attachment.get("content")), "text")
        return None
    if kind != "user":
        return None
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list) and content and all(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return None
    return "user", _exact(content, "text")


def _units(reader, start, end, lane):
    for ref, record in reader.records(start, end):
        found = unit_payload(record, reader.provider)
        if found is not None:
            yield dict(lane=lane, ref=ref, kind=found[0], payload_sha256=digest(found[1].encode()),
                       at=record.get("timestamp"))


def _key(ref):
    return tuple(ref.get(name) for name in ("path", "device", "inode", "offset", "length", "sha256"))


def record_key(ref):
    """Identity of one native record; extra fields a proof carries (session, reception kind) are ignored."""
    return _key(ref)


def landing_refs(reader, fence, end, payload):
    """Exact refs a producer records once its send lands: [first enqueue,] first landing."""
    sha, refs = digest(payload.encode()), []
    for unit in _units(reader, reader.verify_pin(fence), reader.verify_pin(end), None):
        if unit["payload_sha256"] == sha and (unit["kind"] in LANDING or not refs):
            refs.append(unit["ref"])
            if unit["kind"] in LANDING:
                break
    return refs


def _entries(raw, run_id):
    require(raw.endswith(b"\n"), "provenance journal must end at an entry boundary")
    entries, previous = [], GENESIS
    for seq, line in enumerate(raw.split(b"\n")[:-1]):
        line += b"\n"
        entry = strict_json(line, "provenance entry")
        require(isinstance(entry, dict) and canonical(entry) == line, "noncanonical provenance entry")
        require(entry.get("schema") == JOURNAL_SCHEMA and entry.get("run_id") == run_id, "foreign provenance journal")
        require(entry.get("seq") == seq and entry.get("previous_sha256") == previous, "provenance chain broken")
        require((entry.get("kind") == "header") == (seq == 0), "provenance header must be the only first entry")
        entries.append(entry)
        previous = digest(line)
    return entries[0], entries[1:]


def _registration(ref, run_id):
    """Producers frozen before the run: id -> kind and the native session it calls from."""
    value = json_ref(ref, "producer registration")
    require(value.get("schema") == REGISTRATION_SCHEMA and value.get("run_id") == run_id,
            "foreign producer registration")
    producers = value.get("producers")
    require(isinstance(producers, list) and producers, "producer registration is empty")
    found = {}
    for producer in producers:
        require(isinstance(producer, dict) and producer.get("kind") in PRODUCER_CLASS
                and isinstance(producer.get("id"), str) and producer["id"] and producer["id"] not in found,
                "registered producer needs a unique id and a known kind")
        session = producer.get("session")
        require(isinstance(session, dict) and session.get("provider") in ("claude", "codex")
                and isinstance(session.get("session_id"), str) and isinstance(session.get("transcript"), dict),
                "registered producer session required")
        found[producer["id"]] = dict(producer, transport=_transport(producer.get("transport")))
    return found


def _transport(value):
    """The frozen wrapper a registered producer sends through, and its payload inputs.

    Only the shared helper transport is recognised: its request payload is a pure
    function of (identity, mode, message, request_id, home), so a verifier can
    recompute the exact bytes a receiver must land instead of trusting the sender.
    """
    require(isinstance(value, dict) and value.get("kind") in TRANSPORTS, "registered sender transport required")
    helper, home = value.get("helper"), value.get("home")
    require(isinstance(helper, str) and os.path.isabs(helper)
            and os.path.basename(helper) in HELPER_NAMES, "registered helper must be an absolute shared helper path")
    require(isinstance(home, str) and os.path.isabs(home), "registered helper home must be absolute")
    caller, workspace = value.get("caller_surface_uuid"), value.get("workspace_uuid")
    require(_uuid(caller) and _uuid(workspace), "registered caller surface and workspace uuids required")
    return dict(kind=value["kind"], helper=helper, home=home,
                caller_surface_uuid=_uuid(caller), workspace_uuid=_uuid(workspace))


def _uuid(value):
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def expected_payload(producer, reader, *, mode, message, request_id):
    """The exact single-line notice this transport makes a receiver land.

    Recomputed from the registered transport and the receiver's own pinned
    identity, so a producer cannot assert a payload it did not actually compose.
    """
    transport = producer["transport"]
    session = reader.session
    identity = dict(workspace_uuid=transport["workspace_uuid"],
                    caller_surface_uuid=transport["caller_surface_uuid"],
                    target_surface_uuid=_uuid(session.get("surface_uuid")),
                    target_pane_uuid=_uuid(session.get("pane_uuid")))
    require(identity["target_surface_uuid"] and identity["target_pane_uuid"],
            "receiver surface and pane uuids required")
    require(_uuid(session.get("workspace_uuid")) == transport["workspace_uuid"], "WORKSPACE_MISMATCH")
    require(mode in HELPER_MODES, "helper mode is not a send")
    return helper_request(identity, mode, message, request_id, home=Path(transport["home"]))["payload"]


def _producer_calls(producer, supplied):
    """Every tool call inside one registered producer session interval, read once."""
    require(isinstance(supplied, dict), "PRODUCER_SESSION_NOT_SUPPLIED")
    reader, session = supplied["reader"], producer["session"]
    require(reader.provider == session["provider"] and reader.session["session_id"] == session["session_id"]
            and all(reader.pin.get(k) == session["transcript"].get(k) for k in ("path", "device", "inode")),
            "producer reader is not the registered session")
    calls = []
    for ref, record in reader.records(reader.verify_pin(supplied["start"]), reader.verify_pin(supplied["end"])):
        for call_id, name, args in calls_and_results(record, reader.provider)[0]:
            calls.append(dict(ref=ref, at=record.get("timestamp"), call_id=call_id, name=name, args=args))
    return calls


class ProvenanceJournal:
    """Append-only producer journal; each entry is fsynced before the send it describes."""

    def __init__(self, path, run_id, registration):
        require(isinstance(run_id, str) and run_id, "provenance run id required")
        self.path, self.run_id = checked_path(path), run_id
        self.producers = _registration(registration, run_id)
        with exclusive_lock(self._lock(), wait_seconds=5):
            if self.path.exists():
                header, _ = _entries(read_regular(self.path), run_id)
                require(header.get("registration") == registration, "journal is bound to another registration")
            else:
                self._append(dict(kind="header", registration=dict(registration), created_at=utc_now()))

    def _lock(self):
        return self.path.with_name("." + self.path.name + ".lock")

    def _append(self, body):
        raw = read_regular(self.path) if self.path.exists() else b""
        if raw:
            _entries(raw, self.run_id)
        lines = raw.split(b"\n")[:-1]
        require((body["kind"] == "header") == (not lines), "provenance header must be the only first entry")
        entry = dict(body, schema=JOURNAL_SCHEMA, run_id=self.run_id, seq=len(lines),
                     previous_sha256=digest(lines[-1] + b"\n") if lines else GENESIS)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "ab") as stream:
            stream.write(canonical(entry))
            stream.flush()
            os.fsync(stream.fileno())
        return entry

    def intent(self, *, intent_id, producer, lane, reader, fence, mode, message, purpose=""):
        """Record one send before it happens; fence is the receiver snapshot taken before the send.

        The producer's registered session must already hold the one helper call that
        sends message with --request-id intent_id; the journal keeps only the hash of
        the notice that call makes the receiver land, never the message text.
        """
        require(isinstance(intent_id, str) and INTENT_ID.fullmatch(intent_id), "run-unique intent id required")
        pid = producer.get("id") if isinstance(producer, dict) else None
        registered = self.producers.get(pid) if isinstance(pid, str) else None
        require(registered is not None and registered["kind"] == producer.get("kind"), "registered producer required")
        require(isinstance(message, str) and isinstance(purpose, str), "exact message text required")
        payload = expected_payload(registered, reader, mode=mode, message=message, request_id=intent_id)
        reader.verify_pin(fence)
        with exclusive_lock(self._lock(), wait_seconds=5):
            return self._append(dict(kind="intent", intent_id=intent_id,
                producer=dict(id=pid, kind=registered["kind"]), lane=lane,
                session_id=reader.session["session_id"], surface_uuid=reader.session.get("surface_uuid"),
                fence=fence, payload_sha256=digest(payload.encode()), payload_bytes=len(payload.encode()),
                purpose=purpose, at=utc_now()))

    def landed(self, *, intent_id, refs):
        """Record the exact native records one intent produced (see landing_refs)."""
        require(isinstance(refs, list) and 1 <= len(refs) <= 2, "one landing ref, optionally after its enqueue")
        with exclusive_lock(self._lock(), wait_seconds=5):
            return self._append(dict(kind="landed", intent_id=intent_id, refs=[dict(ref) for ref in refs],
                                     at=utc_now()))


def _foreground(call, item):
    """The decoded send is the call's whole, direct foreground command.

    The shared reader splits shell control operators, so it also decodes a send
    after `false &&`, `true ||` or `;`, inside a pipeline, backgrounded with `&`
    or nested in another shell. None of those proves the send ran (ROOT R48):
    admit only one shell frame whose segment is the entire tool command, run as
    a direct foreground tool invocation (valid_command).
    """
    arguments = (item.get("native_tool") or {}).get("arguments")
    command = arguments.get("cmd", arguments.get("command")) if isinstance(arguments, dict) else None
    frames = item.get("shell") or []
    try:
        return (isinstance(command, str) and len(frames) == 1
                and frames[0].get("segment", "").strip() == command.strip()
                and valid_command(call["name"], call["args"], command))
    except ERRORS:
        return False


def _sends(call, producer, reader, intent_id, payload_sha256):
    """Whether one native tool call is the helper send of exactly this intent.

    Decoded by the shared invocation reader (orchestration_inputs.invocation_calls);
    printing, reading or reconciling the id, another wrapper path, another
    receiver or another payload is not a send of this intent.
    """
    try:
        decoded = invocation_calls(call["name"], call["args"])
    except ERRORS:
        return False
    for item in decoded:
        frames = item.get("shell") or [{}]
        # expected_payload requires the receiver's own surface uuid and a send/ask mode, so a
        # surfaceless broadcast or a reconcile (no --request-id) never matches.
        if (item.get("kind") != "helper"
                or frames[-1].get("entrypoint") != producer["transport"]["helper"]
                or _uuid(item.get("surface")) != _uuid(reader.session.get("surface_uuid"))
                or item.get("request_id") != intent_id or not _foreground(call, item)):
            continue
        try:
            payload = expected_payload(producer, reader, mode=item["operation"], message=item["text"],
                                       request_id=intent_id)
        except ERRORS:
            continue
        if digest(payload.encode()) == payload_sha256:
            return True
    return False


def _claim(intent, landed, readers, units, bounds, window, producers, calls):
    """(exact unit keys, producer call ref) of one intent; any mismatch raises and attributes nothing."""
    lane = intent.get("lane")
    require(lane in readers, "UNKNOWN_LANE")
    reader = readers[lane]
    require(intent.get("session_id") == reader.session["session_id"], "SESSION_MISMATCH")
    require(isinstance(reader.session.get("surface_uuid"), str)
            and intent.get("surface_uuid") == reader.session["surface_uuid"], "SURFACE_MISMATCH")
    producer = intent.get("producer")
    pid = producer.get("id") if isinstance(producer, dict) else None
    registered = producers.get(pid) if isinstance(pid, str) else None
    require(registered is not None and registered["kind"] == producer.get("kind"), "UNREGISTERED_PRODUCER")
    fence = reader.verify_pin(intent.get("fence"))
    require(bounds[lane][0] <= fence <= bounds[lane][1], "FENCE_OUTSIDE_RUN")
    at = parse_time(intent.get("at")).timestamp()
    require(window[0] <= at <= window[1], "INTENT_OUTSIDE_RUN")
    ident = intent.get("intent_id")
    require(isinstance(ident, str) and INTENT_ID.fullmatch(ident), "INTENT_ID_NOT_RUN_UNIQUE")
    require(pid in calls, "PRODUCER_SESSION_UNREADABLE")
    payload = intent.get("payload_sha256")
    require(isinstance(payload, str) and re.fullmatch(r"[0-9a-f]{64}", payload), "PAYLOAD_SHA256_INVALID")
    # _foreground admits at most one send per call: the call's whole command.
    sending = [call for call in calls[pid] if _sends(call, registered, reader, ident, payload)]
    require(len(sending) == 1, "ONE_SENDING_INVOCATION_REQUIRED")
    sent = sending[0]
    require(isinstance(sent["call_id"], str) and sent["call_id"]
            and sum(call["call_id"] == sent["call_id"] for call in calls[pid]) == 1, "ONE_ORIGINAL_CALL_ID_REQUIRED")
    sent_at = parse_time(sent["at"], "producer call timestamp").timestamp()
    require(window[0] - 0.001 <= sent_at <= math.ceil(at * 1000) / 1000, "SENDING_INVOCATION_OUTSIDE_INTENT_WINDOW")
    refs = landed.get("refs")
    require(isinstance(refs, list) and 1 <= len(refs) <= 2 and all(isinstance(ref, dict) for ref in refs),
            "LANDED_REFS_INVALID")
    keys = [_key(ref) for ref in refs]
    require(all(key in units and units[key]["lane"] == lane for key in keys), "REF_IS_NOT_A_UNIT_OF_THIS_LANE")
    claimed = [units[key] for key in keys]
    require(all(unit["payload_sha256"] == intent.get("payload_sha256") for unit in claimed), "PAYLOAD_MISMATCH")
    offsets = [unit["ref"]["offset"] for unit in claimed]
    require(fence <= offsets[0] and offsets == sorted(set(offsets)), "REF_BEFORE_FENCE_OR_UNORDERED")
    kinds = [unit["kind"] for unit in claimed]
    require(kinds[-1] in LANDING and all(kind == "enqueue" for kind in kinds[:-1]), "ENQUEUE_THEN_LANDING_REQUIRED")
    # Native CLIs stamp milliseconds; admit that precision only.
    earliest, landed_at = math.floor(at * 1000) / 1000, parse_time(landed.get("at")).timestamp()
    for unit in claimed:
        native_at = parse_time(unit["at"], "native timestamp").timestamp()
        require(earliest <= native_at <= landed_at <= window[1], "NATIVE_RECORD_OUTSIDE_INTENT_WINDOW")
        landing = unit["kind"] in LANDING
        require(not any(other["lane"] == lane and other["payload_sha256"] == unit["payload_sha256"]
                        and (other["kind"] in LANDING) == landing
                        and fence <= other["ref"]["offset"] < unit["ref"]["offset"] for other in units.values()),
                "EARLIER_IDENTICAL_INPUT_AFTER_FENCE")
    return keys, sent["ref"]


def registered_producers(registration, run_id):
    """The frozen producer registration of one run (id -> producer); raises if it is not one."""
    return _registration(registration, run_id)


def lane_producer_sessions(registration, run_id, readers, starts, ends):
    """Route each registered producer to the run lane holding its session.

    Routing only: _producer_calls still authenticates provider, session id and
    transcript identity. A producer outside the run's pinned lanes has no pinned
    interval, so its intents stay unresolved instead of being trusted.
    """
    try:
        producers = _registration(registration, run_id)
    except ERRORS:
        return {}
    return {pid: dict(reader=reader, start=starts[lane], end=ends[lane])
            for pid, producer in producers.items() for lane, reader in readers.items()
            if reader.session["session_id"] == producer["session"]["session_id"]}


def classify_inputs(journal, registration, run_id, readers, starts, ends, *, producer_sessions,
                    not_before, not_after):
    """Attribute every native input unit of a closed run; unverifiable units stay UNKNOWN.

    producer_sessions maps each registered producer id to dict(reader, start, end) of its session.
    """
    bounds = {lane: (reader.verify_pin(starts[lane]), reader.verify_pin(ends[lane]))
              for lane, reader in readers.items()}
    ordered = [unit for lane, reader in readers.items() for unit in _units(reader, *bounds[lane], lane)]
    units = {_key(unit["ref"]): unit for unit in ordered}
    window = (parse_time(not_before).timestamp(), parse_time(not_after).timestamp())
    result = dict(schema=RESULT_SCHEMA, run_id=run_id, journal=journal, registration=registration, units=[],
                  unresolved=[], unlanded_intents=[])
    producers, entries, calls = {}, [], {}
    try:
        producers = _registration(registration, run_id)
        require(journal is not None, "no provenance journal was pinned for this run")
        header, entries = _entries(read_ref(journal, "provenance journal"), run_id)
        require(header.get("registration") == registration, "journal is bound to another registration")
    except ERRORS as exc:
        # A foreign, torn or edited journal or registration supports no attribution at all.
        producers, entries = {}, []
        result["unresolved"].append(dict(intent_id=None, reason=str(exc)))
    for pid, producer in producers.items():
        try:
            calls[pid] = _producer_calls(producer, producer_sessions.get(pid))
        except ERRORS as exc:
            result["unresolved"].append(dict(intent_id=None, producer_id=pid, reason=str(exc)))
    intents, landings, claims = {}, {}, {}
    for entry in entries:
        bucket = {"intent": intents, "landed": landings}.get(entry.get("kind"))
        if bucket is None:
            result["unresolved"].append(dict(intent_id=entry.get("intent_id"), reason="UNKNOWN_ENTRY_KIND"))
            continue
        bucket.setdefault(entry.get("intent_id"), []).append(entry)
    payloads = Counter(entry.get("payload_sha256") for group in intents.values() for entry in group)
    for ident in sorted(set(intents) | set(landings), key=str):
        mine, done = intents.get(ident, []), landings.get(ident, [])
        if len(mine) == 1 and not done:
            result["unlanded_intents"].append(ident)
            continue
        try:
            require(len(mine) == 1 and len(done) == 1, "ONE_INTENT_AND_ONE_LANDED_REQUIRED")
            require(done[0]["seq"] > mine[0]["seq"], "LANDED_BEFORE_INTENT")
            require(payloads[mine[0].get("payload_sha256")] == 1, "PAYLOAD_NOT_RUN_UNIQUE")
            keys, call = _claim(mine[0], done[0], readers, units, bounds, window, producers, calls)
            for key in keys:
                claims.setdefault(key, []).append((ident, PRODUCER_CLASS[mine[0]["producer"]["kind"]], call))
        except ERRORS as exc:
            result["unresolved"].append(dict(intent_id=ident, reason=str(exc)))
    for unit in ordered:
        found = claims.get(_key(unit["ref"]), [])
        # Run-unique payloads leave at most one claimant; never let a second one pick a winner.
        ident, provenance, call = found[0] if len(found) == 1 else (None, "UNKNOWN", None)
        result["units"].append(dict(unit, provenance=provenance, intent_id=ident, producer_call=call))
    for lane, reader in readers.items():
        reader.verify_pin(starts[lane])
        reader.verify_pin(ends[lane])
    counts = Counter(unit["provenance"] for unit in result["units"])
    result["counts"] = {name: counts[name] for name in CLASSES}
    # Registered operator sends only; an independently proved person is not measured here.
    humans = {unit["intent_id"] for unit in result["units"] if unit["provenance"] == "HUMAN_REGISTERED"}
    # Zero is a measurement only when every unit is attributed; otherwise it is unknown.
    result["human_interventions"] = len(humans) if not counts["UNKNOWN"] else None
    return result
