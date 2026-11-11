import itertools
import unittest
from pathlib import Path

import jev_advice
from jev_advice import STATUSES, WORK_KINDS, inbox_advice, route_advice


def lane(kind, *, verified=True, idle=True, author=False):
    return dict(kind=kind, verified=verified, idle_write_domain=idle, author=author)


LANES = dict(codex=lane("codex"), claude_a=lane("claude", author=True), claude_b=lane("claude"))
NOT_PROBABILITIES = (None, "0.9", True, False, float("nan"), float("inf"), -0.1, 1.1)


class RouteAdviceTests(unittest.TestCase):
    def answers(self, kind="coding", applies=0.9):
        return dict(work_kind=kind, work_kind_applies=applies)

    def test_bound_task_never_reads_answers(self):
        advice = route_advice(bound=True, answers=self.answers(), lanes=LANES)
        self.assertEqual((advice["status"], advice["candidates"]), ("NOT_CALLED", ()))
        self.assertEqual(route_advice(bound=True, error="timeout")["status"], "NOT_CALLED")

    def test_failed_request_falls_back_to_rules_and_is_never_a_no(self):
        for error, status in (("NOT_CONFIGURED", "NOT_CONFIGURED"), ("timeout", "JEV_ERROR"),
                              ("http_429", "JEV_ERROR"), ("http_503", "JEV_ERROR")):
            with self.subTest(error=error):
                advice = route_advice(bound=False, answers=self.answers(), error=error, lanes=LANES)
                self.assertEqual((advice["status"], advice["fallback"], advice["candidates"]), (status, "rules", ()))

    def test_missing_field_or_invalid_answer_is_an_error(self):
        cases = [None, {}, dict(work_kind="coding"), dict(work_kind_applies=0.9),
                 self.answers(kind="deploy"), self.answers(kind=None)]
        cases += [self.answers(applies=value) for value in NOT_PROBABILITIES]
        for answers in cases:
            with self.subTest(answers=answers):
                advice = route_advice(bound=False, answers=answers, lanes=LANES)
                self.assertEqual((advice["status"], advice["fallback"]), ("JEV_ERROR", "rules"))

    def test_companion_noul_bands(self):
        for applies, status, fallback in ((0.0, "NOT_APPLICABLE", "rules"), (0.29, "NOT_APPLICABLE", "rules"),
                                          (0.3, "UNSETTLED", "supervisor"), (0.5, "UNSETTLED", "supervisor"),
                                          (0.7, "UNSETTLED", "supervisor"), (0.71, "SUGGEST", None)):
            with self.subTest(applies=applies):
                advice = route_advice(bound=False, answers=self.answers(applies=applies), lanes=LANES)
                self.assertEqual((advice["status"], advice["fallback"]), (status, fallback))

    def test_coding_prefers_verified_idle_codex_then_idle_claude(self):
        for kind in ("coding", "debugging"):
            with self.subTest(kind=kind):
                self.assertEqual(route_advice(bound=False, answers=self.answers(kind), lanes=LANES)["candidates"],
                                 ("codex",))
                for blocked in (lane("codex", verified=False), lane("codex", idle=False),
                                lane("codex", verified="true")):
                    lanes = dict(LANES, codex=blocked)
                    self.assertEqual(route_advice(bound=False, answers=self.answers(kind), lanes=lanes)["candidates"],
                                     ("claude_a", "claude_b"))

    def test_review_and_literature_only_suggest_a_non_author_claude(self):
        for kind in ("review", "literature"):
            with self.subTest(kind=kind):
                advice = route_advice(bound=False, answers=self.answers(kind), lanes=LANES)
                self.assertEqual((advice["status"], advice["candidates"]), ("SUGGEST", ("claude_b",)))
                lanes = dict(LANES, claude_b=lane("claude", author=None))
                self.assertEqual(route_advice(bound=False, answers=self.answers(kind), lanes=lanes)["status"],
                                 "NO_IDLE_LANE")

    def test_no_verified_idle_lane_goes_to_the_supervisor(self):
        busy = {name: dict(value, idle_write_domain=False) for name, value in LANES.items()}
        for kind, lanes in itertools.product(WORK_KINDS, ({}, None, busy)):
            with self.subTest(kind=kind, lanes=lanes):
                advice = route_advice(bound=False, answers=self.answers(kind), lanes=lanes)
                self.assertEqual((advice["status"], advice["fallback"], advice["candidates"]),
                                 ("NO_IDLE_LANE", "supervisor", ()))

    def test_every_combination_has_one_known_outcome(self):
        lane_sets = [dict(zip(LANES, flags)) for flags in itertools.product(
            [lane("codex"), lane("codex", verified=False)], [lane("claude", author=True), lane("claude", idle=False)],
            [lane("claude"), lane("claude", verified=False)])]
        seen = set()
        for kind, applies, error, lanes in itertools.product(
                WORK_KINDS + ("other",), (0.1, 0.3, 0.5, 0.9, float("nan")), (None, "timeout"), lane_sets):
            advice = route_advice(bound=False, answers=self.answers(kind, applies), error=error, lanes=lanes)
            seen.add(advice["status"])
            self.assertIn(advice["status"], STATUSES)
            self.assertEqual(advice["status"] == "SUGGEST", bool(advice["candidates"]))
            for name in advice["candidates"]:
                self.assertIs(lanes[name]["verified"], True)
                self.assertIs(lanes[name]["idle_write_domain"], True)
        self.assertEqual(seen, STATUSES - {"NOT_CALLED", "NOT_CONFIGURED"})


class InboxAdviceTests(unittest.TestCase):
    def test_only_actionable_message_moves_forward_and_body_is_always_read(self):
        for actionable, status, front in ((0.1, "NOT_APPLICABLE", False), (0.3, "UNSETTLED", False),
                                          (0.7, "UNSETTLED", False), (0.71, "SUGGEST", True)):
            with self.subTest(actionable=actionable):
                advice = inbox_advice(answers=dict(message_kind="result", message_actionable=actionable))
                self.assertEqual((advice["status"], advice["front"]), (status, front))
                self.assertIs(advice["full_read_required"], True)

    def test_failed_or_invalid_answer_keeps_original_order(self):
        cases = [dict(error="NOT_CONFIGURED"), dict(error="timeout"), dict(answers=None),
                 dict(answers=dict(message_kind="spam", message_actionable=0.9))]
        cases += [dict(answers=dict(message_kind="request", message_actionable=v)) for v in NOT_PROBABILITIES]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                advice = inbox_advice(**kwargs)
                self.assertIn(advice["status"], {"NOT_CONFIGURED", "JEV_ERROR"})
                self.assertEqual((advice["front"], advice["full_read_required"]), (False, True))


class OfflineBoundaryTests(unittest.TestCase):
    def test_module_imports_no_network_or_sdk(self):
        source = Path(jev_advice.__file__).read_text()
        imports = [line.split()[1] for line in source.splitlines() if line.startswith(("import ", "from "))]
        self.assertIn("math", imports)
        self.assertFalse(set(imports) & {"socket", "urllib", "http", "requests", "httpx", "typesafe", "os"}, imports)


if __name__ == "__main__":
    unittest.main()
