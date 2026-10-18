"""The advice view cannot execute work, replay input or replace acceptance."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, object_sha, read_json, write_json
from orchestration_coordinator import Coordinator
from orchestration_evidence import failure_marker, marker_for, task_identity
from orchestration_observe import _advice, _interval
from orchestration_run import Runner, main
from orchestration_test_support import CampaignFixture
from orchestration_transport import OperationJournal, Transport


class Crash(BaseException):
    pass


class ReadOnlyObservationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.f = CampaignFixture(self.base, version=getattr(self, "campaign_version", 2))
        self.r = Runner(self.f.ref, fixtures=True)
        self.run = "run-01"

    def snapshot(self):
        return {str(p.relative_to(self.base)): file_ref(p) for p in self.base.rglob("*") if p.is_file()}

    def observe(self):
        before = self.snapshot()
        real_open = os.open

        def only_read(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), path)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(Coordinator, "state", side_effect=AssertionError("state replays checkers")), \
             patch.object(Coordinator, "ready", side_effect=AssertionError("ready replays checkers")), \
             patch.object(Transport, "__init__", side_effect=AssertionError("transport must not be constructed")), \
             patch("orchestration_evidence.run_checker", side_effect=AssertionError("no checker")), \
             patch("subprocess.run", side_effect=AssertionError("no subprocess")), \
             patch("os.open", side_effect=only_read):
            result = self.r.next_action(self.run)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(result["acceptance_revalidated"])
        self.assertFalse(result["dispatch_authorized"])
        self.assertEqual(result["input_operations"], 0)
        return result

    def row(self, result, key):
        return next(n for n in result["nodes"] if n["node_id"] == key)

    def candidate(self, co):
        info = co.claim("local")
        self.f.work(info)
        self.r.candidate(self.run, "local")
        return info

    def accept_control(self, info, co):
        candidate = json_ref(file_ref(co.root / "nodes/local/candidate.json"))
        marker = marker_for(info, candidate)
        self.f.control("accept", self.run, node_id="local", marker=marker)
        return marker


class ObservationTests(ReadOnlyObservationFixture):
    def test_drafts_exist_before_dependencies_and_do_not_create_claims(self):
        before = self.snapshot()
        first = self.r.drafts(self.run)
        closeout = next(d["draft"] for d in first["drafts"] if d["draft"]["node_id"] == "closeout")
        self.assertEqual(closeout["dependency_ids"], ["local", "review"])
        self.assertTrue(closeout["draft"])
        self.assertFalse(closeout["dependency_outputs_bound"])
        self.assertNotIn("acceptance_nonce", closeout)
        self.assertNotIn("dependencies", closeout)
        self.assertEqual(closeout["original_session"]["session_id"], self.f.clients["codex"].session["session_id"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.observe()["status"], "NOT_STARTED")
        co = self.f.start()
        info = self.candidate(co)
        self.r.accept(self.run, "local", self.accept_control(info, co))
        self.assertEqual(self.r.drafts(self.run), first)
        self.assertFalse((co.root / "claims/closeout.json").exists())

    def test_advisory_cli_requires_a_run_and_the_frozen_entrypoint(self):
        before = self.snapshot()
        prefix = ["--campaign", self.f.ref["path"], "--campaign-sha256", self.f.ref["sha256"]]
        # Only campaign construction is a fixture here; exercise the real CLI
        # argument dispatch and its entrypoint-location gate.
        with patch("orchestration_run.Runner", return_value=self.r), \
             patch.object(Transport, "__init__", side_effect=AssertionError("no transport")):
            for action, schema in (("drafts", "orchestration-drafts-v1"),
                                   ("next-action", "orchestration-next-action-v1")):
                with self.subTest(action=action):
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as missing:
                        main(prefix + [action])
                    self.assertEqual(missing.exception.code, 2)
                    self.assertIn("--run", err.getvalue())
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        self.assertEqual(main(prefix + [action, "--run", self.run]), 2)
                    self.assertIn("not the mutable checkout", err.getvalue())
                    frozen = str(Path(self.f.campaign["adapter"]["path"]) / "scripts/orchestration_run.py")
                    with patch("orchestration_run.__file__", frozen), contextlib.redirect_stdout(out):
                        self.assertEqual(main(prefix + [action, "--run", self.run]), 0)
                    result = json.loads(out.getvalue())
                    self.assertEqual(result["schema"], schema)
                    self.assertFalse(result["dispatch_authorized"])
        self.assertEqual(self.snapshot(), before)

    def test_empty_running_and_accepted_history_are_observed_without_execution(self):
        co = self.f.start()
        self.assertEqual(self.observe()["recorded_claim_candidates"], ["local", "review"])
        info = self.candidate(co)
        ready = self.observe()
        self.assertEqual(self.row(ready, "local")["recommendation"]["action"], "review-frozen-candidate")
        self.assertIsNone(self.row(ready, "local")["recommendation"]["gated_command"])
        self.r.accept(self.run, "local", self.accept_control(info, co))
        result = self.observe()
        self.assertEqual(result["recorded_states"]["local"], "ACCEPTED")
        self.assertEqual(result["recorded_claim_candidates"], ["review"])
        timing = self.row(result, "local")["timings"]["evidence_ready_recorded_to_accepted_recorded"]
        self.assertIsNotNone(timing["monotonic_seconds"])
        self.assertIn("callback_received", result["native_boundaries_not_derived"])

    def test_crashed_claim_never_recommends_claiming_again(self):
        co = self.f.start()
        with patch.object(Coordinator, "_append", side_effect=Crash()), self.assertRaises(Crash):
            co.claim("local")
        result = self.observe()
        self.assertEqual(result["incomplete_claims"], ["local"])
        self.assertEqual(result["recorded_claim_candidates"], [])
        self.assertEqual(self.row(result, "local")["recommendation"]["action"], "review-incomplete-claim")
        self.assertIsNone(next(iter(co.leases.read().values()))["released_at"])

    def test_partial_claim_files_and_incomplete_start_are_not_fresh_work(self):
        root = self.f.root / "runs" / self.run
        root.mkdir(parents=True)
        result = self.observe()
        self.assertEqual(result["status"], "INCOMPLETE_START")
        self.assertEqual(result["next_action"], "review-original-start")
        root.rmdir()
        co = self.f.start()
        write_json(co.root / "nodes/local/work-request.json", {"partial": True})
        self.assertEqual(self.observe()["recorded_claim_candidates"], [])

    def test_unfinished_operation_keeps_original_intent_and_duration_unknown(self):
        co = self.f.start()
        info = co.claim("review")
        journal = OperationJournal(co.root / "nodes/review", self.f.ref, info["task_id"], self.f.campaign["controller"])
        def crash(_):
            raise Crash()
        with self.assertRaises(Crash):
            journal.once("preflight", {"argv": ["fixture-only"]}, crash)
        row = self.row(self.observe(), "review")
        self.assertEqual(row["recommendation"]["action"], "observe-original-operation")
        self.assertIsNone(row["recommendation"]["gated_command"])
        self.assertEqual(row["operations"][0]["status"], "PENDING")
        self.assertIsNone(row["operations"][0]["recorded_wall_seconds"])

    def test_failed_operation_and_nonzero_return_are_not_prepare_retries(self):
        co = self.f.start()
        info = co.claim("review")
        journal = OperationJournal(co.root / "nodes/review", self.f.ref, info["task_id"], self.f.campaign["controller"])
        def fail(_):
            raise ValueError("original failure")
        with self.assertRaises(ContractError):
            journal.once("preflight", {}, fail)
        journal.once("task-pack", {}, lambda _: {"returncode": 2})
        row = self.row(self.observe(), "review")
        self.assertEqual([x["status"] for x in row["operations"]], ["ERROR", "FAILED_RETURN"])
        self.assertEqual(row["recommendation"]["action"], "observe-original-operation")

    def test_dispatch_intent_before_event_is_never_a_second_submission(self):
        co = self.f.start()
        co.claim("review")
        self.f.prepare(co, "review")
        transport = self.r.transport(co, "review")
        with patch.object(Coordinator, "_append", side_effect=Crash()), \
             patch.object(transport.runtime, "submit") as submit, self.assertRaises(Crash):
            transport.dispatch()
        submit.assert_not_called()
        self.assertTrue((transport.root / "dispatch-intent.json").is_file())
        self.assertFalse((transport.root / "operations/submit-task-pack/intent.json").exists())
        row = self.row(self.observe(), "review")
        self.assertEqual(row["recorded_state"], "PREPARED")
        self.assertEqual(row["recommendation"]["action"], "observe-original-dispatch")
        self.assertIsNone(row["recommendation"]["gated_command"])

    def test_recorded_dispatch_intent_only_suggests_original_reconciliation(self):
        co = self.f.start()
        co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        row = self.row(self.observe(), "review")
        self.assertEqual(row["recommendation"]["gated_command"], "reconcile")
        self.assertIsNone(row["timings"]["dispatch_intent_recorded_to_dispatched_recorded"]["monotonic_seconds"])

    def test_control_publication_without_an_event_rejects_stale_dispatch_advice(self):
        co = self.f.start()
        co.claim("review")
        self.f.prepare(co, "review")
        events, leases = co.events(), co.leases.read()

        def publish_after_read(*args):
            result = _advice(*args)
            if args[1] == "review":
                write_json(co.root / "nodes/review/dispatch-intent.json",
                           task_identity(co.campaign, co.header, co.nodes["review"]), exclusive=True)
            return result

        with patch("orchestration_observe._advice", side_effect=publish_after_read), \
             self.assertRaisesRegex(ContractError, "control evidence changed during observation"):
            self.r.next_action(self.run)
        self.assertEqual(co.events(), events)
        self.assertEqual(co.leases.read(), leases)

    def test_persisted_acceptance_only_suggests_resume_and_keeps_full_gates(self):
        co = self.f.start()
        info = self.candidate(co)
        with self.assertRaisesRegex(ContractError, "original supervisor control"):
            self.r.accept(self.run, "local", resume=True)
        marker = self.accept_control(info, co)
        with patch.object(Coordinator, "accept", side_effect=Crash()), self.assertRaises(Crash):
            self.r.accept(self.run, "local", marker)
        row = self.row(self.observe(), "local")
        self.assertEqual(row["recommendation"]["gated_command"], "resume-accept")
        self.assertNotIn("exec_script", row)
        self.assertIsNone(row["lease_release_recorded_at"])

    def test_recorded_terminal_event_does_not_imply_lease_release(self):
        co = self.f.start()
        info = self.candidate(co)
        with patch.object(Coordinator, "_release", side_effect=Crash()), self.assertRaises(Crash):
            self.r.accept(self.run, "local", self.accept_control(info, co))
        row = self.row(self.observe(), "local")
        self.assertEqual(row["recorded_state"], "ACCEPTED")
        self.assertEqual(row["recommendation"]["gated_command"], "repair-releases")
        self.assertIsNone(row["lease_release_recorded_at"])

    def test_blocked_work_requires_original_settlement_not_a_new_control(self):
        co = self.f.start()
        info = co.claim("local")
        self.r.block(self.run, "local", "business failure")
        result = self.r.failure_candidate(self.run, "local", "local_boundary")
        row = self.row(self.observe(), "local")
        self.assertIsNone(row["recommendation"]["gated_command"])
        marker = failure_marker(info, json_ref(result["candidate"]))
        self.f.control("settle", self.run, node_id="local", marker=marker)
        with patch.object(Coordinator, "_settle_failure_locked", side_effect=Crash()), self.assertRaises(Crash):
            self.r.settle(self.run, "local", marker)
        result = self.observe()
        self.assertEqual(result["recorded_claim_candidates"], [])
        self.assertEqual(self.row(result, "local")["recommendation"]["gated_command"], "resume-settle")

    def test_tampered_pinned_evidence_is_rejected_without_checkers(self):
        co = self.f.start()
        self.candidate(co)
        path = co.root / "nodes/local/candidate.json"
        value = read_json(path)
        value["node_id"] = "review"
        write_json(path, value)
        with patch("orchestration_evidence.run_checker", side_effect=AssertionError("no checker")), \
             self.assertRaisesRegex(ContractError, "hash/size mismatch"):
            self.r.next_action(self.run)

    def test_rehashed_illegal_transition_still_fails(self):
        co = self.f.start()
        co.claim("local")
        event = co.events()[-1]
        forged = dict(event, seq=2, previous=object_sha(event), state="ACCEPTED", data={})
        write_json(co.root / "events/000002.json", forged)
        with self.assertRaisesRegex(ContractError, "illegal recorded lifecycle transition"):
            self.r.next_action(self.run)

    def test_operation_result_cannot_be_rebound_to_another_intent(self):
        co = self.f.start()
        info = co.claim("review")
        journal = OperationJournal(co.root / "nodes/review", self.f.ref, info["task_id"], self.f.campaign["controller"])
        journal.once("preflight", {}, lambda _: {"returncode": 0})
        path = co.root / "nodes/review/operations/preflight/result.json"
        value = read_json(path)
        value["intent"] = self.f.ref
        write_json(path, value)
        with self.assertRaisesRegex(ContractError, "differs from original intent"):
            self.r.next_action(self.run)


class TimingTests(unittest.TestCase):
    def test_missing_cross_boot_and_discontinuous_clocks_remain_unmeasured(self):
        start = dict(seq=1, at="2026-10-15T12:00:00Z", monotonic_ns=10_000_000_000, boot_id="a")
        end = dict(seq=3, at="2026-10-15T12:00:05Z", monotonic_ns=15_000_000_000, boot_id="a")
        self.assertIsNone(_interval(start, None, [start])["monotonic_seconds"])
        same = _interval(start, end, [start, end])
        self.assertEqual((same["recorded_wall_seconds"], same["monotonic_seconds"]), (5, 5))
        changed = dict(end, boot_id="b", monotonic_ns=1)
        self.assertIsNone(_interval(start, changed, [start, changed])["monotonic_seconds"])
        intermediate = dict(start, seq=2, boot_id="b")
        self.assertIsNone(_interval(start, end, [start, intermediate, end])["monotonic_seconds"])
        jumped = dict(end, at="2026-10-15T12:05:00Z")
        self.assertEqual(_interval(start, jumped, [start, jumped])["clock_status"], "wall_clock_discontinuity_unmeasured")


if __name__ == "__main__":
    unittest.main()
