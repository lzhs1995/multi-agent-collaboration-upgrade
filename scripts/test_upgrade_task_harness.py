"""Exact actor phase/ledger tests with explicit offline kernel and harness I/O.

Original .84 argparse and actual stream isolation are tested separately. No
model, client enrollment or live sender authority is asserted by these fixtures.
"""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager,nullcontext,redirect_stdout,redirect_stderr
from contextvars import copy_context
import copy
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
from orchestration_transport import PinnedRuntime
from upgrade_live_patch import explicit_harness_main
import upgrade_task_harness as harness
from upgrade_task_streams import capture
from test_upgrade_task_target import SOURCE
import test_upgrade_task_transport as transport_fixtures


class CaptureTests(unittest.TestCase):
    def test_owner_capture_other_thread_forwarding_and_exception_restore(self):
        outer,outer_err,out,err=(io.StringIO() for _ in range(4))
        with redirect_stdout(outer),redirect_stderr(outer_err):
            before=(sys.stdout,sys.stderr)
            with self.assertRaisesRegex(RuntimeError,"fixture failure"):
                with capture(out,err):
                    print("owner");print("owner-error",file=sys.stderr)
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(lambda:print("other-thread")).result(timeout=5)
                    with self.assertRaisesRegex(ContractError,"CAPTURE_ALREADY_ACTIVE"):
                        with capture(io.StringIO(),io.StringIO()):pass
                    with self.assertRaisesRegex(io.UnsupportedOperation,"RAW_STREAM_FD_REFUSED"):sys.stdout.fileno()
                    raise RuntimeError("fixture failure")
            self.assertEqual((sys.stdout,sys.stderr),before)
        self.assertEqual(out.getvalue(),"owner\n");self.assertEqual(err.getvalue(),"owner-error\n")
        self.assertEqual(outer.getvalue(),"other-thread\n");self.assertEqual(outer_err.getvalue(),"")

    def test_async_child_keeps_original_stream(self):
        import asyncio
        outer,out,err=(io.StringIO() for _ in range(3))
        async def other():print("other-task")
        async def work():
            with capture(out,err):
                print("owner-task");await asyncio.create_task(other())
        with redirect_stdout(outer):asyncio.run(work())
        self.assertEqual(out.getvalue(),"owner-task\n");self.assertEqual(outer.getvalue(),"other-task\n")


