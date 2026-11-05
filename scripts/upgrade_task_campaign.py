"""Explicit typed controls into the original campaign Runner.

The frozen registration declares complete operations; the model selects an ID.
This is a dispatch/evidence adapter, not a live enrollment gate. Runner retains
all original identity, adoption, permit, transport and candidate checks. In
particular this module cannot make the incomplete TASK topology launchable.
Controls v2 permits explicit candidate-to-accept and failure-to-settle slots,
bound once after the original producing operation completes. Other generated-artifact
resolvers remain unavailable in this adapter. There is no arbitrary command.
This module belongs to the frozen orchestration adapter tree, not the private
MAC startup controller (which intentionally has no Runner dependencies).
"""
from __future__ import annotations

import json
from pathlib import Path

from orchestration_common import checked_path, file_ref, json_ref, object_sha, require, write_json
from upgrade_task_control import control_request
from upgrade_task_operations import validate_registration

SCHEMA = "upgrade-task-campaign-controls-v1"
BINDING_SCHEMA = "upgrade-task-campaign-controls-v2"
PAYLOAD_SCHEMA = "upgrade-task-campaign-operation-v1"
KIND = "campaign-control"
ACTIONS = frozenset({"start", "claim", "candidate", "accept", "block", "failure-candidate",
                     "settle", "publish-decision", "bind-unbound"})
FAILURE_BOUNDARIES = frozenset({"local_boundary", "callback_boundary", "no_business_input", "preparation_exited"})
ARGUMENTS = ("node_id", "marker", "decision", "decision_fields", "boundary", "reason")
MARKER_SLOTS = {
    "campaign-accept-marker-v1": ("accept", "candidate", "candidate.json"),
    "campaign-settle-marker-v1": ("settle", "failure-candidate", "failure-candidate.json"),
}


def operation_payload(campaign, action, run_id, *, node_id=None, marker=None,
                      decision=None, decision_fields=None, boundary=None, reason=None):
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
    extra = {}
    if action == "failure-candidate":
        require(isinstance(boundary, str) and boundary in FAILURE_BOUNDARIES,
                "TASK_CAMPAIGN_FAILURE_BOUNDARY_INVALID")
        extra["boundary"] = boundary
    else:
        require(boundary is None, "TASK_CAMPAIGN_UNEXPECTED_FAILURE_BOUNDARY")
    if action == "block":
        require(isinstance(reason, str) and reason.strip() and len(reason.encode()) <= 8192,
                "TASK_CAMPAIGN_CONCRETE_BLOCKER_REQUIRED")
        extra["reason"] = reason
    else:
        require(reason is None, "TASK_CAMPAIGN_UNEXPECTED_BLOCKER")
    return dict(schema=PAYLOAD_SCHEMA, campaign_id=campaign["campaign_id"],
                campaign_root=campaign["root"], action=action, run_id=run_id, node_id=node_id,
                marker=marker, decision=decision, decision_fields=decision_fields, **extra)


