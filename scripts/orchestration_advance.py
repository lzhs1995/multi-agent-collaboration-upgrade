"""Bounded B-arm orchestration; native business/review controls remain external.

This is an explicit foreground action, not a poller. Advisory rows choose work;
every action enters the original runner/transport with fresh authoritative state.
An UNKNOWN operation is observed once per invocation, never resubmitted.
"""
from __future__ import annotations

from pathlib import Path
from time import monotonic

from orchestration_campaign import control_script, find_control
from orchestration_common import ContractError, checked_path, json_ref, require
from orchestration_coordinator import State
from orchestration_native import NativeReader


def _script(runner, run_id, action, key=None):
    return control_script(runner.campaign, runner.ref, action, run_id, node_id=key)


def _original_controls(runner, script, after=0):
    reader = NativeReader(runner.campaign["sessions"]["codex"], fixtures=runner.fixtures)
    matches = [ref for ref, record in reader.records(after, reader.snapshot()["offset"])
               if record.get("type") == "response_item"
               and record.get("payload", {}).get("type") == "custom_tool_call"
               and record["payload"].get("input") == script]
    if matches:
        # Retain the exact original verifier, including duplicate/name rejection.
        find_control(reader, script, after=after)
    return matches


def _review(runner, run_id, key, result):
    candidate = json_ref(result["candidate"])
    script = result["exec_script"]
    original = _original_controls(runner, script, candidate["candidate_native_end"]["codex"]["offset"])
    return dict(node_id=key, action="resume-original-acceptance" if original else "review-and-accept",
                candidate=result["candidate"], report=result["report"], tests=result["tests"],
                output_files=candidate["output_files"], review_kind=candidate.get("review_kind", "independent_supervisor"),
                exec_script=_script(runner, run_id, "resume-accept", key) if original else script,
                instruction=("Recover the existing native acceptance; never execute its acceptance script again."
                             if original else "Read the complete report, tests and output files. Only after independent "
                             "review execute this exact native functions.exec input once, including its final LF."))


def _options(runner, run_id, view):
    """Advisory selection only. No file existence grants task ownership."""
    options, waiting = [], []
    for row in view["nodes"]:
        key, state = row["node_id"], row["recorded_state"]
        advice = row["recommendation"]
        action = advice["gated_command"]
        if state == State.EVIDENCE_READY.value and action is None:
            action = "candidate"
        elif advice["action"] == "review-persisted-candidate":
            action = "candidate"
        if action == "callback":
            # Do not repeatedly run a receiver settlement before any original
            # callback exists. These files are hints, never proof of reception.
            co = runner.co(run_id)
            _, data = co.state()
            pack = json_ref(data[key]["prepared"]["pack"])
            receipt = Path(pack["completion_receipt"])
            if not receipt.exists() and not (receipt.with_name(receipt.stem + "-attempts") / "delivery.lock").is_file():
                action = None
        if action in ("resume-accept", "resume-settle"):
            waiting.append(dict(node_id=key, state=state, action=advice["action"],
                                exec_script=_script(runner, run_id, action, key),
                                instruction="Recover only the existing exact native control; do not repeat it."))
        elif action is not None:
            priority = (0 if action in ("repair-releases", "disarm") else
                        3 if action == "reconcile" else
                        2 if state == State.EVIDENCE_READY.value else 1)
            options.append((priority, key, state, action))
        else:
            waiting.append(dict(node_id=key, state=state, **{k: v for k, v in advice.items() if k != "node_id"}))
    return options, waiting


def _execute(runner, run_id, key, expected, action):
    co = runner.co(run_id)
    states, _ = co.state()
    require(not (co.root / "finish.json").exists(), "run closed before advance action")
    require(states[key].value == expected, "state changed before advance action; observe again")
    if action == "claim":
        from orchestration_run import business_command
        # Runner.claim can return an existing claim. Advance must acquire a new
        # claim under the original coordinator locks before handing out local
        # work, including when another caller wins after the state check above.
        info = co.claim(key)
        state = co.state()[0][key]
        return dict(task_id=info["task_id"], state=state.value, output_root=info["output_root"],
                    work_request=info["work_request"], business_command=business_command(runner.campaign, info)
                    if state == State.LOCAL_RUNNING else None)
    if action == "candidate":
        return runner.candidate(run_id, key)
    if action == "repair-releases":
        return dict(repaired=co.repair_releases())
    require(action in ("prepare", "dispatch", "reconcile", "callback", "disarm"), "unsupported advance action")
    value = getattr(runner.transport(co, key), action)()
    if action == "callback":
        # Persist the original reception pointer in this action. Otherwise a
        # succession of one-step invocations would rediscover the same callback
        # forever without ever reaching candidate creation.
        return dict(callback=value, continuation=runner.publish_continuation(run_id, key))
    return value


