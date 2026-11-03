"""Community failure modes mapped to existing APIs, with offline fixtures only.

The coordinator has a persistent event chain and immutable task owners, not a
ring-buffer cursor or a generation-based worker. Advance is a foreground action,
not a message/job select loop. These tests exercise the corresponding real
boundaries without inventing those runtime interfaces or starting model clients.
"""
import tempfile
import unittest
from pathlib import Path

from orchestration_common import ContractError, file_ref, json_ref, write_json
from orchestration_coordinator import LeaseRegistry, State
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture
from test_orchestration_transport import FakeRuntime


class CommunityInvariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, version=3)
        self.run = "run-01"

    def narrow(self, *keys):
        self.f.plan["nodes"] = [node for node in self.f.plan["nodes"] if node["id"] in keys]
        for node in self.f.plan["nodes"]:
            node["depends_on"] = [key for key in node["depends_on"] if key in keys]
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()

    def runner(self, runtime=None):
        runner = Runner(self.f.ref, fixtures=True)
        runner.runtime = runtime
        return runner

    def native_bytes(self):
        return {key: client.path.read_bytes() for key, client in self.f.clients.items()}

    @staticmethod
    def event_bytes(co):
        return {path.name: path.read_bytes() for path in (co.root / "events").glob("*.json")}

    def test_missing_persisted_event_is_not_an_empty_history(self):
        co = self.f.start()
        self.assertEqual(co.events(), [])
        empty = self.runner().next_action(self.run)
        self.assertEqual(empty["recorded_states"], {key: "PENDING" for key in co.nodes})
        co.claim("local")
        co.claim("review")
        self.assertEqual(len(co.events()), 2)
        (co.root / "events" / "000001.json").unlink()
        events, native = self.event_bytes(co), self.native_bytes()
        leases = (co.leases.root / "leases.json").read_bytes()
        with self.assertRaisesRegex(ContractError, "event history omitted or malformed"):
            co.events()
        with self.assertRaisesRegex(ContractError, "event history omitted or malformed"):
            self.runner().next_action(self.run)
        self.assertEqual(self.event_bytes(co), events)
        self.assertEqual(self.native_bytes(), native)
        self.assertEqual((co.leases.root / "leases.json").read_bytes(), leases)

    def test_late_old_release_cannot_release_successor_on_same_session_and_scope(self):
        registry = LeaseRegistry(self.f.campaign["lease_root"])
        scope = str(self.f.root / "shared-scope")
        old_claim = write_json(self.f.root / "old-claim.json", {"task_id": "old"})
        new_claim = write_json(self.f.root / "new-claim.json", {"task_id": "new"})
        terminal = write_json(self.f.root / "old-terminal.json", dict(
            state="ACCEPTED", data={"verification": {"task_id": "old"}}))
        registry.reserve("old", "same-session", scope, old_claim)
        original = registry.read()["old"]
        old_arguments = dict(session="same-session", scope=scope, claim=old_claim,
                             terminal=terminal, reserved_at=original["reserved_at"])
        registry.release("old", **old_arguments)
        registry.reserve("new", "same-session", scope, new_claim)
        path = registry.root / "leases.json"
        before = (path.stat().st_mtime_ns, path.read_bytes())
        # The original delayed release is idempotent even after resource reuse.
        registry.release("old", **old_arguments)
        # Relabeling it with the current reservation still cannot release new.
        with self.assertRaisesRegex(ContractError, "verified terminal event"):
            registry.release("new", session="same-session", scope=scope, claim=new_claim,
                             terminal=terminal, reserved_at=registry.read()["new"]["reserved_at"])
        with self.assertRaisesRegex(ContractError, "existing lease is observe-only"):
            registry.reserve("old", "same-session", scope, old_claim)
        self.assertEqual((path.stat().st_mtime_ns, path.read_bytes()), before)
        self.assertEqual(registry.verify_original("old", original)["released_by"], terminal)
        self.assertIsNone(registry.read()["new"]["released_at"])

    def test_late_old_candidate_cannot_complete_successor_on_same_session(self):
        self.narrow("local", "closeout")
        co = self.f.start()
        original = co.claim("local")
        self.f.work(original)
        candidate = self.runner().candidate(self.run, "local")["candidate"]
        self.f.accept(co, "local", json_ref(candidate))
        successor = co.claim("closeout")
        self.assertNotEqual(original["task_id"], successor["task_id"])
        stale_path = co.root / "nodes" / "closeout" / "candidate.json"
        stale_path.write_bytes(Path(candidate["path"]).read_bytes())
        events, native = self.event_bytes(co), self.native_bytes()
        leases = (co.leases.root / "leases.json").read_bytes()
        with self.assertRaisesRegex(ContractError, "candidate task ownership changed"):
            co.transition("closeout", State.EVIDENCE_READY, {"candidate": file_ref(stale_path)})
        with self.assertRaisesRegex(ContractError, "candidate task ownership changed"):
            self.runner().candidate(self.run, "closeout")
        self.assertEqual(self.event_bytes(co), events)
        self.assertEqual(self.native_bytes(), native)
        self.assertEqual((co.leases.root / "leases.json").read_bytes(), leases)
        self.assertEqual(co.state()[0], {"local": State.ACCEPTED, "closeout": State.LOCAL_RUNNING})
        self.assertIsNone(co.leases.read()[successor["task_id"]]["released_at"])

    def test_callback_and_local_candidate_ready_together_survive_one_step_yield(self):
        self.narrow("local", "review")
        co = self.f.start()
        local = co.claim("local")
        self.f.work(local)
        candidate = self.runner().candidate(self.run, "local")["candidate"]
        review = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        self.f.work(review, "claude_a")
        protocol = self.f.callback(co, "review")
        runtime = FakeRuntime(self.f, co)
        events, native = self.event_bytes(co), self.native_bytes()
        first = self.runner(runtime).advance(self.run, max_steps=1)
        self.assertEqual([(row["node_id"], row["action"]) for row in first["actions"]],
                         [("review", "callback")])
        self.assertEqual(first["actions"][0]["status"], "RETURNED")
        self.assertTrue((co.root / "nodes" / "review" / "continuation.json").is_file())
        self.assertEqual(self.event_bytes(co), events)
        self.assertEqual(file_ref(candidate["path"]), candidate)
        # No new native message/event is needed to find either pending result.
        second = self.runner(runtime).advance(self.run, max_steps=2)
        self.assertEqual({row["node_id"] for row in second["supervisor_actions"]}, {"local", "review"})
        self.assertTrue(all(row["action"] == "review-and-accept" for row in second["supervisor_actions"]))
        self.assertEqual(second["states"], {"local": "EVIDENCE_READY", "review": "EVIDENCE_READY"})
        frozen_review = json_ref(co.state()[1]["review"]["candidate"])
        self.assertEqual(frozen_review["protocol"]["callback"], protocol["callback"])
        self.assertEqual(self.native_bytes(), native)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(runtime.callback_settlements, [])
        for info in (local, review):
            self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_restarted_runner_discovers_frozen_backlog_without_new_events(self):
        self.narrow("local")
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        candidate = self.runner().candidate(self.run, "local")["candidate"]
        events, native = self.event_bytes(co), self.native_bytes()
        leases = (co.leases.root / "leases.json").read_bytes()
        scripts = []
        for _ in range(3):
            restarted = self.runner()
            view = restarted.next_action(self.run)
            self.assertEqual(view["nodes"][0]["recommendation"]["action"], "review-frozen-candidate")
            result = restarted.advance(self.run, max_steps=1)
            handoff, = result["supervisor_actions"]
            self.assertEqual(handoff["action"], "review-and-accept")
            self.assertEqual(handoff["candidate"], candidate)
            scripts.append(handoff["exec_script"])
        self.assertEqual(len(set(scripts)), 1)
        self.assertEqual(file_ref(candidate["path"]), candidate)
        self.assertEqual(self.event_bytes(co), events)
        self.assertEqual(self.native_bytes(), native)
        self.assertEqual((co.leases.root / "leases.json").read_bytes(), leases)
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])


if __name__ == "__main__":
    unittest.main()
