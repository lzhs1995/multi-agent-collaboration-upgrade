"""Synthetic journals exercise native continuity semantics, never live claims."""
from datetime import datetime, timezone
from pathlib import Path
import copy
import json
import tempfile
import unittest
import uuid
import sys
import subprocess
import time

from orchestration_common import ContractError, canonical, digest, file_ref, json_ref, tree_manifest, write_json
from test_orchestration_native import NativeFixture
from test_upgrade_live_startup import MODERN_SOURCE
from upgrade_continuity_native import collect_native_outcomes, invoke_original_plan

LANES = ("codex", "claude_a", "claude_b")
TASK = "continuity-original-task"


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class NativeContinuityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.journals, self.sessions = {}, {}
        workspace = str(uuid.uuid4())
        for lane in LANES:
            path = self.root / lane
            path.mkdir()
            journal = NativeFixture(path, "codex" if lane == "codex" else "claude",
                                    session_id=str(uuid.uuid4()))
            journal.session.update(workspace_uuid=workspace, surface_uuid=str(uuid.uuid4()),
                                   pane_uuid=str(uuid.uuid4()))
            journal.session["freeze_fence"] = journal.reader.snapshot()
            self.journals[lane], self.sessions[lane] = journal, journal.session

    def claude_success(self, lane, epoch):
        journal = self.journals[lane]
        user_id, message_id = str(uuid.uuid4()), "msg_" + uuid.uuid4().hex
        user = journal.message("user", "Continue the same authorized task.", at=stamp(epoch))
        user.update(uuid=user_id, parentUuid=None, isSidechain=False)
        journal.append(user)
        answer = journal.message("assistant", "Original task checkpoint.", at=stamp(epoch + 1))
        answer.update(uuid=str(uuid.uuid4()), parentUuid=user_id, isSidechain=False)
        answer["message"].update(id=message_id, type="message", stop_reason="end_turn",
                                  model=journal.session["model"])
        return journal.append(answer)

    def codex_success(self, epoch):
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(epoch),
                            payload=dict(type="task_started", turn_id=turn)))
        journal.append(dict(type="turn_context", timestamp=stamp(epoch),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        final = journal.message("assistant", "Original task checkpoint.", at=stamp(epoch + 1))
        final["payload"]["phase"] = "final_answer"
        journal.append(final)
        return journal.append(dict(type="event_msg", timestamp=stamp(epoch + 2),
            payload=dict(type="task_complete", turn_id=turn, last_agent_message="Original task checkpoint.")))

    def codex_error(self, epoch, status):
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(epoch),
                            payload=dict(type="task_started", turn_id=turn)))
        journal.append(dict(type="turn_context", timestamp=stamp(epoch),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        for retry in range(4):
            journal.append(dict(type="event_msg", timestamp=stamp(epoch + 1), payload=dict(
                type="error", turn_id=turn, message="Internal provider retry.", retryAttempt=retry)))
        return journal.append(dict(type="event_msg", timestamp=stamp(epoch + 2), payload=dict(
            type="task_complete", turn_id=turn, error=dict(message="Original native API failure.",
                codex_error_info=dict(kind="http_connection_failed", http_status_code=status)))))

    def claude_error(self, lane, epoch, status):
        journal = self.journals[lane]
        parent = str(uuid.uuid4())
        user = journal.message("user", "Continue the same authorized task.", at=stamp(epoch))
        user.update(uuid=parent, parentUuid=None, isSidechain=False)
        journal.append(user)
        for retry in range(1, 8):
            node = str(uuid.uuid4())
            journal.append(dict(type="system", subtype="api_error", sessionId=journal.session["session_id"],
                uuid=node, parentUuid=parent, timestamp=stamp(epoch + 1), isSidechain=False,
                retryAttempt=retry, maxRetries=10, retryInMs=21000,
                error=dict(status=status, message="Internal provider retry.")))
            parent = node
        final = journal.message("assistant", "API Error: original response failed.", at=stamp(epoch + 2))
        final.update(uuid=str(uuid.uuid4()), parentUuid=parent, isSidechain=False,
                     isApiErrorMessage=True, error="server_error", apiErrorStatus=status)
        final["message"].update(id=str(uuid.uuid4()), type="message", model="<synthetic>",
                                stop_reason="stop_sequence")
        return journal.append(final)

    def test_three_original_complete_successes_keep_native_record_and_attempt_identity(self):
        expected = {"codex": self.codex_success(10)}
        expected.update({lane: self.claude_success(lane, 10) for lane in LANES[1:]})
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(value["task_id"], TASK)
        self.assertEqual(value["input_operations"], 0)
        self.assertEqual(len(value["observations"]), 3)
        by_surface = {item["surface_uuid"]: item for item in value["observations"]}
        for lane in LANES:
            result = by_surface[self.sessions[lane]["surface_uuid"]]
            self.assertEqual(result["kind"], "API_SUCCESS")
            self.assertIs(result["terminal"], True)
            self.assertEqual(result["session_id"], self.sessions[lane]["session_id"])
            self.assertEqual(result["native_record"], expected[lane])
            self.assertEqual(result["task_id"], TASK)
            self.assertTrue(result["attempt_id"])

    def test_original_terminal_errors_count_one_attempt_each_not_internal_retries(self):
        expected = {"codex": self.codex_error(10, 401)}
        expected.update({lane: self.claude_error(lane, 10, 502) for lane in LANES[1:]})
        result = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(len(result["observations"]), 3)
        by_surface = {item["surface_uuid"]: item for item in result["observations"]}
        for lane in LANES:
            event = by_surface[self.sessions[lane]["surface_uuid"]]
            self.assertEqual(event["kind"], "API_ERROR")
            self.assertIs(event["terminal"], True)
            self.assertEqual(event["http_status"], 401 if lane == "codex" else 502)
            self.assertEqual(event["native_record"], expected[lane])

    def test_codex_native_externally_tagged_http_failure_preserves_original_402(self):
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(10),
                            payload=dict(type="task_started", turn_id=turn)))
        journal.append(dict(type="turn_context", timestamp=stamp(10),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        terminal = journal.append(dict(type="event_msg", timestamp=stamp(12), payload=dict(
            type="task_complete", turn_id=turn, last_agent_message=None,
            error=dict(message="Original budget pool quota failure.",
                       codex_error_info=dict(http_connection_failed=dict(http_status_code=402))))))
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(len(value["observations"]), 1)
        event = value["observations"][0]
        self.assertEqual(event["kind"], "API_ERROR")
        self.assertEqual(event["http_status"], 402)
        self.assertEqual(event["native_record"], terminal)
        self.assertEqual(event["attempt_id"], "codex-turn:" + turn)
        self.assertEqual(value["input_operations"], 0)

    def test_codex_unknown_tagged_failure_never_infers_status_from_message_or_other_data(self):
        journal = self.journals["codex"]
        unknown = (
            {"http_connection_failed": {"http_status_code": "402"}},
            {"http_connection_failed": {"http_status_code": True}},
            {"http_connection_failed": {"http_status_code": 200}},
            {"http_connection_failed": {"http_status_code": 600}},
            {"http_connection_failed": {"http_status_code": 402, "extra": "unknown"}},
            {"http_connection_failed": {"http_status_code": 402}, "other_error": {}},
            {"other_error": {"http_status_code": 402}},
            {"http_connection_failed": "402"},
        )
        expected = []
        for index, info in enumerate(unknown):
            turn, epoch = str(uuid.uuid4()), 10 + index * 3
            journal.append(dict(type="event_msg", timestamp=stamp(epoch),
                                payload=dict(type="task_started", turn_id=turn)))
            journal.append(dict(type="turn_context", timestamp=stamp(epoch),
                                payload=dict(turn_id=turn, model=journal.session["model"])))
            expected.append(journal.append(dict(type="event_msg", timestamp=stamp(epoch + 2),
                payload=dict(type="task_complete", turn_id=turn,
                    error=dict(message="Quoted unexpected status 402 Payment Required.",
                               codex_error_info=info)))))
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)
        self.assertEqual(len(value["observations"]), len(unknown))
        for event, ref in zip(value["observations"], expected):
            self.assertEqual(event["kind"], "NATIVE_OUTCOME_UNKNOWN")
            self.assertNotIn("http_status", event)
            self.assertEqual(event["native_record"], ref)
        self.assertEqual(value["input_operations"], 0)

    def test_unknown_terminal_failure_is_retained_without_guessing_status_from_text(self):
        refs = {"codex": self.codex_error(10, "401"),
                "claude_a": self.claude_error("claude_a", 10, None)}
        result = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(len(result["observations"]), 2)
        by_surface = {item["surface_uuid"]: item for item in result["observations"]}
        for lane, ref in refs.items():
            event = by_surface[self.sessions[lane]["surface_uuid"]]
            self.assertEqual(event["kind"], "NATIVE_OUTCOME_UNKNOWN")
            self.assertIs(event["terminal"], True)
            self.assertNotIn("http_status", event)
            self.assertEqual(event["native_record"], ref)

    def test_claude_tool_use_success_follows_real_parent_chain_and_counts_message_chunks_once(self):
        journal = self.journals["claude_a"]
        user_id, first_id = str(uuid.uuid4()), "msg_" + uuid.uuid4().hex
        user = journal.message("user", "Continue original work.", at=stamp(10))
        user.update(uuid=user_id, parentUuid=None, isSidechain=False)
        journal.append(user)
        first = journal.message("assistant", "Work in progress.", at=stamp(11))
        first_uuid = str(uuid.uuid4())
        first.update(uuid=first_uuid, parentUuid=user_id, isSidechain=False)
        first["message"].update(id=first_id, type="message", stop_reason="tool_use")
        first["message"]["content"] = [dict(type="tool_use", id="toolu_1", name="Read",
                                             input=dict(file_path="/offline/task/source"))]
        first_ref = journal.append(first)
        result_uuid = str(uuid.uuid4())
        tool_result = journal.message("user", "unused", at=stamp(12))
        tool_result.update(uuid=result_uuid, parentUuid=first_uuid, isSidechain=False)
        tool_result["message"]["content"] = [dict(type="tool_result", tool_use_id="toolu_1", content="done")]
        journal.append(tool_result)
        final_id = "msg_" + uuid.uuid4().hex
        answer = journal.message("assistant", "Done.", at=stamp(13))
        answer.update(uuid=str(uuid.uuid4()), parentUuid=result_uuid, isSidechain=False)
        answer["message"].update(id=final_id, type="message", stop_reason="end_turn")
        final_ref = journal.append(answer)
        duplicate = dict(answer, uuid=str(uuid.uuid4()), parentUuid=answer["uuid"])
        journal.append(duplicate)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(len(value["observations"]), 2)
        self.assertEqual([event["native_record"] for event in value["observations"]], [first_ref, final_ref])
        self.assertEqual(len({event["attempt_id"] for event in value["observations"]}), 2)

    def test_native_attachments_bridge_parent_chain_without_becoming_health_outcomes(self):
        expected = {}
        for lane in LANES[1:]:
            journal = self.journals[lane]
            user = journal.message("user", "Continue original authorized work.", at=stamp(10))
            user.update(uuid=str(uuid.uuid4()), parentUuid=None, isSidechain=False)
            journal.append(user)
            hook = dict(type="attachment", uuid=str(uuid.uuid4()), parentUuid=user["uuid"],
                        sessionId=journal.session["session_id"], isSidechain=False, timestamp=stamp(11),
                        attachment=dict(type="hook_success", content="Original hook completed."))
            journal.append(hook)
            # An in-flight registration must keep the pre-fence ancestry, not its health.
            self.sessions[lane]["freeze_fence"] = journal.reader.snapshot()
            queued = dict(type="attachment", uuid=str(uuid.uuid4()), parentUuid=hook["uuid"],
                          sessionId=journal.session["session_id"], isSidechain=False, timestamp=stamp(12),
                          attachment=dict(type="queued_command", content="Quoted HTTP 502; not an outcome."))
            journal.append(queued)
            answer = journal.message("assistant", "Original native terminal result.", at=stamp(13))
            answer.update(uuid=str(uuid.uuid4()), parentUuid=queued["uuid"], isSidechain=False)
            answer["message"].update(id="msg_" + uuid.uuid4().hex, type="message", stop_reason="end_turn")
            if lane == "claude_b":
                answer.update(isApiErrorMessage=True, apiErrorStatus=502)
                answer["message"].update(id=str(uuid.uuid4()), model="<synthetic>", stop_reason="stop_sequence")
            expected[lane] = journal.append(answer)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(value["pending_lanes"], [])
        self.assertEqual(len(value["observations"]), 2)
        by_surface = {event["surface_uuid"]: event for event in value["observations"]}
        for lane in LANES[1:]:
            event = by_surface[self.sessions[lane]["surface_uuid"]]
            self.assertEqual(event["native_record"], expected[lane])
            self.assertEqual(event["kind"], "API_SUCCESS" if lane == "claude_a" else "API_ERROR")
            if lane == "claude_b":
                self.assertEqual(event["http_status"], 502)
        self.assertEqual(value["input_operations"], 0)

    def test_attachments_without_an_original_same_session_root_cannot_authenticate_a_result(self):
        for lane in LANES[1:]:
            journal = self.journals[lane]
            for index, change in enumerate((dict(isSidechain=True), dict(parentUuid=str(uuid.uuid4())),
                                             dict(type="queue-operation"))):
                epoch = 10 + index * 3
                user = journal.message("user", "Keep original authorized scope.", at=stamp(epoch))
                user.update(uuid=str(uuid.uuid4()), parentUuid=None, isSidechain=False)
                journal.append(user)
                node = dict(type="attachment", uuid=str(uuid.uuid4()), parentUuid=user["uuid"],
                            sessionId=journal.session["session_id"], isSidechain=False, timestamp=stamp(epoch + 1),
                            attachment=dict(type="hook_success", content="Cannot certify a model result."))
                node.update(change)
                journal.append(node)
                answer = journal.message("assistant", "Unbound native result.", at=stamp(epoch + 2))
                answer.update(uuid=str(uuid.uuid4()), parentUuid=node["uuid"], isSidechain=False)
                answer["message"].update(id="msg_" + uuid.uuid4().hex, type="message", stop_reason="end_turn")
                journal.append(answer)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(value["observations"], [])
        self.journals["claude_a"].append(dict(type="attachment", uuid=str(uuid.uuid4()),
            parentUuid=user["uuid"], sessionId=str(uuid.uuid4()), isSidechain=False, timestamp=stamp(30),
            attachment=dict(type="hook_success")))
        with self.assertRaisesRegex(ContractError, "record belongs to another Claude session"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)

    def test_prefence_partial_codex_turn_with_postfence_activity_still_cannot_overlap(self):
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(1),
                            payload=dict(type="task_started", turn_id=turn)))
        self.sessions["codex"]["freeze_fence"] = journal.reader.snapshot()
        journal.append(dict(type="turn_context", timestamp=stamp(10),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        self.codex_success(11)
        with self.assertRaisesRegex(ContractError, "overlapping original native turns"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)

    def test_postfence_native_response_keeps_a_historical_turn_active_despite_thread_events(self):
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(1),
                            payload=dict(type="task_started", turn_id=turn)))
        journal.append(dict(type="turn_context", timestamp=stamp(2),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        self.sessions["codex"]["freeze_fence"] = journal.reader.snapshot()
        journal.append(dict(type="event_msg", timestamp=stamp(3),
                            payload=dict(type="thread_settings_applied")))
        journal.append(journal.message("assistant", "This same original turn is still working.", at=stamp(4)))
        journal.append(dict(type="event_msg", timestamp=stamp(5),
                            payload=dict(type="thread_goal_updated")))
        self.codex_success(10)
        with self.assertRaisesRegex(ContractError, "overlapping original native turns"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)

    def test_codex_abort_resets_health_but_is_not_an_api_outage_or_success(self):
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(10),
                            payload=dict(type="task_started", turn_id=turn)))
        ref = journal.append(dict(type="event_msg", timestamp=stamp(11),
                                  payload=dict(type="turn_aborted", turn_id=turn, reason="interrupted")))
        result = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["kind"], "NATIVE_OUTCOME_UNKNOWN")
        self.assertEqual(result["observations"][0]["native_record"], ref)
        self.assertNotIn("http_status", result["observations"][0])

    def test_original_codex_string_failure_is_unknown_not_ignored_or_guessed_http_outage(self):
        self.codex_success(10)
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(30),
                            payload=dict(type="task_started", turn_id=turn)))
        journal.append(dict(type="turn_context", timestamp=stamp(30),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        failure = journal.append(dict(type="event_msg", timestamp=stamp(31), payload=dict(
            type="task_complete", turn_id=turn, error="HTTP 401: provider returned an unstructured native error")))
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)
        self.assertEqual(len(value["observations"]), 2)
        self.assertEqual(value["observations"][-1]["kind"], "NATIVE_OUTCOME_UNKNOWN")
        self.assertEqual(value["observations"][-1]["native_record"], failure)
        self.assertNotIn("http_status", value["observations"][-1])

    def test_original_claude_model_change_cannot_leave_old_success_available(self):
        self.claude_success("claude_a", 10)
        journal = self.journals["claude_a"]
        user = journal.message("user", "Keep original work.", at=stamp(30))
        user.update(uuid=str(uuid.uuid4()), parentUuid=None, isSidechain=False)
        journal.append(user)
        answer = journal.message("assistant", "Changed model answer.", at=stamp(31))
        answer.update(uuid=str(uuid.uuid4()), parentUuid=user["uuid"], isSidechain=False)
        answer["message"].update(id="msg_" + uuid.uuid4().hex, type="message", stop_reason="end_turn",
                                  model="different-native-model")
        journal.append(answer)
        with self.assertRaisesRegex(ContractError, "native continuity model changed"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)

    def test_chronology_future_internal_records_and_overlapping_turns_fail_closed(self):
        journal, first, second = self.journals["codex"], str(uuid.uuid4()), str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(30),
                            payload=dict(type="task_started", turn_id=first)))
        with self.assertRaisesRegex(ContractError, "observation clock"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        journal.append(dict(type="event_msg", timestamp=stamp(31),
                            payload=dict(type="task_started", turn_id=second)))
        with self.assertRaisesRegex(ContractError, "overlapping"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)

    def test_unclosed_prefence_codex_history_does_not_poison_a_fresh_original_turn(self):
        journal = self.journals["codex"]
        # Older native crash boundaries provide no terminal outcome. Keep their
        # original bytes, but do not call them provider failures or current work.
        for epoch in (1, 2):
            turn = str(uuid.uuid4())
            journal.append(dict(type="event_msg", timestamp=stamp(epoch),
                                payload=dict(type="task_started", turn_id=turn)))
            journal.append(dict(type="turn_context", timestamp=stamp(epoch),
                                payload=dict(turn_id=turn, model=journal.session["model"])))
        self.sessions["codex"]["freeze_fence"] = journal.reader.snapshot()
        historical = collect_native_outcomes(self.sessions, task_id=TASK, now=5, fixtures=True)
        self.assertEqual(historical["observations"], [])
        expected = self.codex_success(10)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(value["pending_lanes"], [])
        self.assertEqual(len(value["observations"]), 1)
        self.assertEqual(value["observations"][0]["native_record"], expected)
        self.assertEqual(value["observations"][0]["kind"], "API_SUCCESS")
        self.assertEqual(value["input_operations"], 0)

    def test_postfence_thread_events_do_not_turn_historical_crashes_into_active_work(self):
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(1),
                            payload=dict(type="task_started", turn_id=turn)))
        journal.append(dict(type="turn_context", timestamp=stamp(2),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        self.sessions["codex"]["freeze_fence"] = journal.reader.snapshot()
        for index, kind in enumerate(("thread_settings_applied", "thread_goal_updated")):
            journal.append(dict(type="event_msg", timestamp=stamp(3 + index),
                                payload=dict(type=kind)))
        journal.append(dict(type="session_meta", timestamp=stamp(5),
                            payload=dict(id=journal.session["session_id"])))
        expected = self.codex_success(10)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(value["pending_lanes"], [])
        self.assertEqual(len(value["observations"]), 1)
        self.assertEqual(value["observations"][0]["native_record"], expected)
        self.assertEqual(value["observations"][0]["kind"], "API_SUCCESS")
        self.assertEqual(value["input_operations"], 0)

    def test_duplicate_original_turn_id_cannot_count_as_distinct_retry_attempts(self):
        self.codex_error(10, 502)
        journal = self.journals["codex"]
        records = list(journal.reader.records(journal.session["freeze_fence"]["offset"],
                                             journal.reader.snapshot()["offset"]))
        for _, record in records:
            copied = dict(record, timestamp=stamp(80))
            journal.append(copied)
        with self.assertRaisesRegex(ContractError, "duplicate original attempt"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=100, fixtures=True)

    def test_quotes_tool_errors_queue_and_internal_retry_only_do_not_create_outage(self):
        for lane in LANES:
            journal = self.journals[lane]
            quoted = dict(type="event_msg", payload=dict(type="task_complete", turn_id=str(uuid.uuid4()),
                error=dict(message="HTTP 403", codex_error_info=dict(http_status_code=403))))
            journal.append(journal.message("assistant", canonical(quoted).decode(), at=stamp(10)))
            if lane == "codex":
                journal.append(dict(type="response_item", timestamp=stamp(11), payload=dict(
                    type="function_call_output", call_id="tool1", output="HTTP 403 502 API error")))
                journal.append(dict(type="event_msg", timestamp=stamp(12), payload=dict(
                    type="error", turn_id=str(uuid.uuid4()), message="HTTP 502", retryAttempt=3)))
            else:
                journal.append(dict(type="queue-operation", sessionId=journal.session["session_id"],
                    timestamp=stamp(11), content="HTTP 502 queued failure"))
                journal.append(dict(type="system", subtype="api_error", sessionId=journal.session["session_id"],
                    uuid=str(uuid.uuid4()), parentUuid=str(uuid.uuid4()), timestamp=stamp(12),
                    retryAttempt=7, maxRetries=10, error=dict(status=502)))
        result = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["input_operations"], 0)

    def test_old_outcomes_and_partial_append_never_become_fresh_availability(self):
        for lane in LANES:
            if lane == "codex":
                self.codex_error(1, 401)
            else:
                self.claude_error(lane, 1, 403)
            self.sessions[lane]["freeze_fence"] = self.journals[lane].reader.snapshot()
        journal = self.journals["codex"]
        turn = str(uuid.uuid4())
        raw = canonical(dict(type="event_msg", timestamp=stamp(10),
                             payload=dict(type="task_started", turn_id=turn)))
        with journal.path.open("ab") as stream:
            stream.write(raw[:-1])
        result = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["pending_lanes"], ["codex"])
        with journal.path.open("ab") as stream:
            stream.write(raw[-1:])
        result = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["pending_lanes"], [])

    def test_registering_during_original_turn_preserves_ancestry_without_reusing_old_success(self):
        self.codex_success(1)
        journal, turn = self.journals["codex"], str(uuid.uuid4())
        journal.append(dict(type="event_msg", timestamp=stamp(10),
                            payload=dict(type="task_started", turn_id=turn)))
        journal.append(dict(type="turn_context", timestamp=stamp(10),
                            payload=dict(turn_id=turn, model=journal.session["model"])))
        parents = {}
        for lane in LANES[1:]:
            self.claude_success(lane, 1)
            user = self.journals[lane].message("user", "Continue the same task.", at=stamp(10))
            user.update(uuid=str(uuid.uuid4()), parentUuid=None, isSidechain=False)
            self.journals[lane].append(user)
            parents[lane] = user["uuid"]
        for lane in LANES:
            self.sessions[lane]["freeze_fence"] = self.journals[lane].reader.snapshot()
        before = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        self.assertEqual(before["observations"], [])
        final = journal.message("assistant", "Current original turn finished.", at=stamp(30))
        final["payload"]["phase"] = "final_answer"
        journal.append(final)
        expected = {"codex": journal.append(dict(type="event_msg", timestamp=stamp(31), payload=dict(
            type="task_complete", turn_id=turn, last_agent_message="Current original turn finished.")))}
        for lane in LANES[1:]:
            answer = self.journals[lane].message("assistant", "Current original task finished.", at=stamp(31))
            answer.update(uuid=str(uuid.uuid4()), parentUuid=parents[lane], isSidechain=False)
            answer["message"].update(id="msg_" + uuid.uuid4().hex, type="message", stop_reason="end_turn")
            expected[lane] = self.journals[lane].append(answer)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)
        self.assertEqual(len(value["observations"]), 3)
        for lane in LANES:
            event = next(event for event in value["observations"]
                         if event["surface_uuid"] == self.sessions[lane]["surface_uuid"])
            self.assertEqual(event["kind"], "API_SUCCESS")
            self.assertEqual(event["native_record"], expected[lane])

    def test_changed_or_replaced_original_records_and_wrong_private_authority_are_refused(self):
        self.codex_success(10)
        journal = self.journals["codex"]
        saved = journal.path.read_bytes()
        journal.path.rename(journal.path.with_suffix(".saved"))
        journal.path.write_bytes(saved)
        with self.assertRaisesRegex(ContractError, "replaced"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        with self.assertRaisesRegex(ContractError, "private adoption required"):
            collect_native_outcomes(self.sessions, task_id=TASK, now=20)

    @unittest.skipUnless(MODERN_SOURCE.is_dir(), "requires complete fixed original MAC0.4.58")
    def test_caller_forged_health_or_omitted_original_outcome_is_refused_before_output(self):
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        authorization = write_json(self.root / "authorization.json", dict(source="user_message", task_id=TASK))
        actual = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        changed = copy.deepcopy(actual)
        changed["observations"][0].update(kind="API_ERROR", http_status=401)
        omitted = copy.deepcopy(actual)
        omitted["observations"].pop()
        for index, forged in enumerate((changed, omitted)):
            output = self.root / ("forged-health-" + str(index))
            with self.subTest(index=index), self.assertRaisesRegex(ContractError, "caller-authored labels"):
                invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
                    file_ref(Path(sys.executable).resolve()), self.sessions, forged, authorization,
                    output=output, now=20, fixtures=True)
            self.assertFalse(output.exists())

    @unittest.skipUnless(MODERN_SOURCE.is_dir(), "requires complete fixed original MAC0.4.58")
    def test_bounded_incomplete_scan_never_runs_plan_and_explicit_larger_episode_sees_terminal_error(self):
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        for _ in range(300):
            self.journals["codex"].append(dict(type="event_msg", timestamp=stamp(15),
                                              payload=dict(type="agent_message", message="Still working.")))
        self.codex_error(30, 401)
        authorization = write_json(self.root / "authorization.json", dict(source="user_message", task_id=TASK))
        partial = collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)
        self.assertEqual(partial["pending_lanes"], ["codex"])
        with self.assertRaisesRegex(ContractError, "incomplete original native interval"):
            invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
                file_ref(Path(sys.executable).resolve()), self.sessions, partial, authorization,
                output=self.root / "forbidden-partial-plan", now=40, fixtures=True)
        self.assertFalse((self.root / "forbidden-partial-plan").exists())
        complete = collect_native_outcomes(self.sessions, task_id=TASK, now=40,
                                           max_records=512, fixtures=True)
        self.assertEqual(complete["pending_lanes"], [])
        self.assertEqual(complete["record_budget"], 512)
        result = invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
            file_ref(Path(sys.executable).resolve()), self.sessions, complete, authorization,
            output=self.root / "complete-bounded-plan", now=40, fixtures=True)
        self.assertEqual(result["plan"]["mode"], "CLAUDE_PEER")
        self.assertEqual(result["plan"]["health"][self.sessions["codex"]["surface_uuid"]]["state"],
                         "UNAVAILABLE")

    @unittest.skipUnless(MODERN_SOURCE.is_dir(), "requires complete fixed original MAC0.4.58")
    def test_original_plan_verifies_its_complete_pinned_interval_despite_later_native_appends(self):
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        original_eofs = dict(value["observed_eofs"])
        self.codex_error(30, 401)
        authorization = write_json(self.root / "authorization.json", dict(source="user_message", task_id=TASK))
        result = invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
            file_ref(Path(sys.executable).resolve()), self.sessions, value, authorization,
            output=self.root / "pinned-original-interval", now=20, fixtures=True)
        self.assertEqual(result["plan"]["mode"], "TRIO")
        self.assertEqual(value["observed_eofs"], original_eofs)
        fresh = collect_native_outcomes(self.sessions, task_id=TASK, now=40, fixtures=True)
        result = invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
            file_ref(Path(sys.executable).resolve()), self.sessions, fresh, authorization,
            output=self.root / "later-original-interval", now=40, fixtures=True)
        self.assertEqual(result["plan"]["mode"], "CLAUDE_PEER")
        self.assertEqual(result["plan"]["active"], [self.sessions[lane]["surface_uuid"] for lane in LANES[1:]])
        self.assertEqual(result["plan"]["input_operations"], 0)
        self.assertIs(result["plan"]["replacement_session_allowed"], False)

    @unittest.skipUnless(MODERN_SOURCE.is_dir(), "requires complete fixed original MAC0.4.58")
    def test_derived_native_topologies_use_the_complete_original_controller_evaluator(self):
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        authorization = write_json(self.root / "authorization.json", dict(source="user_message", task_id=TASK))
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=20, fixtures=True)
        result = invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
            file_ref(Path(sys.executable).resolve()), self.sessions, value, authorization,
            output=self.root / "original-trio", now=20, fixtures=True)
        self.assertEqual(result["plan"]["mode"], "TRIO")
        self.assertEqual(result["plan"]["max_subagents"], 2)
        self.assertEqual(result["plan"]["child_budget_scope"], "WHOLE_TASK")
        self.assertEqual(result["plan"]["input_operations"], 0)
        self.assertEqual(result["plan"]["resource_releases"], 0)
        self.assertIs(result["plan"]["replacement_session_allowed"], False)
        for lane in LANES[1:]:
            for epoch in (30, 90, 150):
                self.claude_error(lane, epoch, 502)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=160, fixtures=True)
        result = invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
            file_ref(Path(sys.executable).resolve()), self.sessions, value, authorization,
            output=self.root / "original-codex-solo", now=160, fixtures=True)
        self.assertEqual(result["plan"]["mode"], "CODEX_SOLO")
        self.assertEqual(result["plan"]["active"], [self.sessions["codex"]["surface_uuid"]])
        for lane in LANES[1:]:
            self.claude_success(lane, 170)
        self.codex_error(180, 401)
        value = collect_native_outcomes(self.sessions, task_id=TASK, now=190, fixtures=True)
        result = invoke_original_plan(tree_manifest(MODERN_SOURCE.resolve()),
            file_ref(Path(sys.executable).resolve()), self.sessions, value, authorization,
            output=self.root / "original-claude-peer", now=190, fixtures=True)
        self.assertEqual(result["plan"]["mode"], "CLAUDE_PEER")
        self.assertEqual(result["plan"]["active"], [self.sessions[lane]["surface_uuid"] for lane in LANES[1:]])
        self.assertEqual(result["plan"]["resource_releases"], 0)
        self.assertIs(result["plan"]["replay_allowed"], False)


