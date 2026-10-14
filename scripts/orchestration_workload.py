#!/usr/bin/env python3
"""Bounded, offline research-corpus work executed by the assigned native CLI.

No model calls, network access, delays or scheduler operations. The independent
checker deliberately does not import this module or its answer functions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from orchestration_common import (ContractError, checked_path, count, digest, file_ref, json_ref,
                                  parse_time, read_ref, strict_json, verify_tree, write_bytes_once, write_json)
from orchestration_native import require

IDENTITY = ("task_id", "attempt_id", "run_id", "node_id")


def category(name):
    return "git_source" if "@" in name else "documentation" if name.startswith("docs-typesafe__") else "metadata"


def load_corpus(request):
    tree = request["input_tree"]
    require(tree["kind"] == "output", "complete input tree required")
    verify_tree(tree)
    base = checked_path(tree["path"])
    refs = {str(Path(r["path"]).relative_to(base)): r for r in request["inputs"]}
    require(len(refs) == len(request["inputs"]) and set(refs) == set(tree["files"]), "complete unique input pins required")
    raw = {name: read_ref(ref, "corpus input") for name, ref in refs.items()}
    rows = [strict_json(line, "fetch record") for line in raw["MANIFEST.jsonl"].splitlines()]
    require(rows and all(isinstance(r, dict) for r in rows), "nonempty fetch manifest required")
    names = [r["name"] for r in rows]
    require(len(set(names)) == len(names) and all(isinstance(n, str) and Path(n).name == n and n not in ("", ".", "..")
                                               for n in names), "unsafe or duplicate fetched names")
    require(set(raw) == {"MANIFEST.jsonl", "BLOB-VERIFICATION.json"} | {"fetched/" + n for n in names},
            "corpus has missing or extra files")
    for row in rows:
        data = raw["fetched/" + row["name"]]
        require(count(row["bytes"], "fetch bytes") == len(data) and row["sha256"] == digest(data), "fetch pin mismatch")
        count(row["exit_code"], "fetch exit code")
        parse_time(row["fetched_at"])
        require(isinstance(row["stderr"], str), "fetch stderr missing")
    blobs = strict_json(raw["BLOB-VERIFICATION.json"], "blob reference")
    require(isinstance(blobs, dict) and isinstance(blobs.get("rows"), list), "blob reference rows required")
    require({r["file"] for r in blobs["rows"]} == {n for n in names if category(n) == "git_source"} and
            len({r["file"] for r in blobs["rows"]}) == len(blobs["rows"]), "blob coverage differs from source inventory")
    return rows, blobs, raw


def inventory(rows, raw):
    ordered = sorted(rows, key=lambda row: row["name"])
    counts = Counter(category(row["name"]) for row in ordered)
    return dict(manifest_sha256=digest(raw["MANIFEST.jsonl"]),
        counts=dict(total=len(rows), git_source=counts["git_source"], documentation=counts["documentation"],
                    metadata=counts["metadata"], successful_fetches=sum(r["exit_code"] == 0 for r in rows),
                    failed_fetches=sum(r["exit_code"] != 0 for r in rows), total_bytes=sum(r["bytes"] for r in rows)),
        failed_fetches=[r["name"] for r in ordered if r["exit_code"]],
        records=[dict(name=r["name"], category=category(r["name"]), bytes=r["bytes"], sha256=r["sha256"],
                      exit_code=r["exit_code"]) for r in ordered])


def verify_blobs(blobs, raw):
    values = []
    for row in sorted(blobs["rows"], key=lambda item: item["file"]):
        data = raw["fetched/" + row["file"]]
        actual = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        require(row["bytes"] == len(data) and row["sha256"] == digest(data) and row["match"] is True and
                row["git_blob_sha1"] == row["tree_blob_sha1"] == actual, "original Git blob/tree reference mismatch")
        values.append(dict(file=row["file"], repo_path=row["repo_path"], bytes=len(data), sha256=digest(data),
                           git_blob_sha1=actual, tree_blob_sha1=row["tree_blob_sha1"], match=True))
    require(blobs.get("checked") == len(values) and blobs.get("matched") == len(values), "blob summary mismatch")
    return dict(checked=len(values), matched=len(values), rows=values)


def verify_sources(rows, blobs, raw):
    sources = []
    for row in sorted(rows, key=lambda item: item["name"]):
        url = urlsplit(row["url"])
        require(url.scheme == "https" and not url.fragment and not url.username and not url.port, "unexpected source URL")
        commit = None
        if category(row["name"]) == "documentation":
            require(url.netloc == "docs.typesafe.ai" and row["gh_api_args"] == ["CURL", row["url"]], "document provenance mismatch")
            kind = "documentation"
        else:
            endpoint = url.path.lstrip("/") + ("?" + url.query if url.query else "")
            organization_listing = category(row["name"]) == "metadata" and re.fullmatch(
                r"/orgs/[A-Za-z0-9-]+/repos", url.path) is not None
            require(url.netloc == "api.github.com" and (endpoint.startswith("repos/") or organization_listing) and
                    endpoint in row["gh_api_args"], "GitHub provenance mismatch")
            kind = "github_api_metadata"
            if category(row["name"]) == "git_source":
                refs = parse_qs(url.query)
                require("/contents/" in url.path and set(refs) == {"ref"} and len(refs["ref"]) == 1 and
                        re.fullmatch("[0-9a-f]{40}", refs["ref"][0]), "source is not pinned to a Git commit")
                commit, kind = refs["ref"][0], "github_commit_content"
                require(row["name"].split("@", 1)[1].startswith(commit[:12] + "__"), "source filename/commit mismatch")
        sources.append(dict(name=row["name"], url=row["url"], source_kind=kind, git_ref=commit))
    return dict(verified_files=len(raw), manifest_records=len(rows), blob_records=len(blobs["rows"]),
        successful_fetches=sum(r["exit_code"] == 0 for r in rows),
        failed_fetches=[{k: r[k] for k in ("name", "exit_code", "bytes", "sha256", "stderr")}
                        for r in sorted(rows, key=lambda row: row["name"]) if r["exit_code"]], sources=sources)


def dependent_summary(request, rows, raw):
    dependencies = request["dependencies"]
    require(len(dependencies) == 1 and dependencies[0]["node_id"] == "inventory", "same-run inventory dependency required")
    dependency = dependencies[0]
    accepted = json_ref(dependency["accepted_evidence"])
    require(accepted.get("run_id") == request["run_id"] and accepted.get("task_id") == dependency["task_id"] and
            accepted.get("attempt_id") == dependency["attempt_id"] and "acceptance_native" in accepted,
            "inventory has no matching original acceptance")
    require(accepted["output_tree"] == dependency["output_tree"] and accepted["output_files"] == dependency["output_files"],
            "dependency outputs differ from acceptance")
    verify_tree(dependency["output_tree"])
    for ref in dependency["output_files"]:
        read_ref(ref, "accepted inventory output")
    matches = [r for r in dependency["output_files"] if Path(r["path"]).name == "result.json"]
    require(len(matches) == 1, "one inventory result required")
    result = json_ref(matches[0])
    expected = inventory(rows, raw)
    require(result == dict(schema="orchestration-corpus-result-v2", node_id="inventory", run_id=request["run_id"],
                          task_id=dependency["task_id"], attempt_id=dependency["attempt_id"], data=expected),
            "accepted inventory disagrees with frozen corpus")
    byte_counts = {kind: sum(r["bytes"] for r in expected["records"] if r["category"] == kind)
                   for kind in ("git_source", "documentation", "metadata")}
    return dict(inventory_task_id=dependency["task_id"], inventory_sha256=matches[0]["sha256"],
                accepted_evidence_sha256=dependency["accepted_evidence"]["sha256"], counts=expected["counts"],
                bytes_by_category=byte_counts, failed_fetches=expected["failed_fetches"], consistent=True)


def execute(request_ref, output_root):
    request = json_ref(request_ref, "work request")
    require(request.get("schema") == "orchestration-work-request-v2", "work request version mismatch")
    root = checked_path(output_root)
    require(str(root) == request["output_root"] and root.is_dir() and not list(root.iterdir()),
            "assigned output must exist and be empty; never overwrite or repeat business work")
    require(all(isinstance(request.get(k), str) and request[k] for k in IDENTITY), "complete task identity required")
    rows, blobs, raw = load_corpus(request)
    key = request["node_id"]
    if key != "dependent_local":
        require(request["dependencies"] == [], "unexpected dependency")
    if key == "inventory":
        data = inventory(rows, raw)
    elif key == "independent_local":
        data = verify_blobs(blobs, raw)
    elif key == "verify_inputs":
        data = verify_sources(rows, blobs, raw)
    else:
        require(key == "dependent_local", "unknown frozen corpus node")
        data = dependent_summary(request, rows, raw)
    # Recheck the entire corpus before publishing either output.
    verify_tree(request["input_tree"])
    value = dict(schema="orchestration-corpus-result-v2", **{k: request[k] for k in IDENTITY}, data=data)
    result = write_json(root / "result.json", value, exclusive=True)
    failures = ", ".join(sorted(r["name"] for r in rows if r["exit_code"])) or "none"
    report = (f"Task: {request['task_id']}\nRun: {request['run_id']}\nNode: {key}\nResult: PASS\n"
              f"Records: {len(rows)}\nFailed fetches retained: {failures}\n")
    report_ref = write_bytes_once(root / "report.md", report.encode())
    return dict(status="WORK_WRITTEN_REQUIRES_INDEPENDENT_ACCEPTANCE", result=result, report=report_ref)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args(argv)
    try:
        ref = file_ref(checked_path(args.request))
        require(ref["sha256"] == args.request_sha256, "trusted work request hash mismatch")
        print(json.dumps(execute(ref, args.output_root), sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REJECTED", error=str(exc))), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
