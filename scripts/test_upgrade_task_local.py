"""Real frozen workload subprocess/ledger with explicit offline identity fixtures."""
import copy
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, tree_manifest, write_json
import upgrade_task_local as local
import test_upgrade_task_campaign as campaign_fixtures
from test_orchestration_workload import CorpusFixture


class LocalTests(unittest.TestCase):
    def fixture(self, *, failing=False, advance=False):
        f=campaign_fixtures.CampaignActorTests();f.transport_node="inventory";f.transport_actions=()
        f.local_actions=("local-execute",);f.binding_mode=True;f.binding_node="inventory"
        if advance:f.advance_observations=("claimed","executed","candidate")
        def configure(c):
            corpus=CorpusFixture(c.root)
            corpus.inputs.rename(c.root/"inputs");corpus.inputs=c.root/"inputs"
            node=dict(c.plan["nodes"][0],id="inventory",title="inventory",write_scope="inventory")
            c.plan["nodes"]=[node]
            c.campaign["plan"]=write_json(c.root/"plan.json",c.plan)
            scripts=Path(c.campaign["adapter"]["path"])/"scripts"
            # Import dependencies are the same pinned adapter closure.
            for source in Path(__file__).parent.glob("*.py"):
                if not source.name.startswith("test_") and not (scripts/source.name).exists():
                    shutil.copyfile(source,scripts/source.name)
            if failing:(scripts/"orchestration_workload.py").write_text("raise SystemExit(7)\n")
            checker=scripts/"checker.py"
            checker.write_text("import json,sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).parent))\nfrom orchestration_checker import check\nprint(json.dumps(check(json.load(sys.stdin))))\n")
            tree=tree_manifest(corpus.inputs,kind="output")
            c.campaign.update(input_tree=tree,inputs=[file_ref(corpus.inputs/n) for n in sorted(tree["files"])],
                adapter=tree_manifest(scripts.parent),workload=file_ref(scripts/"orchestration_workload.py"),
                checker=file_ref(checker))
            c.save()
        f.configure_campaign=configure;f.setUp();self.addCleanup(f.doCleanups)
        f.call("start");f.complete("start");f.call("claim");f.complete("claim")
        enrollment=dict(schema="upgrade-task-campaign-enrollment-v1",campaign=f.runner.ref,
            registration=f.reg_ref,authority=f.c.campaign["authorization"],runtime=f.c.campaign["runtime"],
            adoption=f.c.campaign["client_isolation"],controller_pid=os.getpid(),
            ledger=dict(path=str(f.ledger.path),device=f.ledger.identity[0],inode=f.ledger.identity[1]))
        owner=SimpleNamespace(runner=f.runner,campaign=f.c.campaign,ledger=f.ledger,
            operations={k:v["payload"] for k,v in f.registration["operations"].items()},
            proof=write_json(f.control/"campaign-enrollment.json",enrollment))
        def actor(runner,run,node):
            row=f.ledger.inspect("inner-local-execute")
            received=json_ref(file_ref(f.directory/"local-execute.json"))["received"]
            return local._scope(runner,run,node,owner,row["request"],received)
        p=patch.object(local,"_actor",side_effect=actor);p.start();self.addCleanup(p.stop)
        f.local_owner=owner
        return f

    def summary(self,f,**kwargs):
        co=f.runner.co("run-01");info=co.state()[1]["inventory"]
        return f.native.reader.action_summary(info["native_start"]["offset"],f.native.reader.snapshot()["offset"],
            task_id=info["task_id"],output_root=info["output_root"],task_campaign=f.c.campaign,**kwargs)

    def test_real_workload_has_original_typed_result_and_independent_acceptance(self):
        f=self.fixture();f.call("local-execute");f.complete("local-execute")
        result=self.summary(f,include_evidence=True)
        self.assertEqual(result["completed_task_calls"],1)
        self.assertEqual(result["actions"][0]["call_id"],"outer-local-execute")
        f.call("candidate");f.complete("candidate");f.call("accept");f.complete("accept")
        self.assertEqual(f.runner.status("run-01")["states"]["inventory"],"ACCEPTED")
        with self.assertRaises(ContractError):f.call("local-execute")

    def test_inner_completion_without_outer_output_remains_pending(self):
        f=self.fixture();f.call("local-execute")
        message,_,_=f.complete("local-execute",defer_outer=True)
        result=self.summary(f,allow_empty=True,allow_incomplete=True,include_evidence=True)
        self.assertEqual(result["pending_task_calls"],1)
        with self.assertRaisesRegex(ContractError,"unfinished task tool call"):
            self.summary(f)
        f.native_result("local-execute",message)
        self.assertEqual(self.summary(f)["completed_task_calls"],1)

    def test_failed_workload_is_completed_failure_not_useful_work(self):
        f=self.fixture(failing=True);f.call("local-execute");f.complete("local-execute")
        result=self.summary(f,allow_empty=True)
        self.assertEqual(result["failed_task_calls"],1);self.assertEqual(result["completed_task_calls"],0)
        with self.assertRaisesRegex(ContractError,"no completed task-bound"):
            self.summary(f)

    def test_response_loss_preserves_executed_stage_and_does_not_repeat(self):
        f=self.fixture()
        def lost(_):raise TimeoutError("lost original response")
        f.f.client.respond=lost
        with self.assertRaises(TimeoutError):f.call("local-execute")
        result=f.c.root/"runs/run-01/nodes/inventory/operations/local-execute/result.json"
        before=file_ref(result)
        self.assertEqual(f.ledger.inspect("inner-local-execute")["phase"],"UNKNOWN")
        with self.assertRaisesRegex(ContractError,"unfinished task tool call"):self.summary(f)
        with self.assertRaises(ContractError):f.call("local-execute")
        self.assertEqual(file_ref(result),before)
        with self.assertRaisesRegex(ContractError,"CALL_CHANGED"):self.summary(f)

    def test_run_metrics_include_typed_work_and_terminal_scan_excludes_old_call(self):
        f=self.fixture();f.call("local-execute");f.complete("local-execute")
        co=f.runner.co("run-01");reader=f.native.reader;end=reader.snapshot()["offset"]
        result=reader.action_summary(co.header["native_start"]["codex"]["offset"],end,
            task_id="run-01",output_root=str(co.root/"work"),task_campaign=f.c.campaign)
        self.assertEqual(result["completed_task_calls"],1)
        info=co.state()[1]["inventory"]
        result=reader.action_summary(end,end,task_id=info["task_id"],output_root=info["output_root"],
            task_campaign=f.c.campaign,allow_empty=True)
        self.assertEqual(result["attempted_task_calls"],0)

    def test_changed_proof_and_missing_campaign_cannot_count_success(self):
        f=self.fixture();f.call("local-execute");f.complete("local-execute")
        co=f.runner.co("run-01");info=co.state()[1]["inventory"]
        with self.assertRaisesRegex(ContractError,"CAMPAIGN_REQUIRED"):
            f.native.reader.action_summary(info["native_start"]["offset"],f.native.reader.snapshot()["offset"],
                task_id=info["task_id"],output_root=info["output_root"])
        proof=f.c.root/"runs/run-01/nodes/inventory/operations/local-execute/task-actor.json"
        value=json_ref(file_ref(proof));value["output_root"]+="-foreign";write_json(proof,value)
        with self.assertRaisesRegex(ContractError,"ACTOR_PROOF_CHANGED"):self.summary(f)

    def test_stop_after_stage_intent_prevents_spawn_and_consumes_original(self):
        f=self.fixture()
        from orchestration_coordinator import Coordinator
        original=Coordinator._gate;calls=[]
        def gate(co,**kwargs):
            if kwargs.get("new_work"):
                calls.append(1)
                if len(calls)==2:raise ContractError("fixture STOP")
            return original(co,**kwargs)
        with patch.object(Coordinator,"_gate",gate),patch.object(local.subprocess,"run") as run:
            with self.assertRaisesRegex(ContractError,"fixture STOP"):f.call("local-execute")
            run.assert_not_called()
        stage=f.c.root/"runs/run-01/nodes/inventory/operations/local-execute"
        self.assertTrue((stage/"intent.json").exists())
        self.assertEqual(json_ref(file_ref(stage/"result.json"))["status"],"ERROR")


