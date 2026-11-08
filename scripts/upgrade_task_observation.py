"""Fresh read-only observations for the original explicitly isolated TASK.

This source has no actor, ledger, input method or response-writer capability.
It reads the actual cmux tree and, when requested, the original task RPC on a
separate metadata connection. Returned evidence is a controller observation,
not the querying agent's direct kernel connection to that RPC server.
"""
import copy
import os
from pathlib import Path
import sys
import uuid

from orchestration_common import file_ref, json_ref, require
from upgrade_rpc_peer import kernel_peer
from upgrade_task_observer_protocol import OBSERVATION as SCHEMA, allowed_kinds, PROCESS_FIELDS


def caller(connection, launches, process):
    """Find the original execution ancestor using the connected kernel PID."""
    peer = kernel_peer(connection)
    require(peer['uid'] == os.geteuid(), 'TASK_OBSERVER_FOREIGN_UID')
    rows, seen, pid, selected = [], set(), peer["pid"], None
    for _ in range(32):
        require(pid > 1 and pid not in seen, "TASK_OBSERVER_CALLER_ANCESTRY_INVALID")
        seen.add(pid)
        row = process(pid)
        require(row["pid"] == pid, "TASK_OBSERVER_CALLER_PROCESS_CHANGED")
        rows.append(row)
        matches = [lane for lane, launch in launches.items() if launch["pid"] == pid]
        if matches:
            require(len(matches) == 1 and len(rows) > 1, "TASK_OBSERVER_TOOL_DESCENDANT_REQUIRED")
            selected = matches[0]
            require(row["birth"] == launches[selected]["birth"], "TASK_OBSERVER_ORIGINAL_CLIENT_CHANGED")
            break
        pid = row["ppid"]
    require(selected is not None, "TASK_OBSERVER_FOREIGN_CALLER")
    require(kernel_peer(connection) == peer and all(process(r["pid"]) == r for r in rows),
            "TASK_OBSERVER_CALLER_CHANGED_DURING_READ")
    return dict(peer=peer, lane=selected, ancestry=[{k:r[k] for k in PROCESS_FIELDS} for r in rows])


def workspace_tree(tree, launches):
    """Only the original three terminal endpoints, with metadata and no text."""
    expected = {str(uuid.UUID(row["surface_uuid"])): row for row in launches.values()}
    require(len(expected) == 3, "TASK_OBSERVER_THREE_ENDPOINTS_REQUIRED")
    windows, found, workspaces = [], set(), set()
    for win in tree.get("windows", []):
        kept_ws = []
        for ws in win.get("workspaces", []):
            panes = []
            for pane in ws.get("panes", []):
                surfaces = []
                for surface in pane.get("surfaces", []):
                    sid = str(uuid.UUID(surface["id"]))
                    if sid not in expected:
                        continue
                    require(sid not in found and surface.get("type") == "terminal"
                            and surface.get("dock_scope") != "global" and surface.get("tty")
                            and uuid.UUID(ws["id"]) == uuid.UUID(expected[sid]["workspace_uuid"]),
                            "TASK_OBSERVER_ORIGINAL_ENDPOINT_CHANGED")
                    found.add(sid); workspaces.add(str(uuid.UUID(ws["id"])))
                    surfaces.append({k:surface[k] for k in ("id", "ref", "type", "tty", "dock_scope") if k in surface})
                if surfaces:
                    panes.append(dict(id=pane["id"], ref=pane["ref"], surfaces=surfaces))
            if panes:
                kept_ws.append(dict(id=ws["id"], ref=ws["ref"], panes=panes))
        if kept_ws:
            windows.append(dict(workspaces=kept_ws, **{k:win[k] for k in ('id','ref') if k in win}))
    require(found == set(expected) and len(workspaces) == 1, "TASK_OBSERVER_ENDPOINTS_MISSING_OR_MOVED")
    return dict(windows=windows)


