"""Real local socket/file boundaries with fixture RPC replies and processes."""
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

from orchestration_common import ContractError, digest
from upgrade_rpc_peer import server_pin
from upgrade_task_thread import observe, parse_descriptor_output

SID = "11111111-1111-4111-8111-111111111111"
TURN = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


@unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "kernel peer")
class TaskThreadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tt", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "sessions" / "explicit.jsonl"
        self.path.parent.mkdir()
        self.records = [
            {"type": "session_meta", "payload": {"id": SID, "source": "cli", "thread_source": "user"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Original nonce 914a startup"}],
                "internal_chat_message_metadata_passthrough": {"turn_id": TURN}}},
        ]
        self.lines = [(json.dumps(r) + "\n").encode() for r in self.records]
        self.path.write_bytes(b"".join(self.lines))
        self.path.chmod(0o600)
        info = self.path.stat()
        journal = {"path": str(self.path), "device": info.st_dev, "inode": info.st_ino}
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.listener.close)
        self.socket_path = self.root / "s"
        self.listener.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        self.listener.listen(1)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        sock.connect(str(self.socket_path))
        accepted, _ = self.listener.accept()
        self.addCleanup(accepted.close)
        st = self.socket_path.stat()
        self.row = dict(pid=os.getpid(), birth=[1234, 567], executable="/fixture/codex",
                        file_identity=[1, 2, 3, 4], argv=["/fixture/codex", "app-server"])
        self.anchor = {"schema": "upgrade-task-thread-read-anchor-v1", "thread_id": SID,
            "native_root": str(self.root), "journal": journal,
            "metadata": {**journal, "offset": 0, "length": len(self.lines[0]),
                         "sha256": digest(self.lines[0])},
            "startup_input": {**journal, "offset": len(self.lines[0]),
                              "length": len(self.lines[1]), "sha256": digest(self.lines[1])},
            "startup_text": "Original nonce 914a startup",
            "server": server_pin(self.row, uid=os.getuid()),
            "socket": dict(path=str(self.socket_path), device=st.st_dev, inode=st.st_ino,
                           uid=os.getuid(), mode=0o600, peer_uid=os.getuid())}
        self.thread = {"id": SID, "path": str(self.path), "canAcceptDirectInput": True,
                       "status": {"type": "idle"}}
        self.turns = [{"id": TURN, "status": "completed"}]
        self.calls = []
        outer = self

        class Client:
            socket_identity = copy.deepcopy(outer.anchor["socket"])

            def request(self, method, params):
                outer.calls.append((method, params))
                outer.assertEqual(params["threadId"], SID)
                if method == "thread/read":
                    return {"thread": outer.thread}
                if method == "thread/turns/list":
                    return {"data": outer.turns}
                raise AssertionError("Mutation or unexpected read: " + method)

        self.client = Client()
        self.client.sock = sock

    def descriptors(self, pid, path, device, inode):
        return {"fd": 38, "access": "u", "device": device, "inode": inode, "path": path}

    def run_observation(self, anchor=None, descriptors=None, process=None):
        return observe(self.client, anchor or self.anchor, process or (lambda pid: dict(self.row)),
                       descriptors=descriptors or self.descriptors)

    def test_idle_and_active_read_proofs_do_not_confer_any_authority(self):
        for status, turn_status, method in [("idle", "completed", "turn/start"),
                                          ("active", "inProgress", "turn/steer")]:
            self.thread["status"]["type"] = status
            self.turns[0]["status"] = turn_status
            result = self.run_observation()
            self.assertEqual(result["observed_input_method"], method)
            self.assertEqual(result["input_operations"], 0)
            for key in ("foreground_authentication", "caller_authentication", "enrollment_proven"):
                self.assertIs(result[key], False)
        self.assertEqual([m for m, _ in self.calls], ["thread/read", "thread/turns/list"] * 2)

    def test_unknown_or_inconsistent_turn_state_grants_no_input_method(self):
        self.thread["status"]["type"] = "active"
        self.assertIsNone(self.run_observation()["observed_input_method"])
        self.thread["status"]["type"] = "futureState"
        self.assertIsNone(self.run_observation()["observed_input_method"])

    def test_foreign_thread_and_missing_or_false_capability_refuse(self):
        for key, value in [("id", OTHER), ("path", str(self.root / "other")),
                           ("canAcceptDirectInput", None), ("canAcceptDirectInput", False),
                           ("canAcceptDirectInput", 1)]:
            original = self.thread.copy()
            with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                self.thread[key] = value
                self.run_observation()
            self.thread = original

    def test_prompt_and_session_spoofs_fail_before_rpc(self):
        for key, value in [("startup_text", "copied other task"), ("thread_id", OTHER),
                           ("schema", "legacy-foreground"), ("native_root", str(self.root / "other"))]:
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.run_observation(dict(self.anchor, **{key: value}))
        self.assertEqual(self.calls, [])

    def test_remote_source_is_metadata_and_subagent_never_becomes_user(self):
        for source, kind, accepted in [("vscode", "user", True), ("cli", "user", True),
                                       ("vscode", "subagent", False), ("unknown", "user", False)]:
            self.records[0]["payload"].update(source=source, thread_source=kind)
            lines = [(json.dumps(r) + "\n").encode() for r in self.records]
            self.path.write_bytes(b"".join(lines))
            anchor = copy.deepcopy(self.anchor)
            anchor["metadata"].update(length=len(lines[0]), sha256=digest(lines[0]))
            anchor["startup_input"]["offset"] = len(lines[0])
            with self.subTest(source=source, kind=kind):
                if accepted:
                    self.assertEqual(self.run_observation(anchor)["journal"]["session_source"], source)
                else:
                    with self.assertRaisesRegex(ContractError, "ORIGINAL_USER_SESSION_REQUIRED"):
                        self.run_observation(anchor)

    def test_replaced_journal_and_modified_native_record_refuse(self):
        old = self.path.with_suffix(".old")
        self.path.rename(old)
        self.path.write_bytes(old.read_bytes())
        with self.assertRaisesRegex(ContractError, "JOURNAL_IDENTITY_CHANGED"):
            self.run_observation()
        self.path.unlink()
        old.rename(self.path)
        self.path.write_bytes(b"X" + b"".join(self.lines)[1:])
        with self.assertRaisesRegex(ContractError, "RECORD_CHANGED"):
            self.run_observation()
        self.assertEqual(self.calls, [])

    def test_changed_descriptor_and_server_after_rpc_refuse(self):
        count = 0

        def changing(*args):
            nonlocal count
            count += 1
            return dict(self.descriptors(*args), fd=38 if count == 1 else 39)

        with self.assertRaisesRegex(ContractError, "CHANGED_DURING_OBSERVATION"):
            self.run_observation(descriptors=changing)
        reads = 0

        def changed_process(pid):
            nonlocal reads
            reads += 1
            return dict(self.row, birth=[1234, 567 if reads <= 2 else 568])

        with self.assertRaisesRegex(ContractError, "SERVER_INSTANCE_CHANGED"):
            self.run_observation(process=changed_process)

    def test_socket_path_replaced_or_untrusted_mode_refuse(self):
        self.socket_path.chmod(0o666)
        with self.assertRaisesRegex(ContractError, "SOCKET_IDENTITY_CHANGED"):
            self.run_observation()
        self.socket_path.chmod(0o600)
        self.socket_path.rename(self.root / "old-s")
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(replacement.close)
        replacement.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        with self.assertRaisesRegex(ContractError, "SOCKET_IDENTITY_CHANGED"):
            self.run_observation()
        self.assertEqual(self.calls, [])

    def test_journal_append_is_allowed_but_does_not_reselect_thread(self):
        request = self.client.request

        def appending(method, params):
            with self.path.open("ab") as f:
                f.write(b'{"type":"event_msg","payload":{}}\n')
            return request(method, params)

        self.client.request = appending
        self.assertEqual(self.run_observation()["thread_id"], SID)

    def test_descriptor_selection_ignores_unrelated_writers_but_rejects_aliases(self):
        info = self.path.stat()
        foreign = "f40\nau\ntREG\nD0x1\ni99\nn/other/rollout-foreign.jsonl\n"
        exact = f"f38\nau\ntREG\nD{hex(info.st_dev)}\ni{info.st_ino}\nn{self.path}\n"
        args = (os.getpid(), self.path, info.st_dev, info.st_ino)
        result = parse_descriptor_output(f"p{os.getpid()}\n" + foreign + exact, *args)
        self.assertEqual(result["fd"], 38)
        for tail in (foreign, exact + exact.replace("f38", "f39"),
                     exact.replace("au", "ar"), exact.replace(f"i{info.st_ino}", "i77"),
                     exact.replace(f"D{hex(info.st_dev)}", "D0x1")):
            with self.subTest(tail=tail), self.assertRaises(ContractError):
                parse_descriptor_output(f"p{os.getpid()}\n" + tail, *args)


if __name__ == "__main__":
    unittest.main()
