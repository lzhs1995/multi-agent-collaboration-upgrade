"""Private freeze and live-schema controls; all client journals are offline fixtures."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_adapter import CAMPAIGN_MODULES
from orchestration_campaign import ORDER, anchored_ref, validate_campaign
from orchestration_common import ContractError, canonical, digest, file_ref, read_json, tree_manifest, verify_tree, write_json
from orchestration_freeze import capture_sessions, corpus_plan, freeze_campaign
from orchestration_planner import ID_RE
from orchestration_native import NativeReader
from test_orchestration_native import NativeFixture
from test_orchestration_workload import CorpusFixture


class FreezeFixture:
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.source = self.base / "source"
        (self.source / "scripts").mkdir(parents=True)
        for name in (name + ".py" for name in CAMPAIGN_MODULES):
            (self.source / "scripts" / name).write_text("# offline frozen entrypoint\n")
        (self.source / ".hidden").write_bytes(b"hidden runtime bytes\n")
        (self.source / "empty").mkdir()
        (self.source / ".git").mkdir()
        (self.source / ".git" / "HEAD").write_text("fixture metadata is not runtime\n")
        os.chmod(self.source / "scripts" / "orchestration_run.py", 0o755)
        self.controller = self.base / "controller"
        self.controller.mkdir()
        (self.controller / "SKILL.md").write_text("offline controller fixture\n")
        # Controller trees retain all original bytes. Only the new adapter is
        # source-only; preserving historical controller bytecode is separate.
        (self.controller / "__pycache__").mkdir()
        (self.controller / "__pycache__" / "existing.pyc").write_bytes(b"original controller bytecode")
        self.corpus = CorpusFixture(self.base / "data")
        self.clients, self.hints = {}, {}
        for lane in ("codex", "claude"):
            path = self.base / lane
            path.mkdir()
            client = NativeFixture(path, lane)
            client.session.update(surface_uuid=lane + "-surface", pane_uuid=lane + "-pane")
            if lane == "codex":
                client.append(dict(type="turn_context", payload={"model": "test-model"}))
            else:
                client.append(client.message("assistant", "ready"))
            self.clients[lane] = client
            self.hints[lane] = {k: client.session[k] for k in
                ("provider", "session_id", "workspace_uuid", "pane_uuid", "surface_uuid", "model")}
            self.hints[lane]["surface_ref"] = "surface:1" if lane == "codex" else "surface:2"
        auth = self.clients["codex"].append(self.clients["codex"].message("user", "Implement the plan."))
        self.root = self.base / "frozen"
        self.request = dict(version=1, campaign_id="offline-freeze", root=str(self.root), source=str(self.source),
            corpus=str(self.corpus.inputs), controller=str(self.controller), lease_root=str(self.base / "leases"),
            revision="offline-revision", sessions=self.hints,
            native_authorizations=[dict(session=copy.deepcopy(self.clients["codex"].session), record=auth,
                                        text_sha256=digest(b"Implement the plan."))])
        self.ref = write_json(self.base / "freeze-request.json", self.request)

    def observe(self, _campaign):
        return {k: {name: copy.deepcopy(c.session[name]) for name in
                    ("provider", "session_id", "workspace_uuid", "pane_uuid", "surface_uuid", "tty", "process", "transcript")}
                for k, c in self.clients.items()}

    def freeze(self):
        return freeze_campaign(self.ref, fixtures=True, observer=self.observe)


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = FreezeFixture(self.tmp.name)

    def test_campaign_task_namespace_is_stable_bounded_and_never_reuses_legacy_id(self):
        first = corpus_plan(self.fx.root / "inputs", "revision")
        self.assertEqual(first, corpus_plan(self.fx.root / "inputs", "revision"))
        second = corpus_plan(self.fx.base / "other-campaign" / "inputs", "revision")
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        self.assertNotEqual(first["plan_id"], "orchestration-corpus-v2")
        self.assertIsNotNone(ID_RE.fullmatch(first["plan_id"]))
        self.assertEqual(first["plan_id"], corpus_plan(self.fx.root / "inputs", "new-revision")["plan_id"])
        # A campaign's shared plan, node ordering and workload are identical
        # for all nine strategies; only its namespace differs from another.
        left = [{k: v for k, v in n.items() if k != "source"} for n in first["nodes"]]
        right = [{k: v for k, v in n.items() if k != "source"} for n in second["nodes"]]
        self.assertEqual(left, right)

    def test_complete_private_freeze_preserves_bytes_modes_failed_fetch_and_all_policies(self):
        before = {k: c.path.read_bytes() for k, c in self.fx.clients.items()}
        result = self.fx.freeze()
        self.assertEqual(result["status"], "FROZEN_NOT_MEASURED")
        self.assertEqual(result["input_operations"], 0)
        value = validate_campaign(result["campaign"], fixtures=True)
        self.assertEqual([r["strategy"] for r in value["runs"]], list(ORDER))
        self.assertEqual(value["controller"]["path"], str(self.fx.controller))
        self.assertEqual(value["adapter"]["files"], tree_manifest(self.fx.source)["files"])
        self.assertIn("empty", value["adapter"]["directories"])
        self.assertNotIn("scripts/__pycache__", value["adapter"]["directories"])
        self.assertIn("__pycache__/existing.pyc", value["controller"]["files"])
        self.assertNotIn(".git", value["adapter"]["directories"])
        self.assertEqual(value["input_tree"]["files"]["fetched/tssdkpy-head.json"]["bytes"], 0)
        self.assertEqual(len(value["inputs"]), 6)
        probe = result["schedule_probe"]
        self.assertTrue(probe["distinguishes_all"])
        self.assertEqual({s: p["estimated_seconds"] for s, p in probe["strategies"].items()},
                         {"serial": 170.0, "omo": 105.0, "codeg": 125.0})
        complete = read_json(self.fx.root / "freeze-complete.json")
        self.assertEqual(complete["campaign"], result["campaign"])
        self.assertEqual(complete["business_executions"], 0)
        self.assertEqual(before, {k: c.path.read_bytes() for k, c in self.fx.clients.items()})
        with self.assertRaisesRegex(ContractError, "already exists"):
            self.fx.freeze()

    def test_latest_real_model_required_and_synthetic_error_does_not_change_it(self):
        controller = tree_manifest(self.fx.controller)
        error = self.fx.clients["claude"].message("assistant", "API unavailable", isApiErrorMessage=True)
        error["message"]["model"] = "<synthetic>"
        self.fx.clients["claude"].append(error)
        captured = capture_sessions(self.fx.hints, controller, fixtures=True, observer=self.fx.observe)
        self.assertEqual(captured["claude"]["model"], "test-model")
        changed = self.fx.clients["claude"].message("assistant", "ready")
        changed["message"]["model"] = "different-real-model"
        self.fx.clients["claude"].append(changed)
        with self.assertRaisesRegex(ContractError, "latest native model"):
            self.fx.freeze()
        self.assertFalse(self.fx.root.exists())

    def test_adapter_bytecode_is_refused_before_freeze_intent(self):
        cache = self.fx.source / "scripts" / "__pycache__"
        cache.mkdir()
        for empty in (True, False):
            with self.subTest(empty=empty):
                if not empty:
                    (cache / "existing.pyc").write_bytes(b"untrusted adapter bytecode")
                with self.assertRaisesRegex(ContractError, "bytecode/cache"):
                    self.fx.freeze()
                self.assertFalse(self.fx.root.exists())

    def test_original_endpoint_authorization_and_live_identity_cannot_be_substituted(self):
        actual = self.fx.observe(None)
        actual["claude"]["surface_uuid"] = "replacement"
        with self.assertRaisesRegex(ContractError, "original live session identity"):
            freeze_campaign(self.fx.ref, fixtures=True, observer=lambda _: actual)
        with self.assertRaisesRegex(ContractError, "offline-only"):
            capture_sessions(self.fx.hints, tree_manifest(self.fx.controller), observer=self.fx.observe)
        self.fx.request["native_authorizations"][0]["text_sha256"] = "a" * 64
        self.fx.ref = write_json(self.fx.ref["path"], self.fx.request)
        with self.assertRaisesRegex(ContractError, "original native user"):
            self.fx.freeze()
        self.assertFalse(self.fx.root.exists())

    def test_failed_corpus_verification_preserves_original_freeze_intent_and_failure(self):
        manifest = self.fx.corpus.inputs / "MANIFEST.jsonl"
        rows = [json.loads(line) for line in manifest.read_bytes().splitlines()]
        rows[0]["url"] = "https://example.invalid/unbound-source"
        manifest.write_bytes(b"".join(canonical(row) for row in rows))
        with self.assertRaises(ContractError):
            self.fx.freeze()
        intent = file_ref(self.fx.root / "freeze-intent.json")
        failure = file_ref(self.fx.root / "freeze-failure.json")
        self.assertEqual(read_json(failure["path"])["input_operations"], 0)
        self.assertFalse((self.fx.root / "campaign.json").exists())
        self.assertFalse((self.fx.root / "freeze-complete.json").exists())
        with self.assertRaisesRegex(ContractError, "already exists"):
            self.fx.freeze()
        self.assertEqual(intent, file_ref(intent["path"]))
        self.assertEqual(failure, file_ref(failure["path"]))

    def test_source_change_during_copy_is_detected_without_a_completed_freeze(self):
        from orchestration_freeze import copy_runtime
        def changed(manifest, target):
            (self.fx.source / ".hidden").write_bytes(b"changed during copy")
            return copy_runtime(manifest, target)
        with patch("orchestration_freeze.copy_runtime", side_effect=changed):
            with self.assertRaises(ContractError):
                self.fx.freeze()
        self.assertTrue((self.fx.root / "freeze-failure.json").is_file())
        self.assertFalse((self.fx.root / "freeze-complete.json").exists())

    def test_aliases_nested_destination_and_untrusted_request_hash_are_refused(self):
        with self.assertRaisesRegex(ContractError, "trusted campaign hash"):
            anchored_ref(self.fx.ref["path"], "0" * 64)
        (self.fx.source / "alias").symlink_to(self.fx.source / ".hidden")
        with self.assertRaisesRegex(ContractError, "symlink"):
            self.fx.freeze()
        (self.fx.source / "alias").unlink()
        self.fx.request["root"] = str(self.fx.source / "nested")
        self.fx.ref = write_json(self.fx.ref["path"], self.fx.request)
        with self.assertRaisesRegex(ContractError, "separate"):
            self.fx.freeze()

    def test_public_freezer_has_no_fixture_or_identity_injection_switch(self):
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("orchestration_freeze.py")),
            "--request", self.fx.ref["path"], "--request-sha256", self.fx.ref["sha256"], "--fixtures"],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)
        self.assertFalse(self.fx.root.exists())


class FrozenCampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = FreezeFixture(self.tmp.name)
        self.ref = self.fx.freeze()["campaign"]
        self.value = read_json(self.ref["path"])

    def test_rehashed_incomplete_inputs_duplicate_paths_checker_and_probe_are_rejected(self):
        mutations = (
            lambda c: c["inputs"].pop(),
            lambda c: c["inputs"].append(c["inputs"][0]),
            lambda c: c.update(checker=c["workload"]),
            lambda c: c.update(checker=file_ref(self.fx.source / "scripts/orchestration_checker.py")),
            lambda c: c["sessions"]["claude"].update(model="forged-model"),
            lambda c: c["runs"].reverse(),
        )
        # Run-order enforcement is live-only; the remaining invariants apply to both classes.
        for mutate in mutations[:-1]:
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(self.value)
                mutate(value)
                ref = write_json(self.ref["path"], value)
                with self.assertRaises(ContractError):
                    validate_campaign(ref, fixtures=True, check_runtime=False)
        value = copy.deepcopy(self.value)
        probe = read_json(value["schedule_probe"]["path"])
        probe["strategies"]["serial"]["estimated_seconds"] = 1
        value["schedule_probe"] = write_json(value["schedule_probe"]["path"], probe)
        with self.assertRaisesRegex(ContractError, "distinguish"):
            validate_campaign(write_json(self.ref["path"], value), fixtures=True)

    def test_hidden_bytecode_modes_and_empty_directories_are_complete_tree_evidence(self):
        root = Path(self.value["adapter"]["path"])
        for mutate, restore in (
            (lambda: (root / "extra").mkdir(), lambda: (root / "extra").rmdir()),
            (lambda: (root / ".new").write_bytes(b"extra"), lambda: (root / ".new").unlink()),
            (lambda: os.chmod(root / ".hidden", 0o700), lambda: os.chmod(root / ".hidden", self.value["adapter"]["files"][".hidden"]["mode"])),
        ):
            mutate()
            with self.assertRaisesRegex(ContractError, "tree changed"):
                verify_tree(self.value["adapter"])
            restore()
        pyc = Path(self.value["controller"]["path"]) / "__pycache__/existing.pyc"
        pyc.write_bytes(b"replacement bytecode")
        with self.assertRaisesRegex(ContractError, "tree changed"):
            validate_campaign(self.ref, fixtures=True)

    def test_live_schema_requires_all_nine_trials_and_mandatory_pins_even_without_runtime_scan(self):
        # Mock only original-file admission inside this offline test. No CLI accepts fixture journals.
        for field in ("input_tree", "schedule_probe", "workload"):
            value = copy.deepcopy(self.value)
            value["evidence_class"] = "cli_campaign_v2"
            del value[field]
            with self.subTest(field=field), patch("orchestration_campaign.NativeReader",
                    side_effect=lambda session, **_: NativeReader(session, fixtures=True)):
                with self.assertRaises(ContractError):
                    validate_campaign(write_json(self.ref["path"], value), check_runtime=False)
        value = copy.deepcopy(self.value)
        value.update(evidence_class="cli_campaign_v2", runs=value["runs"][:-1])
        with patch("orchestration_campaign.NativeReader", side_effect=lambda session, **_: NativeReader(session, fixtures=True)):
            with self.assertRaisesRegex(ContractError, "nine counterbalanced"):
                validate_campaign(write_json(self.ref["path"], value), check_runtime=False)


if __name__ == "__main__":
    unittest.main()
