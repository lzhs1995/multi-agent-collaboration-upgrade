"""Original startup failure evidence; no adoption, input or model requests.

Only an explicitly opted-in private controller may use this contract. Unknown
or changed native evidence cannot create availability or replacement authority.
"""
from __future__ import annotations

from pathlib import Path
import copy
import argparse
import os
import json
import subprocess
import stat
import sys
import time
import uuid

from orchestration_common import (ContractError, canonical, checked_path, contract_errors, count, digest, file_ref, finite, json_ref, parse_time, read_ref,
                                  require, strict_json, verify_tree, within, write_json)
from upgrade_readiness_evidence import command_in_tool, native_records, readiness_evidence
from upgrade_native_outcomes import codex_outcomes, claude_outcomes, native_text
from upgrade_startup_contract import (GOAL_SCHEMA, pressure_startup_prompt, readiness_command, readiness_prompt,
                                      startup_continuity_enabled)
import upgrade_toml as tomllib

MAX_STARTUP_RECORDS = 65536
MAX_STARTUP_BYTES = 32 * 1024 * 1024
SCHEMA = "upgrade-startup-continuity-v1"
FIELDS = {"schema", "runtime", "startup", "task_id", "inspection", "sessions", "healthy_lanes", "failed_lanes",
          "native_authorizations", "source_verified", "production_adoption", "input_operations",
          "automatic_fallback_authorized"}


def continuity_path(spec):
    return Path(spec["adoption_path"]).parent / "startup-continuity.json"


@contract_errors("original startup user authorization")
def original_user_authorizations(native, records, *, account_home, experiment_root, original_sessions):
    """Read exact original user records, never an experiment's startup prompt.

    Production callers supply the immutable account context, not a CLI HOME
    override. Reuse the original metadata and user-message grammar; this bounded
    read does not require adoption, live availability or a model client.
    """
    home, experiment = checked_path(account_home), checked_path(experiment_root)
    require(isinstance(records, list) and 0 < len(records) <= 8,
            "STARTUP_CONTINUITY_NATIVE_AUTHORIZATION_REQUIRED")
    excluded = {session["session_id"] for session in original_sessions.values()}
    for item in records:
        require(set(item) == {"session", "record", "text_sha256"},
                "STARTUP_CONTINUITY_AUTHORIZATION_CHANGED")
        session, ref = item["session"], item["record"]
        require(set(session) == {"provider", "session_id", "transcript"}
                and session["provider"] in ("codex", "claude")
                and str(uuid.UUID(session["session_id"])) == session["session_id"]
                and session["session_id"] not in excluded,
                "STARTUP_CONTINUITY_ORIGINAL_SUPERVISOR_USER_REQUIRED")
        pin = session["transcript"]
        require(set(pin) == {"path", "device", "inode"}
                and set(ref) == {"path", "device", "inode", "offset", "length", "sha256"}
                and all(ref[key] == pin[key] for key in ("path", "device", "inode")),
                "STARTUP_CONTINUITY_AUTHORIZATION_RECORD_IDENTITY_CHANGED")
        path = checked_path(pin["path"])
        root = home / (".codex/sessions" if session["provider"] == "codex" else ".claude/projects")
        require(root in path.parents and experiment != path and experiment not in path.parents
                and path.name.endswith(session["session_id"] + ".jsonl"),
                "STARTUP_CONTINUITY_ORIGINAL_ACCOUNT_USER_REQUIRED")
        for key in ("device", "inode", "offset", "length"):
            count(ref[key], "original user " + key)
        require(0 < ref["length"] <= native.MAX_RECORD,
                "STARTUP_CONTINUITY_AUTHORIZATION_RECORD_BOUND_CHANGED")
        end = ref["offset"] + ref["length"]
        # Nonblocking/no-follow opening refuses a substituted FIFO or symlink.
        # Only the original inode, complete record and exact user text count.
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            def identity():
                held, current = os.fstat(stream.fileno()), path.lstat()
                require(stat.S_ISREG(held.st_mode) and held.st_uid == os.getuid()
                        and (held.st_dev, held.st_ino) == (pin["device"], pin["inode"])
                        == (current.st_dev, current.st_ino) and held.st_size >= end,
                        "STARTUP_CONTINUITY_ORIGINAL_USER_FILE_CHANGED")
            identity()
            native._metadata(stream, session["provider"], session["session_id"])
            if ref["offset"]:
                stream.seek(ref["offset"] - 1)
                require(stream.read(1) == b"\n", "STARTUP_CONTINUITY_USER_RECORD_BOUNDARY_CHANGED")
            stream.seek(ref["offset"])
            raw = stream.read(ref["length"])
            require(len(raw) == ref["length"] and raw.endswith(b"\n") and b"\n" not in raw[:-1]
                    and digest(raw) == ref["sha256"], "STARTUP_CONTINUITY_ORIGINAL_USER_RECORD_CHANGED")
            row = strict_json(raw, "original startup authorization")
            require(isinstance(row, dict) and (session["provider"] != "claude"
                    or row.get("sessionId") == session["session_id"]),
                    "STARTUP_CONTINUITY_ORIGINAL_USER_SESSION_CHANGED")
            text = native_text(row, session["provider"], "user")
            require(isinstance(text, str) and bool(text.strip()) and digest(text.encode()) == item["text_sha256"],
                    "STARTUP_CONTINUITY_ORIGINAL_USER_MESSAGE_REQUIRED")
            identity()
            native._metadata(stream, session["provider"], session["session_id"])
    return copy.deepcopy(records)


