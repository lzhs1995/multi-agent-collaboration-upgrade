#!/usr/bin/env python3
"""Validate and schedule CLI-owned collaboration graphs.

This module is deliberately an orchestration *adapter*, not another agent
runtime. Claude Code or Codex may author a JSON graph, but only the existing
CLI sessions execute its nodes.  The adapter validates the graph, keeps one
writer per scope, schedules independent work, and emits task-pack seeds for
the existing ``mac_harness.py`` lifecycle.

No provider SDK, HTTP client, API key, subprocess, or replacement session is
used here.  A future decision provider may be added behind the same input
contract, but the shipped providers are deterministic rules and an offline
mock only.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from orchestration_common import scopes_conflict


PLAN_VERSION = 1
ALLOWED_EXECUTORS = {"claude", "codex"}
# Opt-in for a v2 plan whose nodes may freeze a complete null executor triple.
# A separate pre-claim step binds such a node; nothing else changes.
UNBOUND_EXECUTOR_POLICY = "bind_before_claim"
NULL_TRIPLE = ("executor", "execution_kind", "transport")
ISOLATED_EXECUTORS = {"codex": "codex", "claude_a": "claude", "claude_b": "claude"}
ALLOWED_GENERATORS = {"claude_code_cli", "codex_cli", "supervisor"}
ALLOWED_DECISION_PROVIDERS = {"rules", "offline_mock"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,63}$")
SURFACE_RE = re.compile(r"^surface:[0-9]+$")


class PlanError(ValueError):
    """Raised when a graph cannot safely enter the collaboration lifecycle."""


def executor_providers(plan):
    """An executor ID addresses a session; it is never a provider name."""
    if type(plan.get("version")) is not int:
        raise PlanError("plan version must be an integer")
    if plan["version"] == 1:
        if "executor_providers" in plan:
            raise PlanError("explicit executor providers require plan version 2")
        return {key: key for key in sorted(ALLOWED_EXECUTORS)}
    if plan["version"] == 2 and plan.get("executor_providers") == ISOLATED_EXECUTORS:
        return dict(ISOLATED_EXECUTORS)
    raise PlanError("plan v2 requires one Codex and two explicitly named Claude sessions")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json_atomic(path: Path, value: Any) -> str:
    """Write one JSON artifact without replacing a symlink or partial file."""
    path = Path(path)
    if path.is_symlink():
        raise PlanError(f"refuse to replace symlink: {path}")
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(value)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return _sha256_bytes(payload)


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise PlanError(f"refuse to read symlink plan: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read JSON plan {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanError("plan root must be a JSON object")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{label} must be a non-empty string")
    return value.strip()


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PlanError(f"{label} must be an array")
    return value


def _valid_source(value: Any, label: str) -> list[str]:
    items = _require_list(value, label)
    if not items:
        raise PlanError(f"{label} must contain at least one entry")
    result: list[str] = []
    for index, item in enumerate(items):
        text = _require_text(item, f"{label}[{index}]")
        if not (text.startswith("/") or text.startswith("https://") or text.startswith("revision:")):
            raise PlanError(f"{label}[{index}] must be an absolute path, https URL, or revision pin")
        result.append(text)
    return result


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a graph and return a detached normalized copy.

    Validation is intentionally stricter than a generic DAG parser.  It
    prevents a plan from silently changing provider, replacing a session, or
    introducing a third-party API path before any task-pack is materialized.
    """
    if not isinstance(plan, dict):
        raise PlanError("plan root must be an object")
    normalized = copy.deepcopy(plan)
    providers = executor_providers(normalized)
    if not ID_RE.fullmatch(_require_text(normalized.get("plan_id"), "plan_id")):
        raise PlanError("plan_id has an invalid shape")
    _require_text(normalized.get("objective"), "objective")

    source = normalized.get("source")
    if not isinstance(source, dict):
        raise PlanError("source must identify who authored the graph")
    generated_by = _require_text(source.get("generated_by"), "source.generated_by")
    if generated_by not in ALLOWED_GENERATORS:
        raise PlanError(f"source.generated_by must be one of {sorted(ALLOWED_GENERATORS)}")
    _require_text(source.get("revision"), "source.revision")

    api_policy = normalized.get("api_policy")
    if not isinstance(api_policy, dict):
        raise PlanError("api_policy is required")
    if api_policy.get("third_party_api") is not False:
        raise PlanError("api_policy.third_party_api must be false")
    owners = api_policy.get("model_call_owners")
    if not isinstance(owners, list) or not owners or not set(owners) <= {"claude_code_cli", "codex_cli"}:
        raise PlanError("model_call_owners must contain only claude_code_cli/codex_cli")
    decision_provider = normalized.get("decision_provider", "rules")
    if decision_provider not in ALLOWED_DECISION_PROVIDERS:
        raise PlanError(f"decision_provider must be one of {sorted(ALLOWED_DECISION_PROVIDERS)}")

    # The key must be present to opt in; an explicit null is a refusal, not a
    # silent "no", so an authoring slip can never look like the opted-in shape.
    if "unbound_executor_policy" in normalized:
        if normalized["version"] != 2:
            raise PlanError("unbound_executor_policy requires plan version 2")
        if normalized["unbound_executor_policy"] != UNBOUND_EXECUTOR_POLICY:
            raise PlanError(f"unbound_executor_policy must be exactly {UNBOUND_EXECUTOR_POLICY}")
    unbound_policy = normalized.get("unbound_executor_policy") == UNBOUND_EXECUTOR_POLICY

    nodes = _require_list(normalized.get("nodes"), "nodes")
    if not nodes:
        raise PlanError("nodes must contain at least one node")
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise PlanError(f"nodes[{index}] must be an object")
        node_id = _require_text(node.get("id"), f"nodes[{index}].id")
        if not ID_RE.fullmatch(node_id):
            raise PlanError(f"nodes[{index}].id has an invalid shape")
        if node_id in by_id:
            raise PlanError(f"duplicate node id: {node_id}")
        # Only an opted-in plan may declare a null member of the triple, and
        # then all three must be explicitly null: an explicit null is auditable
        # in the frozen bytes while an omitted key is indistinguishable from an
        # authoring slip. The provider/transport derivation is skipped because
        # the bound lane, and therefore both derived values, is unknown here.
        declared_null = [key for key in NULL_TRIPLE if key in node and node[key] is None]
        if unbound_policy and declared_null:
            if sorted(declared_null) != sorted(NULL_TRIPLE):
                raise PlanError(f"{node_id} must freeze a complete null executor triple")
        else:
            executor = _require_text(node.get("executor"), f"nodes[{index}].executor")
            if executor not in providers:
                raise PlanError(f"{node_id}.executor must be an explicitly bound CLI session")
            transport = _require_text(node.get("transport"), f"{node_id}.transport")
            expected_transport = {"claude": "claude_code_cli", "codex": "codex_cli"}[providers[executor]]
            if transport != expected_transport:
                raise PlanError(f"{node_id}.transport must be {expected_transport}")
        if node.get("session_policy") != "reuse_existing":
            raise PlanError(f"{node_id}.session_policy must be reuse_existing")
        surface = node.get("surface_ref")
        if surface is not None and (not isinstance(surface, str) or not SURFACE_RE.fullmatch(surface)):
            raise PlanError(f"{node_id}.surface_ref must be surface:<number>")
        depends_on = node.get("depends_on", [])
        if not isinstance(depends_on, list) or any(not isinstance(dep, str) or not dep for dep in depends_on):
            raise PlanError(f"{node_id}.depends_on must be an array of ids")
        if len(depends_on) != len(set(depends_on)):
            raise PlanError(f"{node_id}.depends_on contains duplicates")
        if node_id in depends_on:
            raise PlanError(f"{node_id} cannot depend on itself")
        _require_text(node.get("title"), f"{node_id}.title")
        _require_text(node.get("context"), f"{node_id}.context")
        _require_text(node.get("scope"), f"{node_id}.scope")
        _require_text(node.get("forbidden"), f"{node_id}.forbidden")
        _require_text(node.get("verify"), f"{node_id}.verify")
        _valid_source(node.get("source"), f"{node_id}.source")
        write_scope = _require_text(node.get("write_scope"), f"{node_id}.write_scope")
        if "output_scope" in node:
            if normalized["version"] != 2:
                raise PlanError("output_scope requires plan version 2")
            output_scope = _require_text(node["output_scope"], f"{node_id}.output_scope")
            for label, value in (("write_scope", write_scope), ("output_scope", output_scope)):
                path = Path(value)
                if (path.is_absolute() or ".." in path.parts or value in ("", ".") or
                        str(path) != value or node[label] != value):
                    raise PlanError(f"{node_id}.{label} must be a canonical relative child")
            if Path(write_scope) not in (Path(output_scope), *Path(output_scope).parents):
                raise PlanError(f"{node_id}.output_scope must be inside write_scope")
            node["output_scope"] = output_scope
        expected_seconds = node.get("expected_seconds")
        if isinstance(expected_seconds, bool) or not isinstance(expected_seconds, (int, float)) or not math.isfinite(expected_seconds) or expected_seconds <= 0:
            raise PlanError(f"{node_id}.expected_seconds must be positive")
        retry_seconds = node.get("retry_seconds", 0)
        if isinstance(retry_seconds, bool) or not isinstance(retry_seconds, (int, float)) or not math.isfinite(retry_seconds) or retry_seconds < 0:
            raise PlanError(f"{node_id}.retry_seconds must be non-negative")
        observed_retries = node.get("observed_retries", 0)
        if isinstance(observed_retries, bool) or not isinstance(observed_retries, int) or observed_retries < 0:
            raise PlanError(f"{node_id}.observed_retries must be a non-negative integer")
        if observed_retries > 3:
            raise PlanError(f"{node_id}.observed_retries may not exceed 3")
        node["depends_on"] = list(depends_on)
        node["source"] = _valid_source(node.get("source"), f"{node_id}.source")
        node["write_scope"] = write_scope
        node["expected_seconds"] = float(expected_seconds)
        node["retry_seconds"] = float(retry_seconds)
        node["observed_retries"] = observed_retries
        by_id[node_id] = node
        order.append(node_id)

    for node_id, node in by_id.items():
        missing = [dep for dep in node["depends_on"] if dep not in by_id]
        if missing:
            raise PlanError(f"{node_id} depends on unknown node(s): {', '.join(missing)}")

    # Kahn's algorithm is both a cycle check and a cheap proof that the graph
    # can be reduced to dispatchable waves.
    indegree = {node_id: len(node["depends_on"]) for node_id, node in by_id.items()}
    queue = [node_id for node_id in order if indegree[node_id] == 0]
    visited: list[str] = []
    while queue:
        current = queue.pop(0)
        visited.append(current)
        for candidate in order:
            if current in by_id[candidate]["depends_on"]:
                indegree[candidate] -= 1
                if indegree[candidate] == 0:
                    queue.append(candidate)
    if len(visited) != len(by_id):
        stuck = [node_id for node_id in order if node_id not in visited]
        raise PlanError(f"dependency cycle detected involving: {', '.join(stuck)}")
    normalized["nodes"] = [by_id[node_id] for node_id in order]
    normalized["decision_provider"] = decision_provider
    return normalized


