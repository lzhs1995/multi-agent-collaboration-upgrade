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
import threading
import time
import unittest
import uuid

from orchestration_common import ContractError, canonical, write_json
from test_orchestration_native import NativeFixture
from upgrade_child_budget import budget_status, initialize_ledger
from upgrade_child_budget_protocol import SCHEMA, child_prompt, hook_command, consume_command
from upgrade_body_read import read_command, read_result
import upgrade_child_service as service
from upgrade_child_service import handle_event, recover_child_reports


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
        user_id = str(uuid.uuid4())
        journal.append(journal.message("user", "Continue this original bounded task.",
            at=datetime.now(timezone.utc).isoformat(), uuid=user_id))
        call = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        call.update(uuid=str(uuid.uuid4()), parentUuid=user_id)
        call["message"].update(type="message", id="msg_" + uuid.uuid4().hex, stop_reason="tool_use")
        call["message"]["content"] = [dict(type="tool_use", name="Agent", id=tool_id, input=tool_input)]
        journal.append(call)
        return dict(hook_event_name="PreToolUse", session_id=journal.session["session_id"],
                    transcript_path=str(journal.path), tool_name="Agent", tool_use_id=tool_id, tool_input=tool_input)

    def native_child(self, lane, payload, claim_id, body, *, agent_id="a123456789abcdef0", root_at=None):
        """The child's own transcript, complete before its parent's PostToolUse."""
        journal = self.journals[lane]
        child_path = journal.path.parent / journal.session["session_id"] / "subagents" / ("agent-" + agent_id + ".jsonl")
        child_path.parent.mkdir(parents=True, exist_ok=True)
        root_id = str(uuid.uuid4())
        records = [dict(type="user", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=root_id, parentUuid=None,
            timestamp=(root_at or datetime.now(timezone.utc)).isoformat(),
            message=dict(role="user", content=child_prompt(claim_id, payload["tool_input"]["prompt"]))),
            dict(type="assistant", sessionId=journal.session["session_id"], agentId=agent_id,
            isSidechain=True, uuid=str(uuid.uuid4()), parentUuid=root_id, timestamp=datetime.now(timezone.utc).isoformat(),
            message=dict(role="assistant", content=[dict(type="text", text=body)], stop_reason="end_turn"))]
        child_path.write_bytes(b"".join(canonical(record) for record in records))
        return dict(status="completed", agentId=agent_id, content=[dict(type="text", text=body)])

    def parent_result(self, lane, payload, response, *, at=None):
        """The parent result as Claude Code 2.1.303 persists it, after PostToolUse.

        Its block carries a harness hand-back frame; toolUseResult the bare report.
        """
        journal = self.journals[lane]
        text = response["content"][0]["text"] if response["content"] else ""
        frame = ("[Subagent hand-back] The text below is the final report of a subagent. The report follows:\n"
                 + "\n".join("  " + line for line in text.splitlines())
                 + "\nagentId: " + response["agentId"] + "\n<usage>tool_uses: 1</usage>")
        result = journal.message("user", "unused", at=(at or datetime.now(timezone.utc)).isoformat())
        result["message"]["content"] = [dict(type="tool_result", tool_use_id=payload["tool_use_id"],
                                              content=[dict(type="text", text=frame)])]
        result["toolUseResult"] = dict(response, agentType="general-purpose", totalTokens=1)
        journal.append(result)

    def read_and_consume(self, lane, payload, claim_id, suffix):
        """The original parent's full body read, then its exact-SHA consume call."""
        journal, report = self.journals[lane], budget_status(self.authority_ref)["claims"][claim_id]["report"]
        read = read_command(self.python, self.controller, report["notice"], "/original/rtk")
        call = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        call["message"]["content"] = [dict(type="tool_use", id="toolu_read_" + suffix, name="Bash", input=dict(command=read))]
        journal.append(call)
        result = journal.message("user", "unused", at=datetime.now(timezone.utc).isoformat())
        result["message"]["content"] = [dict(type="tool_result", tool_use_id="toolu_read_" + suffix, is_error=False,
                                              content=json.dumps(read_result(report["notice"])))]
        journal.append(result)
        command = consume_command(self.python, self.controller, claim_id, report["reference"]["sha256"], "/original/rtk")
        consume_payload = dict(payload, tool_name="Bash", tool_use_id="toolu_consume_" + suffix,
                               tool_input=dict(command=command))
        consume = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        consume["message"]["content"] = [dict(type="tool_use", id=consume_payload["tool_use_id"], name="Bash",
                                              input=consume_payload["tool_input"])]
        journal.append(consume)
        return consume_payload

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

    def test_reservation_after_the_hook_reply_budget_refuses_without_occupying_a_slot(self):
        # The installed hook stops waiting after its socket timeout and denies the
        # Agent call. A reservation made after that point would never be used.
        payload = self.original_call("claude_a", "toolu_late")
        peer = dict(pid=102, uid=os.getuid())
        with self.assertRaisesRegex(ContractError, "CHILD_SERVICE_REPLY_BUDGET_EXCEEDED"):
            handle_event(self.registration, dict(schema=SCHEMA, lane="claude_a", payload=payload), peer=peer,
                process_reader=lambda pid: copy.deepcopy(self.processes[pid]), fixtures=True, reply_deadline=0)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)
        timely = self.request("claude_a", self.original_call("claude_a", "toolu_in_time"))
        self.assertEqual((timely["status"], timely["inflight"]), ("RESERVED", 1))

    def hook_budget_request(self, lane, payload):
        peer = dict(pid=102 if lane == "claude_a" else 112, uid=os.getuid())
        return handle_event(self.registration, dict(schema=SCHEMA, lane=lane, payload=payload), peer=peer,
            process_reader=lambda pid: copy.deepcopy(self.processes[pid]), fixtures=True,
            reply_deadline=time.monotonic() + service.REPLY_BUDGET_SECONDS)

    def test_original_call_appended_after_the_hook_started_reserves_within_the_reply_budget(self):
        # u19 C2: PreToolUse read the journal before Claude appended the tool_use.
        journal = self.journals["claude_a"]
        payload = self.original_call("claude_a", "toolu_appended_late")
        lines = journal.path.read_bytes().splitlines(keepends=True)
        journal.path.write_bytes(b"".join(lines[:-1]))
        def append_call():
            with journal.path.open("ab") as stream:
                stream.write(lines[-1])
        late = threading.Timer(1.0, append_call)
        late.start()
        self.addCleanup(late.cancel)
        started = time.monotonic()
        reserved = self.hook_budget_request("claude_a", payload)
        elapsed = time.monotonic() - started
        self.assertEqual((reserved["status"], reserved["inflight"]), ("RESERVED", 1))
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, service.REPLY_BUDGET_SECONDS - service.CALL_WAIT_MARGIN_SECONDS + 0.5)

    def test_absent_original_call_is_refused_inside_the_reply_budget_without_a_slot(self):
        payload = self.original_call("claude_a", "toolu_never_appended")
        journal = self.journals["claude_a"]
        journal.path.write_bytes(b"".join(journal.path.read_bytes().splitlines(keepends=True)[:-1]))
        started = time.monotonic()
        with self.assertRaisesRegex(ContractError, "missing or duplicate native invocation"):
            self.hook_budget_request("claude_a", payload)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, service.REPLY_BUDGET_SECONDS - service.CALL_WAIT_MARGIN_SECONDS - 0.5)
        self.assertLess(elapsed, service.REPLY_BUDGET_SECONDS)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)

    def test_duplicate_original_call_is_refused_at_once_even_with_wait_budget(self):
        payload = self.original_call("claude_a", "toolu_duplicated")
        journal = self.journals["claude_a"]
        copy_record = json.loads(journal.path.read_bytes().splitlines()[-1])
        copy_record["uuid"] = str(uuid.uuid4())
        journal.append(copy_record)
        started = time.monotonic()
        with self.assertRaisesRegex(ContractError, "missing or duplicate native invocation"):
            self.hook_budget_request("claude_a", payload)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)

    def test_later_native_failure_cannot_reuse_an_old_agent_call_to_reserve_new_capacity(self):
        payload = self.original_call("claude_a", "toolu_before_failure")
        journal = self.journals["claude_a"]
        parent = json.loads(journal.path.read_bytes().splitlines()[-1])["parentUuid"]
        journal.append(dict(type="assistant", sessionId=journal.session["session_id"], uuid=str(uuid.uuid4()),
            parentUuid=parent, timestamp=datetime.now(timezone.utc).isoformat(), isApiErrorMessage=True,
            apiErrorStatus=402, message=dict(role="assistant", type="message", id=str(uuid.uuid4()),
                model="<synthetic>", stop_reason="stop_sequence", content=[dict(type="text", text="API Error: 402")])) )
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CURRENT_AGENT_CALL_REQUIRED"):
            self.request("claude_a", payload)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)
        resumed = self.request("claude_a", self.original_call("claude_a", "toolu_same_model_recovered"))
        self.assertEqual((resumed["status"], resumed["inflight"]), ("RESERVED", 1))

    def test_current_split_response_text_then_agent_reserves_its_original_call(self):
        journal = self.journals["claude_a"]
        user_id, text_id = str(uuid.uuid4()), str(uuid.uuid4())
        journal.append(journal.message("user", "Continue this original bounded task.",
            at=datetime.now(timezone.utc).isoformat(), uuid=user_id))
        message_id = "msg_" + uuid.uuid4().hex
        text = journal.message("assistant", "I will delegate the assigned independent review.",
            at=datetime.now(timezone.utc).isoformat(), uuid=text_id, parentUuid=user_id)
        text["message"].update(type="message", id=message_id, stop_reason="tool_use")
        journal.append(text)
        tool_input = dict(description="Original readonly review", prompt="Read assigned original section.",
                          subagent_type="general-purpose")
        agent = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat(),
            uuid=str(uuid.uuid4()), parentUuid=text_id)
        agent["message"].update(type="message", id=message_id, stop_reason="tool_use",
            content=[dict(type="tool_use", name="Agent", id="toolu_split_response", input=tool_input)])
        journal.append(agent)
        payload = dict(hook_event_name="PreToolUse", session_id=journal.session["session_id"],
            transcript_path=str(journal.path), tool_name="Agent", tool_use_id="toolu_split_response",
            tool_input=tool_input)
        reserved = self.request("claude_a", payload)
        self.assertEqual((reserved["status"], reserved["inflight"]), ("RESERVED", 1))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)
        self.assertNotIn("permissionDecision", reserved["hook_output"]["hookSpecificOutput"])

    def test_current_split_response_parallel_agents_share_exactly_two_task_slots(self):
        journal = self.journals["claude_a"]
        parent = str(uuid.uuid4())
        journal.append(journal.message("user", "Review the independent assigned sections.",
            at=datetime.now(timezone.utc).isoformat(), uuid=parent))
        message_id, payloads = "msg_" + uuid.uuid4().hex, []
        for index in range(3):
            tool_input = dict(description="Assigned independent review", prompt="Read assigned section " + str(index),
                              subagent_type="general-purpose")
            tool_id, current = "toolu_parallel_" + str(index), str(uuid.uuid4())
            record = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat(),
                uuid=current, parentUuid=parent)
            record["message"].update(type="message", id=message_id, stop_reason="tool_use",
                content=[dict(type="tool_use", name="Agent", id=tool_id, input=tool_input)])
            journal.append(record)
            parent = current
            payloads.append(dict(hook_event_name="PreToolUse", session_id=journal.session["session_id"],
                transcript_path=str(journal.path), tool_name="Agent", tool_use_id=tool_id, tool_input=tool_input))
        first, second = self.request("claude_a", payloads[0]), self.request("claude_a", payloads[1])
        self.assertEqual((first["status"], first["inflight"]), ("RESERVED", 1))
        self.assertEqual((second["status"], second["inflight"]), ("RESERVED", 2))
        with self.assertRaisesRegex(ContractError, "WHOLE_TASK_CHILD_LIMIT"):
            self.request("claude_a", payloads[2])
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 2)

    def test_current_response_ownership_requires_the_agent_block_in_its_original_chain(self):
        journal = self.journals["claude_a"]
        baseline = journal.path.read_bytes()
        for shape in ("unlinked", "truncated", "nonterminal"):
            with self.subTest(shape=shape):
                journal.path.write_bytes(baseline)
                user_id, text_id = str(uuid.uuid4()), str(uuid.uuid4())
                journal.append(journal.message("user", "Review this original assigned section.",
                    at=datetime.now(timezone.utc).isoformat(), uuid=user_id))
                message_id = "msg_" + uuid.uuid4().hex
                text = journal.message("assistant", "I will review it.", at=datetime.now(timezone.utc).isoformat(),
                    uuid=text_id, parentUuid=user_id)
                text["message"].update(type="message", id=message_id, stop_reason="tool_use")
                journal.append(text)
                tool_input = dict(description="Assigned review", prompt="Read the assigned section.",
                                  subagent_type="general-purpose")
                tool_id = "toolu_unqualified_" + shape
                agent = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat(),
                    uuid=str(uuid.uuid4()), parentUuid=str(uuid.uuid4()) if shape == "unlinked" else text_id)
                agent["message"].update(type="message", id=message_id,
                    stop_reason=None if shape == "nonterminal" else "tool_use",
                    content=[dict(type="tool_use", name="Agent", id=tool_id, input=tool_input)])
                if shape == "truncated":
                    agent["truncatedAfterOutput"] = True
                journal.append(agent)
                payload = dict(hook_event_name="PreToolUse", session_id=journal.session["session_id"],
                    transcript_path=str(journal.path), tool_name="Agent", tool_use_id=tool_id, tool_input=tool_input)
                with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CURRENT_AGENT_CALL_REQUIRED"):
                    self.request("claude_a", payload)
                self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)

    def test_long_parent_history_does_not_refuse_a_fresh_bounded_original_agent_call(self):
        journal = self.journals["claude_a"]
        parent = str(uuid.uuid4())
        journal.append(journal.message("user", "Historical original task.",
            at=datetime.now(timezone.utc).isoformat(), uuid=parent))
        for _ in range(70):
            current = str(uuid.uuid4())
            journal.append(dict(type="system", subtype="historical_test_telemetry",
                sessionId=journal.session["session_id"], uuid=current, parentUuid=parent,
                timestamp=datetime.now(timezone.utc).isoformat(), data="x" * (512 * 1024)))
            parent = current
        self.assertGreater(journal.path.stat().st_size, 32 * 1024 * 1024)
        journal.session["freeze_fence"] = journal.reader.snapshot()
        payload = self.original_call("claude_a", "toolu_after_long_history")
        reserved = self.request("claude_a", payload)
        self.assertEqual((reserved["status"], reserved["inflight"]), ("RESERVED", 1))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)

    def test_late_old_message_block_cannot_make_a_superseded_agent_call_current(self):
        payload = self.original_call("claude_a", "toolu_old_response")
        journal = self.journals["claude_a"]
        old = json.loads(journal.path.read_bytes().splitlines()[-1])
        newer = journal.message("assistant", "The current response is a later independent result.",
            at=datetime.now(timezone.utc).isoformat(), uuid=str(uuid.uuid4()), parentUuid=old["uuid"])
        newer["message"].update(type="message", id="msg_" + uuid.uuid4().hex, stop_reason="end_turn")
        journal.append(newer)
        late = journal.message("assistant", "A late block from the earlier response.",
            at=datetime.now(timezone.utc).isoformat(), uuid=str(uuid.uuid4()), parentUuid=newer["uuid"])
        late["message"].update(type="message", id=old["message"]["id"], stop_reason="tool_use")
        journal.append(late)
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CURRENT_AGENT_CALL_REQUIRED"):
            self.request("claude_a", payload)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)

    def test_current_child_creation_keeps_the_original_window_and_partial_tail_fail_closed(self):
        journal = self.journals["claude_a"]
        partial_payload = self.original_call("claude_a", "toolu_partial_tail")
        complete = journal.path.read_bytes()
        with journal.path.open("ab") as stream:
            stream.write(b'{"type":"assistant"')
        with self.assertRaisesRegex(ContractError, "native EOF is not a record boundary"):
            self.request("claude_a", partial_payload)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)
        journal.path.write_bytes(complete)
        for _ in range(70):
            journal.append(dict(type="system", sessionId=journal.session["session_id"],
                timestamp=datetime.now(timezone.utc).isoformat(), data="x" * (512 * 1024)))
        latest = self.original_call("claude_a", "toolu_overlong_episode")
        with self.assertRaisesRegex(ContractError, "CHILD_NATIVE_CALL_WINDOW_PENDING"):
            self.request("claude_a", latest)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)

    def test_bounded_current_tail_cannot_manufacture_a_missing_original_user_root(self):
        journal = self.journals["claude_a"]
        root = str(uuid.uuid4())
        journal.append(journal.message("user", "Original user root before the bounded current tail.",
            at=datetime.now(timezone.utc).isoformat(), uuid=root))
        parent = root
        for _ in range(70):
            current = str(uuid.uuid4())
            journal.append(dict(type="system", subtype="historical_test_telemetry",
                sessionId=journal.session["session_id"], uuid=current, parentUuid=parent,
                timestamp=datetime.now(timezone.utc).isoformat(), data="x" * (512 * 1024)))
            parent = current
        journal.session["freeze_fence"] = journal.reader.snapshot()
        tool_input = dict(description="Assigned original review", prompt="Read only the assigned section.",
                          subagent_type="general-purpose")
        tool_id = "toolu_missing_bounded_root"
        record = journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat(),
            uuid=str(uuid.uuid4()), parentUuid=parent)
        record["message"].update(type="message", id="msg_" + uuid.uuid4().hex, stop_reason="tool_use",
            content=[dict(type="tool_use", name="Agent", id=tool_id, input=tool_input)])
        journal.append(record)
        payload = dict(hook_event_name="PreToolUse", session_id=journal.session["session_id"],
            transcript_path=str(journal.path), tool_name="Agent", tool_use_id=tool_id, tool_input=tool_input)
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CURRENT_AGENT_CALL_REQUIRED"):
            self.request("claude_a", payload)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)

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
        journal = self.journals["claude_a"]
        call_time = datetime.fromisoformat(json.loads(journal.path.read_bytes().splitlines()[-1])["timestamp"])
        response = self.native_child("claude_a", payload, claim_id, "Complete original chronological result.",
                                     root_at=call_time)
        self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        base = journal.path.read_bytes()
        changed_times = {
            "before_original_call": call_time - timedelta(seconds=1),
            "future_parent_completion": datetime.now(timezone.utc) + timedelta(days=1),
            "before_actual_child_terminal": call_time,
        }
        for shape, stamp in changed_times.items():
            with self.subTest(shape=shape):
                journal.path.write_bytes(base)
                self.parent_result("claude_a", payload, response, at=stamp)
                consume = self.read_and_consume("claude_a", payload, claim_id, shape)
                with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_COMPLETION_CHRONOLOGY_CHANGED"):
                    self.request("claude_a", consume)
                state = budget_status(self.authority_ref)
                self.assertEqual(state["inflight"], 1)
                self.assertEqual(state["claims"][claim_id]["state"], "RESULT_READY")
                self.assertFalse((self.root / "reports" / (claim_id + "-consumed.json")).exists())
        journal.path.write_bytes(base)
        self.parent_result("claude_a", payload, response)
        accepted = self.request("claude_a", self.read_and_consume("claude_a", payload, claim_id, "valid"))
        self.assertEqual((accepted["consumption_status"], accepted["inflight"]), ("CONSUMED", 0))

    def test_post_hook_may_carry_the_exact_registered_updated_input_not_a_changed_task(self):
        payload = self.original_call("claude_a", "toolu_updated")
        reserved = self.request("claude_a", payload)
        updated = reserved["hook_output"]["hookSpecificOutput"]["updatedInput"]
        # The child's own transcript is absent. An exact injected prompt must
        # reach that missing-report check, never fail parent-call/input binding.
        with self.assertRaisesRegex(ContractError, "original native child report"):
            self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_input=updated,
                tool_response=dict(status="completed", agentId="a123456789abcdef0", content=[])))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_POST_INPUT_CHANGED"):
            self.request("claude_a", dict(payload, hook_event_name="PostToolUse",
                tool_input=dict(updated, prompt=updated["prompt"] + "different task"),
                tool_response=dict(status="completed", agentId="a123456789abcdef0", content=[])))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)

    def test_hook_claim_of_completed_status_without_native_child_report_remains_unreported(self):
        payload = self.original_call("claude_a", "toolu_pending")
        claim_id = self.request("claude_a", payload)["claim_id"]
        with self.assertRaisesRegex(ContractError, "original native child report"):
            self.request("claude_a", dict(payload, hook_event_name="PostToolUse",
                tool_response=dict(status="completed", agentId="a123456789abcdef0",
                                   content=[dict(type="text", text="Caller-authored report.")])))
        for response in (None, dict(status="running", agentId="a123456789abcdef0", content=[])):
            with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED"):
                self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)
        self.assertNotIn("report", budget_status(self.authority_ref)["claims"][claim_id])

    def test_original_parent_consumes_via_actual_native_bash_request_after_full_body_read(self):
        payload = self.original_call("claude_a", "toolu_consume")
        claim_id = self.request("claude_a", payload)["claim_id"]
        response = self.native_child("claude_a", payload, claim_id, "Whole original child report.")
        self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        self.parent_result("claude_a", payload, response)
        report = budget_status(self.authority_ref)["claims"][claim_id]["report"]
        command = consume_command(self.python, self.controller, claim_id, report["reference"]["sha256"], "/original/rtk")
        unread = dict(payload, tool_name="Bash", tool_use_id="toolu_consume_unread", tool_input=dict(command=command))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_CONSUMPTION_REQUIRED"):
            self.request("claude_a", unread)
        accepted = self.request("claude_a", self.read_and_consume("claude_a", payload, claim_id, "original"))
        self.assertEqual(accepted["status"], "OBSERVED")
        self.assertEqual(accepted["consumption_status"], "CONSUMED")
        self.assertEqual(accepted["inflight"], 0)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 0)
        receipt = json.loads((self.root / "reports" / (claim_id + "-consumed.json")).read_text())
        self.assertEqual(receipt["completion"]["agent_id"], response["agentId"])

    def test_postuse_runs_before_its_parent_result_exists_and_consumption_binds_it_later(self):
        # Live 2026-10-24 (live-4 C1/C2): PostToolUse ran 4.1 s before Claude Code
        # persisted the parent result, whose block is a hand-back frame around
        # the report. Requiring that result inside the hook stranded both slots.
        payload = self.original_call("claude_a", "toolu_live_order")
        claim_id = self.request("claude_a", payload)["claim_id"]
        response = self.native_child("claude_a", payload, claim_id, "executor_reask.py 6adf13bd")
        observed = self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        self.assertEqual((observed["status"], observed["inflight"]), ("OBSERVED", 1))
        context = observed["hook_output"]["hookSpecificOutput"]["additionalContext"]
        report = budget_status(self.authority_ref)["claims"][claim_id]["report"]
        self.assertIn(report["reference"]["sha256"], context)
        early = self.read_and_consume("claude_a", payload, claim_id, "before_result")
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_CHILD_COMPLETION_REQUIRED"):
            self.request("claude_a", early)
        self.assertEqual(budget_status(self.authority_ref)["inflight"], 1)
        self.parent_result("claude_a", payload, response)
        accepted = self.request("claude_a", self.read_and_consume("claude_a", payload, claim_id, "after_result"))
        self.assertEqual((accepted["consumption_status"], accepted["inflight"]), ("CONSUMED", 0))

    def test_child_tree_written_out_of_order_binds_but_a_detached_or_earlier_record_does_not(self):
        # Live 2026-10-24 (live-4 C1/C2): a child's first startup attachment
        # names a later-written attachment as parent and is stamped after it.
        payload = self.original_call("claude_a", "toolu_tree")
        claim_id = self.request("claude_a", payload)["claim_id"]
        journal = self.journals["claude_a"]
        call_time = datetime.fromisoformat(json.loads(journal.path.read_bytes().splitlines()[-1])["timestamp"])
        response = self.native_child("claude_a", payload, claim_id, "Tree-bound report.", root_at=call_time)
        child_path = journal.path.parent / journal.session["session_id"] / "subagents" / ("agent-" + response["agentId"] + ".jsonl")
        root, terminal = [json.loads(line) for line in child_path.read_bytes().splitlines()]
        time.sleep(0.05)
        span = datetime.now(timezone.utc) - call_time
        at = lambda share: (call_time + span * share).isoformat()
        def attachment(key, parent, stamp):
            return dict(type="attachment", sessionId=root["sessionId"], agentId=root["agentId"], isSidechain=True,
                        uuid=key, parentUuid=parent, timestamp=stamp, attachment=dict(type="fixture"))
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        shapes = {
            "detached_parent": [attachment(first, str(uuid.uuid4()), at(0.6)), attachment(second, root["uuid"], at(0.3))],
            "cycle": [attachment(first, second, at(0.6)), attachment(second, first, at(0.3))],
            "earlier_than_its_parent": [attachment(first, second, at(0.2)), attachment(second, root["uuid"], at(0.3))],
            "live_order": [attachment(first, second, at(0.6)), attachment(second, root["uuid"], at(0.3))],
        }
        expected = dict(detached_parent="PARENT_CHAIN_CHANGED", cycle="PARENT_CHAIN_CHANGED",
                        earlier_than_its_parent="CHILD_NATIVE_CHRONOLOGY_CHANGED")
        for shape, middle in shapes.items():
            with self.subTest(shape=shape):
                final = dict(terminal, parentUuid=first, timestamp=at(0.9))
                child_path.write_bytes(b"".join(canonical(record) for record in [root, *middle, final]))
                post = dict(payload, hook_event_name="PostToolUse", tool_response=response)
                if shape in expected:
                    with self.assertRaisesRegex(ContractError, expected[shape]):
                        self.request("claude_a", post)
                    self.assertNotIn("report", budget_status(self.authority_ref)["claims"][claim_id])
                else:
                    self.assertEqual(self.request("claude_a", post)["status"], "OBSERVED")
                    self.assertIn("report", budget_status(self.authority_ref)["claims"][claim_id])

    def test_parent_result_naming_another_child_or_body_releases_nothing(self):
        for shape in ("other_body", "other_agent"):
            with self.subTest(shape=shape):
                payload = self.original_call("claude_a", "toolu_mismatch_" + shape)
                claim_id = self.request("claude_a", payload)["claim_id"]
                response = self.native_child("claude_a", payload, claim_id, "The actual child report.")
                self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
                if shape == "other_body":
                    changed = dict(response, content=[dict(type="text", text="A different report.")])
                    expected = "ORIGINAL_CHILD_REPORT_BODY_DIFFERENT"
                else:
                    changed = dict(response, agentId="a0000000000000000")
                    expected = "original native child report"
                self.parent_result("claude_a", payload, changed)
                with self.assertRaisesRegex(ContractError, expected):
                    self.request("claude_a", self.read_and_consume("claude_a", payload, claim_id, shape))
                self.assertEqual(budget_status(self.authority_ref)["claims"][claim_id]["state"], "RESULT_READY")
                self.assertFalse((self.root / "reports" / (claim_id + "-consumed.json")).exists())

    def test_read_after_the_original_consume_call_cannot_release_a_slot_even_with_the_right_sha(self):
        payload = self.original_call("claude_a", "toolu_early_consume")
        claim_id = self.request("claude_a", payload)["claim_id"]
        response = self.native_child("claude_a", payload, claim_id, "Complete original result.")
        self.request("claude_a", dict(payload, hook_event_name="PostToolUse", tool_response=response))
        self.parent_result("claude_a", payload, response)
        report = budget_status(self.authority_ref)["claims"][claim_id]["report"]
        prepared = dict(text=report["notice"], reference=report["reference"])
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

    def test_claim_refused_before_its_parent_result_is_recovered_without_releasing_its_slot(self):
        # Live 2026-10-24: live-4 C1 and C2 were RESERVED, their PostToolUse
        # refused, and no read/consume command ever reached either parent.
        payloads, claims, responses = {}, {}, {}
        for lane, tool_id in (("claude_a", "toolu_stranded_a"), ("claude_b", "toolu_stranded_b")):
            payloads[lane] = self.original_call(lane, tool_id)
            claims[lane] = self.request(lane, payloads[lane])["claim_id"]
            responses[lane] = self.native_child(lane, payloads[lane], claims[lane], "Stranded report " + lane + ".")
        first = recover_child_reports(self.registration, fixtures=True)
        self.assertEqual((first["recovered"], set(first["pending"])), ([], set(claims.values())))
        self.parent_result("claude_a", payloads["claude_a"], responses["claude_a"])
        second = recover_child_reports(self.registration, fixtures=True)
        self.assertEqual([item["claim_id"] for item in second["recovered"]], [claims["claude_a"]])
        self.assertEqual(set(second["pending"]), {claims["claude_b"]})
        state = budget_status(self.authority_ref)
        self.assertEqual((state["inflight"], state["claims"][claims["claude_a"]]["state"]), (2, "RESULT_READY"))
        late = json.loads(Path(second["recovered"][0]["late_report"]).read_text())
        self.assertIn(state["claims"][claims["claude_a"]]["report"]["reference"]["sha256"], late["context"])
        accepted = self.request("claude_a", self.read_and_consume("claude_a", payloads["claude_a"], claims["claude_a"], "late"))
        self.assertEqual((accepted["consumption_status"], accepted["inflight"]), ("CONSUMED", 1))
        third = recover_child_reports(self.registration, fixtures=True)
        self.assertEqual((third["recovered"], set(third["pending"])), ([], {claims["claude_b"]}))

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
