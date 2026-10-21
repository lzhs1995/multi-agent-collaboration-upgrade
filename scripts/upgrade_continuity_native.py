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

from orchestration_common import (ContractError, canonical, checked_path, digest, file_ref, finite,
    contract_errors, json_ref, object_sha, parse_time, read_ref, require, strict_json, utc_now,
    verify_tree, within, write_bytes_once, write_json)
from orchestration_native import NativeReader, native_verification
from upgrade_native_outcomes import canonical_uuid, outcome, codex_outcomes, claude_outcomes
from upgrade_experiment import validate_sessions

LANES = ("codex", "claude_a", "claude_b")
MAX_EPISODE_RECORDS = 65536
MAX_EPISODE_BYTES = 32 * 1024 * 1024
RETRYABLE_HTTP_STATUSES = frozenset((429, 500, 502, 503, 504, 524, 529))


def _retry_delay_pending_lanes(sessions, collection):
    """Native terminals preserve status, but not authoritative server advice.

    Do not let the original evaluator's default60 replace an unknown longer
    Retry-After. A later terminal success or another definitive outcome still
    supersedes the retryable failure; old failures do not revoke membership.
    No caller-added field can supply missing provider timing authority.
    """
    latest = {event["surface_uuid"]: event for event in collection["observations"]}
    return [lane for lane in LANES
            if (event := latest.get(sessions[lane]["surface_uuid"])) is not None
            and event["kind"] == "API_ERROR"
            and event.get("http_status") in RETRYABLE_HTTP_STATUSES]



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


@native_verification
@contract_errors("original startup native continuity interval")
def collect_startup_native_outcomes(sessions, runtime_ref, proof_ref, *, task_id, now,
                                   max_records=256, observed_eofs=None):
    """A separately named initial interval, not a rewritten adoption episode.

    Keep the original registration fences in the result and on disk. Only the
    explicitly opted-in, source-verified startup contract may observe its own
    initial journal history, including the failure before registration.
    """
    from upgrade_live_runtime import identity_matches, validate_session_authority
    proof = validate_session_authority(runtime_ref, proof_ref, allow_stopped=True)
    require(proof.get("schema") == "upgrade-startup-continuity-v1" and proof["task_id"] == task_id,
            "ORIGINAL_STARTUP_CONTINUITY_INTERVAL_REQUIRED")
    validate_sessions(sessions)
    initial = copy.deepcopy(sessions)
    for lane, session in initial.items():
        require(identity_matches(session, proof["sessions"][lane])
                and session.get("private_native") == dict(runtime=runtime_ref, adoption=proof_ref),
                "ORIGINAL_STARTUP_CONTINUITY_SESSION_CHANGED")
        reader = NativeReader(session)
        require(reader.verify_pin(session["freeze_fence"]) >=
                reader.verify_pin(proof["sessions"][lane]["freeze_fence"]),
                "ORIGINAL_STARTUP_REGISTRATION_FENCE_CHANGED")
        # Explicitly label zero as STARTUP history, never adoption or a new
        # task fence. Same account/inode/metadata and per-record checks remain.
        session["freeze_fence"] = dict(session["transcript"], offset=0, prefix_size=0,
            prefix_sha256=digest(b""), tail_at=0, tail_sha256=digest(b""), at_record_boundary=True)
    collection = collect_native_outcomes(initial, task_id=task_id, now=now,
        max_records=max_records, observed_eofs=observed_eofs)
    return dict(collection, schema="upgrade-startup-native-continuity-observations-v1",
        observation_scope="ORIGINAL_STARTUP_NOT_ADOPTION_EPISODE", startup_continuity=proof_ref,
        registration_fences={lane: copy.deepcopy(session["freeze_fence"]) for lane, session in sessions.items()})


