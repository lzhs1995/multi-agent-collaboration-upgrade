"""Read-only private-harness inspection before/after client adoption.

This entrypoint never imports a sender or publishes adoption. It authenticates
the original startup PID/birth/TTY and reads metadata from its native journal.
The external controller, outside all three client sandboxes, owns publication.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

from orchestration_common import (ContractError, digest, file_ref, json_ref, parse_time, read_regular,
                                  require, strict_json, utc_now)
from upgrade_client_isolation import LANES, require_kernel_boundary
from upgrade_native_roots import process_root
from upgrade_startup_contract import (GOAL_SCHEMA, pressure_startup_prompt,
                                     readiness_command, readiness_prompt, startup_continuity_enabled)
from upgrade_socket_boundary import binding_boundary, verify_kernel


class ReadOnlyCmux:
    def __init__(self, executable):
        self.executable = executable
        self.snapshot = None

    def _run(self, *args):
        require(args == ("tree", "--all", "--json", "--id-format", "both"),
                "INSPECTOR_METADATA_ONLY")
        # One fresh tree per inspection, shared by workspace and surface checks.
        if self.snapshot is None:
            self.snapshot = subprocess.run([self.executable, *args], capture_output=True,
                                           text=True, timeout=10, check=True).stdout
        return self.snapshot


def launch_workspace(stdout, bridge):
    """Resolve the complete original create response against one live tree."""
    require(isinstance(stdout, str), "LAUNCH_WORKSPACE_RESPONSE_INVALID")
    response = stdout.strip()
    selectors = {}
    if response.startswith("{"):
        value = strict_json(response, "workspace creation response")
        require(isinstance(value, dict) and value
                and set(value) <= {"workspace_id", "workspace_ref", "id", "ref"},
                "LAUNCH_WORKSPACE_RESPONSE_INVALID")
        for key, item in value.items():
            kind = "ref" if key.endswith("ref") else "id"
            require(kind not in selectors, "LAUNCH_WORKSPACE_RESPONSE_AMBIGUOUS")
            selectors[kind] = item
    else:
        match = re.fullmatch(r"(?:OK )?(workspace:[0-9]+|[0-9a-fA-F-]{36})", response)
        require(match is not None, "LAUNCH_WORKSPACE_RESPONSE_INVALID")
        item = match[1]
        selectors["ref" if item.startswith("workspace:") else "id"] = item
    for kind, item in selectors.items():
        require(isinstance(item, str), "LAUNCH_WORKSPACE_RESPONSE_INVALID")
        if kind == "ref":
            require(re.fullmatch(r"workspace:[0-9]+", item), "LAUNCH_WORKSPACE_RESPONSE_INVALID")
        else:
            selectors[kind] = str(uuid.UUID(item))
    tree = strict_json(bridge._run("tree", "--all", "--json", "--id-format", "both"), "live cmux tree")
    workspaces = [ws for window in tree["windows"] for ws in window["workspaces"]]
    matched = []
    # Resolve every selector independently: a duplicate ref cannot be excused
    # by a unique UUID, nor can a ref/UUID mismatch select a different workspace.
    for kind, item in selectors.items():
        rows = [ws for ws in workspaces
                if (str(uuid.UUID(ws["id"])) if kind == "id" else ws.get("ref")) == item]
        require(len(rows) == 1, "LAUNCH_WORKSPACE_NOT_UNIQUE")
        matched.append(str(uuid.UUID(rows[0]["id"])))
    require(len(set(matched)) == 1, "LAUNCH_WORKSPACE_CHANGED")
    workspace = matched[0]
    require(sum(str(uuid.UUID(ws["id"])) == workspace for ws in workspaces) == 1,
            "LAUNCH_WORKSPACE_NOT_UNIQUE")
    return workspace


from upgrade_readiness_evidence import (command_in_tool, completed_probe, hook_matches,
    native_records, nested_readiness_command, readiness_evidence)


def inspect(startup_ref, spec, binding, *, stage=None):
    """stage: a validated fence; readiness then admits only that stage's window.

    The original launches, PIDs, workspace and history are unchanged inputs.
    """
    import cmux_daemon_identity as identity
    import cmux_native_delivery as native
    from cmux_codex_rpc import DaemonRPC
    control = Path(binding["state_root"]) / "startup"
    intent_ref, result_ref = file_ref(control / "intent.json"), file_ref(control / "result.json")
    intent, result = json_ref(intent_ref), json_ref(result_ref)
    require(intent["startup"] == result["startup"] == startup_ref
            and intent.get("spawn") is True and intent.get("spawn_authorized") is True
            and result["returncode"] == 0 and result["workspace_creation_attempts"] == 1,
            "ORIGINAL_LAUNCH_UNCONFIRMED")
    bridge = ReadOnlyCmux(spec["pins"]["cmux"]["path"])
    launches, launch_refs = {}, {}
    for lane in (*LANES, "codex-server"):
        launch_refs[lane] = file_ref(control / (lane + ".json"))
        launches[lane] = json_ref(launch_refs[lane])
        require(launches[lane]["startup"] == startup_ref, "LAUNCH_STARTUP_CHANGED")
    sessions, evidence, kernels, errors = {}, {}, {}, {}
    startup_failures = {}
    for lane in LANES:
        try:
            launch = launches[lane]
            target = str(uuid.UUID(launch["surface_uuid"]))
            require(target not in spec["protected_surface_uuids"], "PRODUCTION_SURFACE_REFUSED")
            row = native._tree_row(bridge, target)
            client = native._target_process(row)
            require(client["pid"] == launch["pid"] and client["birth"] == launch["birth"],
                    "ORIGINAL_CLIENT_PID_OR_BIRTH_CHANGED")
            workspace = str(uuid.UUID(row["workspace_uuid"]))
            require(workspace == str(uuid.UUID(launch["workspace_uuid"]))
                    == launch_workspace(result["stdout"], bridge), "LAUNCH_WORKSPACE_CHANGED")
            provider = "codex" if lane == "codex" else "claude"
            require(native._provider(client) == provider
                    and str(process_root(provider, client)) == spec["clients"][lane]["native_root"],
                    "CLIENT_PROVIDER_OR_NATIVE_ROOT_CHANGED")
            sid = native._live_session(client, provider)
            if provider == "claude":
                require(sid == spec["clients"][lane]["session_id"], "ORIGINAL_CLAUDE_SESSION_CHANGED")
            path = native._transcript_path(provider, sid, client)
            session = dict(provider=provider, session_id=sid, workspace_uuid=workspace,
                surface_uuid=target, pane_uuid=str(uuid.UUID(row["target_pane_uuid"])), tty=row["tty"],
                process=native._public_process(client), native_config_root=str(process_root(provider, client)),
                transcript=native._pin_file(path, provider, sid))
            kernels[lane] = require_kernel_boundary(client["pid"],
                denied=binding["isolation"]["denied_probes"], writable=binding["isolation"]["write_probes"])
            boundary = binding_boundary(binding, binary_pin=spec["pins"]["codex"], live=True)
            if boundary is not None:
                kernels[lane]["socket_boundary"] = verify_kernel(client["pid"], boundary, writer=lane == "codex")
            sessions[lane] = session
            evidence[lane] = readiness_evidence(native, session, lane, spec, startup_ref, binding, launches,
                                                stage=stage)
            session.update(model=evidence[lane]["model"], model_record=evidence[lane]["model_record"],
                           freeze_fence=session["transcript"])
        except (ValueError, OSError, KeyError, native.NativeDeliveryError, identity.IdentityError) as exc:
            errors[lane] = str(exc)
            if (stage is None and lane == "codex" and str(exc) == "READINESS_TOOL_NOT_UNIQUE_OR_NOT_OBSERVED"
                    and lane in sessions and startup_continuity_enabled(binding)):
                from upgrade_startup_continuity import startup_failure_evidence
                try:
                    context = json_ref(binding["context"])
                    failure = startup_failure_evidence(native, sessions[lane], lane, spec, startup_ref,
                        task_id=context["experiment_id"], now=time.time())
                    startup_failures[lane] = failure
                    sessions[lane].update(model=failure["model"], model_record=failure["model_record"],
                                          freeze_fence=sessions[lane]["transcript"])
                except (ValueError, OSError, KeyError, native.NativeDeliveryError) as failure_error:
                    errors[lane] = "STARTUP_FAILURE_UNVERIFIED:" + str(failure_error)
    capability = None
    if "codex" in sessions:
        try:
            server = identity.process(launches["codex-server"]["pid"])
            require(server["birth"] == launches["codex-server"]["birth"]
                    and server["executable"] == spec["pins"]["codex"]["path"]
                    and server["argv"] == spec["rpc"]["argv"], "ORIGINAL_CODEX_SERVER_CHANGED")
            kernels["codex-server"] = require_kernel_boundary(server["pid"],
                denied=binding["isolation"]["denied_probes"], writable=binding["isolation"]["write_probes"])
            boundary = binding_boundary(binding, binary_pin=spec["pins"]["codex"], live=True)
            if boundary is not None:
                kernels["codex-server"]["socket_boundary"] = verify_kernel(server["pid"], boundary, writer=True)
            client = DaemonRPC(spec["rpc"]["path"], timeout=15)
            try:
                session = sessions["codex"]
                metadata = client.request("thread/read", {"threadId": session["session_id"],
                                                          "includeTurns": False})["thread"]
                turns = client.request("thread/turns/list", {"threadId": session["session_id"],
                    "limit": 1, "sortDirection": "desc", "itemsView": "notLoaded"})["data"]
                require(metadata.get("id") == session["session_id"]
                        and metadata.get("path") == session["transcript"]["path"]
                        and metadata.get("canAcceptDirectInput") is True
                        and isinstance(turns, list) and len(turns) <= 1, "CODEX_DIRECT_INPUT_CAPABILITY_UNVERIFIED")
                capability = dict(thread_id=session["session_id"], path=metadata["path"],
                    can_accept_direct_input=True, status=metadata.get("status"), socket=client.socket_identity,
                    last_turn=[{k: t.get(k) for k in ("id", "status")} for t in turns],
                    methods=["thread/read", "thread/turns/list"], input_operations=0)
            finally:
                client.close()
        except Exception as exc:
            errors["codex-rpc"] = str(exc)
    if len(sessions) == 3:
        require(len({s["workspace_uuid"] for s in sessions.values()}) == 1
                and all(len({s[k] for s in sessions.values()}) == 3
                        for k in ("surface_uuid", "pane_uuid", "session_id")), "CLIENT_ENDPOINTS_NOT_DISTINCT")
    return dict(schema="upgrade-live-inspection-v2", startup=startup_ref, observed_at=utc_now(),
                observed_at_ns=time.time_ns(), inspector=file_ref(Path(__file__).resolve()),
                launch_intent=intent_ref, launch_result=result_ref, launches=launch_refs,
                sessions=sessions, readiness=evidence, kernel=kernels, codex_capability=capability,
                errors=errors, ready=not errors and len(evidence) == 3 and capability is not None,
                stopped=os.path.lexists(spec["stop_path"]) or (stage is not None and os.path.lexists(
                    Path(stage["control"]) / "stop.json")), input_operations=0,
                **({"startup_failures": startup_failures}
                   if stage is None and startup_continuity_enabled(binding) else {}),
                **({"stage": file_ref(Path(stage["control"]) / "stage.json")} if stage is not None else {}))
