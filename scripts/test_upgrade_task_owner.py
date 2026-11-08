"""Retained-owner sequencing using the actual lifecycle/SQLite/Unix fixture.

Only native model startup and adoption are doubles; no installed client launch.
"""
from pathlib import Path
from types import MethodType, SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_task_owner import TaskOwner
from upgrade_task_observer_protocol import PROCESS_FIELDS
from test_upgrade_task_lifecycle import LifecycleTests


class ServerStartupTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.owner = object.__new__(TaskOwner)
        self.owner.runtime = dict(state_root=str(self.root), startup={'original': 'startup'})
        self.owner.phase = 'WAITING_ORIGINAL_SERVER'

    def result(self, **changes):
        value = dict(schema='upgrade-server-result-v2',
                     startup=self.owner.runtime['startup'], status='SOCKET_CREATED')
        return write_json(self.root/'startup/codex-server-result.json', dict(value, **changes))

    def test_recorded_server_failure_is_not_hidden_by_missing_origin(self):
        ref = self.result(status='UNKNOWN', returncode=71)
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_SERVER_FAILED'):
            self.owner._connect()
        self.assertEqual(file_ref(ref['path']), ref)
        self.assertFalse((self.root/'startup/codex-origin.json').exists())

    def test_absent_or_successful_server_result_can_wait_for_origin(self):
        self.assertEqual(self.owner._connect()['status'], 'WAITING_ORIGINAL_SERVER')
        self.result()
        self.assertEqual(self.owner._connect()['status'], 'WAITING_ORIGINAL_SERVER')

    def test_other_startup_result_cannot_be_used_as_this_server(self):
        self.result(startup={'foreign': 'startup'})
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_STARTUP_CHANGED'):
            self.owner._connect()


