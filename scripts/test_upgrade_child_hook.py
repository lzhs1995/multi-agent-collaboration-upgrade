"""Native child-hook contracts; no actual child/model launch."""
from pathlib import Path
import json
import tempfile
import unittest
import copy
from datetime import datetime, timezone
import uuid
import os

from orchestration_common import ContractError, canonical, digest, object_sha
from test_orchestration_native import NativeFixture
from upgrade_child_native import original_agent_call, child_report

from upgrade_child_budget_protocol import (registered_hooks, updated_agent_input, child_prompt,
                                           validate_registered_hooks)
import upgrade_child_budget_protocol as protocol
from upgrade_live_integrity import artifact_ref


@unittest.skipUnless(os.name == "posix", "requires ordinary executable fixture programs")
class ChildHookBinaryTests(unittest.TestCase):
    def test_fixed_native_program_version_must_support_blocking_hook_failures_before_new_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory).resolve() / "original-claude"
            for shape, output, code in (
                ("old", "2.1.294 (Claude Code)", 0),
                ("not_native", "2.1.303 unrelated-program", 0),
                ("extra_output", "2.1.303 (Claude Code)\\nsecond program", 0),
                ("failed", "2.1.303 (Claude Code)", 1),
                ("current", "2.1.303 (Claude Code)", 0),
            ):
                with self.subTest(shape=shape):
                    executable.write_text("#!/bin/sh\n[ \"$#\" -eq 1 ] && [ \"$1\" = \"--version\" ] || exit 97\n"
                        "printf '%b\\n' '" + output + "'\nexit " + str(code) + "\n")
                    executable.chmod(0o700)
                    pin = artifact_ref(executable)
                    if shape == "current":
                        verified = protocol.verify_child_hook_binary(pin)
                        self.assertEqual(verified["version"], "2.1.303")
                        self.assertEqual(verified["program"], pin)
                        self.assertEqual(verified["argv"], [str(executable), "--version"])
                        self.assertEqual((verified["model_requests"], verified["input_operations"]), (0, 0))
                    else:
                        with self.assertRaisesRegex(ValueError, "CHILD_NATIVE_HOOK_VERSION_UNSUPPORTED"):
                            protocol.verify_child_hook_binary(pin)


class ChildHookConfigurationTests(unittest.TestCase):
    def test_claude_pre_agent_failures_block_creation_without_changing_other_original_hooks(self):
        python, controller = "/original/python", "/original/controller"
        original = dict(hooks=dict(PreToolUse=[dict(matcher="Read", hooks=[dict(
            type="command", command="/original/readonly-observer", timeout=75)])]))
        document = registered_hooks(original, python, controller, provider="claude")
        gate = document["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(gate.get("onFailure"), "block")
        self.assertEqual(document["hooks"]["PreToolUse"][0]["matcher"], "Agent|Bash")
        self.assertEqual(document["hooks"]["PreToolUse"][1:], original["hooks"]["PreToolUse"])
        validate_registered_hooks(document, python, controller, provider="claude", enabled=True)
        for value in (None, "continue"):
            with self.subTest(on_failure=value):
                changed = copy.deepcopy(document)
                handler = changed["hooks"]["PreToolUse"][0]["hooks"][0]
                if value is None:
                    handler.pop("onFailure")
                else:
                    handler["onFailure"] = value
                with self.assertRaisesRegex(ValueError, "STARTUP_CHILD_HOOK_CHANGED"):
                    validate_registered_hooks(changed, python, controller, provider="claude", enabled=True)

    def test_original_hooks_remain_and_child_capacity_is_checked_before_agent_creation(self):
        original = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "/original/guard", "timeout": 75}]}],
            "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "/original/stop", "timeout": 75}]}]}}
        snapshot = copy.deepcopy(original)
        claude = registered_hooks(original, "/original/python", Path("/original/controller"), provider="claude")
        self.assertEqual(original, snapshot)
        self.assertEqual(claude["hooks"]["PreToolUse"][1:], original["hooks"]["PreToolUse"])
        self.assertEqual(claude["hooks"]["Stop"], original["hooks"]["Stop"])
        self.assertEqual(set(claude["hooks"]) - set(original["hooks"]), {"PostToolUse", "SubagentStart", "SubagentStop"})
        self.assertTrue(claude["hooks"]["PreToolUse"][0]["hooks"][0]["command"].endswith("mac_upgrade_child_hook.py hook"))
        self.assertEqual(claude["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"], 15)
        codex = registered_hooks(original, "/original/python", "/original/controller", provider="codex")
        self.assertEqual(set(codex["hooks"]), set(original["hooks"]))

    def test_native_prompt_update_retains_all_original_fields_without_allowing_other_guards(self):
        original = dict(prompt="Read original section A.", description="Original readonly review",
                        subagent_type="general-purpose", run_in_background=True)
        result = updated_agent_input(original, "a" * 64)
        output = result["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", output)
        self.assertEqual(set(output), {"hookEventName", "updatedInput"})
        for field in ("description", "subagent_type", "run_in_background"):
            self.assertEqual(output["updatedInput"][field], original[field])
        self.assertTrue(output["updatedInput"]["prompt"].endswith(original["prompt"]))
        self.assertIn("UPGRADE_ORIGINAL_CHILD_CLAIM=" + "a" * 64, output["updatedInput"]["prompt"])
        for extra in (dict(model="changed"), dict(resume="existing-id"), dict(team_name="other")):
            with self.assertRaisesRegex(ValueError, "CHILD_NATIVE_INPUT_UNSUPPORTED"):
                updated_agent_input(dict(original, **extra), "a" * 64)


class OriginalChildCallFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.journal = NativeFixture(self.root, session_id=str(uuid.uuid4()))
        self.journal.session["freeze_fence"] = self.journal.reader.snapshot()
        self.input = dict(prompt="Read original bounded task.", description="Readonly review",
                          subagent_type="general-purpose")
        self.payload = dict(hook_event_name="PreToolUse", session_id=self.journal.session["session_id"],
            transcript_path=str(self.journal.path), tool_name="Agent", tool_use_id="toolu_original_child",
            tool_input=self.input)


class OriginalChildCallTests(OriginalChildCallFixture):
    def test_a_claim_requires_an_original_root_native_agent_call_not_a_manual_hook_label(self):
        with self.assertRaisesRegex(ContractError, "ORIGINAL_NATIVE_AGENT_CALL_REQUIRED"):
            original_agent_call(self.journal.session, self.payload, fixtures=True)
        call = self.journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        call["message"]["content"] = [dict(type="tool_use", id=self.payload["tool_use_id"],
                                              name="Agent", input=self.input)]
        ref = self.journal.append(call)
        found = original_agent_call(self.journal.session, self.payload, fixtures=True)
        self.assertEqual(found["call_record"], ref)
        self.assertEqual(found["tool_input"], self.input)
        for changed in (dict(tool_use_id="other"), dict(tool_input=dict(self.input, prompt="changed")),
                        dict(agent_id="inherited-child"), dict(transcript_path=str(self.root / "copied.jsonl"))):
            with self.subTest(change=list(changed)):
                with self.assertRaises(ContractError):
                    original_agent_call(self.journal.session, dict(self.payload, **changed), fixtures=True)


