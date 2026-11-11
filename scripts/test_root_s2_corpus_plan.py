"""ROOT private NR1f checks: graph declarations only, no native/API evidence."""
from __future__ import annotations

import copy
from pathlib import Path
import unittest

import orchestration_binding as binding
import orchestration_freeze_v3 as freezer
from orchestration_planner import validate_plan
from upgrade_benchmark import CLASSES, REGISTRATION_V3
from upgrade_jev_benchmark import SCHEMA as JEV_SCHEMA


class RegisteredCorpusPlanTests(unittest.TestCase):
    input_root = Path("/tmp/declared-only-inputs-not-native-evidence").resolve()

    def plan(self, kind, *, schema=JEV_SCHEMA, advisory="advisory-on"):
        return freezer.registered_corpus_plan(
            self.input_root, "same-business-revision", kind,
            dict(schema=schema, advisory=advisory))

    def test_v3_graph_remains_exactly_unchanged(self):
        for kind in CLASSES:
            with self.subTest(kind=kind):
                original = freezer.corpus_plan(self.input_root, "same-business-revision", kind)
                self.assertEqual(self.plan(kind, schema=REGISTRATION_V3), original)
                self.assertFalse(binding.unbound_plan(original))
                self.assertEqual(binding.unbound_nodes(original), [])

    def test_every_s2_class_declares_real_graph_nodes_for_existing_binding(self):
        for kind in CLASSES:
            with self.subTest(kind=kind):
                plan = self.plan(kind)
                self.assertEqual(validate_plan(plan), plan)
                self.assertTrue(binding.unbound_plan(plan))
                self.assertEqual(binding.unbound_nodes(plan)[:2],
                                 ["inventory", "source_index" if kind == "uneven_successor" else "verify_inputs"])
                for node in plan["nodes"]:
                    if node["id"] in binding.unbound_nodes(plan):
                        self.assertEqual([node[k] for k in ("executor", "execution_kind", "transport")],
                                         [None, None, None])

    def test_on_and_off_have_identical_business_graph_and_policy(self):
        for kind in CLASSES:
            with self.subTest(kind=kind):
                self.assertEqual(self.plan(kind, advisory="advisory-off"), self.plan(kind))

    def test_no_business_source_dependency_scope_command_or_model_policy_changed(self):
        for kind in CLASSES:
            with self.subTest(kind=kind):
                old = freezer.corpus_plan(self.input_root, "same-business-revision", kind)
                new = copy.deepcopy(self.plan(kind))
                new["plan_id"] = old["plan_id"]
                new.pop("unbound_executor_policy")
                for before, after in zip(old["nodes"], new["nodes"]):
                    for key in ("executor", "execution_kind", "transport"):
                        after[key] = before[key]
                self.assertEqual(new, old)

    def test_final_independent_codex_merge_remains_bound(self):
        for kind in CLASSES:
            with self.subTest(kind=kind):
                nodes = [n for n in self.plan(kind)["nodes"] if n["id"].startswith("merge_")]
                self.assertEqual(len(nodes), 1)
                self.assertEqual((nodes[0]["executor"], nodes[0]["execution_kind"], nodes[0]["transport"]),
                                 ("codex", "local_cli", "codex_cli"))
                self.assertNotIn(nodes[0]["id"], binding.unbound_nodes(self.plan(kind)))

    def test_write_conflict_retains_shared_lease_and_separate_output_domains(self):
        nodes = {n["id"]: n for n in self.plan("write_conflict")["nodes"]}
        for key in ("inventory", "verify_inputs"):
            self.assertEqual((nodes[key]["write_scope"], nodes[key]["output_scope"]),
                             ("shared", "shared/" + key))

    def test_binding_policy_cannot_be_removed_or_partial_null_triple_claimed(self):
        plan = self.plan("fan_in")
        for mutate in (lambda p: p.pop("unbound_executor_policy"),
                       lambda p: p["nodes"][0].update(transport="claude_code_cli")):
            bad = copy.deepcopy(plan)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_plan(bad)


if __name__ == "__main__":
    unittest.main()
