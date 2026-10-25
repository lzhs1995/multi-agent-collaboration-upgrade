"""Synthetic deadline/stop races; no test launches models or sends native input."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, exclusive_lock, file_ref, json_ref, write_json
from upgrade_experiment import NAME
import test_upgrade_driver as fixtures
import upgrade_live_control as live_control
import upgrade_watchdog as watchdog


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.DriverTests("test_permit_precedes_launch_and_warm_rounds_reuse_original_group")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.ref = self.case.ref
        self.watchdog = watchdog.Watchdog(self.ref)
        # Only private path construction is synthetic. The real publisher guard
        # receives and checks a controller authority, rather than being mocked.
        def scope(ref):
            runtime = json_ref(ref)
            runtime["controller"] = {"path": str(self.case.root / "source")}
            return runtime, json_ref(runtime["startup"])
        scoped = patch.object(watchdog, "stop_scope", side_effect=scope)
        scoped.start()
        self.addCleanup(scoped.stop)
        kernel = patch.object(live_control, "sandbox_check", return_value=0)
        self.kernel = kernel.start()
        self.addCleanup(kernel.stop)
        self.paths = [Path(json_ref(json_ref(ref)["startup"])["stop_path"])
                      for ref in self.case.driver.config["runtimes"].values()]

    def test_before_deadline_waits_without_stop_or_input(self):
        with self.case.deadline_clock(7199.5):
            result = self.watchdog.tick()
        self.assertEqual(result["status"], "WAITING_ORIGINAL_DEADLINE")
        self.assertAlmostEqual(result["remaining_seconds"], .5)
        self.assertFalse(any(p.exists() for p in self.paths))
        self.assertEqual(self.case.actions, [])

    def test_exact_deadline_stops_all_seven_before_halt_and_full_report(self):
        self.case.driver.tick()
        original_halt = self.watchdog.driver.halt
        def halt_after_stops(*args, **kwargs):
            self.assertEqual(sum(p.is_file() for p in self.paths), 7)
            return original_halt(*args, **kwargs)
        with self.case.deadline_clock(7200), patch.object(self.watchdog.driver, "halt", side_effect=halt_after_stops):
            result = self.watchdog.tick()
        self.assertEqual(result["status"], "HALTED")
        self.assertTrue(result["all_stopped"])
        self.assertEqual(json_ref(result["schedule"]["halt"])["classification"], "TIMEOUT")
        self.assertEqual((result["schedule"]["permits"], result["schedule"]["closures"]), (1, 0))
        report = json_ref(result["report"])
        self.assertEqual((len(report["runs"]), len(report["pairs"])), (33, 15))
        self.assertEqual(report["runs"][0]["status"], "TIMEOUT")
        for path in self.paths:
            self.assertFalse(json_ref(file_ref(path))["terminate_clients"])
        self.assertEqual(self.case.actions, [])

    def test_driver_locks_cannot_delay_watchdog_input_stop_or_report(self):
        root = self.case.driver.root
        with exclusive_lock(root / "publisher.lock"), exclusive_lock(root / "runner.lock"), \
                self.case.deadline_clock(7200):
            result = self.watchdog.tick()
        self.assertTrue(result["all_stopped"])
        self.assertIn("report", result)
        self.assertEqual(sum(p.exists() for p in self.paths), 7)

    def test_schedule_lock_delays_only_finalization_after_all_stops(self):
        stop = watchdog.stop_runtime_input
        with patch.object(watchdog, "stop_runtime_input", wraps=stop) as calls:
            with exclusive_lock(self.case.root / "schedule/publisher.lock"), self.case.deadline_clock(7200):
                waiting = self.watchdog.tick()
            self.assertEqual(waiting["status"], "WAITING_FINALIZATION")
            self.assertEqual(sum(p.exists() for p in self.paths), 7)
            with self.case.deadline_clock(7201):
                complete = watchdog.Watchdog(self.ref).tick()
            self.assertEqual(calls.call_count, 7)
        self.assertEqual(complete["status"], "HALTED")
        self.assertEqual(complete["stops"], waiting["stops"])

    def test_expiry_stops_all_before_a_failed_schedule_status_read(self):
        with self.case.deadline_clock(7200), patch.object(watchdog.schedule, "status", side_effect=OSError("offline ledger I/O")):
            waiting = self.watchdog.tick()
        self.assertEqual(waiting["status"], "WAITING_FINALIZATION")
        self.assertEqual(sum(p.exists() for p in self.paths), 7)
        self.assertTrue(waiting["all_stopped"])

    def test_one_failed_stop_preserves_failure_and_attempts_other_six(self):
        original = watchdog.stop_runtime_input
        first = next(iter(self.case.driver.config["runtimes"].values()))
        def stop(ref):
            if ref == first:
                raise ContractError("offline damaged original stop scope")
            return original(ref)
        with self.case.deadline_clock(7200), patch.object(watchdog, "stop_runtime_input", side_effect=stop) as calls:
            result = self.watchdog.tick()
        self.assertEqual(calls.call_count, 7)
        self.assertFalse(result["all_stopped"])
        self.assertEqual(sum(p.exists() for p in self.paths), 6)
        self.assertEqual(result["status"], "HALTED")
        self.assertIn("report", result)
        saved = json_ref(result["stops"])
        self.assertEqual(sum(v["status"] == "STOP_FAILED_PRESERVED" for v in saved["groups"].values()), 1)
        with self.case.deadline_clock(7300), patch.object(watchdog, "stop_runtime_input") as calls:
            recovered = watchdog.Watchdog(self.ref).tick()
        calls.assert_not_called()
        self.assertEqual(recovered["stops"], result["stops"])

    def test_existing_native_failure_stops_input_without_overwriting_cause(self):
        self.case.started()
        self.case.watch.observe.return_value = dict(status="NATIVE_CODEX_TURN_FAILED",
            turn_id="01a14b64-92de-7931-a649-61ef10b038ac", input_operations=0)
        original = self.case.driver.tick()
        result = self.watchdog.tick()
        self.assertTrue(result["all_stopped"])
        self.assertEqual(result["schedule"]["halt"], original["halt"])
        self.assertEqual(json_ref(original["halt"])["classification"], "FAILED")

    def test_restart_preserves_stop_inodes_and_original_report(self):
        with self.case.deadline_clock(7200):
            first = self.watchdog.tick()
        refs = [file_ref(p) for p in self.paths]
        inodes = [p.stat().st_ino for p in self.paths]
        with self.case.deadline_clock(7500):
            second = watchdog.Watchdog(self.ref).tick()
        self.assertEqual(first["stops"], second["stops"])
        self.assertEqual(first["report"], second["report"])
        self.assertEqual([file_ref(p) for p in self.paths], refs)
        self.assertEqual([p.stat().st_ino for p in self.paths], inodes)

    def test_publisher_kernel_guard_receives_same_runtime_controller(self):
        ref = next(iter(self.case.driver.config["runtimes"].values()))
        with patch.object(live_control, "validate_live_runtime", side_effect=AssertionError("no integrity scan")):
            result = watchdog.stop_runtime_input(ref)
        self.assertEqual(result["status"], "STOPPED_INPUT")
        self.kernel.assert_called_once()
        self.assertEqual(self.kernel.call_args.args[1:], (str(self.case.root / "source"), "file-write-create"))

    def test_sandboxed_client_cannot_publish_stop(self):
        ref = next(iter(self.case.driver.config["runtimes"].values()))
        self.kernel.return_value = 1
        with self.assertRaisesRegex(ContractError, "CLIENT_CANNOT_PUBLISH_ADOPTION"):
            watchdog.stop_runtime_input(ref)
        self.assertFalse(any(p.exists() for p in self.paths))

    def test_unknown_cross_boot_clock_stops_without_reset(self):
        with self.case.deadline_clock(100, changed_boot=True):
            result = self.watchdog.tick()
        self.assertTrue(result["all_stopped"])
        self.assertEqual(json_ref(result["schedule"]["halt"])["classification"], "UNKNOWN")

    def test_clock_read_exception_stops_all_groups_before_unknown_halt(self):
        with patch.object(self.watchdog.driver, "_overall_timing", side_effect=ContractError("offline boot identity unavailable")):
            result = self.watchdog.tick()
        self.assertTrue(result["all_stopped"])
        self.assertEqual(sum(p.exists() for p in self.paths), 7)
        self.assertEqual(json_ref(result["schedule"]["halt"])["classification"], "UNKNOWN")

    def test_cli_at_deadline_exits_with_preserved_failure_report(self):
        args = ["run", "--config", self.ref["path"], "--config-sha256", self.ref["sha256"]]
        with self.case.deadline_clock(7200), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(watchdog.main(args), 2)
        self.assertIn('"all_stopped": true', output.getvalue())
        self.assertIn('"report":', output.getvalue())


@unittest.skipUnless(sys.platform == "darwin", "real external-publisher kernel check requires Darwin")
class RealStopScopeTests(unittest.TestCase):
    """Real pinned scope, filesystem and Darwin sandbox query; zero clients."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve() / "private-experiment"
        names = ("state", "config", "controllers", "campaigns", "evidence", "tmp", "bin")
        paths = {name: str(root / name) for name in names}
        context = write_json(root / "experiment.json", dict(skill=NAME, version=1, root=str(root), paths=paths,
            production_adoption=False, account_uid=os.getuid(), account_home=str(Path.home().resolve())))
        self.startup_path = root / "config/B/startup-v2.json"
        startup = write_json(self.startup_path, {"offline_test_only": True})
        # The materialized layout: runtime-v2.json in the arm's controllers
        # directory, the controller release below its releases/.
        controller = root / "controllers/B/releases/private-live-v2/source"
        controller.mkdir(parents=True)
        self.ref = write_json(root / "controllers/B/runtime-v2.json", dict(version=2, skill=NAME,
            execution_mode="live-isolated-v2", production_adoption=False, arm="B", context=context,
            startup=startup, controller={"path": str(controller)}))
        self.stop_path = root / "evidence/B/live-control/stop.json"

    def test_real_stop_scope_kernel_guard_and_idempotent_publication(self):
        first = watchdog.stop_runtime_input(self.ref)
        self.assertEqual(first["stop"]["path"], str(self.stop_path))
        inode = self.stop_path.stat().st_ino
        self.assertEqual(watchdog.stop_runtime_input(self.ref)["stop"], first["stop"])
        self.assertEqual(self.stop_path.stat().st_ino, inode)
        self.assertFalse(json_ref(first["stop"])["terminate_clients"])

    def test_changed_startup_bytes_cannot_block_original_input_stop(self):
        self.startup_path.write_text("offline changed startup; preserve original pin")
        self.assertEqual(watchdog.stop_runtime_input(self.ref)["status"], "STOPPED_INPUT")
        self.assertEqual(json_ref(file_ref(self.stop_path))["startup"], json_ref(self.ref)["startup"])

    def test_rehashed_runtime_outside_its_original_location_is_refused(self):
        original = json_ref(self.ref)
        replacement = write_json(Path(self.ref["path"]).parent / "replacement.json", original)
        with self.assertRaisesRegex(ContractError, "STOP_RUNTIME_LOCATION_CHANGED"):
            watchdog.stop_runtime_input(replacement)
        self.assertFalse(self.stop_path.exists())

    def test_controller_outside_the_arm_releases_is_refused(self):
        # The publisher's kernel query must be against this arm's own release,
        # never another directory of the same private root.
        root, original = Path(self.ref["path"]).parents[2], json_ref(self.ref)
        for outside in (root / "controllers/B", root / "tmp/B/releases/x/source", root / "controllers/A/releases/x/source"):
            with self.subTest(controller=str(outside)):
                outside.mkdir(parents=True, exist_ok=True)
                ref = write_json(Path(self.ref["path"]), dict(original, controller={"path": str(outside)}))
                with self.assertRaisesRegex(ContractError, "escapes authorized root"):
                    watchdog.stop_runtime_input(ref)
                self.assertFalse(self.stop_path.exists())


if __name__ == "__main__":
    unittest.main()
