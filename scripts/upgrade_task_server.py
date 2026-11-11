"""Launch the TASK server once, outside the terminal's existing Seatbelt.

The external release caller applies the complete server policy exactly once.
This module is not shipped to model-facing private controllers. It performs no
RPC initialization, task creation or model input; the original TUI does that.
"""
import os
from pathlib import Path
import subprocess
import time

from orchestration_common import file_ref, json_ref, require, utc_now, write_json
from upgrade_client_isolation import SANDBOX, clean_child_environment


def launch_plan(runtime_ref, registration_ref, group, request_ref, receipt_ref):
    from upgrade_live_runtime import validate_live_runtime
    from upgrade_live_control import external_publisher
    from upgrade_startup_contract import TASK_SCHEMA
    from upgrade_socket_boundary import binding_boundary
    from orchestration_permit import read_permit, require_running
    from upgrade_task_launcher import observe_launch
    from upgrade_driver import Driver
    import upgrade_schedule as schedule
    import cmux_daemon_identity as identity

    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime['startup'])
    require(spec.get('schema') == TASK_SCHEMA, 'TASK_SERVER_EXPLICIT_TOPOLOGY_REQUIRED')
    external_publisher(spec)
    require_running(registration_ref)
    require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
    state = schedule.status(registration_ref)
    active = state.get('active')
    require(state.get('status') == 'ACTIVE' and active and active['group'] == group,
            'TASK_SERVER_ORIGINAL_ACTIVE_PERMIT_REQUIRED')
    permit_ref, permit = read_permit(registration_ref, active['ordinal'], active=True)
    require(permit['runtime'] == runtime_ref, 'TASK_SERVER_PERMIT_RUNTIME_CHANGED')
    root = Path(spec['adoption_path']).parent
    startup = Path(runtime['state_root'])/'startup'
    require(request_ref == file_ref(root/'launch-request.json'), 'TASK_SERVER_RELEASE_REQUEST_CHANGED')
    request = json_ref(request_ref)
    require(request['runtime'] == runtime_ref and request['startup'] == runtime['startup']
            and request['held_release']['permit'] == permit['step']
            and request['held_release']['group'] == group
            and request['held_release']['receipt'] == receipt_ref,
            'TASK_SERVER_RELEASE_AUTHORITY_CHANGED')
    json_ref(receipt_ref)
    release_ref = file_ref(startup/'release.json')
    release = json_ref(release_ref)
    require(release.get('schema') == 'upgrade-client-release-v1'
            and release['startup'] == runtime['startup']
            and release['exclusion_receipt'] == receipt_ref
            and set(release['held']) == {'codex', 'claude_a', 'claude_b'},
            'TASK_SERVER_ORIGINAL_RELEASE_REQUIRED')
    lanes = {}
    for lane, held_ref in release['held'].items():
        require(held_ref == file_ref(startup/(lane+'-held.json')), 'TASK_SERVER_HELD_REF_CHANGED')
        held = json_ref(held_ref)
        launched = json_ref(held['launched'])
        require(held.get('schema') == 'upgrade-client-held-v1'
                and held['startup'] == runtime['startup'] and held['lane'] == lane
                and held['workspace_uuid'] == release['workspace_uuid']
                and launched['startup'] == runtime['startup'] and launched['lane'] == lane
                and launched['surface_uuid'] == held['surface_uuid']
                and launched['workspace_uuid'] == held['workspace_uuid']
                and request['held_release']['held'][lane] == dict(pid=launched['pid'],
                    birth=launched['birth'], surface_uuid=held['surface_uuid']),
                'TASK_SERVER_HELD_SCOPE_CHANGED')
        live = identity.process(launched['pid'])
        require(live['birth'] == launched['birth'], 'TASK_SERVER_HELD_PROCESS_CHANGED')
        lanes[lane] = launched
    owner = request['task_owner']
    owner_intent = json_ref(owner['intent'])
    driver = Driver(owner_intent['driver'])
    driver._check_overall_deadline()
    require(driver.registration_ref == registration_ref
            and driver.config['runtimes'].get(group) == runtime_ref
            and owner_intent['registration'] == registration_ref and owner_intent['group'] == group
            and observe_launch(runtime_ref, owner_intent['driver'], expected_group=group) == owner,
            'TASK_SERVER_ORIGINAL_OWNER_CHANGED')
    binding = json_ref(file_ref(Path(spec['controller'])/'upgrade-binding.json'))
    binding_boundary(binding, binary_pin=spec['pins']['codex'], fresh=True)
    for name in ('codex-server-intent.json', 'codex-server.json', 'codex-server-result.json', 'codex-server.log'):
        require(not os.path.lexists(startup/name), 'TASK_SERVER_ORIGINAL_ATTEMPT_NO_REPLAY')
    require(not os.path.lexists(spec['rpc']['path']), 'ORIGINAL_CONTROL_SOCKET_ALREADY_EXISTS')
    return runtime, spec, dict(runtime=runtime_ref, startup=runtime['startup'],
        registration=registration_ref, permit=permit_ref, group=group, release=release_ref,
        request=request_ref, receipt=receipt_ref, owner=owner, codex_lane=lanes['codex'])


