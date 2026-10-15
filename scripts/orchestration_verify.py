"""Recompute complete-run and campaign evidence, including honest unknowns.

The caller supplies the trusted campaign hash. Reports cannot select their own
runtime, checker, transcript, thresholds or measurement definitions. Fixtures
exercise the same validators but never qualify for a measured promotion.
"""
from __future__ import annotations

import statistics
from pathlib import Path

from orchestration_campaign import ORDER, validate_campaign
from orchestration_common import (contract_errors, file_ref, json_ref, object_sha, parse_time, read_json,
                                  read_ref, utc_now, write_json, exclusive_lock)
from orchestration_coordinator import ACTIVE, Coordinator, State
from orchestration_evidence import verify_protocol
from orchestration_native import NativeReader, native_text, require


def _sum_known(values):
    return sum(values) if all(v is not None for v in values) else None


def derive_run(co, native_end):
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
    terminal = []
    transports = []
    firsts = []
    disarms = []
    for key, info in data.items():
        if not info:
            continue
        lane = co.nodes[key]["executor"]
        evidence = json_ref(info["accepted_evidence"] if states[key] == State.ACCEPTED else info["failure"])
        event = next(e for e in reversed(events) if e["node_id"] == key)
        terminal.append(event)
        stamp = info["verification"].get("accepted_at", info["verification"].get("settled_at"))
        require(parse_time(stamp) <= parse_time(event["at"]), "terminal event precedes native acceptance/settlement")
        lease = co._lease(key, info)
        require(lease["released_at"] is not None and lease["released_by"] ==
                file_ref(co.root / "events" / f"{event['seq']:06d}.json"), "verified terminal lease still needs exact release")
        require(parse_time(lease["released_at"]) >= parse_time(event["at"]), "lease release predates terminal event")
        if lane == "claude":
            from orchestration_transport import verify_disarm
            disarms.append(verify_disarm(co, key, info, file_ref(co.root / "events" / f"{event['seq']:06d}.json")))
        for other in readers:
            require(ends[other] >= readers[other].verify_pin(evidence["native_end"][other]), "run ends before terminal evidence")
        after = readers[lane].action_summary(evidence["native_end"][lane]["offset"], ends[lane],
                    task_id=info["task_id"], output_root=info["output_root"], allow_empty=True)
        require(after["attempted_task_calls"] == 0, "task output actions continued after terminal boundary")
        if info["verification"].get("first_action_at"):
            firsts.append(parse_time(info["verification"]["first_action_at"]))
        if "protocol" in evidence:
            protocol = verify_protocol(co.campaign, co.header, co.nodes[key], info, evidence,
                                       ends["claude"], ends["codex"], fixtures=co.fixtures)
            allowed["claude"].update(protocol["notices"])
            allowed["codex"].add(protocol["callback"])
            transports.append(protocol["transport_retries"])
        elif info["mode"] == "local_cli":
            transports.append(0)
        else:
            # A failed preparation/no-input attempt cannot silently become a
            # measured zero-retry successful business interaction.
            transports.append(None)
            if "prepared" in info:
                allowed["claude"].add(read_ref(info["prepared"]["handshake"]["notice"]).decode())
    observations = {}
    unknown, status_queries = [], []
    for lane, reader in readers.items():
        start = reader.verify_pin(co.header["native_start"][lane])
        observations[lane] = reader.action_summary(start, ends[lane], task_id=co.spec["run_id"],
                                      output_root=str(co.root / "work"), allow_empty=True)
        for ref, record in reader.records(start, ends[lane]):
            text = native_text(record, lane, "user")
            if text is None or text in allowed[lane]:
                continue
            if text in co.campaign["metrics"]["status_queries"]:
                status_queries.append(ref)
            else:
                unknown.append(ref)
    last = max(terminal, key=lambda event: event["seq"])
    elapsed_wall = (parse_time(last["at"]) - parse_time(co.header["started_at"])).total_seconds()
    require(elapsed_wall >= 0, "negative original elapsed time")
    same_boot = all(e["boot_id"] == co.header["boot_id"] for e in events)
    elapsed = None
    clock_status = "cross_boot_unmeasured"
    if same_boot:
        startup = (parse_time(co.header["created_at"]) - parse_time(co.header["started_at"])).total_seconds()
        observed = (last["monotonic_ns"] - co.header["started_monotonic_ns"]) / 1e9 + startup
        require(observed >= 0, "negative monotonic elapsed time")
        if abs(observed - elapsed_wall) <= 2:
            elapsed, clock_status = observed, "same_boot_monotonic"
        else:
            clock_status = "wall_clock_discontinuity_unmeasured"
    transport_retries = _sum_known(transports)
    failed_tools = sum(v["failed_task_calls"] for v in observations.values())
    provider = _sum_known([v["provider_auto_retries"] for v in observations.values()])
    return dict(status="PASS" if success else "FAILED_PRESERVED", run_id=co.spec["run_id"], strategy=co.spec["strategy"],
        nodes_total=len(states), nodes_accepted=sum(s == State.ACCEPTED for s in states.values()),
        states={k: s.value for k, s in states.items()}, contract_pass=success, disarms=disarms,
        metrics=dict(elapsed_seconds=elapsed, elapsed_wall_seconds=elapsed_wall, clock_status=clock_status,
            first_action_seconds=(min(firsts) - parse_time(co.header["started_at"])).total_seconds() if firsts else None,
            human_interventions=0 if not unknown else None, unclassified_inputs=unknown, status_queries=status_queries,
            transport_retries=transport_retries, failed_business_tool_calls=failed_tools,
            retries=None if transport_retries is None else transport_retries + failed_tools,
            provider_auto_retries=provider, failed_api_attempts=sum(v["failed_api_attempts"] for v in observations.values()),
            duplicate_dispatches=0, reasks=0 if not unknown else None))


