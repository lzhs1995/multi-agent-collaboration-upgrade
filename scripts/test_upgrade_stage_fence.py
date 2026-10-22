"""Offline stage-fence tests; fixtures are not live adoption evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, digest, file_ref, write_json
import upgrade_readiness_evidence as evidence
import upgrade_stage_fence as fence
from upgrade_startup_contract import readiness_command, readiness_prompt


class StageReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.original = str(self.root / "controller")
        self.controller = str(self.root / "stage-controller")
        self.spec = dict(controller=self.original, pins={
            "python": {"path": "/pinned/python"}, "rtk": {"path": "/pinned/rtk"},
            "codex": {"path": "/pinned/codex"}, "claude": {"path": "/pinned/claude"}},
            clients={lane: {"native_root": str(self.root / lane)} for lane in fence.LANES})
        self.startup = write_json(self.root / "startup.json", {"offline_fixture": True})
        self.binding = dict(state_root=str(self.root))
        self.stage = dict(stage_id="stage-a", model="sol", controller=self.controller,
            prompts={lane: fence.stage_prompt("stage-a", "sol", self.controller, "/pinned/python", "/pinned/rtk", lane)
                     for lane in fence.LANES})

    def fixture(self, lane, *, hook_controller=None, probe_controller=None):
        provider = "codex" if lane == "codex" else "claude"
        sid = str(uuid.uuid4())
        session = dict(provider=provider, session_id=sid, transcript=dict(path=str(self.root / (sid + ".jsonl"))))
        command = readiness_command(self.controller, "/pinned/python", "/pinned/rtk", lane)
        prompt = self.stage["prompts"][lane]
        arguments = ("text(await tools.exec_command(" + json.dumps({"cmd": command, "login": False}) + "));\n"
                     if lane == "codex" else {"command": command})
        call = dict(id="stage-call", name="functions.exec" if lane == "codex" else "Bash")
        probe = dict(status="UPGRADE_READINESS_TOOL_COMPLETED", lane=lane,
            native_root=self.spec["clients"][lane]["native_root"], controller=probe_controller or self.controller,
            adoption_proven=False, input_operations=0)
        if lane == "codex":
            old, new = str(uuid.uuid4()), str(uuid.uuid4())
            user = lambda text, turn: {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": text}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn}}}
            rows = [{"type": "event_msg", "payload": {"type": "task_started", "turn_id": old}},
                    {"type": "turn_context", "payload": {"model": "astra", "turn_id": old}},
                    user(readiness_prompt(self.original, "/pinned/python", "/pinned/rtk", lane), old),
                    {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": old}},
                    {"type": "event_msg", "payload": {"type": "task_started", "turn_id": new}},
                    user(prompt, new),
                    {"type": "turn_context", "payload": {"model": "sol", "turn_id": new}},
                    {"type": "response_item", "payload": {"type": "custom_tool_call", "name": call["name"],
                        "input": arguments, "call_id": call["id"]}},
                    {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": call["id"],
                        "output": "Script completed\nOutput:\n" + json.dumps({"exit_code": 0, "output": json.dumps(probe)})}}]
        else:
            rows = [{"type": "assistant", "sessionId": sid, "message": {"role": "assistant", "model": "astra",
                        "content": [{"type": "text", "text": "earlier history"}]}},
                    {"type": "user", "sessionId": sid, "message": {"role": "user", "content": prompt}},
                    {"type": "assistant", "sessionId": sid, "message": {"role": "assistant", "model": "sol",
                        "content": [{"type": "tool_use", "id": call["id"], "name": "Bash", "input": arguments}]}},
                    {"type": "user", "sessionId": sid, "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(probe)}]}}]
        records = [(dict(path=session["transcript"]["path"], offset=100 * n, length=100), row) for n, row in enumerate(rows)]
        hook_command = shlex.join(["/pinned/python", "-I", "-B",
            str(Path(hook_controller or self.controller) / "scripts/cmux_workflow_advisory.py"), "--hook", "observer.py"])
        config = write_json(self.root / (lane + "-hooks.json"), {"hooks": {
            event: [{"hooks": [{"type": "command", "command": hook_command}]}] for event in ("PreToolUse", "PostToolUse")}})
        self.spec["pins"]["codex/hooks.json" if lane == "codex" else lane + "/settings.json"] = config
        for event in ("PreToolUse", "PostToolUse"):
            write_json(self.root / "hook-observations" / lane / (event + ".json"), dict(
                schema="upgrade-automatic-hook-observation-v2", lane=lane, startup=self.startup, adoption_proven=False,
                direct_client=lane, hook="observer.py", configuration=config, command=hook_command,
                parents=[dict(pid=20, ppid=1, birth=[1, 2], executable="/pinned/" + provider)],
                payload=dict(session_id=sid, transcript_path=session["transcript"]["path"], hook_event_name=event,
                             tool_use_id=call["id"], tool_name=call["name"], tool_input=arguments)))
        return session, records, {lane: dict(pid=20, birth=[1, 2])}

    def check(self, lane, fixture, stage="default"):
        session, rows, launches = fixture
        with patch.object(evidence, "native_records", return_value=iter(rows)):
            return evidence.readiness_evidence(None, session, lane, self.spec, self.startup, self.binding, launches,
                                               stage=self.stage if stage == "default" else stage)

    def test_stage_window_admits_single_new_model_after_mixed_history(self):
        for lane in ("codex", "claude_a"):
            with self.subTest(lane=lane):
                fixture = self.fixture(lane)
                result = self.check(lane, fixture)
                self.assertEqual(result["model"], "sol")
                self.assertEqual(result["stage"]["window_offset"], 400 if lane == "codex" else 100)
                with self.assertRaises(ContractError):  # the original path never admits stage evidence
                    self.check(lane, self.fixture(lane), stage=None)

    def test_stage_rejects_wrong_model_duplicate_prompt_and_old_controller(self):
        for lane in ("codex", "claude_a"):
            p = 5 if lane == "codex" else 1
            cases = {
                "wrong_stage_model": (lambda r: None, dict(self.stage, model="other"), "STAGE_MODEL_CHANGED"),
                "model_switch_in_window": (lambda r: r.insert(-1, (dict(r[-1][0], offset=r[-1][0]["offset"] - 50),
                    {"type": "turn_context", "payload": {"model": "astra"}} if lane == "codex" else
                    {"type": "assistant", "message": {"model": "astra", "content": []}})), None, "STARTUP_MODEL_UNCONFIRMED_OR_CHANGED"),
                "duplicate_prompt": (lambda r: r.insert(p + 1, (dict(r[p][0], offset=r[p][0]["offset"] + 10), copy.deepcopy(r[p][1]))),
                                     None, "ORIGINAL_STARTUP_PROMPT_NOT_OBSERVED"),
            }
            for label, (mutate, stage, error) in cases.items():
                with self.subTest(lane=lane, label=label):
                    fixture = self.fixture(lane)
                    mutate(fixture[1])
                    with self.assertRaisesRegex(ContractError, error):
                        self.check(lane, fixture, stage or "default")
            with self.subTest(lane=lane, label="old_controller_probe"), self.assertRaisesRegex(
                    ContractError, "READINESS_TOOL_RESULT_NOT_OBSERVED"):
                self.check(lane, self.fixture(lane, probe_controller=self.original))
            with self.subTest(lane=lane, label="old_controller_hooks"), self.assertRaisesRegex(
                    ContractError, "AUTOMATIC_PRE_POST_HOOKS_NOT_CORRELATED"):
                self.check(lane, self.fixture(lane, hook_controller=self.original))

    def test_codex_stage_requires_its_own_started_turn(self):
        for label, mutate in {"no_task_started": lambda r: r.pop(4),
                              "no_turn_metadata": lambda r: r[5][1]["payload"].pop("internal_chat_message_metadata_passthrough")}.items():
            with self.subTest(label=label):
                fixture = self.fixture("codex")
                mutate(fixture[1])
                with self.assertRaisesRegex(ContractError, "STAGE_PROMPT_TURN_UNOBSERVED"):
                    self.check("codex", fixture)

    def stage_record(self):
        """A complete stage, binding and spec on disk (offline)."""
        evidence_root, config = self.root / "evidence", self.root / "config"
        control = evidence_root / "live-control" / "stage-a"
        control.mkdir(parents=True)
        config.mkdir()
        Path(self.controller).mkdir()
        startup = write_json(config / "startup-v2.json", {"original": True})
        authority = write_json(control / "AUTHORITY.json", {"user": "authorized stage A"})
        spec = copy.deepcopy(self.spec)
        hooks = {}
        for key in fence.HOOK_PINS:
            command = shlex.join(["/pinned/python", "-I", "-B", self.controller + "/scripts/cmux_workflow_advisory.py",
                                  "--hook", "observer.py"])
            path = self.root / "clients" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            hooks[key] = write_json(path, {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": command}]}]}})
            spec["pins"][key] = dict(path=hooks[key]["path"], sha256="0" * 64, mode=0o400)  # original pin: old hash
        stage = dict(schema=fence.SCHEMA, stage_id="stage-a", model="sol", original_startup=startup,
                     authority=dict(path=authority["path"], sha256=authority["sha256"]), controller=self.controller,
                     control=str(control), prompts=self.stage["prompts"], hooks=hooks)
        ref = write_json(control / "stage.json", stage)
        binding = dict(stage_fence=dict(path=ref["path"], stage_id="stage-a", model="sol"),
                       evidence_root=str(evidence_root), config_root=str(config), controller_root=self.controller)
        return ref, binding, spec, stage

    def test_validate_stage_accepts_complete_record_and_rejects_each_change(self):
        ref, binding, spec, stage = self.stage_record()
        self.assertEqual(fence.validate_stage(ref, binding, spec)["stage_id"], "stage-a")
        hook = lambda st: Path(st["hooks"]["claude_a/settings.json"]["path"])
        cases = {
            "binding_absent": lambda r, b, s, st: b.pop("stage_fence"),
            "binding_model": lambda r, b, s, st: b["stage_fence"].update(model="other"),
            "record_changed": lambda r, b, s, st: r.update(sha256="0" * 64),
            "original_startup_changed": lambda r, b, s, st: Path(b["config_root"], "startup-v2.json").write_text("{}"),
            "authority_changed": lambda r, b, s, st: Path(st["authority"]["path"]).write_text("{}"),
            "same_controller_as_original": lambda r, b, s, st: s.update(controller=self.controller),
            "binding_other_controller": lambda r, b, s, st: b.update(controller_root=self.original),
            "hook_file_changed": lambda r, b, s, st: hook(st).write_text(hook(st).read_text() + " "),
            "hook_path_not_original_pin": lambda r, b, s, st: s["pins"]["codex/hooks.json"].update(path="/elsewhere"),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                ref, binding, spec, stage = self.fresh_stage()
                self.assertEqual(fence.validate_stage(ref, binding, spec)["stage_id"], "stage-a")  # control: fresh passes
                mutate(ref, binding, spec, stage)
                with self.assertRaises(ContractError):
                    fence.validate_stage(ref, binding, spec)

    def fresh_stage(self):
        self.tearDown_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tearDown_temp.cleanup)
        self.root = Path(self.tearDown_temp.name).resolve()
        self.original, self.controller = str(self.root / "controller"), str(self.root / "stage-controller")
        self.stage["prompts"] = {lane: fence.stage_prompt("stage-a", "sol", self.controller, "/pinned/python",
                                                          "/pinned/rtk", lane) for lane in fence.LANES}
        self.spec["controller"] = self.original
        return self.stage_record()

    def test_validate_stage_rejects_hooks_still_naming_original_controller(self):
        ref, binding, spec, stage = self.stage_record()
        key = "codex/hooks.json"
        path = Path(stage["hooks"][key]["path"])
        path.write_text(path.read_text().replace(self.controller, self.original))
        stage["hooks"][key] = file_ref(path)
        ref = write_json(Path(ref["path"]), stage)
        with self.assertRaisesRegex(ContractError, "STAGE_HOOK_CONTROLLER_CHANGED:" + key):
            fence.validate_stage(ref, binding, spec)

    def test_claude_sidechain_model_is_not_stage_evidence(self):
        fixture = self.fixture("claude_a")
        fixture[1].insert(2, (dict(fixture[1][1][0], offset=150), {"type": "assistant", "isSidechain": True,
                                                                  "message": {"model": "other", "content": []}}))
        self.assertEqual(self.check("claude_a", fixture)["model"], "sol")
