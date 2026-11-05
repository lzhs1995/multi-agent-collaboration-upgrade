"""Bootstrap integration using real socket/journal boundaries and fixture RPC.

No model, client or production process is started by these tests. The fixture
emulates server persistence so failures can be checked before any new run.
"""
import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from orchestration_common import ContractError, json_ref, object_sha, write_json
from upgrade_rpc_peer import server_pin
from upgrade_task_operations import OperationLedger
from upgrade_task_startup import PLAN_SCHEMA, bootstrap_task
from upgrade_task_thread import journal_evidence

SID = "11111111-1111-4111-8111-111111111111"
TURN = "22222222-2222-4222-8222-222222222222"


class TaskStartupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ts", dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("native/sessions", "work", "rpc"):
            (self.root / name).mkdir(parents=True)
        self.path = self.root / "native/sessions/original.jsonl"
        self.server_row = dict(pid=os.getpid(), birth=[1234, 567], executable="/fixture/codex",
            file_identity=[1, 2, 3, 4], argv=["/fixture/codex", "app-server"])
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        endpoint = self.root / "s"
        listener.bind(str(endpoint)); endpoint.chmod(0o600); listener.listen(1)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close); sock.connect(str(endpoint))
        peer, _ = listener.accept(); self.addCleanup(peer.close)
        st = endpoint.stat()
        self.calls = []
        self.persist = True
        self.thread_source = "user"
        self.user_turn = TURN
        self.lose_response = None
        self.foreign_peer = False
        outer = self

        class Client:
            def __init__(self):
                self.sock = sock
                self.server = server_pin(outer.server_row, uid=os.getuid())
                self.process = lambda pid: copy.deepcopy(outer.server_row)
                self.socket_identity = dict(path=str(endpoint), device=st.st_dev, inode=st.st_ino,
                    uid=os.getuid(), mode=0o600, peer_uid=os.getuid())
                self.pending = [dict(method="item/tool/call", params={"unprocessed": True})]
                self.i = 0
                self.initialize_exchange = self.saved("initialize", dict(
                    clientInfo=dict(name="cmux_native_input"), capabilities=dict(experimentalApi=True)),
                    dict(codexHome=str(outer.root / "native"), userAgent="codex-tui/fixture"))

            def saved(self, method, params, result):
                self.i += 1
                proof = dict(schema="upgrade-task-rpc-peer-v1", server=self.server,
                    peer=dict(pid=os.getpid(), uid=os.getuid()))
                if outer.foreign_peer:
                    proof = copy.deepcopy(proof); proof["server"]["birth"] = [3, 4]
                return {"intent": write_json(outer.root / f"rpc/{self.i}-intent.json", dict(
                    direction="send_intent", message=dict(id=self.i, method=method, params=params), peer=proof)),
                    "response": write_json(outer.root / f"rpc/{self.i}-response.json", dict(
                    direction="received", message=dict(id=self.i, result=result), peer=proof))}

            def request(self, method, params):
                outer.calls.append((method, copy.deepcopy(params)))
                if method == "thread/start":
                    result = dict(thread=dict(id=SID, path=str(outer.path), originator="codex-tui"))
                elif method == "turn/start":
                    if outer.persist:
                        rows = [{"type":"session_meta", "payload":dict(id=SID, source="vscode",
                            thread_source=outer.thread_source, originator="codex-tui")},
                            {"type":"response_item", "payload":dict(type="message", role="user",
                                content=[dict(type="input_text", text=params["input"][0]["text"])],
                                internal_chat_message_metadata_passthrough=dict(turn_id=outer.user_turn))}]
                        outer.path.write_bytes(b"".join((json.dumps(x)+"\n").encode() for x in rows))
                        outer.path.chmod(0o600)
                    result = dict(turn=dict(id=TURN))
                else:
                    raise AssertionError(method)
                if outer.lose_response == method:
                    raise TimeoutError("Response lost after accepted request")
                self.last_exchange = self.saved(method, params, result)
                return result

        self.client = Client()
        authority = write_json(self.root / "authority.json", {"fixture": True})
        self.plan = dict(schema=PLAN_SCHEMA, task_id="startup-fixture", controller_id="fixture",
            authority_ref=authority, native_root=str(self.root / "native"), work_root=str(self.root / "work"),
            model="fixture-model", model_provider="custom", startup_text="Original task instruction",
            base_instructions="Fixture only", operations={"ready":dict(kind="readiness", payload={})})
        self.plan_ref = write_json(self.root / "plan.json", self.plan)
        self.directory = self.root / "attempt"

    def start(self):
        return bootstrap_task(self.client, self.plan_ref, self.directory, journal_timeout=0.01)

    def test_bootstrap_binds_exact_exchange_and_leaves_requests_unexecuted(self):
        result = json_ref(self.start())
        self.assertEqual([x[0] for x in self.calls], ["thread/start", "turn/start"])
        self.assertEqual(self.calls[0][1]["threadSource"], "user")
        self.assertEqual(result["thread_id"], SID)
        self.assertFalse(result["adoption_proven"])
        anchor = json_ref(result["anchor"])
        self.assertEqual(journal_evidence(anchor)["startup_turn"], TURN)
        registration = json_ref(result["operations"])
        ledger = OperationLedger(result["ledger"], object_sha(registration))
        self.addCleanup(ledger.close)
        self.assertEqual(ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)
        self.assertEqual(len(self.client.pending), 1)
        with self.assertRaises(ContractError):
            self.start()
        self.assertEqual(len(self.calls), 2)

    def test_lost_mutation_response_consumes_attempt_and_never_retries(self):
        self.lose_response = "thread/start"
        for _ in range(2):
            with self.assertRaises(ContractError):
                self.start()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads((self.directory / "unknown.json").read_text())["status"], "UNKNOWN_NO_REPLAY")

    def test_missing_journal_never_produces_attach_or_adoption_result(self):
        self.persist = False
        with self.assertRaisesRegex(ContractError, "JOURNAL_TIMEOUT"):
            self.start()
        self.assertFalse((self.directory / "result.json").exists())
        self.assertTrue((self.directory / "operations.sqlite").is_file())

    def test_child_or_null_source_and_other_turn_never_enroll(self):
        self.thread_source = None
        with self.assertRaisesRegex(ContractError, "ORIGINAL_USER_SESSION_REQUIRED"):
            self.start()
        self.assertFalse((self.directory / "result.json").exists())

    def test_wrong_turn_is_not_recovered_from_matching_prompt(self):
        self.user_turn = "33333333-3333-4333-8333-333333333333"
        with self.assertRaisesRegex(ContractError, "TURN_CHANGED"):
            self.start()

    def test_invalid_operation_is_rejected_before_rpc(self):
        self.plan["operations"] = {"unregistered/path":dict(kind="shell", payload={})}
        self.plan_ref = write_json(self.root / "bad-plan.json", self.plan)
        with self.assertRaises(ContractError):
            self.start()
        self.assertEqual(self.calls, [])

    def test_recorded_foreign_peer_is_rejected_before_turn(self):
        self.foreign_peer = True
        with self.assertRaisesRegex(ContractError, "RPC_PEER_CHANGED"):
            self.start()
        self.assertEqual([x[0] for x in self.calls], ["thread/start"])

    def test_helper_first_cannot_create_task_or_attempt(self):
        self.client.initialize_exchange = self.client.saved("initialize", dict(
            clientInfo=dict(name="cmux_native_input"), capabilities=dict(experimentalApi=True)),
            dict(codexHome=str(self.root / "native"), userAgent="cmux_native_input/fixture"))
        with self.assertRaisesRegex(ContractError, "REAL_TUI_FIRST_REQUIRED"):
            self.start()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.directory.exists())


if __name__ == "__main__":
    unittest.main()