@unittest.skipUnless(MODERN_SOURCE.is_dir(), "requires complete fixed original MAC0.4.58")
class RegisteredNativeContinuityTests(unittest.TestCase):
    source_controller = MODERN_SOURCE
    continuity_registration = True
    codex_success = NativeContinuityTests.codex_success
    claude_success = NativeContinuityTests.claude_success
    codex_error = NativeContinuityTests.codex_error
    claude_error = NativeContinuityTests.claude_error

    def setUp(self):
        import test_upgrade_live_startup as startup
        from test_upgrade_live_control import LiveControlTests
        from upgrade_live_runtime import authenticated_sessions
        from orchestration_native import NativeReader
        startup.MaterializedStartupTests.setUpClass.__func__(self)
        self.addCleanup(self.tmp.cleanup)
        _, _, proof = LiveControlTests.observation(self)
        self.adoption = write_json(self.spec["adoption_path"], proof, exclusive=True)
        self.sessions = authenticated_sessions(self.runtime_ref, self.adoption)
        self.journals = {}
        for lane, session in self.sessions.items():
            journal = NativeFixture.__new__(NativeFixture)
            journal.path = Path(session["transcript"]["path"])
            journal.provider, journal.session = session["provider"], session
            journal.reader = NativeReader(session)
            self.journals[lane] = journal
        text = "Implement the original 1+2 continuity plan. [offline fixture only; not live evidence]"
        journal = self.journals["codex"]
        ref = journal.append(journal.message("user", text, at=stamp(1)))
        self.authorizations = [dict(session=self.sessions["codex"], record=ref,
                                    text_sha256=digest(text.encode()))]

    def test_registered_native_observation_runs_generated_original_evaluator_under_kernel_boundary(self):
        from upgrade_continuity_native import register_native_continuity, observe_registered_native
        registration = register_native_continuity(self.runtime_ref, self.adoption,
            task_id=TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        before = {lane: journal.path.read_bytes() for lane, journal in self.journals.items()}
        result = observe_registered_native(registration, now=20)
        self.assertEqual(result["status"], "ORIGINAL_ADVISORY_VERIFIED")
        self.assertEqual(result["original"]["plan"]["mode"], "TRIO")
        self.assertEqual(result["original"]["module"], file_ref(Path(self.spec["controller"]) /
                                                                  "scripts/collaboration_continuity.py"))
        self.assertFalse(result["observations"]["fixture_mode"])
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(result["resource_releases"], 0)
        self.assertIs(result["advisory_only"], True)
        self.assertIs(result["child_budget_enforced"], False)
        process = json_ref(file_ref(Path(result["evidence"]["path"]).parent / "process.json"))
        self.assertEqual(process["argv"][:3], ["/usr/bin/sandbox-exec", "-f",
                                              self.spec["pins"]["control_sandbox"]["path"]])
        self.assertEqual(process["returncode"], 0)
        self.assertEqual(before, {lane: journal.path.read_bytes() for lane, journal in self.journals.items()})

    def test_registered_retryable_failure_without_source_qualified_server_delay_stays_pending(self):
        from upgrade_continuity_native import register_native_continuity, observe_registered_native
        registration = register_native_continuity(self.runtime_ref, self.adoption,
            task_id=TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        healthy = observe_registered_native(registration, now=20)
        self.assertEqual(healthy["original"]["plan"]["mode"], "TRIO")
        registered = json_ref(registration)
        output = Path(registered["output_root"]) / "observations"
        before_outputs = tree_manifest(output, kind="output")
        terminal_refs = [self.codex_error(epoch, 502) for epoch in (30, 90, 150)]
        before_native = {lane: journal.path.read_bytes() for lane, journal in self.journals.items()}

        result = observe_registered_native(registration, now=160)

        self.assertEqual(result["status"], "ORIGINAL_RETRY_DELAY_PENDING")
        self.assertEqual(result["retry_delay_pending_lanes"], ["codex"])
        self.assertEqual(result["observations"]["pending_lanes"], [])
        failures = [event for event in result["observations"]["observations"]
                    if event["surface_uuid"] == self.sessions["codex"]["surface_uuid"]
                    and event["kind"] == "API_ERROR"]
        self.assertEqual([event["native_record"] for event in failures], terminal_refs)
        self.assertEqual([event["http_status"] for event in failures], [502, 502, 502])
        self.assertNotIn("original", result)
        self.assertNotIn("evidence", result)
        self.assertEqual(tree_manifest(output, kind="output"), before_outputs)
        self.assertEqual(json_ref(registration), registered)
        self.assertEqual(before_native, {lane: journal.path.read_bytes() for lane, journal in self.journals.items()})
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(result["resource_releases"], 0)
        self.assertIs(result["advisory_only"], True)

    def test_same_original_success_resolves_retry_delay_pending_without_resetting_registration(self):
        from upgrade_continuity_native import register_native_continuity, observe_registered_native
        registration = register_native_continuity(self.runtime_ref, self.adoption,
            task_id=TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        registered = json_ref(registration)
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        self.codex_error(30, 429)
        pending = observe_registered_native(registration, now=40)
        self.assertEqual(pending["status"], "ORIGINAL_RETRY_DELAY_PENDING")
        self.assertEqual(pending["retry_delay_pending_lanes"], ["codex"])
        self.assertNotIn("original", pending)

        success = self.codex_success(100)
        result = observe_registered_native(registration, now=110)

        self.assertEqual(result["status"], "ORIGINAL_ADVISORY_VERIFIED")
        self.assertEqual(result["original"]["plan"]["mode"], "TRIO")
        health = result["original"]["plan"]["health"][self.sessions["codex"]["surface_uuid"]]
        self.assertEqual(health["state"], "AVAILABLE")
        self.assertEqual(health["consecutive_failures"], 0)
        self.assertIsNone(health["retry_due_epoch"])
        codex_events = [event for event in result["observations"]["observations"]
                        if event["surface_uuid"] == self.sessions["codex"]["surface_uuid"]]
        self.assertEqual(codex_events[-1]["native_record"], success)
        self.assertEqual(codex_events[-1]["kind"], "API_SUCCESS")
        self.assertEqual(json_ref(registration), registered)
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(result["resource_releases"], 0)
        self.assertIs(result["advisory_only"], True)

    def test_original_verifier_cannot_bypass_pending_delay_or_accept_caller_authored_advice(self):
        from upgrade_continuity_native import register_native_continuity, observe_registered_native
        registration = register_native_continuity(self.runtime_ref, self.adoption,
            task_id=TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        registered = json_ref(registration)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        self.codex_error(30, 503)
        result = observe_registered_native(registration, now=40)
        self.assertEqual(result["status"], "ORIGINAL_RETRY_DELAY_PENDING")
        output = Path(registered["output_root"]) / "observations" / "forbidden-direct-plan"
        with self.assertRaisesRegex(ContractError, "ORIGINAL_RETRY_DELAY_NOT_OBSERVABLE"):
            invoke_original_plan(self.runtime["controller"], self.runtime["python"],
                registered["sessions"], result["observations"], registered["authorization"],
                output=output, now=40, runtime_ref=self.runtime_ref)
        self.assertFalse(output.exists())

        forged = copy.deepcopy(result["observations"])
        event = next(event for event in forged["observations"] if event["kind"] == "API_ERROR")
        event["retry_after_seconds"] = 60
        with self.assertRaisesRegex(ContractError, "caller-authored labels supplied"):
            invoke_original_plan(self.runtime["controller"], self.runtime["python"],
                registered["sessions"], forged, registered["authorization"],
                output=output, now=40, runtime_ref=self.runtime_ref)
        self.assertFalse(output.exists())
        self.assertEqual(json_ref(registration), registered)

    def test_both_original_claude_retryable_lanes_need_delay_but_permanent_failures_remain_definitive(self):
        from upgrade_continuity_native import register_native_continuity, observe_registered_native
        registration = register_native_continuity(self.runtime_ref, self.adoption,
            task_id=TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        registered = json_ref(registration)
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_error(lane, 30, 502)
        pending = observe_registered_native(registration, now=40)
        self.assertEqual(pending["status"], "ORIGINAL_RETRY_DELAY_PENDING")
        self.assertEqual(pending["retry_delay_pending_lanes"], ["claude_a", "claude_b"])
        self.assertNotIn("original", pending)
        self.assertFalse((Path(registered["output_root"]) / "observations").exists())

        failures = {lane: self.claude_error(lane, 90, status)
                    for lane, status in (("claude_a", 401), ("claude_b", 403))}
        result = observe_registered_native(registration, now=100)

        self.assertEqual(result["status"], "ORIGINAL_ADVISORY_VERIFIED")
        self.assertEqual(result["original"]["plan"]["mode"], "CODEX_SOLO")
        for lane in LANES[1:]:
            health = result["original"]["plan"]["health"][self.sessions[lane]["surface_uuid"]]
            self.assertEqual(health["state"], "UNAVAILABLE")
            self.assertEqual(health["consecutive_failures"], 0)
            event = [event for event in result["observations"]["observations"]
                     if event["surface_uuid"] == self.sessions[lane]["surface_uuid"]][-1]
            self.assertEqual(event["native_record"], failures[lane])
        self.assertEqual(json_ref(registration), registered)
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(result["resource_releases"], 0)

    def test_registration_rejects_caller_fixture_flag_wrong_scope_and_mutated_authorization(self):
        from upgrade_continuity_native import (register_native_continuity, observe_registered_native,
                                               validate_native_registration)
        registration = register_native_continuity(self.runtime_ref, self.adoption,
            task_id=TASK, native_authorizations=self.authorizations, automatic_fallback_authorized=True)
        original = json_ref(registration)
        directory = Path(original["output_root"])
        registration_path = Path(registration["path"])
        saved = registration_path.read_bytes()
        try:
            mutations = [dict(original, fixture_mode=True),
                         dict(original, task_id="another-task"),
                         dict(original, output_root=str(self.root)),
                         dict(original, sessions=copy.deepcopy(original["sessions"]))]
            mutations[-1]["sessions"]["claude_a"]["surface_uuid"] = str(uuid.uuid4())
            for index, forged in enumerate(mutations):
                registration_path.chmod(0o600)
                candidate = write_json(registration_path, forged)
                registration_path.chmod(0o444)
                with self.subTest(index=index), self.assertRaises(ContractError):
                    observe_registered_native(candidate, now=20)
                self.assertFalse((directory / "observations").exists())
        finally:
            registration_path.chmod(0o600)
            registration_path.write_bytes(saved)
            registration_path.chmod(0o444)
        self.assertEqual(validate_native_registration(registration)[0], original)
        authorization_path = Path(original["authorization"]["path"])
        saved_authorization = authorization_path.read_bytes()
        try:
            authorization_path.chmod(0o600)
            changed = json_ref(original["authorization"])
            changed["native_records"][0]["text_sha256"] = "0" * 64
            forged = dict(original, authorization=write_json(authorization_path, changed))
            candidate = write_json(registration_path, forged)
            with self.assertRaisesRegex(ContractError, "pinned original native user"):
                observe_registered_native(candidate, now=20)
            self.assertFalse((directory / "observations").exists())
        finally:
            authorization_path.write_bytes(saved_authorization)
            authorization_path.chmod(0o444)
            registration_path.chmod(0o600)
            registration_path.write_bytes(saved)
            registration_path.chmod(0o444)
        with self.assertRaisesRegex(ContractError, "exists; do not reset"):
            register_native_continuity(self.runtime_ref, self.adoption,
                task_id=TASK, native_authorizations=self.authorizations, automatic_fallback_authorized=True)

    def test_original_evidence_stays_readable_after_stop_without_restarting_or_changing_native_logs(self):
        from upgrade_continuity_native import register_native_continuity, observe_registered_native
        registration = register_native_continuity(self.runtime_ref, self.adoption,
            task_id=TASK, native_authorizations=self.authorizations, automatic_fallback_authorized=True)
        self.codex_success(10)
        for lane in LANES[1:]:
            self.claude_success(lane, 10)
        write_json(self.spec["stop_path"], dict(status="STOPPED_INPUT", reason="offline control"), exclusive=True)
        before = {lane: journal.path.read_bytes() for lane, journal in self.journals.items()}
        result = observe_registered_native(registration, now=20)
        self.assertEqual(result["original"]["plan"]["mode"], "TRIO")
        self.assertEqual(result["status"], "ORIGINAL_ADVISORY_VERIFIED")
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(before, {lane: journal.path.read_bytes() for lane, journal in self.journals.items()})

    def test_publishing_cli_has_no_fixture_time_or_status_override_and_uses_pinned_requests(self):
        request = write_json(self.root / "native-register-request.json", dict(
            schema="upgrade-native-continuity-register-request-v1", runtime=self.runtime_ref,
            adoption=self.adoption, task_id=TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True))
        program = str(Path(__file__).with_name("upgrade_continuity_native.py"))
        register = [sys.executable, "-B", program, "register", "--request", request["path"],
                    "--request-sha256", request["sha256"]]
        for extra in ("--fixtures", "--now", "--status"):
            refused = subprocess.run(register + [extra], capture_output=True, text=True, timeout=20)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        published = subprocess.run(register, capture_output=True, text=True, timeout=60)
        self.assertEqual(published.returncode, 0, published.stdout + published.stderr)
        registration = json.loads(published.stdout)["registration"]
        epoch = time.time() - 10
        self.codex_success(epoch)
        for lane in LANES[1:]:
            self.claude_success(lane, epoch)
        observe = [sys.executable, "-B", program, "observe", "--registration", registration["path"],
                   "--registration-sha256", registration["sha256"], "--max-records", "512"]
        run = subprocess.run(observe, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], "ORIGINAL_ADVISORY_VERIFIED")
        self.assertEqual(result["original"]["plan"]["mode"], "TRIO")
        self.assertEqual(result["input_operations"], 0)
        self.assertIs(result["advisory_only"], True)


if __name__ == "__main__":
    unittest.main()
