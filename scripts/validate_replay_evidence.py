#!/usr/bin/env python3
"""Historical v1 claim inspection; never a measured replay or promotion gate.

V1 accepts self-reported proofs without opening original native records. Even
structurally valid inputs remain HISTORICAL_ONLY / legacy_unverified_claims.
Use orchestration_run.py with a separately pinned v2 campaign for live evidence.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from orchestration_planner import PlanError, build_schedule, validate_plan


EVIDENCE_VERSION = 1
CLI_TRANSPORTS = {"claude": "claude_code_cli", "codex": "codex_cli"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_PROOFS = ("native_receipt", "ack", "callback", "closeout", "acceptance")


class EvidenceError(ValueError):
    """Raised when a replay cannot support a measured-gain claim."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{label} must be a non-empty string")
    return value.strip()


def _json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise EvidenceError(f"refuse symlink evidence: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read evidence {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError("evidence root must be an object")
    return value


def _time(value: Any, label: str) -> datetime:
    raw = _text(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceError(f"{label} must include a timezone")
    return parsed


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError(f"{label} must be a non-negative integer")
    return value


def _proof(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    if value.get("status") != "PASS":
        raise EvidenceError(f"{label}.status must be PASS")
    source = _text(value.get("source"), f"{label}.source")
    if not (source.startswith("/") or source.startswith("https://")):
        raise EvidenceError(f"{label}.source must be an absolute path or https URL")
    return value


def _validate_node(
    node: dict[str, Any],
    expected: dict[str, Any],
    mode: str,
    index: int,
    expected_wave: int | None = None,
) -> tuple[datetime, datetime, int, int]:
    label = f"runs[{mode}].nodes[{index}]"
    node_id = _text(node.get("node_id"), f"{label}.node_id")
    if node_id != expected["id"]:
        raise EvidenceError(f"{label}.node_id must be {expected['id']}")
    task_id = _text(node.get("task_id"), f"{label}.task_id")
    expected_task_id = f"{expected.get('_plan_id', '')}--{node_id}"
    if task_id != expected_task_id:
        raise EvidenceError(f"{label}.task_id must be {expected_task_id}")
    if node.get("executor") != expected["executor"]:
        raise EvidenceError(f"{label}.executor does not match the plan")
    if node.get("transport") != CLI_TRANSPORTS[expected["executor"]]:
        raise EvidenceError(f"{label}.transport must be the owning CLI transport")
    if node.get("session_policy") != "reuse_existing":
        raise EvidenceError(f"{label}.session_policy must be reuse_existing")
    if node.get("replacement_session") is not False:
        raise EvidenceError(f"{label}.replacement_session must be false")
    if mode == "scheduled":
        wave = node.get("wave")
        if isinstance(wave, bool) or not isinstance(wave, int) or wave != expected_wave:
            raise EvidenceError(f"{label}.wave must be scheduled wave {expected_wave}")
    started = _time(node.get("started_at"), f"{label}.started_at")
    finished = _time(node.get("finished_at"), f"{label}.finished_at")
    if finished < started:
        raise EvidenceError(f"{label}.finished_at precedes started_at")
    for proof_name in REQUIRED_PROOFS:
        _proof(node.get(proof_name), f"{label}.{proof_name}")
    report_sha = _text(node.get("report_sha256"), f"{label}.report_sha256")
    if not SHA256_RE.fullmatch(report_sha):
        raise EvidenceError(f"{label}.report_sha256 must be a lowercase SHA-256")
    retries = _nonnegative_int(node.get("retries", 0), f"{label}.retries")
    interventions = _nonnegative_int(node.get("interventions", 0), f"{label}.interventions")
    return started, finished, retries, interventions


def validate_replay_evidence(
    plan: dict[str, Any], evidence: dict[str, Any], min_reduction_percent: float = 15.0
) -> dict[str, Any]:
    """Inspect legacy structure only; v1 self-reports cannot prove a replay.

    Kept for reading historical artifacts. Promotion requires the separately
    anchored v2 campaign verifier, original CLI journals and frozen checker.
    """
    normalized = validate_plan(plan)
    if evidence.get("version") != EVIDENCE_VERSION:
        raise EvidenceError(f"version must be {EVIDENCE_VERSION}")
    if evidence.get("evidence_class") != "measured_cli_replay":
        raise EvidenceError("evidence_class must be measured_cli_replay")
    if evidence.get("plan_id") != normalized["plan_id"]:
        raise EvidenceError("evidence.plan_id does not match the plan")
    if evidence.get("third_party_api") is not False:
        raise EvidenceError("third_party_api must be false")
    if evidence.get("replacement_sessions") is not False:
        raise EvidenceError("replacement_sessions must be false")
    if evidence.get("external_model_calls", 0) != 0:
        raise EvidenceError("external_model_calls must be zero")
    if isinstance(min_reduction_percent, bool) or min_reduction_percent < 0:
        raise EvidenceError("min_reduction_percent must be non-negative")
    max_parallel = evidence.get("max_parallel_per_executor", 1)
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or max_parallel < 1:
        raise EvidenceError("max_parallel_per_executor must be a positive integer")
    expected_waves = build_schedule(normalized, max_parallel_per_executor=max_parallel)
    expected_wave_by_node = {
        node_id: wave["wave"]
        for wave in expected_waves
        for node_id in wave["nodes"]
    }
    parallel_waves = sum(1 for wave in expected_waves if wave["parallelism"] > 1)
    if parallel_waves == 0:
        raise EvidenceError("scheduled plan contains no parallel wave to measure")

    runs = evidence.get("runs")
    if not isinstance(runs, list) or len(runs) != 2:
        raise EvidenceError("runs must contain exactly serial and scheduled evidence")
    by_mode: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict):
            raise EvidenceError("each run must be an object")
        mode = _text(run.get("mode"), "run.mode")
        if mode not in {"serial", "scheduled"} or mode in by_mode:
            raise EvidenceError("runs must contain one serial and one scheduled run")
        by_mode[mode] = run
    if set(by_mode) != {"serial", "scheduled"}:
        raise EvidenceError("runs must contain one serial and one scheduled run")

    expected_nodes = []
    expected_by_id: dict[str, dict[str, Any]] = {}
    for raw in normalized["nodes"]:
        expected = dict(raw)
        expected["_plan_id"] = normalized["plan_id"]
        expected_nodes.append(expected)
        expected_by_id[expected["id"]] = expected

    metrics: dict[str, Any] = {}
    for mode, run in by_mode.items():
        if run.get("status") != "PASS":
            raise EvidenceError(f"runs[{mode}].status must be PASS")
        run_started = _time(run.get("started_at"), f"runs[{mode}].started_at")
        run_finished = _time(run.get("finished_at"), f"runs[{mode}].finished_at")
        if run_finished < run_started:
            raise EvidenceError(f"runs[{mode}] finishes before it starts")
        raw_nodes = run.get("nodes")
        if not isinstance(raw_nodes, list) or len(raw_nodes) != len(expected_nodes):
            raise EvidenceError(f"runs[{mode}].nodes must contain every plan node exactly once")
        seen: set[str] = set()
        parsed: list[tuple[str, datetime, datetime, int, int]] = []
        for index, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, dict):
                raise EvidenceError(f"runs[{mode}].nodes[{index}] must be an object")
            node_id = _text(raw_node.get("node_id"), f"runs[{mode}].nodes[{index}].node_id")
            if node_id in seen or node_id not in expected_by_id:
                raise EvidenceError(f"runs[{mode}] has duplicate or unknown node {node_id}")
            seen.add(node_id)
            started, finished, retries, interventions = _validate_node(
                raw_node,
                expected_by_id[node_id],
                mode,
                index,
                expected_wave_by_node.get(node_id) if mode == "scheduled" else None,
            )
            if started < run_started or finished > run_finished:
                raise EvidenceError(f"runs[{mode}].nodes[{index}] lies outside the run boundary")
            parsed.append((node_id, started, finished, retries, interventions))
        if seen != set(expected_by_id):
            raise EvidenceError(f"runs[{mode}] is missing one or more plan nodes")
        if mode == "serial":
            ordered = [item[0] for item in parsed]
            expected_order = [item["id"] for item in expected_nodes]
            if ordered != expected_order:
                raise EvidenceError("serial nodes must follow the plan order")
            for previous, current in zip(parsed, parsed[1:]):
                if current[1] < previous[2]:
                    raise EvidenceError("serial nodes overlap")
        metrics[mode] = {
            "elapsed_seconds": round((run_finished - run_started).total_seconds(), 3),
            "retries": sum(item[3] for item in parsed),
            "interventions": sum(item[4] for item in parsed),
            "nodes": len(parsed),
            "parallel_waves": parallel_waves if mode == "scheduled" else 0,
        }

    serial_seconds = metrics["serial"]["elapsed_seconds"]
    scheduled_seconds = metrics["scheduled"]["elapsed_seconds"]
    if serial_seconds <= 0 or scheduled_seconds <= 0:
        raise EvidenceError("run durations must be positive")
    reduction = (serial_seconds - scheduled_seconds) / serial_seconds * 100.0
    correctness = evidence.get("correctness")
    if not isinstance(correctness, dict) or correctness.get("status") != "PASS":
        raise EvidenceError("correctness.status must be PASS")
    checks = correctness.get("checks")
    if isinstance(checks, bool) or not isinstance(checks, int) or checks < 1:
        raise EvidenceError("correctness.checks must be a positive integer")
    return {
        "status": "HISTORICAL_ONLY",
        "structure_status": "PASS",
        "evidence_class": "legacy_unverified_claims",
        "promotion_eligible": False,
        "reason": "v1 contains self-reported proofs; use orchestration_verify.verify_campaign with a trusted campaign SHA256",
        "plan_id": normalized["plan_id"],
        "serial_seconds": serial_seconds,
        "scheduled_seconds": scheduled_seconds,
        "wall_time_reduction_percent": round(reduction, 3),
        "speedup": round(serial_seconds / scheduled_seconds, 3),
        "minimum_reduction_percent": min_reduction_percent,
        "meets_target": False,
        "claimed_meets_target": reduction >= min_reduction_percent,
        "correctness_checks": checks,
        "parallel_waves": metrics["scheduled"]["parallel_waves"],
        "retries": {
            "serial": metrics["serial"]["retries"],
            "scheduled": metrics["scheduled"]["retries"],
        },
        "interventions": {
            "serial": metrics["serial"]["interventions"],
            "scheduled": metrics["scheduled"]["interventions"],
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--min-reduction-percent", type=float, default=15.0)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = validate_replay_evidence(
            _json(Path(args.plan)), _json(Path(args.evidence)), args.min_reduction_percent
        )
    except (EvidenceError, PlanError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Exit 3 distinguishes a readable historical artifact from a measured PASS.
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
