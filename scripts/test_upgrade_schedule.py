"""Synthetic verifier boundaries for ordering; no test creates live clients."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
from orchestration_permit import elapsed, launch_start, read_halt, read_permit, steps, wait_permit, work_budget
from upgrade_benchmark import ARM_STRATEGIES, register, validate_registration
import upgrade_schedule as schedule


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "registered"
        self.ref = register(self.root, "offline-schedule")
        self.registration = validate_registration(self.ref)
        self.runtimes, self.campaigns, self.rows = {}, {}, {}
        for key, group in self.registration["groups"].items():
            directory = Path(group["context_path"]).parent
            context = write_json(directory / "experiment.json", dict(paths={"campaigns": str(directory / "campaigns")}))
            startup = write_json(directory / "startup.json", dict(stop_path=str(directory / "stop.json"),
                                                                 adoption_path=str(directory / "adoption.json")))
            runtime = write_json(directory / "runtime.json", dict(context=context, arm=group["arm"], startup=startup))
            campaign = dict(registration=self.ref, session_group=key, runtime=runtime, experiment=context,
                arm=group["arm"], workload_class=group["workload_class"], campaign_id=group["campaign_id"],
                root=str(directory / "campaigns" / group["arm"] / group["campaign_id"]),
                runs=[dict(run_id=r["run_id"], strategy=ARM_STRATEGIES[group["arm"]]) for r in group["runs"]])
            self.runtimes[key] = runtime
            self.campaigns[key] = write_json(Path(campaign["root"]) / "campaign.json", campaign)
        for name, side_effect in (("validate_live_runtime", lambda ref: (None, json_ref(ref))),
                                  ("validate_campaign", json_ref),
                                  ("measurement", lambda ref, run: self.rows[(ref["path"], run)])):
            mocked = patch.object(schedule, name, side_effect=side_effect)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(schedule, "sandbox_check", return_value=0)
        self.sandbox = mocked.start()
        self.addCleanup(mocked.stop)

    def issue(self, group="pilot-B"):
        return schedule.issue(self.ref, self.runtimes[group])

    def close(self, ordinal=1, **changes):
        step = steps(self.registration)[ordinal - 1]
        campaign = self.campaigns[step["group"]]
        finish = write_json(Path(json_ref(campaign)["root"]) / "runs" / step["run_id"] / "finish.json",
                            dict(synthetic_offline_closeout=True))
        row = dict(**step, arm=self.registration["groups"][step["group"]]["arm"], status="PASS",
            contract_pass=True, within_cap=True, human_interventions=0, elapsed_seconds=10,
            evidence_class="registered_live_measurement_v1", campaign=campaign, finish=finish)
        row.update(changes)
        self.rows[(campaign["path"], step["run_id"])] = row
        return schedule.close(campaign, step["run_id"])

    def test_exact_33_steps_and_unclosed_run_cannot_be_skipped(self):
        self.assertEqual(len(steps(self.registration)), 33)
        first = self.issue()
        self.assertEqual(first["step"]["run_id"], "pilot-01")
        self.assertEqual(self.issue()["permit"], first["permit"])
        with self.assertRaisesRegex(ContractError, "unclosed"):
            self.issue("uneven_successor-A1")
        self.assertEqual(schedule.status(self.ref)["permits"], 1)
        self.close()
        second = self.issue()
        self.assertEqual(second["step"]["run_id"], "pilot-02")
        self.assertEqual(read_permit(self.ref, 1)[0], first["permit"])
        with self.assertRaisesRegex(ContractError, "closed"):
            read_permit(self.ref, 1, active=True)

    def test_same_group_runtime_cannot_be_replaced_after_closeout(self):
        self.issue()
        self.close()
        runtime = json_ref(self.runtimes["pilot-B"])
        replacement = write_json(self.root / "replacement.json", dict(runtime, changed=True))
        with self.assertRaisesRegex(ContractError, "cannot be replaced"):
            schedule.issue(self.ref, replacement)
        self.assertEqual(schedule.status(self.ref)["permits"], 1)

    def test_failure_unknown_timeout_and_B_intervention_never_advance(self):
        for changes in (dict(status="FAILED", contract_pass=False), dict(status="UNKNOWN"),
                        dict(status="TIMEOUT", within_cap=False), dict(human_interventions=1),
                        dict(human_interventions=False), dict(evidence_class="offline_fixture")):
            with self.subTest(changes=changes):
                # Each case is an independent test registration, never a live retry.
                case = ScheduleTests("test_exact_33_steps_and_unclosed_run_cannot_be_skipped")
                case.setUp()
                try:
                    case.issue()
                    result = case.close(**changes)
                    self.assertFalse(result["next_allowed"])
                    with self.assertRaisesRegex(ContractError, "prevents further permits"):
                        case.issue()
                    self.assertEqual(schedule.status(case.ref)["status"], "FAILURE_PRESERVED")
                finally:
                    case.doCleanups()

    def test_previous_measurement_is_recomputed_before_advancement(self):
        self.issue()
        self.close()
        key = (self.campaigns["pilot-B"]["path"], "pilot-01")
        self.rows[key] = dict(self.rows[key], elapsed_seconds=999)
        with self.assertRaisesRegex(ContractError, "no longer verifies"):
            self.issue()
        self.assertEqual(schedule.status(self.ref)["permits"], 1)

    def test_rehashed_false_predecessor_classification_is_rejected(self):
        self.issue()
        self.close()
        second = self.issue()
        closure_path = self.root / "schedule/closures/0001.json"
        closure = json_ref(file_ref(closure_path))
        closure["measurement"]["status"] = "UNKNOWN"
        previous = write_json(closure_path, closure)
        permit = json_ref(second["permit"])
        permit["previous"] = previous
        write_json(second["permit"]["path"], permit)
        with self.assertRaisesRegex(ContractError, "classification"):
            read_permit(self.ref, 2)

    def test_client_cannot_publish_or_create_schedule(self):
        self.sandbox.return_value = 1
        with self.assertRaisesRegex(ContractError, "cannot publish"):
            self.issue()
        self.assertFalse((self.root / "schedule").exists())

    def test_wait_only_observes_original_permit_and_stops_at_failure(self):
        campaign = json_ref(self.campaigns["pilot-B"])
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        self.assertEqual(wait_permit(campaign, "pilot-01", 0)["status"], "WAITING_PERMIT")
        self.assertEqual(before, sorted(p.relative_to(self.root) for p in self.root.rglob("*")))
        self.issue()
        self.assertEqual(wait_permit(campaign, "pilot-01", 0)["status"], "PERMIT_READY")
        self.close(status="UNKNOWN")
        self.assertEqual(wait_permit(campaign, "pilot-01", 0)["status"], "RUN_ALREADY_CLOSED")
        self.assertEqual(wait_permit(campaign, "pilot-02", 0)["status"], "SCHEDULE_ENDED")
        for timeout in (-1, 46, True, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ContractError):
                wait_permit(campaign, "pilot-02", timeout)

    def test_cold_clock_binds_original_launch_after_permit(self):
        runtime = self.runtimes["uneven_successor-A1"]
        campaign = json_ref(self.campaigns["uneven_successor-A1"])
        startup = json_ref(json_ref(runtime)["startup"])
        path = Path(startup["adoption_path"]).parent / "launch-request.json"
        permit = dict(step=dict(phase="cold"), issued_at="2026-10-17T00:00:00Z")
        launch = dict(runtime=runtime, startup=json_ref(runtime)["startup"],
            requested_at="2026-10-17T00:00:01Z", monotonic_ns=1, boot_id="fixture-boot")
        original = write_json(path, launch)
        self.assertEqual(launch_start(campaign, permit), original)
        write_json(path, dict(launch, requested_at="2026-10-16T23:59:59Z"))
        with self.assertRaisesRegex(ContractError, "follow its original permit"):
            launch_start(campaign, permit)

    def test_halt_keeps_original_permit_and_lease_but_allows_closeout(self):
        permit = self.issue()["permit"]
        lease = write_json(self.root / "original-lease.json", {"owner": "original", "released": False})
        halted = schedule.halt(self.ref, "original operation timed out", "TIMEOUT", [lease])
        self.assertEqual(schedule.status(self.ref)["status"], "HALTED")
        self.assertEqual(read_permit(self.ref, 1, active=True)[0], permit)
        self.assertEqual(file_ref(lease["path"]), lease)
        self.assertEqual(read_halt(self.ref)[0], halted["halt"])
        self.assertEqual(wait_permit(json_ref(self.campaigns["pilot-B"]), "pilot-01", 0)["status"], "SCHEDULE_ENDED")
        with self.assertRaisesRegex(ContractError, "halted"):
            self.issue()
        # Observation and a genuine original terminal closeout remain legal.
        self.close(status="TIMEOUT", within_cap=False)
        self.assertEqual(schedule.status(self.ref)["closures"], 1)
        self.assertEqual(schedule.halt(self.ref, "later observation", "FAILED")["halt"], halted["halt"])

    def test_halt_timeouts_keep_all_33_slots_and_15_pairs(self):
        self.issue()
        schedule.halt(self.ref, "startup window expired", "TIMEOUT")
        report = schedule.report(self.ref)
        self.assertEqual((len(report["runs"]), len(report["pairs"])), (33, 15))
        self.assertEqual(report["runs"][0]["status"], "TIMEOUT")
        self.assertEqual(sum(r["status"] == "MISSING" for r in report["runs"]), 32)
        self.assertEqual(report["pilot_passes"], 0)
        self.assertEqual(report["conclusion"], "INSUFFICIENT_EVIDENCE")

    def test_halt_does_not_gate_original_acceptance_but_refuses_new_claims(self):
        from orchestration_coordinator import Coordinator
        permit = self.issue()["permit"]
        co = object.__new__(Coordinator)
        co.campaign_ref = self.campaigns["pilot-B"]
        co.campaign = dict(json_ref(co.campaign_ref), version=3)
        co.fixtures = False
        co.root = Path(co.campaign["root"]) / "runs/pilot-01"
        co.spec = {"run_id": "pilot-01"}
        co.header = {"run_permit": permit}
        schedule.halt(self.ref, "unknown input outcome")
        with patch("orchestration_coordinator.validate_campaign"), patch(
                "orchestration_coordinator.require_live_identity", return_value={"original": True}):
            self.assertEqual(co._gate(), {"original": True})
            with self.assertRaisesRegex(ContractError, "halted"):
                co._gate(new_work=True)

    def test_experimental_client_cannot_halt(self):
        self.issue()
        self.sandbox.return_value = 1
        with self.assertRaisesRegex(ContractError, "cannot publish"):
            schedule.halt(self.ref, "unauthorized external publication")
        self.assertIsNone(read_halt(self.ref))


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.header = dict(started_at="2026-10-17T00:00:00Z", created_at="2026-10-17T00:00:02Z",
                           started_monotonic_ns=2_000_000_000, boot_id="boot", launch_start=None)
        self.end = dict(at="2026-10-17T00:00:12Z", monotonic_ns=12_000_000_000, boot_id="boot")

    def test_warm_includes_start_invocation_and_cold_includes_launch(self):
        self.assertEqual(elapsed(self.header, end=self.end)["seconds"], 12)
        with tempfile.TemporaryDirectory() as path:
            ref = write_json(Path(path).resolve() / "launch.json", dict(requested_at="2026-10-17T00:00:01Z",
                monotonic_ns=1_000_000_000, boot_id="boot"))
            self.assertEqual(elapsed(dict(self.header, launch_start=ref), end=self.end)["seconds"], 11)

    def test_cross_boot_backwards_and_discontinuous_clocks_are_unknown(self):
        for change in (dict(boot_id="other"), dict(monotonic_ns=1),
                       dict(at="2026-10-17T01:00:00Z"), dict(at="2026-10-16T23:59:59Z")):
            with self.subTest(change=change):
                self.assertIsNone(elapsed(self.header, end=dict(self.end, **change))["seconds"])

    def test_expired_or_unknown_budget_refuses_new_work_without_mutations(self):
        with patch("orchestration_permit.require_running"), patch(
                "orchestration_permit.validate_registration", return_value={"contract": {"run_timeout_seconds": 1800}}):
            for seconds in (1800.1, None):
                with self.subTest(seconds=seconds), patch("orchestration_permit.elapsed", return_value={"seconds": seconds}):
                    with self.assertRaises(ContractError):
                        work_budget({"registration": {}}, copy.deepcopy(self.header))
            with patch("orchestration_permit.elapsed", return_value={"seconds": 1800}):
                work_budget({"registration": {}}, self.header)


if __name__ == "__main__":
    unittest.main()