@unittest.skipUnless(SOURCE.is_dir(),"original .84 source required")
class OriginalParserTests(unittest.TestCase):
    def test_explicit_and_default_argv_use_original_parser_without_mutating_process_argv(self):
        source=explicit_harness_main((SOURCE/"scripts/mac_harness.py").read_text())
        main=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=="main")
        calls=[];namespace=dict(sys=sys,argparse=argparse,LEASE_DEFAULT_TTL_SECONDS=600,
            DEFAULT_TASK_ID="multi-agent-task",_supervisor_provider=lambda value:value)
        namespace.update({n.id:lambda args:calls.append(vars(args)) for n in ast.walk(main)
            if isinstance(n,ast.Name) and n.id.startswith("cmd_")})
        exec(compile(ast.Module(body=[main],type_ignores=[]),"original84-explicit-main","exec"),namespace)
        with patch.object(sys,"argv",["harness","map","--task-id","ambient"]):
            original=sys.argv
            namespace["main"](["map","--task-id","explicit","--artifact-root","/fixture/task"])
            self.assertIs(sys.argv,original)
            namespace["main"]()
            self.assertIs(sys.argv,original)
        self.assertEqual([c["task_id"] for c in calls],["explicit","ambient"])
        self.assertTrue(all(c["task_id_explicit"] for c in calls))
        with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            namespace["main"](["arbitrary-command"])
        self.assertEqual(error.exception.code,2)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        helper=transport_fixtures.TypedTransportTests();self.addCleanup(helper.doCleanups)
        self.f,self.fake=helper.fixture();self.transport=helper.transport(self.f)
        f=self.f;self.runtime=PinnedRuntime(f.c.campaign);self.saved_contexts=[];self.scope_calls=0
        enrollment=dict(schema="upgrade-task-campaign-enrollment-v1",campaign=f.runner.ref,
            registration=f.reg_ref,authority=f.c.campaign["authorization"],runtime=f.c.campaign["runtime"],
            adoption=f.c.campaign["client_isolation"],controller_pid=os.getpid(),
            ledger=dict(path=str(f.ledger.path),device=f.ledger.identity[0],inode=f.ledger.identity[1]))
        self.owner=SimpleNamespace(campaign=f.c.campaign,runner=f.runner,ledger=f.ledger,
            operations={k:v["payload"] for k,v in f.registration["operations"].items()},
            proof=write_json(f.control/"campaign-enrollment.json",enrollment))
        original=self.fake.harness
        def main(argv):
            self.saved_contexts.append(copy_context())
            self.assertEqual(json_ref(harness.current_scope())["phase"],argv[0])
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(copy_context().run,harness.current_scope)
                with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):future.result(timeout=5)
            temp=f.control/"fake-harness-output"/argv[0];temp.mkdir(parents=True)
            # Original offline harness writes synthetic Claude evidence only.
            original([*self.runtime.harness_argv(argv[0],self.transport.current()[1]["task_id"],
                self.transport.root/"preflight",executor_id="claude_a")],temp)
            print("fixture phase "+argv[0]);print("fixture diagnostic",file=sys.stderr)
            if getattr(self,"fail_phase",None)==argv[0]:raise SystemExit(19)
        @contextmanager
        def modules(runtime,*names):
            self.assertIs(runtime,self.runtime)
            self.assertEqual(names,("mac_harness","cmux_workspace_guard","mac_upgrade_task_bridge_context"))
            yield (SimpleNamespace(main=main,_UPGRADE_TASK_BRIDGE_PARTICIPANTS=True),None,None)
        def context(runtime,argv,root):
            self.scope_calls+=1
            if getattr(self,"stop_after_initial",False) and self.scope_calls>1:
                raise ContractError("fixture STOP_REQUESTED")
            request=f.ledger.inspect("inner-prepare")["request"]
            received=json_ref(file_ref(f.directory/"prepare.json"))["received"]
            return harness._scope(runtime,argv,root,self.owner,request,received)
        from orchestration_native import NativeReader
        for patcher in (
            patch.object(harness,"_context",side_effect=context),
            patch("upgrade_task_bridge.activate",side_effect=lambda *a:nullcontext()),
            patch.object(PinnedRuntime,"modules",modules),
            patch("orchestration_native.NativeReader",side_effect=lambda session,**kwargs:NativeReader(session,fixtures=True))):
            patcher.start();self.addCleanup(patcher.stop)
        def execute(argv,root):
            self.actual_argv=list(argv);self.actual_root=root
            if getattr(self,"mutate",None):self.mutate(argv)
            return self.runtime.harness(argv,root)
        self.fake.harness=execute

    def test_original_typed_prepare_keeps_same_process_phase_provenance_and_history(self):
        self.f.call("prepare");self.f.complete("prepare")
        co=self.f.runner.co("run-01");info=co.state()[1]["review"]
        harness.verify_prepared_harness(self.f.c.campaign,co.header,co.nodes["review"],info,fixtures=True)
        self.assertEqual(self.f.runner.status("run-01")["states"]["review"],"PREPARED")
        stage=self.transport.root/"operations/preflight"
        result=json_ref(file_ref(stage/"result.json"))["value"]
        self.assertIn("fixture phase preflight",Path(result["stdout"]["path"]).read_text())
        self.assertEqual(json_ref(result["task_actor"])["controller_pid"],os.getpid())
        with self.assertRaisesRegex(ContractError,"ACTIVE_PHASE_REQUIRED"):harness.current_scope()
        with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
            self.saved_contexts[0].run(harness.current_scope)
        self.assertEqual(self.fake.calls,["preflight","map","surface-inventory","task-pack","finalize-pack","notice"])
        with self.assertRaises(ContractError):self.f.call("prepare")
        self.assertEqual(self.fake.calls.count("preflight"),1)

    def test_extra_argv_cannot_start_harness_or_create_phase_provenance(self):
        self.mutate=lambda argv:argv.extend(["--spawn","--spawn-authorized"])
        with self.assertRaisesRegex(ContractError,"EXACT_ARGUMENTS_REQUIRED"):self.f.call("prepare")
        self.assertEqual(self.fake.calls,[])
        self.assertFalse((self.transport.root/"operations/preflight/task-actor.json").exists())
        self.assertEqual(self.f.ledger.inspect("inner-prepare")["phase"],"UNKNOWN")

    def test_scope_stop_after_phase_intent_precedes_harness(self):
        self.stop_after_initial=True
        before=(sys.stdout,sys.stderr)
        with self.assertRaisesRegex(ContractError,"STOP_REQUESTED"):self.f.call("prepare")
        self.assertEqual((sys.stdout,sys.stderr),before)
        self.assertEqual(self.fake.calls,[])
        stage=self.transport.root/"operations/preflight"
        self.assertTrue((stage/"task-actor.json").exists())
        self.assertEqual(json_ref(file_ref(stage/"result.json"))["status"],"ERROR")
        with self.assertRaisesRegex(ContractError,"no replay"):self.transport.prepare()

    def test_system_exit_is_original_returned_failure_with_no_second_phase(self):
        self.fail_phase="preflight"
        with self.assertRaisesRegex(ContractError,"original preflight failed"):self.f.call("prepare")
        result=json_ref(file_ref(self.transport.root/"operations/preflight/result.json"))
        self.assertEqual(result["status"],"RETURNED");self.assertEqual(result["value"]["returncode"],19)
        with self.assertRaisesRegex(ContractError,"original preflight failed"):self.transport.prepare()
        self.assertEqual(self.fake.calls,["preflight"])

    def test_removing_or_retargeting_historical_actor_proof_is_not_legacy_success(self):
        self.f.call("prepare");self.f.complete("prepare")
        co=self.f.runner.co("run-01");info=co.state()[1]["review"]
        stage=self.transport.root/"operations/preflight";path=stage/"result.json"
        result=json_ref(file_ref(path));changed=copy.deepcopy(result);changed["value"].pop("task_actor")
        write_json(path,changed)
        with self.assertRaisesRegex(ContractError,"PROVEN_SUCCESS_REQUIRED"):
            harness.verify_prepared_harness(self.f.c.campaign,co.header,co.nodes["review"],info,fixtures=True)
        write_json(path,result)
        proof=json_ref(result["value"]["task_actor"]);proof["lane"]="claude_b"
        result["value"]["task_actor"]=write_json(stage/"task-actor.json",proof);write_json(path,result)
        with self.assertRaisesRegex(ContractError,"PROVENANCE_CHANGED"):
            harness.verify_prepared_harness(self.f.c.campaign,co.header,co.nodes["review"],info,fixtures=True)


