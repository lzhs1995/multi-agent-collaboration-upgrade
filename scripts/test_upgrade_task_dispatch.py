"""Original typed dispatch with explicit offline kernel and delivery fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import copy
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
from orchestration_transport import PinnedRuntime
import upgrade_task_dispatch as dispatch
import upgrade_task_bridge_context as adapter
import upgrade_task_sender as sender
from upgrade_task_sender_evidence import verify_source
import test_upgrade_task_transport as fixtures


class DispatchTests(unittest.TestCase):
    def setUp(self):
        helper=fixtures.TypedTransportTests();self.addCleanup(helper.doCleanups)
        self.f,self.fake=helper.fixture();f=self.f;self.transport=helper.transport(f)
        f.call("prepare");f.complete("prepare")
        self.runtime=PinnedRuntime(f.c.campaign);self.saved=[];self.scope_calls=0
        enrollment=dict(schema="upgrade-task-campaign-enrollment-v1",campaign=f.runner.ref,
            registration=f.reg_ref,authority=f.c.campaign["authorization"],runtime=f.c.campaign["runtime"],
            adoption=f.c.campaign["client_isolation"],controller_pid=os.getpid(),
            ledger=dict(path=str(f.ledger.path),device=f.ledger.identity[0],inode=f.ledger.identity[1]))
        self.owner=SimpleNamespace(campaign=f.c.campaign,runner=f.runner,ledger=f.ledger,
            operations={k:v["payload"] for k,v in f.registration["operations"].items()},
            proof=write_json(f.control/"campaign-enrollment.json",enrollment))
        original=self.fake.submit
        self.binding=None
        def submit(surface,notice,pack,**kwargs):
            self.saved.append(copy_context())
            proof=dispatch.current_scope();self.assertEqual(adapter.snapshot()["task_actor"],proof)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
                    pool.submit(copy_context().run,dispatch.current_scope).result(timeout=5)
            require_source=dict(identity_source=adapter.SOURCE,task_actor=proof)
            # The legacy fixture owns real temporary receipt/journal writes;
            # it does not simulate a real cmux/model delivery.
            result=original(surface,notice,pack,reconcile_only=kwargs["reconcile_only"])
            info=self.transport.current()[1]
            path=next(self.fake.dispatch_root.glob("attempt-*.json"));attempt=json_ref(file_ref(path))
            self.binding=attempt["native_binding"]
            with patch.object(sender,"_task_caller",return_value=True):
                sender.bind(self.binding,require_source)
                sender.require_live(self.binding)
            return result
        self.bridge=SimpleNamespace(submit_task_pack=submit)
        self.guard=SimpleNamespace(__file__=str(self.runtime.scripts/"cmux_workspace_guard.py"))
        self.fake_adapter=SimpleNamespace(__file__=str(self.runtime.scripts/"mac_upgrade_task_bridge_context.py"),activate=adapter.activate)
        @contextmanager
        def modules(runtime,*names):
            self.assertIs(runtime,self.runtime)
            self.assertEqual(names,("cmux_bridge","cmux_workspace_guard","mac_upgrade_task_bridge_context"))
            yield self.bridge,self.guard,self.fake_adapter
        def context(runtime,surface,notice,pack):
            self.scope_calls+=1
            if getattr(self,"stop_after",False) and self.scope_calls>1:raise ContractError("fixture STOP_REQUESTED")
            request=f.ledger.inspect("inner-dispatch")["request"]
            received=json_ref(file_ref(f.directory/"dispatch.json"))["received"]
            return dispatch._scope(runtime,surface,notice,pack,self.owner,request,received)
        def observe(runtime,guard,reader):
            return dict(identity_source=adapter.SOURCE,foreground_authentication=False,task_actor=reader())
        from orchestration_native import NativeReader
        for p in (patch.object(dispatch,"_context",side_effect=context),patch.object(PinnedRuntime,"modules",modules),
                  patch("upgrade_task_bridge.observe",side_effect=observe),
                  patch("orchestration_native.NativeReader",side_effect=lambda s,**kw:NativeReader(s,fixtures=True))):
            p.start();self.addCleanup(p.stop)
        def execute(surface,notice,pack,*,reconcile_only):
            if getattr(self,"wrong_target",False):surface=f.c.campaign["sessions"]["claude_b"]["surface_uuid"]
            return self.runtime.submit(surface,notice,pack,reconcile_only=reconcile_only)
        self.fake.submit=execute

    def test_exact_original_dispatch_once_and_historical_source_survive_completed_phase(self):
        self.f.call("dispatch");self.f.complete("dispatch")
        co=self.f.runner.co("run-01");info=co.state()[1]["review"]
        proof=file_ref(self.transport.root/"operations/submit-task-pack/task-actor.json")
        dispatch.verify_provenance(self.f.c.campaign,co.header,co.nodes["review"],info,proof,fixtures=True)
        verify_source(self.binding,self.f.c.campaign["sessions"]["codex"],
                      self.f.c.campaign["sessions"]["claude_a"],fixtures=True)
        self.assertEqual(self.fake.calls.count(("submit",False)),1)
        with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
            self.saved[0].run(dispatch.current_scope)
        before=list(self.fake.calls);self.transport.dispatch()
        self.assertEqual(self.fake.calls,before)
        with self.assertRaises(ContractError):self.f.call("dispatch")

    def test_foreign_lane_stops_before_bridge_and_keeps_original_uncertainty(self):
        self.wrong_target=True;self.f.call("dispatch");self.f.complete("dispatch")
        self.assertEqual(self.transport.current()[0].value,"UNCERTAIN")
        self.assertFalse(self.saved)
        self.assertFalse((self.transport.root/"operations/submit-task-pack/task-actor.json").exists())

    def test_stop_after_actor_file_keeps_intent_without_call_or_replay(self):
        self.stop_after=True;self.f.call("dispatch");self.f.complete("dispatch")
        path=self.transport.root/"operations/submit-task-pack"
        self.assertTrue((path/"task-actor.json").exists());self.assertFalse(self.saved)
        self.assertEqual(json_ref(file_ref(path/"result.json"))["status"],"ERROR")
        self.transport.dispatch();self.assertFalse(self.saved)

    def test_unknown_original_send_keeps_both_journals_and_no_second_submission(self):
        self.fake.fail_submit=True;self.f.call("dispatch");self.f.complete("dispatch")
        self.assertEqual(self.transport.current()[0].value,"UNCERTAIN")
        self.transport.dispatch();self.transport.reconcile()
        self.assertEqual(self.fake.calls.count(("submit",False)),1)
        self.assertEqual(self.fake.calls.count(("submit",True)),0)

    def test_missing_or_foreign_dispatch_provenance_refuses_reader(self):
        self.f.call("dispatch");self.f.complete("dispatch")
        co=self.f.runner.co("run-01");info=co.state()[1]["review"]
        path=self.transport.root/"operations/submit-task-pack/task-actor.json"
        value=json_ref(file_ref(path));value["lane"]="claude_b"
        ref=write_json(path,value)
        with self.assertRaisesRegex(ContractError,"PROVENANCE_CHANGED"):
            dispatch.verify_provenance(self.f.c.campaign,co.header,co.nodes["review"],info,ref,fixtures=True)


class BoundaryTests(unittest.TestCase):
    def test_no_actor_cannot_enter_dispatch(self):
        with self.assertRaisesRegex(ContractError,"LIVE_ACTOR_REQUIRED"):
            dispatch._context(None,None,None,None)


if __name__=="__main__":unittest.main()
