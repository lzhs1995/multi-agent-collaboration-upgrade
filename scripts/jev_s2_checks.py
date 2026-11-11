"""Prospective S2 structural checks only, not native authentication or acceptance.

No network, dispatch, process startup, receipt publication or production imports.
Passing these checks never proves actual advice consumption: owner43 must bind
the fields to original native records using the existing independent readers.

Ported from ROOT's R32 checker (`jev_s2_checks.py` sha256
`b6e531161a82af777719405a907504133f88a0c49e7fb058adf2318990b437c5`). The only
change is that the labels R32 wrote as literals now come from the separately
frozen registration (design Q44): the advisor's `provider` and `model`, and the
two arm labels. Every comparison is still an exact equality against a frozen
value, so a trace that named a different advisor or arm is refused exactly as
before. A registration is required: without one there is nothing to compare
against, and that is an `EvidenceError`, never a default.
"""
from __future__ import annotations

import math
import statistics

REGISTRATION_FIELDS = ("provider", "model", "off_arm", "on_arm")


class EvidenceError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def registered(registration):
    """The frozen labels this checker compares against; never invented here."""
    require(isinstance(registration, dict), "frozen registration required; this checker invents no labels")
    for field in REGISTRATION_FIELDS:
        require(isinstance(registration.get(field), str) and registration[field],
                "registration must name " + field)
    require(registration["off_arm"] != registration["on_arm"], "the two registered arms must differ")
    return {field: registration[field] for field in REGISTRATION_FIELDS}


def number(value, field):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            field + " must be finite numeric nonnegative evidence")
    return value


def consumption_join(trace, *, registration=None):
    """Check joins and event ordering in a proposed prebinding consumer trace.

    Native reference authentication is intentionally outside this pure checker.
    The caller cannot turn self-authored timestamps or SHA labels into proof.
    """
    pinned = registered(registration)
    require(isinstance(trace, dict) and isinstance(trace.get("run_id"), str) and trace["run_id"],
            "nonempty original run identity required")
    require(trace.get("mode") == pinned["on_arm"], "shadow/off is not actual on-arm consumption")
    request, advice, read, decision, binding = (
        trace.get(key) for key in ("request", "advice", "read", "decision", "binding")
    )
    require(all(isinstance(item, dict) for item in (request, advice, read, decision, binding)),
            "on-arm requires request, advice, complete read, decision and new binding")
    require(request.get("status") == "HTTP_200", "failed inference must not become a semantic negative")
    require(advice.get("provider") == pinned["provider"] and advice.get("model") == request.get("model")
            == pinned["model"], "actual provider/model provenance required")
    for key in ("input_sha256", "questions_sha256", "request_sha256"):
        require(isinstance(request.get(key), str) and len(request[key]) == 64
                and set(request[key]) <= set("0123456789abcdef"), "invalid " + key)
        require(advice.get(key) == request[key], "advice " + key + " does not match request")
    for item in (request, advice, read, decision, binding):
        require(item.get("run_id") == trace.get("run_id"), "cross-run evidence join")
    advice_sha = advice.get("advice_sha256")
    require(isinstance(advice_sha, str) and len(advice_sha) == 64
            and set(advice_sha) <= set("0123456789abcdef"), "invalid advice digest")
    require(read.get("advice_sha256") == advice_sha and decision.get("advice_sha256") == advice_sha,
            "read/decision must reference the exact same advice")
    require(read.get("complete") is True, "partial/notice-only reading is not full advice consumption")
    require(read.get("actor_session") == decision.get("actor_session") == binding.get("supervisor_session")
            and isinstance(read.get("actor_session"), str) and read["actor_session"],
            "the original supervisor must read and decide")
    for key in ("task_id", "write_scope_sha256"):
        require(decision.get(key) == binding.get(key) and isinstance(decision.get(key), str)
                and decision[key], "bound task/write scope changed")
    require(binding.get("new_binding") is True, "existing tasks may not be rebound through JEV")
    times = [number(item.get(field), field) for item, field in (
        (request, "finished_at"), (advice, "finished_at"), (read, "read_at"),
        (decision, "decided_at"), (binding, "bound_at"),
    )]
    require(times == sorted(times) and times[-2] < times[-1],
            "advice read/decision must precede fresh task binding")
    require(decision.get("action") in ("accepted", "rejected", "supervisor_review"),
            "explicit advice adoption/rejection is required")
    require(isinstance(decision.get("reason"), str) and decision["reason"].strip(),
            "record why advice was adopted or rejected")
    route = decision.get("actual_route")
    eligible = binding.get("eligible_lanes")
    require(isinstance(eligible, list) and all(isinstance(lane, str) and lane for lane in eligible)
            and route == binding.get("actual_route") and route in eligible,
            "actual route must preserve the code-verified eligible write domain")
    if decision["action"] == "accepted":
        require(route == advice.get("route"), "accepted advice differs from the actual route")
    return {"structural_join": "VALID", "native_authenticated": False,
            "runtime_consumption_proven": False, "upgrade_accepted": False}


