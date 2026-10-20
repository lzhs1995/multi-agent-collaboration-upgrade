"""Read original native outcomes for the original MAC continuity evaluator.

No input, provider request, retry, role transfer or lease mutation occurs here.
Fixture mode is explicit Python-only testing; the publishing CLI never enables
it. Complete native API outcomes, not a caller's health labels, drive decisions.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from orchestration_common import (ContractError, canonical, checked_path, file_ref, finite,
    contract_errors, json_ref, object_sha, parse_time, read_ref, require, strict_json, utc_now,
    verify_tree, within, write_bytes_once, write_json)
from orchestration_native import NativeReader, native_text, native_verification
from upgrade_experiment import validate_sessions

LANES = ("codex", "claude_a", "claude_b")
MAX_EPISODE_RECORDS = 65536
MAX_EPISODE_BYTES = 32 * 1024 * 1024


def canonical_uuid(value, label):
    require(isinstance(value, str) and str(uuid.UUID(value)) == value,
            label + " must be an original canonical UUID")
    return value


def outcome(session, task_id, ref, record, attempt_id, now, *, kind="API_SUCCESS", http_status=None):
    epoch = parse_time(record["timestamp"]).timestamp()
    require(0 <= epoch <= now, "native outcome is outside the observation clock")
    value = dict(task_id=task_id, surface_uuid=session["surface_uuid"],
                session_id=session["session_id"], attempt_id=attempt_id,
                kind=kind, terminal=True, at_epoch=epoch, native_record=ref)
    if http_status is not None:
        require(type(http_status) is int and 100 <= http_status <= 599,
                "native HTTP status must be a concrete integer")
        value["http_status"] = http_status
    return value


def codex_outcomes(session, records, task_id, now, *, outcome_after=0):
    active, model, final = None, None, None
    active_offset, active_observed = None, False
    values = []

    def append(ref, record, attempt_id, **fields):
        if ref["offset"] >= outcome_after:
            values.append(outcome(session, task_id, ref, record, attempt_id, now, **fields))

    for ref, record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "event_msg" and payload.get("type") == "task_started":
            # A crash can leave an unclosed historical turn. It is not current
            # availability or an API error. A fresh start may replace only that
            # pre-fence state, never a turn with observed post-fence activity.
            require(active is None or (active_offset < outcome_after and not active_observed),
                    "overlapping original native turns")
            active = canonical_uuid(payload.get("turn_id"), "native turn id")
            active_offset, active_observed = ref["offset"], ref["offset"] >= outcome_after
            model, final = None, None
            continue
        # Thread configuration/goal events and session metadata may sit between
        # a crashed historical turn and its next start. They are not activity
        # of that turn. Native response items have implicit open-turn ownership;
        # context and event records must carry the matching original turn id.
        turn_activity = (record.get("type") == "response_item" or
            record.get("type") in ("turn_context", "event_msg") and payload.get("turn_id") == active)
        if active is not None and ref["offset"] >= outcome_after and turn_activity:
            active_observed = True
        if (record.get("type") == "turn_context" and active is not None
              and payload.get("turn_id") == active):
            require(payload.get("model") == session["model"], "native continuity model changed")
            model = payload["model"]
        elif (record.get("type") == "response_item" and active is not None
              and payload.get("phase") == "final_answer"):
            final = native_text(record, "codex", "assistant")
        elif (record.get("type") == "event_msg" and payload.get("type") == "task_complete"
              and active is not None and payload.get("turn_id") == active):
            error = payload.get("error")
            if (model == session["model"] and payload.get("error") is None
                    and isinstance(final, str) and final.strip()
                    and payload.get("last_agent_message") == final):
                append(ref, record, "codex-turn:" + active)
            elif model == session["model"] and isinstance(error, dict):
                info = error.get("codex_error_info")
                status = info.get("http_status_code") if isinstance(info, dict) else None
                if type(status) is int and 400 <= status <= 599:
                    append(ref, record, "codex-turn:" + active, kind="API_ERROR", http_status=status)
                else:
                    append(ref, record, "codex-turn:" + active, kind="NATIVE_OUTCOME_UNKNOWN")
            else:
                append(ref, record, "codex-turn:" + active, kind="NATIVE_OUTCOME_UNKNOWN")
            active, model, final = None, None, None
        elif (record.get("type") == "event_msg" and payload.get("type") == "turn_aborted"
              and active is not None and payload.get("turn_id") == active):
            require(payload.get("reason") in ("interrupted", "replaced", "review_ended", "budget_limited"),
                    "native abort has no canonical reason")
            append(ref, record, "codex-turn:" + active, kind="NATIVE_OUTCOME_UNKNOWN")
            active, model, final = None, None, None
    return values


def claude_outcomes(session, records, task_id, now, *, outcome_after=0):
    ancestors, seen_messages, values = {}, set(), []
    for ref, record in records:
        if record.get("isSidechain") is True or record.get("sessionId") != session["session_id"]:
            continue
        if record.get("type") == "user" and native_text(record, "claude", "user") is not None:
            uid = canonical_uuid(record.get("uuid"), "native user id")
            ancestors[uid] = uid
            continue
        parent = ancestors.get(record.get("parentUuid"))
        # Native hooks, deferred tools and queued commands are ancestry links,
        # not assistant API outcomes. They retain the original session/UUID gate.
        if parent and record.get("type") in ("assistant", "user", "system", "attachment"):
            uid = canonical_uuid(record.get("uuid"), "native parent-chain id")
            ancestors[uid] = parent
        if record.get("type") == "system" and record.get("subtype") == "api_error" and parent:
            continue
        message = record.get("message")
        if (record.get("type") == "assistant" and isinstance(message, dict)
                and record.get("isApiErrorMessage") is not True
                and isinstance(message.get("model"), str) and message["model"] != "<synthetic>"):
            require(message["model"] == session["model"], "native continuity model changed")
        if (record.get("type") != "assistant" or not isinstance(message, dict)
                or parent is None
                or record.get("truncatedAfterOutput") is True):
            continue
        if record.get("isApiErrorMessage") is True:
            status = record.get("apiErrorStatus")
            if (message.get("role") == "assistant" and message.get("model") == "<synthetic>"
                    and message.get("stop_reason") == "stop_sequence"):
                canonical_uuid(record.get("uuid"), "native terminal error id")
                message_id = canonical_uuid(message.get("id"), "native error message id")
                if message_id in seen_messages:
                    continue
                seen_messages.add(message_id)
                known = type(status) is int and 400 <= status <= 599
                if ref["offset"] >= outcome_after:
                    values.append(outcome(session, task_id, ref, record, "claude-message:" + message_id, now,
                        kind="API_ERROR" if known else "NATIVE_OUTCOME_UNKNOWN",
                        http_status=status if known else None))
            continue
        content = message.get("content")
        visible = isinstance(content, list) and any(isinstance(block, dict) and (
            block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip()
            or block.get("type") == "tool_use" and isinstance(block.get("id"), str)
            and isinstance(block.get("name"), str) and isinstance(block.get("input"), dict))
            for block in content)
        if (message.get("role") == "assistant" and message.get("type") == "message"
                and message.get("model") == session["model"] and message["model"] != "<synthetic>"
                and message.get("stop_reason") in ("end_turn", "tool_use", "stop_sequence")
                and isinstance(message.get("id"), str) and message["id"].startswith("msg_")
                and visible):
            canonical_uuid(record.get("uuid"), "native assistant id")
            if message["id"] in seen_messages:
                continue
            seen_messages.add(message["id"])
            if ref["offset"] >= outcome_after:
                values.append(outcome(session, task_id, ref, record,
                                      "claude-message:" + message["id"], now))
    return values


@native_verification
@contract_errors("native continuity observation")
def collect_native_outcomes(sessions, *, task_id, now, max_records=256, fixtures=False,
                            observed_eofs=None):
    """Collect one bounded original journal interval, never caller-made outcomes.

    The frozen sessions, device/inode and fences bind every returned record.
    Partial appends yield pending observation, not inferred provider failure.
    Private production roots still require the original live adoption authority.
    """
    require(isinstance(task_id, str) and task_id.strip(), "same original task id required")
    now = finite(now, "native observation clock")
    require(now >= 0, "native observation clock must be a nonnegative epoch")
    require(type(max_records) is int and 0 < max_records <= MAX_EPISODE_RECORDS,
            "native record budget must be between 1 and 65536")
    require(type(fixtures) is bool, "native fixture mode must be explicit")
    if observed_eofs is not None:
        require(isinstance(observed_eofs, dict) and set(observed_eofs) == set(LANES),
                "all original pinned native interval ends required")
    validate_sessions(sessions)
    observations, boundaries, pending = [], {}, []
    for lane in LANES:
        session = copy.deepcopy(sessions[lane])
        reader = NativeReader(session, fixtures=fixtures)
        start = reader.verify_pin(session["freeze_fence"])
        try:
            end = copy.deepcopy(observed_eofs[lane]) if observed_eofs is not None else reader.snapshot()
            require(reader.verify_pin(end) >= start, "native interval precedes original task fence")
        except ContractError as exc:
            if str(exc) != "native EOF is not a record boundary; observe later":
                raise
            pending.append(lane)
            continue
        refs = []

        def bounded_records():
            previous_epoch, byte_count, historical, episode = None, 0, 0, 0
            # Original prior records establish the in-flight turn/parent chain,
            # never availability in this episode. No caller supplies parser state.
            for ref, record in reader.records(0, end["offset"]):
                before = ref["offset"] < start
                if ((before and historical == MAX_EPISODE_RECORDS)
                        or (not before and episode == max_records)
                        or byte_count + ref["length"] > MAX_EPISODE_BYTES):
                    pending.append(lane)
                    return
                if "timestamp" in record:
                    epoch = parse_time(record["timestamp"]).timestamp()
                    require(0 <= epoch <= now, "native record is outside the observation clock")
                    require(previous_epoch is None or epoch >= previous_epoch,
                            "native outcome chronology moved backwards")
                    previous_epoch = epoch
                byte_count += ref["length"]
                historical += int(before)
                episode += int(not before)
                refs.append(ref)
                yield ref, record

        scan = codex_outcomes if lane == "codex" else claude_outcomes
        observations.extend(scan(session, bounded_records(), task_id, now, outcome_after=start))
        boundaries[lane] = end
        reader.verify_metadata()
        reader.verify_pin(session["freeze_fence"])
        reader.verify_pin(end)
        for ref in refs:
            reader.record(ref)
    require(len(observations) <= 256, "bounded original outcome episode exceeded")
    attempts = [(event["surface_uuid"], event["attempt_id"]) for event in observations]
    require(len(set(attempts)) == len(attempts), "duplicate original attempt in native journal")
    return dict(schema="upgrade-native-continuity-observations-v1", task_id=task_id,
                workspace_uuid=sessions["codex"]["workspace_uuid"],
                observations=observations, observed_eofs=boundaries, pending_lanes=pending,
                record_budget=max_records, fixture_mode=fixtures, input_operations=0, resource_releases=0)


ORIGINAL_PLAN = r'''
import hashlib, json, sys
from pathlib import Path
controller = Path(sys.argv[1])
sys.path.insert(0, str(controller / 'scripts'))
import collaboration_continuity as original
if Path(original.__file__).resolve() != controller / 'scripts/collaboration_continuity.py':
    raise ValueError('CONTINUITY_ORIGINAL_MODULE_ORIGIN_CHANGED')
raw_input = sys.stdin.buffer.read(1024 * 1024 + 1)
if len(raw_input) > 1024 * 1024:
    raise ValueError('CONTINUITY_ORIGINAL_INPUT_BUDGET_EXCEEDED')
value = json.loads(raw_input)
result = original.plan(value['input'], now=value['now'])
raw = Path(original.__file__).read_bytes()
print(json.dumps(dict(plan=result, module=dict(path=str(Path(original.__file__).resolve()),
    bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())), ensure_ascii=False))
'''


@contract_errors("original continuity evaluation")
def invoke_original_plan(controller, python, sessions, collection, authorization, *, output, now,
                         fixtures=False, runtime_ref=None):
    """Run the pinned original evaluator with rederived observations, not labels.

    This low-level verifier never grants task scope or fallback permission. The
    production entrypoint validates the original scoped user authorization and
    adoption before calling it. Its independent native re-read binds every pin.
    """
    require(type(fixtures) is bool and collection.get("fixture_mode") is fixtures,
            "explicit fixture mode cannot come from an observation label")
    require(not collection["pending_lanes"] and set(collection["observed_eofs"]) == set(LANES),
            "incomplete original native interval cannot establish continuity")
    interval = dict(task_id=collection["task_id"], now=now, fixtures=fixtures,
                    max_records=collection["record_budget"], observed_eofs=collection["observed_eofs"])
    fresh = collect_native_outcomes(sessions, **interval)
    require(fresh == collection, "native outcomes changed or caller-authored labels supplied")
    verify_tree(controller)
    source = checked_path(controller["path"])
    module = file_ref(source / "scripts/collaboration_continuity.py")
    require(module["sha256"] == controller["files"]["scripts/collaboration_continuity.py"]["sha256"],
            "original continuity evaluator pin changed")
    read_ref(python, "original evaluator Python")
    require(python["path"] == str(Path(sys.executable).resolve()), "use the original pinned Python")
    read_ref(authorization, "original continuity authorization")
    prefix, environment, work_root = [], None, None
    if runtime_ref is not None:
        from upgrade_live_runtime import validate_live_runtime
        from upgrade_client_isolation import SANDBOX, clean_child_environment
        _, runtime = validate_live_runtime(runtime_ref)
        spec = json_ref(runtime["startup"])
        require(runtime["controller"] == controller and runtime["python"] == python,
                "original evaluator runtime binding changed")
        prefix = [str(SANDBOX), "-f", spec["pins"]["control_sandbox"]["path"]]
        environment, work_root = clean_child_environment({}), spec["work_root"]
    else:
        require(fixtures is True, "production original evaluator requires private runtime authority")
    output = checked_path(output)
    require(not output.exists(), "original continuity observation output already exists")
    participants = [dict(role="supervisor" if lane == "codex" else "executor",
        surface_uuid=sessions[lane]["surface_uuid"], session_id=sessions[lane]["session_id"],
        workspace_uuid=sessions[lane]["workspace_uuid"], task_id=collection["task_id"]) for lane in LANES]
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write_json(output / "intent.json", dict(schema="upgrade-original-continuity-intent-v1",
        controller=controller, python=python, authorization=authorization,
        observations=collection, fixture_mode=fixtures, now=now, input_operations=0), exclusive=True)
    observations = []
    for event in collection["observations"]:
        pin = write_json(output / ("outcome-" + object_sha(event) + ".json"), event, exclusive=True)
        Path(pin["path"]).chmod(0o444)
        observations.append(dict(event, evidence=pin))
    value = dict(schema="collaboration-continuity-v1", task_id=collection["task_id"],
        workspace_uuid=collection["workspace_uuid"], participants=participants,
        max_subagents=2, automatic_fallback_authorized=True, authorization=authorization,
        observations=observations)
    argv = prefix + [python["path"], "-I", "-B", "-c", ORIGINAL_PLAN, str(source)]
    result = subprocess.run(argv, input=canonical(dict(input=value, now=now)),
                            capture_output=True, timeout=30, check=False,
                            env=environment, cwd=work_root)
    stdout = write_bytes_once(output / "stdout.txt", result.stdout)
    stderr = write_bytes_once(output / "stderr.txt", result.stderr)
    write_json(output / "process.json", dict(argv=argv, returncode=result.returncode,
        stdout=stdout, stderr=stderr, fixture_mode=fixtures, input_operations=0), exclusive=True)
    require(result.returncode == 0, "original continuity evaluator refused; preserve process evidence")
    actual = strict_json(result.stdout, "original continuity evaluator result")
    require(set(actual) == {"plan", "module"} and actual["module"] == module,
            "original continuity evaluator module provenance changed")
    require(actual["plan"]["input_operations"] == 0 and actual["plan"]["resource_releases"] == 0,
            "original continuity evaluator must be advisory only")
    verify_tree(controller)
    read_ref(python, "unchanged evaluator Python")
    read_ref(authorization, "unchanged original continuity authorization")
    require(collection == collect_native_outcomes(sessions, **interval),
            "native observations changed during original evaluation")
    return actual


REGISTRATION_SCHEMA = "upgrade-native-continuity-registration-v1"
REGISTRATION_FIELDS = {"schema", "task_id", "runtime", "startup", "adoption", "sessions",
                       "authorization", "output_root", "input_operations", "resource_releases"}


def registration_root(runtime, task_id):
    require(isinstance(task_id, str) and task_id == task_id.strip() and bool(task_id)
            and len(task_id.encode()) <= 1024, "same bounded original task id required")
    spec = json_ref(runtime["startup"])
    return checked_path(Path(spec["adoption_path"]).parent / "native-continuity" / object_sha(task_id))


@native_verification
def registered_sessions(runtime_ref, adoption_ref):
    from upgrade_live_runtime import validate_adoption
    proof = validate_adoption(runtime_ref, adoption_ref, allow_stopped=True)
    sessions = copy.deepcopy(proof["sessions"])
    for session in sessions.values():
        session["private_native"] = dict(runtime=runtime_ref, adoption=adoption_ref)
        session["transcript"] = {key: session["transcript"][key] for key in ("path", "device", "inode")}
        reader = NativeReader(session)
        session["freeze_fence"] = reader.snapshot()
        reader.record(session["model_record"])
    return sessions


@contract_errors("native continuity registration")
def register_native_continuity(runtime_ref, adoption_ref, *, task_id, native_authorizations,
                               automatic_fallback_authorized):
    """Publish one scoped read-only episode, never launch or adopt new clients."""
    from orchestration_freeze_v3 import authorize
    from upgrade_live_control import external_publisher
    from upgrade_live_runtime import validate_live_runtime
    from upgrade_startup_contract import continuity_registration_enabled
    require(automatic_fallback_authorized is True, "explicit original task fallback authorization required")
    require(isinstance(native_authorizations, list) and 0 < len(native_authorizations) <= 8,
            "bounded original native user authorization required")
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime["startup"])
    binding = json_ref(file_ref(Path(runtime["controller"]["path"]) / "upgrade-binding.json"))
    require(continuity_registration_enabled(binding), "private continuity registration was not enabled")
    external_publisher(spec)
    root = registration_root(runtime, task_id)
    require(not root.exists(), "original native continuity registration exists; do not reset its fences")
    records = authorize(native_authorizations)
    sessions = registered_sessions(runtime_ref, adoption_ref)
    authorization = dict(schema="upgrade-native-continuity-authorization-v1", source="user_message",
        task_id=task_id, runtime=runtime_ref, adoption=adoption_ref, approved_root=str(root),
        automatic_fallback_authorized=True, max_subagents=2, child_budget_scope="WHOLE_TASK",
        session_ids={lane: sessions[lane]["session_id"] for lane in LANES}, native_records=records,
        advisory_only=True, takeover_authorized=False, input_operations=0, resource_releases=0)
    _, current = validate_live_runtime(runtime_ref)
    require(current == runtime, "native registration runtime changed before publication")
    authorize(records)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    authorization_ref = write_json(root / "authorization.json", authorization, exclusive=True)
    Path(authorization_ref["path"]).chmod(0o444)
    value = dict(schema=REGISTRATION_SCHEMA, task_id=task_id, runtime=runtime_ref,
        startup=runtime["startup"], adoption=adoption_ref, sessions=sessions,
        authorization=authorization_ref, output_root=str(root), input_operations=0, resource_releases=0)
    ref = write_json(root / "registration.json", value, exclusive=True)
    Path(ref["path"]).chmod(0o444)
    return ref


@native_verification
@contract_errors("registered native continuity")
def validate_native_registration(ref):
    from orchestration_freeze_v3 import authorize
    from upgrade_live_runtime import validate_adoption, validate_live_runtime, identity_matches
    from upgrade_startup_contract import continuity_registration_enabled
    value = json_ref(ref, "original native registration")
    require(set(value) == REGISTRATION_FIELDS and value["schema"] == REGISTRATION_SCHEMA
            and type(value["input_operations"]) is int and value["input_operations"] == 0
            and type(value["resource_releases"]) is int and value["resource_releases"] == 0,
            "original native continuity registration fields changed")
    _, runtime = validate_live_runtime(value["runtime"])
    spec = json_ref(runtime["startup"])
    binding = json_ref(file_ref(Path(runtime["controller"]["path"]) / "upgrade-binding.json"))
    require(continuity_registration_enabled(binding), "private continuity registration was not enabled")
    root = registration_root(runtime, value["task_id"])
    require(value["startup"] == runtime["startup"] and value["output_root"] == str(root)
            and ref["path"] == str(root / "registration.json")
            and value["authorization"]["path"] == str(root / "authorization.json"),
            "native continuity registration scope changed")
    proof = validate_adoption(value["runtime"], value["adoption"], runtime=runtime, allow_stopped=True)
    validate_sessions(value["sessions"], protected=spec["protected_surface_uuids"])
    for lane, session in value["sessions"].items():
        require(identity_matches(session, proof["sessions"][lane])
                and session.get("private_native") == dict(runtime=value["runtime"], adoption=value["adoption"]),
                "native continuity original session changed")
        reader = NativeReader(session)
        require(reader.verify_pin(session["freeze_fence"]) >=
                reader.verify_pin(proof["sessions"][lane]["freeze_fence"]),
                "native continuity registration predates original adoption")
        reader.record(session["model_record"])
    authorization = json_ref(value["authorization"], "native continuity authorization")
    require(isinstance(authorization.get("native_records"), list)
            and 0 < len(authorization["native_records"]) <= 8, "original native user records missing")
    records = authorize(authorization["native_records"])
    require(authorization == dict(schema="upgrade-native-continuity-authorization-v1", source="user_message",
        task_id=value["task_id"], runtime=value["runtime"], adoption=value["adoption"], approved_root=str(root),
        automatic_fallback_authorized=True, max_subagents=2, child_budget_scope="WHOLE_TASK",
        session_ids={lane: value["sessions"][lane]["session_id"] for lane in LANES}, native_records=records,
        advisory_only=True, takeover_authorized=False, input_operations=0, resource_releases=0),
        "original task continuity authorization changed")
    return value, runtime


@contract_errors("registered native continuity observation")
def observe_registered_native(registration_ref, *, now, max_records=256):
    """Record an immutable original advisory decision; no input or takeover."""
    from upgrade_live_control import external_publisher
    value, runtime = validate_native_registration(registration_ref)
    spec = json_ref(runtime["startup"])
    external_publisher(spec)
    collection = collect_native_outcomes(value["sessions"], task_id=value["task_id"], now=now,
                                         max_records=max_records)
    if collection["pending_lanes"]:
        return dict(status="ORIGINAL_NATIVE_INTERVAL_PENDING", observations=collection,
            advisory_only=True, child_budget_enforced=False, input_operations=0, resource_releases=0)
    output = within(Path(value["output_root"]) / "observations" / str(uuid.uuid4()), value["output_root"])
    original = invoke_original_plan(runtime["controller"], runtime["python"], value["sessions"],
        collection, value["authorization"], output=output, now=now, runtime_ref=value["runtime"])
    require(validate_native_registration(registration_ref) == (value, runtime),
            "original native continuity authority changed during evaluation")
    result = dict(schema="upgrade-original-native-continuity-result-v1", status="ORIGINAL_ADVISORY_VERIFIED",
        registration=registration_ref, observations=collection, original=original, observed_at=utc_now(),
        advisory_only=True, child_budget_enforced=False, input_operations=0, resource_releases=0)
    result_ref = write_json(output / "result.json", result, exclusive=True)
    Path(result_ref["path"]).chmod(0o444)
    return dict(result, evidence=result_ref)


def main(argv=None):
    """No fixture, clock, model, status, input or role-transfer override exists."""
    from orchestration_campaign import anchored_ref
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register", allow_abbrev=False)
    register.add_argument("--request", required=True)
    register.add_argument("--request-sha256", required=True)
    observe = commands.add_parser("observe", allow_abbrev=False)
    observe.add_argument("--registration", required=True)
    observe.add_argument("--registration-sha256", required=True)
    observe.add_argument("--max-records", type=int, default=256)
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            request = json_ref(anchored_ref(args.request, args.request_sha256), "native registration request")
            require(set(request) == {"schema", "runtime", "adoption", "task_id", "native_authorizations",
                                     "automatic_fallback_authorized"}
                    and request["schema"] == "upgrade-native-continuity-register-request-v1",
                    "native registration request fields changed")
            ref = register_native_continuity(request["runtime"], request["adoption"],
                task_id=request["task_id"], native_authorizations=request["native_authorizations"],
                automatic_fallback_authorized=request["automatic_fallback_authorized"])
            value = dict(status="REGISTERED_NOT_MEASURED", registration=ref, input_operations=0,
                         resource_releases=0, advisory_only=True, child_budget_enforced=False)
        else:
            value = observe_registered_native(anchored_ref(args.registration, args.registration_sha256),
                                               now=time.time(), max_records=args.max_records)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except ContractError as exc:
        print(json.dumps(dict(status="REFUSED_EVIDENCE_PRESERVED", error=str(exc),
            input_operations=0, resource_releases=0, advisory_only=True)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
