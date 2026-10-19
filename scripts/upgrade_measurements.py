"""Native accounting and complete paired reporting; unknown is never zero."""
from __future__ import annotations

import math
import statistics
from pathlib import Path

from orchestration_common import ContractError, file_ref, json_ref, parse_time, read_ref, require
from orchestration_native import NativeReader
from orchestration_permit import elapsed, steps
from upgrade_benchmark import (CLASSES, REGISTRATION_V3, runtime_observation, validate_registration)

CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
CLAUDE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
LIVE_MEASUREMENTS = ("registered_live_measurement_v1", "registered_live_measurement_v2")


def _counts(value, fields, optional=()):
    if not isinstance(value, dict):
        return None
    result = {k: value.get(k, 0 if k in optional else None) for k in fields}
    return result if all(type(v) is int and v >= 0 for v in result.values()) else None


def native_usage(records, provider, start, end):
    """Deduplicate native accounting records, preserving uncertain boundaries.

    Codex totals are cumulative counters, not per-record charges. Claude may
    repeat one message in several content records; only a final stop_reason
    establishes complete usage. Observed partial counts remain separately named.
    """
    refs, problems = [], []
    if provider == "codex":
        baseline = {k: 0 for k in CODEX_FIELDS} if start == 0 else None
        last, latest_offset, generated_offset = baseline, -1, -1
        for ref, record in records:
            offset = ref["offset"]
            if offset >= end:
                break
            p = record.get("payload", {})
            if offset >= start and record.get("type") == "response_item" and (
                    p.get("type") in ("function_call", "custom_tool_call") or
                    p.get("type") == "message" and p.get("role") == "assistant"):
                generated_offset = offset
            if record.get("type") != "event_msg" or p.get("type") != "token_count":
                continue
            value = _counts((p.get("info") or {}).get("total_token_usage"), CODEX_FIELDS)
            if value is None:
                if offset >= start:
                    problems.append("missing_native_token_counter")
                continue
            if offset < start:
                baseline, last = value, value
                continue
            refs.append(ref)
            if last is not None and any(value[k] < last[k] for k in CODEX_FIELDS):
                problems.append("native_token_counter_reset")
            last, latest_offset = value, offset
        if baseline is None:
            problems.append("missing_counter_at_start_boundary")
        if generated_offset > latest_offset:
            problems.append("generation_without_final_accounting_at_end_boundary")
        observed = ({k: last[k] - baseline[k] for k in CODEX_FIELDS}
                    if baseline is not None and last is not None and all(last[k] >= baseline[k] for k in CODEX_FIELDS) else None)
        if observed is not None and observed["total_tokens"] != observed["input_tokens"] + observed["output_tokens"]:
            problems.append("inconsistent_total_token_semantics")
    elif provider == "claude":
        prior, messages = set(), {}
        for ref, record in records:
            offset = ref["offset"]
            if offset >= end:
                break
            if record.get("type") != "assistant" or record.get("isApiErrorMessage") is True:
                continue
            message = record.get("message", {})
            key = message.get("id")
            if offset < start:
                if key:
                    prior.add(key)
                continue
            refs.append(ref)
            if not isinstance(key, str) or not key:
                problems.append("missing_native_message_id")
                continue
            if key in prior:
                problems.append("message_crosses_start_boundary")
            value = _counts(message.get("usage"), CLAUDE_FIELDS,
                            optional=("cache_read_input_tokens", "cache_creation_input_tokens"))
            if value is None:
                problems.append("missing_native_message_usage")
                continue
            old = messages.get(key)
            if old is not None and any(value[k] < old["usage"][k] for k in CLAUDE_FIELDS):
                problems.append("message_usage_regressed")
            messages[key] = dict(usage=value, final=message.get("stop_reason") is not None or
                                old is not None and old["final"] and old["usage"] == value)
        if any(not m["final"] for m in messages.values()):
            problems.append("missing_final_message_accounting")
        observed = {k: sum(m["usage"][k] for m in messages.values()) for k in CLAUDE_FIELDS}
        observed["total_tokens"] = sum(observed.values())
    else:
        raise ContractError("unsupported native accounting provider")
    return dict(status="UNKNOWN" if problems or observed is None else "MEASURED",
                tokens=None if problems else observed, observed_accounting=observed,
                reasons=sorted(set(problems)), accounting_records=refs,
                semantics="native_accounting_records_in_the_pinned_interval; not an inferred provider bill")


