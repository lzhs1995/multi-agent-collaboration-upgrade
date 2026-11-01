"""Registered NEW S2 semantic stimuli, using the original helper and native readers.

Input files declare possible sites, not receipts or eligibility. The corpus stays
unchanged. Semantic slots are separate from the 192 ordinary-message samples.
No key, model transport, retry, session creation or receiver input is implemented here.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from orchestration_common import (ContractError, checked_path, digest, file_ref, json_ref,
    object_sha, read_ref, require, strict_json, tree_manifest, utc_now)
from upgrade_communication_plan import _campaign_pin, _require_original_sender, _route

INPUT_SCHEMA = "u2078-s2-semantic-business-input-v1"
SCOPE = "NEW_S2_ONLY_NEVER_REWRITE_OR_RELABEL_ORIGINAL_V3"
BOUNDARY = "after_native_run_start_before_business"
CLASSES = ("uneven_successor", "fan_in", "write_conflict")
BODY_PATH = "semantic/INPUT.json"
SENDER_DUTIES_PATH = "semantic/sender-duties.json"
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\Z")


def load_input(ref):
    value = strict_json(read_ref(ref, "registered S2 semantic input"), "S2 semantic input")
    require(isinstance(value, dict) and set(value) == {
        "schema", "scope", "base_corpus", "origin", "messages_by_class", "delivery_specification"}
        and value["schema"] == INPUT_SCHEMA and value["scope"] == SCOPE,
        "an explicit NEW S2 semantic input is required")
    base, duty = value["base_corpus"], value["delivery_specification"]
    require(isinstance(base, dict) and set(base) == {"path", "files", "tree_object_sha256"}
        and type(base["files"]) is int and base["files"] > 0,
        "the complete original corpus factor is required")
    require(isinstance(duty, dict) and type(duty.get("per_run")) is int and duty["per_run"] == 3
        and duty.get("source_artifact_is_native_proof") is False,
        "three candidate bodies per run are not native evidence")
    require(isinstance(value["messages_by_class"], dict)
        and set(value["messages_by_class"]) == set(CLASSES), "semantic workload classes changed")
    ids = []
    for kind in CLASSES:
        bodies = value["messages_by_class"][kind]
        require(isinstance(bodies, list) and len(bodies) == duty["per_run"], "semantic body count changed")
        for item in bodies:
            require(isinstance(item, dict) and set(item) == {"id", "body"}
                and isinstance(item["id"], str) and ID.fullmatch(item["id"])
                and isinstance(item["body"], str) and item["body"].strip()
                and "\0" not in item["body"] and len(item["body"].encode("utf-8")) <= 10000,
                "complete bounded semantic body and unique ID required")
            require(not item["body"].startswith(("STATUS: COMM_SAMPLE ", "MESSAGE_REFERENCE_V2 ",
                "TASK_PACK_V2 ", "[CMUX-AGENT]", "ACCEPT|", "ACK|", "CALLBACK|")),
                "fixed protocol is not a semantic stimulus")
            ids.append(item["id"])
        require(len({item["body"] for item in bodies}) == len(bodies), "duplicate semantic body in one run")
    require(len(set(ids)) == len(ids), "duplicate semantic stimulus ID")
    tree = tree_manifest(checked_path(base["path"]))
    require(len(tree["files"]) == base["files"] and object_sha(tree) == base["tree_object_sha256"],
        "original corpus source changed")
    return value


def input_factor(ref):
    value = load_input(ref)
    return object_sha(dict(schema="s2-semantic-input-factor-v1",
        corpus_sha256=value["base_corpus"]["tree_object_sha256"],
        semantic_sha256=ref["sha256"], semantic_bytes=ref["bytes"],
        sender="claude_a", receiver="codex", boundary=BOUNDARY, bodies_per_run=3))


def possible_sites(ref, preview_root):
    """Mechanically derived declarations, never runtime opportunity counts."""
    from orchestration_binding import is_unbound
    from orchestration_freeze_v3 import registered_corpus_plan
    from upgrade_jev_benchmark import SCHEMA
    value = load_input(ref)
    counts, sources = {}, {}
    for kind in CLASSES:
        plan = registered_corpus_plan(Path(preview_root) / "inputs", "s2-semantic-site-declaration",
                                      kind, {"schema": SCHEMA})
        nodes = [node["id"] for node in plan["nodes"] if is_unbound(node)]
        bodies = value["messages_by_class"][kind]
        counts[kind] = dict(dp1=len(nodes), dp2=len(bodies))
        sources[kind] = dict(unbound_nodes=nodes, stimulus_ids=[item["id"] for item in bodies],
            stimulus_source=ref, sender="claude_a", receiver="codex", boundary=BOUNDARY,
            consumer="jev_advice_run.record_inbox_order(messages=None)",
            native_opportunities_proven=False)
    return counts, sources


def check_registration(value):
    if "semantic_input" not in value:
        return
    from upgrade_jev_benchmark import SCHEMA
    require(value["schema"] == SCHEMA, "semantic input is S2 only")
    expected, _ = possible_sites(value["semantic_input"], value["root"])
    require(value["opportunities"] == expected, "opportunities differ from the actual registered input/plan paths")
    factor = input_factor(value["semantic_input"])
    require(all(arm["input_sha256"] == factor for pair in value["factors"].values()
                for arm in pair.values()), "S2 input factor must cover corpus plus semantic bundle and schedule")


def check_corpus(value, tree):
    if "semantic_input" not in value:
        return
    bundle = load_input(value["semantic_input"])
    require(object_sha(tree) == bundle["base_corpus"]["tree_object_sha256"],
        "driver/freezer corpus differs from the complete S2 input factor")


def check_campaign(value, campaign):
    if "semantic_input" not in value:
        require("semantic_input" not in campaign, "unregistered semantic input")
        return None
    require(campaign.get("version") == 3, "semantic campaigns use their original private v3 runtime")
    original = read_ref(value["semantic_input"], "registered semantic bundle")
    ref = campaign.get("semantic_input")
    require(isinstance(ref, dict) and Path(ref["path"]) == checked_path(campaign["root"]) / BODY_PATH
        and read_ref(ref, "frozen semantic bundle") == original,
        "campaign lacks the complete separately frozen semantic input")
    bundle = load_input(value["semantic_input"])
    source = tree_manifest(bundle["base_corpus"]["path"])["files"]
    copied = campaign["input_tree"]["files"]
    require(set(copied) == set(source) and all(
        all(copied[name][key] == source[name][key] for key in ("sha256", "bytes")) for name in source),
        "original corpus bytes changed or were mixed with semantic input")
    return bundle


def registration_slots(value):
    if "semantic_input" not in value:
        return []
    from orchestration_permit import steps
    bundle = load_input(value["semantic_input"])
    identity = object_sha({"benchmark_id": value["benchmark_id"], "root": value["root"]})[:16]
    slots = []
    for step in steps(value):
        group = value["groups"][step["group"]]
        for position, item in enumerate(bundle["messages_by_class"][group["workload_class"]]):
            request_id = "semantic-" + "-".join((identity, step["group"], step["run_id"], item["id"]))
            slots.append(dict(group=step["group"], run_id=step["run_id"], arm="B",
                advisory=group["advisory"], workload_class=group["workload_class"],
                stimulus_id=item["id"], position=position, sender="claude_a", receiver="codex",
                request_id=request_id, body=item["body"], body_sha256=digest(item["body"].encode()),
                body_bytes=len(item["body"].encode()), boundary=BOUNDARY))
    require(len(slots) == 99 and len({s["request_id"] for s in slots}) == 99,
            "separate 33-by-3 semantic denominator changed")
    return slots


def bound_command(campaign, operation, *, run_id=None, request_id=None):
    pin = _campaign_pin(campaign)
    argv = ["rtk", "proxy", campaign["python"]["path"], "-BE",
        str(Path(campaign["adapter"]["path"]) / "scripts/upgrade_semantic_inputs.py"),
        operation, "--campaign", pin["path"], "--campaign-sha256", pin["sha256"]]
    if run_id is not None:
        argv += ["--run", run_id]
    if request_id is not None:
        argv += ["--request-id", request_id]
    command = shlex.join(argv)
    require(not any(c in command for c in ("\n", "\r", "`", "$")), "unsafe pinned semantic command")
    return command


def campaign_slots(campaign):
    from upgrade_benchmark import validate_registered_campaign, validate_registration
    validate_registered_campaign(campaign)
    value = validate_registration(campaign["registration"])
    if "semantic_input" not in value:
        return []
    check_campaign(value, campaign)
    helper, identities = _route(campaign)
    slots = [s for s in registration_slots(value) if s["group"] == campaign["session_group"]]
    return [dict(s, command=bound_command(campaign, "send", run_id=s["run_id"], request_id=s["request_id"]),
        sender_surface_uuid=identities["claude_a"], receiver_surface_uuid=identities["codex"],
        helper_argv=["rtk", "proxy", helper["path"], "send", identities["codex"],
                     "--request-id", s["request_id"], s["body"]]) for s in slots]


def sender_duties(campaign):
    """Pinned file-only instructions for 43's existing startup/run duty integration."""
    return dict(schema="s2-semantic-sender-duties-v1", sender="claude_a", receiver="codex",
        boundary=BOUNDARY, ordinary_communication_denominator_changed=False,
        instructions="Only original run-native claude_a sends each listed command once, in order, "
            "after that run's original native START and before business. Collect each original "
            "process, including exit 75. Never paste, resend, add Enter or replace a missing sample.",
        runs={run["run_id"]: [s["command"] for s in campaign_slots(campaign)
            if s["run_id"] == run["run_id"]] for run in campaign["runs"]})


