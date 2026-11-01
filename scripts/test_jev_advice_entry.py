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

    def run_handle(self, requests, results, state=None):
        state, replies = {} if state is None else state, []
        with self.patched(results):
            for request in requests:
                replies.append(run.handle(lambda r: "co:" + r, request, connection="conn-1", credential="key-1",
                                          state=state))
        return replies

    def patched(self, results):
        """Fake record functions: return the next result, or raise it as the real ones raise STOP."""
        from contextlib import ExitStack
        from unittest.mock import patch

        def fake(kind):
            def record(co, node, *a, **k):
                self.calls.append((kind, k["connection"], k["credential"]))
                result = results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            return record
        stack = ExitStack()
        stack.enter_context(patch.object(run, "record_advice", fake("advice")))
        stack.enter_context(patch.object(run, "record_inbox_order", fake("inbox")))
        return stack

    def test_dp1_and_dp2_reuse_the_same_connection_and_credential(self):
        replies = self.run_handle([self.request("advice"), self.request("inbox", event="e1")],
                                  [dict(status="RECORDED"), dict(status="RECORDED")])
        self.assertEqual([r["status"] for r in replies], ["RECORDED", "RECORDED"])
        self.assertEqual(self.calls, [("advice", "conn-1", "key-1"), ("inbox", "conn-1", "key-1")])

    def test_raised_ledger_stop_latches_without_another_call(self):
        state = {}
        with self.assertRaisesRegex(ContractError, run.STOP_REASON):
            self.run_handle([self.request("advice")], [ContractError(run.STOP_REASON)], state=state)
        replies = self.run_handle([self.request("inbox", event="e1")], [], state=state)
        self.assertEqual((replies[0]["status"], replies[0]["model_requests"]), ("STOPPED", 0))
        self.assertEqual(len(self.calls), 1)

    def test_other_refusals_do_not_latch(self):
        state = {}
        with self.assertRaises(ContractError):
            self.run_handle([self.request("advice")], [ContractError("DECISION_ALREADY_RECORDED")], state=state)
        self.assertEqual(self.run_handle([self.request("advice")], [dict(status="RECORDED")], state=state),
                         [dict(status="RECORDED")])

    def test_other_campaign_run_or_transport_is_refused_before_any_call(self):
        from unittest.mock import patch
        for key, value in (("campaign_sha256", "f" * 64), ("run", "pilot-02")):
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, "another campaign or run"):
                self.run_handle([self.request("advice", campaign_sha256="a" * 64) | {key: value}],
                                [], state=dict(campaign_sha256="a" * 64, run="pilot-01"))
        with patch("jev_registration.load", return_value=dict(transport_source=dict(sha256="b" * 64))), \
                self.assertRaisesRegex(ContractError, "another transport"):
            self.run_handle([self.request("advice")], [], state=dict(transport_source=dict(sha256="c" * 64)))
        self.assertEqual(self.calls, [])

    def test_registration_hash_mismatch_or_unknown_op_is_refused_before_any_call(self):
        bad = dict(self.request("advice"), registration_sha256="0" * 64)
        for request in (bad, self.request("other")):
            with self.subTest(op=request["op"]), self.assertRaises(ContractError):
                self.run_handle([request], [])
        self.assertEqual(self.calls, [])


