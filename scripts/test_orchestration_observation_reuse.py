"""Observation-local reuse preserves evidence fences and fresh action gates."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, read_json, write_json
from orchestration_coordinator import Coordinator
from orchestration_evidence import marker_for
from orchestration_observe import _next_action_from_co
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture


class ObservationReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, version=3)
        self.r = Runner(self.f.ref, fixtures=True)
        self.run = "run-01"
        self.co = self.f.start()

    def accept_local(self):
        info = self.co.claim("local")
        self.f.work(info)
        candidate = self.r.candidate(self.run, "local")
        marker = marker_for(info, json_ref(candidate["candidate"]))
        self.f.control("accept", self.run, node_id="local", marker=marker)
        self.r.accept(self.run, "local", marker)
        return info

    def test_status_and_advice_equal_independent_baseline_across_lifecycle(self):
        for stage in ("pending", "accepted", "prepared"):
            with self.subTest(stage=stage):
                if stage == "accepted":
                    self.accept_local()
                elif stage == "prepared":
                    self.co.claim("review")
                    self.f.prepare(self.co, "review")
                co = self.r.co(self.run)
                states, _ = co.state()
                expected = dict(run=co.spec, root=str(co.root), state="STARTED", closed=False,
                                states={k: s.value for k, s in states.items()},
                                ready=[n["id"] for n in co.ready()])
                with patch.object(co, "state", wraps=co.state) as state:
                    self.assertEqual(self.r._status_from_co(co), expected)
                self.assertEqual(state.call_count, 1)
                self.assertEqual(_next_action_from_co(self.r, self.run, co), self.r.next_action(self.run))

    def test_public_observations_always_construct_fresh_coordinators(self):
        factory = self.r.co
        made = []
        def fresh(run_id):
            co = factory(run_id)
            made.append(co)
            return co
        with patch.object(self.r, "co", side_effect=fresh):
            self.r.status(self.run)
            self.r.status(self.run)
            self.r.next_action(self.run)
            self.r.next_action(self.run)
        self.assertEqual(len(made), 4)
        self.assertEqual(len({id(co) for co in made}), 4)

    def test_advance_shares_only_initial_observation_and_keeps_fresh_claim_and_refresh(self):
        factory, gate = self.r.co, Coordinator._gate
        made, gated = [], []
        def fresh(run_id):
            co = factory(run_id)
            made.append(co)
            return co
        def checked_gate(co, *args, **kwargs):
            gated.append(co)
            return gate(co, *args, **kwargs)
        with patch.object(self.r, "co", side_effect=fresh), \
             patch.object(Coordinator, "_gate", checked_gate):
            result = self.r.advance(self.run)
        self.assertEqual(result["actions"][0]["action"], "claim")
        self.assertEqual(len(made), 3)
        self.assertEqual(len({id(co) for co in made}), 3)
        self.assertIn(made[1], gated)
        self.assertNotIn(made[0], gated)
        self.assertNotIn(made[-1], gated)

    def test_header_change_between_status_and_advice_is_refused_without_action(self):
        status = self.r._status_from_co
        def changed(co):
            result = status(co)
            value = read_json(co.root / "run.json")
            value["changed"] = True
            write_json(co.root / "run.json", value)
            return result
        with patch.object(self.r, "_status_from_co", side_effect=changed):
            result = self.r.advance(self.run)
        self.assertEqual(result["status"], "PENDING_OR_BLOCKED")
        self.assertEqual(result["actions"], [])
        self.assertIn("run header changed", result["observation_error"]["error"])

    def test_valid_claim_between_status_and_advice_is_refused_without_second_action(self):
        status = self.r._status_from_co
        def changed(co):
            result = status(co)
            self.co.claim("local")
            return result
        with patch.object(self.r, "_status_from_co", side_effect=changed):
            result = self.r.advance(self.run)
        self.assertEqual(result["stop_reason"], "observation_refused")
        self.assertEqual(result["actions"], [])
        self.assertEqual(len(self.co.events()), 1)

    def test_lease_change_between_status_and_advice_is_refused(self):
        info = self.accept_local()
        status = self.r._status_from_co
        def changed(co):
            result = status(co)
            leases = co.leases.read()
            lease = leases[info["task_id"]]
            lease["released_at"] = (datetime.fromisoformat(lease["released_at"]) + timedelta(seconds=1)).isoformat()
            write_json(co.leases.root / "leases.json", leases)
            return result
        with patch.object(self.r, "_status_from_co", side_effect=changed):
            result = self.r.advance(self.run)
        self.assertEqual(result["stop_reason"], "observation_refused")
        self.assertIn("run advanced during observation", result["observation_error"]["error"])
        self.assertEqual(result["actions"], [])

    def test_event_publication_between_status_and_advice_is_refused(self):
        self.co.claim("review")
        self.f.prepare(self.co, "review")
        event = self.co.events()[-1]
        path = self.co.root / "events" / f"{event['seq']:06d}.json"
        # Simulate already published prepared evidence, before its event.
        path.unlink()
        status = self.r._status_from_co
        def changed(co):
            result = status(co)
            write_json(path, event)
            return result
        with patch.object(self.r, "_status_from_co", side_effect=changed):
            result = self.r.advance(self.run)
        self.assertEqual(result["stop_reason"], "observation_refused")
        self.assertIn("run advanced during observation", result["observation_error"]["error"])
        self.assertEqual(result["actions"], [])

    def test_accepted_output_changed_after_state_is_still_revalidated(self):
        info = self.accept_local()
        co = self.r.co(self.run)
        state = co.state
        def changed():
            snapshot = state()
            (Path(info["output_root"]) / "result.json").write_text("changed after state")
            return snapshot
        with patch.object(co, "state", side_effect=changed), self.assertRaises(ContractError):
            self.r._status_from_co(co)


if __name__ == "__main__":
    unittest.main()
