"""Public child allocator transaction tests with original-journal fixtures.

The peer/process boundary is tested independently with real Unix sockets.
These fixtures never create native model children or claim live adoption.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from orchestration_common import ContractError, canonical, file_ref, write_json
from test_orchestration_native import NativeFixture
from upgrade_child_budget import budget_status, initialize_ledger, record_child_report
from upgrade_child_budget_protocol import SCHEMA, child_prompt, hook_command, consume_command
from cmux_prompt_reference import plan, persist
from upgrade_body_read import read_command, read_result
from upgrade_child_service import handle_event


class NativeChildServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.journals, self.sessions = {}, {}
        self.python, self.controller = "/original/python", self.root / "controller"
        command = hook_command(self.python, self.controller)
        self.processes = {}
        for index, lane in enumerate(("claude_a", "claude_b")):
            directory = self.root / lane
            directory.mkdir()
            journal = NativeFixture(directory, session_id=str(uuid.uuid4()))
            journal.session["native_config_root"] = str(directory)
            journal.session["freeze_fence"] = journal.reader.snapshot()
            parent, peer = 100 + index * 10, 102 + index * 10
            env = dict(HOME=str(self.root), CLAUDE_CONFIG_DIR=str(directory),
                CMUX_WORKSPACE_ID=journal.session["workspace_uuid"], CMUX_SURFACE_ID=journal.session["surface_uuid"])
            self.processes[parent] = dict(pid=parent, ppid=1, birth=[1000, index], executable="/original/claude",
                file_identity=[1, 2, 3, 4], argv=["/original/claude"], env=env)
            self.processes[peer] = dict(pid=peer, ppid=parent, birth=[1001, index], executable=self.python,
                file_identity=[1, 20, 3, 4], argv=command.split(), env=env)
            journal.session["process"] = {k: self.processes[parent][k] for k in (
                "pid", "birth", "executable", "file_identity")}
            self.journals[lane], self.sessions[lane] = journal, journal.session
        self.authority_ref = write_json(self.root / "authority.json", dict(
            schema="upgrade-child-budget-authority-v1", task_id="same-original-task",
            ledger_root=str(self.root / "ledger"), max_inflight=2, budget_scope="WHOLE_TASK",
            parents={k: v["session_id"] for k, v in self.sessions.items()}), exclusive=True)
        Path(self.authority_ref["path"]).chmod(0o444)
        initialize_ledger(self.authority_ref)
        self.registration = dict(authority=self.authority_ref, sessions=self.sessions,
            account_home=str(self.root), python=self.python, controller=str(self.controller),
            report_root=str(self.root / "reports"), rtk="/original/rtk")

    def request(self, lane, payload):
        peer = dict(pid=102 if lane == "claude_a" else 112, uid=os.getuid())
        return handle_event(self.registration, dict(schema=SCHEMA, lane=lane, payload=payload),
            peer=peer, process_reader=lambda pid: copy.deepcopy(self.processes[pid]), fixtures=True)

    def original_call(self, lane, tool_id):
        journal = self.journals[lane]
        tool_input = dict(description="Original readonly review", prompt="Read assigned original section.",
                          subagent_type="general-purpose")
        call = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        call["message"]["content"] = [dict(type="tool_use", name="Agent", id=tool_id, input=tool_input)]
        journal.append(call)
        return dict(hook_event_name="PreToolUse", session_id=journal.session["session_id"],
                    transcript_path=str(journal.path), tool_name="Agent", tool_use_id=tool_id, tool_input=tool_input)

    def test_two_original_parents_share_capacity_and_caller_status_cannot_authorize_a_third(self):
        first_payload = self.original_call("claude_a", "toolu_first")
        first = self.request("claude_a", first_payload)
        second = self.request("claude_b", self.original_call("claude_b", "toolu_second"))
        self.assertEqual((first["status"], second["status"]), ("RESERVED", "RESERVED"))
        self.assertEqual(first["inflight"], 1)
        self.assertEqual(second["inflight"], 2)
        self.assertNotIn("permissionDecision", first["hook_output"]["hookSpecificOutput"])
        with self.assertRaisesRegex(ContractError, "WHOLE_TASK_CHILD_LIMIT"):
            self.request("claude_b", self.original_call("claude_b", "toolu_third"))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_RESERVATION_ALREADY_ISSUED"):
            self.request("claude_a", first_payload)
        with self.assertRaises(ContractError):
            self.request("claude_a", dict(first_payload, agent_id="forged-root"))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 2)

    def test_completed_native_child_body_is_published_without_releasing_its_slot(self):
        payload = self.original_call("claude_a", "toolu_complete")
        reserved = self.request("claude_a", payload)
        claim_id, agent_id = reserved["claim_id"], "a123456789abcdef0"
        journal = self.journals["claude_a"]
        child_path = journal.path.parent / journal.session["session_id"] / "subagents" / ("agent-" + agent_id + ".jsonl")
        child_path.parent.mkdir(parents=True)
        root_id, final_id = str(uuid.uuid4()), str(uuid.uuid4())
        native = [dict(type="user", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=root_id, parentUuid=None, timestamp=datetime.now(timezone.utc).isoformat(),
            message=dict(role="user", content=child_prompt(claim_id, payload["tool_input"]["prompt"]))),
            dict(type="assistant", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=final_id, parentUuid=root_id, timestamp=datetime.now(timezone.utc).isoformat(),
            message=dict(role="assistant", content=[dict(type="text", text="Full original child result.")], stop_reason="end_turn"))]
        child_path.write_bytes(b"".join(canonical(r) for r in native))
        response = dict(status="completed", agentId=agent_id,
                        content=[dict(type="text", text="Full original child result.")])
        result = journal.message("user", "unused", at=datetime.now(timezone.utc).isoformat())
        result["message"]["content"] = [dict(type="tool_result", tool_use_id=payload["tool_use_id"],
                                               is_error=False, content=response["content"])]
        result["toolUseResult"] = response
        journal.append(result)
        observed = self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        self.assertEqual(observed["status"], "OBSERVED")
        self.assertEqual(observed["inflight"], 1)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)
        report = budget_status(self.authority_ref)["claims"][claim_id]["report"]
        self.assertIn("Full original child result.", Path(report["reference"]["path"]).read_text())
        self.assertEqual(observed["hook_output"]["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn(report["reference"]["path"], observed["hook_output"]["hookSpecificOutput"]["additionalContext"])
        self.assertIn(report["reference"]["sha256"], observed["hook_output"]["hookSpecificOutput"]["additionalContext"])

    def test_long_complete_native_report_keeps_the_parent_delivery_notice_bounded(self):
        payload = self.original_call("claude_a", "toolu_long_report")
        claim_id = self.request("claude_a", payload)["claim_id"]
        agent_id, body = "a123456789abcdef0", "Full original report.\n" * 4000
        journal = self.journals["claude_a"]
        child_path = journal.path.parent / journal.session["session_id"] / "subagents" / ("agent-" + agent_id + ".jsonl")
        child_path.parent.mkdir(parents=True)
        root_id = str(uuid.uuid4())
        records = [dict(type="user", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=root_id, parentUuid=None, timestamp=datetime.now(timezone.utc).isoformat(),
            message=dict(role="user", content=child_prompt(claim_id, payload["tool_input"]["prompt"]))),
            dict(type="assistant", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=str(uuid.uuid4()), parentUuid=root_id, timestamp=datetime.now(timezone.utc).isoformat(),
            message=dict(role="assistant", content=[dict(type="text", text=body)], stop_reason="end_turn"))]
        child_path.write_bytes(b"".join(canonical(record) for record in records))
        response = dict(status="completed", agentId=agent_id, content=[dict(type="text", text=body)])
        native = journal.message("user", "unused", at=datetime.now(timezone.utc).isoformat())
        native["message"]["content"] = [dict(type="tool_result", tool_use_id=payload["tool_use_id"],
                                              is_error=False, content=response["content"])]
        native["toolUseResult"] = response
        journal.append(native)
        observed = self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        context = observed["hook_output"]["hookSpecificOutput"]["additionalContext"]
        self.assertLess(len(context), 10000)
        claim = budget_status(self.authority_ref)["claims"][claim_id]
        self.assertEqual(Path(claim["report"]["reference"]["path"]).read_text(), "STATUS: child-" + claim_id + "\n" + body)
        self.assertIn(claim["report"]["reference"]["sha256"], context)
        self.assertIn(claim["report"]["reference"]["path"], context)
        self.assertEqual(observed["inflight"], 1)

    def test_native_completion_must_follow_its_call_and_the_actual_child_terminal_without_future_evidence(self):
        payload = self.original_call("claude_a", "toolu_completion_order")
        claim_id = self.request("claude_a", payload)["claim_id"]
        journal, agent_id = self.journals["claude_a"], "a123456789abcdef0"
        call_time = datetime.fromisoformat(json.loads(journal.path.read_bytes().splitlines()[-1])["timestamp"])
        child_path = journal.path.parent / journal.session["session_id"] / "subagents" / ("agent-" + agent_id + ".jsonl")
        child_path.parent.mkdir(parents=True)
        root_id, body = str(uuid.uuid4()), "Complete original chronological result."
        terminal_time = datetime.now(timezone.utc)
        records = [dict(type="user", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=root_id, parentUuid=None, timestamp=call_time.isoformat(),
            message=dict(role="user", content=child_prompt(claim_id, payload["tool_input"]["prompt"]))),
            dict(type="assistant", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=str(uuid.uuid4()), parentUuid=root_id, timestamp=terminal_time.isoformat(),
            message=dict(role="assistant", content=[dict(type="text", text=body)], stop_reason="end_turn"))]
        child_path.write_bytes(b"".join(canonical(record) for record in records))
        response = dict(status="completed", agentId=agent_id, content=[dict(type="text", text=body)])
        base = journal.path.read_bytes()
        changed_times = {
            "before_original_call": call_time - timedelta(seconds=1),
            "future_parent_completion": datetime.now(timezone.utc) + timedelta(days=1),
            "before_actual_child_terminal": call_time,
        }
        for shape, stamp in changed_times.items():
            with self.subTest(shape=shape):
                journal.path.write_bytes(base)
                result = journal.message("user", "unused", at=stamp.isoformat())
                result["message"]["content"] = [dict(type="tool_result", tool_use_id=payload["tool_use_id"],
                                                      is_error=False, content=response["content"])]
                result["toolUseResult"] = response
                journal.append(result)
                with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_COMPLETION_CHRONOLOGY_CHANGED"):
                    self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
                state = budget_status(self.authority_ref)
                self.assertEqual(state["inflight"], 1)
                self.assertNotIn("report", state["claims"][claim_id])
                self.assertFalse((self.root / "reports" / (claim_id + "-native.json")).exists())
        journal.path.write_bytes(base)
        valid = journal.message("user", "unused", at=datetime.now(timezone.utc).isoformat())
        valid["message"]["content"] = [dict(type="tool_result", tool_use_id=payload["tool_use_id"],
                                              is_error=False, content=response["content"])]
        valid["toolUseResult"] = response
        journal.append(valid)
        observed = self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        self.assertEqual((observed["status"], observed["inflight"]), ("OBSERVED", 1))
        self.assertIn("report", budget_status(self.authority_ref)["claims"][claim_id])

    def test_post_hook_may_carry_the_exact_registered_updated_input_not_a_changed_task(self):
        payload = self.original_call("claude_a", "toolu_updated")
        reserved = self.request("claude_a", payload)
        updated = reserved["hook_output"]["hookSpecificOutput"]["updatedInput"]
        # The native completion is absent. An exact injected prompt must reach
        # that missing-completion check, never fail parent-call/input binding.
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED"):
            self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_input=updated,
                tool_response=dict(status="completed", agentId="a123456789abcdef0", content=[])))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_POST_INPUT_CHANGED"):
            self.request("claude_a", dict(payload, hook_event_name="PostToolUse",
                tool_input=dict(updated, prompt=updated["prompt"] + "different task"),
                tool_response=dict(status="completed", agentId="a123456789abcdef0", content=[])))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)

    def test_hook_claim_of_completed_status_without_native_result_remains_unconsumed(self):
        payload = self.original_call("claude_a", "toolu_pending")
        self.request("claude_a", payload)
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED"):
            self.request("claude_a", dict(payload, hook_event_name="PostToolUse",
                tool_response=dict(status="completed", agentId="a123456789abcdef0",
                                   content=[dict(type="text", text="Caller-authored report.")])))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)

    def test_original_parent_consumes_via_actual_native_bash_request_after_full_body_read(self):
        payload = self.original_call("claude_a", "toolu_consume")
        reserved = self.request("claude_a", payload)
        claim_id = reserved["claim_id"]
        body = "STATUS: original-complete-result\nWhole original child report.\n"
        prepared = plan(body, "original-complete-result", self.root / "reports")
        persist(prepared["reference"], body)
        record_child_report(self.authority_ref, claim_id, parent="claude_a", notice=prepared["text"])
        journal = self.journals["claude_a"]
        command = consume_command(self.python, self.controller, claim_id, prepared["reference"]["sha256"], "/original/rtk")
        consume_payload = dict(payload, tool_name="Bash", tool_use_id="toolu_consume_original",
                               tool_input=dict(command=command))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_CONSUMPTION_REQUIRED"):
            self.request("claude_a", consume_payload)
        read = read_command(self.python, self.controller, prepared["text"], "/original/rtk")
        call = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        call["message"]["content"] = [dict(type="tool_use", id="toolu_read_body", name="Bash", input=dict(command=read))]
        journal.append(call)
        result = journal.message("user", "unused", at=datetime.now(timezone.utc).isoformat())
        result["message"]["content"] = [dict(type="tool_result", tool_use_id="toolu_read_body", is_error=False,
                                               content=json.dumps(read_result(prepared["text"]))) ]
        journal.append(result)
        consume = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        consume["message"]["content"] = [dict(type="tool_use", id=consume_payload["tool_use_id"], name="Bash",
                                               input=consume_payload["tool_input"])]
        journal.append(consume)
        accepted = self.request("claude_a", consume_payload)
        self.assertEqual(accepted["status"], "OBSERVED")
        self.assertEqual(accepted["consumption_status"], "CONSUMED")
        self.assertEqual(accepted["inflight"], 0)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)
        self.assertTrue((self.root / "reports" / (claim_id + "-consumed.json")).is_file())

    def test_read_after_the_original_consume_call_cannot_release_a_slot_even_with_the_right_sha(self):
        payload = self.original_call("claude_a", "toolu_early_consume")
        claim_id = self.request("claude_a", payload)["claim_id"]
        body = "STATUS: child-consume-order\nComplete original result.\n"
        prepared = plan(body, "child-consume-order", self.root / "reports")
        persist(prepared["reference"], body)
        record_child_report(self.authority_ref, claim_id, parent="claude_a", notice=prepared["text"])
        journal = self.journals["claude_a"]
        command = consume_command(self.python, self.controller, claim_id, prepared["reference"]["sha256"], "/original/rtk")
        consume_payload = dict(payload, tool_name="Bash", tool_use_id="toolu_early_consume_command",
                               tool_input=dict(command=command))
        consume = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        consume["message"]["content"] = [dict(type="tool_use", id=consume_payload["tool_use_id"], name="Bash",
                                              input=consume_payload["tool_input"])]
        journal.append(consume)
        read = read_command(self.python, self.controller, prepared["text"], "/original/rtk")
        call = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        call["message"]["content"] = [dict(type="tool_use", id="toolu_late_body_read", name="Bash", input=dict(command=read))]
        result = journal.message("user", "unused", at=datetime.now(timezone.utc).isoformat())
        result["message"]["content"] = [dict(type="tool_result", tool_use_id="toolu_late_body_read", is_error=False,
                                              content=json.dumps(read_result(prepared["text"]))) ]
        original = journal.path.read_bytes()
        for label in ("later_time_and_offset", "earlier_time_but_later_offset"):
            with self.subTest(shape=label):
                journal.path.write_bytes(original)
                changed_call, changed_result = copy.deepcopy(call), copy.deepcopy(result)
                if label == "earlier_time_but_later_offset":
                    changed_call["timestamp"] = changed_result["timestamp"] = consume["timestamp"]
                journal.append(changed_call)
                journal.append(changed_result)
                with self.assertRaisesRegex(ContractError, "ORIGINAL_PARENT_FULL_READ_REQUIRED"):
                    self.request("claude_a", consume_payload)
                self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)

    def test_background_launch_is_refused_before_reservation_until_completion_binding_exists(self):
        payload = self.original_call("claude_a", "toolu_background")
        native = self.journals["claude_a"].path.read_bytes()
        record = json.loads(native.splitlines()[-1])
        record["message"]["content"][0]["input"]["run_in_background"] = True
        self.journals["claude_a"].path.write_bytes(b"\n".join(native.splitlines()[:-1]) + b"\n" + canonical(record))
        with self.assertRaisesRegex(ContractError, "CHILD_NATIVE_BACKGROUND_UNSUPPORTED"):
            self.request("claude_a", dict(payload, tool_input=dict(payload["tool_input"], run_in_background=True)))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)


if __name__ == "__main__":
    unittest.main()