def _runtime(runtime_ref):
    # The external adapter validates the complete runtime/source closure. The
    # private input gate has a distinct namespace and validates its own frozen
    # startup/binding before invoking the same evidence contract below.
    from upgrade_live_runtime import validate_live_runtime
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    binding = json_ref(file_ref(Path(runtime["controller"]["path"]) / "upgrade-binding.json"))
    require(startup_continuity_enabled(binding), "STARTUP_CONTINUITY_NOT_REGISTERED")
    return runtime, spec, binding


SOURCE_VERIFY = r'''
import json, sys
from pathlib import Path
source = Path(sys.argv[1])
sys.path.insert(0, str(source / 'scripts'))
import cmux_native_delivery as native
import mac_upgrade_startup_continuity as original
if Path(original.__file__).resolve() != source / 'scripts/mac_upgrade_startup_continuity.py':
    raise ValueError('STARTUP_CONTINUITY_MODULE_ORIGIN_CHANGED')
raw = sys.stdin.buffer.read(1024 * 1024 + 1)
if len(raw) > 1024 * 1024:
    raise ValueError('STARTUP_CONTINUITY_REQUEST_BOUND_EXCEEDED')
value = json.loads(raw)
checked = original.validate_source_contract(value['value'], value['runtime_ref'], value['runtime'],
    value['spec'], value['binding'], native, allow_stopped=value['allow_stopped'])
print(json.dumps(checked, ensure_ascii=False))
'''


def _verify_in_controller(value, runtime_ref, runtime, spec, binding, *, allow_stopped=False):
    """One fixed non-model verifier, not module swapping in the current client."""
    from upgrade_client_isolation import clean_child_environment
    verify_tree(runtime["controller"])
    raw = canonical(dict(value=value, runtime_ref=runtime_ref, runtime=runtime, spec=spec,
                         binding=binding, allow_stopped=allow_stopped))
    require(len(raw) <= 1024 * 1024, "STARTUP_CONTINUITY_REQUEST_BOUND_EXCEEDED")
    command = ["/usr/bin/sandbox-exec", "-f", spec["pins"]["control_sandbox"]["path"],
        runtime["python"]["path"], "-I", "-B", "-c", SOURCE_VERIFY, runtime["controller"]["path"]]
    checked = subprocess.run(command, input=raw, capture_output=True, timeout=30,
        cwd=spec["work_root"], env=clean_child_environment({}), check=False)
    require(checked.returncode == 0, "STARTUP_CONTINUITY_SOURCE_VERIFIER_REFUSED:" +
            checked.stderr.decode(errors="replace")[-4000:])
    require(strict_json(checked.stdout) == value, "STARTUP_CONTINUITY_SOURCE_VERIFIER_RESULT_CHANGED")
    verify_tree(runtime["controller"])
    return value