def send_body(campaign, run_id, request_id):
    """One original helper invocation, no shell/body interpolation or retry."""
    from orchestration_coordinator import Coordinator, State
    try:
        slots = [s for s in campaign_slots(campaign) if s["request_id"] == request_id and s["run_id"] == run_id]
        require(len(slots) == 1, "unique original S2 semantic slot required")
        sample = slots[0]
        co = Coordinator(Path(campaign["root"]) / "runs" / run_id, _campaign_pin(campaign))
        # The original native START, permit and run fences are checked by the
        # constructor. Claude identity is checked separately: never use the
        # supervisor-only co._gate as a substitute for the original sender.
        with co.state_lock(new_work=True):
            co._work_budget()
            require(not (co.root / "finish.json").exists(), "original run is closed")
            states, _ = co.state()
            require(all(state == State.PENDING for state in states.values()),
                    "semantic input boundary has passed; never replay it")
        _require_original_sender(campaign, sample)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(json.dumps(dict(status="REFUSED_NO_HELPER_CALL", request_id=request_id,
            error_type=type(exc).__name__, error=str(exc), retries=0)), file=sys.stderr)
        return 75
    # Shared locks, original intents/fences and deduplication remain helper-owned.
    # Inherit the helper result verbatim, even if pending. No manufactured receipt.
    return subprocess.run(sample["helper_argv"], check=False).returncode