def usage_for(campaign, header, finish, *, cold=False, fixtures=False):
    lanes = {}
    for lane, session in campaign["sessions"].items():
        reader = NativeReader(session, fixtures=fixtures)
        start = 0 if cold else reader.verify_pin(header["native_start"][lane])
        end = reader.verify_pin(finish["native_end"][lane])
        lanes[lane] = native_usage(reader.records(0, end), reader.provider, start, end)
    total = sum(v["tokens"]["total_tokens"] for v in lanes.values()) if all(v["tokens"] is not None for v in lanes.values()) else None
    return dict(tokens=total, lanes=lanes, cost=None, cost_currency=None,
                cost_reason="no_verified_provider_tariff_or_per_run_bill; subscription_price_is_not_token_cost")


def measurement(campaign_ref, run_id, *, fixtures=False):
    from orchestration_campaign import validate_campaign
    from orchestration_verify import verify_run
    campaign = validate_campaign(campaign_ref, fixtures=fixtures)
    result = verify_run(campaign_ref, run_id, fixtures=fixtures)
    root = Path(campaign["root"]) / "runs" / run_id
    header = json_ref(file_ref(root / "run.json"))
    finish = json_ref(result["finish"])
    registration = json_ref(campaign["registration"])
    step = next(s for s in steps(registration) if (s["group"], s["run_id"]) == (campaign["session_group"], run_id))
    timing = elapsed(header, end=finish["closeout_clock"])
    cap = registration["contract"]["run_timeout_seconds"]
    on_time = timing["seconds"] <= cap if timing["seconds"] is not None else None
    status = "TIMEOUT" if on_time is False else result["status"]
    observation = {}
    evidence_class = "offline_fixture" if fixtures else LIVE_MEASUREMENTS[0]
    if registration["schema"] == REGISTRATION_V3 and not fixtures:
        from upgrade_live_runtime import validate_live_runtime
        _, runtime = validate_live_runtime(campaign["runtime"])
        observation["observer_configuration"] = runtime_observation(registration, campaign["runtime"], runtime)
        evidence_class = LIVE_MEASUREMENTS[1]
    return dict(**step, arm=campaign["arm"], workload_class=campaign["workload_class"], status=status,
                contract_pass=result["contract_pass"], within_cap=on_time, elapsed_seconds=timing["seconds"],
                elapsed_wall_seconds=timing["wall_seconds"], clock_status=timing["clock_status"],
                warm_run_seconds=result["metrics"]["elapsed_seconds"],
                startup_seconds=(timing["seconds"] - result["metrics"]["elapsed_seconds"]
                                 if step["phase"] == "cold" and timing["seconds"] is not None
                                 and result["metrics"]["elapsed_seconds"] is not None else None),
                human_interventions=result["metrics"]["human_interventions"],
                retries=result["metrics"]["retries"], failed_api_attempts=result["metrics"]["failed_api_attempts"],
                finish=result["finish"], campaign=campaign_ref, run_metrics=result["metrics"],
                usage=usage_for(campaign, header, finish, cold=step["phase"] == "cold", fixtures=fixtures),
                evidence_class=evidence_class, **observation)


def next_allowed(row):
    return (row["status"] == "PASS" and row["contract_pass"] is True and row["within_cap"] is True
            and type(row["human_interventions"]) is int and row["human_interventions"] >= 0
            and row.get("arm") in ("A1", "B")
            and (row["arm"] == "A1" or row["human_interventions"] == 0)
            and row.get("evidence_class") in LIVE_MEASUREMENTS
            and (row["evidence_class"] == LIVE_MEASUREMENTS[0] or
                 isinstance(row.get("observer_configuration"), dict)))


