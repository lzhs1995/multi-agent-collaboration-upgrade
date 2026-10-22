"""Automatic private native child hook client; no model or ledger writes.

The external allocator authenticates kernel peer ancestry and native sources.
No caller-supplied authority/session/model/fixture override is accepted here.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys

from orchestration_common import read_regular, require, strict_json
from upgrade_native_roots import configuration
from upgrade_startup_contract import child_budget_enabled
from upgrade_child_budget_protocol import (SCHEMA, MAX_PACKET, child_socket, encode_packet,
                                           parse_consumption)


def request(payload):
    binding, _ = configuration()
    require(child_budget_enabled(binding), "CHILD_NATIVE_HOOK_NOT_REGISTERED")
    lane = os.environ.get("UPGRADE_CLIENT_LANE")
    require(lane in {"codex", "claude_a", "claude_b"}, "CHILD_ORIGINAL_LANE_REQUIRED")
    endpoint = child_socket({"tmp": binding["tmp_root"]})
    packet = encode_packet(dict(schema=SCHEMA, lane=lane, payload=payload))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(10)
        channel.connect(str(endpoint))
        channel.sendall(packet)
        channel.shutdown(socket.SHUT_WR)
        raw = bytearray()
        while b"\n" not in raw:
            chunk = channel.recv(min(16384, MAX_PACKET + 1 - len(raw)))
            require(chunk, "CHILD_NATIVE_SERVICE_REPLY_MISSING")
            raw += chunk
            require(len(raw) <= MAX_PACKET, "CHILD_NATIVE_SERVICE_REPLY_BOUND")
        require(raw.endswith(b"\n") and raw.count(b"\n") == 1, "CHILD_NATIVE_SERVICE_REPLY_CHANGED")
        value = strict_json(raw)
        require(isinstance(value, dict) and value.get("status") in {"RESERVED", "OBSERVED", "REFUSED"},
                "CHILD_NATIVE_SERVICE_REPLY_CHANGED")
        return value


def run_hook(payload):
    require(isinstance(payload, dict), "CHILD_NATIVE_HOOK_PAYLOAD_REQUIRED")
    event, name = payload.get("hook_event_name"), payload.get("tool_name", "")
    if event == "PreToolUse":
        consumption = False
        if name == "Bash":
            binding, _ = configuration()
            startup = strict_json(read_regular(Path(binding["config_root"]) / "startup-v2.json"))
            consumption = parse_consumption(payload.get("tool_input", {}).get("command"),
                startup["pins"]["python"]["path"], binding["controller_root"], startup["pins"]["rtk"]["path"]) is not None
        # A Codex or nested-model child cannot bypass the two-original-Claude scope.
        if name in {"spawn_agent", "collaboration.spawn_agent", "functions.collaboration.spawn_agent"}:
            return 2, dict(hookSpecificOutput=dict(hookEventName="PreToolUse", permissionDecision="deny",
                permissionDecisionReason="CHILD_ORIGINAL_CLAUDE_PARENT_ONLY"))
        if name != "Agent" and not consumption:
            return 0, {}
        if payload.get("agent_id") is not None:
            return 2, dict(hookSpecificOutput=dict(hookEventName="PreToolUse", permissionDecision="deny",
                permissionDecisionReason="CHILD_NESTED_NATIVE_AGENT_REFUSED"))
    elif event not in {"PostToolUse", "SubagentStart", "SubagentStop"} or event == "PostToolUse" and name != "Agent":
        return 0, {}
    try:
        value = request(payload)
        require(value["status"] != "REFUSED", "CHILD_NATIVE_SERVICE_REFUSED:" + value.get("error", "unknown"))
        if event == "PreToolUse" and name == "Agent":
            require(value["status"] == "RESERVED" and isinstance(value.get("hook_output"), dict),
                    "CHILD_NATIVE_RESERVATION_REQUIRED")
            return 0, value["hook_output"]
        return 0, value.get("hook_output", {})
    except (ValueError, KeyError, OSError) as exc:
        if event == "PreToolUse":
            return 2, dict(hookSpecificOutput=dict(hookEventName="PreToolUse", permissionDecision="deny",
                permissionDecisionReason="CHILD_NATIVE_BUDGET_UNVERIFIED: " + str(exc)))
        # Post-creation observation cannot claim a release or stop ordinary work.
        return 0, dict(hookSpecificOutput=dict(hookEventName=event,
            additionalContext="Child budget observation unavailable; preserve the original occupied slot."))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("hook", "consume"))
    parser.add_argument("--claim")
    parser.add_argument("--reply-sha256")
    args = parser.parse_args(argv)
    if args.action == "consume":
        try:
            from upgrade_child_budget_protocol import consume_command
            binding, context = configuration()
            consume_command(context["python"]["path"], binding["controller_root"], args.claim, args.reply_sha256)
            receipt = strict_json(read_regular(Path(binding["evidence_root"]) / "live-control/native-child-budget/reports"
                                              / (args.claim + "-consumed.json")))
            require(receipt.get("schema") == "upgrade-original-child-consumed-v1"
                    and receipt.get("claim_id") == args.claim and receipt.get("reply_sha256") == args.reply_sha256,
                    "ORIGINAL_CHILD_CONSUMPTION_RECEIPT_REQUIRED")
            print(json.dumps(receipt, ensure_ascii=False), flush=True)
            return 0
        except (ValueError, KeyError, OSError) as exc:
            print("CHILD_NATIVE_CONSUMPTION_REFUSED: " + str(exc), file=sys.stderr)
            return 2
    require(args.claim is None and args.reply_sha256 is None, "CHILD_HOOK_ARGUMENT_OVERRIDE_REFUSED")
    raw = sys.stdin.buffer.read(MAX_PACKET + 1)
    try:
        require(len(raw) <= MAX_PACKET, "CHILD_NATIVE_HOOK_INPUT_BOUND")
        code, value = run_hook(strict_json(raw))
    except (ValueError, KeyError, OSError) as exc:
        print("CHILD_NATIVE_HOOK_REFUSED: " + str(exc), file=sys.stderr)
        return 2
    if value:
        print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)
    return code


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
