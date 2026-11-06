"""Typed service -> original Runner/Transport, with explicitly offline CLI I/O.

The service, actor ledger, node locks, preparation journal and evidence readers
are real. FakeRuntime supplies synthetic harness/Claude records; these checks
do not establish live actor identity, deployment or a model's task consumption.
"""
import copy
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, read_json, write_json
from orchestration_preparation import preparation_stages
from upgrade_task_campaign import validate_controls
from test_orchestration_transport import FakeRuntime
import test_upgrade_task_campaign as fixtures


class TypedTransportTests(unittest.TestCase):
    def fixture(self, node="review"):
        f=fixtures.CampaignActorTests();f.transport_node=node;f.service_mode=True
        if hasattr(self,"transport_actions"):f.transport_actions=self.transport_actions
        f.setUp();self.addCleanup(f.doCleanups)
        f.call("start");f.complete("start")
        runtime=FakeRuntime(f.c,f.runner.co("run-01"),node_id=node)
        f.runner.runtime=runtime
        f.call("claim");f.complete("claim")
        for name,value in (("handshake_journal",runtime.handshake_root),("dispatch_journal",runtime.dispatch_root)):
            patcher=patch("orchestration_transport."+name,return_value=value)
            patcher.start();self.addCleanup(patcher.stop)
        return f,runtime

    def transport(self,f):
        return f.runner.transport(f.runner.co("run-01"),f.transport_node)

    def test_prepare_dispatch_preserve_exact_lane_stages_and_original_once_journal(self):
        for node,lane in (("review","claude_a"),("parallel","claude_b")):
            with self.subTest(lane=lane):
                f,runtime=self.fixture(node)
                self.assertEqual(f.call("prepare")["phase"],"RESPONSE_INTENDED")
                f.complete("prepare")
                transport=self.transport(f)
                self.assertEqual(f.runner.status("run-01")["states"][node],"PREPARED")
                stages=preparation_stages(f.c.campaign)
                self.assertEqual({p.name for p in (transport.root/"operations").iterdir()},set(stages))
                for stage in stages:
                    intent=file_ref(transport.root/"operations"/stage/"intent.json")
                    result=read_json(transport.root/"operations"/stage/"result.json")
                    self.assertEqual(result["intent"],intent)
                    self.assertEqual(result["status"],"RETURNED")
                    if stage not in ("fill-pack","task-notice"):
                        argv=json_ref(intent)["arguments"]["argv"]
                        self.assertEqual(argv[3],stage)
                        self.assertEqual(argv[argv.index("--executor-surface")+1],f.c.clients[lane].session["surface_uuid"])
                self.assertEqual(runtime.calls,["preflight","map","surface-inventory","task-pack","finalize-pack","notice"])
                f.call("dispatch");f.complete("dispatch")
                self.assertEqual(f.runner.status("run-01")["states"][node],"DISPATCHED")
                self.assertEqual(f.ledger.inspect("inner-dispatch")["phase"],"NATIVE_COMPLETED")
                self.assertEqual(f.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],4)
                receipt=read_json(f.control/"campaign-results/dispatch.json")
                self.assertEqual(receipt["operation"]["node_id"],node)
                self.assertEqual(json_ref(receipt["result"]["receipt"])["binding"]["task_id"],transport.current()[1]["task_id"])
                before=list(runtime.calls)
                # Original transport recovery observes old results. A new model
                # call cannot reacquire the same typed budget even after success.
                transport.prepare();transport.dispatch()
                self.assertEqual(runtime.calls,before)
                with self.assertRaises(ContractError):f.call("dispatch")
                self.assertEqual(runtime.calls,before)

    def test_dispatch_before_preparation_refuses_without_input_or_stage_intent(self):
        f,runtime=self.fixture()
        with self.assertRaisesRegex(ContractError,"finalize the pack"):
            f.call("dispatch")
        self.assertEqual(runtime.calls,[])
        self.assertFalse((self.transport(f).root/"dispatch-intent.json").exists())
        self.assertEqual(f.ledger.inspect("inner-dispatch")["phase"],"UNKNOWN")
        with self.assertRaisesRegex(ContractError,"CLOSED_NO_REPLAY"):f.actor.step()

    def test_failed_preparation_keeps_original_stage_budget_without_retry(self):
        f,runtime=self.fixture()
        original=runtime.harness
        def fail(argv,root):
            result=original(argv,root)
            if argv[3]=="map":result["returncode"]=17
            return result
        runtime.harness=fail
        with self.assertRaisesRegex(ContractError,"original map failed"):
            f.call("prepare")
        transport=self.transport(f)
        saved=file_ref(transport.root/"operations/map/result.json")
        with self.assertRaisesRegex(ContractError,"original map failed"):
            transport.prepare()
        self.assertEqual(saved,file_ref(saved["path"]))
        self.assertEqual(runtime.calls,["preflight","map"])
        self.assertFalse((transport.root/"dispatch-intent.json").exists())
        self.assertEqual(f.ledger.inspect("inner-prepare")["phase"],"UNKNOWN")

    def test_unknown_dispatch_retains_both_ledgers_and_never_repeats_input(self):
        f,runtime=self.fixture()
        f.call("prepare");f.complete("prepare")
        runtime.fail_submit=True
        f.call("dispatch");f.complete("dispatch")
        transport=self.transport(f)
        self.assertEqual(f.runner.status("run-01")["states"]["review"],"UNCERTAIN")
        receipt=read_json(f.control/"campaign-results/dispatch.json")
        self.assertEqual(receipt["result"]["status"],"UNCERTAIN")
        saved=file_ref(transport.root/"operations/submit-task-pack/result.json")
        self.assertEqual(json_ref(saved)["status"],"ERROR")
        self.assertEqual(transport.dispatch()["status"],"UNCERTAIN")
        self.assertEqual(transport.reconcile()["status"],"UNCERTAIN")
        with self.assertRaises(ContractError):f.call("dispatch")
        self.assertEqual(runtime.calls.count(("submit",False)),1)
        self.assertNotIn(("submit",True),runtime.calls)
        self.assertEqual(saved,file_ref(saved["path"]))
        # Native tool completion means the uncertain result was returned; it
        # cannot upgrade the original transport outcome to a received task.
        self.assertEqual(f.ledger.inspect("inner-dispatch")["phase"],"NATIVE_COMPLETED")

    def test_revoked_service_gate_creates_no_new_typed_or_transport_intent(self):
        f,runtime=self.fixture()
        def stop(*args):raise ContractError("fixture STOP_REQUESTED")
        f.actor.gate=stop
        with self.assertRaisesRegex(ContractError,"STOP_REQUESTED"):f.call("prepare")
        self.assertEqual(f.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],2)
        self.assertFalse((self.transport(f).root/"operations").exists())
        self.assertEqual(runtime.calls,[])

    def test_typed_entry_keeps_original_live_gate(self):
        f,runtime=self.fixture()
        f.runner.fixtures=False
        with self.assertRaises(ContractError):f.call("prepare")
        self.assertEqual(runtime.calls,[])
        self.assertEqual(f.ledger.inspect("inner-prepare")["phase"],"UNKNOWN")

    def test_changed_scope_and_duplicate_transport_budget_cannot_be_registered(self):
        f,_=self.fixture()
        for mutate in (
            lambda r:r["operations"]["prepare"]["payload"].update(node_id="foreign"),
            lambda r:r["operations"]["dispatch"]["payload"].update(run_id="other-run"),
            lambda r:r["operations"].update(duplicate=copy.deepcopy(r["operations"]["dispatch"]))):
            with self.subTest(mutation=mutate):
                registration=copy.deepcopy(f.registration);mutate(registration)
                campaign=copy.deepcopy(f.c.campaign)
                campaign["task_controls"]["registration"]=write_json(f.control/"invalid.json",registration)
                with self.assertRaises(ContractError):validate_controls(campaign,fixtures=True)


if __name__=="__main__":unittest.main()