class SocketServeTests(unittest.TestCase):
    """R548 P2: the run's worker on a private AF_UNIX socket; requests carry no key."""

    def setUp(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="jw-", dir="/tmp")))
        self.addCleanup(lambda: [p.unlink() for p in self.root.iterdir()] and None or self.root.rmdir())
        reg = self.root / "reg.json"
        reg.write_text("{}")
        self.reg = file_ref(reg)

    def request(self, op="advice", **extra):
        return dict(op=op, campaign_sha256="a" * 64, run="pilot-01", node="inventory",
                    registration=self.reg["path"], registration_sha256=self.reg["sha256"], **extra)

    def test_roundtrip_stop_latch_releases_the_connection_and_close_ends_the_worker(self):
        import gc
        import threading
        import weakref
        from unittest.mock import patch

        class Connection:
            pass
        seen, results, refs, served = [], [dict(status="RECORDED"), ContractError(run.STOP_REASON)], [], []

        def record(co, node, *a, **k):
            seen.append((co, k["credential"]))
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        def make():
            connection = Connection()
            refs.append(weakref.ref(connection))
            return connection
        path = self.root / "w.sock"
        listener = run.bind_private_socket(path)
        self.addCleanup(listener.close)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        worker = threading.Thread(target=lambda: served.append(run.serve(
            listener, lambda r: "co:" + r, connection=make(), credential="key-1",
            bound=dict(campaign_sha256="a" * 64, run="pilot-01"))))
        with patch.object(run, "record_advice", record), patch.object(run, "record_inbox_order", record):
            worker.start()
            replies = [run.ask(path, self.request()), run.ask(path, self.request("inbox", event="e1")),
                       run.ask(path, self.request("inbox", event="e2"))]
            gc.collect()
            released = refs[0]() is None
            replies.append(run.ask(path, dict(op="close")))
            worker.join(10)
        self.assertEqual([r["status"] for r in replies], ["RECORDED", "REJECTED", "STOPPED", "CLOSED"])
        self.assertEqual(replies[1]["error"], run.STOP_REASON)
        self.assertEqual(seen, [("co:pilot-01", "key-1")] * 2)
        self.assertTrue(released)
        self.assertEqual(served, [4])

    def test_bind_refuses_an_existing_path_or_a_shared_directory(self):
        (self.root / "taken").write_text("")
        with self.assertRaisesRegex(ContractError, "already exists"):
            run.bind_private_socket(self.root / "taken")
        os.chmod(self.root, 0o755)
        self.addCleanup(os.chmod, self.root, 0o700)
        with self.assertRaisesRegex(ContractError, "private directory"):
            run.bind_private_socket(self.root / "w.sock")
        self.assertFalse((self.root / "w.sock").exists())


class SupervisorJevStepsTests(unittest.TestCase):
    """R548 P2: the frozen supervisor instructions name when DP1/DP2 are called, per arm."""

    def setUp(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="jevsup-")))
        self.addCleanup(__import__("shutil").rmtree, self.root)
        self.campaign = dict(semantic_input={"offline_fixture": True}, registration={"offline_fixture": True},
                             session_group="fixture", plan={"offline_fixture": True}, root=str(self.root),
                             python={"path": "/usr/bin/python3"}, adapter={"path": "/frozen/adapter"})
        self.ref = dict(path=str(self.root / "campaign.json"), sha256="c" * 64)

    def steps(self, advisory, campaign=None):
        from unittest.mock import patch
        import upgrade_supervisor_instructions as sup
        with patch("orchestration_binding.unbound_nodes", return_value=["inventory", "merge"]), \
                patch("orchestration_planner.validate_plan", side_effect=lambda plan: plan), \
                patch("orchestration_common.json_ref", side_effect=lambda ref, *a: ref), \
                patch("upgrade_benchmark.validate_registration",
                      return_value={"groups": {"fixture": {"advisory": advisory}}}):
            return sup.jev_steps(self.campaign if campaign is None else campaign, self.ref)

    def test_non_s2_campaign_adds_nothing(self):
        self.assertEqual(self.steps("advisory-on", campaign={}), "")

    def test_off_arm_names_both_points_and_no_registration_or_socket(self):
        text = self.steps("advisory-off")
        for needle in ("DP1: before your publish-decision", "(inventory, merge)",
                       "--node inventory --inbox-event semantic-RUN", "/frozen/adapter/scripts/jev_advice_run.py",
                       "read_requests command once, in the returned order", "never repeat the call"):
            self.assertIn(needle, text)
        self.assertNotIn("--registration", text)
        self.assertNotIn("--worker-socket", text)
        dp1 = text[text.index("DP1:"):text.index("DP2:")]
        self.assertIn("--advice-sha256 null and --action supervisor_review, then bind-unbound", dp1)
        for word in ("read_command", "advice body", "advice_sha256 and"):
            self.assertNotIn(word, dp1)

    def test_on_arm_pins_the_frozen_index_and_never_a_key(self):
        from upgrade_semantic_inputs import ADVICE_INDEX_PATH
        with self.assertRaises((OSError, ContractError)):
            self.steps("advisory-on")
        index = self.root / ADVICE_INDEX_PATH
        index.parent.mkdir()
        index.write_text("{}")
        text = self.steps("advisory-on")
        self.assertIn(file_ref(index)["sha256"], text)
        self.assertEqual(text.count("--registration REG --registration-sha256 REG_SHA256 --worker-socket SOCKET"), 2)
        self.assertNotIn("--credential-fd", text)
        dp1 = text[text.index("DP1:"):text.index("DP2:")]
        read, publish = dp1.index("execute its returned read_command exactly once"), dp1.index("Only then publish-decision")
        self.assertLess(read, publish)
        self.assertIn("advice_sha256 and --action accepted, rejected or supervisor_review", dp1)
        self.assertIn("never publish a decision citing advice you did not read", dp1)
        self.assertNotIn("--advice-sha256 null", dp1)


