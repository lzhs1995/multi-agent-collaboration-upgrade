"""Minimal OFFLINE synthetic S2 plan fixture, so plan-shaped controls run without the private evidence path.

The design block is derived from the frozen v3 layout itself, never copied or
relabeled from the original plan file: upgrade_jev_benchmark.layout reads only
design.pairs projections, design.warm_pairs, design.cold_pairs and design.pilot.
Where the original plan is present, synthetic_design_matches_frozen_plan proves
the derivation equals it, so the fixture cannot drift from the frozen design.
Nothing here changes formal S2 inputs, denominators or the original plan bytes.
"""
import json
import tempfile
import unittest
from pathlib import Path

import upgrade_benchmark as v3
import upgrade_jev_benchmark as jb
from orchestration_common import ContractError, file_ref

SCHEMA = "u2078-s2-offline-synthetic-plan-fixture-v1"
PILOT_RUNS = 3


def synthetic_design(root=None):
    """design derived from the tested v3 layout, in the S2 advisory naming."""
    with tempfile.TemporaryDirectory(prefix="s2planshape-") as tmp:
        # macOS /var is a symlink, so the shape root must be resolved before checked_path.
        groups, pairs = v3.layout(Path(root or tmp).resolve() / "shape", compact=False)
    names = {name: (name.replace("-A1", "-off").replace("-B", "-on") if name != "pilot-B" else jb.PILOT)
             for name in groups}
    design_pairs = [dict(pair_id="jev-" + p["pair_id"], phase=p["phase"], workload_class=p["workload_class"],
                         order=[jb.ADVISORY[arm] for arm in p["order"]]) for p in pairs]
    return dict(classes=list(v3.CLASSES), order_rule="frozen v3 order per pair",
                pairs=design_pairs,
                warm_pairs=sum(p["phase"] == "warm" for p in design_pairs),
                cold_pairs=sum(p["phase"] == "cold" for p in design_pairs),
                pilot=dict(runs=PILOT_RUNS, arm=jb.ADVISORY[groups["pilot-B"]["arm"]],
                           require_consecutive_passes=PILOT_RUNS, counted_in_pairs=False,
                           note="OFFLINE synthetic fixture; the pilot is not part of the paired denominator"))


def write_plan(directory, *, design=None):
    """Write the fixture plan and return its file_ref, as the real validators consume it."""
    path = Path(directory) / "OFFLINE-SYNTHETIC-S2-PLAN.json"
    path.write_text(json.dumps(dict(schema=SCHEMA, origin="OFFLINE test fixture, never native proof or HUMAN",
                                    scope="plan shape only; no business input, measurement or acceptance",
                                    design=design or synthetic_design(directory)), ensure_ascii=False))
    return file_ref(path)


def plan_ref(directory, *, prefer=None):
    """The original frozen plan when it is present, else the offline fixture."""
    if prefer is not None and Path(prefer).exists():
        return file_ref(Path(prefer))
    return write_plan(directory)


# Exactly the design fields upgrade_jev_benchmark.layout enforces.
CONSUMED = ("pairs", "warm_pairs", "cold_pairs")
CONSUMED_PILOT = ("runs", "arm")


def consumed_design(design):
    value = {key: design[key] for key in CONSUMED}
    value["pilot"] = {key: design["pilot"][key] for key in CONSUMED_PILOT}
    return value


class OfflinePlanFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="s2planfixture-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_real_layout_accepts_the_offline_fixture(self):
        groups, pairs = jb.layout(self.base / "registration", write_plan(self.base), compact=False)
        self.assertEqual((len(groups), len(pairs)), (7, 15))
        self.assertEqual(sum(p["phase"] == "warm" for p in pairs), 12)

    def test_real_layout_refuses_a_changed_fixture_design(self):
        design = synthetic_design(self.base)
        design["warm_pairs"] += 1
        with self.assertRaisesRegex(ContractError, "warm/cold counts differ"):
            jb.layout(self.base / "registration", write_plan(self.base, design=design), compact=False)

    def test_fixture_equals_the_frozen_plan_wherever_layout_reads_it(self):
        from test_upgrade_jev_benchmark import PLAN
        if not PLAN.exists():
            self.skipTest("frozen S2 plan is not available; equivalence is checked where it is")
        frozen = json.loads(PLAN.read_text(encoding="utf-8"))["design"]
        self.assertEqual(consumed_design(synthetic_design(self.base)), consumed_design(frozen))
