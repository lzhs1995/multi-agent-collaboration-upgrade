"""Advisory scheduling from recorded evidence, without checks, input or writes.

This view reads the recorded lifecycle without running the business checker.
Recorded acceptance is explicitly unrevalidated here;
all commands that act on it retain their existing full acceptance/identity gates.
Drafts have no dispatch nonce, reservation or accepted dependency output binding.
"""
from __future__ import annotations

import re
from pathlib import Path

from orchestration_binding import binding_path, bound_lane, claimed_lanes, is_unbound
from orchestration_campaign import node_output, node_write_scope
from orchestration_common import (checked_path, contract_errors, file_ref, json_ref, object_sha,
                                  parse_time, read_json, read_ref, scopes_conflict, verify_tree)
from orchestration_coordinator import ACTIVE, TRANSITIONS, State, select_ready, topological_nodes
from orchestration_continuation import observe_continuation
from orchestration_evidence import task_identity
from orchestration_native import NativeReader, native_verification, require
from orchestration_planner import validate_plan
from orchestration_timeline import node_timeline


NODE_EVIDENCE = ("dispatch-intent.json", "candidate.json", "accepted-evidence.json",
                 "failure-candidate.json", "settled-failure.json", "disarm.json", "continuation.json")


def _control_fence(co):
    """Detect control publication that can precede its lifecycle/lease update.

    Inspect bounded control files, not growing business output trees. This is
    a read consistency check, not a lock or authority to act on the result.
    """
    result = {}

    def pin(path):
        result[str(path.relative_to(co.root))] = (
            file_ref(checked_path(path)) if path.exists() or path.is_symlink() else None)

    for name in ("run.json", "finish.json"):
        pin(co.root / name)
    draft = checked_path(Path(co.campaign["root"]) / "package-drafts" / (co.spec["run_id"] + ".json"))
    result["published_drafts"] = file_ref(draft) if draft.exists() else None
    for key, node in co.nodes.items():
        directory = checked_path(co.root / "nodes" / key)
        output = checked_path(node_output(co.root, node))
        result["node:" + key] = dict(directory_exists=directory.exists(), output_exists=output.exists())
        pin(co.root / "claims" / (key + ".json"))
        if is_unbound(node):
            # A binding is published before its claim; a read that straddles
            # the bind must not mix two binding states.
            pin(binding_path(co.root, key))
        for name in NODE_EVIDENCE + ("work-request.json", "prepared.json", "tests.json", "disarm-frozen.json",
                                    "preflight/task-pack.json", "preflight/handshake-receipt.json",
                                    "preflight/completion-callback-receipt.json"):
            pin(directory / name)
        for name in ("preflight/completion-callback-receipt-attempts", "disarm-recovery"):
            journal = checked_path(directory / name)
            result["journal:" + key + ":" + name] = sorted(str(p.relative_to(journal))
                for p in journal.rglob("*.json")) if journal.exists() else None
            for relative in result["journal:" + key + ":" + name] or []:
                pin(journal / relative)
        operations = checked_path(directory / "operations")
        result["operations:" + key] = sorted(p.name for p in operations.iterdir()) if operations.exists() else None
        for name in result["operations:" + key] or []:
            operation = checked_path(operations / name)
            require(operation.is_dir(), "unknown operation entry")
            pin(operation / "intent.json")
            pin(operation / "result.json")
    return result


def _observation_fence(co):
    require(read_json(co.root / "run.json") == co.header, "run header changed during observation")
    return _control_fence(co), co.events(), co.leases.read()


def _verify_observation_fence(co, fence):
    control, events, leases = fence
    require(read_json(co.root / "run.json") == co.header, "run header changed during observation")
    require(_control_fence(co) == control, "control evidence changed during observation; read again")
    require(co.events() == events and co.leases.read() == leases, "run advanced during observation; read again")


