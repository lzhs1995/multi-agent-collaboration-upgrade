"""Evidence for a returned preparation that never reached business dispatch.

Collection/settlement hold runner, transport, then coordinator locks. Verification is pure so
the same evidence can also be replayed while validating terminal event history.
An absent dispatch is insufficient without all five original returned stages.
"""
from __future__ import annotations

import re
from pathlib import Path

from orchestration_common import (canonical, contract_errors, digest, evidence_names, exclusive_lock, file_ref, json_ref, object_sha,
                                  parse_time, read_ref, tree_manifest, utc_now, write_json)
from orchestration_evidence import (dispatch_journal, node_directory, pack_extension, task_scope,
                                    task_sources, verify_prepared)
from orchestration_native import NativeReader, require

STAGES = ("preflight", "task-pack", "fill-pack", "finalize-pack", "task-notice")
HARNESS_STAGES = {"preflight", "task-pack", "finalize-pack"}


def fill_scaffold(campaign, header, node, info, scaffold, handshake, nonce):
    """Reconstruct the historical pre-finalization bytes without rewriting them."""
    require(scaffold.get("draft") is True and scaffold.get("task_id") == info["task_id"],
            "original scaffold required")
    report = str(Path(info["output_root"]) / node["report"])
    pack = dict(scaffold)
    pack.update(context=node["context"], source=task_sources(campaign, header, node, info),
        scope=task_scope(campaign, header, node, info), forbidden=node["forbidden"], verify=node["verify"],
        needs_auth="Existing user authorization covers only these local replay outputs.",
        authorization_source="user_message", authorization_record=campaign["authorization"]["path"],
        report=report, callback=handshake["ack_line"], completion_nonce=nonce,
        completion_callback=f"DONE|{info['task_id']}|{nonce}|REPORT={report}",
        executor_model="preserve-current-model", model_command=None,
        orchestration=pack_extension(campaign, header, node, info))
    return pack


def collect_preparation_exit(transport):
    """Take the original locks before observing the returned preparation."""
    with exclusive_lock(transport.root / "runner.lock"), exclusive_lock(transport.root / "transport.lock"), \
            exclusive_lock(transport.co.root / "coordinator.lock"):
        transport.co._gate()
        state, info = transport.current()
        require(state.value == "BLOCKED", "original preparation must be blocked")
        return collect_preparation_exit_locked(transport, info)


def collect_preparation_exit_locked(transport, info):
    """Internal collector for callers already holding all three original locks."""
    root = transport.root
    # This file is written by the original preparation before its final gate.
    # Missing original proof is not repaired by this maintenance boundary.
    file_ref(root / "handshake-notice.txt")
    proof = dict(version=1,
        operations={name: {part: file_ref(root / "operations" / name / (part + ".json"))
                           for part in ("intent", "result")} for name in STAGES},
        transport=file_ref(root / "transport.json"), scaffold=file_ref(root / "original-scaffold.json"),
        history=dict(run=file_ref(transport.co.root / "run.json"),
                     claim=file_ref(transport.co.root / "claims" / (transport.key + ".json")),
                     events=[file_ref(transport.co.root / "events" / f"{event['seq']:06d}.json")
                             for event in transport.co.events()]),
        prepared=transport._preparation_proof(info, existing_only=True),
        native_end={lane: NativeReader(session, fixtures=transport.co.fixtures).snapshot()
                    for lane, session in transport.campaign["sessions"].items()}, observed_at=utc_now())
    verify_preparation_exit(transport.campaign, transport.co.header, transport.node, info, proof,
                            fixtures=transport.co.fixtures)
    return proof


def freeze_preparation_proof_locked(transport, info):
    path = transport.root / "preparation-exit.json"
    if path.exists():
        ref = file_ref(path)
        verify_preparation_exit(transport.campaign, transport.co.header, transport.node, info, json_ref(ref),
                                fixtures=transport.co.fixtures)
        return ref
    proof = collect_preparation_exit_locked(transport, info)
    return write_json(path, proof, exclusive=True)


def freeze_failure_candidate_locked(co, key, proof_ref, maintenance=None):
    from orchestration_evidence import task_identity, validate_failure_candidate
    states, data = co.state()
    require(states[key].value == "BLOCKED", "preparation failure candidate requires its original blocker")
    info, node = data[key], co.nodes[key]
    value = dict(version=2, evidence_class="offline_fixture" if co.fixtures else "cli_failure_v2",
                 **task_identity(co.campaign, co.header, node), blocker=info["blocker"], boundary="preparation_exited",
                 output_tree=tree_manifest(info["output_root"], kind="output"), preparation_exit=proof_ref, maintenance=maintenance)
    validate_failure_candidate(co.campaign, co.header, node, info, value, fixtures=co.fixtures)
    path = node_directory(co.campaign, co.header, node) / "failure-candidate.json"
    if path.exists():
        ref = file_ref(path)
        require(json_ref(ref) == value, "original preparation failure candidate changed")
        return ref
    return write_json(path, value, exclusive=True)


