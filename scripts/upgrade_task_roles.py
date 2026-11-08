"""Read the original display, execution server and two executor roles separately.

No enrollment, RPC input, launch or adoption is performed here. The display's
PID is never substituted for the journal writer. Kernel checks describe the
actual frozen profiles: the original TUI profile has the two socket write
exceptions, while the server additionally denies physical-socket unlink.
Network reachability, inherited descriptors and broker isolation are separate
proofs; successful filesystem checks do not establish those properties.
"""
import os
from pathlib import Path
import uuid

from orchestration_common import file_ref, json_ref, require
from upgrade_client_isolation import LANES, require_kernel_boundary
from upgrade_native_roots import process_root
from upgrade_rpc_peer import server_pin
from upgrade_socket_boundary import binding_boundary, verify_kernel
from upgrade_task_readiness import task_startup_result
from upgrade_task_thread import validate_tui_target

SCHEMA = "upgrade-task-live-roles-v1"


def verify_enrolled_roles(runtime, spec, sessions, *, process):
    binding = json_ref(file_ref(Path(spec["controller"]) / "upgrade-binding.json"))
    return verify_roles(runtime["startup"], spec, binding, sessions, process=process)


def verify_roles(startup_ref, spec, binding, sessions, *, process, root_reader=None):
    root_reader = root_reader or process_root
    require(spec.get("schema") == "upgrade-startup-task-v1"
            and spec.get("task_startup") == binding.get("task_startup")
            and spec.get("controller") == binding.get("controller_root")
            and set(sessions) == set(LANES), "TASK_ROLES_EXPLICIT_STARTUP_REQUIRED")
    require(not os.path.lexists(spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")
    result_ref, result, anchor = task_startup_result(spec)
    root = Path(binding["state_root"]) / "startup"
    launches = {lane: file_ref(root / (lane + ".json")) for lane in (*LANES, "codex-server")}
    rows, kernels = {}, {}
    boundary = binding_boundary(binding, binary_pin=spec["pins"]["codex"], live=True)
    require(boundary is not None, "TASK_ROLES_EXACT_SOCKET_BOUNDARY_REQUIRED")
    for lane, launch_ref in launches.items():
        launch = json_ref(launch_ref)
        row = process(launch["pid"])
        require(launch.get("startup") == startup_ref and row["pid"] == launch["pid"]
                and row["birth"] == launch["birth"], "TASK_ROLES_ORIGINAL_PROCESS_CHANGED:" + lane)
        if lane == "codex-server":
            require(server_pin(row, uid=os.geteuid()) == anchor["server"]
                    and row["argv"] == spec["rpc"]["argv"]
                    and row["executable"] == spec["pins"]["codex"]["path"]
                    and str(root_reader("codex", row)) == anchor["native_root"],
                    "TASK_ROLES_EXECUTION_SERVER_CHANGED")
        else:
            session = sessions[lane]
            provider = "codex" if lane == "codex" else "claude"
            require(session["provider"] == provider
                    and {key: row[key] for key in session["process"]} == session["process"]
                    and str(root_reader(provider, row)) == session["native_config_root"]
                    == spec["clients"][lane]["native_root"], "TASK_ROLES_SESSION_CHANGED:" + lane)
            for field, env in (("surface_uuid", "CMUX_SURFACE_ID"),
                               ("workspace_uuid", "CMUX_WORKSPACE_ID")):
                require(uuid.UUID(row["env"][env]) == uuid.UUID(session[field])
                        == uuid.UUID(launch[field]), "TASK_ROLES_ENDPOINT_CHANGED:" + lane)
            if lane == "codex":
                validate_tui_target(row, root_reader(provider, row), spec, anchor)
                require(session["session_id"] == result["thread_id"]
                        and all(session["transcript"][k] == anchor["journal"][k]
                                for k in ("path", "device", "inode")), "TASK_ROLES_TASK_JOURNAL_CHANGED")
            else:
                require(session["session_id"] == spec["clients"][lane]["session_id"],
                        "TASK_ROLES_EXECUTOR_SESSION_CHANGED")
        rows[lane] = row
        kernels[lane] = require_kernel_boundary(row["pid"],
            denied=binding["isolation"]["denied_probes"], writable=binding["isolation"]["write_probes"])
        kernels[lane]["socket_boundary"] = verify_kernel(row["pid"], boundary,
            writer=lane in ("codex", "codex-server"), physical_unlink_denied=lane == "codex-server")
    require(len({row["pid"] for row in rows.values()}) == 4, "TASK_ROLES_DISTINCT_PROCESSES_REQUIRED")
    # Finish with the same incarnations and original immutable launch records.
    for lane, row in rows.items():
        require(process(row["pid"]) == row and file_ref(launches[lane]["path"]) == launches[lane],
                "TASK_ROLES_CHANGED_DURING_READ:" + lane)
    require(task_startup_result(spec) == (result_ref, result, anchor)
            and not os.path.lexists(spec["stop_path"]), "TASK_ROLES_STARTUP_CHANGED_OR_STOPPED")
    additional = {}
    from upgrade_task_runtime_policy import enabled as policy_enabled, observe as observe_policy
    if policy_enabled(binding):
        additional['runtime_policy'] = observe_policy(
            [rows[lane] for lane in ('codex-server', 'claude_a', 'claude_b')], process=process)
    return dict(schema=SCHEMA, startup=startup_ref, startup_result=result_ref, anchor=result["anchor"],
        launches=launches, display=dict(pid=rows["codex"]["pid"], execution_owner=False),
        execution_server=anchor["server"], thread_id=result["thread_id"], kernels=kernels,
        input_operations=0, network_proven=False, broker_isolation_proven=False,
        descriptor_isolation_proven=False, **additional)