@contract_errors("startup continuity source contract")
def validate_source_contract(value, runtime_ref, runtime, spec, binding, native, *, allow_stopped=False):
    """Authenticate exact original launch, journals, hooks and user source.

    This is not trio adoption and grants no Codex takeover or shared-file owner.
    Kernel/process freshness remains a separate requirement at each real input.
    """
    require(set(value) == FIELDS and value["schema"] == SCHEMA
            and value["runtime"] == runtime_ref and value["startup"] == runtime["startup"]
            and value["source_verified"] is True and value["production_adoption"] is False
            and type(value["input_operations"]) is int and value["input_operations"] == 0
            and value["automatic_fallback_authorized"] is True,
            "STARTUP_CONTINUITY_FIELDS_CHANGED")
    require(startup_continuity_enabled(binding), "STARTUP_CONTINUITY_NOT_REGISTERED")
    require(isinstance(value["task_id"], str) and 0 < len(value["task_id"]) <= 128
            and value["task_id"] == value["task_id"].strip(), "STARTUP_CONTINUITY_TASK_CHANGED")
    if not allow_stopped:
        require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    inspection_ref = value["inspection"]
    within(inspection_ref["path"], Path(spec["adoption_path"]).parent / "observations")
    observation = json_ref(inspection_ref, "original startup inspection")
    expected_argv = ["/usr/bin/sandbox-exec", "-f", spec["pins"]["control_sandbox"]["path"],
        runtime["python"]["path"], "-I", "-B", str(Path(spec["controller"]) / "scripts/mac_harness.py"),
        "experiment-inspect", "--startup", runtime["startup"]["path"],
        "--startup-sha256", runtime["startup"]["sha256"]]
    require(observation["runtime"] == runtime_ref and observation["startup"] == runtime["startup"]
            and observation["command"] == "experiment-inspect" and observation["argv"] == expected_argv
            and type(observation["returncode"]) is int and observation["returncode"] == 0
            and type(observation["input_operations"]) is int and observation["input_operations"] == 0,
            "STARTUP_CONTINUITY_INSPECTION_CHANGED")
    result = observation["result"]
    require(result["schema"] == "upgrade-live-inspection-v2" and result["startup"] == runtime["startup"]
            and result["ready"] is False and result["stopped"] is False and result["input_operations"] == 0
            and result["sessions"] == value["sessions"]
            and set(result["readiness"]) == {"claude_a", "claude_b"}
            and result["errors"] == {"codex": "READINESS_TOOL_NOT_UNIQUE_OR_NOT_OBSERVED"}
            and value["healthy_lanes"] == ["claude_a", "claude_b"] and value["failed_lanes"] == ["codex"]
            and set(result.get("startup_failures", {})) == {"codex"},
            "STARTUP_CONTINUITY_ORIGINAL_FAILURE_NOT_PROVEN")
    require(result["inspector"] == file_ref(Path(spec["controller"]) / "scripts/mac_upgrade_live_inspect.py"),
            "STARTUP_CONTINUITY_INSPECTOR_CHANGED")
    require(set(value["sessions"]) == {"codex", "claude_a", "claude_b"}
            and len({str(uuid.UUID(s["workspace_uuid"])) for s in value["sessions"].values()}) == 1
            and all(len({str(uuid.UUID(s[key])) for s in value["sessions"].values()}) == 3
                    for key in ("surface_uuid", "pane_uuid", "session_id")),
            "STARTUP_CONTINUITY_ORIGINAL_ENDPOINTS_CHANGED")
    capability, codex = result["codex_capability"], value["sessions"]["codex"]
    require(capability["thread_id"] == codex["session_id"]
            and capability["path"] == codex["transcript"]["path"]
            and capability["can_accept_direct_input"] is True
            and capability["methods"] == ["thread/read", "thread/turns/list"]
            and type(capability["input_operations"]) is int and capability["input_operations"] == 0,
            "STARTUP_CONTINUITY_CODEX_CAPABILITY_CHANGED")
    control = Path(binding["state_root"]) / "startup"
    intent, launched = json_ref(result["launch_intent"]), json_ref(result["launch_result"])
    require(result["launch_intent"] == file_ref(control / "intent.json")
            and result["launch_result"] == file_ref(control / "result.json")
            and intent["startup"] == launched["startup"] == runtime["startup"]
            and intent["spawn"] is True and intent["spawn_authorized"] is True
            and launched["returncode"] == 0 and launched["workspace_creation_attempts"] == 1,
            "STARTUP_CONTINUITY_ORIGINAL_LAUNCH_CHANGED")
    launches = {}
    require(set(result["launches"]) == {"codex", "claude_a", "claude_b", "codex-server"}
            and set(result["kernel"]) == set(result["launches"]), "STARTUP_CONTINUITY_KERNEL_PROOF_MISSING")
    for lane, ref in result["launches"].items():
        require(ref == file_ref(control / (lane + ".json")), "STARTUP_CONTINUITY_LAUNCH_PIN_CHANGED")
        launch = json_ref(ref)
        require(launch["startup"] == runtime["startup"] and result["kernel"][lane]["pid"] == launch["pid"]
                and result["kernel"][lane]["kernel_checked"] is True,
                "STARTUP_CONTINUITY_ORIGINAL_KERNEL_CHANGED")
        launches[lane] = launch
    failure = result["startup_failures"]["codex"]
    for lane, session in value["sessions"].items():
        launch = launches[lane]
        require(session["provider"] == ("codex" if lane == "codex" else "claude")
                and session["surface_uuid"] not in spec["protected_surface_uuids"]
                and session["process"]["pid"] == launch["pid"] and session["process"]["birth"] == launch["birth"]
                and session["surface_uuid"] == launch["surface_uuid"]
                and session["workspace_uuid"] == launch["workspace_uuid"]
                and session["native_config_root"] == spec["clients"][lane]["native_root"]
                and session["model_record"] == (failure if lane == "codex" else result["readiness"][lane])["model_record"],
                "STARTUP_CONTINUITY_ORIGINAL_SESSION_CHANGED")
        base = Path(session["native_config_root"]) / ("sessions" if lane == "codex" else "projects")
        within(session["transcript"]["path"], base)
        require(Path(session["transcript"]["path"]).name.endswith(session["session_id"] + ".jsonl"),
                "STARTUP_CONTINUITY_ORIGINAL_TRANSCRIPT_CHANGED")
        if lane != "codex":
            require(session["session_id"] == spec["clients"][lane]["session_id"],
                    "STARTUP_CONTINUITY_ORIGINAL_CLAUDE_CHANGED")
        # Authenticate the original fixed interval, then observe its same-inode
        # growth. A later Codex model/input/success cannot reuse an old402.
        with native._open(session["transcript"]["path"]) as stream:
            native._check_file(stream, session)
        fresh = copy.deepcopy(session)
        fresh["transcript"] = native._pin_file(session["transcript"]["path"], session["provider"], session["session_id"])
        if lane == "codex":
            checked = startup_failure_evidence(native, fresh, lane, spec, runtime["startup"],
                task_id=value["task_id"], now=time.time())
            require({k: v for k, v in checked.items() if k != "observed_eof"}
                    == {k: v for k, v in failure.items() if k != "observed_eof"},
                    "STARTUP_CONTINUITY_FAILURE_CHANGED")
        else:
            checked = readiness_evidence(native, fresh, lane, spec, runtime["startup"], binding, launches)
            require(checked == result["readiness"][lane] and checked["model"] == session["model"],
                    "STARTUP_CONTINUITY_READINESS_CHANGED")
            size, seen, rows = 0, 0, []
            for ref, row in native_records(native, fresh):
                size += ref["length"]
                seen += 1
                require(seen <= MAX_STARTUP_RECORDS and size <= MAX_STARTUP_BYTES,
                        "STARTUP_CONTINUITY_READY_INTERVAL_PENDING")
                rows.append((ref, row))
                if row.get("type") != "assistant" or row.get("isSidechain") is True:
                    continue
                message = row.get("message", {})
                model = message.get("model") if isinstance(message, dict) else None
                if row.get("isApiErrorMessage") is not True and model not in (None, "<synthetic>"):
                    require(model == session["model"], "STARTUP_CONTINUITY_READY_MODEL_CHANGED")
            # Missing UUID ancestry or terminal fields are unknown, not an
            # available participant. Reuse the one original native grammar.
            complete = all(isinstance(row.get("uuid"), str) for ref, row in rows
                           if row.get("type") == "user" and native_text(row, "claude", "user") is not None)
            require(complete, "STARTUP_CONTINUITY_READY_API_OUTCOME_REQUIRED")
            outcomes = claude_outcomes(fresh, rows, value["task_id"], time.time())
            require(outcomes and outcomes[-1]["kind"] == "API_SUCCESS"
                    and any(event["native_record"] == checked["call"] and event["kind"] == "API_SUCCESS"
                            for event in outcomes), "STARTUP_CONTINUITY_READY_API_OUTCOME_REQUIRED")
            with native._open(fresh["transcript"]["path"]) as stream:
                native._check_file(stream, fresh)
    context = json_ref(spec["context"], "original startup account context")
    require(context["account_uid"] == os.getuid()
            and context["account_home"] == str(Path.home().resolve())
            and spec["context"] == binding["context"] == runtime["context"],
            "STARTUP_CONTINUITY_AUTHORIZATION_ACCOUNT_CHANGED")
    original_user_authorizations(native, value["native_authorizations"],
        account_home=context["account_home"], experiment_root=context["root"],
        original_sessions=value["sessions"])
    return value


