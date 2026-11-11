#!/usr/bin/env python3
"""Independent immutable-deadline observer. Stops input, never kills clients.

Start alongside upgrade_driver run using the same pinned v2 configuration.
Stop publication does not wait for the driver publisher/runner lock or perform
whole-controller integrity scans. Already executing calls and filesystem I/O
cannot be synchronously cancelled by these markers; existing native gates must
observe them before subsequent input. Original processes and leases survive.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from orchestration_campaign import anchored_ref
from orchestration_common import (ContractError, checked_path, exclusive_lock, file_ref,
    json_ref, require, utc_now, write_json)
from upgrade_driver import Driver, TERMINAL
from upgrade_live_control import external_publisher, stop_scope
import upgrade_schedule as schedule


def stop_runtime_input(runtime_ref):
    """Publish the original runtime stop with only its pinned private scope."""
    runtime, spec = stop_scope(runtime_ref)
    # stop_scope intentionally omits executable/configuration validation. The
    # external publisher still needs the controller authority for its kernel
    # sandbox query; obtain that path from this same pinned runtime.
    external_publisher(dict(spec, controller=runtime["controller"]["path"]))
    path = checked_path(spec["stop_path"])
    if not os.path.lexists(path):
        try:
            write_json(path, dict(schema="upgrade-stop-v2", runtime=runtime_ref,
                startup=runtime["startup"], requested_at=utc_now(), reason="immutable campaign watchdog stop",
                input_operations=0, terminate_clients=False), exclusive=True)
        except FileExistsError:
            pass  # Concurrent file publication is observed, never overwritten.
    ref = file_ref(path)
    saved = json_ref(ref, "original watchdog stop marker")
    require(saved.get("schema") == "upgrade-stop-v2" and saved.get("runtime") == runtime_ref
            and saved.get("startup") == runtime["startup"] and saved.get("terminate_clients") is False,
            "STOP_MARKER_SCOPE_CHANGED")
    return dict(status="STOPPED_INPUT", stop=ref, clients_preserved=True, input_operations=0)


class Watchdog:
    def __init__(self, ref):
        self.driver = Driver(ref)
        require(self.driver.config.get("overall_deadline") is not None,
                "watchdog requires an original v2 configuration with an immutable overall deadline")
        self.root = self.driver.root / "watchdog"

    def _stops(self):
        path = self.root / "stop-results.json"
        if path.exists():
            ref = file_ref(path)
            saved = json_ref(ref)
            require(saved["config"] == self.driver.ref, "original watchdog stop scope changed")
            return ref, saved
        results = {}
        # Each registered group is attempted even when another scope is damaged.
        # No model launch, task dispatch, callback or lease mutation occurs here.
        for group, runtime_ref in self.driver.config["runtimes"].items():
            try:
                results[group] = stop_runtime_input(runtime_ref)
            except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
                results[group] = dict(status="STOP_FAILED_PRESERVED", error=str(exc),
                    error_type=type(exc).__name__, runtime=runtime_ref, input_operations=0)
        value = dict(schema="upgrade-watchdog-stops-v1", config=self.driver.ref, observed_at=utc_now(),
            groups=results, all_stopped=all(v["status"] == "STOPPED_INPUT" for v in results.values()),
            clients_preserved=True, leases_preserved=True, input_operations=0)
        return write_json(path, value, exclusive=True), value

    def tick(self):
        schedule.external(self.driver.registration)
        with exclusive_lock(self.root / "publisher.lock"):
            try:
                observation = self.driver._overall_timing()
            except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
                observation = dict(expired=False, remaining_seconds=0,
                    timing=dict(seconds=None, clock_status="clock_read_failed"), error=str(exc))
            classification = ("TIMEOUT" if observation["expired"] else
                "UNKNOWN" if observation["timing"]["seconds"] is None else None)
            state = None
            if classification is None:
                state = schedule.status(self.driver.registration_ref)
                if state["status"] not in TERMINAL:
                    return dict(status="WAITING_ORIGINAL_DEADLINE", remaining_seconds=observation["remaining_seconds"],
                        deadline=self.driver.config["overall_deadline"], input_operations=0)
            # Critically, stop all seven first. Neither driver lock is acquired.
            stops_ref, stops = self._stops()
            try:
                if state is None:
                    state = schedule.status(self.driver.registration_ref)
                if state["status"] not in TERMINAL:
                    self.driver.halt("Independent watchdog reached immutable overall deadline" if classification == "TIMEOUT"
                        else "Independent watchdog cannot authenticate the original campaign clock",
                        classification, [self.driver.ref, stops_ref])
                state = schedule.status(self.driver.registration_ref)
                result = self.driver.complete_report(state)
            except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
                # A short schedule transaction may still own its independent
                # publisher lock. The input stops are already durable. Subsequent
                # ticks only observe them and finalize the same original ledger.
                return dict(status="WAITING_FINALIZATION", stops=stops_ref, all_stopped=stops["all_stopped"],
                    error=str(exc), clients_preserved=True, input_operations=0)
            return dict(result, stops=stops_ref, all_stopped=stops["all_stopped"], clients_preserved=True,
                leases_preserved=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "tick"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args(argv)
    try:
        require(0 < args.poll_seconds <= 10, "watchdog polling interval must be in (0, 10] seconds")
        watcher = Watchdog(anchored_ref(args.config, args.config_sha256))
        with exclusive_lock(watcher.root / "runner.lock"):
            while True:
                result = watcher.tick()
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                if result["status"] in TERMINAL:
                    return 0 if result["status"] == "COMPLETE" and result["all_stopped"] else 2
                if args.command == "tick":
                    return 0
                delay = (args.poll_seconds if result["status"] == "WAITING_FINALIZATION"
                         else min(args.poll_seconds, result["remaining_seconds"]))
                time.sleep(delay)
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REFUSED_OR_UNKNOWN_PRESERVED", error=str(exc), input_operations=0)), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
