"""Offline checks for the existing nested-research-collaboration-v1 delta fields.

This checks recorded decisions, never grants scientific acceptance, delivery,
enrollment, controller authority or resource release. It does not modify input.
The template remains a blank form; use handoff mode for a populated submission.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SCHEMA = 'nested-research-collaboration-v1'
DELTA_FIELDS = frozenset('chapter_id acceptance_scope task_id owner_actor_id artifact_pins source_evidence_pins terminology_and_method_dependencies checks_performed scientific_limits unresolved_items review resource_state next_action'.split())
LIST_FIELDS = frozenset('artifact_pins source_evidence_pins terminology_and_method_dependencies checks_performed scientific_limits unresolved_items resource_state'.split())
REVIEW_FIELDS = frozenset('baseline_pin rounds_used findings_and_dispositions acceptance_status'.split())


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('Non-JSON numeric constant: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=unique, parse_constant=invalid_constant)


def validate(document, *, mode='handoff', chapter_ids=None, require_accepted=False,
             verify_files=False, base_dir=None):
    """Return diagnostics without writing or dispatching. Caller owns acceptance.

    Subrecords whose shape is not specified by the source contract are opaque.
    Every existing field remains required; this function adds no payload fields.
    File verification is explicit and uses the input file directory for relatives.
    """
    errors, decision_rows = [], []
    base = Path(base_dir or '.').resolve()

    def error(where, message):
        errors.append({'location': where, 'message': message})

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def record(value, fields, where):
        if not isinstance(value, dict):
            error(where, 'object required')
            return False
        for field in sorted(fields - set(value)):
            error(where + '.' + field, 'required field missing')
        return True

    def pin(value, where):
        if not record(value, {'path', 'sha256'}, where):
            return
        path, digest = value.get('path'), value.get('sha256')
        good = text(path) and isinstance(digest, str) and bool(re.fullmatch('[a-f0-9]{64}', digest))
        if not good:
            error(where, 'nonempty path and lowercase 64-character SHA256 required')
        count = value.get('bytes')
        if 'bytes' in value and (type(count) is not int or count < 0):
            error(where + '.bytes', 'optional byte count must be a nonnegative integer')
            good = False
        if not good or not verify_files:
            return
        target = Path(path)
        if not target.is_absolute():
            target = base / target
        try:
            if not stat.S_ISREG(target.stat().st_mode):
                raise ValueError('regular file required')
            with target.open('rb') as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('regular file required')
                actual, size = hashlib.sha256(), 0
                while True:
                    block = handle.read(1024 * 1024)
                    if not block:
                        break
                    actual.update(block)
                    size += len(block)
                after = os.fstat(handle.fileno())
            named = target.stat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if identity(before) != identity(after) or identity(after) != identity(named):
                raise ValueError('file identity or content metadata changed during read')
            if actual.hexdigest() != digest or ('bytes' in value and size != count):
                raise ValueError('file bytes/SHA256 do not match reference')
        except (OSError, ValueError) as exc:
            error(where, 'evidence verification failed: ' + str(exc))

    def pins(values, where, required):
        if not isinstance(values, list):
            return  # list diagnostics are emitted at the field boundary.
        if required and not values:
            error(where, 'at least one artifact/evidence reference required for a recorded decision')
        seen = set()
        for index, value in enumerate(values):
            pin(value, f'{where}[{index}]')
            if isinstance(value, dict) and text(value.get('path')):
                path = value['path']
                if path in seen:
                    error(where, 'duplicate reference path: ' + path)
                seen.add(path)

    if mode not in {'template', 'handoff'}:
        raise ValueError('mode must be template or handoff')
    if require_accepted and mode != 'handoff':
        raise ValueError('require_accepted applies only to handoff mode')
    if not isinstance(document, dict):
        error('$', 'object required')
        document = {}
    if document.get('schema') != SCHEMA:
        error('schema', 'existing nested-research-collaboration-v1 schema required')
    if mode == 'handoff' and (document.get('template_only') is not False
                              or document.get('status') == 'TEMPLATE_NOT_ENROLLED'):
        error('template_only', 'blank template must not be presented as a populated handoff')
    if mode == 'template' and (document.get('template_only') is not True
                               or document.get('status') != 'TEMPLATE_NOT_ENROLLED'):
        error('template_only', 'template mode is only for the explicitly unenrolled form')
    chapters = document.get('chapters')
    if not isinstance(chapters, list) or not chapters:
        error('chapters', 'nonempty chapter list required; chapter count is not fixed at four')
        chapters = []
    by_id = {}
    for index, chapter in enumerate(chapters):
        where = f'chapters[{index}]'
        if not isinstance(chapter, dict) or not text(chapter.get('chapter_id')):
            error(where + '.chapter_id', 'nonempty chapter identifier required')
            continue
        chapter_id = chapter['chapter_id']
        if chapter_id in by_id:
            error(where + '.chapter_id', 'duplicate chapter identifier: ' + chapter_id)
        else:
            by_id[chapter_id] = (index, chapter)
    if chapter_ids is not None and (not isinstance(chapter_ids, (list, tuple))
                                    or not all(text(item) for item in chapter_ids)):
        error('selection', 'chapter selection must be a list of nonempty strings')
        chapter_ids = []
    selected = list(by_id) if chapter_ids is None else list(chapter_ids)
    if not selected or len(selected) != len(set(selected)):
        error('selection', 'select at least one distinct chapter identifier')
    for chapter_id in selected:
        if chapter_id not in by_id:
            error('selection', 'unknown chapter identifier: ' + str(chapter_id))
            continue
        index, chapter = by_id[chapter_id]
        where = f'chapters[{index}].accepted_delta_interface'
        delta = chapter.get('accepted_delta_interface')
        if not record(delta, DELTA_FIELDS, where):
            continue
        if delta.get('chapter_id') != chapter_id:
            error(where + '.chapter_id', 'delta must retain its enclosing chapter identifier')
        for field in LIST_FIELDS:
            if not isinstance(delta.get(field), list):
                error(where + '.' + field, 'list required; an explicit empty list is allowed unless pins are needed')
        review = delta.get('review')
        if not record(review, REVIEW_FIELDS, where + '.review'):
            continue
        decision = review.get('acceptance_status')
        if not text(decision):
            error(where + '.review.acceptance_status', 'nonempty recorded decision string required')
        if mode == 'handoff' and decision == 'UNVERIFIED':
            error(where + '.review.acceptance_status', 'populated handoff requires an explicit recorded decision')
        finalized = mode == 'handoff' or (text(decision) and decision != 'UNVERIFIED')
        for field in ('acceptance_scope', 'task_id', 'owner_actor_id'):
            if finalized and not text(delta.get(field)):
                error(where + '.' + field, 'nonempty value required; acceptance status does not supply scope')
        # Preserve the chapter original task/owner; parent coordination and
        # submitting supervisor are separate roles under the existing contract.
        rounds = review.get('rounds_used')
        if not (mode == 'template' and decision == 'UNVERIFIED' and rounds is None):
            if type(rounds) is not int or not 0 <= rounds <= 3:
                error(where + '.review.rounds_used', 'integer in 0..3 required; bool is not a review count')
        if not isinstance(review.get('findings_and_dispositions'), list):
            error(where + '.review.findings_and_dispositions', 'list required')
        baseline = review.get('baseline_pin')
        if baseline is not None:
            pin(baseline, where + '.review.baseline_pin')
        elif type(rounds) is int and rounds > 0:
            error(where + '.review.baseline_pin', 'reviewed baseline reference required when review rounds were used')
        pins(delta.get('artifact_pins'), where + '.artifact_pins', finalized)
        pins(delta.get('source_evidence_pins'), where + '.source_evidence_pins', finalized)
        decision_rows.append({'chapter_id': chapter_id, 'recorded_decision': decision,
                              'acceptance_scope': delta.get('acceptance_scope'), 'rounds_used': rounds})
    accepted = bool(decision_rows) and all(row['recorded_decision'] == 'ACCEPTED' for row in decision_rows)
    gate = bool(mode == 'handoff' and not errors and accepted)
    return {'evidence_class': 'offline_chapter_delta_validation', 'mode': mode,
            'valid': not errors, 'selected_chapters': selected, 'errors': errors,
            'decisions': decision_rows, 'recorded_all_accepted_gate': gate,
            'requested_gate_satisfied': not errors and (not require_accepted or gate),
            'reference_verification': 'file_bytes_checked' if verify_files else 'descriptors_only',
            'decision_policy': 'Existing decision text is preserved; only exact ACCEPTED satisfies this checker optional all-accepted gate. No universal business-status enum is imposed.',
            'limitations': ['Does not prove authorship, scientific acceptance or acceptance-scope meaning.',
                           'Descriptors-only mode does not read or prove referenced file bytes.',
                           'Does not prove prior ledger continuity or resource release.',
                           'Does not validate live endpoints, dispatch, role enrollment or controller authority.',
                           'Opaque finding/check/resource/dependency subrecords retain the existing contract; no new inner schema imposed.',
                           'No formal business or paired experimental sample is added.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--mode', choices=('template', 'handoff'), default='handoff')
    parser.add_argument('--chapter', action='append', dest='chapters')
    parser.add_argument('--require-accepted', action='store_true')
    parser.add_argument('--verify-files', action='store_true')
    args = parser.parse_args()
    try:
        result = validate(load_json(args.input), mode=args.mode, chapter_ids=args.chapters,
                          require_accepted=args.require_accepted, verify_files=args.verify_files,
                          base_dir=args.input.resolve().parent)
    except (OSError, ValueError) as exc:
        print(json.dumps({'evidence_class': 'offline_chapter_delta_validation', 'valid': False,
                          'errors': [{'location': '$', 'message': str(exc)}]}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['requested_gate_satisfied'] else 1


if __name__ == '__main__':
    sys.exit(main())
