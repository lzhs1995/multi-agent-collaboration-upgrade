"""Pure instructions for 192 independent native messages; never send or retry.

The manifest is a denominator, not a receipt. Only each enrolled native sender
may run its own pinned helper command. Business acceptance remains independent.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

from orchestration_common import ContractError, checked_path, file_ref, json_ref, object_sha, read_ref, require

# The adapter and private controller both contain render_cmux_agent.py. Loading
# the adapter's pure verifier under that public name poisons the later live
# controller import, even for a pilot task whose communication list is empty.
# Keep the fixed sibling in the verified adapter dependency closure, without
# registering or replacing any controller module in sys.modules.
_renderer_spec = importlib.util.spec_from_file_location(
    "_upgrade_communication_renderer", Path(__file__).with_name("render_cmux_agent.py"))
_renderer = importlib.util.module_from_spec(_renderer_spec)
_renderer_spec.loader.exec_module(_renderer)

WARM_RUNS = tuple(f"warm-{n:02d}" for n in range(1, 5))
S2_REGISTRATION = "upgrade-jev-paired-registration-v2"
S2_PLAN = "upgrade-communication-plan-v2"
CLAIMED_PEER = "original_claim_executor_binding"
EXECUTOR_ROLE = "claimed_executor"
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
    from upgrade_benchmark import pilot_group, registered_layout

    expected, _ = registered_layout(registration)
    require(registration["groups"] == expected, "communication registration groups changed")
    pilot = pilot_group(registration)
    s2 = registration["schema"] == S2_REGISTRATION
    identity = object_sha({"benchmark_id": registration["benchmark_id"], "root": registration["root"]})[:16]
    samples = []
    for group_id, group in expected.items():
        if group_id == pilot:
            continue
        for run_id in WARM_RUNS:
            for node_id, executor in NODES[group["workload_class"]]:
                for sender, receiver, stages in (
                    ("codex", executor, ("after_dispatch", "before_accept")),
                    (executor, "codex", ("before_business", "after_business")),
                ):
                    for stage in stages:
                        if s2:
                            # The denominator is frozen before a lane exists. A
                            # role is not a guessed native identity; only the
                            # original claim can resolve it at a reached point.
                            from_role = "codex" if sender == "codex" else EXECUTOR_ROLE
                            to_role = EXECUTOR_ROLE if sender == "codex" else "codex"
                            # Preserve the original opaque slot ID, including
                            # its baseline lane token. That token is a slot
                            # label, never the resolved sender's identity.
                            request_id = "comm-" + "-".join((identity, group_id, run_id, node_id, sender, stage))
                            samples.append(dict(group=group_id, arm=group["arm"], advisory=group["advisory"],
                                workload_class=group["workload_class"], run_id=run_id, node_id=node_id,
                                sender=from_role, receiver=to_role, stage=stage, request_id=request_id,
                                body=None, binding_policy=CLAIMED_PEER))
                            continue
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
    return dict(schema=S2_PLAN if s2 else "upgrade-communication-plan-v1", total_samples=192, samples=samples)


def _route(campaign):
    """The original helper route; also usable by a file-only observer."""
    runtime = json_ref(campaign["runtime"], "communication runtime")
    startup = json_ref(runtime["startup"], "communication startup")
    require(runtime["controller"] == campaign["controller"] and runtime["python"] == campaign["python"]
            and Path(startup["controller"]).resolve() == Path(campaign["controller"]["path"]).resolve(),
            "communication runtime controller/Python changed")
    helper = startup["pins"]["helper"]
    raw = read_ref(helper, "communication helper")
    require(Path(helper["path"]).is_absolute() and os.access(helper["path"], os.X_OK),
            "communication helper must be an absolute executable")
    route = _renderer.verify_route(raw, Path(campaign["controller"]["path"]))
    require(Path(route["python"]).resolve() == Path(campaign["python"]["path"]).resolve(),
            "communication helper Python changed")
    identities = {}
    for lane in ("codex", "claude_a", "claude_b"):
        session = campaign["sessions"][lane]
        require(session["provider"] == ("codex" if lane == "codex" else "claude"),
                "communication sender provider changed")
        identities[lane] = str(uuid.UUID(session["surface_uuid"]))
    require(len(set(identities.values())) == 3, "communication sessions must have distinct native UUIDs")
    return helper, identities


def _campaign_pin(campaign):
    pin = file_ref(checked_path(Path(campaign["root"]) / "campaign.json"))
    require(json_ref(pin, "communication campaign") == campaign, "communication campaign content changed")
    return pin


def _send_command(helper, receiver, request_id, body):
    require(body.isascii() and "\n" not in body and "\r" not in body and len(body) + 160 < 700,
            "communication body/header budget exceeded")
    command = shlex.join(["rtk", "proxy", helper["path"], "send", receiver,
                          "--request-id", request_id, body])
    require(not any(c in command for c in ("\n", "\r", "`", "$")), "unsafe communication command")
    return command


def _bound_command(campaign, request_id):
    pin = _campaign_pin(campaign)
    command = shlex.join(["rtk", "proxy", campaign["python"]["path"], "-BE",
        str(Path(campaign["adapter"]["path"]) / "scripts/upgrade_communication_plan.py"),
        "send-sample", "--campaign", pin["path"], "--campaign-sha256", pin["sha256"],
        "--request-id", request_id])
    require(not any(c in command for c in ("\n", "\r", "`", "$")), "unsafe communication command")
    return command


def resolve_sample(campaign, sample):
    """Read the original claim's locked peer; never bind, guess or send.

    The lease pins the claim, which in turn pins its binding. Mutable current
    binding files are not a fallback. These refs remain stable after release,
    so later attribution resolves the same body and peer as the actual send.
    Missing/unreached claims and self-peers refuse; callers retain the slot.
    """
    if sample.get("binding_policy") != CLAIMED_PEER:
        return sample
    from orchestration_binding import claim_lane

    campaign_pin = _campaign_pin(campaign)
    root = checked_path(Path(campaign["root"]) / "runs" / sample["run_id"])
    header_pin = file_ref(root / "run.json")
    header = json_ref(header_pin, "original communication run")
    require(header["campaign"] == campaign_pin and header["run"] in campaign["runs"]
            and header["run"]["run_id"] == sample["run_id"], "communication run belongs to another campaign")
    plan = json_ref(campaign["plan"], "original communication plan")
    nodes = [node for node in plan["nodes"] if node["id"] == sample["node_id"]]
    require(len(nodes) == 1, "communication node is not in the original plan")
    node = nodes[0]
    claim_pin = file_ref(root / "claims" / (sample["node_id"] + ".json"))
    claim = json_ref(claim_pin, "original communication claim")
    task_id = f"{plan['plan_id']}--{sample['run_id']}--{sample['node_id']}--a0001"
    require(claim["task_id"] == task_id, "communication claim belongs to another node or run")
    peer = claim_lane(node, claim)
    require(peer in campaign["sessions"] and peer != "codex"
            and campaign["sessions"][peer]["provider"] == "claude",
            "communication has no distinct original Claude peer; never send to self")
    leases = json_ref(file_ref(checked_path(Path(campaign["lease_root"]) / "leases.json")),
                      "original communication lease registry")
    lease = leases.get(task_id)
    require(isinstance(lease, dict) and lease["claim"] == claim_pin
            and lease["session_id"] == campaign["sessions"][peer]["session_id"],
            "communication claim differs from its original lease pin")
    binding_pin = claim.get("executor_binding")
    if node["executor"] is None:
        binding = json_ref(binding_pin, "claimed communication executor binding")
        require(binding["campaign"] == campaign_pin and binding["run_id"] == sample["run_id"]
                and binding["node_id"] == sample["node_id"]
                and binding["supervisor_session"] == campaign["sessions"]["codex"]["session_id"],
                "communication executor binding belongs to another campaign, run or supervisor")
    sender = "codex" if sample["sender"] == "codex" else peer
    receiver = peer if sender == "codex" else "codex"
    binding_sha = binding_pin["sha256"] if binding_pin else "frozen_plan"
    body = (f"STATUS: COMM_SAMPLE {sample['request_id']}; sender={sender}; receiver={receiver}; "
            f"point={sample['stage']}; claim={claim_pin['sha256']}; binding={binding_sha}; "
            "no reply required; not business acceptance.")
    helper, identities = _route(campaign)
    return dict(sample, sender=sender, receiver=receiver, body=body, helper=helper,
        sender_surface_uuid=identities[sender], receiver_surface_uuid=identities[receiver],
        command=_bound_command(campaign, sample["request_id"]),
        helper_command=_send_command(helper, identities[receiver], sample["request_id"], body),
        resolution="ORIGINAL_CLAIM_PINNED", claimed_peer=peer,
        binding_resolution_pins=dict(run=header_pin, claim=claim_pin, executor_binding=binding_pin))


def campaign_plan(campaign):
    """Authenticate existing runtime pins and render only this group's commands."""
    if campaign.get("version") != 3 or not any(r["run_id"] in WARM_RUNS for r in campaign["runs"]):
        return []
    from upgrade_benchmark import validate_registered_campaign, validate_registration

    validate_registered_campaign(campaign)
    registration = validate_registration(campaign["registration"])
    samples = [s for s in registration_plan(registration)["samples"] if s["group"] == campaign["session_group"]]
    require(len(samples) == 32, "communication group must contain 32 warm samples")
    helper, identities = _route(campaign)
    result = []
    for sample in samples:
        if sample.get("binding_policy") == CLAIMED_PEER:
            result.append(dict(sample, helper=helper,
                sender_surface_uuid=identities.get(sample["sender"]),
                receiver_surface_uuid=identities.get(sample["receiver"]),
                resolution="AWAITING_ORIGINAL_CLAIM",
                command=_bound_command(campaign, sample["request_id"])))
            continue
        receiver = identities[sample["receiver"]]
        command = _send_command(helper, receiver, sample["request_id"], sample["body"])
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
    if samples[0].get("binding_policy") == CLAIMED_PEER:
        lines.insert(2, "S2 freezes roles, not Claude A/B. Each command reads the original claim's pinned "
            "executor at that reached point and invokes the same original helper once. Missing/changed claims "
            "or a self-peer refuse and stay in the 192 denominator; never substitute another lane or retry.")
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


