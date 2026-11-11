"""Shared reader for bounded TASK startup adoption, without input authority.

The external publisher owns adoption.json. Confined clients only reread its
original inspector invocation, startup evidence and frozen runtime. Every
actual send separately authenticates live roles/STOP/intent through MAC.
This does not assert universal host isolation or successful business work.
"""
import os
from pathlib import Path
import uuid

from orchestration_common import file_ref, json_ref, read_ref, require, verify_tree, within
from upgrade_task_runtime_policy import enabled, verify as verify_policy
from upgrade_task_adoption_evidence import verify as verify_startup

SCHEMA = 'upgrade-live-adoption-task-v1'
INSPECTION = 'upgrade-live-inspection-task-v1'


def deployment(binding, value):
    """Check bounded policy and original inheritance evidence, not universal isolation."""
    require(enabled(binding), 'TASK_ADOPTION_RUNTIME_POLICY_REQUIRED')
    roles = value['task_roles']
    policy = roles['runtime_policy']
    rows = [r['process'] for r in policy['processes']]
    require(len(rows) == 3, 'TASK_ADOPTION_THREE_EXECUTION_POLICIES_REQUIRED')
    verify_policy(policy, rows)
    expected = [roles['execution_server'], value['sessions']['claude_a']['process'],
                value['sessions']['claude_b']['process']]
    for row, identity in zip(rows, expected):
        require(all(row[k] == identity[k] for k in ('pid', 'birth', 'executable', 'file_identity')),
                'TASK_ADOPTION_EXECUTION_POLICY_IDENTITY_CHANGED')
    for lane in ('codex', 'claude_a', 'claude_b'):
        launch = json_ref(value['launches'][lane])
        seal = launch['terminal_descriptors']
        terminal = seal['terminal']
        require(seal['schema'] == 'upgrade-task-launch-descriptors-v1'
                and seal['extra_fds_remaining'] == 0
                and isinstance(seal['closed_fds'], list)
                and all(type(fd) is int and fd >= 3 for fd in seal['closed_fds'])
                and len(terminal) == 3 and [r['fd'] for r in terminal] == [0, 1, 2]
                and len({(r['device'], r['inode'], r['rdev']) for r in terminal}) == 1,
                'TASK_ADOPTION_LAUNCH_DESCRIPTOR_SEAL_CHANGED')
    return policy


def validate_candidate(runtime_ref, runtime, spec, binding, proof, *, native, allow_stopped=False):
    require(enabled(binding) and spec['schema'] == 'upgrade-startup-task-v1',
            'TASK_ADOPTION_EXPLICIT_RUNTIME_REQUIRED')
    if not allow_stopped:
        require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
    require(proof.get('schema') == SCHEMA and proof.get('runtime') == runtime_ref
            and proof.get('startup') == runtime['startup'] and proof.get('verified') is True
            and proof.get('input_operations') == 0, 'TASK_ADOPTION_ORIGINAL_PROOF_CHANGED')
    control = Path(spec['adoption_path']).parent
    ref = proof['inspection']
    within(ref['path'], control/'observations')
    require(Path(ref['path']).name == 'result.json', 'TASK_ADOPTION_INSPECTION_PATH_CHANGED')
    observed = json_ref(ref)
    expected_argv = ['/usr/bin/sandbox-exec', '-f', spec['pins']['control_sandbox']['path'],
        runtime['python']['path'], '-I', '-B', str(Path(spec['controller'])/'scripts/mac_harness.py'),
        'experiment-inspect', '--startup', runtime['startup']['path'],
        '--startup-sha256', runtime['startup']['sha256']]
    require(observed['runtime'] == runtime_ref and observed['startup'] == runtime['startup']
            and observed['command'] == 'experiment-inspect' and observed['argv'] == expected_argv
            and type(observed['returncode']) is int and observed['returncode'] == 0
            and observed['input_operations'] == 0, 'TASK_ADOPTION_INSPECTOR_INVOCATION_CHANGED')
    value = observed['result']
    require(value['schema'] == INSPECTION and value['startup'] == runtime['startup']
            and value['ready'] is True and value['startup_ready'] is True
            and value['business_adoption_supported'] is True and value['errors'] == {}
            and value['stopped'] is False and value['input_operations'] == 0
            and value['sessions'] == proof['sessions'], 'TASK_ADOPTION_INSPECTION_NOT_READY')
    inspector = Path(spec['controller'])/'scripts/mac_upgrade_live_inspect.py'
    require(value['inspector'] == file_ref(inspector), 'TASK_ADOPTION_INSPECTOR_CHANGED')
    sessions = proof['sessions']
    require(set(sessions) == {'codex', 'claude_a', 'claude_b'}, 'TASK_ADOPTION_SESSIONS_CHANGED')
    protected = {uuid.UUID(x) for x in spec['protected_surface_uuids']}
    require(len({uuid.UUID(s['pane_uuid']) for s in sessions.values()}) == 3
            and not protected.intersection(uuid.UUID(s['surface_uuid']) for s in sessions.values()),
            'TASK_ADOPTION_PROTECTED_OR_REUSED_ENDPOINT')
    for lane, session in sessions.items():
        within(session['transcript']['path'], Path(spec['clients'][lane]['native_root'])/
               ('sessions' if lane == 'codex' else 'projects'))
    verify_startup(value['task_startup_evidence'], runtime['startup'], spec, binding, value, native=native)
    deployment(binding, value)
    require(file_ref(ref['path']) == ref, 'TASK_ADOPTION_INSPECTION_CHANGED_DURING_READ')
    if not allow_stopped:
        require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
    return proof


