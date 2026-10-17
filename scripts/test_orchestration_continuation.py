"""A callback pointer survives restart without sending or replacing acceptance."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, exclusive_lock, file_ref, json_ref, write_json
from orchestration_run import Runner
from test_orchestration_observe import Crash
from test_orchestration_timeline import TimelineFixture


class ContinuationTests(TimelineFixture):
    def publish(self, key="review"):
        with patch("orchestration_transport.Transport.__init__", side_effect=AssertionError("no sender")), \
             patch("orchestration_evidence.run_checker", side_effect=AssertionError("no business checker")), \
             patch("subprocess.run", side_effect=AssertionError("no subprocess")):
            return self.r.publish_continuation(self.run, key)

    def test_publication_binds_original_receipt_without_changing_lifecycle(self):
        co = self.f.start()
        _, protocol = self.completed_callback(co)
        before = co.events()
        result = self.publish()
        value = json_ref(result["publication"])
        self.assertEqual(value["protocol"], protocol)
        self.assertEqual(value["callback_native"], json_ref(protocol["callback"]["receipt"])["native_proof"])
        self.assertEqual(value["original_sessions"]["claude"], co.campaign["sessions"]["claude"])
        self.assertEqual(value["event_frontier"]["count"], len(before))
        self.assertEqual(co.events(), before)
        self.assertEqual(result["progress"], "AWAITING_CANDIDATE")
        self.assertIsNone(result["consumed_by"])
        self.assertFalse(result["acceptance_revalidated"])
        row = self.row(self.observe(), "review")
        self.assertEqual(row["continuation"], result)
        self.assertEqual(row["recommendation"]["gated_command"], "candidate")

    def test_restart_and_other_lane_progress_preserve_original_bytes_and_frontier(self):
        co = self.f.start()
        self.completed_callback(co)
        original = self.publish()
        path = Path(original["publication"]["path"])
        before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        co.claim("local")
        self.r = Runner(self.f.ref, fixtures=True)
        self.assertEqual(self.publish(), original)
        self.assertEqual((path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns), before)

    def test_missing_receipt_does_not_turn_native_text_into_continuation(self):
        co = self.f.start()
        _, protocol = self.completed_callback(co)
        Path(protocol["callback"]["receipt"]["path"]).unlink()
        with self.assertRaisesRegex(ContractError, "original callback receipt is absent"):
            self.publish()
        self.assertFalse((co.root / "nodes/review/continuation.json").exists())
        self.assertIsNone(self.row(self.observe(), "review")["continuation"])

    def test_changed_report_or_binding_cannot_overwrite_publication(self):
        co = self.f.start()
        info, _ = self.completed_callback(co)
        published = self.publish()["publication"]
        report = Path(info["output_root"]) / "report.md"
        original = report.read_bytes()
        report.write_text("different report\n")
        with self.assertRaises(ContractError):
            self.publish()
        self.assertEqual(file_ref(published["path"]), published)
        report.write_bytes(original)
        self.assertEqual(self.publish()["publication"], published)

    def test_corrupt_frontier_session_task_and_native_fence_are_rejected_read_only(self):
        co = self.f.start()
        self.completed_callback(co)
        publication = self.publish()["publication"]
        original = json_ref(publication)
        changes = [dict(task_id="another-task"), dict(campaign=dict(self.f.ref, sha256="0" * 64)),
                   dict(event_frontier=dict(original["event_frontier"], count=0)),
                   dict(original_sessions={}), dict(native_end={})]
        for changeset in changes:
            with self.subTest(changeset=changeset):
                write_json(publication["path"], dict(copy.deepcopy(original), **changeset))
                before = self.snapshot()
                with self.assertRaises(ContractError):
                    self.observe()
                self.assertEqual(self.snapshot(), before)
        write_json(publication["path"], original)
        self.assertEqual(self.publish()["publication"], publication)

    def test_consumption_comes_from_existing_acceptance_without_extra_state_file(self):
        co = self.f.start()
        _, protocol = self.completed_callback(co)
        publication = self.publish()["publication"]
        evidence = self.f.candidate(co, "review", protocol)
        row = self.row(self.observe(), "review")
        self.assertEqual(row["continuation"]["progress"], "AWAITING_ACCEPTANCE")
        self.assertIsNone(row["continuation"]["consumed_by"])
        self.f.accept(co, "review", evidence)
        row = self.row(self.observe(), "review")
        self.assertEqual(row["continuation"]["progress"], "ACCEPTED_RECORDED")
        self.assertEqual(json_ref(row["continuation"]["consumed_by"])["state"], "ACCEPTED")
        self.assertEqual(file_ref(publication["path"]), publication)
        self.assertEqual(row["scoped_disarm_record"], "ABSENT")

    def test_local_work_and_partial_existing_publications_are_not_replaced(self):
        co = self.f.start()
        co.claim("local")
        with self.assertRaisesRegex(ContractError, "original dispatched work"):
            self.publish("local")
        self.completed_callback(co)
        path = co.root / "nodes/review/continuation.json"
        path.write_text('{"partial":')
        original = path.read_bytes()
        with self.assertRaises(ContractError):
            self.publish()
        self.assertEqual(path.read_bytes(), original)

    def test_existing_node_lock_fails_without_polling_or_input(self):
        co = self.f.start()
        self.completed_callback(co)
        with exclusive_lock(co.root / "nodes/review/runner.lock"), self.assertRaises(ContractError):
            self.publish()
        self.assertFalse((co.root / "nodes/review/continuation.json").exists())


class ThreeLaneContinuationTests(TimelineFixture):
    campaign_version = 3

    def test_both_claude_continuations_keep_their_original_executor(self):
        co = self.f.start()
        refs = {}
        for key in ("review", "parallel"):
            self.completed_callback(co, key)
            refs[key] = self.r.publish_continuation(self.run, key)["publication"]
        observed = self.observe()
        for key in refs:
            value = json_ref(refs[key])
            lane = co.nodes[key]["executor"]
            self.assertEqual(value["executor_id"], lane)
            self.assertEqual(set(value["original_sessions"]), {"codex", lane})
            self.assertEqual(self.row(observed, key)["continuation"]["publication"], refs[key])
        wrong = json_ref(refs["review"])
        write_json(refs["parallel"]["path"], wrong)
        with self.assertRaises(ContractError):
            self.observe()

    def test_v3_candidate_crash_keeps_callback_for_original_recovery(self):
        co = self.f.start()
        _, protocol = self.completed_callback(co)
        with patch.object(Runner, "_protocol", return_value=protocol), \
             patch("orchestration_run.make_test_result", side_effect=Crash()), self.assertRaises(Crash):
            self.r.candidate(self.run, "review")
        path = co.root / "nodes/review/continuation.json"
        ref = file_ref(path)
        self.assertFalse((co.root / "nodes/review/candidate.json").exists())
        self.assertEqual(self.row(self.observe(), "review")["continuation"]["progress"], "AWAITING_CANDIDATE")
        self.r = Runner(self.f.ref, fixtures=True)
        with patch.object(Runner, "_protocol", return_value=protocol):
            self.r.candidate(self.run, "review")
        self.assertEqual(file_ref(path), ref)
        self.assertEqual(self.row(self.observe(), "review")["continuation"]["progress"], "AWAITING_ACCEPTANCE")


if __name__ == "__main__":
    unittest.main()