@contract_errors("register original startup continuity")
def register_startup_continuity(runtime_ref, inspection_ref, *, task_id, native_authorizations,
                                automatic_fallback_authorized):
    """Publish a source-bound alternative to trio adoption, never replace it."""
    from upgrade_live_control import external_publisher
    runtime, spec, binding = _runtime(runtime_ref)
    external_publisher(spec)
    require(automatic_fallback_authorized is True, "STARTUP_CONTINUITY_EXPLICIT_AUTHORIZATION_REQUIRED")
    require(not os.path.lexists(spec["adoption_path"]) and not os.path.lexists(continuity_path(spec)),
            "STARTUP_CONTINUITY_ORIGINAL_CONTRACT_EXISTS")
    inspection = json_ref(inspection_ref)
    result = inspection["result"]
    require(type(result.get("observed_at_ns")) is int
            and 0 <= time.time_ns() - result["observed_at_ns"] < 30 * 10**9,
            "STARTUP_CONTINUITY_INSPECTION_TOO_OLD")
    value = dict(schema=SCHEMA, runtime=runtime_ref, startup=runtime["startup"], task_id=task_id,
        inspection=inspection_ref, sessions=copy.deepcopy(result["sessions"]),
        healthy_lanes=["claude_a", "claude_b"], failed_lanes=["codex"], native_authorizations=native_authorizations,
        source_verified=True, production_adoption=False, input_operations=0, automatic_fallback_authorized=True)
    _verify_in_controller(value, runtime_ref, runtime, spec, binding)
    require(_runtime(runtime_ref) == (runtime, spec, binding), "STARTUP_CONTINUITY_RUNTIME_CHANGED")
    require(not os.path.lexists(spec["adoption_path"]) and not os.path.lexists(spec["stop_path"]),
            "STARTUP_CONTINUITY_STOP_OR_TRIO_WON")
    ref = write_json(continuity_path(spec), value, exclusive=True)
    Path(ref["path"]).chmod(0o444)
    return ref


