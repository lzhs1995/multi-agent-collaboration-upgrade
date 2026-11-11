"""Bounded TASK scheduling proposals; the model invokes each original ID.

A single observation cannot borrow the claim/prepare/dispatch actor identities.
This profile therefore returns exact unconsumed controls. Each actual effect
still requires its separate original native model call and original budget.
Legacy foreground advance retains its original behavior.
"""
from pathlib import Path

from orchestration_common import ContractError, file_ref, json_ref, require, strict_json


def _owner(runner, run):
    from upgrade_task_enrollment import _active, TaskCampaignGate
    context = _active.get()
    require(context is not None and type(context[0]) is TaskCampaignGate, "TASK_ADVANCE_LIVE_ACTOR_REQUIRED")
    owner, message, received = context
    owner(owner.registration, message, received)
    require(owner.runner is runner and message.get("method") == "item/tool/call", "TASK_ADVANCE_ORIGINAL_OWNER_REQUIRED")
    operation = owner.operations[message["params"]["arguments"]["operation_id"]]
    require(operation["action"] == "advance" and operation["run_id"] == run,
            "TASK_ADVANCE_ORIGINAL_OPERATION_REQUIRED")
    row = owner.ledger.inspect(message["params"]["callId"])
    require(row["phase"] == "INTENDED" and row["request"] == message, "TASK_ADVANCE_ORIGINAL_INTENT_REQUIRED")
    return owner


def advance(runner, run, *, max_steps=1, max_seconds=45):
    from orchestration_campaign import control_script
    from upgrade_task_campaign import validate_controls
    from upgrade_task_control import native_result
    from orchestration_native import NativeReader
    require(runner.campaign.get("arm") == "B", "TASK_ADVANCE_B_ONLY")
    # Fixed typed registration has no caller-supplied batching parameters.
    require(max_steps == 1 and max_seconds == 45, "TASK_ADVANCE_FIXED_OBSERVATION_REQUIRED")
    owner = _owner(runner, run)
    campaign = runner.campaign
    registration, operations = validate_controls(campaign, check_startup=False)
    reader = NativeReader(campaign["sessions"]["codex"], fixtures=runner.fixtures)
    # The existing fenced observer performs no claim, delivery or acceptance.
    view = runner.next_action(run)
    root = Path(campaign["root"]) / "runs" / run
    states = view["recorded_states"]
    rows = {row["node_id"]: row for row in view["nodes"]}
    failed = any(state in ("BLOCKED", "FAILED_PRESERVED") for state in states.values())
    with owner.ledger._transaction():
        used = {name: phase for name, phase in owner.ledger.db.execute("SELECT operation_id,phase FROM calls")}
        turn = owner.ledger.db.execute("SELECT scope FROM turns WHERE turn_id=?", (owner.ledger.active_turn,)).fetchone()
        require(turn is not None, "TASK_ADVANCE_TURN_SCOPE_MISSING")
        scope = set(strict_json(turn[0]))
    available = scope - set(used)
    for choices in registration["choice_groups"].values():
        if set(choices).intersection(used): available.difference_update(choices)
    proposals, waiting = [], []
    for name in sorted(available):
        payload = operations[name]
        if payload["run_id"] != run or payload["action"] in ("advance", "status", "recover"): continue
        action, node = payload["action"], payload["node_id"]
        state = states.get(node)
        eligible = action == "start" and view["status"] == "NOT_STARTED"
        if action == "verify": eligible = view["status"] == "CLOSED_RECORDED"
        if action == "finish":
            eligible = view["status"] == "OBSERVED" and bool(states) and all(
                state in ("ACCEPTED", "FAILED_PRESERVED") and rows[key]["lease_release_recorded_at"] is not None
                and rows[key]["recommendation"]["action"] == "terminal-recorded" for key,state in states.items())
        if state is not None:
            recommended = rows[node]["recommendation"].get("gated_command")
            eligible = action == recommended
            if recommended == "bind-unbound":
                eligible = action in ("advice", "read-advice", "publish-decision", "bind-unbound")
            if state == "LOCAL_RUNNING":
                executed = [key for key, p in operations.items() if p["action"] == "local-execute"
                            and p["run_id"] == run and p["node_id"] == node and key in used]
                if not executed:
                    eligible = action == "local-execute" and not failed
                elif action == "candidate":
                    require(len(executed) == 1, "TASK_ADVANCE_DUPLICATE_LOCAL_EXECUTION")
                    original = file_ref(Path(campaign["task_controls"]["evidence_directory"]) / (executed[0] + ".json"))
                    call = json_ref(original)
                    # No replay if the original inner/outer result is absent.
                    returned = native_result(reader, original, call["request"], until=reader.snapshot()["offset"])
                    eligible = returned is not None and returned["success"] and returned["value"].get("returncode") == 0
                else: eligible = False
            elif state == "EVIDENCE_READY": eligible = action == "accept"
            elif state == "BLOCKED": eligible = action in ("failure-candidate", "settle")
        if not eligible: continue
        try:
            resolved, _ = owner.ledger.resolve_operation(name)
        except ContractError as exc:
            waiting.append(dict(operation_id=name, reason=str(exc))); continue
        effective = resolved["payload"]
        descriptor = control_script(campaign, runner.ref, action, run,
            **{k: effective.get(k) for k in ("node_id", "marker", "decision", "decision_fields", "boundary", "reason", "observation", "advisory_registration")
               if effective.get(k) is not None})
        proposals.append(dict(operation_id=name, operation=effective, request=descriptor,
                              review_required=action in ("accept", "settle")))
    require(_owner(runner, run) is owner, "TASK_ADVANCE_OWNER_CHANGED")
    return dict(schema="upgrade-task-advance-proposals-v1", run_id=run, observation=view,
        proposals=proposals, unresolved=waiting, consumed_operations=used,
        business_actions_executed=0, input_operations=0, new_operation_budgets=0,
        instruction="Select a proposed original operation and invoke its exact script in a separate native call. "
                    "This observation grants no readiness, acceptance or retry authority.")
