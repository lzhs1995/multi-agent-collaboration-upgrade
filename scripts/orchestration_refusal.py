"""Read-only, conditional diagnostics for one registered historical refusal.

A matching source/command/result is not a historical startup attestation. This
module never grants settlement, releases a lease, reads a current message body
as historical evidence, imports controller code, or invokes a sender.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import uuid
from pathlib import Path

from orchestration_common import (ContractError, contract_errors, file_ref, parse_time,
                                  read_ref, strict_json, verify_tree)
from orchestration_input_syntax import StaticJS
from orchestration_inputs import invocation_calls

ERROR = "MESSAGE_REFERENCE_MARKER_REQUIRED"
REPLY = json.dumps(dict(confirmed=False, state="ERROR", error=ERROR)) + "\n"
# V8's reviewed, complete pre-refusal local module closure. Entries cannot be
# supplied by a caller, selected from CURRENT, or inferred from an error string.
MAPPINGS = {
    "d4039ba7078eeaae7df91dcbd83ff886408539ff50874537ba0e555f95b8c917": {
        "entrypoint": "scripts/successor_rebind_cli.py",
        "site": "scripts/successor_native_delivery.py:298",
        "modules": {
            "successor_rebind_cli": "678e5a2699397674b0fac12baed474af42e5f1461019584c0942ee9418eeaa47",
            "cmux_bridge": "27a8bfac62c21be3910ccf81b50d2d77bbbcb870951e5f367fe846a020ea9b8a",
            "successor_native_delivery": "8c67762a91bd6779fb3132c20395244a543b8ec5d004359e9d0c2b6218c614cb",
            "cmux_evidence_io": "67d6f03426971f833e1e744b935d8d9190cfa4240cb0bbbe190e758b13f0ad4c",
            "successor_rebind": "f8e375e533b5aacec51bfdaae942cca5e1a8135a69fee938bbd58ed8811d23d6",
            "cmux_workspace_guard": "93894510540c9b6eaa0518e57b09652c7dd08727edbdad57f04e1e4379d211c2",
            "cmux_native_delivery": "2716c7492fb480e7f0ec96b4c6fe3c3dc68995cf4b4ade4dc8189b608a32fc91",
            "cmux_prompt_reference": "ccc894f81c78a33acfcb422ae0a3386d4156a77052c1a45cfb98c5adb5a3379a",
            "cmux_daemon_identity": "886ef37091b1d6279c351c5ae2a62d2e3bf1980bb4c2524da406927918dd06eb",
            "cmux_identity_budget": "542dacf1d8e2452fe13c5d950dcd1f7d32be27bb18d9f2c050f08ce1b2a5a91d",
            "cmux_foreground_thread": "892cd5f401703e8b7f833cc1dd3a2aba2886fc1859b56b7bed681f5fd92b39b3",
        },
        "review_sha256": "4253e6eadab747931bfb6ec0e6e592a1672d0a900bc1605f7122ee129beb9d9c",
    },
}


class HistoricalRefusal(ContractError):
    """A rejected absence check with structured evidence, never an exception to it."""
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__("historical sender refusal cannot authorize settlement: " + diagnostic["reason"])


def require(ok, reason):
    if not ok:
        raise ContractError(reason)


def _turn(payload):
    value = payload["internal_chat_message_metadata_passthrough"]["turn_id"]
    require(isinstance(value, str) and str(uuid.UUID(value)) == value, "native turn identity missing or invalid")
    return value


def _nested_result(payload):
    require(payload.get("is_error") is not True and payload.get("isError") is not True,
            "outer native exec failed")
    blocks = payload["output"]
    require(isinstance(blocks, list) and len(blocks) == 2 and
            all(isinstance(b, dict) and b.get("type") in ("text", "input_text") and
                isinstance(b.get("text"), str) for b in blocks), "original result envelope differs")
    require(re.fullmatch(r"Script completed\nWall time [0-9]+(?:\.[0-9]+)? seconds\nOutput:\n",
                         blocks[0]["text"]) is not None, "outer exec did not complete")
    value = strict_json(blocks[1]["text"], "nested command result")
    require(isinstance(value, dict) and type(value.get("exit_code")) is int and value["exit_code"] == 2 and
            value.get("output") == REPLY and value.get("session_id") is None and value.get("cell_id") is None,
            "nested result is not the exact terminal refusal")
    return value


@contract_errors("historical refusal native binding")
def _bind(reader, start, end, ref, indexed, call, signature):
    require(reader.provider == "codex" and indexed.get("unique") is True and indexed["call"] == ref,
            "one ordered original call/result pair required")
    record, result_record = reader.record(ref), reader.record(indexed["result"])
    payload, result = record["payload"], result_record["payload"]
    require(record["type"] == result_record["type"] == "response_item" and
            payload["type"] == "custom_tool_call" and result["type"] == "custom_tool_call_output" and
            payload["call_id"] == result["call_id"] and payload["name"] in ("exec", "functions.exec"),
            "unsupported or mismatched original native invocation")
    turn = _turn(payload)
    require(_turn(result) == turn, "native call/result turn differs")
    source = payload.get("input", payload.get("arguments"))
    nested = list(StaticJS(source).calls())
    require(len(nested) == 1 and nested[0][0] == "exec_command" and
            isinstance(nested[0][1], dict) and invocation_calls(payload["name"], source) == [call],
            "refusal mapping requires one literal exec_command")
    args = nested[0][1]
    require(call["native_tool"]["arguments"] == args and args.get("login") is False and
            set(args) <= {"cmd", "login", "workdir", "shell", "tty", "yield_time_ms", "max_output_tokens"} and
            args.get("shell", "/bin/zsh") == "/bin/zsh" and args.get("tty", False) is False,
            "original tool options are outside the registered invocation")
    command = args["cmd"]
    shell = call["shell"]
    require(len(shell) == 1 and shell[0]["literal"] is True and shell[0]["depth"] == 0 and
            not shell[0].get("heredoc") and shell[0]["command"] == shell[0]["segment"] == command and
            shell[0]["argv"] == shlex.split(command), "nested/expanded shell invocation is not registered")
    result_value = _nested_result(result)
    executions, ids, command_calls = [], {}, 0
    observed = hashlib.sha256()
    for count, (candidate_ref, candidate) in enumerate(reader.records(start, end), 1):
        require(count <= 250000, "refusal record observation bound exceeded")
        observed.update(f'{candidate_ref["offset"]}:{candidate_ref["length"]}:{candidate_ref["sha256"]}\n'.encode())
        p = candidate.get("payload", {})
        if not isinstance(p, dict):
            continue
        if candidate.get("type") == "response_item" and p.get("type") in ("custom_tool_call", "function_call"):
            meta = p.get("internal_chat_message_metadata_passthrough", {})
            if isinstance(meta, dict) and meta.get("turn_id") == turn:
                parsed = invocation_calls(p.get("name"), p.get("input", p.get("arguments")))
                command_calls += sum(c.get("native_tool", {}).get("arguments", {}).get("cmd") == command for c in parsed)
        if candidate.get("type") != "event_msg" or p.get("type") != "item_completed":
            continue
        item = p.get("item", {})
        if not isinstance(item, dict) or item.get("type") != "CommandExecution":
            continue
        ident = item.get("id")
        if isinstance(ident, str):
            ids[ident] = ids.get(ident, 0) + 1
        if (p.get("thread_id") == reader.session["session_id"] and p.get("turn_id") == turn and
                item.get("command") == ["/bin/zsh", "-c", command]):
            executions.append((candidate_ref, candidate))
        require(len(ids) <= 65536 and len(executions) <= 4096, "execution observation bound exceeded")
    require(observed.hexdigest() == signature, "native history changed during refusal observation")
    require(command_calls == 1 and len(executions) == 1, "command execution binding is absent or ambiguous")
    execution_ref, execution = executions[0]
    p, item = execution["payload"], execution["payload"]["item"]
    require(ref["offset"] + ref["length"] <= execution_ref["offset"] and
            execution_ref["offset"] + execution_ref["length"] <= indexed["result"]["offset"],
            "command execution lies outside its original call/result")
    require(isinstance(item.get("id"), str) and item["id"] and ids[item["id"]] == 1 and
            isinstance(item.get("process_id"), str) and re.fullmatch(r"[1-9][0-9]*", item["process_id"]),
            "original execution/process identity missing or duplicated")
    require(item.get("source") == "unified_exec_startup" and item.get("status") == "failed" and
            type(item.get("exit_code")) is int and item["exit_code"] == 2 and item.get("stderr") == "" and
            all(item.get(k) == REPLY for k in ("stdout", "aggregated_output", "formatted_output")),
            "execution is not the exact original terminal refusal")
    require(type(p.get("started_at_ms")) is int and type(p.get("completed_at_ms")) is int and
            parse_time(record["timestamp"]).timestamp() * 1000 <= p["started_at_ms"] <= p["completed_at_ms"] <=
            parse_time(execution["timestamp"]).timestamp() * 1000 <= parse_time(result_record["timestamp"]).timestamp() * 1000,
            "original execution chronology differs")
    require(isinstance(item.get("cwd"), str) and item["cwd"].startswith("file:///"), "native command cwd missing")
    if "workdir" in args:
        require(Path(args["workdir"]).is_absolute() and Path(args["workdir"]).as_uri() == item["cwd"],
                "original command cwd differs from tool workdir")
    return dict(call=ref, result=indexed["result"], execution=execution_ref, call_id=payload["call_id"],
                thread_id=reader.session["session_id"], turn_id=turn, execution_id=item["id"], process_id=item["process_id"],
                command=item["command"], cwd=item["cwd"], native_tool=call["native_tool"], shell=shell,
                result_value=result_value, scan_sha256=signature, start=start, end=end)


@contract_errors("registered refusal source")
def _source(context, frame, call):
    require(isinstance(context, dict) and set(context) == {"controller", "python"},
            "only the original controller and Python pins are accepted")
    controller = context["controller"]
    root = Path(controller["path"])
    manifest = file_ref(root.parent / "MANIFEST.json")
    mapping = MAPPINGS.get(manifest["sha256"])
    require(mapping is not None, "controller release has no registered refusal mapping")
    release = strict_json(read_ref(manifest), "registered release manifest")
    require(frame["wrappers"] in ([], ["rtk", "proxy"]) and frame["entrypoint_kind"] == "script" and
            frame["interpreter_options"] == ["-B"] and Path(frame["interpreter"]).is_absolute() and
            frame["entrypoint"] == str(root / mapping["entrypoint"]),
            "wrapper/interpreter/options/entrypoint do not match the registered route")
    require(str(Path(frame["interpreter"]).resolve()) == context["python"]["path"],
            "declared interpreter differs from the campaign Python pin")
    read_ref(context["python"], "currently pinned interpreter")
    args = frame["entrypoint_argv"]
    require(args and args[0] == "submit" and len(args[1:]) % 2 == 0, "subcommand or CLI option shape differs")
    options = dict(zip(args[1::2], args[2::2]))
    require(len(options) * 2 == len(args) - 1 and
            {"--artifact", "--text-file", "--marker"} <= set(options) <=
            {"--artifact", "--text-file", "--marker", "--wait-seconds"} and
            options["--artifact"] == call["artifact"] and options["--text-file"] == call["text_file"] and
            options["--marker"] == call["marker"] and options["--marker"] and
            all(Path(options[k]).is_absolute() for k in ("--artifact", "--text-file")),
            "original submit options differ from the registered route")
    if "--wait-seconds" in options:
        require(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", options["--wait-seconds"]), "nonliteral wait option")
    verify_tree(controller)
    closure = {}
    for module, sha in mapping["modules"].items():
        key = "scripts/" + module + ".py"
        declared = controller["files"][key]
        published = release["files"][key]
        # The complete tree pins POSIX mode; the release manifest records only
        # executable status. Compare the common evidence without discarding
        # the stricter tree/mode check above and below this loop.
        require(declared["sha256"] == published["sha256"] == sha and
                type(declared["bytes"]) is int and type(published["bytes"]) is int and
                declared["bytes"] == published["bytes"] and
                type(declared["mode"]) is int and type(published["executable"]) is bool and
                bool(declared["mode"] & 0o111) == published["executable"],
                "registered controller dependency differs: " + key)
        pin = dict(path=str(root / key), bytes=declared["bytes"], sha256=sha)
        read_ref(pin, "registered refusal dependency")
        closure[key] = pin
    verify_tree(controller)
    return dict(manifest=manifest, closure=closure, refusal_site=mapping["site"],
                static_review_sha256=mapping["review_sha256"], python=context["python"],
                scope="declared pinned source, conditional on unobserved historical startup")


def diagnose_refusal(reader, start, end, ref, indexed, call, context, signature):
    """Keep unknowns even when every observable mapping prerequisite matches."""
    diagnostic = dict(schema="orchestration-historical-refusal-v1", version=1,
        status="BLOCKED", mapping_status="UNMATCHED", original_invocation_zero_input="UNKNOWN",
        controller_zero_input="UNKNOWN", controller_zero_persistent_writes="UNKNOWN",
        startup_environment="UNKNOWN", process_zero_persistent_writes="UNKNOWN",
        invocation_body_identity="NOT_PROVEN", invocation_artifact_identity="NOT_PROVEN",
        prior_marker_input="UNKNOWN", settlement_authorized=False, input_retry_authorized=False,
        diagnostic_input_operations=0, diagnostic_persistent_writes=0)
    try:
        diagnostic["native"] = _bind(reader, start, end, ref, indexed, call, signature)
        diagnostic["source"] = _source(context, call["shell"][0], call)
    except ContractError as exc:
        diagnostic["reason"] = str(exc)
        return diagnostic
    diagnostic.update(mapping_status="MATCHED_STATIC_ONLY", controller_zero_input="CONDITIONAL",
                      controller_zero_persistent_writes="CONDITIONAL",
                      reason="original Python startup/environment is unobserved; current pins cannot attest past execution")
    return diagnostic
