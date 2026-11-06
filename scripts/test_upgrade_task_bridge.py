"""Original .84 scope rules with explicit mocked actor/kernel observations.

No live sender, model, session registration or adoption is claimed.
"""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError
import upgrade_task_bridge_context as context
from upgrade_task_bridge import caller_in_tree
from upgrade_task_bridge_patch import task_bridge_paths
from test_upgrade_task_target import SOURCE,WS,SURFACE,SENDER,PANE,SID

OTHER="99999999-9999-4999-8999-999999999999"


@unittest.skipUnless(SOURCE.is_dir(),"original .84 source required")
class BridgeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        self.tree=dict(windows=[dict(ref="window:1",workspaces=[dict(id=WS,ref="workspace:1",panes=[
            dict(id=PANE,ref="pane:1",surfaces=[dict(id=SENDER,ref="surface:1",type="terminal"),
                dict(id=SURFACE,ref="surface:2",type="terminal"),dict(id=OTHER,ref="surface:3",type="terminal")])])])])
        self.sender=dict(surface_uuid=SENDER,workspace_uuid=WS,pane_uuid=PANE,provider="codex",session_id=SID)
        self.target=dict(self.sender,surface_uuid=SURFACE,provider="claude",session_id=OTHER)
        self.value=dict(identity_source=context.SOURCE,foreground_authentication=False,
            task_actor={"path":"/fixture/task-actor.json"},caller=caller_in_tree(self.tree,self.sender),
            tree=self.tree,supervisor=self.sender,target=self.target)
        self.observe=lambda:copy.deepcopy(self.value)
        self.guard=self.original("cmux_workspace_guard.py",{"require_same_workspace","resolve_snapshot","uuid_value","deny"})
        import uuid
        self.guard["uuid"]=uuid
        self.guard["WorkspaceScopeError"]=type("WorkspaceScopeError",(RuntimeError,),{})
        self.guard["SCOPE_DIR"]=self.root;self.guard["json"]=json
        self.guard["caller_snapshot"]=lambda:(_ for _ in ()).throw(AssertionError("no foreground substitution"))
        self.bridge=self.original("cmux_bridge.py",{"whoami","list_surfaces"})
        self.hooks=self.original("cmux_hook_scope.py",{"bind_participants"})
        self.harness=self.original("mac_harness.py",{"_workspace_key"})

    def original(self,name,names):
        raw=task_bridge_paths(name,(SOURCE/"scripts"/name).read_text())
        namespace={}
        nodes=[n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual(len(nodes),len(names))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),"original84-explicit-actor-"+name,"exec"),namespace)
        return namespace

    def test_original_bridge_uses_explicit_actor_and_actual_tree_without_foreground(self):
        with context.activate(self.observe):
            who=self.bridge["whoami"]()
            self.assertEqual(who["identity_source"],context.SOURCE)
            self.assertNotIn("foreground",who)
            self.assertEqual(self.harness["_workspace_key"](),WS)
            rows=self.bridge["list_surfaces"](WS)
            self.assertEqual([r["ref"] for r in rows if r["selected"]],["surface:1"])
            proof=self.guard["require_same_workspace"](SURFACE)
            self.assertEqual(proof["task_actor"],self.value["task_actor"])
            self.assertEqual(proof["target_surface_uuid"],SURFACE.upper())

    def test_scope_file_and_original_expected_binding_still_narrow_actor(self):
        with context.activate(self.observe):
            with self.assertRaisesRegex(ContractError,"TARGET_OUTSIDE_OPERATION"):
                self.guard["require_same_workspace"](OTHER)
            with self.assertRaisesRegex(RuntimeError,"designated or previously bound identity changed"):
                self.guard["require_same_workspace"](SURFACE,expected={"target_pane_uuid":OTHER})
            (self.root/(SENDER.upper()+".json")).write_text(json.dumps(dict(version=1,
                caller_surface_uuid=SENDER,workspace_uuid=WS,target_surface_uuids=[OTHER])))
            with self.assertRaisesRegex(RuntimeError,"not the user-designated executor"):
                self.guard["require_same_workspace"](SURFACE)

    def test_unregistered_workspace_and_changed_endpoint_tree_refuse(self):
        with context.activate(self.observe):
            with self.assertRaisesRegex(ContractError,"FOREIGN_WORKSPACE"):
                self.bridge["list_surfaces"]("workspace:999")
        moved=copy.deepcopy(self.tree);moved["windows"][0]["workspaces"][0]["panes"][0]["id"]=OTHER
        with self.assertRaisesRegex(ContractError,"DISPLAY_CHANGED"):caller_in_tree(moved,self.sender)
        doubled=copy.deepcopy(self.tree);doubled["windows"]*=2
        with self.assertRaisesRegex(ContractError,"MISSING_OR_AMBIGUOUS"):caller_in_tree(doubled,self.sender)

    def test_participants_carry_task_source_and_exact_original_sessions(self):
        rows=[dict(role="supervisor",surface_uuid=SENDER,provider="codex"),
              dict(role="executor",surface_uuid=SURFACE,provider="claude")]
        with context.activate(self.observe):
            actual=self.hooks["bind_participants"](None,WS,rows)
            self.assertEqual([r["native_session_id"] for r in actual],[SID,OTHER])
            self.assertTrue(all(r["identity_source"]==context.SOURCE for r in actual))
            with self.assertRaisesRegex(ContractError,"PARTICIPANT_SCOPE_CHANGED"):
                self.hooks["bind_participants"](None,WS,rows[::-1])

    def test_copy_thread_async_and_expired_contexts_cannot_inherit_actor(self):
        with context.activate(self.observe):
            saved=copy_context()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
                    pool.submit(saved.run,context.snapshot).result(timeout=5)
        with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):saved.run(context.snapshot)
        async def child():return context.snapshot()
        async def parent():
            with context.activate(self.observe):
                with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
                    await asyncio.create_task(child())
        asyncio.run(parent())
        self.assertIsNone(context.snapshot())

    def test_no_context_preserves_original_native_guard(self):
        expected={"caller_surface_uuid":SENDER}
        self.guard["caller_snapshot"]=lambda:({}, {}, {}, None)
        calls=[]
        def resolver(*args,**kwargs):calls.append((args,kwargs));return expected
        self.guard["resolve_snapshot"]=resolver
        self.assertEqual(self.guard["require_same_workspace"](SURFACE),expected)
        self.assertEqual(len(calls),1)

    def test_observer_failure_and_nested_context_refuse_without_cached_authority(self):
        fail=False
        def observer():
            if fail:raise ContractError("fixture STOP_REQUESTED")
            return self.observe()
        with context.activate(observer):
            with self.assertRaisesRegex(ContractError,"CONTEXT_UNAVAILABLE"):
                with context.activate(observer):pass
            fail=True
            with self.assertRaisesRegex(ContractError,"STOP_REQUESTED"):self.bridge["whoami"]()
        self.assertIsNone(context.snapshot())


