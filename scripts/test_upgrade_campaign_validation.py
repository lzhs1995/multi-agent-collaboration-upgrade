"""Offline materialized gates: local deduplication never authorizes stale peers."""
from __future__ import annotations

import copy
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, json_ref
from orchestration_contracts import validate_private_campaign
from orchestration_native import NativeReader
from orchestration_test_support import CampaignFixture
import test_upgrade_live_control as live_fixtures
import test_upgrade_live_startup as startup_fixtures
import upgrade_benchmark as benchmark
import upgrade_experiment as experiment
from upgrade_live_materialize import materialize_live
import upgrade_live_runtime as runtime
from upgrade_startup_contract import validate_startup


@unittest.skipUnless(startup_fixtures.SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(),
                     "requires fixed MAC and cmux wrappers")
class LiveCampaignValidationTests(unittest.TestCase):
    observation = live_fixtures.LiveControlTests.observation
    publish_fixture = live_fixtures.LiveControlTests.publish_fixture

    def setUp(self):
        # /private/tmp resolves one byte longer than /Users/lzhs; the registered
        # A1 layout also needs to fit Claude's 44-byte private temp path limit.
        self.tmp = tempfile.TemporaryDirectory(prefix="", dir=Path.home())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        # register requires a new root; retain TemporaryDirectory's cleanup but
        # avoid another path component beyond Claude's actual 44-byte limit.
        self.root.rmdir()
        self.registration_ref = benchmark.register(self.root, "validation-fixture", compact=True)
        self.context_ref = experiment.init_experiment(self.root / "g/0", "focused-gate", startup_fixtures.SOURCE)
        binaries = {key: Path("/usr/bin/true") for key in ("codex", "claude", "cmux", "rtk", "node")}
        binaries.update({provider + "_wrapper": startup_fixtures.WRAPPERS / ("cmux-" + provider + "-wrapper")
                         for provider in ("codex", "claude")})
        self.runtime_ref = materialize_live(self.context_ref, "B", binaries=binaries,
                                            provider_configuration=({}, {}))
        self.runtime = json_ref(self.runtime_ref)
        self.ref = self.runtime["startup"]
        self.spec, self.binding = validate_startup(self.ref)
        self.paths = experiment.arm_paths(experiment.validate_context(self.context_ref), "B")

    def rewrite(self, path, value):
        self.assertIn(self.root, path.parents)
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | stat.S_IWUSR)
        try:
            path.write_bytes(value)
        finally:
            path.chmod(mode)

    def campaign(self):
        proof = self.publish_fixture()
        return dict(runtime=self.runtime_ref, experiment=self.context_ref, arm="B",
            registration=self.registration_ref, session_group="pilot-B", campaign_id="pilot-three-rounds",
            workload_class="uneven_successor",
            runs=[dict(run_id=f"pilot-{index:02d}", strategy="omo") for index in range(1, 4)],
            root=str(self.paths["campaigns"] / "fixture"),
            lease_root=str(self.paths["state"] / "orchestration-leases"),
            controller=self.runtime["controller"], python=self.runtime["python"],
            adapter=dict(path=str(self.paths["controllers"] / "adapter")),
            sessions=runtime.authenticated_sessions(self.runtime_ref, proof), client_isolation=proof)

    def test_public_gate_revalidates_source_runtime_adoption_stop_and_session(self):
        campaign = self.campaign()

        def gate(sessions=None):
            return runtime.live_gate_v2(self.context_ref, self.runtime_ref,
                campaign["sessions"] if sessions is None else sessions, campaign["client_isolation"])

        # Mutate only this test's private source copy, never the installed MAC.
        source = Path(self.runtime["controller"]["path"]) / "scripts/mac_upgrade_live_inspect.py"
        targets = [source, Path(self.runtime_ref["path"]), Path(campaign["client_isolation"]["path"])]
        original_validate = runtime.validate_live_runtime
        with patch.object(runtime, "validate_live_runtime", wraps=original_validate) as validate:
            self.assertTrue(gate()["verified"])
            for path in targets:
                original = path.read_bytes()
                try:
                    self.rewrite(path, original + b"\n")
                    with self.subTest(path=path.name), self.assertRaises(ContractError):
                        gate()
                finally:
                    self.rewrite(path, original)
            stop = Path(self.spec["stop_path"])
            try:
                stop.write_text("{}\n")
                with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
                    gate()
            finally:
                stop.unlink()
            sessions = copy.deepcopy(campaign["sessions"])
            sessions["claude_b"]["model"] = "changed-model"
            with self.assertRaisesRegex(ContractError, "CAMPAIGN_SESSION_NOT_ADOPTED"):
                gate(sessions)
            self.assertTrue(gate()["verified"])
            self.assertEqual(validate.call_count, 7)

    def test_private_path_validates_once_per_call_and_authenticates_real_adoption(self):
        campaign = self.campaign()
        original_versioned = runtime.validate_versioned_runtime
        original_live = runtime.validate_live_runtime
        original_adoption = runtime.validate_adoption
        # Spies call real validators, including the complete registered layout.
        with patch.object(benchmark, "validate_registered_campaign", wraps=benchmark.validate_registered_campaign) as registered, \
             patch.object(runtime, "validate_versioned_runtime", wraps=original_versioned) as versioned, \
             patch.object(runtime, "validate_live_runtime", wraps=original_live) as live, \
             patch.object(runtime, "validate_adoption", wraps=original_adoption) as adopted:
            for _ in range(2):
                context, current = validate_private_campaign(campaign, fixtures=False)
                self.assertEqual(current, self.runtime)
                self.assertEqual(context["root"], str(Path(self.context_ref["path"]).parent))
            self.assertEqual((versioned.call_count, live.call_count, adopted.call_count), (2, 2, 2))
            self.assertEqual(registered.call_count, 2)
            proof_path = Path(campaign["client_isolation"]["path"])
            original = proof_path.read_bytes()
            try:
                proof_path.write_bytes(original + b"\n")
                with self.assertRaises(ContractError):
                    validate_private_campaign(campaign, fixtures=False)
            finally:
                proof_path.write_bytes(original)
            self.assertEqual((versioned.call_count, live.call_count, adopted.call_count), (3, 3, 3))
            self.assertEqual(registered.call_count, 2)
            with self.assertRaisesRegex(ContractError, "cannot impersonate active client adoption"):
                validate_private_campaign(campaign, fixtures=True)

    def test_each_native_reader_keeps_full_authority_and_later_calls_see_changes(self):
        campaign = self.campaign()
        original_live = runtime.validate_live_runtime
        with patch.object(runtime, "validate_live_runtime", wraps=original_live) as live:
            validate_private_campaign(campaign, fixtures=False)
            for session in campaign["sessions"].values():
                NativeReader(session)
            self.assertEqual(live.call_count, 4)
            source = Path(self.runtime["controller"]["path"]) / "scripts/mac_upgrade_live_inspect.py"
            original = source.read_bytes()
            try:
                self.rewrite(source, original + b"\n")
                with self.assertRaises(ContractError):
                    NativeReader(campaign["sessions"]["claude_a"])
                with self.assertRaises(ContractError):
                    validate_private_campaign(campaign, fixtures=False)
            finally:
                self.rewrite(source, original)
            self.assertEqual(live.call_count, 6)
            self.assertGreater(NativeReader(campaign["sessions"]["claude_a"]).snapshot()["offset"], 0)
            self.assertEqual(live.call_count, 7)


class OfflineCampaignValidationTests(unittest.TestCase):
    def test_v1_fixture_and_live_refusal_still_use_original_validator(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = CampaignFixture(directory, version=3)
            original = experiment.validate_runtime
            with patch.object(experiment, "validate_runtime", wraps=original) as validate, \
                 patch.object(runtime, "validate_adoption", side_effect=AssertionError("v1 cannot adopt")):
                _, current = validate_private_campaign(fixture.campaign, fixtures=True)
                self.assertEqual((current["version"], current["execution_mode"]), (1, "offline"))
                self.assertEqual(validate.call_count, 1)
                with self.assertRaisesRegex(ContractError, "LIVE_ISOLATION_UNVERIFIED"):
                    validate_private_campaign(fixture.campaign, fixtures=False)
                self.assertEqual(validate.call_count, 3)


if __name__ == "__main__":
    unittest.main()
