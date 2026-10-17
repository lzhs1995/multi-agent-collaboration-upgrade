"""Registration invariants only; these tests do not provide live measurements."""
import copy
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from orchestration_common import ContractError, json_ref, write_json
from upgrade_benchmark import (ARM_STRATEGIES, CLASSES, group_registration, register,
                               validate_registered_campaign, validate_registration)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "registered"
        self.ref = register(self.root, "approved-pairs")
        self.value = validate_registration(self.ref)

    def test_twelve_warm_three_cold_and_balanced_order_with_original_groups(self):
        warm = [p for p in self.value["pairs"] if p["phase"] == "warm"]
        cold = [p for p in self.value["pairs"] if p["phase"] == "cold"]
        self.assertEqual((len(warm), len(cold), len(self.value["groups"])), (12, 3, 7))
        for kind in CLASSES:
            pairs = [p for p in warm if p["workload_class"] == kind]
            self.assertEqual([p["order"] for p in pairs].count(["A1", "B"]), 2)
            self.assertEqual([p["order"] for p in pairs].count(["B", "A1"]), 2)
            for arm in ARM_STRATEGIES:
                self.assertEqual({p["members"][arm]["group"] for p in pairs}, {kind + "-" + arm})
                self.assertEqual(len(self.value["groups"][kind + "-" + arm]["runs"]), 5)
        self.assertEqual([r["run_id"] for r in self.value["groups"]["pilot-B"]["runs"]],
                         ["pilot-01", "pilot-02", "pilot-03"])
        self.assertFalse((self.root / "groups").exists())

    def test_rehashed_changes_to_order_failures_or_groups_are_rejected(self):
        for mutate in (lambda v: v["pairs"].pop(),
                       lambda v: v["pairs"][1]["order"].reverse(),
                       lambda v: v["contract"].update(run_timeout_seconds=3600),
                       lambda v: v["groups"]["pilot-B"]["runs"].pop()):
            value = copy.deepcopy(self.value)
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ContractError, "contract changed"):
                validate_registration(write_json(self.ref["path"], value))

    def test_original_context_arm_and_run_ids_cannot_be_substituted(self):
        group = self.value["groups"]["pilot-B"]
        context = {"path": group["context_path"]}
        self.assertEqual(group_registration(self.ref, "pilot-B", context, "B"), group)
        with self.assertRaisesRegex(ContractError, "context or arm"):
            group_registration(self.ref, "pilot-B", context, "A1")
        campaign = dict(registration=self.ref, session_group="pilot-B", experiment=context, arm="B",
                        campaign_id=group["campaign_id"], workload_class=group["workload_class"],
                        runs=[dict(run_id=r["run_id"], strategy="omo") for r in group["runs"]])
        validate_registered_campaign(campaign)
        campaign["runs"][0]["strategy"] = "mac_native"
        with self.assertRaisesRegex(ContractError, "registered runs"):
            validate_registered_campaign(campaign)

    def test_registration_is_exclusive(self):
        with self.assertRaisesRegex(ContractError, "root exists"):
            register(self.root, "replacement")
        self.assertEqual(json_ref(self.ref), self.value)

    def test_compact_layout_retains_all_slots_pairs_order_and_legacy_validation(self):
        # Darwin resolves /tmp to /private/tmp. Use a genuinely short, new
        # directory so this exercises the real native path-length check.
        root = Path("/tmp").resolve() / ("u" + uuid.uuid4().hex[:5])
        compact_ref = register(root, "approved-compact", compact=True)
        self.addCleanup(shutil.rmtree, root)
        compact = validate_registration(compact_ref)
        self.assertEqual(compact["schema"], "upgrade-paired-registration-v2")
        self.assertEqual(compact["pairs"], self.value["pairs"])
        self.assertEqual(compact["contract"], self.value["contract"])
        self.assertEqual(sum(len(g["runs"]) for g in compact["groups"].values()), 33)
        expected_names = ["pilot-B"] + [kind + "-" + arm for kind in CLASSES for arm in ARM_STRATEGIES]
        for name, group in compact["groups"].items():
            # JSON key sorting may differ from registration's insertion order.
            self.assertEqual(group["context_path"], str(root / "g" / str(expected_names.index(name)) / "experiment.json"))
            original = self.value["groups"][name]
            self.assertEqual({k: v for k, v in group.items() if k != "context_path"},
                             {k: v for k, v in original.items() if k != "context_path"})
            self.assertEqual(group_registration(compact_ref, name, {"path": group["context_path"]},
                                                group["arm"]), group)
        self.assertFalse((root / "g").exists())
        self.assertEqual(validate_registration(self.ref), self.value)
        self.assertEqual(self.value["schema"], "upgrade-paired-registration-v1")

    def test_compact_long_root_refuses_before_creating_any_directories(self):
        # Short enough for the RPC socket, too long for Claude's child TMPDIR.
        root = Path("/tmp").resolve() / ("upgrade-" + uuid.uuid4().hex)
        before = set(self.root.iterdir())
        with self.assertRaisesRegex(ContractError, "TMP_PATH_TOO_LONG"):
            register(root, "too-long", compact=True)
        self.assertFalse(root.exists())
        self.assertEqual(set(self.root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
