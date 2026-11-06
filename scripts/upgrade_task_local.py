"""One frozen local workload, selected by the original model's typed call.

The controller records the real subprocess result; it never manufactures a
native CommandExecution. RPC completion and the outer model result are checked
separately by the historical reader. An interrupted intent cannot run again.
"""
import os
from pathlib import Path
import shlex
import subprocess

from orchestration_common import (exclusive_lock, file_ref, json_ref, object_sha,
                                  read_ref, require, write_json)

SCHEMA = "upgrade-task-local-execution-v1"


def _actor(runner, run, node):
    from upgrade_task_enrollment import _active, TaskCampaignGate
    context = _active.get()
    require(context is not None and type(context[0]) is TaskCampaignGate,
            "TASK_LOCAL_LIVE_ACTOR_REQUIRED")
    owner, message, received = context
    owner(owner.registration, message, received)
    return _scope(runner, run, node, owner, message, received)


def _scope(runner, run, node, owner, message, received):
    from orchestration_native import NativeReader
    from upgrade_task_control import native_invocation
    require(owner.runner is runner and owner.campaign == runner.campaign
            and message.get("method") == "item/tool/call", "TASK_LOCAL_ORIGINAL_OWNER_REQUIRED")
    name = message["params"]["arguments"]["operation_id"]
    payload = owner.operations[name]
    require(payload["action"] == "local-execute" and payload["run_id"] == run
            and payload["node_id"] == node, "TASK_LOCAL_EXACT_OPERATION_REQUIRED")
    row = owner.ledger.inspect(message["params"]["callId"])
    require(row["phase"] == "INTENDED" and row["request"] == message,
            "TASK_LOCAL_ORIGINAL_INTENT_REQUIRED")
    invocation = file_ref(Path(runner.campaign["task_controls"]["evidence_directory"]) / (name + ".json"))
    value = json_ref(invocation)
    require(value["received"] == received and value["operation"]["payload"] == payload,
            "TASK_LOCAL_INVOCATION_CHANGED")
    native_invocation(NativeReader(runner.campaign["sessions"]["codex"], fixtures=runner.fixtures),
                      invocation, value["request"], allow_intended=True)
    return dict(enrollment=owner.proof, invocation=invocation, received=received,
                controller_pid=os.getpid())


def execution_spec(campaign, header, node, info):
    from orchestration_binding import claim_lane
    from orchestration_campaign import node_output
    from orchestration_evidence import work_request, task_identity
    from orchestration_run import business_command
    require(info["mode"] == "local_cli" and claim_lane(node, info) == "codex",
            "TASK_LOCAL_ORIGINAL_LOCAL_CLAIM_REQUIRED")
    root = Path(campaign["root"]) / "runs" / header["run"]["run_id"]
    identity = task_identity(campaign, header, node)
    require(all(info[k] == identity[k] for k in ("task_id", "attempt_id"))
            and info["output_root"] == str(node_output(root, node))
            and info["work_request"]["path"] == str(root / "nodes" / node["id"] / "work-request.json"),
            "TASK_LOCAL_FROZEN_CLAIM_SCOPE_CHANGED")
    require("workload" in campaign and json_ref(info["work_request"]) == work_request(campaign, header, node, info),
            "TASK_LOCAL_FROZEN_WORK_REQUEST_REQUIRED")
    read_ref(campaign["python"]); read_ref(campaign["workload"])
    entry = Path(campaign["workload"]["path"])
    relative = str(entry.relative_to(Path(campaign["adapter"]["path"])))
    require(all(campaign["adapter"]["files"].get(relative, {}).get(k) == campaign["workload"][k]
                for k in ("sha256", "bytes")),
            "TASK_LOCAL_WORKLOAD_OUTSIDE_FROZEN_ADAPTER")
    return dict(argv=shlex.split(business_command(campaign, info)), work_request=info["work_request"],
                output_root=info["output_root"])


