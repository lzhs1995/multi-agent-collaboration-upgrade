"""Explicit consumers of controller observations in new isolated clients.

No function here converts a proxy observation into direct RPC peer evidence.
External controllers keep their own direct readers. Native Claude identity is
checked by the observer and again locally, without a fabricated Codex proof.
"""
import copy
import json
import os
from pathlib import Path
import time
import uuid

from orchestration_common import file_ref, json_ref, require
from upgrade_native_roots import configuration
from upgrade_task_network import OBSERVER_SCHEMA
from upgrade_task_observer_client import read
from upgrade_task_observer_protocol import PROCESS_FIELDS


def client_binding():
    binding, _ = configuration()
    if binding.get('task_model_network', {}).get('schema') != OBSERVER_SCHEMA:
        return None
    from upgrade_client_isolation import sandbox_check
    # The external publisher has direct metadata access. A confined lane may
    # not opt out using an environment flag or a missing ancestor record.
    if not sandbox_check(os.getpid(), binding['controller_root'], 'file-write-create'):
        return None
    import cmux_daemon_identity as identity
    launches = {}
    startup=file_ref(Path(binding['config_root'])/'startup-v2.json')
    for lane in ('claude_a', 'claude_b', 'codex-server'):
        path=Path(binding['state_root'])/'startup'/(lane+'.json')
        if lane=='codex-server' and not os.path.lexists(path):continue
        launch=json_ref(file_ref(path))
        require(launch.get('startup')==startup, 'TASK_OBSERVER_LOCAL_STARTUP_CHANGED')
        launches[lane]=launch
    rows = []
    pid = os.getpid()
    for _ in range(32):
        require(pid not in [r['pid'] for r in rows], 'TASK_OBSERVER_LOCAL_ANCESTRY_CYCLE')
        if pid <= 1:
            require(False, 'TASK_OBSERVER_LOCAL_EXECUTOR_REQUIRED')
        row = identity.process(pid)
        require(row['pid'] == pid, 'TASK_OBSERVER_LOCAL_PROCESS_CHANGED')
        rows.append(row)
        found = [lane for lane, launch in launches.items() if launch['pid'] == pid]
        if found:
            require(len(found) == 1 and len(rows) > 1
                    and row['birth'] == launches[found[0]]['birth']
                    and all(identity.process(r['pid']) == r for r in rows),
                    'TASK_OBSERVER_LOCAL_ANCESTRY_CHANGED')
            return binding
        pid = row['ppid']
    require(False, 'TASK_OBSERVER_LOCAL_ANCESTRY_LIMIT')


def observation(kind):
    binding = client_binding()
    if binding is None:
        return None
    import cmux_daemon_identity as identity
    return read(binding, kind, process=identity.process)


def tree_command(args):
    if tuple(args) != ('tree', '--all', '--json', '--id-format', 'both'):
        return None
    response = observation('tree')
    return None if response is None else json.dumps(response['observation']['tree'])


def caller_snapshot():
    response = observation('tree')
    if response is None:
        return None
    value = response['observation']
    lane = value['caller']['lane']
    # Server tools use the original task actor contract. A remote display is
    # not an authenticated foreground caller of its server's child process.
    require(lane in ('claude_a', 'claude_b'), 'TASK_OBSERVER_CLAUDE_CALLER_REQUIRED')
    session = value['sessions'][lane]
    ancestor = value['caller']['ancestry'][-1]
    import cmux_daemon_identity as identity
    from upgrade_process_inventory import terminal
    process = identity.process(ancestor['pid'])
    require({k:process[k] for k in PROCESS_FIELDS} == ancestor
            and process['pid'] == session['process']['pid']
            and process['birth'] == session['process']['birth'], 'TASK_OBSERVER_NATIVE_CALLER_CHANGED')
    env = dict(os.environ)
    for key, expected in (('CMUX_SURFACE_ID', session['surface_uuid']),
                          ('CMUX_WORKSPACE_ID', session['workspace_uuid'])):
        require(uuid.UUID(process['env'][key]) == uuid.UUID(expected)
                and uuid.UUID(env[key]) == uuid.UUID(expected), 'TASK_OBSERVER_NATIVE_SCOPE_CHANGED')
        env[key] = process['env'][key]
    matches = []
    for window in value['tree']['windows']:
        for ws in window['workspaces']:
            for pane in ws['panes']:
                for surface in pane['surfaces']:
                    if uuid.UUID(surface['id']) == uuid.UUID(session['surface_uuid']):
                        matches.append(dict(surface_ref=surface['ref'], surface_id=surface['id'],
                            workspace_ref=ws['ref'], workspace_id=ws['id'], pane_ref=pane['ref'],
                            pane_id=pane['id'], window_ref=window.get('ref'),
                            surface_type=surface['type'], dock_scope=surface.get('dock_scope'), tty=surface['tty']))
    require(len(matches) == 1 and terminal(process) == matches[0]['tty'] == session['tty']
            and identity.process(process['pid']) == process, 'TASK_OBSERVER_NATIVE_TERMINAL_CHANGED')
    # No fabricated foreground/daemon proof: the explicit proxy provenance
    # travels with the resolved identity instead of occupying that field.
    return (dict(caller=matches[0], identity_source=value['identity_source'],
                 task_observation=response['evidence']), value['tree'], env, None)


