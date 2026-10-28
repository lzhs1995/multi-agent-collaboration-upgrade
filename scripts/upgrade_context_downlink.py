"""Read-only MODEL_CONTEXT_CONSUMED observation of one original Codex->Claude notice.

An explicitly new task policy (surface40 v124) may accept that a busy Claude
lane consumed a MESSAGE_REFERENCE_V2 notice and its body even though Claude
2.1.303 wrote only a queued_command attachment, never a native user record.
This module runs the reviewed shared observer, unchanged, from its complete
pinned release tree in its own interpreter; that observer's import closure is a
different MAC lineage than this repository's, so it is never imported here.

The observation is a separate evidence class. It never sets or implies a native
user receipt, never writes an attempt or receipt, sends no input, consumes no
reply and accepts no work. An attempt that already holds a confirmed native
receipt keeps its native evidence and is refused here. The only writes are the
exact wire text and read-only observation artifacts, each created once beside
the caller's evidence.

Every observation is pinned to one explicit original snapshot endpoint (ROOT
R54-F1): the caller supplies it, this module recomputes the snapshot hash from
the original bytes, and `verify` reuses that same endpoint, so later unrelated
transcript growth can neither invent nor retract a completed proof. Until that
endpoint holds a complete proof, `observe-window` keeps each read-only
observation as its own increment and terminalizes on proof or the real deadline
(R54-F2); re-observation sends no input, pastes nothing and presses no key.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

from cmux_prompt_reference import SINGLE_LINE_FORMAT, _wire
from orchestration_common import (ContractError, canonical, checked_path, digest, file_ref, json_ref, read_ref,
                                  read_regular, require, strict_json, utc_now, write_bytes_once)
from orchestration_native import original_attempts

POLICY_SCHEMA = "prospective-claude-model-context-receipt-policy-v1"
OBSERVATION_SCHEMA = "model-context-consumption-observation-v1"
RESULT_SCHEMA = "upgrade-context-downlink-observation-v1"
WINDOW_SCHEMA = "upgrade-context-downlink-window-v1"
INCREMENT_SCHEMA = "upgrade-context-downlink-increment-v1"
ACK_SCHEMA = "upgrade-context-downlink-ack-order-v1"
EVIDENCE_CLASS = "MODEL_CONTEXT_CONSUMED"
OBSERVER_TIMEOUT_SECONDS = 60
# The observer's own bounded-snapshot limit; asserted against its source.
MAX_SCAN = 8 * 1024 * 1024
MARKER = re.compile(r"[A-Za-z0-9_.-]{8,160}")
# One exact single-line printable handshake ACK (late-handshake-ack.md).
ACK_LINE = re.compile(r"[ -~]{8,400}")
WINDOW_LIMITS = dict(interval=(1.0, 60.0), observations=(1, 720), horizon=3600)
# The observer reports exposure and consumption of the notice/body context only.
NEVER = ("native_user_received", "official_receipt_published", "attempt_changed", "replay_authorized",
         "business_accepted", "task_authorized", "resource_released")
ERRORS = (ContractError, KeyError, TypeError, ValueError, OSError)


def _source_files(root):
    names = set()
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        for name in subdirectories:
            require(not os.path.islink(os.path.join(directory, name)), "release source contains a symlinked directory")
        names.update(os.path.relpath(os.path.join(directory, name), root) for name in files)
    return names


def pinned_release(policy_ref):
    """The reviewed policy, its complete release source and its observer; any drift refuses."""
    policy = json_ref(policy_ref, "context policy")
    require(policy.get("schema") == POLICY_SCHEMA
            and policy.get("disposition") == "ACCEPTED_FOR_EXPLICIT_NEW_POLICY_BINDING"
            and policy.get("evidence_class") == EVIDENCE_CLASS
            and policy.get("native_user_evidence_class") == "NATIVE_USER_RECEIVED", "context policy is not v124-shaped")
    source = checked_path(policy.get("complete_source"))
    manifest_ref = policy.get("source_manifest")
    manifest = json_ref(manifest_ref, "release manifest")
    require(Path(manifest_ref["path"]) == source.parent / "MANIFEST.json", "manifest is not the release's own")
    files = manifest.get("files")
    require(isinstance(files, dict) and files, "release manifest lists no files")
    # The complete tree, not only the observer: its 27-module import closure must be the reviewed bytes.
    require(_source_files(source) == set(files), "release source differs from its manifest file set")
    for name, pin in files.items():
        raw = read_regular(source / name)
        require(isinstance(pin, dict) and pin.get("bytes") == len(raw) and pin.get("sha256") == digest(raw),
                f"release file differs from its manifest: {name}")
    observer_ref = policy.get("observer")
    observer = source / "scripts" / "model_context_consumption.py"
    require(isinstance(observer_ref, dict) and observer_ref.get("path") == str(observer)
            and observer_ref.get("sha256") == files["scripts/model_context_consumption.py"]["sha256"]
            and observer_ref.get("bytes") == files["scripts/model_context_consumption.py"]["bytes"],
            "policy observer is not the manifest's observer")
    require(_observer_constant(source / "scripts" / "model_context_consumption.py", "MAX_SCAN") == MAX_SCAN,
            "release observer uses a different bounded-snapshot limit")
    return dict(policy=dict(policy_ref), manifest=dict(manifest_ref), observer=dict(observer_ref), source=str(source))


def _integer(node):
    """A plain integer literal or product of them, e.g. 8 * 1024 * 1024; nothing is executed."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _integer(node.left) * _integer(node.right)
    raise ContractError("pinned observer constant is not a plain integer")


