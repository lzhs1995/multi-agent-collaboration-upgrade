from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

import availability_contract as a


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = a.initial("task", str(uuid.uuid4()), str(uuid.uuid4()), "surface:2",
                               authorization_source="user_message", authorization_record=self.ref("auth", {"user_message": "Automatically continue solo on executor failure."}),
                               fallback_policy="automatic_user_authorized", protected_paths=[str(self.root / "protected")])

    def ref(self, name, value):
        p = self.root / (name + ".json")
        p.write_text(json.dumps(value))
        return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

    def outage(self):
        return a.transition(self.state, "UNAVAILABLE_AUTH", failure_receipt=self.ref("failure", {"http_status": 401}))

    def manifest(self, active_writers=None):
        return self.ref("takeover", {"task_id": "task", "executor_uuid": self.state["executor_uuid"],
            "checkpoint": self.ref("checkpoint", {"phase": "review"}),
            "freeze_evidence": self.ref("freeze", {"executor_frozen": True, "sentinel_stopped": True, "active_writers": active_writers or []}),
            "active_nonces": ["nonce"], "protected_paths": self.state["protected_paths"], "resume_phase": "review"})

    def test_authorized_dual_solo_dual(self):
        solo = a.transition(self.outage(), "SOLO_TAKEOVER", takeover_manifest=self.manifest())
        self.assertEqual(solo["review_provenance"], "solo_self_review")
        handoff = self.ref("handoff", {"task_id": "task", "phase_boundary": True, "active_writers": []})
        ready = a.transition(solo, "HANDOFF_READY", handoff=handoff)
        ack = self.ref("ack", {"task_id": "task", "status": "PASS", "executor_ack": True, "updated_at": datetime.now(timezone.utc).isoformat()})
        identity = self.ref("identity", {"status": "PASS", "workspace_uuid": ready["workspace_uuid"], "executor_surface_uuid": ready["executor_uuid"]})
        active = a.transition(ready, "ACTIVE", handshake=ack, identity=identity)
        self.assertTrue(active["executor_dispatch_allowed"])

    def test_free_text_and_unfrozen_writer_do_not_authorize_takeover(self):
        state = self.outage()
        state["authorization_source"] = "peer_agent"
        with self.assertRaisesRegex(a.AvailabilityError, "AUTHORIZATION"):
            a.transition(state, "SOLO_TAKEOVER", reason="the user said yes", takeover_manifest=self.manifest())
        with self.assertRaisesRegex(a.AvailabilityError, "NOT_DRAINED"):
            a.transition(self.outage(), "SOLO_TAKEOVER", takeover_manifest=self.manifest(["writer"]))

    def test_renumber_is_not_uuid_replacement(self):
        with self.assertRaisesRegex(a.AvailabilityError, "CONTINUITY"):
            a.transition(self.outage(), "SOLO_TAKEOVER", executor_surface="surface:99", takeover_manifest=self.manifest())

    def test_expired_state_and_late_callback_rejected(self):
        path = self.root / "state.json"
        state = self.outage()
        a.save(path, state)
        for action in ("dispatch", "callback", "round_record", "sentinel"):
            with self.subTest(action=action), self.assertRaises(a.AvailabilityError):
                a.require_action("task", action, {"availability_state": str(path)})
        self.state["expires_at"] = time.time() - 1
        a.save(path, self.state)
        with self.assertRaisesRegex(a.AvailabilityError, "EXPIRED"):
            a.require_action("task", "dispatch", {"availability_state": str(path)})

    def test_failure_classification_and_retry_budget(self):
        for code in (401, 403, 429):
            self.assertFalse(a.classify_failure({"http_status": code})["retry"])
        payload = {"http_status": 502, "retryable": True}
        self.assertTrue(a.classify_failure(payload, attempts=2, now=160, last_attempt_at=100)["retry"])
        self.assertFalse(a.classify_failure(payload, attempts=2, now=159, last_attempt_at=100)["retry"])
        self.assertTrue(a.classify_failure(payload, attempts=3)["takeover"])
        for payload in ({"submission_state": "DELIVERY_QUEUED_AT_RECEIVER", "http_status": 401}, {"budget_below_phase_minimum": True}):
            self.assertFalse(a.classify_failure(payload)["takeover"])

    def test_billing_status_is_independent_of_provider_message_language(self):
        for message in ("余额不足", "", "Compte épuisé"):
            with self.subTest(message=message):
                result = a.classify_failure({"http_status": 402, "message": message})
                self.assertEqual(result["category"], "UNAVAILABLE_BILLING")
                self.assertFalse(result["retry"])
        queued = a.classify_failure({"http_status": 402, "message": "余额不足",
                                    "submission_state": "DELIVERY_QUEUED_AT_RECEIVER"})
        self.assertEqual(queued["category"], "DELIVERY_QUEUED_AT_RECEIVER")
        self.assertFalse(queued["takeover"])

    def test_retry_advice_respects_longer_provider_delay_at_boundary(self):
        payload = {"http_status": 503, "retryable": True, "retry_after_seconds": 300}
        early = a.classify_failure(payload, attempts=1, now=160, last_attempt_at=100)
        self.assertFalse(early["retry"])
        self.assertEqual(early["retry_after_seconds"], 240)
        due = a.classify_failure(payload, attempts=1, now=400, last_attempt_at=100)
        self.assertTrue(due["retry"])
        self.assertEqual(due["retry_after_seconds"], 0)
        exhausted = a.classify_failure(payload, attempts=3, now=400, last_attempt_at=100)
        self.assertFalse(exhausted["retry"])
        short = a.classify_failure(dict(payload, retry_after_seconds=5),
                                   attempts=1, now=159, last_attempt_at=100)
        self.assertFalse(short["retry"])
        self.assertEqual(short["retry_after_seconds"], 1)

    def test_invalid_retry_delay_never_falls_back_to_a_shorter_default(self):
        for delay in (-1, True, "300", float("nan"), float("inf")):
            with self.subTest(delay=delay), self.assertRaisesRegex(a.AvailabilityError, "INVALID_RETRY_DELAY"):
                a.classify_failure({"http_status": 503, "retryable": True,
                                    "retry_after_seconds": delay},
                                   attempts=1, now=160, last_attempt_at=100)

    def test_explicit_retry_delay_without_original_attempt_time_is_not_immediately_eligible(self):
        with self.assertRaisesRegex(a.AvailabilityError, "ORIGINAL_ATTEMPT_TIME"):
            a.classify_failure({"http_status": 503, "retryable": True,
                                "retry_after_seconds": 300}, attempts=1, now=160)

    def test_entry_points_block_before_delivery(self):
        import cmux_bridge
        state_file = self.root / "state.json"
        a.save(state_file, self.outage())
        pack = {"task_id": "task", "availability_state": str(state_file), "report": "unused"}
        with patch.object(cmux_bridge, "validate_task_pack_contract", return_value=pack), patch.object(cmux_bridge, "submit_text") as send:
            with self.assertRaises(a.AvailabilityError):
                cmux_bridge.submit_task_pack("surface:2", "unused", "pack")
            with self.assertRaises(a.AvailabilityError):
                cmux_bridge.submit_completion_callback("pack")
            send.assert_not_called()

    def test_registry_and_direct_submit_cannot_bypass_artifact_state(self):
        import cmux_bridge
        state_file = self.root / "executor-availability.json"
        a.save(state_file, self.outage())
        pack = {"task_id": "task", "availability_state": str(state_file), "availability_required": True}
        (self.root / "task-pack.json").write_text(json.dumps(pack))
        registry = self.root / "registry"
        registry.mkdir()
        (registry / "task.json").write_text(json.dumps({"artifact_root": str(self.root)}))
        with patch.dict(os.environ, {"MULTI_AGENT_REGISTRY_ROOT": str(registry), "MULTI_AGENT_AVAILABILITY_ROOT": str(self.root / "empty-default")}):
            with self.assertRaises(a.AvailabilityError):
                a.require_action("task", "sentinel")
        with patch.object(cmux_bridge, "validate_task_pack_contract", return_value=pack), patch.object(cmux_bridge, "read_screen") as read:
            with self.assertRaises(a.AvailabilityError):
                cmux_bridge.submit_text("surface:2", "task", task_pack_path="pack")
            read.assert_not_called()

    def test_reobserve_keeps_solo_and_accepts_same_uuid_renumber(self):
        solo = a.transition(self.outage(), "SOLO_TAKEOVER", takeover_manifest=self.manifest())
        solo["expires_at"] = time.time() - 10
        identity = self.ref("fresh", {"task_id": "task", "status": "PASS", "workspace_uuid": solo["workspace_uuid"],
            "executor_surface_uuid": solo["executor_uuid"], "executor_surface": "surface:99", "observed_at": time.time()})
        refreshed = a.reobserve(solo, identity)
        self.assertEqual(refreshed["state"], "SOLO_TAKEOVER")
        self.assertEqual(refreshed["executor_surface"], "surface:99")
        self.assertFalse(refreshed["executor_dispatch_allowed"])


if __name__ == "__main__":
    unittest.main()
