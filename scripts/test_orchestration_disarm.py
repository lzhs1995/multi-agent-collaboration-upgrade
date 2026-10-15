"""Offline recovery fault injection; no shared controller or peer is invoked."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, utc_now, write_json
from orchestration_disarm import (Recovery, classify, marker_paths, original_result, reservation_key,
                                  runtime_root, snapshot, state_paths, validate_result)
from orchestration_test_support import OfflineDisarmRuntime
from orchestration_transport import OperationJournal, PendingOperation


class Crash(BaseException):
    pass


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.campaign = dict(controller={"offline": True}, sessions={
            "codex": dict(workspace_uuid="WORKSPACE", surface_uuid="SUPERVISOR"),
            "claude": dict(surface_uuid="ABC-EXECUTOR")})
        self.runtime = OfflineDisarmRuntime(self.campaign, self.root / "runtime")
        self.task = "original-task"
        self.frozen = self.runtime.freeze_disarm(self.task)
        self.frozen_ref = write_json(self.root / "frozen.json", self.frozen, exclusive=True)
        self.journal = OperationJournal(self.root / "node", {"sha256": "campaign"},
                                        self.task, self.campaign["controller"])

    def tearDown(self):
        self.temp.cleanup()

    def original(self, outcome="missing"):
        def action(_):
            if outcome == "success":
                return self.runtime.disarm(self.frozen)
            if outcome == "error":
                raise RuntimeError("original refusal")
            raise Crash()
        try:
            self.journal.once("scoped-disarm", {"frozen": self.frozen_ref}, action)
        except (Crash, PendingOperation):
            pass
        intent = file_ref(self.journal.root / "scoped-disarm/intent.json")
        return Recovery(self.root / "recovery", self.frozen_ref, intent,
                        original_result(intent), runtime=self.runtime)

    def reserve(self, caller=None, **changes):
        caller = caller or self.frozen["callers"][0]
        state = dict(caller_surface_uuid=caller, workspace_uuid=self.frozen["workspace_uuid"],
                     supervisor=self.frozen["supervisor_uuid"], state="WAITING_REPLY",
                     paused_tasks=[reservation_key(self.runtime.marker)])
        state.update(changes)
        path = state_paths(self.frozen, caller)[0]
        write_json(path, state)
        return path

    def assert_unchanged_original(self, recovery, original):
        self.assertEqual(original, {str(p): p.read_bytes() for p in
            Path(recovery.intent_ref["path"]).parent.iterdir()})

    def busy(self, frozen):
        value = self.runtime.observe_disarm(frozen)
        value.update(status="BUSY", error="private lock is busy", markers=[], reservations=[])
        return value

    def test_original_success_and_duplicate_recovery_only_observe(self):
        self.reserve()
        recovery = self.original("success")
        result = json_ref(recovery.original_result_ref)["value"]
        validate_result(result, self.frozen)
        self.assertIs(result["observation_atomic_with_action"], False)
        proof = recovery.run()
        self.assertEqual([r["kind"] for r in proof["slots"]], ["observe"])
        self.assertEqual(recovery.run(), proof)
        self.assertEqual(recovery.verify(), proof)
        self.assertEqual(self.runtime.disarms, [self.task])
        self.assertEqual(self.runtime.recoveries, [])

    def test_original_error_is_preserved_during_one_recovery_action(self):
        self.reserve()
        recovery = self.original("error")
        original = {str(p): p.read_bytes() for p in Path(recovery.intent_ref["path"]).parent.iterdir()}
        proof = recovery.run()
        self.assertEqual([r["kind"] for r in proof["slots"]], ["observe", "act", "observe"])
        self.assertEqual(self.runtime.recoveries, ["DISARM"])
        self.assertEqual(recovery.run(), proof)
        self.assert_unchanged_original(recovery, original)

    def test_absent_original_result_stays_absent(self):
        recovery = self.original()
        proof = recovery.run()
        self.assertIsNone(proof["original_result"])
        self.assertFalse((Path(recovery.intent_ref["path"]).parent / "result.json").exists())
        self.assertEqual(self.runtime.recoveries, ["DISARM"])

    def test_marker_absent_with_held_reservation_requires_release_action(self):
        path = self.reserve()
        marker_paths(self.frozen)[0].unlink()
        self.assertEqual(classify(self.runtime.observe_disarm(self.frozen), self.frozen), "LEAKED_RESERVATION")
        recovery = self.original()
        recovery.run()
        self.assertEqual(self.runtime.recoveries, ["RELEASE"])
        self.assertEqual(json_ref(file_ref(path))["paused_tasks"], [])

    def test_all_original_callers_and_case_sensitive_state_hashes_are_required(self):
        other = "abc-executor"
        self.runtime.marker["participants"].append(dict(role="executor", surface_uuid=other))
        write_json(marker_paths(self.frozen)[0], self.runtime.marker)
        self.frozen = self.runtime.freeze_disarm(self.task)
        self.frozen_ref = write_json(self.root / "frozen.json", self.frozen)
        paths = [self.reserve(caller) for caller in self.frozen["callers"]]
        self.assertEqual(len(set(paths)), 2)
        observation = self.runtime.observe_disarm(self.frozen)
        self.assertEqual([row["caller"] for row in observation["reservations"]], self.frozen["callers"])
        bad = copy.deepcopy(observation)
        bad["reservations"].pop()
        self.assertEqual(classify(bad, self.frozen), "UNPROVEN_UNREADABLE")
        self.original().run()
        self.assertTrue(all(json_ref(file_ref(path))["paused_tasks"] == [] for path in paths))

    def test_controller_stop_or_revocation_refusal_cannot_release_lease(self):
        # These are simulated controller refusals, not a claim of live hook or
        # shared-runtime coverage. The preview deliberately retains the hold.
        for reason in ("STOPPED", "AUTHORIZATION_REVOKED"):
            with self.subTest(reason=reason):
                state_path = self.reserve()
                stop_path = state_paths(self.frozen, self.frozen["callers"][0])[1]
                write_json(stop_path, {"reason": reason})
                def refuse(state, marker, boundary, now, *, publish=False):
                    state["state"] = reason
                    return True
                with patch.object(self.runtime.lifecycle, "apply_boundary", side_effect=refuse):
                    observation = self.runtime.observe_disarm(self.frozen)
                    self.assertEqual(classify(observation, self.frozen), "UNPROVEN_RESERVATION")
                    with self.assertRaisesRegex(ContractError, "UNPROVEN_RESERVATION"):
                        self.runtime.disarm(self.frozen)
                self.assertTrue(marker_paths(self.frozen)[0].exists())
                self.assertEqual(json_ref(file_ref(state_path))["paused_tasks"], [reservation_key(self.runtime.marker)])
                stop_path.unlink()

    def test_wrong_owner_ineligible_state_and_malformed_state_fail_closed(self):
        for changes in ({"supervisor": "OTHER"}, {"state": "STOPPED"}, {"paused_tasks": "bad"}):
            with self.subTest(changes=changes):
                self.reserve(**changes)
                status = classify(self.runtime.observe_disarm(self.frozen), self.frozen)
                self.assertIn(status, ("UNPROVEN_RESERVATION", "UNPROVEN_UNREADABLE"))
                with self.assertRaises(ContractError):
                    self.runtime.disarm(self.frozen)
                self.assertTrue(marker_paths(self.frozen)[0].exists())

    def test_changed_or_relocated_marker_is_not_the_original(self):
        path = marker_paths(self.frozen)[0]
        write_json(path, dict(self.runtime.marker, collaboration_id="another-id"))
        self.assertEqual(classify(self.runtime.observe_disarm(self.frozen), self.frozen), "UNPROVEN_CHANGED")
        write_json(path, self.runtime.marker)
        path.rename(path.with_name("another-path.json"))
        self.assertEqual(classify(self.runtime.observe_disarm(self.frozen), self.frozen), "UNPROVEN_CHANGED")

    def test_last_activity_refresh_is_allowed_but_missing_legacy_snapshot_is_not(self):
        write_json(marker_paths(self.frozen)[0], dict(self.runtime.marker, last_activity_at=utc_now()))
        observation = self.runtime.observe_disarm(self.frozen)
        self.assertEqual(classify(observation, self.frozen), "ORIGINAL_MARKER_PRESENT")
        observation["markers"] = [r for r in observation["markers"] if r["path"] != str(marker_paths(self.frozen)[1])]
        with self.assertRaisesRegex(ContractError, "observations missing"):
            classify(observation, self.frozen)

    def test_crashed_action_has_mandatory_observation_and_no_second_action(self):
        recovery = self.original()
        recovery.slot("observe")
        action = self.runtime.recover_disarm
        def crash_after(frozen, kind):
            action(frozen, kind)
            raise Crash()
        with patch.object(self.runtime, "recover_disarm", side_effect=crash_after), self.assertRaises(Crash):
            recovery.slot("act", "DISARM")
        self.assertFalse((recovery.root / "0002-act/result.json").exists())
        proof = recovery.run()
        self.assertEqual([r["kind"] for r in proof["slots"]], ["observe", "act", "observe"])
        self.assertIsNone(proof["slots"][1]["result"])
        self.assertEqual(self.runtime.recoveries, ["DISARM"])

    def test_crashed_action_before_effect_cannot_be_retried(self):
        recovery = self.original()
        recovery.slot("observe")
        with patch.object(self.runtime, "recover_disarm", side_effect=Crash()) as action:
            with self.assertRaises(Crash):
                recovery.slot("act", "DISARM")
            for _ in range(2):
                with self.assertRaisesRegex(ContractError, "ORIGINAL_MARKER_PRESENT"):
                    recovery.run()
            action.assert_called_once()
        self.assertEqual([r["kind"] for r in recovery.history()], ["observe", "act", "observe"])

    def test_error_after_action_still_requires_an_independent_observation(self):
        recovery = self.original()
        action = self.runtime.recover_disarm
        def error_after(frozen, kind):
            action(frozen, kind)
            raise RuntimeError("return lost")
        with patch.object(self.runtime, "recover_disarm", side_effect=error_after):
            proof = recovery.run()
        self.assertEqual(json_ref(proof["slots"][1]["result"])["status"], "ERROR")
        self.assertEqual(proof["slots"][-1]["kind"], "observe")
        self.assertEqual(self.runtime.recoveries, ["DISARM"])

    def test_directory_created_before_intent_permanently_consumes_slot(self):
        recovery = self.original()
        with patch("orchestration_disarm.write_json", side_effect=Crash()), self.assertRaises(Crash):
            recovery.slot("observe")
        self.assertEqual(list((recovery.root / "0001-observe").iterdir()), [])
        with self.assertRaisesRegex(ContractError, "slot cannot be recycled"):
            recovery.run()
        self.assertEqual(self.runtime.recoveries, [])

    def test_one_busy_observation_allows_exactly_four_slots(self):
        recovery = self.original()
        busy = self.busy(self.frozen)
        with patch.object(self.runtime, "observe_disarm", return_value=busy):
            # Timestamp belongs to the actual observation, not before intent.
            with patch("orchestration_disarm.utc_now", return_value=busy["observed_at"]):
                recovery.slot("observe")
        proof = recovery.run()
        self.assertEqual([r["kind"] for r in proof["slots"]], ["observe", "observe", "act", "observe"])
        self.assertEqual(self.runtime.recoveries, ["DISARM"])

    def test_second_busy_and_failed_observation_do_not_authorize_actions(self):
        recovery = self.original()
        observe = self.runtime.observe_disarm
        def busy(frozen):
            value = observe(frozen)
            return dict(value, status="BUSY", error="busy", markers=[], reservations=[])
        with patch.object(self.runtime, "observe_disarm", side_effect=busy):
            with self.assertRaisesRegex(ContractError, "UNPROVEN_BUSY"):
                recovery.run()
        self.assertEqual(len(recovery.history()), 2)
        self.assertEqual(self.runtime.recoveries, [])

    def test_missing_observation_result_is_terminal_not_a_fresh_budget(self):
        recovery = self.original()
        with patch.object(self.runtime, "observe_disarm", side_effect=Crash()), self.assertRaises(Crash):
            recovery.slot("observe")
        with self.assertRaisesRegex(ContractError, "UNPROVEN_OBSERVATION"):
            recovery.run()
        self.assertEqual(len(recovery.history()), 1)

    def test_original_absence_cannot_be_replaced_by_a_late_result(self):
        recovery = self.original()
        write_json(Path(recovery.intent_ref["path"]).parent / "result.json",
                   dict(intent=recovery.intent_ref, status="ERROR", error_type="Error", error="late",
                        completed_at=utc_now()), exclusive=True)
        with self.assertRaisesRegex(ContractError, "original absence changed"):
            recovery.run()
        self.assertEqual(self.runtime.recoveries, [])

    def test_changed_pins_and_backdated_slot_are_rejected(self):
        recovery = self.original()
        recovery.slot("observe")
        ref = file_ref(recovery.root / "0001-observe/intent.json")
        intent = json_ref(ref)
        for changed in (dict(intent, created_at="2000-01-01T00:00:00Z"),
                        dict(intent, original_result={"path": "/unrelated"}), dict(intent, action="DISARM")):
            with self.subTest(changed=changed):
                write_json(ref["path"], changed)
                with self.assertRaises(ContractError):
                    recovery.run()
                self.assertEqual(self.runtime.recoveries, [])
        write_json(ref["path"], intent)

    def test_wrong_transition_extra_slot_and_excess_slots_are_rejected(self):
        recovery = self.original("success")
        recovery.run()
        (recovery.root / "0002-act").mkdir()
        with self.assertRaisesRegex(ContractError, "wrong transition"):
            recovery.run()
        for number in range(3, 6):
            (recovery.root / f"{number:04d}-observe").mkdir()
        with self.assertRaisesRegex(ContractError, "excess"):
            recovery.run()
        self.assertEqual(self.runtime.recoveries, [])

    def test_tmp_prefix_is_trusted_but_child_alias_is_rejected(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as name:
            actual = Path(name).resolve()
            prefix = Path("/tmp") / actual.name
            self.assertEqual(runtime_root(prefix, platform_tmp=True), actual)
            (actual / "child").mkdir()
            (actual / "alias").symlink_to(actual / "child", target_is_directory=True)
            with self.assertRaisesRegex(ContractError, "symlink aliases"):
                runtime_root(prefix / "alias", platform_tmp=True)
            self.assertEqual(snapshot(actual / "alias")["status"], "UNREADABLE")


if __name__ == "__main__":
    unittest.main()
