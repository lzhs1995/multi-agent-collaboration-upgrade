"""One external owner launch for the original TASK hold, never a model launch.

The existing driver and seven-group registration determine all paths. A durable
intent precedes the sole Popen. Missing/unknown results permit observation only.
The original controller still owns held clients, CAS, permits and release.
"""
import fcntl
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import time

from orchestration_common import (file_ref, json_ref, read_ref, require,
                                  verify_tree, write_json)
from upgrade_task_observer_protocol import PROCESS_FIELDS, socket_pin

LAUNCH_PROCESS_FIELDS = tuple(k for k in PROCESS_FIELDS if k != 'ppid') + ('argv',)


def process_row(pid):
    import cmux_daemon_identity as identity
    return identity.process(pid)


def execution_python(pin):
    """Pin the real binary of the original macOS framework Python, if present.

    Framework bin/python is a launcher that execs Resources/Python.app and
    changes argv[0]. Pin and invoke that exact adjacent binary up front, rather
    than accepting an arbitrary executable change after recording the PID.
    Non-framework interpreters keep their original pin.
    """
    read_ref(pin)
    path = Path(pin['path'])
    version = path.parent.parent
    if (sys.platform == 'darwin' and path.parent.name == 'bin'
            and version.parent.name == 'Versions'
            and version.parent.parent.name == 'Python.framework'):
        binary = version/'Resources/Python.app/Contents/MacOS/Python'
        require(binary.is_file() and not binary.is_symlink()
                and binary.resolve().is_relative_to(version.resolve())
                and os.access(binary, os.X_OK), 'TASK_LAUNCH_FRAMEWORK_PYTHON_MISSING')
        return file_ref(binary.resolve())
    return pin


