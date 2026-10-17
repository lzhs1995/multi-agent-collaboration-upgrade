"""Versioned orchestration boundaries; v3 never falls back to production MAC."""
from __future__ import annotations

from pathlib import Path

from orchestration_common import checked_path, json_ref, within
from orchestration_native import require


def campaign_version(campaign):
    value = campaign.get("version")
    require(type(value) is int and value in (2, 3), "campaign version must be 2 or 3")
    return value


def evidence_class(campaign, stem, fixtures):
    return "offline_fixture" if fixtures else f"{stem}_v{campaign_version(campaign)}"


def state_root(campaign):
    if campaign_version(campaign) == 2:
        return Path.home() / ".local/state/multi-agent-collaboration"
    # Recheck the pinned context, not an arbitrary state_root field in a pack.
    from upgrade_live_runtime import validate_versioned_runtime
    _, runtime = validate_versioned_runtime(campaign["runtime"], context_ref=campaign["experiment"])
    return checked_path(runtime["state_root"])


def validate_private_campaign(campaign, *, fixtures):
    from upgrade_experiment import arm_paths, live_gate, validate_sessions
    from upgrade_live_runtime import live_gate_v2, validate_versioned_runtime
    context, runtime = validate_versioned_runtime(campaign["runtime"], context_ref=campaign["experiment"])
    paths = arm_paths(context, runtime["arm"])
    require(campaign.get("arm") == runtime["arm"], "campaign arm differs from its private controller")
    expected_strategy = {"A1": "mac_native", "B": "omo"}.get(runtime["arm"])
    require(expected_strategy is not None and all(r.get("strategy") == expected_strategy for r in campaign["runs"]),
            "v3 A1/B strategy differs from its registered arm")
    within(campaign["root"], paths["campaigns"])
    require(campaign["lease_root"] == str(paths["state"] / "orchestration-leases"),
            "v3 leases must use the arm's private registry")
    require(campaign["controller"] == runtime["controller"] and campaign["python"] == runtime["python"],
            "v3 controller/Python differs from its private runtime")
    within(campaign["adapter"]["path"], paths["controllers"])
    validate_sessions(campaign["sessions"])
    if fixtures:
        require(runtime["execution_mode"] == "offline" and campaign.get("client_isolation") is None,
                "offline fixtures cannot impersonate active client adoption")
    else:
        gate = live_gate if runtime["version"] == 1 else live_gate_v2
        gate(campaign["experiment"], campaign["runtime"], campaign["sessions"], campaign.get("client_isolation"))
        from upgrade_benchmark import validate_registered_campaign
        validate_registered_campaign(campaign)
    return context, runtime
