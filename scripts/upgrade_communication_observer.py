#!/usr/bin/env python3
"""Bounded, file-only communication observation for a pinned live candidate.

This adapter imports no generated controller modules and never runs the reader.
Only the receiver's original native tool call and complete result can attest
body exposure. It never reconciles, sends, launches clients, retries, edits
runtime state or accepts business work. Old observation schemas stay unchanged.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from uuid import UUID

from cmux_prompt_reference import verify, wire_reference
from upgrade_body_read import read_command
from upgrade_body_read_evidence import body_read_evidence, strict_json

META_LIMIT = 2 * 1024 * 1024
SCAN_LIMIT = 8 * 1024 * 1024
RECORD_LIMIT = 8 * 1024 * 1024
TOTAL_NATIVE_LIMIT = 256 * 1024 * 1024
RETAIN_LIMIT = 32 * 1024 * 1024
HEADER_LIMIT = 1024 * 1024
SCHEMA = 'upgrade-communication-observation-v2'


class Uncertain(Exception):
    pass


def need(condition, reason):
    if not condition:
        raise Uncertain(reason)


def sha(raw):
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def stable_id(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode


def raw_file(path, limit=META_LIMIT):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'NONCANONICAL_OR_SYMLINK_PATH')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_size <= limit
             and before.st_uid == os.getuid(), 'METADATA_SIZE_TYPE_OR_OWNER')
        raw = f.read(limit + 1)
        need(stable_id(before) == stable_id(os.fstat(f.fileno())) == stable_id(path.stat()),
             'FILE_CHANGED_DURING_OBSERVATION')
    return raw, dict(path=str(path), bytes=len(raw), sha256=sha(raw),
                     device=before.st_dev, inode=before.st_ino)


def executable_pin(ref):
    """Verify a bounded executable without allocating its whole binary."""
    path = Path(ref['path'])
    need(path.is_absolute() and path.resolve() == path and os.access(path, os.X_OK),
         'EXECUTABLE_PATH_CHANGED')
    need(type(ref['bytes']) is int and 0 < ref['bytes'] <= 256 * 1024 * 1024,
         'EXECUTABLE_SIZE_BUDGET')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_size == ref['bytes'], 'EXECUTABLE_SIZE_CHANGED')
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            block = stream.read(min(65536, remaining))
            need(block, 'EXECUTABLE_TRUNCATED')
            remaining -= len(block)
            digest.update(block)
        need(stable_id(before) == stable_id(os.fstat(stream.fileno())) == stable_id(path.stat())
             and digest.hexdigest() == ref['sha256'], 'EXECUTABLE_PIN_CHANGED')
    return dict(path=str(path), bytes=before.st_size, sha256=digest.hexdigest())


def json_file(path, expected=None):
    raw, pin = raw_file(path)
    if expected:
        need(all(pin[k] == expected[k] for k in ('path', 'bytes', 'sha256')), 'FILE_PIN_CHANGED')
    value = strict_json(raw)
    need(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value, pin


def json_ref(ref):
    return json_file(ref['path'], ref)


def original_event(attempt, binding, payload):
    events = attempt.get('events', [])
    need(isinstance(events, list) and all(isinstance(e, dict) for e in events), 'EVENTS_INVALID')
    inputs = [e for e in events if e.get('phase') in ('PASTE_INTENT', 'NATIVE_INPUT_INTENT')]
    need(len(inputs) == 1, 'ONE_ORIGINAL_INPUT_INTENT_REQUIRED')
    event = inputs[0]
    at = event.get('at_epoch')
    bound = binding.get('bound_at_epoch')
    need(type(at) in (int, float) and type(bound) in (int, float)
         and math.isfinite(at) and math.isfinite(bound) and at >= bound, 'INPUT_TIME_INVALID')
    if event['phase'] == 'PASTE_INTENT':
        screen = event.get('screen')
        need(isinstance(screen, str) and sha(screen)[:16] == event.get('screen_sha256'), 'PASTE_SCREEN_PIN')
        fence = event.get('native_paste_fence', {})
        need(fence.get('schema') == 'native-paste-fence-v1', 'PASTE_FENCE_REQUIRED')
    else:
        request = event.get('native_request', {})
        params = request.get('params', {})
        expected = dict(threadId=binding['session_id'], input=[dict(type='text', text=payload)])
        method = request.get('method')
        if method == 'turn/steer':
            need(isinstance(params.get('expectedTurnId'), str) and params['expectedTurnId'], 'TURN_ID_MISSING')
            expected['expectedTurnId'] = params['expectedTurnId']
        else:
            need(method == 'turn/start', 'UNKNOWN_NATIVE_METHOD')
        need(binding['provider'] == 'codex' and params == expected, 'NATIVE_REQUEST_CHANGED')
        need(not any(e.get('phase') in {'PASTE_INTENT', 'PASTED', 'ENTER_INTENT', 'ENTER_SENT',
                    'QUEUE_TAB_INTENT', 'EXTRA_ENTER_INTENT', 'KEY_INTENT', 'KEY_SENT'} for e in events),
             'MIXED_NATIVE_TERMINAL_TRANSPORT')
        fence = event.get('native_input_fence', {})
        need(fence.get('schema') == 'native-input-fence-v1'
             and fence.get('native_request_sha256') == sha(canonical(request)), 'NATIVE_REQUEST_FENCE_CHANGED')
    need(fence.get('binding_sha256') == sha(json.dumps(binding, sort_keys=True)), 'FENCE_BINDING_CHANGED')
    captured = fence.get('captured_at_epoch')
    need(type(captured) in (int, float) and math.isfinite(captured) and bound <= captured <= at,
         'FENCE_TIME_INVALID')
    old, fresh = binding['transcript'], fence['transcript']
    need(all(old[k] == fresh[k] for k in ('path', 'device', 'inode')) and fresh['offset'] >= old['offset'],
         'FENCE_TRANSCRIPT_CHANGED')
    return event, fresh


def check_prefix(f, pin, path):
    held, current = os.fstat(f.fileno()), path.stat()
    need(type(pin['offset']) is int and pin['offset'] >= 0, 'NATIVE_OFFSET_INVALID')
    need(stat.S_ISREG(held.st_mode) and held.st_uid == os.getuid()
         and (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino) == (pin['device'], pin['inode'])
         and held.st_size >= pin['offset'], 'NATIVE_FILE_IDENTITY_CHANGED')
    need(type(pin['offset']) is int and pin['offset'] >= 0
         and pin['prefix_size'] == min(pin['offset'], 4096)
         and pin['tail_at'] == max(0, pin['offset'] - 4096)
         and type(pin['at_record_boundary']) is bool, 'NATIVE_PREFIX_SHAPE_INVALID')
    f.seek(0)
    need(sha(f.read(pin['prefix_size'])) == pin['prefix_sha256'], 'NATIVE_PREFIX_CHANGED')
    f.seek(pin['tail_at'])
    need(sha(f.read(pin['offset'] - pin['tail_at'])) == pin['tail_sha256'], 'NATIVE_FENCE_TAIL_CHANGED')
    return held.st_size


def relevant(record, provider):
    """Retain complete call/result wrappers, including Codex nested command events."""
    if provider == 'codex':
        payload = record.get('payload', {})
        return (record.get('type') == 'session_meta' or
                record.get('type') == 'event_msg' or
                record.get('type') == 'response_item' and isinstance(payload, dict)
                and payload.get('type') in ('function_call', 'custom_tool_call',
                    'function_call_output', 'custom_tool_call_output'))
    return record.get('type') in ('assistant', 'user')


def inbound(record, binding, payload, at):
    stamp = record.get('timestamp')
    if not isinstance(stamp, str):
        return None
    try:
        date = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return None
    if date.tzinfo is None:
        return None
    earliest = max(binding['bound_at_epoch'], at)
    precision = re.search(r'\.(\d{3,6})(?:Z|[+-]\d{2}:\d{2})$', stamp)
    if precision:
        scale = 10 ** len(precision[1])
        earliest = math.floor(earliest * scale) / scale
    if not earliest <= date.timestamp() <= time.time() + 2:
        return None
    if binding['provider'] == 'codex':
        p = record.get('payload', {})
        if (record.get('type') == 'response_item' and isinstance(p, dict)
                and p.get('type') == 'message' and p.get('role') == 'user'
                and p.get('content') == [dict(type='input_text', text=payload)]):
            return 'NATIVE_NOTICE_OBSERVED'
        return None
    if record.get('sessionId') != binding['session_id'] or record.get('isSidechain') is True:
        return None
    p = record.get('message', {})
    if (record.get('type') == 'user' and isinstance(p, dict) and p.get('role') == 'user'
            and p.get('content') in (payload, [dict(type='text', text=payload)])):
        return 'NATIVE_NOTICE_OBSERVED'
    p = record.get('attachment', {})
    if (record.get('type') == 'attachment' and isinstance(p, dict)
            and p.get('type') == 'queued_command' and p.get('prompt') == payload):
        return 'QUEUED_OBSERVED'
    return None


class Observer:
    def __init__(self, *, scan_limit=SCAN_LIMIT, record_limit=RECORD_LIMIT,
                 total_native_limit=TOTAL_NATIVE_LIMIT, retain_limit=RETAIN_LIMIT):
        for value, maximum in ((scan_limit, 64 * 1024 * 1024),
                               (record_limit, 16 * 1024 * 1024),
                               (total_native_limit, 1024 * 1024 * 1024),
                               (retain_limit, 64 * 1024 * 1024)):
            need(type(value) is int and 1 <= value <= maximum, 'OBSERVER_BUDGET_INVALID')
        self.scan_limit, self.record_limit = scan_limit, record_limit
        self.total_native_limit, self.retain_limit = total_native_limit, retain_limit
        self.native_bytes = 0

    def _read(self, stream, length, *, line=False):
        length = min(length, max(0, self.total_native_limit - self.native_bytes))
        raw = stream.readline(length) if line else stream.read(length)
        self.native_bytes += len(raw)
        return raw

    def _prefix(self, stream, pin, path):
        # Count original binding/fence checks in the shared I/O budget, too.
        cost = pin.get('prefix_size', 4096) + min(pin.get('offset', 4096), 4096)
        need(type(cost) is int and cost >= 0 and
             self.native_bytes + cost <= self.total_native_limit, 'NATIVE_TOTAL_BUDGET_EXHAUSTED')
        size = check_prefix(stream, pin, path)
        self.native_bytes += cost
        return size

    def _metadata(self, stream, binding, end):
        """Authenticate original session metadata, never infer it from a filename."""
        stream.seek(0)
        used = 0
        for _ in range(64):
            allowance = min(256 * 1024, HEADER_LIMIT - used, end - stream.tell())
            if allowance <= 0:
                break
            raw = self._read(stream, allowance, line=True)
            used += len(raw)
            if not raw.endswith(b'\n'):
                break
            try:
                record = strict_json(raw)
            except (ValueError, UnicodeError, RecursionError):
                continue
            if not isinstance(record, dict):
                continue
            if binding['provider'] == 'codex':
                payload = record.get('payload', {})
                if record.get('type') != 'session_meta' or not isinstance(payload, dict):
                    continue
                values = [payload[k] for k in ('id', 'session_id') if k in payload]
            else:
                values = [record['sessionId']] if 'sessionId' in record else []
            if values:
                need(all(value == binding['session_id'] for value in values), 'NATIVE_SESSION_METADATA_CHANGED')
                return
        raise Uncertain('NATIVE_SESSION_METADATA_MISSING_OR_BOUNDED')

    def native(self, attempt, payload, receiver, candidates=(), *, command=None):
        binding = attempt['native_binding']
        need(binding.get('schema') == 'native-delivery-v1'
             and binding.get('payload_sha256') == sha(payload)
             and binding.get('session_id') == receiver['session_id']
             and binding.get('provider') == receiver['provider']
             and binding.get('process') == receiver['process']
             and binding.get('tty') == receiver['tty']
             and binding.get('provider') in ('codex', 'claude'), 'ORIGINAL_RECEIVER_BINDING_MISMATCH')
        pin = binding['transcript']
        need(all(pin[k] == receiver['transcript'][k] for k in ('path', 'device', 'inode')),
             'CAMPAIGN_NATIVE_TRANSCRIPT_CHANGED')
        path = Path(pin['path'])
        need(path.resolve() == path and path.is_relative_to(Path(receiver['native_config_root']))
             and path.name.endswith(binding['session_id'] + '.jsonl'), 'PRIVATE_NATIVE_PATH_INVALID')
        event, fence = original_event(attempt, binding, payload)
        need(wire_reference(payload) is not None, 'EXACT_REFERENCE_NOTICE_REQUIRED')
        results, records = [], []
        retained_bytes = 0
        retention_complete = True
        scan = dict(start=fence['offset'], budget=self.scan_limit, complete=False,
                    next_offset=fence['offset'], tool_records_retained=0)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f:
            self._prefix(f, pin, path)
            end = self._prefix(f, fence, path)
            observed_before = time.time()
            self._metadata(f, binding, end)
            seen = set()

            def observe_record(offset, raw, expected=None):
                nonlocal retained_bytes, retention_complete
                if offset in seen or not raw.endswith(b'\n'):
                    return
                need(len(raw) <= self.record_limit, 'NATIVE_RECORD_BUDGET_EXCEEDED')
                # The sequential scan never repeats an offset; retain only the
                # at-most-eight saved proof offsets to avoid an unbounded set.
                if expected is not None:
                    seen.add(offset)
                    need(sha(raw) == expected['sha256'], 'EXISTING_NATIVE_PROOF_HASH_CHANGED')
                try:
                    record = strict_json(raw)
                except (ValueError, UnicodeError, RecursionError):
                    retention_complete = False
                    return
                if not isinstance(record, dict):
                    retention_complete = False
                    return
                if binding['provider'] == 'codex' and record.get('type') == 'session_meta':
                    meta = record.get('payload', {})
                    need(isinstance(meta, dict) and any(k in meta for k in ('id', 'session_id'))
                         and all(meta[k] == binding['session_id'] for k in ('id', 'session_id') if k in meta),
                         'NATIVE_SESSION_METADATA_CHANGED')
                proof = dict(path=str(path), device=pin['device'], inode=pin['inode'],
                             offset=offset, length=len(raw), sha256=sha(raw))
                kind = inbound(record, binding, payload, event['at_epoch'])
                if kind:
                    need(len(results) < 256, 'NATIVE_NOTICE_OBSERVATION_COUNT_BUDGET')
                    results.append(dict(kind=kind, record=proof,
                        session_id=binding['session_id'], timestamp=record['timestamp']))
                if relevant(record, binding['provider']):
                    # Bound parsed-object overhead as well as serialized bytes.
                    if retained_bytes + len(raw) > self.retain_limit or len(records) >= 8192:
                        retention_complete = False
                    else:
                        records.append((proof, record))
                        retained_bytes += len(raw)

            # Existing proofs allow exact record reads even beyond the bounded scan.
            for proof in candidates[:8]:
                if not isinstance(proof, dict):
                    continue
                need(all(proof.get(k) == pin[k] for k in ('path', 'device', 'inode'))
                     and proof.get('session_id') == binding['session_id']
                     and proof.get('payload_sha256') == sha(payload), 'EXISTING_NATIVE_PROOF_BINDING_CHANGED')
                offset, length = proof.get('offset'), proof.get('length')
                need(type(offset) is int and type(length) is int and offset >= fence['offset']
                     and 0 < length <= self.record_limit and offset + length <= end, 'PROOF_RANGE_INVALID')
                if self.native_bytes + length + 1 > self.total_native_limit:
                    break
                if offset:
                    f.seek(offset - 1)
                    need(self._read(f, 1) == b'\n', 'PROOF_NOT_RECORD_BOUNDARY')
                f.seek(offset)
                raw = self._read(f, length)
                observe_record(offset, raw, proof)
            f.seek(fence['offset'])
            if fence['at_record_boundary'] and (not fence['offset'] or self._newline_before(f, fence['offset'])):
                offset = fence['offset']
                read_bytes = 0
                reason = 'COMPLETE_BOUND_RANGE'
                while offset < end:
                    allowance = min(self.record_limit + 1, self.scan_limit - read_bytes,
                                    end - offset, self.total_native_limit - self.native_bytes)
                    if allowance <= 0:
                        reason = 'NATIVE_SCAN_BUDGET_EXHAUSTED'
                        break
                    line = self._read(f, allowance, line=True)
                    read_bytes += len(line)
                    if not line.endswith(b'\n'):
                        reason = ('NATIVE_RECORD_BUDGET_EXCEEDED' if len(line) > self.record_limit
                                  else 'INCOMPLETE_OR_BUDGET_TRUNCATED_RECORD')
                        break
                    observe_record(offset, line)
                    offset += len(line)
                scan.update(end=offset, next_offset=offset, snapshot_eof=end, complete=offset == end,
                            bytes_read=read_bytes, reason=reason)
            else:
                scan['reason'] = 'FENCE_NOT_COMPLETE_RECORD_BOUNDARY'
            need(os.fstat(f.fileno()).st_size >= end, 'NATIVE_SNAPSHOT_TRUNCATED')
            # Recheck the exact records supporting any claim before closing the
            # snapshot. An in-place edit after the first read must not keep a pin.
            claim_pins = {p['record']['offset']: p['record'] for p in results}
            claim_pins.update({p['offset']: p for p, _ in records})
            for proof in claim_pins.values():
                need(self.native_bytes + proof['length'] <= self.total_native_limit,
                     'NATIVE_RECORD_REVALIDATION_BUDGET_EXHAUSTED')
                f.seek(proof['offset'])
                raw = self._read(f, proof['length'])
                need(len(raw) == proof['length'] and sha(raw) == proof['sha256'],
                     'NATIVE_RECORD_CHANGED_DURING_OBSERVATION')
            self._prefix(f, pin, path)
            self._prefix(f, fence, path)
        scan.update(tool_records_retained=len(records), retained_bytes=retained_bytes,
                    tool_record_retention_complete=retention_complete)
        # Incomplete scans cannot rule out a duplicate call ID in an unread suffix.
        # Do not turn an exact reader string, a queued notice or our own body read
        # into an observation of the receiver's successful complete tool result.
        if command and scan['complete'] and retention_complete:
            proofs = body_read_evidence(sorted(records, key=lambda item: item[0]['offset']),
                provider=binding['provider'], session_id=binding['session_id'], notice=payload,
                command=command, not_before=max(binding['bound_at_epoch'], event['at_epoch']),
                not_after=observed_before)
            need(isinstance(proofs, list) and all(isinstance(p, dict) and
                 p.get('kind') == 'BODY_READ_OBSERVED' for p in proofs), 'BODY_MATCHER_CONTRACT_CHANGED')
            results.extend(proofs)
        elif command:
            scan['body_read_reason'] = 'FULL_SCAN_AND_TOOL_RECORD_RETENTION_REQUIRED'
        return results, scan, dict(phase=event['phase'], at_epoch=event['at_epoch'], fence=fence)

    def _newline_before(self, f, offset):
        f.seek(offset - 1)
        return self._read(f, 1) == b'\n'


def default_row(sample, reason):
    return dict(sample, attempt='MISSING', original_input='UNKNOWN', refusal='UNKNOWN',
                native_reception='UNKNOWN', body_read='UNKNOWN', business_acceptance='NOT_ASSESSED',
                sender_runtime='UNKNOWN', receiver_runtime='UNKNOWN', reasons=[reason], evidence={})


def canonical_identity(identity):
    keys = {'workspace_uuid', 'caller_surface_uuid', 'target_surface_uuid', 'target_pane_uuid'}
    need(isinstance(identity, dict) and set(identity) == keys, 'IDENTITY_FIELDS_CHANGED')
    result = {}
    for key, value in identity.items():
        need(isinstance(value, str) and str(UUID(value)) == value.lower(), 'IDENTITY_UUID_CHANGED')
        result[key] = str(UUID(value))
    return result


def original_journal(state, identity, marker):
    """Adapter UUIDs are lowercase; bridge journals preserve cmux UUID case.

    Do not rewrite either producer's hashes. Current producers emit canonical
    lower/uppercase UUIDs; two original directories are ambiguous and refused.
    """
    caller = canonical_identity(identity)['caller_surface_uuid']
    candidates = [state / 'message-dispatch-v1' / sha(json.dumps([value, marker], sort_keys=True))
                  for value in sorted({caller, caller.upper()})]
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    need(len(present) <= 1, 'AMBIGUOUS_ORIGINAL_JOURNAL_UUID_CASE')
    return present[0] if present else state / 'message-dispatch-v1' / sha(json.dumps([caller, marker], sort_keys=True))


def original_dispatch_binding(attempt, journal, identity, marker, payload):
    """Compare UUID semantics only; authenticate every original hashed byte."""
    binding = attempt.get('binding', {})
    raw_identity = binding.get('identity')
    need(canonical_identity(raw_identity) == canonical_identity(identity), 'DISPATCH_IDENTITY_CHANGED')
    expected = dict(identity=raw_identity, marker=marker, payload_sha256=sha(payload))
    need(binding == expected and journal.name == sha(json.dumps(
        [raw_identity['caller_surface_uuid'], marker], sort_keys=True)), 'DISPATCH_BINDING_CHANGED')
    return expected


def expected_intent(sample, identity):
    identity = canonical_identity(identity)
    spec = dict(schema='cmux-agent-bridge-adapter-v1', identity=identity, mode='send',
                message=sample['body'], request_id=sample['request_id'])
    marker = 'CMUX_HELPER_' + sha(canonical(spec))
    channel = sha(canonical([identity['workspace_uuid'], identity['target_surface_uuid']]))
    return spec, marker, channel


def observe_sample(observer, sample, info):
    row = default_row(sample, 'NO_ORIGINAL_INTENT_EVIDENCE')
    if info.get('error'):
        row['attempt'] = 'UNKNOWN'
        row['reasons'] = [info['error']]
        return row
    campaign = info.get('campaign')
    if not campaign:
        row['reasons'] = ['CAMPAIGN_NOT_YET_PRESENT; absence is not proof of non-invocation']
        return row
    sessions = campaign['sessions']
    sender, receiver = sessions[sample['sender']], sessions[sample['receiver']]
    identity = canonical_identity(dict(workspace_uuid=receiver['workspace_uuid'], caller_surface_uuid=sender['surface_uuid'],
                    target_surface_uuid=receiver['surface_uuid'], target_pane_uuid=receiver['pane_uuid']))
    need(str(UUID(sender['workspace_uuid'])) == identity['workspace_uuid'], 'CAMPAIGN_WORKSPACE_MISMATCH')
    spec, marker, channel = expected_intent(sample, identity)
    state = Path(info['state_root'])
    intent_path = state / 'cmux-agent-adapter-v1/targets' / channel / 'intents' / (marker + '.json')
    row['expected_original_intent'] = str(intent_path)
    if not intent_path.exists():
        return row
    try:
        intent, intent_pin = json_file(intent_path)
        row['evidence']['helper_intent'] = intent_pin
        need(all(intent.get(k) == v for k, v in spec.items()) and intent.get('marker') == marker,
             'EXACT_REQUEST_OR_IDENTITY_CHANGED')
        payload = intent['payload']
        need(sha(payload) == intent['payload_sha256'], 'ORIGINAL_PAYLOAD_PIN_CHANGED')
        full = '[CMUX-AGENT][delivery:' + marker + '][from:' + identity['caller_surface_uuid'] + ']\n' + sample['body']
        reference = intent['body_reference']
        need(reference['path'] == str(state / 'message-bodies-v1' / (sha(full) + '.txt'))
             and reference['sha256'] == sha(full) and reference['bytes'] == len(full.encode())
             and reference.get('format') == 'cmux-message-reference-v2'
             and intent.get('original_payload_sha256') == sha(full), 'BODY_REFERENCE_CHANGED')
        body, body_pin = raw_file(reference['path'])
        need(body == full.encode(), 'BODY_CONTENT_CHANGED')
        row['evidence']['body_file'] = body_pin
        need(wire_reference(payload) == reference and payload.split(' ', 3)[2] == marker,
             'NOTICE_SHAPE_CHANGED')
        verify(reference, full)
        row['attempt'] = 'ORIGINAL_INTENT_PRESENT'
        row['reasons'] = []
        journal = original_journal(state, identity, marker)
        receipt = None
        receipt_path = journal / 'receipt.json'
        if receipt_path.exists():
            receipt, rp = json_file(receipt_path)
            row['evidence']['canonical_receipt_file'] = rp
            row['canonical_receipt_claim'] = dict(confirmed=receipt.get('confirmed'),
                confirmation_scope=receipt.get('confirmation_scope'),
                body_read_confirmed=receipt.get('body_read_confirmed'))
        attempts = [p for p in (journal / 'attempt-0001.json', journal / 'attempt-0002.json') if p.exists()]
        need(len(attempts) <= 1, 'MULTIPLE_ATTEMPTS_OBSERVED; no retry merged into original')
        if not attempts:
            row['reasons'].append('NO_DISPATCH_ATTEMPT; helper lock refusal may have no receipt')
            return row
        path = attempts[0]
        attempt, ap = json_file(path)
        row['evidence']['dispatch_attempt'] = ap
        expected_binding = original_dispatch_binding(attempt, journal, identity, marker, payload)
        row['original_phase_observed'] = attempt.get('phase')
        if attempt.get('error'):
            # Keep an exact-file pin and only a bounded diagnostic; arbitrary exception text is not refusal proof.
            row['attempt_error_type_or_prefix'] = str(attempt['error']).split(':', 1)[0][:160]
        if attempt.get('phase') == 'NO_INPUT':
            row['refusal'] = 'NO_INPUT_OBSERVED'
        elif attempt.get('phase') == 'REFUSED':
            row['refusal'] = 'REFUSED_OBSERVED'
        b = attempt.get('native_binding')
        need(isinstance(b, dict) and b.get('identity') == expected_binding['identity'], 'NO_ORIGINAL_NATIVE_BINDING')
        proofs = []
        if receipt:
            need(receipt.get('binding') == expected_binding and receipt.get('native_binding') == b
                 and receipt.get('attempt') == str(path), 'RECEIPT_ORIGINAL_ATTEMPT_CHANGED')
            proofs += [receipt.get('native_proof')]
        scan_path = path.with_name('native-scan-' + path.stem + '.json')
        if scan_path.exists():
            saved, sp = json_file(scan_path)
            row['evidence']['original_native_scan'] = sp
            need(saved.get('attempt') == str(path)
                 and saved.get('binding_sha256') == sha(json.dumps(b, sort_keys=True)), 'SAVED_SCAN_BINDING_CHANGED')
            proofs += [saved.get('native_proof'), saved.get('queued_proof')]
        command = read_command(info['python']['path'], info['controller']['path'], payload,
                               rtk=info['rtk']['path'])
        records, scan, original = observer.native(attempt, payload, receiver, proofs, command=command)
        row['original_input'] = 'ORIGINAL_INPUT_INTENT_OBSERVED'
        row['evidence'].update(native_records=records, bounded_scan=scan, original_input=original)
        kinds = {p['kind'] for p in records}
        if 'NATIVE_NOTICE_OBSERVED' in kinds:
            row['native_reception'] = 'NATIVE_NOTICE_OBSERVED'
        elif 'QUEUED_OBSERVED' in kinds:
            row['native_reception'] = 'QUEUED_OBSERVED'
        if 'BODY_READ_OBSERVED' in kinds:
            row['body_read'] = 'BODY_READ_OBSERVED'
        else:
            row['reasons'].append('BODY_READ_UNKNOWN: no complete matched receiver reader result in bounded scan')
    except (OSError, ValueError, TypeError, KeyError, RecursionError, Uncertain) as exc:
        row['reasons'].append(type(exc).__name__ + ': ' + str(exc)[:240])
        if row['attempt'] == 'MISSING':
            row['attempt'] = 'UNKNOWN'
    return row


def prepare_group(candidate, registration, group):
    info = {}
    try:
        config, config_pin = json_ref(candidate['groups'][group])
        context, context_pin = json_ref(config['context'])
        runtime, runtime_pin = json_ref(config['runtime'])
        startup, startup_pin = json_ref(runtime['startup'])
        expected = registration['groups'][group]
        root = Path(candidate['root'])
        state = Path(context['paths']['state']) / expected['arm']
        need(runtime['state_root'] == str(state) and state.is_relative_to(root)
             and context_pin['path'] == expected['context_path'], 'GROUP_PRIVATE_ROOT_CHANGED')
        helper = startup['pins']['helper']
        _, helper_pin = raw_file(helper['path'])
        need(all(helper_pin[k] == helper[k] for k in ('path', 'bytes', 'sha256')), 'HELPER_PIN_CHANGED')
        controller = runtime['controller']
        need(startup['controller'] == controller['path']
             and Path(controller['path']).is_relative_to(root)
             and runtime['python'] == startup['pins']['python'] == context['python'],
             'READER_RUNTIME_PINS_CHANGED')
        python_pin, rtk_pin = (executable_pin(startup['pins'][name]) for name in ('python', 'rtk'))
        reader_path = Path(controller['path']) / 'scripts/mac_upgrade_body_read.py'
        expected_reader = controller['files']['scripts/mac_upgrade_body_read.py']
        _, reader_pin = raw_file(reader_path)
        need(all(reader_pin[k] == expected_reader[k] for k in ('bytes', 'sha256')), 'READER_PIN_CHANGED')
        info.update(state_root=str(state), pins=dict(group=config_pin, context=context_pin,
            runtime=runtime_pin, startup=startup_pin, helper=helper_pin, reader=reader_pin,
            python=python_pin, rtk=rtk_pin), controller=controller, python=python_pin, rtk=rtk_pin)
        cp = Path(context['paths']['campaigns']) / expected['arm'] / expected['campaign_id'] / 'campaign.json'
        info['expected_campaign'] = str(cp)
        if cp.exists():
            campaign, campaign_pin = json_file(cp)
            need(campaign['session_group'] == group and campaign['runtime'] == config['runtime']
                 and campaign['registration'] == candidate['registration']
                 and campaign['controller'] == controller and campaign['python'] == runtime['python'],
                 'CAMPAIGN_PINS_CHANGED')
            need(set(campaign['sessions']) == {'codex', 'claude_a', 'claude_b'}
                 and len({s['surface_uuid'] for s in campaign['sessions'].values()}) == 3
                 and len({s['session_id'] for s in campaign['sessions'].values()}) == 3
                 and all(campaign['sessions'][lane]['provider'] == ('codex' if lane == 'codex' else 'claude')
                         for lane in campaign['sessions']),
                 'CAMPAIGN_SESSION_IDENTITY_NOT_DISTINCT')
            info.update(campaign=campaign)
            info['pins']['campaign'] = campaign_pin
    except (OSError, ValueError, TypeError, KeyError, RecursionError, Uncertain) as exc:
        info['error'] = type(exc).__name__ + ': ' + str(exc)[:240]
    return info


def write_guard(output, event, args):
    if event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'socket.connect', 'socket.bind'):
        raise PermissionError('FILE_ONLY_OBSERVER')
    paths = ()
    if event == 'open':
        path, mode, flags = args
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            paths = (path,)
    elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.chmod', 'os.truncate', 'os.utime'):
        paths = args[:1]
    elif event in ('os.rename', 'os.link', 'os.symlink'):
        paths = args[:2]
    for path in paths:
        if not isinstance(path, (str, bytes, os.PathLike)) or not Path(os.fsdecode(path)).resolve().is_relative_to(output):
            raise PermissionError('OBSERVER_WRITE_OUTSIDE_EVIDENCE_OUTPUT')


def collect(candidate_path, *, observer=None):
    """Return a read-only snapshot; caller owns publication of new evidence."""
    started = dt.datetime.now(dt.timezone.utc)
    candidate, candidate_pin = json_file(candidate_path)
    plan, plan_pin = json_ref(candidate['communication_plan'])
    registration, registration_pin = json_ref(candidate['registration'])
    root = Path(candidate['root'])
    need(root.is_absolute() and root.resolve() == root, 'PRIVATE_ROOT_MUST_BE_CANONICAL')
    need(plan['schema'] == 'upgrade-communication-plan-v1'
         and plan['total_samples'] == 192 and len(plan['samples']) == 192
         and len({s['request_id'] for s in plan['samples']}) == 192, 'FIXED_192_DENOMINATOR_REQUIRED')
    from upgrade_communication_plan import registration_plan
    need(plan == registration_plan(registration), 'COMMUNICATION_PLAN_REGISTRATION_CHANGED')
    observer = observer or Observer()
    groups = {g: prepare_group(candidate, registration, g) for g in sorted({s['group'] for s in plan['samples']})}
    rows = [observe_sample(observer, s, groups[s['group']]) for s in plan['samples']]
    counts = {key: dict(collections.Counter(r[key] for r in rows)) for key in
              ('attempt', 'original_input', 'refusal', 'native_reception', 'body_read', 'business_acceptance')}
    by_arm = {arm: dict(total=sum(r['arm'] == arm for r in rows),
        native_reception=dict(collections.Counter(r['native_reception'] for r in rows if r['arm'] == arm)))
        for arm in ('A1', 'B')}
    result = dict(schema=SCHEMA, observation_only=True,
        started_at=started.isoformat(), finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        total_samples=192, counts=counts, by_arm=by_arm, native_bytes_read=observer.native_bytes,
        input_operations=0, runtime_writes=0, canonical_receipts_written=0, model_calls=0,
        source_pins=dict(observer=raw_file(Path(__file__).resolve())[1], candidate=candidate_pin,
                         plan=plan_pin, registration=registration_pin),
        groups={g: {k: v for k, v in info.items() if k != 'campaign'} for g, info in groups.items()},
        limits=dict(metadata_file_bytes=META_LIMIT, native_per_sample_bytes=observer.scan_limit,
                    exact_native_record_bytes=observer.record_limit, total_native_bytes=observer.total_native_limit,
                    retained_tool_record_bytes=observer.retain_limit, retained_tool_record_count=8192),
        limitations=[
            'All 192 planned samples remain in the denominator, including unreached warm runs.',
            'MISSING means no original intent evidence at the bound path; it does not prove no invocation or refusal.',
            'Only exact original request IDs, original sender/receiver campaign bindings and original EOF fences are used.',
            'Existing canonical receipt assertions are retained separately; this observer never issues transport acceptance.',
            'Queued records prove queue observation only; notice reception does not prove body access, processing or business acceptance.',
            'BODY_READ_OBSERVED means the complete original body entered the receiver tool-result context, not understanding, agreement, execution or business acceptance.',
            'Only the pinned fixed reader command and a unique original call/result pair qualify; arbitrary shell reads and model claims do not.',
            'Queued notice and BODY_READ_OBSERVED can coexist; this never manufactures a native notice receipt.',
            'Native scans stream from the original EOF fence within explicit byte/record/memory budgets; incomplete scans keep body reading UNKNOWN.',
            'No persistent cursor is trusted or written. A larger bounded read may inspect later evidence without replaying the original input.',
            'No live process reauthentication, full-controller tree rehash, active/idle inference or efficiency calculation is performed.',
            'Snapshot reads are individually stable, not an atomic campaign snapshot. No journal locks, cursor writes, reconcile calls or input occur.',
        ], samples=rows)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--scan-limit', type=int, default=SCAN_LIMIT)
    parser.add_argument('--record-limit', type=int, default=RECORD_LIMIT)
    parser.add_argument('--total-native-limit', type=int, default=TOTAL_NATIVE_LIMIT)
    parser.add_argument('--retain-limit', type=int, default=RETAIN_LIMIT)
    args = parser.parse_args()
    need(args.candidate.is_absolute() and args.candidate.resolve() == args.candidate
         and args.output.is_absolute() and args.output.resolve() == args.output,
         'CANONICAL_ABSOLUTE_CANDIDATE_AND_OUTPUT_REQUIRED')
    candidate, _ = json_file(args.candidate)
    root = Path(candidate['root'])
    need(not args.output.is_relative_to(root) and not root.is_relative_to(args.output),
         'OUTPUT_MUST_BE_SEPARATE_FROM_PRIVATE_RUNTIME')
    # Do not let a broad output root accidentally admit writes to existing input.
    need(not args.candidate.is_relative_to(args.output)
         and not Path(__file__).resolve().is_relative_to(args.output), 'OUTPUT_ROOT_TOO_BROAD')
    args.output.mkdir(parents=True, exist_ok=True)
    sys.addaudithook(lambda event, values: write_guard(args.output, event, values))
    observer = Observer(scan_limit=args.scan_limit, record_limit=args.record_limit,
                        total_native_limit=args.total_native_limit, retain_limit=args.retain_limit)
    result = collect(args.candidate, observer=observer)
    started = dt.datetime.fromisoformat(result['started_at'])
    counts = result['counts']
    out = args.output / (started.strftime('%Y%m%dT%H%M%S.%fZ') + '-' + str(os.getpid()))
    out.mkdir(parents=True, exist_ok=False)
    path = out / 'observation.json'
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    report = ('# Communication observation v2\n\n'
        + 'All 192 planned samples are retained; A1=96, B=96. This is file-only observation, not business or transport acceptance.\n\n'
        + '```json\n' + json.dumps(counts, indent=2) + '\n```\n\n'
        + '\n'.join('- ' + line for line in result['limitations']) + '\n')
    (out / 'README.md').write_text(report)
    print(json.dumps(dict(output=raw_file(path)[1], total_samples=192, counts=counts,
                          native_bytes_read=observer.native_bytes, input_operations=0, runtime_writes=0)))


if __name__ == '__main__':
    main()
