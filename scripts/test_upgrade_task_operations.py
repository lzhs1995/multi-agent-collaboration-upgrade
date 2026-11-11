"""Durability, competing processes, cancellation and wrong native results."""
import copy
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from orchestration_common import ContractError, file_ref, object_sha
from upgrade_task_operations import OperationLedger, NAMESPACE, TOOL


def contender(path, sha, request, output):
    ledger = OperationLedger(path, sha)
    try:
        ledger.receive(request)
        output.put("intended")
    except ContractError:
        output.put("refused")
    finally:
        ledger.close()


def crash_after_effect(path, sha, request, sentinel):
    ledger = OperationLedger(path, sha)
    ledger.receive(request)
    Path(sentinel).write_text("external effect happened")
    os._exit(7)


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.root.chmod(0o700)
        self.thread, self.turn = str(uuid.uuid4()), str(uuid.uuid4())
        (self.root/"authority.json").write_text('{"test_fixture":true}')
        self.registration = dict(schema="upgrade-task-operations-v1", task_id="fixture", thread_id=self.thread, turn_id=self.turn,
            controller_id="fixture-controller", authority_ref=file_ref(self.root/"authority.json"),
            operations={"probe": {"kind": "record_probe", "payload": {"marker": "fixed-by-controller"}},
                        "other": {"kind": "record_probe", "payload": {"marker": "second"}}})
        self.path = self.root/"ledger.sqlite"
        self.ledger = OperationLedger.create(self.path, self.registration)
        self.addCleanup(self.ledger.close)
        self.request = dict(id=3, method="item/tool/call", params=dict(threadId=self.thread, turnId=self.turn,
            callId="call_1", namespace=NAMESPACE, tool=TOOL, arguments={"operation_id": "probe"}))
        self.result = dict(success=True, contentItems=[dict(type="inputText", text="unique result")])
        (self.root/"receipt.json").write_text('{"effect":"recorded"}')
        self.receipt = file_ref(self.root/"receipt.json")

    def ready_response(self):
        self.ledger.receive(self.request)
        self.ledger.operation_result("call_1", self.result, self.receipt)
        return self.ledger.response_intent("call_1")

    def completion(self, **changes):
        item = dict(type="dynamicToolCall", id="call_1", namespace=NAMESPACE, tool=TOOL,
                    arguments={"operation_id":"probe"}, status="completed", **self.result)
        item.update(changes)
        return dict(method="item/completed", params=dict(threadId=self.thread, turnId=self.turn, item=item))

    def finish(self, msg):
        (self.root/"completion.json").write_text(json.dumps(msg))
        return self.ledger.complete(msg, file_ref(self.root/"completion.json"))

    def test_complete_original_result_does_not_claim_model_consumption(self):
        self.assertEqual(self.ready_response(), {"id":3,"result":self.result})
        result = self.finish(self.completion())
        self.assertEqual(result["phase"], "NATIVE_COMPLETED")
        self.assertFalse(result["model_consumption_proven"])
        with self.assertRaises(ContractError): self.ledger.receive(self.request)
        with self.assertRaises(ContractError): self.ledger.response_intent("call_1")

    def test_foreign_child_wrong_tool_arbitrary_argument_and_unregistered_slot_refused(self):
        for patch in ({"threadId":str(uuid.uuid4())}, {"namespace":"codex_tui"},
                      {"tool":"shell"}, {"arguments":{"operation_id":"probe","cmd":"touch arbitrary"}},
                      {"arguments":{"operation_id":"unknown"}}, {"turnId":"bad"}, {"turnId":str(uuid.uuid4())}):
            req = copy.deepcopy(self.request); req["params"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ContractError): self.ledger.receive(req)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0], 0)

    def test_original_operation_reused_under_new_call_id_refused(self):
        self.ledger.receive(self.request)
        second = copy.deepcopy(self.request); second["id"]=4; second["params"]["callId"]="call_2"
        with self.assertRaisesRegex(ContractError,"NO_REPLAY"): self.ledger.receive(second)
        second["params"]["arguments"]["operation_id"]="other";second["id"]=3
        with self.assertRaisesRegex(ContractError,"NO_REPLAY"): self.ledger.receive(second)

    def test_two_processes_only_one_intent_wins(self):
        ctx = multiprocessing.get_context("spawn"); queue=ctx.Queue()
        children=[ctx.Process(target=contender,args=(self.path,object_sha(self.registration),self.request,queue)) for _ in range(2)]
        try:
            for child in children:child.start()
            for child in children:child.join(10);self.assertEqual(child.exitcode,0)
            self.assertEqual(sorted([queue.get(timeout=2),queue.get(timeout=2)]),["intended","refused"])
        finally:
            for child in children:
                if child.is_alive():child.terminate();child.join(5)
            queue.close();queue.join_thread()

    def test_crash_after_external_effect_does_not_reexecute_after_reopen(self):
        ctx=multiprocessing.get_context("spawn")
        sentinel=self.root/"effect.txt"
        child=ctx.Process(target=crash_after_effect,args=(self.path,object_sha(self.registration),self.request,sentinel))
        child.start();child.join(10)
        self.assertEqual(child.exitcode,7);self.assertTrue(sentinel.exists())
        reopened=OperationLedger(self.path,object_sha(self.registration))
        try:
            self.assertEqual(reopened.inspect("call_1")["phase"],"INTENDED")
            with self.assertRaisesRegex(ContractError,"NO_REPLAY"):reopened.receive(self.request)
            with self.assertRaises(ContractError):reopened.response_intent("call_1")
        finally:reopened.close()

    def test_unknown_response_is_not_resent_but_exact_native_result_can_resolve_it(self):
        self.ready_response();self.ledger.stop("call_1","UNKNOWN")
        with self.assertRaises(ContractError):self.ledger.response_intent("call_1")
        self.assertEqual(self.finish(self.completion())["phase"],"NATIVE_COMPLETED")

    def test_unknown_without_response_intent_cannot_claim_completion(self):
        self.ledger.receive(self.request);self.ledger.stop("call_1","UNKNOWN")
        with self.assertRaises(ContractError):self.finish(self.completion())
        self.assertEqual(self.ledger.inspect("call_1")["phase"],"UNKNOWN")

    def test_cancelled_operation_has_no_late_response_or_success_claim(self):
        self.ready_response();self.ledger.stop("call_1","CANCELLED")
        with self.assertRaises(ContractError):self.ledger.response_intent("call_1")
        self.assertEqual(self.finish(self.completion())["phase"],"CONFLICT")

    def test_other_responder_result_is_conflict_and_original_receipt_remains(self):
        self.ready_response()
        self.assertEqual(self.finish(self.completion(contentItems=[dict(type="inputText",text="foreign")]))["phase"],"CONFLICT")
        state=self.ledger.inspect("call_1")
        self.assertEqual(state["receipt"],self.receipt);self.assertEqual(state["result"],self.result)

    def test_mutated_receipt_refuses_send_and_wrong_registration_cannot_open(self):
        self.ledger.receive(self.request);self.ledger.operation_result("call_1",self.result,self.receipt)
        (self.root/"receipt.json").write_text("changed")
        with self.assertRaises(ContractError):self.ledger.response_intent("call_1")
        self.assertEqual(self.ledger.inspect("call_1")["phase"],"RESULT_RECORDED")
        with self.assertRaises(ContractError):OperationLedger(self.path,"0"*64)


if __name__ == "__main__": unittest.main()