def validate_controls(campaign, *, fixtures=False, check_startup=True):
    """Bind producer and reader to one frozen registration, without permission."""
    require(campaign.get("version") == 3, "TASK_CAMPAIGN_EXPLICIT_V3_REQUIRED")
    controls = campaign["task_controls"]
    require(isinstance(controls, dict) and set(controls) == {"schema", "registration", "evidence_directory"}
            and controls["schema"] in (SCHEMA, BINDING_SCHEMA), "TASK_CAMPAIGN_CONTROLS_INVALID")
    registration = validate_registration(json_ref(controls["registration"]))
    require(registration["schema"] == ("upgrade-task-operations-v3" if controls["schema"] == BINDING_SCHEMA
                                      else "upgrade-task-operations-v2")
            and registration["thread_id"] == campaign["sessions"]["codex"]["session_id"],
            "TASK_CAMPAIGN_ORIGINAL_TASK_REQUIRED")
    directory = checked_path(controls["evidence_directory"])
    require(directory == Path(controls["registration"]["path"]).parent / "campaign-invocations",
            "TASK_CAMPAIGN_EVIDENCE_DIRECTORY_CHANGED")
    found, seen = {}, set()
    decisions = {}
    slots = registration.get("binding_slots", {})
    for name, operation in registration["operations"].items():
        if operation["kind"] != KIND:
            continue
        value = operation["payload"]
        kwargs={k:value.get(k) for k in ARGUMENTS}
        if name in slots:
            slot=slots[name]
            contract=MARKER_SLOTS.get(slot["resolver"])
            require(contract is not None and value.get("action") == contract[0],
                    "TASK_CAMPAIGN_RESOLVER_NOT_INTEGRATED")
            require(slot["source_path"] == str(Path(campaign["root"]) / "runs" / value["run_id"] /
                    "nodes" / value["node_id"] / contract[2]), "TASK_CAMPAIGN_SLOT_PATH_CHANGED")
            producer=registration["operations"][slot["producer_operation"]]
            producer_args={"boundary":producer["payload"].get("boundary")} if contract[1]=="failure-candidate" else {}
            require(producer==dict(kind=KIND,payload=operation_payload(campaign,contract[1],value["run_id"],
                node_id=value["node_id"],**producer_args)), "TASK_CAMPAIGN_SLOT_PRODUCER_CHANGED")
            kwargs["marker"]="declared-candidate-marker"
        expected = operation_payload(campaign, value.get("action"), value.get("run_id"), **kwargs)
        if name in slots:
            expected["marker"]=None
        require(value == expected, "TASK_CAMPAIGN_OPERATION_SCOPE_CHANGED")
        fingerprint = object_sha(value)
        require(fingerprint not in seen, "TASK_CAMPAIGN_DUPLICATE_OPERATION")
        seen.add(fingerprint); found[name] = value
        if value["action"] == "publish-decision":
            decisions.setdefault((value["run_id"], value["node_id"]), set()).add(name)
    require(found, "TASK_CAMPAIGN_NO_REGISTERED_CONTROLS")
    require(set(slots) <= set(found), "TASK_CAMPAIGN_FOREIGN_SLOT")
    # A fixed marker and a slot for the same node cannot provide two attempts.
    endpoints=[(p["action"],p["run_id"],p["node_id"]) for p in found.values() if p["action"]=="accept"]
    require(len(endpoints)==len(set(endpoints)), "TASK_CAMPAIGN_DUPLICATE_ACCEPTANCE")
    for action in ("block", "failure-candidate", "settle"):
        endpoints=[(p["run_id"],p["node_id"]) for p in found.values() if p["action"]==action]
        require(len(endpoints)==len(set(endpoints)), "TASK_CAMPAIGN_DUPLICATE_FAILURE_CONTROL:"+action)
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
    registration, operations = validate_controls(campaign, check_startup=False)
    payload = operation_payload(campaign, action, run_id, **kwargs)
    matches = [name for name, value in operations.items() if value == payload]
    for name in registration.get("binding_slots", {}):
        from upgrade_task_bindings import resolve_replacement
        template=operations[name]
        if payload["action"]==template["action"] and payload["run_id"]==template["run_id"] and payload["node_id"]==template["node_id"]:
            expected=resolve_replacement(registration,name,_candidate_marker(campaign,registration["binding_slots"][name],template))
            require(expected==dict(kind=KIND,payload=payload), "TASK_CAMPAIGN_GENERATED_MARKER_CHANGED")
            matches.append(name)
    require(len(matches) == 1, "TASK_CAMPAIGN_EXACT_OPERATION_NOT_FROZEN")
    controls = campaign["task_controls"]
    return control_request(controls["registration"], matches[0], controls["evidence_directory"],
                           operation=dict(kind=KIND,payload=payload))


