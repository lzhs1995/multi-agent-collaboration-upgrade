"""Default-off, single-attempt JEV transport entry; offline doubles, zero network."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from jev_request_ledger import ledger_summary, ledgered_transport
from jev_transport import build_transport
from orchestration_common import ContractError

REPLY = dict(http_status=200, body=dict(answers=dict(work_kind="coding", work_kind_applies=0.9)),
             usage=dict(input_tokens=11, output_tokens=3))


def parse(response):
    return dict(answers=response["body"]["answers"], usage=response.get("usage"))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def ledgered(self, transport, decision="d1"):
        return ledgered_transport(transport, ledger_root=self.base / "ledger", run_id="run-1", arm="advisory-on",
                                  decision=decision, marker="M1", provider="fixture",
                                  budget=dict(max_model_requests=3))

    def test_default_is_no_transport(self):
        self.assertIsNone(build_transport(None, model="jev-1.14.0", parse=parse))

    def test_refuses_an_unusable_sender_or_model(self):
        for send, model in (("not callable", "m"), (lambda request: REPLY, ""), (lambda request: REPLY, None)):
            with self.subTest(send=send, model=model):
                with self.assertRaises(ContractError):
                    build_transport(send, model=model, parse=parse)

    def test_one_attempt_per_request_with_the_pinned_model(self):
        sent = []
        transport = build_transport(lambda request: sent.append(request) or REPLY, model="jev-1.14.0", parse=parse)
        value = transport(dict(model="other", prompt="p"))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["model"], "jev-1.14.0")
        self.assertEqual(value["http_status"], 200)
        self.assertEqual(value["answers"], REPLY["body"]["answers"])

    def test_a_send_error_propagates_once_and_the_decision_never_reopens(self):
        sent = []

        def lost(request):
            sent.append(request)
            raise ConnectionError("lost")
        call = self.ledgered(build_transport(lost, model="jev-1.14.0", parse=parse))
        with self.assertRaises(ConnectionError):
            call(dict(prompt="p"))
        with self.assertRaisesRegex(ContractError, "DECISION_ALREADY_ATTEMPTED"):
            call(dict(prompt="p"))
        self.assertEqual(len(sent), 1)
        summary = ledger_summary(self.base / "ledger")
        self.assertEqual((summary["reserved_attempts"], summary["transport_raised_unknown"]), (1, 1))

    def test_a_401_reply_reaches_the_ledger_stop(self):
        call = self.ledgered(build_transport(lambda request: dict(REPLY, http_status=401),
                                             model="jev-1.14.0", parse=parse))
        call(dict(prompt="p"))
        with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"):
            self.ledgered(build_transport(lambda request: self.fail("dead key retried"),
                                          model="jev-1.14.0", parse=parse), decision="d2")(dict(prompt="p"))
        self.assertTrue(ledger_summary(self.base / "ledger")["stopped"])


if __name__ == "__main__":
    unittest.main()
