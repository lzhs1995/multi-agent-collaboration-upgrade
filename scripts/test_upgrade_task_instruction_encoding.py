"""Complete route options survive compact model instructions without new calls."""
import copy
import itertools
import json
import unittest

from orchestration_common import ContractError
from upgrade_task_control import invocation_script
from upgrade_task_instructions import grouped_catalog, expand_grouped_catalog, remaining_operations


class EncodingTests(unittest.TestCase):
    def registration(self):
        operations, slots, choices = {}, {}, {}
        for run, node in itertools.product(('cold-01', 'warm-01'), ('inventory', 'source_index')):
            members = []
            for index, (kind, lane, action) in enumerate(itertools.product(
                    ('coding', 'debugging', 'review', 'literature'),
                    ('codex', 'claude_a', 'claude_b'),
                    ('accepted', 'rejected', 'supervisor_review'))):
                name = f'{run}:{node}:publish-decision:choice-{index+1:03d}'
                members.append(name)
                operations[name] = dict(kind='campaign-control', payload=dict(
                    run_id=run, node_id=node, action='publish-decision',
                    decision_fields=dict(work_kind=kind, actual_route=lane, action=action,
                        reason='Original explanation: "quoted", 汉字, and \\ escapes.', advice_sha256=None)))
                slots[name] = dict(source_path=f'/original/{run}/{node}/read.json',
                    producer_operation=f'{run}:{node}:read-advice', nested={'empty': {}})
            choices[f'{run}:{node}:decision'] = members
        operations['status'] = dict(kind='campaign-control', payload=dict(
            run_id='cold-01', node_id=None, action='status', values=[None, {}, 'literal']))
        return dict(operations=operations, binding_slots=slots, choice_groups=choices)

    def test_every_option_slot_and_exact_script_round_trips_through_json(self):
        reg = self.registration(); before = copy.deepcopy(reg)
        encoded = json.loads(json.dumps(grouped_catalog(reg, sorted(reg['operations']))))
        decoded = expand_grouped_catalog(encoded)
        self.assertEqual(set(decoded), set(reg['operations']))
        for name, op in reg['operations'].items():
            expected = dict(operation=op, exact_exec_script=invocation_script(name))
            if name in reg['binding_slots']: expected['binding_slot'] = reg['binding_slots'][name]
            self.assertEqual(decoded[name], expected)
        self.assertEqual(reg, before)

    def test_consumed_choice_cannot_reappear_in_compact_remaining_scope(self):
        reg = self.registration(); used = [next(iter(reg['choice_groups'].values()))[0]]
        ids = remaining_operations(reg, reg['operations'], used)
        decoded = expand_grouped_catalog(grouped_catalog(reg, ids))
        self.assertEqual(set(decoded), set(ids))
        self.assertFalse(set(next(iter(reg['choice_groups'].values()))) & set(decoded))
        self.assertIn('status', decoded)

    def test_json_scalar_and_array_types_are_not_coalesced_by_python_equality(self):
        reg = dict(operations={str(i):dict(kind='campaign-control', payload=dict(
            action='status', value=value)) for i, value in enumerate((False, 0, 1, 1.0, [False], [0]))})
        restored = expand_grouped_catalog(grouped_catalog(reg, list(reg['operations'])))
        for name, operation in reg['operations'].items():
            self.assertEqual(json.dumps(restored[name]['operation'], sort_keys=True),
                             json.dumps(operation, sort_keys=True))

    def test_changed_script_or_duplicate_id_is_refused(self):
        catalog = grouped_catalog(self.registration(), ['status'])
        changed = copy.deepcopy(catalog); changed['exact_exec_script']['suffix'] += 'unexpected();'
        with self.assertRaisesRegex(ContractError, 'SCRIPT_CHANGED'): expand_grouped_catalog(changed)
        duplicate = copy.deepcopy(catalog)
        duplicate['groups'][0]['entries'] *= 2
        with self.assertRaisesRegex(ContractError, 'DUPLICATE_OPERATION'): expand_grouped_catalog(duplicate)

    def test_shared_field_reference_cannot_hide_a_missing_or_ambiguous_payload(self):
        reg = self.registration()
        original = grouped_catalog(reg, sorted(reg['operations']))
        for index in (-1, True, len(original['field_sets'])):
            bad = copy.deepcopy(original)
            entry = next(e for g in bad['groups'] for e in g['entries'] if 'fields_ref' in e)
            entry['fields_ref'] = index
            with self.assertRaisesRegex(ContractError, 'FIELD_REFERENCE_INVALID'):
                expand_grouped_catalog(bad)
        bad = copy.deepcopy(original)
        entry = next(e for g in bad['groups'] for e in g['entries'] if 'fields_ref' in e)
        entry['fields'] = {}
        with self.assertRaisesRegex(ContractError, 'FIELD_SOURCE_AMBIGUOUS'):
            expand_grouped_catalog(bad)


if __name__ == '__main__':
    unittest.main()