def build_schedule(plan: dict[str, Any], max_parallel_per_executor: int = 1) -> list[dict[str, Any]]:
    """Build bounded ready waves while preserving one writer per scope."""
    normalized = validate_plan(plan)
    # A deliberately unbound node has no lane to count against, so refuse the
    # whole graph with a typed error instead of raising KeyError(None) below.
    unbound = [node["id"] for node in normalized["nodes"] if node["executor"] is None]
    if unbound:
        raise PlanError(f"cannot schedule deliberately unbound node(s): {', '.join(unbound)}")
    if type(max_parallel_per_executor) is not int or max_parallel_per_executor != 1:
        raise PlanError("max_parallel_per_executor must be 1 for each existing CLI session")
    nodes = {node["id"]: node for node in normalized["nodes"]}
    order = [node["id"] for node in normalized["nodes"]]
    remaining = set(order)
    completed: set[str] = set()
    waves: list[dict[str, Any]] = []
    while remaining:
        ready = [node_id for node_id in order if node_id in remaining and set(nodes[node_id]["depends_on"]) <= completed]
        selected: list[str] = []
        executor_counts = {executor: 0 for executor in executor_providers(normalized)}
        scopes: set[str] = set()
        for node_id in ready:
            node = nodes[node_id]
            executor = node["executor"]
            if executor_counts[executor] >= max_parallel_per_executor:
                continue
            if any(scopes_conflict(node["write_scope"], scope, normalized.get("scope_root", Path.cwd())) for scope in scopes):
                continue
            selected.append(node_id)
            executor_counts[executor] += 1
            scopes.add(node["write_scope"])
        if not selected:
            raise PlanError("scheduler made no progress; check graph or concurrency limits")
        wave_nodes = [nodes[node_id] for node_id in selected]
        waves.append({
            "wave": len(waves) + 1,
            "nodes": selected,
            "executors": sorted({node["executor"] for node in wave_nodes}),
            "parallelism": len(selected),
            "estimated_seconds": max(_node_runtime(node) for node in wave_nodes),
        })
        remaining.difference_update(selected)
        completed.update(selected)
    return waves