class ObservationSource:
    def __init__(self, runtime_ref):
        from upgrade_live_runtime import validate_live_runtime
        from upgrade_startup_contract import TASK_SCHEMA, validate_startup
        from upgrade_task_network import OBSERVER_SCHEMA
        from upgrade_live_control import external_publisher
        _, self.runtime = validate_live_runtime(runtime_ref)
        self.runtime_ref = copy.deepcopy(runtime_ref)
        self.spec, self.binding = validate_startup(self.runtime["startup"])
        require(self.spec["schema"] == TASK_SCHEMA
                and self.binding.get("task_model_network", {}).get("schema") == OBSERVER_SCHEMA,
                "TASK_OBSERVER_EXPLICIT_CONTRACT_REQUIRED")
        external_publisher(self.spec)
        # Load the frozen generated readers before starting the thread. Never
        # swap sys.modules or mutate sys.path inside a concurrent read.
        from orchestration_campaign import controller_modules
        require(str(Path(self.runtime['controller']['path'])/'scripts') in sys.path,
                'TASK_OBSERVER_FROZEN_IMPORT_SCOPE_REQUIRED')
        _, self.native, _, identity = controller_modules(self.runtime)
        self.process = identity.process
        self.owner_pid = os.getpid()
        owner = self.process(self.owner_pid)
        self.owner = {k:owner[k] for k in PROCESS_FIELDS}

    def verify(self):
        from upgrade_live_runtime import validate_live_runtime
        from upgrade_startup_contract import validate_startup
        from upgrade_live_control import external_publisher
        owner = self.process(self.owner_pid)
        require(os.getpid() == self.owner_pid
                and {k:owner[k] for k in PROCESS_FIELDS} == self.owner,
                "TASK_OBSERVER_OWNER_CHANGED")
        require(validate_live_runtime(self.runtime_ref)[1] == self.runtime
                and validate_startup(self.runtime["startup"]) == (self.spec, self.binding),
                "TASK_OBSERVER_ORIGINAL_RUNTIME_CHANGED")
        external_publisher(self.spec)
        require(not os.path.lexists(self.spec["stop_path"]), "EXPERIMENT_STOP_REQUESTED")

    def read(self, connection, kind):
        require(kind in allowed_kinds(self.binding), "TASK_OBSERVER_READ_KIND_REFUSED")
        startup_descriptors = kind == 'startup'
        if startup_descriptors:
            kind = 'tree'
        self.verify()
        native = self.native
        from upgrade_cmux_inventory import ReadOnlyCmux
        process_root = native.private_roots.process_root
        from orchestration_common import strict_json
        root = Path(self.binding["state_root"]) / "startup"
        refs = {lane:file_ref(root/(lane+".json")) for lane in ("codex", "claude_a", "claude_b")}
        server_path = root/'codex-server.json'
        if os.path.lexists(server_path):
            refs['codex-server'] = file_ref(server_path)
        require(kind == 'tree' or 'codex-server' in refs, 'TASK_OBSERVER_ORIGINAL_SERVER_REQUIRED')
        launches = {lane:json_ref(ref) for lane,ref in refs.items()}
        require(all(v.get("startup") == self.runtime["startup"] for v in launches.values()),
                "TASK_OBSERVER_ORIGINAL_LAUNCH_REQUIRED")
        callers = {k:v for k,v in launches.items() if k != "codex"}
        who = caller(connection, callers, self.process)
        bridge = ReadOnlyCmux(self.spec["pins"]["cmux"]["path"])
        tree = workspace_tree(strict_json(bridge._run("tree", "--all", "--json", "--id-format", "both")),
                              {k:v for k,v in launches.items() if k != "codex-server"})
        rows, sessions = {}, {}
        # Early tree hooks authenticate their actual native ancestor and the
        # allocated endpoints. They do not need another lane's journal or a
        # remote TUI which may still be waiting for the startup task.
        lanes = ("codex", "claude_a", "claude_b") if kind == 'task' else (
            () if who['lane'] == 'codex-server' else (who['lane'],))
        for lane in lanes:
            launch = launches[lane]
            endpoint = native._tree_row(bridge, launch["surface_uuid"])
            row = native._target_process(endpoint)
            provider = "codex" if lane == "codex" else "claude"
            require(row["pid"] == launch["pid"] and row["birth"] == launch["birth"]
                    and native._provider(row) == provider
                    and str(process_root(provider, row)) == self.spec["clients"][lane]["native_root"]
                    and native.private_process.terminal(row) == endpoint['tty']
                    and all(uuid.UUID(row['env'][key]) == uuid.UUID(launch[field])
                            for key, field in (('CMUX_SURFACE_ID','surface_uuid'),
                                               ('CMUX_WORKSPACE_ID','workspace_uuid'))),
                    "TASK_OBSERVER_ORIGINAL_PROCESS_CHANGED")
            rows[lane] = row
            sessions[lane] = dict(provider=provider, process=native._public_process(row),
                surface_uuid=launch["surface_uuid"], workspace_uuid=launch["workspace_uuid"],
                pane_uuid=endpoint["target_pane_uuid"], tty=endpoint["tty"],
                native_config_root=str(process_root(provider,row)))
            if lane != "codex" and kind == 'task':
                sid = native._live_session(row, provider, require_native=True)
                require(sid == self.spec["clients"][lane]["session_id"], "TASK_OBSERVER_EXECUTOR_SESSION_CHANGED")
                pin = native._pin_file(native._transcript_path(provider, sid, row), provider, sid)
                sessions[lane].update(session_id=sid, transcript=pin)
        server = None
        if kind == 'task' or who['lane'] == 'codex-server':
            server = self.process(launches["codex-server"]["pid"])
            require(server["birth"] == launches["codex-server"]["birth"]
                and server["executable"] == self.spec["pins"]["codex"]["path"]
                and server["argv"] == self.spec["rpc"]["argv"]
                and str(process_root("codex",server)) == self.spec["clients"]["codex"]["native_root"],
                "TASK_OBSERVER_ORIGINAL_SERVER_CHANGED")
            rows['codex-server'] = server
        task = None
        if kind == "task":
            from upgrade_task_readiness import task_startup_result
            from upgrade_task_thread import observe
            from upgrade_task_roles import verify_roles
            from upgrade_controller_rpc import rpc_type
            DaemonRPC = rpc_type(self.spec['controller'])
            _, result, anchor = task_startup_result(self.spec)
            sessions["codex"].update(session_id=result["thread_id"], transcript=anchor["journal"])
            roles = verify_roles(self.runtime["startup"], self.spec, self.binding, sessions,
                                 process=self.process, root_reader=process_root)
            with DaemonRPC(anchor["socket"]["path"], timeout=8) as client:
                proof = observe(client, anchor, self.process)
            task = dict(anchor=result["anchor"], proof=proof, roles=roles)
        require(caller(connection, callers, self.process) == who
                and all(self.process(row["pid"]) == row for row in rows.values())
                and all(file_ref(ref["path"]) == ref for ref in refs.values()),
                "TASK_OBSERVER_CHANGED_DURING_READ")
        fresh = ReadOnlyCmux(self.spec["pins"]["cmux"]["path"])
        require(workspace_tree(strict_json(fresh._run("tree", "--all", "--json", "--id-format", "both")),
                {k:v for k,v in launches.items() if k != "codex-server"}) == tree,
                "TASK_OBSERVER_TREE_CHANGED_DURING_READ")
        descriptors = {}
        if startup_descriptors:
            from upgrade_task_native_fds import observe as observe_descriptors
            descriptors['requester_descriptors'] = observe_descriptors(connection, who,
                self.binding['task_model_network']['observer'], self.process)
            from upgrade_task_runtime_policy import enabled, observe as observe_policy
            if enabled(self.binding):
                descriptors['runtime_policy'] = observe_policy(who['ancestry'], process=self.process)
        self.verify()
        return dict(schema=SCHEMA, runtime=self.runtime_ref, startup=self.runtime["startup"],
            caller=who, tree=tree, task=task, sessions=sessions,
            input_operations=0, adoption_proven=False,
            identity_source="original_controller_read_only_observation",
            requester_direct_rpc_peer=False, **descriptors)
