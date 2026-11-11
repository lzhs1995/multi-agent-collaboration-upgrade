"""Join typed local work to the original native call, RPC and frozen claim."""
from pathlib import Path

from orchestration_common import file_ref, json_ref, read_json, require, parse_time


def action(reader, ref, record, end, campaign, output_root):
    from orchestration_inputs import invocation_calls
    from upgrade_task_campaign import validate_controls
    from upgrade_task_control import native_invocation, native_result
    from upgrade_task_local import execution_spec, verify_stage
    payload = record["payload"]
    args = payload.get("arguments", payload.get("input", ""))
    calls = invocation_calls(payload.get("name"), args)
    typed = [c for c in calls if c.get("kind") == "task_control"]
    if not typed: return None
    require(campaign is not None and "task_controls" in campaign
            and reader.session == campaign["sessions"]["codex"], "TASK_LOCAL_READER_CAMPAIGN_REQUIRED")
    require(len(typed) == 1 and len(calls) == 1, "TASK_LOCAL_READER_EXACT_CALL_REQUIRED")
    controls = campaign["task_controls"]
    _, operations = validate_controls(campaign, check_startup=False)
    name = typed[0]["operation_id"]
    require(name in operations, "TASK_LOCAL_READER_UNREGISTERED_OPERATION")
    invocation = file_ref(Path(controls["evidence_directory"]) / (name + ".json"))
    value = json_ref(invocation)
    native = native_invocation(reader, invocation, value["request"], allow_intended=True)
    require(native["native"] == ref and value["request"]["registration"] == controls["registration"]
            and value["request"]["evidence_directory"] == controls["evidence_directory"]
            and value["request"]["operation_id"] == name, "TASK_LOCAL_READER_CALL_CHANGED")
    operation = native["operation"]["payload"]
    if operation["action"] != "local-execute": return None
    require(operation == operations[name], "TASK_LOCAL_READER_SCOPE_CHANGED")
    root = Path(campaign["root"]) / "runs" / operation["run_id"]
    header = read_json(root / "run.json")
    require(json_ref(header["campaign"]) == campaign and header["run"]["run_id"] == operation["run_id"],
            "TASK_LOCAL_READER_RUN_CHANGED")
    node = next(n for n in json_ref(campaign["plan"])["nodes"] if n["id"] == operation["node_id"])
    info = read_json(root / "claims" / (node["id"] + ".json"))
    spec = execution_spec(campaign, header, node, info)
    # Exact node roots and the original run's work root are both supported.
    # Path components, not textual prefixes, establish inclusion.
    if not Path(spec["output_root"]).is_relative_to(Path(output_root)): return None
    require(info["native_start"]["offset"] <= ref["offset"], "TASK_LOCAL_READER_BEFORE_CLAIM")
    returned = native_result(reader, invocation, value["request"], until=end)
    if returned is not None:
        verify_stage(campaign, header, node, info, invocation, completed=returned)
        intent = read_json(root / "nodes" / node["id"] / "operations/local-execute/intent.json")
        require(native["at"] <= parse_time(intent["created_at"]), "TASK_LOCAL_READER_INTENT_BEFORE_CALL")
    return dict(invocation=invocation, native=native, returned=returned,
                failed=returned is not None and (not returned["success"] or returned["value"]["returncode"] != 0))
