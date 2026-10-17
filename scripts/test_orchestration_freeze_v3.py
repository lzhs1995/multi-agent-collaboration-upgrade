"""Offline synthetic adoption exercises; never evidence of live client readiness."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

from orchestration_adapter import validate_adapter
from orchestration_campaign import validate_campaign
from orchestration_common import (ContractError, digest, file_ref, json_ref, read_json,
                                  tree_manifest, verify_tree, write_json)
import orchestration_freeze_v3 as freezer
from orchestration_native import NativeReader
from test_orchestration_workload import CorpusFixture
import test_upgrade_live_control as control_fixtures
import test_upgrade_live_startup as startup_fixtures
from upgrade_benchmark import register, validate_registration
from upgrade_experiment import arm_paths, init_experiment, validate_context
from upgrade_live_materialize import materialize_live
from upgrade_live_runtime import authenticated_sessions
from upgrade_startup_contract import validate_startup


@lru_cache(maxsize=1)
def source_files():
    source = Path(__file__).resolve().parents[1]
    return source, validate_adapter(tree_manifest(source), purpose="campaign")


@unittest.skipUnless(startup_fixtures.SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(),
                     "requires immutable local controller and cmux wrappers")
class V3FreezeTests(unittest.TestCase):
    def setUp(self):
        # Exercise the real compact registration and native temp limits.
        # /tmp resolves to /private/tmp on macOS; longer fixture roots cannot
        # materialize a Claude client without falling back outside its sandbox.
        self.root = Path("/tmp").resolve() / ("v" + uuid.uuid4().hex[:6])
        self.registration = register(self.root, "offline-only", compact=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.group = validate_registration(self.registration)["groups"]["pilot-B"]
        self.context_ref = init_experiment(Path(self.group["context_path"]).parent, "fixture", startup_fixtures.SOURCE)
        binaries = {key: Path("/usr/bin/true") for key in ("codex", "claude", "cmux", "rtk", "node")}
        binaries.update({p + "_wrapper": startup_fixtures.WRAPPERS / ("cmux-" + p + "-wrapper")
                         for p in ("codex", "claude")})
        self.runtime_ref = materialize_live(self.context_ref, "B", binaries=binaries, provider_configuration=({}, {}))
        self.runtime = json_ref(self.runtime_ref)
        self.ref = self.runtime["startup"]
        self.spec, _ = validate_startup(self.ref)
        self.paths = arm_paths(validate_context(self.context_ref), "B")
        _, _, proof = control_fixtures.LiveControlTests.observation(self)
        self.adoption = write_json(self.spec["adoption_path"], proof, exclusive=True)
        self.sessions = authenticated_sessions(self.runtime_ref, self.adoption)
        session = self.sessions["codex"]
        path = Path(session["transcript"]["path"])
        before = path.stat().st_size
        text = "Implement the plan. [offline fixture authorization]"
        self.append("codex", dict(type="response_item", payload=dict(type="message", role="user",
                    content=[dict(type="input_text", text=text)])))
        reader = NativeReader(session, fixtures=True)
        record = list(reader.records(before, reader.snapshot()["offset"]))[0][0]
        self.corpus = CorpusFixture(self.root / "data")
        self.source = self.root / "source"
        (self.source / "scripts").mkdir(parents=True)
        original, names = source_files()
        for name in names:
            (self.source / name).write_bytes((original / name).read_bytes())
        (self.source / "SKILL.md").write_text("offline adapter fixture\n")
        self.request = dict(version=3, experiment=self.context_ref, runtime=self.runtime_ref,
            adoption=self.adoption, registration=self.registration, session_group="pilot-B",
            source=str(self.source), corpus=str(self.corpus.inputs), revision="offline-source",
            native_authorizations=[dict(session=session, record=record, text_sha256=digest(text.encode()))])
        self.request_ref = write_json(self.root / "request.json", self.request)
        self.campaign_root = self.paths["campaigns"] / self.group["campaign_id"]

    def append(self, lane, record):
        with Path(self.sessions[lane]["transcript"]["path"]).open("a") as stream:
            stream.write(json.dumps(record) + "\n")

    def freeze(self):
        return freezer.freeze_campaign(self.request_ref)

    def test_complete_freeze_preserves_adopted_model_record_and_uses_fresh_fences(self):
        self.append("codex", dict(type="turn_context", payload=dict(model="fixture-codex")))
        before = {k: Path(s["transcript"]["path"]).read_bytes() for k, s in self.sessions.items()}
        result = self.freeze()
        self.assertEqual((result["status"], result["input_operations"]), ("FROZEN_NOT_MEASURED", 0))
        campaign = validate_campaign(result["campaign"])
        self.assertEqual(campaign["controller"], self.runtime["controller"])
        self.assertTrue(Path(campaign["adapter"]["path"]).is_relative_to(self.paths["controllers"]))
        self.assertEqual(campaign["lease_root"], str(self.paths["state"] / "orchestration-leases"))
        self.assertEqual(campaign["runs"], [dict(run_id=f"pilot-{i:02d}", strategy="omo") for i in range(1, 4)])
        for lane, session in campaign["sessions"].items():
            self.assertEqual(session["model_record"], self.sessions[lane]["model_record"])
            NativeReader(session).verify_pin(session["freeze_fence"])
        self.assertGreater(campaign["sessions"]["codex"]["freeze_fence"]["offset"],
                           self.sessions["codex"]["freeze_fence"]["offset"])
        verify_tree(campaign["input_tree"])
        self.assertEqual(before, {k: Path(s["transcript"]["path"]).read_bytes() for k, s in self.sessions.items()})
        with self.assertRaisesRegex(ContractError, "exists"):
            self.freeze()

    def test_latest_real_model_change_rejected_but_synthetic_error_ignored(self):
        self.append("claude_a", dict(type="assistant", sessionId=self.sessions["claude_a"]["session_id"],
            isApiErrorMessage=True, message=dict(role="assistant", model="<synthetic>", content=[])))
        sessions = freezer.capture_adopted_sessions(self.runtime_ref, self.adoption)
        self.assertEqual(sessions["claude_a"]["model"], "fixture-claude")
        self.append("claude_a", dict(type="assistant", sessionId=self.sessions["claude_a"]["session_id"],
            message=dict(role="assistant", model="different-model", content=[])))
        with self.assertRaisesRegex(ContractError, "model changed"):
            self.freeze()
        self.assertFalse(self.campaign_root.exists())

    def test_missing_adoption_wrong_authorization_and_stop_are_not_bypassed(self):
        original = copy.deepcopy(self.request)
        cases = [dict(adoption=dict(self.adoption, path=str(self.root / "forged.json"))),
                 dict(native_authorizations=[dict(original["native_authorizations"][0], text_sha256="0" * 64)]),
                 dict(session_group="fan_in-A1")]
        for changes in cases:
            self.request_ref = write_json(self.request_ref["path"], dict(original, **changes))
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                self.freeze()
            self.assertFalse(self.campaign_root.exists())
        self.request_ref = write_json(self.request_ref["path"], original)
        write_json(self.spec["stop_path"], {"operator_stop": True})
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            self.freeze()

    def test_corrupt_corpus_retains_original_intent_and_failure(self):
        manifest = self.corpus.inputs / "MANIFEST.jsonl"
        rows = [json.loads(line) for line in manifest.read_bytes().splitlines()]
        rows[0]["url"] = "https://example.invalid/unbound"
        manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaises(ContractError):
            self.freeze()
        intent = file_ref(self.campaign_root / "freeze-intent.json")
        failure = file_ref(self.campaign_root / "freeze-failure.json")
        self.assertEqual(read_json(failure["path"])["input_operations"], 0)
        self.assertFalse((self.campaign_root / "freeze-complete.json").exists())
        with self.assertRaisesRegex(ContractError, "exists"):
            self.freeze()
        self.assertEqual(file_ref(intent["path"]), intent)
        self.assertEqual(file_ref(failure["path"]), failure)

    def test_public_entry_has_no_fixture_or_observer_bypass(self):
        result = subprocess.run([sys.executable, "-B", freezer.__file__, "--request", self.request_ref["path"],
            "--request-sha256", self.request_ref["sha256"], "--fixtures"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)
        self.assertFalse(self.campaign_root.exists())


class GraphTests(unittest.TestCase):
    def test_three_graphs_share_business_inputs_and_reserve_conflicting_outputs(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path).resolve()
            uneven = freezer.corpus_plan(root, "revision", "uneven_successor")
            nodes = {n["id"]: n for n in uneven["nodes"]}
            self.assertEqual(nodes["inventory_successor"]["depends_on"], ["inventory"])
            self.assertEqual(nodes["source_index"]["executor"], "claude_b")
            self.assertEqual(set(nodes["merge_uneven"]["depends_on"]), {"inventory", "inventory_successor", "source_index"})
            fan = freezer.corpus_plan(root, "revision", "fan_in")
            conflict = freezer.corpus_plan(root, "revision", "write_conflict")
            self.assertEqual([n["id"] for n in fan["nodes"]], [n["id"] for n in conflict["nodes"]])
            for node in conflict["nodes"][:2]:
                self.assertEqual(node["write_scope"], "shared")
                self.assertEqual(node["output_scope"], "shared/" + node["id"])


if __name__ == "__main__":
    unittest.main()