def _observer_constant(path, name):
    """Read one module-level integer from the pinned observer without importing it."""
    for node in ast.parse(read_regular(path).decode("utf-8"), str(path)).body:
        targets = getattr(node, "targets", [])
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in targets):
            return _integer(node.value)
    raise ContractError(f"pinned observer has no {name}")


def _fence(attempt):
    """The one original paste-intent fence; its transcript pins the observed file."""
    fences = [event.get("native_paste_fence") for event in attempt.get("events") or []
              if isinstance(event, dict) and event.get("phase") == "PASTE_INTENT"]
    require(len(fences) == 1 and isinstance(fences[0], dict)
            and fences[0].get("schema") == "native-paste-fence-v1", "one original paste-intent fence required")
    fence = fences[0]
    transcript = fence.get("transcript")
    require(isinstance(transcript, dict) and isinstance(transcript.get("path"), str)
            and all(type(transcript.get(k)) is int for k in ("offset", "device", "inode")) and transcript["offset"] >= 0,
            "original fence transcript pin required")
    return fence, transcript


def _open_pinned(transcript):
    """The pinned original transcript, by device/inode, never through a symlink."""
    path = checked_path(transcript["path"])
    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(handle, "rb")
    try:
        info = os.fstat(handle)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "pinned transcript owner/type mismatch")
        require((info.st_dev, info.st_ino) == (transcript["device"], transcript["inode"]),
                "pinned original transcript replaced")
    except BaseException:
        stream.close()
        raise
    return stream


def _snapshot(transcript, end):
    """The original bytes this observation is pinned to, recomputed here independently."""
    start = transcript["offset"]
    require(type(end) is int and start < end and end - start <= MAX_SCAN, "snapshot endpoint outside the bounded window")
    with _open_pinned(transcript) as stream:
        require(os.fstat(stream.fileno()).st_size >= end, "snapshot endpoint is past the original file")
        if start:
            stream.seek(start - 1)
            require(stream.read(1) == b"\n", "snapshot start is inside a record")
        stream.seek(start)
        raw = stream.read(end - start)
    require(len(raw) == end - start and raw.endswith(b"\n"), "snapshot endpoint is not a complete record boundary")
    return dict(path=transcript["path"], device=transcript["device"], inode=transcript["inode"],
                start=start, end=end, bytes=len(raw), sha256=digest(raw))


def _boundary(transcript):
    """(latest complete-record endpoint inside the bounded window or None, window full).

    Once the file reaches the window cap the capped bytes are immutable, so the
    endpoint is final; both values come from one read so growth cannot split them.
    """
    start = transcript["offset"]
    with _open_pinned(transcript) as stream:
        size = os.fstat(stream.fileno()).st_size
        end = min(size, start + MAX_SCAN)
        full = size >= start + MAX_SCAN
        if end <= start:
            return None, full
        stream.seek(start)
        raw = stream.read(end - start)
    cut = raw.rfind(b"\n")
    return (start + cut + 1 if cut >= 0 else None), full


