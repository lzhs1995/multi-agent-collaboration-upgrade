"""Protect fixed-base transport while adding measured Claude chrome handling."""
import ast
from pathlib import Path
import unittest

from orchestration_common import ContractError
from upgrade_live_patch import claude_measured_chrome

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


if __name__ == "__main__":
    unittest.main()
