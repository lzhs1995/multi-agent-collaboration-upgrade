"""Dynamic readiness binds native data and consumption, never fake hooks."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from orchestration_common import ContractError, json_ref, write_json
import test_upgrade_task_startup as startup_fixture
from upgrade_task_operations import OperationLedger
from upgrade_task_readiness import SCHEMA, READY_CALL, dynamic_readiness

SID, TURN = startup_fixture.SID, startup_fixture.TURN


class DynamicReadinessTests(unittest.TestCase):
    def setUp(self):
        self.fixture=startup_fixture.TaskStartupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        if getattr(self,"multi_plan",False):
            f.multi_plan()
        f.directory=f.root / "control/task-startup"
        f.directory.parent.mkdir()
        startup=json_ref(f.start())
        self.startup=startup
        self.spec={"adoption_path":str(f.root/"control/adoption.json"),"task_startup":f.plan_ref}
        ledger=OperationLedger(startup["ledger"],startup["registration_sha256"])
        self.addCleanup(ledger.close)
        self.ledger=ledger
        self.nonce="a"*48
        value=dict(schema=SCHEMA,plan=f.plan_ref,thread_id=SID,turn_id=TURN,receipt_nonce=self.nonce)
        output=dict(success=True,contentItems=[dict(type="inputText",text=json.dumps(value,sort_keys=True))])
        request=dict(id=10,method="item/tool/call",params=dict(threadId=SID,turnId=TURN,
            callId="call-ready",namespace="upgrade_collaboration",tool="execute",arguments={"operation_id":"ready"}))
        ledger.receive(request)
        ledger.operation_result("call-ready",output,write_json(f.root/"receipt.json",value))
        ledger.response_intent("call-ready")
        item=dict(id="call-ready",type="dynamicToolCall",namespace="upgrade_collaboration",tool="execute",
                  arguments={"operation_id":"ready"},status="completed",**output)
        message=dict(method="item/completed",params=dict(threadId=SID,turnId=TURN,item=item))
        ledger.complete(message,write_json(f.root/"completed.json",message))
        native_item=dict(item,type="DynamicToolCall",content_items=item["contentItems"])
        del native_item["contentItems"]
        self.extra=[dict(type="turn_context",payload=dict(turn_id=TURN,model="fixture-model")),
            dict(type="event_msg",payload=dict(type="item_completed",thread_id=SID,turn_id=TURN,item=native_item)),
            dict(type="response_item",payload=dict(type="message",id="msg-ready",role="assistant",phase="final_answer",
                content=[dict(type="output_text",text=self.nonce)],internal_chat_message_metadata_passthrough=dict(turn_id=TURN)))]
        self.outer_call = dict(type="response_item",payload=dict(type="custom_tool_call",name="exec",
            call_id="outer-ready",input=READY_CALL,internal_chat_message_metadata_passthrough=dict(turn_id=TURN)))
        self.outer_output = dict(type="response_item",payload=dict(type="custom_tool_call_output",call_id="outer-ready",
            output=[dict(type="input_text",text="Script completed\nWall time 0.3 seconds\nOutput:\n"),
                    dict(type="input_text",text=json.dumps(value,sort_keys=True))],
            internal_chat_message_metadata_passthrough=dict(turn_id=TURN)))
        self.original=f.path.read_bytes()
        self.native=SimpleNamespace(_open=lambda path:open(path,"rb"),
            _check_file=lambda stream,binding: self.assertEqual(os.fstat(stream.fileno()).st_ino,
                                                               binding["transcript"]["inode"]),MAX_RECORD=2*1024*1024)

    def evaluate(self):
        f=self.fixture
        records=[self.extra[0],self.outer_call,self.extra[1],self.outer_output,*self.extra[2:]]
        f.path.write_bytes(self.original+b"".join((json.dumps(x)+"\n").encode() for x in records))
        st=f.path.stat()
        session=dict(provider="codex",session_id=SID,transcript=dict(path=str(f.path),device=st.st_dev,
            inode=st.st_ino,offset=st.st_size,at_record_boundary=True))
        return dynamic_readiness(self.native,session,self.spec)

    def test_exact_dynamic_completion_and_model_consumption_pass_separately(self):
        value=self.evaluate()
        self.assertEqual(value["schema"],SCHEMA)
        self.assertEqual(value["model"],"fixture-model")
        self.assertFalse(value["hooks_proven"])
        self.assertFalse(value["adoption_proven"])
        self.assertNotIn("hooks",value)

    def test_v2_startup_readiness_uses_same_frozen_plan_and_ledger(self):
        f=DynamicReadinessTests();f.multi_plan=True;f.setUp()
        self.addCleanup(f.doCleanups)
        self.assertEqual(f.ledger.registration["schema"],"upgrade-task-operations-v2")
        self.assertEqual(f.evaluate()["model"],"fixture-model")
        # Replacing both the external pin and database contents is still not
        # permission to change the operation set frozen in the original plan.
        from orchestration_common import canonical, object_sha
        original=dict(f.ledger.registration)
        original["server"]=dict(original["server"],birth=[999,0])
        f.startup["operations"]=write_json(f.fixture.directory/"operations.json",original)
        f.startup["registration_sha256"]=object_sha(original)
        write_json(f.fixture.directory/"result.json",f.startup)
        f.ledger.db.execute("UPDATE registration SET body=?",(canonical(original),))
        with self.assertRaisesRegex(ContractError,"REGISTRATION_CHANGED"):f.evaluate()

    def test_visible_wrong_nonce_never_counts_as_consumption(self):
        self.extra[-1]["payload"]["content"][0]["text"]="b"*48
        with self.assertRaisesRegex(ContractError,"CONSUMPTION_MISSING"):self.evaluate()

    def test_other_turn_result_cannot_pass(self):
        self.extra[1]["payload"]["turn_id"]=SID
        with self.assertRaisesRegex(ContractError,"NATIVE_RESULT_CHANGED"):self.evaluate()

    def test_shell_success_never_substitutes_dynamic_completion(self):
        self.extra[1]["payload"]["item"]["type"]="CommandExecution"
        with self.assertRaisesRegex(ContractError,"NATIVE_RESULT_CHANGED"):self.evaluate()

    def test_cancelled_or_unknown_ledger_is_not_ready(self):
        self.ledger.db.execute("UPDATE calls SET phase='UNKNOWN'")
        with self.assertRaisesRegex(ContractError,"NATIVE_OPERATION_REQUIRED"):self.evaluate()

    def test_later_user_developer_and_system_nonce_never_prove_tool_consumption(self):
        for role in ("user", "developer", "system"):
            record=dict(type="response_item",payload=dict(type="message",role=role,
                content=[dict(type="input_text",text=self.nonce)],
                internal_chat_message_metadata_passthrough=dict(turn_id=TURN)))
            self.extra.insert(-1,record)
            with self.subTest(role=role),self.assertRaisesRegex(ContractError,"NONCE_OTHER_SOURCE"):
                self.evaluate()
            self.extra.remove(record)

    def test_event_wrapped_input_other_tool_and_split_nonce_are_rejected(self):
        records=[dict(type="event_msg",payload=dict(type="user_message",message=self.nonce)),
            dict(type="response_item",payload=dict(type="function_call_output",call_id="foreign",output=self.nonce)),
            dict(type="response_item",payload=dict(type="message",role="developer",content=[
                dict(type="input_text",text=self.nonce[:24]),dict(type="input_text",text=self.nonce[24:])]))]
        for record in records:
            self.extra.insert(-1,record)
            with self.subTest(record=record),self.assertRaisesRegex(ContractError,"NONCE_OTHER_SOURCE"):
                self.evaluate()
            self.extra.remove(record)

    def test_outer_call_text_is_code_not_comment_or_quoted_example(self):
        self.outer_call["payload"]["input"]="// "+READY_CALL
        with self.assertRaisesRegex(ContractError,"OUTER_CALL_UNBOUND"):self.evaluate()

    def test_second_call_or_wrong_return_id_is_not_an_unambiguous_chain(self):
        self.extra.insert(-1,self.outer_call)
        with self.assertRaisesRegex(ContractError,"OUTER_CALL_AMBIGUOUS"):self.evaluate()
        self.extra.pop(-2)
        self.outer_output["payload"]["call_id"]="foreign"
        with self.assertRaisesRegex(ContractError,"OUTER_OUTPUT_AMBIGUOUS"):self.evaluate()

    def test_post_final_input_is_outside_consumption_window(self):
        self.extra.append(dict(type="response_item",payload=dict(type="message",role="user",
            content=[dict(type="input_text",text=self.nonce)])))
        self.assertFalse(self.evaluate()["consumption_source"]["other_nonce_sources"])

    def test_native_agent_message_mirror_requires_exact_identity_and_content(self):
        record=dict(type="event_msg",payload=dict(type="item_completed",thread_id=SID,turn_id=TURN,
            item=dict(type="AgentMessage",id="msg-ready",phase="final_answer",content=[dict(type="Text",text=self.nonce)])))
        self.extra.insert(-1,record)
        self.assertFalse(self.evaluate()["consumption_source"]["other_nonce_sources"])
        for target,key,value in ((record["payload"]["item"],"id","foreign"),
                                 (record["payload"],"thread_id",TURN),
                                 (record["payload"],"turn_id",SID),
                                 (record["payload"]["item"],"content",[{"type":"Text","text":self.nonce+"extra"}])):
            original=target[key];target[key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ContractError,"NONCE_OTHER_SOURCE"):self.evaluate()
            target[key]=original


if __name__=="__main__":unittest.main()
