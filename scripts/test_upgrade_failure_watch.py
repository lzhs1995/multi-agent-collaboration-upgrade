"""Native failure classification and bounded original-journal observation."""
import copy
import tempfile
import unittest
from pathlib import Path

from orchestration_common import ContractError
from test_orchestration_native import NativeFixture, STAMP
from upgrade_failure_watch import CodexFailureWatch

TURN = "01a14b64-92de-7931-a649-61ef10b038ac"


class FailureWatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.f = NativeFixture(Path(tmp.name), "codex")
        self.f.session["freeze_fence"] = self.f.reader.snapshot()

    def event(self, **updates):
        payload = dict(type="task_complete", turn_id=TURN,
                       error={"message": "HTTP 400 invalid_encrypted_content"})
        payload.update(updates)
        return dict(type="event_msg", timestamp=STAMP, payload=payload)

    def watch(self):
        return CodexFailureWatch(self.f.session, fixtures=True)

    def abort(self, **updates):
        payload = dict(type="turn_aborted", turn_id=TURN, reason="interrupted")
        payload.update(updates)
        return dict(type="event_msg", timestamp=STAMP, payload=payload)

    def test_real_terminal_event_is_pinned_and_preserved(self):
        watch = self.watch()
        ref = self.f.append(self.event())
        failure = watch.observe()
        self.assertEqual(failure["record"], ref)
        self.assertEqual(failure["turn_id"], TURN)
        self.assertEqual(failure["input_operations"], 0)
        self.f.append(self.event(error=None))
        self.assertEqual(watch.observe(), failure)

    def test_old_error_before_freeze_does_not_fail_new_campaign(self):
        self.f.append(self.event())
        self.f.session["freeze_fence"] = self.f.reader.snapshot()
        self.assertIsNone(self.watch().observe())

    def test_quotes_tool_errors_and_success_are_not_terminal_failures(self):
        self.f.append(self.f.message("assistant", str(self.event())))
        self.f.append(dict(type="response_item", payload=self.event()["payload"]))
        self.f.append(self.event(type="tool_error"))
        for error in (None, False, {}, {"message": ""}, {"message": True}):
            self.f.append(self.event(error=error))
        self.assertIsNone(self.watch().observe())

    def test_budget_advances_without_rescanning_and_finds_later_error(self):
        watch = self.watch()
        for _ in range(5):
            self.f.append(self.event(error=None))
        ref = self.f.append(self.event())
        self.assertIsNone(watch.observe(max_records=2))
        self.assertTrue(watch.pending)
        offset = watch.offset
        self.assertIsNone(watch.observe(max_records=2))
        self.assertGreater(watch.offset, offset)
        self.assertEqual(watch.observe(max_records=2)["record"], ref)
        self.assertFalse(watch.pending)

    def test_partial_append_waits_and_then_reads_original_completed_record(self):
        watch = self.watch()
        from orchestration_common import canonical
        raw = canonical(self.event())
        with self.f.path.open("ab") as stream:
            stream.write(raw[:-1])
        self.assertIsNone(watch.observe())
        self.assertTrue(watch.pending)
        with self.f.path.open("ab") as stream:
            stream.write(raw[-1:])
        self.assertIsNotNone(watch.observe())

    def test_known_failure_is_not_masked_by_later_partial_append(self):
        watch = self.watch()
        self.f.append(self.event())
        failure = watch.observe()
        with self.f.path.open("ab") as stream:
            stream.write(b'{"type":')
        self.assertEqual(watch.observe(), failure)

    def test_saved_failure_survives_restart_and_append_but_not_changed_evidence(self):
        watch = self.watch()
        self.f.append(self.event())
        saved = watch.observe()
        self.f.append(self.event(error=None))
        resumed = self.watch()
        self.assertNotEqual(resumed.observe()["observed_eof"], saved["observed_eof"])
        resumed.verify_saved(saved)
        for field, value in (("message", "other"), ("turn_id", "other"),
                             ("observed_eof", self.f.session["freeze_fence"])):
            with self.assertRaises(ContractError):
                resumed.verify_saved(dict(saved, **{field: value}))

    def test_empty_or_complete_success_observation_does_not_hold_driver(self):
        watch = self.watch()
        self.assertIsNone(watch.observe())
        self.assertFalse(watch.pending)
        self.f.append(self.event(error=None))
        self.assertIsNone(watch.observe())
        self.assertFalse(watch.pending)

    def test_invalid_budget_is_rejected(self):
        watch = self.watch()
        for budget in (0, -1, True, 1.5, 257):
            with self.assertRaises(ContractError):
                watch.observe(budget)

    def test_replaced_file_and_changed_cursor_evidence_are_rejected(self):
        watch = self.watch()
        self.f.append(self.event(error=None))
        self.assertIsNone(watch.observe())
        raw = self.f.path.read_bytes().replace(b'task_complete', b'task_changed!')
        self.f.path.write_bytes(raw)
        with self.assertRaises(ContractError):
            watch.observe()
        watch = self.watch()
        self.f.path.rename(self.f.path.with_suffix(".saved"))
        self.f.path.write_bytes(raw)
        with self.assertRaises(ContractError):
            watch.observe()

    def test_changed_freeze_and_malformed_terminal_identity_rejected(self):
        bad = copy.deepcopy(self.f.session)
        bad["freeze_fence"]["tail_sha256"] = "0" * 64
        with self.assertRaises(ContractError):
            CodexFailureWatch(bad, fixtures=True)
        self.f.append(self.event(turn_id="other"))
        with self.assertRaises((ContractError, ValueError)):
            self.watch().observe()

    def test_native_abort_precedes_background_completion_and_later_success(self):
        watch = self.watch()
        ref = self.f.append(self.abort())
        self.f.append(dict(type="event_msg", timestamp=STAMP, payload=dict(
            type="item_completed", item=dict(type="CommandExecution", status="completed",
                                              exit_code=0, stdout="BOOTSTRAP_READY"))))
        failure = watch.observe()
        self.assertEqual(failure["status"], "NATIVE_CODEX_TURN_ABORTED")
        self.assertEqual(failure["record"], ref)
        self.assertEqual(failure["reason"], "interrupted")
        self.assertEqual(failure["turn_id"], TURN)
        self.assertEqual(failure["input_operations"], 0)
        self.assertFalse(watch.pending)
        self.f.append(self.event(error=None))
        self.assertEqual(watch.observe(), failure)

    def test_all_canonical_native_abort_reasons_are_retained_exactly(self):
        for reason in ("interrupted", "replaced", "review_ended", "budget_limited"):
            with self.subTest(reason=reason):
                self.f.session["freeze_fence"] = self.f.reader.snapshot()
                self.f.append(self.abort(reason=reason))
                failure = self.watch().observe()
                self.assertEqual(failure["status"], "NATIVE_CODEX_TURN_ABORTED")
                self.assertEqual(failure["reason"], reason)
                self.assertNotIn("message", failure)
                self.assertNotIn("codex_error_info", failure)

    def test_first_native_terminal_record_wins_in_both_orders(self):
        for events in ((self.abort(), self.event()), (self.event(), self.abort())):
            with self.subTest(first=events[0]["payload"]["type"]):
                self.f.session["freeze_fence"] = self.f.reader.snapshot()
                first = self.f.append(events[0])
                self.f.append(events[1])
                watch = self.watch()
                saved = watch.observe()
                self.assertEqual(saved["record"], first)
                self.f.append(events[1])
                self.assertEqual(watch.observe(), saved)
                self.assertEqual(self.watch().observe()["record"], first)

    def test_prefreeze_abort_and_quoted_abort_do_not_fail_campaign(self):
        from orchestration_common import canonical
        self.f.append(self.abort())
        self.f.session["freeze_fence"] = self.f.reader.snapshot()
        self.f.append(self.f.message("assistant", canonical(self.abort()).decode()))
        self.f.append(dict(type="response_item", timestamp=STAMP, payload=self.abort()["payload"]))
        self.f.append(dict(type="event_msg", timestamp=STAMP,
                           payload=dict(type="agent_message", message=self.abort())))
        self.f.append(dict(type="event_msg", timestamp=STAMP,
                           payload=dict(type="tool_error", reason="interrupted", turn_id=TURN)))
        self.f.append(dict(type="turn_aborted", timestamp=STAMP, reason="interrupted", turn_id=TURN))
        watch = self.watch()
        self.assertIsNone(watch.observe())
        self.assertFalse(watch.pending)

    def test_abort_at_and_after_observation_budget_boundary(self):
        for budget in (1, 2, 256):
            for before in (budget - 1, budget):
                with self.subTest(budget=budget, preceding_records=before):
                    self.f.session["freeze_fence"] = self.f.reader.snapshot()
                    for _ in range(before):
                        self.f.append(self.event(error=None))
                    ref = self.f.append(self.abort())
                    self.f.append(self.event(error=None))
                    watch = self.watch()
                    failure = watch.observe(max_records=budget)
                    if before == budget:
                        self.assertIsNone(failure)
                        self.assertTrue(watch.pending)
                        self.assertEqual(watch.offset, ref["offset"])
                        failure = watch.observe(max_records=budget)
                    self.assertEqual(failure["record"], ref)
                    self.assertFalse(watch.pending)
                    self.assertEqual(watch.offset, ref["offset"] + ref["length"])

    def test_noncanonical_or_missing_abort_reason_is_rejected(self):
        for reason in (None, True, 1, {}, [], "", "INTERRUPTED", "interrupted ",
                       "user", "budget-limited", "new_reason"):
            with self.subTest(reason=reason):
                self.f.session["freeze_fence"] = self.f.reader.snapshot()
                self.f.append(self.abort(reason=reason))
                with self.assertRaisesRegex(ContractError, "canonical native reason"):
                    self.watch().observe()
        self.f.session["freeze_fence"] = self.f.reader.snapshot()
        event = self.abort()
        del event["payload"]["reason"]
        self.f.append(event)
        with self.assertRaisesRegex(ContractError, "canonical native reason"):
            self.watch().observe()

    def test_abort_requires_canonical_turn_identity_and_timestamp(self):
        for turn in (None, "", "other", TURN.upper(), TURN.replace("-", "")):
            with self.subTest(turn=turn):
                self.f.session["freeze_fence"] = self.f.reader.snapshot()
                self.f.append(self.abort(turn_id=turn))
                with self.assertRaises((ContractError, ValueError)):
                    self.watch().observe()
        self.f.session["freeze_fence"] = self.f.reader.snapshot()
        self.f.append(dict(self.abort(), timestamp="not-a-timestamp"))
        with self.assertRaises((ContractError, ValueError)):
            self.watch().observe()

    def test_saved_abort_survives_restart_without_evidence_mutation(self):
        self.f.append(self.abort())
        saved = self.watch().observe()
        self.f.append(self.event(error=None))
        resumed = self.watch()
        self.assertNotEqual(resumed.observe()["observed_eof"], saved["observed_eof"])
        resumed.verify_saved(saved)
        for field, value in (("reason", "replaced"), ("status", "NATIVE_CODEX_TURN_FAILED"),
                             ("record", self.f.session["freeze_fence"]),
                             ("observed_eof", self.f.session["freeze_fence"])):
            with self.subTest(field=field), self.assertRaises(ContractError):
                resumed.verify_saved(dict(saved, **{field: value}))

    def test_known_abort_is_not_masked_by_later_partial_append(self):
        self.f.append(self.abort())
        watch = self.watch()
        failure = watch.observe()
        with self.f.path.open("ab") as stream:
            stream.write(b'{"type":')
        self.assertEqual(watch.observe(), failure)


if __name__ == "__main__":
    unittest.main()
