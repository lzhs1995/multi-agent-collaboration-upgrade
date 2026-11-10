"""Recover the original publication/SQLite gap without a second operation."""
import json
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref
import test_upgrade_task_campaign as campaign_fixtures


class BindingRecoveryTests(unittest.TestCase):
    def setUp(self):
        f=campaign_fixtures.CampaignActorTests();f.binding_mode=True;f.setUp()
        self.addCleanup(f.doCleanups);self.f=f
        f.call("start");f.complete("start");f.call("claim");f.complete("claim")
        f.c.work(f.runner.co("run-01").state()[1]["local"])
        f.call("candidate")
        self.message,self.ref,_=f.complete("candidate",defer_outer=True)
        f.native_result("candidate",self.message)
        self.path=f.control/"campaign-bindings/accept.json"

    def fail_binding(self,*,after_commit=False):
        f=self.f;original=f.ledger.bind_artifact
        def interrupted(*args):
            if after_commit:original(*args)
            raise OSError("fixture interrupted after artifact publication")
        with patch.object(f.ledger,"bind_artifact",side_effect=interrupted):
            with self.assertRaisesRegex(OSError,"interrupted"):
                f.actor.bind_completed(self.message,self.ref)
        self.assertTrue(f.actor.closed)
        self.assertTrue(self.path.is_file())
        self.assertEqual(f.ledger.inspect("inner-candidate")["phase"],"NATIVE_COMPLETED")

    def recover(self):
        return self.f.actor.reconcile_binding(self.message,self.ref)

    def test_published_artifact_attaches_once_without_reexecuting_or_reopening(self):
        f=self.f;self.fail_binding();original=file_ref(self.path)
        count=f.ledger.db.execute("SELECT count(*) FROM events").fetchone()[0]
        result=self.recover()
        self.assertEqual(result["status"],"RECONCILED_ORIGINAL_BINDING")
        self.assertEqual((result["operations_executed"],result["responses_sent"]),(0,0))
        self.assertEqual(f.ledger.resolve_operation("accept")[1],original)
        self.assertEqual(file_ref(self.path),original)
        self.recover()
        self.assertEqual(f.ledger.db.execute("SELECT count(*) FROM events").fetchone()[0],count+1)
        self.assertEqual(f.ledger.db.execute("SELECT count(*) FROM calls").fetchone()[0],3)
        self.assertEqual(len(f.sent),3)
        with self.assertRaisesRegex(ContractError,"CLOSED_NO_REPLAY"):f.actor.step()

    def test_lost_commit_return_observes_same_existing_binding(self):
        self.fail_binding(after_commit=True)
        before=self.f.ledger.db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertTrue(self.recover()["bindings"][0]["already_attached"])
        self.assertEqual(self.f.ledger.db.execute("SELECT count(*) FROM events").fetchone()[0],before)
        self.assertEqual(len(self.f.sent),3)

    def test_changed_original_binding_is_not_regenerated(self):
        self.fail_binding();value=json.loads(self.path.read_text())
        value["replacement"]="foreign-marker"
        self.path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContractError,"RECOVERY_BINDING_CHANGED"):self.recover()
        self.assertEqual(json.loads(self.path.read_text())["replacement"],"foreign-marker")
        self.assertEqual(self.f.ledger.db.execute("SELECT count(*) FROM bindings").fetchone()[0],0)

    def test_missing_artifact_cannot_be_recreated(self):
        self.fail_binding();self.path.unlink()
        with self.assertRaises((OSError,ContractError)):self.recover()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.f.ledger.db.execute("SELECT count(*) FROM bindings").fetchone()[0],0)

    def test_expired_live_gate_keeps_original_partial_state(self):
        self.fail_binding();original=file_ref(self.path)
        def expired(*args):raise ContractError("fixture enrollment expired")
        self.f.actor.gate=expired
        with self.assertRaisesRegex(ContractError,"expired"):self.recover()
        self.assertEqual(file_ref(self.path),original)
        self.assertEqual(self.f.ledger.db.execute("SELECT count(*) FROM bindings").fetchone()[0],0)
        self.assertEqual(len(self.f.sent),3)


if __name__=="__main__":unittest.main()
