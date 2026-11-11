"""Typed terminal release, real temporary journals, explicit offline platform.

These tests exercise original terminal/lease and bounded recovery logic. No
real agent, model, reservation, launch or adoption is created.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
from orchestration_transport import PinnedRuntime,Transport
from orchestration_native import NativeReader
import upgrade_task_bridge_context as adapter
import upgrade_task_disarm as disarm
import upgrade_task_sender as sender
import test_upgrade_task_campaign as fixtures
from test_orchestration_transport import FakeRuntime


class DisarmTests(unittest.TestCase):
    def setUp(self):
        class Fixture(fixtures.CampaignActorTests):
            transport_node="review"
            transport_actions=("prepare","dispatch","disarm")
            binding_mode=True
            binding_node="review"
            service_mode=True
        f=Fixture();f.setUp();self.addCleanup(f.doCleanups);self.f=f
        f.call("start");f.complete("start");f.call("claim");f.complete("claim")
        co=f.runner.co("run-01");self.fake=FakeRuntime(f.c,co,node_id="review");f.runner.runtime=self.fake
        for name,path in (("handshake_journal",self.fake.handshake_root),("dispatch_journal",self.fake.dispatch_root)):
            p=patch("orchestration_transport."+name,return_value=path);p.start();self.addCleanup(p.stop)
        for name in ("prepare","dispatch"):f.call(name);f.complete(name)
        info=co.state()[1]["review"];f.c.work(info,"claude_a");f.c.callback(co,"review")
        f.call("candidate");f.complete("candidate");f.call("accept");f.complete("accept")
        self.runtime=PinnedRuntime(f.c.campaign);f.runner.runtime=self.runtime
        self.transport=f.runner.transport(f.runner.co("run-01"),"review")
        enrollment=dict(schema="upgrade-task-campaign-enrollment-v1",campaign=f.runner.ref,
            registration=f.reg_ref,authority=f.c.campaign["authorization"],runtime=f.c.campaign["runtime"],
            adoption=f.c.campaign["client_isolation"],controller_pid=os.getpid(),
            ledger=dict(path=str(f.ledger.path),device=f.ledger.identity[0],inode=f.ledger.identity[1]))
        self.owner=SimpleNamespace(campaign=f.c.campaign,runner=f.runner,ledger=f.ledger,
            operations={k:v["payload"] for k,v in f.registration["operations"].items()},
            proof=write_json(f.control/"campaign-enrollment.json",enrollment))
        self.scope_calls=0;self.saved=[]
        def context(transport):
            self.scope_calls+=1
            if getattr(self,"stop",False) and self.scope_calls>1:raise ContractError("fixture STOP_REQUESTED")
            request=f.ledger.inspect("inner-disarm")["request"]
            received=json_ref(file_ref(f.directory/"disarm.json"))["received"]
            return disarm._scope(transport,self.owner,request,received)
        def observe(runtime,guard,reader):
            return dict(identity_source=adapter.SOURCE,foreground_authentication=False,task_actor=reader())
        guard=SimpleNamespace(__file__=str(self.runtime.scripts/"cmux_workspace_guard.py"))
        context_module=SimpleNamespace(__file__=str(self.runtime.scripts/"mac_upgrade_task_bridge_context.py"),activate=adapter.activate)
        @contextmanager
        def modules(runtime,*names):
            if names==("cmux_workspace_guard","mac_upgrade_task_bridge_context"):
                yield guard,context_module
            else:
                self.assertEqual(names,("mac_harness","executor_reask_lifecycle","executor_reask"))
                yield self.fake.disarm_harness,self.fake.lifecycle,self.fake.reask
        self.fake.freeze_disarm(info["task_id"])
        original_remove=self.fake.disarm_harness.disarm_task
        def remove(task):
            self.saved.append(copy_context());proof=disarm.current_scope()
            self.assertEqual(adapter.snapshot()["task_actor"],proof)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
                    pool.submit(copy_context().run,disarm.current_scope).result(timeout=5)
            if getattr(self,"fail_remove",False):
                self.fail_remove=False
                raise RuntimeError("fixture original release error")
            return original_remove(task)
        self.fake.disarm_harness.disarm_task=remove
        # Fixture marker has no live enrollment; marker provenance is tested
        # separately. The full terminal and original recovery readers run here.
        for p in (patch.object(disarm,"_context",side_effect=context),
                  patch.object(disarm,"verify_marker"),
                  patch.object(PinnedRuntime,"modules",modules),
                  patch("upgrade_task_bridge.observe",side_effect=observe),
                  patch("orchestration_native.NativeReader",side_effect=lambda s,**kw:NativeReader(s,fixtures=True))):
            p.start();self.addCleanup(p.stop)
        def invoke(transport):
            with transport.co.node_locks(transport.key):return disarm.run(transport)
        p=patch.object(Transport,"disarm",invoke);p.start();self.addCleanup(p.stop)

    def test_original_typed_terminal_releases_once_and_source_survives_completed_call(self):
        self.f.call("disarm");self.f.complete("disarm")
        co=self.transport.co;info,lane,terminal=disarm.terminal(self.transport)
        proof=disarm.verify_provenance(co,"review",info,terminal,fixtures=True)
        self.assertEqual(json_ref(proof)["lane"],"claude_a")
        self.assertTrue((self.transport.root/"disarm.json").is_file())
        self.assertEqual(len(self.saved),1)
        with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
            self.saved[0].run(disarm.current_scope)
        with self.assertRaises(ContractError):self.f.call("disarm")
        self.assertEqual(len(self.saved),1)

    def test_original_error_keeps_result_and_bounded_recovery_slot_budget(self):
        self.fail_remove=True;self.f.call("disarm");self.f.complete("disarm")
        root=self.transport.root
        self.assertEqual(json_ref(file_ref(root/"operations/scoped-disarm/result.json"))["status"],"ERROR")
        value=json_ref(file_ref(root/"disarm.json"));self.assertEqual(value["proof_kind"],"BOUNDED_RECOVERY")
        self.assertEqual([r["kind"] for r in value["proof"]["slots"]],["observe","act","observe"])
        self.assertEqual(len(self.saved),2)

    def test_stop_after_actor_file_never_touches_original_reservation(self):
        before=file_ref(next(self.fake.active.glob("*/*.json")));self.stop=True
        with self.assertRaisesRegex(ContractError,"STOP_REQUESTED"):self.f.call("disarm")
        self.assertEqual(before,file_ref(before["path"]))
        self.assertTrue((self.transport.root/"disarm-task-actor.json").is_file())
        self.assertFalse((self.transport.root/"disarm-frozen.json").exists());self.assertFalse(self.saved)

    def test_marker_or_terminal_source_cannot_be_borrowed(self):
        self.f.call("disarm");self.f.complete("disarm")
        info,_,terminal=disarm.terminal(self.transport)
        path=self.transport.root/"disarm-task-actor.json";value=json_ref(file_ref(path))
        value["lane"]="claude_b";write_json(path,value)
        with self.assertRaisesRegex(ContractError,"PROVENANCE_CHANGED"):
            disarm.verify_provenance(self.transport.co,"review",info,terminal,fixtures=True)

    def test_release_actor_cannot_be_used_as_native_sender(self):
        self.f.call("disarm");self.f.complete("disarm")
        proof=file_ref(self.transport.root/"disarm-task-actor.json")
        binding=dict(identity={},task_sender=dict(schema=sender.SCHEMA,identity_source=adapter.SOURCE,task_actor=proof))
        with patch.object(sender,"_task_caller",return_value=True):
            with self.assertRaisesRegex(ContractError,"ORIGINAL_OPERATION_REQUIRED"):sender.validate(binding)


class BoundaryTests(unittest.TestCase):
    def test_no_actor_has_no_release_authority(self):
        with self.assertRaisesRegex(ContractError,"LIVE_ACTOR_REQUIRED"):disarm._context(None)


class MarkerTests(unittest.TestCase):
    def test_original_preflight_marker_source_and_sessions_are_required(self):
        import test_upgrade_task_harness as harness_fixture
        from orchestration_test_support import OfflineDisarmRuntime
        from orchestration_disarm import snapshot
        f=harness_fixture.HarnessTests();f.setUp();self.addCleanup(f.doCleanups)
        f.f.call("prepare");f.f.complete("prepare")
        co=f.f.runner.co("run-01");info=co.state()[1]["review"]
        runtime=OfflineDisarmRuntime(f.f.c.campaign,co.root/"marker-fixture",executor_id="claude_a")
        frozen=runtime.freeze_disarm(info["task_id"])
        marker=json_ref(file_ref(frozen["marker"]["path"]))
        marker.update(task_actor=file_ref(f.transport.root/"operations/preflight/task-actor.json"),
            identity_source=adapter.SOURCE,artifact_root=str(f.transport.root/"preflight"))
        for p,lane in zip(marker["participants"],("codex","claude_a")):
            session=f.f.c.campaign["sessions"][lane]
            p.update(provider=session["provider"],native_session_id=session["session_id"])
        write_json(frozen["marker"]["path"],marker);frozen["marker"]=snapshot(frozen["marker"]["path"])
        disarm.verify_marker(co,"review",info,frozen,fixtures=True)
        for mutate in (lambda v:v.pop("task_actor"),
                       lambda v:v["participants"][1].update(native_session_id="other-session"),
                       lambda v:v.update(artifact_root="/other-task/preflight")):
            bad=copy.deepcopy(marker);mutate(bad)
            write_json(frozen["marker"]["path"],bad);changed=dict(frozen,marker=snapshot(frozen["marker"]["path"]))
            with self.assertRaisesRegex(ContractError,"ORIGINAL_MARKER"):
                disarm.verify_marker(co,"review",info,changed,fixtures=True)

    def test_nonterminal_task_never_acquires_release_scope(self):
        import test_upgrade_task_transport as transport_fixture
        helper=transport_fixture.TypedTransportTests();self.addCleanup(helper.doCleanups)
        f,_=helper.fixture();transport=helper.transport(f)
        with self.assertRaisesRegex(ContractError,"VERIFIED_TERMINAL_REQUIRED"):disarm.terminal(transport)
        self.assertFalse((transport.root/"disarm-task-actor.json").exists())


if __name__=="__main__":unittest.main()
