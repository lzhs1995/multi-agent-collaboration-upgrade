#!/usr/bin/env python3
"""Preregister the approved private pilot and paired experiment; never start clients.

The registration fixes groups, order and failure accounting before any measured
run. Each group's later campaign binds its original, actually adopted sessions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from orchestration_campaign import anchored_ref
from orchestration_common import (ContractError, checked_path, contract_errors, json_ref,
                                  parse_time, require, utc_now, write_json)

CLASSES = ("uneven_successor", "fan_in", "write_conflict")
ARM_STRATEGIES = {"A1": "mac_native", "B": "omo"}
CONTRACT = dict(
    warm_pairs=12, cold_pairs=3, pilot_consecutive_passes=3,
    maximum_executing_claudes=2, run_timeout_seconds=1800,
    warm_timing="preparation_through_dispatch_execution_callback_acceptance_release_disarm",
    cold_timing="startup_adoption_handshake_plus_complete_run",
    session_policy="fixed_original_sessions_per_class_and_arm; no_replacement_or_reset",
    comparison="median_of_paired_(T_A1-T_B)/T_A1",
    minimum_improvement_percent=15, maximum_class_regression_percent=5,
    disclose_cost_increase_percent=10, require_all_correct_and_isolated=True,
    require_B_zero_human_prompts=True, human_interventions_may_increase=False,
    failures="retain_failed_unknown_timeout_and_incomplete_pairs; never_impute_success",
    unknown_tokens_and_cost="unknown", production_adoption=False,
)


def layout(root, *, compact=False):
    root = checked_path(root)
    groups = {"pilot-B": dict(arm="B", workload_class="uneven_successor",
        context_path=str(root / "groups/pilot-B/experiment.json"), campaign_id="pilot-three-rounds",
        runs=[dict(run_id=f"pilot-{i:02d}", phase="pilot", pair_id=None) for i in range(1, 4)])}
    pairs = []
    for index, kind in enumerate(CLASSES, 1):
        for arm in ARM_STRATEGIES:
            group = kind + "-" + arm
            groups[group] = dict(arm=arm, workload_class=kind,
                context_path=str(root / "groups" / group / "experiment.json"),
                campaign_id="paired-" + kind, runs=[])
        for phase, number, order in [("cold", 1, ("A1", "B") if index % 2 else ("B", "A1"))] + [
                ("warm", n, order) for n, order in enumerate(
                    (("A1", "B"), ("B", "A1"), ("B", "A1"), ("A1", "B")), 1)]:
            pair_id = f"{phase}-{index:02d}-{number:02d}"
            run_id = f"{phase}-{number:02d}"
            pair = dict(pair_id=pair_id, phase=phase, workload_class=kind, order=list(order), members={})
            for arm in ARM_STRATEGIES:
                group = kind + "-" + arm
                groups[group]["runs"].append(dict(run_id=run_id, phase=phase, pair_id=pair_id))
                pair["members"][arm] = dict(group=group, run_id=run_id)
            pairs.append(pair)
    if compact:
        # Only physical directory names change. Group identities, original
        # sessions, the 33 slots and the paired order remain identical.
        for index, group in enumerate(groups.values()):
            group["context_path"] = str(root / "g" / str(index) / "experiment.json")
    return groups, pairs


def register(root, benchmark_id, *, compact=False):
    from upgrade_experiment import private_root
    from orchestration_planner import ID_RE
    root = private_root(root)
    require(isinstance(benchmark_id, str) and ID_RE.fullmatch(benchmark_id), "invalid benchmark ID")
    require(not root.exists(), "registration root exists; preserve the original registration")
    groups, pairs = layout(root, compact=compact)
    if compact:
        from upgrade_client_isolation import client_temp_base, control_socket
        for group in groups.values():
            paths = {"tmp": Path(group["context_path"]).parent / "tmp" / group["arm"]}
            control_socket(paths)
            for lane in ("claude_a", "claude_b"):
                client_temp_base(paths, lane)
    value = dict(schema="upgrade-paired-registration-v2" if compact else "upgrade-paired-registration-v1",
                 benchmark_id=benchmark_id,
                 root=str(root), created_at=utc_now(), contract=CONTRACT, groups=groups, pairs=pairs)
    root.mkdir(parents=True, mode=0o700)
    return write_json(root / "registration.json", value, exclusive=True)


@contract_errors("paired registration")
def validate_registration(ref):
    value = json_ref(ref, "paired registration")
    require(set(value) == {"schema", "benchmark_id", "root", "created_at", "contract", "groups", "pairs"}
            and value["schema"] in ("upgrade-paired-registration-v1", "upgrade-paired-registration-v2"),
            "registration format changed")
    root = checked_path(value["root"])
    require(Path(ref["path"]) == root / "registration.json", "registration location changed")
    parse_time(value["created_at"])
    groups, pairs = layout(root, compact=value["schema"] == "upgrade-paired-registration-v2")
    require(value["contract"] == CONTRACT and value["groups"] == groups and value["pairs"] == pairs,
            "approved pairing, groups, order or measurement contract changed")
    return value


def group_registration(ref, group_id, context_ref, arm):
    value = validate_registration(ref)
    require(group_id in value["groups"], "unregistered session group")
    group = value["groups"][group_id]
    require(group["context_path"] == context_ref["path"] and group["arm"] == arm,
            "registered context or arm changed")
    return group


def validate_registered_campaign(campaign):
    group = group_registration(campaign["registration"], campaign["session_group"],
                               campaign["experiment"], campaign["arm"])
    require(campaign["campaign_id"] == group["campaign_id"]
            and campaign["workload_class"] == group["workload_class"]
            and campaign["runs"] == [dict(run_id=r["run_id"], strategy=ARM_STRATEGIES[group["arm"]])
                                     for r in group["runs"]], "campaign differs from registered runs")
    return group


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("register")
    create.add_argument("--root", required=True)
    create.add_argument("--id", required=True)
    create.add_argument("--compact", action="store_true", help="short private directories for native Claude startup")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--registration", required=True)
    inspect.add_argument("--registration-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = (register(args.root, args.id, compact=args.compact) if args.command == "register" else
                  validate_registration(anchored_ref(args.registration, args.registration_sha256)))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
