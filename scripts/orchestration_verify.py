"""Recompute complete-run and campaign evidence, including honest unknowns.

The caller supplies the trusted campaign hash. Reports cannot select their own
runtime, checker, transcript, thresholds or measurement definitions. Fixtures
exercise the same validators but never qualify for a measured promotion.
"""
from __future__ import annotations

import statistics
import time
from pathlib import Path

from orchestration_binding import bound_lane
from orchestration_campaign import (METRICS_V3_COMMUNICATION, METRICS_V3_INPUT_PROVENANCE, METRICS_V3_OUTPUT_EFFECTS,
                                    ORDER, campaign_effect_policy, input_provenance_registration, validate_campaign)
from orchestration_common import (boot_id, contract_errors, count, digest, file_ref, json_ref, object_sha, parse_time,
                                  read_json, read_ref, utc_now, write_json, exclusive_lock)
from orchestration_contracts import campaign_version, evidence_class
from orchestration_coordinator import ACTIVE, Coordinator, State
from orchestration_evidence import verify_protocol
from orchestration_native import NativeReader, native_text, require


def _sum_known(values):
    return sum(values) if all(v is not None for v in values) else None


def derive_run(co, native_end, *, closeout_clock=None):
    """All metrics are derived from the same complete event/native interval."""
    states, data = co.state()
    events = co.events()
    require(events, "no original run events")
    require(not any(s in ACTIVE for s in states.values()), "active/uncertain work retains its lease; run cannot close")
    require(not any(item["state"] == "INCOMPLETE_CLAIM_INTENT" for item in co.recover()), "incomplete claim intent; preserve run")
    success = all(s == State.ACCEPTED for s in states.values())
    require(success or any(s == State.FAILED_PRESERVED for s in states.values()), "unfinished run has no verified failure boundary")
    require(set(native_end) == set(co.campaign["sessions"]), "run native lanes missing")
    readers = {k: NativeReader(s, fixtures=co.fixtures) for k, s in co.campaign["sessions"].items()}
    ends = {k: r.verify_pin(native_end[k]) for k, r in readers.items()}
    allowed = {k: set() for k in readers}
    allowed_refs = {k: set() for k in readers}
    registration = input_provenance_registration(co.campaign, co.spec["run_id"])
    terminal = []
    transports = []
    firsts = []
    disarms = []
    cleanup_at = []
    for key, info in data.items():
        if not info:
            continue
        lane = bound_lane(co, key, info)
        evidence = json_ref(info["accepted_evidence"] if states[key] == State.ACCEPTED else info["failure"])
        event = next(e for e in reversed(events) if e["node_id"] == key)
        terminal.append(event)
        stamp = info["verification"].get("accepted_at", info["verification"].get("settled_at"))
        require(parse_time(stamp) <= parse_time(event["at"]), "terminal event precedes native acceptance/settlement")
        lease = co._lease(key, info)
        require(lease["released_at"] is not None and lease["released_by"] ==
                file_ref(co.root / "events" / f"{event['seq']:06d}.json"), "verified terminal lease still needs exact release")
        require(parse_time(lease["released_at"]) >= parse_time(event["at"]), "lease release predates terminal event")
        cleanup_at.append(parse_time(lease["released_at"]))
        if co.campaign["sessions"][lane]["provider"] == "claude":
            from orchestration_transport import verify_disarm
            ref = verify_disarm(co, key, info, file_ref(co.root / "events" / f"{event['seq']:06d}.json"))
            disarms.append(ref)
            disarm = json_ref(ref)
            cleanup_at.append(parse_time(disarm["proof"]["observed_at"]))
            if disarm["operation_result"]:
                cleanup_at.append(parse_time(json_ref(disarm["operation_result"])["completed_at"]))
        for other in readers:
            require(ends[other] >= readers[other].verify_pin(evidence["native_end"][other]), "run ends before terminal evidence")
        after = readers[lane].action_summary(evidence["native_end"][lane]["offset"], ends[lane],
                    task_id=info["task_id"], output_root=info["output_root"], allow_empty=True,
                    effect_policy=campaign_effect_policy(co.campaign))
        attempts = "attempted_potential_write_calls" if campaign_effect_policy(co.campaign) else "attempted_task_calls"
        require(after[attempts] == 0, "task output actions continued after terminal boundary")
        if info["verification"].get("first_action_at"):
            firsts.append(parse_time(info["verification"]["first_action_at"]))
        if "protocol" in evidence:
            protocol = verify_protocol(co.campaign, co.header, co.nodes[key], info, evidence,
                                       ends[lane], ends["codex"], fixtures=co.fixtures)
            allowed[lane].update(protocol["notices"])
            allowed["codex"].add(protocol["callback"])
            if registration is not None:
                # v5 exempts the exact verified records, never a body a later input can repeat (ROOT R51).
                from upgrade_input_provenance import record_key
                allowed_refs[lane].add(record_key(protocol["delivery_record"]))
                allowed_refs[lane].add(record_key(protocol["preparation"]["handshake_delivery"]["native_proof"]))
                allowed_refs["codex"].add(record_key(protocol["callback_record"]))
            transports.append(protocol["transport_retries"])
        elif info["mode"] == "local_cli":
            transports.append(0)
        else:
            # A failed preparation/no-input attempt cannot silently become a
            # measured zero-retry successful business interaction.
            transports.append(None)
            if "prepared" in info:
                allowed[lane].add(read_ref(info["prepared"]["handshake"]["notice"]).decode())
    observations = {}
    unknown, status_queries = [], []
    for lane, reader in readers.items():
        start = reader.verify_pin(co.header["native_start"][lane])
        observations[lane] = reader.action_summary(start, ends[lane], task_id=co.spec["run_id"],
                                      output_root=str(co.root / "work"), allow_empty=True,
                                      effect_policy=campaign_effect_policy(co.campaign))
        if registration is not None:
            # v5 classifies every input unit below, after the closeout clock bounds the run.
            continue
        for ref, record in reader.records(start, ends[lane]):
            text = native_text(record, reader.provider, "user")
            if text is None or text in allowed[lane]:
                continue
            if text in co.campaign["metrics"]["status_queries"]:
                status_queries.append(ref)
            else:
                unknown.append(ref)
    last = max(terminal, key=lambda event: event["seq"])
    measurement_end = last
    if campaign_version(co.campaign) == 3:
        if closeout_clock is None:
            closeout_clock = dict(at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id())
        require(set(closeout_clock) == {"at", "monotonic_ns", "boot_id"}, "closeout clock fields changed")
        count(closeout_clock["monotonic_ns"], "closeout clock")
        require(isinstance(closeout_clock["boot_id"], str) and closeout_clock["boot_id"], "closeout boot missing")
        require(parse_time(closeout_clock["at"]) >= max([parse_time(last["at"]), *cleanup_at]),
                "closeout clock precedes complete lease/disarm cleanup")
        if closeout_clock["boot_id"] == last["boot_id"]:
            require(closeout_clock["monotonic_ns"] >= last["monotonic_ns"], "closeout monotonic clock moved backwards")
        measurement_end = closeout_clock
    else:
        require(closeout_clock is None, "v2 measurements cannot adopt v3 closeout clocks")
    provenance = None
    if registration is not None:
        provenance, unknown, status_queries = _input_units(co, readers, native_end, allowed_refs, registration,
                                                           not_after=measurement_end["at"])
    communication = None
    if co.campaign["metrics"] in (METRICS_V3_COMMUNICATION, METRICS_V3_OUTPUT_EFFECTS, METRICS_V3_INPUT_PROVENANCE):
        from upgrade_communication_attribution import attribute_inputs
        communication = attribute_inputs(co.campaign, co.header, native_end, readers,
                                         not_after=measurement_end["at"])
        exempt = {object_sha(item["native_record"]) for item in communication["inputs"]}
        unknown = [ref for ref in unknown if object_sha(ref) not in exempt]
    elapsed_wall = (parse_time(measurement_end["at"]) - parse_time(co.header["started_at"])).total_seconds()
    require(elapsed_wall >= 0, "negative original elapsed time")
    same_boot = all(e["boot_id"] == co.header["boot_id"] for e in [*events, measurement_end])
    elapsed = None
    clock_status = "cross_boot_unmeasured"
    if same_boot:
        startup = (parse_time(co.header["created_at"]) - parse_time(co.header["started_at"])).total_seconds()
        observed = (measurement_end["monotonic_ns"] - co.header["started_monotonic_ns"]) / 1e9 + startup
        require(observed >= 0, "negative monotonic elapsed time")
        if abs(observed - elapsed_wall) <= 2:
            elapsed, clock_status = observed, "same_boot_monotonic"
        else:
            clock_status = "wall_clock_discontinuity_unmeasured"
    transport_retries = _sum_known(transports)
    failed_tools = sum(v["failed_task_calls"] for v in observations.values())
    provider = _sum_known([v["provider_auto_retries"] for v in observations.values()])
    result = dict(status="PASS" if success else "FAILED_PRESERVED", run_id=co.spec["run_id"], strategy=co.spec["strategy"],
        nodes_total=len(states), nodes_accepted=sum(s == State.ACCEPTED for s in states.values()),
        states={k: s.value for k, s in states.items()}, contract_pass=success, disarms=disarms,
        metrics=dict(elapsed_seconds=elapsed, elapsed_wall_seconds=elapsed_wall, clock_status=clock_status,
            first_action_seconds=(min(firsts) - parse_time(co.header["started_at"])).total_seconds() if firsts else None,
            human_interventions=_human_interventions(unknown, provenance),
            unclassified_inputs=unknown, status_queries=status_queries,
            transport_retries=transport_retries, failed_business_tool_calls=failed_tools,
            retries=None if transport_retries is None else transport_retries + failed_tools,
            provider_auto_retries=provider, failed_api_attempts=sum(v["failed_api_attempts"] for v in observations.values()),
            duplicate_dispatches=0, reasks=0 if not unknown else None))
    if closeout_clock is not None:
        result["closeout_clock"] = closeout_clock
    if communication is not None:
        result["metrics"]["automated_communication_inputs"] = communication
    if provenance is not None:
        result["metrics"]["input_provenance"] = provenance
    return result


