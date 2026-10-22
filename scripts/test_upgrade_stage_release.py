"""Offline stage release build/install/rollback; no client, model or live path."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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