class ActorBoundaryTests(unittest.TestCase):
    def test_no_live_actor_cannot_execute(self):
        with self.assertRaisesRegex(ContractError,"LIVE_ACTOR_REQUIRED"):local._actor(None,None,None)


class AdvanceTests(unittest.TestCase):
    def fixture(self):
        helper=LocalTests();self.addCleanup(helper.doCleanups);f=helper.fixture(advance=True)
        import upgrade_task_advance
        p=patch.object(upgrade_task_advance,"_owner",return_value=f.local_owner);p.start();self.addCleanup(p.stop)
        return f

    def observe(self,f,name):
        f.call(name);f.complete(name)
        return json_ref(f.ledger.inspect("inner-"+name)["receipt"])["result"]

    def test_proposals_preserve_separate_model_actions_and_original_budgets(self):
        f=self.fixture()
        result=self.observe(f,"advance-claimed")
        self.assertEqual([p["operation_id"] for p in result["proposals"]],["local-execute"])
        self.assertEqual(result["business_actions_executed"],0)
        self.assertIsNone(f.ledger.db.execute("SELECT 1 FROM calls WHERE operation_id=\"local-execute\"").fetchone())
        f.call("local-execute");f.complete("local-execute")
        result=self.observe(f,"advance-executed")
        self.assertEqual([p["operation_id"] for p in result["proposals"]],["candidate"])
        f.call("candidate");f.complete("candidate")
        result=self.observe(f,"advance-candidate")
        self.assertEqual([p["operation_id"] for p in result["proposals"]],["accept"])
        self.assertTrue(result["proposals"][0]["review_required"])
        self.assertEqual(f.runner.status("run-01")["states"]["inventory"],"EVIDENCE_READY")

    def test_a_arm_cannot_register_advance_and_no_actor_cannot_enter(self):
        from upgrade_task_campaign import operation_payload
        import upgrade_task_advance
        f=self.fixture();campaign=copy.deepcopy(f.c.campaign);campaign["arm"]="A"
        with self.assertRaisesRegex(ContractError,"B_ONLY"):
            operation_payload(campaign,"advance","run-01",observation="a")
        with self.assertRaisesRegex(ContractError,"FIXED_OBSERVATION_REQUIRED"):
            f.runner.advance("run-01",max_steps=2)


if __name__=="__main__":unittest.main()
