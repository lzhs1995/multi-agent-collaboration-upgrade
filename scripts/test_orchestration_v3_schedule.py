"""Fair native choices and broad reservations with immutable per-node outputs."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_campaign import node_output, node_write_scope, validate_campaign
from orchestration_common import ContractError, file_ref, write_json
from orchestration_coordinator import Coordinator, State, STRATEGIES, select_ready, strategy_probe
from orchestration_planner import PlanError, validate_plan
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture


class NativeSchedulingTests(unittest.TestCase):
    def fixture(self, arm="B", strategy="omo", version=3):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CampaignFixture(directory.name, strategies=(strategy,), version=version, arm=arm)

    def save(self, f):
        f.campaign["plan"] = write_json(f.root / "plan.json", f.plan)
        f.save()

    def completed_callback(self, f, co, key):
        info = co.claim(key)
        f.prepare(co, key)
        f.intent(co, key)
        f.dispatch(co, key)
        f.work(info, co.nodes[key]["executor"])
        return info, f.callback(co, key)

    def test_native_choices_do_not_suppress_alternatives_but_claim_rechecks_conflicts(self):
        f = self.fixture("A1", "mac_native")
        alternate = copy.deepcopy(f.plan["nodes"][1])
        alternate.update(id="alternative", write_scope="alternative")
        f.plan["nodes"].append(alternate)
        self.save(f)
        co = f.start()
        self.assertEqual({n["id"] for n in co.ready()}, {"local", "review", "parallel", "alternative"})
        co.claim("alternative")
        with self.assertRaisesRegex(ContractError, "not ready"):
            co.claim("review")
        co.claim("parallel")
        self.assertEqual({n["id"] for n in co.ready()}, {"local"})
        self.assertEqual(len([v for v in co.leases.read().values() if v["released_at"] is None]), 2)
        states = {key: State.PENDING for key in co.nodes}
        self.assertNotIn("alternative", {n["id"] for n in select_ready(f.plan, states, "omo", co.root / "work", co.header["queues"])})

    def test_native_strategy_is_new_format_only_and_old_probe_is_unchanged(self):
        f = self.fixture(version=2)
        self.assertEqual(tuple(strategy_probe(f.plan)["strategies"]), STRATEGIES)
        f.campaign["runs"][0]["strategy"] = "mac_native"
        f.save()
        with self.assertRaisesRegex(ContractError, "run specification"):
            validate_campaign(f.ref, fixtures=True)
        with self.assertRaisesRegex(ContractError, "unsupported strategy"):
            select_ready(f.plan, {n["id"]: State.PENDING for n in f.plan["nodes"]}, "mac_native", f.root, {})

    def test_a1_preserves_callback_and_candidate_without_upgrade_continuation(self):
        f = self.fixture("A1", "mac_native")
        co = f.start()
        _, protocol = self.completed_callback(f, co, "review")
        runner = Runner(f.ref, fixtures=True)
        with patch.object(runner, "_protocol", return_value=protocol):
            result = runner.candidate(co.spec["run_id"], "review")
        self.assertEqual(result["state"], "EVIDENCE_READY")
        self.assertFalse((co.root / "nodes/review/continuation.json").exists())
        for name, args in (("drafts", ("run-01",)), ("publish_drafts", ("run-01",)),
                           ("next_action", ("run-01",)), ("publish_continuation", ("run-01", "review"))):
            with self.subTest(name=name), self.assertRaisesRegex(ContractError, "B-only"):
                getattr(runner, name)(*args)

    def test_shared_reservation_releases_after_acceptance_and_keeps_prior_outputs(self):
        f = self.fixture()
        f.plan["nodes"] = [n for n in f.plan["nodes"] if n["id"] in ("review", "parallel")]
        for node in f.plan["nodes"]:
            node.update(write_scope="shared", output_scope="shared/" + node["id"])
        self.save(f)
        co = f.start()
        info, protocol = self.completed_callback(f, co, "review")
        lease = co._lease("review", info)
        self.assertEqual(lease["scope"], str(co.root / "work/shared"))
        self.assertEqual(info["output_root"], str(co.root / "work/shared/review"))
        with self.assertRaisesRegex(ContractError, "not ready"):
            co.claim("parallel")
        original = file_ref(Path(info["output_root"]) / "result.json")
        evidence = f.candidate(co, "review", protocol)
        # A restart between terminal acceptance and release repairs the same
        # broad reservation; it must not create a new claim or output tree.
        with patch.object(co.leases, "release", side_effect=RuntimeError("release crash")):
            with self.assertRaisesRegex(RuntimeError, "release crash"):
                f.accept(co, "review", evidence)
        restarted = Coordinator(co.root, f.ref, fixtures=True)
        self.assertEqual(restarted.repair_releases(), ["review"])
        second, other_protocol = self.completed_callback(f, restarted, "parallel")
        f.accept(restarted, "parallel", f.candidate(restarted, "parallel", other_protocol))
        self.assertEqual(file_ref(original["path"]), original)
        self.assertEqual(second["output_root"], str(co.root / "work/shared/parallel"))
        self.assertTrue(all(v["released_at"] is not None for v in co.leases.read().values()))

    def test_advice_checks_broad_scope_against_other_run_reservations(self):
        f = self.fixture()
        node = f.plan["nodes"][1]
        node.update(write_scope="shared", output_scope="shared/review")
        self.save(f)
        co = f.start()
        foreign = write_json(f.root / "external-claim.json", {"original": True})
        co.leases.reserve("external-owner", "external-session", str(co.root / "work/shared/other"), foreign)
        result = Runner(f.ref, fixtures=True).next_action("run-01")
        self.assertNotIn("review", result["recorded_claim_candidates"])
        row = next(r for r in result["nodes"] if r["node_id"] == "review")
        self.assertEqual(row["recommendation"]["action"], "wait-original-lease")
        self.assertEqual(row["recommendation"]["lease_owners"], ["external-owner"])

    def test_output_scope_rejects_escape_noncanonical_and_legacy_schema(self):
        f = self.fixture()
        for value in ("../elsewhere", "elsewhere", "/tmp/output", "shared//child", "shared/./child", ".", " shared/child"):
            plan = copy.deepcopy(f.plan)
            plan["nodes"][0].update(write_scope="shared", output_scope=value)
            with self.subTest(value=value), self.assertRaises(PlanError):
                validate_plan(plan)
        legacy = self.fixture(version=2).plan
        legacy["nodes"][0]["output_scope"] = "local/child"
        with self.assertRaisesRegex(PlanError, "version 2"):
            validate_plan(legacy)
        self.assertEqual(node_output(f.root, f.plan["nodes"][0]), node_write_scope(f.root, f.plan["nodes"][0]))


if __name__ == "__main__":
    unittest.main()