def package_drafts(campaign_ref, campaign, spec):
    plan = validate_plan(json_ref(campaign["plan"]))
    root = Path(campaign["root"]) / "runs" / spec["run_id"]
    drafts = []
    for node in topological_nodes(plan):
        unbound = is_unbound(node)
        # A draft of an unbound node names no session: the lane is unknown
        # until bind-unbound, and a draft must not pre-commit it to one.
        session = None if unbound else campaign["sessions"][node["executor"]]
        value = dict(schema="orchestration-package-draft-v1", draft=True,
            campaign=campaign_ref, run=spec, node_id=node["id"], executor=node["executor"],
            original_session=None if unbound else
                {k: session[k] for k in ("session_id", "surface_uuid", "pane_uuid", "workspace_uuid")},
            title=node["title"], context=node["context"], source=node["source"], inputs=campaign["inputs"],
            scope=node["scope"], output_root=str(node_output(root, node)), forbidden=node["forbidden"],
            verify=node["verify"], deliverables=node["deliverables"], report=node["report"],
            dependency_ids=sorted(node["depends_on"]), dependency_outputs_bound=False,
            authorization=campaign["authorization"], required_skill=str(Path(campaign["controller"]["path"]) / "SKILL.md"))
        if unbound:
            value["binding_required"] = True
        drafts.append(dict(draft=value, sha256=object_sha(value)))
    return dict(schema="orchestration-drafts-v1", campaign=campaign_ref, run=spec,
                drafts=drafts, dispatch_authorized=False)


def _pins(value):
    """Read explicit file pins; never interpret a pinned file as executable code."""
    if isinstance(value, dict):
        if {"path", "sha256", "bytes"} <= set(value):
            read_ref(value, "observed evidence")
        else:
            for child in value.values():
                _pins(child)
    elif isinstance(value, list):
        for child in value:
            _pins(child)


def _bound_record(co, key, ref, filename):
    require(ref["path"] == str(co.root / "nodes" / key / filename), "observed evidence path changed")
    value = json_ref(ref, "recorded node evidence")
    identity = task_identity(co.campaign, co.header, co.nodes[key])
    require(all(value.get(k) == v for k, v in identity.items()), "observed evidence task/run/attempt changed")
    _pins(value)
    if "output_tree" in value:
        verify_tree(value["output_tree"])
    return value


def recorded_state(co, events):
    """Check lifecycle shape, ownership and pins, without revalidating acceptance."""
    states, data = {k: State.PENDING for k in co.nodes}, {k: {} for k in co.nodes}
    filenames = {"candidate": "candidate.json", "accepted_evidence": "accepted-evidence.json",
                 "failure": "settled-failure.json", "dispatch_intent": "dispatch-intent.json"}
    for event in events:
        key, target, extra = event["node_id"], State(event["state"]), event["data"]
        require(target in TRANSITIONS[states[key]], "illegal recorded lifecycle transition")
        if states[key] == State.PENDING:
            require(not any(s in (State.BLOCKED, State.FAILED_PRESERVED) for s in states.values()),
                    "recorded claim follows preserved run failure")
            require(key in {n["id"] for n in select_ready(co.plan, states, co.spec["strategy"],
                        co.root / "work", co.header["queues"], lanes=claimed_lanes(co, data))},
                    "recorded claim violates scheduling")
            co._check_claim(key, extra, target, data)
            co._check_decision(key, extra, states, data)
            require(read_json(co.root / "claims" / (key + ".json")) == extra, "original claim intent changed")
            require(parse_time(extra["started_at"]) <= parse_time(event["at"]), "claim begins after recorded event")
        else:
            require(not set(extra) & set(data[key]) and set(extra) == co._fields(target),
                    "immutable recorded data or transition fields changed")
            _pins(extra)
            for field in set(extra) & set(filenames):
                _bound_record(co, key, extra[field], filenames[field])
            if target in (State.BLOCKED, State.UNCERTAIN):
                value = json_ref(extra["blocker" if target == State.BLOCKED else "uncertainty"])
                require(value.get("task_id") == data[key]["task_id"] and
                        isinstance(value.get("reason"), str) and value["reason"], "unbound recorded blocker")
            if target in (State.ACCEPTED, State.FAILED_PRESERVED):
                identity = task_identity(co.campaign, co.header, co.nodes[key])
                require(extra["verification"].get("status") == target.value and
                        all(extra["verification"].get(k) == v for k, v in identity.items()),
                        "recorded terminal verification identity changed")
        states[key] = target
        data[key].update(extra)
    return states, data