def _candidate_marker(campaign, slot, payload):
    """Recompute from the original candidate and original run state, read-only.

    Full business validation stays in Runner and the binding producer. This
    reconstruction does not acquire live authority or manufacture a new pin.
    """
    from orchestration_evidence import marker_for, failure_marker
    from orchestration_common import read_json
    root=Path(campaign["root"])/"runs"/payload["run_id"]
    # Full state replay would recursively reconstruct this same control when
    # verifying ACCEPTED. Read its immutable claim here; the caller's Runner
    # and completion binder validate the full state and candidate separately.
    info=read_json(root/"claims"/(payload["node_id"]+".json"))
    ref=file_ref(slot["source_path"])
    candidate=json_ref(ref)
    require(candidate.get("run_id")==payload["run_id"] and candidate.get("node_id")==payload["node_id"]
            and candidate.get("task_id")==info.get("task_id")
            and candidate.get("attempt_id")==info.get("attempt_id"), "TASK_CAMPAIGN_ORIGINAL_CANDIDATE_CHANGED")
    if slot["resolver"]=="campaign-settle-marker-v1":
        # The claim predates BLOCKED and therefore contains no blocker field.
        # Replay of the state would recurse via settlement control verification.
        # Pin the original node artifact here; Runner/binder verify its transition.
        blocker=file_ref(root/"nodes"/payload["node_id"]/"blocker.json")
        require(candidate.get("blocker")==blocker and json_ref(blocker).get("task_id")==info["task_id"],
                "TASK_CAMPAIGN_ORIGINAL_BLOCKER_CHANGED")
        return failure_marker(info,candidate)
    return marker_for(info,candidate)


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
            **{k:payload.get(k) for k in ARGUMENTS})
        expected=script.get("operation",registration["operations"][script["operation_id"]])
        require(expected == {"kind":KIND, "payload":payload},
                "TASK_CAMPAIGN_HANDLER_SCOPE_CHANGED")
        reader = NativeReader(runner.campaign["sessions"]["codex"], fixtures=runner.fixtures)
        find_control(reader, script, after=runner.campaign["sessions"]["codex"]["freeze_fence"]["offset"])
        action, run, node = payload["action"], payload["run_id"], payload["node_id"]
        if action == "start": result = runner.start(run)
        elif action == "claim": result = runner.claim(run, node)
        elif action == "candidate": result = runner.candidate(run, node)
        elif action == "block": result = runner.block(run, node, payload["reason"])
        elif action == "failure-candidate": result = runner.failure_candidate(run, node, payload["boundary"])
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


