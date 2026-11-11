"""The actual pre-adoption actor entry must serve only the initial ready slot.

The fixture uses a local socket and startup journal. Deployment checks are
explicit fixtures here, not evidence of a live adoption or production launch.
"""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, write_json
from upgrade_live_control import readiness_task_actor
from upgrade_startup_contract import TASK_SCHEMA
import test_upgrade_task_startup as fixtures


class ReadyActorTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.TaskStartupTests();f.setUp()
        self.addCleanup(f.doCleanups);self.f=f
        f.multi_plan()
        f.plan["operations"]["future-ready"]={"kind":"readiness","payload":{}}
        f.plan_ref=write_json(f.root/"multi-plan.json",f.plan)
        f.directory=f.root/"control/task-startup"
        f.directory.parent.mkdir()
        self.startup=json_ref(f.start())
        self.queue=[];self.sent=[]
        f.client.receive_recorded=lambda timeout:self.queue.pop(0)
        f.client.respond=self.sent.append
        self.spec=dict(schema=TASK_SCHEMA,task_startup=f.plan_ref,
            adoption_path=str(f.root/"control/adoption.json"),stop_path=str(f.root/"STOP"),
            controller=str(f.root/"controller"),pins={"codex":{"path":"/fixture/codex"}})
        write_json(f.root/"controller/upgrade-binding.json",dict(state_root=str(f.root/"state")))
        write_json(f.root/"state/startup/codex.json",dict(pid=999,birth=[3,4]))
        self.runtime={"startup":write_json(f.root/"spec.json",self.spec)}
        self.process=dict(birth=[3,4],executable="/fixture/codex",argv=["/fixture/codex","resume",fixtures.SID])
        self.observation=dict(thread_id=fixtures.SID,latest_turn_id=fixtures.TURN)
        for target,kwargs in (
            ("upgrade_live_control.validate_live_runtime",dict(return_value=({},self.runtime))),
            ("upgrade_live_control.external_publisher",dict(return_value=None)),
            ("cmux_daemon_identity.process",dict(side_effect=lambda pid:copy.deepcopy(self.process))),
            ("upgrade_task_thread.observe",dict(side_effect=lambda *args:copy.deepcopy(self.observation)))):
            p=patch(target,**kwargs);p.start();self.addCleanup(p.stop)

    def actor(self):
        actor=readiness_task_actor({"fixture":True},self.f.client)
        self.addCleanup(actor.ledger.close)
        return actor

    def enqueue(self,op):
        message=dict(id=10,method="item/tool/call",params=dict(threadId=fixtures.SID,turnId=fixtures.TURN,
            callId="call-ready",namespace="upgrade_collaboration",tool="execute",arguments={"operation_id":op}))
        ref=write_json(self.f.root/f"request-{len(self.queue)}.json",dict(direction="received",message=message,
            peer=dict(server=self.f.client.server,peer={k:self.f.client.server[k] for k in ("pid","uid")})))
        self.queue.append((message,ref))

    def test_ready_entry_rejects_future_business_but_returns_original_ready_nonce(self):
        actor=self.actor()
        for op in ("left","future-ready"):
            self.enqueue(op)
            with self.assertRaisesRegex(ContractError,"OUTSIDE_STAGE"):actor.step()
        self.assertEqual(actor.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],0)
        self.enqueue("ready")
        self.assertEqual(actor.step()["phase"],"RESPONSE_INTENDED")
        self.assertEqual(len(self.sent),1)
        self.assertEqual(len(json_ref(actor.ledger.inspect("call-ready")["receipt"])["receipt_nonce"]),48)

    def test_stop_or_tui_change_cannot_run_even_ready(self):
        actor=self.actor()
        self.process["birth"]=[9,9]
        self.enqueue("ready")
        with self.assertRaisesRegex(ContractError,"TUI_OR_SERVER_CHANGED"):actor.step()
        self.process["birth"]=[3,4]
        Path(self.spec["stop_path"]).write_text("stop")
        self.enqueue("ready")
        with self.assertRaisesRegex(ContractError,"STOPPED_OR_CHANGED"):actor.step()
        self.assertEqual(self.sent,[])
        self.assertFalse((self.f.root/"control/task-readiness-receipt.json").exists())

    def test_initial_ready_does_not_accept_an_unenrolled_later_turn(self):
        actor=self.actor()
        self.observation["latest_turn_id"]=fixtures.SID
        self.enqueue("ready")
        with self.assertRaisesRegex(ContractError,"ACTIVE_TURN_CHANGED"):actor.step()
        self.assertEqual(actor.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],0)


if __name__=="__main__":unittest.main()