def _operations(co, key, info):
    root = co.root / "nodes" / key / "operations"
    rows = []
    for directory in sorted(root.iterdir()) if root.exists() else []:
        checked_path(directory)
        require(directory.is_dir() and re.fullmatch(r"[a-z][a-z0-9-]*", directory.name), "unknown operation entry")
        intent_path, result_path = directory / "intent.json", directory / "result.json"
        if not intent_path.exists():
            require(not result_path.exists(), "operation result has no original intent")
            rows.append(dict(operation=directory.name, status="INCOMPLETE_PUBLICATION", evidence=str(directory)))
            continue
        intent_ref = file_ref(checked_path(intent_path))
        intent = json_ref(intent_ref)
        expected = dict(version=2, campaign=co.campaign_ref, task_id=info["task_id"],
                        controller_sha256=object_sha(co.campaign["controller"]), operation=directory.name)
        require(set(intent) == set(expected) | {"arguments", "created_at"} and
                all(intent[k] == v for k, v in expected.items()) and isinstance(intent["arguments"], dict),
                "observed operation binding changed")
        start = parse_time(intent["created_at"])
        row = dict(operation=directory.name, status="PENDING", intent=intent_ref,
                   intent_created_at=intent["created_at"], result_completed_at=None, recorded_wall_seconds=None)
        if result_path.exists() or result_path.is_symlink():
            ref = file_ref(checked_path(result_path))
            result = json_ref(ref)
            require(result.get("intent") == intent_ref and result.get("status") in ("RETURNED", "ERROR"),
                    "observed operation result differs from original intent")
            fields = {"value"} if result["status"] == "RETURNED" else {"error", "error_type"}
            require(set(result) == {"intent", "status", "completed_at"} | fields, "unknown operation result fields")
            elapsed = (parse_time(result["completed_at"]) - start).total_seconds()
            require(elapsed >= 0, "operation result predates original intent")
            row.update(status=result["status"], result=ref, result_completed_at=result["completed_at"],
                       recorded_wall_seconds=elapsed)
            value = result.get("value")
            if isinstance(value, dict) and "returncode" in value:
                require(type(value["returncode"]) is int, "malformed operation return code")
                row["returncode"] = value["returncode"]
                if value["returncode"] != 0:
                    row["status"] = "FAILED_RETURN"
        rows.append(row)
    return rows


def _interval(start, end, events):
    result = dict(start_at=start["at"] if start else None, end_at=end["at"] if end else None,
                  start_event_seq=start["seq"] if start else None, end_event_seq=end["seq"] if end else None,
                  recorded_wall_seconds=None, monotonic_seconds=None, clock_status="missing_endpoint")
    if start is None or end is None:
        return result
    wall = (parse_time(end["at"]) - parse_time(start["at"])).total_seconds()
    require(wall >= 0, "recorded interval moved backwards")
    result.update(recorded_wall_seconds=wall, clock_status="cross_boot_unmeasured")
    interval = [e for e in events if start["seq"] <= e["seq"] <= end["seq"]]
    if all(e["boot_id"] == start["boot_id"] for e in interval):
        elapsed = (end["monotonic_ns"] - start["monotonic_ns"]) / 1e9
        require(elapsed >= 0, "negative recorded monotonic interval")
        if abs(elapsed - wall) <= 2:
            result.update(monotonic_seconds=elapsed, clock_status="same_boot_monotonic")
        else:
            result["clock_status"] = "wall_clock_discontinuity_unmeasured"
    return result


def _timings(key, node, events):
    by_state = {e["state"]: e for e in events if e["node_id"] == key}
    pairs = [(State.PREPARING, State.PREPARED), (State.PREPARED, State.DISPATCH_INTENT),
             (State.DISPATCH_INTENT, State.DISPATCHED), (State.DISPATCHED, State.EVIDENCE_READY),
             (State.LOCAL_RUNNING, State.EVIDENCE_READY), (State.EVIDENCE_READY, State.ACCEPTED)]
    result = {a.value.lower() + "_recorded_to_" + b.value.lower() + "_recorded":
              _interval(by_state.get(a), by_state.get(b), events) for a, b in pairs}
    dependencies = [e for e in events if e["node_id"] in node["depends_on"] and e["state"] == State.ACCEPTED]
    start = max(dependencies, key=lambda e: e["seq"]) if node["depends_on"] and len(dependencies) == len(node["depends_on"]) else None
    end = by_state.get(State.PREPARING, by_state.get(State.LOCAL_RUNNING))
    result["last_dependency_acceptance_recorded_to_claim_recorded"] = _interval(start, end, events)
    return result


