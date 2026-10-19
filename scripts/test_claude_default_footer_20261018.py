"""Both untouched r11 screens plus explicit derived negatives; all I/O is offline."""
import hashlib
import unittest
from unittest.mock import patch

import cmux_bridge as b
import test_bridge_clear_recovery as recovery
from native_test_support import NativeFixture
from test_claude_footer_parser_20261008 import FIXTURES, with_draft


PERMISSIONS = "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
TIP = "  ⎿ \u00a0Tip: Use /clear to start fresh when switching topics and free up context"
PINS = {
    "a": "e512b4961023f38ec5c8f2330699ba50dfbbd69cdb588d903fbc464ca366e983",
    "b": "3231def4788525f486985c5d2ed4e612db59d27af69c3af5abcdcf6992f946c7",
}


def captured(lane="a"):
    return (FIXTURES / f"claude-default-{lane}-20261018.txt").read_text()


def before_composer(screen, text):
    rows = screen.splitlines()
    start = max(i for i, row in enumerate(rows) if row.startswith("❯"))
    rows[start - 1:start - 1] = text.splitlines()
    return "\n".join(rows) + "\n"


def setUpModule():
    recovery.setUpModule()


class ClaudeDefaultFooterTests(unittest.TestCase):
    def assert_zero_input(self, screen):
        with NativeFixture(provider="claude"), patch.object(b, "read_screen", return_value=screen), \
                patch.object(b, "send_text") as paste, patch.object(b, "send_key") as key:
            with self.assertRaises(b.DispatchUnconfirmed):
                b.submit_text("offline-peer", "STATUS: new-token", marker="new-token")
        paste.assert_not_called()
        key.assert_not_called()

    def test_original_captures_are_unchanged_and_both_idle(self):
        for lane, sha in PINS.items():
            with self.subTest(lane=lane):
                raw = (FIXTURES / f"claude-default-{lane}-20261018.txt").read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
                screen = raw.decode()
                self.assertEqual(b.receiver_input_kind(screen), "AGENT_TUI")
                self.assertEqual(b.compose_block_text(screen), "")
                self.assertTrue(b.compose_block_is_empty(screen))
                self.assertFalse(b._queued_or_active_input(screen))
                b.require_clearable_agent_input(screen, "offline-peer")

    def test_exact_permissions_row_without_agents_hint(self):
        screen = captured().replace(" · ← for agents", "")
        self.assertEqual(b.receiver_input_kind(screen), "AGENT_TUI")
        self.assertTrue(b.compose_block_is_empty(screen))

    def test_drafts_including_footer_lookalikes_are_preserved(self):
        for draft in ("STATUS: original", PERMISSIONS, "\n  " + PERMISSIONS,
                      "  spaced\t正文  ", "first\n  last", "\n  ✓ Bash ×20",
                      "\n  [claude-opus-5-5[1M]]", "\n  ◐ Bash: pending"):
            with self.subTest(draft=draft):
                screen = with_draft(captured(), draft)
                self.assertEqual(b.compose_block_text(screen), draft)
                self.assertFalse(b.compose_block_is_empty(screen))
                self.assert_zero_input(screen)

    def test_pending_match_requires_whole_original_payload(self):
        payload = "STATUS: original marker with exact spaces"
        screen = with_draft(captured(), payload)
        self.assertTrue(b._exact_pending_text(screen, payload))
        for changed in (payload[:-1], payload + " edited", payload.replace("exact ", "exact  ")):
            self.assertFalse(b._exact_pending_text(screen, changed))
        self.assert_zero_input(screen)

    def test_unknown_extra_or_truncated_footer_rows_refuse_input(self):
        for footer in (PERMISSIONS + " unknown", PERMISSIONS[:-1],
                       PERMISSIONS + "\n  [Opus 5]", PERMISSIONS + "\n  " + PERMISSIONS,
                       "unknown\n  " + PERMISSIONS, "", "⏵⏵ bypass permissions on"):
            with self.subTest(footer=footer):
                screen = captured().replace(PERMISSIONS, footer)
                self.assertIsNone(b._claude_bordered_compose(screen))
                self.assertEqual(b.receiver_input_kind(screen), "UNKNOWN")
                self.assert_zero_input(screen)

    def test_missing_and_mismatched_borders_refuse_input(self):
        rows = captured().splitlines()
        start = max(i for i, row in enumerate(rows) if row.startswith("❯"))
        for removed in ((start - 1,), (start + 1,), (start - 1, start + 1)):
            screen = "\n".join(row for i, row in enumerate(rows) if i not in removed)
            with self.subTest(removed=removed):
                self.assertIsNone(b._claude_bordered_compose(screen))
                self.assert_zero_input(screen)
        rows[start + 1] = rows[start + 1][:-1]
        self.assert_zero_input("\n".join(rows))

    def test_same_width_border_inside_draft_cannot_hide_text(self):
        screen = captured()
        border = b._claude_bordered_compose(screen)
        border = screen.splitlines()[border["end"]]
        screen = with_draft(screen, "\n" + border + "\n  " + PERMISSIONS + "\n  keep me")
        self.assertIsNone(b._claude_bordered_compose(screen))
        self.assert_zero_input(screen)

    def test_active_spinner_and_queue_refuse_even_with_empty_composer(self):
        for activity in ("✢ Crafting… (24m 23s · ↑ 44.9k tokens)",
                         "Messages to be submitted after completion:\n  queued payload"):
            with self.subTest(activity=activity):
                screen = before_composer(captured(), activity)
                self.assertTrue(b.compose_block_is_empty(screen))
                self.assertTrue(b._queued_or_active_input(screen))
                with self.assertRaises(b.DispatchUnconfirmed):
                    b.require_clearable_agent_input(screen, "offline-peer")

    def test_shell_after_footer_overrides_history(self):
        for shell in ("user@mac ~ %", "bash-3.2$ ", "PS C:\\work> "):
            with self.subTest(shell=shell):
                screen = captured() + shell
                self.assertEqual(b.receiver_input_kind(screen), "SHELL")
                self.assert_zero_input(screen)

    def submit_case(self, *, native_record="exact", altered_draft=None):
        text = "STATUS: default-footer-native-proof"
        idle = captured()
        pending = with_draft(idle, altered_draft if altered_draft is not None else text)
        with NativeFixture(provider="claude") as native:
            def observe(*args, **kwargs):
                return idle if not native.send.called or native.key.called else pending

            def receive(*args):
                if native_record == "exact":
                    native.append_user(text)
                elif native_record == "changed":
                    native.append_user(text + " changed")
                elif native_record == "queue":
                    native.append_queued(text)

            native.key.side_effect = receive
            with patch.object(b, "read_screen", side_effect=observe):
                if native_record == "exact" and altered_draft is None:
                    result = b.submit_text("offline-peer", text, marker="default-footer-native-proof")
                    self.assertTrue(result["confirmed"])
                    self.assertEqual(result["confirmation_source"], "native_user_message_v1")
                else:
                    with self.assertRaises(b.DispatchUnconfirmed):
                        b.submit_text("offline-peer", text, marker="default-footer-native-proof")
            native.send.assert_called_once_with("offline-peer", text)
            if altered_draft is None:
                native.key.assert_called_once_with("offline-peer", "enter")
            else:
                native.key.assert_not_called()

    def test_formal_submission_requires_exact_native_receipt(self):
        self.submit_case()

    def test_formal_submission_rejects_missing_or_changed_native_receipt(self):
        for record in ("none", "changed", "queue"):
            with self.subTest(record=record):
                self.submit_case(native_record=record)

    def test_formal_submission_preserves_altered_paste_without_key(self):
        for draft in ("STATUS: default-footer-native-proof edited", "STATUS: partial",
                      "STATUS: default-footer-native-proof\n  extra"):
            with self.subTest(draft=draft):
                self.submit_case(altered_draft=draft)


