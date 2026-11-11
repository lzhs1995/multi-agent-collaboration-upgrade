"""Real fixture socket/journal -> actor intent -> existing control readers.

No live enrollment is claimed. The explicit fixture gate permits a local
handler; production campaign/adoption and transport gates remain unchanged.
"""
import copy
import json
import unittest

from orchestration_common import ContractError, file_ref, json_ref, write_json
from orchestration_native import NativeReader
from orchestration_campaign import find_control, native_control
from upgrade_task_actor import TaskActor
from upgrade_task_control import control_request, invocation_recorder, native_invocation, native_result
from upgrade_task_operations import OperationLedger
import test_upgrade_task_startup as fixtures


class TypedControlTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.TaskStartupTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        f.path=f.root/("native/sessions/rollout-"+fixtures.SID+".jsonl")
        f.multi_plan()
        self.startup=json_ref(f.start())
        self.ledger=OperationLedger(self.startup["ledger"],self.startup["registration_sha256"])
        self.addCleanup(self.ledger.close)
        st=f.path.stat()
        self.reader=NativeReader(dict(provider="codex",session_id=fixtures.SID,
            transcript=dict(path=str(f.path),device=st.st_dev,inode=st.st_ino)),fixtures=True)
        self.after=self.reader.snapshot()["offset"]
        self.directory=f.root/"typed-control";self.directory.mkdir(mode=0o700)
        self.descriptor=control_request(self.startup["operations"],"ready",self.directory)
        self.request=dict(id=77,method="item/tool/call",params=dict(threadId=fixtures.SID,turnId=fixtures.TURN,
            callId="inner-ready",namespace="upgrade_collaboration",tool="execute",arguments={"operation_id":"ready"}))
        self.outer=dict(timestamp="2026-11-05T08:30:00Z",type="response_item",payload=dict(type="custom_tool_call",
            name="exec",call_id="outer-ready",input=self.descriptor["script"],
            internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.TURN)))
        self.queue=[];self.sent=[];self.effects=[];self.gates=[]
        f.client.receive_recorded=lambda timeout:self.queue.pop(0)
        f.client.respond=self.sent.append
        self.output=dict(success=True,contentItems=[dict(type="inputText",text="fixture-effect")])
        def handler(payload):
            # Existing reader joins the running model invocation before a
            # native response exists; it does not impersonate a shell call.
            ref=find_control(self.reader,self.descriptor,after=self.after)
            when=native_control(self.reader,ref,self.descriptor)
            self.effects.append((payload,when.isoformat(),ref))
            return self.output,write_json(f.root/"receipt.json",{"effect":True})
        self.actor=TaskActor(f.client,self.ledger,{"readiness":handler},lambda *args:self.gates.append(args),
            f.root/"events",operation_scope={"ready"},
            invocation_recorder=invocation_recorder(self.reader,self.startup["operations"],self.directory,after=self.after))

    def append(self,value):
        with self.f.path.open("ab") as stream:stream.write((json.dumps(value)+"\n").encode())

    def enqueue(self,message):
        path=self.f.root/f"incoming-{len(list(self.f.root.glob('incoming-*')))}.json"
        ref=write_json(path,dict(direction="received",message=message,peer=dict(server=self.f.client.server,
            peer={k:self.f.client.server[k] for k in ("pid","uid")})))
        self.queue.append((message,ref));return ref

    def run_call(self):
        self.append(self.outer);self.enqueue(self.request)
        return self.actor.step()

    def completion(self):
        return dict(method="item/completed",params=dict(threadId=fixtures.SID,turnId=fixtures.TURN,
            item=dict(id="inner-ready",type="dynamicToolCall",namespace="upgrade_collaboration",tool="execute",
                arguments={"operation_id":"ready"},status="completed",**self.output)))

    def test_actor_producer_and_existing_readers_share_exact_original_evidence(self):
        self.assertEqual(self.run_call()["phase"],"RESPONSE_INTENDED")
        self.assertEqual(len(self.effects),1)
        ref=self.effects[0][2]
        self.assertEqual(ref["task_invocation"]["path"],str(self.directory/"ready.json"))
        evidence=json_ref(ref["task_invocation"])
        self.assertEqual(evidence["inner_call_id"],"inner-ready")
        self.assertEqual(evidence["outer_call_id"],"outer-ready")
        self.assertFalse(evidence["execution_authority"])
        with self.assertRaisesRegex(ContractError,"NATIVE_COMPLETION_REQUIRED"):
            native_invocation(self.reader,ref["task_invocation"],self.descriptor)
        self.enqueue(self.completion());self.actor.step()
        self.assertEqual(native_invocation(self.reader,ref["task_invocation"],self.descriptor)["phase"],"NATIVE_COMPLETED")
        with self.assertRaisesRegex(ContractError,"BEFORE_ORIGINAL_FENCE"):
            find_control(self.reader,self.descriptor,after=ref["offset"]+1)
        self.assertEqual(len(self.sent),1)

    def test_comment_or_wrong_turn_never_executes(self):
        for mutate in (lambda p:p.update(input="// "+p["input"]),
                       lambda p:p.update(internal_chat_message_metadata_passthrough={"turn_id":fixtures.SID})):
            f=TypedControlTests();f.setUp();self.addCleanup(f.doCleanups)
            mutate(f.outer["payload"])
            with self.assertRaisesRegex(ContractError,"OUTER_UNBOUND"):f.run_call()
            self.assertEqual(f.effects,[])
            self.assertEqual(f.ledger.inspect("inner-ready")["phase"],"UNKNOWN")

    def test_competing_unreturned_call_or_already_returned_call_cannot_correlate(self):
        for record in (
            dict(type="response_item",payload=dict(type="custom_tool_call",name="exec",call_id="other",input="text(1)")),
            dict(type="response_item",payload=dict(type="custom_tool_call_output",call_id="outer-ready",output=[]))):
            f=TypedControlTests();f.setUp();self.addCleanup(f.doCleanups)
            f.append(f.outer);f.append(record);f.enqueue(f.request)
            with self.assertRaisesRegex(ContractError,"OUTER_AMBIGUOUS"):f.actor.step()
            self.assertEqual(f.effects,[])

    def test_lost_response_retains_original_control_but_does_not_prove_completion(self):
        def fail(_):raise TimeoutError("lost original response")
        self.f.client.respond=fail
        with self.assertRaises(TimeoutError):self.run_call()
        ref=self.effects[0][2]
        self.assertEqual(self.ledger.inspect("inner-ready")["phase"],"UNKNOWN")
        native_control(self.reader,ref,self.descriptor)
        with self.assertRaisesRegex(ContractError,"NATIVE_COMPLETION_REQUIRED"):
            native_invocation(self.reader,ref["task_invocation"],self.descriptor)
        self.assertEqual(len(self.effects),1)

    def test_cancelled_or_replaced_ledger_refuses_control(self):
        self.run_call();ref=self.effects[0][2]
        self.actor.close("CANCELLED")
        with self.assertRaisesRegex(ContractError,"NATIVE_COMPLETION_REQUIRED"):
            native_control(self.reader,ref,self.descriptor)
        old=self.ledger.path
        replacement=old.with_suffix(".copy");replacement.write_bytes(old.read_bytes());replacement.chmod(0o600)
        replacement.replace(old)
        with self.assertRaisesRegex(ContractError,"LEDGER_REPLACED"):
            native_control(self.reader,ref,self.descriptor)

    def test_wrong_operation_peer_or_native_ref_cannot_reuse_proof(self):
        self.run_call();ref=self.effects[0][2]
        wrong=dict(ref,offset=0)
        with self.assertRaisesRegex(ContractError,"REFERENCE_CHANGED"):
            native_control(self.reader,wrong,self.descriptor)
        evidence=json_ref(ref["task_invocation"])
        rpc=json_ref(evidence["received"])
        rpc["peer"]["server"]=dict(rpc["peer"]["server"],birth=[999,0])
        evidence["received"]=write_json(self.f.root/"wrong-peer.json",rpc)
        rewritten=write_json(self.directory/"ready.json",evidence)
        with self.assertRaisesRegex(ContractError,"ORIGINAL_RPC_CHANGED"):
            native_invocation(self.reader,rewritten,self.descriptor,allow_intended=True)

    def test_phase_label_cannot_replace_original_completion_evidence(self):
        self.run_call();ref=self.effects[0][2]
        self.ledger.db.execute("UPDATE calls SET phase='NATIVE_COMPLETED'")
        with self.assertRaisesRegex(ContractError,"RESULT_HISTORY_CHANGED"):
            native_invocation(self.reader,ref["task_invocation"],self.descriptor)

    def test_late_original_completion_resolves_unknown_without_another_effect(self):
        def fail(_):raise TimeoutError("original send uncertain")
        self.f.client.respond=fail
        with self.assertRaises(TimeoutError):self.run_call()
        completion=self.completion();rpc=self.enqueue(completion)
        self.actor.recover_completion(completion,rpc)
        ref=self.effects[0][2]
        proof=native_invocation(self.reader,ref["task_invocation"],self.descriptor)
        self.assertEqual(proof["rpc_completion"],completion)
        self.assertFalse(proof["model_consumption_proven"])
        self.assertEqual(len(self.effects),1)

    def test_original_shell_control_reader_keeps_its_existing_semantics(self):
        script='text(await tools.exec_command({"cmd":"fixture-only"}));\n'
        self.outer["payload"]["input"]=script
        self.append(self.outer)
        ref=find_control(self.reader,script,after=self.after)
        self.assertNotIn("task_invocation",ref)
        self.assertEqual(native_control(self.reader,ref,script).isoformat(),"2026-11-05T08:30:00+00:00")

    def result_records(self):
        item=self.completion()["params"]["item"]
        return [dict(timestamp="2026-11-05T08:30:01Z",type="event_msg",payload=dict(type="item_completed",
            thread_id=fixtures.SID,turn_id=fixtures.TURN,item=dict(type="DynamicToolCall",content_items=item["contentItems"],
                **{k:item[k] for k in ("id","namespace","tool","arguments","status","success")}))),
            dict(timestamp="2026-11-05T08:30:02Z",type="response_item",payload=dict(type="custom_tool_call_output",
                call_id="outer-ready",internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.TURN),
                output=[dict(type="input_text",text="Script completed\nWall time 0.1 seconds\nOutput:\n"),
                        dict(type="input_text",text=item["contentItems"][0]["text"])]))]

    def test_rpc_completion_alone_is_not_native_result_or_publication(self):
        from upgrade_advice_chain import decision_publish_evidence, bind_result_evidence
        self.output["contentItems"][0]["text"]=json.dumps({"path":"/fixture","bytes":1,"sha256":"a"*64})
        self.run_call();ref=self.effects[0][2]
        self.enqueue(self.completion());self.actor.step()
        self.assertIsNone(native_result(self.reader,ref["task_invocation"],self.descriptor,until=self.reader.snapshot()["offset"]))
        for record in self.result_records():self.append(record)
        end=self.reader.snapshot()["offset"]
        result=native_result(self.reader,ref["task_invocation"],self.descriptor,until=end)
        self.assertFalse(result["model_consumption_proven"])
        published=decision_publish_evidence(self.reader,fence=self.after,until=end,script=self.descriptor)
        self.assertEqual(published["envelope"],result["value"])
        bind=dict(call_record=ref,task_control=self.descriptor)
        self.assertEqual(bind_result_evidence(self.reader,bind,result["value"],until=end)["call_id"],"outer-ready")
        with self.assertRaisesRegex(ContractError,"did not return"):
            bind_result_evidence(self.reader,bind,{"different":True},until=end)
        self.append(self.result_records()[-1])
        # Original decision fence stays stable; a wider ambiguous window fails.
        self.assertEqual(native_result(self.reader,ref["task_invocation"],self.descriptor,until=end),result)
        with self.assertRaisesRegex(ContractError,"AMBIGUOUS"):
            native_result(self.reader,ref["task_invocation"],self.descriptor,until=self.reader.snapshot()["offset"])

    def test_changed_output_wrong_turn_or_wrong_inner_event_refuses_result(self):
        for index,mutate in (
            (1,lambda r:r["payload"]["output"][1].update(text='{"wrong":true}')),
            (1,lambda r:r["payload"]["internal_chat_message_metadata_passthrough"].update(turn_id=fixtures.SID)),
            (0,lambda r:r["payload"]["item"].update(arguments={"operation_id":"other"}))):
            f=TypedControlTests();f.setUp();self.addCleanup(f.doCleanups)
            f.output["contentItems"][0]["text"]='{"result":true}'
            f.run_call();ref=f.effects[0][2];f.enqueue(f.completion());f.actor.step()
            records=f.result_records();mutate(records[index])
            for record in records:f.append(record)
            with self.assertRaises(ContractError):
                native_result(f.reader,ref["task_invocation"],f.descriptor,until=f.reader.snapshot()["offset"])

    def test_typed_bind_requires_current_intent_not_finished_control(self):
        from upgrade_advice_chain import live_bind_call
        self.run_call()
        with self.assertRaisesRegex(ContractError,"ORIGINAL_INTENT_REQUIRED"):
            live_bind_call(self.reader,self.descriptor,after=self.after)

    def test_contradictory_session_before_call_or_result_is_not_ignored(self):
        record=dict(type="session_meta",payload=dict(id=fixtures.TURN))
        self.append(record)
        with self.assertRaisesRegex(ContractError,"NATIVE_SESSION_CHANGED"):self.run_call()
        f=TypedControlTests();f.setUp();self.addCleanup(f.doCleanups)
        f.output["contentItems"][0]["text"]='{"result":true}'
        f.run_call();ref=f.effects[0][2];f.enqueue(f.completion());f.actor.step()
        f.append(record)
        for row in f.result_records():f.append(row)
        with self.assertRaisesRegex(ContractError,"NATIVE_SESSION_CHANGED"):
            native_result(f.reader,ref["task_invocation"],f.descriptor,until=f.reader.snapshot()["offset"])


if __name__=="__main__":unittest.main()