def campaign_completion_binder(runner, ledger, registration_ref):
    """Append acceptance/failure bindings after a live-gated native completion.

    TaskActor invokes this after recording completion. A failure leaves the
    original completed operation intact and closes the actor; no retry/input.
    """
    from orchestration_run import Runner
    from upgrade_task_bindings import BINDING_SCHEMA as ARTIFACT_SCHEMA
    from upgrade_task_control import native_invocation, native_result
    from orchestration_native import NativeReader
    require(type(runner) is Runner, "TASK_CAMPAIGN_ORIGINAL_RUNNER_REQUIRED")
    registration,_=validate_controls(runner.campaign,fixtures=runner.fixtures)
    require(registration==ledger.registration and runner.campaign["task_controls"]["registration"]==registration_ref
            and registration["schema"]=="upgrade-task-operations-v3", "TASK_CAMPAIGN_BINDING_REGISTRATION_CHANGED")
    directory=Path(registration_ref["path"]).parent/"campaign-bindings"
    directory.mkdir(mode=0o700,exist_ok=False)

    def bind(message, original_rpc, *, existing_only=False):
        call=message["params"]["item"]["id"]
        row=ledger.inspect(call)
        require(row["phase"]=="NATIVE_COMPLETED", "TASK_SLOT_PRODUCER_NOT_COMPLETED")
        slots=[(name,slot) for name,slot in registration["binding_slots"].items()
               if slot["producer_operation"]==row["operation_id"]]
        if not slots or row["result"]["success"] is not True:
            return []
        controls=runner.campaign["task_controls"]
        reader=NativeReader(runner.campaign["sessions"]["codex"],fixtures=runner.fixtures)
        request=control_request(registration_ref,row["operation_id"],controls["evidence_directory"])
        invocation=file_ref(Path(controls["evidence_directory"])/(row["operation_id"]+".json"))
        native_invocation(reader,invocation,request)
        fence=reader.snapshot(complete_records=True)
        returned=native_result(reader,invocation,request,until=fence["offset"])
        if returned is None:
            # The original inner completion can arrive before its native outer
            # result. Preserve it; the owner may observe the same result later.
            return dict(status="WAITING_ORIGINAL_NATIVE_RESULT",call_id=call,bindings=[])
        receipt=json_ref(row["receipt"])
        require(receipt.get("schema")=="upgrade-task-campaign-result-v1" and receipt.get("campaign")==runner.ref
                and receipt.get("request")==request
                and receipt.get("operation")==registration["operations"][row["operation_id"]]["payload"],
                "TASK_CAMPAIGN_PRODUCER_RESULT_CHANGED")
        from orchestration_common import strict_json
        items=row["result"]["contentItems"]
        require(len(items)==1 and items[0]["type"]=="inputText"
                and strict_json(items[0]["text"])==receipt["result"]==returned["value"],
                "TASK_CAMPAIGN_PRODUCER_OUTPUT_CHANGED")
        refs=[]
        from orchestration_evidence import validate_candidate, marker_for, validate_failure_candidate, failure_marker
        for name,slot in slots:
            payload=registration["operations"][name]["payload"]
            co=runner.co(payload["run_id"])
            with co.node_locks(payload["node_id"]):
                co._gate()
                _,data=co.state();info=data[payload["node_id"]]
                source=file_ref(slot["source_path"])
                require(source==receipt["result"].get("candidate"),
                        "TASK_CAMPAIGN_ORIGINAL_CANDIDATE_CHANGED")
                if slot["resolver"]=="campaign-settle-marker-v1":
                    producer=registration["operations"][row["operation_id"]]["payload"]
                    candidate=json_ref(source)
                    require(candidate.get("boundary")==receipt["result"].get("boundary")==producer["boundary"],
                            "TASK_CAMPAIGN_FAILURE_BOUNDARY_CHANGED")
                    validate_failure_candidate(runner.campaign,co.header,co.nodes[payload["node_id"]],info,candidate,
                                               fixtures=runner.fixtures)
                    replacement=failure_marker(info,candidate)
                else:
                    require(source==info.get("candidate"), "TASK_CAMPAIGN_ORIGINAL_CANDIDATE_CHANGED")
                    candidate=validate_candidate(runner.campaign,co.header,co.nodes[payload["node_id"]],info,source,
                                                 fixtures=runner.fixtures,rerun=False)
                    replacement=marker_for(info,candidate)
                value=dict(schema=ARTIFACT_SCHEMA,registration_sha256=object_sha(registration),operation_id=name,
                    slot=slot,source=source,producer_call_id=call,producer_result=row["receipt"],
                    producer_native=dict(invocation=invocation,fence=fence),
                    replacement=replacement)
                if existing_only:
                    # Publication and SQLite insertion are separate. Recovery
                    # may attach this original complete artifact, never replace
                    # a missing/truncated one or regenerate its observation fence.
                    ref=file_ref(directory/(name+".json"))
                    existing=json_ref(ref)
                    from upgrade_task_bindings import validate_binding, verify_producer
                    validate_binding(registration,name,ref)
                    verify_producer(ledger.db,existing)
                    original_fence=existing["producer_native"]["fence"]
                    original_end=reader.verify_pin(original_fence)
                    original_return=native_result(reader,invocation,request,until=original_end)
                    require(original_return is not None and original_return["success"] is True
                            and original_return["value"]==returned["value"],
                            "TASK_CAMPAIGN_RECOVERY_ORIGINAL_RESULT_MISSING")
                    value["producer_native"]["fence"]=original_fence
                    require(existing==value,"TASK_CAMPAIGN_RECOVERY_BINDING_CHANGED")
                else:
                    ref=write_json(directory/(name+".json"),value,exclusive=True)
                co._gate()
                if existing_only and ledger.db.execute("SELECT 1 FROM bindings WHERE operation_id=?",(name,)).fetchone():
                    _,bound=ledger.resolve_operation(name)
                    require(bound==ref,"TASK_CAMPAIGN_RECOVERY_LEDGER_BINDING_CHANGED")
                    refs.append(dict(operation_id=name,binding=bound,already_attached=True))
                else:
                    refs.append(ledger.bind_artifact(name,ref))
        return dict(status="RECONCILED_ORIGINAL_BINDING" if existing_only else "BOUND",call_id=call,bindings=refs,
                    **({"actor_reopened":False,"operations_executed":0,"responses_sent":0} if existing_only else {}))
    bind.reconcile=lambda message,original_rpc:bind(message,original_rpc,existing_only=True)
    return bind


