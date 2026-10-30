"""Resolve which original CLI lane a deliberately unbound frozen node uses.

A frozen plan node may carry the complete null triple
(``executor``/``execution_kind``/``transport`` all ``null``) when its plan opts
in with ``unbound_executor_policy``. A separate pre-claim step binds such a node
by writing one exclusive binding record, and the claim pins that record. Every
later consumer resolves the lane through :func:`bound_lane` so claim, lease,
transport and verification can never disagree.

This module is deliberately named ``orchestration_*`` so live maintenance
re-reads its exact frozen bytes (``orchestration_maintenance.bound_source``
filters on that prefix): it decides which session a claim reserves.

Every judgment this module decides is a named module-level callable registered
in :data:`BIND_PREDICATES`, so each one can be excised, flipped and restored
individually. Callers reach them through the module attribute, never through a
name imported at import time, because an imported name is a second reference
the excision could not replace. The schema, field-set, type and non-empty
``require`` calls that frame those predicates (for example in
:func:`check_decision` and :func:`read_binding`) are record-shape checks, not
judgments about a lane, a route or a plan, and are not registered. Nothing in
this module measures time, reads an environment variable, or asks any model:
all inputs arrive as arguments or as pinned file references.
"""
from __future__ import annotations

from pathlib import Path

from orchestration_common import (ContractError, checked_path, file_ref, json_ref, require, scopes_conflict,
                                  write_json)
from orchestration_planner import UNBOUND_EXECUTOR_POLICY, executor_providers
import jev_advice


SCHEMA = "orchestration-executor-binding-v1"
DECISION_SCHEMA = "orchestration-bind-decision-v1"
UNBOUND = "UNBOUND_PENDING_BIND"
POLICY = UNBOUND_EXECUTOR_POLICY
BINDING_FIELDS = {"schema", "campaign", "run_id", "node_id", "lane", "execution_kind",
                  "transport", "mode", "arm", "decision", "advice", "eligible_lanes",
                  "lane_facts", "bound_at", "bound_native", "input_units", "supervisor_session"}
DECISION_FIELDS = {"schema", "run_id", "node_id", "work_kind", "advice_sha256", "actual_route",
                   "action", "reason", "decided_at", "supervisor_session"}
REF_FIELDS = ("path", "bytes", "sha256")
# R62 consumer.new_entrypoint.checks[6] names an "actual context" fact that no
# producer anywhere in this repository measures (design Conflict C9, confirmed
# by ROOT R94 section 2). It is recorded as this literal, never invented.
CONTEXT_FACT = "NOT_IMPLEMENTED_NO_PRODUCER"


def unbound_plan(plan) -> bool:
    """True only for a plan that explicitly opted in to unbound nodes."""
    return plan.get("unbound_executor_policy") == POLICY


def unbound_claim_started(root, node_id, node) -> bool:
    """A claim of this unbound node began and left an artefact behind.

    For a bound node the node directory exists only once a claim created it,
    so "the directory exists" is a sound incomplete-claim test there. An
    unbound node's directory is created before any claim: ``publish-decision``
    and ``bind-unbound`` both take ``nodes/<id>/runner.lock``, and the worker's
    advice and journal live there too. Those are pre-claim artefacts and must
    not read as an incomplete claim, which would otherwise suppress every
    claim candidate in the run from the moment the decision is published.

    Only artefacts a claim itself creates count: the claim intent, its gate
    record, its work request, or its output directory.
    """
    from orchestration_campaign import node_output
    root = Path(root)
    return any(path.exists() or path.is_symlink() for path in (
        root / "claims" / (node_id + ".json"),
        root / "nodes" / node_id / "claim-gates.json",
        root / "nodes" / node_id / "work-request.json",
        node_output(root, node)))


def node_is_unbound(node) -> bool:
    """The frozen node deliberately carries no executor."""
    return node.get("executor") is None


def is_unbound(node) -> bool:
    """Call the named predicate through the module so excision reaches it."""
    return globals()["node_is_unbound"](node)


def unbound_nodes(plan) -> list[str]:
    """Ids of the deliberately unbound nodes, in the plan's topological order."""
    from orchestration_coordinator import topological_nodes
    return [node["id"] for node in topological_nodes(plan) if is_unbound(node)]


