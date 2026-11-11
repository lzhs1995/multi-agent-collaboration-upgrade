"""Fresh-process adapter/controller boundaries; no clients or transport input."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from orchestration_common import file_ref, tree_manifest, write_json
from upgrade_controller_patch import add_bootstrap
from upgrade_experiment import arm_paths, init_experiment, validate_context
import test_upgrade_communication_plan as plan_tests


class CommunicationImportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = plan_tests.CommunicationPlanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.scripts = Path(__file__).resolve().parent
        source = f.root / "original"
        (source / "scripts").mkdir(parents=True)
        (source / "SKILL.md").write_text("offline import boundary fixture\n")
        (source / "scripts/mac_harness.py").write_text("# never invoked\n")
        context_ref = init_experiment(f.root / "lab", "communication-imports", source)
        context = validate_context(context_ref)
        paths = arm_paths(context, "B")
        for path in paths.values():
            path.mkdir()
        controller = paths["controllers"] / "releases/private/source"
        shutil.copytree(f.controller, controller)
        scripts = controller / "scripts"
        # Exercise the actual private-controller scope, including its module
        # provenance refusal. These four controller entrypoints perform no I/O.
        shutil.copyfile(self.scripts / "upgrade_controller_scope.py",
                        scripts / "upgrade_controller_scope.py")
        for name in ("cmux_bridge", "cmux_native_delivery", "cmux_workspace_guard", "cmux_daemon_identity"):
            (scripts / (name + ".py")).write_text(add_bootstrap("OFFLINE_IMPORT_FIXTURE = True\n"))
        (scripts / "render_cmux_agent.py").write_text(
            add_bootstrap((self.scripts / "render_cmux_agent.py").read_text()))
        write_json(controller / "upgrade-binding.json", dict(
            context=context_ref, controller_root=str(controller), execution_mode="offline",
            **{name + "_root": str(paths[key]) for name, key in (
                ("state", "state"), ("campaign", "campaigns"), ("evidence", "evidence"), ("tmp", "tmp"))}))
        for path in controller.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        self.controller_pin = tree_manifest(controller)
        write_json(controller.parent / "MANIFEST.json", dict(files=self.controller_pin["files"]))
        f.helper.write_bytes(plan_tests.render(f.original, expected_sha256=plan_tests.BASELINE_SHA256,
            python=f.python["path"], adapter=controller / "scripts/cmux_agent_adapter.py"))
        f.startup["controller"] = str(controller)
        f.runtime["controller"] = self.controller_pin
        f.repin_helper()
        self.campaign = dict(f.campaign, controller=self.controller_pin, runtime=file_ref(f.runtime_path))

    def child(self, order, run_id="warm-01"):
        campaign = dict(self.campaign)
        if run_id != "warm-01":
            campaign["runs"] = [{"run_id": run_id}]
        request = write_json(self.fixture.root / "child-campaign.json", campaign)
        code = r'''
import importlib, json, sys
from pathlib import Path
adapter, campaign_path, order, run_id = sys.argv[1:]
sys.path.insert(0, adapter)
campaign = json.loads(Path(campaign_path).read_bytes())
from orchestration_campaign import controller_modules

def load_controller():
    modules = controller_modules(campaign)
    assert all(m.OFFLINE_IMPORT_FIXTURE for m in modules)
    from orchestration_transport import PinnedRuntime
    with PinnedRuntime(campaign).modules("render_cmux_agent") as values:
        renderer = values[0]
    assert Path(renderer.__file__).parent == Path(campaign["controller"]["path"]) / "scripts"
    return renderer

if order == "foreign_first":
    import render_cmux_agent
    before = sys.modules["render_cmux_agent"]
    try:
        load_controller()
    except RuntimeError as exc:
        assert str(exc) == "UPGRADE_MIXED_CONTROLLER_IMPORT:render_cmux_agent", str(exc)
        assert sys.modules["render_cmux_agent"] is before
        print("FOREIGN_IMPORT_REFUSED")
    else:
        raise AssertionError("foreign controller module was silently replaced or accepted")
else:
    original = load_controller() if order == "controller_first" else None
    # Import the adapter after the controller too: an existing public renderer
    # must neither redirect the adapter's pure verifier nor be replaced.
    sys.path.insert(0, adapter)
    from orchestration_evidence import task_scope
    import upgrade_communication_plan as communication
    info = dict(work_request={"path": "/request.json", "sha256": "b" * 64}, output_root="/output")
    node = dict(id="inventory", executor="claude_a", scope="Read corpus.", deliverables=["report.json"])
    scope = task_scope(campaign, {"run": {"run_id": run_id}}, node, info)
    samples = communication.campaign_plan(campaign)
    assert len(samples) == (32 if run_id == "warm-01" else 0)
    assert scope.count(" --request-id ") == (2 if run_id == "warm-01" else 0)
    if original is None:
        had_public_renderer = "render_cmux_agent" in sys.modules
        original = load_controller()
        assert not had_public_renderer
    assert Path(communication._renderer.__file__).parent == Path(adapter)
    assert sys.modules["render_cmux_agent"] is original
    assert communication._renderer is not original
    print("PRIVATE_IMPORT_OK", run_id, order, len(samples))
'''
        return subprocess.run([sys.executable, "-I", "-B", "-c", code, str(self.scripts),
            request["path"], order, run_id], text=True, capture_output=True, timeout=20)

    def test_pilot_and_cold_scope_then_controller_preserve_private_imports(self):
        for run_id in ("pilot-01", "cold-01"):
            with self.subTest(run=run_id):
                result = self.child("communication_first", run_id)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PRIVATE_IMPORT_OK", result.stdout)

    def test_warm_communication_then_controller_preserves_private_imports(self):
        result = self.child("communication_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PRIVATE_IMPORT_OK warm-01 communication_first 32", result.stdout)

    def test_controller_then_warm_communication_keeps_both_module_identities(self):
        result = self.child("controller_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PRIVATE_IMPORT_OK warm-01 controller_first 32", result.stdout)

    def test_actual_foreign_public_import_still_fails_without_replacement(self):
        result = self.child("foreign_first")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FOREIGN_IMPORT_REFUSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
