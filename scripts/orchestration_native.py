"""Read original CLI journals. Never sends input or adopts copied transcripts.

Fixture readers are explicitly offline; the campaign promotion entrypoint never
enables them. Native files may append, so pins bind identity and byte ranges,
not the changing hash of the entire transcript.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import uuid
from pathlib import Path

from orchestration_common import ContractError, checked_path, contract_errors, count, digest, finite, json_ref, parse_time, strict_json

MAX_RECORD = 16 * 1024 * 1024


def task_work(arguments, output_root):
    """A completed call touching task output, excluding transport/control work.

    This is an observable first-action definition, not a correctness claim.
    Mentioning a task id, preflight or callback never counts as useful work.
    """
    if not isinstance(arguments, str):
        return False
    controls = ("orchestration_run.py", "mac_harness.py", "cmux_bridge", "cmux-agent",
                "submit_completion_callback", "submit_task_pack", "ORCH_ACCEPT_V2|")
    if any(token in arguments for token in controls):
        return False
    return bool(re.search(re.escape(output_root.rstrip("/")) + r'(?=/|[\s"\x27\\]|$)', arguments))


def tool_failed(value):
    """Recognize explicit failures in native and nested exec result envelopes."""
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("is_error") is True:
            return True
        if "exit_code" in value and value["exit_code"] not in (0, None):
            return True
        if value.get("status") == "rejected":
            return True
        return any(tool_failed(value[k]) for k in ("content", "output", "value", "text") if k in value)
    if isinstance(value, list):
        return any(tool_failed(item) for item in value)
    if isinstance(value, str):
        if value.startswith(("Error:", "Error ", "Traceback (most recent call last)")):
            return True
        if re.search(r"(?:Process exited with code|exit_code[\"']?\s*:)\s*-?[1-9][0-9]*", value):
            return True
        try:
            decoded = strict_json(value)
        except (ContractError, ValueError):
            return False
        if not isinstance(decoded, str):
            return tool_failed(decoded)
    return False


def tool_pending(value):
    """A yielded/background result is not a terminal tool result."""
    if isinstance(value, dict):
        if value.get("status") in ("running", "in_progress", "pending"):
            return True
        if (value.get("session_id") is not None or value.get("cell_id") is not None) and value.get("exit_code") is None:
            return True
        return any(tool_pending(value[k]) for k in ("content", "output", "value", "text") if k in value)
    if isinstance(value, list):
        return any(tool_pending(item) for item in value)
    if isinstance(value, str):
        if re.search(r"Script running with cell ID|Process running with session ID|Command running in background", value):
            return True
        try:
            decoded = strict_json(value)
        except (ContractError, ValueError):
            return False
        return not isinstance(decoded, str) and tool_pending(decoded)
    return False


def require(condition, reason):
    if not condition:
        raise ContractError(reason)


def native_text(record, provider, role):
    if not isinstance(record, dict) or provider not in ("claude", "codex"):
        return None
    if provider == "codex":
        message = record.get("payload", {})
        if not isinstance(message, dict) or record.get("type") != "response_item" or message.get("type") != "message":
            return None
    else:
        if record.get("type") != role or record.get("isSidechain") is True:
            return None
        message = record.get("message", {})
    if not isinstance(message, dict) or message.get("role") != role:
        return None
    content = message.get("content")
    if isinstance(content, str) and provider == "claude":
        return content
    if not isinstance(content, list):
        return None
    kind = ("input_text" if role == "user" else "output_text") if provider == "codex" else "text"
    texts = []
    for block in content:
        if not isinstance(block, dict):
            return None
        if block.get("type") == kind and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif role == "assistant" and block.get("type") in ("thinking", "redacted_thinking"):
            # Ignore reasoning contents, never expose them as receipt evidence.
            if not isinstance(block.get("thinking", block.get("data")), str):
                return None
        else:
            return None
    return texts[0] if len(texts) == 1 else None


class NativeReader:
    @contract_errors("native session")
    def __init__(self, session, *, fixtures=False):
        self.session = session
        self.provider = session["provider"]
        require(self.provider in ("codex", "claude"), "unsupported native provider")
        self.pin = session["transcript"]
        for name in ("device", "inode"):
            count(self.pin.get(name), "native " + name)
        require(isinstance(session.get("session_id"), str), "native session id required")
        try:
            require(str(uuid.UUID(session["session_id"])) == session["session_id"], "noncanonical session id")
        except ValueError as exc:
            raise ContractError("invalid native session id") from exc
        self.path = checked_path(self.pin["path"])
        require(self.path.name.endswith(session["session_id"] + ".jsonl"), "native session filename mismatch")
        if not fixtures:
            root = Path.home() / (".codex/sessions" if self.provider == "codex" else ".claude/projects")
            require(root.resolve() in self.path.parents, "copied/non-native transcript cannot prove a CLI run")
        with self.open() as stream:
            found = False
            for _ in range(64):
                line = stream.readline(MAX_RECORD + 1)
                if not line or len(line) > MAX_RECORD:
                    break
                try:
                    record = strict_json(line, "native metadata")
                except (ValueError, UnicodeError):
                    continue
                require(isinstance(record, dict), "native metadata must be an object")
                if self.provider == "codex" and record.get("type") == "session_meta":
                    meta = record.get("payload", {})
                    require(meta.get("id", meta.get("session_id")) == session["session_id"], "native metadata session mismatch")
                    found = True
                    break
                if self.provider == "claude" and record.get("sessionId"):
                    require(record["sessionId"] == session["session_id"], "native metadata session mismatch")
                    found = True
                    break
            require(found, "original native session metadata missing")

    def open(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, "rb")
        st = os.fstat(fd)
        try:
            require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid(), "native file owner/type mismatch")
            require((st.st_dev, st.st_ino) == (self.pin["device"], self.pin["inode"]), "original native transcript replaced")
        except Exception:
            stream.close()
            raise
        return stream

    def snapshot(self):
        with self.open() as stream:
            st = os.fstat(stream.fileno())
            offset = st.st_size
            head_size, tail_at = min(offset, 4096), max(0, offset - 4096)
            prefix = stream.read(head_size)
            stream.seek(tail_at)
            tail = stream.read(offset - tail_at)
        require(not tail or tail.endswith(b"\n"), "native EOF is not a record boundary; observe later")
        return dict(path=str(self.path), device=st.st_dev, inode=st.st_ino, offset=offset,
                    prefix_size=head_size, prefix_sha256=digest(prefix), tail_at=tail_at,
                    tail_sha256=digest(tail), at_record_boundary=True)

    def verify_pin(self, pin):
        require(isinstance(pin, dict), "native EOF pin required")
        require(all(pin.get(k) == self.pin[k] for k in ("path", "device", "inode")), "native EOF identity mismatch")
        offset = count(pin.get("offset"), "native offset")
        for name in ("device", "inode", "prefix_size", "tail_at"):
            count(pin.get(name), "native EOF " + name)
        require(pin.get("prefix_size") == min(offset, 4096) and pin.get("tail_at") == max(0, offset - 4096), "native EOF range invalid")
        require(pin.get("at_record_boundary") is True, "native EOF boundary missing")
        with self.open() as stream:
            require(os.fstat(stream.fileno()).st_size >= offset, "native journal truncated")
            require(digest(stream.read(pin["prefix_size"])) == pin.get("prefix_sha256"), "native prefix changed")
            stream.seek(pin["tail_at"])
            tail = stream.read(offset - pin["tail_at"])
            require(digest(tail) == pin.get("tail_sha256") and (not tail or tail.endswith(b"\n")), "native EOF fence changed")
        return offset

    def records(self, start, end):
        start, end = count(start, "start offset"), count(end, "end offset")
        require(start <= end and end - start <= 64 * 1024 * 1024, "native observation range invalid/too large")
        with self.open() as stream:
            require(os.fstat(stream.fileno()).st_size >= end, "native range truncated")
            if start:
                stream.seek(start - 1)
                require(stream.read(1) == b"\n", "native range starts inside a record")
            stream.seek(start)
            while stream.tell() < end:
                offset = stream.tell()
                raw = stream.readline(min(MAX_RECORD + 1, end - offset))
                require(raw.endswith(b"\n") and len(raw) <= MAX_RECORD, "partial/oversized native record")
                try:
                    record = strict_json(raw, "native record")
                except (ValueError, UnicodeError) as exc:
                    raise ContractError("invalid original native JSON record") from exc
                require(isinstance(record, dict), "native record must be an object")
                if self.provider == "claude" and record.get("sessionId"):
                    require(record["sessionId"] == self.session["session_id"], "record belongs to another Claude session")
                yield dict(path=str(self.path), device=self.pin["device"], inode=self.pin["inode"],
                           offset=offset, length=len(raw), sha256=digest(raw)), record

    def record(self, ref):
        require(isinstance(ref, dict) and all(ref.get(k) == self.pin[k] for k in ("path", "device", "inode")), "native record identity mismatch")
        length = count(ref.get("length"), "record length")
        offset = count(ref.get("offset"), "record offset")
        for name in ("device", "inode"):
            count(ref.get(name), "record " + name)
        require(0 < length <= MAX_RECORD, "native record length invalid")
        records = list(self.records(offset, offset + length))
        require(len(records) == 1 and records[0][0]["sha256"] == ref.get("sha256"), "native record hash/length mismatch")
        return records[0][1]

    @contract_errors("native actions")
    def action_summary(self, start, end, *, task_id, output_root, allow_empty=False):
        """Derive useful action, model, retries and interventions from raw records."""
        model_seen, failed_api, explicit_retries = set(), 0, set()
        tool_calls, completed_calls = {}, {}
        user_texts = []
        prior = None
        for _, r in self.records(0, start):
            if self.provider == "codex" and r.get("type") == "turn_context":
                prior = r.get("payload", {}).get("model")
            elif self.provider == "claude" and r.get("type") == "assistant":
                model = r.get("message", {}).get("model")
                if isinstance(model, str) and model != "<synthetic>":
                    prior = model
        if prior:
            model_seen.add(prior)

        def complete(key, value, record, failed=False):
            if key not in tool_calls:
                return
            require(key not in completed_calls, "duplicate native tool result")
            require(not tool_pending(value), "unfinished task tool call: asynchronous result is not completion")
            stamp = parse_time(record["timestamp"])
            require(stamp >= tool_calls[key], "native tool completion precedes its call")
            completed_calls[key] = (stamp, failed or tool_failed(value))

        for ref, r in self.records(start, end):
            p = r.get("payload", {})
            require(isinstance(p, dict), "malformed native payload")
            if self.provider == "codex":
                if r.get("type") == "turn_context" and isinstance(p.get("model"), str):
                    model_seen.add(p["model"])
                if r.get("type") == "response_item" and p.get("type") in ("function_call", "custom_tool_call"):
                    args = p.get("arguments", p.get("input", ""))
                    if task_work(args, output_root) and isinstance(p.get("call_id"), str):
                        require(p["call_id"] not in tool_calls, "duplicate native tool call id")
                        tool_calls[p["call_id"]] = parse_time(r["timestamp"])
                if r.get("type") == "response_item" and p.get("type") in ("function_call_output", "custom_tool_call_output"):
                    output = p.get("output")
                    complete(p.get("call_id"), output, r, p.get("is_error") is True)
                if r.get("type") == "event_msg" and p.get("type") == "error":
                    failed_api += 1
            else:
                message = r.get("message", {})
                require(isinstance(message, dict), "malformed native message")
                noise = r.get("isApiErrorMessage") is True and message.get("model") == "<synthetic>"
                if r.get("type") == "assistant" and isinstance(message.get("model"), str) and not noise:
                    model_seen.add(message["model"])
                if noise:
                    failed_api += 1
                content = message.get("content", [])
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if r.get("type") == "assistant" and block.get("type") == "tool_use":
                        args = json.dumps(block.get("input", {}), ensure_ascii=False)
                        if task_work(args, output_root) and isinstance(block.get("id"), str):
                            require(block["id"] not in tool_calls, "duplicate native tool call id")
                            tool_calls[block["id"]] = parse_time(r["timestamp"])
                    if r.get("type") == "user" and block.get("type") == "tool_result":
                        complete(block.get("tool_use_id"), block.get("content"), r, block.get("is_error") is True)
                if r.get("type") == "system" and r.get("subtype") == "api_error":
                    failed_api += 1
                    retry = r.get("retryAttempt")
                    if type(retry) is int and retry > 0:
                        explicit_retries.add((r.get("uuid", ref["offset"]), retry))
            text = native_text(r, self.provider, "user")
            if text is not None:
                user_texts.append(text)
        require(model_seen == {self.session["model"]}, "model missing or changed during replay")
        require(set(tool_calls) == set(completed_calls), "unfinished task tool call at native boundary")
        times = [t for key, t in tool_calls.items() if not completed_calls[key][1]]
        require(times or allow_empty, "no completed task-bound native CLI tool action")
        return dict(first_action_at=min(times).isoformat() if times else None,
                    last_action_at=max(t for t, _ in completed_calls.values()).isoformat() if completed_calls else None,
                    failed_api_attempts=failed_api,
                    provider_auto_retries=len(explicit_retries) if explicit_retries or failed_api == 0 else None,
                    user_inputs=user_texts,
                    attempted_task_calls=len(tool_calls), failed_task_calls=len(tool_calls) - len(times),
                    completed_task_calls=len(times), models=sorted(model_seen))


def original_attempts(refs):
    """Open the complete contiguous original journal, including failed attempts."""
    require(isinstance(refs, list) and refs, "original dispatch attempts missing")
    parent = Path(refs[0]["path"]).parent
    require([Path(r["path"]).name for r in refs] == [f"attempt-{i:04d}.json" for i in range(1, len(refs) + 1)], "attempt history is not contiguous")
    require(all(Path(r["path"]).parent == parent for r in refs), "attempts from different journals")
    require(sorted(p.name for p in parent.glob("attempt-*.json")) == [Path(r["path"]).name for r in refs], "attempt history omitted or changed")
    return [json_ref(ref, "dispatch attempt") for ref in refs]


def delivery_identity(receiver, sender):
    return dict(workspace_uuid=receiver["workspace_uuid"], caller_surface_uuid=sender["surface_uuid"],
                target_surface_uuid=receiver["surface_uuid"], target_pane_uuid=receiver["pane_uuid"])


def check_native_binding(binding, receiver, sender, text, *, fixtures=False):
    require(binding.get("schema") == "native-delivery-v1", "original native binding required")
    require(binding.get("provider") == receiver["provider"] and binding.get("session_id") == receiver["session_id"],
            "receiver session mismatch")
    require(binding.get("process") == receiver["process"] and binding.get("tty") == receiver["tty"],
            "receiver process/birth/TTY mismatch")
    require(binding.get("identity") == delivery_identity(receiver, sender), "native workspace/surface/pane binding mismatch")
    require(binding.get("payload_sha256") == digest(text.encode()), "payload hash changed")
    reader = NativeReader(receiver, fixtures=fixtures)
    return reader.verify_pin(binding["transcript"])


@contract_errors("zero-input history")
def verify_no_input(refs, receiver, sender, text, expected_binding, *, not_before, not_after, fixtures=False):
    """Only a completed, positively zero-input task operation may release a lane.

    Ordinary message journals lack these operation clocks. They deliberately do
    not qualify as a task write-boundary proof.
    """
    attempts = original_attempts(refs)
    previous_end = finite(not_before, "operation intent time")
    last_offset = 0
    for attempt in attempts:
        require(attempt.get("phase") == "NO_INPUT" and attempt.get("events") == [],
                "uncertain input cannot release a lease")
        require(attempt.get("binding") == expected_binding, "zero-input task/pack/notice/endpoint binding changed")
        started = finite(attempt.get("started_at_epoch"), "attempt start time")
        ended = finite(attempt.get("ended_at_epoch"), "attempt end time")
        b = attempt.get("native_binding", {})
        bound = finite(b.get("bound_at_epoch"), "native binding time")
        require(previous_end <= started <= bound <= ended <= not_after, "zero-input operation chronology invalid")
        offset = check_native_binding(b, receiver, sender, text, fixtures=fixtures)
        require(offset >= last_offset, "zero-input native fence moved backwards")
        require(isinstance(attempt.get("error"), str) and attempt["error"], "zero-input terminal error missing")
        previous_end, last_offset = ended, offset
    return dict(attempts=len(attempts), ended_at_epoch=previous_end, native_offset=last_offset)


@contract_errors("native delivery")
def verify_delivery(bundle, receiver, sender, text, *, fixtures=False):
    """Check the complete original attempt list, fence and exact native input."""
    receipt = json_ref(bundle["receipt"], "delivery receipt")
    refs = bundle["attempts"]
    attempts = original_attempts(refs)
    for prior in attempts[:-1]:
        require(prior.get("phase") == "NO_INPUT" and prior.get("events") == [], "uncertain input was resent")
        require(prior.get("binding") == attempts[-1].get("binding"), "attempt binding changed")
        check_native_binding(prior.get("native_binding", {}), receiver, sender, text, fixtures=fixtures)
    attempt = attempts[-1]
    require(receipt.get("attempt") == refs[-1]["path"] and receipt.get("confirmed") is True, "receipt not bound to original attempt")
    b = attempt.get("native_binding", {})
    require(receipt.get("native_binding") == b and b.get("schema") == "native-delivery-v1", "original native binding required")
    # Task receipts store `binding`; callback receipts flatten the binding fields
    # and retain identity in the original attempt/native binding.
    if "binding" in receipt:
        require(receipt["binding"] == attempt.get("binding"), "receipt binding changed")
    else:
        require(all(receipt.get(k) == v for k, v in attempt.get("binding", {}).items()
                    if k != "identity"), "callback receipt binding changed")
    require(b.get("provider") == receiver["provider"] and b.get("session_id") == receiver["session_id"], "receiver session mismatch")
    require(b.get("process") == receiver["process"] and b.get("tty") == receiver["tty"], "receiver process/birth/TTY mismatch")
    expected_identity = delivery_identity(receiver, sender)
    require(b.get("identity") == expected_identity and attempt.get("binding", {}).get("identity") == expected_identity, "native workspace/surface/pane binding mismatch")
    require(b.get("payload_sha256") == digest(text.encode()), "payload hash changed")
    process = b.get("process", {})
    require(count(process.get("pid"), "receiver pid") > 0, "invalid receiver pid")
    require(isinstance(process.get("birth"), list) and len(process["birth"]) == 2, "process birth missing")
    for item in process["birth"]:
        count(item, "process birth")
    reader = NativeReader(receiver, fixtures=fixtures)
    original_offset = reader.verify_pin(b["transcript"])
    events = attempt.get("events")
    require(isinstance(events, list) and all(isinstance(e, dict) for e in events), "input events missing")
    inputs = [e for e in events if e.get("phase") in ("PASTE_INTENT", "NATIVE_INPUT_INTENT")]
    require(len(inputs) == 1, "one original input intent required")
    event = inputs[0]
    native = event["phase"] == "NATIVE_INPUT_INTENT"
    require(native == (receiver["provider"] == "codex"), "input transport differs from frozen CLI provider")
    fence = event.get("native_input_fence" if native else "native_paste_fence", {})
    require(fence.get("schema") == ("native-input-fence-v1" if native else "native-paste-fence-v1"), "original input-intent EOF fence missing")
    require(fence.get("binding_sha256") == digest(json.dumps(b, sort_keys=True).encode()), "fence not bound to original native binding")
    bound, captured, intent = (finite(b.get("bound_at_epoch"), "bound time"), finite(fence.get("captured_at_epoch"), "fence time"), finite(event.get("at_epoch"), "input time"))
    require(bound <= captured <= intent, "retroactive input fence")
    times = [finite(e.get("at_epoch"), "event time") for e in events]
    require(times == sorted(times) and all(t >= bound for t in times), "input event chronology invalid")
    offset = reader.verify_pin(fence.get("transcript"))
    require(offset >= original_offset, "input EOF predates original binding")
    if native:
        req = event.get("native_request", {})
        params = {"threadId": receiver["session_id"], "input": [{"type": "text", "text": text}]}
        if req.get("method") == "turn/steer":
            turn_id = req.get("params", {}).get("expectedTurnId")
            try:
                require(isinstance(turn_id, str) and str(uuid.UUID(turn_id)) == turn_id, "active turn missing")
            except ValueError as exc:
                raise ContractError("invalid active turn id") from exc
            params["expectedTurnId"] = turn_id
            require(req.get("observed_status") == "active" and req.get("observed_turn_id") == turn_id, "active turn observation changed")
        else:
            require(req.get("method") == "turn/start", "native input method invalid")
            require(req.get("observed_status") == "idle", "idle turn observation missing")
        require(req.get("transport") == "codex-native-input-v1" and req.get("params") == params, "native input request/overrides changed")
        require(fence.get("native_request_sha256") == digest(json.dumps(req, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()), "native request hash mismatch")
        peer = req.get("socket", {})
        for name in ("uid", "peer_uid", "mode", "device", "inode"):
            count(peer.get(name), "native socket " + name)
        require(peer.get("uid") == os.getuid() and peer.get("peer_uid") == os.getuid() and peer.get("mode") == 0o600
                and isinstance(peer.get("path"), str) and Path(peer["path"]).is_absolute(), "native socket identity missing")
        require(bound <= finite(req.get("observed_at_epoch"), "native request time") <= captured, "native request chronology invalid")
        require(not any(e.get("phase") in ("PASTED", "ENTER_INTENT", "ENTER_SENT", "QUEUE_TAB_INTENT", "KEY_SENT") for e in events), "native input used terminal fallback")
    else:
        screen = event.get("screen")
        require(isinstance(screen, str) and event.get("screen_sha256") == digest(screen.encode())[:16], "original paste intent screen changed")
    proof = receipt.get("native_proof", {})
    require(count(proof.get("offset"), "receipt offset") >= offset and proof.get("session_id") == receiver["session_id"], "receipt predates input EOF/session mismatch")
    require(proof.get("payload_sha256") == b["payload_sha256"] and proof.get("reception_kind") == "native_user_message" and receipt.get("confirmation_source") == "native_user_message_v1", "queue/screen/RPC is not native reception")
    record = reader.record(proof)
    require(native_text(record, receiver["provider"], "user") == text, "complete exact native user payload missing")
    matching = [ref for ref, item in reader.records(offset, reader.snapshot()["offset"])
                if native_text(item, receiver["provider"], "user") == text]
    require(len(matching) == 1, "duplicate native delivery of the same original payload")
    stamp = parse_time(record.get("timestamp"))
    # CLI journals truncate to milliseconds; the EOF fence rules out old input.
    require(stamp.timestamp() >= math.floor(intent * 1000) / 1000, "native input precedes original intent")
    return dict(receipt=receipt, attempt=attempt, received_at=stamp.isoformat(), native_proof=proof,
                transport_retries=len(attempts) - 1)
