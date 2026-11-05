"""Operation effects, lost replies, stop and native read-only recovery."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, write_json
from upgrade_task_actor import TaskActor
from upgrade_task_operations import OperationLedger

SID = "11111111-1111-4111-8111-111111111111"
TURN = "22222222-2222-4222-8222-222222222222"


class ActorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve(); self.root.chmod(0o700)
        registration = dict(schema="upgrade-task-operations-v1", task_id="test", thread_id=SID,
            turn_id=TURN, controller_id="original", authority_ref=write_json(self.root / "auth.json", {"fixture":True}),
            operations={"dispatch-1":dict(kind="dispatch",payload={"target":"exact-enrolled-executor"})})
        self.ledger = OperationLedger.create(self.root / "ledger.sqlite", registration)
        self.addCleanup(self.ledger.close)
        self.request = dict(id=77, method="item/tool/call", params=dict(threadId=SID,turnId=TURN,
            callId="call-1",namespace="upgrade_collaboration",tool="execute",arguments={"operation_id":"dispatch-1"}))
        self.effects, self.gates = [], []
        self.result = dict(success=True,contentItems=[dict(type="inputText",text="exact-result")])
        outer = self

        class Client:
            server={"pid":os.getpid(),"uid":os.getuid(),"fixture":True}
            sock=object()
            process=None
            def __init__(self): self.queue=[]; self.sent=[]; self.fail_send=False
            def receive_recorded(self, timeout): return self.queue.pop(0)
            def respond(self, value):
                self.sent.append(value)
                if self.fail_send: raise TimeoutError("lost response outcome")

        self.client=Client()
        checker=patch("upgrade_task_actor.verify_peer",return_value={"fixture":True})
        checker.start();self.addCleanup(checker.stop)

        def handler(payload):
            self.effects.append(payload)
            return self.result,write_json(self.root / "receipt.json",dict(payload=payload),exclusive=True)

        def gate(reg, request, ref):
            self.gates.append(request)

        self.actor=TaskActor(self.client,self.ledger,{"dispatch":handler},gate,self.root / "events")

    def enqueue(self, message):
        ref=write_json(self.root / ("rpc-"+str(len(list(self.root.glob("rpc-*"))))+".json"),
            dict(direction="received",message=message,peer=dict(server=self.client.server,
                 peer={k:self.client.server[k] for k in ("pid","uid")})),exclusive=True)
        self.client.queue.append((message,ref))
        return ref

    def completion(self):
        return dict(method="item/completed",params=dict(threadId=SID,turnId=TURN,item=dict(
            id="call-1",type="dynamicToolCall",namespace="upgrade_collaboration",tool="execute",
            arguments={"operation_id":"dispatch-1"},status="completed",**self.result)))

    def test_one_effect_then_exact_native_completion(self):
        self.enqueue(self.request)
        self.assertEqual(self.actor.step()["phase"],"RESPONSE_INTENDED")
        self.enqueue(self.completion())
        self.assertEqual(self.actor.step()["phase"],"NATIVE_COMPLETED")
        self.assertEqual(len(self.effects),1)
        self.assertEqual(len(self.gates),2)
        self.assertEqual(len(self.client.sent),1)
        self.enqueue(self.request)
        with self.assertRaisesRegex(ContractError,"ALREADY_INTENDED"):
            self.actor.step()
        self.assertEqual(len(self.effects),1)

    def test_unknown_send_recovers_original_completion_without_rerun(self):
        self.client.fail_send=True;self.enqueue(self.request)
        with self.assertRaises(TimeoutError):self.actor.step()
        self.assertEqual(self.ledger.inspect("call-1")["phase"],"UNKNOWN")
        with self.assertRaisesRegex(ContractError,"CLOSED_NO_REPLAY"):self.actor.step()
        completion=self.completion();ref=self.enqueue(completion)
        self.assertEqual(self.actor.recover_completion(completion,ref)["phase"],"NATIVE_COMPLETED")
        self.assertEqual(len(self.effects),1)
        self.assertEqual(len(self.client.sent),1)

    def test_gate_failure_before_intent_has_zero_effects(self):
        def refuse(*args):raise ContractError("task revoked")
        self.actor.gate=refuse;self.enqueue(self.request)
        with self.assertRaisesRegex(ContractError,"revoked"):self.actor.step()
        self.assertEqual(self.effects,[])
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],0)

    def test_stop_after_effect_before_response_preserves_unknown(self):
        def gate(*args):
            if self.effects:raise ContractError("revoked after operation")
        self.actor.gate=gate;self.enqueue(self.request)
        with self.assertRaisesRegex(ContractError,"revoked"):self.actor.step()
        self.assertEqual(len(self.effects),1)
        self.assertEqual(self.client.sent,[])
        self.assertEqual(self.ledger.inspect("call-1")["phase"],"UNKNOWN")

    def test_other_turn_never_executes(self):
        request=copy.deepcopy(self.request);request["params"]["turnId"]=SID
        self.enqueue(request)
        with self.assertRaisesRegex(ContractError,"FOREIGN_TURN"):self.actor.step()
        self.assertEqual(self.effects,[])

    def test_cancelled_call_does_not_become_success_on_late_native_result(self):
        self.enqueue(self.request);self.actor.step();self.actor.close("CANCELLED")
        completion=self.completion();ref=self.enqueue(completion)
        self.assertEqual(self.actor.recover_completion(completion,ref)["phase"],"CONFLICT")
        self.assertEqual(len(self.effects),1)


if __name__ == "__main__":unittest.main()
