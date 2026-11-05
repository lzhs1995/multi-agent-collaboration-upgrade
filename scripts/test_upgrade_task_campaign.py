"""Typed actor -> unmodified offline Runner/Coordinator business entrypoints.

Real temporary journals/socket/SQLite are used with an explicitly fixture-only
deployment gate. This does not enroll a live task or validate a model run.
"""
import copy
import unittest

from orchestration_common import ContractError, json_ref, object_sha, utc_now, write_json
from orchestration_campaign import control_script
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture
from upgrade_task_actor import TaskActor
from upgrade_task_campaign import KIND, SCHEMA, campaign_handler, operation_payload, validate_controls
from upgrade_task_control import invocation_recorder
from upgrade_task_operations import OperationLedger
import test_upgrade_task_startup as fixtures


class CampaignActorTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.TaskStartupTests();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.c=CampaignFixture(f.root,version=3)
        self.native=self.c.clients["codex"]
        self.control=f.root/"operations";self.control.mkdir(mode=0o700)
        operations={name:dict(kind=KIND,payload=operation_payload(self.c.campaign,name,"run-01",
            node_id="local" if name=="claim" else None)) for name in ("start","claim")}
        self.registration=dict(schema="upgrade-task-operations-v2", task_id="fixture-campaign",
            controller_id="fixture-controller",thread_id=self.native.session["session_id"],turn_id=fixtures.TURN,
            authority_ref=self.c.campaign["authorization"],server=f.client.server,operations=operations,
            initial_operations=list(operations),choice_groups={})
        self.reg_ref=write_json(self.control/"operations.json",self.registration)
        self.ledger=OperationLedger.create(self.control/"ledger.sqlite",self.registration)
        self.addCleanup(self.ledger.close)
        self.directory=self.control/"campaign-invocations";self.directory.mkdir(mode=0o700)
        self.c.campaign["task_controls"]=dict(schema=SCHEMA,registration=self.reg_ref,
                                              evidence_directory=str(self.directory))
        self.c.save();self.runner=Runner(self.c.ref,fixtures=True)
        self.queue=[];self.sent=[];self.gates=[];self.sequence=0
        f.client.receive_recorded=lambda timeout:self.queue.pop(0)
        f.client.respond=self.sent.append
        self.handler=campaign_handler(self.runner,self.reg_ref,self.control/"campaign-results")
        self.actor=TaskActor(f.client,self.ledger,{KIND:self.handler},lambda *args:self.gates.append(args),
            self.control/"actor-events",invocation_recorder=invocation_recorder(self.native.reader,self.reg_ref,
                self.directory,after=self.native.session["freeze_fence"]["offset"]))

    def enqueue(self,message):
        self.sequence+=1
        ref=write_json(self.control/f"received-{self.sequence}.json",dict(direction="received",message=message,
            peer=dict(server=self.f.client.server,peer={k:self.f.client.server[k] for k in ("pid","uid")})))
        self.queue.append((message,ref))

    def call(self,name):
        payload=self.registration["operations"][name]["payload"]
        descriptor=control_script(self.c.campaign,self.c.ref,payload["action"],payload["run_id"],
            **{k:payload[k] for k in ("node_id","marker","decision","decision_fields")})
        self.native.append(dict(timestamp=utc_now(),type="response_item",payload=dict(type="custom_tool_call",
            name="exec",call_id="outer-"+name,input=descriptor["script"],
            internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.TURN))))
        self.enqueue(dict(id=10+self.sequence,method="item/tool/call",params=dict(threadId=self.registration["thread_id"],
            turnId=fixtures.TURN,callId="inner-"+name,namespace="upgrade_collaboration",tool="execute",
            arguments={"operation_id":name})))
        return self.actor.step()

    def complete(self,name):
        self.enqueue(dict(method="item/completed",params=dict(threadId=self.registration["thread_id"],
            turnId=fixtures.TURN,item=dict(id="inner-"+name,type="dynamicToolCall",namespace="upgrade_collaboration",
                tool="execute",arguments={"operation_id":name},status="completed",**self.sent[-1]["result"]))))
        self.actor.step()
        self.native.append(dict(timestamp=utc_now(),type="response_item",payload=dict(type="custom_tool_call_output",
            call_id="outer-"+name,output=[],internal_chat_message_metadata_passthrough=dict(turn_id=fixtures.TURN))))

    def test_original_runner_start_and_claim_use_typed_intents_and_same_ledger(self):
        self.assertEqual(self.call("start")["phase"],"RESPONSE_INTENDED")
        self.complete("start")
        self.assertEqual(self.runner.status("run-01")["state"],"STARTED")
        header=json_ref(write_json(self.control/"header-copy.json",self.runner.co("run-01").header))
        self.assertIn("task_invocation",header["start_native"])
        self.call("claim");self.complete("claim")
        self.assertEqual(self.runner.status("run-01")["states"]["local"],"LOCAL_RUNNING")
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],2)
        self.assertEqual(len(list((self.control/"campaign-results").glob("*.json"))),2)
        self.assertEqual(len(self.gates),4)

    def test_original_identity_gate_is_still_required_for_live_handler(self):
        self.runner.fixtures=False
        with self.assertRaises(ContractError):self.call("start")
        self.assertFalse((self.c.root/"runs/run-01").exists())
        self.assertEqual(self.ledger.inspect("inner-start")["phase"],"UNKNOWN")

    def test_external_enrollment_failure_occurs_before_business_intent(self):
        def deny(*args):raise ContractError("fixture enrollment denied")
        self.actor.gate=deny
        with self.assertRaisesRegex(ContractError,"enrollment denied"):self.call("start")
        self.assertFalse((self.c.root/"runs/run-01").exists())
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],0)

    def test_missing_frozen_acceptance_has_no_shell_fallback(self):
        with self.assertRaisesRegex(ContractError,"EXACT_OPERATION_NOT_FROZEN"):
            control_script(self.c.campaign,self.c.ref,"accept","run-01",node_id="local",marker="real-later-marker")
        with self.assertRaisesRegex(ContractError,"ACTION_NOT_REGISTERABLE"):
            control_script(self.c.campaign,self.c.ref,"arbitrary-shell","run-01")
        self.assertFalse((self.c.root/"runs/run-01").exists())

    def test_wrong_task_scope_and_duplicate_control_are_refused(self):
        for mutate in (
            lambda r:r.update(thread_id=fixtures.SID),
            lambda r:r["operations"]["start"]["payload"].update(campaign_id="foreign"),
            lambda r:r["operations"].update(duplicate=copy.deepcopy(r["operations"]["start"]))):
            value=copy.deepcopy(self.registration);mutate(value)
            campaign=copy.deepcopy(self.c.campaign)
            campaign["task_controls"]["registration"]=write_json(self.control/"changed.json",value)
            with self.assertRaises(ContractError):validate_controls(campaign,fixtures=True)

    def test_failed_original_run_step_cannot_be_replayed(self):
        with self.assertRaises(ContractError):self.call("claim")
        self.assertEqual(self.ledger.inspect("inner-claim")["phase"],"UNKNOWN")
        with self.assertRaisesRegex(ContractError,"CLOSED_NO_REPLAY"):self.actor.step()
        self.assertFalse((self.c.root/"runs/run-01").exists())


if __name__=="__main__":unittest.main()
