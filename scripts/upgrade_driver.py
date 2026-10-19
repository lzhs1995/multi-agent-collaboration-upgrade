#!/usr/bin/env python3
"""External one-attempt driver for the registered real-client experiment.

The driver publishes files and launches each permitted original group once.
Only the real Codex supervisor performs business execution and native acceptance.
Uncertain mutations are observed or halted; they are never repeated on recovery.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import timedelta
from pathlib import Path

from orchestration_campaign import anchored_ref, validate_campaign
from orchestration_common import (ContractError, boot_id, exclusive_lock, file_ref, json_ref,
    parse_time, read_ref, require, utc_now, verify_tree, write_json)
from orchestration_freeze_v3 import authorize, freeze_campaign
from orchestration_permit import elapsed, read_permit, require_running
from upgrade_benchmark import group_registration, runtime_observation, validate_registration
from upgrade_live_control import control, publish_bootstrap
from upgrade_live_runtime import validate_adoption, validate_live_runtime
from upgrade_failure_watch import CodexFailureWatch
import upgrade_schedule as schedule
from upgrade_supervisor_instructions import publish as publish_instructions

REQUEST_FIELDS = {"schema", "registration", "runtimes", "source", "corpus", "revision", "native_authorizations"}
TERMINAL = {"HALTED", "COMPLETE", "FAILURE_PRESERVED"}
DEADLINE_FIELDS = {"timeout_seconds", "started_at", "started_monotonic_ns", "boot_id", "deadline_at"}


class OverallDeadline(ContractError):
    """A terminal clock observation, never permission to restart an attempt."""

    def __init__(self, classification, reason, observation):
        super().__init__(reason)
        self.classification = classification
        self.observation = observation


def configure(request_ref, *, overall_timeout_seconds=None):
    require(overall_timeout_seconds is None or
            (type(overall_timeout_seconds) is int and overall_timeout_seconds > 0),
            "overall timeout must be a positive integer number of seconds")
    request = json_ref(request_ref, "fixed driver request")
    require(set(request) == REQUEST_FIELDS and request["schema"] == "upgrade-driver-request-v1",
            "complete fixed driver request required")
    registration = validate_registration(request["registration"])
    schedule.external(registration)
    require(set(request["runtimes"]) == set(registration["groups"]), "all seven original runtime pins required")
    require(isinstance(request["revision"], str) and request["revision"], "source revision required")
    verify_tree(request["source"])
    verify_tree(request["corpus"])
    authorize(request["native_authorizations"])
    for group, runtime_ref in request["runtimes"].items():
        _, runtime = validate_live_runtime(runtime_ref)
        group_registration(request["registration"], group, runtime["context"], runtime["arm"])
        runtime_observation(registration, runtime_ref, runtime)
        checked = control(runtime_ref, "check")
        require(not checked["launched"] and not checked["stopped"] and not checked["adoption_published"],
                "driver registration must precede original launch and adoption")
        spec = json_ref(runtime["startup"])
        require(not os.path.lexists(Path(spec["adoption_path"]).parent / "launch-request.json"),
                "original launch request already exists; do not register a replacement driver")
    require(schedule.status(request["registration"])["permits"] == 0, "configure before the first permit")
    root = Path(registration["root"]) / "driver"
    with exclusive_lock(root / "publisher.lock"):
        created_at = utc_now()
        value = dict(schema="upgrade-driver-config-v1", request=request_ref,
            created_at=created_at, **{k: v for k, v in request.items() if k != "schema"})
        if overall_timeout_seconds is not None:
            value["schema"] = "upgrade-driver-config-v2"
            value["overall_deadline"] = dict(timeout_seconds=overall_timeout_seconds,
                started_at=created_at, started_monotonic_ns=time.monotonic_ns(), boot_id=boot_id(),
                deadline_at=(parse_time(created_at) + timedelta(seconds=overall_timeout_seconds)).isoformat())
        return write_json(root / "config.json", value, exclusive=True)


class Driver:
    def __init__(self, ref):
        self.ref = ref
        self.config = json_ref(ref, "original driver configuration")
        fields = (REQUEST_FIELDS - {"schema"}) | {"schema", "request", "created_at"}
        version = self.config.get("schema")
        require(version in ("upgrade-driver-config-v1", "upgrade-driver-config-v2") and
                set(self.config) == fields | ({"overall_deadline"} if version == "upgrade-driver-config-v2" else set()),
                "driver configuration changed")
        if version == "upgrade-driver-config-v2":
            deadline = self.config["overall_deadline"]
            require(isinstance(deadline, dict) and set(deadline) == DEADLINE_FIELDS,
                    "complete immutable overall deadline required")
            require(type(deadline["timeout_seconds"]) is int and deadline["timeout_seconds"] > 0
                    and type(deadline["started_monotonic_ns"]) is int and deadline["started_monotonic_ns"] >= 0
                    and isinstance(deadline["boot_id"], str) and deadline["boot_id"], "invalid overall deadline clock")
            require(deadline["started_at"] == self.config["created_at"] and
                    parse_time(deadline["deadline_at"]) - parse_time(deadline["started_at"]) ==
                    timedelta(seconds=deadline["timeout_seconds"]), "overall deadline anchor changed")
        request = json_ref(self.config["request"])
        require(request == dict(schema="upgrade-driver-request-v1", **{
            k: self.config[k] for k in REQUEST_FIELDS - {"schema"}}), "driver request pins changed")
        self.registration_ref = self.config["registration"]
        self.registration = validate_registration(self.registration_ref)
        require(set(self.config["runtimes"]) == set(self.registration["groups"]), "driver runtime groups changed")
        self.root = Path(self.registration["root"]) / "driver"
        self.failure_watches = {}
        require(ref["path"] == str(self.root / "config.json"), "driver location changed")

    def once(self, key, action, operation, recover=None):
        """An intent without a result never grants another side effect."""
        # Source/identity validation between ticks may take time. Recheck just
        # before each issue/launch/freeze/bootstrap/close action, not only at entry.
        self._check_overall_deadline()
        directory = self.root / "actions" / key
        intent_path, result_path = directory / "intent.json", directory / "result.json"
        if os.path.lexists(intent_path):
            intent_ref = file_ref(intent_path)
            intent = json_ref(intent_ref)
            require(intent["config"] == self.ref and intent["action"] == action, "original driver action changed")
            if result_path.exists():
                result = json_ref(file_ref(result_path))
                require(result["intent"] == intent_ref, "driver result belongs to another intent")
                require(result["status"] == "RETURNED", "original driver operation failed: " + str(result.get("error")))
                return result["value"]
            observed = recover() if recover else None
            require(observed is not None, "unconfirmed original " + action + "; no replay is authorized")
            return observed
        intent_ref = write_json(intent_path, dict(schema="upgrade-driver-action-v1", config=self.ref,
            action=action, started_at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id()), exclusive=True)
        try:
            value = operation()
        except Exception as exc:
            write_json(result_path, dict(intent=intent_ref, completed_at=utc_now(), status="ERROR_PRESERVED",
                error=str(exc), error_type=type(exc).__name__), exclusive=True)
            raise
        write_json(result_path, dict(intent=intent_ref, completed_at=utc_now(), status="RETURNED", value=value), exclusive=True)
        return value

    def halt(self, reason, classification="UNKNOWN", evidence=()):
        return schedule.halt(self.registration_ref, reason, classification, evidence)

    def complete_report(self, state):
        path = self.root / "final-report.json"
        if not path.exists():
            try:
                write_json(path, schedule.report(self.registration_ref), exclusive=True)
            except FileExistsError:
                # The independent deadline watchdog may publish first. Both
                # observe the same halted original ledger; never replace it.
                pass
        return dict(status=state["status"], schedule=state, report=file_ref(path), input_operations=0)

    def _overall_timing(self):
        deadline = self.config.get("overall_deadline")
        if deadline is None:
            return None
        end = dict(at=utc_now(), monotonic_ns=time.monotonic_ns(), boot_id=boot_id())
        timing = elapsed(dict(started_at=deadline["started_at"], created_at=deadline["started_at"],
            started_monotonic_ns=deadline["started_monotonic_ns"], boot_id=deadline["boot_id"],
            launch_start=None), end=end)
        wall_remaining = (parse_time(deadline["deadline_at"]) - parse_time(end["at"])).total_seconds()
        same_boot = deadline["boot_id"] == end["boot_id"]
        monotonic_remaining = (deadline["timeout_seconds"] -
            (end["monotonic_ns"] - deadline["started_monotonic_ns"]) / 1e9) if same_boot else None
        # Neither a wall-clock rollback nor a process restart extends the cap.
        remaining = min(wall_remaining, monotonic_remaining) if same_boot else wall_remaining
        return dict(observed=end, timing=timing, remaining_seconds=max(0, remaining), expired=remaining <= 0)

    def _check_overall_deadline(self):
        observation = self._overall_timing()
        if observation is None:
            return
        if observation["expired"]:
            raise OverallDeadline("TIMEOUT", "Immutable overall campaign deadline reached; preserve all original attempts",
                                  observation)
        if observation["timing"]["seconds"] is None:
            raise OverallDeadline("UNKNOWN", "Immutable overall campaign clock is unknown; no deadline reset or new work",
                                  observation)

    def poll_delay(self, requested):
        """Wake by the original deadline; never start a fresh polling budget."""
        observation = self._overall_timing()
        return requested if observation is None else min(requested, observation["remaining_seconds"])

    def _deadline_report(self, exc):
        path = self.root / "overall-deadline-observation.json"
        if not path.exists():
            write_json(path, dict(schema="upgrade-driver-deadline-observation-v1", config=self.ref,
                classification=exc.classification, reason=str(exc), **exc.observation, input_operations=0), exclusive=True)
        ref = file_ref(path)
        saved = json_ref(ref)
        require(saved["config"] == self.ref, "original deadline observation changed")
        stopped = self.halt(saved["reason"], saved["classification"], [self.ref, ref])
        return dict(stopped, **self.complete_report(schedule.status(self.registration_ref)))

    def _freeze(self, group, runtime_ref, runtime, adoption_ref, campaign_path):
        verify_tree(self.config["source"])
        verify_tree(self.config["corpus"])
        directory = self.root / "groups" / group
        request = dict(version=3, experiment=runtime["context"], runtime=runtime_ref, adoption=adoption_ref,
            registration=self.registration_ref, session_group=group, source=self.config["source"]["path"],
            corpus=self.config["corpus"]["path"], revision=self.config["revision"],
            native_authorizations=self.config["native_authorizations"])
        path = directory / "freeze-request.json"
        if path.exists():
            request_ref = file_ref(path)
            require(json_ref(request_ref) == request, "original freeze request changed")
        else:
            request_ref = write_json(path, request, exclusive=True)

        def observed():
            complete = campaign_path.parent / "freeze-complete.json"
            if not complete.exists():
                return None
            ref = json_ref(file_ref(complete))["campaign"]
            campaign = validate_campaign(ref)
            require(ref["path"] == str(campaign_path) and campaign["freeze_request"] == request_ref,
                    "original freeze completion changed")
            return dict(status="FROZEN_NOT_MEASURED", campaign=ref, recovered_by_observation=True)

        require_running(self.registration_ref)
        return self.once(group + "-freeze", "freeze", lambda: freeze_campaign(request_ref), observed)

    def _bootstrap(self, group, runtime_ref, spec, campaign_ref):
        instructions_ref = publish_instructions(campaign_ref)

        def observed():
            path = Path(spec["adoption_path"]).parent / "bootstrap.json"
            if not path.exists():
                return None
            ref = file_ref(path)
            value = json_ref(ref)
            require(value["runtime"] == runtime_ref and value["campaign"] == campaign_ref
                    and value["instructions"] == read_ref(instructions_ref).decode("utf-8"),
                    "original bootstrap changed")
            return dict(status="BOOTSTRAP_ALREADY_PUBLISHED", bootstrap=ref, recovered_by_observation=True)

        require_running(self.registration_ref)
        return self.once(group + "-bootstrap", "bootstrap",
            lambda: publish_bootstrap(runtime_ref, campaign_ref, instructions_ref), observed)

    def _clock(self, stamp, key):
        return elapsed(dict(started_at=stamp[key], created_at=stamp[key],
            started_monotonic_ns=stamp["monotonic_ns"], boot_id=stamp["boot_id"], launch_start=None))

    def supervisor_failure(self, campaign_ref, campaign):
        key = campaign_ref["sha256"]
        if key not in self.failure_watches:
            self.failure_watches[key] = CodexFailureWatch(campaign["sessions"]["codex"])
        watch = self.failure_watches[key]
        failure = watch.observe()
        if failure is None:
            if watch.pending:
                return dict(status="WAITING_NATIVE_OBSERVATION", input_operations=0)
            return None
        path = self.root / "faults" / ("native-" + key + ".json")
        if os.path.lexists(path):
            ref = file_ref(path)
            saved = json_ref(ref)
            require(saved.get("campaign") == campaign_ref and saved.get("config") == self.ref,
                    "original native failure belongs to another campaign/configuration")
            parse_time(saved.get("observed_at"))
            watch.verify_saved({k: v for k, v in saved.items()
                                if k not in ("campaign", "config", "observed_at")})
        else:
            ref = write_json(path, dict(campaign=campaign_ref, config=self.ref,
                observed_at=utc_now(), **failure), exclusive=True)
        cause = ("aborted (" + failure["reason"] + ")"
                 if failure["status"] == "NATIVE_CODEX_TURN_ABORTED"
                 else "terminated with a native error")
        return self.halt("Original Codex turn " + cause + "; no replay: "
                         + failure["turn_id"], "FAILED", [ref])

    def _tick(self):
        state = schedule.status(self.registration_ref)
        if state["status"] in TERMINAL:
            return self.complete_report(state)
        self._check_overall_deadline()
        step = state["active"] or state["next_step"]
        group, ordinal = step["group"], step["ordinal"]
        runtime_ref = self.config["runtimes"][group]
        if state["active"] is None:
            def observed():
                path = Path(self.registration["root"]) / "schedule/permits" / f"{ordinal:04d}.json"
                if not path.exists():
                    return None
                ref, permit = read_permit(self.registration_ref, ordinal)
                require(permit["runtime"] == runtime_ref, "original permit runtime changed")
                return dict(status="ORIGINAL_PERMIT_ACTIVE", permit=ref, step=permit["step"])
            return self.once(f"{ordinal:04d}-issue", "issue",
                lambda: schedule.issue(self.registration_ref, runtime_ref), observed)
        permit_ref, permit = read_permit(self.registration_ref, ordinal, active=True)
        require(permit["runtime"] == runtime_ref, "active group runtime differs from registration")
        _, runtime = validate_live_runtime(runtime_ref)
        runtime_observation(self.registration, runtime_ref, runtime)
        spec = json_ref(runtime["startup"])
        group_info = group_registration(self.registration_ref, group, runtime["context"], runtime["arm"])
        context = json_ref(runtime["context"])
        campaign_path = Path(context["paths"]["campaigns"]) / runtime["arm"] / group_info["campaign_id"] / "campaign.json"
        launch_action = self.root / "actions" / (group + "-launch")
        if not (launch_action / "intent.json").exists():
            require_running(self.registration_ref)
            verify_tree(self.config["source"])
            verify_tree(self.config["corpus"])
            return self.once(group + "-launch", "launch", lambda: control(runtime_ref, "launch"))
        # A launch intent, with or without a returned result, is consumed forever.
        # Adoption only inspects the originally launched processes and journals.
        launch_path = Path(spec["adoption_path"]).parent / "launch-request.json"
        if launch_path.exists():
            launch_ref = file_ref(launch_path)
            launch = json_ref(launch_ref)
            require(launch["runtime"] == runtime_ref and launch["startup"] == runtime["startup"],
                    "original launch binding changed")
            startup_clock = self._clock(launch, "requested_at")
        else:
            launch_ref = file_ref(launch_action / "intent.json")
            startup_clock = self._clock(json_ref(launch_ref), "started_at")
        if os.path.lexists(spec["stop_path"]):
            return self.halt("Original private runtime was stopped", "STOPPED", [file_ref(spec["stop_path"])])
        bootstrap_action = self.root / "actions" / (group + "-bootstrap")
        bootstrapped = (bootstrap_action / "result.json").exists() or (
            (bootstrap_action / "intent.json").exists() and (Path(spec["adoption_path"]).parent / "bootstrap.json").exists())
        if not bootstrapped:
            if startup_clock["seconds"] is None:
                return self.halt("Original startup clock cannot be measured", "UNKNOWN", [launch_ref])
            if startup_clock["seconds"] >= 900:
                return self.halt("Original startup/bootstrap exceeded the 900-second window", "TIMEOUT", [launch_ref])
        adoption_path = Path(spec["adoption_path"])
        if not adoption_path.exists():
            if not (Path(runtime["state_root"]) / "startup/intent.json").exists():
                return dict(status="WAITING_ORIGINAL_LAUNCH", step=step, input_operations=0)
            require_running(self.registration_ref)
            self._check_overall_deadline()
            result = control(runtime_ref, "adopt")
            return dict(result, step=step)
        adoption_ref = file_ref(adoption_path)
        validate_adoption(runtime_ref, adoption_ref, runtime=runtime)
        if not (self.root / "actions" / (group + "-freeze") / "result.json").exists():
            frozen = self._freeze(group, runtime_ref, runtime, adoption_ref, campaign_path)
            # A complete frozen result observed after a crash is safe to use;
            # no second freeze occurs. Proceed to bootstrap on the next tick.
            if not campaign_path.exists():
                return frozen
        campaign_ref = file_ref(campaign_path)
        campaign = validate_campaign(campaign_ref)
        require(campaign["runtime"] == runtime_ref and campaign["registration"] == self.registration_ref,
                "original frozen campaign changed")
        if not bootstrapped:
            return dict(self._bootstrap(group, runtime_ref, spec, campaign_ref), step=step)
        # Validate the immutable publication, including a crash with no result.
        self._bootstrap(group, runtime_ref, spec, campaign_ref)
        failure = self.supervisor_failure(campaign_ref, campaign)
        if failure is not None and failure["status"] != "WAITING_NATIVE_OBSERVATION":
            return failure
        run_root = Path(campaign["root"]) / "runs" / step["run_id"]
        if failure is None and (run_root / "finish.json").exists():
            def observed_close():
                path = Path(self.registration["root"]) / "schedule/closures" / f"{ordinal:04d}.json"
                if not path.exists():
                    return None
                ref = file_ref(path)
                value = json_ref(ref)
                require(value["campaign"] == campaign_ref and value["permit"] == permit_ref,
                        "original closeout scope changed")
                return dict(status="CLOSED" if value["next_allowed"] else "CLOSED_WITH_FAILURE_PRESERVED", closure=ref)
            return self.once(f"{ordinal:04d}-close", "close",
                lambda: schedule.close(campaign_ref, step["run_id"]), observed_close)
        header_path = run_root / "run.json"
        if header_path.exists():
            header_ref = file_ref(header_path)
            header = json_ref(header_ref)
            require(header["campaign"] == campaign_ref and header["run"]["run_id"] == step["run_id"],
                    "original run clock scope changed")
            timing = elapsed(header)
            clock_ref = header_ref
            for path in sorted((run_root / "events").glob("*.json")):
                event = json_ref(file_ref(path))
                if event["state"] in ("BLOCKED", "FAILED_PRESERVED"):
                    return self.halt("Original supervisor recorded a failed task boundary", "FAILED", [file_ref(path)])
        else:
            # A granted run which never starts cannot hold the driver forever.
            # This is a watchdog, not a fabricated native business measurement.
            intent_ref = file_ref(self.root / "actions" / f"{ordinal:04d}-issue" / "intent.json")
            timing, clock_ref = self._clock(json_ref(intent_ref), "started_at"), intent_ref
        if timing["seconds"] is None:
            return self.halt("Original run/watchdog clock is unknown", "UNKNOWN", [clock_ref])
        if timing["seconds"] > self.registration["contract"]["run_timeout_seconds"]:
            return self.halt("Original registered run exceeded 1800 seconds without complete closeout", "TIMEOUT", [clock_ref])
        if failure is not None:
            return dict(failure, step=step, elapsed_seconds=timing["seconds"])
        return dict(status="WAITING_ORIGINAL_SUPERVISOR", step=step, elapsed_seconds=timing["seconds"], input_operations=0)

    def tick(self):
        schedule.external(self.registration)
        with exclusive_lock(self.root / "publisher.lock"):
            try:
                result = self._tick()
                # A synchronous original operation may cross the boundary.
                # Preserve its returned evidence, then finalize on this tick;
                # do not promise to cancel processes already executing inside it.
                if result["status"] not in TERMINAL and self.config.get("overall_deadline") is not None:
                    if schedule.status(self.registration_ref)["status"] not in TERMINAL:
                        self._check_overall_deadline()
                return result
            except OverallDeadline as exc:
                return self._deadline_report(exc)
            except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
                path = self.root / "faults" / f"{time.time_ns()}.json"
                ref = write_json(path, dict(config=self.ref, at=utc_now(), error=str(exc),
                    error_type=type(exc).__name__, input_operations=0), exclusive=True)
                return self.halt(str(exc), "UNKNOWN", [ref])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configure", "tick", "run"))
    parser.add_argument("--request")
    parser.add_argument("--request-sha256")
    parser.add_argument("--config")
    parser.add_argument("--config-sha256")
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--overall-timeout-seconds", type=int,
        help="configure only: immutable total campaign cap, anchored now; run/tick cannot reset it")
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            require(args.request and args.request_sha256, "pinned driver request required")
            print(json.dumps(configure(anchored_ref(args.request, args.request_sha256),
                overall_timeout_seconds=args.overall_timeout_seconds), sort_keys=True), flush=True)
            return 0
        require(args.overall_timeout_seconds is None, "overall timeout is configure-only; the original deadline is immutable")
        require(args.config and args.config_sha256, "pinned original driver configuration required")
        require(1 <= args.poll_seconds <= 45, "driver observation interval must be between 1 and 45 seconds")
        driver = Driver(anchored_ref(args.config, args.config_sha256))
        # Keep one owner between publication steps, including idle observations.
        # A second CLI must not alternate ticks with the original running driver.
        with exclusive_lock(driver.root / "runner.lock"):
            while True:
                result = driver.tick()
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                if result["status"] in TERMINAL:
                    if "report" not in result:
                        result = driver.tick()
                        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                    return 0 if result["status"] == "COMPLETE" else 2
                if args.command == "tick":
                    return 0
                if result["status"].startswith("WAITING") or result["status"] == "NOT_READY":
                    time.sleep(driver.poll_delay(args.poll_seconds))
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REFUSED_OR_UNKNOWN_PRESERVED", error=str(exc), input_operations=0)), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