def observe(attempt_ref, policy_ref, interpreter, wire_dir, snapshot_end, *, timeout=OBSERVER_TIMEOUT_SECONDS):
    """One read-only observation of the journal's latest original attempt at one explicit
    complete-record snapshot endpoint; raises ContractError otherwise."""
    release = pinned_release(policy_ref)
    interpreter = checked_path(interpreter)
    require(os.access(interpreter, os.X_OK), "observer interpreter is not executable")
    journal = checked_path(Path(attempt_ref["path"]).parent)
    refs = [file_ref(path) for path in sorted(journal.glob("attempt-*.json"))]
    require(refs and refs[-1] == dict(attempt_ref), "only the journal's latest original attempt can be observed")
    attempts = original_attempts(refs)
    for prior in attempts[:-1]:
        # As verify_delivery: an earlier attempt must be proven input-free and identically bound.
        require(prior.get("phase") == "NO_INPUT" and prior.get("events") == [], "an earlier attempt may have input")
        require(prior.get("binding") == attempts[-1].get("binding"), "attempt binding changed")
    attempt = attempts[-1]
    binding = attempt.get("native_binding")
    require(isinstance(binding, dict) and binding.get("schema") == "native-delivery-v1"
            and binding.get("provider") == "claude", "an original Claude native binding is required")
    receipt = None
    if os.path.lexists(journal / "receipt.json"):
        receipt = file_ref(journal / "receipt.json")
        require(json_ref(receipt, "delivery receipt").get("confirmed") is not True,
                "a confirmed native receipt keeps its native evidence")
    _, transcript = _fence(attempt)
    require(transcript.get("path") == (binding.get("transcript") or {}).get("path"),
            "fence and binding name different transcripts")
    # Pin the exact original bytes before the observer runs, and refuse a
    # task-wide or mid-record endpoint here rather than inheriting a later EOF.
    snapshot = _snapshot(transcript, snapshot_end)
    marker, body = binding.get("receipt_marker"), attempt.get("body_reference")
    require(isinstance(marker, str) and MARKER.fullmatch(marker), "receipt marker is not a plain token")
    require(isinstance(body, dict) and set(body) == {"format", "path", "sha256", "bytes"}
            and body["format"] == SINGLE_LINE_FORMAT, "a MESSAGE_REFERENCE_V2 body reference is required")
    # The exact original wire is a pure function of the pinned marker and body; its sha binds it.
    wire = _wire(marker, body)
    require(digest(wire.encode()) == binding.get("payload_sha256"), "recomputed wire differs from the original payload")
    wire_ref = write_bytes_once(checked_path(wire_dir) / f"context-wire-{marker}.txt", wire.encode())
    # -B -E are the observer's own preconditions; it imports its closure from its own scripts directory.
    argv = [str(interpreter), "-BE", release["observer"]["path"], "--attempt", attempt_ref["path"],
            "--attempt-sha256", attempt_ref["sha256"], "--text-file", wire_ref["path"],
            "--snapshot-end", str(snapshot["end"])]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=release["source"])
    except subprocess.TimeoutExpired as exc:
        raise ContractError("context observer timed out") from exc
    require(done.returncode in (0, 2), f"context observer failed: rc={done.returncode}")
    value = strict_json(done.stdout, "context observation")
    require(isinstance(value, dict) and value.get("schema") == OBSERVATION_SCHEMA, "unexpected context observer output")
    require(all(value.get(k) is False for k in NEVER[:4]) and value.get("input_operations") == 0,
            "context observer claimed more than an observation")
    if value.get("observation") != EVIDENCE_CLASS:
        require(done.returncode == 2 and value.get("observation") == "UNVERIFIED", "unexpected context observer verdict")
        raise ContractError("context not consumed: " + str(value.get("reason")))
    require(done.returncode == 0, "consumption verdict without a successful exit")
    require(all(value.get(k) is False for k in NEVER), "context observer claimed more than an observation")
    slices = value.get("proof_slices")
    require(value.get("body_full_read") is True
            and value.get("marker") == marker and value.get("wire_sha256") == binding["payload_sha256"]
            and value.get("body") == body and value.get("session_id") == binding.get("session_id")
            and value.get("attempt") == dict(path=attempt_ref["path"], sha256=attempt_ref["sha256"])
            and value.get("historical_receipt_contract") == binding.get("receipt_contract")
            and isinstance(slices, list) and slices
            and all(isinstance(s, dict) and s.get("path") == transcript["path"] for s in slices),
            "context observation does not bind this exact attempt, wire, full body and session")
    require(value.get("snapshot_end") == snapshot["end"] and value.get("snapshot_sha256") == snapshot["sha256"],
            "context observation is not of the pinned original snapshot")
    require(all(type(s.get("offset")) is int and type(s.get("bytes")) is int
                and snapshot["start"] <= s["offset"] and s["offset"] + s["bytes"] <= snapshot["end"] for s in slices),
            "context proof slice lies outside the pinned snapshot")
    return dict(schema=RESULT_SCHEMA, evidence_class=EVIDENCE_CLASS, attempt=dict(attempt_ref), receipt=receipt,
                snapshot=snapshot, wire=wire_ref, release=release, interpreter=str(interpreter), argv=argv,
                observation=value)