def verify_history(campaign, header, node, info, proof, terminal_failure):
    """Bind the complete old prefix; at most its own verified failure may follow."""
    run_root = Path(campaign["root"]) / "runs" / header["run"]["run_id"]
    history = proof["history"]
    require(set(history) == {"run", "claim", "events"}, "preparation history fields changed")
    require(history["run"]["path"] == str(run_root / "run.json") and json_ref(history["run"]) == header,
            "preparation run header changed")
    require(history["claim"]["path"] == str(run_root / "claims" / (node["id"] + ".json")), "original claim path changed")
    claim = json_ref(history["claim"])
    require(set(info) in (set(claim) | {"blocker"}, set(claim) | {"blocker", "failure", "verification"}) and
            all(info[k] == v for k, v in claim.items()), "preparation must have only its original claim and blocker")
    refs = history["events"]
    require(isinstance(refs, list) and len(refs) >= 2 and
            [r["path"] for r in refs] == [str(run_root / "events" / f"{i:06d}.json") for i in range(1, len(refs) + 1)],
            "preparation event prefix is not complete and contiguous")
    events = [json_ref(ref) for ref in refs]
    task_events = [e for e in events if e["node_id"] == node["id"]]
    require([e["state"] for e in task_events] == ["PREPARING", "BLOCKED"] and
            task_events[0]["data"] == claim and task_events[1]["data"] == {"blocker": info["blocker"]} and
            events[-1] == task_events[-1], "preparation prefix must end at its original PREPARING-to-BLOCKED boundary")
    actual = sorted((run_root / "events").glob("*.json"))
    require(actual[:len(refs)] == [Path(ref["path"]) for ref in refs] and len(actual) in (len(refs), len(refs) + 1),
            "preparation event history omitted or extended")
    terminal = None
    if len(actual) > len(refs):
        terminal = file_ref(actual[-1])
        event = json_ref(terminal)
        require(terminal_failure is not None and event["state"] == "FAILED_PRESERVED" and
                event["node_id"] == node["id"] and set(event["data"]) == {"failure", "verification"} and
                event["data"]["failure"] == terminal_failure, "preparation suffix is not its verified terminal failure")
        events.append(event)
    previous, last_at = object_sha(header), parse_time(header["created_at"])
    last_clock, last_boot = header["started_monotonic_ns"], header["boot_id"]
    fields = {"seq", "previous", "at", "monotonic_ns", "boot_id", "clock_transition", "node_id", "state", "data"}
    for index, event in enumerate(events, 1):
        require(set(event) == fields and type(event["seq"]) is int and event["seq"] == index and
                event["previous"] == previous, "preparation event chain changed")
        stamp, clock = parse_time(event["at"]), event["monotonic_ns"]
        require(type(clock) is int and clock >= 0 and stamp >= last_at and isinstance(event["boot_id"], str) and
                event["boot_id"], "preparation event clocks changed")
        if event["boot_id"] == last_boot:
            require(clock >= last_clock and event["clock_transition"] is None, "preparation same-boot clock changed")
        else:
            require(event["clock_transition"] == dict(from_boot=last_boot, to_boot=event["boot_id"],
                    previous_monotonic_ns=last_clock), "preparation boot transition changed")
        previous, last_at, last_clock, last_boot = object_sha(event), stamp, clock, event["boot_id"]
    require(parse_time(task_events[-1]["at"]) <= parse_time(proof["observed_at"]), "proof predates original blocker event")
    return terminal