def _require_original_sender(campaign, sample):
    """Reuse the pinned native caller guard before the one original helper call."""
    from orchestration_campaign import controller_modules

    bridge, native, guard, daemon = controller_modules(campaign)
    scripts = str(Path(campaign["controller"]["path"]) / "scripts")
    sys.path.insert(0, scripts)
    try:
        proof = daemon.collect(os.environ)
        require(isinstance(proof, dict) and isinstance(proof.get("client"), dict),
                "communication sender native proof is unresolved")
        identity, _tree, _env, public = guard.caller_snapshot(initial_proof=proof)
        expected = campaign["sessions"][sample["sender"]]
        require(public is not None and public["session"] == expected["session_id"]
                and public["tty"] == expected["tty"]
                and native._public_process(proof["client"]) == expected["process"],
                "only the original claimed sender may sample communication")
        caller = identity["caller"]
        require(all(str(uuid.UUID(caller[key])) == str(uuid.UUID(expected[field])) for key, field in
            (("workspace_id", "workspace_uuid"), ("surface_id", "surface_uuid"), ("pane_id", "pane_uuid"))),
            "communication sender workspace/surface/pane changed")
        if expected["provider"] == "codex":
            require(public.get("foreground", {}).get("thread_id") == expected["session_id"],
                    "communication sender is not the original foreground supervisor")
    finally:
        sys.path.pop(0)


