"""Allow/refuse tests for the separate JEV S2 paired registration (R514)."""
import collections
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import upgrade_benchmark as v3
import upgrade_jev_benchmark as jb
from orchestration_common import ContractError, file_ref

PLAN = Path("/Users/lzhs/projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/execution-20261021/"
            "jev-s2-consumer-plan-v1/OWNER43-JEV-S2-CONSUMER-ATTRIBUTION-PLAN-v1.json")
PLAN_SHA = "da34a9c5d09dbe53456f600645fe90a90ac394c70b7d3e1a010fa04b264581db"


def factors(pairs):
    arm = dict(input_sha256="a" * 64, policies=dict(resource="b" * 64, response_observer="c" * 64, write_scope="d" * 64),
               codex_model="gpt-x", claude_model="claude-x")
    return {p["pair_id"]: {"advisory-off": json.loads(json.dumps(arm)), "advisory-on": json.loads(json.dumps(arm))}
            for p in pairs}


OPPS = {kind: dict(dp1=1, dp2=1) for kind in v3.CLASSES}


@unittest.skipUnless(PLAN.exists(), "frozen S2 plan not present")
class JevRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="jevreg-")))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = self.base / "r"
        _, self.pairs = jb.layout(self.root, file_ref(PLAN))

    def reg(self, root=None, **change):
        # compact=False like test_upgrade_benchmark: the tmp root is too long for the 44 B Claude TMPDIR check.
        kwargs = dict(factors=factors(self.pairs), opportunities=OPPS, compact=False)
        kwargs.update(change)
        return jb.register(root or self.root, "jev-s2-first", PLAN, **kwargs)

    def mutate(self, ref, edit):
        path = Path(ref["path"])
        value = json.loads(path.read_text())
        edit(value)
        os.chmod(path, 0o644)
        path.write_text(json.dumps(value))
        return file_ref(path)

    def test_valid_registration_keeps_the_plan_pairs_and_both_arms_omo(self):
        self.assertEqual(file_ref(PLAN)["sha256"], PLAN_SHA)
        value = jb.validate_registration(self.reg())
        pairs = value["pairs"]
        self.assertEqual((len(pairs), sum(p["phase"] == "warm" for p in pairs)), (15, 12))
        self.assertEqual(collections.Counter(p["workload_class"] for p in pairs), {k: 5 for k in v3.CLASSES})
        self.assertEqual(collections.Counter(tuple(p["order"]) for p in pairs),
                         {("advisory-off", "advisory-on"): 8, ("advisory-on", "advisory-off"): 7})
        self.assertTrue(all(p["pair_id"].startswith("jev-") for p in pairs))
        self.assertEqual({g["strategy"] for g in value["groups"].values()}, {"omo"})
        self.assertEqual(len(value["groups"]["pilot-on"]["runs"]), 3)

    def test_an_original_v3_registration_is_never_accepted_as_s2(self):
        ref = v3.register(self.base / "v3", "orig-v3", compact=False)
        with self.assertRaises(ContractError):
            jb.validate_registration(ref)
        with self.assertRaises(ContractError):
            jb.validate_registration(self.mutate(ref, lambda v: v.update(schema=jb.SCHEMA)))

    def test_altered_missing_extra_or_duplicate_pairs_are_refused(self):
        edits = [lambda v: v["pairs"][0].update(order=list(reversed(v["pairs"][0]["order"]))),
                 lambda v: v["pairs"].pop(),
                 lambda v: v["pairs"].append(dict(v["pairs"][0], pair_id="jev-extra")),
                 lambda v: v["pairs"].__setitem__(1, v["pairs"][0]),
                 lambda v: v["pairs"][2].update(pair_id=v["pairs"][2]["pair_id"][4:])]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaises(ContractError):
                jb.validate_registration(self.mutate(self.reg(self.base / str(id(edit))), edit))

    def test_non_omo_wrong_group_or_run_and_off_transport_are_refused(self):
        edits = [lambda v: v["groups"]["fan_in-off"].update(strategy="mac_native"),
                 lambda v: v["pairs"][0]["members"]["advisory-on"].update(group="fan_in-on"),
                 lambda v: v["pairs"][0]["members"]["advisory-off"].update(run_id="warm-09"),
                 lambda v: v["groups"]["fan_in-off"].update(transport=dict(path="/x", sha256="0" * 64, bytes=1))]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaises(ContractError):
                jb.validate_registration(self.mutate(self.reg(self.base / str(id(edit))), edit))

    def test_arms_with_different_input_model_or_policy_are_refused(self):
        for key, value in (("input_sha256", "e" * 64), ("codex_model", "other"),
                           ("policies", dict(resource="f" * 64, response_observer="c" * 64, write_scope="d" * 64))):
            bad = factors(self.pairs)
            bad[self.pairs[3]["pair_id"]]["advisory-on"][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.reg(self.base / key, factors=bad)

    def test_empty_dp1_or_dp2_opportunities_are_refused(self):
        for kind, field in (("fan_in", "dp1"), ("write_conflict", "dp2")):
            bad = {k: dict(v) for k, v in OPPS.items()}
            bad[kind][field] = 0
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                self.reg(self.base / (kind + field), opportunities=bad)

    def test_a_changed_plan_pin_is_refused(self):
        ref = self.reg()
        with self.assertRaises(ContractError):
            jb.validate_registration(self.mutate(ref, lambda v: v["plan"].update(sha256="0" * 64)))


@unittest.skipUnless(PLAN.exists(), "frozen S2 plan not present")
class S2ConsumerPathTests(unittest.TestCase):
    """R517 NR1: shared consumers dispatch S2 by schema; v3 keeps pilot-B and A1=mac_native."""
    setUp = JevRegistrationTests.setUp
    reg = JevRegistrationTests.reg
    mutate = JevRegistrationTests.mutate

    def test_validate_registration_dispatches_s2_with_every_runtime_arm_b_omo(self):
        value = v3.validate_registration(self.reg())
        self.assertEqual(value["schema"], jb.SCHEMA)
        self.assertEqual({g["arm"] for g in value["groups"].values()}, {"B"})
        self.assertEqual({v3.ARM_STRATEGIES[g["arm"]] for g in value["groups"].values()}, {"omo"})
        self.assertEqual(collections.Counter(g["advisory"] for g in value["groups"].values()),
                         {"advisory-on": 4, "advisory-off": 3})
        self.assertEqual(value["contract"], v3.CONTRACT)
        self.assertEqual(value["response_observation"], dict(mode="disabled", sha256=None, bytes=None))
        self.assertEqual(v3.pilot_group(value), "pilot-on")
        self.assertEqual(v3.registered_layout(value), (value["groups"], value["pairs"]))

    def test_changed_contract_policy_or_arm_is_refused(self):
        edits = (lambda v: v["contract"].update(run_timeout_seconds=3600),
                 lambda v: v["response_observation"].update(mode="consumed-response-v1"),
                 lambda v: v["groups"]["fan_in-off"].update(arm="A1"),
                 lambda v: v.pop("contract"))
        for index, edit in enumerate(edits):
            ref = self.reg(self.base / f"m{index}")
            with self.subTest(index=index), self.assertRaises(ContractError):
                v3.validate_registration(self.mutate(ref, edit))

    def test_group_registration_and_campaign_take_s2_groups_as_omo(self):
        ref = self.reg()
        value = v3.validate_registration(ref)
        group = value["groups"]["write_conflict-off"]
        context = dict(path=group["context_path"])
        self.assertEqual(v3.group_registration(ref, "write_conflict-off", context, "B"), group)
        other = dict(path=value["groups"]["write_conflict-on"]["context_path"])
        for args in (("write_conflict-off", context, "A1"), ("write_conflict-A1", context, "B"),
                     ("write_conflict-off", other, "B")):
            with self.subTest(args=args[0] + ":" + args[2]), self.assertRaises(ContractError):
                v3.group_registration(ref, *args)
        campaign = dict(registration=ref, session_group="write_conflict-off", experiment=context, arm="B",
                        campaign_id=group["campaign_id"], workload_class="write_conflict",
                        runs=[dict(run_id=r["run_id"], strategy="omo") for r in group["runs"]])
        self.assertEqual(v3.validate_registered_campaign(campaign), group)
        for key, edit in (("mac_native", dict(runs=[dict(r, strategy="mac_native") for r in campaign["runs"]])),
                          ("missing_run", dict(runs=campaign["runs"][1:])),
                          ("v3_campaign_id", dict(campaign_id="paired-write_conflict"))):
            with self.subTest(key=key), self.assertRaises(ContractError):
                v3.validate_registered_campaign(dict(campaign, **edit))

    def test_permit_order_and_communication_plan_select_pilot_on_for_s2_only(self):
        from orchestration_permit import steps
        from upgrade_communication_plan import registration_plan
        value = v3.validate_registration(self.reg())
        order = steps(value)
        self.assertEqual(len(order), 33)
        self.assertEqual([s["group"] for s in order[:3]], ["pilot-on"] * 3)
        self.assertLessEqual({s["group"] for s in order}, set(value["groups"]))
        plan = registration_plan(value)
        self.assertEqual(plan["total_samples"], 192)
        self.assertNotIn("pilot-on", {s["group"] for s in plan["samples"]})
        self.assertEqual({s["arm"] for s in plan["samples"]}, {"B"})

    def test_v3_keeps_pilot_b_and_a1_mac_native(self):
        self.assertEqual(v3.pilot_group(dict(schema=v3.REGISTRATION_V3)), "pilot-B")
        self.assertEqual(v3.ARM_STRATEGIES, {"A1": "mac_native", "B": "omo"})
        groups, _ = v3.layout(self.base / "v3")
        self.assertEqual({g["arm"] for g in groups.values()}, {"A1", "B"})

    def test_runtime_observation_checks_the_s2_policy(self):
        from upgrade_startup_contract import RESPONSE_OBSERVER_PIN
        value = v3.validate_registration(self.reg())
        plain, observed = self.base / "startup.json", self.base / "startup2.json"
        plain.write_text(json.dumps(dict(pins={})))
        observed.write_text(json.dumps(dict(pins={RESPONSE_OBSERVER_PIN: dict(sha256="e" * 64, bytes=9)})))
        result = v3.runtime_observation(value, "rt", dict(startup=file_ref(plain)))
        self.assertEqual(result["policy"]["mode"], "disabled")
        with self.assertRaises(ContractError):
            v3.runtime_observation(value, "rt", dict(startup=file_ref(observed)))


if __name__ == "__main__":
    unittest.main()
