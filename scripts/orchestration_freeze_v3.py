#!/usr/bin/env python3
"""Freeze a registered 1+2 campaign from externally adopted private clients.

This separate entrypoint cannot create sessions, send input or promote fixtures.
Historical v2 campaigns and their freezer retain their original semantics.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from orchestration_adapter import validate_adapter
from orchestration_campaign import METRICS_V3_COMMUNICATION, anchored_ref, validate_campaign
from orchestration_common import (ContractError, checked_path, digest, file_ref, json_ref,
    read_ref, require, strict_json, tree_manifest, utc_now, write_bytes_once, write_json)
from orchestration_freeze import copy_runtime
from orchestration_native import NativeReader, native_text
from orchestration_planner import ISOLATED_EXECUTORS, validate_plan
from upgrade_benchmark import (ARM_STRATEGIES, CLASSES, group_registration,
                               runtime_observation, validate_registration)
from upgrade_experiment import arm_paths
from upgrade_live_runtime import authenticated_sessions, validate_live_runtime

REQUEST_FIELDS = {"version", "experiment", "runtime", "adoption", "registration", "session_group",
                  "source", "corpus", "revision", "native_authorizations"}


def corpus_plan(input_root, revision, workload_class):
    require(workload_class in CLASSES, "unknown registered workload class")
    input_root = checked_path(input_root)
    specs = [("inventory", "claude_a", [], 30, "Inventory and classify the frozen corpus")]
    if workload_class == "uneven_successor":
        specs += [
            ("source_index", "claude_b", [], 60, "Index lexical identifiers in all verified Git source bytes"),
            ("inventory_successor", "claude_a", ["inventory"], 15, "Summarize the accepted inventory categories"),
            ("merge_uneven", "codex", ["inventory", "source_index", "inventory_successor"], 20,
             "Independently check accepted branches and merge source coverage"),
        ]
    else:
        specs += [
            ("verify_inputs", "claude_b", [], 35, "Verify URLs, commits, fetch outcomes and input pins"),
            ("merge_checks", "codex", ["inventory", "verify_inputs"], 20,
             "Verify and merge the accepted inventory and provenance branches"),
        ]
    namespace = digest(str(input_root).encode())[:24]
    plan = dict(version=2, plan_id=f"corpus-v3-{namespace}", executor_providers=dict(ISOLATED_EXECUTORS),
        objective="Complete the registered corpus graph using only the original adopted CLI sessions",
        source=dict(generated_by="supervisor", revision=revision), decision_provider="rules",
        api_policy=dict(third_party_api=False, model_call_owners=["codex_cli", "claude_code_cli"]), nodes=[])
    for key, lane, deps, estimate, objective in specs:
        node = dict(id=key, title=objective, executor=lane, depends_on=deps,
            execution_kind="local_cli" if lane == "codex" else "delegated_cli",
            transport="codex_cli" if lane == "codex" else "claude_code_cli", session_policy="reuse_existing",
            context="Read the complete pinned task and original inputs. Lexical indexing includes comments and strings.",
            source=[str(input_root / "MANIFEST.jsonl"), str(input_root / "BLOB-VERIFICATION.json")],
            scope=objective + ". Execute the assigned bounded corpus command once; report facts from the bytes.",
            forbidden="No network/API calls, replacement/reset/model switching, artificial delays, source/shared writes or repeated business commands.",
            verify="The original Codex supervisor runs the frozen independent isolated checker, then natively accepts the exact outputs.",
            write_scope=key, expected_seconds=estimate, deliverables=["result.json", "report.md"], report="report.md")
        if workload_class == "write_conflict" and lane != "codex":
            node.update(write_scope="shared", output_scope="shared/" + key)
        plan["nodes"].append(node)
    return validate_plan(plan)


def latest_model(reader, fence):
    latest = None
    for ref, record in reader.records(0, reader.verify_pin(fence)):
        if reader.provider == "codex" and record.get("type") == "turn_context":
            model = record.get("payload", {}).get("model")
        elif reader.provider == "claude" and record.get("type") == "assistant" and record.get("isApiErrorMessage") is not True:
            model = record.get("message", {}).get("model")
        else:
            continue
        if isinstance(model, str) and model and model != "<synthetic>":
            latest = ref, model
    require(latest is not None, "latest native model evidence missing")
    return latest


def capture_adopted_sessions(runtime_ref, adoption_ref):
    # External publication does not run collect_live_sessions(): that method is
    # deliberately reserved for the actual native supervisor's later commands.
    sessions = authenticated_sessions(runtime_ref, adoption_ref)
    for session in sessions.values():
        reader = NativeReader(session)
        fence = reader.snapshot()
        require(latest_model(reader, fence)[1] == session["model"], "latest native model changed after adoption")
        original = session["model_record"]
        require(original["offset"] + original["length"] <= fence["offset"], "adopted model is outside freeze fence")
        reader.record(original)
        # Keep the adopted model_record, which identity_matches authenticates.
        session["freeze_fence"] = fence
    return sessions


def authorize(records):
    require(isinstance(records, list) and records, "original native user authorization required")
    for item in records:
        require(set(item) == {"session", "record", "text_sha256"}, "invalid native authorization fields")
        reader = NativeReader(item["session"])
        value = native_text(reader.record(item["record"]), reader.provider, "user")
        require(value is not None and digest(value.encode()) == item["text_sha256"],
                "authorization is not the pinned original native user message")
    return copy.deepcopy(records)


def corpus_refs(corpus):
    manifest = file_ref(corpus / "MANIFEST.jsonl")
    rows = [strict_json(line) for line in read_ref(manifest).splitlines()]
    names = [r["name"] for r in rows]
    require(names and all(isinstance(n, str) and Path(n).name == n and n not in ("", ".", "..") for n in names)
            and len(set(names)) == len(names), "invalid corpus fetch names")
    require({p.name for p in (corpus / "fetched").iterdir()} == set(names), "fetched directory differs from manifest")
    return {name: file_ref(corpus / name) for name in
            ["MANIFEST.jsonl", "BLOB-VERIFICATION.json"] + ["fetched/" + n for n in sorted(names)]}


def freeze_campaign(request_ref):
    request = json_ref(request_ref, "v3 freeze request")
    require(set(request) == REQUEST_FIELDS and type(request["version"]) is int and request["version"] == 3,
            "separate v3 freeze request required")
    context, runtime = validate_live_runtime(request["runtime"], context_ref=request["experiment"])
    group = group_registration(request["registration"], request["session_group"], request["experiment"], runtime["arm"])
    runtime_observation(validate_registration(request["registration"]), request["runtime"], runtime)
    paths = arm_paths(context, runtime["arm"])
    root = paths["campaigns"] / group["campaign_id"]
    destination = paths["controllers"] / "adapters" / group["campaign_id"]
    require(not root.exists() and not destination.exists(), "campaign or adapter exists; preserve original freeze evidence")
    source, corpus = checked_path(request["source"]), checked_path(request["corpus"])
    for output in (root, destination):
        require(all(output != p and output not in p.parents and p not in output.parents
                    for p in (source, corpus, Path(runtime["controller"]["path"]))),
                "private campaign/adapter must be separate from source/corpus/controller")
    source_tree = tree_manifest(source)
    validate_adapter(source_tree, purpose="campaign")
    sessions = capture_adopted_sessions(request["runtime"], request["adoption"])
    records = authorize(request["native_authorizations"])
    inputs_source = corpus_refs(corpus)
    plan = corpus_plan(root / "inputs", request["revision"], group["workload_class"])
    root.mkdir(parents=True, mode=0o700)
    write_json(root / "freeze-intent.json", dict(request=request_ref, started_at=utc_now(),
        source=source_tree, controller=runtime["controller"], input_sources=inputs_source, sessions=sessions,
        registration=request["registration"], session_group=request["session_group"], input_operations=0), exclusive=True)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        adapter = copy_runtime(source_tree, destination)
        validate_adapter(adapter, purpose="campaign")
        for name, ref in inputs_source.items():
            write_bytes_once(root / "inputs" / name, read_ref(ref))
        input_tree = tree_manifest(root / "inputs", kind="output")
        inputs = [file_ref(root / "inputs" / name) for name in sorted(input_tree["files"])]
        from orchestration_workload import load_corpus, verify_blobs, verify_sources
        rows, blobs, raw = load_corpus(dict(input_tree=input_tree, inputs=inputs))
        verify_blobs(blobs, raw)
        verify_sources(rows, blobs, raw)
        authorization = write_json(root / "authorization.json", dict(source="user_message", approved_root=str(root),
            session_ids={k: s["session_id"] for k, s in sessions.items()}, native_records=records), exclusive=True)
        campaign = dict(version=3, evidence_class="cli_campaign_v3", campaign_id=group["campaign_id"],
            root=str(root), lease_root=str(paths["state"] / "orchestration-leases"), created_at=utc_now(),
            freeze_request=request_ref, authorization=authorization, sessions=sessions,
            experiment=request["experiment"], runtime=request["runtime"], client_isolation=request["adoption"],
            arm=runtime["arm"], registration=request["registration"], session_group=request["session_group"],
            workload_class=group["workload_class"], plan=write_json(root / "plan.json", plan, exclusive=True),
            runs=[dict(run_id=r["run_id"], strategy=ARM_STRATEGIES[runtime["arm"]]) for r in group["runs"]],
            inputs=inputs, input_tree=input_tree, adapter=adapter, controller=runtime["controller"], python=runtime["python"],
            workload=file_ref(destination / "scripts/orchestration_workload.py"),
            checker=file_ref(destination / "scripts/orchestration_checker.py"), checker_timeout_seconds=30,
            metrics=copy.deepcopy(METRICS_V3_COMMUNICATION), decision_provider="rules", minimum_improvement_percent=15,
            third_party_api=False, replacement_sessions=False)
        # Re-read the same adopted original files, including their current model.
        # A fresh external native inventory is verified again by every real run.
        capture_adopted_sessions(request["runtime"], request["adoption"])
        ref = write_json(root / "campaign.json", campaign, exclusive=True)
        validate_campaign(ref)
        write_json(root / "freeze-complete.json", dict(campaign=ref, completed_at=utc_now(),
            input_operations=0, business_executions=0, classification="FROZEN_NOT_MEASURED"), exclusive=True)
        return dict(status="FROZEN_NOT_MEASURED", campaign=ref, input_operations=0)
    except Exception as exc:
        write_json(root / "freeze-failure.json", dict(failed_at=utc_now(), error=str(exc), input_operations=0), exclusive=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--request-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(freeze_campaign(anchored_ref(args.request, args.request_sha256)), sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REJECTED_FREEZE_PRESERVED", error=str(exc), input_operations=0)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