def _input_units(co, readers, native_end, allowed_refs, registration, *, not_after):
    """v5: every input-bearing native unit of the run, with the v2-v4 protocol roles kept.

    Only the exact native records a verified protocol chain pinned are exempt; a
    later input repeating that same body is not (ROOT R51). Status queries keep
    their separate v2-v4 role. A unit the registered producer journal attributes is
    MACHINE or HUMAN_REGISTERED; anything else, including inputs native_text cannot
    read, stays unknown. The closeout summary holds the complete result, journal pin
    included, so a later edit to the journal fails verify_run.
    """
    from upgrade_input_provenance import classify_inputs, lane_producer_sessions, record_key
    path = co.root / "input-provenance.jsonl"
    journal = file_ref(path) if path.exists() else None
    starts = co.header["native_start"]
    provenance = classify_inputs(journal, registration, co.spec["run_id"], readers, starts, native_end,
                                 producer_sessions=lane_producer_sessions(registration, co.spec["run_id"], readers,
                                                                          starts, native_end),
                                 not_before=co.header["started_at"], not_after=not_after)
    # Its journal-level count also treats protocol records and status queries as unknown;
    # the run's one count is _human_interventions below.
    provenance.pop("human_interventions")
    queries = {digest(text.encode()) for text in co.campaign["metrics"]["status_queries"]}
    unknown, status_queries = [], []
    for unit in provenance["units"]:
        if unit["provenance"] != "UNKNOWN" or record_key(unit["ref"]) in allowed_refs[unit["lane"]]:
            continue
        (status_queries if unit["payload_sha256"] in queries else unknown).append(unit["ref"])
    return provenance, unknown, status_queries


