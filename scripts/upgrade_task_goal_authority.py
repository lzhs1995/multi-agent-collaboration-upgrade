"""Verify a native Goal plus the exact attachment read in that original thread.

This is a separate authority kind, never a synthesized native user message.
The caller supplies the already authorized context and separately binds the
requested capability scope and original owner's acceptance. This verifier does
not grant a live actor identity, change a controller, or authorize retries.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shlex

from orchestration_common import (contract_errors, count, digest, json_ref,
    parse_time, read_ref, require, strict_json)
from orchestration_native import NativeReader

SCHEMA = 'upgrade-task-goal-attachment-authority-v1'
CONTEXT_FIELDS = {'original_session_id', 'goal_created_at', 'attachment_bytes',
                  'attachment_sha256'}
FIELDS = {'schema', 'session', 'goal_record', 'goal_created_at', 'attachment',
          'read_call', 'read_execution', 'read_output'}
RECORD_FIELDS = {'path', 'device', 'inode', 'offset', 'length', 'sha256'}
MAX_ATTACHMENT = 256 * 1024
MAX_READ_DELAY_SECONDS = 600


def _full_read_command(argv, attachment_path, body):
    """A finite literal command grammar; no interpreter or shell execution."""
    require(isinstance(argv, list) and len(argv) == 3
            and argv[0] in ('/bin/zsh', '/bin/bash', '/bin/sh')
            and argv[1] in ('-c', '-lc') and isinstance(argv[2], str),
            'GOAL_ATTACHMENT_LITERAL_READ_COMMAND_REQUIRED')
    command = argv[2]
    require(not any(c in command for c in '\n\r\x00$`;&|<>'),
            'GOAL_ATTACHMENT_LITERAL_READ_COMMAND_REQUIRED')
    tokens = shlex.split(command, comments=False, posix=True)
    if tokens[:2] == ['rtk', 'proxy']:
        tokens = tokens[2:]
    if tokens == ['cat', attachment_path]:
        return command
    require(len(tokens) == 4 and tokens[:2] == ['sed', '-n']
            and tokens[3] == attachment_path,
            'GOAL_ATTACHMENT_LITERAL_READ_COMMAND_REQUIRED')
    match = re.fullmatch(r'1,([1-9][0-9]*)p', tokens[2])
    require(match is not None and int(match[1]) >= len(body.splitlines()),
            'GOAL_ATTACHMENT_COMPLETE_READ_REQUIRED')
    return command


def _returned_bodies(payload):
    """Decode the exact exec result blocks; never substring-match escaped JSON."""
    blocks = payload.get('output')
    require(isinstance(blocks, list) and 0 < len(blocks) <= 64,
            'GOAL_ATTACHMENT_NATIVE_OUTPUT_BLOCKS_REQUIRED')
    result = []
    for block in blocks:
        require(isinstance(block, dict) and block.get('type') == 'input_text'
                and isinstance(block.get('text'), str), 'GOAL_ATTACHMENT_OUTPUT_BLOCK_INVALID')
        text = block['text']
        if not text.startswith('{'):
            continue  # The native exec tool's human-readable header.
        value = strict_json(text)
        if not isinstance(value, dict) or 'output' not in value:
            continue
        if (type(value.get('exit_code')) is int and value['exit_code'] == 0
                and isinstance(value['output'], str) and value.get('session_id') is None
                and value.get('cell_id') is None):
            result.append(value['output'].encode('utf-8'))
    return result


def _verify(value, authority_ref, expected_context, *, fixtures=False):
    # One independent snapshot is used for every subsequent comparison.
    value, expected = copy.deepcopy(value), copy.deepcopy(expected_context)
    require(isinstance(value, dict) and set(value) == FIELDS and value['schema'] == SCHEMA,
            'GOAL_AUTHORITY_SCHEMA_INVALID')
    require(isinstance(expected, dict) and set(expected) == CONTEXT_FIELDS,
            'GOAL_AUTHORITY_EXPECTED_CONTEXT_REQUIRED')
    count(expected['goal_created_at'], 'goal creation time')
    count(expected['attachment_bytes'], 'authorized attachment bytes')
    require(0 < expected['attachment_bytes'] <= MAX_ATTACHMENT
            and isinstance(expected['attachment_sha256'], str)
            and re.fullmatch('[0-9a-f]{64}', expected['attachment_sha256']),
            'GOAL_AUTHORITY_EXPECTED_CONTENT_INVALID')
    session = value['session']
    require(session.get('provider') == 'codex'
            and session.get('session_id') == expected['original_session_id']
            and type(value['goal_created_at']) is int
            and value['goal_created_at'] == expected['goal_created_at'],
            'GOAL_AUTHORITY_ORIGINAL_CONTEXT_CHANGED')
    reader = NativeReader(session, fixtures=fixtures)
    attachment = value['attachment']
    require(isinstance(attachment, dict) and set(attachment) == {'path', 'bytes', 'sha256'}
            and type(attachment['bytes']) is int
            and attachment['bytes'] == expected['attachment_bytes']
            and attachment['sha256'] == expected['attachment_sha256'],
            'GOAL_AUTHORITY_ATTACHMENT_CONTEXT_CHANGED')
    body = read_ref(attachment, 'original goal attachment')
    body.decode('utf-8')  # Exact UTF-8 bytes, with no newline/Unicode normalization.
    records = {}
    previous_end = -1
    for name in ('goal_record', 'read_call', 'read_execution', 'read_output'):
        ref = value[name]
        require(isinstance(ref, dict) and set(ref) == RECORD_FIELDS,
                'GOAL_AUTHORITY_NATIVE_RECORD_PIN_REQUIRED')
        record = reader.record(ref)
        require(ref['offset'] >= previous_end, 'GOAL_AUTHORITY_RECORD_ORDER_INVALID')
        previous_end = ref['offset'] + ref['length']
        records[name] = record
    goal_record = records['goal_record']; payload = goal_record.get('payload', {})
    goal = payload.get('goal', {})
    require(goal_record.get('type') == 'event_msg'
            and payload.get('type') == 'thread_goal_updated'
            and payload.get('threadId') == session['session_id']
            and goal.get('threadId') == session['session_id']
            and goal.get('status') == 'active'
            and type(goal.get('createdAt')) is int and type(goal.get('updatedAt')) is int
            and goal['createdAt'] == goal['updatedAt'] == expected['goal_created_at']
            and type(goal.get('tokensUsed')) is int and goal['tokensUsed'] == 0
            and type(goal.get('timeUsedSeconds')) is int and goal['timeUsedSeconds'] == 0,
            'GOAL_AUTHORITY_NATIVE_INITIAL_GOAL_REQUIRED')
    require(goal.get('objective') == 'pasted text file: ' + attachment['path']
            + '. Read this file before continuing.', 'GOAL_AUTHORITY_ATTACHMENT_PATH_CHANGED')
    interval_start = value['goal_record']['offset'] + value['goal_record']['length']
    interval_end = value['read_output']['offset'] + value['read_output']['length']
    require(interval_end - interval_start <= 2 * 1024 * 1024,
            'GOAL_AUTHORITY_CONTEMPORANEOUS_INTERVAL_TOO_LARGE')
    for _, intermediate in reader.records(interval_start, interval_end):
        middle = intermediate.get('payload', {})
        if intermediate.get('type') == 'event_msg' and middle.get('type') == 'thread_goal_updated':
            later = middle.get('goal', {})
            require(middle.get('threadId') == session['session_id']
                    and later.get('threadId') == session['session_id']
                    and later.get('createdAt') == goal['createdAt']
                    and later.get('objective') == goal['objective']
                    and later.get('status') == 'active',
                    'GOAL_AUTHORITY_GOAL_CHANGED_BEFORE_READ')
    times = [parse_time(records[k].get('timestamp')) for k in
             ('goal_record', 'read_call', 'read_execution', 'read_output')]
    require(int(times[0].timestamp()) == expected['goal_created_at']
            and times == sorted(times)
            and (times[-1] - times[0]).total_seconds() <= MAX_READ_DELAY_SECONDS,
            'GOAL_AUTHORITY_READ_TIME_INVALID')
    call = records['read_call'].get('payload', {})
    output = records['read_output'].get('payload', {})
    event = records['read_execution'].get('payload', {}); item = event.get('item', {})
    turn = call.get('internal_chat_message_metadata_passthrough', {}).get('turn_id')
    require(records['read_call'].get('type') == 'response_item'
            and call.get('type') == 'custom_tool_call' and call.get('name') == 'exec'
            and isinstance(call.get('input'), str) and isinstance(call.get('call_id'), str)
            and bool(call['call_id']) and isinstance(turn, str) and bool(turn),
            'GOAL_AUTHORITY_NATIVE_READ_CALL_REQUIRED')
    require(records['read_execution'].get('type') == 'event_msg'
            and event.get('type') == 'item_completed'
            and event.get('thread_id') == session['session_id'] and event.get('turn_id') == turn
            and item.get('type') == 'CommandExecution' and item.get('status') == 'completed'
            and type(item.get('exit_code')) is int and item['exit_code'] == 0
            and item.get('stderr') == ''
            and isinstance(item.get('stdout'), str) and item['stdout'].encode('utf-8') == body
            and item.get('aggregated_output') == item['stdout'],
            'GOAL_AUTHORITY_COMPLETE_NATIVE_READ_REQUIRED')
    command = _full_read_command(item.get('command'), attachment['path'], body)
    # This checks the recorded exec source without evaluating JavaScript.
    cmd_literals = re.findall(r'\bcmd\s*:\s*("(?:[^"\\]|\\.)*")', call['input'])
    require(sum(json.loads(literal) == command for literal in cmd_literals) == 1,
            'GOAL_AUTHORITY_READ_CALL_COMMAND_MISMATCH')
    require(records['read_output'].get('type') == 'response_item'
            and output.get('type') == 'custom_tool_call_output'
            and output.get('call_id') == call['call_id']
            and output.get('internal_chat_message_metadata_passthrough', {}).get('turn_id') == turn,
            'GOAL_AUTHORITY_SAME_CALL_OUTPUT_REQUIRED')
    require(sum(b == body for b in _returned_bodies(output)) == 1,
            'GOAL_AUTHORITY_FULL_CONTENT_NOT_RETURNED')
    reader.verify_metadata()
    require(read_ref(attachment) == body, 'GOAL_AUTHORITY_ATTACHMENT_CHANGED_DURING_CHECK')
    return dict(schema='upgrade-task-goal-attachment-verification-v1',
        kind='native_goal_attachment_read', native_user_message=False,
        original_session_id=session['session_id'], goal_created_at=expected['goal_created_at'],
        goal_timestamp=goal_record['timestamp'], attachment=attachment,
        attachment_text_sha256=digest(body), expected_context=expected,
        read_turn_id=turn, read_call_id=call['call_id'], authority_ref=copy.deepcopy(authority_ref),
        records={k:value[k] for k in ('goal_record', 'read_call', 'read_execution', 'read_output')},
        scope='Original Goal and exact contemporaneous attachment consumption only; no live actor or capability scope grant.')


@contract_errors('original Goal attachment authority')
def verify_goal_authority(ref, *, expected_context):
    """Read-only production verifier. There is intentionally no fixture switch."""
    require(isinstance(ref, dict) and set(ref) == {'path', 'bytes', 'sha256'},
            'GOAL_AUTHORITY_PINNED_DESCRIPTOR_REQUIRED')
    return _verify(json_ref(ref), ref, expected_context)