class AdviceReadCommandTests(unittest.TestCase):
    """R556: the on result carries the frozen reader command for its real published notice."""

    def test_command_equals_the_bind_gate_reader_for_a_published_notice(self):
        import json
        from upgrade_advice_publish import publish_advice
        from upgrade_body_read import read_command
        root = Path(os.path.realpath(tempfile.mkdtemp(prefix="jevread-")))
        self.addCleanup(__import__("shutil").rmtree, root)
        body = json.dumps(dict(marker="JEV_ADVICE_r1_n1_X", run_id="r1", node_id="n1",
                               advice={"status": "RESPONDED", "text": "x" * 4000}))
        notice = publish_advice(root, "r1", "n1", body)["notice"]
        campaign = dict(python={"path": "/usr/bin/python3"}, controller={"path": "/frozen/controller"})
        self.assertEqual(run.advice_read_command(campaign, notice),
                         read_command("/usr/bin/python3", "/frozen/controller", notice))
        self.assertIn("/frozen/controller/scripts/mac_upgrade_body_read.py --notice", run.advice_read_command(campaign, notice))


class DP1PairedArmTests(unittest.TestCase):
    """R548 P2: DP1 advice in an S2 campaign uses exactly its paired group's advisory arm."""

    def setUp(self):
        from types import SimpleNamespace
        self.co = SimpleNamespace(root=Path("/nonexistent-dp1-fixture"), spec={"run_id": "warm-01"},
                                  nodes={"inventory": {}},
                                  campaign=dict(semantic_input={"offline_fixture": True},
                                                registration={"offline_fixture": True}, session_group="fixture"))

    def advise(self, group_arm, reg_arm):
        from unittest.mock import patch
        with patch("jev_registration.load", return_value=dict(arm=reg_arm)), \
                patch("upgrade_benchmark.validate_registration",
                      return_value={"groups": {"fixture": {"advisory": group_arm}}}), \
                patch.object(run, "load_transport", side_effect=AssertionError("transport loaded")):
            return run.record_advice(self.co, "inventory")

    def test_other_arm_is_refused_before_any_transport(self):
        for group_arm, reg_arm in (("advisory-off", "advisory-on"), ("advisory-on", "advisory-off")):
            with self.subTest(group=group_arm), self.assertRaisesRegex(ContractError, "DP1 advice arm differs"):
                self.advise(group_arm, reg_arm)

    def test_matching_arm_passes_the_check(self):
        # Past the arm check the fixture has no frozen plan; that later failure is expected.
        with self.assertRaises(AttributeError):
            self.advise("advisory-off", "advisory-off")


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