class DefaultFooterPreflightTests(recovery.r3.OfflineWorkspaceFixture):
    def test_experience_tip_and_goal_cannot_hide_live_spinner(self):
        for lane in PINS:
            for spinner in ("✢ Brewing… (1m 2s)", "✻ Compacting conversation…",
                            "✢ Brewing…", "· Thinking...", "✽ Compacting context..."):
                for hints in (TIP, TIP + "\n" + TIP, TIP + "\n\n◎ /goal active (59m)"):
                    with self.subTest(lane=lane, spinner=spinner, hints=hints):
                        screen = before_composer(captured(lane), spinner + "\n" + hints)
                        self.assertTrue(b.compose_block_is_empty(screen))
                        self.assertTrue(b._queued_or_active_input(screen))
                        with self.assertRaises(b.DispatchUnconfirmed):
                            b.require_clearable_agent_input(screen, "offline-peer")

    def test_experience_tip_keeps_completed_summary_idle(self):
        for summary in ("✻ Brewed for 3m", "✻ Cooked for 42s",
                        "· Note: see README...", "✢ Brewing…\nFinished reviewing."):
            with self.subTest(summary=summary):
                screen = before_composer(captured(), summary + "\n" + TIP)
                self.assertFalse(b._queued_or_active_input(screen))
                b.require_clearable_agent_input(screen, "offline-peer")

    def test_experience_tip_shaped_draft_cannot_be_filtered(self):
        draft = "✢ Brewing…\n" + TIP + "\n◎ /goal active (59m)"
        screen = with_draft(before_composer(captured(), "✻ Brewed for 3m\n" + TIP), draft)
        self.assertEqual(b.compose_block_text(screen), draft)
        self.assertFalse(b.compose_block_is_empty(screen))
        self.assertFalse(b._queued_or_active_input(screen))
        ClaudeDefaultFooterTests.assert_zero_input(self, screen)

    def test_actual_probe_refuses_tip_spinner_without_keys(self):
        for spinner in ("✢ Brewing… (1m 2s)", "✻ Compacting conversation…"):
            with self.subTest(spinner=spinner):
                screen = before_composer(captured(), spinner + "\n" + TIP)
                evidence, keys = recovery.BridgeActivityTests.run_probe(self, [screen], sent=False)
                self.assertEqual(keys, [])
                self.assertEqual(evidence["status"], "COMPOSE_OCCUPIED")
                self.assertEqual(evidence["clear_key_count"], 0)

    def test_actual_probe_entry_refuses_active_and_queued_without_input(self):
        for activity in ("✢ Crafting… (24m 23s · ↑ 44.9k tokens)",
                         "Messages to be submitted after completion:\n  queued payload"):
            with self.subTest(activity=activity):
                screen = before_composer(captured(), activity)
                evidence, keys = recovery.BridgeActivityTests.run_probe(self, [screen], sent=False)
                self.assertEqual(keys, [])
                self.assertEqual(evidence["status"], "COMPOSE_OCCUPIED")
                self.assertEqual(evidence["clear_key_count"], 0)

    def test_actual_probe_entry_accepts_default_footer_and_observes_cleanup(self):
        for lane in PINS:
            with self.subTest(lane=lane):
                evidence, keys = recovery.BridgeActivityTests.run_probe(
                    self, [captured(lane), with_draft(captured(lane), "B1_01234567"),
                           captured(lane)], sent=True, success=True)
                self.assertTrue(evidence["observed_in_screen"])
                self.assertTrue(evidence["clear_confirmed"])
                self.assertEqual(keys, ["end"] + ["backspace"] * len("B1_01234567"))


if __name__ == "__main__":
    unittest.main()