def verify(result_ref, policy_sha256, interpreter, *, timeout=OBSERVER_TIMEOUT_SECONDS):
    """Reproduce one recorded observation at its own pinned endpoint, never a later EOF.

    Journal facts that are not observer inputs do not retract the proof: an
    unconfirmed receipt that later appears, changes or goes away is no evidence
    either way. A changed or superseded original attempt, and a native receipt
    confirmed since (the notice then keeps its native evidence), are refused by
    name (1A38 O1).
    """
    recorded = json_ref(result_ref, "context observation")
    require(recorded.get("schema") == RESULT_SCHEMA and recorded.get("evidence_class") == EVIDENCE_CLASS,
            "not a recorded context observation")
    release, snapshot, wire = recorded.get("release"), recorded.get("snapshot"), recorded.get("wire")
    attempt, receipt = recorded.get("attempt"), recorded.get("receipt")
    require(isinstance(release, dict) and isinstance(release.get("policy"), dict)
            and release["policy"].get("sha256") == policy_sha256, "observation is bound to another policy")
    require(recorded.get("interpreter") == str(checked_path(interpreter)), "observation used another interpreter")
    require(isinstance(snapshot, dict) and isinstance(wire, dict) and isinstance(wire.get("path"), str),
            "observation has no pinned snapshot")
    require(isinstance(attempt, dict) and isinstance(attempt.get("path"), str), "observation has no pinned attempt")
    require(receipt is None or (isinstance(receipt, dict) and set(receipt) == {"path", "sha256", "bytes"}),
            "recorded receipt is not a pinned file reference")
    # The recorded wire must already exist as pinned, so re-observing never writes a new file anywhere.
    read_ref(wire, "recorded wire")
    refs = [file_ref(path) for path in sorted(checked_path(Path(attempt["path"]).parent).glob("attempt-*.json"))]
    require(dict(attempt) in refs, "the original attempt changed or was removed after this observation")
    require(refs[-1] == dict(attempt), "the journal gained a later attempt after this observation")
    again = observe(attempt, release["policy"], interpreter, Path(wire["path"]).parent,
                    snapshot.get("end"), timeout=timeout)
    require(json.loads(json.dumps(dict(again, receipt=None), ensure_ascii=False)) == dict(recorded, receipt=None),
            "observation does not reproduce at its pinned endpoint")
    return recorded