def execute(runner, run, node):
    from orchestration_coordinator import State
    from orchestration_run import Runner
    from orchestration_transport import OperationJournal
    require(type(runner) is Runner and "task_controls" in runner.campaign,
            "TASK_LOCAL_ORIGINAL_RUNNER_REQUIRED")
    actor = _actor(runner, run, node)
    co = runner.co(run)
    require(node in co.nodes, "TASK_LOCAL_UNKNOWN_NODE")
    root = co.root / "nodes" / node
    with exclusive_lock(root / "runner.lock"):
        with co.state_lock(new_work=True):
            co._gate(new_work=True)
            states, data = co.state(); info = data[node]
            require(states[node] == State.LOCAL_RUNNING and co._lease(node, info)["released_at"] is None,
                    "TASK_LOCAL_ACTIVE_LEASE_REQUIRED")
            spec = execution_spec(co.campaign, co.header, co.nodes[node], info)
        journal = OperationJournal(root, runner.ref, info["task_id"], co.campaign["controller"])

        def invoke(stage):
            require(_actor(runner, run, node) == actor, "TASK_LOCAL_ACTOR_CHANGED")
            proof = write_json(stage / "task-actor.json", dict(schema=SCHEMA, campaign=runner.ref,
                run_id=run, node_id=node, task_id=info["task_id"], attempt_id=info["attempt_id"],
                stage_intent=file_ref(stage / "intent.json"), **actor, **spec), exclusive=True)
            out, err = stage / "stdout.txt", stage / "stderr.txt"
            with out.open("xb") as stdout, err.open("xb") as stderr:
                # Check again after recording intent. STOP, stale identity or a
                # changed request leaves this attempt consumed, with no spawn.
                with co.state_lock(new_work=True):
                    co._gate(new_work=True)
                    states, current = co.state()
                    require(states[node] == State.LOCAL_RUNNING and current[node] == info
                            and co._lease(node, info)["released_at"] is None,
                            "TASK_LOCAL_PRE_EXECUTION_CLAIM_CHANGED")
                    require(_actor(runner, run, node) == actor and
                            execution_spec(co.campaign, co.header, co.nodes[node], info) == spec,
                            "TASK_LOCAL_PRE_EXECUTION_SCOPE_CHANGED")
                    output = Path(spec["output_root"])
                    require(output.is_dir() and not any(output.iterdir()), "TASK_LOCAL_OUTPUT_NOT_EMPTY")
                # Keep the node lock but release the shared coordinator lock
                # while working, so independent lane callbacks can progress.
                result = subprocess.run(spec["argv"], cwd=output, stdin=subprocess.DEVNULL,
                                        stdout=stdout, stderr=stderr, close_fds=True, check=False)
                for stream in (stdout, stderr): stream.flush(); os.fsync(stream.fileno())
            return dict(returncode=result.returncode, stdout=file_ref(out), stderr=file_ref(err), task_actor=proof)

        value = journal.once("local-execute", spec, invoke)
        # A new model ID cannot reuse an old successful stage as its own work.
        require(json_ref(value["task_actor"])["invocation"] == actor["invocation"],
                "TASK_LOCAL_ORIGINAL_EXECUTION_CHANGED")
        return dict(schema=SCHEMA, stage_result=file_ref(root / "operations/local-execute/result.json"), **value)


def verify_stage(campaign, header, node, info, invocation, *, completed=None):
    """Verify original execution evidence; no process, input or state replay."""
    from orchestration_common import parse_time
    value = json_ref(invocation)
    directory = Path(campaign["root"]) / "runs" / header["run"]["run_id"] / "nodes" / node["id"] / "operations/local-execute"
    spec = execution_spec(campaign, header, node, info)
    intent = file_ref(directory / "intent.json"); original = json_ref(intent)
    expected = dict(version=2, campaign=header["campaign"], task_id=info["task_id"],
        controller_sha256=object_sha(campaign["controller"]), operation="local-execute", arguments=spec)
    require(set(original) == set(expected) | {"created_at"} and
            all(original[k] == v for k, v in expected.items()), "TASK_LOCAL_STAGE_CHANGED")
    actor_ref = file_ref(directory / "task-actor.json"); actor = json_ref(actor_ref)
    enrollment = json_ref(actor["enrollment"])
    require(actor["enrollment"]["path"] == str(Path(campaign["task_controls"]["registration"]["path"]).parent / "campaign-enrollment.json")
            and enrollment["schema"] == "upgrade-task-campaign-enrollment-v1"
            and enrollment["campaign"] == header["campaign"]
            and enrollment["registration"] == campaign["task_controls"]["registration"]
            and enrollment["authority"] == campaign["authorization"]
            and enrollment["runtime"] == campaign["runtime"]
            and enrollment["adoption"] == campaign["client_isolation"]
            and enrollment["ledger"] == value["ledger"], "TASK_LOCAL_ENROLLMENT_CHANGED")
    require(actor == dict(schema=SCHEMA, campaign=header["campaign"], run_id=header["run"]["run_id"],
        node_id=node["id"], task_id=info["task_id"], attempt_id=info["attempt_id"], stage_intent=intent,
        enrollment=actor["enrollment"], invocation=invocation, received=value["received"],
        controller_pid=enrollment["controller_pid"], **spec), "TASK_LOCAL_ACTOR_PROOF_CHANGED")
    if completed is None: return
    result_ref = file_ref(directory / "result.json"); result = json_ref(result_ref)
    require(set(result) == {"intent", "status", "value", "completed_at"} and
            result["intent"] == intent and result["status"] == "RETURNED", "TASK_LOCAL_RETURN_REQUIRED")
    actual = result["value"]
    require(set(actual) == {"returncode", "stdout", "stderr", "task_actor"}
            and type(actual["returncode"]) is int and actual["task_actor"] == actor_ref,
            "TASK_LOCAL_RESULT_CHANGED")
    for name in ("stdout", "stderr"):
        require(actual[name]["path"] == str(directory / (name + ".txt")), "TASK_LOCAL_STREAM_CHANGED")
        read_ref(actual[name])
    require(completed["value"] == dict(schema=SCHEMA, stage_result=result_ref, **actual),
            "TASK_LOCAL_NATIVE_RESULT_CHANGED")
    require(parse_time(info["started_at"]) <= parse_time(original["created_at"]) <=
            parse_time(result["completed_at"]) <= parse_time(completed["timestamp"]),
            "TASK_LOCAL_RESULT_CHRONOLOGY_CHANGED")
