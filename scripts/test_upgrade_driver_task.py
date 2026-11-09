"""Driver/retained-owner integration; synthetic native startup, no live launch."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import cmux_daemon_identity as identity
from cmux_daemon_identity import process as native_process
from orchestration_common import file_ref, json_ref, write_json
import test_upgrade_driver as legacy
import upgrade_driver as driver
import upgrade_task_owner_contract as owner_module
from upgrade_startup_contract import TASK_SCHEMA


class TaskDriverTests(unittest.TestCase):
    def setUp(self):
        self.f = legacy.DriverTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.driver = self.f.driver
        self.ref = self.f.case.runtimes['pilot-B']
        self.runtime = json_ref(self.ref)
        self.spec = json_ref(self.runtime['startup'])
        # Read-only fixture view selects the new branch; original runtime
        # refs remain fixed and every native action is the explicit old double.
        original_json = driver.json_ref
        reader = patch.object(driver, 'json_ref', side_effect=lambda ref, *args:
            dict(original_json(ref, *args), schema=TASK_SCHEMA)
            if ref == self.runtime['startup'] else original_json(ref, *args))
        reader.start(); self.addCleanup(reader.stop)
        self.row = dict(pid=123456, ppid=123450, birth=[11, 22],
                        executable='/fixture/python', file_identity={'inode': 9})
        self.process = lambda pid: dict(self.row)
        original_observe = owner_module.observe_owner
        observe = patch.object(owner_module, 'observe_owner', side_effect=lambda *args, **kwargs:
            original_observe(*args, **kwargs, process=self.process))
        observe.start(); self.addCleanup(observe.stop)
        self.campaign_ref = self.f.case.campaigns['pilot-B']
        self.campaign_path = Path(self.campaign_ref['path'])
        self.control_root = Path(self.spec['adoption_path']).parent
        self.f.held = True
        self.f.held_spawn('pilot-B')
        self.assertEqual(self.driver.tick()['status'], 'PERMIT_ISSUED')
        self.f.owner_release('pilot-B')
        self.assertEqual(self.driver.tick()['status'], 'HELD_RELEASE_OBSERVED')

    def owner(self):
        return write_json(self.control_root/'task-owner/owner.json', dict(
            schema='upgrade-task-external-owner-v1', runtime=self.ref,
            driver=self.driver.ref,
            campaign_path=str(self.campaign_path), process=self.row,
            reconnect_allowed=False, model_operations_by_operator=0), exclusive=True)

    def adopted(self):
        write_json(self.spec['adoption_path'], {'fixture': 'original adoption'}, exclusive=True)
        result = self.driver.tick()
        self.assertEqual(result['status'], 'WAITING_ORIGINAL_TASK_CAMPAIGN_ATTACH')
        self.campaign_ref = self.f.case.campaigns['pilot-B']
        return result

    def attached(self):
        campaign = json_ref(self.campaign_ref)
        campaign['client_isolation'] = file_ref(self.spec['adoption_path'])
        self.campaign_ref = write_json(self.campaign_path, campaign)
        self.f.case.campaigns['pilot-B'] = self.campaign_ref
        lifecycle = write_json(self.control_root/'task-lifecycle/owner.json', dict(
            schema='upgrade-task-lifecycle-v1', runtime=self.ref,
            startup=self.runtime['startup'], owner_pid=self.row['pid'],
            server={'fixture': 'original server'}, reconnect_allowed=False), exclusive=True)
        ledger = dict(path='/fixture/ledger', device=1, inode=2)
        enrollment = write_json(self.control_root/'fixture-enrollment.json', dict(
            schema='upgrade-task-campaign-enrollment-v1', campaign=self.campaign_ref,
            runtime=self.ref, startup=self.runtime['startup'],
            adoption=campaign['client_isolation'], server={'fixture': 'original server'},
            controller_pid=self.row['pid'], ledger=ledger), exclusive=True)
        service = write_json(self.control_root/'fixture-service.json', dict(
            schema='upgrade-task-runtime-owner-v1', campaign=self.campaign_ref,
            enrollment=enrollment, owner_pid=self.row['pid'], ledger=ledger), exclusive=True)
        return write_json(self.control_root/'task-lifecycle/campaign-result.json', dict(
            owner=lifecycle, runtime_owner=service, operation_budget_reset=False,
            model_inputs_sent=0), exclusive=True)

    def test_missing_owner_waits_without_competing_adoption_or_bootstrap(self):
        self.assertEqual(self.driver.tick()['status'], 'WAITING_ORIGINAL_TASK_OWNER')
        self.assertEqual(self.f.actions, [('held-observe', 'pilot-B')])
        self.assertFalse(Path(self.spec['adoption_path']).exists())

    def test_failed_server_without_origin_halts_before_waiting_for_adoption(self):
        self.owner()
        startup = Path(self.runtime['state_root'])/'startup'
        failed = write_json(startup/'codex-server-result.json', dict(
            schema='upgrade-server-result-v2', startup=self.runtime['startup'],
            status='UNKNOWN', returncode=71, input_operations=0), exclusive=True)
        self.assertFalse((startup/'codex-origin.json').exists())
        result = self.driver.tick()
        self.assertEqual((result['status'], result['classification']), ('HALTED', 'UNKNOWN'))
        self.assertIn('ORIGINAL_SERVER_FAILED', json_ref(result['halt'])['reason'])
        self.assertEqual(file_ref(failed['path']), failed)
        self.assertEqual(self.f.actions, [('held-observe', 'pilot-B')])
        self.assertFalse(Path(self.spec['adoption_path']).exists())

    def test_original_attachment_drives_closeout_without_legacy_bootstrap(self):
        ref = self.owner()
        self.assertEqual(self.driver.tick()['status'], 'WAITING_ORIGINAL_TASK_ADOPTION')
        self.assertEqual(json_ref(file_ref(self.driver.root/'groups/pilot-B/task-owner.json'))['owner'], ref)
        self.adopted()
        self.attached()
        self.assertEqual(self.driver.tick()['status'], 'WAITING_ORIGINAL_SUPERVISOR')
        self.f.finish(1)
        self.assertEqual(self.driver.tick()['status'], 'CLOSED')
        self.assertEqual(self.driver.tick()['step']['run_id'], 'pilot-02')
        self.assertEqual(self.driver.tick()['status'], 'WAITING_ORIGINAL_SUPERVISOR')
        self.assertEqual(self.f.actions, [('held-observe', 'pilot-B'), ('freeze', 'pilot-B')])
        self.assertFalse((self.driver.root/'actions/pilot-B-bootstrap').exists())

    def test_replaced_owner_preserves_halt_without_another_input(self):
        ref = self.owner()
        self.driver.tick()
        changed = dict(json_ref(ref), model_operations_by_operator=1)
        write_json(ref['path'], changed)
        result = self.driver.tick()
        self.assertEqual((result['status'], result['classification']), ('HALTED', 'UNKNOWN'))
        self.assertIn('ORIGINAL_OWNER_CHANGED', json_ref(result['halt'])['reason'])
        self.assertEqual(self.f.actions, [('held-observe', 'pilot-B')])

    def test_pid_reuse_cannot_masquerade_as_original_live_owner(self):
        self.owner(); self.driver.tick()
        self.row['birth'] = [99, 99]
        result = self.driver.tick()
        self.assertEqual(result['classification'], 'UNKNOWN')
        self.assertIn('OWNER_PROCESS_CHANGED', json_ref(result['halt'])['reason'])

    def test_unknown_owner_is_terminal_and_not_restarted(self):
        self.owner(); self.driver.tick()
        write_json(self.control_root/'task-owner/unknown.json', {'error': 'lost original response'})
        result = self.driver.tick()
        self.assertEqual(result['classification'], 'UNKNOWN')
        self.assertIn('CLOSED_NO_REPLAY', json_ref(result['halt'])['reason'])
        self.assertEqual(self.f.actions, [('held-observe', 'pilot-B')])

    def assert_owner_fault_preserved(self, marker, owner_ref):
        result = self.driver.tick()
        self.assertEqual((result['status'], result['classification']), ('HALTED', 'UNKNOWN'))
        halt_ref = result['halt']
        self.assertIn(marker, json_ref(halt_ref)['reason'])
        self.assertEqual(file_ref(owner_ref['path']), owner_ref)
        self.assertEqual(self.driver.tick()['status'], 'HALTED')
        self.assertEqual(file_ref(halt_ref['path']), halt_ref)
        self.assertEqual(self.f.actions, [('held-observe', 'pilot-B')])
        self.assertFalse(Path(self.spec['adoption_path']).exists())

    def test_closed_owner_does_not_require_a_still_live_process(self):
        ref = self.owner(); self.driver.tick()
        closed = write_json(self.control_root/'task-owner/closed.json',
                            {'status': 'UNKNOWN_NO_REPLAY'}, exclusive=True)
        self.process = unittest.mock.Mock(side_effect=identity.ProcessExited('gone'))
        self.assert_owner_fault_preserved('OWNER_CLOSED_NO_REPLAY', ref)
        self.process.assert_not_called()
        self.assertEqual(file_ref(closed['path']), closed)

    def test_absent_owner_without_closeout_halts_without_relaunch(self):
        ref = self.owner(); self.driver.tick()
        self.process = unittest.mock.Mock(side_effect=identity.ProcessExited('gone'))
        self.assert_owner_fault_preserved('OWNER_EXITED_NO_REPLAY', ref)

    def test_owner_exit_during_read_is_preserved(self):
        ref = self.owner(); self.driver.tick()
        self.process = unittest.mock.Mock(side_effect=[dict(self.row), identity.ProcessExited('gone')])
        self.assert_owner_fault_preserved('OWNER_EXITED_NO_REPLAY', ref)

    def test_unreadable_identity_is_not_reported_as_process_exit(self):
        ref = self.owner(); self.driver.tick()
        self.process = unittest.mock.Mock(side_effect=identity.IdentityError('inaccessible'))
        self.assert_owner_fault_preserved('OWNER_IDENTITY_UNAVAILABLE_NO_REPLAY', ref)

    @unittest.skipUnless(sys.platform == 'darwin', 'real Darwin kernel reader')
    def test_reaped_native_process_produces_original_driver_halt(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(timeout=10)
        self.row['pid'] = child.pid
        ref = self.owner()
        # Keep the real reader captured before OfflineResult installs its
        # per-test invoking-client fixture. Only this owned, reaped PID is read.
        self.process = native_process
        self.assert_owner_fault_preserved('OWNER_EXITED_NO_REPLAY', ref)

    def test_foreign_attachment_cannot_count_as_bootstrapped(self):
        self.owner(); self.adopted()
        ref = self.attached()
        value = dict(json_ref(ref), operation_budget_reset=True)
        write_json(ref['path'], value)
        result = self.driver.tick()
        self.assertEqual(result['classification'], 'UNKNOWN')
        self.assertIn('ATTACHMENT_CHANGED', json_ref(result['halt'])['reason'])

    def test_owner_wait_still_obeys_original_startup_watchdog(self):
        self.owner()
        with patch.object(self.driver, '_clock', return_value={'seconds': 900}):
            result = self.driver.tick()
        self.assertEqual((result['status'], result['classification']), ('HALTED', 'TIMEOUT'))
        self.assertEqual(self.f.actions, [('held-observe', 'pilot-B')])


if __name__ == '__main__':
    unittest.main()
