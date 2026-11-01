"""R545 P2 on-arm entry pieces: in-process key from an inherited fd, connection from pinned bytes."""
import os
import tempfile
import unittest
from pathlib import Path

import jev_advice_run as run
from orchestration_common import ContractError, file_ref


class CredentialFdTests(unittest.TestCase):
    def pipe_with(self, data):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, data)
        os.close(write_fd)
        return read_fd

    def test_key_is_read_once_and_the_fd_is_closed(self):
        fd = self.pipe_with(b"sk-test-offline\n")
        self.assertEqual(run.read_credential(fd), "sk-test-offline")
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_regular_file_fd_is_refused_unread_and_closed(self):
        handle = tempfile.TemporaryFile()
        handle.write(b"sk-in-a-regular-file\n")
        handle.flush()
        fd = os.dup(handle.fileno())
        handle.close()
        with self.assertRaisesRegex(ContractError, "pipe or FIFO"):
            run.read_credential(fd)
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_empty_or_multi_token_input_is_refused(self):
        for data in (b"", b"\n", b"two keys\n"):
            with self.subTest(data=data), self.assertRaises(ContractError):
                run.read_credential(self.pipe_with(data))


class SenderDutiesTests(unittest.TestCase):
    """R545 P1: the frozen duties list each run's pinned send commands in registered order."""

    def test_commands_grouped_by_run_in_order_and_boundary_fixed(self):
        from unittest.mock import patch
        import upgrade_semantic_inputs as sem
        slots = [dict(run_id="pilot-01", command="c1"), dict(run_id="pilot-01", command="c2"),
                 dict(run_id="pilot-02", command="c3")]
        campaign = dict(runs=[dict(run_id="pilot-01"), dict(run_id="pilot-02"), dict(run_id="pilot-03")])
        with patch.object(sem, "campaign_slots", return_value=slots):
            duties = sem.sender_duties(campaign)
        self.assertEqual(duties["runs"], {"pilot-01": ["c1", "c2"], "pilot-02": ["c3"], "pilot-03": []})
        self.assertEqual((duties["sender"], duties["receiver"], duties["boundary"]),
                         ("claude_a", "codex", sem.BOUNDARY))
        self.assertFalse(duties["ordinary_communication_denominator_changed"])
        self.assertEqual(sem.SENDER_DUTIES_PATH, "semantic/sender-duties.json")


class SemanticSenderStartupTests(unittest.TestCase):
    def test_opt_in_must_be_boolean_before_any_materialization(self):
        from upgrade_live_materialize import materialize_live
        for value in (None, 0, 1, "true"):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "SEMANTIC_SENDER_CHANGED"):
                materialize_live({}, "B", binaries={}, semantic_sender=value)


class WorkerHandleTests(unittest.TestCase):
    """R548 P2: one held connection/credential serves DP1 and DP2; a stop latches."""

    def setUp(self):
        root = Path(os.path.realpath(tempfile.mkdtemp(prefix="jevwork-")))
        self.addCleanup(lambda: [p.unlink() for p in root.iterdir()] and None or root.rmdir())
        reg = root / "reg.json"
        reg.write_text("{}")
        self.reg = file_ref(reg)
        self.calls = []

    def request(self, op, **extra):
        return dict(op=op, run="pilot-01", node="inventory", registration=self.reg["path"],
                    registration_sha256=self.reg["sha256"], **extra)

    def run_handle(self, requests, results):
        from unittest.mock import patch
        state, replies = {}, []
        fake = lambda kind: (lambda co, node, *a, **k: self.calls.append((kind, k["connection"], k["credential"]))
                             or results.pop(0))
        with patch.object(run, "record_advice", fake("advice")), patch.object(run, "record_inbox_order", fake("inbox")):
            for request in requests:
                replies.append(run.handle(lambda r: "co:" + r, request, connection="conn-1", credential="key-1", state=state))
        return replies

    def test_dp1_and_dp2_reuse_the_same_connection_and_credential(self):
        replies = self.run_handle([self.request("advice"), self.request("inbox", event="e1")],
                                  [dict(status="RECORDED"), dict(status="RECORDED")])
        self.assertEqual([r["status"] for r in replies], ["RECORDED", "RECORDED"])
        self.assertEqual(self.calls, [("advice", "conn-1", "key-1"), ("inbox", "conn-1", "key-1")])

    def test_auth_refusal_latches_stop_without_another_call(self):
        replies = self.run_handle([self.request("advice"), self.request("inbox", event="e1")],
                                  [dict(status="RECORDED", error="AUTH_REFUSED", stopped=True)])
        self.assertEqual(replies[1]["status"], "STOPPED")
        self.assertEqual(len(self.calls), 1)

    def test_registration_hash_mismatch_or_unknown_op_is_refused_before_any_call(self):
        bad = dict(self.request("advice"), registration_sha256="0" * 64)
        for request in (bad, self.request("other")):
            with self.subTest(op=request["op"]), self.assertRaises(ContractError):
                self.run_handle([request], [])
        self.assertEqual(self.calls, [])


class RegisteredConnectionTests(unittest.TestCase):
    def source(self, body):
        root = Path(os.path.realpath(tempfile.mkdtemp(prefix="jevconn-")))
        self.addCleanup(lambda: [p.unlink() for p in root.iterdir()] and None or root.rmdir())
        path = root / "transport.py"
        path.write_text(body)
        return file_ref(path)

    def test_connection_comes_from_the_pinned_bytes(self):
        ref = self.source("def connect():\n    return 'offline-connection'\n\ndef bind(c, k, *, model):\n    return c\n")
        self.assertEqual(run.registered_connection(ref), "offline-connection")

    def test_changed_bytes_or_missing_connect_is_refused(self):
        ref = self.source("def bind(c, k, *, model):\n    return c\n")
        with self.assertRaises(ContractError):
            run.registered_connection(ref)
        Path(ref["path"]).write_text("def connect():\n    return 'other'\n")
        with self.assertRaises(Exception):
            run.registered_connection(ref)


if __name__ == "__main__":
    unittest.main()