class OriginalChildReportTests(OriginalChildCallFixture):
    def setUp(self):
        super().setUp()
        call = self.journal.message("assistant", "unused", at=datetime.now(timezone.utc).isoformat())
        call["message"]["content"] = [dict(type="tool_use", id=self.payload["tool_use_id"],
                                             name="Agent", input=self.input)]
        self.journal.append(call)
        self.call = original_agent_call(self.journal.session, self.payload, fixtures=True)
        self.claim = object_sha(dict(session_id=self.journal.session["session_id"],
                                     tool_use_id=self.payload["tool_use_id"]))
        self.child_id = "a123456789abcdef0"
        self.child_path = (self.journal.path.parent / self.journal.session["session_id"]
                           / "subagents" / ("agent-" + self.child_id + ".jsonl"))
        self.child_path.parent.mkdir(parents=True)
        self.records = []
        self.add_child("user", child_prompt(self.claim, self.input["prompt"]), parent=None)

    def add_child(self, kind, text, *, parent, **extra):
        record = dict(type=kind, sessionId=self.journal.session["session_id"], agentId=self.child_id,
            isSidechain=True, uuid=str(uuid.uuid4()), parentUuid=parent,
            timestamp=datetime.now(timezone.utc).isoformat(), **extra)
        if kind in {"assistant", "user"}:
            record["message"] = dict(role=kind, content=[dict(type="text", text=text)])
        else:
            record["attachment"] = dict(type="hook_success", content=text)
        self.records.append(record)
        with self.child_path.open("ab") as stream:
            stream.write(canonical(record))
        return record

    def observe(self):
        return child_report(self.journal.session, self.call, claim_id=self.claim,
                            agent_id=self.child_id, transcript_path=str(self.child_path), fixtures=True)

    def test_original_marker_and_complete_end_turn_body_are_required_even_with_trailing_attachment(self):
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_REPORT_PENDING"):
            self.observe()
        terminal = self.add_child("assistant", "Complete original readonly report.",
                                   parent=self.records[0]["uuid"])
        terminal["message"]["stop_reason"] = "end_turn"
        # Rewrite only this fixture to include the native terminal field.
        self.child_path.write_bytes(b"".join(canonical(record) for record in self.records))
        self.add_child("attachment", "Not the child report.", parent=terminal["uuid"])
        observed = self.observe()
        self.assertEqual(observed["body"], "Complete original readonly report.")
        self.assertEqual(observed["agent_id"], self.child_id)
        self.assertEqual(observed["terminal_uuid"], terminal["uuid"])
        self.assertEqual(observed["body_sha256"], digest(observed["body"].encode()))
        self.assertEqual(observed["input_operations"], 0)

    def test_quoted_marker_unlinked_terminal_or_other_original_child_cannot_publish_a_report(self):
        terminal = self.add_child("assistant", "Original report.", parent=self.records[0]["uuid"])
        terminal["message"]["stop_reason"] = "end_turn"
        original = copy.deepcopy(self.records)
        mutations = [
            lambda rs: rs[0]["message"]["content"][0].update(text="Quoted: " + child_prompt(self.claim, self.input["prompt"])),
            lambda rs: rs[1].update(parentUuid=str(uuid.uuid4())),
            lambda rs: rs[1].update(agentId="another-original-child"),
            lambda rs: rs[1].update(isSidechain=False),
            lambda rs: rs[1]["message"].update(stop_reason="tool_use"),
            lambda rs: rs[1].update(isApiErrorMessage=True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                records = copy.deepcopy(original)
                mutate(records)
                self.child_path.write_bytes(b"".join(canonical(record) for record in records))
                with self.assertRaises(ContractError):
                    self.observe()

    def test_final_report_requires_the_original_child_tool_calls_to_have_matching_native_results(self):
        tool_id = "toolu_original_child_read"
        invocation = self.add_child("assistant", "unused", parent=self.records[0]["uuid"])
        invocation["message"].update(stop_reason="tool_use", content=[dict(type="tool_use",
            id=tool_id, name="Read", input=dict(file_path="/original/assigned-section.md"))])
        terminal = self.add_child("assistant", "Complete report after assigned original read.", parent=invocation["uuid"])
        terminal["message"]["stop_reason"] = "end_turn"
        missing = copy.deepcopy(self.records)
        self.child_path.write_bytes(b"".join(canonical(record) for record in missing))
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_TOOL_RESULTS_PENDING"):
            self.observe()
        result = dict(type="user", sessionId=self.journal.session["session_id"], agentId=self.child_id,
            isSidechain=True, uuid=str(uuid.uuid4()), parentUuid=invocation["uuid"],
            timestamp=terminal["timestamp"], message=dict(role="user", content=[dict(type="tool_result",
                tool_use_id=tool_id, is_error=False, content="Whole assigned original section.")]))
        completed = copy.deepcopy(missing)
        completed[-1]["parentUuid"] = result["uuid"]
        completed.insert(-1, result)
        for shape in ("wrong_tool_id", "duplicate_tool_result", "duplicate_tool_call"):
            with self.subTest(shape=shape):
                changed = copy.deepcopy(completed)
                if shape == "wrong_tool_id":
                    changed[-2]["message"]["content"][0]["tool_use_id"] = "toolu_unrelated_read"
                elif shape == "duplicate_tool_result":
                    changed[-2]["message"]["content"] *= 2
                else:
                    changed[1]["message"]["content"] *= 2
                self.child_path.write_bytes(b"".join(canonical(record) for record in changed))
                with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_TOOL_RESULTS_PENDING"):
                    self.observe()
        self.child_path.write_bytes(b"".join(canonical(record) for record in completed))
        observed = self.observe()
        self.assertEqual(observed["body"], terminal["message"]["content"][0]["text"])
        self.assertEqual(observed["terminal_uuid"], terminal["uuid"])


if __name__ == "__main__":
    unittest.main()
