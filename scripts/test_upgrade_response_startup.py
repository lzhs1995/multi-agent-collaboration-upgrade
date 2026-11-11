"""Opt-in response observer wiring; no workspace, model request, or live input."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, file_ref, json_ref, verify_tree
from upgrade_client_isolation import LANES, SANDBOX, private_native_roots
from upgrade_experiment import arm_paths, init_experiment, validate_context
import upgrade_live_entry as entry
from upgrade_live_integrity import artifact_ref
from upgrade_live_materialize import materialize_live
from upgrade_startup_contract import (RESPONSE_OBSERVATION, RESPONSE_OBSERVER_FILE,
    RESPONSE_OBSERVER_PIN, bun_preload_option, client_environment,
    response_observation_enabled, validate_startup)

SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
WRAPPERS = Path("/Applications/cmux.app/Contents/Resources/bin")


class ResponseContractTests(unittest.TestCase):
    def test_binding_requires_exact_opt_in_and_preserves_absence(self):
        self.assertFalse(response_observation_enabled({}))
        self.assertTrue(response_observation_enabled({"response_observation": RESPONSE_OBSERVATION}))
        for value in (None, False, True, 1, "", "unknown", {}, []):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "OBSERVATION_CHANGED"):
                response_observation_enabled({"response_observation": value})

    def test_opt_in_must_be_boolean_before_any_materialization(self):
        for value in (None, 0, 1, "true", RESPONSE_OBSERVATION):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "OBSERVATION_CHANGED"):
                materialize_live({}, "B", binaries={}, response_observation=value)

    def test_environment_encodes_bun_preload_without_shell_quotes_or_inherited_options(self):
        # CCC's measured native parser uses backslash escapes, not shlex quotes.
        # The independently written expectation includes both quote kinds,
        # whitespace and shell metacharacters; unsupported backslash filenames
        # have a separate rejection test.
        root = Path("/private/tmp") / "observer 'quoted' \"double\"\t$name `literal`"
        encoded_root = "/private/tmp/observer\\ \\'quoted\\'\\ \\\"double\\\"\\\t$name\\ `literal`"
        paths = {name: root / name / "B" for name in
                 ("state", "config", "controllers", "campaigns", "evidence", "tmp", "bin")}
        paths["tmp"] = Path("/private/tmp/r/B")
        roots = private_native_roots(paths)
        with patch.dict(os.environ, {"BUN_OPTIONS": "--preload /untrusted.cjs"}):
            for lane in LANES:
                default = client_environment(roots, paths, lane, "/usr/bin/true")
                opted = client_environment(roots, paths, lane, "/usr/bin/true", response_observation=True)
                self.assertNotIn("BUN_OPTIONS", default)
                if lane == "codex":
                    self.assertEqual(default, opted)
                else:
                    expected = ("--preload " + encoded_root +
                                "/controllers/B/releases/private-live-v2/source/scripts/" +
                                RESPONSE_OBSERVER_FILE)
                    self.assertEqual(opted["BUN_OPTIONS"], expected)
                    self.assertEqual(opted["UPGRADE_RESPONSE_OBSERVATIONS_DIR"],
                                     str(roots[lane] / "response-observations"))
                    self.assertNotIn("UPGRADE_RESPONSE_OBSERVER_ROOT_PID", opted)

    def test_bun_preload_rejects_backslash_and_control_delimiters(self):
        for delimiter in ("\\", "\n", "\r", "\x00"):
            with self.subTest(delimiter=repr(delimiter)), self.assertRaisesRegex(
                    ContractError, "OBSERVER_PATH_CHANGED"):
                bun_preload_option("/private/tmp/observer" + delimiter + ".cjs")


@unittest.skipUnless(SOURCE.is_dir() and WRAPPERS.is_dir(), "requires fixed local MAC and cmux wrappers")
class ResponseMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name).resolve()
        cls.binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
        cls.binaries.update({p + "_wrapper": WRAPPERS / ("cmux-" + p + "-wrapper")
                             for p in ("codex", "claude")})
        cls.fixtures = {}
        for short, name, enabled, goals in (("d", "default", False, False), ("o", "observed", True, False),
                                             ("g", "goal", True, True)):
            context = init_experiment(cls.root / short, "response-" + name, SOURCE)
            extra = {"response_observation": True} if enabled else {}
            if goals:
                docs = {}
                for lane in LANES:
                    path = cls.root / (lane + ".md")
                    path.write_text("Wait for external adoption and original registered tasks.\n")
                    docs[lane] = file_ref(path)
                extra.update(goal_startup={lane: "Complete original registered work"
                                           for lane in ("claude_a", "claude_b")},
                             lane_instructions=docs)
            runtime = materialize_live(context, "B", binaries=cls.binaries,
                                       provider_configuration=({}, {}), **extra)
            runtime = json_ref(runtime)
            ref = runtime["startup"]
            spec, binding = validate_startup(ref)
            cls.fixtures[name] = dict(runtime=runtime, ref=ref, spec=spec, binding=binding,
                                      paths=arm_paths(validate_context(context), "B"))

    @contextmanager
    def changed_spec(self, name="observed"):
        fixture = self.fixtures[name]
        path = Path(fixture["ref"]["path"])
        original = path.read_bytes()
        spec = copy.deepcopy(fixture["spec"])
        def validate():
            path.chmod(0o600)
            path.write_text(json.dumps(spec))
            path.chmod(0o444)
            return validate_startup(file_ref(path))
        try:
            yield spec, validate
        finally:
            path.chmod(0o600)
            path.write_bytes(original)
            path.chmod(0o444)

    def test_default_has_no_observer_artifact_pin_directory_or_environment(self):
        fixture = self.fixtures["default"]
        spec = fixture["spec"]
        self.assertEqual(spec["schema"], "upgrade-startup-v2")
        self.assertNotIn("response_observation", fixture["binding"])
        self.assertNotIn(RESPONSE_OBSERVER_PIN, spec["pins"])
        self.assertNotIn("scripts/" + RESPONSE_OBSERVER_FILE, fixture["runtime"]["controller"]["files"])
        for client in spec["clients"].values():
            self.assertNotIn("BUN_OPTIONS", client["environment"])
            self.assertFalse(any(k.startswith("UPGRADE_RESPONSE_") for k in client["environment"]))
            self.assertFalse((Path(client["native_root"]) / "response-observations").exists())

    def test_unsupported_preload_filename_refuses_before_arm_artifacts(self):
        context = init_experiment(self.root / "bad\\", "response-unsupported", SOURCE)
        paths = arm_paths(validate_context(context), "B")
        with self.assertRaisesRegex(ContractError, "OBSERVER_PATH_CHANGED"):
            materialize_live(context, "B", binaries=self.binaries,
                             provider_configuration=({}, {}), response_observation=True)
        self.assertFalse(any(path.exists() for path in paths.values()))

    def test_enabled_v2_and_v3_freeze_exact_module_without_changing_schema(self):
        for name, schema in (("observed", "upgrade-startup-v2"), ("goal", "upgrade-startup-v3")):
            fixture = self.fixtures[name]
            spec = fixture["spec"]
            self.assertEqual(spec["schema"], schema)
            self.assertEqual(fixture["binding"]["response_observation"], RESPONSE_OBSERVATION)
            pin = spec["pins"][RESPONSE_OBSERVER_PIN]
            self.assertEqual(pin["path"], str(Path(spec["controller"]) / "scripts" / RESPONSE_OBSERVER_FILE))
            self.assertEqual(pin["mode"], 0o444)
            self.assertEqual(artifact_ref(pin["path"]), pin)
            self.assertEqual(Path(pin["path"]).read_bytes(), Path(__file__).with_name(RESPONSE_OBSERVER_FILE).read_bytes())
            verify_tree(fixture["runtime"]["controller"])
            for lane, client in spec["clients"].items():
                directory = Path(client["native_root"]) / "response-observations"
                self.assertNotIn("UPGRADE_RESPONSE_OBSERVER_ROOT_PID", client["environment"])
                if lane == "codex":
                    self.assertFalse(directory.exists())
                    self.assertNotIn("BUN_OPTIONS", client["environment"])
                else:
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(list(directory.iterdir()), [])
                    self.assertEqual(client["environment"]["BUN_OPTIONS"], "--preload " + pin["path"])
            self.assertFalse(Path(spec["adoption_path"]).exists())
            self.assertFalse((fixture["paths"]["state"] / "startup").exists())

    def test_missing_added_mislocated_or_mutable_pin_is_rejected(self):
        changes = (lambda x: x["pins"].pop(RESPONSE_OBSERVER_PIN),
                   lambda x: x["pins"][RESPONSE_OBSERVER_PIN].update(path="/private/tmp/wrong.cjs"),
                   lambda x: x["pins"][RESPONSE_OBSERVER_PIN].update(mode=0o644))
        for change in changes:
            with self.changed_spec() as (spec, validate):
                change(spec)
                with self.assertRaises(ContractError):
                    validate()
        with self.changed_spec("default") as (spec, validate):
            spec["pins"][RESPONSE_OBSERVER_PIN] = self.fixtures["observed"]["spec"]["pins"][RESPONSE_OBSERVER_PIN]
            with self.assertRaisesRegex(ContractError, "PIN_SET_CHANGED"):
                validate()

    def test_added_preload_in_disabled_mode_and_environment_tampering_are_rejected(self):
        cases = (("default", "claude_a", {"BUN_OPTIONS": "--preload /private/tmp/foreign.cjs"}),
                 ("observed", "claude_a", {"BUN_OPTIONS": ""}),
                 ("observed", "claude_a", {"BUN_OPTIONS": "--preload /private/tmp/foreign.cjs"}),
                 ("observed", "claude_a", {"UPGRADE_RESPONSE_OBSERVER_ROOT_PID": "1"}),
                 ("observed", "claude_a", {"UPGRADE_RESPONSE_OBSERVATIONS_DIR":
                     self.fixtures["observed"]["spec"]["clients"]["claude_b"]["native_root"] + "/response-observations"}),
                 ("observed", "codex", {"BUN_OPTIONS": "--preload /private/tmp/foreign.cjs"}))
        for name, lane, extra in cases:
            with self.subTest(name=name, lane=lane, extra=extra), self.changed_spec(name) as (spec, validate):
                spec["clients"][lane]["environment"].update(extra)
                with self.assertRaisesRegex(ContractError, "ARGUMENTS_OR_ENV_CHANGED"):
                    validate()

    def test_module_content_tamper_is_rejected_by_startup_and_controller(self):
        fixture = self.fixtures["observed"]
        path = Path(fixture["spec"]["pins"][RESPONSE_OBSERVER_PIN]["path"])
        original = path.read_bytes()
        try:
            path.chmod(0o600)
            path.write_bytes(original + b"\n// changed\n")
            path.chmod(0o444)
            with self.assertRaisesRegex(ValueError, "ARTIFACT_PIN_CHANGED"):
                validate_startup(fixture["ref"])
            with self.assertRaises((ContractError, ValueError)):
                verify_tree(fixture["runtime"]["controller"])
        finally:
            path.chmod(0o600)
            path.write_bytes(original)
            path.chmod(0o444)

    def test_binding_unknown_value_or_downgrade_is_rejected(self):
        fixture = self.fixtures["observed"]
        path = Path(fixture["spec"]["controller"]) / "upgrade-binding.json"
        original = path.read_bytes()
        try:
            for remove in (False, True):
                binding = copy.deepcopy(fixture["binding"])
                if remove:
                    del binding["response_observation"]
                else:
                    binding["response_observation"] = "unreviewed-v9"
                path.chmod(0o600)
                path.write_text(json.dumps(binding))
                path.chmod(0o444)
                with self.assertRaises(ContractError):
                    validate_startup(fixture["ref"])
        finally:
            path.chmod(0o600)
            path.write_bytes(original)
            path.chmod(0o444)

    def test_observation_directory_mode_symlink_or_absence_is_rejected(self):
        fixture = self.fixtures["observed"]
        path = Path(fixture["spec"]["clients"]["claude_a"]["native_root"]) / "response-observations"
        path.chmod(0o755)
        try:
            with self.assertRaisesRegex(ContractError, "OBSERVATION_DIRECTORY_CHANGED"):
                validate_startup(fixture["ref"])
        finally:
            path.chmod(0o700)
        path.rmdir()
        try:
            with self.assertRaises(FileNotFoundError):
                validate_startup(fixture["ref"])
            target = Path(fixture["spec"]["clients"]["claude_b"]["native_root"]) / "response-observations"
            path.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ContractError):
                validate_startup(fixture["ref"])
        finally:
            if path.is_symlink():
                path.unlink()
            path.mkdir(mode=0o700)

    def test_entry_uses_own_pid_only_for_enabled_claude_and_strips_parent_observer(self):
        incoming = {"CMUX_SURFACE_ID": str(uuid.uuid4()), "CMUX_WORKSPACE_ID": str(uuid.uuid4()),
                    "BUN_OPTIONS": "--preload /untrusted.cjs", "NODE_OPTIONS": "--require /untrusted.cjs",
                    "UPGRADE_RESPONSE_OBSERVATIONS_DIR": "/untrusted",
                    "UPGRADE_RESPONSE_OBSERVER_ROOT_PID": "999999",
                    "UPGRADE_RESPONSE_UNREGISTERED": "inherited"}
        for name, lane in (("default", "claude_a"), ("observed", "codex"),
                           ("observed", "claude_a"), ("observed", "claude_b")):
            fixture = self.fixtures[name]
            intent = json.dumps(dict(startup=fixture["ref"], spawn=True, spawn_authorized=True)).encode()
            with self.subTest(name=name, lane=lane), patch.dict(os.environ, incoming), \
                    patch.object(entry, "read_regular", return_value=intent), \
                    patch.object(entry, "write_json"), patch.object(entry, "start_server"), \
                    patch.object(entry.os, "chdir"), patch.object(entry.os, "umask"), \
                    patch.object(entry.os, "getpid", return_value=54321), \
                    patch("cmux_daemon_identity.process", return_value={"pid": 54321, "birth": [123, 456]}), \
                    patch.object(entry.os, "execve") as execute:
                entry.start_client(fixture["ref"], fixture["spec"], fixture["binding"], lane)
                execute.assert_called_once()
                env = execute.call_args.args[2]
                self.assertNotIn("NODE_OPTIONS", env)
                self.assertNotIn("UPGRADE_RESPONSE_UNREGISTERED", env)
                if name == "observed" and lane != "codex":
                    self.assertEqual(env["UPGRADE_RESPONSE_OBSERVER_ROOT_PID"], "54321")
                    expected = fixture["spec"]["clients"][lane]["environment"]
                    self.assertEqual(env["BUN_OPTIONS"], expected["BUN_OPTIONS"])
                    self.assertEqual(env["UPGRADE_RESPONSE_OBSERVATIONS_DIR"], expected["UPGRADE_RESPONSE_OBSERVATIONS_DIR"])
                else:
                    self.assertNotIn("BUN_OPTIONS", env)
                    self.assertFalse(any(k.startswith("UPGRADE_RESPONSE_") for k in env))

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires local macOS isolation")
    def test_generated_private_validator_accepts_opt_in_in_both_schemas(self):
        for name in ("observed", "goal"):
            fixture = self.fixtures[name]
            spec = fixture["spec"]
            code = ("import sys; sys.path.insert(0," + repr(str(Path(spec["controller"]) / "scripts")) + "); "
                    "from mac_upgrade_startup_contract import validate_startup; "
                    "from mac_upgrade_live_entry import layout_for; "
                    "spec,binding=validate_startup(" + repr(fixture["ref"]) + "); "
                    "assert binding['response_observation']=='consumed-response-v1'; "
                    "assert len(layout_for(" + repr(fixture["ref"]) + ",spec)['children'])==2; "
                    "print('RESPONSE_CONTRACT_VALID')")
            result = subprocess.run([str(SANDBOX), "-f", spec["pins"]["control_sandbox"]["path"],
                                     sys.executable, "-I", "-B", "-c", code],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RESPONSE_CONTRACT_VALID", result.stdout)


if __name__ == "__main__":
    unittest.main()
