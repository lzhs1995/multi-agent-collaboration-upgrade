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


if __name__ == "__main__":
    unittest.main()
