"""Protect fixed-base transport while adding measured Claude chrome handling."""
import ast
import hashlib
import json
from pathlib import Path
import unittest

from orchestration_common import ContractError
from upgrade_live_patch import claude_measured_chrome
from test_upgrade_bridge_probe_evidence import parser_namespace

SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"


@unittest.skipUnless(SOURCE.is_dir(), "requires fixed local MAC source")
class ClaudeChromeTransformTests(unittest.TestCase):
    def setUp(self):
        self.original = (SOURCE / "scripts/cmux_bridge.py").read_text()

    def test_only_two_functions_change_and_native_delivery_is_preserved(self):
        changed = claude_measured_chrome(self.original)
        original_tree, changed_tree = ast.parse(self.original), ast.parse(changed)
        self.assertEqual(len(original_tree.body), len(changed_tree.body))
        names = []
        for old, new in zip(original_tree.body, changed_tree.body):
            if ast.dump(old) != ast.dump(new):
                names.append(old.name)
        self.assertEqual(set(names), {"_claude_bordered_compose", "_queued_or_active_input"})
        for name in ("_submit_text_once", "_submit_terminal_once", "_observe_native",
                     "pin_successor_pair", "_run_successor"):
            old = next(n for n in original_tree.body if getattr(n, "name", None) == name)
            new = next(n for n in changed_tree.body if getattr(n, "name", None) == name)
            self.assertEqual(ast.get_source_segment(self.original, old), ast.get_source_segment(changed, new))

    def test_unknown_missing_and_ambiguous_source_shapes_are_refused(self):
        anchor = '        screen = next((row for row in reversed(claude["before"]) if row.strip()), "")\n'
        for text in (self.original.replace(anchor, '        screen = "unknown"\n'),
                     self.original.replace(anchor, anchor + anchor),
                     self.original.replace('def _claude_bordered_compose(', 'def unknown_compose(')):
            with self.subTest(text_length=len(text)), self.assertRaises(ContractError):
                claude_measured_chrome(text)

    def test_reapplying_repair_is_refused(self):
        with self.assertRaises(ContractError):
            claude_measured_chrome(claude_measured_chrome(self.original))


class MeasuredGoalFooterTests(unittest.TestCase):
    """Real captured empty composer, and changes which must never hide a draft."""

    @classmethod
    def setUpClass(cls):
        cls.parsers = {"repository": parser_namespace(
            Path(__file__).with_name("cmux_bridge.py").read_text())}
        if SOURCE.is_dir():
            cls.parsers["generated"] = parser_namespace(claude_measured_chrome(
                (SOURCE / "scripts/cmux_bridge.py").read_text()))
        root = Path(__file__).with_name("fixtures")
        cls.screens = []
        for row in json.loads((root / "claude-goal-footer-20261019.json").read_text()):
            raw = (root / row["fixture"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == row["sha256"]
            cls.screens.append(raw.decode())

    def test_exact_captured_empty_composer_is_recognized_with_live_activity(self):
        for name, parser in self.parsers.items():
            for screen in self.screens:
                with self.subTest(parser=name, screen=screen[:40]):
                    block = parser["_claude_bordered_compose"](screen)
                    self.assertIsNotNone(block)
                    self.assertEqual(block["body"], "")
                    self.assertEqual(block["footer"], [screen.splitlines()[-1].strip()])
                    self.assertTrue(parser["compose_block_is_empty"](screen))
                    self.assertTrue(parser["_queued_or_active_input"](screen))

    def test_exact_footer_text_inside_composer_remains_verbatim_draft(self):
        for name, parser in self.parsers.items():
            for original in self.screens:
                footer = original.splitlines()[-1].strip()
                for draft in ("用户草稿", " /goal", footer, "\n" + footer, "\t", " ",
                              "continue", "◎ /goal active (13m)"):
                    screen = original.replace("❯\u00a0\n", "❯\u00a0" + draft + "\n")
                    with self.subTest(parser=name, draft=draft):
                        self.assertNotEqual(screen, original)
                        self.assertEqual(parser["_claude_bordered_compose"](screen)["body"], draft)
                        if draft.strip():
                            self.assertFalse(parser["compose_block_is_empty"](screen))

    def test_truncated_or_unknown_footer_and_borders_remain_unknown(self):
        for name, parser in self.parsers.items():
            for original in self.screens:
                lines = original.splitlines(keepends=True)
                border = next(row for row in lines if row.startswith("─"))
                variants = [original + "unknown suffix\n", original.rstrip() + " unknown\n",
                            original.replace("esc to interrupt", "esc to cancel"),
                            original.replace("/goal active", "/goal paused"),
                            original.replace("(13m)", "(unknown)"),
                            original.replace("◎", "●"),
                            original.replace(border, "", 1)]
                # Every tail truncation across the exact native Goal annotation.
                end = original.rfind("◎")
                variants.extend(original[:cut] for cut in range(end, len(original.rstrip())))
                for screen in variants:
                    with self.subTest(parser=name, ending=screen[-80:]):
                        self.assertNotEqual(screen, original)
                        self.assertIsNone(parser["_claude_bordered_compose"](screen))

    def test_new_footer_is_not_itself_a_proof_of_running_goal_or_activity(self):
        for name, parser in self.parsers.items():
            for original in self.screens:
                lines = original.splitlines()
                first_border = next(i for i, row in enumerate(lines) if row.startswith("─"))
                screen = "✻ Brewed for 3m\n\n" + "\n".join(lines[first_border:]) + "\n"
                with self.subTest(parser=name):
                    self.assertTrue(parser["compose_block_is_empty"](screen))
                    self.assertFalse(parser["_queued_or_active_input"](screen))


if __name__ == "__main__":
    unittest.main()
