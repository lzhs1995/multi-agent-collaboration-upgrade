"""State-machine and restart tests; synthetic journals never claim live results."""
import copy
from contextlib import ExitStack
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchestration_campaign import anchored_ref, validate_campaign
from orchestration_common import ContractError, file_ref, read_json, write_json
from orchestration_coordinator import Coordinator, LeaseRegistry, State, select_ready
from orchestration_test_support import CampaignFixture


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = CampaignFixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_needs_original_exact_control_not_a_file_claim(self):
        c = self.f.campaign
        with self.assertRaisesRegex(ContractError, "control invocation"):
            Coordinator.create(self.f.root / "runs/run-01", self.f.ref, c["runs"][0], fixtures=True)
        self.assertFalse((self.f.root / "runs/run-01").exists())
        co = self.f.start()
        self.assertEqual([n["id"] for n in co.ready()], ["local", "review"])

    def test_local_acceptance_is_independently_checked_and_immutable(self):
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        evidence = self.f.candidate(co, "local")
        result = self.f.accept(co, "local", evidence)
        self.assertEqual(result["review_kind"], "solo_self_review")
        self.assertEqual(co.state()[0]["local"], State.ACCEPTED)
        self.assertIsNotNone(co.leases.read()[info["task_id"]]["released_at"])
        (Path(info["output_root"]) / "unreported.txt").write_text("extra")
        with self.assertRaisesRegex(ContractError, "deliverables|tree changed"):
            co.ready()

    def test_incorrect_output_cannot_pass_a_self_declared_test(self):
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        write_json(Path(info["output_root"]) / "result.json", {"answer": 43})
        with self.assertRaisesRegex(ContractError, "correctness checks failed"):
            self.f.candidate(co, "local")
        self.assertEqual(co.state()[0]["local"], State.LOCAL_RUNNING)

    def test_immutable_claim_fields_cannot_be_overridden_by_transition(self):
        co = self.f.start()
        info = co.claim("review")
        with self.assertRaisesRegex(ContractError, "immutable claim"):
            co.transition("review", State.PREPARED, {"output_root": "/elsewhere"})
        self.assertEqual(co.state()[1]["review"], info)

    def test_uncertain_dispatch_retains_lane_and_scope_across_restart(self):
        co = self.f.start()
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        uncertainty = write_json(co.root / "nodes/review/uncertainty.json", dict(task_id=info["task_id"], reason="lost response"))
        co.transition("review", State.UNCERTAIN, {"uncertainty": uncertainty})
        restarted = Coordinator(co.root, self.f.ref, fixtures=True)
        self.assertEqual(restarted.recover()[0]["action"], "observe_original_attempt")
        self.assertIsNone(restarted.leases.read()[info["task_id"]]["released_at"])
        with self.assertRaises(ContractError):
            restarted.claim("review")
        with self.assertRaises(ContractError):
            restarted.transition("review", State.DISPATCH_INTENT, {"dispatch_intent": {}})

    def test_crash_after_intent_never_claims_again(self):
        co = self.f.start()
        with patch.object(co.leases, "reserve", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                co.claim("local")
        restarted = Coordinator(co.root, self.f.ref, fixtures=True)
        self.assertEqual(restarted.recover()[0]["state"], "INCOMPLETE_CLAIM_INTENT")
        with self.assertRaisesRegex(ContractError, "original claim intent"):
            restarted.claim("local")
        self.assertFalse((co.root / "work/local").exists())

    def test_session_and_parent_scope_leases_are_cross_run(self):
        leases = LeaseRegistry(self.f.campaign["lease_root"])
        claim = write_json(self.f.root / "claim-a.json", {"task_id": "a"})
        other = write_json(self.f.root / "claim-b.json", {"task_id": "b"})
        leases.reserve("a", "one", str(self.f.root / "scope"), claim)
        for session, path in (("one", "different"), ("two", "scope/child")):
            with self.assertRaisesRegex(ContractError, "already reserved"):
                leases.reserve("b", session, str(self.f.root / path), other)
        terminal = write_json(self.f.root / "terminal.json", dict(state="ACCEPTED", data={"verification": {"task_id": "a"}}))
        leases.release("a", session="one", scope=str(self.f.root / "scope"), claim=claim, terminal=terminal)
        leases.reserve("b", "two", str(self.f.root / "scope/child"), other)
        self.assertEqual(len(leases.read()), 2)

    def test_queue_or_event_corruption_is_not_restart(self):
        co = self.f.start()
        co.claim("local")
        path = co.root / "events/000001.json"
        event = read_json(path)
        event["data"]["task_id"] = "invented"
        write_json(path, event)
        with self.assertRaisesRegex(ContractError, "task/attempt"):
            Coordinator(co.root, self.f.ref, fixtures=True).state()
        header = read_json(co.root / "run.json")
        header["queues"]["codex"].reverse()
        write_json(co.root / "run.json", header)
        with self.assertRaisesRegex(ContractError, "FIFO"):
            Coordinator(co.root, self.f.ref, fixtures=True)

    def test_counterbalanced_run_order_cannot_skip_unfinished_trial(self):
        self.f.campaign["runs"].append(dict(run_id="run-02", strategy="serial"))
        self.f.save()
        with self.assertRaisesRegex(ContractError, "earlier frozen trial"):
            self.f.start(1)

    def test_campaign_pin_and_runtime_additions_fail_before_claim(self):
        with self.assertRaisesRegex(ContractError, "hash mismatch"):
            anchored_ref(self.f.ref["path"], "a" * 64)
        co = self.f.start()
        (Path(self.f.campaign["adapter"]["path"]) / "surprise.py").write_text("pass\n")
        with self.assertRaisesRegex(ContractError, "tree changed"):
            co.claim("local")
        self.assertFalse((co.root / "claims/local.json").exists())

    def test_invalid_execution_mode_is_rejected_before_any_reservation(self):
        self.f.plan["nodes"][0]["execution_kind"] = "delegated_cli"
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()
        with self.assertRaisesRegex(ContractError, "execution ownership"):
            self.f.start()
        self.assertFalse((Path(self.f.campaign["lease_root"]) / "leases.json").exists())

    def test_fixture_never_passes_live_validation(self):
        with self.assertRaisesRegex(ContractError, "execution class"):
            validate_campaign(self.f.ref)

    def test_fifo_waits_for_its_head_while_omo_can_use_a_ready_follower(self):
        plan = copy.deepcopy(self.f.plan)
        plan["nodes"][0]["depends_on"] = ["review"]
        plan["nodes"][2]["depends_on"] = []
        states = {n["id"]: State.PENDING for n in plan["nodes"]}
        queues = {"codex": ["local", "closeout"], "claude": ["review"]}
        self.assertEqual([n["id"] for n in select_ready(plan, states, "codeg", self.f.root, queues)], ["review"])
        self.assertEqual([n["id"] for n in select_ready(plan, states, "omo", self.f.root, queues)], ["review", "closeout"])

    def test_clock_rejection_preserves_history_candidate_and_lease(self):
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        self.f.candidate(co, "local")
        history = {p: p.read_bytes() for p in (co.root / "events").glob("*.json")}
        candidate = file_ref(co.root / "nodes/local/candidate.json")
        lease = file_ref(co.leases.root / "leases.json")
        original_state = co.state()
        last = co.events()[-1]
        blocker = write_json(co.root / "nodes/local/blocker.json", dict(task_id=info["task_id"], reason="preserve boundary"))
        cases = [{"time.monotonic_ns": value} for value in (-1, True, 1.5, last["monotonic_ns"] - 1)]
        cases += [{"utc_now": "2000-01-01T00:00:00Z"}, {"boot_id": ""}]
        for values in cases:
            with self.subTest(values=values), ExitStack() as stack:
                for name, value in values.items():
                    stack.enter_context(patch("orchestration_coordinator." + name, return_value=value))
                with self.assertRaises(ContractError):
                    co.transition("local", State.BLOCKED, {"blocker": blocker})
            self.assertEqual({p: p.read_bytes() for p in (co.root / "events").glob("*.json")}, history)
            self.assertEqual(file_ref(candidate["path"]), candidate)
            self.assertEqual(file_ref(lease["path"]), lease)
            self.assertEqual(co.state(), original_state)
        co.transition("local", State.BLOCKED, {"blocker": blocker})
        self.assertEqual(co.state()[0]["local"], State.BLOCKED)
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])


if __name__ == "__main__":
    unittest.main()