def _advice(co, key, state, info, operations, files, lease):
    def advice(action, reason, route=None):
        return dict(node_id=key, action=action, reason=reason, gated_command=route)
    pending = [o for o in operations if o["status"] != "RETURNED"]
    if state in (State.ACCEPTED, State.FAILED_PRESERVED):
        if lease["released_at"] is None:
            return advice("repair-original-release", "Terminal event recorded; original lease still retained.", "repair-releases")
        if co.campaign["sessions"][bound_lane(co, key, info)]["provider"] == "claude" and "disarm.json" not in files:
            if any(o["operation"] == "disarm" for o in operations):
                return advice("review-original-disarm", "Preserve the original disarm operation and its bounded recovery evidence.")
            return advice("disarm-original-task", "Business acceptance and lease release are separate from scoped disarm.", "disarm")
        return advice("terminal-recorded", "Recorded terminal evidence still requires the full verifier before run closeout.")
    if state == State.BLOCKED:
        if "settled-failure.json" in files:
            return advice("resume-original-settlement", "Validate persisted settlement against its original native invocation.", "resume-settle")
        return advice("review-original-blocker", "Keep the lease; review the frozen failure and original native control before settlement.")
    if state == State.EVIDENCE_READY:
        if "accepted-evidence.json" in files:
            return advice("resume-original-acceptance", "Validate persisted acceptance against its original native invocation.", "resume-accept")
        return advice("review-frozen-candidate", "Independent acceptance and its original native invocation are not supplied by this view.")
    if state in (State.DISPATCH_INTENT, State.UNCERTAIN) or "dispatch-intent.json" in files and state == State.PREPARED:
        return advice("observe-original-dispatch", "Existing input intent gives no second submit budget.",
                      "reconcile" if "dispatch_intent" in info else None)
    if pending:
        return advice("observe-original-operation", "An unfinished or failed operation must not be replayed.")
    if state == State.PREPARING:
        return advice("prepare-original-claim", "Continue original preparation phases through their persisted journals.", "prepare")
    if state == State.PREPARED:
        return advice("dispatch-original-pack", "Fresh live preflight and all original input gates still apply.", "dispatch")
    if state in (State.LOCAL_RUNNING, State.DISPATCHED):
        if "candidate.json" in files:
            return advice("review-persisted-candidate", "Review recovery of the frozen candidate; do not invent another native acceptance.")
        if "continuation.json" in files:
            return advice("review-callback-evidence", "Verified original callback is durable; prepare the candidate for independent acceptance.", "candidate")
        return advice("await-frozen-report", "Continue owned work or observe its original callback; a report filename alone is not DONE.",
                      "callback" if state == State.DISPATCHED else None)
    return advice("wait-dependencies-or-lane", "The frozen dependency, session FIFO and write scope constrain the next claim.")


@contract_errors("next-action observation")
@native_verification
def next_action(runner, run_id):
    root = checked_path(Path(runner.campaign["root"]) / "runs" / run_id)
    co = runner.co(run_id) if (root / "run.json").exists() else None
    return _next_action_from_co(runner, run_id, co)


