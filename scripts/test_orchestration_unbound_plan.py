"""A deliberately unbound frozen node: plan opt-in and campaign refusal.

Every PlanError text is asserted against validate_plan directly, because
@contract_errors rewrites non-ContractError exceptions at the validate_campaign
boundary and would hide the plan-level wording.
"""
import copy
import tempfile
import unittest

from orchestration_campaign import validate_campaign
from orchestration_common import ContractError, json_ref, write_json
from orchestration_planner import PlanError, UNBOUND_EXECUTOR_POLICY, build_schedule, validate_plan
from orchestration_test_support import CampaignFixture


def v2_plan():
    """A minimal valid v2 plan: two bound lanes and one dependent node."""
    nodes = [("intake", "codex", []), ("review", "claude_a", ["intake"])]
    plan = dict(version=2, plan_id="unbound-fixture", objective="Bind one deliberately unbound node",
                source={"generated_by": "supervisor", "revision": "fixture"}, decision_provider="rules",
                executor_providers={"codex": "codex", "claude_a": "claude", "claude_b": "claude"},
                api_policy={"third_party_api": False, "model_call_owners": ["codex_cli", "claude_code_cli"]},
                nodes=[])
    for key, lane, deps in nodes:
        plan["nodes"].append(node_value(key, lane, deps))
    return plan


def node_value(key, lane, deps):
    provider = "codex" if lane == "codex" else "claude"
    return dict(id=key, title=key, executor=lane, depends_on=list(deps),
                execution_kind="local_cli" if provider == "codex" else "delegated_cli",
                transport="codex_cli" if provider == "codex" else "claude_code_cli",
                session_policy="reuse_existing", context="Inspect the input",
                source=["revision:fixture"], scope="Write assigned output",
                forbidden="No APIs or replacements", verify="Run the pinned checker",
                write_scope=key, expected_seconds=1, deliverables=["result.json", "report.md"],
                report="report.md")


def unbound_node(key="unbound", deps=()):
    node = node_value(key, "codex", deps)
    node.update(executor=None, execution_kind=None, transport=None)
    return node


