"""Native time observations cannot settle, send, accept or manufacture clocks."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, parse_time, utc_now, write_json
from orchestration_evidence import task_identity
from orchestration_observe import _advice
from orchestration_timeline import boundary, wall_interval
from test_orchestration_observe import ReadOnlyObservationFixture


class TimelineFixture(ReadOnlyObservationFixture):
    def begin(self, co, key="review"):
        info = co.claim(key)
        self.f.prepare(co, key)
        self.f.intent(co, key)
        self.f.dispatch(co, key)
        return info

    def timeline(self, key="review"):
        return self.row(self.observe(), key)["timeline"]

    def completed_callback(self, co, key="review"):
        info = self.begin(co, key)
        self.f.work(info, co.nodes[key]["executor"])
        return info, self.f.callback(co, key)


class NativeTimelineTests(TimelineFixture):
    def test_prepared_ack_has_original_native_refs_and_no_task_receipt(self):
        co = self.f.start()
        co.claim("review")
        proof = self.f.prepare(co, "review")
        timeline = self.timeline()
        points = timeline["boundaries"]
        received = json_ref(proof["handshake"]["delivery"]["receipt"])["native_proof"]
        self.assertEqual(points["handshake_received"]["evidence"], [received])
        self.assertEqual(points["acknowledgement"]["evidence"], [proof["handshake"]["ack_native"]])
        self.assertEqual(points["pack_finalized"]["evidence"], [proof["pack"]])
        self.assertIsNone(points["task_received"])
        self.assertIsNone(points["body_read"])
        self.assertIsNone(timeline["native_actions"])

    def test_callback_before_candidate_has_exact_reception_but_no_freeze_or_read(self):
        co = self.f.start()
        info, protocol = self.completed_callback(co)
        points = self.timeline()["boundaries"]
        for key, bundle in (("task_received", protocol["delivery"]), ("callback_received", protocol["callback"])):
            self.assertEqual(points[key]["evidence"], [json_ref(bundle["receipt"])["native_proof"]])
        self.assertEqual(points["callback_input_intent"]["kind"], "native_input_intent")
        self.assertEqual(points["report_hash_bound"]["kind"], "callback_binding_observed")
        self.assertEqual(points["report_hash_bound"]["evidence"][1]["path"], str(Path(info["output_root"]) / "report.md"))
        for key in ("body_read", "report_frozen", "report_discovered", "report_read", "candidate_ready",
                    "acceptance_native", "accepted_recorded", "lease_released", "cleanup_complete"):
            self.assertIsNone(points[key], key)
        self.assertEqual(co.state()[0]["review"].value, "DISPATCHED")
        for interval in self.timeline()["intervals"].values():
            self.assertIsNone(interval["monotonic_seconds"])

    def test_unsettled_native_callback_is_not_a_receipt_and_never_settles(self):
        co = self.f.start()
        _, protocol = self.completed_callback(co)
        Path(protocol["callback"]["receipt"]["path"]).unlink()
        timeline = self.timeline()
        self.assertIsNotNone(timeline["boundaries"]["task_received"])
        self.assertIsNone(timeline["boundaries"]["callback_received"])
        self.assertIsNone(timeline["boundaries"]["report_hash_bound"])
        self.assertIn("callback_received", timeline["missing_boundaries"])
        self.assertFalse((co.root / "nodes/review/operations").exists())

    def test_pending_and_failed_results_do_not_prove_successful_work(self):
        co = self.f.start()
        info = co.claim("local")
        client = self.f.clients["codex"]

        def call(call_id, output):
            ref = client.append(dict(type="response_item", timestamp=utc_now(), payload=dict(
                type="custom_tool_call", name="exec", call_id=call_id, input=info["output_root"] + "/result.json")))
            result = client.append(dict(type="response_item", timestamp=utc_now(), payload=dict(
                type="custom_tool_call_output", call_id=call_id, output=output)))
            return ref, result

        _, pending = call("pending", {"status": "running", "session_id": 12})
        first = self.timeline("local")
        self.assertEqual(first["native_actions"]["pending_task_calls"], 1)
        self.assertEqual(first["native_actions"]["actions"][0]["pending_results"], [pending])
        self.assertIsNone(first["boundaries"]["first_successful_action"])
        self.assertIsNone(first["boundaries"]["last_completed_action"])
        _, failed = call("failed", {"exit_code": 2})
        second = self.timeline("local")
        self.assertIsNone(second["boundaries"]["first_successful_action"])
        self.assertEqual(second["boundaries"]["last_completed_action"]["evidence"], [failed])
        successful, result = call("success", {"exit_code": 0})
        third = self.timeline("local")
        self.assertEqual(third["boundaries"]["first_successful_action"]["evidence"], [successful, result])
        self.assertEqual(third["native_actions"]["completed_task_calls"], 1)
        self.assertEqual(third["native_actions"]["pending_task_calls"], 1)

    def test_candidate_acceptance_release_and_disarm_are_separate_boundaries(self):
        co = self.f.start()
        info, protocol = self.completed_callback(co)
        candidate = self.f.candidate(co, "review", protocol)
        points = self.timeline()["boundaries"]
        self.assertIsNotNone(points["candidate_ready"])
        self.assertIsNone(points["acceptance_native"])
        self.assertIsNone(points["lease_released"])
        self.f.accept(co, "review", candidate)
        points = self.timeline()["boundaries"]
        for key in ("acceptance_native", "accepted_recorded", "lease_released"):
            self.assertIsNotNone(points[key], key)
        self.assertIsNone(points["disarmed"])
        self.assertIsNone(points["cleanup_complete"])
        self.f.disarm(co, "review")
        timeline = self.timeline()
        points = timeline["boundaries"]
        self.assertIsNotNone(points["disarmed"])
        self.assertGreaterEqual(parse_time(points["cleanup_complete"]["at"]), parse_time(points["lease_released"]["at"]))
        # Later work in the reused original session cannot extend this task.
        client = self.f.clients["claude"]
        client.append(client.message("assistant", "later turn", at=utc_now()))
        self.assertEqual(self.timeline(), timeline)

    def test_changed_report_or_callback_binding_rejects_observation(self):
        co = self.f.start()
        info, protocol = self.completed_callback(co)
        report = Path(info["output_root"]) / "report.md"
        original = report.read_bytes()
        report.write_text("changed report\n")
        with self.assertRaisesRegex(ContractError, "different report/pack/nonce"):
            self.observe()
        report.write_bytes(original)
        attempt = json_ref(protocol["callback"]["attempts"][0])
        attempt["binding"]["completion_nonce"] = "different-nonce"
        write_json(protocol["callback"]["attempts"][0]["path"], attempt)
        with self.assertRaisesRegex(ContractError, "binding changed"):
            self.observe()

    def test_callback_publication_during_read_invalidates_stale_advice(self):
        co = self.f.start()
        info = self.begin(co)
        self.f.work(info, "claude")
        events = co.events()

        def publish(*args):
            result = _advice(*args)
            if args[1] == "review":
                self.f.callback(co, "review")
            return result

        with patch("orchestration_observe._advice", side_effect=publish), \
             self.assertRaisesRegex(ContractError, "control evidence changed during observation"):
            self.r.next_action(self.run)
        self.assertEqual(co.events(), events)

    def test_continuation_publication_during_read_invalidates_stale_advice(self):
        co = self.f.start()
        self.completed_callback(co)

        def publish(*args):
            result = _advice(*args)
            if args[1] == "review":
                write_json(co.root / "nodes/review/continuation.json", task_identity(co.campaign, co.header, co.nodes["review"]))
            return result

        with patch("orchestration_observe._advice", side_effect=publish), \
             self.assertRaisesRegex(ContractError, "control evidence changed during observation"):
            self.r.next_action(self.run)


class ThreeLaneTimelineTests(TimelineFixture):
    campaign_version = 3

    def test_each_claude_has_its_own_receipt_and_callback_identity(self):
        co = self.f.start()
        for key in ("review", "parallel"):
            info, protocol = self.completed_callback(co, key)
            points = self.timeline(key)["boundaries"]
            lane = co.nodes[key]["executor"]
            self.assertEqual(points["task_received"]["evidence"][0]["session_id"], self.f.clients[lane].session["session_id"])
            self.assertEqual(points["callback_received"]["evidence"][0]["session_id"], self.f.clients["codex"].session["session_id"])
        attempt = copy.deepcopy(self.f.clients["codex"].attempt)
        attempt["native_binding"]["identity"]["caller_surface_uuid"] = self.f.clients["claude_a"].session["surface_uuid"]
        write_json(protocol["callback"]["attempts"][0]["path"], attempt)
        with self.assertRaises(ContractError):
            self.observe()


class WallObservationTests(unittest.TestCase):
    def test_wall_timestamps_never_manufacture_a_monotonic_duration(self):
        start = boundary("2026-10-17T00:00:02Z", "native")
        end = boundary("2026-10-17T00:00:05Z", "native")
        result = wall_interval(start, end)
        self.assertEqual(result["recorded_wall_seconds"], 3)
        self.assertIsNone(result["monotonic_seconds"])
        self.assertEqual(wall_interval(start, None)["clock_status"], "missing_endpoint")
        backwards = wall_interval(end, start)
        self.assertIsNone(backwards["recorded_wall_seconds"])
        self.assertIsNone(backwards["monotonic_seconds"])
        self.assertEqual(backwards["clock_status"], "wall_clock_discontinuity_unmeasured")


if __name__ == "__main__":
    unittest.main()
