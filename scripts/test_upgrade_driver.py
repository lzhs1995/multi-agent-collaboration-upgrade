"""Offline crash/order tests. Synthetic client actions are never live evidence."""
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import (ContractError, boot_id, exclusive_lock, file_ref, json_ref,
    tree_manifest, utc_now, write_json)
from orchestration_permit import read_permit, steps
import test_upgrade_schedule as schedule_tests
import upgrade_driver as driver
import upgrade_supervisor_instructions as instructions


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.case = schedule_tests.ScheduleTests("test_exact_33_steps_and_unclosed_run_cannot_be_skipped")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root
        self.actions = []
        for group, ref in list(self.case.runtimes.items()):
            runtime = json_ref(ref)
            runtime["state_root"] = str(Path(ref["path"]).parent / "state")
            ref = write_json(ref["path"], runtime)
            self.case.runtimes[group] = ref
            campaign = json_ref(self.case.campaigns[group])
            campaign.update(runtime=ref, version=3, evidence_class="cli_campaign_v3",
                adapter={"path": str(self.root / "source")}, python={"path": "/pinned/python"},
                sessions={lane: dict(provider=p, session_id=lane + "-original", surface_uuid=lane + "-uuid")
                          for lane, p in (("codex", "codex"), ("claude_a", "claude"), ("claude_b", "claude"))})
            self.case.campaigns[group] = write_json(self.case.campaigns[group]["path"], campaign)
        for name in ("source", "corpus"):
            (self.root / name).mkdir()
            (self.root / name / "pinned.txt").write_text(name)
        mocks = [(driver, "validate_live_runtime", lambda ref: (None, json_ref(ref))),
                 (driver, "validate_adoption", lambda *a, **kw: None),
                 (driver, "validate_campaign", json_ref),
                 (instructions, "validate_campaign", json_ref),
                 (driver, "authorize", lambda value: value),
                 (driver, "control", self.control),
                 (driver, "freeze_campaign", self.freeze),
                 (driver, "publish_bootstrap", self.bootstrap)]
        for module, name, effect in mocks:
            mocked = patch.object(module, name, side_effect=effect)
            mocked.start()
            self.addCleanup(mocked.stop)
        watcher = patch.object(driver, "CodexFailureWatch")
        self.watch = watcher.start().return_value
        self.watch.observe.return_value = None
        self.watch.pending = False
        self.addCleanup(watcher.stop)
        request = write_json(self.root / "request.json", dict(schema="upgrade-driver-request-v1",
            registration=self.case.ref, runtimes=self.case.runtimes, source=tree_manifest(self.root / "source"),
            corpus=tree_manifest(self.root / "corpus"), revision="offline-only",
            native_authorizations=[{"offline_test_only": True}]))
        self.ref = driver.configure(request)
        self.driver = driver.Driver(self.ref)

    def control(self, ref, command):
        import time
        runtime = json_ref(ref)
        spec = json_ref(runtime["startup"])
        group = next(k for k, r in self.case.runtimes.items() if r == ref)
        if command == "check":
            return dict(status="CONFIGURATION_VERIFIED", launched=False, stopped=False, adoption_published=False)
        self.actions.append((command, group))
        if command == "launch":
            permit = read_permit(self.case.ref, driver.schedule.status(self.case.ref)["permits"])[1]
            self.assertEqual(permit["runtime"], ref)
            write_json(Path(runtime["state_root"]) / "startup/intent.json", {"synthetic": True}, exclusive=True)
            write_json(Path(spec["adoption_path"]).parent / "launch-request.json", dict(runtime=ref,
                startup=runtime["startup"], requested_at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id()), exclusive=True)
            return {"status": "LAUNCH_RETURNED"}
        self.assertEqual(command, "adopt")
        adoption = write_json(spec["adoption_path"], {"synthetic": True}, exclusive=True)
        return dict(status="ADOPTED", adoption=adoption)

    def freeze(self, request_ref):
        request = json_ref(request_ref)
        group = request["session_group"]
        self.actions.append(("freeze", group))
        ref = self.case.campaigns[group]
        campaign = json_ref(ref)
        campaign["freeze_request"] = request_ref
        ref = write_json(ref["path"], campaign)
        self.case.campaigns[group] = ref
        write_json(Path(ref["path"]).parent / "freeze-complete.json", {"campaign": ref}, exclusive=True)
        return dict(status="FROZEN_NOT_MEASURED", campaign=ref)

    def bootstrap(self, runtime_ref, campaign_ref, instructions_ref):
        self.actions.append(("bootstrap", json_ref(campaign_ref)["session_group"]))
        spec = json_ref(json_ref(runtime_ref)["startup"])
        ref = write_json(Path(spec["adoption_path"]).parent / "bootstrap.json", dict(runtime=runtime_ref,
            campaign=campaign_ref, instructions=Path(instructions_ref["path"]).read_text()), exclusive=True)
        return dict(status="BOOTSTRAP_PUBLISHED", bootstrap=ref)

    def started(self):
        self.assertEqual(self.driver.tick()["status"], "PERMIT_ISSUED")
        self.assertEqual(self.driver.tick()["status"], "LAUNCH_RETURNED")
        self.assertEqual(self.driver.tick()["status"], "ADOPTED")
        self.assertEqual(self.driver.tick()["status"], "BOOTSTRAP_PUBLISHED")

    def finish(self, ordinal):
        step = steps(self.case.registration)[ordinal - 1]
        ref = self.case.campaigns[step["group"]]
        finish = write_json(Path(json_ref(ref)["root"]) / "runs" / step["run_id"] / "finish.json", {"synthetic": True})
        self.case.rows[(ref["path"], step["run_id"])] = dict(**step,
            arm=self.case.registration["groups"][step["group"]]["arm"], status="PASS", contract_pass=True,
            workload_class=self.case.registration["groups"][step["group"]]["workload_class"],
            usage=dict(tokens=None, cost=None),
            within_cap=True, human_interventions=0, elapsed_seconds=10,
            evidence_class="registered_live_measurement_v1", campaign=ref, finish=finish)

    def test_permit_precedes_launch_and_warm_rounds_reuse_original_group(self):
        self.started()
        self.assertEqual(self.driver.tick()["status"], "WAITING_ORIGINAL_SUPERVISOR")
        self.finish(1)
        self.assertEqual(self.driver.tick()["status"], "CLOSED")
        self.assertEqual(self.driver.tick()["step"]["run_id"], "pilot-02")
        self.assertEqual(self.driver.tick()["status"], "WAITING_ORIGINAL_SUPERVISOR")
        self.assertEqual(self.actions, [(a, "pilot-B") for a in ("launch", "adopt", "freeze", "bootstrap")])

    def test_crash_after_launch_does_not_launch_again(self):
        self.driver.tick()
        def lost_result(ref, command):
            value = self.control(ref, command)
            if command == "launch":
                raise SystemExit("offline simulated publisher crash")
            return value
        with patch.object(driver, "control", side_effect=lost_result), self.assertRaises(SystemExit):
            self.driver.tick()
        resumed = driver.Driver(self.ref)
        self.assertEqual(resumed.tick()["status"], "ADOPTED")
        self.assertEqual(resumed.tick()["status"], "BOOTSTRAP_PUBLISHED")
        self.assertEqual(self.actions.count(("launch", "pilot-B")), 1)

    def test_crash_without_a_freeze_result_cannot_repeat_freeze(self):
        self.driver.tick()
        self.driver.tick()
        self.driver.tick()
        def crashed(ref):
            self.actions.append(("freeze_attempt", "pilot-B"))
            raise SystemExit("offline simulated freezer crash")
        with patch.object(driver, "freeze_campaign", side_effect=crashed), self.assertRaises(SystemExit):
            self.driver.tick()
        self.assertEqual(driver.Driver(self.ref).tick()["status"], "HALTED")
        self.assertEqual(self.actions.count(("freeze_attempt", "pilot-B")), 1)
        self.assertEqual(driver.schedule.status(self.case.ref)["permits"], 1)

    def test_crash_after_complete_freeze_observes_original_artifact(self):
        self.driver.tick()
        self.driver.tick()
        self.driver.tick()
        def crashed(ref):
            self.freeze(ref)
            raise SystemExit("offline crash after freeze completion")
        with patch.object(driver, "freeze_campaign", side_effect=crashed), self.assertRaises(SystemExit):
            self.driver.tick()
        self.assertEqual(driver.Driver(self.ref).tick()["status"], "BOOTSTRAP_PUBLISHED")
        self.assertEqual(self.actions.count(("freeze", "pilot-B")), 1)

    def test_held_publisher_lock_cannot_be_replaced(self):
        with exclusive_lock(self.driver.root / "publisher.lock"):
            original = file_ref(self.driver.root / "publisher.lock")
            with self.assertRaises(ContractError):
                self.driver.tick()
            self.assertEqual(file_ref(self.driver.root / "publisher.lock"), original)
        self.assertEqual(driver.schedule.status(self.case.ref)["permits"], 0)

    def test_cli_keeps_runner_lock_between_ticks_and_refuses_another_cli(self):
        args = ["run", "--config", self.ref["path"], "--config-sha256", self.ref["sha256"]]
        seen = []

        def during_wait(seconds):
            path = self.driver.root / "runner.lock"
            before = path.stat()
            with contextlib.redirect_stdout(io.StringIO()):
                seen.append(driver.main(["tick", *args[1:]]))
            self.assertEqual((path.stat().st_dev, path.stat().st_ino), (before.st_dev, before.st_ino))
            self.assertEqual(driver.schedule.status(self.case.ref)["permits"], 0)

        with patch.object(driver.Driver, "tick", side_effect=[
                {"status": "WAITING_ORIGINAL_SUPERVISOR"}, {"status": "COMPLETE", "report": {}}]) as tick, \
                patch.object(driver.time, "sleep", side_effect=during_wait), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(driver.main(args), 0)
        self.assertEqual(seen, [2])
        self.assertEqual(tick.call_count, 2)
        with exclusive_lock(self.driver.root / "runner.lock"):
            pass

    def test_startup_timeout_keeps_original_identities_and_full_denominator(self):
        self.driver.tick()
        self.driver.tick()
        with patch.object(driver.Driver, "_clock", return_value={"seconds": 901}):
            self.assertEqual(self.driver.tick()["status"], "HALTED")
        result = self.driver.tick()
        report = json_ref(result["report"])
        self.assertEqual((len(report["runs"]), len(report["pairs"])), (33, 15))
        self.assertEqual(report["runs"][0]["status"], "TIMEOUT")
        self.assertEqual(self.actions, [("launch", "pilot-B")])

    def test_changed_frozen_source_halts_before_any_client_launch(self):
        self.driver.tick()
        (self.root / "source/pinned.txt").write_text("unregistered source drift")
        self.assertEqual(self.driver.tick()["status"], "HALTED")
        self.assertEqual(self.actions, [])

    def test_native_supervisor_error_halts_without_waiting_or_replaying(self):
        self.started()
        self.watch.observe.return_value = dict(status="NATIVE_CODEX_TURN_FAILED",
            turn_id="01a14b64-92de-7931-a649-61ef10b038ac", input_operations=0)
        self.assertEqual(self.driver.tick()["status"], "HALTED")
        report = json_ref(self.driver.tick()["report"])
        self.assertEqual((len(report["runs"]), len(report["pairs"])), (33, 15))
        self.assertEqual(report["runs"][0]["status"], "FAILED")
        self.assertEqual(self.actions, [(a, "pilot-B") for a in ("launch", "adopt", "freeze", "bootstrap")])

    def test_native_abort_halts_with_reason_and_preserves_registered_denominator(self):
        self.started()
        self.watch.observe.return_value = dict(status="NATIVE_CODEX_TURN_ABORTED",
            reason="interrupted", turn_id="01a14b64-92de-7931-a649-61ef10b038ac", input_operations=0)
        result = self.driver.tick()
        self.assertEqual(result["classification"], "FAILED")
        halt = json_ref(result["halt"])
        self.assertEqual(halt["reason"], "Original Codex turn aborted (interrupted); no replay: "
                         "01a14b64-92de-7931-a649-61ef10b038ac")
        evidence = json_ref(halt["evidence"][0])
        self.assertEqual(evidence["reason"], "interrupted")
        self.assertEqual(evidence["status"], "NATIVE_CODEX_TURN_ABORTED")
        report = json_ref(self.driver.tick()["report"])
        self.assertEqual((len(report["runs"]), len(report["pairs"])), (33, 15))
        self.assertEqual(report["runs"][0]["status"], "FAILED")
        self.assertEqual(self.actions, [(a, "pilot-B") for a in ("launch", "adopt", "freeze", "bootstrap")])

    def test_abort_preserves_finished_measurement_and_prevents_next_permit(self):
        self.started()
        self.finish(1)
        self.watch.observe.return_value = dict(status="NATIVE_CODEX_TURN_ABORTED",
            reason="interrupted", turn_id="01a14b64-92de-7931-a649-61ef10b038ac", input_operations=0)
        self.assertEqual(self.driver.tick()["classification"], "FAILED")
        report = json_ref(self.driver.tick()["report"])
        self.assertEqual(report["runs"][0]["status"], "PASS")
        self.assertEqual((len(report["runs"]), len(report["pairs"])), (33, 15))
        state = driver.schedule.status(self.case.ref)
        self.assertEqual((state["permits"], state["closures"]), (1, 0))

    def test_crash_after_abort_evidence_reuses_exact_first_file(self):
        self.started()
        self.watch.observe.return_value = dict(status="NATIVE_CODEX_TURN_ABORTED",
            reason="interrupted", turn_id="01a14b64-92de-7931-a649-61ef10b038ac", input_operations=0)
        with patch.object(self.driver, "halt", side_effect=SystemExit("crash after abort evidence")):
            with self.assertRaises(SystemExit):
                self.driver.tick()
        evidence = next((self.driver.root / "faults").glob("native-*.json"))
        original = file_ref(evidence)
        result = driver.Driver(self.ref).tick()
        self.assertEqual(result["classification"], "FAILED")
        self.assertEqual(file_ref(evidence), original)
        self.watch.verify_saved.assert_called_once_with(self.watch.observe.return_value)
        self.assertEqual(json_ref(result["halt"])["evidence"], [original])

    def test_real_native_abort_after_budget_stops_driver_despite_tool_completion(self):
        from test_orchestration_native import NativeFixture, STAMP
        from upgrade_failure_watch import CodexFailureWatch
        native = NativeFixture(self.root, "codex")
        native.session["freeze_fence"] = native.reader.snapshot()
        ref = self.case.campaigns["pilot-B"]
        campaign = json_ref(ref)
        campaign["sessions"]["codex"] = native.session
        self.case.campaigns["pilot-B"] = write_json(ref["path"], campaign)
        self.started()
        self.finish(1)
        for _ in range(256):
            native.append(native.message("assistant", "offline preceding observation"))
        abort = native.append(dict(type="event_msg", timestamp=STAMP, payload=dict(
            type="turn_aborted", turn_id="01a14b64-92de-7931-a649-61ef10b038ac", reason="interrupted")))
        native.append(dict(type="event_msg", timestamp=STAMP, payload=dict(
            type="item_completed", item=dict(type="CommandExecution", status="completed",
                                              exit_code=0, stdout="BOOTSTRAP_READY"))))
        with patch.object(driver, "CodexFailureWatch", side_effect=lambda session:
                          CodexFailureWatch(session, fixtures=True)):
            self.assertEqual(self.driver.tick()["status"], "WAITING_NATIVE_OBSERVATION")
            result = self.driver.tick()
        self.assertEqual(result["status"], "HALTED")
        self.assertEqual(result["classification"], "FAILED")
        saved = json_ref(json_ref(result["halt"])["evidence"][0])
        self.assertEqual(saved["record"], abort)
        self.assertEqual(saved["reason"], "interrupted")
        self.assertEqual(saved["input_operations"], 0)
        report = json_ref(self.driver.tick()["report"])
        self.assertEqual(report["runs"][0]["status"], "PASS")
        self.assertEqual((len(report["runs"]), len(report["pairs"])), (33, 15))
        state = driver.schedule.status(self.case.ref)
        self.assertEqual((state["permits"], state["closures"]), (1, 0))
        self.assertEqual(self.actions, [(a, "pilot-B") for a in ("launch", "adopt", "freeze", "bootstrap")])

    def test_unread_native_records_hold_finished_run_before_next_permit(self):
        self.started()
        self.finish(1)
        self.watch.pending = True
        self.assertEqual(self.driver.tick()["status"], "WAITING_NATIVE_OBSERVATION")
        state = driver.schedule.status(self.case.ref)
        self.assertEqual((state["permits"], state["closures"]), (1, 0))
        self.watch.pending = False
        self.assertEqual(self.driver.tick()["status"], "CLOSED")

    def test_crash_after_native_evidence_reuses_exact_first_file(self):
        self.started()
        self.watch.observe.return_value = dict(status="NATIVE_CODEX_TURN_FAILED",
            turn_id="01a14b64-92de-7931-a649-61ef10b038ac", input_operations=0)
        with patch.object(self.driver, "halt", side_effect=SystemExit("crash after evidence")):
            with self.assertRaises(SystemExit):
                self.driver.tick()
        evidence = next((self.driver.root / "faults").glob("native-*.json"))
        original = file_ref(evidence)
        result = driver.Driver(self.ref).tick()
        self.assertEqual(result["classification"], "FAILED")
        self.assertEqual(file_ref(evidence), original)
        self.watch.verify_saved.assert_called_once_with(self.watch.observe.return_value)
        self.assertEqual(json_ref(result["halt"])["evidence"], [original])

    def test_pending_native_reader_still_enforces_registered_timeout(self):
        self.started()
        self.watch.pending = True
        with patch.object(self.driver, "_clock", return_value={"seconds": 1801}):
            self.assertEqual(self.driver.tick()["classification"], "TIMEOUT")

    def test_failure_does_not_erase_finished_measurement_or_allow_next_run(self):
        self.started()
        self.finish(1)
        self.watch.observe.return_value = dict(status="NATIVE_CODEX_TURN_FAILED",
            turn_id="01a14b64-92de-7931-a649-61ef10b038ac", input_operations=0)
        self.assertEqual(self.driver.tick()["classification"], "FAILED")
        report = json_ref(self.driver.tick()["report"])
        self.assertEqual(report["runs"][0]["status"], "PASS")
        self.assertEqual(len(report["runs"]), 33)
        state = driver.schedule.status(self.case.ref)
        self.assertEqual((state["permits"], state["closures"]), (1, 0))

    def test_original_supervisor_instructions_are_bounded_and_arm_specific(self):
        a = instructions.render(self.case.campaigns["uneven_successor-A1"])
        b = instructions.render(self.case.campaigns["pilot-B"])
        self.assertIn("A1 is not an artificially serial baseline", a)
        self.assertIn("Do not call drafts, publish-drafts, next-action or publish-continuation", a)
        self.assertIn("Use `publish-drafts --run RUN`", b)
        for body in (a, b):
            self.assertLess(len(body), 24000)
            self.assertIn("including its final LF", body)
            self.assertIn("same bounded", body)
            self.assertIn("run its bounded business command", body)
            self.assertIn("Do not create internal subagents", body)
        ref = instructions.publish(self.case.campaigns["pilot-B"])
        self.assertEqual(instructions.publish(self.case.campaigns["pilot-B"]), ref)
        Path(ref["path"]).write_text("changed original instructions")
        with self.assertRaisesRegex(ContractError, "changed"):
            instructions.publish(self.case.campaigns["pilot-B"])


if __name__ == "__main__":
    unittest.main()