@contract_errors("original startup continuity registration")
def validate_startup_continuity(runtime_ref, ref, *, allow_stopped=False):
    runtime, spec, binding = _runtime(runtime_ref)
    require(ref["path"] == str(continuity_path(spec)), "STARTUP_CONTINUITY_LOCATION_CHANGED")
    value = json_ref(ref)
    _verify_in_controller(value, runtime_ref, runtime, spec, binding, allow_stopped=allow_stopped)
    return value


@contract_errors("original startup failure")
def startup_failure_evidence(native, session, lane, spec, startup_ref, *, task_id, now):
    """Prove one original pre-probe failure from a complete process-bound journal.

    The caller must authenticate the original process and kernel scope first.
    This function never upgrades a copied file into a live-native authority.
    """
    require(lane == "codex" and session["provider"] == "codex", "STARTUP_FAILURE_ORIGINAL_CODEX_REQUIRED")
    now = finite(now, "startup continuity clock")
    config = tomllib.loads(read_ref(spec["pins"]["codex/config.toml"], "original Codex configuration").decode())
    model = config.get("model")
    require(isinstance(model, str) and bool(model), "STARTUP_FAILURE_PINNED_MODEL_REQUIRED")
    command = readiness_command(spec["controller"], spec["pins"]["python"]["path"],
                                spec["pins"]["rtk"]["path"], lane)
    prompt = (pressure_startup_prompt(lane, spec["lane_instructions"], spec["goal_startup"])
              if spec.get("schema") == GOAL_SCHEMA else
              readiness_prompt(spec["controller"], spec["pins"]["python"]["path"],
                               spec["pins"]["rtk"]["path"], lane))
    rows, prompts, models, previous, size = [], [], [], None, 0
    started, completed, open_turn = {}, set(), None
    for ref, record in native_records(native, session):
        require(len(rows) < MAX_STARTUP_RECORDS and size + ref["length"] <= MAX_STARTUP_BYTES,
                "STARTUP_FAILURE_NATIVE_INTERVAL_PENDING")
        size += ref["length"]
        epoch = parse_time(record["timestamp"]).timestamp() if "timestamp" in record else None
        if epoch is not None:
            require(0 <= epoch <= now and (previous is None or epoch >= previous),
                    "STARTUP_FAILURE_NATIVE_CHRONOLOGY_CHANGED")
            previous = epoch
        rows.append((ref, record))
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if native_text(record, "codex", "user") == prompt:
            prompts.append((ref, open_turn))
        if record.get("type") == "turn_context" and payload.get("model"):
            require(payload["model"] == model, "STARTUP_FAILURE_MODEL_CHANGED")
            models.append((ref, payload))
        if (record.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call")
                and command_in_tool(payload.get("arguments", payload.get("input")), command)):
            require(False, "STARTUP_FAILURE_PROBE_ALREADY_EXECUTED")
        if record.get("type") == "event_msg" and payload.get("type") == "task_started":
            require(open_turn is None, "STARTUP_FAILURE_OVERLAPPING_TURNS")
            open_turn = payload.get("turn_id")
            require(isinstance(open_turn, str) and open_turn not in started,
                    "STARTUP_FAILURE_ORIGINAL_TURN_REQUIRED")
            started[open_turn] = ref
        elif record.get("type") == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
            require(open_turn is not None and payload.get("turn_id") == open_turn,
                    "STARTUP_FAILURE_TERMINAL_WITHOUT_ORIGINAL_START")
            completed.add(open_turn)
            open_turn = None
    require(len(prompts) == 1 and models and open_turn is None,
            "STARTUP_FAILURE_ORIGINAL_COMPLETE_INPUT_REQUIRED")
    prompt_ref, turn = prompts[0]
    require(turn in started and turn in completed, "STARTUP_FAILURE_PROMPT_TURN_CHANGED")
    values = codex_outcomes(dict(session, model=model), rows, task_id, now)
    require(values and values[-1]["attempt_id"] == "codex-turn:" + turn
            and values[-1]["kind"] == "API_ERROR" and values[-1].get("http_status") in {401, 402, 403},
            "STARTUP_FAILURE_UNAVAILABLE_NOT_PROVEN")
    event = values[-1]
    require(started[turn]["offset"] < prompt_ref["offset"] < event["native_record"]["offset"],
            "STARTUP_FAILURE_INPUT_BOUNDARY_CHANGED")
    relevant = [(ref, value) for ref, value in models
                if value.get("turn_id") == turn and ref["offset"] < event["native_record"]["offset"]]
    require(relevant, "STARTUP_FAILURE_ORIGINAL_MODEL_CONTEXT_REQUIRED")
    # Recheck the captured complete interval after all parsing; it may grow,
    # but none of its original prefix/tail or process-bound inode may change.
    with native._open(session["transcript"]["path"]) as stream:
        native._check_file(stream, session)
    read_ref(spec["pins"]["codex/config.toml"], "unchanged original Codex configuration")
    return dict(schema="upgrade-original-startup-failure-v1", startup=startup_ref, lane=lane,
        prompt=prompt_ref, model=model, model_record=relevant[-1][0], outcome=event,
        observed_eof=session["transcript"], adoption_proven=False, input_operations=0)