def observe_window(attempt_ref, policy_ref, interpreter, evidence_dir, *, deadline_epoch, interval=5.0,
                   max_observations=120, timeout=OBSERVER_TIMEOUT_SECONDS, now=time.time, sleep=time.sleep):
    """Bounded provisional observation of one original attempt; read-only and input-free.

    Each observer run at a new complete-record endpoint is preserved as its own
    increment. A not-yet-consumed verdict stays provisional and is re-observed
    only after the original transcript grows; the window terminalizes once on a
    complete proof, on any other refusal, or at the real deadline. The deadline
    is checked between observations, so the last one may end up to the observer
    timeout after it. Nothing here sends input, re-pastes, presses a key or
    retries the original notice.
    """
    started = now()
    low, high = WINDOW_LIMITS["interval"]
    require(isinstance(interval, (int, float)) and low <= interval <= high, "observation interval out of bounds")
    low, high = WINDOW_LIMITS["observations"]
    require(type(max_observations) is int and low <= max_observations <= high, "observation budget out of bounds")
    require(isinstance(deadline_epoch, (int, float)) and started < deadline_epoch <= started + WINDOW_LIMITS["horizon"],
            "observation deadline must be in the next hour")
    window = checked_path(evidence_dir)
    _, transcript = _fence(json_ref(attempt_ref, "dispatch attempt"))
    window.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation: one window per directory, and a finished window is never reopened.
        os.mkdir(window, 0o700)
    except FileExistsError as exc:
        raise ContractError("observation window already used; never reopened") from exc
    increments, last, reason = [], None, None
    while True:
        try:
            end, full = _boundary(transcript)
        except ERRORS as exc:
            return _finish(window, attempt_ref, deadline_epoch, interval, increments, None, "refused: " + str(exc))
        if end is not None and end != last:
            last, result, reason = end, None, None
            try:
                result = observe(attempt_ref, policy_ref, interpreter, window, end, timeout=timeout)
            except ERRORS as exc:
                result, reason = None, str(exc)
            increments.append(_increment(window, attempt_ref, transcript, end, result, reason, increments, now()))
            if result is not None:
                return _finish(window, attempt_ref, deadline_epoch, interval, increments, result, None)
            if not reason.startswith("context not consumed: "):
                return _finish(window, attempt_ref, deadline_epoch, interval, increments, None, "refused: " + reason)
            if len(increments) >= max_observations:
                return _finish(window, attempt_ref, deadline_epoch, interval, increments, None,
                               "observation budget exhausted; last: " + reason)
        if full:
            return _finish(window, attempt_ref, deadline_epoch, interval, increments, None,
                           "bounded original window exhausted; last: " + str(reason))
        remaining = deadline_epoch - now()
        if remaining <= 0:
            return _finish(window, attempt_ref, deadline_epoch, interval, increments, None,
                           "deadline reached; last: " + str(reason))
        sleep(min(interval, remaining))


def _increment(window, attempt_ref, transcript, end, result, reason, increments, at):
    """One read-only observation, its endpoint and its reason, written once and hash-chained."""
    try:
        sha = result["snapshot"]["sha256"] if result else _snapshot(transcript, end)["sha256"]
    except ERRORS:
        sha = None
    previous = increments[-1]["sha256"] if increments else None
    value = dict(schema=INCREMENT_SCHEMA, seq=len(increments) + 1, observed_at_epoch=at, attempt=dict(attempt_ref),
                 snapshot_end=end, snapshot_sha256=sha, observation=EVIDENCE_CLASS if result else "UNVERIFIED",
                 reason=reason, provisional=result is None, previous_sha256=previous,
                 native_user_received=False, input_operations=0)
    return write_bytes_once(window / f"observation-{len(increments) + 1:04d}.json", canonical(value))


def _finish(window, attempt_ref, deadline_epoch, interval, increments, result, reason):
    """Terminalize the window exactly once; a consumed result is kept as its own file for verify."""
    result_ref = write_bytes_once(window / "result.json", canonical(result)) if result else None
    value = dict(schema=WINDOW_SCHEMA, attempt=dict(attempt_ref), deadline_epoch=deadline_epoch, interval=interval,
                 increments=increments, evidence_class=EVIDENCE_CLASS if result else None,
                 observation=EVIDENCE_CLASS if result else "UNVERIFIED", reason=reason, result=result_ref,
                 snapshot_end=result["snapshot"]["end"] if result else None,
                 snapshot_sha256=result["snapshot"]["sha256"] if result else None,
                 native_user_received=False, input_operations=0, finished_at=utc_now())
    write_bytes_once(window / "terminal.json", canonical(value))
    return value


