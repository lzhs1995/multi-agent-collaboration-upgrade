"""Batch-held launch: hold without the launch clock; owner release only under the group's own active permit."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, write_json
from upgrade_client_isolation import LANES
import upgrade_live_control as control_mod

REAL_RUN = control_mod.subprocess.run


class BatchHoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = self.base = Path(self.tmp.name).resolve()
        for d in ("state/startup", "live-control", "work", "controller"):
            (base / d).mkdir(parents=True)
        write_json(base / "controller/upgrade-binding.json", {})
        self.startup = write_json(base / "startup.json", dict(adoption_path=str(base / "live-control/adoption.json"),
            stop_path=str(base / "STOP"), work_root=str(base / "work"), controller=str(base / "controller"),
            pins=dict(control_sandbox=dict(path="/pinned/control.sb"))))
        self.runtime = dict(startup=self.startup, state_root=str(base / "state"),
                            controller=dict(path=str(base / "controller")), python=dict(path="/pinned/python"))
        self.ref = write_json(base / "runtime.json", self.runtime)
        self.receipt = write_json(base / "receipt.json", {"synthetic": True})
        self.held, self.active, self.calls = True, dict(group="pilot-B", ordinal=1), []
        for name, effect in (("validate_live_runtime", lambda ref: (None, self.runtime)),
                             ("held_launch", lambda runtime: self.held),
                             ("external_publisher", lambda spec: None)):
            p = patch.object(control_mod, name, side_effect=effect)
            p.start()
            self.addCleanup(p.stop)

    def held_spawn(self, birth=(7, 8)):
        write_json(self.base / "live-control/held-request.json", {"synthetic": True})
        write_json(self.base / "state/startup/intent.json", {"synthetic": True})
        for i, lane in enumerate(LANES):
            launched = write_json(self.base / f"state/startup/{lane}.json", dict(pid=4000 + i, birth=list(birth),
                                                                                surface_uuid=f"surface-{lane}"))
            write_json(self.base / f"state/startup/{lane}-held.json", dict(launched=launched, native_exec=0,
                                                                          surface_uuid=f"surface-{lane}"))

    def release(self, group="pilot-B", live_birth=(7, 8)):
        def run(argv, **kw):
            if "experiment-release" not in argv:
                # boot_id() reads sysctl through the same subprocess module.
                return REAL_RUN(argv, **kw)
            self.calls.append(argv)
            # The clock exists before the private release; release.json does not.
            self.assertTrue((self.base / "live-control/launch-request.json").exists())
            self.assertFalse((self.base / "state/startup/release.json").exists())
            write_json(self.base / "state/startup/release.json", {"synthetic": True})
            return type("R", (), dict(returncode=0, stdout="{}", stderr=""))()
        permit = dict(runtime=self.ref, step=dict(group="pilot-B", ordinal=1))
        with patch("upgrade_schedule.status", return_value=dict(active=self.active)), \
                patch("orchestration_permit.read_permit", return_value=(None, permit)), \
                patch("cmux_daemon_identity.process", return_value=dict(birth=list(live_birth))), \
                patch.object(control_mod.subprocess, "run", side_effect=run):
            return control_mod.release_held(self.ref, {"registration": True}, group,
                                            self.receipt["path"], self.receipt["sha256"])

    def test_hold_writes_held_request_and_never_the_launch_clock(self):
        with patch.object(control_mod, "invocation", return_value=(None, dict(returncode=0))) as spawn:
            result = control_mod.control(self.ref, "hold")
        self.assertEqual(result["status"], "HELD_LAUNCH_RETURNED")
        spawn.assert_called_once()
        self.assertTrue((self.base / "live-control/held-request.json").exists())
        self.assertFalse((self.base / "live-control/launch-request.json").exists())

    def test_hold_refuses_a_runtime_without_the_materialized_hold(self):
        self.held = False
        with patch.object(control_mod, "invocation") as spawn, \
                self.assertRaisesRegex(ContractError, "BATCH_HOLD_REQUIRES_PRE_EXEC_HOLD"):
            control_mod.control(self.ref, "hold")
        spawn.assert_not_called()
        self.assertFalse((self.base / "live-control/held-request.json").exists())

    def test_release_writes_the_launch_clock_then_releases_once(self):
        self.held_spawn()
        result = self.release()
        self.assertEqual(result["status"], "RELEASED")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][-4:], ["--receipt", self.receipt["path"], "--receipt-sha256", self.receipt["sha256"]])
        request = json.loads((self.base / "live-control/launch-request.json").read_text())
        self.assertEqual((request["runtime"], request["held_release"]["group"]), (self.ref, "pilot-B"))
        with self.assertRaisesRegex(ContractError, "HELD_GROUP_ALREADY_RELEASED"):
            self.release()
        self.assertEqual(len(self.calls), 1)

    def test_release_refuses_another_group_no_permit_changed_process_or_no_hold(self):
        cases = dict(other_group=(dict(group="fan_in-A1"), "RELEASE_REQUIRES_THIS_GROUP_ACTIVE_PERMIT"),
                     no_permit=(dict(active=None), "RELEASE_REQUIRES_THIS_GROUP_ACTIVE_PERMIT"),
                     birth=(dict(live_birth=(7, 9)), "HELD_PROCESS_CHANGED"))
        for case, (kw, error) in cases.items():
            with self.subTest(case=case):
                self.setUp()
                self.held_spawn()
                if "active" in kw:
                    self.active = kw.pop("active")
                with self.assertRaisesRegex(ContractError, error):
                    self.release(**kw)
                self.assertFalse((self.base / "live-control/launch-request.json").exists())
                self.assertEqual(self.calls, [])
        self.setUp()
        with self.assertRaisesRegex(ContractError, "BATCH_HELD_LAUNCH_REQUIRED"):
            self.release()
        self.held_spawn()
        self.held = False
        with self.assertRaisesRegex(ContractError, "PRE_EXEC_HOLD_NOT_BOUND"):
            self.release()
        self.assertEqual(self.calls, [])

    def test_held_observe_requires_the_owner_release_record(self):
        self.held_spawn()
        write_json(self.base / "live-control/launch-request.json", dict(runtime=self.ref, startup=self.startup,
                                                                       held_release={"synthetic": True}))
        with self.assertRaisesRegex(ContractError, "HELD_RELEASE_NOT_OBSERVED"):
            control_mod.control(self.ref, "held-observe")
        write_json(self.base / "state/startup/release.json", {"synthetic": True})
        self.assertEqual(control_mod.control(self.ref, "held-observe")["status"], "HELD_RELEASE_OBSERVED")


if __name__ == "__main__":
    unittest.main()