def main(argv=None):
    """Only hash-pinned register/verify; no input, fixture, model or clock override."""
    from orchestration_campaign import anchored_ref
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register", allow_abbrev=False)
    register.add_argument("--request", required=True)
    register.add_argument("--request-sha256", required=True)
    verify = commands.add_parser("verify", allow_abbrev=False)
    verify.add_argument("--registration", required=True)
    verify.add_argument("--registration-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            request = json_ref(anchored_ref(args.request, args.request_sha256), "original startup request")
            require(set(request) == {"schema", "runtime", "inspection", "task_id", "native_authorizations",
                                     "automatic_fallback_authorized"}
                    and request["schema"] == "upgrade-startup-continuity-register-request-v1",
                    "STARTUP_CONTINUITY_REQUEST_CHANGED")
            ref = register_startup_continuity(request["runtime"], request["inspection"],
                task_id=request["task_id"], native_authorizations=request["native_authorizations"],
                automatic_fallback_authorized=request["automatic_fallback_authorized"])
            status = "ORIGINAL_STARTUP_REGISTERED_NOT_ADOPTED"
        else:
            ref = anchored_ref(args.registration, args.registration_sha256)
            value = json_ref(ref, "original startup registration")
            validate_startup_continuity(value["runtime"], ref, allow_stopped=True)
            status = "ORIGINAL_STARTUP_SOURCE_VERIFIED_NOT_ADOPTED"
        print(json.dumps(dict(status=status, registration=ref, input_operations=0, model_requests=0,
            production_adoption=False, shared_write_authority=False, replay_allowed=False), ensure_ascii=False))
        return 0
    except ContractError as exc:
        print(json.dumps(dict(status="REFUSED_EVIDENCE_PRESERVED", error=str(exc), input_operations=0,
            model_requests=0, production_adoption=False), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
