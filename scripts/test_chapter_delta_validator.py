import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chapter_delta_validator import DELTA_FIELDS, load_json, main, validate


def fixture(decision='ACCEPTED', chapter_id='chapter_A'):
    ref = {'path': 'evidence.txt', 'sha256': hashlib.sha256(b'original evidence').hexdigest()}
    delta = {'chapter_id': chapter_id, 'acceptance_scope': 'definitions/evidence package only',
             'task_id': 'original-chapter-task', 'owner_actor_id': 'original-supervisor',
             'artifact_pins': [copy.deepcopy(ref)], 'source_evidence_pins': [copy.deepcopy(ref)],
             'terminology_and_method_dependencies': [], 'checks_performed': [],
             'scientific_limits': ['No new scientific inference'], 'unresolved_items': [],
             'review': {'baseline_pin': copy.deepcopy(ref), 'rounds_used': 1,
                        'findings_and_dispositions': [], 'acceptance_status': decision},
             'resource_state': [], 'next_action': None}
    return {'schema': 'nested-research-collaboration-v1', 'template_only': False,
            'status': 'PRIVATE_HANDOFF', 'chapters': [{'chapter_id': chapter_id, 'accepted_delta_interface': delta}]}


class ChapterDeltaTests(unittest.TestCase):
    def test_accepted_partial_rejected_decisions_are_not_conflated(self):
        for decision in ('ACCEPTED', 'PARTIAL', 'REJECTED'):
            with self.subTest(decision=decision):
                value = validate(fixture(decision), require_accepted=True)
                self.assertTrue(value['valid'])
                self.assertEqual(value['requested_gate_satisfied'], decision == 'ACCEPTED')
                self.assertEqual(value['decisions'][0]['recorded_decision'], decision)

    def test_every_existing_field_is_required(self):
        for field in DELTA_FIELDS:
            with self.subTest(field=field):
                doc = fixture()
                del doc['chapters'][0]['accepted_delta_interface'][field]
                self.assertFalse(validate(doc)['valid'])

    def test_empty_scope_and_implicit_status_fail(self):
        for scope in (None, '', '   '):
            doc = fixture()
            doc['chapters'][0]['accepted_delta_interface']['acceptance_scope'] = scope
            self.assertFalse(validate(doc)['valid'])
        for decision in ('UNVERIFIED', None):
            self.assertFalse(validate(fixture(decision))['valid'])

    def test_domain_specific_decision_is_preserved_without_universal_enum(self):
        value = validate(fixture('ACCEPTED_AS_BOUNDED_CANDIDATE'), require_accepted=True)
        self.assertTrue(value['valid'])
        self.assertFalse(value['requested_gate_satisfied'])
        self.assertEqual(value['decisions'][0]['recorded_decision'], 'ACCEPTED_AS_BOUNDED_CANDIDATE')

    def test_chapter_id_must_be_unique_and_retained(self):
        doc = fixture()
        doc['chapters'].append(copy.deepcopy(doc['chapters'][0]))
        self.assertFalse(validate(doc)['valid'])
        doc = fixture()
        doc['chapters'][0]['accepted_delta_interface']['chapter_id'] = 'another-chapter'
        self.assertFalse(validate(doc)['valid'])

    def test_original_scope_and_input_unchanged(self):
        doc = fixture('PARTIAL')
        before = copy.deepcopy(doc)
        validate(doc)
        self.assertEqual(doc, before)

    def test_original_owner_is_preserved_when_supervisor_differs(self):
        doc = fixture()
        doc['chapters'][0]['supervisor_actor_id'] = 'another-chapter-supervisor'
        before = copy.deepcopy(doc)
        self.assertTrue(validate(doc, require_accepted=True)['requested_gate_satisfied'])
        self.assertEqual(doc, before)
        for missing in (None, '', '   '):
            doc['chapters'][0]['accepted_delta_interface']['owner_actor_id'] = missing
            self.assertFalse(validate(doc)['valid'])

    def test_original_chapter_task_is_preserved_under_parent_coordination(self):
        doc = fixture()
        doc['task_id'] = 'whole-thesis-coordination'
        before = copy.deepcopy(doc)
        self.assertTrue(validate(doc, require_accepted=True)['requested_gate_satisfied'])
        self.assertEqual(doc, before)
        for missing in (None, '', '   '):
            doc['chapters'][0]['accepted_delta_interface']['task_id'] = missing
            self.assertFalse(validate(doc)['valid'])

    def test_optional_chapter_selection_does_not_freeze_independent_work(self):
        doc = fixture()
        unrelated = fixture('UNVERIFIED', chapter_id='unrelated')['chapters'][0]
        unrelated['accepted_delta_interface']['acceptance_scope'] = None
        doc['chapters'].append(unrelated)
        self.assertTrue(validate(doc, chapter_ids=['chapter_A'])['valid'])
        self.assertFalse(validate(doc)['valid'])
        self.assertFalse(validate(doc, chapter_ids=['missing'])['valid'])

    def test_review_counts_preserve_zero_and_three_but_reject_four_and_bool(self):
        for rounds, expected in ((0, True), (3, True), (4, False), (-1, False), (True, False), ('1', False)):
            with self.subTest(rounds=rounds):
                doc = fixture()
                review = doc['chapters'][0]['accepted_delta_interface']['review']
                review['rounds_used'] = rounds
                if rounds == 0:
                    review['baseline_pin'] = None
                self.assertEqual(validate(doc)['valid'], expected)
        doc = fixture()
        doc['chapters'][0]['accepted_delta_interface']['review']['baseline_pin'] = None
        self.assertFalse(validate(doc)['valid'])

    def test_artifact_and_evidence_pins_cannot_be_empty_or_invalid(self):
        for field in ('artifact_pins', 'source_evidence_pins'):
            for value in ([], [None], [{'path': 'x', 'sha256': 'bad'}], [{'path': '', 'sha256': 'a' * 64}]):
                with self.subTest(field=field, value=value):
                    doc = fixture()
                    doc['chapters'][0]['accepted_delta_interface'][field] = value
                    self.assertFalse(validate(doc)['valid'])

    def test_pin_byte_count_and_duplicate_reference(self):
        doc = fixture()
        refs = doc['chapters'][0]['accepted_delta_interface']['artifact_pins']
        refs[0]['bytes'] = True
        self.assertFalse(validate(doc)['valid'])
        del refs[0]['bytes']
        refs.append(copy.deepcopy(refs[0]))
        self.assertFalse(validate(doc)['valid'])

    def test_verification_detects_real_file_changes_and_missing_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'evidence.txt'
            path.write_bytes(b'original evidence')
            doc = fixture()
            self.assertTrue(validate(doc, verify_files=True, base_dir=directory)['valid'])
            path.write_bytes(b'altered evidence')
            self.assertFalse(validate(doc, verify_files=True, base_dir=directory)['valid'])
            path.unlink()
            self.assertFalse(validate(doc, verify_files=True, base_dir=directory)['valid'])
            self.assertTrue(validate(doc, verify_files=False, base_dir=directory)['valid'])

    def test_duplicate_json_keys_rejected_without_silent_last_write_wins(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'input.json'
            path.write_text('{"chapter_id":"a","chapter_id":"b"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
                load_json(path)

    def test_invalid_types_are_diagnostics_not_exceptions(self):
        for value in (None, [], True, 2):
            with self.subTest(root=value):
                self.assertFalse(validate(value)['valid'])
        for value in ([], {}, True, 2, None):
            with self.subTest(decision=value):
                self.assertFalse(validate(fixture(value))['valid'])
        self.assertFalse(validate(fixture(), chapter_ids=[{}])['valid'])
        for value in (None, [], True):
            doc = fixture()
            doc['chapters'][0]['accepted_delta_interface']['review'] = value
            self.assertFalse(validate(doc)['valid'])

    def test_non_json_numeric_constants_rejected_at_file_boundary(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'input.json'
            for literal in ('NaN', 'Infinity', '-Infinity'):
                with self.subTest(literal=literal):
                    path.write_text('{"value":' + literal + '}', encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'Non-JSON numeric constant'):
                        load_json(path)

    def test_cli_exit_codes_preserve_partial_and_parse_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'input.json'
            path.write_text(json.dumps(fixture('PARTIAL')), encoding='utf-8')
            with patch('sys.argv', ['validator', str(path), '--require-accepted']), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 1)
            value = json.loads(output.getvalue())
            self.assertTrue(value['valid'])
            self.assertFalse(value['requested_gate_satisfied'])
            path.write_text('{"x":NaN}', encoding='utf-8')
            with patch('sys.argv', ['validator', str(path)]), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 1)
            self.assertFalse(json.loads(output.getvalue())['valid'])

    def test_blank_form_is_allowed_only_as_template(self):
        doc = fixture('UNVERIFIED')
        doc['template_only'], doc['status'] = True, 'TEMPLATE_NOT_ENROLLED'
        delta = doc['chapters'][0]['accepted_delta_interface']
        for field in ('acceptance_scope', 'task_id', 'next_action'):
            delta[field] = None
        delta['artifact_pins'], delta['source_evidence_pins'] = [], []
        delta['review']['baseline_pin'], delta['review']['rounds_used'] = None, None
        self.assertTrue(validate(doc, mode='template')['valid'])
        self.assertFalse(validate(doc, mode='template')['recorded_all_accepted_gate'])
        self.assertFalse(validate(doc)['valid'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
