"""Explicit typed controls into the original campaign Runner.

The frozen registration declares complete operations; the model selects an ID.
This is a dispatch/evidence adapter, not a live enrollment gate. Runner retains
all original identity, adoption, permit, transport and candidate checks. In
particular this module cannot make the incomplete TASK topology launchable.
Later-produced markers/decisions must already have their own frozen operation;
there is no wildcard, mutable binding slot or fallback to an arbitrary command.
This module belongs to the frozen orchestration adapter tree, not the private
MAC startup controller (which intentionally has no Runner dependencies).
"""
from __future__ import annotations

import json
from pathlib import Path

from orchestration_common import checked_path, json_ref, object_sha, require, write_json
from upgrade_task_control import control_request
from upgrade_task_operations import validate_registration

SCHEMA = "upgrade-task-campaign-controls-v1"
PAYLOAD_SCHEMA = "upgrade-task-campaign-operation-v1"
KIND = "campaign-control"
ACTIONS = frozenset({"start", "claim", "candidate", "accept", "settle", "publish-decision", "bind-unbound"})


def operation_payload(campaign, action, run_id, *, node_id=None, marker=None,
                      decision=None, decision_fields=None):
    """Exact finite command, suitable for freezing before its model invocation."""
    from orchestration_campaign import DECISION_ARGUMENTS
    require(action in ACTIONS, "TASK_CAMPAIGN_ACTION_NOT_REGISTERABLE")
    require(run_id in {r["run_id"] for r in campaign["runs"]}, "TASK_CAMPAIGN_UNKNOWN_RUN")
    if action == "start":
        require(node_id is None, "TASK_CAMPAIGN_START_HAS_NO_NODE")
    else:
        plan = json_ref(campaign["plan"])
        require(isinstance(node_id, str) and node_id in {n["id"] for n in plan["nodes"]},
                "TASK_CAMPAIGN_UNKNOWN_NODE")
    if action in ("accept", "settle"):
        require(isinstance(marker, str) and marker, "TASK_CAMPAIGN_EXACT_MARKER_REQUIRED")
    else:
        require(marker is None, "TASK_CAMPAIGN_UNEXPECTED_MARKER")
    if action == "bind-unbound":
        require(isinstance(decision, str) and decision
                and checked_path(decision) == Path(campaign["root"]) / "runs" / run_id / "decisions" / (node_id+".json"),
                "TASK_CAMPAIGN_EXACT_DECISION_PATH_REQUIRED")
    else:
        require(decision is None, "TASK_CAMPAIGN_UNEXPECTED_DECISION")
    if action == "publish-decision":
        require(isinstance(decision_fields, dict) and set(decision_fields) == set(DECISION_ARGUMENTS)
                and all(isinstance(x, str) and x for x in decision_fields.values()),
                "TASK_CAMPAIGN_EXACT_DECISION_FIELDS_REQUIRED")
    else:
        require(decision_fields is None, "TASK_CAMPAIGN_UNEXPECTED_DECISION_FIELDS")
    return dict(schema=PAYLOAD_SCHEMA, campaign_id=campaign["campaign_id"],
                campaign_root=campaign["root"], action=action, run_id=run_id, node_id=node_id,
                marker=marker, decision=decision, decision_fields=decision_fields)


def validate_controls(campaign, *, fixtures=False, check_startup=True):
    """Bind producer and reader to one frozen registration, without permission."""
    require(campaign.get("version") == 3, "TASK_CAMPAIGN_EXPLICIT_V3_REQUIRED")
    controls = campaign["task_controls"]
    require(isinstance(controls, dict) and set(controls) == {"schema", "registration", "evidence_directory"}
            and controls["schema"] == SCHEMA, "TASK_CAMPAIGN_CONTROLS_INVALID")
    registration = validate_registration(json_ref(controls["registration"]))
    require(registration["schema"] == "upgrade-task-operations-v2"
            and registration["thread_id"] == campaign["sessions"]["codex"]["session_id"],
            "TASK_CAMPAIGN_ORIGINAL_TASK_REQUIRED")
    directory = checked_path(controls["evidence_directory"])
    require(directory == Path(controls["registration"]["path"]).parent / "campaign-invocations",
            "TASK_CAMPAIGN_EVIDENCE_DIRECTORY_CHANGED")
    found, seen = {}, set()
    decisions = {}
    for name, operation in registration["operations"].items():
        if operation["kind"] != KIND:
            continue
        value = operation["payload"]
        expected = operation_payload(campaign, value.get("action"), value.get("run_id"),
            **{k:value.get(k) for k in ("node_id", "marker", "decision", "decision_fields")})
        require(value == expected, "TASK_CAMPAIGN_OPERATION_SCOPE_CHANGED")
        fingerprint = object_sha(value)
        require(fingerprint not in seen, "TASK_CAMPAIGN_DUPLICATE_OPERATION")
        seen.add(fingerprint); found[name] = value
        if value["action"] == "publish-decision":
            decisions.setdefault((value["run_id"], value["node_id"]), set()).add(name)
    require(found, "TASK_CAMPAIGN_NO_REGISTERED_CONTROLS")
    for choices in decisions.values():
        require(len(choices) == 1 or any(choices <= set(group) for group in registration["choice_groups"].values()),
                "TASK_CAMPAIGN_DECISION_CHOICE_BUDGET_REQUIRED")
    if check_startup and not fixtures:
        from upgrade_startup_contract import TASK_SCHEMA
        from upgrade_task_readiness import task_startup_result
        runtime = json_ref(campaign["runtime"])
        spec = json_ref(runtime["startup"])
        require(spec.get("schema") == TASK_SCHEMA, "TASK_CAMPAIGN_NEW_TOPOLOGY_REQUIRED")
        _, startup, _ = task_startup_result(spec)
        require(controls["registration"] == startup["operations"], "TASK_CAMPAIGN_STARTUP_REGISTRATION_CHANGED")
    return registration, found