@contract_errors("preparation exit")
def verify_preparation_exit(campaign, header, node, info, proof, *, fixtures=False, native_end=None, terminal_failure=None):
    from orchestration_transport import PinnedRuntime, disarm_sources

    require(info["mode"] == "delegated_cli" and node["executor"] == "claude" and
            not {"prepared", "dispatch_intent", "delivery", "uncertainty", "candidate"} & set(info),
            "preparation exit requires an undispatched original Claude task")
    require(set(proof) == {"version", "operations", "transport", "scaffold", "prepared", "history", "native_end", "observed_at"}
            and type(proof["version"]) is int and proof["version"] == 1, "preparation exit proof fields changed")
    root = node_directory(campaign, header, node)
    terminal = verify_history(campaign, header, node, info, proof, terminal_failure)
    for path in (root / "dispatch-intent.json", dispatch_journal(campaign, info)):
        require(not path.exists() and not path.is_symlink(), "business dispatch intent/journal exists; preserve the lease")
    entries = list((root / "operations").iterdir())
    allowed = set(STAGES) | ({"scoped-disarm"} if terminal is not None else set())
    require(all(p.is_dir() and not p.is_symlink() for p in entries) and
            set(STAGES) <= {p.name for p in entries} <= allowed,
            "unexpected or incomplete original preparation operations")
    if any(p.name == "scoped-disarm" for p in entries):
        # Source validation must not read the lease: lease validation itself
        # replays this preparation proof. Preserve original ERROR/absence while
        # the separate bounded recovery supplies the final release proof.
        disarm_sources(campaign, header["campaign"], root, info["task_id"], terminal)
    require(set(proof["operations"]) == set(STAGES), "all five original stages required")
    for field, name in (("transport", "transport.json"), ("scaffold", "original-scaffold.json")):
        require(proof[field]["path"] == str(root / name), "preparation source path changed")
    config = json_ref(proof["transport"])
    require(set(config) == {"version", "task_id", "campaign", "completion_nonce"} and
            type(config["version"]) is int and config["version"] == 2 and
            config["task_id"] == info["task_id"] and config["campaign"] == header["campaign"] and
            isinstance(config["completion_nonce"], str) and re.fullmatch(r"[0-9a-f]{24}", config["completion_nonce"]),
            "original transport config/nonce changed")
    prepared = proof["prepared"]
    checked = verify_prepared(campaign, header, node, info, prepared, fixtures=fixtures)
    pack = checked["pack"]
    require(pack["completion_nonce"] == config["completion_nonce"], "final pack transport nonce changed")
    scaffold = json_ref(proof["scaffold"])
    receiver = campaign["sessions"]["claude"]
    require(scaffold.get("executor") == scaffold.get("executor_uuid") == receiver["surface_uuid"] and
            scaffold.get("executors") == pack["executors"], "original scaffold executor identity changed")
    handshake = json_ref(prepared["handshake"]["receipt"])
    filled = fill_scaffold(campaign, header, node, info, scaffold, handshake, config["completion_nonce"])
    historical_bytes = canonical(filled)
    historical_ref = dict(path=prepared["pack"]["path"], sha256=digest(historical_bytes), bytes=len(historical_bytes))
    changed = {key for key in set(filled) | set(pack) if filled.get(key) != pack.get(key)}
    paths = [str(item).rstrip("。，、；;,.)）]】") for item in pack["source"]]
    require(changed == {"draft", "finalized_at", "source_entries"} and filled["draft"] is True and pack["draft"] is False and
            pack["source_entries"] == [dict(path=p, exists=True) for p in paths if p.startswith("/")],
            "finalization changed fields beyond the original harness contract")
    previous = parse_time(info["started_at"])
    runtime = PinnedRuntime(campaign)
    for name in STAGES:
        refs = proof["operations"][name]
        require(set(refs) == {"intent", "result"}, "original stage pins missing")
        stage_root = root / "operations" / name
        for part in ("intent", "result"):
            require(refs[part]["path"] == str(stage_root / (part + ".json")), "original stage path changed")
        intent, result = json_ref(refs["intent"]), json_ref(refs["result"])
        arguments = ({"argv": runtime.harness_argv(name, info["task_id"], root / "preflight")}
                     if name in HARNESS_STAGES else {"transport": proof["transport"]}
                     if name == "fill-pack" else {"pack": prepared["pack"]})
        expected = dict(version=2, campaign=header["campaign"], task_id=info["task_id"],
                        controller_sha256=object_sha(campaign["controller"]), operation=name, arguments=arguments)
        require(set(intent) == set(expected) | {"created_at"} and
                {k: v for k, v in intent.items() if k != "created_at"} == expected,
                "original stage identity/controller/arguments changed")
        require(set(result) == {"intent", "status", "value", "completed_at"} and
                result["intent"] == refs["intent"] and result["status"] == "RETURNED", "original stage has not returned")
        started, completed = parse_time(intent["created_at"]), parse_time(result["completed_at"])
        require(previous <= started <= completed, "original preparation chronology changed")
        previous = completed
        value = result["value"]
        expected_files = {"intent.json", "result.json"}
        if name in HARNESS_STAGES:
            require(set(value) == {"returncode", "stdout", "stderr"} and
                    type(value["returncode"]) is int and value["returncode"] == 0,
                    "original harness success and complete stdout/stderr required")
            for stream in ("stdout", "stderr"):
                require(value[stream]["path"] == str(stage_root / (stream + ".txt")), "harness stream path changed")
                read_ref(value[stream], "original harness " + stream)
                expected_files.add(stream + ".txt")
        elif name == "fill-pack":
            # finalize-pack overwrote this path. Check reconstructed historical
            # bytes; never rehash the final pack against the earlier stage pin.
            require(value == historical_ref, "historical fill-pack pin differs from original scaffold/config")
        else:
            require(value == read_ref(prepared["notice"]).decode(), "original task notice differs from final pack")
        require(evidence_names(stage_root, expected_files) == expected_files, "unexpected stage evidence files")
        if name == "finalize-pack":
            require(started <= parse_time(pack["finalized_at"]) <= completed, "finalization outside original operation")
    require(previous <= parse_time(json_ref(info["blocker"])["observed_at"]) <= parse_time(proof["observed_at"]),
            "preparation had not returned before the preserved blocker")
    output = tree_manifest(info["output_root"], kind="output")
    require(not output["files"] and not output["directories"], "preparation exit requires empty business output")
    require(set(proof["native_end"]) == set(campaign["sessions"]), "preparation native lanes missing")
    ends = native_end if native_end is not None else proof["native_end"]
    require(set(ends) == set(campaign["sessions"]), "settlement native lanes missing")
    for lane, session in campaign["sessions"].items():
        reader = NativeReader(session, fixtures=fixtures)
        start = reader.verify_pin(info["native_start"] if lane == "claude" else header["native_start"][lane])
        end = reader.verify_pin(ends[lane])
        require(start <= reader.verify_pin(proof["native_end"][lane]) <= end, "preparation native fences changed")
        actions = reader.action_summary(start, end, task_id=info["task_id"], output_root=info["output_root"], allow_empty=True)
        require(actions["attempted_task_calls"] == 0, "business action contradicts preparation exit")
        reader.require_no_task_input(start, end, task_id=info["task_id"], pack=prepared["pack"], notice=checked["notice"],
                                     receiver=campaign["sessions"]["claude"], sender=campaign["sessions"]["codex"],
                                     refusal_context=dict(controller=campaign["controller"], python=campaign["python"]))
    return dict(completed_at=previous.isoformat(), observed_at=proof["observed_at"])