@contract_errors("whole run")
def verify_run(campaign_ref, run_id, *, fixtures=False):
    campaign = validate_campaign(campaign_ref, fixtures=fixtures)
    require(run_id in {r["run_id"] for r in campaign["runs"]}, "unknown frozen run")
    root = Path(campaign["root"]) / "runs" / run_id
    co = Coordinator(root, campaign_ref, fixtures=fixtures)
    finish = read_json(root / "finish.json")
    require(set(finish) == {"version", "evidence_class", "campaign", "run_id", "header", "events", "native_end", "summary", "closed_at"},
            "run closeout fields changed")
    require(type(finish["version"]) is int and finish["version"] == 2 and
            finish["evidence_class"] == ("offline_fixture" if fixtures else "cli_run_v2"), "run execution class changed")
    require(finish["campaign"] == campaign_ref and finish["run_id"] == run_id and
            finish["header"] == file_ref(root / "run.json"), "run closeout binding changed")
    require(finish["events"] == [file_ref(root / "events" / f"{e['seq']:06d}.json") for e in co.events()],
            "complete final event history changed")
    require(parse_time(finish["closed_at"]) >= parse_time(co.events()[-1]["at"]), "closeout precedes terminal event")
    result = derive_run(co, finish["native_end"])
    require(all(parse_time(json_ref(ref)["proof"]["observed_at"]) <= parse_time(finish["closed_at"])
                for ref in result["disarms"]), "closeout precedes scoped disarm")
    require(finish["summary"] == result, "stored metrics differ from original evidence")
    return dict(result, evidence_class="offline_fixture" if fixtures else "verified_cli_run_v2", finish=file_ref(root / "finish.json"))


def finish_run(co):
    with exclusive_lock(co.root / "coordinator.lock"):
        if (co.root / "finish.json").exists():
            return verify_run(co.campaign_ref, co.spec["run_id"], fixtures=co.fixtures)
        co._gate()
        ends = {k: NativeReader(s, fixtures=co.fixtures).snapshot() for k, s in co.campaign["sessions"].items()}
        result = derive_run(co, ends)
        value = dict(version=2, evidence_class="offline_fixture" if co.fixtures else "cli_run_v2", campaign=co.campaign_ref,
                     run_id=co.spec["run_id"], header=file_ref(co.root / "run.json"),
                     events=[file_ref(co.root / "events" / f"{e['seq']:06d}.json") for e in co.events()],
                     native_end=ends, summary=result, closed_at=utc_now())
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
    return dict(version=2, evidence_class="offline_fixture" if fixtures else "verified_cli_campaign_v2",
                campaign=campaign_ref, runs=results, missing_runs=missing,
                promotion=select_promotion(results, live=not fixtures))
