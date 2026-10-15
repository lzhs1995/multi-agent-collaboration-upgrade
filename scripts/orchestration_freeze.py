#!/usr/bin/env python3
"""Freeze a private nine-run campaign from authorized, existing native sessions.

This command observes identity and copies local inputs/source. It never sends a
message, starts a model, changes shared settings or starts a measured run.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

from orchestration_adapter import validate_adapter
from orchestration_campaign import METRICS, ORDER, anchored_ref, collect_live_sessions, validate_campaign
from orchestration_common import (ContractError, checked_path, digest, file_ref, json_ref, read_ref, strict_json,
                                  tree_manifest, utc_now, verify_tree, write_bytes_once, write_json)
from orchestration_coordinator import strategy_probe
from orchestration_native import NativeReader, native_text, require
from orchestration_planner import ID_RE, validate_plan


def corpus_plan(input_root, revision):
    # The immutable absolute input root is unique to a campaign. Run/node/attempt
    # names are deliberately identical across policies, but must never reuse a
    # retired campaign's lease owner or shared dispatch journal key.
    namespace = digest(str(checked_path(input_root)).encode())[:32]
    specs = (("dependent_local", "codex", ["inventory"], 30,
              "Consume this run's accepted inventory and derive a consistency summary"),
             ("inventory", "claude", [], 60, "Inventory and classify the frozen research corpus"),
             ("independent_local", "codex", [], 35, "Independently recompute Git blob hashes from the original bytes"),
             ("verify_inputs", "claude", [], 45, "Verify source URLs, commit references, fetch outcomes and pins"))
    plan = dict(version=1, plan_id=f"orchestration-corpus-v2-{namespace}", objective="Audit the accepted research corpus through the original CLI sessions",
                source=dict(generated_by="supervisor", revision=revision), decision_provider="rules",
                api_policy=dict(third_party_api=False, model_call_owners=["codex_cli", "claude_code_cli"]), nodes=[])
    for key, lane, deps, estimate, objective in specs:
        plan["nodes"].append(dict(id=key, title=objective, executor=lane, depends_on=deps,
            execution_kind="local_cli" if lane == "codex" else "delegated_cli",
            transport="codex_cli" if lane == "codex" else "claude_code_cli", session_policy="reuse_existing",
            context="Use the frozen 2026-10-14 research snapshot; preserve failed fetches and original evidence.",
            source=[str(input_root / "MANIFEST.jsonl"), str(input_root / "BLOB-VERIFICATION.json")],
            scope=objective + ". Execute the assigned bounded corpus command once; report facts from the bytes.",
            forbidden="No network/API calls, new sessions, model switches, artificial delays, source/shared-runtime writes or repeated business commands.",
            verify="The supervisor runs the separate, frozen, isolated corpus checker before native acceptance.",
            write_scope=key, expected_seconds=estimate, deliverables=["result.json", "report.md"], report="report.md"))
    return validate_plan(plan)


def capture_sessions(hints, controller, *, fixtures=False, observer=None):
    require(set(hints) == {"codex", "claude"}, "both original session hints required")
    provisional = dict(sessions=hints, controller=controller)
    require(fixtures or observer is None, "identity injection is offline-only")
    observed = (observer or collect_live_sessions)(provisional)
    sessions = {}
    fields = {"provider", "session_id", "workspace_uuid", "pane_uuid", "surface_uuid", "surface_ref", "model"}
    for lane, hint in hints.items():
        require(set(hint) == fields and hint["provider"] == lane, "complete explicit session/model hints required")
        require(all(observed[lane].get(k) == hint[k] for k in fields - {"model", "surface_ref"}),
                "original live session identity changed; do not select another executor")
        require(isinstance(hint["surface_ref"], str) and hint["surface_ref"].startswith("surface:"), "surface reference missing")
        session = dict(observed[lane], surface_ref=hint["surface_ref"], model=hint["model"])
        reader = NativeReader(session, fixtures=fixtures)
        fence = reader.snapshot()
        last = None
        for ref, record in reader.records(0, fence["offset"]):
            if lane == "codex" and record.get("type") == "turn_context":
                model = record.get("payload", {}).get("model")
            elif lane == "claude" and record.get("type") == "assistant" and record.get("isApiErrorMessage") is not True:
                model = record.get("message", {}).get("model")
            else:
                continue
            if isinstance(model, str) and model and model != "<synthetic>":
                last = ref, model
        require(last is not None and last[1] == hint["model"], "latest native model differs from authorized frozen model")
        session.update(model_record=last[0], freeze_fence=fence)
        sessions[lane] = session
    return sessions


def copy_runtime(manifest, destination):
    destination.mkdir()
    for directory in manifest["directories"]:
        (destination / directory).mkdir(parents=True, exist_ok=True)
    for name, entry in manifest["files"].items():
        ref = dict(path=str(Path(manifest["path"]) / name), **{k: entry[k] for k in ("bytes", "sha256")})
        target = destination / name
        write_bytes_once(target, read_ref(ref, "adapter source"))
        os.chmod(target, entry["mode"])
    verify_tree(manifest)
    copied = tree_manifest(destination)
    require(copied["files"] == manifest["files"] and copied["directories"] == manifest["directories"], "incomplete adapter copy")
    return copied


def freeze_campaign(request_ref, *, fixtures=False, observer=None):
    request = json_ref(request_ref, "freeze request")
    require(request.get("version") == 1 and isinstance(request.get("campaign_id"), str) and
            ID_RE.fullmatch(request["campaign_id"]), "invalid freeze request")
    root = checked_path(request["root"])
    require(not root.exists(), "campaign root already exists; preserve original freeze/run evidence")
    source, corpus = checked_path(request["source"]), checked_path(request["corpus"])
    controller_path = checked_path(request["controller"])
    lease_root = checked_path(request["lease_root"])
    require(all(root != p and p not in root.parents and root not in p.parents for p in (source, corpus, controller_path)),
            "private campaign must be separate from its source/corpus/controller")
    require(lease_root != root and root not in lease_root.parents, "use a separate cross-run lease registry")
    source_tree, controller = tree_manifest(source), tree_manifest(controller_path)
    validate_adapter(source_tree, purpose="campaign")
    require("SKILL.md" in controller["files"], "original collaboration controller missing")
    sessions = capture_sessions(request["sessions"], controller, fixtures=fixtures, observer=observer)
    auth_records = copy.deepcopy(request["native_authorizations"])
    require(isinstance(auth_records, list) and auth_records, "original native user authorization required")
    from orchestration_common import digest
    for item in auth_records:
        reader = NativeReader(item["session"], fixtures=fixtures)
        text = native_text(reader.record(item["record"]), reader.provider, "user")
        require(text is not None and digest(text.encode()) == item["text_sha256"], "authorization is not original native user text")
    manifest_ref = file_ref(corpus / "MANIFEST.jsonl")
    manifest_rows = [strict_json(line) for line in read_ref(manifest_ref).splitlines()]
    names = [r["name"] for r in manifest_rows]
    require(names and len(set(names)) == len(names) and all(isinstance(n, str) and Path(n).name == n and
            n not in ("", ".", "..") for n in names), "invalid corpus fetch names")
    require({p.name for p in (corpus / "fetched").iterdir()} == set(names), "source fetched directory differs from manifest")
    input_refs = {n: file_ref(checked_path(corpus / n)) for n in
                  ["MANIFEST.jsonl", "BLOB-VERIFICATION.json"] + ["fetched/" + n for n in sorted(names)]}
    plan = corpus_plan(root / "inputs", request["revision"])
    probe = strategy_probe(plan)
    require(probe["distinguishes_all"], "workload does not distinguish all scheduling policies")
    root.mkdir(parents=True)
    write_json(root / "freeze-intent.json", dict(request=request_ref, started_at=utc_now(),
               source=source_tree, controller=controller, input_sources=input_refs, sessions=sessions), exclusive=True)
    try:
        adapter = copy_runtime(source_tree, root / "adapter")
        validate_adapter(adapter, purpose="campaign")
        for name, ref in input_refs.items():
            write_bytes_once(root / "inputs" / name, read_ref(ref))
        input_tree = tree_manifest(root / "inputs", kind="output")
        inputs = [file_ref(root / "inputs" / name) for name in sorted(input_tree["files"])]
        from orchestration_workload import load_corpus, verify_blobs, verify_sources
        rows, blobs, raw = load_corpus(dict(input_tree=input_tree, inputs=inputs))
        verify_blobs(blobs, raw)
        verify_sources(rows, blobs, raw)
        authorization = write_json(root / "authorization.json", dict(source="user_message", approved_root=str(root),
            session_ids={k: s["session_id"] for k, s in sessions.items()}, native_records=auth_records), exclusive=True)
        campaign = dict(version=2, evidence_class="offline_fixture" if fixtures else "cli_campaign_v2",
            campaign_id=request["campaign_id"], root=str(root), lease_root=str(lease_root), created_at=utc_now(),
            freeze_request=request_ref, authorization=authorization, sessions=sessions,
            plan=write_json(root / "plan.json", plan, exclusive=True),
            schedule_probe=write_json(root / "schedule-probe.json", probe, exclusive=True),
            runs=[dict(run_id=f"run-{index:02d}-{strategy}", strategy=strategy) for index, strategy in enumerate(ORDER, 1)],
            inputs=inputs, input_tree=input_tree, adapter=adapter, controller=controller,
            python=file_ref(Path(sys.executable).resolve()),
            workload=file_ref(root / "adapter/scripts/orchestration_workload.py"),
            checker=file_ref(root / "adapter/scripts/orchestration_checker.py"), checker_timeout_seconds=30,
            metrics=copy.deepcopy(METRICS), decision_provider="rules", minimum_improvement_percent=15,
            third_party_api=False, replacement_sessions=False)
        if not fixtures:
            current = collect_live_sessions(campaign)
            require(all(all(sessions[k].get(name) == value for name, value in actual.items()) for k, actual in current.items()),
                    "identity changed during freeze")
        ref = write_json(root / "campaign.json", campaign, exclusive=True)
        validate_campaign(ref, fixtures=fixtures)
        write_json(root / "freeze-complete.json", dict(campaign=ref, completed_at=utc_now(), input_operations=0,
                   business_executions=0, classification="FROZEN_NOT_MEASURED"), exclusive=True)
        return dict(status="FROZEN_NOT_MEASURED", campaign=ref, schedule_probe=probe, input_operations=0)
    except Exception as exc:
        write_json(root / "freeze-failure.json", dict(failed_at=utc_now(), error=str(exc), input_operations=0), exclusive=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--request-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        ref = anchored_ref(args.request, args.request_sha256)
        print(json.dumps(freeze_campaign(ref), sort_keys=True, allow_nan=False))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REJECTED_FREEZE_PRESERVED", error=str(exc), input_operations=0)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
