"""R545 P2/R561: S2 off-zero, auth STOP and paired-arm checks through the real coordinator gate.

The campaign is CampaignFixture's frozen v3 omo/unbound campaign with an
in-memory S2 overlay; the paired registration, per-node JEV registration and
native semantic batch are patched exactly as ROOT's SemanticEntryTests do.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import jev_advice_run as run
import jev_on_call
from jev_request_ledger import ledger_summary
from orchestration_common import ContractError, file_ref, write_json
from test_jev_inbox import MODEL, OfflineInboxConnection, msg
from test_orchestration_bind_unbound import BindFixtureMixin


class S2RealGateTests(BindFixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.conn, self.group = OfflineInboxConnection(), "advisory-off"
        self.reg = dict(arm="advisory-off", registration={"offline_fixture": True}, budget={"max_model_requests": 20},
                        model=MODEL, provider="typesafe-fixture", transport_source=file_ref(Path(jev_on_call.__file__).resolve()))
        self.batch = dict(ready=True, messages=[msg(i, body) for i, body in enumerate(("note", "constraint", "priority ACT"))],
                          attribution={"offline_fixture": True})
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("jev_registration.load", side_effect=lambda *a, **k: self.reg))
        stack.enter_context(patch("upgrade_benchmark.validate_registration",
                                  side_effect=lambda *a: {"groups": {"fixture": {"advisory": self.group}}}))
        stack.enter_context(patch("upgrade_semantic_inputs.native_batch", side_effect=lambda *a, **k: self.batch))
        stack.enter_context(patch("upgrade_semantic_inputs.read_requests", return_value=[]))

    def s2(self):
        co = self.r.co(self.run)
        co.campaign = dict(co.campaign, semantic_input={"offline_fixture": True},
                           registration={"offline_fixture": True}, session_group="fixture")
        return co

    def on(self):
        self.group = self.reg["arm"] = "advisory-on"
        return dict(connection=self.conn, credential="placeholder-not-a-key")

    def ledger(self):
        return self.co.root / run.LEDGER_DIRECTORY

    def test_off_group_dp1_and_dp2_make_zero_requests(self):
        advice = run.record_advice(self.s2(), "unbound")
        order = run.record_inbox_order(self.s2(), "unbound", "semantic-" + self.run, None)
        self.assertEqual((advice["arm"], advice["model_requests"], advice["read_command"]), ("advisory-off", 0, None))
        self.assertEqual((order["order"]["model_requests"], order["order"]["order"]), (0, ["m0", "m1", "m2"]))
        self.assertEqual(len(self.conn.sent), 0)
        self.assertFalse(self.ledger().exists())

    def test_on_registration_in_an_off_group_is_refused_before_any_transport(self):
        held = self.on()
        self.group = "advisory-off"
        with self.assertRaisesRegex(ContractError, "DP1 advice arm differs"):
            run.record_advice(self.s2(), "unbound", **held)
        with self.assertRaisesRegex(ContractError, "DP2 transport arm differs"):
            run.record_inbox_order(self.s2(), "unbound", "semantic-" + self.run, None, **held)
        self.assertEqual(len(self.conn.sent), 0)
        self.assertFalse(self.ledger().exists())

    def test_auth_stop_in_dp2_stops_dp1_and_latches_the_worker(self):
        held = self.on()
        self.conn.statuses = [401]
        with self.assertRaisesRegex(ContractError, run.STOP_REASON):
            run.record_inbox_order(self.s2(), "unbound", "semantic-" + self.run, None, **held)
        self.assertEqual(len(self.conn.sent), 1)
        self.assertTrue(ledger_summary(self.ledger())["stopped"])
        path = self.co.root / "fixture-registration.json"
        write_json(path, {"offline_fixture": True}, exclusive=True)
        registration = file_ref(path)
        request = dict(op="advice", run=self.run, node="unbound", registration=registration["path"],
                       registration_sha256=registration["sha256"])
        state = {}
        with self.assertRaisesRegex(ContractError, run.STOP_REASON):
            run.handle(lambda r: self.s2(), request, state=state, **held)
        self.assertEqual(run.handle(lambda r: self.s2(), request, state=state, **held)["status"], "STOPPED")
        self.assertEqual(len(self.conn.sent), 1)


if __name__ == "__main__":
    unittest.main()
