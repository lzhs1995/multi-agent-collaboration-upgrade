"""Read-only registered run permits. Clients cannot publish into this directory.

The external scheduler verifies complete closeout before issuing the next permit.
Historical validation follows immutable pins; active gates additionally refuse a
closed permit. A timeout blocks new work, never original-attempt reconciliation.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from orchestration_common import (boot_id, count, file_ref, json_ref, parse_time,
                                  read_ref, require, utc_now)
from upgrade_benchmark import validate_registered_campaign, validate_registration


def steps(registration):
    result = []
    for run in registration["groups"]["pilot-B"]["runs"]:
        result.append(dict(group="pilot-B", **run))
    for pair in registration["pairs"]:
        for arm in pair["order"]:
            result.append(dict(**pair["members"][arm], phase=pair["phase"], pair_id=pair["pair_id"]))
    return [dict(ordinal=i, **row) for i, row in enumerate(result, 1)]


def step_for(registration, group, run_id):
    found = [s for s in steps(registration) if (s["group"], s["run_id"]) == (group, run_id)]
    require(len(found) == 1, "run is not in the registered global order")
    return found[0]


def schedule_path(registration, kind, ordinal):
    require(kind in ("permits", "closures"), "unknown schedule record kind")
    require(type(ordinal) is int and 1 <= ordinal <= len(steps(registration)), "invalid schedule ordinal")
    return Path(registration["root"]) / "schedule" / kind / f"{ordinal:04d}.json"


def read_halt(registration_ref):
    """An external halt stops new work, not historical validation or closeout."""
    registration = validate_registration(registration_ref)
    path = Path(registration["root"]) / "schedule" / "halt.json"
    if not os.path.lexists(path):
        return None
    ref = file_ref(path)
    value = json_ref(ref, "original schedule halt")
    require(set(value) == {"schema", "registration", "requested_at", "classification", "reason",
                           "active_permit", "evidence"}
            and value["schema"] == "upgrade-schedule-halt-v1" and value["registration"] == registration_ref,
            "schedule halt binding changed")
    require(value["classification"] in ("TIMEOUT", "FAILED", "UNKNOWN", "STOPPED")
            and isinstance(value["reason"], str) and value["reason"].strip(), "invalid halt reason")
    parse_time(value["requested_at"])
    if value["active_permit"] is not None:
        permit = json_ref(value["active_permit"])
        read_permit(registration_ref, permit["step"]["ordinal"], ref=value["active_permit"])
    require(isinstance(value["evidence"], list), "halt evidence must be pinned files")
    for evidence in value["evidence"]:
        read_ref(evidence, "preserved halt evidence")
    return ref, value


def require_running(registration_ref):
    require(read_halt(registration_ref) is None,
            "schedule halted; only original observation and safe closeout are allowed")


def read_permit(registration_ref, ordinal, *, ref=None, active=False):
    registration = validate_registration(registration_ref)
    expected = steps(registration)[ordinal - 1] if type(ordinal) is int and 1 <= ordinal <= len(steps(registration)) else None
    require(expected is not None, "unregistered permit ordinal")
    path = schedule_path(registration, "permits", ordinal)
    ref = file_ref(path) if ref is None else ref
    require(ref["path"] == str(path), "permit must remain in the external schedule directory")
    value = json_ref(ref, "original run permit")
    require(set(value) == {"schema", "registration", "step", "runtime", "context", "issued_at", "previous"}
            and value["schema"] == "upgrade-run-permit-v1" and value["registration"] == registration_ref
            and value["step"] == expected, "registered permit scope or order changed")
    issued = parse_time(value["issued_at"])
    require(value["context"]["path"] == registration["groups"][expected["group"]]["context_path"],
            "permit context belongs to another group")
    json_ref(value["context"], "permitted context")
    runtime = json_ref(value["runtime"], "permitted runtime")
    require(runtime["context"] == value["context"] and
            runtime["arm"] == registration["groups"][expected["group"]]["arm"], "permit runtime changed")
    if ordinal == 1:
        require(value["previous"] is None, "first permit must not invent a predecessor")
    else:
        previous = value["previous"]
        require(isinstance(previous, dict) and previous.get("path") ==
                str(schedule_path(registration, "closures", ordinal - 1)), "preceding closeout is missing")
        closure = json_ref(previous, "previous external closeout")
        require(set(closure) == {"schema", "registration", "permit", "campaign", "finish", "verified_at", "measurement", "next_allowed"}
                and closure.get("schema") == "upgrade-run-closure-v1" and
                closure.get("registration") == registration_ref and closure.get("next_allowed") is True,
                "previous run has not earned the next permit")
        from upgrade_measurements import next_allowed
        require(next_allowed(closure["measurement"]) and closure["finish"] == closure["measurement"]["finish"]
                and closure["campaign"] == closure["measurement"]["campaign"], "previous closeout classification changed")
        prior_ref, prior = read_permit(registration_ref, ordinal - 1, ref=closure["permit"])
        require(closure["permit"] == prior_ref and closure["measurement"]["group"] == prior["step"]["group"]
                and closure["measurement"]["run_id"] == prior["step"]["run_id"] and
                parse_time(prior["issued_at"]) <= parse_time(closure["verified_at"]) <= issued,
                "previous closeout scope or time changed")
        json_ref(closure["finish"], "previous immutable run closeout")
    if active:
        require(not schedule_path(registration, "closures", ordinal).exists(), "run permit is closed; observe original evidence")
    return ref, value


def campaign_permit(campaign, run_id, *, ref=None, active=False):
    validate_registered_campaign(campaign)
    registration = validate_registration(campaign["registration"])
    step = step_for(registration, campaign["session_group"], run_id)
    ref, value = read_permit(campaign["registration"], step["ordinal"], ref=ref, active=active)
    require(value["context"] == campaign["experiment"] and value["runtime"] == campaign["runtime"],
            "campaign differs from the originally permitted runtime")
    return ref, value


def wait_permit(campaign, run_id, timeout_seconds=45):
    """Bounded file observation in the original supervisor turn; never input."""
    require(type(timeout_seconds) in (int, float) and 0 <= timeout_seconds <= 45,
            "permit wait must be between 0 and 45 seconds")
    validate_registered_campaign(campaign)
    registration = validate_registration(campaign["registration"])
    step = step_for(registration, campaign["session_group"], run_id)
    path = schedule_path(registration, "permits", step["ordinal"])
    startup = json_ref(json_ref(campaign["runtime"])["startup"])
    deadline = time.monotonic() + timeout_seconds
    signature = None
    while True:
        if os.path.lexists(startup["stop_path"]):
            return dict(status="STOPPED_INPUT", step=step, input_operations=0)
        halted = read_halt(campaign["registration"])
        if halted is not None:
            return dict(status="SCHEDULE_ENDED", halt=halted[0], classification=halted[1]["classification"],
                        step=step, input_operations=0)
        if os.path.lexists(path):
            ref, _ = campaign_permit(campaign, run_id)
            closed = os.path.lexists(schedule_path(registration, "closures", step["ordinal"]))
            return dict(status="RUN_ALREADY_CLOSED" if closed else "PERMIT_READY", permit=ref,
                        step=step, input_operations=0)
        # Revalidate on a directory change. The external publisher only creates
        # immutable records; waiting never repeatedly replays business checks.
        directories = [Path(registration["root"]) / "schedule" / kind for kind in ("permits", "closures")]
        current = tuple((p.stat().st_mtime_ns, p.stat().st_ctime_ns) if p.exists() else None for p in directories)
        if current != signature:
            from upgrade_schedule import status
            observed = status(campaign["registration"])
            if observed["status"] in ("FAILURE_PRESERVED", "COMPLETE"):
                return dict(status="SCHEDULE_ENDED", schedule=observed, step=step, input_operations=0)
            signature = current
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return dict(status="WAITING_PERMIT", step=step, input_operations=0, sessions_preserved=True)
        time.sleep(min(remaining, 0.5))


def launch_start(campaign, permit):
    if permit["step"]["phase"] != "cold":
        return None
    runtime = json_ref(campaign["runtime"])
    startup = json_ref(runtime["startup"])
    ref = file_ref(Path(startup["adoption_path"]).parent / "launch-request.json")
    value = json_ref(ref, "original cold launch clock")
    require(value.get("runtime") == campaign["runtime"] and value.get("startup") == runtime["startup"]
            and parse_time(value["requested_at"]) >= parse_time(permit["issued_at"]),
            "cold launch must follow its original permit")
    count(value["monotonic_ns"], "cold launch monotonic clock")
    require(isinstance(value["boot_id"], str) and value["boot_id"], "cold launch boot identity missing")
    return ref


def elapsed(header, *, end=None):
    """Use native start for warm, actual external launch intent for cold."""
    end = end or dict(at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id())
    if header.get("launch_start") is not None:
        start = json_ref(header["launch_start"], "pinned cold start")
        at, clock, boot = start["requested_at"], start["monotonic_ns"], start["boot_id"]
        overhead = 0
    else:
        at, clock, boot = header["started_at"], header["started_monotonic_ns"], header["boot_id"]
        overhead = (parse_time(header["created_at"]) - parse_time(at)).total_seconds()
    wall = (parse_time(end["at"]) - parse_time(at)).total_seconds()
    count(clock, "measurement start clock")
    count(end["monotonic_ns"], "measurement end clock")
    observed = (end["monotonic_ns"] - clock) / 1e9 + overhead
    if boot != end["boot_id"]:
        return dict(seconds=None, wall_seconds=wall, clock_status="cross_boot_unmeasured")
    if wall < 0 or observed < 0 or overhead < 0 or abs(observed - wall) > 2:
        return dict(seconds=None, wall_seconds=wall, clock_status="wall_clock_discontinuity_unmeasured")
    return dict(seconds=observed, wall_seconds=wall, clock_status="same_boot_monotonic")


def work_budget(campaign, header):
    require_running(campaign["registration"])
    registration = validate_registration(campaign["registration"])
    measured = elapsed(header)
    require(measured["seconds"] is not None, "run clock is unknown; only original evidence and cleanup are allowed")
    require(measured["seconds"] <= registration["contract"]["run_timeout_seconds"],
            "registered time cap reached; no new work, replacement or lease release by timeout")
