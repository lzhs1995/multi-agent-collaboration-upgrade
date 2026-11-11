"""Typed call history uses actual fixture RPC/ledger; no live model claim."""
import copy
import unittest

from orchestration_common import ContractError,file_ref,json_ref
from orchestration_inputs import invocation_calls
from orchestration_input_history import sender_history
from upgrade_task_control import invocation_script
import test_upgrade_task_dispatch as fixtures


class TypedHistoryTests(unittest.TestCase):
    def setUp(self):
        helper=fixtures.DispatchTests();helper.setUp();self.addCleanup(helper.doCleanups)
        self.helper=helper;self.f=helper.f
        self.scope=dict(campaign=self.f.c.campaign,run_id="run-01",node_id="review")

    def history(self,scope=True):
        f=self.f
        return sender_history(f.native.reader,f.native.session["freeze_fence"]["offset"],
            f.native.reader.snapshot()["offset"],f.c.campaign["sessions"]["claude_a"],
            f.c.campaign["sessions"]["codex"],("business-task",),task_scope=self.scope if scope else None)

    def test_original_prepare_and_control_calls_are_explicitly_classified(self):
        result=self.history();calls=result["sender_calls"]
        self.assertEqual([c["source"]["action"] for c in calls],["start","claim","prepare"])
        self.assertTrue(all(c["source"]["classification"]=="no_business_dispatch" for c in calls))

    def test_attempted_dispatch_refuses_absence_even_when_receiver_result_is_unknown(self):
        self.helper.fake.hide_receipt=True;self.f.call("dispatch");self.f.complete("dispatch")
        self.assertEqual(self.helper.transport.current()[0].value,"UNCERTAIN")
        with self.assertRaisesRegex(ContractError,"attempted typed dispatch"):self.history()

    def test_original_different_task_dispatch_is_visible_and_unrelated(self):
        self.f.call("dispatch");self.f.complete("dispatch")
        self.scope["node_id"]="parallel"
        result=self.history()
        self.assertEqual(result["sender_calls"][-1]["source"]["classification"],"different_task")

    def test_missing_scope_does_not_silently_ignore_typed_call(self):
        with self.assertRaisesRegex(ContractError,"ORIGINAL_CAMPAIGN_REQUIRED"):self.history(False)

    def test_duplicate_or_unregistered_native_call_cannot_prove_absence(self):
        original=json_ref(file_ref(self.f.directory/"prepare.json"))["native"]
        record=self.f.native.reader.record(original)
        self.f.native.append(copy.deepcopy(record))
        with self.assertRaisesRegex(ContractError,"DUPLICATE_ORIGINAL_CALL"):self.history()


class InvocationTests(unittest.TestCase):
    def test_actual_typed_invocation_and_dynamic_arguments_are_not_ignored(self):
        self.assertEqual(invocation_calls("exec",invocation_script("dispatch")),
                         [dict(kind="task_control",operation_id="dispatch")])
        self.assertEqual(invocation_calls("exec","await tools.upgrade_collaboration__execute(load('opaque'));"),
                         [dict(kind="unresolved",reason="nonliteral native tool arguments")])
        self.assertEqual(invocation_calls("exec",'text("tools.upgrade_collaboration__execute");'),[])


if __name__=="__main__":unittest.main()