class OwnerFlowTests(unittest.TestCase):
    def setUp(self):
        self.f = LifecycleTests(); self.addCleanup(self.f.doCleanups);self.f.setUp()
        # Use the existing real lifecycle; only the process-bootstrap factory
        # is bypassed to avoid launching real model clients in these tests.
        owner = object.__new__(TaskOwner)
        owner.runtime_ref = self.f.ref; owner.runtime = self.f.f.runtime
        owner.spec = self.f.f.spec; owner.pid = self.f.owner.pid
        owner.process = self.f.o.source.process
        row = owner.process(owner.pid)
        owner.owner_process = {k:row[k] for k in PROCESS_FIELDS}
        owner.root = self.f.f.f.root/'outer-owner'; owner.root.mkdir()
        owner.owner = write_json(owner.root/'owner.json', {'fixture': 'original owner'})
        owner.lifecycle = self.f.owner; owner.client = self.f.client
        owner.observer = self.f.o.service; owner.closed = False
        owner.phase = 'LIFECYCLE'; owner.sequence = owner.rpc_sequence = 0
        owner.campaign_path = self.f.f.f.root/'frozen/campaign.json'
        owner.advice_connections = None
        owner.group = 'fixture-group'
        owner._schedule = lambda: dict(status='ACTIVE', active=dict(group=owner.group, run_id='fixture-run'))
        self.owner = owner; self.addCleanup(lambda: owner.close() if not owner.closed else None)

    def test_bootstrap_yield_keeps_original_connection_then_attaches_readiness(self):
        self.assertEqual(self.owner.tick(), {'fixture': 'original creation'})
        self.assertEqual(self.f.owner.phase, 'WAITING_TUI')
        with patch.object(self.owner, '_attached', return_value=False):
            self.assertEqual(self.owner.tick()['status'], 'WAITING_ORIGINAL_TASK_DISPLAY')
        with patch.object(self.owner, '_attached', return_value=True):
            self.assertEqual(self.owner.tick()['status'], 'READINESS_ATTACHED')
        ledger = self.f.owner.readiness.actor.ledger
        result = self.owner.tick(seconds=.01)
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertIs(self.f.owner.readiness.actor.ledger, ledger)
        self.assertIs(self.owner.client, self.f.client)
        self.assertTrue(self.owner.observer.thread.is_alive())

    def test_adoption_then_missing_freeze_waits_without_reenrollment(self):
        self.f.completed_ready()
        with patch('upgrade_live_control.control', return_value={'status': 'ADOPTED'}):
            self.assertEqual(self.owner.tick()['status'], 'ADOPTED')
        ledger = self.f.owner.readiness.actor.ledger
        self.assertEqual(self.owner.tick()['status'], 'WAITING_ORIGINAL_CAMPAIGN_FREEZE')
        self.assertIs(self.f.owner.readiness.actor.ledger, ledger)
        self.assertFalse(self.owner.closed)
        self.assertFalse((self.f.owner.root/'campaign-intent.json').exists())

    def test_foreign_campaign_closes_original_owner_and_never_attaches(self):
        self.f.completed_ready()
        with patch('upgrade_live_control.control', return_value={'status': 'ADOPTED'}):
            self.owner.tick()
        write_json(self.owner.campaign_path, {'runtime': {'foreign': True}})
        with patch.object(self.f.owner, 'attach_campaign') as attach:
            with self.assertRaisesRegex(ContractError, 'FROZEN_CAMPAIGN_CHANGED'):
                self.owner.tick()
            attach.assert_not_called()
        self.assertTrue(self.owner.closed)
        self.assertEqual(json_ref(file_ref(self.owner.root/'closed.json'))['status'], 'UNKNOWN_NO_REPLAY')
        self.assertFalse(self.owner.observer.thread.is_alive())
        self.assertEqual(self.f.closes, ['closed'])

    def test_stop_before_any_new_input_closes_original_resources(self):
        Path(self.owner.spec['stop_path']).touch()
        with patch.object(self.f.owner, 'bootstrap') as bootstrap:
            with self.assertRaisesRegex(ContractError, 'STOP_REQUESTED'):
                self.owner.tick()
            bootstrap.assert_not_called()
        self.assertTrue(self.owner.closed)
        self.assertEqual(self.f.closes, ['closed'])

    def test_schedule_halt_before_bootstrap_closes_without_model_input(self):
        self.owner._schedule = lambda: dict(status='HALTED', active=None)
        with patch.object(self.f.owner, 'bootstrap') as bootstrap:
            self.assertEqual(self.owner.tick()['status'], 'ORIGINAL_SCHEDULE_HALTED')
            bootstrap.assert_not_called()
        self.assertFalse(self.owner.observer.thread.is_alive())
        self.assertEqual(self.f.closes, ['closed'])

    def test_other_group_permit_does_not_start_readiness_or_consume_operations(self):
        self.owner._schedule = lambda: dict(status='ACTIVE', active=dict(group='different-group'))
        with patch.object(self.f.owner, 'bootstrap') as bootstrap:
            self.assertEqual(self.owner.tick()['status'], 'WAITING_ORIGINAL_GROUP_PERMIT')
            bootstrap.assert_not_called()
        self.assertFalse(self.owner.closed)
        self.assertTrue(self.owner.observer.thread.is_alive())

    def test_original_overall_deadline_stops_owner_before_readiness(self):
        self.owner.driver_config = {'fixture': 'original driver'}
        self.owner.driver_ref = write_json(self.owner.root/'driver.json', self.owner.driver_config)
        def expired():
            raise ContractError('original overall deadline reached')
        self.owner.driver = SimpleNamespace(_check_overall_deadline=expired)
        self.owner._schedule = MethodType(TaskOwner._schedule, self.owner)
        with patch.object(self.f.owner, 'bootstrap') as bootstrap:
            with self.assertRaisesRegex(ContractError, 'overall deadline'):
                self.owner.tick()
            bootstrap.assert_not_called()
        self.assertTrue(self.owner.closed)
        self.assertFalse(self.owner.observer.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
