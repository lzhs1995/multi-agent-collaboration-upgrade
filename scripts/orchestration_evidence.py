"""Independent v2 acceptance. File claims alone never authorize success.

Checks use original native CLI records and a caller-frozen checker. No executable
path, command, duration or PASS supplied by an executor is treated as authority.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from orchestration_campaign import control_script, find_control, native_control, node_output
from orchestration_contracts import campaign_uuid, campaign_version, controller_uuid, evidence_class, state_root
from orchestration_common import (canonical, checked_path, contract_errors, count, digest, file_ref, json_ref, object_sha, parse_time,
                                  read_ref, strict_json, tree_manifest, utc_now, verify_tree, within)
from orchestration_native import (NativeReader, delivery_identity, native_text, require, verify_delivery, verify_no_input)


def task_identity(campaign, header, node):
    plan = json_ref(campaign["plan"])
    run_id, node_id = header["run"]["run_id"], node["id"]
    return dict(task_id=f"{plan['plan_id']}--{run_id}--{node_id}--a0001",
                attempt_id=f"{run_id}:{node_id}:a0001", run_id=run_id, node_id=node_id)


def marker_for(info, evidence):
    return f"ORCH_ACCEPT_V2|{info['task_id']}|{info['acceptance_nonce']}|{evidence['report']['sha256']}|{evidence['tests']['sha256']}"


def dependency_bindings(node, data):
    """Bind accepted producer artifacts, never a guessed path or pending report."""
    dependencies = []
    for key in sorted(node["depends_on"]):
        info = data[key]
        require(info.get("verification", {}).get("status") == "ACCEPTED", "dependency is not accepted")
        evidence = json_ref(info["accepted_evidence"], "accepted dependency")
        verify_tree(evidence["output_tree"])
        for ref in evidence["output_files"]:
            read_ref(ref, "dependency output")
        dependencies.append(dict(node_id=key, task_id=info["task_id"], attempt_id=info["attempt_id"],
                                 accepted_evidence=info["accepted_evidence"], output_tree=evidence["output_tree"],
                                 output_files=evidence["output_files"]))
    return dependencies


def verify_dependencies(campaign, header, node, info):
    values = info["dependencies"]
    require(isinstance(values, list) and [d["node_id"] for d in values] == sorted(node["depends_on"]),
            "dependency inputs missing, reordered or added")
    plan = {n["id"]: n for n in json_ref(campaign["plan"])["nodes"]}
    for dependency in values:
        require(set(dependency) == {"node_id", "task_id", "attempt_id", "accepted_evidence", "output_tree", "output_files"},
                "dependency pin fields changed")
        producer = plan[dependency["node_id"]]
        expected = task_identity(campaign, header, producer)
        require(all(dependency[k] == expected[k] for k in ("task_id", "attempt_id")), "dependency belongs to another run/attempt")
        require(dependency["accepted_evidence"]["path"] == str(node_directory(campaign, header, producer) / "accepted-evidence.json"),
                "dependency acceptance path changed")
        accepted = json_ref(dependency["accepted_evidence"], "dependency acceptance")
        require(all(accepted.get(k) == v for k, v in expected.items()) and "acceptance_native" in accepted,
                "dependency has no original accepted evidence")
        require(accepted["output_tree"] == dependency["output_tree"] and accepted["output_files"] == dependency["output_files"],
                "dependency outputs differ from their acceptance")
        verify_tree(dependency["output_tree"])
        for ref in dependency["output_files"]:
            read_ref(ref, "dependency output")
    return values


def work_request(campaign, header, node, info):
    request = dict(schema="orchestration-work-request-v2", **task_identity(campaign, header, node),
                output_root=info["output_root"], inputs=campaign["inputs"],
                dependencies=verify_dependencies(campaign, header, node, info))
    if "input_tree" in campaign:
        request["input_tree"] = campaign["input_tree"]
    return request


def task_sources(campaign, header, node, info):
    sources = [ref["path"] for ref in campaign["inputs"]]
    sources.extend(ref["path"] for dep in verify_dependencies(campaign, header, node, info) for ref in dep["output_files"])
    require(json_ref(info["work_request"]) == work_request(campaign, header, node, info), "work request changed")
    sources.append(info["work_request"]["path"])
    if campaign_version(campaign) == 3:
        from orchestration_pack_inputs import input_paths
        sources.extend(input_paths(campaign, header, node).values())
        if campaign.get("client_isolation") is not None:
            sources.extend([campaign["runtime"]["path"], campaign["client_isolation"]["path"]])
    return sources


def pack_extension(campaign, header, node, info):
    extension = dict(version=campaign_version(campaign), campaign_sha256=header["campaign"]["sha256"], **task_identity(campaign, header, node),
                output_root=info["output_root"], deliverables=node["deliverables"], inputs=campaign["inputs"],
                dependencies=verify_dependencies(campaign, header, node, info), work_request=info["work_request"])
    if campaign_version(campaign) == 3:
        extension["executor_id"] = node["executor"]
    return extension


def task_notice(pack_ref, pack):
    # Pure verification of the pinned release's rendering. Live submission always
    # calls that release's cmux_bridge.task_pack_notice / submit_task_pack.
    return ("TASK_PACK_V2 TASK=" + json.dumps(pack["task_id"], ensure_ascii=False) +
            " PACK=" + json.dumps(pack_ref["path"], ensure_ascii=False) + " SHA256=" + pack_ref["sha256"] +
            " READ_AND_OBEY_REQUIRED_SKILL_FIRST; verify pack SHA; follow only its scope; "
            "write report then call cmux_bridge.submit_completion_callback(pack).")


def node_directory(campaign, header, node):
    return Path(campaign["root"]) / "runs" / header["run"]["run_id"] / "nodes" / node["id"]


def task_scope(campaign, header, node, info):
    instruction = "\nRead the exact pinned work request: " + info["work_request"]["path"] + "."
    if campaign_version(campaign) == 3:
        instruction += ("\nVerify preparation_inputs pins before work. Its role_map and pane_inventory bind this task; "
                        "client_adoption points directly to the original external startup proof and runtime. "
                        "Use those pinned files for startup evidence; do not rediscover configuration or rerun startup. "
                        "Historical adoption does not replace current identity or callback delivery checks.")
    if campaign.get("workload"):
        import shlex
        argv = ["rtk", "proxy", campaign["python"]["path"], "-B", campaign["workload"]["path"],
                "--request", info["work_request"]["path"], "--request-sha256", info["work_request"]["sha256"],
                "--output-root", info["output_root"]]
        instruction += "\nExecute this bounded local business command once: " + shlex.join(argv)
    return (node["scope"] + instruction + "\nWrite only these deliverables under " + info["output_root"] + ": " +
            json.dumps(node["deliverables"], ensure_ascii=False) +
            ". Verify the orchestration input hashes before work. Preserve the assigned CLI session and model. "
            "Freeze all outputs before the report-bound callback; no output writes after the callback. "
            "No replacement agents, API calls, source/shared-runtime writes, reasks or continuing loops. "
            "Use rtk for shell commands. Then remain available for a fresh finalized task pack.")


def dispatch_journal(campaign, info):
    caller = controller_uuid(campaign, campaign["sessions"]["codex"]["surface_uuid"])
    key = digest(json.dumps([caller, info["task_id"]]).encode())
    return state_root(campaign) / "task-dispatch-v1" / key


def output_refs(campaign, header, node, info):
    run_root = Path(campaign["root"]) / "runs" / header["run"]["run_id"]
    root = node_output(run_root, node)
    require(info["output_root"] == str(root), "node output escapes its frozen scope")
    manifest = tree_manifest(root, kind="output")
    require(set(manifest["files"]) == set(node["deliverables"]), "missing or additional node deliverables")
    allowed_dirs = {str(parent) for name in node["deliverables"] for parent in Path(name).parents if str(parent) != "."}
    require(set(manifest["directories"]) == allowed_dirs, "additional output directories")
    refs = [file_ref(root / name) for name in sorted(node["deliverables"])]
    return manifest, refs, next(ref for ref in refs if ref["path"] == str(root / node["report"]))


def checker_request(campaign, header, node, info, outputs):
    require(json_ref(info["work_request"]) == work_request(campaign, header, node, info), "work request pin differs")
    request = dict(schema="orchestration-check-request-v2", **task_identity(campaign, header, node),
                output_root=info["output_root"], output_files=outputs, inputs=campaign["inputs"],
                work_request=info["work_request"], dependencies=verify_dependencies(campaign, header, node, info))
    if "input_tree" in campaign:
        request["input_tree"] = campaign["input_tree"]
    return request


def run_checker(campaign, request):
    # There is intentionally no `command` field in node evidence.
    read_ref(campaign["python"], "frozen Python")
    read_ref(campaign["checker"], "trusted checker")
    within(campaign["checker"]["path"], campaign["adapter"]["path"])
    verify_tree(campaign["adapter"])
    # input= opens an anonymous pipe for writing in the parent. Darwin's
    # descriptor path audit cannot authenticate that pipe as a private file.
    # Use one exclusive private scratch directory and a read-only stdin instead;
    # retain the original scope guard and never retry a failed checker. Scratch
    # is outside the business output tree and cleaned on success or failure.
    with tempfile.TemporaryDirectory(prefix=".checker-request-", dir=checked_path(campaign["root"])) as scratch:
        request_path = Path(scratch) / "request.json"
        with request_path.open("xb") as stream:
            stream.write(canonical(request))
        with request_path.open("rb") as stream:
            require(stream.read() == canonical(request), "checker scratch request changed")
            stream.seek(0)
            request_path.unlink()  # Child receives only the already-open read-only descriptor.
            result = subprocess.run([campaign["python"]["path"], "-I", "-B", campaign["checker"]["path"]],
                                    stdin=stream, capture_output=True,
                                    cwd=campaign["adapter"]["path"], timeout=campaign["checker_timeout_seconds"],
                                    env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}, check=False)
    require(len(result.stdout) <= 1024 * 1024 and len(result.stderr) <= 1024 * 1024,
            "checker output exceeds bounded evidence size")
    require(result.returncode == 0, "trusted checker failed: " + result.stderr.decode(errors="replace")[:500])
    value = strict_json(result.stdout, "trusted checker output")
    require(isinstance(value, dict) and isinstance(value.get("checks"), list) and value["checks"],
            "checker must return nonempty structured checks")
    require(all(isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"] and c.get("passed") is True
                for c in value["checks"]), "one or more independent correctness checks failed")
    require(len({c["id"] for c in value["checks"]}) == len(value["checks"]), "duplicate correctness checks")
    return value


def make_test_result(campaign, header, node, info, outputs):
    request = checker_request(campaign, header, node, info, outputs)
    started = utc_now()
    result = run_checker(campaign, request)
    # Rehash after execution as well: the checker or a concurrent writer cannot
    # validate one artifact and deliver another under the same result.
    for ref in outputs + campaign["inputs"] + [r for d in request["dependencies"] for r in d["output_files"]]:
        read_ref(ref)
    return dict(schema="orchestration-check-result-v2", checker=campaign["checker"],
                request_sha256=digest(canonical(request)), started_at=started, finished_at=utc_now(), result=result)


def verify_checks(campaign, header, node, info, evidence, *, rerun):
    tests = json_ref(evidence["tests"], "independent test result")
    require(tests.get("schema") == "orchestration-check-result-v2" and tests.get("checker") == campaign["checker"],
            "test result uses an unfrozen checker")
    request = checker_request(campaign, header, node, info, evidence["output_files"])
    require(tests.get("request_sha256") == digest(canonical(request)), "test result belongs to different output/input/task")
    started, finished = parse_time(tests["started_at"]), parse_time(tests["finished_at"])
    require(parse_time(info["started_at"]) <= started <= finished, "test result chronology invalid")
    require(rerun, "final acceptance requires rerunning the trusted frozen checker")
    result = run_checker(campaign, request)
    require(tests.get("result") == result, "stored test result differs from independent rerun")
    for ref in evidence["output_files"] + campaign["inputs"] + [r for d in request["dependencies"] for r in d["output_files"]]:
        read_ref(ref)
    return dict(checks=len(result["checks"]), started_at=started, finished_at=finished)


def _native_message(reader, ref, role, expected, lower, upper):
    require(lower <= ref["offset"] and ref["offset"] + ref["length"] <= upper, "native message outside task fence")
    record = reader.record(ref)
    require(native_text(record, reader.provider, role) == expected, "native ACK/callback text differs from exact contract")
    return parse_time(record.get("timestamp"))


def prepared_supervisor(campaign, node, gate):
    """Resolve the original C34 callback ref through its pinned v3 identity gate.

    v3 adoption stores UUIDs, not mutable UI refs. Do not invent a ref or change
    the harness callback wire to a UUID; authenticate the original gate mapping.
    """
    sender = campaign["sessions"]["codex"]
    if campaign_version(campaign) != 3:
        return sender["surface_ref"]
    receiver = campaign["sessions"][node["executor"]]
    supervisor = gate.get("supervisor")
    require(isinstance(supervisor, str) and re.fullmatch(r"surface:[0-9]+", supervisor),
            "identity gate supervisor ref is invalid")
    require(gate.get("supervisor_provider") == "codex" and
            campaign_uuid(campaign, gate.get("supervisor_surface_uuid")) == sender["surface_uuid"] and
            campaign_uuid(campaign, gate.get("workspace_uuid")) == sender["workspace_uuid"] == receiver["workspace_uuid"],
            "identity gate supervisor/workspace changed")
    participants = gate.get("native_participants")
    require(isinstance(participants, list) and len(participants) == 2, "identity gate participants changed")
    expected = {(role, s["provider"], s["surface_uuid"], s["session_id"])
                for role, s in (("supervisor", sender), ("executor", receiver))}
    observed = {(p.get("role"), p.get("provider"), campaign_uuid(campaign, p.get("surface_uuid")),
                 p.get("native_session_id")) for p in participants}
    require(observed == expected, "identity gate native roles/sessions changed")
    proofs = gate.get("workspace_proofs")
    require(isinstance(proofs, dict) and len(proofs) == 1, "identity gate workspace proof is not unique")
    selector, mapping = next(iter(proofs.items()))
    require(campaign_uuid(campaign, selector) == receiver["surface_uuid"] and isinstance(mapping, dict),
            "identity gate workspace proof receiver changed")
    expected_mapping = dict(caller_surface_uuid=sender["surface_uuid"], caller_pane_uuid=sender["pane_uuid"],
                            target_surface_uuid=receiver["surface_uuid"], target_pane_uuid=receiver["pane_uuid"],
                            workspace_uuid=sender["workspace_uuid"])
    require(all(campaign_uuid(campaign, mapping.get(k)) == v for k, v in expected_mapping.items()) and
            mapping.get("caller_surface_ref") == supervisor and
            isinstance(mapping.get("target_surface_ref"), str) and
            re.fullmatch(r"surface:[0-9]+", mapping["target_surface_ref"]),
            "identity gate original ref/UUID/pane binding changed")
    return supervisor


@contract_errors("prepared task")
def verify_prepared(campaign, header, node, info, proof, *, fixtures=False):
    require(set(proof) == {"pack", "notice", "handshake", "identity_gate", "validation"},
            "complete prepared-task evidence required")
    directory = node_directory(campaign, header, node)
    receiver, sender = campaign["sessions"][node["executor"]], campaign["sessions"]["codex"]
    reader = NativeReader(receiver, fixtures=fixtures)
    receiver_end = reader.snapshot()["offset"]
    pack_ref = proof["pack"]
    require(pack_ref["path"] == str(directory / "preflight" / "task-pack.json") and
            proof["notice"]["path"] == str(directory / "task-notice.txt"), "prepared task paths changed")
    pack = json_ref(pack_ref, "finalized task pack")
    gate = json_ref(proof["identity_gate"], "identity gate")
    supervisor = prepared_supervisor(campaign, node, gate)
    if campaign_version(campaign) == 3:
        from orchestration_pack_inputs import verify_inputs
        verify_inputs(campaign, header, node, info, pack, gate, fixtures=fixtures)
    require(pack.get("draft") is False and pack.get("task_id") == info["task_id"] and
            campaign_uuid(campaign, pack.get("executor_uuid")) == receiver["surface_uuid"] and pack.get("supervisor") == supervisor and
            pack.get("executor") == receiver["surface_uuid"] and
            pack.get("executors") == [dict(display="EXECUTOR_1", ordinal=1, provider="claude",
                                           surface_ref=receiver["surface_uuid"])] and
            pack.get("callback_target") == supervisor,
            "pack task/executor/supervisor binding changed")
    require(pack.get("orchestration") == pack_extension(campaign, header, node, info), "pack differs from frozen task/input/scope")
    require(pack.get("authorization_source") == "user_message" and pack.get("authorization_record") == campaign["authorization"]["path"],
            "pack authority differs from campaign")
    require(pack.get("required_skill") == str(Path(campaign["controller"]["path"]) / "SKILL.md"), "task controller changed")
    require(pack.get("report") == str(Path(info["output_root"]) / node["report"]), "pack/report path mismatch")
    expected_fields = dict(context=node["context"], source=task_sources(campaign, header, node, info),
                           scope=task_scope(campaign, header, node, info), forbidden=node["forbidden"],
                           verify=node["verify"], executor_model="preserve-current-model", model_command=None)
    require(all(pack.get(k) == v for k, v in expected_fields.items()), "pack instructions differ from frozen node")
    require(pack.get("completion_delivery") == dict(transport="cmux_bridge.submit_completion_callback", require_confirmed=True),
            "dedicated callback contract changed")
    require(pack.get("completion_receipt") == str(directory / "preflight" / "completion-callback-receipt.json"),
            "callback path changed")
    nonce = pack.get("completion_nonce")
    require(isinstance(nonce, str) and len(nonce) >= 8 and nonce != info["acceptance_nonce"], "independent completion nonce required")
    callback = f"DONE|{info['task_id']}|{nonce}|REPORT={pack['report']}"
    require(pack.get("completion_callback") == callback, "callback completion nonce/task/report changed")
    expected_notice = task_notice(pack_ref, pack)
    require(read_ref(proof["notice"]).decode() == expected_notice, "task notice differs from frozen pack")

    handshake = json_ref(proof["handshake"]["receipt"], "handshake receipt")
    require(proof["handshake"]["receipt"]["path"] == str(directory / "preflight" / "handshake-receipt.json"),
            "handshake path changed")
    require(handshake.get("task_id") == info["task_id"] and handshake.get("status") == "PASS" and
            handshake.get("executor_ack") is True, "strict current-task handshake did not pass")
    ack = f"PREFLIGHT_ACK|{info['task_id']}|claude:identity|READY|INLINE|{handshake.get('ack_nonce')}"
    require(handshake.get("ack_line") == ack and pack.get("callback") == ack, "preflight ACK nonce changed")
    handshake_notice = read_ref(proof["handshake"]["notice"]).decode()
    handshake_delivery = verify_delivery(proof["handshake"]["delivery"], receiver, sender, handshake_notice, fixtures=fixtures,
                                         uuid_semantics=campaign_version(campaign) == 3)
    body = handshake.get("dispatch_body_reference")
    if body:
        require(proof["handshake"].get("body") == {k: body[k] for k in ("path", "sha256", "bytes")}, "handshake body pin changed")
        read_ref(proof["handshake"]["body"])
        require(body["sha256"] in handshake_notice and body["path"] in handshake_notice, "notice omits pinned handshake body")
    ack_at = _native_message(reader, proof["handshake"]["ack_native"], "assistant", ack,
                             info["native_start"]["offset"], receiver_end)
    validation = json_ref(proof["validation"], "harness validation")
    require(proof["identity_gate"]["path"] == str(directory / "preflight" / "identity-gate.json") and
            proof["validation"]["path"] == str(directory / "preflight" / "validation.json"), "preflight paths changed")
    expected_participants = {(s["provider"], s["surface_uuid"], s["session_id"]) for s in (sender, receiver)}
    observed = {(p.get("provider"), campaign_uuid(campaign, p.get("surface_uuid")), p.get("native_session_id"))
                for p in gate.get("native_participants", [])}
    require(gate.get("task_id") == info["task_id"] and gate.get("status") == "PASS" and observed == expected_participants,
            "identity gate does not bind both original native sessions")
    require(validation.get("status") == "PASS" and validation.get("task_id") == info["task_id"], "harness validation/task mismatch")
    require(pack.get("identity_gate") == proof["identity_gate"]["path"] and
            pack.get("validation_evidence") == proof["validation"]["path"], "pack uses different preflight files")
    require(parse_time(info["started_at"]) <= parse_time(handshake_delivery["received_at"]) <= ack_at <=
            parse_time(pack["finalized_at"]), "handshake/ACK/finalize chronology invalid")
    require(handshake_delivery["native_proof"]["offset"] >= info["native_start"]["offset"], "handshake precedes node fence")
    return dict(pack=pack, notice=expected_notice, handshake_delivery=handshake_delivery,
                handshake_notice=handshake_notice, ack_at=ack_at)


@contract_errors("dispatch intent")
def verify_dispatch_intent(campaign, header, node, info, ref, *, fixtures=False):
    prepared = verify_prepared(campaign, header, node, info, info["prepared"], fixtures=fixtures)
    return _dispatch_intent(campaign, header, node, info, ref, prepared, fixtures=fixtures)


def _dispatch_intent(campaign, header, node, info, ref, prepared, *, fixtures):
    """Internal step after preparation verification, avoiding a second scan."""
    value = json_ref(ref, "dispatch intent")
    require(ref["path"] == str(node_directory(campaign, header, node) / "dispatch-intent.json"), "dispatch intent path changed")
    expected = dict(version=2, operation="submit_task_pack", **task_identity(campaign, header, node),
                    prepared=info["prepared"], controller_sha256=object_sha(campaign["controller"]))
    require(set(value) == set(expected) | {"created_at", "native_fence"} and
            all(value.get(k) == v for k, v in expected.items()), "dispatch intent binding changed")
    require(parse_time(value["created_at"]) >= parse_time(prepared["pack"]["finalized_at"]), "dispatch intent predates finalization")
    reader = NativeReader(campaign["sessions"][node["executor"]], fixtures=fixtures)
    require(reader.verify_pin(value["native_fence"]) >= info["native_start"]["offset"], "dispatch fence predates claim")
    return value


@contract_errors("task delivery")
def verify_dispatched(campaign, header, node, info, bundle, *, fixtures=False):
    prepared = verify_prepared(campaign, header, node, info, info["prepared"], fixtures=fixtures)
    intent = _dispatch_intent(campaign, header, node, info, info["dispatch_intent"], prepared, fixtures=fixtures)
    delivered = verify_delivery(bundle, campaign["sessions"][node["executor"]], campaign["sessions"]["codex"],
                                prepared["notice"], fixtures=fixtures, uuid_semantics=campaign_version(campaign) == 3)
    binding = delivered["attempt"]["binding"]
    require(binding.get("task_id") == info["task_id"] and binding.get("marker") == info["task_id"] and
            binding.get("task_pack_sha256") == info["prepared"]["pack"]["sha256"], "dispatch task/pack hash mismatch")
    if not fixtures:
        require(Path(bundle["receipt"]["path"]) == dispatch_journal(campaign, info) / "receipt.json",
                "copied dispatch journal is not original evidence")
    require(delivered["attempt"]["native_binding"]["bound_at_epoch"] >= parse_time(intent["created_at"]).timestamp(),
            "dispatch operation preceded its durable intent")
    require(delivered["native_proof"]["offset"] >= intent["native_fence"]["offset"], "delivery predates dispatch intent fence")
    return dict(delivered, preparation=prepared, dispatch_intent=intent)


def verify_protocol(campaign, header, node, info, evidence, receiver_end, supervisor_end, *, fixtures=False):
    proof = evidence["protocol"]
    require(set(proof) == {"prepared", "delivery", "callback"} and proof["prepared"] == info["prepared"] and
            proof["delivery"] == info["delivery"], "candidate protocol differs from original lifecycle")
    delivered = verify_dispatched(campaign, header, node, info, proof["delivery"], fixtures=fixtures)
    preparation = delivered["preparation"]
    pack, pack_ref = preparation["pack"], proof["prepared"]["pack"]
    receiver, sender = campaign["sessions"][node["executor"]], campaign["sessions"]["codex"]
    nonce, callback = pack["completion_nonce"], pack["completion_callback"]
    require(pack["report"] == evidence["report"]["path"], "callback report path changed")
    returned = verify_delivery(proof["callback"], sender, receiver, callback, fixtures=fixtures,
                               uuid_semantics=campaign_version(campaign) == 3)
    cb = returned["attempt"]["binding"]
    expected_callback = dict(task_id=info["task_id"], completion_nonce=nonce, completion_callback=callback,
                             callback_target=pack["callback_target"], task_pack_sha256=pack_ref["sha256"],
                             report=evidence["report"]["path"], report_sha256=evidence["report"]["sha256"],
                             report_bytes=evidence["report"]["bytes"])
    require(all(cb.get(k) == v for k, v in expected_callback.items()), "callback pins a different report/pack/nonce")
    require(proof["callback"]["receipt"]["path"] == pack["completion_receipt"], "callback receipt path changed")
    require(Path(proof["callback"]["attempts"][0]["path"]).parent ==
            Path(pack["completion_receipt"]).with_name(Path(pack["completion_receipt"]).stem + "-attempts"),
            "callback original attempt directory changed")
    delivery_at, callback_at = parse_time(delivered["received_at"]), parse_time(returned["received_at"])
    require(parse_time(pack["finalized_at"]) <= delivery_at <= callback_at,
            "receive/ACK/finalize/delivery/callback chronology invalid")
    for observation, upper in ((delivered, receiver_end), (returned, supervisor_end)):
        ref = observation["native_proof"]
        require(ref["offset"] + ref["length"] <= upper, "native receipt after task closeout fence")
    return dict(delivery_at=delivery_at, callback_at=callback_at,
                transport_retries=sum(x["transport_retries"] for x in (preparation["handshake_delivery"], delivered, returned)),
                notices=[preparation["handshake_notice"], preparation["notice"]], callback=callback,
                delivery_record=delivered["native_proof"], callback_record=returned["native_proof"],
                preparation=preparation, delivery=delivered, callback_delivery=returned)


def original_callback_evidence(campaign, header, node, info):
    """Inspect the original receipt only; never settle, send or infer reception.

    The caller must run verify_protocol before using this evidence. No receipt
    means no callback observation, even if an unconfirmed native line exists.
    """
    require("prepared" in info and "delivery" in info, "original task delivery required")
    pack = json_ref(info["prepared"]["pack"])
    directory = node_directory(campaign, header, node)
    receipt = checked_path(directory / "preflight/completion-callback-receipt.json")
    require(pack["completion_receipt"] == str(receipt) and
            pack["report"] == str(Path(info["output_root"]) / node["report"]), "original callback paths changed")
    if not receipt.exists():
        return None
    attempts = receipt.with_name(receipt.stem + "-attempts")
    bundle = dict(receipt=file_ref(receipt), attempts=[file_ref(checked_path(p))
                  for p in sorted(attempts.glob("attempt-*.json"))])
    return dict(protocol=dict(prepared=info["prepared"], delivery=info["delivery"], callback=bundle),
                report=file_ref(checked_path(pack["report"])))


@contract_errors("candidate")
def validate_candidate(campaign, header, node, info, ref, *, fixtures=False):
    require(ref["path"] == str(node_directory(campaign, header, node) / "candidate.json"), "candidate path changed")
    evidence = json_ref(ref, "frozen candidate")
    expected = {"version", "evidence_class", "task_id", "attempt_id", "run_id", "node_id", "execution_kind",
                "output_tree", "output_files", "report", "tests", "candidate_native_end"}
    delegated = campaign["sessions"][node["executor"]]["provider"] == "claude"
    require(set(evidence) == expected | ({"protocol"} if delegated else {"review_kind"}), "candidate fields incomplete or changed")
    require(type(evidence["version"]) is int and evidence["version"] == campaign_version(campaign) and
            evidence["evidence_class"] == evidence_class(campaign, "cli_node", fixtures), "candidate execution class changed")
    require(all(evidence.get(k) == v for k, v in task_identity(campaign, header, node).items()) and
            evidence["execution_kind"] == info["mode"], "candidate task ownership changed")
    manifest, outputs, report = output_refs(campaign, header, node, info)
    require(evidence["output_tree"] == manifest and evidence["output_files"] == outputs and evidence["report"] == report and report["bytes"] > 0,
            "candidate outputs/report changed")
    require(evidence["tests"]["path"] == str(node_directory(campaign, header, node) / "tests.json"), "tests path changed")
    checks = verify_checks(campaign, header, node, info, evidence, rerun=True)
    require(set(evidence["candidate_native_end"]) == set(campaign["sessions"]), "candidate native lanes missing")
    readers = {k: NativeReader(s, fixtures=fixtures) for k, s in campaign["sessions"].items()}
    ends = {k: r.verify_pin(evidence["candidate_native_end"][k]) for k, r in readers.items()}
    lower = info["native_start"]["offset"]
    if delegated:
        protocol = verify_protocol(campaign, header, node, info, evidence, ends[node["executor"]], ends["codex"], fixtures=fixtures)
        require(protocol["callback_at"] <= checks["started_at"], "tests precede callback")
        lower = protocol["delivery_record"]["offset"]
    else:
        require(evidence["review_kind"] == "solo_self_review", "local work must use solo_self_review")
    readers[node["executor"]].action_summary(lower, ends[node["executor"]], task_id=info["task_id"], output_root=info["output_root"])
    return evidence


def failure_marker(info, evidence):
    frozen = {k: v for k, v in evidence.items() if k not in ("failure_native", "native_end")}
    return f"ORCH_FAIL_V2|{info['task_id']}|{info['acceptance_nonce']}|{object_sha(frozen)}"


@contract_errors("failure settlement")
def verify_failure(campaign, header, node, info, ref, *, fixtures=False):
    value = json_ref(ref, "failure settlement")
    require(ref["path"] == str(node_directory(campaign, header, node) / "settled-failure.json"), "failure path changed")
    return verify_failure_value(campaign, header, node, info, value, fixtures=fixtures, terminal_failure=ref)


@contract_errors("failure candidate")
def validate_failure_candidate(campaign, header, node, info, value, *, fixtures=False, terminal_failure=None):
    require(type(value.get("version")) is int and value["version"] == campaign_version(campaign) and
            value.get("evidence_class") == evidence_class(campaign, "cli_failure", fixtures),
            "failure execution class changed")
    base = {"version", "evidence_class", "task_id", "attempt_id", "run_id", "node_id", "blocker", "boundary", "output_tree"}
    additions = {"no_business_input": {"no_input_attempts"}, "callback_boundary": {"report", "protocol"},
                 "local_boundary": set(), "preparation_exited": {"preparation_exit", "maintenance"}}
    require(value.get("boundary") in additions and set(value) == base | additions[value["boundary"]],
            "failure boundary fields missing or changed")
    require(all(value.get(k) == v for k, v in task_identity(campaign, header, node).items()) and
            value.get("blocker") == info["blocker"], "failure task/blocker changed")
    json_ref(info["blocker"])
    require(value["output_tree"]["path"] == info["output_root"] and value["output_tree"].get("kind") == "output",
            "failure output scope changed")
    verify_tree(value["output_tree"])
    if value["boundary"] == "preparation_exited":
        from orchestration_preparation import verify_preparation_exit
        proof = json_ref(value["preparation_exit"], "original preparation exit")
        require(value["preparation_exit"]["path"] == str(node_directory(campaign, header, node) / "preparation-exit.json"),
                "preparation exit path changed")
        verify_preparation_exit(campaign, header, node, info, proof, fixtures=fixtures, terminal_failure=terminal_failure)
        if value["maintenance"] is not None:
            from orchestration_maintenance import validate_maintenance
            validate_maintenance(value["maintenance"], campaign, header, node, info, value["preparation_exit"], fixtures=fixtures)
        elif not fixtures:
            require("scripts/orchestration_preparation.py" in campaign["adapter"]["files"],
                    "old adapter needs a separately frozen maintenance entrypoint")
    return value


@contract_errors("failure settlement value")
def verify_failure_value(campaign, header, node, info, value, *, fixtures=False, terminal_failure=None, native_end=None):
    frozen = json_ref(file_ref(node_directory(campaign, header, node) / "failure-candidate.json"), "frozen failure candidate")
    require({k: v for k, v in value.items() if k not in ("failure_native", "native_end")} == frozen,
            "failure differs from the previously frozen candidate")
    require(set(value) == set(frozen) | {"failure_native", "native_end"}, "failure settlement fields changed")
    validate_failure_candidate(campaign, header, node, info, frozen, fixtures=fixtures, terminal_failure=terminal_failure)
    readers = {k: NativeReader(s, fixtures=fixtures) for k, s in campaign["sessions"].items()}
    require(set(value["native_end"]) == set(readers), "failure native lanes missing")
    fences = value["native_end"] if native_end is None else native_end
    require(set(fences) == set(readers), "release native lanes missing")
    ends = {k: r.verify_pin(fences[k]) for k, r in readers.items()}
    require(all(ends[k] >= r.verify_pin(value["native_end"][k]) for k, r in readers.items()),
            "release fences precede original settlement")
    lane = node["executor"]
    start = readers[lane].verify_pin(info["native_start"])
    require(all(ends[k] >= readers[k].verify_pin(header["native_start"][k]) for k in readers) and ends[lane] >= start,
            "failure fences precede run/claim")
    script = control_script(campaign, header["campaign"], "settle", header["run"]["run_id"],
                            node_id=node["id"], marker=failure_marker(info, value))
    if value.get("maintenance") is not None:
        from orchestration_maintenance import maintenance_control
        script = maintenance_control(value["maintenance"], failure_marker(info, value))
    at = native_control(readers["codex"], value["failure_native"], script)
    require(at >= parse_time(info["started_at"]), "failure settlement precedes claim")
    require(value["failure_native"]["offset"] >= header["native_start"]["codex"]["offset"] and
            value["failure_native"]["offset"] + value["failure_native"]["length"] <= ends["codex"], "failure outside supervisor fence")
    reason = value.get("boundary")
    require(value["output_tree"]["path"] == info["output_root"] and value["output_tree"].get("kind") == "output", "failure output scope changed")
    verify_tree(value["output_tree"])
    if reason == "preparation_exited":
        from orchestration_preparation import verify_preparation_exit
        proof = json_ref(value["preparation_exit"])
        require(at >= parse_time(proof["observed_at"]), "settlement precedes preparation exit observation")
        # Count all exact controls, including any appended after an interrupted
        # settlement. A repeated native request never creates a recovery budget.
        require(find_control(readers["codex"], script, after=proof["native_end"]["codex"]["offset"]) == value["failure_native"],
                "preparation settlement must use its unique original control")
        verify_preparation_exit(campaign, header, node, info, proof, fixtures=fixtures,
                                native_end=fences, terminal_failure=terminal_failure)
    elif reason == "no_business_input":
        require(info["mode"] == "delegated_cli" and "delivery" not in info, "delivered work is not zero input")
        # A missing dispatch intent does not prove a possibly still-running
        # preparation has ended. Such blockers remain observation-only.
        require("dispatch_intent" in info, "preparation completion is unproven; preserve the lease")
        intent = verify_dispatch_intent(campaign, header, node, info, info["dispatch_intent"], fixtures=fixtures)
        refs = value["no_input_attempts"]
        require(isinstance(refs, list) and refs, "no original zero-input attempts; preserve the lease")
        if not fixtures:
            require(Path(refs[0]["path"]).parent == dispatch_journal(campaign, info), "zero-input journal is not original")
        notice = read_ref(info["prepared"]["notice"]).decode()
        expected = dict(task_id=info["task_id"], marker=info["task_id"], payload_sha256=digest(notice.encode()),
                        task_pack_sha256=info["prepared"]["pack"]["sha256"],
                        identity=delivery_identity(campaign["sessions"][lane], campaign["sessions"]["codex"]))
        original = verify_no_input(refs, campaign["sessions"][lane], campaign["sessions"]["codex"], notice, expected,
                                   not_before=parse_time(intent["created_at"]).timestamp(), not_after=at.timestamp(), fixtures=fixtures,
                                   uuid_semantics=campaign_version(campaign) == 3)
        require(intent["native_fence"]["offset"] <= original["native_offset"] <= ends[lane],
                "zero-input attempt is outside settlement fence")
        # Handshake is not business dispatch. No useful task call may exist at
        # this boundary, even when the transport claims it did not send input.
        observation = readers[lane].action_summary(info["native_start"]["offset"], ends[lane],
                        task_id=info["task_id"], output_root=info["output_root"], allow_empty=True)
        require(observation["attempted_task_calls"] == 0, "business action contradicts zero-input claim")
        require(not value["output_tree"]["files"] and not value["output_tree"]["directories"],
                "zero-input settlement requires genuinely empty output")
    elif reason == "callback_boundary":
        require(info["mode"] == "delegated_cli" and "delivery" in info, "callback boundary requires original delivery")
        read_ref(value["report"])
        protocol = verify_protocol(campaign, header, node, info, value, ends[lane], ends["codex"], fixtures=fixtures)
        require(protocol["callback_at"] <= at, "failure settlement precedes original callback")
        observation = readers[lane].action_summary(protocol["delivery_record"]["offset"], ends[lane],
                        task_id=info["task_id"], output_root=info["output_root"], allow_empty=True)
        require(observation["last_action_at"] is None or parse_time(observation["last_action_at"]) <= protocol["callback_at"],
                "output work continued after callback")
    else:
        require(reason == "local_boundary" and info["mode"] == "local_cli", "no proven terminal write boundary")
        readers["codex"].action_summary(info["native_start"]["offset"], value["failure_native"]["offset"],
                        task_id=info["task_id"], output_root=info["output_root"], allow_empty=True)
    return dict(status="FAILED_PRESERVED", evidence_class=evidence_class(campaign, "verified_cli_failure", fixtures),
                **task_identity(campaign, header, node), settled_at=at.isoformat(), boundary=reason)


@contract_errors("node evidence")
def verify_node(campaign, header, node, info, evidence, *, fixtures=False, rerun=True):
    require(type(evidence.get("version")) is int and evidence["version"] == campaign_version(campaign) and
            evidence.get("evidence_class") == evidence_class(campaign, "cli_node", fixtures),
            "node evidence execution class mismatch")
    candidate = validate_candidate(campaign, header, node, info, info["candidate"], fixtures=fixtures)
    require(set(evidence) == set(candidate) | {"acceptance_native", "native_end"}, "accepted evidence fields changed")
    require({k: v for k, v in evidence.items() if k not in ("acceptance_native", "native_end")} == candidate,
            "accepted evidence differs from the previously frozen candidate")
    identity = task_identity(campaign, header, node)
    require(all(evidence.get(k) == v for k, v in identity.items()) and
            all(info.get(k) == identity[k] for k in ("task_id", "attempt_id")), "node/run/task/attempt identity changed")
    lane = node["executor"]
    mode = "local_cli" if campaign["sessions"][lane]["provider"] == "codex" else "delegated_cli"
    require(info.get("mode") == mode and evidence.get("execution_kind") == mode, "execution ownership changed")
    manifest, outputs, report = output_refs(campaign, header, node, info)
    require(evidence.get("output_tree") == manifest and evidence.get("output_files") == outputs and evidence.get("report") == report,
            "output/report files changed or omitted")
    require(report["bytes"] > 0, "empty executor report")
    readers = {key: NativeReader(session, fixtures=fixtures) for key, session in campaign["sessions"].items()}
    ends = {key: readers[key].verify_pin(evidence["native_end"][key]) for key in readers}
    require(all(ends[k] >= candidate["candidate_native_end"][k]["offset"] for k in readers), "acceptance predates candidate fences")
    start = readers[lane].verify_pin(info["native_start"])
    require(start >= readers[lane].verify_pin(header["native_start"][lane]), "node begins before original run")
    acceptance = evidence["acceptance_native"]
    script = control_script(campaign, header["campaign"], "accept", identity["run_id"],
                            node_id=node["id"], marker=marker_for(info, evidence))
    accepted_at = native_control(readers["codex"], acceptance, script)
    require(acceptance["offset"] >= header["native_start"]["codex"]["offset"] and
            acceptance["offset"] + acceptance["length"] <= ends["codex"], "acceptance outside original supervisor fence")
    checks = verify_checks(campaign, header, node, info, evidence, rerun=rerun)
    require(checks["finished_at"] <= accepted_at, "acceptance precedes independent tests")
    protocol = None
    if mode == "delegated_cli":
        protocol = verify_protocol(campaign, header, node, info, evidence, ends[lane], ends["codex"], fixtures=fixtures)
        require(protocol["callback_at"] <= checks["finished_at"] <= accepted_at, "acceptance/tests precede completion callback")
        action_start, action_end = protocol["delivery_record"]["offset"], ends[lane]
    else:
        require(evidence.get("review_kind") == "solo_self_review" and "protocol" not in evidence,
                "local work requires honest solo_self_review; no invented self ACK/callback")
        action_start, action_end = start, acceptance["offset"]
    action = readers[lane].action_summary(action_start, action_end, task_id=info["task_id"], output_root=info["output_root"])
    first = parse_time(action["first_action_at"])
    require(parse_time(info["started_at"]) <= first <= accepted_at, "useful action lies outside node lifetime")
    if protocol:
        require(protocol["delivery_at"] <= first <= protocol["callback_at"], "task action precedes delivery or follows callback")
        require(parse_time(action["last_action_at"]) <= protocol["callback_at"], "task writes continued after callback")
    return dict(status="ACCEPTED", evidence_class=evidence_class(campaign, "verified_cli_node", fixtures),
                **identity, checks=checks["checks"], accepted_at=accepted_at.isoformat(),
                first_action_at=action["first_action_at"], completed_task_calls=action["completed_task_calls"],
                failed_api_attempts=action["failed_api_attempts"],
                provider_auto_retries=action["provider_auto_retries"],
                transport_retries=protocol["transport_retries"] if protocol else 0,
                review_kind="independent_executor_review" if protocol else "solo_self_review")
