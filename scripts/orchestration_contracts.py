"""Versioned orchestration boundaries; v3 never falls back to production MAC."""
from __future__ import annotations

from pathlib import Path

from orchestration_common import checked_path, json_ref, within
from orchestration_native import identity_uuid, require


def campaign_version(campaign):
    value = campaign.get("version")
    require(type(value) is int and value in (2, 3), "campaign version must be 2 or 3")
    return value


def campaign_uuid(campaign, value):
    return identity_uuid(value, uuid_semantics=campaign_version(campaign) == 3)


def controller_uuid(campaign, value):
    """The pinned v3 workspace guard emits uppercase UUIDs for journal keys."""
    return campaign_uuid(campaign, value).upper() if campaign_version(campaign) == 3 else value


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
    from upgrade_live_runtime import (_match_adopted_sessions, validate_adoption,
                                      validate_versioned_runtime)
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
        if runtime["version"] == 1:
            live_gate(campaign["experiment"], campaign["runtime"], campaign["sessions"], campaign.get("client_isolation"))
        else:
            # This invocation already fully validated this runtime above. There
            # is no input or authorization boundary between those scope checks
            # and adoption; still read and authenticate adoption afresh here.
            # Only the second bulk scan before adoption is removed. A transient
            # artifact change/restoration at that old scan point is no longer
            # separately observed. Every subsequent NativeReader and public
            # live gate retains its own complete runtime validation.
            proof_ref = campaign.get("client_isolation")
            proof = validate_adoption(campaign["runtime"], proof_ref, runtime=runtime)
            _match_adopted_sessions(campaign["runtime"], campaign["sessions"], proof_ref, proof)
        from upgrade_benchmark import runtime_observation, validate_registered_campaign, validate_registration
        validate_registered_campaign(campaign)
        runtime_observation(validate_registration(campaign["registration"]), campaign["runtime"], runtime)
    return context, runtime