def binding_path(root, node_id) -> Path:
    return Path(root) / "bindings" / (node_id + ".json")


def binding_absent(root, node_id) -> bool:
    """No binding record exists yet for this node."""
    return not binding_path(root, node_id).exists()


def binding_ref(root, node_id) -> dict:
    return file_ref(binding_path(root, node_id))


def read_binding(root, node_id) -> dict | None:
    """The frozen binding record, or None when the node is not bound yet."""
    if globals()["binding_absent"](root, node_id):
        return None
    value = json_ref(binding_ref(root, node_id), "original executor binding")
    require(set(value) == BINDING_FIELDS, "executor binding fields changed")
    require(value["schema"] == SCHEMA, "executor binding schema changed")
    require(isinstance(value["lane"], str) and value["lane"], "executor binding lane missing")
    return value


def bound_lane(co, node_id, info=None) -> str | None:
    """The lane this node actually uses: the plan, else the claim's own pin.

    A claim pins its binding by path+bytes+sha256, so replay resolves the same
    lane from frozen bytes forever. The binding *file* is deliberately not a
    fallback here when a claim exists: a record edited after the claim must fail
    the pin instead of silently re-routing an earlier claim.
    """
    node = co.nodes[node_id]
    if not is_unbound(node):
        return node["executor"]
    if isinstance(info, dict) and "executor_binding" in info:
        value = json_ref(info["executor_binding"], "claimed executor binding")
        require(set(value) == BINDING_FIELDS and value["schema"] == SCHEMA,
                "claimed executor binding fields changed")
        return value["lane"]
    binding = read_binding(co.root, node_id)
    return binding["lane"] if binding else None


def claim_lane(node, info) -> str:
    """The lane a claimed node uses, from the frozen node or the claim's own pin.

    For consumers that hold the frozen node and its claim data but no
    coordinator (evidence, transport, timeline, preparation, maintenance). A
    bound node returns ``node["executor"]`` exactly as before; an unbound node
    must carry the claim's ``executor_binding`` pin, which is re-read by
    path+bytes+sha256 so an edited binding refuses instead of re-routing.
    """
    if not is_unbound(node):
        return node["executor"]
    require(isinstance(info, dict) and isinstance(info.get("executor_binding"), dict),
            "an unbound node is used only through its claimed executor binding")
    value = json_ref(info["executor_binding"], "claimed executor binding")
    require(set(value) == BINDING_FIELDS and value["schema"] == SCHEMA,
            "claimed executor binding fields changed")
    require(value["node_id"] == node["id"], "claimed executor binding belongs to another frozen node")
    return value["lane"]


def claimed_lanes(co, data) -> dict[str, str | None]:
    """Lane per node resolved from frozen claim records only.

    ``data`` is the replay's accumulated claim data, so a binding written later
    can never retroactively change an earlier claim's gate context. A bound
    node keeps its frozen executor; an unbound node has a lane here only once
    its own claim pins a binding. The binding *file* is never consulted: a
    pending node bound after an earlier claim would otherwise make that earlier
    claim's replayed selection differ from the selection it actually passed.
    """
    return {key: (node["executor"] if not is_unbound(node)
                  else claim_lane(node, data[key]) if "executor_binding" in (data.get(key) or {})
                  else None)
            for key, node in co.nodes.items()}


def claim_mode_matches_frozen_kind(node, info, mode) -> bool:
    """The claim's mode against the frozen plan, conditional on the null triple.

    A bound node keeps today's chained equality verbatim. An unbound node froze
    ``execution_kind: null`` by design, so it requires exactly that null and a
    claim mode equal to the mode resolved from its bound lane.
    """
    if is_unbound(node):
        return node.get("execution_kind") is None and info.get("mode") == mode
    return info["mode"] == node["execution_kind"] == mode


def resolved_mode(campaign, lane) -> str:
    """local_cli for the Codex lane, delegated_cli for a Claude lane."""
    return "local_cli" if _provider(campaign, lane) == "codex" else "delegated_cli"


def resolved_transport(campaign, lane) -> str:
    return {"codex": "codex_cli", "claude": "claude_code_cli"}[_provider(campaign, lane)]