def estimate_jev_increment(pairs, expected_pair_ids, *, registration=None):
    """Report paired full-wall-time effects without dropping failed observations.

    The expected IDs come from the separately frozen registration, not observed
    successful rows. Caller still has to authenticate registration and each row.
    Wall time covers the whole closed run; overlapping tool/component times are
    disclosed separately, never naively added to wall time.
    """
    pinned = registered(registration)
    require(isinstance(pairs, list) and isinstance(expected_pair_ids, list) and expected_pair_ids
            and all(isinstance(item, str) and item for item in expected_pair_ids)
            and len(expected_pair_ids) == len(set(expected_pair_ids)), "unique frozen denominator required")
    ids = [item.get("pair_id") for item in pairs if isinstance(item, dict)]
    require(len(ids) == len(pairs) and all(isinstance(item, str) and item for item in ids)
            and len(ids) == len(set(ids)) and set(ids) == set(expected_pair_ids),
            "missing, duplicate or extra pairs cannot change the frozen denominator")
    effects, cold_effects, by_workload, unresolved = [], [], {}, []
    observed_warm = expected_warm = 0
    disclosed_increases = []
    invariants = ("phase", "workload", "input_sha256", "main_models", "strategy", "resource_policy_sha256",
                  "response_observer_sha256", "write_scope_policy_sha256")
    for pair in pairs:
        off, on = pair.get("off"), pair.get("on")
        require(isinstance(off, dict) and isinstance(on, dict), "both registered arms are required")
        require(off.get("mode") == pinned["off_arm"] and on.get("mode") == pinned["on_arm"],
                "shadow is not the actual JEV treatment")
        for key in invariants:
            require(key in off and key in on and off[key] == on[key],
                    "JEV-only attribution confounded by " + key)
        require(off["phase"] in ("warm", "cold"), "explicit warm/cold phase required")
        expected_warm += off["phase"] == "warm"
        require(type(off.get("model_requests")) is int and off["model_requests"] == 0,
                "off arm must not silently query JEV")
        require(on.get("advice_consumption_authenticated") is True,
                "on arm lacks separately authenticated native advice consumption")
        # This flag is an upstream verifier's input, never generated here.
        if any(row.get("outcome") != "SUCCESS" or row.get("full_elapsed_seconds") is None
               or row.get("all_costs_observed") is not True for row in (off, on)):
            unresolved.append(pair["pair_id"])
            continue
        baseline = number(off["full_elapsed_seconds"], "off full elapsed")
        candidate = number(on["full_elapsed_seconds"], "on full elapsed")
        require(baseline > 0 and candidate >= 0, "zero baseline is percentage-ineligible")
        effect = 1 - candidate / baseline
        if off["phase"] == "warm":
            effects.append(effect)
            observed_warm += 1
            by_workload.setdefault(off["workload"], []).append(effect)
        else:
            cold_effects.append(effect)
        require(type(on.get("human_prompts")) is int and on["human_prompts"] == 0,
                "candidate human prompts violate the registered gate")
        for key in ("human_interventions", "total_main_child_tokens"):
            require(type(off.get(key)) is int and type(on.get(key)) is int
                    and off[key] >= 0 and on[key] >= 0, "missing full " + key)
        require(on["human_interventions"] <= off["human_interventions"], "human intervention regression")
        old_cost, new_cost = off.get("total_cost_usd"), on.get("total_cost_usd")
        if old_cost is None or new_cost is None:
            unresolved.append(pair["pair_id"])
        else:
            old_cost, new_cost = number(old_cost, "off cost"), number(new_cost, "on cost")
            require(old_cost >= 0 and new_cost >= 0, "negative cost")
            if (old_cost == 0 and new_cost > 0) or (old_cost > 0 and new_cost / old_cost > 1.10):
                disclosed_increases.append(pair["pair_id"])
    medians = {key: statistics.median(values) for key, values in by_workload.items()}
    median = statistics.median(effects) if effects else None
    complete = not unresolved and observed_warm == expected_warm and bool(effects)
    numerically_eligible = complete and median >= 0.15 - 1e-12 and all(v >= -0.05 - 1e-12 for v in medians.values())
    return {"registered_pairs": len(expected_pair_ids), "observed_pairs": len(pairs),
            "registered_warm_pairs": expected_warm, "observed_warm_pairs": observed_warm,
            "cold_paired_improvements_disclosed_separately": cold_effects,
            "performance_incomplete_pairs": sorted(set(unresolved)), "median_paired_improvement": median,
            "workload_paired_medians": medians, "cost_increase_over_10_percent_or_zero_base": disclosed_increases,
            "numeric_gate_only": numerically_eligible, "native_authenticated_by_this_checker": False,
            "upgrade_accepted": False}
