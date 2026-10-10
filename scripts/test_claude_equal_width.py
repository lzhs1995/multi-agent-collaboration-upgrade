"""Regression for Claude's measured equality word-wrap boundary.

Only the renderer geometry is synthetic; this reproduces 128 + 1 + 7 == 136
from the actual 2026-10-09 composer. Native receipt equality is tested separately.
"""
import unittest

import cmux_bridge as b


def screen(first, second, *, border=140, cursor=True):
    return '\n'.join([
        '⏺ Completed the previous task.',
        '✻ Sautéed for 27m 9s',
        '─' * border,
        '❯\u00a0' + first,
        '  ' + second + (' ' if cursor else ''),
        '─' * border,
        '  [claude-opus-5[1M]] │ ~/project git:(main) │ ⏱️  4h 1m',
        '  上下文 ██░░░░░░░░ 18%',
        '  1 CLAUDE.md | 9 MCPs | 7 钩子',
        '  ⏵⏵ bypass permissions on (shift+tab to cycle)',
    ])


class EqualWidthTests(unittest.TestCase):
    first = 'MESSAGE ' + 'x' * 120
    second = 'reading and acceptance are separate.'

    def test_exact_width_word_boundary_accepts_full_original(self):
        self.assertEqual(len(self.first) + 1 + len('reading'), 136)
        for cursor in (True, False):
            with self.subTest(cursor=cursor):
                self.assertTrue(b._exact_pending_text(
                    screen(self.first, self.second, cursor=cursor),
                    self.first + ' ' + self.second))

    def test_missing_extra_and_changed_spaces_are_rejected(self):
        original = self.first + ' ' + self.second
        for value in (original[1:], original[:-1], original + 'x',
                      original[:80], original.replace(' reading', '  reading'),
                      original.replace(' reading', 'reading'),
                      original.replace('and acceptance', 'and  acceptance')):
            with self.subTest(value=value):
                self.assertFalse(b._exact_pending_text(screen(self.first, self.second), value))

    def test_short_boundary_does_not_invent_separator(self):
        first = self.first[:-1]
        self.assertFalse(b._exact_pending_text(
            screen(first, self.second), first + ' ' + self.second))

    def test_display_equivalent_newline_is_not_native_byte_proof(self):
        # Both render the same. The screen matcher never claims native receipt;
        # the original single-line paste and exact native user record govern it.
        self.assertTrue(b._exact_pending_text(
            screen(self.first, self.second), self.first + '\n' + self.second))

    def test_unknown_footer_keeps_draft_protected(self):
        value = screen(self.first, self.second).replace(
            '  1 CLAUDE.md | 9 MCPs | 7 钩子', '  unknown layout')
        self.assertFalse(b._exact_pending_text(value, self.first + ' ' + self.second))

    def test_extra_blank_or_content_line_keeps_draft_protected(self):
        original = self.first + ' ' + self.second
        for line in ('', '  additional input'):
            value = screen(self.first, self.second).replace(
                '\n' + '─' * 140 + '\n  [', '\n' + line + '\n' + '─' * 140 + '\n  [')
            with self.subTest(line=line):
                self.assertFalse(b._exact_pending_text(value, original))

    def test_live_claude_variable_continuation_width_is_accepted(self):
        # Claude Code 2.1.170 exposed a 144-cell border while the live
        # composer rendered this long single-line notice as 140/136/136/28
        # content cells.  The matcher must prove the same bytes before it
        # permits Enter, while an edited byte remains rejected.
        text = (
            'STATUS: MESSAGE_REFERENCE_V2 BOUNDARY_20261010 '
            'BODY="/tmp/message-bodies/abcdef0123456789.txt" '
            'SHA256=' + 'abcdef0123456789' * 30 + ' '
            'BYTES=1609 Verify and read the exact UTF-8 body; follow its reply '
            'contract within existing authority.'
        )
        parts = [text[:140], text[140:276], text[276:412], text[412:]]
        value = '\n'.join([parts[0]] + ['  ' + part for part in parts[1:]])
        screen_text = screen(parts[0], parts[1], border=144, cursor=False)
        rows = screen_text.splitlines()
        start = max(i for i, row in enumerate(rows) if row.startswith('❯'))
        end = next(i for i in range(start + 1, len(rows)) if rows[i] == '─' * 144)
        rows[start:end] = ['❯\u00a0' + parts[0]] + ['  ' + part for part in parts[1:]]
        screen_text = '\n'.join(rows)
        self.assertTrue(b._exact_pending_text(screen_text, text))
        self.assertFalse(b._exact_pending_text(screen_text, text.replace('Verify and', 'Verify  and')))


if __name__ == '__main__':
    unittest.main()