def _node_runtime(node: dict[str, Any]) -> float:
    return float(node["expected_seconds"]) + float(node.get("retry_seconds", 0)) * int(node.get("observed_retries", 0))


def benchmark(plan: dict[str, Any], max_parallel_per_executor: int = 1) -> dict[str, Any]:
    """Return a deterministic schedule estimate; never claim live CLI results."""
    normalized = validate_plan(plan)
    waves = build_schedule(normalized, max_parallel_per_executor=max_parallel_per_executor)
    nodes = {node["id"]: node for node in normalized["nodes"]}
    serial_seconds = sum(_node_runtime(node) for node in normalized["nodes"])
    scheduled_seconds = sum(float(wave["estimated_seconds"]) for wave in waves)
    reduction = 0.0 if serial_seconds == 0 else (serial_seconds - scheduled_seconds) / serial_seconds * 100.0
    return {
        "plan_id": normalized["plan_id"],
        "evidence_class": "offline_schedule_estimate",
        "claim": "This is always an offline schedule estimate. Measured promotion requires the separate v2 verifier.",
        "nodes": len(nodes),
        "waves": len(waves),
        "parallel_waves": sum(1 for wave in waves if wave["parallelism"] > 1),
        "serial_seconds": round(serial_seconds, 3),
        "scheduled_seconds": round(scheduled_seconds, 3),
        "wall_time_reduction_percent": round(reduction, 3),
        "speedup": round(serial_seconds / scheduled_seconds, 3) if scheduled_seconds else None,
        "meets_fifteen_percent_target": reduction >= 15.0,
        "schedule": waves,
    }


