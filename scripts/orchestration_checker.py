#!/usr/bin/env python3
"""Standalone independent corpus checker; run with Python -I -B.

Only the standard library is imported. No workload, adapter answer function,
executor-selected command or live service participates in this calculation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def must(value, message):
    if not value:
        raise ValueError(message)


def decode(raw):
    def pairs(items):
        obj = {}
        for key, value in items:
            must(key not in obj, "duplicate JSON key")
            obj[key] = value
        return obj
    def invalid(_):
        raise ValueError("nonfinite JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def path(value):
    p = Path(value)
    must(p.is_absolute() and p == p.resolve(), "noncanonical or symlink path")
    return p


def raw_file(value):
    p = path(value)
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        must(stat.S_ISREG(before.st_mode) and before.st_size <= 64 * 1024 * 1024, "nonregular/oversized input")
        raw = stream.read(64 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        must((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "file changed during verification")
        must(len(raw) == before.st_size, "incomplete file read")
    return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned(ref):
    raw = raw_file(ref["path"])
    must(type(ref["bytes"]) is int and ref["bytes"] == len(raw) and ref["sha256"] == sha(raw), "file pin mismatch")
    return raw


def tree_files(tree):
    must(tree["version"] == 2 and tree["kind"] == "output", "complete input/output tree required")
    base = path(tree["path"])
    must(base.is_dir(), "tree directory missing")
    files, directories = {}, []
    for parent, dirs, names in os.walk(base, followlinks=False):
        for name in dirs + names:
            p = Path(parent) / name
            must(not p.is_symlink(), "tree symlink refused")
        directories.extend(str((Path(parent) / d).relative_to(base)) for d in dirs)
        for name in names:
            p = Path(parent) / name
            raw = raw_file(p)
            files[str(p.relative_to(base))] = dict(sha256=sha(raw), bytes=len(raw), mode=stat.S_IMODE(p.stat().st_mode))
    must(files == tree["files"] and sorted(directories) == tree["directories"], "complete tree mismatch")
    return base, files


def classify(name):
    if "@" in name:
        return "git_source"
    if name[:15] == "docs-typesafe__":
        return "documentation"
    return "metadata"


def inspect_corpus(request):
    base, files = tree_files(request["input_tree"])
    inputs = request["inputs"]
    must(len(inputs) == len({r["path"] for r in inputs}), "duplicate input pin")
    names = {str(path(r["path"]).relative_to(base)) for r in inputs}
    must(names == set(files), "input pins do not cover complete tree")
    contents = {str(Path(r["path"]).relative_to(base)): pinned(r) for r in inputs}
    rows = [decode(line) for line in contents["MANIFEST.jsonl"].splitlines()]
    must(rows and all(isinstance(r, dict) for r in rows), "fetch records missing")
    by_name = {}
    for row in rows:
        name = row["name"]
        must(isinstance(name, str) and name not in ("", ".", "..") and Path(name).name == name and name not in by_name,
             "invalid/duplicate fetch name")
        by_name[name] = row
        data = contents["fetched/" + name]
        must(type(row["bytes"]) is int and row["bytes"] == len(data) and row["sha256"] == sha(data), "fetch metadata mismatch")
        must(type(row["exit_code"]) is int and row["exit_code"] >= 0 and isinstance(row["stderr"], str), "fetch result malformed")
        must(datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00")).tzinfo is not None, "fetch time lacks timezone")
    must(set(contents) == {"MANIFEST.jsonl", "BLOB-VERIFICATION.json"} | {"fetched/" + n for n in by_name}, "extra/missing corpus member")
    blob_doc = decode(contents["BLOB-VERIFICATION.json"])
    blob_rows = blob_doc["rows"]
    source_names = {name for name in by_name if classify(name) == "git_source"}
    must(len(blob_rows) == len(source_names) and {r["file"] for r in blob_rows} == source_names, "Git source coverage mismatch")
    ordered = [by_name[k] for k in sorted(by_name)]
    counts = dict(total=len(ordered), git_source=0, documentation=0, metadata=0,
                  successful_fetches=0, failed_fetches=0, total_bytes=0)
    records, failed = [], []
    for row in ordered:
        group = classify(row["name"])
        counts[group] += 1
        counts["total_bytes"] += row["bytes"]
        counts["failed_fetches" if row["exit_code"] else "successful_fetches"] += 1
        if row["exit_code"]:
            failed.append(row["name"])
        records.append({"name": row["name"], "category": group, "bytes": row["bytes"],
                        "sha256": sha(contents["fetched/" + row["name"]]), "exit_code": row["exit_code"]})
    inventory = dict(manifest_sha256=sha(contents["MANIFEST.jsonl"]), counts=counts, failed_fetches=failed, records=records)
    return contents, ordered, blob_doc, inventory


def check(request):
    must(request["schema"] == "orchestration-check-request-v2", "checker request version mismatch")
    work = decode(pinned(request["work_request"]))
    fields = ("task_id", "attempt_id", "run_id", "node_id", "output_root", "inputs", "dependencies", "input_tree")
    must(work == dict(schema="orchestration-work-request-v2", **{k: request[k] for k in fields}), "work/check requests differ")
    contents, rows, blobs, inventory = inspect_corpus(request)
    root = path(request["output_root"])
    must(root.is_dir() and {p.name for p in root.iterdir()} == {"result.json", "report.md"}, "output set mismatch")
    outputs = request["output_files"]
    must(len(outputs) == 2 and {r["path"] for r in outputs} == {str(root / n) for n in ("result.json", "report.md")},
         "output references differ from assigned scope")
    output = {Path(r["path"]).name: pinned(r) for r in outputs}
    node = request["node_id"]
    if node != "dependent_local":
        must(request["dependencies"] == [], "unexpected dependency")
    if node == "inventory":
        expected = inventory
    elif node == "independent_local":
        verified = []
        for row in sorted(blobs["rows"], key=lambda value: value["file"]):
            data = contents["fetched/" + row["file"]]
            hasher = hashlib.sha1()
            hasher.update(f"blob {len(data)}\x00".encode("ascii"))
            hasher.update(data)
            value = hasher.hexdigest()
            must(row["match"] is True and row["git_blob_sha1"] == value == row["tree_blob_sha1"] and
                 row["bytes"] == len(data) and row["sha256"] == sha(data), "Git blob/tree mismatch")
            verified.append(dict(file=row["file"], repo_path=row["repo_path"], bytes=len(data), sha256=sha(data),
                                 git_blob_sha1=value, tree_blob_sha1=value, match=True))
        must(blobs["checked"] == len(verified) == blobs["matched"], "Git summary mismatch")
        expected = dict(checked=len(verified), matched=len(verified), rows=verified)
    elif node == "verify_inputs":
        sources = []
        for row in rows:
            url = urlsplit(row["url"])
            must(url.scheme == "https" and url.fragment == "" and url.username is None and url.port is None, "invalid source URL")
            commit = None
            if classify(row["name"]) == "documentation":
                must(url.netloc == "docs.typesafe.ai" and row["gh_api_args"] == ["CURL", row["url"]], "document source mismatch")
                source_kind = "documentation"
            else:
                endpoint = url.path[1:] + ("?" + url.query if url.query else "")
                must(url.netloc == "api.github.com" and endpoint.startswith("repos/") and endpoint in row["gh_api_args"],
                     "GitHub source mismatch")
                source_kind = "github_api_metadata"
                if classify(row["name"]) == "git_source":
                    query = parse_qs(url.query)
                    must(set(query) == {"ref"} and len(query["ref"]) == 1 and "/contents/" in url.path,
                         "source commit reference missing")
                    commit = query["ref"][0]
                    must(re.fullmatch(r"[0-9a-f]{40}", commit) and
                         row["name"].split("@", 1)[1].startswith(commit[:12] + "__"), "source commit differs")
                    source_kind = "github_commit_content"
            sources.append(dict(name=row["name"], url=row["url"], source_kind=source_kind, git_ref=commit))
        expected = dict(verified_files=len(contents), manifest_records=len(rows), blob_records=len(blobs["rows"]),
            successful_fetches=sum(1 for r in rows if r["exit_code"] == 0),
            failed_fetches=[{k: r[k] for k in ("name", "exit_code", "bytes", "sha256", "stderr")} for r in rows if r["exit_code"]],
            sources=sources)
    else:
        must(node == "dependent_local", "unknown corpus node")
        deps = request["dependencies"]
        must(len(deps) == 1 and deps[0]["node_id"] == "inventory", "inventory dependency missing")
        dep = deps[0]
        accepted = decode(pinned(dep["accepted_evidence"]))
        must(accepted["run_id"] == request["run_id"] and accepted["task_id"] == dep["task_id"] and
             accepted["attempt_id"] == dep["attempt_id"] and "acceptance_native" in accepted, "foreign/unaccepted inventory")
        must(accepted["output_tree"] == dep["output_tree"] and accepted["output_files"] == dep["output_files"], "dependency pins differ")
        tree_files(dep["output_tree"])
        dep_outputs = {Path(r["path"]).name: (r, pinned(r)) for r in dep["output_files"]}
        must(len(dep_outputs) == len(dep["output_files"]) == 2 and set(dep_outputs) == {"result.json", "report.md"},
             "dependency deliverables differ")
        ref, raw = dep_outputs["result.json"]
        must(decode(raw) == dict(schema="orchestration-corpus-result-v2", task_id=dep["task_id"], attempt_id=dep["attempt_id"],
             node_id="inventory", run_id=request["run_id"], data=inventory), "inventory differs from independently calculated corpus")
        totals = {group: 0 for group in ("git_source", "documentation", "metadata")}
        for record in inventory["records"]:
            totals[record["category"]] += record["bytes"]
        expected = dict(inventory_task_id=dep["task_id"], inventory_sha256=ref["sha256"],
            accepted_evidence_sha256=dep["accepted_evidence"]["sha256"], counts=inventory["counts"],
            bytes_by_category=totals, failed_fetches=inventory["failed_fetches"], consistent=True)
    result = dict(schema="orchestration-corpus-result-v2", **{k: request[k] for k in fields[:4]}, data=expected)
    must(decode(output["result.json"]) == result, "business answer differs from independent calculation")
    failed = ", ".join(inventory["failed_fetches"]) or "none"
    report = "\n".join(["Task: " + request["task_id"], "Run: " + request["run_id"], "Node: " + node,
                        "Result: PASS", "Records: " + str(len(rows)), "Failed fetches retained: " + failed, ""])
    must(output["report.md"].decode("utf-8") == report, "report identity/counts/failures differ")
    return dict(checks=[dict(id=name, passed=True) for name in (
        "complete_input_tree", "all_file_pins", "fetch_record_integrity", "git_source_coverage", "request_binding",
        "same_run_dependencies", "independent_business_answer", "complete_output_and_report")],
        records=len(rows), git_sources=len(blobs["rows"]))


def main():
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        must(len(raw) <= 1024 * 1024, "checker request oversized")
        print(json.dumps(check(decode(raw)), sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps(dict(status="REJECTED", error=str(exc))), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