@contract_errors("next-action observation")
@native_verification
def _next_action_from_co(runner, run_id, co):
    """Only reuse a fresh coordinator within one uninterrupted observation."""
    spec = runner.spec(run_id)
    root = checked_path(Path(runner.campaign["root"]) / "runs" / run_id)
    drafts = runner.drafts(run_id)["drafts"]
    result = dict(schema="orchestration-next-action-v1", campaign=runner.ref, run=spec,
        evidence_class="recorded_advisory", acceptance_revalidated=False, dispatch_authorized=False,
        input_operations=0, drafts=[dict(node_id=d["draft"]["node_id"], sha256=d["sha256"],
                                       dependency_ids=d["draft"]["dependency_ids"]) for d in drafts],
        native_boundaries_not_derived=["native_receipt", "acknowledgement", "body_read", "report_frozen",
                                       "report_discovered", "report_read", "callback_received"])
    if not (root / "run.json").exists():
        result.update(status="INCOMPLETE_START" if root.exists() else "NOT_STARTED", recorded_states={}, nodes=[],
                      recorded_claim_candidates=[], next_action="review-original-start" if root.exists() else "start-script")
        return result
    require(co is not None, "run started during observation; read again")
    control_fence, events, leases = _observation_fence(co)
    states, data = recorded_state(co, events)
    snapshots = {lane: NativeReader(session, fixtures=co.fixtures).snapshot()
                 for lane, session in co.campaign["sessions"].items()}
    rows, incomplete = [], []
    for key, node in co.nodes.items():
        info, state = data[key], states[key]
        directory = root / "nodes" / key
        files = {}
        for name in NODE_EVIDENCE:
            path = directory / name
            if path.exists() or path.is_symlink():
                files[name] = file_ref(checked_path(path))
        lease, operations = None, []
        if info:
            lease = co._lease(key, info)
            if state in ACTIVE:
                require(lease["released_at"] is None, "active recorded lease was prematurely released")
            elif lease["released_at"] is not None:
                terminal = next(e for e in reversed(events) if e["node_id"] == key)
                require(lease["released_by"] == file_ref(root / "events" / f"{terminal['seq']:06d}.json"),
                        "recorded lease released by a different terminal event")
                require(parse_time(lease["released_at"]) >= parse_time(terminal["at"]), "release precedes terminal event")
            for name in set(files) & {"candidate.json", "accepted-evidence.json", "failure-candidate.json", "settled-failure.json", "dispatch-intent.json"}:
                _bound_record(co, key, files[name], name)
            operations = _operations(co, key, info)
        elif ((root / "claims" / (key + ".json")).exists() or directory.exists() or node_output(root, node).exists()):
            incomplete.append(key)
        row = dict(node_id=key, recorded_state=state.value, dependency_ids=node["depends_on"],
            operations=operations, evidence_files=files, timings=_timings(key, node, events),
            continuation=observe_continuation(co, key, info, events, files.get("continuation.json")),
            timeline=node_timeline(co, key, info, events, files, lease, snapshots),
            lease_release_recorded_at=lease["released_at"] if lease else None,
            scoped_disarm_record="PRESENT_UNREVALIDATED" if "disarm.json" in files else "ABSENT")
        row["recommendation"] = _advice(co, key, state, info, operations, files, lease)
        if key in incomplete:
            row["recommendation"].update(action="review-incomplete-claim", reason="Original claim files or output exist without a lifecycle event.", gated_command=None)
        rows.append(row)
    candidates = []
    failed = any(s in (State.BLOCKED, State.FAILED_PRESERVED) for s in states.values())
    closed = (root / "finish.json").exists()
    if not failed and not incomplete and not closed:
        for node in select_ready(co.plan, states, spec["strategy"], root / "work", co.header["queues"],
                                 lanes=claimed_lanes(co, data)):
            row = next(r for r in rows if r["node_id"] == node["id"])
            # A pending unbound node has no claim, so its lane is its binding
            # file if one exists; without one there is no session to look up.
            lane = bound_lane(co, node["id"])
            if lane is None:
                row["recommendation"].update(action="bind-original-unbound-node", gated_command="bind-unbound",
                    reason="The frozen node is deliberately unbound; the original supervisor binds it before any claim.")
                continue
            session = runner.campaign["sessions"][lane]["session_id"]
            held = [owner for owner, lease in leases.items() if lease["released_at"] is None and
                    (lease["session_id"] == session or scopes_conflict(lease["scope"], str(node_write_scope(root, node)), "/"))]
            if held:
                row["recommendation"].update(action="wait-original-lease", reason="Original session or scope lease retained.", lease_owners=held)
            else:
                candidates.append(node["id"])
                row["recommendation"].update(action="claim-after-live-gates", reason="Recorded dependencies and lane permit a proposed claim; full gates must still pass.", gated_command="claim")
    _verify_observation_fence(co, (control_fence, events, leases))
    for lane, pin in snapshots.items():
        NativeReader(co.campaign["sessions"][lane], fixtures=co.fixtures).verify_pin(pin)
    observed = {name for row in rows for name, point in row["timeline"]["boundaries"].items() if point is not None}
    derived = {"native_receipt": "task_received", "acknowledgement": "acknowledgement",
               "callback_received": "callback_received"}
    result["native_boundaries_not_derived"] = [name for name in result["native_boundaries_not_derived"]
        if derived.get(name, name) not in observed]
    result.update(status="CLOSED_RECORDED" if closed else "OBSERVED", recorded_states={k: s.value for k, s in states.items()},
                  recorded_claim_candidates=candidates, incomplete_claims=incomplete, nodes=rows,
                  control_fence_sha256=object_sha(control_fence),
                  event_fence=file_ref(root / "events" / f"{events[-1]['seq']:06d}.json") if events else file_ref(root / "run.json"))
    return result