def task_proof(anchor, *, response=None):
    if response is None:
        response = observation('task')
    if response is None:
        return None
    value = response['observation']
    task = value['task']
    require(isinstance(task, dict) and json_ref(task['anchor']) == anchor
            and task['proof']['thread_id'] == anchor['thread_id']
            and task['proof']['socket'] == anchor['socket']
            and task['proof']['input_operations'] == 0
            and value['requester_direct_rpc_peer'] is False,
            'TASK_OBSERVER_ORIGINAL_TASK_CHANGED')
    return dict(copy.deepcopy(task['proof']), identity_source=value['identity_source'],
                task_observation=response['evidence'], requester_direct_rpc_peer=False)


class ObserverMetadata:
    """Fixed metadata projection; never an RPC connection or input client."""
    def __init__(self, response):
        self.received_at=time.monotonic()
        self.response = copy.deepcopy(response)
        self.anchor = json_ref(response['observation']['task']['anchor'])
        self.proof = task_proof(self.anchor, response=response)
        self.socket_identity = copy.deepcopy(self.anchor['socket'])
        self.used = []

    def require_binding(self, binding, anchor):
        require(time.monotonic()-self.received_at<=30, 'TASK_OBSERVER_METADATA_EXPIRED')
        require(anchor == self.anchor and binding['session_id'] == self.anchor['thread_id']
                and binding['transcript']['path'] == self.anchor['journal']['path'],
                'TASK_OBSERVER_METADATA_BINDING_CHANGED')
        # Reauthenticate every call against the original local lane; freshness
        # for actual input is checked by the original callback owner separately.
        original = client_binding()
        require(original is not None, 'TASK_OBSERVER_METADATA_CALLER_CHANGED')
        require(file_ref(self.response['evidence']['path']) == self.response['evidence'],
                'TASK_OBSERVER_METADATA_EVIDENCE_CHANGED')

    def request(self, method, params):
        require(time.monotonic()-self.received_at<=30, 'TASK_OBSERVER_METADATA_EXPIRED')
        thread = self.anchor['thread_id']
        expected = [('thread/read', dict(threadId=thread, includeTurns=False)),
                    ('thread/turns/list', dict(threadId=thread, limit=1,
                        sortDirection='desc', itemsView='notLoaded'))]
        require(len(self.used) < 2 and (method, params) == expected[len(self.used)],
                'TASK_OBSERVER_FIXED_METADATA_ONLY')
        self.used.append((method, copy.deepcopy(params)))
        p = self.proof
        if method == 'thread/read':
            return dict(thread=dict(id=thread, path=self.anchor['journal']['path'],
                canAcceptDirectInput=p['can_accept_direct_input'], status=dict(type=p['thread_status'])))
        return dict(data=[] if p['latest_turn_id'] is None else [dict(
            id=p['latest_turn_id'], status=p['latest_turn_status'])])


def metadata_client():
    response = observation('task')
    return None if response is None else ObserverMetadata(response)


def annotate_metadata(client, metadata):
    if type(client) is ObserverMetadata:
        metadata.update(metadata_source='original_controller_read_only_observation',
                        task_observation=client.response['evidence'],
                        requester_direct_rpc_peer=False, provider_error_details_observed=False)
    return metadata


def require_input_route(client, request):
    if type(client) is ObserverMetadata:
        # Both input methods must leave the read-only projection and enter the
        # original callback owner; descriptor validation occurs before intent.
        require(request['method'] in ('turn/start','turn/steer'), 'TASK_OBSERVER_CALLBACK_ROUTE_UNAVAILABLE')
