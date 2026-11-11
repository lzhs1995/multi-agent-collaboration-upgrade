#!/usr/bin/env python3
"""External file-only scheduler for the fixed 33-run experiment.

No model messages, business execution or supervisor acceptance occur here.
Only verified original run closeout can advance the immutable permit chain.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from orchestration_campaign import anchored_ref, validate_campaign
from orchestration_common import (ContractError, exclusive_lock, file_ref, json_ref,
                                  read_ref, require, utc_now, write_json)
from orchestration_permit import campaign_permit, read_halt, read_permit, require_running, schedule_path, steps
from upgrade_benchmark import group_registration, runtime_observation, validate_registration
from upgrade_client_isolation import sandbox_check
from upgrade_live_runtime import validate_live_runtime
from upgrade_measurements import compare, measurement, next_allowed


def external(registration):
    # Darwin returns denial for a nonexistent query path even outside Seatbelt.
    # The registered root already exists and is outside every client's writable
    # subtrees, so query that authority without creating a ledger placeholder.
    path = Path(registration["root"])
    require(path.is_dir(), "registered publisher authority is missing")
    require(not sandbox_check(os.getpid(), str(path), "file-write-create"),
            "experimental clients cannot publish run permits or measurements")


def ledger(registration_ref):
    registration = validate_registration(registration_ref)
    root = Path(registration["root"]) / "schedule"
    permits = sorted((root / "permits").glob("*.json"))
    closures = sorted((root / "closures").glob("*.json"))
    for paths in (permits, closures):
        require([p.name for p in paths] == [f"{n:04d}.json" for n in range(1, len(paths) + 1)],
                "schedule records are not contiguous; preserve original evidence")
    require(len(permits) <= len(steps(registration)) and len(closures) in (len(permits) - 1, len(permits)),
            "multiple unclosed permits or orphan closure")
    if permits:
        read_permit(registration_ref, len(permits))
    if closures:
        closure = json_ref(file_ref(closures[-1]), "latest external closeout")
        require(set(closure) == {"schema", "registration", "permit", "campaign", "finish", "verified_at", "measurement", "next_allowed"}
                and closure["schema"] == "upgrade-run-closure-v1" and closure["registration"] == registration_ref
                and closure["permit"] == file_ref(permits[len(closures) - 1]), "latest closeout binding changed")
        require(closure["next_allowed"] is next_allowed(closure["measurement"]), "closeout progression classification changed")
        require(closure["finish"] == closure["measurement"]["finish"] and closure["campaign"] == closure["measurement"]["campaign"],
                "closeout measurement pins changed")
    return registration, permits, closures


def issue(registration_ref, runtime_ref):
    registration = validate_registration(registration_ref)
    external(registration)
    _, runtime = validate_live_runtime(runtime_ref)
    runtime_observation(registration, runtime_ref, runtime)
    root = Path(registration["root"]) / "schedule"
    with exclusive_lock(root / "publisher.lock"):
        registration, permits, closures = ledger(registration_ref)
        require_running(registration_ref)
        if len(permits) > len(closures):
            ref, current = read_permit(registration_ref, len(permits), active=True)
            require(current["runtime"] == runtime_ref, "previous original run is unclosed; no other group may run")
            return dict(status="ORIGINAL_PERMIT_ACTIVE", permit=ref, step=current["step"], input_operations=0)
        require(len(permits) < len(steps(registration)), "registered experiment is already complete")
        previous = None
        if closures:
            previous = file_ref(closures[-1])
            closure = json_ref(previous)
            require(closure["next_allowed"] is True, "previous failed, UNKNOWN or timed-out run prevents further permits")
            # Recompute once at advancement; clients only read the immutable
            # external attestation. This is never a substitute for native accept.
            require(measurement(closure["campaign"], closure["measurement"]["run_id"]) == closure["measurement"],
                    "previous original closeout no longer verifies")
        step = steps(registration)[len(permits)]
        group_registration(registration_ref, step["group"], runtime["context"], runtime["arm"])
        for prior_path in permits:
            prior = json_ref(file_ref(prior_path))
            if prior["step"]["group"] == step["group"]:
                require(prior["runtime"] == runtime_ref, "original group runtime cannot be replaced")
        value = dict(schema="upgrade-run-permit-v1", registration=registration_ref, step=step,
                     runtime=runtime_ref, context=runtime["context"], issued_at=utc_now(), previous=previous)
        path = schedule_path(registration, "permits", step["ordinal"])
        ref = write_json(path, value, exclusive=True)
        read_permit(registration_ref, step["ordinal"], ref=ref, active=True)
        return dict(status="PERMIT_ISSUED", permit=ref, step=step, input_operations=0)


def halt(registration_ref, reason, classification="UNKNOWN", evidence=()):
    """Publish once; preserve the active permit, clients, attempts and leases."""
    registration = validate_registration(registration_ref)
    external(registration)
    require(classification in ("TIMEOUT", "FAILED", "UNKNOWN", "STOPPED")
            and isinstance(reason, str) and reason.strip(), "concrete halt classification and reason required")
    refs = list(evidence)
    for ref in refs:
        read_ref(ref, "halt evidence")
    root = Path(registration["root"]) / "schedule"
    with exclusive_lock(root / "publisher.lock"):
        old = read_halt(registration_ref)
        if old is not None:
            # Neither a later observation nor recovery may rewrite the cause.
            return dict(status="HALTED", halt=old[0], classification=old[1]["classification"], input_operations=0)
        _, permits, closures = ledger(registration_ref)
        active = file_ref(permits[-1]) if len(permits) > len(closures) else None
        ref = write_json(root / "halt.json", dict(schema="upgrade-schedule-halt-v1", registration=registration_ref,
            requested_at=utc_now(), reason=reason, classification=classification, active_permit=active,
            evidence=refs), exclusive=True)
        read_halt(registration_ref)
        return dict(status="HALTED", halt=ref, classification=classification, input_operations=0)


def close(campaign_ref, run_id):
    campaign = validate_campaign(campaign_ref)
    registration_ref = campaign["registration"]
    registration = validate_registration(registration_ref)
    external(registration)
    root = Path(registration["root"]) / "schedule"
    with exclusive_lock(root / "publisher.lock"):
        ledger(registration_ref)
        permit_ref, permit = campaign_permit(campaign, run_id)
        row = measurement(campaign_ref, run_id)
        path = schedule_path(registration, "closures", permit["step"]["ordinal"])
        if path.exists():
            value = json_ref(file_ref(path))
            require(value["measurement"] == row and value["permit"] == permit_ref, "original external closeout changed")
        else:
            value = dict(schema="upgrade-run-closure-v1", registration=registration_ref, permit=permit_ref,
                         campaign=campaign_ref, finish=row["finish"], verified_at=utc_now(), measurement=row,
                         next_allowed=next_allowed(row))
            write_json(path, value, exclusive=True)
        return dict(status="CLOSED" if value["next_allowed"] else "CLOSED_WITH_FAILURE_PRESERVED",
                    closure=file_ref(path), next_allowed=value["next_allowed"], measurement=row, input_operations=0)


def report(registration_ref):
    registration, permits, closures = ledger(registration_ref)
    halted = read_halt(registration_ref)
    rows = []
    for path in permits:
        permit = json_ref(file_ref(path))
        step = permit["step"]
        runtime = json_ref(permit["runtime"])
        group = registration["groups"][step["group"]]
        context = json_ref(permit["context"])
        campaign_path = Path(context["paths"]["campaigns"]) / runtime["arm"] / group["campaign_id"] / "campaign.json"
        try:
            if not campaign_path.exists():
                raise ContractError("original campaign has not been frozen")
            rows.append(measurement(file_ref(campaign_path), step["run_id"]))
        except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
            # The permitted slot remains part of the report. Never release its
            # lease or issue a replacement because this observation is unknown.
            termination = (halted[1]["classification"] if halted is not None
                           and halted[1]["active_permit"] == file_ref(path) else "UNKNOWN")
            rows.append(dict(**step, arm=group["arm"], workload_class=group["workload_class"], status=termination,
                             contract_pass=False, within_cap=False if termination == "TIMEOUT" else None,
                             elapsed_seconds=None, human_interventions=None,
                             usage=dict(tokens=None, cost=None), evidence_class="unverified_original_attempt",
                             reason=str(exc), permit=file_ref(path)))
    value = compare(registration, rows, registration_ref=registration_ref)
    value.update(registration=registration_ref, generated_at=utc_now(), input_operations=0,
                 halt=halted[0] if halted else None)
    return value


def status(registration_ref):
    registration, permits, closures = ledger(registration_ref)
    halted = read_halt(registration_ref)
    order = steps(registration)
    active = json_ref(file_ref(permits[-1]))["step"] if len(permits) > len(closures) else None
    can_advance = not closures or json_ref(file_ref(closures[-1]))["next_allowed"]
    return dict(status="HALTED" if halted else "ACTIVE" if active else "COMPLETE" if len(closures) == len(order) and can_advance else
                "READY" if can_advance else "FAILURE_PRESERVED", active=active,
                next_step=order[len(permits)] if not halted and not active and can_advance and len(permits) < len(order) else None,
                halt=halted[0] if halted else None,
                permits=len(permits), closures=len(closures), expected=len(order), input_operations=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("issue", "close", "status", "report", "halt"))
    parser.add_argument("--registration")
    parser.add_argument("--registration-sha256")
    parser.add_argument("--runtime")
    parser.add_argument("--runtime-sha256")
    parser.add_argument("--campaign")
    parser.add_argument("--campaign-sha256")
    parser.add_argument("--run")
    parser.add_argument("--output")
    parser.add_argument("--reason")
    parser.add_argument("--classification", choices=("TIMEOUT", "FAILED", "UNKNOWN", "STOPPED"), default="UNKNOWN")
    args = parser.parse_args(argv)
    try:
        if args.command == "close":
            require(args.campaign and args.campaign_sha256 and args.run, "pinned campaign and original run required")
            value = close(anchored_ref(args.campaign, args.campaign_sha256), args.run)
        else:
            require(args.registration and args.registration_sha256, "pinned registration required")
            ref = anchored_ref(args.registration, args.registration_sha256)
            if args.command == "issue":
                require(args.runtime and args.runtime_sha256, "original runtime pin required")
                value = issue(ref, anchored_ref(args.runtime, args.runtime_sha256))
            elif args.command == "halt":
                value = halt(ref, args.reason, args.classification)
            else:
                value = status(ref) if args.command == "status" else report(ref)
            if args.output:
                require(args.command == "report", "only a report may use an explicit output")
                registration = validate_registration(ref)
                external(registration)
                from orchestration_common import within
                output = within(args.output, Path(registration["root"]) / "reports")
                value = dict(report=write_json(output, value, exclusive=True), input_operations=0)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REFUSED_OR_UNKNOWN_PRESERVED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
