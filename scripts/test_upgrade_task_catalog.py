"""Catalog compiler against real campaign/startup validators; no live clients."""
import copy
from pathlib import Path
import tempfile
import unittest

from orchestration_common import ContractError, file_ref, json_ref, write_json
from orchestration_freeze_v3 import corpus_plan
from upgrade_task_catalog import catalog, startup_options
from upgrade_task_campaign import validate_controls, BINDING_SCHEMA, ADVICE_SCHEMA
from upgrade_task_startup import registration_for_plan, BINDING_PLAN_SCHEMA
from upgrade_rpc_peer import SERVER_KEYS


class CatalogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.task = self.root/'control/task-startup'
        self.campaign = dict(version=3, campaign_id='original-campaign', root=str(self.root/'campaign'),
            arm='B', runs=[dict(run_id='run-01'), dict(run_id='run-02')],
            sessions={'codex': {'session_id': '11111111-0000-4000-8000-000000000001'}},
            plan=write_json(self.root/'plan.json', corpus_plan(self.root/'inputs', 'fixed', 'uneven_successor')))
        self.authority = write_json(self.root/'authority.json', {'fixture': 'original authority'})

    def validate(self, value, *, advice=False):
        plan = dict(schema=BINDING_PLAN_SCHEMA, task_id='task', controller_id='controller',
                    authority_ref=self.authority, **value)
        reg = registration_for_plan(plan, self.campaign['sessions']['codex']['session_id'],
            '22222222-0000-4000-8000-000000000002', {k: 'fixture' for k in SERVER_KEYS})
        ref = write_json(self.task/'operations.json', reg)
        campaign = dict(self.campaign, task_controls=dict(
            schema=ADVICE_SCHEMA if advice else BINDING_SCHEMA, registration=ref,
            evidence_directory=str(self.task/'campaign-invocations')))
        actual, operations = validate_controls(campaign, check_startup=False)
        self.assertEqual(actual, reg)
        return operations

    def test_every_original_run_has_local_and_dual_claude_paths_and_acceptance(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        original = copy.deepcopy(self.campaign)
        value = catalog(self.campaign, self.task, observations=2)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertEqual(original, self.campaign)
        operations = self.validate(value)
        self.assertEqual(value['initial_operations'], ['ready'])
        nodes = json_ref(self.campaign['plan'])['nodes']
        for run in ('run-01', 'run-02'):
            for node in nodes:
                actions = {p['action'] for p in operations.values()
                           if p['run_id'] == run and p['node_id'] == node['id']}
                self.assertTrue({'claim', 'candidate', 'accept'} <= actions)
                if node['executor'] == 'codex':
                    self.assertIn('local-execute', actions); self.assertNotIn('dispatch', actions)
                else:
                    self.assertTrue({'prepare', 'dispatch', 'callback', 'disarm', 'reconcile'} <= actions)
                    self.assertNotIn('local-execute', actions)
                accept = f"{run}:{node['id']}:accept"
                self.assertIsNone(value['operations'][accept]['payload']['marker'])
                slot = value['binding_slots'][accept]
                self.assertEqual(slot['producer_operation'], f"{run}:{node['id']}:candidate")
                self.assertEqual(slot['source_path'],
                    str(Path(self.campaign['root'])/'runs'/run/'nodes'/node['id']/'candidate.json'))

    def unbound(self, on):
        plan = json_ref(self.campaign['plan'])
        node = plan['nodes'][0]; node['executor'] = None
        self.campaign['plan'] = write_json(self.root/'unbound.json', plan)
        from jev_registration import SCHEMA, EXCEPTION
        transport = self.root/'transport.py'; transport.write_text('def bind(*a, **k): pass\n')
        advice, decisions = {}, {}
        for run in ('run-01', 'run-02'):
            endpoint = run, node['id']
            value = dict(schema=SCHEMA, run_id=run, node_id=node['id'], arm='advisory-on',
                provider='fixture', model='fixture-model', budget={'max_model_requests': 3},
                external_advice_exception=EXCEPTION, transport_source=file_ref(transport))
            value['authorization'] = write_json(self.root/(run+'-advice-authority.json'), value)
            advice[endpoint] = write_json(self.root/(run+'-advice.json'), value) if on else None
            decisions[endpoint] = [dict(work_kind='coding', actual_route=lane, action='supervisor_review',
                reason='Original model-visible alternative') for lane in ('codex', 'claude_a', 'claude_b')]
        return advice, decisions

    def test_on_and_off_preserve_all_choices_and_original_advice_read_sources(self):
        for on in (True, False):
            with self.subTest(on=on):
                advice, decisions = self.unbound(on)
                value = catalog(self.campaign, self.task, observations=1,
                    advice_registrations=advice, decision_options=decisions)
                operations = self.validate(value, advice=True)
                for run in ('run-01', 'run-02'):
                    names = value['choice_groups'][run+':inventory:decision']
                    self.assertEqual({operations[n]['decision_fields']['actual_route'] for n in names},
                                     {'codex', 'claude_a', 'claude_b'})
                    reader = run+':inventory:read-advice'
                    self.assertEqual(reader in operations, on)
                    for name in names:
                        if on:
                            self.assertIsNone(operations[name]['decision_fields']['advice_sha256'])
                            self.assertEqual(value['binding_slots'][name]['source_path'],
                                str(self.task/'campaign-results'/(reader+'.json')))
                        else:
                            self.assertNotIn(name, value['binding_slots'])

    def test_unknown_scope_duplicate_choices_and_invalid_budget_fail_before_writes(self):
        advice, choices = self.unbound(True)
        with self.assertRaisesRegex(ContractError, 'UNBOUND_SCOPE'):
            catalog(self.campaign, self.task)
        duplicated = copy.deepcopy(choices)
        duplicated[('run-01', 'inventory')].append(duplicated[('run-01', 'inventory')][0])
        with self.assertRaisesRegex(ContractError, 'DUPLICATE_PAYLOAD'):
            catalog(self.campaign, self.task, advice_registrations=advice, decision_options=duplicated)
        with self.assertRaisesRegex(ContractError, 'OBSERVATION_BUDGET'):
            catalog(self.campaign, self.task, observations=0)
        self.assertFalse(self.task.exists())

    def test_explicit_failure_slots_and_materialization_options_share_original_budget(self):
        failure = {('run-01', 'source_index'):
                   dict(reason='Original preparation exited without business input', boundary='preparation_exited')}
        value = startup_options(self.campaign, self.task, task_id='task', controller_id='controller',
            authority_ref=self.authority, startup_text='Run only ready, read the receipt, then end normally.',
            base_instructions='Use only the registered operation IDs.', failures=failure)
        self.validate({k:value[k] for k in ('operations', 'initial_operations', 'choice_groups', 'binding_slots')})
        self.assertEqual(value['binding_slots']['run-01:source_index:settle']['producer_operation'],
                         'run-01:source_index:failure-candidate')
        self.assertNotIn('run-02:source_index:settle', value['operations'])


if __name__ == '__main__':
    unittest.main()