def launch_plan(runtime_ref, driver_ref, *, credential_fd=None):
    """Validate the full original binding before consuming any launch intent."""
    from upgrade_live_runtime import validate_live_runtime
    from upgrade_live_control import external_publisher, held_launch
    from upgrade_startup_contract import TASK_SCHEMA
    from upgrade_task_startup import plan_value
    from upgrade_task_owner import advice_requirements
    from upgrade_driver import Driver
    from upgrade_benchmark import group_registration, runtime_observation
    import upgrade_schedule as schedule
    require(driver_ref is not None, 'TASK_LAUNCH_ORIGINAL_DRIVER_REQUIRED')
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime['startup'])
    require(spec['schema'] == TASK_SCHEMA and held_launch(runtime),
            'TASK_LAUNCH_ORIGINAL_HELD_TOPOLOGY_REQUIRED')
    external_publisher(spec)
    require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
    driver = Driver(driver_ref)
    driver._check_overall_deadline()
    state = schedule.status(driver.registration_ref)
    require(state['status'] not in ('HALTED', 'COMPLETE', 'FAILURE_PRESERVED'),
            'TASK_LAUNCH_ORIGINAL_SCHEDULE_TERMINAL')
    groups = [g for g, ref in driver.config['runtimes'].items() if ref == runtime_ref]
    require(len(groups) == 1, 'TASK_LAUNCH_ORIGINAL_RUNTIME_REQUIRED')
    group = groups[0]
    registered = group_registration(driver.registration_ref, group, runtime['context'], runtime['arm'])
    runtime_observation(driver.registration, runtime_ref, runtime)
    source = driver.config['source']
    verify_tree(source)
    source_root = Path(source['path'])
    require(source_root == Path(__file__).resolve().parent.parent,
            'TASK_LAUNCH_ORIGINAL_SOURCE_REQUIRED')
    read_ref(runtime['python'])
    binding = json_ref(file_ref(Path(spec['controller'])/'upgrade-binding.json'))
    from upgrade_task_network import OBSERVER_SCHEMA
    require(binding.get('task_model_network', {}).get('schema') == OBSERVER_SCHEMA,
            'TASK_LAUNCH_ORIGINAL_OBSERVER_CONTRACT_REQUIRED')
    campaign = Path(binding['campaign_root'])/registered['campaign_id']/'campaign.json'
    context = json_ref(runtime['context'])
    require(campaign == Path(context['paths']['campaigns'])/runtime['arm']/registered['campaign_id']/'campaign.json',
            'TASK_LAUNCH_ORIGINAL_CAMPAIGN_REQUIRED')
    task = plan_value(spec['task_startup'])
    producers, _ = advice_requirements(task)
    require((credential_fd is not None) == bool(producers), 'TASK_OWNER_EXACT_CREDENTIAL_PIPE_REQUIRED')
    if credential_fd is not None:
        require(type(credential_fd) is int and credential_fd >= 3
                and stat.S_ISFIFO(os.fstat(credential_fd).st_mode)
                and fcntl.fcntl(credential_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
                'TASK_OWNER_READ_ONLY_CREDENTIAL_PIPE_REQUIRED')
    root = Path(spec['adoption_path']).parent
    for path in (root/'task-owner-launch', root/'task-owner', root/'task-lifecycle',
                 root/'launch-request.json', root/'held-request.json',
                 Path(runtime['state_root'])/'startup/intent.json'):
        require(not os.path.lexists(path), 'TASK_LAUNCH_ORIGINAL_ATTEMPT_EXISTS_NO_REPLAY: '+path.name)
    return dict(schema='upgrade-task-owner-launch-plan-v1', runtime=runtime_ref,
        startup=runtime['startup'], driver=driver_ref, registration=driver.registration_ref,
        group=group, campaign_path=str(campaign), source=source, python=runtime['python'],
        execution_python=execution_python(runtime['python']),
        entry=file_ref(source_root/'scripts/upgrade_task_owner_entry.py'),
        work_root=spec['work_root'], stop_path=spec['stop_path'],
        root=str(root/'task-owner-launch'), credential_pipe_required=bool(producers))


def _live_record(ref, fields, *, process=process_row):
    value = json_ref(ref)
    row = process(value['process']['pid'])
    require({k:row[k] for k in fields} == value['process'], 'TASK_LAUNCH_PROCESS_CHANGED')
    return value, row


def observe_launch(runtime_ref, driver_ref, *, expected_group=None, process=process_row):
    """Fresh original process/socket evidence; no launch, reconnect or input."""
    runtime = json_ref(runtime_ref)
    spec = json_ref(runtime['startup'])
    root = Path(spec['adoption_path']).parent
    directory = root/'task-owner-launch'
    intent_ref = file_ref(directory/'intent.json')
    intent = json_ref(intent_ref)
    require(intent.get('schema') == 'upgrade-task-owner-launch-intent-v1'
            and intent['runtime'] == runtime_ref and intent['startup'] == runtime['startup']
            and intent['driver'] == driver_ref and intent['max_process_starts'] == 1
            and intent['reconnect_allowed'] is False,
            'TASK_LAUNCH_INTENT_CHANGED')
    require(expected_group is None or intent['group'] == expected_group, 'TASK_LAUNCH_GROUP_CHANGED')
    require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
    for parent in (directory, root/'task-owner', root/'task-lifecycle'):
        require(not any(os.path.lexists(parent/name) for name in ('unknown.json', 'closed.json')),
                'TASK_LAUNCH_CLOSED_OR_UNKNOWN_NO_REPLAY')
    launch_ref = file_ref(directory/'process.json')
    launched, launcher = _live_record(launch_ref, LAUNCH_PROCESS_FIELDS, process=process)
    require(launched['intent'] == intent_ref and launcher['argv'] == intent['argv'],
            'TASK_LAUNCH_PARENT_CHANGED')
    ready_ref = file_ref(root/'task-owner/launch-ready.json')
    ready = json_ref(ready_ref)
    owner_ref = file_ref(root/'task-owner/owner.json')
    owner, child = _live_record(owner_ref, PROCESS_FIELDS, process=process)
    require(ready.get('schema') == 'upgrade-task-owner-launch-ready-v1'
            and ready['intent'] == intent_ref and ready['launcher'] == launch_ref
            and ready['owner'] == owner_ref and ready['credential_bound'] is True
            and owner['runtime'] == runtime_ref and owner['driver'] == driver_ref
            and owner['campaign_path'] == intent['campaign_path']
            and owner['reconnect_allowed'] is False and owner['model_operations_by_operator'] == 0
            and child['ppid'] == launcher['pid'] and child['argv'] == intent['argv']
            and child['executable'] == launcher['executable']
            and child['file_identity'] == launcher['file_identity'],
            'TASK_LAUNCH_ORIGINAL_OWNER_CHANGED')
    from upgrade_task_observer_client import descriptor
    binding = json_ref(file_ref(Path(spec['controller'])/'upgrade-binding.json'))
    descriptor_ref, observer = descriptor(binding)
    started_ref = file_ref(Path(descriptor_ref['path']).parent/'started.json')
    started = json_ref(started_ref)
    require(ready['observer'] == descriptor_ref and ready['observer_started'] == started_ref
            and observer['owner'] == owner['process']
            and observer['schedule_budget']['driver'] == driver_ref
            and observer['schedule_budget']['group'] == intent['group']
            and observer['socket'] == socket_pin(observer['socket']['path'])
            and started['descriptor'] == descriptor_ref and started['owner_pid'] == child['pid']
            and not any(os.path.lexists(Path(descriptor_ref['path']).parent/name)
                        for name in ('closed.json', 'failure.json', 'terminal.json')),
            'TASK_LAUNCH_OBSERVER_NOT_READY')
    # Recheck after reading files; evidence does not guarantee future liveness.
    _live_record(launch_ref, LAUNCH_PROCESS_FIELDS, process=process)
    _live_record(owner_ref, PROCESS_FIELDS, process=process)
    require(not os.path.lexists(spec['stop_path'])
            and not os.path.lexists(directory/'unknown.json')
            and not os.path.lexists(Path(descriptor_ref['path']).parent/'terminal.json'),
            'TASK_LAUNCH_STOPPED_DURING_READ')
    return dict(status='ORIGINAL_TASK_OWNER_READY', intent=intent_ref, launcher=launch_ref,
                owner=owner_ref, ready=ready_ref, observer=descriptor_ref, input_operations=0)


def launch_owner(plan, *, credential_fd=None, timeout=20):
    """Consume one preflight plan; preserve every uncertain launch, never retry."""
    from upgrade_client_isolation import clean_child_environment
    require(type(timeout) in (int, float) and 0 < timeout <= 30, 'TASK_LAUNCH_TIMEOUT_INVALID')
    # Revalidate before creating any intent. The caller must not have held yet.
    require(launch_plan(plan['runtime'], plan['driver'], credential_fd=credential_fd) == plan,
            'TASK_LAUNCH_PLAN_CHANGED')
    directory = Path(plan['root'])
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    ref = plan['runtime']; driver = plan['driver']
    argv = [plan['execution_python']['path'], '-I', '-B', plan['entry']['path'],
            '--runtime', ref['path'], '--runtime-sha256', ref['sha256'],
            '--driver', driver['path'], '--driver-sha256', driver['sha256'],
            '--campaign', plan['campaign_path'], '--launcher-intent', str(directory/'intent.json')]
    if credential_fd is not None:
        argv += ['--credential-fd', str(credential_fd)]
    intent = write_json(directory/'intent.json', dict(plan,
        schema='upgrade-task-owner-launch-intent-v1', argv=argv,
        max_process_starts=1, reconnect_allowed=False), exclusive=True)
    child = None
    try:
        require(not os.path.lexists(plan['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
        try:
            with open(directory/'stdout.log', 'xb') as stdout, open(directory/'stderr.log', 'xb') as stderr:
                child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    cwd=plan['work_root'], env=clean_child_environment({}), close_fds=True,
                    pass_fds=() if credential_fd is None else (credential_fd,), start_new_session=True)
        finally:
            # The launch consumes this read end, including a failed Popen. The
            # owner alone reads it; the writer must close after its one key.
            if credential_fd is not None:
                os.close(credential_fd)
        row = process_row(child.pid)
        require(row['argv'] == argv
                and Path(row['executable']).resolve() == Path(plan['execution_python']['path']).resolve(),
                'TASK_LAUNCH_EXECUTABLE_CHANGED')
        write_json(directory/'process.json', dict(intent=intent,
            process={k:row[k] for k in LAUNCH_PROCESS_FIELDS}), exclusive=True)
        deadline = time.monotonic()+timeout
        ready = directory.parent/'task-owner/launch-ready.json'
        while True:
            require(child.poll() is None, 'TASK_LAUNCH_OWNER_EXITED_NO_REPLAY')
            require(not os.path.lexists(plan['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
            if ready.is_file():
                result = observe_launch(ref, driver, expected_group=plan['group'])
                write_json(directory/'result.json', result, exclusive=True)
                return result
            require(time.monotonic() < deadline, 'TASK_LAUNCH_READY_TIMEOUT_NO_REPLAY')
            time.sleep(.05)
    except BaseException as exc:
        write_json(directory/'unknown.json', dict(intent=intent, error_type=type(exc).__name__,
            error=str(exc), process_pid=None if child is None else child.pid,
            retry_allowed=False, clients_preserved=True), exclusive=True)
        raise


def read_launch_credential(owner, fd, *, timeout=10):
    """Bounded startup-only pipe read, interruptible by original STOP/UNKNOWN."""
    require(type(timeout) in (int, float) and 0 < timeout <= 10,
            'TASK_OWNER_CREDENTIAL_TIMEOUT_INVALID')
    data = bytearray()
    try:
        require(type(fd) is int and fd >= 3 and stat.S_ISFIFO(os.fstat(fd).st_mode)
                and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
                'TASK_OWNER_READ_ONLY_CREDENTIAL_PIPE_REQUIRED')
        # Only this newly inherited read end is consumed. Nonblocking reads
        # prevent a competing reader from turning select readiness into a hang.
        os.set_blocking(fd, False)
        deadline = time.monotonic()+timeout
        while True:
            owner._verify()
            remaining = deadline-time.monotonic()
            require(remaining > 0, 'TASK_OWNER_CREDENTIAL_EOF_TIMEOUT_NO_REPLAY')
            if not select.select([fd], [], [], min(.1, remaining))[0]:
                continue
            try:
                chunk = os.read(fd, 4097-len(data))
            except BlockingIOError:
                continue
            if not chunk:
                break
            data.extend(chunk)
            require(len(data) <= 4096, 'TASK_OWNER_CREDENTIAL_TOO_LARGE')
        owner._verify()
        try:
            value = data.decode('ascii').strip()
        except UnicodeDecodeError:
            raise ValueError('TASK_OWNER_CREDENTIAL_ENCODING_INVALID') from None
        require(value and not any(c.isspace() for c in value), 'TASK_OWNER_CREDENTIAL_FORMAT_INVALID')
        return value
    finally:
        data[:] = b'\x00'*len(data)
        os.close(fd)


def publish_launch_ready(owner, intent_path):
    """Child binds the original launcher after credentials and observer startup."""
    directory = owner.root.parent/'task-owner-launch'
    require(Path(intent_path) == directory/'intent.json', 'TASK_LAUNCH_INTENT_PATH_CHANGED')
    intent_ref = file_ref(intent_path); intent = json_ref(intent_ref)
    require(intent['runtime'] == owner.runtime_ref and intent['driver'] == owner.driver_ref
            and intent['campaign_path'] == str(owner.campaign_path) and intent['group'] == owner.group,
            'TASK_LAUNCH_OWNER_SCOPE_CHANGED')
    deadline = time.monotonic()+10
    started = Path(owner.observer.descriptor['path']).parent/'started.json'
    process_path = directory/'process.json'
    while not (process_path.is_file() and started.is_file()):
        owner._verify()
        require(time.monotonic() < deadline, 'TASK_LAUNCH_PARENT_PUBLICATION_TIMEOUT')
        time.sleep(.05)
    owner._verify()
    launch_ref = file_ref(process_path)
    launched, row = _live_record(launch_ref, LAUNCH_PROCESS_FIELDS, process=owner.process)
    require(launched['intent'] == intent_ref and row['pid'] == os.getppid()
            and row['argv'] == intent['argv'], 'TASK_LAUNCH_ORIGINAL_PARENT_REQUIRED')
    return write_json(owner.root/'launch-ready.json', dict(schema='upgrade-task-owner-launch-ready-v1',
        intent=intent_ref, launcher=launch_ref, owner=owner.owner, observer=owner.observer.descriptor,
        observer_started=file_ref(started), credential_bound=True), exclusive=True)
