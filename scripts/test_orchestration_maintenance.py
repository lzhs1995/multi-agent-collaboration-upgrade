"""Returned preparation, immutable lease and terminal crash-window integration.

Only the external harness is a fixture. Original operation files, native fences,
adapter closure, lifecycle validation and lease release use their real code.
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from orchestration_adapter import CAMPAIGN_MODULES
from orchestration_common import ContractError, file_ref, json_ref, parse_time, tree_manifest, utc_now, write_json
from orchestration_coordinator import Coordinator, LeaseRegistry, State
from orchestration_evidence import failure_marker
from orchestration_maintenance import Maintenance, freeze_maintenance, validate_maintenance
from orchestration_test_support import CampaignFixture
from orchestration_transport import Transport
from test_orchestration_transport import Crash, FakeRuntime


def ownership_world(root):
    scripts = root / "adapter/scripts"
    scripts.mkdir(parents=True)
    for name in CAMPAIGN_MODULES:
        filename = name + ".py"
        (scripts / filename).write_bytes((Path(__file__).parent / filename).read_bytes())
    controller = root / "controller/scripts"
    controller.mkdir(parents=True)
    for name in ("cmux_bridge", "cmux_native_delivery", "cmux_workspace_guard", "cmux_daemon_identity"):
        (controller / (name + ".py")).write_text(
            "import cmux_submission_inputs\n" if name == "cmux_bridge" else "# private controller\n")
    (controller / "cmux_submission_inputs.py").write_text("OWNER = 'controller'\n")
    for path in (root / "foreign.py", scripts / "other.py", controller / "other.py"):
        path.write_text("OWNER = 'different file'\n")


class MaintenanceOwnershipTests(unittest.TestCase):
    """Real production ownership checks with a separately pinned toy controller.

    Campaign/native/lease setup is isolated here; validate_adapter, source reads,
    controller_modules and the fixtures=False branch of _validate are unchanged.
    Each case has a fresh interpreter, so loaded modules cannot leak across tests.
    """
    PROBE = r'''
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

root, case = Path(sys.argv[1]), sys.argv[2]
adapter, controller = root / "adapter", root / "controller"
sys.path.insert(0, str(adapter / "scripts"))
assert "orchestration_inputs" not in sys.modules
import orchestration_maintenance as maintenance
orchestration_inputs = sys.modules["orchestration_inputs"]
from orchestration_campaign import controller_modules
from orchestration_common import ContractError, file_ref, tree_manifest

m = maintenance.Maintenance.__new__(maintenance.Maintenance)
m.fixtures, m.key, m.ref, m.node = False, "review", {}, {}
m.manifest = dict(adapter=tree_manifest(adapter), python=file_ref(Path(sys.executable).resolve()), preparation_exit={})
m.campaign = dict(controller=tree_manifest(controller))
m.co = SimpleNamespace(state=lambda: ({}, {"review": {}}), header={})

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

with patch.object(maintenance, "validate_maintenance"):
    assert "cmux_submission_inputs" not in sys.modules
    assert "orchestration_literal_inputs" not in sys.modules
    m._validate()
    modules = controller_modules(m.campaign)
    assert len(modules) == 4
    plain = sys.modules["cmux_submission_inputs"]
    assert Path(plain.__file__) == controller / "scripts/cmux_submission_inputs.py"
    assert Path(orchestration_inputs._literal.__file__) == adapter / "scripts/cmux_submission_inputs.py"
    m._validate()
    if case == "private_to_controller":
        orchestration_inputs._literal = plain
    elif case == "private_missing":
        orchestration_inputs._literal = None
    elif case == "plain_to_adapter":
        sys.modules["cmux_submission_inputs"] = orchestration_inputs._literal
    elif case in {"plain_to_foreign", "plain_to_other_controller_file"}:
        path = root / "foreign.py" if case == "plain_to_foreign" else controller / "scripts/other.py"
        sys.modules["cmux_submission_inputs"] = load("cmux_submission_inputs", path)
    elif case in {"decision_to_other_adapter_file", "orchestration_to_other_adapter_file"}:
        name = "decision_provider" if case.startswith("decision") else "orchestration_workload"
        sys.modules[name] = load(name, adapter / "scripts/other.py")
    elif case in {"changed_adapter", "changed_controller"}:
        path = (adapter if case == "changed_adapter" else controller) / "scripts/cmux_submission_inputs.py"
        path.write_bytes(path.read_bytes() + b"\n# changed after pin\n")
    elif case != "valid":
        raise AssertionError(case)
    try:
        m._validate()
        result = dict(status="PASS")
    except ContractError as exc:
        result = dict(status="REJECTED", error=str(exc))
result.update(before_controller="PASS", after_controller="PASS", case=case)
print(json.dumps(result))
'''

    def probe(self, case):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ownership_world(root)
            process = subprocess.run([sys.executable, "-B", "-c", self.PROBE, str(root), case],
                                     cwd=root, text=True, capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["before_controller"], "PASS")
            self.assertEqual(result["after_controller"], "PASS")
            return result

    def test_real_controller_loading_keeps_its_plain_parser_and_private_adapter_parser_valid(self):
        self.assertEqual(self.probe("valid")["status"], "PASS")

    def test_private_parser_and_plain_controller_parser_cannot_exchange_owners(self):
        for case in ("private_to_controller", "private_missing", "plain_to_adapter", "plain_to_foreign"):
            with self.subTest(case=case):
                result = self.probe(case)
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn("exact frozen source", result["error"])

    def test_another_registered_file_under_the_same_root_is_not_module_provenance(self):
        for case in ("plain_to_other_controller_file", "decision_to_other_adapter_file",
                     "orchestration_to_other_adapter_file"):
            with self.subTest(case=case):
                result = self.probe(case)
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn("exact frozen source", result["error"])

    def test_pinned_adapter_or_controller_bytes_cannot_change_between_validations(self):
        for case in ("changed_adapter", "changed_controller"):
            with self.subTest(case=case):
                result = self.probe(case)
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn("tree changed", result["error"])


class MaintenanceEntrypointTests(unittest.TestCase):
    """Fresh main() calls using disk manifests and the production ownership gate.

    Campaign/native validation, state replay and candidate construction are
    isolated. Source validation, main/constructor/action dispatch, node locks,
    controller loading, lease lookup/release and idempotence use real code.
    These private fixtures do not establish live settlement authority.
    """
    PROBE = r'''
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

root, action, case = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
cfg = json.loads((root / "world.json").read_text())
sys.path.insert(0, str(root / "adapter/scripts"))
if case == "foreign":
    spec = importlib.util.spec_from_file_location("decision_provider", root / "foreign.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
assert "orchestration_inputs" not in sys.modules
import orchestration_maintenance as maintenance
import orchestration_coordinator as coordinator
from orchestration_campaign import controller_modules
from orchestration_common import file_ref, json_ref

campaign = json_ref(cfg["campaign"])
run = Path(campaign["root"]) / "runs/R1"
co = coordinator.Coordinator.__new__(coordinator.Coordinator)
co.root, co.campaign_ref, co.campaign, co.fixtures = run, cfg["campaign"], campaign, False
co.nodes, co.spec, co.header = {"review": dict(id="review", executor="claude")}, dict(run_id="R1"), {}
co.leases = coordinator.LeaseRegistry(campaign["lease_root"])
state = coordinator.State.BLOCKED if action == "candidate" else coordinator.State.ACCEPTED
co.state = lambda: ({"review": state}, {"review": cfg["info"]})
co.events = lambda: [json_ref(cfg["terminal"])]
seen = []

def candidate(actual, key, proof, manifest):
    assert actual is co and key == "review" and state == coordinator.State.BLOCKED
    assert manifest == cfg["manifest"] and not co.fixtures
    seen.append("candidate")
    return file_ref(run / "nodes/review/failure-candidate.json")

controller_modules(campaign)
assert Path(sys.modules["cmux_submission_inputs"].__file__) == root / "controller/scripts/cmux_submission_inputs.py"
out, err = io.StringIO(), io.StringIO()
with patch.object(maintenance, "validate_campaign", side_effect=lambda ref, **kw: json_ref(ref)), \
     patch.object(coordinator, "validate_campaign", side_effect=lambda ref, **kw: json_ref(ref)), \
     patch.object(maintenance, "Coordinator", return_value=co), \
     patch.object(maintenance, "node_directory", return_value=run / "nodes/review"), \
     patch.object(maintenance, "Transport", return_value=SimpleNamespace()), \
     patch.object(maintenance, "validate_maintenance"), \
     patch.object(coordinator, "require_live_identity"), \
     patch.object(maintenance, "freeze_failure_candidate_locked", side_effect=candidate), \
     contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    rc = maintenance.main([action, "--manifest", cfg["manifest"]["path"],
                           "--manifest-sha256", cfg["manifest"]["sha256"]])
print(json.dumps(dict(rc=rc, output=out.getvalue(), error=err.getvalue(), reached=seen)))
'''

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        ownership_world(self.root)
        # A real dataclass module must be registered before exec_module in the
        # child, so negative controls reach the ownership gate itself.
        (self.root / "foreign.py").write_bytes((Path(__file__).parent / "decision_provider.py").read_bytes())
        self.run = self.root / "campaign/runs/R1"
        claim = write_json(self.run / "claims/review.json", dict(task_id="task-review"))
        self.terminal = write_json(self.run / "events/000001.json", dict(
            state="ACCEPTED", node_id="review", seq=1, data=dict(verification=dict(task_id="task-review"))))
        output = self.root / "output"
        output.mkdir()
        self.leases = LeaseRegistry(self.root / "leases")
        self.leases.reserve("task-review", "session-claude", str(output), claim)
        lease = self.leases.read()["task-review"]
        campaign = write_json(self.root / "campaign.json", dict(
            controller=tree_manifest(self.root / "controller"), root=str(self.root / "campaign"),
            sessions=dict(claude=dict(session_id="session-claude")), lease_root=str(self.leases.root)))
        manifest = write_json(self.run / "nodes/review/maintenance/manifest.json", dict(
            campaign=campaign, run_id="R1", node_id="review", adapter=tree_manifest(self.root / "adapter"),
            python=file_ref(Path(sys.executable).resolve()), preparation_exit={}, claim=claim, lease=lease))
        write_json(self.run / "nodes/review/failure-candidate.json", dict(maintenance=manifest))
        self.cfg = dict(campaign=campaign, manifest=manifest, terminal=self.terminal, info=dict(
            task_id="task-review", output_root=str(output), acceptance_nonce="private-candidate",
            started_at="2026-01-01T00:00:00Z"))
        write_json(self.root / "world.json", self.cfg)

    def main(self, action, case="valid"):
        proc = subprocess.run([sys.executable, "-B", "-c", self.PROBE, str(self.root), action, case],
                              cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def lease_pin(self):
        path = self.leases.root / "leases.json"
        return file_ref(path), path.stat().st_mtime_ns

    def test_blocked_candidate_main_loads_its_own_parser_without_preimport(self):
        before = self.lease_pin()
        result = self.main("candidate")
        self.assertEqual(result["rc"], 0, result)
        self.assertEqual(result["reached"], ["candidate"])
        value = json.loads(result["output"])
        self.assertEqual(json_ref(value["candidate"])["maintenance"], self.cfg["manifest"])
        self.assertIn(self.cfg["manifest"]["sha256"], value["exec_script"])
        self.assertEqual(self.lease_pin(), before)

    def test_repair_main_releases_original_lease_and_fresh_retry_does_not_rewrite(self):
        result = self.main("repair-releases")
        self.assertEqual(result["rc"], 0, result)
        self.assertEqual(json.loads(result["output"]), {"repaired": ["review"]})
        self.assertEqual(self.leases.read()["task-review"]["released_by"], self.terminal)
        before = self.lease_pin()
        result = self.main("repair-releases")
        self.assertEqual(result["rc"], 0, result)
        self.assertEqual(json.loads(result["output"]), {"repaired": []})
        self.assertEqual(self.lease_pin(), before)

    def test_foreign_module_reaches_main_ownership_rejection_without_release(self):
        before = self.lease_pin()
        result = self.main("repair-releases", "foreign")
        self.assertEqual(result["rc"], 2, result)
        self.assertIn("exact frozen source: decision_provider", result["error"])
        self.assertEqual(self.lease_pin(), before)

    def test_main_rejects_changed_reservation_before_release(self):
        value = self.leases.read()
        value["task-review"]["reserved_at"] = (
            parse_time(value["task-review"]["reserved_at"]) + timedelta(microseconds=1)).isoformat()
        write_json(self.leases.root / "leases.json", value)
        before = self.lease_pin()
        result = self.main("repair-releases")
        self.assertEqual(result["rc"], 2, result)
        self.assertIn("reservation identity changed", result["error"])
        self.assertEqual(self.lease_pin(), before)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Freeze complete real sources without inheriting the checkout's caches.
        # Product closure validation and all copied bytes remain unchanged.
        scripts = Path(self.temp.name).resolve() / "maintenance-source/scripts"
        scripts.mkdir(parents=True)
        for name in CAMPAIGN_MODULES:
            filename = name + ".py"
            (scripts / filename).write_bytes((Path(__file__).parent / filename).read_bytes())
        source = patch("orchestration_maintenance.__file__", str(scripts / "orchestration_maintenance.py"))
        source.start()
        self.addCleanup(source.stop)
        self.f = CampaignFixture(self.temp.name)
        self.co = self.f.start()
        self.info = self.co.claim("review")
        self.runtime = FakeRuntime(self.f, self.co)
        self.transport = Transport(self.co, "review", runtime=self.runtime)
        self.hs = patch("orchestration_transport.handshake_journal", return_value=self.runtime.handshake_root)
        self.hs.start()
        self.addCleanup(self.hs.stop)
        self.ds = patch("orchestration_transport.dispatch_journal", return_value=self.runtime.dispatch_root)
        self.ds.start()
        self.addCleanup(self.ds.stop)
        transition = self.co.transition

        def failed_final_gate(key, state, data):
            if state == State.PREPARED:
                raise ContractError("fixture final preparation gate rejected after return")
            return transition(key, state, data)

        with patch.object(self.co, "transition", side_effect=failed_final_gate):
            with self.assertRaisesRegex(ContractError, "after return"):
                self.transport.prepare()
        blocker = write_json(self.transport.root / "blocker.json", dict(
            task_id=self.info["task_id"], reason="fixture final gate rejected after all five stages returned",
            observed_at=utc_now()), exclusive=True)
        self.co.transition("review", State.BLOCKED, {"blocker": blocker})
        self.info = self.co.state()[1]["review"]
        self.original_lease = copy.deepcopy(self.co.leases.read()[self.info["task_id"]])
        self.manifest = freeze_maintenance(self.f.ref, self.co.spec["run_id"], "review",
                                           fixtures=True, runtime=self.runtime)
        self.maintenance = Maintenance(self.manifest, fixtures=True, runtime=self.runtime)
        self.calls_before = list(self.runtime.calls)

    def registry(self, value):
        write_json(self.co.leases.root / "leases.json", value)

    def native_control(self, script, call_id="maintenance-settlement"):
        client = self.f.clients["codex"]
        return client.append(dict(type="response_item", timestamp=utc_now(), payload=dict(
            type="custom_tool_call", name="exec", call_id=call_id, input=script)))

    def candidate(self):
        candidate = self.maintenance.candidate()
        self.native_control(candidate["exec_script"])
        return failure_marker(self.info, json_ref(candidate["candidate"]))

    def validate_leaf(self, ref=None):
        ref = ref or self.manifest
        value = json_ref(ref)
        return validate_maintenance(ref, self.f.campaign, self.co.header, self.co.nodes["review"],
            self.info, value["preparation_exit"], fixtures=True)

    def test_freeze_retains_original_lease_and_is_idempotent_without_harness(self):
        self.assertEqual(json_ref(self.manifest)["lease"], self.original_lease)
        self.assertEqual(freeze_maintenance(self.f.ref, self.co.spec["run_id"], "review",
            fixtures=True, runtime=self.runtime), self.manifest)
        self.assertEqual(self.runtime.calls, self.calls_before)
        self.assertEqual(self.co.state()[0]["review"], State.BLOCKED)
        self.assertIsNone(self.co.leases.read()[self.info["task_id"]]["released_at"])

    def test_manifest_validation_is_leaf_only_and_does_not_replay_state(self):
        with patch.object(Coordinator, "state", side_effect=AssertionError("recursive state replay")):
            self.validate_leaf()

    def test_changed_reservation_identity_or_missing_lease_cannot_validate_or_release(self):
        original = self.co.leases.read()
        other_claim = write_json(Path(self.temp.name).resolve() / "other-claim.json", {"task_id": "different"})
        for field, value in (("reserved_at", (parse_time(self.original_lease["reserved_at"]) +
                    timedelta(microseconds=1)).isoformat()),
                ("session_id", "another-session"), ("scope", str(Path(self.temp.name).resolve() / "elsewhere")),
                ("claim", other_claim), ("missing", None)):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                if field == "missing":
                    del changed[self.info["task_id"]]
                else:
                    changed[self.info["task_id"]][field] = value
                self.registry(changed)
                with self.assertRaises(ContractError):
                    self.validate_leaf()
                with self.assertRaises(ContractError):
                    self.co.state()
                self.assertEqual(self.co.leases.read(), changed)
                self.registry(original)

    def test_terminal_settlement_replay_and_disarm_preserve_lease_identity(self):
        marker = self.candidate()
        result = self.maintenance.settle(marker)
        lease = self.co.leases.read()[self.info["task_id"]]
        self.assertEqual(result["status"], "FAILED_PRESERVED")
        self.assertIsNotNone(lease["released_at"])
        for field in ("session_id", "scope", "claim", "reserved_at"):
            self.assertEqual(lease[field], self.original_lease[field])
        self.assertEqual(self.maintenance.settle(resume=True), result)
        self.assertEqual(self.co.repair_releases(), [])
        self.assertEqual(self.co.leases.read()[self.info["task_id"]], lease)
        self.assertEqual(len(list((self.transport.root / "release-checks").glob("*.json"))), 1)
        self.assertEqual(self.co.state()[0]["review"], State.FAILED_PRESERVED)
        self.assertTrue(self.maintenance.verify()["lease_released"])
        self.assertEqual(self.runtime.calls, self.calls_before)
        receipt = self.maintenance.transport.disarm()
        self.assertEqual(self.maintenance.transport.disarm(), receipt)
        self.assertEqual(self.runtime.disarms, [self.info["task_id"]])

    def test_terminal_crash_before_release_is_repaired_without_new_control(self):
        marker = self.candidate()
        with patch.object(self.maintenance.co, "_release", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.maintenance.settle(marker)
        self.assertEqual(self.co.state()[0]["review"], State.FAILED_PRESERVED)
        self.assertIsNone(self.co.leases.read()[self.info["task_id"]]["released_at"])
        resumed = Maintenance(self.manifest, fixtures=True, runtime=self.runtime)
        self.assertEqual(resumed.co.repair_releases(), ["review"])
        self.assertEqual(resumed.co.repair_releases(), [])
        self.assertTrue(resumed.verify()["lease_released"])
        self.assertEqual(len(list((self.transport.root / "release-checks").glob("*.json"))), 2)
        self.assertEqual(self.runtime.calls, self.calls_before)

    def test_locked_release_detects_reservation_change_after_leaf_read(self):
        marker = self.candidate()
        release = self.maintenance.co.leases.release

        def raced_release(owner, **kwargs):
            value = self.co.leases.read()
            value[owner]["reserved_at"] = (parse_time(value[owner]["reserved_at"]) +
                                            timedelta(microseconds=1)).isoformat()
            self.registry(value)
            return release(owner, **kwargs)

        with patch.object(self.maintenance.co.leases, "release", side_effect=raced_release):
            with self.assertRaisesRegex(ContractError, "before release"):
                self.maintenance.settle(marker)
        self.assertIsNone(self.co.leases.read()[self.info["task_id"]]["released_at"])
        with self.assertRaisesRegex(ContractError, "reservation identity changed"):
            self.co.repair_releases()

    def test_released_snapshot_and_premature_or_wrong_terminal_release_rejected(self):
        manifest = json_ref(self.manifest)
        changed = copy.deepcopy(manifest)
        changed["lease"].update(released_at=utc_now(), released_by=self.info["blocker"])
        ref = write_json(self.manifest["path"], changed)
        with self.assertRaisesRegex(ContractError, "unreleased lease snapshot"):
            self.validate_leaf(ref)
        write_json(self.manifest["path"], manifest)
        original = self.co.leases.read()
        changed = copy.deepcopy(original)
        changed[self.info["task_id"]].update(released_at=utc_now(), released_by=self.info["blocker"])
        self.registry(changed)
        with self.assertRaisesRegex(ContractError, "prematurely released"):
            self.co.state()
        self.registry(original)
        self.maintenance.settle(self.candidate())
        changed = self.co.leases.read()
        changed[self.info["task_id"]]["released_by"] = self.info["blocker"]
        self.registry(changed)
        with self.assertRaisesRegex(ContractError, "different event"):
            self.co.state()

    def test_post_terminal_business_action_prevents_release_repair(self):
        marker = self.candidate()
        with patch.object(self.maintenance.co, "_release", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.maintenance.settle(marker)
        self.f.work(self.info, "claude")
        with self.assertRaises(ContractError):
            self.co.repair_releases()
        self.assertIsNone(self.co.leases.read()[self.info["task_id"]]["released_at"])
        self.assertEqual(self.runtime.calls, self.calls_before)


if __name__ == "__main__":
    unittest.main()
