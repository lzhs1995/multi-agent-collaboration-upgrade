"""The original hook observer must correlate both events to the inner call."""
import copy
import json
import os
import shlex
import unittest

from orchestration_common import ContractError, write_json
from upgrade_startup_contract import TASK_SCHEMA
from upgrade_task_readiness import DYNAMIC_HOOK_NAME, task_readiness
import test_upgrade_task_readiness as fixtures


class TaskHookTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.DynamicReadinessTests(); f.setUp()
        self.addCleanup(f.doCleanups);self.f=f
        f.evaluate()
        self.root=f.fixture.root
        self.spec=copy.deepcopy(f.spec)
        self.spec.update(schema=TASK_SCHEMA,controller=str(self.root/"controller"),
            pins={"codex":{"path":"/fixture/codex"},"python":{"path":"/fixture/python"}})
        self.startup=write_json(self.root/"startup-spec.json",{"fixture":True})
        self.binding={"state_root":str(self.root/"state")}
        self.launches={"codex-server":{"pid":999,"birth":[100,1]},"codex":{"pid":888,"birth":[100,2]}}
        self.hook="cmux_supervisor_callback_guard.py"
        self.command=shlex.join(["/fixture/python","-I","-B",
            self.spec["controller"]+"/scripts/cmux_workflow_advisory.py","--hook",self.hook])
        config=write_json(self.root/"hooks.json",{"hooks":{event:[{"matcher":"*","hooks":[{
            "type":"command","command":self.command}]}] for event in ("PreToolUse","PostToolUse")}})
        self.spec["pins"]["codex/hooks.json"]=config
        self.values={}
        for index,event in enumerate(("PreToolUse","PostToolUse")):
            self.values[event]=dict(schema="upgrade-automatic-hook-observation-v2",lane="codex",
                startup=self.startup,adoption_proven=False,direct_client="codex-server",hook=self.hook,
                configuration=config,command=self.command,observed_at_ns=1000+index,
                parents=[dict(pid=999,ppid=1,birth=[100,1],executable="/fixture/codex")],
                payload=dict(session_id=fixtures.SID,transcript_path=str(f.fixture.path),
                    hook_event_name=event,tool_name=DYNAMIC_HOOK_NAME,tool_use_id="call-ready",tool_input={"operation_id":"ready"}))

    def evaluate(self):
        for event,value in self.values.items():
            write_json(self.root/("state/hook-observations/codex/"+event+".json"),value)
        path=self.f.fixture.path;stat=path.stat()
        session=dict(provider="codex",session_id=fixtures.SID,transcript=dict(path=str(path),device=stat.st_dev,
            inode=stat.st_ino,offset=stat.st_size,at_record_boundary=True))
        return task_readiness(self.f.native,session,self.spec,self.startup,self.binding,self.launches)

    def test_dynamic_and_both_original_server_hook_events_required(self):
        proof=self.evaluate()
        self.assertTrue(proof["hooks_proven"])
        self.assertFalse(proof["adoption_proven"])
        self.assertEqual(proof["hook_call_id"],"call-ready")
        self.assertEqual({k:len(v) for k,v in proof["hooks"].items()},{"PreToolUse":1,"PostToolUse":1})

    def test_dynamic_result_cannot_substitute_missing_post(self):
        del self.values["PostToolUse"]
        with self.assertRaisesRegex(ContractError,"PRE_POST_HOOKS_NOT_CORRELATED"):self.evaluate()

    def test_outer_call_or_guessed_namespace_never_matches(self):
        original=copy.deepcopy(self.values["PreToolUse"])
        for patch in ({"tool_use_id":"outer-ready"},{"tool_name":"upgrade_collaboration.execute"},
                      {"tool_name":"upgrade_collaboration__execute"},{"tool_name":"other.execute"},
                      {"tool_input":{"operation_id":"foreign"}},
                      {"tool_input":{"input":{"operation_id":"ready"}}},
                      {"tool_input":{"code":{"operation_id":"ready"}}}):
            self.values["PreToolUse"]=copy.deepcopy(original)
            self.values["PreToolUse"]["payload"].update(patch)
            with self.subTest(patch=patch),self.assertRaisesRegex(ContractError,"PRE_POST_HOOKS_NOT_CORRELATED"):
                self.evaluate()

    def test_wrong_server_birth_session_and_manual_parent_are_refused(self):
        original=copy.deepcopy(self.values["PreToolUse"])
        for mutate in (lambda v:v["parents"][0].update(birth=[100,2]),
                       lambda v:v["payload"].update(session_id=fixtures.TURN),
                       lambda v:v.update(direct_client=None),
                       lambda v:v["parents"].insert(0,dict(pid=1001,ppid=999,exact_hook_shell=False))):
            self.values["PreToolUse"]=copy.deepcopy(original);mutate(self.values["PreToolUse"])
            with self.assertRaisesRegex(ContractError,"PRE_POST_HOOKS_NOT_CORRELATED"):self.evaluate()

    def test_hook_file_change_and_old_topology_refuse(self):
        self.spec["schema"]="upgrade-startup-v3"
        with self.assertRaisesRegex(ContractError,"EXPLICIT_TOPOLOGY"):self.evaluate()
        self.spec["schema"]=TASK_SCHEMA
        (self.root/"hooks.json").write_text('{}')
        with self.assertRaises(ContractError):self.evaluate()

    def test_hook_event_order_must_be_before_then_after(self):
        self.values["PostToolUse"]["observed_at_ns"]=999
        with self.assertRaisesRegex(ContractError,"EVENT_ORDER_CHANGED"):self.evaluate()


if __name__=="__main__":unittest.main()
