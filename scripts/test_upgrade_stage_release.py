"""Offline stage release build/install/rollback; no client, model or live path."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, json_ref, write_json
from upgrade_client_isolation import SANDBOX
from upgrade_experiment import init_experiment
from upgrade_live_materialize import materialize_live
from upgrade_live_runtime import validate_live_runtime
import upgrade_stage_fence as fence
import upgrade_stage_release as release
import test_upgrade_live_startup as startup_fixtures

MODELS = {"codex": "fixture-sol", "claude_a": "fixture-opus", "claude_b": "fixture-opus"}


@unittest.skipUnless(startup_fixtures.MODERN_SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(),
                     "requires fixed local MAC and cmux wrappers")
class StageReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        context = init_experiment(self.root / "l", "stage-release", startup_fixtures.MODERN_SOURCE)
        binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
        binaries.update({p + "_wrapper": startup_fixtures.WRAPPERS / ("cmux-" + p + "-wrapper")
                         for p in ("codex", "claude")})
        # A provider secret in the Claude settings must never reach staging.
        self.secret = "fixture-provider-secret-value"
        self.runtime_ref = materialize_live(context, "B", binaries=binaries,
            provider_configuration=({"model": "fixture-sol"}, {"model": "opus",
                                    "env": {"ANTHROPIC_AUTH_TOKEN": self.secret}}),
            continuity_registration=True, child_budget=True)
        self.authority = write_json(self.root / "AUTHORITY.json", {"user": "authorized fixture stage"})
        self.staging, self.backup = self.root / "staging", self.root / "backup"
        # A launched arm already has its original live-control directory; the
        # installer never creates that original evidence directory itself.
        (self.root / "l/evidence/B/live-control").mkdir(mode=0o700)
        # Host free space is not a fixture input; the floor itself is tested below.
        floor = patch.object(release, "FREE_FLOOR_BYTES", 0)
        floor.start()
        self.addCleanup(floor.stop)

    def build(self):
        ref = release.build(self.runtime_ref, "stage-a", MODELS, self.authority, self.staging, self.backup)
        return ref, release.load_plan(ref["path"], ref["sha256"])

    def test_build_apply_and_rollback_change_only_three_hook_fields(self):
        _, plan = self.build()
        for path in self.staging.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret.encode(), path.read_bytes(), path)
        self.assertEqual(release.plan_check(plan)["status"], "PLAN_VERIFIED_NOT_INSTALLED")
        self.assertFalse(Path(plan["release"]).exists() or Path(plan["control"]).exists())
        before = {key: Path(item["before"]["path"]).read_bytes() for key, item in plan["hooks"].items()}
        result = release.apply(plan)
        self.assertEqual(result["status"], "INSTALLED_NOT_ADOPTED", result)
        _, _, binding, stage = fence.load_stage(Path(plan["release"]) / "source")
        self.assertEqual(stage["models"], MODELS)
        self.assertEqual(binding["controller_root"], plan["release"] + "/source")
        validate_live_runtime(self.runtime_ref, stage_controller=stage["controller"])
        with self.assertRaises((ContractError, ValueError)):  # the original path no longer verifies
            validate_live_runtime(self.runtime_ref)
        for key, raw in before.items():
            old, new = json.loads(raw), json.loads(Path(plan["hooks"][key]["after"]["path"]).read_bytes())
            self.assertEqual({k: v for k, v in old.items() if k != "hooks"}, {k: v for k, v in new.items() if k != "hooks"})
            self.assertNotEqual(old["hooks"], new["hooks"])
            self.assertNotIn(json.loads(raw.decode())["hooks"]["PreToolUse"][0]["hooks"][0]["command"],
                             json.dumps(new["hooks"]))
            self.assertEqual(oct(Path(plan["hooks"][key]["after"]["path"]).stat().st_mode & 0o777),
                             oct(plan["hooks"][key]["before"]["mode"]))
        with self.assertRaisesRegex(ContractError, "STAGE_INSTALL_ALREADY_ATTEMPTED"):
            release.apply(plan)
        self.assertEqual(release.rollback(plan)["status"], "HOOKS_RESTORED")
        for key, raw in before.items():
            self.assertEqual(Path(plan["hooks"][key]["before"]["path"]).read_bytes(), raw)
        validate_live_runtime(self.runtime_ref)
        self.assertTrue(Path(plan["release"]).is_dir() and Path(plan["control"], "stage.json").is_file())
        self.assertEqual(release.rollback(plan)["restored"], [])  # idempotent, nothing else touched

    def test_plan_refuses_changed_target_and_changed_staging_before_any_write(self):
        _, plan = self.build()
        target = Path(plan["hooks"]["claude_a/settings.json"]["before"]["path"])
        raw = target.read_bytes()
        target.chmod(0o600)
        target.write_bytes(raw.replace(b'"opus"', b'"other"'))
        target.chmod(0o400)
        with self.assertRaises((ContractError, ValueError)):
            release.plan_check(plan)
        with self.assertRaises((ContractError, ValueError)):
            release.apply(plan)
        self.assertFalse(self.backup.exists() or Path(plan["release"]).exists())
        target.chmod(0o600)
        target.write_bytes(raw)
        target.chmod(0o400)
        self.assertEqual(release.plan_check(plan)["status"], "PLAN_VERIFIED_NOT_INSTALLED")  # control
        staged = self.staging / "control/stage.json"
        staged.chmod(0o600)
        staged.write_bytes(staged.read_bytes() + b" ")
        with self.assertRaisesRegex(ContractError, "STAGE_STAGING_CHANGED"):
            release.plan_check(plan)

    def test_stage_binding_cannot_differ_from_original_except_controller_and_fence(self):
        _, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        config = Path(plan["unchanged"]["codex/config.toml"]["path"])
        raw = config.read_bytes()
        config.chmod(0o600)
        config.write_bytes(raw + b"# changed\n")
        with self.assertRaises((ContractError, ValueError)):
            fence.load_stage(Path(plan["release"]) / "source")
        config.write_bytes(raw)
        config.chmod(0o444)
        fence.load_stage(Path(plan["release"]) / "source")

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_installed_stage_release_runs_inspect_only_inside_control_sandbox(self):
        _, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        runtime = json_ref(self.runtime_ref)
        spec = json_ref(runtime["startup"])
        harness = Path(plan["release"]) / "source/scripts/mac_harness.py"
        base = [str(SANDBOX), "-f", spec["pins"]["control_sandbox"]["path"], sys.executable, "-I", "-B",
                str(harness)]
        pin = ["--startup", runtime["startup"]["path"], "--startup-sha256", runtime["startup"]["sha256"]]
        launch = subprocess.run(base + ["experiment-launch", *pin, "--spawn", "--spawn-authorized"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(launch.stdout)["error"], "STAGE_INSPECT_ONLY", launch.stderr)
        inspect = subprocess.run(base + ["experiment-inspect", *pin], capture_output=True, text=True, timeout=60)
        error = json.loads(inspect.stdout)["error"]
        self.assertNotIn("STAGE", error)  # loaded the stage; stops at the absent original launch record
        self.assertIn("intent.json", error)

    def test_failure_at_second_hook_write_restores_the_first_and_keeps_evidence(self):
        _, plan = self.build()
        before = {key: Path(item["before"]["path"]).read_bytes() for key, item in plan["hooks"].items()}
        original, calls = release.replace_bytes, []

        def failing(path, raw, mode):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("fixture write failure")
            return original(path, raw, mode)
        with patch.object(release, "replace_bytes", side_effect=failing):
            result = release.apply(plan)
        self.assertEqual(result["status"], "INSTALL_FAILED_HOOKS_RESTORED", result)
        self.assertEqual(len(result["restored"]), 1)
        for key, raw in before.items():
            self.assertEqual(Path(plan["hooks"][key]["before"]["path"]).read_bytes(), raw)
        self.assertTrue(Path(plan["release"]).is_dir() and Path(plan["control"], "stage.json").is_file())
        validate_live_runtime(self.runtime_ref)

    def test_restore_never_overwrites_a_foreign_change(self):
        _, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        target = Path(plan["hooks"]["codex/hooks.json"]["after"]["path"])
        target.chmod(0o644)
        target.write_bytes(target.read_bytes() + b" ")
        with self.assertRaisesRegex(ContractError, "STAGE_HOOK_TARGET_CHANGED_BY_OTHER"):
            release.rollback(plan)
        self.assertTrue(target.read_bytes().endswith(b" "))

    def test_free_space_floor_refuses_before_any_write(self):
        _, plan = self.build()
        with patch.object(release.shutil, "disk_usage", return_value=release.shutil._ntuple_diskusage(
                0, 0, plan["free_floor_bytes"] + 1)):
            with self.assertRaisesRegex(ContractError, "STAGE_FREE_SPACE_BELOW_FLOOR"):
                release.apply(plan)
        self.assertFalse(self.backup.exists() or Path(plan["release"]).exists())
        self.assertEqual(release.FREE_FLOOR_BYTES, 0)  # control: fixture floor, the module default is 10 GB

    def restore_lanes(self, plan, token):
        """What restoring the sessions did to an installed stage: a rotated Claude secret, the Codex TUI marker."""
        for key in ("claude_a/settings.json", "claude_b/settings.json"):
            target = Path(plan["hooks"][key]["after"]["path"])
            document = json.loads(target.read_bytes())
            document["env"]["ANTHROPIC_AUTH_TOKEN"] = token
            target.chmod(0o600)
            target.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")
            target.chmod(plan["hooks"][key]["after"]["mode"])
        config = Path(plan["unchanged"]["codex/config.toml"]["path"])
        config.chmod(0o600)
        config.write_bytes(config.read_bytes() + release.CODEX_TUI_APPEND)
        return config

    def recovered_build(self, base, recovery=None, models=MODELS, stage_id="stage-b"):
        recovery = recovery or dict(workspace_uuid=str(uuid.uuid4()).upper(), codex_session=str(uuid.uuid4()))
        ref = release.build(self.runtime_ref, stage_id, models, self.authority, self.root / ("staging-" + stage_id),
                            self.root / ("backup-" + stage_id), base_ref=base, recovery=recovery)
        return ref, release.load_plan(ref["path"], ref["sha256"])

    def test_recovered_stage_replaces_only_base_hooks_and_pins_the_tui_marker(self):
        base, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        with self.assertRaisesRegex(ContractError, "STAGE_RECOVERED_CODEX_CONFIG_NOT_ORIGINAL_PLUS_TUI"):
            self.recovered_build(base)  # nothing restored yet: nothing to recover
        token = self.secret + "-rotated"
        config = self.restore_lanes(plan, token)
        with self.assertRaises((ContractError, ValueError)):
            fence.load_stage(Path(plan["release"]) / "source")  # the base stage itself no longer verifies
        recovery = dict(workspace_uuid=str(uuid.uuid4()).upper(), codex_session=str(uuid.uuid4()))
        _, new = self.recovered_build(base, recovery)
        self.assertEqual(release.plan_check(new)["status"], "PLAN_VERIFIED_NOT_INSTALLED")
        for path in (self.root / "staging-stage-b").rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret.encode(), path.read_bytes(), path)
        staged = json.loads((self.root / "staging-stage-b/control/stage.json").read_bytes())
        spec = json_ref(json_ref(self.runtime_ref)["startup"])
        self.assertEqual(staged["schema"], fence.RECOVERED_SCHEMA)
        self.assertEqual(staged["recovery"]["workspace_uuid"], recovery["workspace_uuid"].lower())
        self.assertEqual(staged["recovery"]["sessions"], dict(codex=recovery["codex_session"],
                         **{lane: spec["clients"][lane]["session_id"] for lane in ("claude_a", "claude_b")}))
        self.assertEqual(staged["recovery"]["pins"], {"codex/config.toml": release.mode_ref(config)})
        self.assertEqual(staged["recovery"]["pins"]["codex/config.toml"]["mode"], 0o600)
        self.assertNotIn("codex/config.toml", new["unchanged"])
        before = {key: Path(item["before"]["path"]).read_bytes() for key, item in new["hooks"].items()}
        self.assertEqual(json.loads(before["claude_a/settings.json"])["env"]["ANTHROPIC_AUTH_TOKEN"], token)
        result = release.apply(new)
        self.assertEqual(result["status"], "INSTALLED_NOT_ADOPTED", result)
        _, _, _, installed = fence.load_stage(Path(new["release"]) / "source")
        self.assertEqual(installed["recovery"], staged["recovery"])
        validate_live_runtime(self.runtime_ref, stage_controller=installed["controller"])
        for key, raw in before.items():
            after = Path(new["hooks"][key]["after"]["path"]).read_bytes()
            old, now = json.loads(raw), json.loads(after)
            self.assertEqual({k: v for k, v in old.items() if k != "hooks"}, {k: v for k, v in now.items() if k != "hooks"})
            self.assertNotIn(plan["release"] + "/source/scripts", after.decode())
            self.assertIn(new["release"] + "/source/scripts", after.decode())
        self.assertEqual(release.rollback(new)["status"], "HOOKS_RESTORED")
        for key, raw in before.items():  # back to the restored base files, rotated secret included
            self.assertEqual(Path(new["hooks"][key]["before"]["path"]).read_bytes(), raw)

    def test_recovered_build_refuses_every_change_but_the_secret_and_tui_marker(self):
        base, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        config = self.restore_lanes(plan, self.secret + "-rotated")
        settings = Path(plan["hooks"]["claude_a/settings.json"]["after"]["path"])
        codex_hooks = Path(plan["hooks"]["codex/hooks.json"]["after"]["path"])
        wrapper = Path(plan["unchanged"]["claude_ps"]["path"])

        def edit(path, change):
            raw, mode = path.read_bytes(), path.stat().st_mode & 0o777
            path.chmod(0o600)
            path.write_bytes(change(raw))
            path.chmod(mode)

            def undo():
                path.chmod(0o600)
                path.write_bytes(raw)
                path.chmod(mode)
            return undo
        claude = lambda change: lambda raw: (json.dumps(change(json.loads(raw)), sort_keys=True, indent=2) + "\n").encode()
        codex = lambda change: lambda raw: (json.dumps(change(json.loads(raw)), sort_keys=True) + "\n").encode()
        hook = [{"hooks": [{"type": "command", "command": "/bin/echo foreign"}]}]
        tui = "STAGE_RECOVERED_CODEX_CONFIG_NOT_ORIGINAL_PLUS_TUI"
        files = {
            "tui_marker_twice": (config, lambda raw: raw + release.CODEX_TUI_APPEND, tui),
            "config_other_append": (config, lambda raw: raw + b"# other\n", tui),
            "config_original_byte": (config, lambda raw: bytes([raw[0] ^ 1]) + raw[1:], tui),
            "settings_model": (settings, claude(lambda d: dict(d, model="other")), "STAGE_RECOVERED_LANE_FILE_CHANGED:claude_a"),
            "settings_other_env": (settings, claude(lambda d: dict(d, env=dict(d["env"], OTHER="1"))),
                                   "STAGE_RECOVERED_LANE_FILE_CHANGED:claude_a"),
            "settings_secret_removed": (settings, claude(lambda d: dict(d, env={k: v for k, v in d["env"].items()
                                        if k != "ANTHROPIC_AUTH_TOKEN"})), "STAGE_RECOVERED_SECRET_SHAPE_CHANGED"),
            "settings_foreign_hook": (settings, claude(lambda d: dict(d, hooks=dict(d["hooks"], Notification=hook))),
                                      "STARTUP_CHILD_HOOK_CHANGED|STAGE_RECOVERED_LANE_FILE_CHANGED"),
            "codex_hooks_field": (codex_hooks, codex(lambda d: dict(d, extra=1)),
                                  "STARTUP_CHILD_HOOK_CHANGED|STAGE_RECOVERED_LANE_FILE_CHANGED"),
            "other_original_pin": (wrapper, lambda raw: raw + b"\n", "STARTUP_ARTIFACT_PIN_CHANGED"),
        }
        def refused(call, error):
            with self.assertRaises((ContractError, ValueError)) as caught:
                call()
            # The runtime validator wraps a pin ValueError; its cause carries the exact refusal.
            self.assertRegex(str(caught.exception) + " " + str(caught.exception.__cause__), error)
        for label, (path, change, error) in files.items():
            with self.subTest(label=label):
                undo = edit(path, change)
                try:
                    refused(lambda: self.recovered_build(base), error)
                finally:
                    undo()
                self.assertFalse((self.root / "staging-stage-b").exists())
        claude_a = json_ref(json_ref(self.runtime_ref)["startup"])["clients"]["claude_a"]["session_id"]
        calls = {
            "model_changed": (lambda: self.recovered_build(base, models=dict(MODELS, codex="other")),
                              "STAGE_RECOVERY_SCOPE_CHANGED"),
            "base_plan_sha": (lambda: self.recovered_build(dict(base, sha256="0" * 64)), "trusted campaign hash mismatch"),
            "codex_is_claude": (lambda: self.recovered_build(base, dict(workspace_uuid=str(uuid.uuid4()),
                                codex_session=claude_a)), "STAGE_RECOVERY_IDENTITY_CHANGED"),
            "codex_not_uuid": (lambda: self.recovered_build(base, dict(workspace_uuid=str(uuid.uuid4()),
                               codex_session="codex")), "badly formed hexadecimal UUID string"),
        }
        for label, (call, error) in calls.items():
            with self.subTest(label=label):
                refused(call, error)
                self.assertFalse((self.root / "staging-stage-b").exists())
        self.assertEqual(self.recovered_build(base)[1]["recovery"]["pins"].keys(), {"codex/config.toml"})  # control

    def test_recovered_plan_refuses_drift_an_adopted_base_and_a_second_recovery(self):
        base, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        config = self.restore_lanes(plan, self.secret + "-rotated")
        ref, new = self.recovered_build(base)
        self.assertEqual(release.plan_check(new)["status"], "PLAN_VERIFIED_NOT_INSTALLED")
        settings = Path(plan["hooks"]["claude_b/settings.json"]["after"]["path"])
        raw, mode = settings.read_bytes(), settings.stat().st_mode & 0o777
        settings.chmod(0o600)
        settings.write_bytes(raw.replace(b"-rotated", b"-rotated-again"))
        with self.assertRaisesRegex(ContractError, "STAGE_HOOK_TARGET_CHANGED:claude_b"):
            release.plan_check(new)
        settings.write_bytes(raw)
        settings.chmod(mode)
        config.write_bytes(config.read_bytes() + b"#\n")
        with self.assertRaisesRegex(ContractError, "STAGE_RECOVERED_CODEX_CONFIG_NOT_ORIGINAL_PLUS_TUI"):
            release.apply(new)
        config.write_bytes(config.read_bytes()[:-2])
        adoption = Path(plan["control"]) / "adoption.json"
        adoption.write_text("{}")
        with self.assertRaisesRegex(ContractError, "STAGE_PRECONDITION_PRESENT"):
            release.plan_check(new)
        adoption.unlink()
        self.assertFalse(Path(new["backup"]).exists() or Path(new["release"]).exists())
        self.assertEqual(release.apply(new)["status"], "INSTALLED_NOT_ADOPTED")  # control
        with self.assertRaisesRegex(ContractError, "STAGE_BASE_PLAN_CHANGED"):
            self.recovered_build(ref, stage_id="stage-c")  # a recovered stage is never itself a base

    def launched_settings(self, stage, launches, document=None):
        """What the wrapper generated at each restored launch: the lane file plus cmux's own hooks."""
        processes = {}
        for lane in ("claude_a", "claude_b"):
            value = document(lane) if document else json.loads(Path(stage["hooks"][lane + "/settings.json"]["path"]).read_bytes())
            value = dict(value, hooks=dict(value["hooks"], SessionStart=[{"hooks": [
                {"type": "command", "command": "cmux hooks feed --source claude"}]}]))
            flag = write_json(self.root / "cmux-wrapper" / (lane + ".json"), value)
            launch = launches[lane]
            processes[launch["pid"]] = dict(pid=launch["pid"], birth=launch["birth"], argv=[
                "/fixture/claude", "--settings", flag["path"], "--resume", launch["session_id"], "--setting-sources", ""])
        return processes

    def test_launch_settings_admit_only_this_stage_controller_hooks(self):
        import upgrade_stage_control as control
        base, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        self.restore_lanes(plan, self.secret + "-rotated")
        _, new = self.recovered_build(base)
        self.assertEqual(release.apply(new)["status"], "INSTALLED_NOT_ADOPTED")
        stage, old = json_ref(new["stage"]), json_ref(plan["stage"])
        launches = {lane: dict(pid=800 + n, birth=[8, n], session_id=stage["recovery"]["sessions"][lane])
                    for n, lane in enumerate(("claude_a", "claude_b"))}
        lane_file = lambda st, lane: json.loads(Path(st["hooks"][lane + "/settings.json"]["path"]).read_bytes())
        backup = lambda lane: json.loads((Path(new["backup"]) / release.backup_name(lane + "/settings.json")).read_bytes())

        def both(lane):
            current, previous = lane_file(stage, lane), backup(lane)
            return dict(current, hooks={event: current["hooks"][event] + previous["hooks"].get(event, [])
                                        for event in current["hooks"]})
        check = lambda processes: patch("cmux_daemon_identity.process", side_effect=lambda pid, **k: processes[pid])
        with check(self.launched_settings(stage, launches)):
            result = control.launch_settings(stage, launches)
        controller_commands = lambda lane: sum(stage["controller"] + "/" in hook.get("command", "")
            for groups in lane_file(stage, lane)["hooks"].values() for group in groups for hook in group["hooks"])
        self.assertEqual({lane: item["stage_commands"] for lane, item in result.items()},
                         {lane: controller_commands(lane) for lane in ("claude_a", "claude_b")})
        self.assertTrue(all(item["stage_commands"] > 0 for item in result.values()))
        self.assertNotIn(self.secret.encode(), json.dumps(result).encode())
        cases = {
            "base_hooks_only": (lambda: self.launched_settings(stage, launches, backup), "NOT_STAGE_ONLY:claude_a"),
            "base_and_stage": (lambda: self.launched_settings(stage, launches, both), "NOT_STAGE_ONLY:claude_a"),
            "original_hooks": (lambda: self.launched_settings(stage, launches, lambda lane: json.loads(
                Path(plan["backup"], release.backup_name(lane + "/settings.json")).read_bytes())), "NOT_STAGE_ONLY:claude_a"),
            "no_release_hooks": (lambda: self.launched_settings(stage, launches, lambda lane: {"hooks": {}}),
                                 "NOT_STAGE_ONLY:claude_a"),
        }
        for label, (processes, error) in cases.items():
            with self.subTest(label=label), check(processes()), self.assertRaisesRegex(ContractError, error):
                control.launch_settings(stage, launches)
        processes = self.launched_settings(stage, launches)
        argv = processes[800]["argv"]
        for label, change, error in (
                ("other_birth", lambda p: p[800].update(birth=[9, 9]), "STAGE_RECOVERED_CLIENT_CHANGED:claude_a"),
                ("no_flag", lambda p: p[800].update(argv=argv[:1] + argv[3:]), "SETTINGS_FLAG_NOT_UNIQUE:claude_a"),
                ("two_flags", lambda p: p[800].update(argv=argv + ["--settings=" + argv[2]]), "SETTINGS_FLAG_NOT_UNIQUE:claude_a"),
                ("relative_flag", lambda p: p[800].update(argv=[argv[0], "--settings", "x.json"] + argv[3:]),
                 "SETTINGS_FLAG_NOT_UNIQUE:claude_a")):
            changed = copy.deepcopy(processes)
            change(changed)
            with self.subTest(label=label), check(changed), self.assertRaisesRegex(ContractError, error):
                control.launch_settings(stage, launches)
        self.assertNotEqual(old["controller"], stage["controller"])  # control: the base controller is the rejected one

    def test_recover_publishes_the_inspected_restored_launches_once(self):
        import time
        from orchestration_common import file_ref
        import upgrade_stage_control as control
        base, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        with self.assertRaisesRegex(ContractError, "STAGE_NOT_RECOVERED"):
            control.recover(self.runtime_ref, file_ref(plan["stage"]["path"]))  # an original stage has no recovery
        self.restore_lanes(plan, self.secret + "-rotated")
        _, new = self.recovered_build(base)
        self.assertEqual(release.apply(new)["status"], "INSTALLED_NOT_ADOPTED")
        stage_ref = file_ref(new["stage"]["path"])
        stage, startup = json_ref(stage_ref), json_ref(self.runtime_ref)["startup"]
        candidates = {lane: dict(schema=fence.RECOVERED_LAUNCH_SCHEMA, lane=lane,
            provider="codex" if lane == "codex" else "claude", native_root="/fixture/" + lane,
            session_id=stage["recovery"]["sessions"][lane], pid=700 + n, birth=[7, n], surface_uuid=str(uuid.uuid4()),
            pane_uuid=str(uuid.uuid4()), workspace_uuid=stage["recovery"]["workspace_uuid"], tty="ttys7" + str(n),
            startup=startup) for n, lane in enumerate(fence.LANES)}

        def inspection(**changes):
            result = dict(stage=stage_ref, startup=startup, recovery_candidates=candidates, stopped=False,
                          errors={"recovery": "STAGE_RECOVERY_NOT_PUBLISHED"}, observed_at_ns=time.time_ns(),
                          input_operations=0)
            return lambda *a, persist: (dict(path="/fixture/observation.json") if persist else None,
                                        dict(returncode=0, result=dict(result, **changes)))
        path = fence.recovery_path(stage)
        bad = copy.deepcopy(candidates)
        bad["claude_b"]["session_id"] = stage["recovery"]["sessions"]["claude_a"]
        refusals = {
            "inspector_not_waiting": (inspection(errors={}), "NOT_RECOVERED"),
            "inspector_failed": (inspection(errors={"recovery": "STAGE_RECOVERED_CLIENT_NOT_UNIQUE:codex=0"},
                                            recovery_candidates=None), "NOT_RECOVERED"),
            "other_stage": (inspection(stage=dict(stage_ref, sha256="0" * 64)), "NOT_RECOVERED"),
            "stale": (inspection(observed_at_ns=time.time_ns() - 31 * 10**9), "INSPECTION_TOO_OLD_FOR_RECOVERY"),
            "wrong_session": (inspection(recovery_candidates=bad), "STAGE_RECOVERY_LAUNCH_CHANGED:claude_b"),
        }
        for label, (observe, expected) in refusals.items():
            with self.subTest(label=label), patch.object(control, "observe", side_effect=observe):
                try:
                    self.assertEqual(control.recover(self.runtime_ref, stage_ref)["status"], expected)
                except ContractError as exc:
                    self.assertIn(expected, str(exc))
                self.assertFalse(path.exists())  # nothing is ever published from a refused inspection
        processes = self.launched_settings(stage, candidates)
        with patch.object(control, "observe", side_effect=inspection()) as observed, \
                patch("cmux_daemon_identity.process", side_effect=lambda pid, **k: processes[pid]):
            result = control.recover(self.runtime_ref, stage_ref)
        self.assertEqual(result["status"], "STAGE_RECOVERED", result)
        self.assertEqual(set(json_ref(result["recovery"])["launch_settings"]), {"claude_a", "claude_b"})
        self.assertEqual(observed.call_args.kwargs, {"persist": True})
        self.assertEqual(oct(path.stat().st_mode & 0o777), oct(0o444))
        self.assertEqual(fence.recovered_launches(stage, stage_ref, startup), (result["recovery"], candidates))
        with patch.object(control, "observe", side_effect=AssertionError("no second inspection")):
            self.assertEqual(control.recover(self.runtime_ref, stage_ref)["status"], "ALREADY_RECOVERED")
        self.assertEqual(file_ref(path), result["recovery"])  # never rewritten

    def test_recovered_install_failure_restores_the_restored_base_files(self):
        base, plan = self.build()
        self.assertEqual(release.apply(plan)["status"], "INSTALLED_NOT_ADOPTED")
        self.restore_lanes(plan, self.secret + "-rotated")
        _, new = self.recovered_build(base)
        before = {key: Path(item["before"]["path"]).read_bytes() for key, item in new["hooks"].items()}
        original, calls = release.replace_bytes, []

        def failing(path, raw, mode):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("fixture write failure")
            return original(path, raw, mode)
        with patch.object(release, "replace_bytes", side_effect=failing):
            result = release.apply(new)
        self.assertEqual(result["status"], "INSTALL_FAILED_HOOKS_RESTORED", result)
        self.assertEqual(len(result["restored"]), 2)
        for key, raw in before.items():
            self.assertEqual(Path(new["hooks"][key]["before"]["path"]).read_bytes(), raw)
