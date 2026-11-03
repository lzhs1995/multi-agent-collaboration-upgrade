"""Offline semantic controls; synthetic files never establish live Goal success."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from orchestration_common import ContractError, canonical
from test_orchestration_native import NativeFixture
from upgrade_goal_observer import collect, goal_event, main, observe

CONDITION = "Finish the registered workload; preserve pending messages."


class GoalObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.f = NativeFixture(self.root)
        self.start = self.f.reader.snapshot()
        self.serial = 0

    def event(self, **fields):
        self.serial += 1
        return dict(type="attachment", uuid=f"native-goal-{self.serial}",
                    sessionId=self.f.session["session_id"], timestamp="2026-10-19T05:00:01Z",
                    attachment=dict(type="goal_status", met=False, condition=CONDITION, **fields))

    def append(self, **fields):
        record = self.event(**fields)
        self.f.append(record)
        return record

    def result(self, **limits):
        return observe(self.f.reader, self.start, self.f.reader.snapshot(), CONDITION, **limits)

    def test_set_check_and_later_model_are_separate_observations(self):
        self.append(sentinel=True)
        self.append(reason="Work remains")
        self.f.append(self.f.message("assistant", "Continued", at="2026-10-19T05:00:02Z"))
        result = self.result()
        self.assertEqual(result["counts"]["sets"], 1)
        self.assertEqual(result["counts"]["unmet_checks"], 1)
        self.assertEqual(result["counts"]["model_records_after_check"], 1)
        self.assertEqual(result["last_observed_state"], "EXPECTED_GOAL_OBSERVED")
        self.assertTrue(result["coverage_complete"])
        self.assertFalse(result["continuous_goal_proven"])
        self.assertEqual(result["state_at_interval_end"], "UNKNOWN")
        self.assertEqual(result["communication_acceptance"], "NOT_EVALUATED")

    def test_clear_is_not_a_completed_goal(self):
        self.append(sentinel=True)
        clear = self.event(sentinel=True)
        clear["attachment"].update(met=True, condition=None)
        self.f.append(clear)
        result = self.result()
        self.assertEqual(result["events"][-1]["kind"], "CLEARED_OR_MET")
        self.assertEqual(result["last_observed_state"], "INACTIVE_OBSERVED")

    def test_failed_and_met_are_distinct(self):
        failed = self.event(failed=True)
        met = self.event()
        met["attachment"]["met"] = True
        self.assertEqual(goal_event(failed)["kind"], "FAILED")
        self.assertEqual(goal_event(met)["kind"], "MET")

    def test_quoted_prompt_tool_queue_and_sidechain_are_not_goal_state(self):
        goal = self.event()
        self.f.append(self.f.message("user", "/goal " + CONDITION))
        self.f.append(self.f.message("assistant", json.dumps(goal)))
        self.f.append(dict(type="queue-operation", content=json.dumps(goal)))
        self.f.append(dict(type="user", message=dict(role="user", content=[
            dict(type="tool_result", content=json.dumps(goal), tool_use_id="a")])))
        goal["isSidechain"] = True
        self.f.append(goal)
        result = self.result()
        self.assertEqual(result["events"], [])
        self.assertEqual(result["last_observed_state"], "UNKNOWN")

    def test_malformed_flags_conditions_and_counters_refuse(self):
        for fields in ({"met": "false"}, {"met": 0}, {"sentinel": 1}, {"failed": "false"},
                       {"condition": None}, {"condition": ""}, {"reason": []},
                       {"iterations": True}, {"iterations": -1}, {"met": True, "failed": True}):
            with self.subTest(fields=fields):
                record = self.event()
                record["attachment"].update(fields)
                with self.assertRaises(ContractError):
                    goal_event(record)

    def test_missing_timestamp_uuid_duplicate_or_backwards_event_refuses(self):
        for mutate in (lambda r: r.pop("timestamp"), lambda r: r.pop("uuid")):
            record = self.event()
            mutate(record)
            self.f.append(record)
            with self.assertRaises(ContractError):
                self.result()
            self.f.path.write_bytes(self.f.path.read_bytes()[:self.start["offset"]])
        r = self.append()
        self.f.append(r)
        with self.assertRaisesRegex(ContractError, "Duplicate"):
            self.result()
        self.f.path.write_bytes(self.f.path.read_bytes()[:self.start["offset"]])
        self.append()
        r = self.event()
        r["timestamp"] = "2026-10-18T05:00:01Z"
        self.f.append(r)
        with self.assertRaisesRegex(ContractError, "chronology"):
            self.result()

    def test_clear_or_changed_condition_breaks_check_to_model_pair(self):
        self.append(reason="unfinished")
        self.append(sentinel=True)
        changed = self.event()
        changed["attachment"]["condition"] = CONDITION + " changed"
        self.f.append(changed)
        self.f.append(self.f.message("assistant", "action", at="2026-10-19T05:00:02Z"))
        result = self.result()
        self.assertEqual(result["counts"]["model_records_after_check"], 0)
        self.assertEqual(result["last_observed_state"], "OTHER_GOAL_OBSERVED")

    def test_synthetic_errors_and_sidechain_do_not_count_as_continuation(self):
        self.append(reason="unfinished")
        for fields in ({"isApiErrorMessage": True}, {"isSidechain": True}):
            self.f.append(self.f.message("assistant", "error", at="2026-10-19T05:00:02Z", **fields))
        r = self.f.message("assistant", "synthetic", at="2026-10-19T05:00:02Z")
        r["message"]["model"] = "<synthetic>"
        self.f.append(r)
        self.assertEqual(self.result()["counts"]["model_records_after_check"], 0)

    def test_unmet_check_pairs_with_at_most_one_subsequent_model_record(self):
        self.append(reason="unfinished")
        for _ in range(3):
            self.f.append(self.f.message("assistant", "continued", at="2026-10-19T05:00:02Z"))
        self.assertEqual(self.result()["counts"]["model_records_after_check"], 1)

    def test_budget_keeps_partial_coverage_unknown(self):
        self.append(sentinel=True)
        self.append(reason="unfinished")
        for limits in ({"max_records": 1}, {"max_bytes": 1}, {"max_events": 1}):
            with self.subTest(limits=limits):
                r = self.result(**limits)
                self.assertFalse(r["coverage_complete"])
                self.assertIn("SCAN_BUDGET_EXHAUSTED", r["uncertainties"])
                self.assertEqual(r["state_at_interval_end"], "UNKNOWN")

    def test_unsupported_wire_channel_remains_unknown(self):
        self.f.append(dict(type="active_goal", value=dict(condition=CONDITION)))
        self.assertFalse(self.result()["coverage_complete"])

    def test_existing_history_before_start_does_not_count(self):
        self.append(sentinel=True)
        self.start = self.f.reader.snapshot()
        self.assertEqual(self.result()["counts"]["sets"], 0)

    def test_growth_past_end_is_not_observed_and_input_is_unchanged(self):
        self.append(sentinel=True)
        end = self.f.reader.snapshot()
        self.append(reason="later")
        before = self.f.path.read_bytes()
        result = observe(self.f.reader, self.start, end, CONDITION)
        self.assertEqual(result["counts"]["unmet_checks"], 0)
        self.assertEqual(self.f.path.read_bytes(), before)

    def test_changed_fence_inode_or_session_refuses(self):
        self.append(sentinel=True)
        end = self.f.reader.snapshot()
        original = self.f.path.read_bytes()
        self.f.path.write_bytes(original.replace(b"Finish", b"finish"))
        with self.assertRaises(ContractError):
            observe(self.f.reader, self.start, end, CONDITION)
        self.f.path.write_bytes(original)
        foreign = self.event()
        foreign["sessionId"] = "other-session"
        self.f.append(foreign)
        with self.assertRaisesRegex(ContractError, "another Claude"):
            self.result()
        self.f.path.rename(self.root / "preserved.jsonl")
        self.f.path.write_bytes(original)
        with self.assertRaises(ContractError):
            observe(self.f.reader, self.start, end, CONDITION)

    def test_partial_native_record_is_not_accepted(self):
        with self.f.path.open("ab") as stream:
            stream.write(b'{"type":"attachment"')
        with self.assertRaisesRegex(ContractError, "record boundary"):
            self.result()

    def test_public_entrypoint_authenticates_and_does_not_offer_fixture_bypass(self):
        self.append(sentinel=True)
        request = dict(schema="upgrade-goal-observation-request-v1", session=self.f.session,
                       start=self.start, end=self.f.reader.snapshot(), condition=CONDITION)
        path = self.root / "request.json"
        path.write_bytes(canonical(request))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["--request", str(path)])
        self.assertEqual(code, 2)  # This is a copied/offline journal.
        self.assertEqual(json.loads(output.getvalue())["status"], "UNKNOWN")
        with mock.patch("upgrade_goal_observer.NativeReader", return_value=self.f.reader) as factory:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(["--request", str(path)])
            factory.assert_called_once_with(self.f.session)  # no fixtures=True
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["counts"]["sets"], 1)

    def test_reject_codex_and_invalid_limits(self):
        for limit in (True, 0, -1, 100_001):
            with self.assertRaises(ContractError):
                self.result(max_records=limit)
        self.f.reader.provider = "codex"
        with self.assertRaisesRegex(ContractError, "Only Claude"):
            self.result()

    def test_192_cycles_keep_exact_counts_without_claiming_continuity(self):
        self.append(sentinel=True)
        for i in range(192):
            stamp = datetime(2026, 10, 19, 5, 0, 2, tzinfo=timezone.utc) + timedelta(seconds=2*i)
            record = self.event(reason=f"unfinished cycle {i}")
            record["timestamp"] = stamp.isoformat()
            self.f.append(record)
            self.f.append(self.f.message("assistant", "continued", at=(stamp+timedelta(seconds=1)).isoformat()))
        result = self.result()
        self.assertEqual(result["counts"]["unmet_checks"], 192)
        self.assertEqual(result["counts"]["model_records_after_check"], 192)
        self.assertFalse(result["continuous_goal_proven"])


if __name__ == "__main__":
    unittest.main()
