"""Real entry/TaskOwner/observer readiness under explicit registration fixtures.

Only Driver, group_registration and schedule.status are doubles. The entry's
fork/waitpid, owner scope, process identities, observer, readiness publication
and external observe_launch are real. No native client, harness, model, permit
or release runs. This does not validate launch_plan or benchmark registration.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import cmux_daemon_identity as identity
from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_client_isolation import clean_child_environment
from upgrade_task_launcher import LAUNCH_PROCESS_FIELDS, execution_python, observe_launch
from upgrade_task_observer_protocol import socket_pin
from test_upgrade_task_observer_budget import fixture_driver
import test_upgrade_task_owner_scope as owner_scope_fixtures

KERNEL_PROCESS = identity.process

BOOTSTRAP = r'''
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

source, runtime_json, driver_json, campaign, intent = sys.argv[1:]
sys.path.insert(0, source)
from orchestration_common import json_ref
import upgrade_driver
import upgrade_benchmark
import upgrade_schedule
from upgrade_task_owner_entry import main

runtime_ref = json.loads(runtime_json)
driver_ref = json.loads(driver_json)
config = json_ref(driver_ref)
driver = SimpleNamespace(config=config, registration_ref=config['registration'],
                         _check_overall_deadline=lambda: None)
# Python-level argv supplies the original owner arguments. Darwin's actual OS
# argv stays the -c invocation and is pinned independently by the test parent.
sys.argv = [str(Path(source) / 'upgrade_task_owner_entry.py'),
    '--runtime', runtime_ref['path'], '--runtime-sha256', runtime_ref['sha256'],
    '--driver', driver_ref['path'], '--driver-sha256', driver_ref['sha256'],
    '--campaign', campaign, '--launcher-intent', intent]
with patch.object(upgrade_driver, 'Driver', return_value=driver), \
     patch.object(upgrade_benchmark, 'group_registration', return_value={'campaign_id': 'original'}), \
     patch.object(upgrade_schedule, 'status', return_value={'status': 'ACTIVE', 'active': None}):
    raise SystemExit(main())
'''


@unittest.skipUnless(sys.platform == 'darwin' and owner_scope_fixtures.SOURCE.is_dir()
                     and owner_scope_fixtures.PACKAGE.is_dir(),
                     'requires the original local macOS packages')
class LauncherOwnerScopeTests(unittest.TestCase):
    def setUp(self):
        # This test reads its real child process; test_all's ordinary-client
        # fixture is intended only for tests that do not inspect kernel peers.
        kernel = patch.object(identity, 'process', KERNEL_PROCESS)
        kernel.start(); self.addCleanup(kernel.stop)

    @classmethod
    def setUpClass(cls):
        owner_scope_fixtures.OwnerScopeTests.setUpClass()
        cls.fixture = owner_scope_fixtures.OwnerScopeTests.fixture

    @classmethod
    def tearDownClass(cls):
        owner_scope_fixtures.OwnerScopeTests.tearDownClass()

    def stop(self, process):
        path = Path(self.fixture.spec['stop_path'])
        if not path.exists():
            write_json(path, {'fixture': 'private test STOP; no signal or restart'}, exclusive=True)
        process.wait(timeout=20)

    def await_file(self, path, process, logs, *, seconds=25):
        deadline = time.monotonic() + seconds
        while not path.is_file():
            if process.poll() is not None or time.monotonic() >= deadline:
                evidence = '\n'.join(p.read_text(errors='replace') for p in logs)
                self.fail('owner did not publish ' + path.name + '\n' + evidence)
            time.sleep(.05)
        return file_ref(path)

    def test_real_entry_owner_observer_readiness_and_private_stop(self):
        f = self.fixture
        driver_ref = fixture_driver(Path(f.binding['tmp_root']) / 'benchmark', f.runtime_ref)
        campaign = Path(f.binding['campaign_root']) / 'original/campaign.json'
        root = Path(f.spec['adoption_path']).parent
        launch = root / 'task-owner-launch'
        launch.mkdir(mode=0o700, parents=True, exist_ok=False)
        source = Path(__file__).resolve().parent
        argv = [execution_python(f.runtime['python'])['path'], '-I', '-B', '-c', BOOTSTRAP, str(source),
                json.dumps(f.runtime_ref), json.dumps(driver_ref), str(campaign),
                str(launch / 'intent.json')]
        intent = write_json(launch / 'intent.json', {
            'schema': 'upgrade-task-owner-launch-intent-v1',
            'runtime': f.runtime_ref, 'startup': f.runtime['startup'], 'driver': driver_ref,
            'group': 'g0', 'campaign_path': str(campaign), 'argv': argv,
            'max_process_starts': 1, 'reconnect_allowed': False,
            'fixture_scope': 'registration metadata only; launch_plan not exercised',
        }, exclusive=True)
        stdout_path, stderr_path = launch / 'stdout.log', launch / 'stderr.log'
        with stdout_path.open('xb') as out, stderr_path.open('xb') as err:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                close_fds=True, start_new_session=True, cwd=f.spec['work_root'],
                env=clean_child_environment({}))
        self.addCleanup(self.stop, process)
        row = identity.process(process.pid)
        self.assertEqual(row['argv'], argv)
        process_ref = write_json(launch / 'process.json', {
            'intent': intent, 'process': {key: row[key] for key in LAUNCH_PROCESS_FIELDS},
        }, exclusive=True)
        ready_ref = self.await_file(root / 'task-owner/launch-ready.json', process,
                                   (stdout_path, stderr_path))
        result = observe_launch(f.runtime_ref, driver_ref, expected_group='g0')
        self.assertEqual(result['status'], 'ORIGINAL_TASK_OWNER_READY')
        self.assertEqual(result['input_operations'], 0)
        self.assertEqual(result['launcher'], process_ref)
        self.assertEqual(result['intent'], intent)
        self.assertEqual(result['ready'], ready_ref)
        owner = json_ref(result['owner'])
        child = identity.process(owner['process']['pid'])
        self.assertEqual(child['ppid'], process.pid)
        self.assertNotEqual(child['pid'], process.pid)
        self.assertEqual(child['argv'], argv)
        ready = json_ref(ready_ref)
        observer = json_ref(result['observer'])
        self.assertEqual(observer['owner'], owner['process'])
        self.assertEqual(observer['socket'], socket_pin(observer['socket']['path']))
        self.assertEqual(observer['schedule_budget']['driver'], driver_ref)
        self.assertEqual(observer['schedule_budget']['group'], 'g0')
        self.assertTrue(ready['credential_bound'])
        started = json_ref(ready['observer_started'])
        self.assertEqual(started['owner_pid'], child['pid'])
        pulse_ref = self.await_file(root / 'task-owner/observations/0000001.json', process,
                                   (stdout_path, stderr_path))
        pulse = json_ref(pulse_ref)
        self.assertEqual(pulse['result'], {
            'status': 'WAITING_ORIGINAL_GROUP_PERMIT', 'input_operations': 0})
        self.assertEqual(pulse['phase'], 'WAITING_ORIGINAL_SERVER')
        self.assertFalse((Path(f.runtime['state_root']) / 'startup').exists())
        self.assertFalse((root / 'task-lifecycle').exists())
        self.assertFalse((root / 'task-owner/connection-intent.json').exists())
        self.assertFalse(Path(f.spec['adoption_path']).exists())
        self.assertFalse(campaign.exists())

        self.stop(process)
        # Current owner main preserves STOP through its no-replay exception
        # path and exits 2. This is controlled shutdown, not model acceptance.
        self.assertEqual(process.returncode, 2)
        closed = json_ref(file_ref(root / 'task-owner/closed.json'))
        unknown = json_ref(file_ref(root / 'task-owner/unknown.json'))
        self.assertEqual(closed['status'], 'UNKNOWN_NO_REPLAY')
        self.assertFalse(closed['business_accepted'])
        self.assertFalse(closed['reconnect_allowed'])
        self.assertIn(unknown['error'], ('EXPERIMENT_STOP_REQUESTED', 'TASK_OWNER_OBSERVER_STOPPED'))
        self.assertFalse(unknown['retry_allowed'])
        terminal = json_ref(file_ref(Path(result['observer']['path']).parent / 'terminal.json'))
        if 'requests' in terminal:
            self.assertEqual(terminal['requests'], 0)
        with self.assertRaisesRegex(ContractError, 'EXPERIMENT_STOP_REQUESTED'):
            observe_launch(f.runtime_ref, driver_ref, expected_group='g0')
        with self.assertRaises(ProcessLookupError):
            os.kill(child['pid'], 0)
        self.assertEqual(stderr_path.read_bytes(), b'')
        output = [json.loads(line) for line in stdout_path.read_text().splitlines()]
        self.assertEqual(output[-1]['status'], 'TASK_OWNER_STOPPED_NO_REPLAY')
        self.assertFalse(output[-1]['business_accepted'])
        print(json.dumps({
            'test_scope': 'real entry/TaskOwner/observer; registration metadata fixtures',
            'entry': file_ref(source / 'upgrade_task_owner_entry.py'),
            'task_owner': file_ref(source / 'upgrade_task_owner.py'),
            'launcher': file_ref(source / 'upgrade_task_launcher.py'),
            'runtime': f.runtime_ref, 'ready': ready_ref,
            'actual_reaper': {key: row[key] for key in LAUNCH_PROCESS_FIELDS},
            'actual_owner': owner['process'], 'observer_socket': observer['socket'],
            'observed_status': result['status'], 'waiting_status': pulse['result']['status'],
            'private_stop_exit_code': process.returncode, 'closed': closed,
            'observer_terminal': terminal, 'input_operations': 0,
            'native_cli_launches': 0, 'launch_plan_validated': False,
            'benchmark_registration_validated': False, 'business_accepted': False,
        }, sort_keys=True), flush=True)


if __name__ == '__main__':
    unittest.main()
