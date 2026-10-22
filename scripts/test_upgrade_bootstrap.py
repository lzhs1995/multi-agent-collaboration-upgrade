"""Offline bootstrap publication/wait races; no model or transport is invoked."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_bytes_once, write_json
import orchestration_common as common
import upgrade_bootstrap as bootstrap
import upgrade_live_control as control


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.directory = self.root / "evidence/live-control"
        self.directory.mkdir(parents=True)
        self.spec = dict(adoption_path=str(self.directory / "adoption.json"),
                         stop_path=str(self.directory / "stop.json"))
        self.startup = write_json(self.root / "config/startup-v2.json", self.spec)
        self.context = write_json(self.root / "experiment.json", dict(offline_fixture=True))
        self.runtime = dict(startup=self.startup, context=self.context, arm="B",
                            controller=dict(path=str(self.root / "controller")))
        self.runtime_ref = write_json(self.root / "runtime.json", self.runtime)
        self.adoption = write_json(self.spec["adoption_path"], dict(schema="upgrade-live-adoption-v2",
            verified=True, runtime=self.runtime_ref, startup=self.startup))
        self.campaign = dict(version=3, evidence_class="cli_campaign_v3", runtime=self.runtime_ref,
            experiment=self.context, client_isolation=self.adoption, arm="B",
            root=str(self.root / "campaigns/one"))
        self.campaign_ref = write_json(self.root / "campaigns/one/campaign.json", self.campaign)
        self.instructions = write_bytes_once(self.root / "campaigns/one/instructions.txt",
                                             "Read the frozen plan. 验收后继续。\n".encode())
        self.binding = dict(evidence_root=str(self.root / "evidence"), config_root=str(self.root / "config"),
            campaign_root=str(self.root / "campaigns"), controller_root=str(self.root / "controller"),
            context=self.context, arm="B")
        self.addCleanup(patch.stopall)
        self.runtime_check = patch.object(control, "validate_live_runtime",
            return_value=({}, self.runtime)).start()
        self.adoption_check = patch.object(control, "validate_adoption").start()
        self.campaign_check = patch("orchestration_campaign.validate_campaign",
                                    return_value=self.campaign).start()
        self.external_check = patch.object(control, "external_publisher").start()

    def publish(self):
        return control.publish_bootstrap(self.runtime_ref, self.campaign_ref, self.instructions)

    def test_publish_and_wait_preserve_exact_body_without_transport(self):
        self.assertIsNone(bootstrap.read_instruction(self.binding))
        published = self.publish()
        self.assertEqual(published["status"], "BOOTSTRAP_PUBLISHED")
        result = bootstrap.wait_instruction(self.binding, 0)
        self.assertEqual(result["status"], "BOOTSTRAP_READY")
        self.assertEqual(result["instructions"], "Read the frozen plan. 验收后继续。\n")
        self.assertEqual(result["input_operations"], 0)
        self.assertFalse(published["native_reception_verified"])
        self.adoption_check.assert_called_once_with(self.runtime_ref, self.adoption, runtime=self.runtime)
        self.campaign_check.assert_called_once_with(self.campaign_ref)
        self.assertEqual(self.publish()["status"], "BOOTSTRAP_ALREADY_PUBLISHED")

    def test_reader_never_observes_staged_or_partial_publication(self):
        staged, release = threading.Event(), threading.Event()
        original_replace = os.replace

        def replace(source, destination, **kwargs):
            if Path(destination).name == "bootstrap.json":
                staged.set()
                self.assertTrue(release.wait(2))
            return original_replace(source, destination, **kwargs)

        with patch.object(common.os, "replace", side_effect=replace), ThreadPoolExecutor(2) as pool:
            future = pool.submit(self.publish)
            try:
                self.assertTrue(staged.wait(2))
                self.assertIsNone(bootstrap.read_instruction(self.binding))
                self.assertEqual(bootstrap.wait_instruction(self.binding, 0)["status"], "BOOTSTRAP_TIMEOUT")
                waiting = pool.submit(bootstrap.wait_instruction, self.binding, 2)
            finally:
                release.set()
            self.assertEqual(future.result()["status"], "BOOTSTRAP_PUBLISHED")
            self.assertEqual(waiting.result()["status"], "BOOTSTRAP_READY")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_racing_publishers_keep_one_identical_instruction(self):
        def attempt(_):
            try:
                return self.publish()
            except ContractError as exc:
                self.assertIn("already active", str(exc))
                return dict(status="BUSY_ORIGINAL_PUBLISHER")
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(item["status"] == "BOOTSTRAP_PUBLISHED" for item in results), 1)
        self.assertTrue(all(item["status"] in ("BOOTSTRAP_PUBLISHED", "BOOTSTRAP_ALREADY_PUBLISHED",
                                              "BUSY_ORIGINAL_PUBLISHER") for item in results))
        original = next(item["bootstrap"] for item in results if item["status"] == "BOOTSTRAP_PUBLISHED")
        self.assertEqual(self.publish()["bootstrap"], original)
        other = write_bytes_once(self.root / "campaigns/one/changed.txt", b"Different task")
        with self.assertRaisesRegex(ContractError, "ALREADY_PUBLISHED_DIFFERENT"):
            control.publish_bootstrap(self.runtime_ref, self.campaign_ref, other)
        self.assertEqual(bootstrap.read_instruction(self.binding)["instructions"],
                         "Read the frozen plan. 验收后继续。\n")

    def test_timeout_and_stop_are_zero_input_and_preserve_publication(self):
        for timeout in (-1, 901, True):
            with self.assertRaisesRegex(ContractError, "TIMEOUT_OUT_OF_RANGE"):
                bootstrap.wait_instruction(self.binding, timeout)
        self.assertEqual(bootstrap.wait_instruction(self.binding, 0)["input_operations"], 0)
        published = self.publish()["bootstrap"]
        Path(self.spec["stop_path"]).write_text("stop")
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            bootstrap.wait_instruction(self.binding, 0)
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            self.publish()
        self.assertEqual(file_ref(published["path"]), published)

    def test_stop_during_validation_prevents_publication(self):
        def validate(_):
            Path(self.spec["stop_path"]).write_text("stop")
            return self.campaign
        self.campaign_check.side_effect = validate
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            self.publish()
        self.assertFalse((self.directory / "bootstrap.json").exists())

    def test_reader_rejects_changed_runtime_adoption_campaign_and_body(self):
        ref = self.publish()["bootstrap"]
        original = json_ref(ref)
        mutations = [lambda v: v.update(startup=self.context),
                     lambda v: v.update(adoption=self.context),
                     lambda v: v.update(campaign=self.context),
                     lambda v: v.update(instructions=""),
                     lambda v: v.update(unknown="unexpected")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = copy.deepcopy(original)
                mutate(value)
                write_json(ref["path"], value)
                with self.assertRaises((ContractError, KeyError)):
                    bootstrap.read_instruction(self.binding)
        write_json(ref["path"], original)
        self.runtime["arm"] = "A1"
        new_runtime = write_json(self.runtime_ref["path"], self.runtime)
        original["runtime"] = new_runtime
        write_json(ref["path"], original)
        with self.assertRaisesRegex(ContractError, "BOOTSTRAP_RUNTIME_CHANGED"):
            bootstrap.read_instruction(self.binding)

    def test_client_or_foreign_campaign_cannot_publish(self):
        self.external_check.side_effect = ContractError("CLIENT_CANNOT_PUBLISH_ADOPTION")
        with self.assertRaisesRegex(ContractError, "CLIENT_CANNOT_PUBLISH"):
            self.publish()
        self.external_check.side_effect = None
        self.campaign_check.return_value = dict(self.campaign, arm="A1")
        with self.assertRaisesRegex(ContractError, "BOOTSTRAP_CAMPAIGN_CHANGED"):
            self.publish()
        self.assertFalse((self.directory / "bootstrap.json").exists())


if __name__ == "__main__":
    unittest.main()
