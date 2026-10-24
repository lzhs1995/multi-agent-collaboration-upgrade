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
        self.models = {lane: "sol" for lane in fence.LANES}
        self.stage = dict(stage_id="stage-a", models=self.models, controller=self.controller,
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
                "wrong_stage_model": (lambda r: None, dict(self.stage, models=dict(self.models, **{lane: "other"})),
                                      "STAGE_MODEL_CHANGED"),
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
        stage = dict(schema=fence.SCHEMA, stage_id="stage-a", models=self.models, original_startup=startup,
                     authority=dict(path=authority["path"], sha256=authority["sha256"]), controller=self.controller,
                     control=str(control), prompts=self.stage["prompts"], hooks=hooks)
        ref = write_json(control / "stage.json", stage)
        binding = dict(stage_fence=dict(path=ref["path"], sha256=ref["sha256"], stage_id="stage-a",
                                        models=self.models),
                       evidence_root=str(evidence_root), config_root=str(config), controller_root=self.controller)
        return ref, binding, spec, stage

    def test_validate_stage_accepts_complete_record_and_rejects_each_change(self):
        ref, binding, spec, stage = self.stage_record()
        self.assertEqual(fence.validate_stage(ref, binding, spec)["stage_id"], "stage-a")
        hook = lambda st: Path(st["hooks"]["claude_a/settings.json"]["path"])
        cases = {
            "binding_absent": lambda r, b, s, st: b.pop("stage_fence"),
            "binding_model": lambda r, b, s, st: b["stage_fence"].update(models=dict(self.models, codex="other")),
            "binding_record_sha": lambda r, b, s, st: b["stage_fence"].update(sha256="0" * 64),
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
        binding["stage_fence"]["sha256"] = ref["sha256"]  # a correctly re-pinned record is still refused
        with self.assertRaisesRegex(ContractError, "STAGE_HOOK_CONTROLLER_CHANGED:" + key):
            fence.validate_stage(ref, binding, spec)

    def recovered_record(self, base=None):
        """A recovered stage over the same files: original Claude IDs, one Codex thread, one exact re-pin."""
        ref, binding, spec, stage = base or self.stage_record()
        for lane in ("claude_a", "claude_b"):
            spec["clients"][lane]["session_id"] = str(uuid.uuid4())
        config = self.root / "clients/codex/config.toml"
        config.write_bytes(b"model = 'sol'\n")
        spec["pins"]["codex/config.toml"] = dict(path=str(config), sha256=digest(b"model = 'sol'\n"), bytes=14, mode=0o444)
        config.write_bytes(b"model = 'sol'\n" + b"\n[tui]\nscreen_reader_detection_done = true\n")
        from upgrade_live_integrity import artifact_ref
        stage = dict(stage, schema=fence.RECOVERED_SCHEMA, recovery=dict(workspace_uuid=str(uuid.uuid4()),
            sessions=dict(codex=str(uuid.uuid4()), **{l: spec["clients"][l]["session_id"] for l in ("claude_a", "claude_b")}),
            pins={"codex/config.toml": artifact_ref(config)}))
        ref = write_json(Path(ref["path"]), stage)
        binding["stage_fence"]["sha256"] = ref["sha256"]
        return ref, binding, spec, stage

    def fresh_recovered(self):
        return self.recovered_record(self.fresh_stage())

    def test_recovered_stage_accepts_restored_identity_and_rejects_each_change(self):
        ref, binding, spec, stage = self.recovered_record()
        self.assertEqual(fence.validate_stage(ref, binding, spec)["recovery"], stage["recovery"])
        upper = lambda value: value.upper()
        cases = {
            "claude_session_not_original": lambda st, s: st["recovery"]["sessions"].update(claude_a=str(uuid.uuid4())),
            "codex_equals_claude": lambda st, s: st["recovery"]["sessions"].update(codex=s["clients"]["claude_a"]["session_id"]),
            "lane_missing": lambda st, s: st["recovery"]["sessions"].pop("codex"),
            "workspace_not_canonical": lambda st, s: st["recovery"].update(workspace_uuid=upper(st["recovery"]["workspace_uuid"])),
            "extra_recovery_field": lambda st, s: st["recovery"].update(pids={}),
            "pin_not_recoverable": lambda st, s: st["recovery"]["pins"].update({"claude_a/settings.json": st["recovery"]["pins"]["codex/config.toml"]}),
            "pin_other_location": lambda st, s: st["recovery"]["pins"]["codex/config.toml"].update(path="/elsewhere/config.toml"),
            "pin_equals_original": lambda st, s: st["recovery"]["pins"].update({"codex/config.toml": dict(s["pins"]["codex/config.toml"])}),
            "v1_with_recovery": lambda st, s: st.update(schema=fence.SCHEMA),
            "v2_without_recovery": lambda st, s: st.pop("recovery"),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                ref, binding, spec, stage = self.fresh_recovered()
                self.assertEqual(fence.validate_stage(ref, binding, spec)["schema"], fence.RECOVERED_SCHEMA)  # control
                mutate(stage, spec)
                ref = write_json(Path(ref["path"]), stage)
                binding["stage_fence"]["sha256"] = ref["sha256"]  # re-pinned: the content itself is refused
                with self.assertRaises((ContractError, ValueError)):
                    fence.validate_stage(ref, binding, spec)

    def test_recovered_view_uses_stage_hook_and_recovered_pins_only(self):
        ref, binding, spec, stage = self.recovered_record()
        view = fence.stage_view(spec, stage)
        self.assertEqual(view["controller"], stage["controller"])
        for key in fence.HOOK_PINS:
            self.assertEqual(view["pins"][key], stage["hooks"][key])
            self.assertNotEqual(spec["pins"][key], stage["hooks"][key])  # the original pin is untouched history
        self.assertEqual(view["pins"]["codex/config.toml"], stage["recovery"]["pins"]["codex/config.toml"])
        self.assertEqual(view["pins"]["python"], spec["pins"]["python"])
        original = fence.stage_view(spec, dict(stage, schema=fence.SCHEMA))
        self.assertIs(original["pins"], spec["pins"])  # control: an original stage keeps every original pin

    def recovery_value(self, ref, stage):
        launches = {lane: dict(schema=fence.RECOVERED_LAUNCH_SCHEMA, lane=lane,
            provider="codex" if lane == "codex" else "claude", native_root=str(self.root / lane),
            session_id=stage["recovery"]["sessions"][lane], pid=500 + n, birth=[5, n], surface_uuid=str(uuid.uuid4()),
            pane_uuid=str(uuid.uuid4()), workspace_uuid=stage["recovery"]["workspace_uuid"], tty="ttys5" + str(n),
            startup=self.startup) for n, lane in enumerate(fence.LANES)}
        return dict(schema=fence.RECOVERY_SCHEMA, stage=ref, startup=self.startup, input_operations=0, launches=launches)

    def test_recovered_launches_require_one_published_exact_record(self):
        ref, binding, spec, stage = self.recovered_record()
        self.assertEqual(fence.recovered_launches(dict(stage, schema=fence.SCHEMA), ref, self.startup), (None, None))
        with self.assertRaisesRegex(ContractError, "recovery.json"):  # unpublished: never the original launches
            fence.recovered_launches(stage, ref, self.startup)
        value = self.recovery_value(ref, stage)
        path = fence.recovery_path(stage)
        published = write_json(path, value)
        got_ref, launches = fence.recovered_launches(stage, ref, self.startup)
        self.assertEqual((got_ref, launches), (published, value["launches"]))
        cases = {
            "other_stage": lambda v: v.update(stage=dict(ref, sha256="0" * 64)),
            "other_startup": lambda v: v.update(startup=dict(self.startup, sha256="0" * 64)),
            "input_sent": lambda v: v.update(input_operations=1),
            "lane_missing": lambda v: v["launches"].pop("claude_b"),
            "wrong_session": lambda v: v["launches"]["codex"].update(session_id=str(uuid.uuid4())),
            "wrong_workspace": lambda v: v["launches"]["claude_a"].update(workspace_uuid=str(uuid.uuid4())),
            "wrong_provider": lambda v: v["launches"]["claude_a"].update(provider="codex"),
            "extra_field": lambda v: v["launches"]["claude_a"].update(title="claude_a"),
            "shared_surface": lambda v: v["launches"]["claude_b"].update(surface_uuid=v["launches"]["claude_a"]["surface_uuid"]),
            "shared_pid": lambda v: v["launches"]["claude_b"].update(pid=v["launches"]["claude_a"]["pid"]),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(value)
                mutate(changed)
                write_json(path, changed)
                with self.assertRaises(ContractError):
                    fence.recovered_launches(stage, ref, self.startup)
        write_json(path, value)
        self.assertEqual(fence.recovered_launches(stage, ref, self.startup)[1], value["launches"])  # control

    def test_hook_observer_scope_follows_only_the_published_recovered_stage(self):
        from upgrade_hook_observer import stage_scope
        ref, binding, spec, stage = self.recovered_record()
        self.assertEqual(stage_scope({}, self.startup), (None, None))
        self.assertEqual(stage_scope(binding, self.startup), (stage, None))  # before recovery: no launch source
        value = self.recovery_value(ref, stage)
        write_json(fence.recovery_path(stage), value)
        self.assertEqual(stage_scope(binding, self.startup), (stage, value["launches"]))
        with self.assertRaisesRegex(ValueError, "STAGE_RECORD_CHANGED"):
            stage_scope(dict(binding, stage_fence=dict(binding["stage_fence"], sha256="0" * 64)), self.startup)
        original = write_json(Path(ref["path"]), dict(stage, schema=fence.SCHEMA))
        self.assertEqual(stage_scope(dict(binding, stage_fence=dict(binding["stage_fence"], sha256=original["sha256"])),
                                     self.startup), (None, None))  # an original stage keeps the original launches

    def test_stage_prompt_with_any_prefix_is_not_the_stage_prompt(self):
        prefix = "claude_a（surface " + str(uuid.uuid4()).upper() + "）：\n"
        self.assertEqual(len(prefix.encode()), 62)  # the measured restored-input prefix shape
        for lane in ("codex", "claude_a"):
            with self.subTest(lane=lane):
                fixture = self.fixture(lane)
                self.assertEqual(self.check(lane, fixture)["model"], "sol")  # control: the exact prompt passes
                fixture = self.fixture(lane)
                for _, row in fixture[1]:
                    if lane == "codex" and row.get("payload", {}).get("role") == "user":
                        content = row["payload"]["content"][0]
                        if content["text"] == self.stage["prompts"][lane]:
                            content["text"] = prefix + content["text"]
                    elif row.get("type") == "user" and row["message"]["content"] == self.stage["prompts"][lane]:
                        row["message"]["content"] = prefix + row["message"]["content"]
                with self.assertRaisesRegex(ContractError, "ORIGINAL_STARTUP_PROMPT_NOT_OBSERVED"):
                    self.check(lane, fixture)

    def test_claude_sidechain_model_is_not_stage_evidence(self):
        fixture = self.fixture("claude_a")
        fixture[1].insert(2, (dict(fixture[1][1][0], offset=150), {"type": "assistant", "isSidechain": True,
                                                                  "message": {"model": "other", "content": []}}))
        self.assertEqual(self.check("claude_a", fixture)["model"], "sol")


import test_upgrade_live_startup as startup_fixtures
import upgrade_input_gate as gate


class StageInputGateTests(startup_fixtures.InputGateTests):
    """The original gate fixture, with a stage fence in the client binding."""

    def setUp(self):
        super().setUp()
        self.stage_control = self.control / "stage-a"
        self.stage_control.mkdir()
        self.stage_ref = write_json(self.stage_control / "stage.json", {"stage": "fixture"})
        self.binding["stage_fence"] = dict(path=self.stage_ref["path"], sha256=self.stage_ref["sha256"],
                                           stage_id="stage-a", models={})

    def adopt(self):
        return write_json(self.stage_control / "adoption.json", dict(schema=fence.ADOPTION_SCHEMA,
            stage=self.stage_ref, startup=self.startup, verified=True, input_operations=0, sessions=self.sessions))

    def test_stage_accepts_only_its_own_adoption_and_both_stops(self):
        # The original adoption.json is never authority for a fenced stage.
        write_json(self.control / "adoption.json", dict(schema="upgrade-live-adoption-v2",
                   startup=self.startup, verified=True, sessions=self.sessions))
        with self.assertRaises((OSError, ContractError)):
            gate.check_input(self.proof)
        self.adopt()
        gate.check_input(self.proof)  # control: the stage adoption admits enrolled endpoints
        (self.stage_control / "stop.json").write_text("stop")
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            gate.check_input(self.proof)
        (self.stage_control / "stop.json").unlink()
        self.stage_ref.update(sha256="0" * 64)
        self.binding["stage_fence"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractError, "STAGE_RECORD_CHANGED"):
            gate.check_input(self.proof)


class StageEntryTests(unittest.TestCase):
    def test_stage_release_entry_inspects_with_its_stage_and_never_launches(self):
        import io
        import upgrade_live_entry as entry
        import upgrade_live_inspect as inspector
        ref = dict(path="/fixture/startup-v2.json", sha256="a" * 64)
        stage = {"stage_id": "stage-a"}
        argv = ["--startup", ref["path"], "--startup-sha256", ref["sha256"]]
        with patch.object(entry, "stage_bound", return_value=True), \
             patch.object(fence, "load_stage", return_value=(ref, {}, {}, stage)), \
             patch.object(entry, "checked_path", side_effect=Path), \
             patch.object(entry, "load_startup") as original, patch.object(entry, "launch") as launch, \
             patch.object(entry, "start_client") as client, \
             patch.object(inspector, "inspect", return_value={"ready": False}) as inspect, \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            for command in ("experiment-launch", "experiment-client"):
                self.assertEqual(entry.main([command, *argv, "--lane", "codex"]), 2)
                self.assertIn("STAGE_INSPECT_ONLY", out.getvalue())
            self.assertEqual(entry.main(["experiment-inspect", "--startup", ref["path"],
                                         "--startup-sha256", "b" * 64]), 2)  # pin must equal the stage's startup
            inspect.assert_not_called()
            self.assertEqual(entry.main(["experiment-inspect", *argv]), 0)
        inspect.assert_called_once_with(ref, {}, {}, stage=stage)
        original.assert_not_called()
        launch.assert_not_called()
        client.assert_not_called()


class StagePromptWireTests(unittest.TestCase):
    """The shared sender's inline limit: one line, at most 700 UTF-8 bytes."""

    def test_live_length_paths_fit_one_inline_line_for_every_lane(self):
        controller = "/Users/lzhs/uz/controllers/B/releases/stage-a-sol-20261022-v2/source"
        python = "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
        for lane, model in (("codex", "gpt-6.1-sol"), ("claude_a", "claude-opus-5-5"), ("claude_b", "claude-opus-5-5")):
            text = fence.stage_prompt("stage-a-sol-20261022-v2", model, controller, python, "/opt/homebrew/bin/rtk", lane)
            self.assertLessEqual(len(text.encode()), fence.INLINE_MAX_BYTES, lane)
            self.assertFalse(any(c in text for c in "\r\n\t"), lane)
            self.assertIn("stage-a-sol-20261022-v2", text)  # the receipt marker is a substring
            self.assertIn(readiness_command(controller, python, "/opt/homebrew/bin/rtk", lane), text)

    def test_overlong_stage_is_refused_before_any_record(self):
        with self.assertRaisesRegex(ContractError, "STAGE_PROMPT_NOT_SINGLE_INLINE_LINE"):
            fence.stage_prompt("s" * 300, "m", "/c" * 40, "/p", "/r", "codex")
