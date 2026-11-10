"""Real worker, body files, operation ledger and native reader joins.

Model/RPC and sender arrival are explicit fixtures; no real CLI or model call.
The existing semantic attribution suite separately exercises original sends.
"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, digest, file_ref, json_ref, parse_time, utc_now, write_json
from upgrade_task_campaign import KIND, operation_payload
from upgrade_task_semantic import STATEMENT_SCHEMA, declaration, disposition_pair, typed_read_evidence
import test_upgrade_task_advice as advice_fixture


class SemanticTests(unittest.TestCase):
    setUp = advice_fixture.AdviceTests.setUp
    enqueue = advice_fixture.AdviceTests.enqueue
    call = advice_fixture.AdviceTests.call
    complete = advice_fixture.AdviceTests.complete
    native_output = advice_fixture.AdviceTests.native_output

    def transport(self, request):
        self.calls.append(copy.deepcopy(request))
        if self.failure:raise self.failure
        if hasattr(self, 'priority_index'):
            selected = self.batch['messages'][self.priority_index]['body']
            return dict(answers=dict(message_kind='request',
                message_actionable=0.9 if request['state']==selected else 0.1), http_status=200)
        return copy.deepcopy(self.reply)

    def extend_operations(self, operations, slots):
        from cmux_prompt_reference import persist, plan
        self.samples = []
        messages = []
        self.c.campaign.update(semantic_input=write_json(self.control/'input.json', {'explicit_fixture': True}),
            registration=write_json(self.control/'paired.json', {'explicit_fixture': True}), session_group='fixture')
        for i, body in enumerate(('核对原始来源。', '保留既定写域。', '复查依赖，然后合并。')):
            rid = f'semantic-fixture-{i}'
            self.samples.append(dict(run_id='run-01', request_id=rid, body_sha256=digest(body.encode()), position=i))
            marker = 'SEMANTIC_FIXTURE_' + str(i)
            full = '[CMUX-AGENT][delivery:' + marker + ']\n' + body
            wire = plan(full, marker, root=self.control/'bodies')
            persist(wire['reference'], full)
            intent = write_json(self.control/f'intent-{i}.json', {'payload': wire['text']})
            messages.append(dict(id=rid, body=body, sha256=digest(body.encode()), bytes=len(body.encode()),
                                 eligible=True, source={'intent': intent}))
        self.batch = dict(schema='s2-semantic-native-batch-v1', ready=True,
                          attribution={'explicit_sender_arrival_fixture': True}, messages=messages)
        for target, value in (
            ('upgrade_task_semantic.samples', self.samples),
            ('upgrade_semantic_inputs.campaign_slots', self.samples),
            ('upgrade_semantic_inputs.native_batch', self.batch),
            ('upgrade_benchmark.validate_registration', {'groups': {'fixture': {'advisory': 'advisory-on' if self.on else 'advisory-off'}}}),
        ):
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        def add(name, action, **kwargs):
            operations[name] = dict(kind=KIND, payload=operation_payload(self.c.campaign, action,
                'run-01', node_id='unbound', **kwargs))
        add('semantic-status', 'semantic-status', observation='original-01')
        add('semantic-order', 'semantic-order')
        for i, s in enumerate(self.samples):
            add(f'semantic-read-{i}', 'semantic-read', semantic_request=s['request_id'])
            add(f'semantic-dispose-{i}', 'semantic-disposition', semantic_request=s['request_id'])
        # DP2's fake provider returns only its own semantic priority output.
        self.reply = dict(answers={'message_kind': 'request', 'message_actionable': 0.9}, http_status=200)

    def ordered(self):
        for message in self.batch['messages']:
            notice = json_ref(message['source']['intent'])['payload']
            message['source']['native_record'] = self.native.append(self.native.message('user', notice, at=utc_now()))
        self.call('semantic-status'); self.complete('semantic-status')
        status = json.loads(self.sent[-1]['result']['contentItems'][0]['text'])
        self.assertTrue(status['ready'])
        self.assertNotIn('messages', status)
        self.assertTrue(all(m['body'] not in json.dumps(status, ensure_ascii=False) for m in self.batch['messages']))
        self.call('semantic-order'); self.complete('semantic-order')
        return json_ref(file_ref(self.c.root/'runs/run-01/jev-inbox/semantic-run-01.json'))

    def statement(self, i, *, role='assistant', work=None, reason='我已完整读取；先保留这条建议，待原节点处理。'):
        value = dict(schema=STATEMENT_SCHEMA, run_id='run-01', request_id=self.samples[i]['request_id'],
                     inner_body_sha256=self.samples[i]['body_sha256'], disposition='defer', reason=reason, work=work or [])
        self.native.append(dict(timestamp=utc_now(), type='response_item',
            payload=dict(type='message', role=role, content=[dict(type='output_text', text=json.dumps(value, ensure_ascii=False))])))
        return value

    def read(self, i):
        self.call(f'semantic-read-{i}'); self.complete(f'semantic-read-{i}')

    def test_off_actual_complete_reads_model_reasons_and_native_disposition_join(self):
        ordered = self.ordered()
        self.assertEqual(ordered['order']['order'], [s['request_id'] for s in self.samples])
        self.assertEqual(ordered['order']['model_requests'], 0)
        self.assertFalse(self.calls)
        co = self.runner.co('run-01')
        from orchestration_native import NativeReader
        reader = NativeReader(self.c.campaign['sessions']['codex'], fixtures=True)
        for i, sample in enumerate(self.samples):
            self.read(i)
            request = ordered['read_requests'][i]
            proof = typed_read_evidence(reader, fence=co.header['native_start']['codex'],
                until=reader.snapshot(complete_records=True)['offset'], notice=request['notice'], command=request['command'],
                not_before=0, not_after=parse_time(utc_now()).timestamp(), request_id=sample['request_id'],
                body_sha256=sample['body_sha256'])[0]
            expected = self.statement(i)
            self.call(f'semantic-dispose-{i}'); self.complete(f'semantic-dispose-{i}')
            result = json.loads(self.sent[-1]['result']['contentItems'][0]['text'])
            self.assertEqual(json_ref(result['disposition'])['reason'], expected['reason'])
            joined = disposition_pair(co, reader, 'run-01', sample['request_id'], result['disposition'], proof,
                until=reader.snapshot(complete_records=True)['offset'], not_after=parse_time(utc_now()).timestamp())
            self.assertGreater(joined['call_record']['offset'], proof['result_record']['offset'])
        self.assertFalse(self.calls)
        from upgrade_semantic_inputs import semantic_closeout
        readers = {lane: client.reader for lane, client in self.c.clients.items()}
        ends = {lane: r.snapshot(complete_records=True) for lane, r in readers.items()}
        final = semantic_closeout(co, {}, readers, ends, not_after=utc_now())
        self.assertEqual(final['qualified'], 3)
        self.assertTrue(final['read_order']['matches_advice'])
        self.assertEqual(final['semantic_correctness'], 'NOT_JUDGED')

    def test_on_same_original_connection_budget_and_order_is_used(self):
        self.priority_index = 1
        ordered = self.ordered()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(ordered['order']['model_requests'], 3)
        self.assertEqual(set(ordered['order']['order']), {s['request_id'] for s in self.samples})
        self.assertEqual(ordered['registration'], self.jev)
        self.assertEqual(ordered['order']['order'], [self.samples[i]['request_id'] for i in (1,0,2)])
        for i in (1,0,2):
            self.read(i); self.statement(i)
            self.call(f'semantic-dispose-{i}'); self.complete(f'semantic-dispose-{i}')
        from upgrade_semantic_inputs import semantic_closeout
        readers={lane:c.reader for lane,c in self.c.clients.items()}
        final=semantic_closeout(self.runner.co('run-01'), {}, readers,
            {lane:r.snapshot(complete_records=True) for lane,r in readers.items()},not_after=utc_now())
        self.assertEqual(final['qualified'],3)
        self.assertTrue(final['read_order']['matches_advice'])
        self.assertEqual(len(self.calls),3)

    def test_on_auth_stop_clears_original_held_capability(self):
        self.reply={'http_status':403}
        with self.assertRaisesRegex(ContractError,'STOP_AFFECTED'):
            self.call('semantic-order')
        self.assertEqual(len(self.calls),1)
        self.assertIsNone(self.held['advice']['connection'])
        self.assertIsNone(self.held['advice']['credential'])

    def test_off_partial_read_closeout_keeps_unread_messages_unqualified(self):
        self.ordered(); self.read(0); self.statement(0)
        self.call('semantic-dispose-0'); self.complete('semantic-dispose-0')
        from upgrade_semantic_inputs import semantic_closeout
        readers={lane:c.reader for lane,c in self.c.clients.items()}
        final=semantic_closeout(self.runner.co('run-01'), {}, readers,
            {lane:r.snapshot(complete_records=True) for lane,r in readers.items()},not_after=utc_now())
        self.assertEqual(final['qualified'],1)
        self.assertEqual(final['unqualified'],[s['request_id'] for s in self.samples[1:]])

    def test_off_missing_or_user_echo_disposition_is_not_model_decision(self):
        self.ordered(); self.read(0); self.statement(0, role='user')
        with self.assertRaisesRegex(ContractError, 'ONE_MODEL_DECLARATION'):
            self.call('semantic-dispose-0')
        self.assertFalse((self.c.root/'runs/run-01/jev-inbox/dispositions').exists())
        self.assertEqual(self.ledger.inspect('inner-semantic-dispose-0')['phase'], 'UNKNOWN')

    def test_off_duplicate_model_statement_refuses_without_rewriting(self):
        self.ordered(); self.read(0); self.statement(0); self.statement(0)
        with self.assertRaisesRegex(ContractError, 'ONE_MODEL_DECLARATION'):
            self.call('semantic-dispose-0')
        self.assertFalse((self.c.root/'runs/run-01/jev-inbox/dispositions').exists())

    def test_off_unread_preceding_message_cannot_be_skipped(self):
        self.ordered()
        with self.assertRaises(ContractError): self.call('semantic-read-1')
        self.assertFalse((self.control/'campaign-results/semantic-read-1.json').exists())

    def test_off_body_output_truncation_cannot_be_disposed(self):
        self.ordered(); self.call('semantic-read-0')
        self.complete('semantic-read-0', change=lambda text: text[:-8])
        self.statement(0)
        with self.assertRaises(ContractError): self.call('semantic-dispose-0')
        self.assertFalse((self.c.root/'runs/run-01/jev-inbox/dispositions').exists())

    def test_off_early_statement_or_foreign_work_cannot_change_scope(self):
        self.ordered(); self.statement(0); self.read(0)
        with self.assertRaisesRegex(ContractError, 'ONE_MODEL_DECLARATION'):
            self.call('semantic-dispose-0')

    def test_off_new_work_node_is_refused(self):
        self.ordered(); self.read(0); self.statement(0, work=['unregistered-new-task'])
        with self.assertRaisesRegex(ContractError, 'DECLARATION_INVALID'):
            self.call('semantic-dispose-0')

    def test_pending_original_arrivals_do_not_call_the_advisor(self):
        self.batch['ready'] = False
        with self.assertRaisesRegex(ContractError, 'pending or UNKNOWN'):
            self.call('semantic-order')
        self.assertFalse(self.calls)
        self.assertTrue(self.actor.closed)


class FullCatalogTests(unittest.TestCase):
    def test_seven_original_groups_preserve_all_99_slots_and_fit_native_instructions(self):
        import test_root_s2_semantic_inputs as fixture
        from orchestration_freeze_v3 import registered_corpus_plan
        from upgrade_task_catalog import catalog
        from upgrade_task_startup import registration_for_plan, BINDING_PLAN_SCHEMA, validate_plan_operations
        from upgrade_task_campaign import ADVICE_SCHEMA, validate_controls
        from upgrade_task_instructions import render, compact_catalog
        from upgrade_rpc_peer import SERVER_KEYS
        from jev_registration import SCHEMA, EXCEPTION
        f = fixture.SemanticRegistrationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        paired_ref = f.register(); paired = json_ref(paired_ref)
        transport = f.base/'transport.py'; transport.write_text('def bind(*a, **k): pass\n')
        count = 0
        ordinary_count = {'communication-send': 0, 'communication-read': 0}
        for group_id, group in paired['groups'].items():
            root = f.base/group_id
            plan = registered_corpus_plan(root/'inputs', 'fixture', group['workload_class'], paired)
            thread = '11111111-0000-4000-8000-000000000001'
            campaign = dict(version=3, campaign_id='original-' + group_id, root=str(root), arm='B',
                registration=paired_ref, session_group=group_id, runs=group['runs'],
                semantic_input=f.ref, plan=write_json(root/'plan.json', plan),
                sessions={lane:dict(provider='codex' if lane=='codex' else 'claude',
                    session_id=thread, surface_uuid=lane) for lane in ('codex','claude_a','claude_b')})
            advice, decisions = {}, {}
            for run in group['runs']:
                for node in plan['nodes']:
                    if node['executor'] is not None:continue
                    endpoint = run['run_id'], node['id']
                    value = dict(schema=SCHEMA, run_id=endpoint[0], node_id=endpoint[1], arm='advisory-on',
                        provider='fixture', model='fixture', budget={'max_model_requests': 200},
                        external_advice_exception=EXCEPTION, transport_source=file_ref(transport))
                    value['authorization'] = write_json(root/('auth-'+'-'.join(endpoint)+'.json'), value)
                    advice[endpoint] = write_json(root/('advice-'+'-'.join(endpoint)+'.json'), value) if group['advisory']=='advisory-on' else None
                    decisions[endpoint] = [dict(work_kind='coding', actual_route=lane, action='supervisor_review',
                        reason='Model chooses this original available route after reading task facts.')
                        for lane in ('codex','claude_a','claude_b')]
            directory=root/'control/task-startup'
            compiled = catalog(campaign, directory, advice_registrations=advice, decision_options=decisions)
            task_plan = dict(schema=BINDING_PLAN_SCHEMA, task_id='task', controller_id='controller',
                authority_ref=write_json(root/'authority.json', {'fixture':True}), **compiled)
            validate_plan_operations(task_plan)
            reg = registration_for_plan(task_plan, thread, '22222222-0000-4000-8000-000000000002',
                {k:'fixture' for k in SERVER_KEYS})
            campaign['task_controls'] = dict(schema=ADVICE_SCHEMA,
                registration=write_json(directory/'operations.json', reg),
                evidence_directory=str(directory/'campaign-invocations'))
            _, operations = validate_controls(campaign, check_startup=False)
            reads = [p for p in operations.values() if p['action']=='semantic-read']
            disposals = [p for p in operations.values() if p['action']=='semantic-disposition']
            self.assertEqual(len(reads),3*len(group['runs']))
            self.assertEqual({p['semantic_request'] for p in reads}, {p['semantic_request'] for p in disposals})
            count += len(reads)
            for action in ordinary_count:
                selected = [p for p in operations.values() if p['action'] == action]
                ordinary_count[action] += len(selected)
                self.assertEqual(len(selected), 0 if group_id.startswith('pilot') else 16)
            ref = write_json(root/'campaign.json', campaign)
            compact = compact_catalog(reg, list(operations))
            for entry in compact['entries']:
                restored = dict(kind=entry['operation']['kind'], payload=dict(
                    compact['shared_payload_fields'], **entry['operation']['payload']))
                self.assertEqual(restored, reg['operations'][entry['operation_id']])
            text = render(ref, reg, list(operations))
            self.assertLessEqual(len(text.encode()),256*1024)
            for p in reads:self.assertIn(p['semantic_request'],text)
            bad = copy.deepcopy(reg)
            del bad['operations'][next(k for k,v in bad['operations'].items() if v['payload'].get('action')=='semantic-read')]
            changed = copy.deepcopy(campaign)
            changed['task_controls']['registration'] = write_json(directory/'missing-read.json',bad)
            with self.assertRaisesRegex(ContractError,'ONE_ORIGINAL_OPERATION'):
                validate_controls(changed,check_startup=False)
        self.assertEqual(count,99)
        self.assertEqual(ordinary_count, {'communication-send':96, 'communication-read':96})


if __name__ == '__main__':
    unittest.main()