def _task_seed(plan: dict[str, Any], node: dict[str, Any], artifact_root: Path, run_id: str) -> dict[str, Any]:
    task_id = f"{plan['plan_id']}--{run_id}--{node['id']}--a0001"
    seed = {
        "seed_version": 2,
        "task_id": task_id,
        "run_id": run_id,
        "attempt_id": f"{run_id}:{node['id']}:a0001",
        "orchestration": {
            "plan_id": plan["plan_id"],
            "node_id": node["id"],
            "depends_on": node["depends_on"],
            "write_scope": node["write_scope"],
        },
        "role": "executor",
        "executor": executor_providers(plan)[node["executor"]],
        "executor_surface": node.get("surface_ref"),
        "transport": node["transport"],
        "session_policy": "reuse_existing",
        "context": node["context"],
        "source": node["source"],
        "scope": node["scope"],
        "forbidden": node["forbidden"],
        "verify": node["verify"],
        "required_skill": str((Path(__file__).resolve().parent.parent / "SKILL.md").resolve()),
        "artifact_root": str((artifact_root / task_id).resolve()),
        "api_policy": {
            "third_party_api": False,
            "model_call_owner": node["transport"],
        },
        "dispatch": {
            "next": "Run mac_harness.py preflight, then task-pack/finalize-pack; dispatch only via cmux_bridge.",
            "replacement_session": False,
            "direct_cli_launch": False,
        },
    }
    if plan["version"] == 2:
        seed["orchestration"]["executor_id"] = node["executor"]
        seed["orchestration"]["plan_version"] = 2
    return seed


