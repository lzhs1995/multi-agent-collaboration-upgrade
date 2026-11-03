"""Bounded first-start file continuation; never a recurring scheduler or sender.

The external controller alone can publish live-control/bootstrap.json. The
supervisor reads its pinned instructions in its original startup turn. All
business delivery and later wakeups still belong to the original MAC bridge.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import time

from orchestration_common import (ContractError, file_ref, json_ref, require, within)
from upgrade_native_roots import configuration


def read_instruction(binding):
    directory = Path(binding["evidence_root"]) / "live-control"
    require(not os.path.lexists(directory / "stop.json"), "EXPERIMENT_STOP_REQUESTED")
    path = directory / "bootstrap.json"
    if not os.path.lexists(path):
        return None
    value = json_ref(file_ref(path), "startup continuation")
    require(set(value) == {"schema", "runtime", "startup", "adoption", "campaign", "instructions"}
            and value["schema"] == "upgrade-bootstrap-v1", "BOOTSTRAP_FIELDS_CHANGED")
    startup = file_ref(Path(binding["config_root"]) / "startup-v2.json")
    require(value["startup"] == startup, "BOOTSTRAP_STARTUP_CHANGED")
    runtime = json_ref(value["runtime"], "bootstrap runtime")
    require(runtime["startup"] == startup and runtime["context"] == binding["context"]
            and runtime["arm"] == binding["arm"]
            and runtime["controller"]["path"] == binding["controller_root"], "BOOTSTRAP_RUNTIME_CHANGED")
    require(value["adoption"] == file_ref(directory / "adoption.json"), "BOOTSTRAP_ADOPTION_CHANGED")
    proof = json_ref(value["adoption"], "bootstrap adoption")
    require(proof.get("schema") == "upgrade-live-adoption-v2" and proof.get("verified") is True
            and proof["runtime"] == value["runtime"] and proof["startup"] == startup,
            "BOOTSTRAP_NOT_ADOPTED")
    within(value["campaign"]["path"], binding["campaign_root"])
    campaign = json_ref(value["campaign"], "bootstrap campaign")
    require(campaign.get("version") == 3 and campaign.get("evidence_class") == "cli_campaign_v3"
            and campaign["runtime"] == value["runtime"]
            and campaign["client_isolation"] == value["adoption"], "BOOTSTRAP_CAMPAIGN_CHANGED")
    require(isinstance(value["instructions"], str) and 0 < len(value["instructions"]) < 24000,
            "BOOTSTRAP_INSTRUCTIONS_MISSING")
    require(not os.path.lexists(directory / "stop.json"), "EXPERIMENT_STOP_REQUESTED")
    return dict(status="BOOTSTRAP_READY", campaign=value["campaign"],
                instructions=value["instructions"], input_operations=0)


def wait_instruction(binding, timeout_seconds):
    require(type(timeout_seconds) in (int, float) and 0 <= timeout_seconds <= 900,
            "BOOTSTRAP_TIMEOUT_OUT_OF_RANGE")
    directory = Path(binding["evidence_root"]) / "live-control"
    deadline = time.monotonic() + timeout_seconds
    descriptor = queue = None
    try:
        # Register before the first read so publication cannot race the wait.
        if hasattr(select, "kqueue"):
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            queue = select.kqueue()
            event = select.kevent(descriptor, filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE)
            queue.control([event], 0, 0)
        while True:
            value = read_instruction(binding)
            if value is not None:
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return dict(status="BOOTSTRAP_TIMEOUT", input_operations=0, sessions_preserved=True)
            if queue is not None:
                queue.control(None, 1, min(remaining, 10))
            else:
                time.sleep(min(remaining, 0.2))
    finally:
        if queue is not None:
            queue.close()
        if descriptor is not None:
            os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        binding, _ = configuration()
        require(os.environ.get("UPGRADE_CLIENT_LANE") == "codex", "BOOTSTRAP_SUPERVISOR_ONLY")
        value = wait_instruction(binding, args.timeout_seconds)
        print(json.dumps(value, ensure_ascii=False))
        return 0 if value["status"] == "BOOTSTRAP_READY" else 3
    except (ContractError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(dict(status="BOOTSTRAP_REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
