"""Caller-anchored immutable inputs for an existing-CLI replay campaign.

A manifest supplied by an evidence file is not an authority root. Public entry
points require its SHA256 separately. Offline fixtures can never be promoted.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import shlex
import sys
from pathlib import Path

from orchestration_common import (ContractError, checked_path, contract_errors, count, digest, file_ref, finite, json_ref,
                                  parse_time, read_ref, verify_tree, within)
from orchestration_native import (NativeReader, OUTPUT_EFFECT_POLICY, READ_ONLY_LS_PREFIX, READ_ONLY_RTK_PREFIX,
                                  native_text, native_verification, require)
from orchestration_planner import ID_RE, executor_providers, validate_plan
from orchestration_contracts import campaign_version, evidence_class, validate_private_campaign

ORDER = ("serial", "omo", "codeg", "omo", "codeg", "serial", "codeg", "serial", "omo")
SHA = re.compile(r"[0-9a-f]{64}\Z")
METRICS = {
    "version": 1,
    "elapsed": "native_start_to_final_terminal_event; same_boot_monotonic_plus_startup",
    "first_action": "first_successful_completed_output_scoped_tool_call_start",
    "retries": "transport_zero_input_retries_plus_failed_business_tool_calls",
    "provider_auto_retries": "separate_native_counter; missing_is_null",
    "interventions": "unclassified_user_inputs_are_unmeasured; status_queries_excluded",
    "status_queries": ["status", "status?", "进度", "进度？", "任务进展到哪里了？"],
    "promotion": "all_nine_correct_and_contract_valid; median_improvement_at_least_15_percent",
    "zero_baseline": "percentage_ineligible",
    "tie_break": ["elapsed_seconds", "human_interventions", "retries", "omo"],
}

# Separate registration: historical v2 results keep their original meaning.
METRICS_V3 = {
    "version": 2,
    "elapsed": "native_start_to_complete_closeout; same_boot_monotonic_plus_startup",
    "first_action": "first_successful_completed_output_scoped_tool_call_start",
    "retries": "transport_zero_input_retries_plus_failed_business_tool_calls",
    "provider_auto_retries": "separate_native_counter; missing_is_null",
    "interventions": "unclassified_user_inputs_are_unmeasured; status_queries_reported_separately",
    "status_queries": list(METRICS["status_queries"]),
    "promotion": "separate_registered_A1_B_paired_experiment; no_single_campaign_promotion",
    "zero_baseline": "percentage_ineligible",
    "native_timeline": "original_receipt_ack_actions_callback_acceptance_release_disarm; missing_is_null",
    "failures": "retain_all_runs_recovery_provider_wait_and_cleanup",
}

# New campaigns opt in explicitly; frozen v3 campaigns retain version 2's
# intervention semantics and their byte-for-byte original closeout summaries.
METRICS_V3_COMMUNICATION = {
    **METRICS_V3,
    "version": 3,
    "interventions": "unclassified_user_inputs_are_unmeasured; status_queries_reported_separately; "
                     "exact_registered_communication_with_original_sender_intent_fence_and_native_record_excluded",
}

# Explicit new registration: neither callback boundaries nor historical action
# attribution are reinterpreted for the three earlier frozen metric definitions.
METRICS_V3_OUTPUT_EFFECTS = {
    **METRICS_V3_COMMUNICATION,
    "version": 4,
    "first_action": "first_successful_completed_output_scoped_tool_call_start; "
                    "shell_separator_boundaries; control_name_substrings_do_not_exempt_calls",
    "output_effect_policy": OUTPUT_EFFECT_POLICY,
    "output_boundary": "successful_proven_reads_exempt; writes_unknown_failed_and_pending_retained",
    "readonly_shell": {"prefix": list(READ_ONLY_LS_PREFIX),
                       "optional_outer_prefix": list(READ_ONLY_RTK_PREFIX),
                       "program": "one_or_more_literal_ls_-la_absolute_path_commands_separated_by_semicolon",
                       "trust": "system_env_sh_ls_binaries_and_configured_rtk_proxy_forwarding; "
                                "bare_ls_and_arbitrary_wrappers_unknown"},
}

# Explicit new registration (ROOT R46): every input-bearing native record is a
# unit, including the queued and multi-block inputs native_text cannot read, and
# only an exact registered producer-journal join attributes one. The four earlier
# definitions keep their frozen meaning and byte-for-byte closeout summaries.
METRICS_V3_INPUT_PROVENANCE = {
    **METRICS_V3_OUTPUT_EFFECTS,
    "version": 5,
    "interventions": "every_input_bearing_native_record_is_a_unit; status_queries_reported_separately; "
                     "exact_verified_protocol_records_and_registered_communication_excluded; "
                     "registered_producer_journal_join_attributes_machine_or_registered_operator; "
                     "any_other_unit_unknown_and_count_null",
    "input_provenance": "upgrade-input-provenance-v1; per_run_registration_frozen_before_start; "
                        "journal_runs/<run_id>/input-provenance.jsonl_pinned_by_closeout_summary",
}


def campaign_effect_policy(campaign):
    return (OUTPUT_EFFECT_POLICY if campaign.get("metrics") in (METRICS_V3_OUTPUT_EFFECTS, METRICS_V3_INPUT_PROVENANCE)
            else None)


def input_provenance_registration(campaign, run_id):
    """The producer registration a v5 campaign froze for one run, else None."""
    if campaign.get("metrics") != METRICS_V3_INPUT_PROVENANCE:
        return None
    return campaign["input_provenance"]["registrations"][run_id]


def anchored_ref(path, expected_sha256):
    require(isinstance(expected_sha256, str) and SHA.fullmatch(expected_sha256),
            "a separately supplied trusted campaign SHA256 is required")
    checked_path(path)
    ref = file_ref(path)
    require(ref["sha256"] == expected_sha256, "trusted campaign hash mismatch")
    return ref


# Argv order is part of the proof: find_control compares payload["input"] to
# the generated script byte for byte, so the decision fields are appended in
# this exact fixed order and in no other. Adding a field here changes the
# pinned call shape and is a deliberate, visible change.
DECISION_ARGUMENTS = ("work_kind", "actual_route", "action", "reason", "advice_sha256")
# The off arm cites no advice. The absence is stated explicitly in the pinned
# call rather than by omitting an argument, so a supervisor who forgot the pin
# cannot silently produce an off-arm decision on the on arm.
NO_ADVICE = "null"


def decision_argument_fields(decision):
    """The five supervisor-chosen fields of a decision, as pinned call text."""
    fields = {name: decision.get(name) for name in DECISION_ARGUMENTS}
    if fields["advice_sha256"] is None:
        fields["advice_sha256"] = NO_ADVICE
    return fields


def control_script(campaign, campaign_ref, action, run_id, *, node_id=None, marker=None,
                   decision=None, decision_fields=None):
    """Exact native supervisor tool request used to bind start/accept timestamps.

    This is returned for the supervisor to invoke, never appended to a journal by
    Python. The verifier requires the original CLI's complete custom-tool record.
    Include its terminal LF so that the returned script is the exact native input.

    ``decision`` is the path of the published decision a ``bind-unbound`` call
    binds. It is appended after ``--marker``, so the order is ``--node``,
    ``--marker``, ``--decision``; any other order matches no original call.

    ``decision_fields`` carries the supervisor's own choices for the fixed
    decision publisher. Its keys must be exactly ``DECISION_ARGUMENTS``, and
    they are appended in that frozen order so the reconstructed script and the
    original native call can only ever agree byte for byte.
    """
    argv = ["rtk", "proxy", campaign["python"]["path"], "-B",
            str(Path(campaign["adapter"]["path"]) / "scripts" / "orchestration_run.py"),
            "--campaign", campaign_ref["path"], "--campaign-sha256", campaign_ref["sha256"],
            action, "--run", run_id]
    if node_id is not None:
        argv.extend(["--node", node_id])
    if marker is not None:
        argv.extend(["--marker", marker])
    if decision is not None:
        require(isinstance(decision, str) and decision, "a bind call names its published decision path")
        argv.extend(["--decision", decision])
    if decision_fields is not None:
        require(set(decision_fields) == set(DECISION_ARGUMENTS),
                "the decision publisher call needs exactly its frozen field set")
        for name in DECISION_ARGUMENTS:
            value = decision_fields[name]
            require(isinstance(value, str) and value, "every published decision field must be original text")
            argv.extend(["--" + name.replace("_", "-"), value])
    args = dict(cmd=shlex.join(argv), workdir=campaign["root"], login=False, max_output_tokens=1600)
    return "text(await tools.exec_command(" + json.dumps(args, ensure_ascii=False, separators=(",", ":")) + "));\n"


def native_control(reader, ref, script):
    record = reader.record(ref)
    payload = record.get("payload", {})
    require(record.get("type") == "response_item" and payload.get("type") == "custom_tool_call" and
            payload.get("name") in ("exec", "functions.exec") and payload.get("input") == script,
            "original supervisor control call does not match the complete bound request")
    return parse_time(record.get("timestamp"))


def find_control(reader, script, *, after=0):
    end = reader.snapshot()["offset"]
    matches = []
    for ref, record in reader.records(after, end):
        payload = record.get("payload", {})
        if record.get("type") == "response_item" and payload.get("type") == "custom_tool_call" and payload.get("input") == script:
            native_control(reader, ref, script)
            matches.append(ref)
    require(len(matches) == 1, "one original supervisor control invocation required; duplicate/absent request")
    return matches[0]


def node_write_scope(root, node):
    """Reserve the whole mutable resource, even when outputs use child directories."""
    scope = Path(node["write_scope"])
    require(not scope.is_absolute() and ".." not in scope.parts and str(scope) not in ("", "."),
            "live write scope must be a relative child of the run work directory")
    return within(Path(root) / "work" / scope, Path(root) / "work")


def node_output(root, node):
    reserved = node_write_scope(root, node)
    if "output_scope" not in node:
        return reserved
    scope = Path(node["output_scope"])
    require(not scope.is_absolute() and ".." not in scope.parts and str(scope) not in ("", ".") and
            str(scope) == node["output_scope"], "live output scope must be a canonical relative child")
    return within(Path(root) / "work" / scope, reserved)


@contract_errors("campaign")
@native_verification
def validate_campaign(ref, *, fixtures=False, check_runtime=True):
    campaign = json_ref(ref, "trusted campaign")
    version = campaign_version(campaign)
    require(campaign.get("evidence_class") == evidence_class(campaign, "cli_campaign", fixtures),
            "campaign execution class mismatch; fixtures are never live evidence")
    require(isinstance(campaign.get("campaign_id"), str) and ID_RE.fullmatch(campaign["campaign_id"]),
            "invalid campaign id")
    root = checked_path(campaign["root"])
    require(Path(ref["path"]) == root / "campaign.json", "campaign path must be fixed before runs")
    require(checked_path(campaign["lease_root"]) != root, "dedicated session lease registry required")
    parse_time(campaign["created_at"])
    require(campaign.get("third_party_api") is False and campaign.get("replacement_sessions") is False,
            "only the explicitly bound original CLI sessions may own model calls")
    require(campaign.get("decision_provider") == "rules", "live campaign uses frozen rules provider")
    require(campaign.get("metrics") in ((METRICS,) if version == 2 else
                                       (METRICS_V3, METRICS_V3_COMMUNICATION, METRICS_V3_OUTPUT_EFFECTS,
                                        METRICS_V3_INPUT_PROVENANCE)),
            "measurement definitions were not frozen")
    if campaign["metrics"] == METRICS_V3_INPUT_PROVENANCE:
        provenance = campaign.get("input_provenance")
        registrations = provenance.get("registrations") if isinstance(provenance, dict) else None
        require(isinstance(registrations, dict) and set(provenance) == {"registrations"}
                and set(registrations) == {r["run_id"] for r in campaign.get("runs") or [] if isinstance(r, dict)},
                "input provenance needs exactly one frozen producer registration per run")
        from upgrade_input_provenance import registered_producers
        for run_id, ref in registrations.items():
            registered_producers(ref, run_id)
    else:
        require("input_provenance" not in campaign, "input provenance belongs to its own metric definition")
    require(finite(campaign.get("minimum_improvement_percent"), "improvement threshold") == 15,
            "approved promotion threshold is 15 percent")
    require(0 < count(campaign.get("checker_timeout_seconds"), "checker timeout") <= 60,
            "checker timeout must be between 1 and 60 seconds")

    plan = validate_plan(json_ref(campaign["plan"], "frozen plan"))
    require(plan["version"] == version - 1, "plan and campaign executor formats differ")
    if version == 2 and (not fixtures or "schedule_probe" in campaign):
        from orchestration_coordinator import strategy_probe
        probe = json_ref(campaign["schedule_probe"], "frozen schedule probe")
        require(probe == strategy_probe(plan) and probe["distinguishes_all"] is True,
                "frozen workload must distinguish all three actual scheduling policies")
    sessions = campaign.get("sessions")
    providers = executor_providers(plan)
    require(isinstance(sessions, dict) and set(sessions) == set(providers), "original CLI lanes differ from the plan")
    require(len({s["session_id"] for s in sessions.values()}) == len(sessions), "session lanes must be distinct")
    require(len({s["workspace_uuid"] for s in sessions.values()}) == 1, "campaign endpoints moved workspaces")
    require(len({s["pane_uuid"] for s in sessions.values()}) == len(sessions), "executors must remain in distinct side panes")
    if version == 3:
        validate_private_campaign(campaign, fixtures=fixtures)
    for lane, session in sessions.items():
        require(session.get("provider") == providers[lane], "session provider mismatch")
        reader = NativeReader(session, fixtures=fixtures)
        freeze = reader.verify_pin(session["freeze_fence"])
        model = reader.record(session["model_record"])
        require(session["model_record"]["offset"] + session["model_record"]["length"] <= freeze,
                "model was not recorded before campaign freeze")
        if lane == "codex":
            require(model.get("type") == "turn_context", "Codex model must come from native turn context")
            actual = model.get("payload", {}).get("model")
        else:
            require(model.get("type") == "assistant", "Claude model must come from a native assistant record")
            actual = model.get("message", {}).get("model")
            require(model.get("isApiErrorMessage") is not True and actual != "<synthetic>",
                    "provider error noise cannot freeze a model")
        require(isinstance(actual, str) and actual and actual == session.get("model"), "frozen model differs from original CLI evidence")
    for node in plan["nodes"]:
        lane = node["executor"]
        if lane is None:
            # A deliberately unbound node derives both values from the lane the
            # separate pre-claim bind step chooses, so freezing either here
            # would pre-commit the node to a provider before it is bound.
            require(node.get("execution_kind") is None and node.get("transport") is None,
                    "unbound node must not freeze an execution kind")
        else:
            require(node.get("execution_kind") == ("local_cli" if lane == "codex" else "delegated_cli"),
                    "unsupported execution ownership")
        node_output(root / "runs" / "scope-check", node)
        deliverables = node.get("deliverables")
        require(isinstance(deliverables, list) and deliverables and len(deliverables) == len(set(deliverables)),
                "node must freeze unique deliverables")
        for name in deliverables:
            require(isinstance(name, str) and name and not Path(name).is_absolute() and
                    ".." not in Path(name).parts and str(Path(name)) == name and name != ".",
                    "invalid relative deliverable")
        require(node.get("report") in deliverables, "report must be a frozen deliverable")
    runs = campaign.get("runs")
    require(isinstance(runs, list) and runs, "frozen run order required")
    require(all(isinstance(r, dict) and set(r) == {"run_id", "strategy"} and
                isinstance(r["run_id"], str) and ID_RE.fullmatch(r["run_id"]) and
                (r["strategy"] in ORDER or version == 3 and r["strategy"] == "mac_native") for r in runs),
            "invalid frozen run specification")
    require(len({r["run_id"] for r in runs}) == len(runs), "run ids must be unique")
    require(version == 3 or fixtures or tuple(r["strategy"] for r in runs) == ORDER,
            "nine counterbalanced original runs required")
    inputs = campaign.get("inputs")
    require(isinstance(inputs, list) and inputs, "frozen workload inputs required")
    require(len({item["path"] for item in inputs}) == len(inputs), "duplicate frozen input paths")
    for item in inputs:
        read_ref(item, "frozen input")
    if not fixtures or "input_tree" in campaign:
        tree = campaign["input_tree"]
        require(tree.get("kind") == "output" and tree.get("path") == str(root / "inputs"),
                "live inputs require the complete private input tree")
        verify_tree(tree)
        expected = {str(Path(tree["path"]) / name): value for name, value in tree["files"].items()}
        require(set(expected) == {item["path"] for item in inputs}, "input list does not cover the complete tree")
        require(all({k: expected[item["path"]][k] for k in ("sha256", "bytes")} ==
                    {k: item[k] for k in ("sha256", "bytes")} for item in inputs), "input tree/file pins differ")
    authorization = json_ref(campaign["authorization"], "authorization")
    require(authorization.get("source") == "user_message" and authorization.get("approved_root") == str(root) and
            authorization.get("session_ids") == {k: s["session_id"] for k, s in sessions.items()},
            "authorization must bind campaign scope and original sessions")
    records = authorization.get("native_records")
    require(isinstance(records, list) and records, "first-hand native user authorization required")
    for item in records:
        reader = NativeReader(item["session"], fixtures=fixtures)
        record = reader.record(item["record"])
        text = native_text(record, reader.provider, "user")
        require(text is not None and digest(text.encode()) == item.get("text_sha256"), "authorization is not the pinned native user message")
    for name in ("checker", "workload"):
        if name == "workload" and fixtures and name not in campaign:
            continue
        within(campaign[name]["path"], campaign["adapter"]["path"])
        read_ref(campaign[name], "trusted frozen " + name)
    if "workload" in campaign:
        require(campaign["workload"]["path"] != campaign["checker"]["path"],
                "workload and independent checker must be separate files")
    if check_runtime:
        for name in ("controller", "adapter"):
            verify_tree(campaign[name])
        read_ref(campaign["python"], "frozen Python")
        require(str(Path(sys.executable).resolve()) == campaign["python"]["path"], "use the frozen Python executable")
    return campaign


def controller_modules(campaign):
    """Load only the complete, previously pinned immutable collaboration release."""
    verify_tree(campaign["controller"])
    root = Path(campaign["controller"]["path"]) / "scripts"
    sys.path.insert(0, str(root))
    try:
        modules = [importlib.import_module(name) for name in
                   ("cmux_bridge", "cmux_native_delivery", "cmux_workspace_guard", "cmux_daemon_identity")]
    finally:
        sys.path.pop(0)
    for module in modules:
        within(Path(module.__file__).resolve(), root)
    return modules


def collect_live_sessions(campaign):
    """Read kernel/foreground identity; never selects another peer or sends input."""
    bridge, native, guard, daemon = controller_modules(campaign)
    # Runtime entry points lazily import siblings, so keep the pinned directory
    # available while they run. No module is loaded from an executor output.
    root = str(Path(campaign["controller"]["path"]) / "scripts")
    sys.path.insert(0, root)
    try:
        # caller_snapshot returns a public projection, not the raw daemon proof.
        # Pass the original proof so the guard rechecks that same kernel identity
        # around its live cmux reads; keep it only for the process projection.
        proof = daemon.collect(os.environ)
        require(isinstance(proof, dict) and isinstance(proof.get("client"), dict),
                "current native supervisor process proof is unresolved")
        identity, _tree, _env, public = guard.caller_snapshot(initial_proof=proof)
        require(public is not None, "current native supervisor identity is unresolved")
        caller = identity["caller"]
        expected = campaign["sessions"]["codex"]
        require(public["session"] == expected["session_id"] and
                public.get("foreground", {}).get("thread_id") == expected["session_id"],
                "only the original foreground supervisor may operate this campaign")
        codex = dict(provider="codex", session_id=public["session"], tty=public["tty"],
                     process=native._public_process(proof["client"]),
                     workspace_uuid=caller["workspace_id"], surface_uuid=caller["surface_id"], pane_uuid=caller["pane_id"])
        private_live = campaign.get("version") == 3
        path = (native._transcript_path("codex", public["session"], proof["client"]) if private_live
                else native._transcript_path("codex", public["session"]))
        pin = native._pin_file(path, "codex", public["session"])
        codex["transcript"] = {k: pin[k] for k in ("path", "device", "inode")}
        if private_live:
            # This module belongs to the separately pinned controller. Importing
            # it as an adapter dependency would either break source freezing or
            # load a second copy from the wrong tree. The authenticated native
            # reader already owns its private-root implementation.
            within(Path(native.private_roots.__file__).resolve(), root)
            codex["native_config_root"] = str(native.private_roots.process_root("codex", proof["client"]))
            canonical_live_uuids(codex)
        observed = dict(codex=codex)
        for lane, session in campaign["sessions"].items():
            if lane == "codex":
                continue
            bound = native.bind_target(bridge, session["surface_uuid"], "ORCHESTRATION_IDENTITY_READ_ONLY")
            receiver = {k: bound[k] for k in ("provider", "session_id", "tty", "process")}
            receiver.update(workspace_uuid=bound["identity"]["workspace_uuid"],
                            surface_uuid=bound["identity"]["target_surface_uuid"], pane_uuid=bound["identity"]["target_pane_uuid"],
                             transcript={k: bound["transcript"][k] for k in ("path", "device", "inode")})
            if private_live:
                receiver["native_config_root"] = bound["native_config_root"]
                canonical_live_uuids(receiver)
            observed[lane] = receiver
        return observed
    finally:
        sys.path.pop(0)


def canonical_live_uuids(session):
    """v3 freezes UUIDs in lowercase; cmux and the transport return uppercase."""
    import uuid
    for key in ("workspace_uuid", "surface_uuid", "pane_uuid"):
        try:
            session[key] = str(uuid.UUID(session[key]))
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            raise ContractError(f"invalid live {key}") from exc


def require_live_identity(campaign):
    observed = collect_live_sessions(campaign)
    require(set(observed) == set(campaign["sessions"]), "original live session lanes missing")
    for lane, actual in observed.items():
        changed = sorted(k for k, v in actual.items() if campaign["sessions"][lane].get(k) != v)
        require(not changed,
                f"original {lane} identity/process/transcript changed ({', '.join(changed)}); do not retarget")
    return observed