class UnboundPlanTests(unittest.TestCase):
    def campaign_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CampaignFixture(directory.name, strategies=("omo",), version=3, arm="B")

    @staticmethod
    def save(f):
        f.campaign["plan"] = write_json(f.root / "plan.json", f.plan)
        f.save()

    def test_unbound_executor_requires_the_named_opt_in(self):
        plan = v2_plan()
        plan["nodes"] = [unbound_node("intake")]
        with self.assertRaises(PlanError) as refusal:
            validate_plan(copy.deepcopy(plan))
        self.assertEqual(str(refusal.exception), "nodes[0].executor must be a non-empty string")
        plan["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
        accepted = validate_plan(copy.deepcopy(plan))
        self.assertIsNone(accepted["nodes"][0]["executor"])
        self.assertIsNone(accepted["nodes"][0]["execution_kind"])
        self.assertIsNone(accepted["nodes"][0]["transport"])

    def test_opt_in_requires_the_complete_null_triple(self):
        for field, value in (("execution_kind", "local_cli"), ("transport", "codex_cli"),
                             ("execution_kind", "delegated_cli")):
            plan = v2_plan()
            plan["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
            node = unbound_node()
            node[field] = value
            plan["nodes"].append(node)
            with self.subTest(field=field, value=value), self.assertRaises(PlanError) as refusal:
                validate_plan(plan)
            self.assertEqual(str(refusal.exception), "unbound must freeze a complete null executor triple")

    def test_an_omitted_triple_member_is_not_an_opt_in_shape(self):
        for omitted in ("execution_kind", "transport"):
            plan = v2_plan()
            plan["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
            node = unbound_node()
            del node[omitted]
            plan["nodes"].append(node)
            with self.subTest(omitted=omitted), self.assertRaises(PlanError) as refusal:
                validate_plan(plan)
            self.assertEqual(str(refusal.exception), "unbound must freeze a complete null executor triple")

    def test_opt_in_value_must_be_exact_and_v2_only(self):
        plan = v2_plan()
        plan["unbound_executor_policy"] = "bind_before_claim "
        with self.assertRaises(PlanError) as refusal:
            validate_plan(copy.deepcopy(plan))
        self.assertEqual(str(refusal.exception), "unbound_executor_policy must be exactly bind_before_claim")
        plan["unbound_executor_policy"] = None
        with self.assertRaises(PlanError):
            validate_plan(copy.deepcopy(plan))
        v1 = dict(plan, version=1, unbound_executor_policy=UNBOUND_EXECUTOR_POLICY)
        del v1["executor_providers"]
        for node in v1["nodes"]:
            node["executor"] = "codex" if node["executor"] == "codex" else "claude"
            node["transport"] = "codex_cli" if node["executor"] == "codex" else "claude_code_cli"
        with self.assertRaises(PlanError) as refusal:
            validate_plan(v1)
        self.assertEqual(str(refusal.exception), "unbound_executor_policy requires plan version 2")

    def test_bound_nodes_keep_their_derived_transport(self):
        plan = v2_plan()
        plan["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
        plan["nodes"].append(unbound_node())
        plan["nodes"][1]["transport"] = "codex_cli"
        with self.assertRaises(PlanError) as refusal:
            validate_plan(plan)
        self.assertEqual(str(refusal.exception), "review.transport must be claude_code_cli")

    def test_campaign_refuses_a_frozen_execution_kind_on_an_unbound_node(self):
        f = self.campaign_fixture()
        node = unbound_node(deps=())
        node["write_scope"] = "unbound"
        node["execution_kind"] = "local_cli"
        f.plan["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
        f.plan["nodes"].append(node)
        self.save(f)
        # Layer one: a mixed triple never reaches the campaign loop, because
        # validate_campaign normalizes through validate_plan first and
        # @contract_errors rewrites the PlanError at that boundary.
        with self.assertRaisesRegex(ContractError, r"malformed or unavailable evidence \(PlanError\)"):
            validate_campaign(f.ref, fixtures=True)
        # Layer two: the complete null triple is what the campaign now accepts.
        # Before this commit the same bytes failed "unsupported execution
        # ownership", because lane None matched neither frozen pairing.
        f.plan["nodes"][-1]["execution_kind"] = None
        self.save(f)
        campaign = validate_campaign(f.ref, fixtures=True)
        self.assertEqual(campaign["plan"], f.campaign["plan"])
        frozen = validate_plan(json_ref(campaign["plan"]))
        self.assertEqual([n["id"] for n in frozen["nodes"] if n["executor"] is None], ["unbound"])

    def test_plan_error_text_is_asserted_against_validate_plan_directly(self):
        plan = v2_plan()
        plan["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
        node = unbound_node()
        node["transport"] = "codex_cli"
        plan["nodes"].append(node)
        with self.assertRaises(PlanError) as refusal:
            validate_plan(copy.deepcopy(plan))
        self.assertEqual(str(refusal.exception), "unbound must freeze a complete null executor triple")
        f = self.campaign_fixture()
        f.plan = plan
        self.save(f)
        with self.assertRaises(ContractError) as rewritten:
            validate_campaign(f.ref, fixtures=True)
        self.assertNotIn("null executor triple", str(rewritten.exception))

    def test_build_schedule_refuses_an_unbound_graph(self):
        plan = v2_plan()
        plan["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
        plan["nodes"].append(unbound_node())
        with self.assertRaises(PlanError) as refusal:
            build_schedule(plan)
        self.assertEqual(str(refusal.exception), "cannot schedule deliberately unbound node(s): unbound")
        bound = v2_plan()
        bound["unbound_executor_policy"] = UNBOUND_EXECUTOR_POLICY
        self.assertEqual([wave["nodes"] for wave in build_schedule(bound)], [["intake"], ["review"]])


if __name__ == "__main__":
    unittest.main()
