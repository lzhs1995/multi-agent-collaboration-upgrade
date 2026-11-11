"""R579 P5: S2 lane Goal startup text is one template for both arms and passes the startup contract."""
import unittest

from upgrade_s2_startup import GOAL, lane_responsibilities
from upgrade_startup_contract import native_goal_condition, pressure_lane_document, require_goal_condition


class S2StartupTextTests(unittest.TestCase):
    def texts(self, advisory):
        return lane_responsibilities("fan_in-" + advisory[9:], dict(advisory=advisory), "/fx/campaigns/jev-c")

    def test_both_arms_differ_only_in_group_identity(self):
        off, on = self.texts("advisory-off"), self.texts("advisory-on")
        self.assertEqual(set(off), {"codex", "claude_a", "claude_b"})
        for lane in off:
            self.assertEqual(off[lane].replace("fan_in-off", "G").replace("advisory-off", "A"),
                             on[lane].replace("fan_in-on", "G").replace("advisory-on", "A"))

    def test_codex_waits_for_the_generated_supervisor_instructions(self):
        self.assertIn("/fx/campaigns/jev-c/supervisor-instructions.md", self.texts("advisory-on")["codex"])

    def test_no_old_exercise_or_shadow_wording(self):
        for text in [GOAL, *self.texts("advisory-on").values()]:
            for word in ("trio", "shadow", "u25", "60-second"):
                self.assertNotIn(word, text.lower())

    def test_text_passes_the_startup_contract(self):
        require_goal_condition(GOAL)
        native_goal_condition(GOAL, "/fx/config/claude_a-instructions.md")
        for lane, text in self.texts("advisory-off").items():
            document = pressure_lane_document("/fx/controller", "/fx/python", "/fx/rtk", lane, text)
            self.assertIn(text, document)


if __name__ == "__main__":
    unittest.main()
