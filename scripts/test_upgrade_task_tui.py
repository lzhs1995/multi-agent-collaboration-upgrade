"""Attachment failure never launches a replacement or signals an existing task."""
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
import test_upgrade_task_startup as fixture
from upgrade_task_tui import task_tui_argv


class TaskTUITests(unittest.TestCase):
    def setUp(self):
        f=fixture.TaskStartupTests(); f.setUp()
        self.addCleanup(f.doCleanups);self.f=f
        f.directory=f.root/"control/task-startup";f.directory.parent.mkdir()
        self.result=f.start()
        self.binding={"state_root":str(f.root/"state")}
        (f.root/"state/startup").mkdir(parents=True)
        self.ref=write_json(f.root/"startup.json",{"fixture":True})
        self.spec=dict(work_root=str(f.root/"work"),adoption_path=str(f.root/"control/adoption.json"),
            stop_path=str(f.root/"STOP"),task_startup=f.plan_ref,
            clients={"codex":dict(argv=["/fixture/codex","--remote","unix://fixture"],native_root=str(f.root/"native"))})
        self.attach=write_json(f.root/"control/task-attach.json",
            dict(schema="upgrade-task-attach-v1",startup=self.ref,result=self.result))
        self.signals=[]
        outer=self
        class Child:
            pid=99555
            returncode=None
            def poll(self):return self.returncode
            def terminate(self):outer.signals.append(self.pid)
            def wait(self,timeout):self.returncode=-15;return -15
        self.child=Child()
        for target,kw in (("upgrade_task_tui.os.isatty",dict(return_value=True)),
                          ("upgrade_task_tui.os.ttyname",dict(return_value="/dev/fixture")),
                          ("cmux_daemon_identity.process",dict(return_value={"birth":[1,2]})),
                          ("upgrade_task_tui.subprocess.Popen",dict(return_value=self.child))):
            p=patch(target,**kw);mock=p.start();self.addCleanup(p.stop)
            if target.endswith("Popen"):self.spawn=mock

    def test_attach_exact_created_session_and_retire_only_owned_empty_tui(self):
        argv=task_tui_argv(self.ref,self.spec,self.binding,{"FIXTURE":"1"})
        self.assertEqual(argv[-2:],["resume",fixture.SID])
        self.assertEqual(self.signals,[self.child.pid])
        self.assertEqual(self.spawn.call_count,1)
        self.assertTrue(self.spawn.call_args.kwargs["close_fds"])
        intent=json_ref(file_ref(self.f.root/"state/startup/codex-attach-intent.json"))
        self.assertEqual(intent["terminal_input_operations"],0)

    def test_non_terminal_refuses_before_spawning(self):
        with patch("upgrade_task_tui.os.isatty",return_value=False):
            with self.assertRaisesRegex(ContractError,"REAL_TERMINAL"):
                task_tui_argv(self.ref,self.spec,self.binding,{})
        self.spawn.assert_not_called();self.assertEqual(self.signals,[])

    def test_changed_attach_refuses_resume_and_preserves_attempt(self):
        value=json_ref(self.attach);value["startup"]={"changed":True}
        write_json(self.attach["path"],value)
        with self.assertRaisesRegex(ContractError,"ATTACH_CONTRACT_CHANGED"):
            task_tui_argv(self.ref,self.spec,self.binding,{})
        self.assertEqual(self.signals,[self.child.pid])
        self.assertFalse((self.f.root/"state/startup/codex-attach-intent.json").exists())

    def test_stop_prevents_resume_and_never_replaces_child(self):
        Path(self.spec["stop_path"]).touch()
        with self.assertRaisesRegex(ContractError,"STOP_REQUESTED"):
            task_tui_argv(self.ref,self.spec,self.binding,{})
        self.spawn.assert_not_called()
        self.assertEqual(self.signals,[])
        self.assertFalse((self.f.root/"state/startup/codex-attach-intent.json").exists())


if __name__=="__main__":unittest.main()
