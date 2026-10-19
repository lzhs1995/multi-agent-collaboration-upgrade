"""Offline counterexamples for bounded orchestration; no live clients or input."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from orchestration_campaign import control_script
from orchestration_common import ContractError, file_ref, json_ref, write_json
from orchestration_coordinator import Coordinator, State
from orchestration_evidence import marker_for
from orchestration_run import Runner, main
from orchestration_test_support import CampaignFixture
from orchestration_transport import OperationJournal, Transport
from test_orchestration_transport import FakeRuntime


class AdvanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, version=3)
        self.r = Runner(self.f.ref, fixtures=True)
        self.run = "run-01"

    def narrow(self, *keys):
        self.f.plan["nodes"] = [n for n in self.f.plan["nodes"] if n["id"] in keys]
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()
        self.r = Runner(self.f.ref, fixtures=True)

    def start(self):
        self.co = self.f.start()
        return self.co

    @contextlib.contextmanager
    def fixture_transport(self):
        def prepare(t):
            return self.f.prepare(t.co, t.key)
        def dispatch(t):
            self.f.intent(t.co, t.key)
            return self.f.dispatch(t.co, t.key)
        with patch.object(Transport, "prepare", prepare), patch.object(Transport, "dispatch", dispatch):
            yield

    def native_bytes(self):
        return {k: c.path.read_bytes() for k, c in self.f.clients.items()}

    def local_candidate(self):
        co = self.start()
        info = co.claim("local")
        self.f.work(info)
        candidate = self.r.candidate(self.run, "local")
        return info, candidate

    def test_start_is_exact_native_handoff_and_never_executed(self):
        before = self.native_bytes()
        with patch.object(Runner, "start", side_effect=AssertionError("no wrapper start")):
            result = self.r.advance(self.run)
        script = result["supervisor_actions"][0]["exec_script"]
        self.assertEqual(script, control_script(self.f.campaign, self.f.ref, "start", self.run))
        self.assertTrue(script.endswith("\n"))
        self.assertFalse((self.f.root / "runs" / self.run).exists())
        self.assertEqual(before, self.native_bytes())
        self.assertGreaterEqual(result["initialization_seconds"], 0)
        self.assertEqual(result["action_elapsed_seconds"], 0)
        self.assertEqual(result["initialization_seconds"], result["elapsed_seconds"])

    def test_original_start_without_directory_never_issues_second_start(self):
        self.f.control("start", self.run)
        result = self.r.advance(self.run)
        self.assertEqual(result["supervisor_actions"][0]["action"], "review-original-start")
        self.assertNotIn("exec_script", result["supervisor_actions"][0])

    def test_partial_start_is_preserved(self):
        root = self.f.root / "runs" / self.run
        root.mkdir(parents=True)
        (root / "original.txt").write_text("partial")
        result = self.r.advance(self.run)
        self.assertEqual(result["stop_reason"], "INCOMPLETE_START")
        self.assertFalse(result["actions"])
        self.assertEqual((root / "original.txt").read_text(), "partial")
        self.assertEqual(result["action_elapsed_seconds"], 0)
        self.assertEqual(result["initialization_seconds"], result["elapsed_seconds"])

    def test_default_one_action_claims_but_never_executes_local_work(self):
        co = self.start()
        before = self.native_bytes()
        original_run = subprocess.run
        def clock_only(argv, *args, **kwargs):
            self.assertEqual(argv[0], "/usr/sbin/sysctl", "no local business subprocess")
            return original_run(argv, *args, **kwargs)
        with patch("subprocess.run", side_effect=clock_only):
            result = self.r.advance(self.run)
        self.assertEqual([(r["node_id"], r["action"]) for r in result["actions"]], [("local", "claim")])
        self.assertEqual(result["supervisor_actions"][0]["action"], "execute-local-business-once")
        self.assertFalse((Path(co.state()[1]["local"]["output_root"]) / "result.json").exists())
        self.assertEqual(before, self.native_bytes())
        again = self.r.advance(self.run)
        local = next(r for r in again["supervisor_actions"] if r["node_id"] == "local")
        self.assertEqual(local["action"], "collect-original-local-work")
        self.assertNotIn("business_command", local)

    def test_bounded_progress_fairly_dispatches_independent_lanes(self):
        self.start()
        with self.fixture_transport():
            first = self.r.advance(self.run, max_steps=12)
            result = self.r.advance(self.run, max_steps=4)
        self.assertEqual([(r["node_id"], r["action"]) for r in first["actions"]], [
            ("local", "claim"), ("review", "claim"), ("parallel", "claim")])
        self.assertEqual(first["stop_reason"], "local_business_ready_before_prepare")
        self.assertEqual([(r["node_id"], r["action"]) for r in result["actions"]], [
            ("review", "prepare"), ("parallel", "prepare"), ("review", "dispatch"), ("parallel", "dispatch")])
        self.assertEqual(result["states"]["review"], "DISPATCHED")
        self.assertEqual(result["states"]["parallel"], "DISPATCHED")
        self.assertEqual(result["states"]["followup"], "PENDING")

    def test_ready_local_handoff_never_enters_long_prepare(self):
        self.start()
        with patch.object(Transport, "prepare", side_effect=AssertionError("would wait for the original 600-second preparation")) as prepare:
            result = self.r.advance(self.run, max_steps=12)
        prepare.assert_not_called()
        self.assertEqual(result["stop_reason"], "local_business_ready_before_prepare")
        self.assertEqual(result["supervisor_actions"][0]["action"], "execute-local-business-once")
        self.assertEqual(result["states"]["review"], "PREPARING")
        self.assertEqual(result["states"]["parallel"], "PREPARING")

    def test_ready_local_handoff_precedes_an_existing_prepared_lane_dispatch(self):
        self.narrow("local", "review")
        co = self.start()
        co.claim("review")
        self.f.prepare(co, "review")
        with patch.object(Transport, "dispatch", side_effect=AssertionError("would wait for native submit")) as dispatch:
            result = self.r.advance(self.run, max_steps=12)
        dispatch.assert_not_called()
        self.assertEqual(result["stop_reason"], "local_business_ready_before_dispatch")
        self.assertEqual(result["supervisor_actions"][0]["action"], "execute-local-business-once")
        self.assertEqual(result["states"]["review"], "PREPARED")
        self.assertEqual(result["actions"][0]["action"], "claim")

    def test_pending_original_preparation_does_not_block_other_lane_or_replay(self):
        co = self.start()
        co.claim("local")
        info = co.claim("review")
        journal = OperationJournal(co.root / "nodes/review", self.f.ref, info["task_id"], self.f.campaign["controller"])
        with self.assertRaises(ContractError):
            journal.once("preflight", {}, Mock(side_effect=RuntimeError("original failure")))
        intent = (journal.root / "preflight/intent.json").read_bytes()
        with self.fixture_transport():
            result = self.r.advance(self.run, max_steps=12)
        self.assertFalse(any(r["node_id"] == "review" for r in result["actions"]))
        self.assertEqual(result["states"]["parallel"], "DISPATCHED")
        self.assertEqual(intent, (journal.root / "preflight/intent.json").read_bytes())
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_repeated_single_steps_do_not_starve_ready_lane_behind_review(self):
        info, candidate = self.local_candidate()
        with self.fixture_transport():
            results = [self.r.advance(self.run) for _ in range(4)]
        self.assertEqual([(r["actions"][0]["node_id"], r["actions"][0]["action"]) for r in results],
                         [("review", "claim"), ("parallel", "claim"), ("review", "prepare"), ("parallel", "prepare")])
        self.assertEqual(results[-1]["states"]["local"], "EVIDENCE_READY")
        self.assertIsNone(self.co.leases.read()[info["task_id"]]["released_at"])

    def test_fair_progress_survives_new_runner_for_every_single_step(self):
        from orchestration_observe import _next_action_from_co
        self.narrow("review", "parallel")
        self.start()
        clock = [0]
        def slow_observation(*args):
            value = _next_action_from_co(*args)
            clock[0] += 90
            return value
        with self.fixture_transport(), \
             patch("orchestration_advance.monotonic", side_effect=lambda: clock[0]), \
             patch("orchestration_observe._next_action_from_co", side_effect=slow_observation):
            results = [Runner(self.f.ref, fixtures=True).advance(self.run, max_steps=1) for _ in range(6)]
        self.assertEqual([(r["actions"][0]["node_id"], r["actions"][0]["action"]) for r in results], [
            ("review", "claim"), ("parallel", "claim"),
            ("review", "prepare"), ("parallel", "prepare"),
            ("review", "dispatch"), ("parallel", "dispatch")])
        self.assertEqual(results[-1]["states"], {"review": "DISPATCHED", "parallel": "DISPATCHED"})
        self.assertEqual([r["initialization_seconds"] for r in results], [90] * 6)
        self.assertTrue(all(len(r["actions"]) == 1 and r["stop_reason"] == "step_budget" for r in results))

    def test_ineligible_pending_node_does_not_hold_back_prepared_lane(self):
        self.narrow("review", "followup")
        co = self.start()
        co.claim("review")
        self.f.prepare(co, "review")
        with self.fixture_transport():
            result = Runner(self.f.ref, fixtures=True).advance(self.run, max_steps=1)
        self.assertEqual([(r["node_id"], r["action"]) for r in result["actions"]], [("review", "dispatch")])
        self.assertEqual(result["states"]["followup"], "PENDING")

    def test_unknown_native_submit_never_resends_across_advances(self):
        self.narrow("local", "review")
        co = self.start()
        co.claim("local")
        runtime = FakeRuntime(self.f, co)
        runtime.fail_submit = True
        self.r.runtime = runtime
        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root), \
             patch("orchestration_transport.dispatch_journal", return_value=runtime.dispatch_root):
            first = self.r.advance(self.run, max_steps=12)
            second = self.r.advance(self.run, max_steps=12)
        self.assertEqual(first["states"]["review"], "UNCERTAIN")
        self.assertEqual(second["states"]["review"], "UNCERTAIN")
        self.assertEqual(runtime.calls.count(("submit", False)), 1)
        self.assertNotIn(("submit", True), runtime.calls)
        self.assertLessEqual(sum(r["action"] == "reconcile" for r in second["actions"]), 1)
        self.assertEqual(first["states"]["local"], "LOCAL_RUNNING")

    def test_existing_dispatch_intent_without_event_is_not_dispatched_again(self):
        co = self.start()
        co.claim("review")
        self.f.prepare(co, "review")
        ref = self.f.intent(co, "review")
        events = co.events()
        (co.root / "events" / f"{events[-1]['seq']:06d}.json").unlink()
        before = Path(ref["path"]).read_bytes()
        with patch.object(Transport, "dispatch", side_effect=AssertionError("no second dispatch")):
            result = self.r.advance(self.run, max_steps=2)
        self.assertEqual(Path(ref["path"]).read_bytes(), before)
        self.assertFalse(any(r["node_id"] == "review" for r in result["actions"]))

    def test_stale_advice_cannot_claim_again_and_preserves_partial_results(self):
        self.start()
        changed = False
        # Inject after the complete fenced observation, at the actual action
        # boundary: it must still enter a freshly constructed coordinator.
        import orchestration_advance
        execute = orchestration_advance._execute
        def stale_before_execute(runner, run_id, key, expected, action):
            nonlocal changed
            if not changed:
                changed = True
                self.r.co(run_id).claim("local")
            return execute(runner, run_id, key, expected, action)
        with patch.object(orchestration_advance, "_execute", side_effect=stale_before_execute):
            result = self.r.advance(self.run, max_steps=2)
        self.assertEqual(result["actions"][0]["status"], "PENDING_OR_BLOCKED")
        self.assertIn("state changed", result["actions"][0]["error"])
        self.assertEqual(result["states"]["review"], "PREPARING")
        self.assertEqual(sum(e["node_id"] == "local" for e in self.co.events()), 1)

    def test_competing_claim_after_fresh_state_never_reissues_local_business(self):
        co = self.start()
        import orchestration_advance
        execute = orchestration_advance._execute
        competed = False
        def race(runner, run_id, key, expected, action):
            nonlocal competed
            if key != "local" or action != "claim" or competed:
                return execute(runner, run_id, key, expected, action)
            competed = True
            proxy = Mock(wraps=co)
            proxy.root = co.root
            first = True
            def state_then_compete():
                nonlocal first
                snapshot = co.state()
                if first:
                    first = False
                    co.claim("local")
                return snapshot
            proxy.state.side_effect = state_then_compete
            with patch.object(runner, "co", return_value=proxy):
                return execute(runner, run_id, key, expected, action)
        with patch.object(orchestration_advance, "_execute", side_effect=race):
            result = self.r.advance(self.run, max_steps=2)
        self.assertTrue(competed)
        self.assertEqual(result["actions"][0]["status"], "PENDING_OR_BLOCKED")
        self.assertIn("node is not ready", result["actions"][0]["error"])
        self.assertFalse(any(r["action"] == "execute-local-business-once" for r in result["supervisor_actions"]))
        self.assertEqual(result["states"]["review"], "PREPARING")
        self.assertEqual(sum(e["node_id"] == "local" for e in co.events()), 1)

    def test_changed_shared_evidence_stops_and_keeps_completed_action_diagnostic(self):
        self.start()
        import orchestration_advance
        execute = orchestration_advance._execute
        def changed(*args):
            value = execute(*args)
            Path(self.f.campaign["inputs"][0]["path"]).write_text("changed after claim")
            return value
        with patch.object(orchestration_advance, "_execute", side_effect=changed):
            result = self.r.advance(self.run, max_steps=12)
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["status"], "RETURNED")
        self.assertEqual(result["status"], "PENDING_OR_BLOCKED")
        self.assertEqual(result["stop_reason"], "final_state_refused")
        self.assertIn("final_state_error", result)
        self.assertFalse(result["supervisor_actions"])

    def test_candidate_handoff_does_not_accept_or_release(self):
        self.narrow("local")
        info, candidate = self.local_candidate()
        before = self.native_bytes()
        with patch.object(Runner, "accept", side_effect=AssertionError("native review required")):
            result = self.r.advance(self.run, max_steps=12)
        handoff = result["supervisor_actions"][0]
        self.assertEqual(handoff["action"], "review-and-accept")
        self.assertEqual(handoff["exec_script"], candidate["exec_script"])
        self.assertEqual(handoff["review_kind"], "solo_self_review")
        self.assertTrue(handoff["output_files"])
        self.assertEqual(result["states"]["local"], "EVIDENCE_READY")
        self.assertIsNone(self.co.leases.read()[info["task_id"]]["released_at"])
        self.assertEqual(before, self.native_bytes())

    def test_local_handoff_is_removed_if_another_turn_already_created_candidate(self):
        co = self.start()
        import orchestration_advance
        execute = orchestration_advance._execute
        def raced(runner, run_id, key, expected, action):
            value = execute(runner, run_id, key, expected, action)
            if key == "review" and action == "claim":
                self.f.work(co.state()[1]["local"])
                self.r.candidate(self.run, "local")
            return value
        with patch.object(orchestration_advance, "_execute", side_effect=raced):
            result = self.r.advance(self.run, max_steps=2)
        self.assertEqual(result["states"]["local"], "EVIDENCE_READY")
        self.assertFalse(any(r["action"] == "execute-local-business-once" for r in result["supervisor_actions"]))

    def test_review_handoff_is_removed_if_another_turn_accepted_candidate(self):
        self.narrow("local")
        info, candidate = self.local_candidate()
        marker = marker_for(info, json_ref(candidate["candidate"]))
        import orchestration_advance
        review = orchestration_advance._review
        def accepted(*args):
            value = review(*args)
            self.f.control("accept", self.run, node_id="local", marker=marker)
            self.r.accept(self.run, "local", marker)
            return value
        with patch.object(orchestration_advance, "_review", side_effect=accepted):
            result = self.r.advance(self.run)
        self.assertEqual(result["states"]["local"], "ACCEPTED")
        self.assertEqual([r["action"] for r in result["supervisor_actions"]], ["finish-and-verify"])

    def test_review_handoff_refresh_recovers_acceptance_started_after_selection(self):
        self.narrow("local")
        info, candidate = self.local_candidate()
        marker = marker_for(info, json_ref(candidate["candidate"]))
        import orchestration_advance
        review = orchestration_advance._review
        first = True
        def started(*args):
            nonlocal first
            value = review(*args)
            if first:
                first = False
                self.f.control("accept", self.run, node_id="local", marker=marker)
            return value
        with patch.object(orchestration_advance, "_review", side_effect=started):
            result = self.r.advance(self.run)
        self.assertEqual(result["states"]["local"], "EVIDENCE_READY")
        self.assertEqual(result["supervisor_actions"][0]["action"], "resume-original-acceptance")
        self.assertEqual(result["supervisor_actions"][0]["exec_script"], control_script(
            self.f.campaign, self.f.ref, "resume-accept", self.run, node_id="local"))
        self.assertFalse((self.co.root / "nodes/local/accepted-evidence.json").exists())

    def test_duplicate_acceptance_during_handoff_refresh_cannot_return_accept_command(self):
        self.narrow("local")
        info, candidate = self.local_candidate()
        marker = marker_for(info, json_ref(candidate["candidate"]))
        import orchestration_advance
        review = orchestration_advance._review
        first = True
        def duplicated(*args):
            nonlocal first
            value = review(*args)
            if first:
                first = False
                for _ in range(2):
                    self.f.control("accept", self.run, node_id="local", marker=marker)
            return value
        with patch.object(orchestration_advance, "_review", side_effect=duplicated):
            result = self.r.advance(self.run)
        self.assertEqual(result["status"], "PENDING_OR_BLOCKED")
        self.assertEqual(result["stop_reason"], "handoff_refresh_refused")
        self.assertNotIn("exec_script", result["supervisor_actions"][0])

    def test_existing_acceptance_invocation_without_file_returns_resume_only(self):
        self.narrow("local")
        info, candidate = self.local_candidate()
        self.f.control("accept", self.run, node_id="local", marker=marker_for(info, json_ref(candidate["candidate"])))
        result = self.r.advance(self.run)
        handoff = result["supervisor_actions"][0]
        self.assertEqual(handoff["action"], "resume-original-acceptance")
        self.assertEqual(handoff["exec_script"], control_script(self.f.campaign, self.f.ref, "resume-accept", self.run, node_id="local"))
        self.assertFalse((self.co.root / "nodes/local/accepted-evidence.json").exists())

    def test_duplicate_native_acceptance_is_not_silently_recovered(self):
        self.narrow("local")
        info, candidate = self.local_candidate()
        marker = marker_for(info, json_ref(candidate["candidate"]))
        for _ in range(2):
            self.f.control("accept", self.run, node_id="local", marker=marker)
        result = self.r.advance(self.run)
        self.assertEqual(result["status"], "PENDING_OR_BLOCKED")
        self.assertIn("duplicate/absent", result["actions"][0]["error"])
        self.assertNotIn("exec_script", result["supervisor_actions"][0])
        self.assertIsNone(self.co.leases.read()[info["task_id"]]["released_at"])

    def test_callback_to_candidate_requires_original_completed_native_work(self):
        self.narrow("review")
        co = self.start()
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        self.f.work(info, "claude_a")
        self.f.callback(co, "review")
        result = self.r.advance(self.run, max_steps=4)
        self.assertEqual([r["action"] for r in result["actions"]], ["callback", "candidate"])
        self.assertEqual(result["states"]["review"], "EVIDENCE_READY")
        self.assertEqual(result["supervisor_actions"][0]["action"], "review-and-accept")
        self.assertTrue((co.root / "nodes/review/continuation.json").exists())
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_report_and_callback_without_native_work_cannot_be_accepted(self):
        self.narrow("review")
        co = self.start()
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        output = Path(info["output_root"])
        write_json(output / "result.json", {"answer": 42})
        (output / "report.md").write_text("This report has no actual original native work.\n")
        self.f.callback(co, "review")
        result = self.r.advance(self.run, max_steps=4)
        self.assertEqual(result["status"], "PENDING_OR_BLOCKED")
        self.assertEqual(result["states"]["review"], "DISPATCHED")
        self.assertIn("no completed task-bound", result["actions"][-1]["error"])
        self.assertFalse((co.root / "nodes/review/accepted-evidence.json").exists())
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_single_step_callback_persists_continuation_for_next_invocation(self):
        self.narrow("review")
        co = self.start()
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        self.f.work(info, "claude_a")
        self.f.callback(co, "review")
        first = self.r.advance(self.run)
        self.assertEqual([r["action"] for r in first["actions"]], ["callback"])
        self.assertTrue((co.root / "nodes/review/continuation.json").is_file())
        restarted = Runner(self.f.ref, fixtures=True)
        second = restarted.advance(self.run)
        self.assertEqual([r["action"] for r in second["actions"]], ["candidate"])
        self.assertEqual(second["supervisor_actions"][0]["action"], "review-and-accept")

    def test_terminal_release_repair_hands_finish_to_supervisor(self):
        self.narrow("local")
        info, candidate = self.local_candidate()
        marker = marker_for(info, json_ref(candidate["candidate"]))
        self.f.control("accept", self.run, node_id="local", marker=marker)
        with patch.object(Coordinator, "_release", side_effect=OSError("crash after accepted event")):
            with self.assertRaises(OSError):
                self.r.accept(self.run, "local", marker)
        result = self.r.advance(self.run, max_steps=2)
        self.assertEqual(result["actions"][0]["action"], "repair-releases")
        self.assertIsNotNone(self.co.leases.read()[info["task_id"]]["released_at"])
        self.assertEqual(result["supervisor_actions"][0]["action"], "finish-and-verify")
        self.assertFalse((self.co.root / "finish.json").exists())

    def test_terminal_delegated_disarm_reuses_original_terminal(self):
        self.narrow("review")
        co = self.start()
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        self.f.work(info, "claude_a")
        protocol = self.f.callback(co, "review")
        evidence = self.f.candidate(co, "review", protocol)
        self.f.accept(co, "review", evidence)
        from orchestration_test_support import OfflineDisarmRuntime
        self.r.runtime = OfflineDisarmRuntime(self.f.campaign, co.root / "offline-disarm", executor_id="claude_a")
        result = self.r.advance(self.run, max_steps=4)
        self.assertEqual([r["action"] for r in result["actions"]], ["disarm"])
        self.assertTrue((co.root / "nodes/review/disarm.json").exists())
        self.assertEqual(result["supervisor_actions"][0]["action"], "finish-and-verify")

    def test_time_budget_stops_between_actions_without_replaying(self):
        from orchestration_advance import _execute
        self.start()
        clock = [0]
        def slow_action(*args):
            value = _execute(*args)
            clock[0] += 2
            return value
        with patch("orchestration_advance.monotonic", side_effect=lambda: clock[0]), \
             patch("orchestration_advance._execute", side_effect=slow_action):
            result = self.r.advance(self.run, max_steps=12, max_seconds=1)
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["stop_reason"], "time_budget_between_actions")
        self.assertEqual(result["initialization_seconds"], 0)
        self.assertEqual(result["action_elapsed_seconds"], 2)
        self.assertEqual(result["elapsed_seconds"], 2)

    def test_slow_initialization_and_first_observation_do_not_starve_first_action(self):
        from orchestration_observe import _next_action_from_co
        self.start()
        clock = [0]
        status = self.r._status_from_co
        def slow_status(*args):
            value = status(*args)
            clock[0] += 60
            return value
        def slow_observation(*args):
            value = _next_action_from_co(*args)
            clock[0] += 70
            return value
        with patch("orchestration_advance.monotonic", side_effect=lambda: clock[0]), \
             patch.object(self.r, "_status_from_co", side_effect=slow_status), \
             patch("orchestration_observe._next_action_from_co", side_effect=slow_observation):
            result = self.r.advance(self.run, max_steps=1, max_seconds=45)
        self.assertEqual([(r["node_id"], r["action"]) for r in result["actions"]], [("local", "claim")])
        self.assertEqual(result["states"]["local"], "LOCAL_RUNNING")
        self.assertEqual(result["initialization_seconds"], 130)
        self.assertEqual(result["action_elapsed_seconds"], 0)
        self.assertEqual(result["elapsed_seconds"], 130)

    def test_slow_later_observation_cannot_start_action_after_phase_deadline(self):
        self.start()
        clock = [0]
        next_action = self.r.next_action
        def slow_observation(*args):
            value = next_action(*args)
            clock[0] += 2
            return value
        with patch("orchestration_advance.monotonic", side_effect=lambda: clock[0]), \
             patch.object(self.r, "next_action", side_effect=slow_observation):
            result = self.r.advance(self.run, max_steps=12, max_seconds=1)
        self.assertEqual([(r["node_id"], r["action"]) for r in result["actions"]], [("local", "claim")])
        self.assertEqual(result["stop_reason"], "time_budget_between_actions")
        self.assertEqual(result["states"]["review"], "PENDING")
        self.assertEqual(result["action_elapsed_seconds"], 2)

    def test_slow_first_observation_refusal_reports_time_without_action(self):
        self.start()
        clock = [0]
        def refused(*args):
            clock[0] += 90
            raise ContractError("original observation changed")
        with patch("orchestration_advance.monotonic", side_effect=lambda: clock[0]), \
             patch("orchestration_observe._next_action_from_co", side_effect=refused):
            result = self.r.advance(self.run)
        self.assertEqual(result["stop_reason"], "observation_refused")
        self.assertEqual(result["actions"], [])
        self.assertEqual(self.co.events(), [])
        self.assertEqual(result["initialization_seconds"], 90)
        self.assertEqual(result["action_elapsed_seconds"], 0)
        self.assertEqual(result["elapsed_seconds"], 90)

    def test_slow_initial_observation_never_resets_absolute_deadline_or_stop(self):
        from orchestration_lock import new_work_guard
        from orchestration_observe import _next_action_from_co
        from orchestration_permit import elapsed
        from test_orchestration_coordinator_contention import registered_policy
        for boundary in ("deadline", "STOP", "schedule/halt.json"):
            with self.subTest(boundary=boundary):
                root = Path(self.temp.name) / boundary.replace("/", "-")
                root.mkdir()
                self.f = CampaignFixture(root, version=3)
                self.narrow("local")
                self.start()
                original_header = (self.co.root / "run.json").read_bytes()
                before = self.native_bytes()
                policy = registered_policy(root / "policy", self.co.header)
                limit = json_ref(policy.campaign["registration"])["contract"]["run_timeout_seconds"]
                clock, observed = [0], []
                def measured(header):
                    value = elapsed(header)
                    if observed and boundary == "deadline":
                        value["seconds"] = limit + 1
                    return value
                def slow_observation(*args):
                    value = _next_action_from_co(*args)
                    clock[0] += 90
                    observed.append(True)
                    if boundary != "deadline":
                        stop = policy.root / boundary
                        stop.parent.mkdir(parents=True, exist_ok=True)
                        stop.write_text("external stop during initial observation")
                    return value
                with patch("orchestration_permit.elapsed", side_effect=measured):
                    guard = new_work_guard(policy)
                    with patch("orchestration_advance.monotonic", side_effect=lambda: clock[0]), \
                         patch("orchestration_observe._next_action_from_co", side_effect=slow_observation), \
                         patch("orchestration_lock.new_work_guard", return_value=guard), \
                         patch.object(Coordinator, "_work_budget", side_effect=guard):
                        result = self.r.advance(self.run, max_steps=1, max_seconds=45)
                self.assertEqual(result["actions"][0]["status"], "PENDING_OR_BLOCKED")
                self.assertIn("time cap reached" if boundary == "deadline" else "stopped or closed",
                              result["actions"][0]["error"])
                self.assertEqual(result["states"], {"local": "PENDING"})
                self.assertEqual(result["initialization_seconds"], 90)
                self.assertEqual(self.co.events(), [])
                self.assertEqual(self.co.leases.read(), {})
                self.assertFalse((self.co.root / "claims/local.json").exists())
                self.assertEqual((self.co.root / "run.json").read_bytes(), original_header)
                self.assertEqual(before, self.native_bytes())

    def test_limits_and_arm_refuse_without_mutation(self):
        for kwargs in ({"max_steps": 0}, {"max_steps": 33}, {"max_steps": True},
                       {"max_seconds": 0}, {"max_seconds": float("nan")}, {"max_seconds": 601}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContractError):
                self.r.advance(self.run, **kwargs)
        self.r.campaign = dict(self.r.campaign, arm="A1")
        with self.assertRaisesRegex(ContractError, "B arm"):
            self.r.advance(self.run)
        self.assertFalse((self.f.root / "runs" / self.run).exists())

    def test_cli_binds_limits_and_rejects_fixture_switch(self):
        self.start()
        args = ["--campaign", self.f.ref["path"], "--campaign-sha256", self.f.ref["sha256"],
                "advance", "--run", self.run, "--max-steps", "2", "--max-seconds", "30"]
        out, err = io.StringIO(), io.StringIO()
        with patch("orchestration_run.Runner", return_value=self.r), \
             patch("orchestration_run.__file__", str(Path(self.r.campaign["adapter"]["path"]) / "scripts/orchestration_run.py")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(args)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(len(json.loads(out.getvalue())["actions"]), 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(args + ["--fixtures"])

    def test_bootstrap_requires_advance_only_for_b_and_preserves_native_review(self):
        import upgrade_supervisor_instructions as instructions
        campaign = dict(self.f.campaign, evidence_class="cli_campaign_v3", session_group="fixture-group",
                        workload_class="fixture")
        for arm in ("A1", "B"):
            campaign["arm"] = arm
            with patch.object(instructions, "validate_campaign", return_value=campaign), \
                 patch.object(instructions, "supervisor_messages", return_value=""):
                body = instructions.render(self.f.ref)
            if arm == "B":
                self.assertIn("advance --run RUN --max-steps 12 --max-seconds 45", body)
                self.assertIn("primary orchestration entrypoint", body)
                self.assertIn("not an instruction to duplicate actions", body)
            else:
                self.assertIn("Do not call advance.", body)
                self.assertNotIn("primary orchestration entrypoint", body)
            self.assertIn("including its final LF", body)
            self.assertIn("READ the complete report", body)
            self.assertLess(len(body), 24000)


if __name__ == "__main__":
    unittest.main()
