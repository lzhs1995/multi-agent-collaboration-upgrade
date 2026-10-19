"""Pinned, task-bound preparation inputs for new v3 task packs.

The inventory is produced by the original harness, not synthesized from a gate.
Historical adoption is evidence of startup only; live dispatch gates still apply.
"""
from pathlib import Path
import re

from orchestration_common import contract_errors, file_ref, json_ref, parse_time, require
from orchestration_contracts import campaign_uuid


def input_paths(campaign, header, node):
    root = Path(campaign["root"]) / "runs" / header["run"]["run_id"] / "nodes" / node["id"] / "preflight"
    return {"role_map": str(root / "role-map.json"),
            "pane_inventory": str(root / "surface-inventory.json")}


def adoption_reference(campaign, node):
    proof = campaign.get("client_isolation")
    return None if proof is None else dict(runtime=campaign["runtime"], proof=proof, executor_id=node["executor"])


def pin_inputs(campaign, header, node, pack):
    paths = input_paths(campaign, header, node)
    require(all(pack.get(k) == v for k, v in paths.items()), "preparation input paths changed")
    return dict(schema="orchestration-preparation-inputs-v1",
                **{key: file_ref(path) for key, path in paths.items()},
                client_adoption=adoption_reference(campaign, node))


@contract_errors("preparation inputs")
def verify_inputs(campaign, header, node, info, pack, gate, *, fixtures=False):
    from orchestration_evidence import prepared_supervisor
    supervisor = prepared_supervisor(campaign, node, gate)
    pins = pack.get("preparation_inputs")
    require(isinstance(pins, dict) and set(pins) == {"schema", "role_map", "pane_inventory", "client_adoption"}
            and pins["schema"] == "orchestration-preparation-inputs-v1", "preparation input pins required")
    paths = input_paths(campaign, header, node)
    for key, path in paths.items():
        require(pack.get(key) == path and pins[key]["path"] == path, "preparation input paths changed")
    role_map, inventory = json_ref(pins["role_map"]), json_ref(pins["pane_inventory"])
    workspace = gate.get("workspace_ref")
    require(isinstance(workspace, str) and re.fullmatch(r"workspace:[0-9]+", workspace),
            "preparation workspace ref invalid")
    for value in (role_map, inventory):
        require(value.get("task_id") == info["task_id"] and value.get("workspace_ref") == workspace,
                "preparation input task/workspace changed")
        stamp = parse_time(value["updated_at"])
        require(parse_time(info["started_at"]) <= stamp, "preparation input predates task claim")
        if pack.get("finalized_at"):
            require(stamp <= parse_time(pack["finalized_at"]), "preparation input postdates finalization")
    sender, receiver = campaign["sessions"]["codex"], campaign["sessions"][node["executor"]]
    executors = gate.get("executors")
    require(isinstance(executors, list) and len(executors) == 1, "preparation gate requires one assigned executor")
    item = executors[0]
    require(item.get("surface_ref") == gate.get("executor") == receiver["surface_uuid"] and
            campaign_uuid(campaign, item.get("surface_uuid")) ==
            campaign_uuid(campaign, gate.get("executor_surface_uuid")) == receiver["surface_uuid"] and
            item.get("provider") == gate.get("executor_provider") == receiver["provider"] and
            item.get("workspace_ref") == gate.get("executor_workspace_ref") == workspace and
            item.get("pane_ref") == gate.get("executor_pane_ref"), "preparation gate executor aliases disagree")
    require(set(role_map) == {"task_id", "workspace_ref", "updated_at", "supervisor", "executor"} and
            role_map["supervisor"] == dict(surface_ref=supervisor, provider="codex", display="SUPERVISOR") and
            role_map["executor"] == dict(surface_ref=receiver["surface_uuid"], provider="claude", display="EXECUTOR_1"),
            "role map differs from assigned participants")
    mapping = next(iter(gate["workspace_proofs"].values()))
    require(mapping.get("workspace_ref") == workspace, "inventory/gate workspace ref changed")
    rows = inventory.get("surfaces_same_workspace")
    require(set(inventory) == {"task_id", "workspace_ref", "updated_at", "surfaces_same_workspace", "cross_workspace_rejected"}
            and isinstance(rows, list) and rows and inventory.get("cross_workspace_rejected") == [],
            "same-workspace inventory required")
    refs, uuids = [], []
    for row in rows:
        ref = row.get("ref")
        require(isinstance(ref, str) and re.fullmatch(r"surface:[0-9]+", ref) and
                row.get("workspace_ref") == workspace and
                campaign_uuid(campaign, row.get("workspace_id")) == sender["workspace_uuid"],
                "inventory contains invalid or cross-workspace row")
        refs.append(ref)
        uuids.append(campaign_uuid(campaign, row.get("surface_id")))
    require(len(refs) == len(set(refs)) and len(uuids) == len(set(uuids)), "inventory has duplicate identities")
    for session, ref, pane in ((sender, supervisor, gate.get("supervisor_pane_ref")),
                               (receiver, mapping["target_surface_ref"], gate.get("executor_pane_ref"))):
        require(isinstance(pane, str) and re.fullmatch(r"pane:[0-9]+", pane), "inventory gate pane ref invalid")
        matches = [row for row in rows if campaign_uuid(campaign, row["surface_id"]) == session["surface_uuid"]]
        require(len(matches) == 1 and matches[0]["ref"] == ref and matches[0].get("pane_ref") == pane and
                matches[0].get("is_terminal") is True and
                matches[0].get("surface_type") == "terminal", "inventory does not bind the assigned terminal")
        # Inventory provider is a title heuristic, possibly unknown or stale.
        # prepared_supervisor authenticates the actual native provider/session.
    require(gate["supervisor_pane_ref"] != gate["executor_pane_ref"], "inventory participants share a pane")
    expected = adoption_reference(campaign, node)
    require(pins["client_adoption"] == expected, "task adoption differs from campaign/lane")
    if fixtures:
        require(expected is None and json_ref(campaign["runtime"])["execution_mode"] == "offline",
                "offline inputs cannot impersonate live adoption")
    else:
        require(expected is not None, "live preparation requires external adoption")
        from upgrade_live_runtime import identity_matches, validate_adoption
        # Historical verification must remain possible after operator stop.
        # prepare/dispatch independently enforce current stop and live identity.
        adoption = validate_adoption(expected["runtime"], expected["proof"], allow_stopped=True)
        require(set(adoption["sessions"]) == set(campaign["sessions"]) and
                all(identity_matches(session, adoption["sessions"][lane])
                    for lane, session in campaign["sessions"].items()), "adoption original sessions changed")
    return pins
