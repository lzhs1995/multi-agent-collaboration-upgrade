"""Goal startup contracts; no clients, model requests, or live messages."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from orchestration_common import ContractError, file_ref, json_ref
from upgrade_client_isolation import LANES, SANDBOX
from upgrade_experiment import arm_paths, init_experiment, validate_context
from upgrade_live_integrity import artifact_ref
from upgrade_live_materialize import materialize_live
from upgrade_startup_contract import (GOAL_SCHEMA, bootstrap_continuation, native_goal_condition,
    pressure_lane_document, require_goal_condition, validate_startup)

SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
WRAPPERS = Path("/Applications/cmux.app/Contents/Resources/bin")


class GoalConditionTests(unittest.TestCase):
    def test_rejects_clear_aliases_empty_controls_and_invalid_unicode(self):
        for value in ("", " ", "clear", "STOP", "off", "reset", "none", "cancel",
                      " goal", "goal ", "goal\nnext", "goal\tmore", "goal\x00", "goal\x7f", "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises((ContractError, ValueError)):
                require_goal_condition(value)

    def test_limit_counts_native_utf16_units_including_document_prefix(self):
        self.assertEqual(require_goal_condition("a" * 4000), "a" * 4000)
        self.assertEqual(require_goal_condition("😀" * 2000), "😀" * 2000)
        for value in ("a" * 4001, "😀" * 2001):
            with self.assertRaisesRegex(ContractError, "TOO_LONG"):
                require_goal_condition(value)
        with self.assertRaisesRegex(ContractError, "TOO_LONG"):
            native_goal_condition("a" * 3990, Path("/tmp/instructions.md"))

    def test_documents_keep_probe_and_gate_without_old_bootstrap(self):
        for lane in LANES:
            document = pressure_lane_document(Path("/tmp/controller"), "/usr/bin/python3",
                                               "/usr/bin/rtk", lane, "Wait for the pinned gate.")
            self.assertIn("mac_upgrade_client_probe.py", document)
            self.assertIn("Formal business and communication must wait", document)
            self.assertIn("Wait for the pinned gate.", document)
            self.assertNotIn("mac_upgrade_bootstrap.py", document)
            self.assertEqual("tools.exec_command" in document, lane == "codex")

    def test_registered_bootstrap_is_preserved_without_conflicting_prohibition(self):
        responsibilities = bootstrap_continuation(Path("/pinned/controller"), "/usr/bin/python3", "/usr/bin/rtk")
        document = pressure_lane_document(Path("/pinned/controller"), "/usr/bin/python3",
                                           "/usr/bin/rtk", "codex", responsibilities)
        self.assertIn(responsibilities, document)
        self.assertIn("bootstrap only when explicitly registered", document)
        self.assertNotIn("or invoke the legacy", document)
        self.assertIn("mac_upgrade_bootstrap.py", document)
        self.assertIn("BOOTSTRAP_READY", document)


@unittest.skipUnless(SOURCE.is_dir() and WRAPPERS.is_dir(), "requires fixed local MAC and cmux wrappers")
class GoalMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        cls.root = Path(cls.temp.name).resolve()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.context = init_experiment(cls.root / "l", "goal-startup-contract", SOURCE)
        cls.binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
        cls.binaries.update({p + "_wrapper": WRAPPERS / ("cmux-" + p + "-wrapper")
                             for p in ("codex", "claude")})
        cls.instructions = {}
        for lane in LANES:
            p = cls.root / (lane + ".md")
            p.write_text("Wait for external adoption; then complete registered pressure for " + lane + ".\n")
            cls.instructions[lane] = file_ref(p)
        cls.goals = {lane: "Complete all three registered windows for " + lane
                     for lane in ("claude_a", "claude_b")}
        runtime = materialize_live(cls.context, "B", binaries=cls.binaries,
            provider_configuration=({}, {}), goal_startup=cls.goals, lane_instructions=cls.instructions)
        cls.ref = json_ref(runtime)["startup"]
        cls.spec, cls.binding = validate_startup(cls.ref)

    @contextmanager
    def changed_spec(self):
        path = Path(self.ref["path"])
        original = path.read_bytes()
        spec = copy.deepcopy(self.spec)
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

    def test_exact_goal_args_and_independent_lane_pins(self):
        self.assertEqual(self.spec["schema"], GOAL_SCHEMA)
        for lane in LANES:
            ref = self.spec["lane_instructions"][lane]
            self.assertEqual(ref, self.spec["pins"][lane + "_instructions"])
            self.assertEqual(artifact_ref(ref["path"]), ref)
            self.assertIn(lane, Path(ref["path"]).read_text())
            argv = self.spec["clients"][lane]["argv"]
            self.assertNotIn("mac_upgrade_bootstrap.py", argv[-1])
            if lane == "codex":
                self.assertFalse(argv[-1].startswith("/goal"))
                self.assertNotIn(lane, self.spec["goal_startup"])
            else:
                self.assertEqual(argv[-1], "/goal " + self.spec["goal_startup"][lane])
                self.assertIn(ref["path"], argv[-1])
                self.assertNotIn("-p", argv)
                self.assertIn("--session-id", argv)
        self.assertNotEqual(self.spec["goal_startup"]["claude_a"], self.spec["goal_startup"]["claude_b"])

    def test_materialized_private_validator_accepts_contract(self):
        controller = str(Path(self.spec["controller"]) / "scripts")
        code = ("import sys; sys.path.insert(0, " + repr(controller) + "); "
                "from mac_upgrade_startup_contract import validate_startup; "
                "import mac_upgrade_live_inspect, mac_upgrade_bootstrap, mac_upgrade_live_entry; "
                "spec,binding=validate_startup(" + repr(self.ref) + "); "
                "assert spec['schema']=='upgrade-startup-v3'; "
                "assert mac_upgrade_live_inspect.GOAL_SCHEMA==spec['schema']; "
                "assert mac_upgrade_bootstrap.read_instruction(binding) is None; "
                "assert len(mac_upgrade_live_entry.layout_for(" + repr(self.ref) + ",spec)['children'])==2; "
                "print('GOAL_CONTRACT_VALID')")
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GOAL_CONTRACT_VALID", result.stdout)

    def test_changed_goal_even_with_matching_argv_is_rejected(self):
        with self.changed_spec() as (spec, validate):
            spec["goal_startup"]["claude_a"] += " altered"
            spec["clients"]["claude_a"]["argv"][-1] = "/goal " + spec["goal_startup"]["claude_a"]
            with self.assertRaisesRegex(ContractError, "GOAL_BINDING_CHANGED"):
                validate()

    def test_cross_lane_instruction_swap_is_rejected(self):
        with self.changed_spec() as (spec, validate):
            spec["lane_instructions"]["claude_a"] = spec["lane_instructions"]["claude_b"]
            with self.assertRaisesRegex(ContractError, "GOAL_BINDING_CHANGED"):
                validate()

    def test_changed_argv_and_headless_switch_are_rejected(self):
        for transform in (lambda x: x.__setitem__(-1, "ordinary prompt"), lambda x: x.insert(1, "-p")):
            with self.changed_spec() as (spec, validate):
                transform(spec["clients"]["claude_a"]["argv"])
                with self.assertRaisesRegex(ContractError, "ARGUMENTS_OR_ENV_CHANGED"):
                    validate()

    def test_downgrade_to_old_schema_is_rejected(self):
        with self.changed_spec() as (spec, validate):
            spec["schema"] = "upgrade-startup-v2"
            del spec["goal_startup"], spec["lane_instructions"]
            with self.assertRaisesRegex(ContractError, "GOAL_DOWNGRADE_REFUSED"):
                validate()

    def test_changed_document_bytes_are_rejected_without_repin(self):
        path = Path(self.spec["lane_instructions"]["claude_a"]["path"])
        original = path.read_bytes()
        try:
            path.chmod(0o600)
            path.write_bytes(original + b"changed")
            path.chmod(0o444)
            with self.assertRaisesRegex(ValueError, "ARTIFACT_PIN_CHANGED"):
                validate_startup(self.ref)
        finally:
            path.chmod(0o600)
            path.write_bytes(original)
            path.chmod(0o444)

    def test_repin_changed_document_does_not_change_binding(self):
        with self.changed_spec() as (spec, validate):
            changed = dict(spec["lane_instructions"]["claude_a"], sha256="0" * 64)
            spec["lane_instructions"]["claude_a"] = changed
            spec["pins"]["claude_a_instructions"] = changed
            with self.assertRaisesRegex(ContractError, "GOAL_BINDING_CHANGED"):
                validate()

    def test_invalid_requests_refuse_before_arm_creation(self):
        cases = [dict(goal_startup=self.goals), dict(lane_instructions=self.instructions),
                 dict(goal_startup={"claude_a": "done"}, lane_instructions=self.instructions),
                 dict(goal_startup=self.goals, lane_instructions={"codex": self.instructions["codex"]}),
                 dict(goal_startup={"claude_a": "clear", "claude_b": "done"},
                      lane_instructions=self.instructions)]
        paths = arm_paths(validate_context(self.context), "A1")
        for request in cases:
            with self.subTest(request=list(request)), self.assertRaises(ContractError):
                materialize_live(self.context, "A1", binaries=self.binaries,
                                 provider_configuration=({}, {}), **request)
            self.assertFalse(any(path.exists() for path in paths.values()))


if __name__ == "__main__":
    unittest.main()
