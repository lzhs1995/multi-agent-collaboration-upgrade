"""One external process retains a TASK connection from readiness to closeout.

The schedule driver owns permits, launch/release and campaign freezing. This
owner publishes adoption and serves only model-selected registered operations.
It never launches clients, issues permits, retries a connection or performs
business actions on behalf of the model. Separate groups need separate processes
because generated controller imports and their write audits are permanent.
"""
import importlib
import argparse
import json
import os
from pathlib import Path
import sys
import time

from orchestration_common import file_ref, json_ref, read_ref, require, write_json
from upgrade_task_observer_protocol import PROCESS_FIELDS
from upgrade_task_owner_contract import observe_owner, advice_requirements




def install_owner_scope(runtime_ref):
    """Explicit trusted entry before importing any private native module."""
    from upgrade_live_runtime import validate_live_runtime
    from upgrade_startup_contract import TASK_SCHEMA
    from upgrade_live_control import external_publisher
    _, runtime = validate_live_runtime(runtime_ref)
    spec = json_ref(runtime['startup'])
    require(spec['schema'] == TASK_SCHEMA, 'TASK_OWNER_EXPLICIT_TOPOLOGY_REQUIRED')
    external_publisher(spec)
    require(not os.path.lexists(spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
    # Materialization pins this future publication directory but need not have
    # created it. Only the already kernel-verified external publisher creates it.
    Path(spec['adoption_path']).parent.mkdir(mode=0o700, exist_ok=True)
    root = Path(runtime['controller']['path'])/'scripts'
    name = 'mac_upgrade_controller_scope'
    require(not any(k in sys.modules for k in ('cmux_native_delivery', 'cmux_bridge', name)),
            'TASK_OWNER_FRESH_PROCESS_REQUIRED')
    # Validate the exact member before importing its explicit entry. The
    # imported guard verifies the complete tree before enabling the audit.
    manifest = json_ref(runtime['release_manifest'])
    member = manifest['files']['scripts/'+name+'.py']
    read_ref(dict(path=str(root/(name+'.py')), bytes=member['bytes'], sha256=member['sha256']))
    sys.path.insert(0, str(root))
    module = importlib.import_module(name)
    require(Path(module.__file__).resolve() == root/(name+'.py'), 'TASK_OWNER_FOREIGN_SCOPE_IMPORT')
    module.install_scope(live=True, external_startup=runtime['startup'])
    return runtime, spec


class TaskOwner:
    """One retained lifecycle; tick is bounded observation or original work."""
    def __init__(self, runtime_ref, campaign_path, *, driver_ref, advice_connections=None):
        self.runtime_ref = runtime_ref
        self.runtime, self.spec = install_owner_scope(runtime_ref)
        from orchestration_campaign import controller_modules
        _, _, _, identity = controller_modules(self.runtime)
        self.process = identity.process
        self.pid = os.getpid()
        self.campaign_path = Path(campaign_path)
        campaign_root = Path(json_ref(file_ref(Path(self.spec['controller'])/'upgrade-binding.json'))['campaign_root'])
        require(self.campaign_path.is_absolute() and self.campaign_path == self.campaign_path.resolve()
                and self.campaign_path.name == 'campaign.json'
                and campaign_root in self.campaign_path.parents, 'TASK_OWNER_CAMPAIGN_PATH_CHANGED')
        from upgrade_driver import Driver
        from upgrade_benchmark import group_registration
        driver = Driver(driver_ref)
        groups = [g for g, ref in driver.config['runtimes'].items() if ref == runtime_ref]
        require(len(groups) == 1, 'TASK_OWNER_ORIGINAL_DRIVER_RUNTIME_REQUIRED')
        self.group = groups[0]
        self.registration_ref = driver.registration_ref
        self.driver_ref = driver_ref
        self.driver_config = driver.config
        self.driver = driver
        group = group_registration(self.registration_ref, self.group,
                                   self.runtime['context'], self.runtime['arm'])
        require(self.campaign_path == campaign_root/group['campaign_id']/'campaign.json',
                'TASK_OWNER_ORIGINAL_GROUP_CAMPAIGN_REQUIRED')
        self.root = Path(self.spec['adoption_path']).parent/'task-owner'
        self.root.mkdir(mode=0o700, exist_ok=False)
        row = self.process(self.pid)
        self.owner_process = {k:row[k] for k in PROCESS_FIELDS}
        self.owner = write_json(self.root/'owner.json', dict(schema='upgrade-task-external-owner-v1',
            runtime=runtime_ref, driver=driver_ref, campaign_path=str(self.campaign_path), process=self.owner_process,
            reconnect_allowed=False, model_operations_by_operator=0), exclusive=True)
        self.client = self.lifecycle = None
        self.phase = 'WAITING_ORIGINAL_SERVER'
        self.sequence = self.rpc_sequence = 0
        self.closed = False
        self.advice_connections = advice_connections
        # Start before release so the original Claude startup hooks already
        # have their bounded observation endpoint when native exec begins.
        from upgrade_task_observer import start_observer
        self.observer = start_observer(runtime_ref, driver_ref=driver_ref)

    def _verify(self):
        require(not self.closed and os.getpid() == self.pid, 'TASK_OWNER_CLOSED_OR_REPLACED')
        row = self.process(self.pid)
        require({k:row[k] for k in PROCESS_FIELDS} == self.owner_process,
                'TASK_OWNER_PROCESS_CHANGED')
        require(file_ref(self.root/'owner.json') == self.owner, 'TASK_OWNER_PROOF_CHANGED')
        require(not os.path.lexists(self.spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
        require(not os.path.lexists(self.root.parent/'task-owner-launch/unknown.json'),
                'TASK_OWNER_LAUNCH_UNKNOWN_NO_REPLAY')
        require(not self.observer.closed and self.observer.thread.is_alive(),
                'TASK_OWNER_OBSERVER_STOPPED')

    def _record(self, direction, message, peer):
        self.rpc_sequence += 1
        return write_json(self.root/'rpc'/f'{self.rpc_sequence:07d}.json',
                          dict(direction=direction, message=message, peer=peer), exclusive=True)

    def _schedule(self):
        from upgrade_schedule import status
        require(json_ref(self.driver_ref) == self.driver_config, 'TASK_OWNER_ORIGINAL_DRIVER_CHANGED')
        self.driver._check_overall_deadline()
        return status(self.registration_ref)

    def _connect(self):
        from upgrade_controller_rpc import rpc_type
        from upgrade_rpc_peer import server_pin
        from upgrade_task_rpc import task_rpc_type
        from upgrade_task_lifecycle import TaskLifecycle
        startup = Path(self.runtime['state_root'])/'startup'
        # Missing files are a wait; a changed recorded identity is a failure.
        names = ('release.json', 'codex-server.json', 'codex-server-result.json', 'codex-origin.json')
        if not all((startup/name).is_file() for name in names):
            return dict(status=self.phase, input_operations=0)
        rows = {name: json_ref(file_ref(startup/name)) for name in names}
        require(all(row.get('startup') == self.runtime['startup'] for row in rows.values()),
                'TASK_OWNER_ORIGINAL_STARTUP_CHANGED')
        require(rows['codex-server-result.json']['status'] == 'SOCKET_CREATED',
                'TASK_OWNER_ORIGINAL_SERVER_FAILED')
        server = rows['codex-server.json']; origin = rows['codex-origin.json']
        live = self.process(server['pid']); tui = self.process(origin['pid'])
        require(live['birth'] == server['birth'] and live['argv'] == self.spec['rpc']['argv']
                and live['executable'] == self.spec['pins']['codex']['path'],
                'TASK_OWNER_ORIGINAL_SERVER_CHANGED')
        if tui['birth'] == origin['birth'] and tui['executable'] != self.spec['pins']['codex']['path']:
            return dict(status='WAITING_ORIGINAL_TUI_EXEC', input_operations=0)
        require(tui['birth'] == origin['birth'] and tui['executable'] == self.spec['pins']['codex']['path'],
                'TASK_OWNER_ORIGINAL_TUI_CHANGED')
        pinned = server_pin(live, uid=os.geteuid())
        write_json(self.root/'connection-intent.json', dict(owner=self.owner, server=pinned,
            socket=self.spec['rpc']['path'], reconnect_allowed=False), exclusive=True)
        self.phase = 'CONNECTING'
        self.client = task_rpc_type(rpc_type(self.spec['controller']))(
            self.spec['rpc']['path'], pinned, self.process, record=self._record)
        self.lifecycle = TaskLifecycle(self.runtime_ref, self.client, observer=self.observer)
        self.phase = 'LIFECYCLE'
        return dict(status='ORIGINAL_CONNECTION_RETAINED', input_operations=0)

    def _attached(self):
        from upgrade_task_readiness import task_startup_result
        from upgrade_task_thread import validate_tui_target
        _, startup, anchor = task_startup_result(self.spec)
        launched = json_ref(file_ref(Path(self.runtime['state_root'])/'startup/codex.json'))
        row = self.process(launched['pid'])
        require(row['birth'] == launched['birth'], 'TASK_OWNER_DISPLAY_REPLACED')
        if row['executable'] != self.spec['pins']['codex']['path']:
            return False  # Original launcher has not execed the returned resume yet.
        validate_tui_target(row, self.spec['clients']['codex']['native_root'], self.spec, anchor)
        require(row['argv'][-1] == startup['thread_id'], 'TASK_OWNER_DISPLAY_THREAD_CHANGED')
        return True

    def tick(self, *, seconds=1):
        require(type(seconds) in (int, float) and 0 < seconds <= 30, 'TASK_OWNER_PULSE_BUDGET_INVALID')
        try:
            self._verify()
            state = self._schedule()
            if state['status'] in ('HALTED', 'COMPLETE', 'FAILURE_PRESERVED'):
                return self.close('ORIGINAL_SCHEDULE_'+state['status'])
            step = state['active']
            if step is None or step['group'] != self.group:
                return dict(status='WAITING_ORIGINAL_GROUP_PERMIT', input_operations=0)
            if self.lifecycle is None:
                return self._connect()
            lifecycle = self.lifecycle
            if lifecycle.phase == 'OBSERVER':
                return lifecycle.bootstrap()
            if lifecycle.phase == 'WAITING_TUI':
                if not self._attached():
                    return dict(status='WAITING_ORIGINAL_TASK_DISPLAY', input_operations=0)
                lifecycle.attach_readiness()
                return dict(status='READINESS_ATTACHED', input_operations=0)
            if lifecycle.phase == 'READINESS':
                return lifecycle.poll_readiness(seconds=min(1, seconds))
            if lifecycle.phase == 'AWAITING_ADOPTION':
                return lifecycle.adopt()
            if lifecycle.phase == 'ADOPTED':
                if not self.campaign_path.is_file():
                    return dict(status='WAITING_ORIGINAL_CAMPAIGN_FREEZE', input_operations=0)
                campaign_ref = file_ref(self.campaign_path)
                campaign = json_ref(campaign_ref)
                require(campaign.get('runtime') == self.runtime_ref,
                        'TASK_OWNER_FROZEN_CAMPAIGN_CHANGED')
                proof = lifecycle.attach_campaign(campaign_ref, advice_connections=self.advice_connections)
                return dict(status='CAMPAIGN_ATTACHED', owner=proof, input_operations=0)
            require(lifecycle.phase == 'CAMPAIGN', 'TASK_OWNER_UNKNOWN_PHASE')
            # The original permit controls which run may advance. A finished
            # run waits for the driver to close it and issue the next permit.
            campaign = lifecycle.campaign.gate.campaign
            require(campaign['registration'] == self.registration_ref
                    and campaign['session_group'] == self.group, 'TASK_OWNER_CAMPAIGN_SCHEDULE_CHANGED')
            if (Path(campaign['root'])/'runs'/step['run_id']/'finish.json').exists():
                return dict(status='WAITING_ORIGINAL_RUN_CLOSEOUT', input_operations=0)
            result = lifecycle.run(seconds=seconds)
            if lifecycle.closed:
                self.close('ORIGINAL_SCOPE_EXHAUSTED')
            return result
        except BaseException as exc:
            try:
                write_json(self.root/'unknown.json', dict(phase=self.phase,
                    error_type=type(exc).__name__, error=str(exc), retry_allowed=False), exclusive=True)
            finally:
                self.close('UNKNOWN_NO_REPLAY')
            raise

    def close(self, reason='OWNER_STOPPED'):
        require(os.getpid() == self.pid, 'TASK_OWNER_PROCESS_CHANGED')
        if self.closed:
            return json_ref(file_ref(self.root/'closed.json'))
        self.closed = True
        result = self.lifecycle.close() if self.lifecycle is not None else None
        if self.lifecycle is None and self.client is not None:
            self.client.close()
        if self.lifecycle is None:
            self.observer.stop()
        value = dict(status=reason, lifecycle=result, business_accepted=False, reconnect_allowed=False)
        write_json(self.root/'closed.json', value, exclusive=True)
        return value




def held_advice(plan, credential_fd, *, launch_owner=None):
    """Bind original per-run connections; the key enters only by inherited pipe."""
    from jev_advice_run import read_credential, registered_connection
    producers, by_run = advice_requirements(plan)
    require((credential_fd is not None) == bool(producers), 'TASK_OWNER_EXACT_CREDENTIAL_PIPE_REQUIRED')
    if not producers:
        return None
    if launch_owner is None:
        credential = read_credential(credential_fd)
    else:
        from upgrade_task_launcher import read_launch_credential
        credential = read_launch_credential(launch_owner, credential_fd)
    connections = {}
    try:
        for run, registration in by_run.items():
            if launch_owner is not None:
                launch_owner._verify()
            connections[run] = registered_connection(registration['transport_source'])
        return {name:dict(registration=ref, connection=connections[run], credential=credential)
                for name,(run,ref) in producers.items()}
    except BaseException:
        for connection in connections.values():
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--runtime-sha256', required=True)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--driver', required=True)
    parser.add_argument('--driver-sha256', required=True)
    parser.add_argument('--credential-fd', type=int)
    parser.add_argument('--launcher-intent')
    args = parser.parse_args(argv)
    owner = None
    held = None
    try:
        from orchestration_campaign import anchored_ref
        from upgrade_live_runtime import validate_live_runtime
        from upgrade_task_startup import plan_value
        ref = anchored_ref(args.runtime, args.runtime_sha256)
        _, runtime = validate_live_runtime(ref)
        spec = json_ref(runtime['startup'])
        plan = plan_value(spec['task_startup'])
        # The owner's exclusive directory must precede connection creation:
        # a previous owner/unknown cannot accidentally open another transport.
        driver_ref = anchored_ref(args.driver, args.driver_sha256)
        owner = TaskOwner(ref, args.campaign, driver_ref=driver_ref)
        held = held_advice(plan, args.credential_fd, launch_owner=owner if args.launcher_intent else None)
        owner.advice_connections = held
        if args.launcher_intent is not None:
            from upgrade_task_launcher import publish_launch_ready
            publish_launch_ready(owner, args.launcher_intent)
        while not owner.closed:
            began = time.monotonic()
            result = owner.tick(seconds=1)
            owner.sequence += 1
            write_json(owner.root/'observations'/f'{owner.sequence:07d}.json',
                dict(owner=owner.owner, result=result, phase=owner.lifecycle.phase if owner.lifecycle else owner.phase),
                exclusive=True)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            if not owner.closed:
                time.sleep(max(0, 1-(time.monotonic()-began)))
        return 0
    except BaseException as exc:
        print(json.dumps(dict(status='TASK_OWNER_STOPPED_NO_REPLAY', error_type=type(exc).__name__,
            error=str(exc), business_accepted=False), ensure_ascii=False), flush=True)
        return 2
    finally:
        if owner is not None and not owner.closed:
            owner.close()
        if held:
            connections = {id(v['connection']):v['connection'] for v in held.values() if v['connection'] is not None}
            for value in held.values():
                value.update(credential=None, connection=None)
            for connection in connections.values():
                close = getattr(connection, 'close', None)
                if callable(close):
                    close()


if __name__ == '__main__':
    raise SystemExit(main())
