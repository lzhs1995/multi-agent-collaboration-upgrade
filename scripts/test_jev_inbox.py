"""Focused tests for the DP2 inbox path (R505): order, full read, fallback, dedup, counting, 401 STOP."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import jev_inbox
import jev_on_call
from jev_request_ledger import ledger_summary, ledgered_transport
from jev_transport import build_transport

MODEL = "jev-1.14.0"
PLACEHOLDER = "placeholder-not-a-key"


def msg(i, body, eligible=True):
    raw = body.encode()
    return dict(id="m%d" % i, body=body, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), eligible=eligible)


def ok(noul, kind="request"):
    return dict(answers=dict(message_kind=kind, message_actionable=noul), error=None, usage=None, http_status=200)


def fake(answer_for):
    calls = []

    def transport_for(decision):
        def call(request):
            calls.append((decision, request))
            return answer_for(request)
        return call
    return transport_for, calls


class Response:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body.encode()


class OfflineInboxConnection:
    """Stands in for the HTTPS connection; answers by message content, never touches the network."""

    def __init__(self, statuses=()):
        self.sent, self.statuses = [], list(statuses)

    def request(self, method, path, body, headers):
        self.sent.append(json.loads(body))

    def getresponse(self):
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return Response(status, "")
        noul = 0.95 if "ACT" in self.sent[-1]["state"]["message"] else 0.05
        probabilities = {k: 1.0 if k == "request" else 0.0 for k in jev_on_call.MESSAGE}
        answers = dict(message_kind=dict(type="choice", choice="request", confidence=1.0, probabilities=probabilities),
                       message_actionable=dict(type="noul", noul=noul))
        return Response(200, json.dumps(dict(model=MODEL, answers=answers,
                                             usage=dict(input_tokens=700, output_tokens=120))))


class InboxOrderTests(unittest.TestCase):
    def test_off_arm_keeps_arrival_order_and_makes_no_call(self):
        tf, calls = fake(lambda r: self.fail("the off arm must not call"))
        out = jev_inbox.order_inbox([msg(0, "a"), msg(1, "b ACT")], arm="advisory-off", transport_for=tf, model=MODEL)
        self.assertEqual((out["order"], calls, out["model_requests"], out["reordered"]), (["m0", "m1"], [], 0, False))

    def test_on_arm_moves_only_actionable_eligible_messages_forward(self):
        tf, calls = fake(lambda r: ok(0.95 if "ACT" in r["state"] else 0.05))
        msgs = [msg(0, "plain"), msg(1, "ineligible ACT", eligible=False), msg(2, "do this ACT")]
        out = jev_inbox.order_inbox(msgs, arm="advisory-on", transport_for=tf, model=MODEL)
        self.assertEqual(out["order"], ["m2", "m1", "m0"])
        self.assertEqual((len(calls), out["model_requests"]), (2, 2))
        self.assertNotIn("ineligible ACT", [r["state"] for _, r in calls])

    def test_body_that_differs_from_its_pin_is_refused_before_any_call(self):
        tf, calls = fake(lambda r: self.fail("must not call"))
        with self.assertRaises(ValueError):
            jev_inbox.order_inbox([dict(msg(0, "body"), body="edited body")], arm="advisory-on",
                                  transport_for=tf, model=MODEL)
        self.assertEqual(calls, [])

    def test_uncertain_or_malformed_answers_keep_original_order(self):
        for answer in (ok(0.5), ok(0.99, kind="spam"), "not a dict"):
            tf, _ = fake(lambda r, a=answer: a)
            out = jev_inbox.order_inbox([msg(0, "first"), msg(1, "second ACT")], arm="advisory-on",
                                        transport_for=tf, model=MODEL)
            self.assertEqual(out["order"], ["m0", "m1"])

    def test_identical_bodies_are_asked_once_and_known_bodies_are_reused(self):
        tf, calls = fake(lambda r: ok(0.95))
        first = msg(0, "same ACT")
        out = jev_inbox.order_inbox([first, msg(1, "same ACT")], arm="advisory-on", transport_for=tf, model=MODEL)
        self.assertEqual(len(calls), 1)
        self.assertEqual([d["status"] for d in out["decisions"]], ["SUGGEST", "DUPLICATE_BODY"])
        tf2, calls2 = fake(lambda r: self.fail("a judged body must be reused"))
        out2 = jev_inbox.order_inbox([msg(0, "same ACT")], arm="advisory-on", transport_for=tf2, model=MODEL,
                                     judged={first["sha256"]: dict(front=True)})
        self.assertEqual((calls2, out2["decisions"][0]["status"]), ([], "REUSED"))


class InboxProductionPathTests(unittest.TestCase):
    """jev_on_call.bind -> jev_transport -> jev_request_ledger -> jev_inbox, with an offline connection."""

    def run_path(self, connection, messages):
        root = Path(os.path.realpath(tempfile.mkdtemp(prefix="jev-inbox-ledger-")))
        send, parse = jev_on_call.bind(connection, PLACEHOLDER, model=MODEL)
        transport = build_transport(send, model=MODEL, parse=parse)

        def transport_for(decision):
            return ledgered_transport(transport, ledger_root=root, run_id="R505-SMOKE", arm="advisory-on",
                                      decision=decision, marker="R505_SMOKE", provider="typesafe",
                                      budget=dict(max_model_requests=10))
        return jev_inbox.order_inbox(messages, arm="advisory-on", transport_for=transport_for, model=MODEL), root

    def test_no_network_smoke_counts_requests_and_usage_and_sends_only_inbox_questions(self):
        conn = OfflineInboxConnection()
        out, root = self.run_path(conn, [msg(0, "fyi only"), msg(1, "please run the review ACT")])
        self.assertEqual(out["order"], ["m1", "m0"])
        self.assertEqual((out["model_requests"], len(conn.sent)), (2, 2))
        self.assertEqual(out["usage"], dict(input_tokens=1400, output_tokens=240))
        self.assertTrue(all(set(p["questions"]) == set(jev_on_call.INBOX_QUESTIONS) and p["state"]["task"] == ""
                            for p in conn.sent))
        self.assertEqual(ledger_summary(root)["reserved_attempts"], 2)

    def test_401_stops_at_once_and_later_messages_keep_order_unasked(self):
        conn = OfflineInboxConnection(statuses=[401])
        out, root = self.run_path(conn, [msg(0, "first ACT"), msg(1, "second ACT")])
        self.assertEqual(len(conn.sent), 1)
        self.assertEqual(out["order"], ["m0", "m1"])
        self.assertEqual([d["status"] for d in out["decisions"]], ["JEV_ERROR", "NOT_ASKED_AFTER_STOP"])
        self.assertTrue(out["stopped"].startswith("AUTH_REFUSED"))
        self.assertEqual(ledger_summary(root)["reserved_attempts"], 1)


import test_jev_registration as registration_tests  # noqa: E402  module import keeps its tests out of this file
from test_orchestration_bind_unbound import BindFixtureMixin  # noqa: E402


class EchoInboxConnection(OfflineInboxConnection):
    """Echoes the registered model so the S1 provenance check passes offline."""

    def getresponse(self):
        response = super().getresponse()
        if response.status == 200:
            value = json.loads(response._body)
            value["model"] = self.sent[-1]["model"]
            response._body = json.dumps(value)
        return response


class InboxEntryTests(BindFixtureMixin, unittest.TestCase):
    """D1: record_inbox_order under the real coordinator gate, fixture campaign only."""
    reg = registration_tests.RegistrationTests.reg

    def test_off_arm_entry_keeps_order_makes_no_request_and_refuses_a_repeat_event(self):
        from jev_advice_run import record_inbox_order
        out = record_inbox_order(self.r.co(self.run), "unbound", "evt-1", [msg(0, "a"), msg(1, "b ACT")])
        self.assertEqual((out["arm"], out["order"]["order"], out["order"]["model_requests"]),
                         ("advisory-off", ["m0", "m1"], 0))
        with self.assertRaises(Exception):
            record_inbox_order(self.r.co(self.run), "unbound", "evt-1", [msg(0, "a")])

    def test_on_arm_entry_orders_through_the_ledger_and_reuses_judged_bodies(self):
        from jev_advice_run import LEDGER_DIRECTORY, record_inbox_order
        reg = self.reg(budget=dict(max_model_requests=5))
        conn = EchoInboxConnection()
        first = record_inbox_order(self.r.co(self.run), "unbound", "evt-1", [msg(0, "fyi"), msg(1, "do it ACT")],
                                   registration=reg, connection=conn, credential=PLACEHOLDER)
        self.assertEqual((first["order"]["order"], len(conn.sent)), (["m1", "m0"], 2))
        second = record_inbox_order(self.r.co(self.run), "unbound", "evt-2",
                                    [msg(0, "do it ACT"), msg(1, "new ACT")],
                                    registration=reg, connection=conn, credential=PLACEHOLDER)
        self.assertEqual([d["status"] for d in second["order"]["decisions"]], ["REUSED", "SUGGEST"])
        self.assertEqual(len(conn.sent), 3)
        self.assertEqual(ledger_summary(self.co.root / LEDGER_DIRECTORY)["reserved_attempts"], 3)


if __name__ == "__main__":
    unittest.main()
