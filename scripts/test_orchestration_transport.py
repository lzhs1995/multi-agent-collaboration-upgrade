"""Offline fault injection; the fake runtime never imports a live sender."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from orchestration_common import (ContractError, digest, file_ref, json_ref, read_json,
                                  utc_now, write_bytes_once, write_json)
from orchestration_coordinator import Coordinator, State
from orchestration_evidence import task_notice, verify_prepared
from orchestration_test_support import CampaignFixture, OfflineDisarmRuntime
from orchestration_transport import OperationJournal, PendingOperation, PinnedRuntime, Transport, exact_native, journal_bundle


class Crash(BaseException):
    pass


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.journal = OperationJournal(self.root, {"sha256": "fixture"}, "fixture-task", {})

    def tearDown(self):
        self.temp.cleanup()

    def test_returned_operation_is_read_without_a_second_external_call(self):
        action = Mock(return_value={"value": 42})
        self.assertEqual(self.journal.once("send", {"input": 1}, action), {"value": 42})
        self.assertEqual(self.journal.once("send", {"input": 1}, action), {"value": 42})
        action.assert_called_once()
        with self.assertRaisesRegex(ContractError, "binding changed"):
            self.journal.once("send", {"input": 2}, action)

    def test_error_and_process_crash_both_consume_the_input_budget(self):
        for name, error in (("exception", RuntimeError("uncertain")), ("crash", Crash())):
            with self.subTest(name=name):
                action = Mock(side_effect=error)
                with self.assertRaises(PendingOperation if name == "exception" else Crash):
                    self.journal.once(name, {}, action)
                intent = (self.journal.root / name / "intent.json").read_bytes()
                with self.assertRaises(PendingOperation):
                    self.journal.once(name, {}, action)
                action.assert_called_once()
                self.assertEqual(intent, (self.journal.root / name / "intent.json").read_bytes())

    def test_crash_after_external_result_before_result_persistence_never_replays(self):
        action = Mock(return_value="sent")
        original = write_json
        def persist(path, value, **kwargs):
            if Path(path).name == "result.json":
                raise Crash()
            return original(path, value, **kwargs)
        with patch("orchestration_transport.write_json", side_effect=persist), self.assertRaises(Crash):
            self.journal.once("send", {}, action)
        with self.assertRaises(PendingOperation):
            self.journal.once("send", {}, action)
        action.assert_called_once()

    def test_observations_have_separate_immutable_records(self):
        action = Mock(return_value="observed")
        self.journal.observation("observe", {"input_operations": 0}, action)
        self.journal.observation("observe", {"input_operations": 0}, action)
        self.assertEqual(action.call_count, 2)
        self.assertEqual(len(list(self.journal.root.glob("observe-*/result.json"))), 2)

    def test_partial_immutable_file_cannot_be_overwritten(self):
        path = self.root / "body.txt"
        pin = write_bytes_once(path, b"exact\n")
        self.assertEqual(write_bytes_once(path, b"exact\n"), pin)
        with self.assertRaisesRegex(ContractError, "immutable bytes differ"):
            write_bytes_once(path, b"exact")


class FakeRuntime(OfflineDisarmRuntime):
    def __init__(self, fixture, co):
        super().__init__(fixture.campaign, co.root / "offline-disarm")
        self.f, self.co = fixture, co
        self.calls = []
        self.fail_submit = False
        self.hide_receipt = False
        self.saved_receipt = None
        self.handshake_root = co.root / "handshake-original"
        self.dispatch_root = co.root / "dispatch-original"
        self.callback_settlements = []
        self.disarms = []
        self.executor_selector = self.f.clients["claude"].session["surface_uuid"]

    def harness_argv(self, phase, task_id, artifact_root):
        return PinnedRuntime(self.f.campaign).harness_argv(phase, task_id, artifact_root)

    def harness(self, argv, operation_root):
        phase, task = argv[3], argv[argv.index("--task-id") + 1]
        root = Path(argv[argv.index("--artifact-root") + 1])
        self.calls.append(phase)
        if phase == "preflight":
            root.mkdir()
            ack = f"PREFLIGHT_ACK|{task}|claude:identity|READY|INLINE|1234abcd"
            self.f.clients["claude"].delivery("PREFLIGHT " + ack, sender=self.f.clients["codex"].session,
                journal=self.handshake_root, at=utc_now())
            self.f.clients["claude"].append(self.f.clients["claude"].message("assistant", ack, at=utc_now()))
            write_json(root / "handshake-receipt.json", dict(task_id=task, status="PASS", executor_ack=True,
                ack_nonce="1234abcd", ack_line=ack))
            write_json(root / "identity-gate.json", dict(task_id=task, status="PASS", native_participants=[
                dict(provider=k, surface_uuid=c.session["surface_uuid"], native_session_id=c.session["session_id"])
                for k, c in self.f.clients.items()]))
            write_json(root / "validation.json", dict(task_id=task, status="PASS"))
        elif phase == "task-pack":
            write_json(root / "task-pack.json", dict(task_id=task, draft=True,
                executor_uuid=self.f.clients["claude"].session["surface_uuid"],
                supervisor="surface:1", executor=self.executor_selector, callback_target="surface:1",
                executors=[dict(display="EXECUTOR_1", ordinal=1, provider="claude", surface_ref=self.executor_selector)],
                required_skill=str(Path(self.f.campaign["controller"]["path"]) / "SKILL.md"),
                identity_gate=str(root / "identity-gate.json"), validation_evidence=str(root / "validation.json"),
                completion_receipt=str(root / "completion-callback-receipt.json"),
                completion_delivery=dict(transport="cmux_bridge.submit_completion_callback", require_confirmed=True)))
        elif phase == "finalize-pack":
            value = read_json(root / "task-pack.json")
            value.update(draft=False, finalized_at=utc_now(), source_entries=[
                dict(path=str(p).rstrip("。，、；;,.)）]】"), exists=True) for p in value["source"] if str(p).startswith("/")])
            write_json(root / "task-pack.json", value)
        return dict(returncode=0, stdout=write_bytes_once(operation_root / "stdout.txt", b"fixture harness returned\n"),
                    stderr=write_bytes_once(operation_root / "stderr.txt", b""))

    def notice(self, pack):
        self.calls.append("notice")
        return task_notice(file_ref(pack), read_json(pack))

    def submit(self, surface, notice, pack, *, reconcile_only):
        self.calls.append(("submit", reconcile_only))
        if reconcile_only:
            write_json(self.dispatch_root / "receipt.json", self.saved_receipt)
            return {"confirmed": True}
        if self.fail_submit:
            raise RuntimeError("simulated original unknown outcome")
        value = read_json(pack)
        bundle = self.f.clients["claude"].delivery(notice, sender=self.f.clients["codex"].session,
            journal=self.dispatch_root, at=utc_now(), task_id=value["task_id"], marker=value["task_id"],
            task_pack_sha256=file_ref(pack)["sha256"], payload_sha256=digest(notice.encode()))
        self.saved_receipt = json_ref(bundle["receipt"])
        if self.hide_receipt:
            Path(bundle["receipt"]["path"]).unlink()
        return {"confirmed": not self.hide_receipt}

    def settle_callback(self, pack, transcript, line):
        self.callback_settlements.append((pack, transcript, line))
        value = read_json(pack)
        write_json(value["completion_receipt"], self.saved_callback)
        return {"confirmed": True}

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = CampaignFixture(self.temp.name)
        self.co = self.f.start()
        self.info = self.co.claim("review")
        self.runtime = FakeRuntime(self.f, self.co)
        self.transport = Transport(self.co, "review", runtime=self.runtime)
        self.hs = patch("orchestration_transport.handshake_journal", return_value=self.runtime.handshake_root)
        self.ds = patch("orchestration_transport.dispatch_journal", return_value=self.runtime.dispatch_root)
        self.hs.start()
        self.ds.start()

    def tearDown(self):
        self.hs.stop()
        self.ds.stop()
        self.temp.cleanup()

    def test_prepare_and_dispatch_are_idempotent_without_rehandshake_or_resend(self):
        proof = self.transport.prepare()
        self.assertEqual(json_ref(proof["pack"])["executor"], self.f.clients["claude"].session["surface_uuid"])
        self.assertEqual(self.transport.prepare(), proof)
        self.assertEqual(self.runtime.calls, ["preflight", "task-pack", "finalize-pack", "notice"])
        delivered = self.transport.dispatch()
        self.assertEqual(self.transport.dispatch(), delivered)
        self.assertEqual(self.runtime.calls.count(("submit", False)), 1)
        self.assertEqual(self.co.state()[0]["review"], State.DISPATCHED)

    def test_ref_cannot_replace_the_original_uuid_harness_selector(self):
        self.runtime.executor_selector = self.f.clients["claude"].session["surface_ref"]
        with self.assertRaisesRegex(ContractError, "binding changed"):
            self.transport.prepare()
        self.assertEqual(self.co.state()[0]["review"], State.PREPARING)
        self.assertFalse((self.transport.root / "dispatch-intent.json").exists())

    def test_selector_alias_does_not_relax_uuid_role_or_original_session_binding(self):
        proof = self.transport.prepare()
        pack = json_ref(proof["pack"])
        gate = json_ref(proof["identity_gate"])
        for field, value in (("executor", "surface:999"), ("executor", "other-uuid"),
                             ("executor_uuid", "other-uuid"), ("supervisor", "surface:2"),
                             ("callback_target", "surface:2")):
            with self.subTest(field=field, value=value):
                changed = dict(pack, **{field: value})
                mutated = dict(proof, pack=write_json(proof["pack"]["path"], changed))
                Path(proof["notice"]["path"]).write_text(task_notice(mutated["pack"], changed))
                mutated["notice"] = file_ref(proof["notice"]["path"])
                with self.assertRaisesRegex(ContractError, "binding changed"):
                    verify_prepared(self.f.campaign, self.co.header, self.co.nodes["review"], self.info, mutated, fixtures=True)
        write_json(proof["pack"]["path"], pack)
        Path(proof["notice"]["path"]).write_text(task_notice(proof["pack"], pack))
        changed = copy.deepcopy(gate)
        changed["native_participants"][1]["native_session_id"] = "another-session"
        mutated = dict(proof, identity_gate=write_json(proof["identity_gate"]["path"], changed))
        with self.assertRaisesRegex(ContractError, "both original native sessions"):
            verify_prepared(self.f.campaign, self.co.header, self.co.nodes["review"], self.info, mutated, fixtures=True)

    def test_unknown_submit_and_restart_never_send_again(self):
        self.transport.prepare()
        self.runtime.fail_submit = True
        self.assertEqual(self.transport.dispatch()["status"], "UNCERTAIN")
        resumed = Transport(Coordinator(self.co.root, self.f.ref, fixtures=True), "review", runtime=self.runtime)
        self.assertEqual(resumed.dispatch()["status"], "UNCERTAIN")
        self.assertEqual(resumed.reconcile()["status"], "UNCERTAIN")
        self.assertEqual(self.runtime.calls.count(("submit", False)), 1)
        self.assertNotIn(("submit", True), self.runtime.calls)
        self.assertIsNone(self.co.leases.read()[self.info["task_id"]]["released_at"])

    def test_existing_dispatch_intent_without_event_is_observe_only(self):
        self.transport.prepare()
        ref = self.f.intent(self.co, "review")
        # Simulate loss of only the lifecycle transition after durable intent.
        (self.co.root / "events" / "000003.json").unlink()
        original = Path(ref["path"]).read_bytes()
        self.assertEqual(self.transport.dispatch()["status"], "UNCERTAIN")
        self.assertNotIn(("submit", False), self.runtime.calls)
        self.assertEqual(Path(ref["path"]).read_bytes(), original)

    def test_original_attempt_reconciliation_is_explicitly_zero_input(self):
        self.transport.prepare()
        self.runtime.hide_receipt = True
        self.assertEqual(self.transport.dispatch()["status"], "UNCERTAIN")
        original = (self.runtime.dispatch_root / "attempt-0001.json").read_bytes()
        self.assertIn("receipt", self.transport.reconcile())
        self.assertEqual(self.runtime.calls.count(("submit", False)), 1)
        self.assertEqual(self.runtime.calls.count(("submit", True)), 1)
        self.assertEqual((self.runtime.dispatch_root / "attempt-0001.json").read_bytes(), original)

    def test_callback_existing_receipt_does_not_invoke_settlement(self):
        self.transport.prepare()
        self.transport.dispatch()
        self.f.work(self.info, "claude")
        protocol = self.f.callback(self.co, "review")
        self.assertEqual(self.transport.callback(), protocol["callback"])
        self.assertEqual(self.runtime.callback_settlements, [])

    def test_missing_receipt_settles_original_native_line_without_callback_send(self):
        self.transport.prepare()
        self.transport.dispatch()
        self.f.work(self.info, "claude")
        protocol = self.f.callback(self.co, "review")
        receipt = Path(protocol["callback"]["receipt"]["path"])
        self.runtime.saved_callback = json_ref(protocol["callback"]["receipt"])
        receipt.unlink()
        original = Path(protocol["callback"]["attempts"][0]["path"])
        (original.parent / "delivery.lock").touch()
        self.transport.callback()
        _, transcript, line = self.runtime.callback_settlements[0]
        self.assertEqual(transcript, str(self.f.clients["codex"].path))
        raw = Path(transcript).read_text().splitlines()[line - 1]
        self.assertIn(read_json(self.co.root / "nodes/review/preflight/task-pack.json")["completion_callback"], raw)
        self.assertEqual(len(self.runtime.callback_settlements), 1)
        self.transport.callback()
        self.assertEqual(len(self.runtime.callback_settlements), 1)

    def test_only_accepted_terminal_disarms_and_duplicate_call_is_read_only(self):
        with self.assertRaisesRegex(ContractError, "verified terminal"):
            self.transport.disarm()
        self.transport.prepare()
        self.transport.dispatch()
        self.f.work(self.info, "claude")
        protocol = self.f.callback(self.co, "review")
        self.f.accept(self.co, "review", self.f.candidate(self.co, "review", protocol))
        pin = self.transport.disarm()
        self.assertEqual(self.transport.disarm(), pin)
        self.assertEqual(self.runtime.disarms, [self.info["task_id"]])

    def test_duplicate_native_ack_and_omitted_original_attempt_are_rejected(self):
        proof = self.transport.prepare()
        ack = json_ref(proof["handshake"]["receipt"])["ack_line"]
        self.f.clients["claude"].append(self.f.clients["claude"].message("assistant", ack, at=utc_now()))
        with self.assertRaisesRegex(ContractError, "one exact"):
            exact_native(self.f.clients["claude"].reader, ack, self.info["native_start"]["offset"], "assistant")
        path = self.runtime.handshake_root / "attempt-0001.json"
        path.rename(path.with_name("attempt-0002.json"))
        with self.assertRaisesRegex(ContractError, "contiguous"):
            journal_bundle(self.runtime.handshake_root)


if __name__ == "__main__":
    unittest.main()