def runtime_for(binding):
    """Read the frozen runtime without importing an external controller."""
    require(enabled(binding), 'TASK_ADOPTION_RUNTIME_POLICY_REQUIRED')
    from upgrade_startup_contract import validate_startup
    startup = file_ref(Path(binding['config_root'])/'startup-v2.json')
    spec, checked = validate_startup(startup, controller=binding['controller_root'])
    require(checked == binding, 'TASK_ADOPTION_BINDING_CHANGED')
    runtime_ref = file_ref(Path(binding['controller_root']).parents[2]/'runtime-v2.json')
    runtime = json_ref(runtime_ref)
    require(runtime['version'] == 2 and runtime['execution_mode'] == 'live-isolated-v2'
            and runtime['production_adoption'] is False and runtime['startup'] == startup
            and runtime['context'] == binding['context'] and runtime['arm'] == binding['arm']
            and runtime['controller']['path'] == binding['controller_root']
            and runtime['state_root'] == binding['state_root']
            and runtime['config_root'] == binding['config_root'], 'TASK_ADOPTION_RUNTIME_CHANGED')
    verify_tree(runtime['controller'])
    verify_tree(runtime['source_controller'])
    return runtime_ref, runtime, spec


def check_request(request):
    """Isolated external candidate check; no file publication or live input."""
    require(set(request) == {'runtime', 'proof', 'allow_stopped'}
            and type(request['allow_stopped']) is bool, 'TASK_ADOPTION_CHECK_REQUEST_CHANGED')
    runtime = json_ref(request['runtime'])
    binding = json_ref(file_ref(Path(runtime['controller']['path'])/'upgrade-binding.json'))
    runtime_ref, checked, spec = runtime_for(binding)
    require(runtime_ref == request['runtime'] and checked == runtime,
            'TASK_ADOPTION_CHECK_RUNTIME_CHANGED')
    import cmux_native_delivery as native
    within(native.__file__, Path(binding['controller_root'])/'scripts')
    return validate_candidate(runtime_ref, runtime, spec, binding, request['proof'],
                              native=native, allow_stopped=request['allow_stopped'])


def read(binding, *, allow_stopped=False):
    """Private same-source reader; no dependency on the external publisher."""
    runtime_ref, runtime, spec = runtime_for(binding)
    ref = file_ref(spec['adoption_path'])
    require(Path(ref['path']) == Path(binding['evidence_root'])/'live-control/adoption.json',
            'TASK_ADOPTION_PUBLICATION_PATH_CHANGED')
    import cmux_native_delivery as native
    within(native.__file__, Path(binding['controller_root'])/'scripts')
    result = validate_candidate(runtime_ref, runtime, spec, binding, json_ref(ref),
                                native=native, allow_stopped=allow_stopped)
    require(file_ref(ref['path']) == ref, 'TASK_ADOPTION_PUBLICATION_CHANGED_DURING_READ')
    return result
