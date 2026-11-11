"""Attachment failure never launches a replacement or signals an existing task."""
from pathlib import Path
from contextlib import closing
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
import test_upgrade_task_startup as fixture
from upgrade_task_tui import task_tui_argv, origin_log_fence, observe_origin_ready, ORIGIN_READY_MARKER


def log_record(path, *, pid=123, ts=12, body=None):
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                   'ts INTEGER, ts_nanos INTEGER, level TEXT, target TEXT, '
                   'feedback_log_body TEXT, process_uuid TEXT)')
        db.execute('INSERT INTO logs(ts,ts_nanos,level,target,feedback_log_body,process_uuid) '
                   'VALUES(?,0,?,?,?,?)', (ts, 'INFO', 'codex_tui::app::startup',
                    body or ORIGIN_READY_MARKER+'duration_ms=144',
                    f'pid:{pid}:11111111-2222-4333-8444-555555555555'))
        db.commit()


class OriginReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.log = self.root/'log/logs_2.sqlite'
        self.log.parent.mkdir()
        self.process = dict(pid=123, birth=[11, 22])

    def observe(self, fence):
        return observe_origin_ready(self.root, fence, self.process)

    def test_process_launch_and_old_frame_do_not_establish_new_readiness(self):
        log_record(self.log)
        fence = origin_log_fence(self.root)
        self.assertIsNone(self.observe(fence))
        log_record(self.log, body='still loading')
        self.assertIsNone(self.observe(fence))
        log_record(self.log)
        observed = self.observe(fence)
        self.assertEqual(observed['row']['id'], 3)
        self.assertFalse(observed['foreground_authentication'])
        self.assertEqual(observed['input_operations'], 0)

    def test_first_log_after_launch_can_supply_ordering_evidence(self):
        fence = origin_log_fence(self.root)
        self.assertIsNone(self.observe(fence))
        log_record(self.log)
        self.assertEqual(self.observe(fence)['row']['id'], 1)

    def test_wrong_process_or_record_before_birth_cannot_become_ready(self):
        fence = origin_log_fence(self.root)
        log_record(self.log, pid=456)
        log_record(self.log, ts=10)
        self.assertIsNone(self.observe(fence))

    def test_replaced_log_refuses(self):
        log_record(self.log)
        fence = origin_log_fence(self.root)
        self.log.rename(self.log.with_suffix('.old'))
        log_record(self.log)
        with self.assertRaisesRegex(ContractError, 'LOG_REPLACED'):
            self.observe(fence)

    def test_multiple_startup_records_are_ambiguous(self):
        fence = origin_log_fence(self.root)
        log_record(self.log); log_record(self.log)
        with self.assertRaisesRegex(ContractError, 'AMBIGUOUS'):
            self.observe(fence)

    def test_symlink_log_cannot_supply_ready_evidence(self):
        fence = origin_log_fence(self.root)
        target = self.root/'foreign.sqlite'; log_record(target)
        self.log.symlink_to(target)
        with self.assertRaises(ContractError):
            self.observe(fence)


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