def _provider(campaign, lane) -> str:
    session = (campaign.get("sessions") or {}).get(lane)
    require(isinstance(session, dict), "lane is not an original campaign session")
    provider = session.get("provider")
    require(provider in ("codex", "claude"), "lane provider is not an original CLI provider")
    return provider


def lane_verified(lane, identity, sessions) -> bool:
    """All or nothing over the lane set, because no per-lane flag is recorded.

    ``require_live_identity`` either returns the complete observed lane set or
    refuses, so the honest reading of "verified" is: the whole set passed and
    this lane is in it. It is documented as such rather than invented per lane.
    """
    if not isinstance(identity, dict) or not isinstance(sessions, dict):
        return False
    if set(identity) != set(sessions) or lane not in sessions:
        return False
    return all(all(sessions[name].get(key) == value for key, value in actual.items())
               for name, actual in identity.items())


def lane_idle_write_domain(lane, leases, sessions, occupied_lanes, *, scope=None, scope_root="/") -> bool:
    """Exactly the condition ``LeaseRegistry.reserve`` would refuse.

    No unreleased lease holds this lane's session, no unreleased lease conflicts
    with the node's write scope, and no currently active node resolves to this
    lane. Bind-time candidates and claim-time reservation therefore cannot
    disagree (``orchestration_coordinator.py:153-155``).
    """
    session = (sessions or {}).get(lane)
    if not isinstance(session, dict) or not session.get("session_id"):
        return False
    if lane in set(occupied_lanes or ()):
        return False
    for lease in (leases or {}).values():
        if lease.get("released_at") is not None:
            continue
        if lease.get("session_id") == session["session_id"]:
            return False
        if scope is not None and scopes_conflict(lease["scope"], str(scope), scope_root):
            return False
    return True


def lane_is_author(plan, node_id, lane, lanes_by_node) -> bool:
    """The lane resolved any node in this node's transitive dependency closure.

    No recorded fact names an author, so it is derived from the frozen graph
    rather than added as a new claim field.
    """
    nodes = {node["id"]: node for node in plan["nodes"]}
    require(node_id in nodes, "unknown frozen node")
    pending, closure = list(nodes[node_id].get("depends_on") or ()), set()
    while pending:
        current = pending.pop()
        if current in closure or current not in nodes:
            continue
        closure.add(current)
        pending.extend(nodes[current].get("depends_on") or ())
    return any((lanes_by_node or {}).get(key) == lane for key in closure)


def lane_facts(co, node_id, identity, states, data) -> dict[str, dict]:
    """The ``lanes`` mapping ``jev_advice.route_advice`` consumes, measured here."""
    from orchestration_coordinator import ACTIVE, State
    from orchestration_campaign import node_write_scope
    sessions = co.campaign["sessions"]
    lanes_by_node = claimed_lanes(co, data)
    occupied = {lane for key, lane in lanes_by_node.items()
                if lane is not None and State(states[key]) in ACTIVE}
    leases = co.leases.read()
    scope = str(node_write_scope(co.root, co.nodes[node_id]))
    facts = {}
    for lane in sorted(sessions):
        facts[lane] = dict(
            kind=_provider(co.campaign, lane),
            verified=globals()["lane_verified"](lane, identity, sessions),
            idle_write_domain=globals()["lane_idle_write_domain"](
                lane, leases, sessions, occupied, scope=scope, scope_root="/"),
            author=globals()["lane_is_author"](co.plan, node_id, lane, lanes_by_node))
    return facts


def bind_facts(lanes) -> dict:
    """What a binding records about the facts code measured at bind time.

    ``lanes`` is exactly the mapping ``route_advice`` consumed, so it stays a
    separate key: a non-lane entry inside it would be read as a lane. The
    actual-context fact has no producer and is stated as such.
    """
    return dict(lanes=lanes, context=CONTEXT_FACT)


def eligible_candidates(co, node_id, identity, states, data, *, work_kind) -> tuple[str, ...]:
    """Recompute the admissible lanes in code; never read them from advice.

    The selection is ``jev_advice.route_advice``'s own branch, reached through
    that public API with a synthetic certain answer, so bind-time candidates and
    the advice truth table cannot drift apart. ``jev_advice.py`` is not edited.
    """
    require(globals()["decision_work_kind_known"]({"work_kind": work_kind}),
            "work kind must be one of the frozen S0 work kinds")
    facts = lane_facts(co, node_id, identity, states, data)
    advice = jev_advice.route_advice(
        bound=False, lanes=facts,
        answers={"work_kind": work_kind, "work_kind_applies": 1.0})
    require(advice["status"] in ("SUGGEST", "NO_IDLE_LANE"), "candidate recompute did not reach a lane decision")
    return tuple(advice["candidates"])