def campaign_control(campaign, campaign_ref, action, run_id, **kwargs):
    # Caller validators retain their live gates. Static readers never acquire
    # execution authority merely by interpreting this descriptor.
    require(json_ref(campaign_ref) == campaign, "TASK_CAMPAIGN_FROZEN_CAMPAIGN_CHANGED")
    _, operations = validate_controls(campaign, check_startup=False)
    payload = operation_payload(campaign, action, run_id, **kwargs)
    matches = [name for name, value in operations.items() if value == payload]
    require(len(matches) == 1, "TASK_CAMPAIGN_EXACT_OPERATION_NOT_FROZEN")
    controls = campaign["task_controls"]
    return control_request(controls["registration"], matches[0], controls["evidence_directory"])


def campaign_handler(runner, registration_ref, receipts):
    """Fixed TaskActor handler; it never changes Runner's live authority checks."""
    from orchestration_campaign import find_control
    from orchestration_native import NativeReader
    from orchestration_run import Runner
    require(type(runner) is Runner, "TASK_CAMPAIGN_ORIGINAL_RUNNER_REQUIRED")
    registration, _ = validate_controls(runner.campaign, fixtures=runner.fixtures)
    require(runner.campaign["task_controls"]["registration"] == registration_ref,
            "TASK_CAMPAIGN_HANDLER_REGISTRATION_CHANGED")
    directory = checked_path(receipts)
    require(directory == Path(registration_ref["path"]).parent / "campaign-results",
            "TASK_CAMPAIGN_RESULT_DIRECTORY_CHANGED")
    directory.mkdir(mode=0o700, exist_ok=False)

    def handle(payload):
        require(json_ref(registration_ref) == registration, "TASK_CAMPAIGN_HANDLER_REGISTRATION_CHANGED")
        script = campaign_control(runner.campaign, runner.ref, payload["action"], payload["run_id"],
            **{k:payload[k] for k in ("node_id", "marker", "decision", "decision_fields")})
        require(registration["operations"][script["operation_id"]] == {"kind":KIND, "payload":payload},
                "TASK_CAMPAIGN_HANDLER_SCOPE_CHANGED")
        reader = NativeReader(runner.campaign["sessions"]["codex"], fixtures=runner.fixtures)
        find_control(reader, script, after=runner.campaign["sessions"]["codex"]["freeze_fence"]["offset"])
        action, run, node = payload["action"], payload["run_id"], payload["node_id"]
        if action == "start": result = runner.start(run)
        elif action == "claim": result = runner.claim(run, node)
        elif action == "candidate": result = runner.candidate(run, node)
        elif action == "accept": result = runner.accept(run, node, payload["marker"])
        elif action == "settle": result = runner.settle(run, node, payload["marker"])
        elif action == "publish-decision": result = runner.publish_decision(run, node, payload["decision_fields"])
        elif action == "bind-unbound": result = runner.bind_unbound(run, node, payload["decision"])
        else: raise AssertionError("unvalidated campaign action")
        receipt = write_json(directory / (script["operation_id"]+".json"),
            dict(schema="upgrade-task-campaign-result-v1", campaign=runner.ref,
                 request=script, operation=payload, result=result), exclusive=True)
        return {"success":True, "contentItems":[{"type":"inputText", "text":json.dumps(result,ensure_ascii=False)}]}, receipt
    return handle