def send_sample(campaign, request_id):
    """One authorized sampling invocation; zero retries and original helper output."""
    try:
        samples = [s for s in campaign_plan(campaign) if s["request_id"] == request_id]
        require(len(samples) == 1 and samples[0].get("binding_policy") == CLAIMED_PEER,
                "a unique S2 registered communication slot is required")
        sample = resolve_sample(campaign, samples[0])
        _require_original_sender(campaign, sample)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(json.dumps(dict(status="REFUSED_NO_HELPER_CALL", request_id=request_id,
            error_type=type(exc).__name__, error=str(exc), retries=0)), file=sys.stderr)
        return 75
    # The helper owns all original input intents, deduplication, shared locks,
    # native checks and receipts. Inherit stdout/stderr without reconstructing
    # its result. The caller collects this one process, even after exit 75.
    return subprocess.run(shlex.split(sample["helper_command"]), check=False).returncode


def main(argv=None):
    import argparse
    from orchestration_campaign import anchored_ref, validate_campaign

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("send-sample",))
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--campaign-sha256", required=True)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args(argv)
    try:
        campaign = validate_campaign(anchored_ref(args.campaign, args.campaign_sha256))
        require(Path(__file__).resolve() == Path(campaign["adapter"]["path"]) / "scripts/upgrade_communication_plan.py",
                "use the original pinned communication adapter")
    except (OSError, ValueError, TypeError, KeyError, ContractError) as exc:
        print(json.dumps(dict(status="REFUSED_NO_HELPER_CALL", request_id=args.request_id,
            error_type=type(exc).__name__, error=str(exc), retries=0)), file=sys.stderr)
        return 75
    # Do not label an exception after a helper invocation as "no input".
    # Such a failure preserves the original helper journal and propagates.
    return send_sample(campaign, args.request_id)


if __name__ == "__main__":
    raise SystemExit(main())