@unittest.skipUnless(SOURCE.is_dir(),"original .84 source required")
class GeneratedBridgeTests(unittest.TestCase):
    def test_complete_generated_bridge_uses_its_own_guard_and_explicit_context(self):
        import subprocess,sys
        import test_upgrade_live_startup as materialized
        from orchestration_common import json_ref
        self.source_controller=SOURCE
        materialized.MaterializedStartupTests.setUpClass.__func__(self)
        self.addCleanup(self.tmp.cleanup)
        code='''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from orchestration_transport import PinnedRuntime
from orchestration_common import json_ref
runtime=PinnedRuntime(json_ref(json.loads(sys.argv[2])))
with runtime.modules('cmux_bridge','cmux_workspace_guard','mac_upgrade_task_bridge_context','cmux_hook_scope','mac_harness','mac_upgrade_task_sender') as (bridge,guard,context,hooks,harness,sender):
 assert Path(sender.__file__).resolve().parent==runtime.scripts
 def forbidden(*a,**kw):raise AssertionError('foreground called')
 guard.caller_snapshot=forbidden
 value=json.loads(sys.argv[3])
 with context.activate(lambda:value):
  assert bridge.whoami()['task_actor']==value['task_actor']
  assert sender.snapshot()['task_actor']==value['task_actor']
  assert harness._workspace_key()==value['caller']['workspace_id']
  assert len(bridge.list_surfaces())==3
  proof=bridge.pin_workspace(value['target']['surface_uuid'])
  assert proof['identity_source']==context.SOURCE
  rows=[dict(role=role,surface_uuid=value[key]['surface_uuid'],provider=value[key]['provider']) for role,key in [('supervisor','supervisor'),('executor','target')]]
  assert hooks.bind_participants(bridge,value['caller']['workspace_id'],rows)[0]['native_session_id']==value['supervisor']['session_id']
 assert context.snapshot() is None
 assert sender.snapshot() is None
print('GENERATED84_EXPLICIT_BRIDGE_PASS')
'''
        f=BridgeTests();f.setUp();self.addCleanup(f.doCleanups)
        result=subprocess.run([str(materialized.SANDBOX),'-f',self.spec['pins']['control_sandbox']['path'],
            sys.executable,'-I','-B','-c',code,str(Path(__file__).resolve().parent),
            json.dumps(self.runtime_ref),json.dumps(f.value)],capture_output=True,text=True,timeout=30,cwd=self.root)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('GENERATED84_EXPLICIT_BRIDGE_PASS',result.stdout)


