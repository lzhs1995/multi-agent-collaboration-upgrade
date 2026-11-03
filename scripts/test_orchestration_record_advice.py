"""Production off-arm worker advice before an unbound node's decision; offline fixtures."""
from __future__ import annotations

import json
import unittest

from jev_advice_run import record_advice
from orchestration_common import ContractError
from test_orchestration_bind_unbound import BindFixtureMixin


class RecordAdviceTests(BindFixtureMixin, unittest.TestCase):
    def journal_path(self):
        return self.co.root / "nodes" / "unbound" / "advice-worker-journal.json"

    def test_off_arm_writes_only_the_exact_journal(self):
        value = record_advice(self.r.co(self.run), "unbound")
        self.assertEqual((value["arm"], value["model_requests"], value["ledger"]), ("advisory-off", 0, None))
        self.assertEqual(value["worker_status"], "NOT_CONFIGURED")
        journal = json.loads(self.journal_path().read_text())
        self.assertEqual(journal["schema"], "jev-advice-worker-journal-v1")
        self.assertEqual([entry["model_requests"] for entry in journal["entries"]], [0])
        self.assertFalse((self.co.root / "nodes" / "unbound" / "advice").exists())
        self.assertFalse((self.co.root / "jev-request-ledger").exists())

    def test_reentry_is_refused_before_the_worker_runs_again(self):
        record_advice(self.r.co(self.run), "unbound")
        before = self.journal_path().read_bytes()
        with self.assertRaisesRegex(ContractError, "DECISION_ALREADY_RECORDED"):
            record_advice(self.r.co(self.run), "unbound")
        self.assertEqual(self.journal_path().read_bytes(), before)

    def test_a_bound_or_unknown_node_is_refused(self):
        bound = next(key for key in self.co.nodes if key != "unbound")
        for key, error in ((bound, "only a deliberately unbound frozen node"), ("nowhere", "unknown frozen node")):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ContractError, error):
                    record_advice(self.r.co(self.run), key)

    def test_a_published_decision_refuses_new_advice(self):
        path = self.co.root / "decisions" / "unbound.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
        with self.assertRaisesRegex(ContractError, "original published decision exists"):
            record_advice(self.r.co(self.run), "unbound")
        self.assertFalse(self.journal_path().exists())


if __name__ == "__main__":
    unittest.main()