def decision_work_kind_known(decision) -> bool:
    """The decision names one of the frozen S0 work kinds, never a default."""
    return decision.get("work_kind") in jev_advice.WORK_KINDS


def decision_matches_run_node(decision, run_id, node_id) -> bool:
    """The decision was authored for exactly this run and this frozen node."""
    return decision.get("run_id") == run_id and decision.get("node_id") == node_id


def decision_route_recomputed(decision, candidates) -> bool:
    """The actual route came from the candidates recomputed at binding time."""
    return decision.get("actual_route") in tuple(candidates or ())


def validate_decision(ref, run_id, node_id) -> dict:
    """Read the supervisor's decision file; refuse before anything is written."""
    return check_decision(json_ref(ref, "original bind decision"), run_id, node_id)


def check_decision(decision, run_id, node_id) -> dict:
    """The same checks on an in-memory value, so a publisher refuses before writing."""
    require(isinstance(decision, dict), "bind decision must be a JSON object")
    require(set(decision) == DECISION_FIELDS, "bind decision fields changed")
    require(decision["schema"] == DECISION_SCHEMA, "bind decision schema changed")
    require(globals()["decision_matches_run_node"](decision, run_id, node_id),
            "bind decision belongs to another run or frozen node")
    require(globals()["decision_work_kind_known"](decision),
            "bind decision work_kind is missing or not a frozen S0 work kind")
    require(isinstance(decision["actual_route"], str) and decision["actual_route"],
            "bind decision must name the actual route")
    require(decision["advice_sha256"] is None or
            (isinstance(decision["advice_sha256"], str) and len(decision["advice_sha256"]) == 64),
            "bind decision advice pin must be a sha256 or null")
    require(isinstance(decision["reason"], str) and decision["reason"].strip(),
            "bind decision must carry the supervisor's reason")
    return decision


def advice_inputs_unchanged(decision, advice, campaign, run_id) -> bool:
    """The input version did not change between advice generation and binding.

    R62 ``consumer.new_entrypoint.checks[6]``, first half. The on arm compares
    the input refs recorded inside the sha-pinned advice body against the run's
    frozen ``campaign["inputs"]``, element-wise on path+bytes+sha256. On the off
    arm there is no advice to compare, so the run's own frozen inputs are the
    only input version and the predicate reads nothing else.
    """
    frozen = campaign.get("inputs")
    if not isinstance(frozen, list) or not frozen:
        return False
    if decision.get("advice_sha256") is None:
        return True
    if not isinstance(advice, dict):
        return False
    if advice.get("run_id") != run_id:
        return False
    units = advice.get("input_units")
    if not isinstance(units, list) or len(units) != len(frozen):
        return False
    return all(isinstance(unit, dict) and all(unit.get(key) == item.get(key) for key in REF_FIELDS)
               for unit, item in zip(units, frozen))


def write_binding(co, node_id, value) -> dict:
    """Exclusively create the binding record; it is never rewritten."""
    require(set(value) == BINDING_FIELDS, "executor binding fields changed")
    require(value["schema"] == SCHEMA, "executor binding schema changed")
    try:
        return write_json(binding_path(co.root, node_id), value, exclusive=True)
    except FileExistsError as exc:
        raise ContractError("original executor binding exists; observe only") from exc


BIND_PREDICATES = {"unbound_plan": unbound_plan, "unbound_claim_started": unbound_claim_started,
                   "lane_verified": lane_verified, "lane_idle_write_domain": lane_idle_write_domain,
                   "lane_is_author": lane_is_author, "decision_route_recomputed": decision_route_recomputed,
                   "decision_matches_run_node": decision_matches_run_node,
                   "node_is_unbound": node_is_unbound, "binding_absent": binding_absent,
                   "decision_work_kind_known": decision_work_kind_known,
                   "advice_inputs_unchanged": advice_inputs_unchanged,
                   "claim_mode_matches_frozen_kind": claim_mode_matches_frozen_kind}
