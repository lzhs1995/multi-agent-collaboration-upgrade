"""Pure executor-binding helpers: no coordinator wiring, no model, no network."""
import copy
import tempfile
import unittest

import jev_advice
from orchestration_binding import (BINDING_FIELDS, BIND_PREDICATES, DECISION_FIELDS, DECISION_SCHEMA, POLICY,
                                   SCHEMA, UNBOUND, advice_inputs_unchanged, binding_absent, binding_path,
                                   binding_ref, bound_lane, claimed_lanes, decision_matches_run_node,
                                   decision_route_recomputed, decision_work_kind_known, eligible_candidates,
                                   is_unbound, lane_facts, lane_idle_write_domain, lane_is_author, lane_verified,
                                   node_is_unbound, read_binding, resolved_mode, resolved_transport, unbound_nodes,
                                   unbound_plan, validate_decision, write_binding)
from orchestration_common import ContractError, file_ref, write_json
from orchestration_test_support import CampaignFixture


class BindingHelperTests(unittest.TestCase):
    def fixture(self, *, extra_unbound=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        f = CampaignFixture(directory.name, strategies=("omo",), version=3, arm="B")
        f.plan["unbound_executor_policy"] = POLICY
        f.plan["nodes"].append(self.unbound_node(f, "unbound"))
        if extra_unbound:
            f.plan["nodes"].append(self.unbound_node(f, "unbound-b"))
        f.campaign["plan"] = write_json(f.root / "plan.json", f.plan)
        f.save()
        return f

    @staticmethod
    def unbound_node(f, key):
        node = copy.deepcopy(f.plan["nodes"][0])
        node.update(id=key, write_scope=key, depends_on=["review"], executor=None,
                    execution_kind=None, transport=None)
        return node

    def value(self, f, co, lane, **changes):
        base = dict(schema=SCHEMA, campaign=f.ref, run_id=co.spec["run_id"], node_id="unbound",
                    lane=lane, execution_kind=resolved_mode(f.campaign, lane),
                    transport=resolved_transport(f.campaign, lane), mode=resolved_mode(f.campaign, lane),
                    arm=f.campaign.get("arm"), decision=None, advice=None, eligible_lanes=[lane],
                    lane_facts={}, bound_at="2026-10-29T00:00:00+00:00", bound_native=None,
                    input_units=f.campaign["inputs"], supervisor_session=f.campaign["sessions"]["codex"]["session_id"])
        base.update(changes)
        return base

    def decision(self, co, **changes):
        base = dict(schema=DECISION_SCHEMA, run_id=co.spec["run_id"], node_id="unbound", work_kind="review",
                    advice_sha256=None, actual_route="claude_a", action="accepted",
                    reason="the supervisor chose this lane", decided_at="2026-10-29T00:00:00+00:00",
                    supervisor_session="codex-session")
        base.update(changes)
        return base

    def test_unbound_plan_and_unbound_nodes_read_the_explicit_opt_in(self):
        f = self.fixture(extra_unbound=True)
        self.assertTrue(unbound_plan(f.plan))
        self.assertEqual(unbound_nodes(f.plan), ["unbound", "unbound-b"])
        without = copy.deepcopy(f.plan)
        del without["unbound_executor_policy"]
        self.assertFalse(unbound_plan(without))
        self.assertFalse(unbound_plan(dict(without, unbound_executor_policy="bind_before_claim_v2")))

    def test_node_is_unbound_reads_the_executor_only(self):
        f = self.fixture()
        self.assertTrue(node_is_unbound(f.plan["nodes"][-1]))
        self.assertTrue(is_unbound(f.plan["nodes"][-1]))
        self.assertFalse(node_is_unbound(f.plan["nodes"][0]))
        self.assertFalse(is_unbound(f.plan["nodes"][0]))

    def test_bound_lane_prefers_the_plan_then_the_claim_pin_then_the_file(self):
        f = self.fixture()
        co = f.start()
        self.assertEqual(bound_lane(co, "local"), "codex")
        self.assertEqual(bound_lane(co, "review"), "claude_a")
        self.assertIsNone(bound_lane(co, "unbound"))
        write_binding(co, "unbound", self.value(f, co, "claude_a"))
        self.assertEqual(bound_lane(co, "unbound"), "claude_a")
        other = write_json(co.root / "nodes" / "unbound" / "alternate-binding.json",
                           self.value(f, co, "claude_b"))
        self.assertEqual(bound_lane(co, "unbound", {"executor_binding": other}), "claude_b")
        self.assertEqual(bound_lane(co, "unbound", {}), "claude_a")

    def test_bound_lane_is_none_for_an_unbound_node_without_a_binding(self):
        f = self.fixture()
        co = f.start()
        self.assertTrue(binding_absent(co.root, "unbound"))
        self.assertIsNone(read_binding(co.root, "unbound"))
        self.assertIsNone(bound_lane(co, "unbound"))
        self.assertEqual(claimed_lanes(co, {}),
                         {"local": "codex", "review": "claude_a", "parallel": "claude_b",
                          "followup": "claude_a", "closeout": "codex", "unbound": None})

    def test_claimed_lanes_reads_frozen_claim_records_only(self):
        f = self.fixture()
        co = f.start()
        pin = write_binding(co, "unbound", self.value(f, co, "claude_b"))
        # A binding written after an earlier claim must not change that claim's
        # lane: the pin in the claim record is the only source.
        self.assertEqual(claimed_lanes(co, {"unbound": {}})["unbound"], "claude_b")
        self.assertEqual(claimed_lanes(co, {"unbound": {"executor_binding": pin}})["unbound"], "claude_b")

    def test_binding_fields_are_exact(self):
        f = self.fixture()
        co = f.start()
        for change in ({"extra": 1}, None):
            value = self.value(f, co, "claude_a")
            if change:
                value.update(change)
            else:
                del value["arm"]
            with self.subTest(change=change), self.assertRaisesRegex(ContractError, "binding fields changed"):
                write_binding(co, "unbound", value)
        with self.assertRaisesRegex(ContractError, "binding schema changed"):
            write_binding(co, "unbound", self.value(f, co, "claude_a", schema="other-v1"))
        self.assertTrue(binding_absent(co.root, "unbound"))
        write_binding(co, "unbound", self.value(f, co, "claude_a"))
        self.assertEqual(set(read_binding(co.root, "unbound")), BINDING_FIELDS)

    def test_binding_is_write_once(self):
        f = self.fixture()
        co = f.start()
        ref = write_binding(co, "unbound", self.value(f, co, "claude_a"))
        self.assertEqual(ref, binding_ref(co.root, "unbound"))
        with self.assertRaisesRegex(ContractError, "binding exists; observe only"):
            write_binding(co, "unbound", self.value(f, co, "claude_b"))
        self.assertEqual(file_ref(binding_path(co.root, "unbound")), ref)
        self.assertEqual(read_binding(co.root, "unbound")["lane"], "claude_a")

    def test_an_edited_binding_fails_its_pin(self):
        f = self.fixture()
        co = f.start()
        pin = write_binding(co, "unbound", self.value(f, co, "claude_a"))
        binding_path(co.root, "unbound").unlink()
        write_json(binding_path(co.root, "unbound"), self.value(f, co, "claude_b"))
        with self.assertRaisesRegex(ContractError, "hash/size mismatch"):
            bound_lane(co, "unbound", {"executor_binding": pin})

    def test_resolved_mode_and_transport_come_from_the_lane_provider(self):
        f = self.fixture()
        self.assertEqual((resolved_mode(f.campaign, "codex"), resolved_transport(f.campaign, "codex")),
                         ("local_cli", "codex_cli"))
        for lane in ("claude_a", "claude_b"):
            self.assertEqual((resolved_mode(f.campaign, lane), resolved_transport(f.campaign, lane)),
                             ("delegated_cli", "claude_code_cli"))
        with self.assertRaisesRegex(ContractError, "not an original campaign session"):
            resolved_mode(f.campaign, "claude_c")

    def test_lane_verified_is_all_or_nothing_over_the_lane_set(self):
        f = self.fixture()
        sessions = f.campaign["sessions"]
        identity = {lane: {"session_id": s["session_id"]} for lane, s in sessions.items()}
        for lane in sessions:
            self.assertTrue(lane_verified(lane, identity, sessions))
        partial = {lane: value for lane, value in identity.items() if lane != "claude_b"}
        for lane in sessions:
            with self.subTest(lane=lane, case="missing lane"):
                self.assertFalse(lane_verified(lane, partial, sessions))
        changed = copy.deepcopy(identity)
        changed["claude_b"]["session_id"] = "retargeted"
        for lane in sessions:
            with self.subTest(lane=lane, case="one lane changed"):
                self.assertFalse(lane_verified(lane, changed, sessions))
        self.assertFalse(lane_verified("claude_c", identity, sessions))
        self.assertFalse(lane_verified("codex", None, sessions))

    def test_lane_idle_write_domain_matches_what_reserve_would_refuse(self):
        f = self.fixture()
        sessions = f.campaign["sessions"]
        held = {"task": dict(session_id=sessions["claude_a"]["session_id"], scope="/work/other",
                             claim={}, reserved_at="2026-10-29T00:00:00+00:00",
                             released_at=None, released_by=None)}
        released = {"task": dict(held["task"], released_at="2026-10-29T00:01:00+00:00",
                                 released_by={"path": "/x"})}
        self.assertTrue(lane_idle_write_domain("claude_a", {}, sessions, ()))
        with self.subTest(case="unreleased lease on that session"):
            self.assertFalse(lane_idle_write_domain("claude_a", held, sessions, ()))
            self.assertTrue(lane_idle_write_domain("claude_b", held, sessions, ()))
        with self.subTest(case="released lease no longer holds the lane"):
            self.assertTrue(lane_idle_write_domain("claude_a", released, sessions, ()))
        with self.subTest(case="conflicting write scope"):
            scoped = {"task": dict(held["task"], session_id="unrelated", scope="/work/unbound")}
            self.assertFalse(lane_idle_write_domain("claude_a", scoped, sessions, (),
                                                    scope="/work/unbound", scope_root="/"))
            self.assertTrue(lane_idle_write_domain("claude_a", scoped, sessions, (),
                                                   scope="/work/elsewhere", scope_root="/"))
        with self.subTest(case="active node already resolves to that lane"):
            self.assertFalse(lane_idle_write_domain("claude_a", {}, sessions, ("claude_a",)))
        self.assertFalse(lane_idle_write_domain("claude_c", {}, sessions, ()))

    def test_lane_is_author_walks_the_whole_dependency_closure(self):
        f = self.fixture()
        lanes = {"local": "codex", "review": "claude_a", "parallel": "claude_b",
                 "followup": "claude_a", "closeout": "codex"}
        # unbound depends_on review (claude_a); closeout depends on the whole
        # graph, so every lane is an author of it through the closure.
        self.assertTrue(lane_is_author(f.plan, "unbound", "claude_a", lanes))
        self.assertFalse(lane_is_author(f.plan, "unbound", "claude_b", lanes))
        self.assertFalse(lane_is_author(f.plan, "unbound", "codex", lanes))
        for lane in ("codex", "claude_a", "claude_b"):
            with self.subTest(lane=lane):
                self.assertTrue(lane_is_author(f.plan, "closeout", lane, lanes))
        self.assertFalse(lane_is_author(f.plan, "local", "codex", lanes))
        # Transitivity, not just the direct parents: followup -> review.
        deep = copy.deepcopy(f.plan)
        deep["nodes"][-1]["depends_on"] = ["followup"]
        self.assertTrue(lane_is_author(deep, "unbound", "claude_a", lanes))
        # Only the closure counts: with no node resolved to it, a lane that
        # appears nowhere in the mapping is not an author.
        self.assertFalse(lane_is_author(deep, "unbound", "claude_a", {}))
        self.assertFalse(lane_is_author(deep, "unbound", "claude_a", {"closeout": "claude_a"}))
        with self.assertRaisesRegex(ContractError, "unknown frozen node"):
            lane_is_author(f.plan, "absent", "codex", lanes)

    def test_lane_facts_measure_kind_verified_idle_and_author(self):
        f = self.fixture()
        co = f.start()
        states, data = co.state()
        facts = lane_facts(co, "unbound", f.campaign["sessions"], states, data)
        self.assertEqual(sorted(facts), ["claude_a", "claude_b", "codex"])
        self.assertEqual({lane: value["kind"] for lane, value in facts.items()},
                         {"codex": "codex", "claude_a": "claude", "claude_b": "claude"})
        self.assertTrue(all(value["verified"] is True for value in facts.values()))
        self.assertTrue(all(value["idle_write_domain"] is True for value in facts.values()))
        self.assertEqual({lane: value["author"] for lane, value in facts.items()},
                         {"codex": False, "claude_a": True, "claude_b": False})
        co.claim("review")
        states, data = co.state()
        busy = lane_facts(co, "unbound", f.campaign["sessions"], states, data)
        self.assertFalse(busy["claude_a"]["idle_write_domain"])
        self.assertTrue(busy["claude_b"]["idle_write_domain"])

    def test_eligible_candidates_prefer_codex_then_claude_for_coding(self):
        f = self.fixture()
        co = f.start()
        states, data = co.state()
        identity = f.campaign["sessions"]
        for kind in ("coding", "debugging"):
            with self.subTest(kind=kind):
                self.assertEqual(eligible_candidates(co, "unbound", identity, states, data, work_kind=kind),
                                 ("codex",))
        co.claim("local")
        states, data = co.state()
        for kind in ("coding", "debugging"):
            with self.subTest(kind=kind, case="codex busy"):
                # The author restriction is scoped to review/literature, so the
                # coding fallback still offers the author lane (design Q6).
                self.assertEqual(eligible_candidates(co, "unbound", identity, states, data, work_kind=kind),
                                 ("claude_a", "claude_b"))

    def test_eligible_candidates_exclude_the_author_for_review_and_literature(self):
        f = self.fixture()
        co = f.start()
        states, data = co.state()
        identity = f.campaign["sessions"]
        for kind in ("review", "literature"):
            with self.subTest(kind=kind):
                # claude_a authored the dependency; codex is never offered for
                # review/literature, exactly as the S0 truth table decides.
                self.assertEqual(eligible_candidates(co, "unbound", identity, states, data, work_kind=kind),
                                 ("claude_b",))

    def test_eligible_candidates_use_the_same_predicate_as_route_advice(self):
        f = self.fixture()
        co = f.start()
        states, data = co.state()
        identity = f.campaign["sessions"]
        facts = lane_facts(co, "unbound", identity, states, data)
        for kind in jev_advice.WORK_KINDS:
            with self.subTest(kind=kind):
                advice = jev_advice.route_advice(bound=False, lanes=facts,
                                                 answers={"work_kind": kind, "work_kind_applies": 1.0})
                self.assertEqual(eligible_candidates(co, "unbound", identity, states, data, work_kind=kind),
                                 tuple(advice["candidates"]))

    def test_eligible_candidates_refuse_an_unknown_work_kind(self):
        f = self.fixture()
        co = f.start()
        states, data = co.state()
        for kind in (None, "", "coding ", "architecture"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ContractError, "work kind must be one of"):
                eligible_candidates(co, "unbound", f.campaign["sessions"], states, data, work_kind=kind)

    def test_validate_decision_requires_its_exact_fields_run_and_node(self):
        f = self.fixture()
        co = f.start()
        good = write_json(co.root / "decision.json", self.decision(co))
        self.assertEqual(set(validate_decision(good, co.spec["run_id"], "unbound")), DECISION_FIELDS)
        cases = {
            "fields changed": [dict(extra="x"), "drop:work_kind", "drop:reason"],
            "schema changed": [dict(schema="orchestration-bind-decision-v2")],
            "another run or frozen node": [dict(run_id="run-99"), dict(node_id="review")],
            "must name the actual route": [dict(actual_route=None), dict(actual_route="")],
            "advice pin must be a sha256": [dict(advice_sha256="abc"), dict(advice_sha256=64 * "z" + "z")],
            "supervisor's reason": [dict(reason="  "), dict(reason=None)],
        }
        for expected, variants in cases.items():
            for index, change in enumerate(variants):
                value = self.decision(co)
                if isinstance(change, str) and change.startswith("drop:"):
                    del value[change.split(":", 1)[1]]
                else:
                    value.update(change)
                ref = write_json(co.root / f"decision-{expected[:6]}-{index}.json", value)
                with self.subTest(expected=expected, change=change), \
                        self.assertRaisesRegex(ContractError, expected):
                    validate_decision(ref, co.spec["run_id"], "unbound")

    def test_decision_work_kind_must_be_a_known_work_kind(self):
        f = self.fixture()
        co = f.start()
        for kind in jev_advice.WORK_KINDS:
            self.assertTrue(decision_work_kind_known({"work_kind": kind}))
        for kind in (None, "", "architecture", "Coding"):
            with self.subTest(kind=kind):
                self.assertFalse(decision_work_kind_known({"work_kind": kind}))
        self.assertFalse(decision_work_kind_known({}))
        ref = write_json(co.root / "unknown-kind.json", self.decision(co, work_kind="architecture"))
        with self.assertRaisesRegex(ContractError, "work_kind is missing or not a frozen S0 work kind"):
            validate_decision(ref, co.spec["run_id"], "unbound")

    def test_decision_matches_run_node_and_route_recompute_are_named(self):
        f = self.fixture()
        co = f.start()
        decision = self.decision(co)
        self.assertTrue(decision_matches_run_node(decision, co.spec["run_id"], "unbound"))
        self.assertFalse(decision_matches_run_node(decision, "run-02", "unbound"))
        self.assertFalse(decision_matches_run_node(decision, co.spec["run_id"], "review"))
        self.assertTrue(decision_route_recomputed(decision, ("claude_a", "claude_b")))
        self.assertFalse(decision_route_recomputed(decision, ("claude_b",)))
        self.assertFalse(decision_route_recomputed(decision, ()))
        self.assertFalse(decision_route_recomputed(decision, None))

    def test_advice_inputs_unchanged_compares_the_recorded_input_units(self):
        f = self.fixture()
        co = f.start()
        run_id = co.spec["run_id"]
        units = copy.deepcopy(f.campaign["inputs"])
        on_arm = self.decision(co, advice_sha256=64 * "a")
        advice = dict(run_id=run_id, input_units=units)
        self.assertTrue(advice_inputs_unchanged(on_arm, advice, f.campaign, run_id))
        changed_sha = copy.deepcopy(units)
        changed_sha[0]["sha256"] = 64 * "b"
        changed_bytes = copy.deepcopy(units)
        changed_bytes[0]["bytes"] = changed_bytes[0]["bytes"] + 1
        added = units + [dict(units[0], path=units[0]["path"] + ".copy")]
        for case, value in (("changed sha256", changed_sha), ("changed byte count", changed_bytes),
                            ("added unit", added), ("removed unit", []),
                            ("missing key", None), ("not a list", {})):
            with self.subTest(case=case):
                self.assertFalse(advice_inputs_unchanged(on_arm, dict(run_id=run_id, input_units=value),
                                                         f.campaign, run_id))
        with self.subTest(case="advice from another run"):
            self.assertFalse(advice_inputs_unchanged(on_arm, dict(run_id="run-99", input_units=units),
                                                     f.campaign, run_id))
        with self.subTest(case="no advice body at all on the on arm"):
            self.assertFalse(advice_inputs_unchanged(on_arm, None, f.campaign, run_id))
        with self.subTest(case="off arm reads nothing else"):
            off_arm = self.decision(co, advice_sha256=None)
            self.assertTrue(advice_inputs_unchanged(off_arm, None, f.campaign, run_id))
        with self.subTest(case="a run with no frozen inputs is never unchanged"):
            self.assertFalse(advice_inputs_unchanged(off_arm, None, dict(f.campaign, inputs=[]), run_id))

    def test_every_bind_predicate_is_a_named_module_level_callable(self):
        self.assertEqual(set(BIND_PREDICATES), {
            "lane_verified", "lane_idle_write_domain", "lane_is_author", "decision_route_recomputed",
            "decision_matches_run_node", "node_is_unbound", "binding_absent",
            "decision_work_kind_known", "advice_inputs_unchanged"})
        import orchestration_binding
        for name, function in BIND_PREDICATES.items():
            with self.subTest(name=name):
                self.assertIs(getattr(orchestration_binding, name), function)
                self.assertEqual(function.__module__, "orchestration_binding")
                self.assertIsNotNone(function.__doc__)

    def test_shared_matcher_predicates_are_not_in_this_registry(self):
        for name in ("valid_command", "calls_and_results", "codex_stdout", "exact_body_output"):
            with self.subTest(name=name):
                self.assertNotIn(name, BIND_PREDICATES)

    def test_the_unbound_sentinel_is_the_decision_provider_fact(self):
        from decision_provider import UNBOUND_PENDING_BIND
        self.assertEqual(UNBOUND, UNBOUND_PENDING_BIND)


if __name__ == "__main__":
    unittest.main()