def advance(runner, run_id, *, max_steps=1, max_seconds=45):
    require(runner.campaign.get("version") == 3 and runner.campaign.get("arm") == "B",
            "advance requires the B arm of a v3 campaign")
    require(type(max_steps) is int and 1 <= max_steps <= 32, "max-steps must be an integer from 1 to 32")
    require(type(max_seconds) in (int, float) and 0 < max_seconds <= 600,
            "max-seconds must be positive and at most 600")
    runner.spec(run_id)
    began = monotonic()
    result = dict(schema="orchestration-advance-v1", run_id=run_id, status="YIELDED",
                  max_steps=max_steps, max_seconds=max_seconds, actions=[], supervisor_actions=[],
                  input_retry_authorized=False, native_control_executed=False,
                  budget_kind="between_original_synchronous_actions", stop_reason="step_budget")
    from orchestration_observe import (_next_action_from_co, _observation_fence,
                                       _verify_observation_fence)
    root = checked_path(Path(runner.campaign["root"]) / "runs" / run_id)
    initial_co = runner.co(run_id) if (root / "run.json").exists() else None
    initial_fence = _observation_fence(initial_co) if initial_co is not None else None
    status = runner._status_from_co(initial_co) if initial_co is not None else runner.status(run_id)
    if status["state"] != "STARTED":
        result["stop_reason"] = status["state"]
        if status["state"] == "NOT_STARTED":
            script = runner.start_script(run_id)["exec_script"]
            if _original_controls(runner, script):
                result["supervisor_actions"].append(dict(action="review-original-start", instruction="An original start call exists; preserve it. Never start again."))
            else:
                result["supervisor_actions"].append(dict(action="start", exec_script=script))
        result["states"] = status["states"]
        return result
    attempted, counts, handoffs, reviews = set(), {}, {}, {}
    waiting = []
    for _ in range(max_steps):
        if monotonic() - began >= max_seconds:
            result["stop_reason"] = "time_budget_between_actions"
            break
        try:
            if initial_co is not None:
                view = _next_action_from_co(runner, run_id, initial_co)
                _verify_observation_fence(initial_co, initial_fence)
                # No observed coordinator or state crosses an action boundary.
                initial_co, initial_fence = None, None
            else:
                view = runner.next_action(run_id)
            options, waiting = _options(runner, run_id, view)
        except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
            from orchestration_run import exception_trace
            result.update(status="PENDING_OR_BLOCKED", stop_reason="observation_refused",
                          observation_error=dict(error=str(exc), exception_trace=exception_trace(exc)))
            break
        result["states"] = view["recorded_states"]
        if view["status"] != "OBSERVED":
            result["stop_reason"] = view["status"]
            break
        options = [o for o in options if (o[1], o[3]) not in attempted]
        # Recorded lifecycle progress survives single-step invocations. Claim
        # all eligible independent lanes before prepare, then prepare before
        # dispatch. Keep closeout/review/reconcile priorities and fresh gates.
        phases = {State.PENDING.value: 0, State.PREPARING.value: 1, State.PREPARED.value: 2}
        options.sort(key=lambda o: (o[0], phases.get(o[2], 3) if o[0] == 1 else 0,
                                    counts.get(o[1], 0)))
        if not options:
            result["stop_reason"] = "supervisor_or_original_evidence_required"
            break
        _, key, expected, action = options[0]
        if action in ("prepare", "dispatch", "reconcile", "callback", "disarm") and any(
                handoff["action"] == "execute-local-business-once"
                and view["recorded_states"][node] == State.LOCAL_RUNNING.value
                for node, handoff in handoffs.items()):
            # A native local command cannot start until this foreground call
            # returns. Keep short claims/closeout batched, but do not hide that
            # handoff behind an original synchronous transport operation.
            result["stop_reason"] = "local_business_ready_before_" + action
            break
        attempted.add((key, action))
        counts[key] = counts.get(key, 0) + 1
        row = dict(node_id=key, action=action, expected_state=expected)
        result["actions"].append(row)
        try:
            value = _execute(runner, run_id, key, expected, action)
            row.update(status="RETURNED", result=value)
            if action == "candidate" and "exec_script" in value:
                reviews[key] = value
                handoffs[key] = _review(runner, run_id, key, value)
            elif action == "claim" and value["state"] == State.LOCAL_RUNNING.value:
                handoffs[key] = dict(node_id=key, action="execute-local-business-once", **value,
                    instruction="Execute the returned business command once in the original supervisor native tool. "
                    "Collect that same process, then call candidate for this node. Never rerun an existing local action.",
                    candidate_exec_script=_script(runner, run_id, "candidate", key))
        except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
            from orchestration_run import exception_trace
            row.update(status="PENDING_OR_BLOCKED", error=str(exc), exception_trace=exception_trace(exc))
            if getattr(exc, "diagnostic", None) is not None:
                row["diagnostic"] = exc.diagnostic
            # A refusal on one original lane cannot authorize a retry, nor should
            # it prevent independently eligible lanes from reaching their gates.
            handoffs[key] = dict(node_id=key, action="review-original-failure", error=str(exc), input_retry_authorized=False)
    # Refresh before returning handoffs/closeout. This also rejects evidence
    # tampering rather than treating the last advisory snapshot as authority.
    try:
        co = runner.co(run_id)
        states, data = co.state()
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        from orchestration_run import exception_trace
        result.update(status="PENDING_OR_BLOCKED", stop_reason="final_state_refused",
                      final_state_error=dict(error=str(exc), exception_trace=exception_trace(exc)),
                      elapsed_seconds=monotonic() - began)
        return result
    result["states"] = {k: s.value for k, s in states.items()}
    for key, handoff in tuple(handoffs.items()):
        action = handoff["action"]
        if action == "execute-local-business-once" and states[key] != State.LOCAL_RUNNING:
            del handoffs[key]
        elif action in ("review-and-accept", "resume-original-acceptance"):
            if states[key] != State.EVIDENCE_READY:
                del handoffs[key]
                continue
            try:
                # Another native turn can accept after candidate selection.
                # Recheck its original call even if no accepted event exists
                # yet, so a lost return cannot produce a second acceptance.
                handoffs[key] = _review(runner, run_id, key, reviews[key])
            except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
                from orchestration_run import exception_trace
                result.update(status="PENDING_OR_BLOCKED", stop_reason="handoff_refresh_refused")
                handoffs[key] = dict(node_id=key, action="review-original-failure", error=str(exc),
                    exception_trace=exception_trace(exc), input_retry_authorized=False)
        elif action == "review-original-failure" and states[key] in (State.ACCEPTED, State.FAILED_PRESERVED):
            del handoffs[key]
    for key, state in states.items():
        if state == State.LOCAL_RUNNING and key not in handoffs:
            handoffs[key] = dict(node_id=key, action="collect-original-local-work", task_id=data[key]["task_id"],
                output_root=data[key]["output_root"], candidate_exec_script=_script(runner, run_id, "candidate", key),
                instruction="Collect the original native business action. Do not rerun it. After completion call candidate.")
    result["supervisor_actions"] = list(handoffs.values())
    result["waiting"] = [row for row in waiting if row["node_id"] not in handoffs
                         and row.get("state", result["states"][row["node_id"]]) == result["states"][row["node_id"]]]
    if all(s == State.ACCEPTED for s in states.values()) and all(
            co._lease(k, data[k])["released_at"] is not None and
            (runner.campaign["sessions"][co.nodes[k]["executor"]]["provider"] != "claude"
             or (co.root / "nodes" / k / "disarm.json").is_file()) for k in states):
        result["supervisor_actions"].append(dict(action="finish-and-verify",
            exec_script=_script(runner, run_id, "finish"), verify_exec_script=_script(runner, run_id, "verify")))
    result["elapsed_seconds"] = monotonic() - began
    if any(row["status"] == "PENDING_OR_BLOCKED" or row.get("result", {}).get("status") == "UNCERTAIN"
           for row in result["actions"]):
        result["status"] = "PENDING_OR_BLOCKED"
    return result
