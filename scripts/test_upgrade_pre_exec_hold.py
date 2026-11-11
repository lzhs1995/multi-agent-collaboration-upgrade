"""Pre-exec hold: no native exec, server start or provider request before one exact release."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, file_ref, write_json
from upgrade_client_isolation import LANES
import upgrade_live_entry as entry
from upgrade_live_materialize import materialize_live
from upgrade_startup_contract import PRE_EXEC_HOLD, pre_exec_hold_enabled


class HoldContractTests(unittest.TestCase):
    def test_binding_requires_exact_opt_in_and_preserves_absence(self):
        self.assertFalse(pre_exec_hold_enabled({}))
        self.assertTrue(pre_exec_hold_enabled({"pre_exec_hold": PRE_EXEC_HOLD}))
        for value in (None, False, True, 1, "", "held-uuid-release-v0", {}, []):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "PRE_EXEC_HOLD_CHANGED"):
                pre_exec_hold_enabled({"pre_exec_hold": value})

    def test_opt_in_must_be_boolean_before_any_materialization(self):
        for value in (None, 0, 1, "true", PRE_EXEC_HOLD):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "PRE_EXEC_HOLD_CHANGED"):
                materialize_live({}, "B", binaries={}, pre_exec_hold=value)


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = str(uuid.uuid4())

    def fixture(self, name, *, held=True):
        base = self.root / name
        (base / "state/startup").mkdir(parents=True)
        (base / "work").mkdir()
        (base / "startup.json").write_text("{}")
        ref = file_ref(base / "startup.json")
        spec = dict(stop_path=str(base / "STOP"), work_root=str(base / "work"), protected_surface_uuids=[],
                    clients={lane: dict(environment={}, argv=["/usr/bin/true", lane], native_root=str(base / lane),
                                        provider=lane, session_id=None) for lane in LANES})
        binding = dict(state_root=str(base / "state"), **({"pre_exec_hold": PRE_EXEC_HOLD} if held else {}))
        write_json(base / "state/startup/intent.json", dict(startup=ref, spawn=True, spawn_authorized=True))
        return ref, spec, binding, base
    def run_client(self, fixture, lane, *, on_sleep=None, error=None):
        ref, spec, binding, _ = fixture
        surface = str(uuid.uuid4())
        incoming = {"CMUX_SURFACE_ID": surface, "CMUX_WORKSPACE_ID": self.workspace}
        with patch.dict(os.environ, incoming), patch.object(entry.os, "chdir"), patch.object(entry.os, "umask"), \
                patch("cmux_daemon_identity.process", return_value={"pid": 54321, "birth": [1, 2]}), \
                patch.object(entry, "start_server") as server, patch.object(entry.os, "execve") as execute, \
                patch.object(entry.time, "sleep", side_effect=on_sleep or (lambda _: self.fail("unexpected wait"))):
            if error:
                with self.assertRaisesRegex(ContractError, error):
                    entry.start_client(ref, spec, binding, lane)
            else:
                entry.start_client(ref, spec, binding, lane)
        return surface, server, execute

    def test_without_opt_in_the_client_execs_without_any_hold_record(self):
        fixture = self.fixture("plain", held=False)
        _, server, execute = self.run_client(fixture, "codex")
        execute.assert_called_once()
        server.assert_called_once()
        self.assertFalse((fixture[3] / "state/startup/codex-held.json").exists())

    def test_each_lane_execs_only_after_a_release_names_its_exact_held_record(self):
        for lane in LANES:
            fixture = self.fixture("held-" + lane)
            ref, _, _, base = fixture
            control = base / "state/startup"
            calls = []

            def on_sleep(_, lane=lane, control=control, ref=ref):
                calls.append(lane)
                held = file_ref(control / (lane + "-held.json"))
                write_json(control / "release.json", dict(schema="upgrade-client-release-v1", startup=ref,
                           workspace_uuid=self.workspace, held={lane: held}), exclusive=True)

            with self.subTest(lane=lane):
                surface, server, execute = self.run_client(fixture, lane, on_sleep=on_sleep)
                self.assertEqual(calls, [lane])
                execute.assert_called_once()
                self.assertEqual(server.call_count, 1 if lane == "codex" else 0)
                held = json.loads((control / (lane + "-held.json")).read_text())
                self.assertEqual((held["surface_uuid"], held["workspace_uuid"], held["native_exec"]),
                                 (surface, self.workspace, 0))

    def test_a_release_for_another_lane_record_startup_or_schema_never_releases(self):
        for case in ("other_lane", "changed_record", "other_startup", "schema", "other_workspace"):
            fixture = self.fixture("mismatch-" + case)
            ref, _, _, base = fixture
            control = base / "state/startup"

            def on_sleep(_, case=case, control=control, ref=ref):
                held = file_ref(control / "claude_a-held.json")
                if case == "changed_record":
                    held = dict(held, sha256="0" * 64)
                value = dict(schema="upgrade-client-release-v1", startup=ref, workspace_uuid=self.workspace,
                             held={("claude_b" if case == "other_lane" else "claude_a"): held})
                if case == "other_startup":
                    value["startup"] = dict(ref, sha256="1" * 64)
                if case == "schema":
                    value["schema"] = "upgrade-client-release-v0"
                if case == "other_workspace":
                    value["workspace_uuid"] = str(uuid.uuid4())
                write_json(control / "release.json", value, exclusive=True)

            with self.subTest(case=case):
                _, server, execute = self.run_client(fixture, "claude_a", on_sleep=on_sleep,
                                                     error="RELEASE_DOES_NOT_NAME_THIS_LANE")
                execute.assert_not_called()
                server.assert_not_called()
    def test_stop_or_the_deadline_ends_the_hold_without_exec(self):
        fixture = self.fixture("stop")
        calls = []

        def on_sleep(_):
            calls.append(1)
            self.assertLess(len(calls), 3, "stop was not honoured while held")
            Path(fixture[1]["stop_path"]).write_text("stop")

        _, server, execute = self.run_client(fixture, "codex", on_sleep=on_sleep, error="EXPERIMENT_STOP_REQUESTED")
        self.assertEqual(calls, [1])
        execute.assert_not_called()
        server.assert_not_called()
        fixture = self.fixture("deadline")
        with patch.object(entry, "HOLD_DEADLINE_SECONDS", 0):
            _, server, execute = self.run_client(fixture, "codex", error="PRE_EXEC_HOLD_DEADLINE")
        execute.assert_not_called()
        server.assert_not_called()

    def held_lane(self, fixture, lane, surface, *, workspace=None, launch_surface=None, overrides=None):
        ref, _, _, base = fixture
        control = base / "state/startup"
        workspace = workspace or self.workspace
        launched = write_json(control / (lane + ".json"), dict(schema="upgrade-client-launch-v2", startup=ref,
                              lane=lane, surface_uuid=launch_surface or surface, workspace_uuid=workspace),
                              exclusive=True)
        return write_json(control / (lane + "-held.json"), dict(dict(
            schema="upgrade-client-held-v1", startup=ref, lane=lane, launched=launched,
            workspace_uuid=workspace, surface_uuid=surface, native_exec=0), **(overrides or {})), exclusive=True)

    def receipt(self, fixture, text):
        path = fixture[3] / "receipt.json"
        path.write_text(text)
        return str(path), file_ref(path)["sha256"]

    def prepared(self, name, **codex):
        """Three held lanes plus a receipt naming all of them; codex= changes only the codex lane."""
        fixture = self.fixture(name)
        words = [self.workspace.upper()]
        for lane in LANES:
            surface = str(uuid.uuid4())
            self.held_lane(fixture, lane, surface, **(codex if lane == "codex" else {}))
            words.append(surface.upper())
        return (fixture, *self.receipt(fixture, " ".join(words)))

    def test_release_requires_the_pinned_receipt_to_name_every_held_surface_and_the_workspace(self):
        fixture = self.fixture("release")
        ref, spec, binding, base = fixture
        surfaces = {lane: str(uuid.uuid4()) for lane in LANES}
        for lane in LANES:
            self.held_lane(fixture, lane, surfaces[lane])
        full = json.dumps(dict(workspace=self.workspace.upper(), surfaces=[s.upper() for s in surfaces.values()]))
        cases = dict(pin=(full, "EXCLUSION_RECEIPT_PIN_CHANGED"),
                     surface=(full.replace(surfaces["claude_b"].upper(), "MISSING"), "EXCLUSION_RECEIPT_OMITS_SURFACE"),
                     workspace=(full.replace(self.workspace.upper(), "MISSING"), "EXCLUSION_RECEIPT_OMITS_WORKSPACE"),
                     lowercase=(full.lower(), "EXCLUSION_RECEIPT_OMITS_SURFACE"))
        for case, (text, error) in cases.items():
            path, sha = self.receipt(fixture, text)
            with self.subTest(case=case), self.assertRaisesRegex(ContractError, error):
                entry.release(ref, spec, binding, path, "2" * 64 if case == "pin" else sha)
        self.assertFalse((base / "state/startup/release.json").exists())
        path, sha = self.receipt(fixture, full)
        released = entry.release(ref, spec, binding, path, sha)
        value = json.loads(Path(released["path"]).read_text())
        self.assertEqual(sorted(value["held"]), sorted(LANES))
        self.assertEqual(value["exclusion_receipt"]["sha256"], sha)
        with self.assertRaises(FileExistsError):
            entry.release(ref, spec, binding, path, sha)

    def test_release_refuses_an_exec_count_split_workspaces_no_opt_in_or_stop(self):
        changed = dict(exec=dict(overrides=dict(native_exec=1)), lane=dict(overrides=dict(lane="claude_a")),
                       schema=dict(overrides=dict(schema="upgrade-client-held-v0")),
                       launch_surface=dict(launch_surface=str(uuid.uuid4())))
        for case, codex in changed.items():
            fixture, path, sha = self.prepared("changed-" + case, **codex)
            with self.subTest(case=case), self.assertRaisesRegex(ContractError, "HELD_RECORD_CHANGED"):
                entry.release(*fixture[:3], path, sha)
            self.assertFalse((fixture[3] / "state/startup/release.json").exists())
        fixture, path, sha = self.prepared("split", workspace=str(uuid.uuid4()))
        with self.assertRaisesRegex(ContractError, "HELD_LANES_SPAN_WORKSPACES"):
            entry.release(*fixture[:3], path, sha)
        unbound = self.fixture("unbound", held=False)
        with self.assertRaisesRegex(ContractError, "PRE_EXEC_HOLD_NOT_BOUND"):
            entry.release(*unbound[:3], path, sha)
        fixture, path, sha = self.prepared("stopped")
        Path(fixture[1]["stop_path"]).write_text("stop")
        with self.assertRaisesRegex(ContractError, "EXPERIMENT_STOP_REQUESTED"):
            entry.release(*fixture[:3], path, sha)
        for name in ("split", "stopped"):
            self.assertFalse((self.root / name / "state/startup/release.json").exists())

    def test_hold_accepts_the_release_written_by_release(self):
        fixture = self.fixture("join")
        ref, spec, binding, base = fixture
        others = {lane: str(uuid.uuid4()) for lane in ("claude_b", "codex")}
        done = {}

        def on_sleep(_):
            for lane, surface in others.items():
                self.held_lane(fixture, lane, surface)
            held = json.loads((base / "state/startup/claude_a-held.json").read_text())
            words = [self.workspace.upper(), held["surface_uuid"].upper()] + [s.upper() for s in others.values()]
            done["release"] = entry.release(ref, spec, binding, *self.receipt(fixture, " ".join(words)))

        _, _, execute = self.run_client(fixture, "claude_a", on_sleep=on_sleep)
        execute.assert_called_once()
        self.assertTrue(done["release"]["path"].endswith("/release.json"))

    def test_release_cli_requires_a_receipt_and_refuses_with_zero_input(self):
        fixture = self.fixture("cli")
        with patch.object(entry, "stage_bound", return_value=False), \
                patch.object(entry, "load_startup", return_value=fixture[:3]), patch("builtins.print") as printed:
            rc = entry.main(["experiment-release", "--startup", "/x", "--startup-sha256", "0" * 64])
        out = json.loads(printed.call_args.args[0])
        self.assertEqual((rc, out["status"], out["input_operations"]), (2, "REFUSED", 0))
        self.assertIn("EXCLUSION_RECEIPT_REQUIRED", out["error"])


if __name__ == "__main__":
    unittest.main()
