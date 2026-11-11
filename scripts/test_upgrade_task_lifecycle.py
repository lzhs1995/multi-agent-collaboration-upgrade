"""Actual observer thread/Unix connection and readiness SQLite/service.

Startup identity, creation and adoption are explicit fixtures. No model or
production task launches. Campaign handoff uses the existing runtime fixture.
"""
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_task_lifecycle import TaskLifecycle
from upgrade_task_network import OBSERVER_SCHEMA
import test_upgrade_task_ready_actor as ready
import test_upgrade_task_observer_transport as observer


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.o = observer.ObserverTransportTests(); self.addCleanup(self.o.doCleanups);self.o.setUp()
        original_process = observer.identity.process
        self.f = ready.ReadyActorTests(); self.addCleanup(self.f.doCleanups);self.f.setUp()
        # The readiness fixture uses PID999 for its fake display. All observer
        # parent/descendant and transport peer reads remain real kernel reads.
        p = patch('cmux_daemon_identity.process', side_effect=lambda pid, **kw:
                  self.f.process.copy() if pid == 999 else original_process(pid, **kw))
        p.start(); self.addCleanup(p.stop)
        self.ref = self.o.source.runtime_ref
        self.client = self.f.f.client
        self.client.poll_recorded = lambda **k: self.f.queue.pop(0) if self.f.queue else None
        self.closes = []
        self.client.close = lambda: self.closes.append('closed')
        self.binding = {'task_model_network': {'schema': OBSERVER_SCHEMA}}
        self.patches = []
        for p in (
            patch('upgrade_live_runtime.validate_live_runtime', return_value=({}, self.f.runtime)),
            patch('upgrade_startup_contract.validate_startup', return_value=(self.f.spec, self.binding)),
            patch('upgrade_task_observer.start_observer', side_effect=lambda *a: self.o.start()),
            patch('upgrade_live_control.bootstrap_task_thread', return_value={'fixture': 'original creation'}),
        ):
            p.start(); self.addCleanup(p.stop)
        self.owner = TaskLifecycle(self.ref, self.client)
        self.addCleanup(self.owner.close)

    def started(self):
        self.owner.bootstrap()
        self.owner.attach_readiness()
        return self.owner

    def completed_ready(self):
        owner = self.started()
        self.f.enqueue('ready')
        owner.poll_readiness(seconds=.1, max_messages=1)
        actor = owner.readiness.actor
        output = actor.ledger.inspect('call-ready')['result']
        messages = [dict(method='item/completed', params=dict(
            threadId=ready.fixtures.SID, turnId=ready.fixtures.TURN,
            item=dict(id='call-ready', type='dynamicToolCall', namespace='upgrade_collaboration',
                tool='execute', arguments={'operation_id': 'ready'}, status='completed', **output))),
            dict(method='turn/completed', params=dict(threadId=ready.fixtures.SID,
                turn=dict(id=ready.fixtures.TURN, status='completed')))]
        for i, message in enumerate(messages):
            ref = write_json(self.f.f.root/f'completed-{i}.json', dict(direction='received', message=message,
                peer=dict(server=self.client.server, peer={k: self.client.server[k] for k in ('pid', 'uid')})))
            self.f.queue.append((message, ref))
        result = owner.poll_readiness(seconds=.1, max_messages=2)
        self.assertEqual(result['lifecycle_phase'], 'AWAITING_ADOPTION')
        return owner

    def test_observer_stays_alive_across_yields_then_original_resources_close(self):
        owner = self.started()
        result = owner.poll_readiness(seconds=.01, max_messages=1)
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertTrue(self.o.service.thread.is_alive())
        child = self.o.child("from upgrade_task_observer_client import read; import cmux_daemon_identity as i;"
                            "print(json.dumps(read(binding,'tree',process=i.process)['status']))")
        self.assertEqual((child.returncode, child.stderr), (0, ''))
        self.assertEqual(json.loads(child.stdout), 'OBSERVED')
        ledger = owner.readiness.actor.ledger
        closed = owner.close()
        self.assertTrue(closed['observer']['closed'])
        self.assertFalse(self.o.service.thread.is_alive())
        self.assertEqual(self.closes, ['closed'])
        self.assertIs(owner.close(), closed)
        with self.assertRaises(Exception):
            ledger.db.execute('SELECT 1')
        self.assertTrue((owner.root/'closed.json').exists())
        self.assertTrue(self.o.service.path.exists())

    def test_unresolved_readiness_cannot_adopt_or_attach_business(self):
        owner = self.started()
        self.f.enqueue('ready'); owner.poll_readiness(seconds=.1, max_messages=1)
        with self.assertRaisesRegex(ContractError, 'READINESS_REQUIRED'):
            owner.adopt()
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_ADOPTION_REQUIRED'):
            owner.attach_campaign({})
        owner.close()
        import sqlite3
        db = sqlite3.connect(self.f.startup['ledger'])
        try:
            self.assertEqual(db.execute('SELECT phase FROM calls').fetchone()[0], 'UNKNOWN')
        finally:
            db.close()
        self.assertEqual(len(self.f.sent), 1)

    def test_real_normal_completion_and_not_ready_retain_same_ledger(self):
        owner = self.completed_ready()
        ledger = owner.readiness.actor.ledger
        with patch('upgrade_live_control.control', return_value={'status': 'NOT_READY'}) as adopt:
            self.assertEqual(owner.adopt()['status'], 'NOT_READY')
        adopt.assert_called_once_with(self.ref, 'adopt')
        self.assertIs(owner.readiness.actor.ledger, ledger)
        self.assertFalse(owner.closed)
        self.assertTrue(self.o.service.thread.is_alive())
        with self.assertRaisesRegex(ContractError, 'READINESS_NOT_ACTIVE'):
            owner.poll_readiness()
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_ADOPTION_REQUIRED'):
            owner.attach_campaign({})
        self.assertFalse((owner.root/'campaign-intent.json').exists())

    def test_lost_bootstrap_result_closes_without_second_input_or_restart(self):
        with patch('upgrade_live_control.bootstrap_task_thread', side_effect=TimeoutError('original unknown')) as launch:
            with self.assertRaisesRegex(TimeoutError, 'original unknown'):
                self.owner.bootstrap()
            with self.assertRaisesRegex(ContractError, 'BOOTSTRAP_ALREADY_ATTEMPTED'):
                self.owner.bootstrap()
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(self.owner.closed)
        self.assertFalse(self.o.service.thread.is_alive())
        self.assertEqual(self.closes, ['closed'])
        self.assertFalse(json_ref(file_ref(self.owner.root/'unknown.json'))['retry_allowed'])
        with self.assertRaises(FileExistsError):
            TaskLifecycle(self.ref, self.client)

    def test_observer_failure_stops_original_owner_before_readiness_response(self):
        owner = self.started()
        self.f.enqueue('ready')
        self.o.service.stop()
        with self.assertRaisesRegex(ContractError, 'OBSERVER_UNAVAILABLE'):
            owner.poll_readiness(seconds=.1, max_messages=1)
        self.assertEqual(self.f.sent, [])
        self.assertTrue(owner.closed)
        self.assertEqual(self.closes, ['closed'])

    def test_foreign_execution_thread_cannot_close_original_resources(self):
        import threading
        errors = []
        def close():
            try: self.owner.close()
            except Exception as exc: errors.append(str(exc))
        thread = threading.Thread(target=close); thread.start(); thread.join(timeout=2)
        self.assertEqual(errors, ['TASK_LIFECYCLE_EXECUTION_OWNER_CHANGED'])
        self.assertFalse(self.owner.closed)
        self.assertTrue(self.o.service.thread.is_alive())
        self.assertFalse(self.closes)

    def test_campaign_handoff_uses_original_terminal_ledger_and_busy_ingress(self):
        from upgrade_task_actor import TaskActor, TaskService
        from upgrade_task_enrollment import TaskCampaignGate
        from upgrade_task_runtime import CampaignRuntime
        from upgrade_task_callback_contract import BUSY_SCHEMA
        owner = self.completed_ready()
        old = owner.readiness
        previous = old._normal_terminal()
        ledger = old.actor.ledger
        with patch('upgrade_live_control.control', return_value={'status': 'ADOPTED'}):
            owner.adopt()
        campaign_ref = write_json(self.f.f.root/'campaign.json', {'fixture': True})
        gate = object.__new__(TaskCampaignGate)
        gate.client = self.client; gate.ledger = ledger
        gate.owner_pid = owner.pid; gate.spec = self.f.spec
        gate.proof = write_json(self.f.f.root/'enrollment.json', {'fixture': True})
        gate.proof_path = Path(gate.proof['path'])
        gate.runner = SimpleNamespace(ref=campaign_ref)
        gate.campaign = {'task_controls': {'registration': self.f.startup['operations']}}
        write_json(Path(self.f.spec['controller'])/'upgrade-binding.json', self.binding)
        gate._observe = lambda: None  # Explicit external enrollment fixture.
        made = []
        def attach(runtime_ref, campaign, client, retained, **kwargs):
            self.assertIs(client, self.client); self.assertIs(retained, ledger)
            actor = TaskActor(client, retained, {'route': lambda p: None}, gate,
                self.f.f.directory/'campaign-actor-events', operation_scope={'left', 'right'})
            runtime = CampaignRuntime(TaskService(actor)); made.append(runtime)
            return runtime
        with patch('upgrade_live_control.campaign_task_runtime', side_effect=attach):
            owner.attach_campaign(campaign_ref)
        runtime = made[0]
        self.assertEqual(runtime.service._normal_terminal(), previous)
        self.assertEqual(ledger.inspect('call-ready')['phase'], 'NATIVE_COMPLETED')
        self.assertEqual(ledger.active_turn, ready.fixtures.TURN)
        self.assertTrue(runtime.callback_ingress.busy_enabled)
        self.assertEqual(json_ref(runtime.callback_ingress.proof)['schema'], BUSY_SCHEMA)
        # No new input budget: retained runtime yields at the original normal
        # terminal without trying to read another completion from the server.
        with patch.object(runtime, '_remaining', return_value=['left', 'right']):
            result = owner.run(seconds=.1, max_pulses=1, max_new_turns=0)
        self.assertEqual(result['model_inputs_sent'], 0)
        self.assertFalse(owner.closed); self.assertTrue(self.o.service.thread.is_alive())
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_ADOPTION_REQUIRED'):
            owner.attach_campaign(campaign_ref)
        owner.close()
        self.assertTrue(runtime.closed); self.assertTrue(runtime.callback_ingress.closed)
        self.assertEqual(self.closes, ['closed'])

    def test_stop_before_bootstrap_never_calls_creation(self):
        Path(self.f.spec['stop_path']).touch()
        with patch('upgrade_live_control.bootstrap_task_thread') as creation:
            with self.assertRaisesRegex(ContractError, 'STOP_REQUESTED'):
                self.owner.bootstrap()
        creation.assert_not_called()
        self.assertFalse((self.owner.root/'bootstrap-intent.json').exists())
        self.assertTrue(self.owner.closed); self.assertEqual(self.closes, ['closed'])

    def test_legacy_or_nonobserver_contract_cannot_create_lifecycle_owner(self):
        for change in ('schema', 'network'):
            spec = dict(self.f.spec)
            binding = dict(self.binding)
            if change == 'schema': spec['schema'] = 'upgrade-startup-v2'
            else: binding['task_model_network'] = {'schema': 'upgrade-task-model-network-v1'}
            with patch('upgrade_startup_contract.validate_startup', return_value=(spec, binding)), \
                    patch('upgrade_task_observer.start_observer') as start:
                with self.assertRaisesRegex(ContractError, 'EXPLICIT_OBSERVER_CONTRACT_REQUIRED'):
                    TaskLifecycle(self.ref, self.client)
                start.assert_not_called()
        self.assertFalse(self.owner.closed); self.assertFalse(self.closes)

    def test_campaign_adoption_refusal_closes_without_new_turn(self):
        owner = self.completed_ready()
        with patch('upgrade_live_control.control', return_value={'status': 'ADOPTED'}):
            owner.adopt()
        import upgrade_task_observer as observer_service
        terminal_written = threading.Event()
        release_thread = threading.Event()
        original_write = observer_service.write_json
        def hold_terminal(path, *args, **kwargs):
            result = original_write(path, *args, **kwargs)
            if Path(path).name == 'terminal.json':
                terminal_written.set()
                release_thread.wait(10)
            return result
        try:
            with patch.object(observer_service, 'write_json', side_effect=hold_terminal), \
                    patch('upgrade_live_control.campaign_task_service',
                          side_effect=ContractError('original adoption refusal')) as gate:
                with self.assertRaisesRegex(ContractError, 'original adoption refusal'):
                    owner.attach_campaign({'fixture': True})
                self.assertTrue(terminal_written.wait(2))
                # Bounded stop reports a pending thread; it is not a hard join.
                self.assertTrue(owner.closed)
                self.assertTrue(self.o.service.stop_event.is_set())
                receipt = json_ref(file_ref(owner.root/'closed.json'))
                self.assertEqual(receipt['observer'], dict(
                    closed=False, thread_alive=True, input_operations=0))
                self.assertEqual(receipt['model_inputs_sent'], 0)
        finally:
            release_thread.set()
            self.o.service.thread.join(timeout=5)
        self.assertEqual(gate.call_count, 1)
        self.assertFalse(self.o.service.thread.is_alive())
        # Eventual exit does not rewrite the first close receipt as synchronous.
        self.assertEqual(json_ref(file_ref(owner.root/'closed.json')), receipt)
        self.assertEqual(self.closes, ['closed'])
        self.assertFalse((owner.root/'campaign-result.json').exists())


if __name__ == '__main__': unittest.main()