def finite_number(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def observation_checks(registration, registration_ref, rows):
    """External report checks, with one full campaign validation per group.

    A claimed row must match the original external closeout in full; copying
    valid runtime pins does not authenticate caller-supplied timing or counters.
    This checks configuration symmetry, not actual observer activation.
    """
    from orchestration_campaign import validate_campaign
    from orchestration_permit import read_permit, schedule_path
    from upgrade_live_runtime import validate_live_runtime
    require(registration_ref is not None and validate_registration(registration_ref) == registration,
            "v3 comparison requires the pinned original registration")
    groups, checks = {}, {}
    for row in rows:
        key = (row["group"], row["run_id"])
        try:
            require(row.get("evidence_class") == LIVE_MEASUREMENTS[1], "v3 original measurement required")
            group = row["group"]
            if group not in groups:
                campaign = validate_campaign(row["campaign"])
                require(campaign["registration"] == registration_ref and campaign["session_group"] == group,
                        "observation campaign registration/group changed")
                _, runtime = validate_live_runtime(campaign["runtime"])
                groups[group] = (row["campaign"], runtime_observation(registration, campaign["runtime"], runtime))
            campaign_ref, observation = groups[group]
            require(row["campaign"] == campaign_ref and row["observer_configuration"] == observation,
                    "original measurement observer configuration changed")
            permit_ref, permit = read_permit(registration_ref, row["ordinal"])
            require(permit["runtime"] == observation["runtime"]
                    and (permit["step"]["group"], permit["step"]["run_id"]) == key,
                    "observation original permit changed")
            closure = json_ref(file_ref(schedule_path(registration, "closures", row["ordinal"])))
            require(closure.get("schema") == "upgrade-run-closure-v1"
                    and closure.get("registration") == registration_ref and closure.get("permit") == permit_ref
                    and closure.get("campaign") == campaign_ref and closure.get("finish") == row["finish"]
                    and closure.get("measurement") == row and closure.get("next_allowed") is next_allowed(row),
                    "measurement differs from original external closeout")
            read_ref(row["finish"], "original observation closeout finish")
            checks[key] = dict(verified=True, reason=None)
        except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
            checks[key] = dict(verified=False, reason=str(exc))
    return checks


def compare(registration, rows, *, registration_ref=None):
    """Include every registered slot; exploratory medians cannot qualify a run."""
    known = {(r["group"], r["run_id"]): r for r in rows}
    if len(known) != len(rows):
        raise ContractError("duplicate registered measurements")
    expected = {(s["group"], s["run_id"]) for s in steps(registration)}
    if not set(known) <= expected:
        raise ContractError("unregistered measurement")
    modern = registration.get("schema") == REGISTRATION_V3
    checks = observation_checks(registration, registration_ref, rows) if modern else {}
    def verified(row):
        if not modern:
            return row.get("evidence_class") == LIVE_MEASUREMENTS[0]
        return (row.get("evidence_class") == LIVE_MEASUREMENTS[1] and
                checks.get((row["group"], row["run_id"]), {}).get("verified") is True)
    complete = []
    for step in steps(registration):
        group = registration["groups"][step["group"]]
        row = known.get((step["group"], step["run_id"]))
        if row is None:
            row = dict(**step, arm=group["arm"], workload_class=group["workload_class"], status="MISSING",
                       contract_pass=False, within_cap=None, elapsed_seconds=None, human_interventions=None,
                       usage=dict(tokens=None, cost=None), evidence_class="not_measured")
        if any(row.get(k) != v for k, v in dict(**step, arm=group["arm"], workload_class=group["workload_class"]).items()):
            raise ContractError("measurement identity differs from registration")
        complete.append(row)
    indexed = {(r["group"], r["run_id"]): r for r in complete}
    pairs = []
    for pair in registration["pairs"]:
        members = {a: indexed[(m["group"], m["run_id"])] for a, m in pair["members"].items()}
        a, b = members["A1"], members["B"]
        comparable = all(r["status"] == "PASS" and r["contract_pass"] is True and r["within_cap"] is True
                         and verified(r)
                         and finite_number(r["elapsed_seconds"], positive=True) for r in members.values())
        improvement = 100 * (a["elapsed_seconds"] - b["elapsed_seconds"]) / a["elapsed_seconds"] if comparable else None
        ca, cb = a["usage"].get("cost"), b["usage"].get("cost")
        cost_verified = not modern or all(verified(r) for r in members.values())
        cost_increase = ((cb - ca) / ca * 100 if cost_verified and
                         finite_number(ca, positive=True) and finite_number(cb) else None)
        intervention_ok = (all(type(r["human_interventions"]) is int and r["human_interventions"] >= 0
                               for r in members.values()) and b["human_interventions"] == 0
                           and b["human_interventions"] <= a["human_interventions"])
        pairs.append(dict(pair_id=pair["pair_id"], phase=pair["phase"], workload_class=pair["workload_class"],
                          order=pair["order"], members=members, comparable=comparable,
                          intervention_contract_pass=intervention_ok,
                          improvement_percent=improvement, cost_increase_percent=cost_increase))
    warm = [p for p in pairs if p["phase"] == "warm"]
    available = [p["improvement_percent"] for p in warm if p["comparable"]]
    medians = {kind: statistics.median([p["improvement_percent"] for p in warm if p["workload_class"] == kind])
               if all(p["comparable"] for p in warm if p["workload_class"] == kind) else None for kind in CLASSES}
    full_median = statistics.median(available) if len(available) == registration["contract"]["warm_pairs"] else None
    all_verified = all(next_allowed(r) and verified(r) for r in complete)
    interventions_ok = all(p["intervention_contract_pass"] for p in pairs)
    regressions = [k for k, v in medians.items() if v is not None and v < -registration["contract"]["maximum_class_regression_percent"]]
    eligible = (all_verified and interventions_ok and all(p["comparable"] for p in pairs) and full_median is not None
                and full_median >= registration["contract"]["minimum_improvement_percent"] and not regressions)
    return dict(schema="upgrade-paired-report-v2" if modern else "upgrade-paired-report-v1",
                benchmark_id=registration["benchmark_id"],
                qualification_scope=("observer_configuration_verified" if all_verified else "observer_configuration_unverified")
                    if modern else "legacy_unverified",
                observer_configuration=(dict(expected=registration["response_observation"],
                    rows=[dict(group=g, run_id=r, **check) for (g, r), check in checks.items()],
                    activation="NOT_ESTABLISHED_BY_CONFIGURATION") if modern else None),
                runs=complete, pairs=pairs,
                pilot_passes=sum(next_allowed(r) and verified(r) for r in complete if r["phase"] == "pilot"),
                expected_runs=len(complete), observed_runs=len(rows),
                verified_runs=sum(verified(r) for r in complete),
                measured_runs=sum(verified(r)
                                  and finite_number(r["elapsed_seconds"], positive=True) for r in complete),
                passing_runs=sum(next_allowed(r) and verified(r) for r in complete),
                complete_correct_and_intervention_contract=all_verified and interventions_ok,
                complete_correct_unattended=all_verified and all(r["human_interventions"] == 0 for r in complete),
                paired_warm_median_improvement_percent=full_median, warm_class_median_improvement_percent=medians,
                available_pairs=len(available), exploratory_available_pair_median=statistics.median(available) if available else None,
                classes_exceeding_regression_limit=regressions,
                timing_criterion_met=eligible,
                cost_status="UNKNOWN" if any(p["cost_increase_percent"] is None for p in pairs) else "MEASURED",
                cost_increase_over_10_percent=[p["pair_id"] for p in pairs if p["cost_increase_percent"] is not None
                                              and p["cost_increase_percent"] > registration["contract"]["disclose_cost_increase_percent"]],
                conclusion="TIMING_CRITERION_MET" if eligible else "CRITERION_NOT_MET" if all_verified else "INSUFFICIENT_EVIDENCE",
                production_adoption=False,
                limitations=["Cold pairs are reported separately and never pooled into the warm median.",
                             "A1 intervention counts are retained; B must have zero and cannot exceed its paired A1 count.",
                             "Missing, failed, timed-out and UNKNOWN slots remain in the registered denominator.",
                             "Native usage covers pinned accounting intervals; unobserved bills and incomplete boundaries remain unknown.",
                             "No production adoption is performed by this report."])