class NoAuthorityTests(unittest.TestCase):
    def test_task_runtime_without_active_actor_refuses_before_files_or_subprocess(self):
        with patch("subprocess.run") as subprocess_run:
            with self.assertRaisesRegex(ContractError,"LIVE_ACTOR_REQUIRED"):
                PinnedRuntime(dict(task_controls={},controller={"path":"/fixture"})).harness([],Path("/fixture"))
            subprocess_run.assert_not_called()


@unittest.skipUnless(SOURCE.is_dir(),"original .84 source required")
class GeneratedEntryTests(unittest.TestCase):
    def test_actual_complete_generated_controller_main_uses_explicit_argv(self):
        import subprocess
        import test_upgrade_live_startup as materialized
        self.source_controller=SOURCE
        materialized.MaterializedStartupTests.setUpClass.__func__(self)
        self.addCleanup(self.tmp.cleanup)
        code='''import sys,inspect
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import orchestration_common
from orchestration_transport import PinnedRuntime
runtime=PinnedRuntime(orchestration_common.json_ref(__import__('json').loads(sys.argv[2])))
with runtime.modules('mac_harness') as (harness,):
 assert 'argv' in inspect.signature(harness.main).parameters
 calls=[]
 harness.cmd_map=lambda args:calls.append(vars(args))
 before=list(sys.argv)
 harness.main(['map','--task-id','explicit-generated','--artifact-root','/fixture'])
 assert sys.argv==before
 assert calls[0]['task_id']=='explicit-generated' and calls[0]['task_id_explicit']
assert sys.modules['orchestration_common'] is orchestration_common
print('GENERATED84_EXPLICIT_MAIN_AND_DISTINCT_MODULES_PASS')
'''
        import json
        result=subprocess.run([str(materialized.SANDBOX),'-f',self.spec['pins']['control_sandbox']['path'],
            sys.executable,'-I','-B','-c',code,str(Path(__file__).resolve().parent),
            json.dumps(self.runtime_ref)],cwd=self.root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('GENERATED84_EXPLICIT_MAIN_AND_DISTINCT_MODULES_PASS',result.stdout)


if __name__=="__main__":unittest.main()