def native_batch(campaign, header, native_end, readers, *, not_after):
    from upgrade_communication_attribution import attribute_samples
    samples = [s for s in campaign_slots(campaign) if s["run_id"] == header["run"]["run_id"]]
    require(len(samples) == 3, "three registered semantic sites required")
    require(header["campaign"] == _campaign_pin(campaign)
        and header["run"] in campaign["runs"], "semantic input belongs to another run or campaign")
    attribution = attribute_samples(campaign, header, native_end, readers, samples=samples,
        not_after=not_after, schema="s2-semantic-native-attribution-v1")
    by_id = {item["request_id"]: item for item in attribution["inputs"]}
    ready = not attribution["unresolved"] and set(by_id) == {s["request_id"] for s in samples}
    result = dict(schema="s2-semantic-native-batch-v1", ready=ready, attribution=attribution, messages=[])
    if not ready:
        return result
    expected = [s["request_id"] for s in samples]
    sent = sorted(expected, key=lambda key: by_id[key]["sender_call"]["offset"])
    arrived = sorted(expected, key=lambda key: by_id[key]["native_record"]["offset"])
    require(sent == arrived == expected, "original semantic send/arrival order differs from the frozen boundary")
    for sample in samples:
        source = by_id[sample["request_id"]]
        # The reused attribution validated this entire inner body, original
        # intent, native sender call, exact receiver, fence and single notice.
        result["messages"].append(dict(id=sample["request_id"], body=sample["body"],
            sha256=sample["body_sha256"], bytes=sample["body_bytes"], eligible=True, source=source))
    return result


