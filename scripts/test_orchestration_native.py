"""Original-journal regression controls; all synthetic files stay offline."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchestration_common import ContractError, canonical, digest, file_ref, parse_time, write_json
from orchestration_native import NativeReader, native_text, verify_delivery

STAMP = "2026-10-14T00:00:02+00:00"
EPOCH = parse_time(STAMP).timestamp()


class NativeFixture:
    def __init__(self, root, provider="claude", *, session_id=None, native_uuid_uppercase=False):
        self.native_uuid_uppercase = native_uuid_uppercase
        self.root = Path(root).resolve()
        sid = session_id or ("42977dde-eb08-4900-aad1-1d20db13ff17" if provider == "claude" else "01a13a5f-2412-7012-a872-501c8f75fda9")
        self.path = self.root / (sid + ".jsonl")
        self.provider = provider
        self.path.write_bytes(canonical(dict(type="session_meta", payload={"id": sid}, sessionId=sid)))
        st = self.path.stat()
        self.session = dict(provider=provider, session_id=sid, model="test-model", tty="ttys079",
                            process=dict(pid=123, birth=[123456, 123], executable="/fixture/cli"),
                            workspace_uuid="workspace", surface_uuid="receiver", pane_uuid="receiver-pane",
                            transcript=dict(path=str(self.path), device=st.st_dev, inode=st.st_ino))
        self.sender = dict(workspace_uuid="workspace", surface_uuid="sender", pane_uuid="sender-pane")
        self.reader = NativeReader(self.session, fixtures=True)

    def append(self, record):
        raw = canonical(record)
        offset = self.path.stat().st_size
        with self.path.open("ab") as stream:
            stream.write(raw)
        return dict(self.session["transcript"], offset=offset, length=len(raw), sha256=digest(raw))

    def message(self, role, text, *, at=STAMP, **extra):
        if self.provider == "claude":
            return dict(type=role, sessionId=self.session["session_id"], timestamp=at,
                        message=dict(role=role, model="test-model", content=[dict(type="text", text=text)]), **extra)
        return dict(type="response_item", timestamp=at, payload=dict(type="message", role=role,
                    content=[dict(type="input_text" if role == "user" else "output_text", text=text)]), **extra)

    def delivery(self, text="TASK|exact", *, sender=None, journal=None, receipt_path=None, at=STAMP, **binding_fields):
        native = self.provider == "codex"
        sender = sender or self.sender
        epoch = parse_time(at).timestamp()
        self.journal = Path(journal) if journal is not None else self.root
        self.receipt_path = Path(receipt_path) if receipt_path is not None else self.journal / "receipt.json"
        identity = dict(workspace_uuid=self.session["workspace_uuid"], caller_surface_uuid=sender["surface_uuid"],
                        target_surface_uuid=self.session["surface_uuid"], target_pane_uuid=self.session["pane_uuid"])
        if self.native_uuid_uppercase:
            identity = {k: v.upper() for k, v in identity.items()}
        b = dict(schema="native-delivery-v1", identity=identity, tty=self.session["tty"],
                 process=self.session["process"], provider=self.provider, session_id=self.session["session_id"],
                 transcript=self.reader.snapshot(), bound_at_epoch=epoch, payload_sha256=digest(text.encode()))
        self.attempt = dict(phase="CONFIRMED", binding=dict(identity=identity, **binding_fields), native_binding=b,
                            started_at_epoch=epoch, ended_at_epoch=epoch + .000004)
        fence = dict(schema="native-input-fence-v1" if native else "native-paste-fence-v1",
                     transcript=self.reader.snapshot(), binding_sha256=digest(json.dumps(b, sort_keys=True).encode()),
                     captured_at_epoch=epoch + .000001)
        event = dict(phase="NATIVE_INPUT_INTENT" if native else "PASTE_INTENT", at_epoch=epoch + .000002)
        if native:
            request = dict(transport="codex-native-input-v1", method="turn/start",
                           params=dict(threadId=self.session["session_id"], input=[dict(type="text", text=text)]),
                           observed_status="idle", observed_turn_id=None, observed_at_epoch=epoch,
                           socket=dict(uid=os.getuid(), peer_uid=os.getuid(), mode=0o600, device=1, inode=2, path="/fixture/socket"))
            event["native_request"] = request
            fence["native_request_sha256"] = digest(json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
            event["native_input_fence"] = fence
        else:
            event.update(screen="empty composer", screen_sha256=digest(b"empty composer")[:16], native_paste_fence=fence)
        self.attempt["events"] = [event]
        proof = self.append(self.message("user", text, at=datetime.fromtimestamp(epoch + .000003, timezone.utc).isoformat()))
        proof.update(session_id=self.session["session_id"], payload_sha256=b["payload_sha256"], reception_kind="native_user_message")
        self.receipt = dict(attempt=str(self.journal / "attempt-0001.json"), confirmed=True,
                            native_binding=b, binding=self.attempt["binding"], native_proof=proof,
                            confirmation_source="native_user_message_v1")
        return self.save()

    def save(self):
        return dict(attempts=[write_json(self.journal / "attempt-0001.json", self.attempt)],
                    receipt=write_json(self.receipt_path, self.receipt))


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
    def tearDown(self):
        self.temp.cleanup()

    def test_original_exact_record_passes_for_both_clis(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                root = self.root / provider
                root.mkdir()
                f = NativeFixture(root, provider)
                self.assertEqual(verify_delivery(f.delivery(), f.session, f.sender, "TASK|exact", fixtures=True)["transport_retries"], 0)

    def test_offline_copy_cannot_claim_native_run(self):
        f = NativeFixture(self.root)
        with self.assertRaisesRegex(ContractError, "copied/non-native"):
            NativeReader(f.session)

    def test_missing_fake_or_changed_proof_rejected(self):
        f = NativeFixture(self.root)
        bundle = f.delivery()
        Path(bundle["receipt"]["path"]).write_text("{}")
        with self.assertRaisesRegex(ContractError, "hash/size"):
            verify_delivery(bundle, f.session, f.sender, "TASK|exact", fixtures=True)

    def test_exact_not_substring_whitespace_or_old_record(self):
        for mutate in (lambda f: f.receipt["native_proof"].update(offset=True),
                       lambda f: f.receipt.update(confirmation_source="queue"),
                       lambda f: f.attempt["events"][0].pop("native_paste_fence"),
                       lambda f: f.attempt["native_binding"].update(session_id="other")):
            f = NativeFixture(self.root)
            f.delivery()
            mutate(f)
            with self.assertRaises(ContractError):
                verify_delivery(f.save(), f.session, f.sender, "TASK|exact", fixtures=True)
        f = NativeFixture(self.root)
        b = f.delivery("TASK|exact ")
        with self.assertRaisesRegex(ContractError, "payload hash"):
            verify_delivery(b, f.session, f.sender, "TASK|exact", fixtures=True)

    def test_uncertain_resend_and_omitted_history_rejected(self):
        f = NativeFixture(self.root)
        b = f.delivery()
        write_json(self.root / "attempt-0002.json", f.attempt)
        with self.assertRaisesRegex(ContractError, "omitted"):
            verify_delivery(b, f.session, f.sender, "TASK|exact", fixtures=True)
        b["attempts"].append(file_ref(self.root / "attempt-0002.json"))
        with self.assertRaisesRegex(ContractError, "uncertain input"):
            verify_delivery(b, f.session, f.sender, "TASK|exact", fixtures=True)

    def test_inode_truncation_and_fence_rewrite_rejected(self):
        f = NativeFixture(self.root)
        pin = f.reader.snapshot()
        f.path.write_bytes(b"{}\n")
        with self.assertRaisesRegex(ContractError, "truncated"):
            f.reader.verify_pin(pin)
        f.path.rename(self.root / "saved.jsonl")
        f.path.write_bytes(b"{}\n")
        with self.assertRaisesRegex(ContractError, "replaced"):
            f.reader.snapshot()

    def test_native_request_overrides_socket_and_fallback_rejected(self):
        f = NativeFixture(self.root, "codex")
        f.delivery()
        original = copy.deepcopy(f.attempt)
        for mutate in (lambda a: a["events"][0]["native_request"]["params"].update(model="other"),
                       lambda a: a["events"][0]["native_request"]["socket"].pop("inode"),
                       lambda a: a["events"].append(dict(phase="ENTER_SENT", at_epoch=EPOCH))):
            f.attempt = copy.deepcopy(original)
            mutate(f.attempt)
            with self.assertRaises(ContractError):
                verify_delivery(f.save(), f.session, f.sender, "TASK|exact", fixtures=True)

    def test_model_comes_from_native_messages_and_completed_actions(self):
        f = NativeFixture(self.root)
        start = f.reader.snapshot()["offset"]
        call = f.message("assistant", "unused")
        call["message"]["content"] = [dict(type="tool_use", id="call", input={"path": "/task/output/report"})]
        f.append(call)
        result = f.message("user", "unused")
        result["message"]["content"] = [dict(type="tool_result", tool_use_id="call", content="ok")]
        f.append(result)
        self.assertEqual(f.reader.action_summary(start, f.reader.snapshot()["offset"], task_id="task", output_root="/task/output")["completed_task_calls"], 1)
        bad = f.message("assistant", "done")
        bad["message"]["model"] = "different"
        f.append(bad)
        with self.assertRaisesRegex(ContractError, "model"):
            f.reader.action_summary(start, f.reader.snapshot()["offset"], task_id="task", output_root="/task/output")

    def test_synthetic_api_noise_and_explicit_retries_are_separate_from_models(self):
        f = NativeFixture(self.root)
        f.append(f.message("assistant", "ready"))
        start = f.reader.snapshot()["offset"]
        error = f.message("assistant", "API error", isApiErrorMessage=True)
        error["message"]["model"] = "<synthetic>"
        f.append(error)
        summary = f.reader.action_summary(start, f.reader.snapshot()["offset"],
            task_id="task", output_root="/task/output", allow_empty=True)
        self.assertEqual(summary["models"], ["test-model"])
        self.assertEqual(summary["failed_api_attempts"], 1)
        self.assertIsNone(summary["provider_auto_retries"])
        for key, number in (("retry-one", 1), ("retry-two", 2), ("retry-one", 1)):
            f.append(dict(type="system", subtype="api_error", sessionId=f.session["session_id"],
                timestamp=STAMP, uuid=key, retryAttempt=number))
        summary = f.reader.action_summary(start, f.reader.snapshot()["offset"],
            task_id="task", output_root="/task/output", allow_empty=True)
        self.assertEqual(summary["provider_auto_retries"], 2)
        self.assertEqual(summary["failed_api_attempts"], 4)

    def test_real_model_change_cannot_hide_behind_api_error_flag(self):
        f = NativeFixture(self.root)
        f.append(f.message("assistant", "ready"))
        start = f.reader.snapshot()["offset"]
        changed = f.message("assistant", "error", isApiErrorMessage=True)
        changed["message"]["model"] = "different-real-model"
        f.append(changed)
        with self.assertRaisesRegex(ContractError, "model missing or changed"):
            f.reader.action_summary(start, f.reader.snapshot()["offset"],
                task_id="task", output_root="/task/output", allow_empty=True)

    def action_records(self, f, output):
        if f.provider == "codex":
            f.append(dict(type="turn_context", timestamp=STAMP, payload={"model": "test-model"}))
            call = dict(type="response_item", timestamp=STAMP, payload=dict(type="custom_tool_call",
                name="exec", call_id="action", input="write /task/output/result.json"))
            result = dict(type="response_item", timestamp=STAMP, payload=dict(type="custom_tool_call_output",
                call_id="action", output=output))
        else:
            call = f.message("assistant", "")
            call["message"]["content"] = [dict(type="tool_use", id="action", name="Bash",
                input={"command": "write /task/output/result.json"})]
            result = f.message("user", "")
            result["message"]["content"] = [dict(type="tool_result", tool_use_id="action", content=output)]
        return call, result

    def test_missing_and_asynchronous_results_never_prove_completion(self):
        values = [{"status": "pending"}, {"output": {"session_id": 12, "exit_code": None}},
                  "Script running with cell ID 42", {"content": [{"text": "Process running with session ID 12"}]}]
        for provider in ("claude", "codex"):
            for index, output in enumerate(values):
                with self.subTest(provider=provider, output=output):
                    root = self.root / (provider + str(index))
                    root.mkdir()
                    f = NativeFixture(root, provider)
                    call, result = self.action_records(f, output)
                    start = f.reader.snapshot()["offset"]
                    f.append(call)
                    for complete in (False, True):
                        if complete:
                            f.append(result)
                        with self.assertRaisesRegex(ContractError, "unfinished task tool call"):
                            f.reader.action_summary(start, f.reader.snapshot()["offset"],
                                task_id="task", output_root="/task/output", allow_empty=True)

    def test_long_original_session_preserves_prior_model_and_native_tail(self):
        f = NativeFixture(self.root, "codex")
        call, result = self.action_records(f, {"exit_code": 0})
        padding = canonical(dict(type="event_msg", payload={"text": "x" * (1024 * 1024)}))
        with f.path.open("ab") as stream:
            for _ in range(65):
                stream.write(padding)
        start = f.reader.snapshot()["offset"]
        self.assertGreater(start, 64 * 1024 * 1024)
        original = f.append(call)
        f.append(result)
        end = f.reader.snapshot()["offset"]
        summary = f.reader.action_summary(start, end, task_id="task", output_root="/task/output")
        self.assertEqual(summary["models"], ["test-model"])
        self.assertEqual(summary["completed_task_calls"], 1)
        self.assertEqual(f.reader.record(original), call)
        self.assertEqual(sum(1 for _ in f.reader.records(0, end)), 69)
        with self.assertRaisesRegex(ContractError, "native range truncated"):
            next(f.reader.records(0, end + 1))
        with self.assertRaisesRegex(ContractError, "native range starts inside a record"):
            next(f.reader.records(start + 1, end))

    def test_observation_retains_pending_evidence_without_completion(self):
        for provider in ("claude", "codex"):
            root = self.root / provider
            root.mkdir()
            f = NativeFixture(root, provider)
            call, pending = self.action_records(f, {"status": "running", "session_id": 12})
            start = f.reader.snapshot()["offset"]
            call_ref = f.append(call)
            for result in (None, pending):
                pending_ref = f.append(result) if result else None
                summary = f.reader.action_summary(start, f.reader.snapshot()["offset"],
                    task_id="task", output_root="/task/output", allow_empty=True,
                    allow_incomplete=True, include_evidence=True)
                self.assertEqual((summary["pending_task_calls"], summary["completed_task_calls"]), (1, 0))
                self.assertIsNone(summary["first_action_at"])
                self.assertIsNone(summary["last_action_at"])
                action = summary["actions"][0]
                self.assertEqual(action["call"], call_ref)
                self.assertEqual(action["pending_results"], [pending_ref] if result else [])
                self.assertIsNone(action["completed_at"])
                self.assertIsNone(action["result"])
                with self.assertRaisesRegex(ContractError, "unfinished task tool call"):
                    f.reader.action_summary(start, f.reader.snapshot()["offset"],
                        task_id="task", output_root="/task/output", allow_empty=True)

    def test_pending_and_failed_actions_cannot_be_first_successful_work(self):
        f = NativeFixture(self.root, "codex")
        start = f.reader.snapshot()["offset"]
        for index, value in enumerate(({"exit_code": 2}, {"status": "running"}, {"exit_code": 0})):
            call, result = self.action_records(f, value)
            call["payload"]["call_id"] = result["payload"]["call_id"] = str(index)
            call["timestamp"] = result["timestamp"] = f"2026-10-14T00:00:0{index + 2}+00:00"
            f.append(call)
            f.append(result)
        summary = f.reader.action_summary(start, f.reader.snapshot()["offset"], task_id="task",
            output_root="/task/output", allow_incomplete=True, include_evidence=True)
        self.assertEqual([a["status"] for a in summary["actions"]], ["FAILED", "PENDING", "COMPLETED"])
        self.assertEqual(summary["first_action_at"], "2026-10-14T00:00:04+00:00")
        self.assertEqual(summary["last_action_at"], "2026-10-14T00:00:04+00:00")

    def test_pending_result_chronology_is_checked_before_observing_it(self):
        f = NativeFixture(self.root, "codex")
        call, pending = self.action_records(f, {"status": "running"})
        start = f.reader.snapshot()["offset"]
        f.append(call)
        pending["timestamp"] = "2026-10-14T00:00:01+00:00"
        f.append(pending)
        with self.assertRaisesRegex(ContractError, "precedes its call"):
            f.reader.action_summary(start, f.reader.snapshot()["offset"], task_id="task",
                output_root="/task/output", allow_empty=True, allow_incomplete=True, include_evidence=True)

    def test_final_result_cannot_precede_an_observed_pending_result(self):
        f = NativeFixture(self.root, "codex")
        call, pending = self.action_records(f, {"status": "running"})
        start = f.reader.snapshot()["offset"]
        f.append(call)
        pending["timestamp"] = "2026-10-14T00:00:05+00:00"
        f.append(pending)
        _, final = self.action_records(f, {"exit_code": 0})
        f.append(final)
        with self.assertRaisesRegex(ContractError, "chronology moved backwards"):
            f.reader.action_summary(start, f.reader.snapshot()["offset"], task_id="task",
                output_root="/task/output", allow_empty=True, allow_incomplete=True, include_evidence=True)

    def test_duplicate_calls_and_results_cannot_inflate_useful_actions(self):
        for provider in ("claude", "codex"):
            for duplicate in ("call", "result"):
                with self.subTest(provider=provider, duplicate=duplicate):
                    root = self.root / (provider + duplicate)
                    root.mkdir()
                    f = NativeFixture(root, provider)
                    call, result = self.action_records(f, {"exit_code": 0})
                    start = f.reader.snapshot()["offset"]
                    f.append(call)
                    f.append(result)
                    f.append(call if duplicate == "call" else result)
                    with self.assertRaisesRegex(ContractError, "duplicate native tool"):
                        f.reader.action_summary(start, f.reader.snapshot()["offset"],
                            task_id="task", output_root="/task/output")

    def test_failed_results_are_attempts_but_never_successful_first_actions(self):
        for provider in ("claude", "codex"):
            for index, output in enumerate(({"exit_code": 1}, {"content": [{"isError": True}]},
                                             "Process exited with code 2")):
                with self.subTest(provider=provider, output=output):
                    root = self.root / (provider + str(index))
                    root.mkdir()
                    f = NativeFixture(root, provider)
                    call, result = self.action_records(f, output)
                    start = f.reader.snapshot()["offset"]
                    f.append(call)
                    f.append(result)
                    summary = f.reader.action_summary(start, f.reader.snapshot()["offset"],
                        task_id="task", output_root="/task/output", allow_empty=True)
                    self.assertEqual(summary["attempted_task_calls"], 1)
                    self.assertEqual(summary["failed_task_calls"], 1)
                    self.assertEqual(summary["completed_task_calls"], 0)
                    self.assertIsNone(summary["first_action_at"])
                    with self.assertRaisesRegex(ContractError, "no completed task-bound"):
                        f.reader.action_summary(start, f.reader.snapshot()["offset"],
                            task_id="task", output_root="/task/output")

    def test_ack_ignores_valid_reasoning_but_never_tools_or_extra_text(self):
        f = NativeFixture(self.root)
        r = f.message("assistant", "ACK")
        r["message"]["content"].insert(0, dict(type="thinking", thinking="private", signature="opaque"))
        self.assertEqual(native_text(r, "claude", "assistant"), "ACK")
        r["message"]["content"].append(dict(type="text", text="extra"))
        self.assertIsNone(native_text(r, "claude", "assistant"))


if __name__ == "__main__":
    unittest.main()