def _human_interventions(unknown, provenance):
    if unknown:
        return None
    if provenance is None:
        return 0
    # Registered operator sends only; one queued input is one intervention, not two units.
    return len({unit["intent_id"] for unit in provenance["units"] if unit["provenance"] == "HUMAN_REGISTERED"})


@contract_errors("whole run")
def verify_run(campaign_ref, run_id, *, fixtures=False):
    campaign = validate_campaign(campaign_ref, fixtures=fixtures)
    require(run_id in {r["run_id"] for r in campaign["runs"]}, "unknown frozen run")
    root = Path(campaign["root"]) / "runs" / run_id
    co = Coordinator(root, campaign_ref, fixtures=fixtures)
    finish = read_json(root / "finish.json")
    version = campaign_version(campaign)
    fields = {"version", "evidence_class", "campaign", "run_id", "header", "events", "native_end", "summary", "closed_at"}
    require(set(finish) == fields | ({"closeout_clock"} if version == 3 else set()),
            "run closeout fields changed")
    require(type(finish["version"]) is int and finish["version"] == version and
            finish["evidence_class"] == evidence_class(campaign, "cli_run", fixtures), "run execution class changed")
    require(finish["campaign"] == campaign_ref and finish["run_id"] == run_id and
            finish["header"] == file_ref(root / "run.json"), "run closeout binding changed")
    require(finish["events"] == [file_ref(root / "events" / f"{e['seq']:06d}.json") for e in co.events()],
            "complete final event history changed")
    require(parse_time(finish["closed_at"]) >= parse_time(co.events()[-1]["at"]), "closeout precedes terminal event")
    if version == 3:
        require(finish["closed_at"] == finish["closeout_clock"]["at"], "closeout timestamps differ")
    result = derive_run(co, finish["native_end"], closeout_clock=finish.get("closeout_clock"))
    require(all(parse_time(json_ref(ref)["proof"]["observed_at"]) <= parse_time(finish["closed_at"])
                for ref in result["disarms"]), "closeout precedes scoped disarm")
    require(finish["summary"] == result, "stored metrics differ from original evidence")
    return dict(result, evidence_class=evidence_class(campaign, "verified_cli_run", fixtures), finish=file_ref(root / "finish.json"))


