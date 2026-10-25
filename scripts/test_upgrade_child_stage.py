"""Child service and native continuity under an installed fenced stage; offline only."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, digest, file_ref, json_ref, read_regular, tree_manifest, utc_now, write_json
from orchestration_native import NativeReader
from test_orchestration_native import NativeFixture
from upgrade_child_budget_protocol import hook_command
from upgrade_child_service import register_service, serve, validate_service_registration
from upgrade_client_isolation import LANES
from upgrade_experiment import init_experiment
from upgrade_live_materialize import materialize_live
from upgrade_live_runtime import authenticated_continuing_sessions, session_authority, validate_live_runtime
import upgrade_stage_control as control
import upgrade_stage_fence as fence
import upgrade_stage_release as release
import test_upgrade_live_startup as startup_fixtures
from test_upgrade_child_peer import kernel_measured_python

MODELS = {"codex": "fixture-sol", "claude_a": "fixture-opus", "claude_b": "fixture-opus"}


@unittest.skipUnless(startup_fixtures.MODERN_SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(),
                     "requires fixed local MAC and cmux wrappers")
class InstalledStageChildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        context = init_experiment(self.root / "l", "stage-child", startup_fixtures.MODERN_SOURCE)
        binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
        binaries.update({p + "_wrapper": startup_fixtures.WRAPPERS / ("cmux-" + p + "-wrapper")
                         for p in ("codex", "claude")})
        self.runtime_ref = materialize_live(context, "B", binaries=binaries,
            provider_configuration=({"model": "fixture-sol"}, {"model": "opus"}),
            continuity_registration=True, child_budget=True)
        (self.root / "l/evidence/B/live-control").mkdir(mode=0o700)
        floor = patch.object(release, "FREE_FLOOR_BYTES", 0)
        floor.start()
        self.addCleanup(floor.stop)
        # serve() measures the pinned Python from a probe it starts itself.
        measured = patch("upgrade_child_service.measured_python_executable", kernel_measured_python)
        measured.start()
        self.addCleanup(measured.stop)
        ref = release.build(self.runtime_ref, "stage-a", MODELS, write_json(self.root / "AUTHORITY.json", {}),
                            self.root / "staging", self.root / "backup")
        self.plan = release.load_plan(ref["path"], ref["sha256"])
        self.assertEqual(release.apply(self.plan)["status"], "INSTALLED_NOT_ADOPTED")
        self.stage_ref = file_ref(self.plan["stage"]["path"])
        self.runtime, self.spec, self.binding, self.stage = control.scope(self.runtime_ref, self.stage_ref)
        self.adoption_ref = self.adopt()

    def adopt(self):
        """The existing synthetic inspection, as the stage's own published adoption."""
        workspace, sessions, readiness, kernel = str(uuid.uuid4()), {}, {}, {}
        for index, lane in enumerate(LANES):
            provider = "codex" if lane == "codex" else "claude"
            sid = str(uuid.uuid4())
            native_root = Path(self.spec["clients"][lane]["native_root"])
            path = native_root / ("sessions" if lane == "codex" else "projects") / (sid + ".jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            records = ([dict(type="session_meta", payload=dict(id=sid)),
                        dict(type="turn_context", payload=dict(model=MODELS[lane]))]
                       if provider == "codex" else
                       [dict(type="assistant", sessionId=sid,
                             message=dict(role="assistant", model=MODELS[lane], content=[]))])
            path.write_text("".join(json.dumps(row) + "\n" for row in records))
            st = path.stat()
            session = dict(provider=provider, session_id=sid, workspace_uuid=workspace,
                surface_uuid=str(uuid.uuid4()), pane_uuid=str(uuid.uuid4()), tty="/dev/ttys998",
                process=dict(pid=8100 + index, birth=[2, index], executable="/offline/" + provider),
                native_config_root=str(native_root), model=MODELS[lane],
                transcript=dict(path=str(path), device=st.st_dev, inode=st.st_ino))
            reader = NativeReader(session, fixtures=True)
            session["freeze_fence"] = reader.snapshot()
            session["transcript"] = copy.deepcopy(session["freeze_fence"])
            session["model_record"] = list(reader.records(0, st.st_size))[-1][0]
            sessions[lane] = session
            readiness[lane] = dict(model=MODELS[lane], model_record=session["model_record"],
                stage=dict(stage_id=self.stage["stage_id"], window_offset=0, controller=self.stage["controller"]),
                hooks={"PreToolUse": [{"fixture": True}], "PostToolUse": [{"fixture": True}]})
            kernel[lane] = dict(pid=session["process"]["pid"], kernel_checked=True)
        kernel["codex-server"] = dict(pid=9998, kernel_checked=True)
        result = dict(schema=control.INSPECTION_SCHEMA, startup=self.runtime["startup"], stage=self.stage_ref,
            ready=True, stopped=False, errors={}, observed_at_ns=time.time_ns(), input_operations=0,
            sessions=sessions, readiness=readiness, kernel=kernel,
            inspector=file_ref(Path(self.stage["controller"]) / "scripts/mac_upgrade_live_inspect.py"),
            codex_capability=dict(thread_id=sessions["codex"]["session_id"],
                path=sessions["codex"]["transcript"]["path"], can_accept_direct_input=True, input_operations=0))
        observation = write_json(Path(self.stage["control"]) / "observations/fixture/result.json", dict(
            runtime=self.runtime_ref, stage=self.stage_ref, startup=self.runtime["startup"],
            command="experiment-inspect", argv=control.harness_argv(self.runtime, self.spec, self.stage),
            returncode=0, result=result, input_operations=0))
        proof = dict(schema=fence.ADOPTION_SCHEMA, runtime=self.runtime_ref, stage=self.stage_ref,
                     startup=self.runtime["startup"], verified=True, observed_at=utc_now(), inspection=observation,
                     sessions=sessions, input_operations=0)
        # Positive control: the production stage validator accepts this fixture.
        control.validate_stage_adoption(self.runtime_ref, self.stage_ref, self.runtime, self.spec, self.binding,
                                        self.stage, proof)
        return write_json(Path(self.stage["control"]) / "adoption.json", proof, exclusive=True)

    def native_authorization(self):
        sessions = authenticated_continuing_sessions(self.runtime_ref, self.adoption_ref)
        journal = NativeFixture.__new__(NativeFixture)
        journal.path, journal.provider, journal.session = (Path(sessions["codex"]["transcript"]["path"]), "codex",
                                                           sessions["codex"])
        text = "Run the stage whole-task max-two child experiment. [offline fixture only]"
        record = journal.append(journal.message("user", text, at=datetime.now(timezone.utc).isoformat()))
        return [dict(session=sessions["codex"], record=record, text_sha256=digest(text.encode()))]

    def register(self, adoption_ref=None):
        return register_service(self.runtime_ref, adoption_ref or self.adoption_ref, task_id="stage-child-task",
            native_authorizations=self.native_authorization(), child_budget_authorized=True,
            source=tree_manifest(Path(__file__).resolve().parent.parent))

    def test_installed_stage_adoption_is_the_session_authority_the_original_path_refuses(self):
        # Without the stage the installed lane files no longer match the original pins.
        with self.assertRaisesRegex(ContractError, "STARTUP_CHILD_HOOK_CHANGED"):
            validate_live_runtime(self.runtime_ref)
        _, runtime, proof = session_authority(self.runtime_ref, self.adoption_ref)
        self.assertEqual(runtime, self.runtime)
        self.assertEqual((proof["schema"], proof["stage"]), (fence.ADOPTION_SCHEMA, self.stage_ref))
        sessions = authenticated_continuing_sessions(self.runtime_ref, self.adoption_ref)
        self.assertEqual({lane: s["session_id"] for lane, s in sessions.items()},
                         {lane: s["session_id"] for lane, s in proof["sessions"].items()})
        # Native authorization reads through the stage-routed private native root.
        self.assertEqual(len(self.native_authorization()), 1)

    def test_child_service_binds_the_stage_controller_whose_hooks_the_lanes_run(self):
        registered = self.register()
        value, runtime = validate_service_registration(registered)
        self.assertEqual(runtime, self.runtime)
        self.assertEqual(value["controller"], self.stage["controller"])
        self.assertNotEqual(value["controller"], self.runtime["controller"]["path"])
        self.assertEqual(value["adoption"], self.adoption_ref)
        self.assertEqual(value["socket"], str(Path(self.binding["tmp_root"]) / "child-budget.sock"))
        self.assertEqual({lane: s["session_id"] for lane, s in value["sessions"].items()},
                         {lane: s["session_id"] for lane, s in json_ref(self.adoption_ref)["sessions"].items()})
        command = hook_command(value["python"], value["controller"])
        for lane in ("claude_a", "claude_b"):
            document = json.loads(read_regular(self.plan["hooks"][lane + "/settings.json"]["after"]["path"]))
            self.assertEqual(sum(hook.get("command") == command for group in document["hooks"]["PreToolUse"]
                                 for hook in group["hooks"]), 1, lane)
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_SERVICE_REGISTRATION_EXISTS"):
            self.register()

    def test_stage_stop_ends_service_start_but_keeps_registration_readable(self):
        registered = self.register()
        control.stop(self.stage_ref)
        with self.assertRaisesRegex(ContractError, "EXPERIMENT_STOP_REQUESTED"):
            serve(registered, timeout_seconds=1)
        self.assertFalse(Path(json_ref(registered)["socket"]).exists())
        self.assertEqual(validate_service_registration(registered)[0]["controller"], self.stage["controller"])

    def test_stage_adoption_elsewhere_or_the_original_path_is_not_authority(self):
        proof = json_ref(self.adoption_ref)
        live_control = Path(self.spec["adoption_path"]).parent
        other = write_json(live_control / "stage-other" / "adoption.json", proof, exclusive=True)
        with self.assertRaisesRegex(ContractError, "STAGE_ADOPTION_PATH_CHANGED"):
            session_authority(self.runtime_ref, other)
        outside = write_json(self.root / "copied" / "adoption.json", proof, exclusive=True)
        original = write_json(self.spec["adoption_path"], proof, exclusive=True)
        for ref in (outside, original):
            with self.subTest(path=ref["path"]), self.assertRaises(ContractError):
                self.register(ref)
        self.assertFalse((live_control / "native-child-budget").exists())

    def test_rolled_back_stage_adoption_no_longer_authorizes(self):
        self.assertEqual(release.rollback(self.plan)["status"], "HOOKS_RESTORED")
        validate_live_runtime(self.runtime_ref)  # control: the original pins are back
        with self.assertRaises(ContractError):
            session_authority(self.runtime_ref, self.adoption_ref)

    def test_native_continuity_registers_and_revalidates_under_the_stage(self):
        from upgrade_continuity_native import register_native_continuity, validate_native_registration
        registered = register_native_continuity(self.runtime_ref, self.adoption_ref, task_id="stage-child-task",
            native_authorizations=self.native_authorization(), automatic_fallback_authorized=True)
        value, runtime = validate_native_registration(registered)
        self.assertEqual(runtime, self.runtime)
        self.assertEqual(value["adoption"], self.adoption_ref)
        self.assertEqual({lane: s["session_id"] for lane, s in value["sessions"].items()},
                         {lane: s["session_id"] for lane, s in json_ref(self.adoption_ref)["sessions"].items()})


if __name__ == "__main__":
    unittest.main()