def materialize(plan: dict[str, Any], output_root: Path, max_parallel_per_executor: int = 1, *, run_id: str | None = None) -> dict[str, Any]:
    """Write a graph, schedule and task-pack seeds for later guarded dispatch."""
    normalized = validate_plan(plan)
    output_root = Path(output_root)
    if output_root.is_symlink():
        raise PlanError(f"refuse to materialize through symlink: {output_root}")
    output_root = output_root.absolute()
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise PlanError("materialize requires an empty output root; preserve existing run artifacts")
    run_id = run_id or uuid.uuid4().hex
    if not ID_RE.fullmatch(run_id):
        raise PlanError("run_id has an invalid shape")
    schedule = build_schedule(normalized, max_parallel_per_executor=max_parallel_per_executor)
    plan_sha = _write_json_atomic(output_root / "orchestration-plan.json", normalized)
    schedule_doc = {
        "version": 1,
        "plan_id": normalized["plan_id"],
        "run_id": run_id,
        "created_at": _now(),
        "plan_sha256": plan_sha,
        "max_parallel_per_executor": max_parallel_per_executor,
        "waves": schedule,
        "dispatch_contract": "existing multi-agent-collaboration mac_harness + cmux_bridge",
    }
    schedule_sha = _write_json_atomic(output_root / "orchestration-schedule.json", schedule_doc)
    seed_dir = output_root / "task-pack-seeds"
    seed_hashes: dict[str, str] = {}
    for node in normalized["nodes"]:
        seed_path = seed_dir / f"{node['id']}.json"
        seed_hashes[node["id"]] = _write_json_atomic(seed_path, _task_seed(normalized, node, output_root, run_id))
    manifest = {
        "version": 1,
        "plan_id": normalized["plan_id"],
        "run_id": run_id,
        "created_at": _now(),
        "plan_sha256": plan_sha,
        "schedule_sha256": schedule_sha,
        "seed_sha256": seed_hashes,
        "next_step": "Bind each seed to a live identity-gate and run the existing task-pack/finalize-pack flow.",
        "live_model_calls": "Claude Code CLI or Codex CLI only",
        "third_party_api": False,
    }
    manifest_sha = _write_json_atomic(output_root / "orchestration-manifest.json", manifest)
    return {"manifest": str(output_root / "orchestration-manifest.json"), "manifest_sha256": manifest_sha, **manifest}


def _cmd_validate(args: argparse.Namespace) -> int:
    plan = validate_plan(_load_json(Path(args.plan)))
    print(json.dumps({"status": "PASS", "plan_id": plan["plan_id"], "nodes": len(plan["nodes"])}, ensure_ascii=False, indent=2))
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    plan = _load_json(Path(args.plan))
    output = {"status": "PASS", "plan_id": plan.get("plan_id"), "waves": build_schedule(plan, args.max_parallel_per_executor)}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    output = benchmark(_load_json(Path(args.plan)), args.max_parallel_per_executor)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _cmd_materialize(args: argparse.Namespace) -> int:
    output = materialize(_load_json(Path(args.plan)), Path(args.output_root), args.max_parallel_per_executor, run_id=args.run_id)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "schedule", "benchmark", "materialize"):
        child = sub.add_parser(name)
        child.add_argument("--plan", required=True, help="path to the CLI-authored JSON graph")
        child.add_argument("--max-parallel-per-executor", type=int, default=1)
        if name == "materialize":
            child.add_argument("--output-root", required=True)
            child.add_argument("--run-id", help="unique run identity; defaults to a new UUID")
    sub.choices["validate"].set_defaults(handler=_cmd_validate)
    sub.choices["schedule"].set_defaults(handler=_cmd_schedule)
    sub.choices["benchmark"].set_defaults(handler=_cmd_benchmark)
    sub.choices["materialize"].set_defaults(handler=_cmd_materialize)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except PlanError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