def _records(transcript, start, end):
    """Complete native records of the pinned transcript in [start, end), each with its own pin."""
    require(transcript["offset"] <= start < end and end - transcript["offset"] <= MAX_SCAN,
            "record range outside the bounded original window")
    with _open_pinned(transcript) as stream:
        require(os.fstat(stream.fileno()).st_size >= end, "record range is past the original file")
        if start:
            stream.seek(start - 1)
            require(stream.read(1) == b"\n", "record range starts inside a record")
        stream.seek(start)
        raw = stream.read(end - start)
    require(len(raw) == end - start and raw.endswith(b"\n"), "record range does not end at a record boundary")
    offset = start
    for line in raw.split(b"\n")[:-1]:
        record = strict_json(line, "native record")
        require(isinstance(record, dict), "native record must be an object")
        yield dict(offset=offset, length=len(line) + 1, sha256=digest(line + b"\n")), record
        offset += len(line) + 1


def _is_ack(record, line, session):
    """The exact native assistant ACK: its sole text block, optionally beside reasoning blocks."""
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content")
    if (record.get("type") != "assistant" or record.get("sessionId") != session or message.get("role") != "assistant"
            or record.get("isSidechain", False) is not False or record.get("isMeta", False) is not False
            or record.get("isApiErrorMessage", False) is not False):
        return False
    if content == line:
        return True
    if not isinstance(content, list) or not all(isinstance(block, dict) for block in content):
        return False
    texts = [block for block in content if block.get("type") == "text"]
    return (len(texts) == 1 and texts[0].get("text") == line
            and all(block.get("type") in ("text", "thinking", "redacted_thinking") for block in content))


def ack_order(result_ref, policy_sha256, interpreter, handshake_ref, ack_end, *, observed_ack=None,
              timeout=OBSERVER_TIMEOUT_SECONDS):
    """Whether the exact handshake ACK followed full body consumption, in one transcript (ROOT R53/R54).

    Both offsets are positions in the same observed Claude transcript (one
    path/device/inode). A Codex receiver offset belongs to another file and is
    never compared with them. The ACK is joined to its original handshake by
    payload and nonce, must be the only exact ACK between the original fence and
    the explicit ACK endpoint and may be no earlier than the response that
    consumed the full body. The observation is first reproduced at its own
    pinned endpoint.
    """
    result = verify(result_ref, policy_sha256, interpreter, timeout=timeout)
    attempt = json_ref(result["attempt"], "dispatch attempt")
    binding = attempt["native_binding"]
    _, transcript = _fence(attempt)
    handshake = json_ref(handshake_ref, "handshake receipt")
    line, nonce = handshake.get("ack_line_expected"), handshake.get("ack_nonce")
    proof = handshake.get("handshake_precondition") if isinstance(handshake.get("handshake_precondition"), dict) else {}
    require(handshake.get("executor_provider") == "claude" and isinstance(nonce, str)
            and re.fullmatch(r"[0-9a-f]{8,64}", nonce) and isinstance(line, str) and ACK_LINE.fullmatch(line)
            and line.startswith("PREFLIGHT_ACK|") and line.endswith("|" + nonce)
            and proof.get("payload_sha256") == binding["payload_sha256"]
            and handshake.get("marker", binding["receipt_marker"]) == binding["receipt_marker"],
            "handshake receipt is not this notice's original ACK contract")
    slices = result["observation"]["proof_slices"]
    consumed_at = max(s["offset"] for s in slices)
    require(type(ack_end) is int, "explicit ACK endpoint required")
    found = [(ref, record) for ref, record in _records(transcript, transcript["offset"], ack_end)
             if _is_ack(record, line, binding["session_id"])]
    require(found, "exact ACK not in the pinned ACK range")
    require(len(found) == 1, "more than one exact ACK after the original fence")
    ref = found[0][0]
    require(ref["offset"] >= consumed_at, "ACK precedes full body consumption")
    ack = dict(path=transcript["path"], device=transcript["device"], inode=transcript["inode"], **ref)
    if observed_ack is not None:
        claimed = json_ref(observed_ack, "observed ACK")
        require(all(claimed.get(k) == ack[k] for k in ("path", "offset", "length", "sha256"))
                and claimed.get("ack_line", line) == line,
                "observed ACK is not this exact record of the observed transcript")
    return dict(schema=ACK_SCHEMA, ordering="ACK_AFTER_FULL_BODY_CONSUMPTION", coordinate_system="observed_claude_transcript",
                transcript=dict(path=transcript["path"], device=transcript["device"], inode=transcript["inode"]),
                body_consumed_at_offset=consumed_at, ack=ack, ack_end=ack_end, ack_line_sha256=digest(line.encode()),
                handshake=dict(handshake_ref), observation=dict(result_ref),
                observed_ack=dict(observed_ack) if observed_ack is not None else None,
                receiver_offsets_compared=False, native_user_received=False, input_operations=0)


