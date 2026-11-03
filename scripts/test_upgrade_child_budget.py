"""Whole-task child accounting; no model clients or live adoption claims."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from datetime import datetime, timezone, timedelta
import json
import tempfile
import unittest
import uuid
import copy

from orchestration_common import ContractError, file_ref, write_json
from cmux_prompt_reference import plan, persist
from upgrade_body_read import read_command, read_result
from test_orchestration_native import NativeFixture
from upgrade_child_budget import (initialize_ledger, reserve_child, budget_status,
                                  record_child_report, consume_child_report)


def competing_reservation(arguments):
    authority, index = arguments
    try:
        return reserve_child(authority, parent="claude_a" if index % 2 else "claude_b",
            tool_use_id="toolu_race_" + str(index), tool_input=dict(prompt="Original bounded race task."))
    except ContractError as exc:
        return dict(status="REFUSED", error=str(exc))


class ChildBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.parents = {lane: str(uuid.uuid4()) for lane in ("claude_a", "claude_b")}
        self.authority = dict(schema="upgrade-child-budget-authority-v1", task_id="same-native-task",
            ledger_root=str(self.root / "state/native-child-budget/task"), max_inflight=2,
            budget_scope="WHOLE_TASK", parents=self.parents)
        self.authority_ref = write_json(self.root / "authority.json", self.authority, exclusive=True)
        Path(self.authority_ref["path"]).chmod(0o444)
        initialize_ledger(self.authority_ref)

    def test_third_unconsumed_child_is_refused_across_both_original_parents(self):
        first = reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_first",
                              tool_input=dict(prompt="Read original task section A."))
        second = reserve_child(self.authority_ref, parent="claude_b", tool_use_id="toolu_second",
                               tool_input=dict(prompt="Read original task section B."))
        self.assertNotEqual(first["claim_id"], second["claim_id"])
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 2)
        with self.assertRaisesRegex(ContractError, "WHOLE_TASK_CHILD_LIMIT"):
            reserve_child(self.authority_ref, parent="claude_b", tool_use_id="toolu_third",
                          tool_input=dict(prompt="A third task must not start."))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 2)

    def test_eight_actual_processes_share_one_two_slot_task_budget(self):
        # Ordinary Python workers, not native/model agents. Both parents race
        # the same on-disk transaction lock and fixed original authority.
        with ProcessPoolExecutor(max_workers=8, mp_context=multiprocessing.get_context("spawn")) as pool:
            results = list(pool.map(competing_reservation, [(self.authority_ref, i) for i in range(8)]))
        self.assertEqual(sum(value["status"] == "RESERVED" for value in results), 2)
        denied = [value for value in results if value["status"] == "REFUSED"]
        self.assertEqual(len(denied), 6)
        self.assertTrue(all(value["error"].startswith("WHOLE_TASK_CHILD_LIMIT:") for value in denied))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 2)

    def test_original_tool_id_is_idempotent_but_changed_input_cannot_reuse_a_claim(self):
        request = dict(prompt="Read the original immutable task.")
        first = reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_original", tool_input=request)
        again = reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_original", tool_input=request)
        self.assertEqual(first["claim_id"], again["claim_id"])
        self.assertEqual(again["status"], "ORIGINAL_RESERVATION")
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_CLAIM_DIFFERENT"):
            reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_original",
                          tool_input=dict(prompt="Changed task must not reuse the original tool ID."))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)

    def test_returned_child_reports_still_occupy_both_slots_before_parent_consumption(self):
        for lane in self.parents:
            reserved = reserve_child(self.authority_ref, parent=lane, tool_use_id="toolu_" + lane,
                                     tool_input=dict(prompt="Original readonly child work."))
            body = "STATUS: child-" + lane + "\nOriginal full child report.\n"
            prepared = plan(body, "child-" + lane, self.root / "reports")
            persist(prepared["reference"], body)
            record_child_report(self.authority_ref, reserved["claim_id"], parent=lane,
                                notice=prepared["text"])
        state = budget_status(self.authority_ref)
        self.assertEqual(state["inflight"], 2)
        self.assertTrue(all(claim["state"] == "RESULT_READY" for claim in state["claims"].values()))
        with self.assertRaisesRegex(ContractError, "WHOLE_TASK_CHILD_LIMIT"):
            reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_after_completion",
                          tool_input=dict(prompt="Completion alone must not release a slot."))

    def test_only_an_original_parent_full_native_read_and_exact_sha_releases_one_slot(self):
        lane = "claude_a"
        reserved = reserve_child(self.authority_ref, parent=lane, tool_use_id="toolu_report",
                                 tool_input=dict(prompt="Complete original bounded report."))
        body = "STATUS: child-original-report\nThe complete original child report.\n"
        prepared = plan(body, "child-original-report", self.root / "reports")
        persist(prepared["reference"], body)
        record_child_report(self.authority_ref, reserved["claim_id"], parent=lane, notice=prepared["text"])
        journal = NativeFixture(self.root, session_id=self.parents[lane])
        command = read_command("/original/python", self.root, prepared["text"], "/original/rtk")
        def consume(sha):
            return consume_child_report(self.authority_ref, reserved["claim_id"], parent=lane,
                parent_session=journal.session, command=command, reply_sha256=sha, fixtures=True)
        with self.assertRaisesRegex(ContractError, "ORIGINAL_PARENT_FULL_READ_REQUIRED"):
            consume(prepared["reference"]["sha256"])
        now = datetime.now(timezone.utc)
        call = journal.message("assistant", "unused", at=now.isoformat())
        call["message"]["content"] = [dict(type="tool_use", id="toolu_body_read", name="Bash",
                                               input=dict(command=command))]
        journal.append(call)
        result = journal.message("user", "unused", at=(now + timedelta(milliseconds=1)).isoformat())
        result["message"]["content"] = [dict(type="tool_result", tool_use_id="toolu_body_read", is_error=False,
            content=json.dumps(read_result(prepared["text"]), ensure_ascii=False))]
        journal.append(result)
        with self.assertRaisesRegex(ContractError, "CHILD_REPORT_SHA_MISMATCH"):
            consume("0" * 64)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)
        accepted = consume(prepared["reference"]["sha256"])
        self.assertEqual(accepted["status"], "CONSUMED")
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)
        self.assertEqual(consume(prepared["reference"]["sha256"])["status"], "ORIGINAL_CONSUMPTION")
        next_claim = reserve_child(self.authority_ref, parent="claude_b", tool_use_id="toolu_next",
                                   tool_input=dict(prompt="Original task can continue after actual consumption."))
        self.assertNotEqual(next_claim["claim_id"], reserved["claim_id"])
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)

    def test_missing_ledger_is_not_an_empty_budget_and_initialization_cannot_reset_it(self):
        reserved = reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_retained",
                                 tool_input=dict(prompt="Original inflight child must remain accounted."))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_LEDGER_EXISTS"):
            initialize_ledger(self.authority_ref)
        path = Path(self.authority["ledger_root"]) / "ledger.json"
        saved = path.with_name("original-ledger-preserved.json")
        path.rename(saved)
        with self.assertRaisesRegex(ContractError, "cannot read"):
            reserve_child(self.authority_ref, parent="claude_b", tool_use_id="toolu_missing_ledger",
                          tool_input=dict(prompt="Missing original ledger is not free capacity."))
        saved.rename(path)
        self.assertIn(reserved["claim_id"], budget_status(self.authority_ref)["claims"])

    def test_replaced_transaction_lock_cannot_authorize_writes_against_original_ledger(self):
        lock = Path(self.authority["ledger_root"]) / "transaction.lock"
        lock.rename(lock.with_name("original-transaction-lock-preserved"))
        lock.touch(mode=0o600)
        with self.assertRaisesRegex(ContractError, "CHILD_LEDGER_AUTHORITY_CHANGED"):
            reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_changed_lock",
                          tool_input=dict(prompt="Changed lock cannot split the task budget."))

    def test_failed_truncated_sidechain_and_duplicate_native_reads_cannot_release_capacity(self):
        reserved = reserve_child(self.authority_ref, parent="claude_a", tool_use_id="toolu_exact",
                                 tool_input=dict(prompt="Original complete child result required."))
        body = "STATUS: exact-report-read\nPinned complete child body.\n"
        prepared = plan(body, "exact-report-read", self.root / "reports")
        persist(prepared["reference"], body)
        record_child_report(self.authority_ref, reserved["claim_id"], parent="claude_a", notice=prepared["text"])
        journal = NativeFixture(self.root, session_id=self.parents["claude_a"])
        command = read_command("/original/python", self.root, prepared["text"], "/original/rtk")
        now = datetime.now(timezone.utc)
        call = journal.message("assistant", "unused", at=now.isoformat())
        call["message"]["content"] = [dict(type="tool_use", id="toolu_full_read", name="Bash", input=dict(command=command))]
        result = journal.message("user", "unused", at=(now + timedelta(milliseconds=1)).isoformat())
        result["message"]["content"] = [dict(type="tool_result", tool_use_id="toolu_full_read", is_error=False,
            content=json.dumps(read_result(prepared["text"]), ensure_ascii=False))]
        original = journal.path.read_bytes()
        for label in ("failed", "truncated", "sidechain", "duplicate", "wrong_command"):
            with self.subTest(shape=label):
                journal.path.write_bytes(original)
                changed_call, changed_result = copy.deepcopy(call), copy.deepcopy(result)
                if label == "failed":
                    changed_result["message"]["content"][0]["is_error"] = True
                elif label == "truncated":
                    changed_result["message"]["content"][0]["content"] = changed_result["message"]["content"][0]["content"][:-5]
                elif label == "sidechain":
                    changed_call["isSidechain"] = True
                elif label == "wrong_command":
                    changed_call["message"]["content"][0]["input"]["command"] += " --changed"
                journal.append(changed_call)
                journal.append(changed_result)
                if label == "duplicate":
                    journal.append(changed_result)
                with self.assertRaisesRegex(ContractError, "ORIGINAL_PARENT_FULL_READ_REQUIRED"):
                    consume_child_report(self.authority_ref, reserved["claim_id"], parent="claude_a",
                        parent_session=journal.session, command=command,
                        reply_sha256=prepared["reference"]["sha256"], fixtures=True)
                self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)


if __name__ == "__main__":
    unittest.main()