class GateReaderTests(unittest.TestCase):
    def test_original_phase_provenance_required_on_gate_members_and_workspace_proof(self):
        import test_upgrade_task_harness as fixture
        from orchestration_common import file_ref,json_ref
        from upgrade_task_bridge import verify_gate
        f=fixture.HarnessTests();f.setUp();self.addCleanup(f.doCleanups)
        f.f.call('prepare');f.f.complete('prepare')
        co=f.f.runner.co('run-01');info=co.state()[1]['review'];campaign=f.f.c.campaign
        gate=json_ref(info['prepared']['identity_gate'])
        proof=file_ref(f.transport.root/'operations/preflight/task-actor.json')
        for value in [gate,*gate['native_participants'],*gate['workspace_proofs'].values()]:
            value.update(task_actor=proof,identity_source=context.SOURCE)
        verify_gate(campaign,co.header,co.nodes['review'],info,gate,fixtures=True)
        for select in (lambda g:g,lambda g:g['native_participants'][0],
                       lambda g:next(iter(g['workspace_proofs'].values()))):
            bad=copy.deepcopy(gate);select(bad).pop('task_actor')
            with self.assertRaisesRegex(ContractError,'PROVENANCE_CHANGED'):
                verify_gate(campaign,co.header,co.nodes['review'],info,bad,fixtures=True)


class ObserverTests(unittest.TestCase):
    def test_tree_observation_rechecks_original_phase_and_fails_on_changed_scope(self):
        import upgrade_task_bridge as bridge
        from upgrade_task_enrollment import TaskCampaignGate,_active
        from orchestration_common import write_json
        f=BridgeTests();f.setUp();self.addCleanup(f.doCleanups)
        campaign=dict(sessions=dict(codex=f.sender,claude_a=f.target))
        owner=object.__new__(TaskCampaignGate);owner.campaign=campaign;owner.runner=SimpleNamespace(ref={"fixture":True})
        proof=write_json(f.root/'phase.json',dict(campaign=owner.runner.ref,lane='claude_a'))
        runtime=SimpleNamespace(campaign=campaign)
        reads=[]
        def tree(*args):reads.append(args);return copy.deepcopy(f.tree)
        guard=SimpleNamespace(_read_json_command=tree)
        token=_active.set((owner,None,None));self.addCleanup(_active.reset,token)
        with patch.object(bridge,'current_scope',return_value=proof) as scope:
            value=bridge.observe(runtime,guard)
            self.assertEqual(scope.call_count,2)
            self.assertFalse(value['foreground_authentication'])
            self.assertEqual(value['target'],f.target)
        with patch.object(bridge,'current_scope',side_effect=[proof,ContractError('fixture STOP_REQUESTED')]):
            with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):bridge.observe(runtime,guard)
        self.assertEqual(reads,[('tree','--all','--json','--id-format','both')]*2)


class SourceCompatibilityTests(unittest.TestCase):
    def test_source_transform_keeps_supported_legacy_sources_renderable(self):
        roots=[Path.home()/'.local/share/multi-agent-collaboration/releases'/release/'source'
               for release in ('0.4.34-d333557fb498','0.4.58-cf3f6175840b')]
        for root in roots:
            if not root.is_dir():continue
            for name in ('cmux_bridge.py','cmux_workspace_guard.py','cmux_hook_scope.py','mac_harness.py'):
                path=root/'scripts'/name
                if not path.is_file():continue
                with self.subTest(release=root.parent.name,file=name):
                    compile(task_bridge_paths(name,path.read_text()),str(path),'exec')


if __name__=="__main__":unittest.main()
