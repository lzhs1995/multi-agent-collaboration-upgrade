"""Offline native-journal fixtures, never live delivery or model-efficiency evidence."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from cmux_prompt_reference import plan, persist
from orchestration_common import file_ref, write_json
from upgrade_benchmark import CONTRACT, layout
from upgrade_body_read import SCHEMA, SCOPE, read_command
from upgrade_communication_plan import registration_plan
import upgrade_communication_observer as observation


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.at = time.time() - 20
        self.make('claude')

    def make(self, provider):
        self.provider = provider
        self.session = str(uuid.uuid4())
        self.native_root = self.root / ('native-' + self.session)
        self.native_root.mkdir()
        self.path = self.native_root / (self.session + '.jsonl')
        self.state = self.root / ('state-' + self.session)
        self.state.mkdir()
        self.sample = dict(group='test-B', arm='B', request_id='one-original-request',
            body='STATUS: Offline fixture, no acknowledgement required.',
            sender='codex' if provider == 'claude' else 'claude_a',
            receiver='claude_a' if provider == 'claude' else 'codex')
        workspace = str(uuid.uuid4())
        self.sender = dict(surface_uuid=str(uuid.uuid4()), workspace_uuid=workspace)
        self.receiver = dict(provider=provider, session_id=self.session, process={'pid': 42, 'birth': 'fixture'},
            tty='/dev/ttys-offline', native_config_root=str(self.native_root),
            surface_uuid=str(uuid.uuid4()), pane_uuid=str(uuid.uuid4()), workspace_uuid=workspace)
        self.identity = dict(workspace_uuid=workspace, caller_surface_uuid=self.sender['surface_uuid'],
            target_surface_uuid=self.receiver['surface_uuid'], target_pane_uuid=self.receiver['pane_uuid'])
        self.spec, self.marker, self.channel = observation.expected_intent(self.sample, self.identity)
        self.full = ('[CMUX-AGENT][delivery:' + self.marker + '][from:' + self.sender['surface_uuid']
                     + ']\n' + self.sample['body'])
        planned = plan(self.full, self.marker, root=self.state / 'message-bodies-v1')
        self.notice, self.reference = planned['text'], planned['reference']
        persist(self.reference, self.full)
        self.command = read_command('/fixture/python', '/fixture/controller', self.notice, rtk='/fixture/rtk')
        header = (dict(type='session_meta', payload={'id': self.session}) if provider == 'codex'
                  else dict(type='user', sessionId=self.session, message={'role': 'user', 'content': 'initial'}))
        header['timestamp'] = self.stamp(-5)
        self.path.write_bytes(self.encoded(header))
        transcript = self.pin()
        self.receiver['transcript'] = transcript
        self.binding = dict(schema='native-delivery-v1', payload_sha256=observation.sha(self.notice),
            session_id=self.session, provider=provider, process=self.receiver['process'], tty=self.receiver['tty'],
            bound_at_epoch=self.at - 3, transcript=transcript, identity=self.identity)
        self.attempt = dict(native_binding=self.binding, phase='INPUT_SENT', binding=dict(
            identity=self.identity, marker=self.marker, payload_sha256=observation.sha(self.notice)))
        fence = dict(binding_sha256=observation.sha(json.dumps(self.binding, sort_keys=True)),
                     captured_at_epoch=self.at - 1, transcript=transcript)
        event = dict(at_epoch=self.at)
        if provider == 'codex':
            request = dict(method='turn/start', params={'threadId': self.session,
                           'input': [dict(type='text', text=self.notice)]})
            fence.update(schema='native-input-fence-v1', native_request_sha256=observation.sha(observation.canonical(request)))
            event.update(phase='NATIVE_INPUT_INTENT', native_request=request, native_input_fence=fence)
        else:
            fence['schema'] = 'native-paste-fence-v1'
            event.update(phase='PASTE_INTENT', screen='original fixture composer',
                         screen_sha256=observation.sha('original fixture composer')[:16], native_paste_fence=fence)
        self.attempt['events'] = [event]

    def stamp(self, seconds=1):
        return dt.datetime.fromtimestamp(self.at + seconds, dt.timezone.utc).isoformat()

    @staticmethod
    def encoded(value):
        return json.dumps(value, ensure_ascii=False).encode() + b'\n'

    def pin(self):
        raw, st = self.path.read_bytes(), self.path.stat()
        offset = len(raw)
        return dict(path=str(self.path), device=st.st_dev, inode=st.st_ino, offset=offset,
            prefix_size=min(offset, 4096), prefix_sha256=observation.sha(raw[:4096]),
            tail_at=max(0, offset - 4096), tail_sha256=observation.sha(raw[-4096:]),
            at_record_boundary=not raw or raw.endswith(b'\n'))

    def append(self, *records):
        with self.path.open('ab') as stream:
            for record in records:
                stream.write(self.encoded(record))

    def inbound(self, queued=False):
        if self.provider == 'codex':
            return dict(type='response_item', timestamp=self.stamp(), payload=dict(type='message',
                role='user', content=[dict(type='input_text', text=self.notice)]))
        if queued:
            return dict(type='attachment', timestamp=self.stamp(), sessionId=self.session,
                        attachment=dict(type='queued_command', prompt=self.notice))
        return dict(type='user', timestamp=self.stamp(), sessionId=self.session,
                    message=dict(role='user', content=self.notice))

    def pair(self):
        output = json.dumps(dict(schema=SCHEMA, scope=SCOPE, notice=self.notice,
                                reference=self.reference, body=self.full)) + '\n'
        if self.provider == 'codex':
            call = dict(type='response_item', timestamp=self.stamp(2), payload=dict(
                type='function_call', name='exec_command', call_id='read-1',
                arguments=json.dumps(dict(cmd=self.command, login=False))))
            result = dict(type='response_item', timestamp=self.stamp(3), payload=dict(
                type='function_call_output', call_id='read-1',
                output=json.dumps(dict(exit_code=0, output=output))))
        else:
            call = dict(type='assistant', timestamp=self.stamp(2), sessionId=self.session,
                message=dict(role='assistant', content=[dict(type='tool_use', id='read-1',
                    name='Bash', input={'command': self.command})]))
            result = dict(type='user', timestamp=self.stamp(3), sessionId=self.session,
                message=dict(role='user', content=[dict(type='tool_result', tool_use_id='read-1',
                    content=output, is_error=False)]))
        return call, result

    def observe(self, **kwargs):
        return observation.Observer(**kwargs).native(self.attempt, self.notice, self.receiver, command=self.command)

    def materialize_attempt(self):
        intent = dict(self.spec, marker=self.marker, payload=self.notice, payload_sha256=observation.sha(self.notice),
                      original_payload_sha256=observation.sha(self.full), body_reference=self.reference)
        ip = self.state / 'cmux-agent-adapter-v1/targets' / self.channel / 'intents' / (self.marker + '.json')
        ip.parent.mkdir(parents=True)
        ip.write_bytes(self.encoded(intent))
        journal = self.state / 'message-dispatch-v1' / observation.sha(json.dumps(
            [self.sender['surface_uuid'], self.marker], sort_keys=True))
        journal.mkdir(parents=True)
        ap = journal / 'attempt-0001.json'
        ap.write_bytes(self.encoded(self.attempt))
        info = dict(state_root=str(self.state), python={'path': '/fixture/python'},
            rtk={'path': '/fixture/rtk'}, controller={'path': '/fixture/controller'},
            campaign={'sessions': {self.sample['sender']: self.sender, self.sample['receiver']: self.receiver}})
        return ip, ap, info

    def test_both_providers_complete_read_has_exact_raw_pins(self):
        for provider in ('claude', 'codex'):
            with self.subTest(provider=provider):
                self.make(provider)
                self.append(self.inbound(), *self.pair())
                results, scan, original = self.observe()
                self.assertEqual({r['kind'] for r in results}, {'NATIVE_NOTICE_OBSERVED', 'BODY_READ_OBSERVED'})
                self.assertTrue(scan['complete'])
                self.assertEqual(original['at_epoch'], self.at)
                body = next(r for r in results if r['kind'] == 'BODY_READ_OBSERVED')
                for pin in (body['call_record'], body['result_record']):
                    raw = self.path.read_bytes()[pin['offset']:pin['offset'] + pin['length']]
                    self.assertEqual(observation.sha(raw), pin['sha256'])

    def test_queued_and_body_read_are_independent_and_no_reader_exec(self):
        self.append(self.inbound(queued=True), *self.pair())
        _, _, info = self.materialize_attempt()
        with patch('upgrade_body_read.read_result', side_effect=AssertionError('observer must not run reader')):
            row = observation.observe_sample(observation.Observer(), self.sample, info)
        self.assertEqual(row['native_reception'], 'QUEUED_OBSERVED')
        self.assertEqual(row['body_read'], 'BODY_READ_OBSERVED')
        self.assertEqual(row['business_acceptance'], 'NOT_ASSESSED')
        self.assertEqual(row['receiver_runtime'], 'UNKNOWN')

    def test_notice_only_keeps_body_unknown_despite_readable_file(self):
        self.append(self.inbound())
        _, _, info = self.materialize_attempt()
        row = observation.observe_sample(observation.Observer(), self.sample, info)
        self.assertEqual(row['native_reception'], 'NATIVE_NOTICE_OBSERVED')
        self.assertEqual(row['body_read'], 'UNKNOWN')

    def test_changed_wire_suffix_cannot_use_prefix_match(self):
        ip, _, info = self.materialize_attempt()
        value = json.loads(ip.read_text())
        value['payload'] += ' injected suffix'
        value['payload_sha256'] = observation.sha(value['payload'])
        ip.write_bytes(self.encoded(value))
        row = observation.observe_sample(observation.Observer(), self.sample, info)
        self.assertEqual(row['attempt'], 'UNKNOWN')
        self.assertTrue(any('WIRE_CHANGED' in r for r in row['reasons']))

    def test_later_body_beyond_old_256k_prefix_is_found(self):
        self.append(dict(type='progress', payload='x' * (300 * 1024)), *self.pair())
        results, scan, _ = self.observe()
        self.assertTrue(scan['complete'])
        self.assertEqual([r['kind'] for r in results], ['BODY_READ_OBSERVED'])
        self.assertGreater(results[0]['call_record']['offset'], 256 * 1024)

    def test_incomplete_suffix_prevents_body_claim_and_exposes_next_offset(self):
        self.append(self.inbound(), *self.pair())
        with self.path.open('ab') as stream:
            stream.write(b'{"type":"incomplete"')
        results, scan, _ = self.observe()
        self.assertEqual([r['kind'] for r in results], ['NATIVE_NOTICE_OBSERVED'])
        self.assertFalse(scan['complete'])
        self.assertLess(scan['next_offset'], scan['snapshot_eof'])

    def test_scan_and_memory_budgets_never_make_partial_success(self):
        self.append(self.inbound(), *self.pair())
        for kwargs in ({'scan_limit': 25}, {'retain_limit': 1}):
            results, scan, _ = self.observe(**kwargs)
            self.assertNotIn('BODY_READ_OBSERVED', {r['kind'] for r in results})
            self.assertIn('body_read_reason', scan)

    def test_total_and_record_budgets_are_enforced(self):
        self.append(*self.pair())
        with self.assertRaisesRegex(observation.Uncertain, 'BUDGET|METADATA'):
            self.observe(total_native_limit=100)
        results, scan, _ = self.observe(record_limit=50)
        self.assertEqual(results, [])
        self.assertEqual(scan['reason'], 'NATIVE_RECORD_BUDGET_EXCEEDED')

    def test_invalid_budget_boolean_and_unbounded_values_refused(self):
        for name, value in (('scan_limit', True), ('record_limit', 0),
                            ('total_native_limit', 2 ** 40), ('retain_limit', -1)):
            with self.assertRaisesRegex(observation.Uncertain, 'BUDGET_INVALID'):
                observation.Observer(**{name: value})

    def test_wrong_session_metadata_rejected_even_with_matching_filename_and_fence(self):
        self.make('codex')
        wrong = self.path.read_bytes().replace(self.session.encode(), str(uuid.uuid4()).encode())
        self.path.write_bytes(wrong)
        # Repin fixture's original fence to isolate the metadata check.
        pin = self.pin()
        self.binding['transcript'] = pin
        self.receiver['transcript'] = pin
        event = self.attempt['events'][0]
        event['native_input_fence']['transcript'] = pin
        event['native_input_fence']['binding_sha256'] = observation.sha(json.dumps(self.binding, sort_keys=True))
        self.append(*self.pair())
        with self.assertRaisesRegex(observation.Uncertain, 'SESSION_METADATA_CHANGED'):
            self.observe()

    def test_changed_inode_or_original_fence_is_rejected(self):
        original = self.path.read_bytes()
        self.path.rename(self.path.with_suffix('.old'))
        self.path.write_bytes(original)
        with self.assertRaisesRegex(observation.Uncertain, 'IDENTITY_CHANGED'):
            self.observe()

    def test_original_input_required_no_retroactive_binding(self):
        self.append(*self.pair())
        self.attempt['events'] = []
        with self.assertRaisesRegex(observation.Uncertain, 'ONE_ORIGINAL_INPUT'):
            self.observe()

    def test_two_input_intents_never_merge(self):
        self.attempt['events'].append(copy.deepcopy(self.attempt['events'][0]))
        with self.assertRaisesRegex(observation.Uncertain, 'ONE_ORIGINAL_INPUT'):
            self.observe()

    def test_duplicate_call_wrong_session_sidechain_future_and_truncated_result_unknown(self):
        for corruption in ('duplicate', 'session', 'sidechain', 'future', 'truncated', 'command', 'error'):
            with self.subTest(corruption=corruption):
                self.make('claude')
                call, result = self.pair()
                if corruption == 'duplicate':
                    self.append(copy.deepcopy(call))
                elif corruption == 'session':
                    result['sessionId'] = str(uuid.uuid4())
                elif corruption == 'sidechain':
                    result['isSidechain'] = True
                elif corruption == 'future':
                    result['timestamp'] = self.stamp(3600)
                elif corruption == 'truncated':
                    result['message']['content'][0]['content'] = '{"schema":'
                elif corruption == 'command':
                    call['message']['content'][0]['input']['command'] = 'echo ' + self.command
                elif corruption == 'error':
                    result['message']['content'][0]['is_error'] = True
                self.append(call, result)
                self.assertEqual(self.observe()[0], [])

    def test_saved_notice_proof_beyond_scan_budget_does_not_grant_body_read(self):
        self.append(dict(type='progress', payload='x' * 1000))
        offset = self.path.stat().st_size
        raw = self.encoded(self.inbound())
        self.append(self.inbound(), *self.pair())
        pin = self.binding['transcript']
        proof = dict(path=str(self.path), device=pin['device'], inode=pin['inode'],
            offset=offset, length=len(raw), sha256=observation.sha(raw), session_id=self.session,
            payload_sha256=observation.sha(self.notice))
        results, scan, _ = observation.Observer(scan_limit=20).native(
            self.attempt, self.notice, self.receiver, [proof], command=self.command)
        self.assertEqual([r['kind'] for r in results], ['NATIVE_NOTICE_OBSERVED'])
        self.assertFalse(scan['complete'])
        proof['sha256'] = '0' * 64
        with self.assertRaisesRegex(observation.Uncertain, 'PROOF_HASH_CHANGED'):
            observation.Observer().native(self.attempt, self.notice, self.receiver, [proof])

    def test_guard_blocks_external_mutations_network_and_processes(self):
        output = self.root / 'output'
        for event, args in (('subprocess.Popen', ()), ('socket.connect', ()), ('os.system', ()),
                            ('open', (str(self.path), 'w', os.O_WRONLY)),
                            ('os.rename', (str(output / 'a'), str(self.path)))):
            with self.assertRaises(PermissionError):
                observation.write_guard(output, event, args)
        observation.write_guard(output, 'open', (str(self.path), 'r', os.O_RDONLY))
        observation.write_guard(output, 'open', (str(output / 'result.json'), 'x', os.O_CREAT | os.O_WRONLY))

    def test_raw_symlink_refused(self):
        link = self.root / 'alias.jsonl'
        link.symlink_to(self.path)
        with self.assertRaisesRegex(observation.Uncertain, 'SYMLINK'):
            observation.raw_file(link)

    def test_malformed_duplicate_key_record_keeps_body_unknown(self):
        self.append(*self.pair())
        with self.path.open('ab') as stream:
            stream.write(b'{"type":"assistant","type":"user"}\n')
        results, scan, _ = self.observe()
        self.assertEqual(results, [])
        self.assertFalse(scan['tool_record_retention_complete'])

    def test_metadata_duplicate_json_keys_and_nonfinite_rejected(self):
        path = self.root / 'bad.json'
        for raw in ('{"a":1,"a":2}', '{"a":NaN}'):
            path.write_text(raw)
            with self.assertRaises(ValueError):
                observation.json_file(path)

    def test_absent_campaigns_retain_all_192_without_model_calls(self):
        groups, pairs = layout(self.root)
        registration = dict(schema='upgrade-paired-registration-v1', benchmark_id='observer-offline',
            root=str(self.root), created_at=self.stamp(), contract=CONTRACT, groups=groups, pairs=pairs)
        rp = write_json(self.root / 'registration.json', registration)
        pp = write_json(self.root / 'plan.json', registration_plan(registration))
        candidate = dict(root=str(self.root), registration=rp, communication_plan=pp,
            groups={g: dict(path=str(self.root / ('missing-' + g)), bytes=1, sha256='a' * 64) for g in groups})
        cp = write_json(self.root / 'candidate.json', candidate)
        result = observation.collect(cp['path'])
        self.assertEqual(result['schema'], observation.SCHEMA)
        self.assertEqual(result['total_samples'], 192)
        self.assertEqual(result['counts']['attempt'], {'UNKNOWN': 192})
        for field in ('input_operations', 'runtime_writes', 'canonical_receipts_written', 'model_calls'):
            self.assertEqual(result[field], 0)

    def test_prepare_group_uses_pinned_python_rtk_reader_and_rejects_reader_change(self):
        controller = self.root / 'controller'
        (controller / 'scripts').mkdir(parents=True)
        reader = controller / 'scripts/mac_upgrade_body_read.py'
        reader.write_text('# offline fixture only\n')
        helper = self.root / 'helper'
        helper.write_text('# offline helper never executed\n')
        python = file_ref(Path(sys.executable).resolve())
        rtk = file_ref(Path('/bin/cat').resolve())
        startup = dict(controller=str(controller), pins={'helper': file_ref(helper), 'python': python, 'rtk': rtk})
        startup_ref = write_json(self.root / 'startup.json', startup)
        context = dict(paths={'state': str(self.root / 'states'), 'campaigns': str(self.root / 'campaigns')}, python=python)
        context_ref = write_json(self.root / 'context.json', context)
        runtime = dict(state_root=str(self.root / 'states/B'), python=python, startup=startup_ref,
            controller=dict(path=str(controller), files={'scripts/mac_upgrade_body_read.py': file_ref(reader)}))
        runtime_ref = write_json(self.root / 'runtime.json', runtime)
        config_ref = write_json(self.root / 'config.json', dict(context=context_ref, runtime=runtime_ref))
        candidate = dict(root=str(self.root), groups={'g': config_ref})
        registration = dict(groups={'g': dict(arm='B', context_path=context_ref['path'], campaign_id='one')})
        info = observation.prepare_group(candidate, registration, 'g')
        self.assertNotIn('error', info)
        self.assertEqual(info['rtk']['path'], rtk['path'])
        self.assertEqual(info['python']['path'], python['path'])
        reader.write_text('# changed after startup\n')
        self.assertIn('READER_PIN_CHANGED', observation.prepare_group(candidate, registration, 'g')['error'])


if __name__ == '__main__':
    unittest.main()
