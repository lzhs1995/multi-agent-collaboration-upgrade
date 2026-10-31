"""Intent-first JEV request ledger and its production entry; offline, zero network."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jev_advice_run import LEDGER_DIRECTORY, run_and_record
from jev_request_ledger import ledger_summary, ledgered_transport, record_result
from orchestration_common import ContractError

HERE = Path(__file__).resolve().parent


class Forbidden(Exception):
    """An adapter that raises its 403 instead of returning it."""
    status_code = 403

OK = dict(answers=dict(work_kind="coding", work_kind_applies=0.9), http_status=200,
          usage=dict(input_tokens=11, output_tokens=3))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "ledger"

    def tearDown(self):
        self._tmp.cleanup()

    def wrap(self, transport, budget=1, arm="advisory-on", decision="d1"):
        return ledgered_transport(transport, ledger_root=self.root, run_id="run-1", arm=arm, decision=decision,
                                  marker="M1", provider="fixture", budget=dict(max_model_requests=budget))

    def results(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob("*.result.json"))]

    def test_p1_one_response_is_one_reservation_and_one_result(self):
        self.assertEqual(self.wrap(lambda request: OK)(dict(model="m")), OK)
        summary = ledger_summary(self.root)
        self.assertEqual((summary["reserved_attempts"], summary["transport_returned"]), (1, 1))
        self.assertEqual(self.results()[0]["status"], "RESPONDED")
        self.assertEqual(self.results()[0]["usage"], OK["usage"])
        self.assertEqual(summary["gateway_authoritative_ledger"], "NOT_VERIFIED")

    def test_n1_zero_budget_and_n2_off_arm_build_no_ledger(self):
        for budget, arm, error in ((0, "advisory-on", "BUDGET_NOT_POSITIVE"),
                                   (1, "advisory-off", "LEDGER_ONLY_FOR_THE_ON_ARM")):
            with self.assertRaisesRegex(ContractError, error):
                self.wrap(lambda request: self.fail("transport must not be called"), budget=budget, arm=arm)
        self.assertFalse(self.root.exists())

    def test_n3_transport_exception_is_unknown_and_not_retried(self):
        calls = []

        def boom(request):
            calls.append(request)
            raise ConnectionError("lost")
        call = self.wrap(boom, budget=2)
        with self.assertRaises(ConnectionError):
            call(dict(model="m"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.results()[0]["status"], "UNKNOWN")
        self.assertEqual(ledger_summary(self.root)["transport_returned"], 0)

    def test_n4_crash_after_intent_stays_unresolved_never_success(self):
        def crash(request):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.wrap(crash)(dict(model="m"))
        summary = ledger_summary(self.root)
        self.assertEqual((summary["reserved_attempts"], summary["transport_returned"]), (1, 0))
        self.assertEqual(summary["intents_without_result_unknown"], ["000001"])

    def test_n5_lost_response_is_malformed(self):
        self.assertIsNone(self.wrap(lambda request: None)(dict(model="m")))
        self.assertEqual(self.results()[0]["status"], "MALFORMED")

    def test_n6_duplicate_result_is_refused_and_original_kept(self):
        self.wrap(lambda request: OK)(dict(model="m"))
        path = self.root / "000001.result.json"
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            record_result(self.root, "000001", dict(status="RESPONDED", forged=True))
        self.assertEqual(path.read_bytes(), before)

    def test_n7_n8_auth_refusal_stops_before_any_new_intent(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.root = Path(self._tmp.name) / ("ledger-%d" % status)
                call = self.wrap(lambda request: dict(http_status=status, error="auth"), budget=3)
                call(dict(model="m"))
                self.assertEqual(self.results()[0]["status"], "AUTH_REFUSED")
                with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"):
                    call(dict(model="m"))
                self.assertEqual(ledger_summary(self.root)["reserved_attempts"], 1)

    def test_n9_shared_budget_exhausted_across_decisions_before_intent(self):
        self.wrap(lambda request: OK, budget=1, decision="node-a")(dict(model="m"))
        with self.assertRaisesRegex(ContractError, "MODEL_REQUEST_BUDGET_EXHAUSTED"):
            self.wrap(lambda request: self.fail("over budget"), budget=1, decision="node-b")(dict(model="m"))
        self.assertEqual(ledger_summary(self.root)["reserved_attempts"], 1)

    def test_n10_gateway_authority_is_never_verified(self):
        self.wrap(lambda request: dict(OK, gateway_authoritative_ledger="VERIFIED"))(dict(model="m"))
        (self.root / "gateway.json").write_text('{"verified": true}')
        self.assertEqual(ledger_summary(self.root)["gateway_authoritative_ledger"], "NOT_VERIFIED")

    def test_n11_ledger_bound_to_one_run_arm_marker(self):
        self.wrap(lambda request: OK)
        with self.assertRaisesRegex(ContractError, "LEDGER_BOUND_TO_ANOTHER_RUN_OR_ARM"):
            ledgered_transport(lambda request: OK, ledger_root=self.root, run_id="run-2", arm="advisory-on",
                               decision="d1", marker="M1", provider="fixture", budget=dict(max_model_requests=1))

    def test_n12_concurrent_call_fails_fast_while_one_is_in_flight(self):
        inner = []

        def nested(request):
            with self.assertRaises(ContractError):
                call(dict(model="m"))
            inner.append(True)
            return OK
        call = self.wrap(nested, budget=2)
        call(dict(model="m"))
        self.assertEqual(inner, [True])
        self.assertEqual(ledger_summary(self.root)["reserved_attempts"], 1)

    def test_n13_auth_result_without_stop_file_still_refuses_every_decision(self):
        self.wrap(lambda request: dict(OK, http_status=401), budget=3)(dict(model="m"))
        (self.root / "STOP.json").unlink()  # the STOP write failed or the process died
        for decision in ("d1", "d2"):
            with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"):
                self.wrap(lambda request: self.fail("dead key retried"), budget=3,
                          decision=decision)(dict(model="m"))
        self.assertTrue(ledger_summary(self.root)["stopped"])
        self.assertEqual(ledger_summary(self.root)["reserved_attempts"], 1)

    def test_n14_unknown_never_reopens_its_decision(self):
        def boom(request):
            raise ConnectionError("lost")
        with self.assertRaises(ConnectionError):
            self.wrap(boom, budget=3)(dict(model="m"))
        with self.assertRaisesRegex(ContractError, "DECISION_ALREADY_ATTEMPTED"):
            self.wrap(lambda request: self.fail("retried"), budget=3)(dict(model="m"))
        self.assertEqual(ledger_summary(self.root)["reserved_attempts"], 1)

    def test_n15_unresolved_intent_refuses_every_new_request(self):
        def crash(request):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.wrap(crash, budget=3)(dict(model="m"))
        for decision in ("d1", "d2"):
            with self.assertRaisesRegex(ContractError, "UNRESOLVED_INTENT_REFUSES_NEW_REQUESTS"):
                self.wrap(lambda request: self.fail("crash window reopened"), budget=3,
                          decision=decision)(dict(model="m"))

    def test_n16_raised_auth_status_stops_the_arm(self):
        class Refused(Exception):
            status_code = 403
        def refuse(request):
            raise Refused("forbidden")
        with self.assertRaises(Refused):
            self.wrap(refuse, budget=3)(dict(model="m"))
        self.assertEqual(self.results()[0]["status"], "AUTH_REFUSED")
        self.assertTrue((self.root / "STOP.json").exists())


    def test_n18_returned_and_raised_403_are_counted_apart_and_both_stop(self):
        self.root = Path(self._tmp.name) / "returned"
        self.wrap(lambda request: dict(OK, http_status=403), budget=3)(dict(model="m"))
        returned = ledger_summary(self.root)
        self.assertEqual((returned["transport_returned"], returned["transport_raised_auth"],
                          returned["transport_raised_unknown"], returned["stopped"]), (1, 0, 0, True))

        def refuse(request):
            raise Forbidden("forbidden")
        self.root = Path(self._tmp.name) / "raised"
        with self.assertRaises(Forbidden):
            self.wrap(refuse, budget=3)(dict(model="m"))
        raised = ledger_summary(self.root)
        self.assertEqual((raised["transport_returned"], raised["transport_raised_auth"],
                          raised["transport_raised_unknown"], raised["stopped"]), (0, 1, 0, True))

    def test_n17_second_process_fails_fast_while_a_call_is_in_flight(self):
        script = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                  "from jev_request_ledger import ledgered_transport\n"
                  "from orchestration_common import ContractError\n"
                  "call = ledgered_transport(lambda request: {}, ledger_root=sys.argv[2], run_id='run-1',\n"
                  "    arm='advisory-on', decision='other', marker='M2', provider='fixture',\n"
                  "    budget=dict(max_model_requests=2))\n"
                  "try:\n    call(dict(model='m'))\n    print('CALLED')\n"
                  "except ContractError as exc:\n    print('REFUSED', exc)\n")
        seen = []

        def in_flight(request):
            seen.append(subprocess.run([sys.executable, "-B", "-c", script, str(HERE), str(self.root)],
                                       capture_output=True, text=True, timeout=30, check=False))
            return OK
        self.wrap(in_flight, budget=2)(dict(model="m"))
        self.assertTrue(seen[0].stdout.startswith("REFUSED"), seen[0].stdout + seen[0].stderr)
        self.assertEqual(ledger_summary(self.root)["reserved_attempts"], 1)


class ProductionEntryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.node = Path(self._tmp.name) / "nodes" / "unbound"
        self.ledger = Path(self._tmp.name) / LEDGER_DIRECTORY

    def tearDown(self):
        self._tmp.cleanup()

    def run_entry(self, **changes):
        values = dict(ledger_root=self.ledger, run_id="run-1", decision="unbound",
                      state_text="route the unbound node", lanes={}, bound=False,
                      arm="advisory-off", marker="M1", provider="fixture",
                      budget=dict(max_model_requests=0), model="jev-1.14.0", transport=None)
        values.update(changes)
        return run_and_record(self.node, **values)

    def test_default_is_zero_calls_and_no_ledger(self):
        answer = self.run_entry()
        self.assertEqual(answer["model_requests"], 0)
        self.assertIsNone(answer["ledger"])
        self.assertFalse(self.ledger.exists())
        journal = json.loads((self.node / "advice-worker-journal.json").read_text())
        self.assertEqual([e["model_requests"] for e in journal["entries"]], [0])

    def test_transport_is_ignored_unless_on_arm_with_positive_budget(self):
        never = lambda request: self.fail("transport must not be called")
        for index, changes in enumerate((dict(arm="advisory-on"), dict(budget=dict(max_model_requests=1)),
                                         dict(arm="advisory-on", budget=None))):
            with self.subTest(**{k: str(v) for k, v in changes.items()}):
                self.node = Path(self._tmp.name) / "nodes" / ("n%d" % index)
                answer = self.run_entry(transport=never, decision="n%d" % index, **changes)
                self.assertEqual(answer["model_requests"], 0)
                self.assertFalse(self.ledger.exists())

    def test_on_arm_call_is_ledgered_and_journaled(self):
        answer = self.run_entry(arm="advisory-on", budget=dict(max_model_requests=1),
                                transport=lambda request: OK)
        self.assertEqual(answer["model_requests"], 1)
        self.assertEqual(answer["ledger"]["reserved_attempts"], 1)
        self.assertEqual(answer["ledger"]["gateway_authoritative_ledger"], "NOT_VERIFIED")

    def test_two_nodes_share_one_run_budget(self):
        on = dict(arm="advisory-on", budget=dict(max_model_requests=1))
        self.run_entry(transport=lambda request: OK, **on)
        self.node = Path(self._tmp.name) / "nodes" / "second"
        with self.assertRaisesRegex(ContractError, "MODEL_REQUEST_BUDGET_EXHAUSTED"):
            self.run_entry(transport=lambda request: self.fail("over budget"), decision="second", **on)
        self.assertEqual(ledger_summary(self.ledger)["reserved_attempts"], 1)

    def test_reentry_never_calls_the_transport_again(self):
        on = dict(arm="advisory-on", budget=dict(max_model_requests=3))
        self.run_entry(transport=lambda request: OK, **on)
        with self.assertRaisesRegex(ContractError, "DECISION_ALREADY_RECORDED"):
            self.run_entry(transport=lambda request: self.fail("journal re-entry"), **on)
        self.node = Path(self._tmp.name) / "nodes" / "again"  # no journal, same decision
        with self.assertRaisesRegex(ContractError, "DECISION_ALREADY_ATTEMPTED"):
            self.run_entry(transport=lambda request: self.fail("intent re-entry"), **on)
        self.assertEqual(ledger_summary(self.ledger)["reserved_attempts"], 1)

    def test_auth_refusal_raises_after_journal_instead_of_fallback_advice(self):
        with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"):
            self.run_entry(arm="advisory-on", budget=dict(max_model_requests=3),
                           transport=lambda request: dict(OK, http_status=401))
        self.assertTrue((self.node / "advice-worker-journal.json").exists())


    def test_run_wide_stop_is_reported_before_any_duplicate_reason(self):
        def raise_403(request):
            raise Forbidden("forbidden")
        on = dict(arm="advisory-on", budget=dict(max_model_requests=5))
        for index, transport in enumerate((lambda request: dict(OK, http_status=401), raise_403)):
            with self.subTest(index=index):
                self.ledger = Path(self._tmp.name) / ("run-%d" % index) / LEDGER_DIRECTORY
                self.node = Path(self._tmp.name) / ("first-%d" % index) / "a"
                with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"):
                    self.run_entry(transport=transport, decision="a", **on)
                never = lambda request: self.fail("dead key retried")
                for node, decision in (("a", "a"), ("b", "other"), ("c", "a")):
                    self.node = Path(self._tmp.name) / ("first-%d" % index) / node
                    with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"):
                        self.run_entry(transport=never, decision=decision, **on)
                with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"):
                    self.run_entry()  # the off arm of a stopped run is told too
                self.assertEqual(ledger_summary(self.ledger)["reserved_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