def _collect_original_interval(sessions, collection, *, runtime_ref, **interval):
    if collection.get("schema") == "upgrade-startup-native-continuity-observations-v1":
        require(runtime_ref is not None and interval.pop("fixtures") is False,
                "ORIGINAL_STARTUP_NATIVE_RUNTIME_REQUIRED")
        return collect_startup_native_outcomes(sessions, runtime_ref, collection["startup_continuity"], **interval)
    return collect_native_outcomes(sessions, **interval)


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
    fresh = _collect_original_interval(sessions, collection, runtime_ref=runtime_ref, **interval)
    require(fresh == collection, "native outcomes changed or caller-authored labels supplied")
    require(fixtures or not _retry_delay_pending_lanes(sessions, collection),
            "ORIGINAL_RETRY_DELAY_NOT_OBSERVABLE")
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
    require(collection == _collect_original_interval(sessions, collection, runtime_ref=runtime_ref, **interval),
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
    from upgrade_live_runtime import validate_session_authority
    proof = validate_session_authority(runtime_ref, adoption_ref, allow_stopped=True)
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
    from upgrade_live_runtime import validate_live_runtime, validate_session_authority
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
    proof = validate_session_authority(runtime_ref, adoption_ref)
    if proof.get("schema") == "upgrade-startup-continuity-v1":
        require(task_id == proof["task_id"] and native_authorizations == proof["native_authorizations"],
                "STARTUP_CONTINUITY_OBSERVER_AUTHORIZATION_CHANGED")
        records = proof["native_authorizations"]
    else:
        records = authorize(native_authorizations)
    sessions = registered_sessions(runtime_ref, adoption_ref)
    authorization = dict(schema="upgrade-native-continuity-authorization-v1", source="user_message",
        task_id=task_id, runtime=runtime_ref, adoption=adoption_ref, approved_root=str(root),
        automatic_fallback_authorized=True, max_subagents=2, child_budget_scope="WHOLE_TASK",
        session_ids={lane: sessions[lane]["session_id"] for lane in LANES}, native_records=records,
        advisory_only=True, takeover_authorized=False, input_operations=0, resource_releases=0)
    _, current = validate_live_runtime(runtime_ref)
    require(current == runtime, "native registration runtime changed before publication")
    if proof.get("schema") == "upgrade-startup-continuity-v1":
        require(validate_session_authority(runtime_ref, adoption_ref) == proof, "STARTUP_CONTINUITY_CHANGED")
    else:
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
    from upgrade_live_runtime import validate_session_authority, validate_live_runtime, identity_matches
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
    proof = validate_session_authority(value["runtime"], value["adoption"], runtime=runtime, allow_stopped=True)
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
    if proof.get("schema") == "upgrade-startup-continuity-v1":
        require(value["task_id"] == proof["task_id"] and authorization["native_records"] == proof["native_authorizations"],
                "STARTUP_CONTINUITY_OBSERVER_AUTHORIZATION_CHANGED")
        records = proof["native_authorizations"]
    else:
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
    from upgrade_live_runtime import validate_session_authority
    proof = validate_session_authority(value["runtime"], value["adoption"], runtime=runtime, allow_stopped=True)
    if proof.get("schema") == "upgrade-startup-continuity-v1":
        collection = collect_startup_native_outcomes(value["sessions"], value["runtime"], value["adoption"],
            task_id=value["task_id"], now=now, max_records=max_records)
    else:
        collection = collect_native_outcomes(value["sessions"], task_id=value["task_id"], now=now,
                                             max_records=max_records)
    if collection["pending_lanes"]:
        return dict(status="ORIGINAL_NATIVE_INTERVAL_PENDING", observations=collection,
            advisory_only=True, child_budget_enforced=False, input_operations=0, resource_releases=0)
    retry_pending = _retry_delay_pending_lanes(value["sessions"], collection)
    if retry_pending:
        return dict(status="ORIGINAL_RETRY_DELAY_PENDING", observations=collection,
            retry_delay_pending_lanes=retry_pending, advisory_only=True,
            child_budget_enforced=False, input_operations=0, resource_releases=0)
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