def _pinned(path, sha256, label):
    ref = file_ref(checked_path(path))
    require(ref["sha256"] == sha256, f"{label} differs from its pin")
    return ref


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("observe", help="observe one original attempt at one explicit snapshot endpoint")
    window = commands.add_parser("observe-window", help="bounded provisional observation; increments, then one terminal")
    for sub in (one, window):
        for name in ("--attempt", "--attempt-sha256", "--policy", "--policy-sha256", "--interpreter"):
            sub.add_argument(name, required=True)
    one.add_argument("--wire-dir", required=True)
    one.add_argument("--snapshot-end", type=int, required=True)
    window.add_argument("--window-dir", required=True)
    window.add_argument("--deadline-epoch", type=float, required=True)
    window.add_argument("--interval", type=float, default=5.0)
    window.add_argument("--max-observations", type=int, default=120)
    again = commands.add_parser("verify", help="reproduce one recorded observation at its own pinned endpoint")
    order = commands.add_parser("ack-order", help="exact ACK after full body consumption, same transcript only")
    for sub in (again, order):
        for name in ("--result", "--result-sha256", "--policy-sha256", "--interpreter"):
            sub.add_argument(name, required=True)
    order.add_argument("--handshake-receipt", required=True)
    order.add_argument("--handshake-receipt-sha256", required=True)
    order.add_argument("--ack-end", type=int, required=True)
    order.add_argument("--observed-ack")
    order.add_argument("--observed-ack-sha256")
    args = parser.parse_args(argv)
    schema = {"observe": RESULT_SCHEMA, "verify": RESULT_SCHEMA, "observe-window": WINDOW_SCHEMA,
              "ack-order": ACK_SCHEMA}[args.command]
    try:
        if args.command in ("observe", "observe-window"):
            attempt = _pinned(args.attempt, args.attempt_sha256, "attempt")
            policy = _pinned(args.policy, args.policy_sha256, "policy")
        else:
            recorded = _pinned(args.result, args.result_sha256, "observation")
        if args.command == "observe":
            result = observe(attempt, policy, args.interpreter, args.wire_dir, args.snapshot_end)
        elif args.command == "observe-window":
            result = observe_window(attempt, policy, args.interpreter, args.window_dir,
                                    deadline_epoch=args.deadline_epoch, interval=args.interval,
                                    max_observations=args.max_observations)
        elif args.command == "verify":
            result = verify(recorded, args.policy_sha256, args.interpreter)
        else:
            require((args.observed_ack is None) == (args.observed_ack_sha256 is None),
                    "observed ACK and its pin go together")
            observed = (_pinned(args.observed_ack, args.observed_ack_sha256, "observed ACK")
                        if args.observed_ack is not None else None)
            result = ack_order(recorded, args.policy_sha256, args.interpreter,
                               _pinned(args.handshake_receipt, args.handshake_receipt_sha256, "handshake receipt"),
                               args.ack_end, observed_ack=observed)
    except ERRORS as exc:
        print(json.dumps(dict(schema=schema, evidence_class=None, observation="UNVERIFIED", reason=str(exc),
                              native_user_received=False, input_operations=0), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if args.command == "observe-window" and result["evidence_class"] != EVIDENCE_CLASS else 0


if __name__ == "__main__":
    raise SystemExit(main())
