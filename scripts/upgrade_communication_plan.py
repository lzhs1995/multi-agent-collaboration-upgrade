"""Pure instructions for 192 independent native messages; never send or retry.

The manifest is a denominator, not a receipt. Only each enrolled native sender
may run its own pinned helper command. Business acceptance remains independent.
"""
from __future__ import annotations

import os
import shlex
import uuid
from pathlib import Path

from orchestration_common import json_ref, object_sha, read_ref, require
from render_cmux_agent import verify_route

WARM_RUNS = tuple(f"warm-{n:02d}" for n in range(1, 5))
NODES = {
    "uneven_successor": (("inventory", "claude_a"), ("source_index", "claude_b")),
    "fan_in": (("inventory", "claude_a"), ("verify_inputs", "claude_b")),
    "write_conflict": (("inventory", "claude_a"), ("verify_inputs", "claude_b")),
}
RULES = (
    "These are separately authorized ordinary communication samples, not tasks or business ACKs. "
    "Only the named enrolled sender personally executes its exact command once, in its original "
    "native session, using login:false and the campaign workdir. Retain the original tool result "
    "and intent path. If it yields, collect only that process. After an uncertain or failed result, "
    "never resend, change request ID, paste, or use raw input. Only read-only reconciliation of "
    "that original intent is permitted: the same helper reconcile TARGET_UUID --intent ORIGINAL_INTENT. "
    "A failure to obtain an intent stays failed or UNKNOWN. Before/after name sampling points, "
    "not active/idle runtime observations; UNKNOWN runtime remains UNKNOWN. A queued notice, "
    "native receipt, body read, processing, callback, and business acceptance are distinct evidence. "
    "No reply or additional ACK is requested. Keep all failed, queued, and missing samples in the "
    "192 denominator. Do not delay independent business work, wait for all samples, extend the "
    "original deadline, or add communication artifacts to business deliverables."
)


def registration_plan(registration):
    """Return the complete fixed denominator before native sessions are adopted."""
    from upgrade_benchmark import layout

    expected, _ = layout(registration["root"], compact=registration.get("schema") == "upgrade-paired-registration-v2")
    require(registration["groups"] == expected, "communication registration groups changed")
    identity = object_sha({"benchmark_id": registration["benchmark_id"], "root": registration["root"]})[:16]
    samples = []
    for group_id, group in expected.items():
        if group_id == "pilot-B":
            continue
        for run_id in WARM_RUNS:
            for node_id, executor in NODES[group["workload_class"]]:
                for sender, receiver, stages in (
                    ("codex", executor, ("after_dispatch", "before_accept")),
                    (executor, "codex", ("before_business", "after_business")),
                ):
                    for stage in stages:
                        request_id = "comm-" + "-".join((identity, group_id, run_id, node_id, sender, stage))
                        body = (f"STATUS: COMM_SAMPLE {request_id}; sender={sender}; receiver={receiver}; "
                                f"point={stage}; no reply required; not business acceptance.")
                        require(body.isascii() and "\n" not in body and "\r" not in body
                                and len(body) + 160 < 700, "communication body/header budget exceeded")
                        samples.append(dict(group=group_id, arm=group["arm"], workload_class=group["workload_class"],
                            run_id=run_id, node_id=node_id, sender=sender, receiver=receiver,
                            stage=stage, request_id=request_id, body=body))
    require(len(samples) == 192 and len({s["request_id"] for s in samples}) == 192,
            "communication denominator changed")
    return dict(schema="upgrade-communication-plan-v1", total_samples=192, samples=samples)


def campaign_plan(campaign):
    """Authenticate existing runtime pins and render only this group's commands."""
    if campaign.get("version") != 3 or not any(r["run_id"] in WARM_RUNS for r in campaign["runs"]):
        return []
    from upgrade_benchmark import validate_registered_campaign, validate_registration

    validate_registered_campaign(campaign)
    registration = validate_registration(campaign["registration"])
    samples = [s for s in registration_plan(registration)["samples"] if s["group"] == campaign["session_group"]]
    require(len(samples) == 32, "communication group must contain 32 warm samples")
    runtime = json_ref(campaign["runtime"], "communication runtime")
    startup = json_ref(runtime["startup"], "communication startup")
    require(runtime["controller"] == campaign["controller"] and runtime["python"] == campaign["python"]
            and Path(startup["controller"]).resolve() == Path(campaign["controller"]["path"]).resolve(),
            "communication runtime controller/Python changed")
    helper = startup["pins"]["helper"]
    raw = read_ref(helper, "communication helper")
    require(Path(helper["path"]).is_absolute() and os.access(helper["path"], os.X_OK),
            "communication helper must be an absolute executable")
    route = verify_route(raw, Path(campaign["controller"]["path"]))
    require(Path(route["python"]).resolve() == Path(campaign["python"]["path"]).resolve(),
            "communication helper Python changed")
    identities = {}
    for lane in ("codex", "claude_a", "claude_b"):
        session = campaign["sessions"][lane]
        require(session["provider"] == ("codex" if lane == "codex" else "claude"),
                "communication sender provider changed")
        identities[lane] = str(uuid.UUID(session["surface_uuid"]))
    require(len(set(identities.values())) == 3, "communication sessions must have distinct native UUIDs")
    result = []
    for sample in samples:
        receiver = identities[sample["receiver"]]
        command = shlex.join(["rtk", "proxy", helper["path"], "send", receiver,
                              "--request-id", sample["request_id"], sample["body"]])
        require(not any(c in command for c in ("\n", "\r", "`", "$")), "unsafe communication command")
        result.append(dict(sample, helper=helper, sender_surface_uuid=identities[sample["sender"]],
                           receiver_surface_uuid=receiver, command=command))
    return result


def supervisor_messages(campaign):
    samples = [s for s in campaign_plan(campaign) if s["sender"] == "codex"]
    if not samples:
        return ""
    lines = ["\nAdditional warm-run communication sampling (identical A1/B load).", RULES,
             "For each listed Claude node, execute after_dispatch immediately after its original dispatch "
             "returns, and before_accept after its original callback is verified and immediately before "
             "candidate/independent acceptance. Do not execute an unreached sampling point. "
             "The Claude task scope separately authorizes that Claude's own two messages."]
    lines.extend(f"{s['run_id']} {s['node_id']} {s['stage']}: {s['command']}" for s in samples)
    return "\n".join(lines) + "\n"


def executor_messages(campaign, run_id, node_id):
    if campaign.get("version") != 3 or run_id not in WARM_RUNS:
        return ""
    if node_id not in {node for node, _ in NODES.get(campaign.get("workload_class"), ())}:
        return ""
    samples = [s for s in campaign_plan(campaign) if s["run_id"] == run_id
               and s["node_id"] == node_id and s["sender"] != "codex"]
    require(len(samples) == 2, "executor communication points changed")
    lines = ["\nAdditional authorized ordinary communication for this original executor only.", RULES,
             "Personally execute before_business immediately before the bounded business command; "
             "execute after_business immediately after that same original process returns, before "
             "freezing the report and sending the original callback. An unreached point stays missing."]
    lines.extend(f"{s['stage']}: {s['command']}" for s in samples)
    return "\n".join(lines)
