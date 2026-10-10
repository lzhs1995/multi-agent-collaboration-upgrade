"""Regression for the Claude 2.1.170 144/140/136-cell composer layout."""
import unittest

import cmux_bridge as bridge


TEXT = (
    'STATUS: MESSAGE_REFERENCE_V2 deadbeef '
    'BODY="/tmp/multi-agent-body-reference.txt" '
    'SHA256=' + 'a' * 64 + ' BYTES=1609 Verify and read the exact UTF-8 body; '
    'EXTRA=' + 'b' * 120 + ' follow its reply contract within existing authority. '
    'Native receipt proves this notice only; body reading and acceptance are separate.'
)


def observed_screen(text=TEXT, *, tail=4, border=144):
    """Build the measured live shape without embedding private task paths."""
    first = text[: border - 4]
    second = text[border - 4 : border - 4 + border - 8]
    third = text[border - 4 + border - 8 : border - 4 + 2 * (border - 8)]
    fourth = text[border - 4 + 2 * (border - 8) :]
    return "\n".join([
        "─" * border,
        "❯\u00a0" + first,
        "  " + second,
        "  " + third,
        "  " + fourth + (" " * tail),
        "─" * border,
        "  [claude-opus-5-5[1M]] │ /tmp/project git:(main)",
        "  上下文 ░░░░░░░░░░ 0%",
        "  1 CLAUDE.md | 1 规则 | 1 MCPs | 1 钩子",
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ])


class ClaudeMeasuredWrapTests(unittest.TestCase):
    def test_live_shape_matches_original_single_line_payload(self):
        self.assertTrue(bridge._exact_pending_text(observed_screen(), TEXT))

    def test_live_shape_does_not_authorize_changed_payload(self):
        screen = observed_screen()
        for changed in (
            TEXT[1:],
            TEXT + "x",
            TEXT.replace("receipt proves", "receipt  proves", 1),
            TEXT.replace("deadbeef", "deadbeee", 1),
        ):
            with self.subTest(changed=changed[-32:]):
                self.assertFalse(bridge._exact_pending_text(screen, changed))

    def test_renderer_padding_is_bound_to_exact_measured_shape(self):
        self.assertFalse(bridge._exact_pending_text(observed_screen(tail=5), TEXT))
        self.assertFalse(bridge._exact_pending_text(observed_screen(border=143), TEXT))

    def test_short_or_small_borders_keep_original_fail_closed_rules(self):
        short = observed_screen(border=96)
        self.assertFalse(bridge._exact_pending_text(short, TEXT))


if __name__ == "__main__":
    unittest.main()
