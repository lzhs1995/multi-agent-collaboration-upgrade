"""Offline probe for actual materialized modules; never operates a live client.

Only live process/GUI/RPC observations are fixtures. Native binding validation,
file pins, receipt parser and the materialized capability code are real.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import uuid
from unittest.mock import Mock, patch


def run(controller, native_root, iterations=1):
    import cmux_bridge as bridge
    import cmux_native_delivery as native
    import mac_upgrade_handshake_capability as cap
    import cmux_codex_input as codex

    for module in (bridge, native, cap, codex):
        assert Path(module.__file__).parent == Path(controller), module.__file__
    root = Path(native_root) / 'projects' / ('offline-handshake-' + str(uuid.uuid4()))
    root.mkdir(parents=True)
    cases = []

    def screen(body='', active='✻ Thinking…\n◎ /goal active (2m)'):
        border = '─' * 64
        return (active + '\n\n' + border + '\n❯ ' + body + '\n' + border +
                '\n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n')

    def rejected(call):
        try:
            call()
        except (ValueError, RuntimeError, OSError):
            return
        raise AssertionError('expected refusal')

    with patch.object(bridge, 'send_text') as paste, patch.object(bridge, 'send_key') as key, \
            patch.object(bridge, 'submit_text') as submit, patch.object(codex, 'DaemonRPC') as rpc:
        for index in range(iterations):
            sid = str(uuid.uuid4())
            text = 'MESSAGE_REFERENCE_V2 offline-' + sid
            log = root / (sid + '.jsonl')
            log.write_text(json.dumps(dict(type='system', sessionId=sid)) + '\n')
            ids = {k: str(uuid.uuid4()) for k in native.IDENTITY_KEYS}
            surface = 'surface:45'
            member = dict(surface_ref=surface, surface_uuid=ids['target_surface_uuid'],
                          provider='claude', workspace_ref='workspace:8', pane_ref='pane:20', ordinal=1)
            gate = dict(status='PASS', task_id='offline-' + sid, supervisor='surface:42',
                supervisor_surface_uuid=ids['caller_surface_uuid'], workspace_uuid=ids['workspace_uuid'],
                workspace_ref='workspace:8', native_participants=[dict(role='executor',
                    surface_uuid=ids['target_surface_uuid'], native_session_id=sid, provider='claude')],
                workspace_proofs={surface: dict(ids, workspace_ref='workspace:8', target_surface_ref=surface)})
            binding = dict(schema=native.SCHEMA, identity=ids, tty='ttys99',
                process=dict(pid=12345, birth=[100, 1]), provider='claude', session_id=sid,
                native_config_root=str(native_root), transcript=native._pin_file(log, 'claude', sid),
                bound_at_epoch=time.time(), payload_sha256=hashlib.sha256(text.encode()).hexdigest())
            native._validate_binding(binding, text)
            with patch.object(native, 'bind_target', return_value=binding), \
                    patch.object(native, 'require_bound') as bound, \
                    patch.object(cap, 'check_input') as check, \
                    patch.object(bridge, 'read_screen', side_effect=[screen(), screen(active='✢ Working…\n◎ /goal active (3m)')]):
                proof = cap.observe(bridge, native, gate, [member], member, text)
                assert check.call_count == 2 and bound.call_count == 1
                assert proof['input_operations'] == 0
                assert all(x['active_or_queued'] for x in proof['capability']['observations'])
            cases.append('active_goal_capability')

            selected = dict(cap.panel_rows(gate, [member])[0], handshake_precondition=proof)
            actual = copy.deepcopy(binding)
            actual['bound_at_epoch'] = time.time()
            cap.require_original(selected, gate, actual, text, native)
            cases.append('actual_native_binding_and_prefix')
            for name, screens in [('draft', [screen('preserve 用户草稿')]),
                                  ('changed', [screen(), screen('new draft')]),
                                  ('partial', ['truncated ❯']),
                                  ('unknown', [screen() + 'unknown footer'])]:
                with patch.object(native, 'bind_target', return_value=binding), \
                        patch.object(cap, 'check_input'), patch.object(bridge, 'read_screen', side_effect=screens):
                    rejected(lambda: cap.observe(bridge, native, gate, [member], member, text))
                cases.append(name)
            for field in ('session_id', 'process', 'transcript'):
                wrong = copy.deepcopy(actual)
                if field == 'session_id':
                    wrong[field] = str(uuid.uuid4())
                elif field == 'process':
                    wrong[field]['birth'][0] += 1
                else:
                    wrong[field]['inode'] += 1
                rejected(lambda: cap.require_original(selected, gate, wrong, text, native))
                cases.append('rebind_' + field)

            # Real original native fence: a queue never becomes native reception.
            fence = native.capture_paste_fence(actual)
            intent_at = time.time()
            queued = dict(type='attachment', sessionId=sid,
                timestamp=datetime.now(timezone.utc).isoformat(),
                attachment=dict(type='queued_command', prompt=text))
            with log.open('a') as stream:
                stream.write(json.dumps(queued) + '\n')
            result = native.probe(actual, text, paste_fence=fence, not_before=intent_at)
            assert not result['confirmed'] and result['state'] == 'NATIVE_QUEUED', result
            cases.append('queue_unconfirmed')
            with log.open('a') as stream:
                stream.write(json.dumps(dict(type='user', sessionId=sid,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    message=dict(role='user', content=text))) + '\n')
            result = native.probe(actual, text, paste_fence=fence, not_before=intent_at)
            assert result['confirmed'] and native.validate_proof(actual, text, result['native_proof'],
                paste_fence=fence, not_before=intent_at)
            cases.append('exact_native_user_fixture')
            # Corrupt the original bytes at the same inode; no later binding repairs them.
            with log.open('r+b') as stream:
                stream.write(b'!')
            rejected(lambda: cap.require_original(selected, gate, actual, text, native))
            cases.append('original_prefix_changed')
            log.unlink()
        paste.assert_not_called()
        key.assert_not_called()
        submit.assert_not_called()
        rpc.assert_not_called()
    root.rmdir()
    return dict(iterations=iterations, checks=len(cases), case_counts={k: cases.count(k) for k in sorted(set(cases))},
                model_requests=0, native_inputs=0, live_observations_are_fixtures=True,
                native_receipt_records_are_fixtures=True)