def finish_run(co):
    with co.state_lock():
        if (co.root / "finish.json").exists():
            return verify_run(co.campaign_ref, co.spec["run_id"], fixtures=co.fixtures)
        co._gate()
        ends = {k: NativeReader(s, fixtures=co.fixtures).snapshot() for k, s in co.campaign["sessions"].items()}
        result = derive_run(co, ends)
        version = campaign_version(co.campaign)
        value = dict(version=version, evidence_class=evidence_class(co.campaign, "cli_run", co.fixtures), campaign=co.campaign_ref,
                     run_id=co.spec["run_id"], header=file_ref(co.root / "run.json"),
                     events=[file_ref(co.root / "events" / f"{e['seq']:06d}.json") for e in co.events()],
                     native_end=ends, summary=result, closed_at=utc_now())
        if version == 3:
            value.update(closeout_clock=result["closeout_clock"], closed_at=result["closeout_clock"]["at"])
        write_json(co.root / "finish.json", value, exclusive=True)
    return verify_run(co.campaign_ref, co.spec["run_id"], fixtures=co.fixtures)


def select_promotion(runs, *, live):
    """Pure comparison; only verify_campaign may supply verified live runs."""
    result = dict(selected="serial", promoted=False, reason="insufficient_evidence", medians={}, improvements={})
    if len(runs) != 9 or tuple(r["strategy"] for r in runs) != ORDER or any(
            r["status"] != "PASS" or not r["contract_pass"] or r["nodes_accepted"] != r["nodes_total"] for r in runs):
        return result
    fields = ("elapsed_seconds", "human_interventions", "retries")
    for strategy in ("serial", "omo", "codeg"):
        samples = [r["metrics"] for r in runs if r["strategy"] == strategy]
        result["medians"][strategy] = {key: statistics.median([m[key] for m in samples])
                                      if all(m[key] is not None for m in samples) else None for key in fields}
    eligible = []
    for strategy in ("omo", "codeg"):
        improvement = {}
        for key in fields:
            baseline, candidate = result["medians"]["serial"][key], result["medians"][strategy][key]
            improvement[key] = (baseline - candidate) / baseline * 100 if baseline is not None and baseline > 0 and candidate is not None else None
        result["improvements"][strategy] = improvement
        if any(v is not None and v >= 15 for v in improvement.values()):
            eligible.append(strategy)
    if not live:
        result["reason"] = "offline_fixtures_never_promote"
    elif eligible:
        def rank(strategy):
            medians = result["medians"][strategy]
            return tuple(medians[k] if medians[k] is not None else float("inf") for k in fields) + (strategy != "omo",)
        result.update(selected=min(eligible, key=rank), promoted=True, reason="verified_median_improvement_at_least_15_percent")
    else:
        result["reason"] = "no_eligible_15_percent_improvement"
    return result


@contract_errors("campaign verification")
def verify_campaign(campaign_ref, *, fixtures=False):
    campaign = validate_campaign(campaign_ref, fixtures=fixtures)
    results, missing = [], []
    for spec in campaign["runs"]:
        if not (Path(campaign["root"]) / "runs" / spec["run_id"] / "finish.json").exists():
            missing.append(spec["run_id"])
        else:
            require(not missing, "later trial closed before an earlier frozen trial")
            results.append(verify_run(campaign_ref, spec["run_id"], fixtures=fixtures))
    version = campaign_version(campaign)
    promotion = (select_promotion(results, live=not fixtures) if version == 2 else
                 dict(promoted=False, selected=None, reason="separate_registered_paired_experiment_required"))
    return dict(version=version, evidence_class=evidence_class(campaign, "verified_cli_campaign", fixtures),
                campaign=campaign_ref, runs=results, missing_runs=missing,
                promotion=promotion)
