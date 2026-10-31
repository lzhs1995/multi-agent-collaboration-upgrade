"""Separate JEV S2 paired registration (upgrade-jev-paired-registration-v1, R514).

The frozen S2 plan keeps the v3 alternation and slots but changes only the
advisory switch: the A1 position becomes advisory-off, the B position
advisory-on, and both arms run strategy omo. The pairs are derived from the
tested v3 ``layout`` and must equal the pinned plan field by field. Original
v3 registrations, roots, ids, denominators and failures are never relabeled or
read as S2. Each pair's two arms take the same input, policies and models, and
every class must have DP1 and DP2 opportunities before a formal pilot.
"""
from __future__ import annotations

from pathlib import Path

import upgrade_benchmark as v3
from orchestration_common import ContractError, file_ref, json_ref, read_ref, strict_json, utc_now, write_json

SCHEMA = "upgrade-jev-paired-registration-v1"
STRATEGY = "omo"
ADVISORY = {"A1": "advisory-off", "B": "advisory-on"}
FACTOR_KEYS = ("input_sha256", "policies", "codex_model", "claude_model")
POLICY_KEYS = ("resource", "response_observer", "write_scope")
FIELDS = {"schema", "benchmark_id", "root", "created_at", "plan", "compact", "groups", "pairs", "factors",
          "opportunities"}


def require(value, message):
    if not value:
        raise ContractError("JEV_PAIRED_REGISTRATION: " + message)


def _plan(plan_ref):
    plan = strict_json(read_ref(plan_ref, "S2 plan"), "S2 plan")
    require(isinstance(plan, dict) and isinstance(plan.get("design"), dict), "S2 plan design required")
    return plan


def layout(root, plan_ref, *, compact=True):
    """Groups and pairs from the tested v3 layout, renamed to the S2 advisory arms and checked against the plan."""
    design = _plan(plan_ref)["design"]
    base_groups, base_pairs = v3.layout(root, compact=compact)
    names = {name: (name.replace("-A1", "-off").replace("-B", "-on") if name != "pilot-B" else "pilot-on")
             for name in base_groups}
    groups = {}
    for name, group in base_groups.items():
        advisory = ADVISORY[group["arm"]]
        groups[names[name]] = dict(group, advisory=advisory, strategy=STRATEGY, transport=None,
                                   campaign_id="jev-" + group["campaign_id"],
                                   runs=[dict(run, pair_id=None if run["pair_id"] is None else "jev-" + run["pair_id"])
                                         for run in group["runs"]])
    pairs = []
    for pair in base_pairs:
        members = {ADVISORY[arm]: dict(group=names[member["group"]], run_id=member["run_id"])
                   for arm, member in pair["members"].items()}
        pairs.append(dict(pair_id="jev-" + pair["pair_id"], phase=pair["phase"], workload_class=pair["workload_class"],
                          order=[ADVISORY[arm] for arm in pair["order"]], members=members))
    project = lambda p: {k: p[k] for k in ("pair_id", "phase", "workload_class", "order")}
    require([project(p) for p in pairs] == [project(p) for p in design["pairs"]], "pairs differ from the frozen S2 plan")
    require(sum(p["phase"] == "warm" for p in pairs) == design["warm_pairs"]
            and sum(p["phase"] == "cold" for p in pairs) == design["cold_pairs"], "warm/cold counts differ from the plan")
    pilot = groups["pilot-on"]
    require(pilot["advisory"] == design["pilot"]["arm"] and len(pilot["runs"]) == design["pilot"]["runs"]
            and all(run["pair_id"] is None for run in pilot["runs"]), "pilot differs from the plan")
    return groups, pairs


def check_factors(pairs, factors):
    """Both arms of a pair take the same business input, three policies and models (plan fixed_factors)."""
    require(isinstance(factors, dict) and set(factors) == {p["pair_id"] for p in pairs}, "one factor set per pair required")
    for pair in pairs:
        value = factors[pair["pair_id"]]
        require(isinstance(value, dict) and set(value) == set(ADVISORY.values()), "both arms required: " + pair["pair_id"])
        off, on = value["advisory-off"], value["advisory-on"]
        for arm in (off, on):
            require(isinstance(arm, dict) and set(arm) == set(FACTOR_KEYS)
                    and isinstance(arm["policies"], dict) and set(arm["policies"]) == set(POLICY_KEYS),
                    "factor fields changed: " + pair["pair_id"])
        require(off == on, "arms differ in input, policy or model: " + pair["pair_id"])


def check_opportunities(opportunities):
    """Every class has DP1 unbound and DP2 semantic inbox opportunities; zero-opportunity workloads are refused."""
    require(isinstance(opportunities, dict) and set(opportunities) == set(v3.CLASSES), "opportunities per class required")
    for kind, value in opportunities.items():
        require(isinstance(value, dict) and set(value) == {"dp1", "dp2"}
                and all(type(value[k]) is int and value[k] > 0 for k in ("dp1", "dp2")),
                "zero or missing DP1/DP2 opportunities: " + kind)


def register(root, benchmark_id, plan_path, *, factors, opportunities, compact=True):
    from upgrade_experiment import private_root
    from orchestration_planner import ID_RE
    root = private_root(root)
    require(isinstance(benchmark_id, str) and ID_RE.fullmatch(benchmark_id), "invalid benchmark ID")
    require(not root.exists(), "registration root exists; preserve the original registration")
    require(type(compact) is bool, "compact flag must be boolean")
    plan_ref = file_ref(Path(plan_path))
    groups, pairs = layout(root, plan_ref, compact=compact)
    check_factors(pairs, factors)
    check_opportunities(opportunities)
    if compact:
        from upgrade_client_isolation import client_temp_base, control_socket
        for group in groups.values():
            paths = {"tmp": Path(group["context_path"]).parent / "tmp" / group["arm"]}
            control_socket(paths)
            for lane in ("claude_a", "claude_b"):
                client_temp_base(paths, lane)
    value = dict(schema=SCHEMA, benchmark_id=benchmark_id, root=str(root), created_at=utc_now(), plan=plan_ref,
                 compact=compact, groups=groups, pairs=pairs, factors=factors, opportunities=opportunities)
    root.mkdir(parents=True, mode=0o700)
    return write_json(root / "registration.json", value, exclusive=True)


def validate_registration(ref):
    value = json_ref(ref, "JEV paired registration")
    require(value.get("schema") == SCHEMA, "not an S2 registration; v3 data is never relabeled")
    require(set(value) == FIELDS, "registration fields changed")
    groups, pairs = layout(value["root"], value["plan"], compact=value["compact"])
    require(value["groups"] == groups and value["pairs"] == pairs, "groups or pairs differ from the frozen S2 layout")
    require(all(g["strategy"] == STRATEGY and g["transport"] is None for g in value["groups"].values()),
            "both arms must be omo and no group may carry a transport before its per-run grant")
    check_factors(value["pairs"], value["factors"])
    check_opportunities(value["opportunities"])
    return value
