"""Registration invariants only; these tests do not provide live measurements."""
import copy
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_benchmark import (ARM_STRATEGIES, CLASSES, group_registration, register,
                               runtime_observation, validate_registered_campaign, validate_registration)


def legacy_registration(root, benchmark_id, *, compact=False):
    """Synthetic historical fixture only; production register emits v3."""
    ref = register(root, benchmark_id, compact=compact)
    value = json_ref(ref)
    value.pop("compact")
    value.pop("response_observation")
    value["schema"] = "upgrade-paired-registration-v2" if compact else "upgrade-paired-registration-v1"
    return write_json(ref["path"], value)


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
        self.assertEqual(compact["schema"], "upgrade-paired-registration-v3")
        self.assertIs(compact["compact"], True)
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
        self.assertEqual(self.value["schema"], "upgrade-paired-registration-v3")

    def test_legacy_shapes_remain_readable_without_rewriting(self):
        for compact in (False, True):
            root = Path("/tmp").resolve() / ("o" + uuid.uuid4().hex[:5])
            ref = legacy_registration(root, "legacy-fixture", compact=compact)
            self.addCleanup(shutil.rmtree, root)
            raw = Path(ref["path"]).read_bytes()
            value = validate_registration(ref)
            self.assertNotIn("response_observation", value)
            self.assertIsNone(runtime_observation(value, {}, {}))
            self.assertEqual(Path(ref["path"]).read_bytes(), raw)

    def test_enabled_policy_pins_complete_current_source_and_disabled_is_explicit(self):
        ref = register(self.root / "enabled", "enabled", response_observation=True)
        value = validate_registration(ref)
        source = file_ref(Path(__file__).with_name("upgrade_claude_response_observer.cjs"))
        self.assertEqual(value["response_observation"], dict(mode="consumed-response-v1",
                         sha256=source["sha256"], bytes=source["bytes"]))
        self.assertEqual(self.value["response_observation"], dict(mode="disabled", sha256=None, bytes=None))

    def test_malformed_policy_and_version_downgrade_are_refused(self):
        changes = [dict(mode="disabled", sha256="0" * 64, bytes=1),
                   dict(mode="consumed-response-v1", sha256="0" * 64, bytes=True),
                   dict(mode="consumed-response-v1", sha256="bad", bytes=1),
                   dict(mode="consumed-response-v1", sha256="0" * 64, bytes=0),
                   dict(mode="unknown", sha256=None, bytes=None), None]
        for policy in changes:
            value = copy.deepcopy(self.value)
            value["response_observation"] = policy
            with self.subTest(policy=policy), self.assertRaises(ContractError):
                validate_registration(write_json(self.ref["path"], value))
        for change in (dict(compact=1), dict(schema="upgrade-paired-registration-v2")):
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_registration(write_json(self.ref["path"], dict(self.value, **change)))

    def test_seven_group_policy_rejects_each_mismatching_member_and_changed_startup(self):
        # Minimal already-validated-runtime inputs; real startup validation is
        # separately exercised by materialized campaign/freezer tests.
        refs = {}
        for group in self.value["groups"]:
            refs[group] = write_json(self.root / group / "startup.json", dict(pins={}))
        for group, ref in refs.items():
            runtime = dict(startup=ref)
            self.assertEqual(runtime_observation(self.value, {}, runtime)["policy"],
                             self.value["response_observation"])
            changed = write_json(ref["path"], dict(pins={"claude_response_observer": dict(sha256="0" * 64, bytes=1)}))
            with self.subTest(group=group), self.assertRaisesRegex(ContractError, "differs from runtime"):
                runtime_observation(self.value, {}, dict(startup=changed))
            with self.assertRaises(ContractError):
                runtime_observation(self.value, {}, runtime)

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