def campaign_task_service(runner, client, ledger, enrollment_gate):
    """Connect the original retained RPC and ledger to the fixed business actor.

    The deployment supplies its live enrollment gate. This adapter never
    authenticates a caller from file contents and never launches a client.
    Production Runner validation still requires real adoption, whose TASK
    branch remains unavailable until its complete deployment checks are ready.
    """
    from orchestration_run import Runner
    from orchestration_native import NativeReader
    from upgrade_rpc_peer import verify_peer
    from upgrade_task_actor import TaskActor, TaskService
    from upgrade_task_control import invocation_recorder
    from upgrade_task_operations import OperationLedger
    require(type(runner) is Runner and type(ledger) is OperationLedger and callable(enrollment_gate),
            "TASK_CAMPAIGN_ORIGINAL_SERVICE_INPUTS_REQUIRED")
    registration,operations=validate_controls(runner.campaign,fixtures=runner.fixtures)
    require(registration==ledger.registration and object_sha(registration)==ledger.registration_sha256
            and client.server==registration["server"], "TASK_CAMPAIGN_SERVICE_IDENTITY_CHANGED")
    controls=runner.campaign["task_controls"];parent=Path(controls["registration"]["path"]).parent
    require(ledger.path.parent==parent, "TASK_CAMPAIGN_SERVICE_LEDGER_PATH_CHANGED")
    if not runner.fixtures:
        from upgrade_task_readiness import task_startup_result
        runtime=json_ref(runner.campaign["runtime"])
        _,startup,_=task_startup_result(json_ref(runtime["startup"]))
        require(startup["ledger"]==str(ledger.path) and startup["registration_sha256"]==ledger.registration_sha256,
                "TASK_CAMPAIGN_SERVICE_ORIGINAL_STARTUP_REQUIRED")
    require(callable(getattr(client,"poll_recorded",None)), "TASK_CAMPAIGN_SERVICE_POLL_REQUIRED")
    verify_peer(client.sock,client.server,client.process)
    if not runner.fixtures:
        from upgrade_task_enrollment import TaskCampaignGate
        require(type(enrollment_gate) is TaskCampaignGate and enrollment_gate.runner is runner
                and enrollment_gate.client is client and enrollment_gate.ledger is ledger,
                "TASK_CAMPAIGN_LIVE_GATE_REQUIRED")
    reader=NativeReader(runner.campaign["sessions"]["codex"],fixtures=runner.fixtures)
    directory=Path(controls["evidence_directory"])
    directory.mkdir(mode=0o700,exist_ok=False)
    handler=campaign_handler(runner,controls["registration"],parent/"campaign-results")
    completion=(campaign_completion_binder(runner,ledger,controls["registration"])
                if registration["schema"]=="upgrade-task-operations-v3" else None)
    if not runner.fixtures:
        original_handler=handler
        def handler(payload):
            with enrollment_gate.activate():return original_handler(payload)
        if completion is not None:
            original_completion=completion
            def completion(message,record):
                with enrollment_gate.activate():return original_completion(message,record)
            def reconcile(message,record):
                with enrollment_gate.activate():return original_completion.reconcile(message,record)
            completion.reconcile=reconcile
    actor=TaskActor(client,ledger,{KIND:handler},enrollment_gate,parent/"campaign-actor-events",
        operation_scope=set(operations),invocation_recorder=invocation_recorder(reader,controls["registration"],directory,
            after=runner.campaign["sessions"]["codex"]["freeze_fence"]["offset"]),completion_observer=completion)
    return TaskService(actor)