def check_fresh_release(co, key, info, failure_ref, terminal):
    """Called with all node locks immediately before releasing its exact lease."""
    from orchestration_evidence import verify_failure_value
    failure = json_ref(failure_ref)
    ends = {lane: NativeReader(session, fixtures=co.fixtures).snapshot()
            for lane, session in co.campaign["sessions"].items()}
    verify_failure_value(co.campaign, co.header, co.nodes[key], info, failure, fixtures=co.fixtures,
                         terminal_failure=failure_ref, native_end=ends)
    root = node_directory(co.campaign, co.header, co.nodes[key]) / "release-checks"
    prior = sorted(root.glob("*.json"))
    require([p.name for p in prior] == [f"{i:06d}.json" for i in range(1, len(prior) + 1)],
            "release check history is not contiguous")
    return write_json(root / f"{len(prior) + 1:06d}.json", dict(version=1, failure=failure_ref, terminal=terminal,
                      native_end=ends, observed_at=utc_now()), exclusive=True)


@contract_errors("preparation lease release")
def verify_release_check(co, key, info, terminal, lease):
    """A terminal timestamp alone cannot prove the later resource-release fence."""
    from orchestration_evidence import verify_failure_value
    root = node_directory(co.campaign, co.header, co.nodes[key]) / "release-checks"
    paths = sorted(root.glob("*.json"))
    require(paths and [p.name for p in paths] == [f"{i:06d}.json" for i in range(1, len(paths) + 1)],
            "original preparation release checks missing or noncontiguous")
    expected = {p.name for p in paths}
    require(evidence_names(root, expected | {f"{len(paths) + 1:06d}.json"}) == expected, "unexpected release evidence")
    failure = json_ref(info["failure"])
    previous = parse_time(json_ref(terminal)["at"])
    for path in paths:
        check = json_ref(file_ref(path))
        require(set(check) == {"version", "failure", "terminal", "native_end", "observed_at"} and
                type(check["version"]) is int and check["version"] == 1 and
                check["failure"] == info["failure"] and check["terminal"] == terminal,
                "release check belongs to another terminal/failure")
        observed = parse_time(check["observed_at"])
        require(previous <= observed <= parse_time(lease["released_at"]), "release check chronology changed")
        previous = observed
    verify_failure_value(co.campaign, co.header, co.nodes[key], info, failure, fixtures=co.fixtures,
                         terminal_failure=info["failure"], native_end=check["native_end"])
