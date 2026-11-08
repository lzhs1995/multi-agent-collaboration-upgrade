"""Read the original TASK startup evidence before any adoption decision.

Both the external inspector and generated private reader use this code. It
rechecks original native readiness and hook receipts, the named journal and
the separate display/server identities. It grants no input or publication
authority and does not turn startup evidence into complete broker isolation.
"""
from pathlib import Path
import uuid

from orchestration_common import file_ref, json_ref, require
from upgrade_task_fd_contract import enabled as descriptors_enabled
from upgrade_task_readiness import task_startup_result, task_readiness
from upgrade_task_thread import journal_evidence
from upgrade_readiness_evidence import readiness_evidence

SCHEMA = 'upgrade-task-adoption-startup-evidence-v1'
LANES = ('codex', 'claude_a', 'claude_b')


def collect(startup, spec, binding, value, *, native):
    """Re-read fixed original evidence; never query RPC or execute a hook.

    The caller must authenticate the inspector/runtime separately. This
    function is also useful after a client exits: stored observations are
    historical evidence, not an assertion that its process is still alive.
    """
    require(spec.get('schema') == 'upgrade-startup-task-v1'
            and descriptors_enabled(binding)
            and spec['task_startup'] == binding['task_startup']
            and spec['controller'] == binding['controller_root'],
            'TASK_ADOPTION_EXPLICIT_DESCRIPTOR_CONTRACT_REQUIRED')
    require(value.get('schema') == 'upgrade-live-inspection-task-v1'
            and value['startup'] == startup and value['errors'] == {}
            and value['stopped'] is False and value['input_operations'] == 0
            and set(value['sessions']) == set(value['readiness']) == set(LANES),
            'TASK_ADOPTION_STARTUP_INSPECTION_CHANGED')
    root = Path(binding['state_root'])/'startup'
    refs = {lane: file_ref(root/(lane+'.json')) for lane in (*LANES, 'codex-server')}
    require(value['launches'] == refs, 'TASK_ADOPTION_ORIGINAL_LAUNCHES_CHANGED')
    launches = {lane: json_ref(ref) for lane, ref in refs.items()}
    require(all(row['startup'] == startup for row in launches.values()),
            'TASK_ADOPTION_LAUNCH_STARTUP_CHANGED')
    result_ref, result, anchor = task_startup_result(spec)
    roles = value['task_roles']
    sessions = value['sessions']
    require(roles['schema'] == 'upgrade-task-live-roles-v1'
            and roles['startup'] == startup and roles['startup_result'] == result_ref
            and roles['anchor'] == result['anchor'] and roles['launches'] == refs
            and roles['execution_server'] == anchor['server']
            and roles['thread_id'] == result['thread_id'] == anchor['thread_id']
            and roles['display'] == dict(pid=launches['codex']['pid'], execution_owner=False)
            and roles['input_operations'] == 0
            and set(roles['kernels']) == set(value['kernel']) == set(refs),
            'TASK_ADOPTION_ORIGINAL_ROLES_CHANGED')
    require(len({row['pid'] for row in launches.values()}) == 4
            and anchor['server']['pid'] == launches['codex-server']['pid']
            and anchor['server']['birth'] == launches['codex-server']['birth'],
            'TASK_ADOPTION_SERVER_DISPLAY_SUBSTITUTED')
    for lane, launch in launches.items():
        for kernel in (roles['kernels'][lane], value['kernel'][lane]):
            require(kernel['pid'] == launch['pid'] and kernel['kernel_checked'] is True,
                    'TASK_ADOPTION_ROLE_KERNEL_CHANGED:'+lane)
        require(roles['kernels'][lane] == value['kernel'][lane],
                'TASK_ADOPTION_ROLE_KERNEL_DISAGREEMENT:'+lane)
        if lane == 'codex-server':
            continue
        session = sessions[lane]
        require(session['process']['pid'] == launch['pid']
                and session['process']['birth'] == launch['birth']
                and session['provider'] == ('codex' if lane == 'codex' else 'claude')
                and session['native_config_root'] == spec['clients'][lane]['native_root']
                and all(uuid.UUID(session[k]) == uuid.UUID(launch[k])
                        for k in ('surface_uuid', 'workspace_uuid')),
                'TASK_ADOPTION_ORIGINAL_SESSION_CHANGED:'+lane)
        require(session['session_id'] == (result['thread_id'] if lane == 'codex'
                else spec['clients'][lane]['session_id']), 'TASK_ADOPTION_SESSION_ID_CHANGED:'+lane)
    codex = sessions['codex']
    require(len({uuid.UUID(s['workspace_uuid']) for s in sessions.values()}) == 1
            and len({uuid.UUID(s['surface_uuid']) for s in sessions.values()}) == 3
            and len({s['session_id'] for s in sessions.values()}) == 3,
            'TASK_ADOPTION_ENDPOINTS_NOT_DISTINCT')
    require(all(codex['transcript'][k] == anchor['journal'][k] for k in ('path', 'device', 'inode')),
            'TASK_ADOPTION_ORIGINAL_JOURNAL_CHANGED')
    capability = value['codex_capability']
    thread = capability['task_thread']
    peer = thread['server_peer']
    require(capability['thread_id'] == thread['thread_id'] == anchor['thread_id']
            and capability['path'] == anchor['journal']['path']
            and capability['can_accept_direct_input'] is True
            and capability['methods'] == ['thread/read', 'thread/turns/list']
            and capability['input_operations'] == 0
            and thread['schema'] == 'upgrade-task-thread-read-proof-v1'
            and thread['socket'] == anchor['socket'] == capability['socket']
            and thread['journal'] == journal_evidence(anchor)
            and thread['can_accept_direct_input'] is True and thread['input_operations'] == 0
            and thread['foreground_authentication'] is False and thread['caller_authentication'] is False
            and thread['enrollment_proven'] is False
            and peer['schema'] == 'upgrade-task-rpc-peer-v1' and peer['server'] == anchor['server']
            and peer['peer'] == {k: anchor['server'][k] for k in ('pid', 'uid')}
            and peer['foreground_authentication'] is False and peer['enrollment_proven'] is False
            and peer['input_operations'] == 0,
            'TASK_ADOPTION_ORIGINAL_RPC_EVIDENCE_CHANGED')
    descriptor = thread['descriptor']
    require(type(descriptor['fd']) is int and descriptor['fd'] >= 0
            and descriptor['access'] in ('w', 'u')
            and all(descriptor[k] == anchor['journal'][k] for k in ('path', 'device', 'inode')),
            'TASK_ADOPTION_ORIGINAL_WRITER_CHANGED')
    original = {}
    for lane, session in sessions.items():
        fresh = (task_readiness(native, session, spec, startup, binding, launches) if lane == 'codex'
                 else readiness_evidence(native, session, lane, spec, startup, binding, launches))
        require(fresh == value['readiness'][lane]
                and fresh['model'] == session['model']
                and fresh['model_record'] == session['model_record'],
                'TASK_ADOPTION_ORIGINAL_READINESS_CHANGED:'+lane)
        original[lane] = fresh
    # The readiness readers authenticate native invocation and both automatic
    # events; their observer reader in turn requires the original FD evidence.
    require(all(file_ref(ref['path']) == ref for ref in refs.values())
            and task_startup_result(spec) == (result_ref, result, anchor),
            'TASK_ADOPTION_STARTUP_CHANGED_DURING_READ')
    return dict(schema=SCHEMA, startup=startup, startup_result=result_ref,
                anchor=result['anchor'], launches=refs, readiness=original,
                server=anchor['server'], journal=anchor['journal'],
                input_operations=0, live_processes_proven=False,
                business_adoption_proven=False, whole_client_isolation_proven=False)


def verify(expected, startup, spec, binding, value, *, native):
    actual = collect(startup, spec, binding, value, native=native)
    require(actual == expected, 'TASK_ADOPTION_STARTUP_EVIDENCE_CHANGED')
    return actual