def read_requests(campaign, batch, order):
    from upgrade_body_read import read_command
    by_id = {item["id"]: item for item in batch["messages"]}
    requests = []
    for request_id in order:
        item = by_id[request_id]
        notice = json_ref({k: item["source"]["intent"][k] for k in ("path", "sha256", "bytes")})["payload"]
        requests.append(dict(request_id=request_id, notice=notice,
            command=read_command(campaign["python"]["path"], campaign["controller"]["path"], notice),
            inner_body_sha256=item["sha256"], original_source=item["source"]))
    return requests


def observe_body_exposure(campaign, header, record, native_end, readers, *, not_after):
    """Complete native body exposure/order only; never claims semantic action."""
    from upgrade_body_read_evidence import body_read_evidence
    from orchestration_common import parse_time
    batch = native_batch(campaign, header, native_end, readers, not_after=not_after)
    require(batch["ready"] and record["semantic_inputs"] == batch["attribution"],
            "original semantic input lineage changed")
    expected = read_requests(campaign, batch, record["order"]["order"])
    require(record["read_requests"] == expected, "registered complete-body reader commands changed")
    reader = readers["codex"]
    start, end = reader.verify_pin(header["native_start"]["codex"]), reader.verify_pin(native_end["codex"])
    body_reads, unresolved = [], []
    for request in expected:
        original = request["original_source"]["native_record"]
        notice = reader.record(original)
        from upgrade_body_read_evidence import stamp
        proof = body_read_evidence(reader.records(start, end), provider=reader.provider,
            session_id=reader.session["session_id"], notice=request["notice"], command=request["command"],
            not_before=stamp(notice), not_after=parse_time(not_after).timestamp())
        if len(proof) != 1 or proof[0]["call_record"]["offset"] < original["offset"] + original["length"]:
            unresolved.append(request["request_id"])
            continue
        body_reads.append(dict(request_id=request["request_id"], proof=proof[0]))
    actual = [item["request_id"] for item in sorted(body_reads, key=lambda item: item["proof"]["result_record"]["offset"])]
    return dict(schema="s2-semantic-body-exposure-v1", body_reads=body_reads, unresolved=unresolved,
        all_bodies_exposed=len(body_reads) == 3, native_exposure_order=actual,
        exposure_matches_advice=not unresolved and actual == record["order"]["order"],
        semantic_action="NOT_PROVEN_BY_BODY_EXPOSURE", business_accepted=False)


def main(argv=None):
    from orchestration_campaign import anchored_ref, validate_campaign
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect-input", "duties", "send"))
    parser.add_argument("--campaign")
    parser.add_argument("--campaign-sha256")
    parser.add_argument("--input")
    parser.add_argument("--input-sha256")
    parser.add_argument("--preview-root")
    parser.add_argument("--run")
    parser.add_argument("--request-id")
    args = parser.parse_args(argv)
    try:
        if args.operation == "inspect-input":
            require(args.input is not None and args.input_sha256 is not None and args.preview_root is not None,
                    "inspect-input needs exact input pin and private preview root")
            ref = anchored_ref(args.input, args.input_sha256)
            counts, sources = possible_sites(ref, checked_path(args.preview_root))
            print(json.dumps(dict(schema="s2-semantic-input-preparation-v1", input=ref,
                input_factor_sha256=input_factor(ref), declared_sites=counts, source_paths=sources,
                native_opportunities_proven=False, api_requests=0, input_operations=0),
                ensure_ascii=False, sort_keys=True))
            return 0
        require(args.campaign is not None and args.campaign_sha256 is not None, "original campaign pin required")
        campaign = validate_campaign(anchored_ref(args.campaign, args.campaign_sha256))
        require(Path(__file__).resolve() == Path(campaign["adapter"]["path"]) / "scripts/upgrade_semantic_inputs.py",
                "use the complete original frozen semantic adapter")
        if args.operation == "duties":
            print(json.dumps(sender_duties(campaign), ensure_ascii=False, sort_keys=True))
            return 0
        require(args.run is not None and args.request_id is not None, "send needs original run and request ID")
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REFUSED_NO_HELPER_CALL", error=str(exc), retries=0)), file=sys.stderr)
        return 75
    return send_body(campaign, args.run, args.request_id)


if __name__ == "__main__":
    raise SystemExit(main())
