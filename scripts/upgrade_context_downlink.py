"""Read-only MODEL_CONTEXT_CONSUMED observation of one original Codex->Claude notice.

An explicitly new task policy (surface40 v124) may accept that a busy Claude
lane consumed a MESSAGE_REFERENCE_V2 notice and its body even though Claude
2.1.303 wrote only a queued_command attachment, never a native user record.
This module runs the reviewed shared observer, unchanged, from its complete
pinned release tree in its own interpreter; that observer's import closure is a
different MAC lineage than this repository's, so it is never imported here.

The observation is a separate evidence class. It never sets or implies a native
user receipt, never writes an attempt or receipt, sends no input, consumes no
reply and accepts no work. An attempt that already holds a confirmed native
receipt keeps its native evidence and is refused here. The only write is the
exact wire text, created once beside the caller's evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from cmux_prompt_reference import SINGLE_LINE_FORMAT, _wire
from orchestration_common import (ContractError, checked_path, digest, file_ref, json_ref, read_regular,
                                  require, strict_json, write_bytes_once)
from orchestration_native import original_attempts

POLICY_SCHEMA = "prospective-claude-model-context-receipt-policy-v1"
OBSERVATION_SCHEMA = "model-context-consumption-observation-v1"
RESULT_SCHEMA = "upgrade-context-downlink-observation-v1"
EVIDENCE_CLASS = "MODEL_CONTEXT_CONSUMED"
OBSERVER_TIMEOUT_SECONDS = 60
MARKER = re.compile(r"[A-Za-z0-9_.-]{8,160}")
# The observer reports exposure and consumption of the notice/body context only.
NEVER = ("native_user_received", "official_receipt_published", "attempt_changed", "replay_authorized",
         "business_accepted", "task_authorized", "resource_released")
ERRORS = (ContractError, KeyError, TypeError, ValueError, OSError)


def _source_files(root):
    names = set()
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        for name in subdirectories:
            require(not os.path.islink(os.path.join(directory, name)), "release source contains a symlinked directory")
        names.update(os.path.relpath(os.path.join(directory, name), root) for name in files)
    return names


def pinned_release(policy_ref):
    """The reviewed policy, its complete release source and its observer; any drift refuses."""
    policy = json_ref(policy_ref, "context policy")
    require(policy.get("schema") == POLICY_SCHEMA
            and policy.get("disposition") == "ACCEPTED_FOR_EXPLICIT_NEW_POLICY_BINDING"
            and policy.get("evidence_class") == EVIDENCE_CLASS
            and policy.get("native_user_evidence_class") == "NATIVE_USER_RECEIVED", "context policy is not v124-shaped")
    source = checked_path(policy.get("complete_source"))
    manifest_ref = policy.get("source_manifest")
    manifest = json_ref(manifest_ref, "release manifest")
    require(Path(manifest_ref["path"]) == source.parent / "MANIFEST.json", "manifest is not the release's own")
    files = manifest.get("files")
    require(isinstance(files, dict) and files, "release manifest lists no files")
    # The complete tree, not only the observer: its 27-module import closure must be the reviewed bytes.
    require(_source_files(source) == set(files), "release source differs from its manifest file set")
    for name, pin in files.items():
        raw = read_regular(source / name)
        require(isinstance(pin, dict) and pin.get("bytes") == len(raw) and pin.get("sha256") == digest(raw),
                f"release file differs from its manifest: {name}")
    observer_ref = policy.get("observer")
    observer = source / "scripts" / "model_context_consumption.py"
    require(isinstance(observer_ref, dict) and observer_ref.get("path") == str(observer)
            and observer_ref.get("sha256") == files["scripts/model_context_consumption.py"]["sha256"]
            and observer_ref.get("bytes") == files["scripts/model_context_consumption.py"]["bytes"],
            "policy observer is not the manifest's observer")
    return dict(policy=dict(policy_ref), manifest=dict(manifest_ref), observer=dict(observer_ref), source=str(source))


def observe(attempt_ref, policy_ref, interpreter, wire_dir, *, timeout=OBSERVER_TIMEOUT_SECONDS):
    """One read-only observation of the journal's latest original attempt; raises ContractError otherwise."""
    release = pinned_release(policy_ref)
    interpreter = checked_path(interpreter)
    require(os.access(interpreter, os.X_OK), "observer interpreter is not executable")
    journal = checked_path(Path(attempt_ref["path"]).parent)
    refs = [file_ref(path) for path in sorted(journal.glob("attempt-*.json"))]
    require(refs and refs[-1] == dict(attempt_ref), "only the journal's latest original attempt can be observed")
    attempts = original_attempts(refs)
    for prior in attempts[:-1]:
        # As verify_delivery: an earlier attempt must be proven input-free and identically bound.
        require(prior.get("phase") == "NO_INPUT" and prior.get("events") == [], "an earlier attempt may have input")
        require(prior.get("binding") == attempts[-1].get("binding"), "attempt binding changed")
    attempt = attempts[-1]
    binding = attempt.get("native_binding")
    require(isinstance(binding, dict) and binding.get("schema") == "native-delivery-v1"
            and binding.get("provider") == "claude", "an original Claude native binding is required")
    receipt = None
    if os.path.lexists(journal / "receipt.json"):
        receipt = file_ref(journal / "receipt.json")
        require(json_ref(receipt, "delivery receipt").get("confirmed") is not True,
                "a confirmed native receipt keeps its native evidence")
    marker, body = binding.get("receipt_marker"), attempt.get("body_reference")
    require(isinstance(marker, str) and MARKER.fullmatch(marker), "receipt marker is not a plain token")
    require(isinstance(body, dict) and set(body) == {"format", "path", "sha256", "bytes"}
            and body["format"] == SINGLE_LINE_FORMAT, "a MESSAGE_REFERENCE_V2 body reference is required")
    # The exact original wire is a pure function of the pinned marker and body; its sha binds it.
    wire = _wire(marker, body)
    require(digest(wire.encode()) == binding.get("payload_sha256"), "recomputed wire differs from the original payload")
    wire_ref = write_bytes_once(checked_path(wire_dir) / f"context-wire-{marker}.txt", wire.encode())
    # -B -E are the observer's own preconditions; it imports its closure from its own scripts directory.
    argv = [str(interpreter), "-BE", release["observer"]["path"], "--attempt", attempt_ref["path"],
            "--attempt-sha256", attempt_ref["sha256"], "--text-file", wire_ref["path"]]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=release["source"])
    except subprocess.TimeoutExpired as exc:
        raise ContractError("context observer timed out") from exc
    require(done.returncode in (0, 2), f"context observer failed: rc={done.returncode}")
    value = strict_json(done.stdout, "context observation")
    require(isinstance(value, dict) and value.get("schema") == OBSERVATION_SCHEMA, "unexpected context observer output")
    require(all(value.get(k) is False for k in NEVER[:4]) and value.get("input_operations") == 0,
            "context observer claimed more than an observation")
    if value.get("observation") != EVIDENCE_CLASS:
        require(done.returncode == 2 and value.get("observation") == "UNVERIFIED", "unexpected context observer verdict")
        raise ContractError("context not consumed: " + str(value.get("reason")))
    require(done.returncode == 0, "consumption verdict without a successful exit")
    require(all(value.get(k) is False for k in NEVER), "context observer claimed more than an observation")
    transcript = binding.get("transcript") if isinstance(binding.get("transcript"), dict) else {}
    slices = value.get("proof_slices")
    require(value.get("body_full_read") is True
            and value.get("marker") == marker and value.get("wire_sha256") == binding["payload_sha256"]
            and value.get("body") == body and value.get("session_id") == binding.get("session_id")
            and value.get("attempt") == dict(path=attempt_ref["path"], sha256=attempt_ref["sha256"])
            and value.get("historical_receipt_contract") == binding.get("receipt_contract")
            and isinstance(slices, list) and slices
            and all(isinstance(s, dict) and s.get("path") == transcript.get("path") for s in slices),
            "context observation does not bind this exact attempt, wire, full body and session")
    return dict(schema=RESULT_SCHEMA, evidence_class=EVIDENCE_CLASS, attempt=dict(attempt_ref), receipt=receipt,
                wire=wire_ref, release=release, interpreter=str(interpreter), argv=argv, observation=value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("observe", help="observe one original attempt; prints JSON and never sends input")
    for name in ("--attempt", "--attempt-sha256", "--policy", "--policy-sha256", "--interpreter", "--wire-dir"):
        one.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        attempt, policy = file_ref(checked_path(args.attempt)), file_ref(checked_path(args.policy))
        require(attempt["sha256"] == args.attempt_sha256, "attempt differs from its pin")
        require(policy["sha256"] == args.policy_sha256, "policy differs from its pin")
        result = observe(attempt, policy, args.interpreter, args.wire_dir)
    except ERRORS as exc:
        print(json.dumps(dict(schema=RESULT_SCHEMA, evidence_class=None, observation="UNVERIFIED", reason=str(exc),
                              native_user_received=False, input_operations=0), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
