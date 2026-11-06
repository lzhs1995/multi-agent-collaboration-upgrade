"""Original .84 task journal reconciliation with explicit offline native reads.

Task RPC/SQLite/stage files and original delivery locks/publication run here.
Kernel/model and native scan result are fixtures, not live delivery evidence.
"""
from contextvars import copy_context
import copy
import fcntl
import hashlib
import json
from pathlib import Path
from types import ModuleType,SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
from orchestration_native import verify_delivery
import upgrade_task_reconcile as reconcile
import upgrade_task_bridge_context as adapter
import upgrade_task_sender as sender
from upgrade_task_bridge_patch import task_bridge_paths
from test_upgrade_task_target import SOURCE
import test_upgrade_task_dispatch as fixtures


@unittest.skipUnless(SOURCE.is_dir(),"original .84 source required")
class ReconcileTests(unittest.TestCase):
    def setUp(self):
        helper=fixtures.DispatchTests();helper.transport_actions=("prepare","dispatch","reconcile")
        helper.setUp();self.addCleanup(helper.doCleanups);self.helper=helper;f=helper.f;self.f=f
        helper.fake.hide_receipt=True;f.call("dispatch");f.complete("dispatch")
        self.transport=helper.transport;self.runtime=helper.runtime;self.saved=[];self.reads=[]
        self.assertEqual(self.transport.current()[0].value,"UNCERTAIN")
        source=helper.fake.dispatch_root;old=json_ref(file_ref(source/"attempt-0001.json"))
        # Complete synthetic producer source; production creates these before
        # input. This fixture upgrade does not claim a real retroactive binding.
        old["native_binding"]=helper.binding;old["phase"]="UNKNOWN"
        digest=hashlib.sha256(json.dumps(helper.binding,sort_keys=True).encode()).hexdigest()
        for event in old["events"]:
            if "native_paste_fence" in event:event["native_paste_fence"]["binding_sha256"]=digest
        self.notice=Path(json_ref(helper.binding["task_sender"]["task_actor"])["arguments"]["notice"]["path"]).read_text()
        self.pack=Path(self.transport.current()[1]["prepared"]["pack"]["path"])
        self.journal_root=self.transport.root/"original84/task-dispatch-v1"
        key=hashlib.sha256(json.dumps([old["binding"]["identity"]["caller_surface_uuid"],old["binding"]["task_id"]]).encode()).hexdigest()
        self.journal=self.journal_root/key;self.journal.mkdir(parents=True)
        self.attempt=self.journal/"attempt-0001.json";write_json(self.attempt,old)
        self.before=file_ref(self.attempt)
        self.fixture_result=dict(helper.fake.saved_receipt,native_binding=helper.binding,attempt=str(self.attempt))
        self.scope_calls=0
        def context(runtime,surface,notice,pack,stage):
            self.scope_calls+=1
            if getattr(self,"stop",False) and self.scope_calls>1:raise ContractError("fixture STOP_REQUESTED")
            request=f.ledger.inspect("inner-reconcile")["request"]
            record=json_ref(file_ref(f.directory/"reconcile.json"))["received"]
            return reconcile._scope(runtime,surface,notice,pack,stage,helper.owner,request,record)
        def scan(bridge,surface,text,path,binding,*,read_only=False):
            self.assertTrue(read_only);self.assertEqual(file_ref(path),self.before)
            self.saved.append(copy_context());proof=reconcile.current_scope()
            self.assertEqual(adapter.snapshot()["task_actor"],proof)
            with self.assertRaisesRegex(ContractError,"ACTIVE_OPERATION_REQUIRED"):sender.require_live(binding)
            sender.check(binding,read_only=True)
            self.reads.append("scan")
            if getattr(self,"pending",False):return dict(confirmed=False,state="NATIVE_PENDING")
            return copy.deepcopy(self.fixture_result)
        def receipt_evidence(*a,read_only=False):
            self.assertTrue(read_only);self.reads.append("receipt");return True
        native=SimpleNamespace(scan_original=scan,receipt_evidence=receipt_evidence)
        availability=ModuleType("availability_contract");availability.require_action=lambda *a:None
        confirmation=ModuleType("cmux_confirmation")
        exec(compile((SOURCE/"scripts/cmux_confirmation.py").read_text(),"original84-confirmation","exec"),confirmation.__dict__)
        raw=task_bridge_paths("cmux_task_journal.py",(SOURCE/"scripts/cmux_task_journal.py").read_text())
        raw=raw.replace("Path.home() / '.local/state/multi-agent-collaboration/task-dispatch-v1'","Path("+repr(str(self.journal_root))+")")
        module=ModuleType("original84_task_journal");module.__file__=str(SOURCE/"scripts/cmux_task_journal.py")
        self.original_journal=module
        with patch.dict(sys.modules,cmux_native_delivery=native):exec(compile(raw,module.__file__,"exec"),module.__dict__)
        class Failure(RuntimeError):
            def __init__(self,message,details=None):super().__init__(message);self.details=details
        bridge=SimpleNamespace(validate_task_pack_contract=lambda *a,**kw:json_ref(file_ref(self.pack)),
            pin_workspace=lambda *a:dict(old["binding"]["identity"]),
            _sha256_file=lambda p:file_ref(p)["sha256"],TaskPackContractError=Failure,DispatchUnconfirmed=Failure,
            submit_text=lambda *a,**kw:(_ for _ in ()).throw(AssertionError("reconciliation tried input")))
        self.journal_bridge=bridge;self.native=native;self.rendered=raw
        helper.bridge.submit_task_pack=lambda surface,text,pack,**kw:module.deliver(bridge,surface,text,pack,**kw)
        original_submit=helper.fake.submit
        def submit(surface,notice,pack,*,reconcile_only):
            if not reconcile_only:return original_submit(surface,notice,pack,reconcile_only=False)
            stage=self.transport.root/"operations/reconcile-task-0001"
            return self.runtime.reconcile_task(surface,notice,pack,stage)
        helper.fake.submit=submit
        for p in (patch.object(reconcile,"_context",side_effect=context),
            patch("orchestration_transport.dispatch_journal",return_value=self.journal),
            patch.object(sender,"_task_caller",return_value=True),
            patch.dict(sys.modules,availability_contract=availability,cmux_confirmation=confirmation)):
            p.start();self.addCleanup(p.stop)

    def test_original_unknown_receipt_uses_only_reads_and_preserves_attempt(self):
        self.f.call("reconcile");self.f.complete("reconcile")
        self.assertEqual(self.transport.current()[0].value,"DISPATCHED")
        self.assertEqual(file_ref(self.attempt),self.before)
        receipt=json_ref(file_ref(self.journal/"receipt.json"));self.assertEqual(receipt["input_operations"],0)
        reconcile.verify_receipt(receipt,dict(attempts=[self.before]),self.f.c.campaign["sessions"]["codex"],
                                 self.f.c.campaign["sessions"]["claude_a"],fixtures=True)
        self.assertEqual(self.reads,["scan","receipt"])
        with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
            self.saved[0].run(reconcile.current_scope)
        with self.assertRaises(ContractError):self.f.call("reconcile")

    def test_pending_original_stays_unknown_without_receipt_or_resend(self):
        self.pending=True;self.f.call("reconcile");self.f.complete("reconcile")
        self.assertEqual(self.transport.current()[0].value,"UNCERTAIN")
        self.assertEqual(file_ref(self.attempt),self.before)
        self.assertFalse((self.journal/"receipt.json").exists())
        self.assertEqual(self.reads,["scan"])

    def test_stop_after_observation_intent_never_reads_or_sends(self):
        self.stop=True;self.f.call("reconcile");self.f.complete("reconcile")
        self.assertFalse(self.reads);self.assertEqual(file_ref(self.attempt),self.before)
        self.assertFalse((self.journal/"receipt.json").exists())

    def test_saved_source_or_attempt_omission_cannot_explain_receipt(self):
        self.f.call("reconcile");self.f.complete("reconcile")
        receipt=json_ref(file_ref(self.journal/"receipt.json"));receipt.pop("task_reconciler")
        with self.assertRaisesRegex(ContractError,"RECEIPT_SOURCE_REQUIRED"):
            reconcile.verify_receipt(receipt,dict(attempts=[self.before]),{}, {},fixtures=True)

    def test_confirmed_phase_without_original_result_cannot_downgrade_reconciliation(self):
        old=json_ref(self.before);old["phase"]="CONFIRMED"
        self.before=write_json(self.attempt,old)
        self.f.call("reconcile");self.f.complete("reconcile")
        path=self.journal/"receipt.json";receipt=json_ref(file_ref(path))
        receipt.pop("task_reconciler");receipt.pop("reconciled_read_only")
        bundle=dict(receipt=write_json(path,receipt),attempts=[self.before])
        with self.assertRaisesRegex(ContractError,"RECEIPT_SOURCE_REQUIRED"):
            verify_delivery(bundle,self.f.c.campaign["sessions"]["claude_a"],
                self.f.c.campaign["sessions"]["codex"],self.notice,fixtures=True)

    def test_scope_and_chronology_cannot_be_rewritten_together(self):
        self.f.call("reconcile");self.f.complete("reconcile")
        receipt=json_ref(file_ref(self.journal/"receipt.json"));proof=receipt["task_reconciler"]
        original=json_ref(proof);intent_ref=original["stage_intent"];intent=json_ref(intent_ref)
        for change,error in (("pack","DISPATCH_SCOPE_CHANGED"),("time","CHRONOLOGY_CHANGED")):
            with self.subTest(change=change):
                value=copy.deepcopy(original);entry=copy.deepcopy(intent)
                if change=="pack":
                    value["arguments"]["pack"]=value["arguments"]["notice"]
                    entry["arguments"]=value["arguments"]
                else:entry["created_at"]="2000-01-01T00:00:00+00:00"
                value["stage_intent"]=write_json(intent_ref["path"],entry)
                candidate=dict(receipt,task_reconciler=write_json(proof["path"],value))
                with self.assertRaisesRegex(ContractError,error):
                    reconcile.verify_receipt(candidate,dict(attempts=[self.before]),
                        self.f.c.campaign["sessions"]["codex"],self.f.c.campaign["sessions"]["claude_a"],fixtures=True)
        write_json(intent_ref["path"],intent);write_json(proof["path"],original)

    def test_original_confirmed_source_needs_no_reconcile_capability(self):
        old=json_ref(self.before);old["phase"]="CONFIRMED";old["result"]=self.fixture_result
        ref=write_json(self.attempt,old)
        receipt=dict(self.fixture_result,binding=old["binding"])
        bundle=dict(receipt=write_json(self.journal/"receipt.json",receipt),attempts=[ref])
        verified=verify_delivery(bundle,self.f.c.campaign["sessions"]["claude_a"],
            self.f.c.campaign["sessions"]["codex"],self.notice,fixtures=True)
        self.assertEqual(verified["transport_retries"],0);self.assertFalse(self.reads)

    def test_original_journal_lock_conflict_prevents_observation(self):
        with (self.journal/"delivery.lock").open("w") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.f.call("reconcile");self.f.complete("reconcile")
        self.assertFalse(self.reads);self.assertEqual(file_ref(self.attempt),self.before)
        self.assertEqual(self.transport.current()[0].value,"UNCERTAIN")

    def test_generated_journal_retains_first_confirmation_and_readonly_revalidation(self):
        fresh=self.journal_root.parent/"fresh84"
        raw=self.rendered.replace(repr(str(self.journal_root)),repr(str(fresh)))
        module=ModuleType("fresh84_task_journal");module.__file__=str(SOURCE/"scripts/cmux_task_journal.py")
        native=SimpleNamespace(bind_target=lambda *a:self.helper.binding,
            marker_binding=lambda binding,*a:binding,receipt_evidence=lambda *a,**kw:True,
            _original_intent=lambda *a:None)
        inline=ModuleType("cmux_prompt_reference");inline.require_inline=lambda *a:None
        bridge=copy.copy(self.journal_bridge)
        bridge.submit_text=lambda *a,**kw:copy.deepcopy(self.fixture_result)
        with patch.dict(sys.modules,cmux_native_delivery=native,cmux_prompt_reference=inline):
            exec(compile(raw,module.__file__,"exec"),module.__dict__)
            result=module.deliver(bridge,self.f.c.campaign["sessions"]["claude_a"]["surface_uuid"],
                self.notice,self.pack,wait_seconds=0)
        old=json_ref(file_ref(result["attempt"]))
        self.assertEqual(old["phase"],"CONFIRMED");self.assertEqual(old["result"],self.fixture_result)
        reads=[]
        def readonly(*a,read_only=False):reads.append(read_only);return read_only
        native.receipt_evidence=readonly
        self.assertIsNotNone(module.verified_receipt(bridge,
            self.f.c.campaign["sessions"]["claude_a"]["surface_uuid"],self.notice,None,self.pack))
        self.assertEqual(reads,[True])


class BoundaryTests(unittest.TestCase):
    def test_observer_requires_live_owner(self):
        with self.assertRaisesRegex(ContractError,"LIVE_ACTOR_REQUIRED"):reconcile._context()


if __name__=="__main__":unittest.main()