def launch(runtime_ref, registration_ref, group, request_ref, receipt_ref):
    runtime, spec, authority = launch_plan(runtime_ref, registration_ref, group, request_ref, receipt_ref)
    from orchestration_permit import require_running
    import cmux_daemon_identity as identity
    startup = Path(runtime['state_root'])/'startup'
    argv = [str(SANDBOX), '-f', spec['pins']['codex_server_sandbox']['path'],
            *spec['rpc'].get('launch_argv', spec['rpc']['argv'])]
    env = clean_child_environment(spec['clients']['codex']['environment'])
    env.update(spec['rpc'].get('launch_environment', {}))
    # These are endpoint metadata from the exact held lane, not caller identity.
    env.update(CMUX_SURFACE_ID=authority['codex_lane']['surface_uuid'],
               CMUX_WORKSPACE_ID=authority['codex_lane']['workspace_uuid'])
    intent = write_json(startup/'codex-server-intent.json', dict(
        schema='upgrade-server-intent-v2', startup=runtime['startup'], created_at=utc_now(),
        argv=spec['rpc']['argv'], launch_argv=argv, external_release=authority,
        input_operations=0), exclusive=True)
    child = None
    try:
        require_running(registration_ref)
        require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
        with open(os.devnull, 'rb') as null_input, (startup/'codex-server.log').open('xb') as log:
            child = subprocess.Popen(argv, stdin=null_input, stdout=log, stderr=subprocess.STDOUT,
                cwd=spec['work_root'], env=env, close_fds=True, start_new_session=True, umask=0o077)
        row = identity.process(child.pid, validate_argv=False)
        write_json(startup/'codex-server.json', dict(schema='upgrade-server-launch-v2',
            startup=runtime['startup'], created_at=utc_now(), pid=row['pid'], birth=row['birth'],
            argv=spec['rpc']['argv'], surface_uuid=authority['codex_lane']['surface_uuid'],
            workspace_uuid=authority['codex_lane']['workspace_uuid'], input_operations=0), exclusive=True)
        deadline = time.monotonic()+20
        socket = Path(spec['rpc']['path'])
        while child.poll() is None and not socket.is_socket() and time.monotonic() < deadline:
            require_running(registration_ref)
            require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
            time.sleep(.05)
        require(socket.is_socket() and child.poll() is None, 'TASK_SERVER_START_UNCONFIRMED_NO_REPLAY')
        live = identity.process(child.pid)
        require(live['birth'] == row['birth'] and live['executable'] == spec['pins']['codex']['path']
                and live['argv'] == spec['rpc']['argv'], 'TASK_SERVER_NATIVE_EXEC_CHANGED')
        require_running(registration_ref)
        require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
    except BaseException as exc:
        write_json(startup/'codex-server-result.json', dict(schema='upgrade-server-result-v2',
            startup=runtime['startup'], observed_at=utc_now(), status='UNKNOWN', intent=intent,
            pid=None if child is None else child.pid, returncode=None if child is None else child.poll(),
            error_type=type(exc).__name__, error=str(exc), input_operations=0), exclusive=True)
        raise
    return write_json(startup/'codex-server-result.json', dict(schema='upgrade-server-result-v2',
        startup=runtime['startup'], observed_at=utc_now(), status='SOCKET_CREATED', intent=intent,
        pid=child.pid, returncode=None, input_operations=0), exclusive=True)
