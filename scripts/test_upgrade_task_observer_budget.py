"""Full-schedule observation and real Unix transport; native runtime is a fixture."""
import copy
import json
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, write_json
from upgrade_task_observer import ReadOnlyObserver
from upgrade_task_observer_budget import SCHEMA, schedule_budget, verify_descriptor_budget


def fixture_driver(root, runtime_ref, *, timeout=None):
    groups = {f'g{i}':dict(runs=[dict(run_id=f'r{j}') for j in range(3 if i == 0 else 5)])
              for i in range(7)}
    registration = write_json(root/'registration.json', dict(root=str(root), groups=groups,
        contract=dict(run_timeout_seconds=1800)))
    runtimes = {g:(runtime_ref if g == 'g0' else dict(fixture_runtime=g)) for g in groups}
    request_value = dict(schema='upgrade-driver-request-v1', registration=registration,
        runtimes=runtimes, source={}, corpus={}, revision='fixture', native_authorizations=[])
    request = write_json(root/'request.json', request_value)
    config = dict(request_value, schema='upgrade-driver-config-v1', request=request, created_at='fixture')
    if timeout is not None:
        config.update(schema='upgrade-driver-config-v2', overall_deadline=dict(timeout_seconds=timeout))
    return write_json(root/'driver/config.json', config)


class ScheduledObserverTests(unittest.TestCase):
    def setUp(self):
        # Import native identity fixtures only for these transport tests. The
        # pure driver fixture is also used before private owner scope loads.
        import test_upgrade_task_observer_transport as transport
        self.f = transport.ObserverTransportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.driver_ref = fixture_driver(self.f.root/'benchmark', self.f.source.runtime_ref)

    def start(self):
        service = ReadOnlyObserver(self.f.source, driver_ref=self.driver_ref)
        service.start();self.addCleanup(service.stop)
        return service

    def test_same_endpoint_and_original_budget_cover_interleaved_rounds(self):
        service = self.start()
        value = json_ref(service.descriptor)
        self.assertEqual(value['schema'], SCHEMA)
        self.assertEqual(value['maximum_seconds'], 90900)
        self.assertEqual(value['maximum_requests'], 8192)
        self.assertEqual(value['schedule_budget']['original_runs'], 3)
        self.assertFalse(value['schedule_budget']['budget_renewal'])
        result = self.f.child("import cmux_daemon_identity as i;from upgrade_task_observer_client import read;"
                              "r=read(binding,'tree',process=i.process);print(json.dumps(r))")
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['descriptor'], service.descriptor)
        self.assertEqual(service.sequence, 1)
        with self.assertRaises(FileExistsError):
            ReadOnlyObserver(self.f.source, driver_ref=self.driver_ref)

    def test_live_halt_ends_service_but_historical_budget_stays_verifiable(self):
        service = self.start()
        value = json_ref(service.descriptor)
        write_json(self.f.root/'benchmark/schedule/halt.json', {'fixture': 'original halt'})
        service.thread.join(timeout=2)
        self.assertFalse(service.thread.is_alive())
        terminal = json.loads((service.root/'terminal.json').read_text())
        self.assertIn('ORIGINAL_SCHEDULE_HALTED', terminal['error'])
        self.assertEqual(verify_descriptor_budget(value, self.f.source.runtime_ref), value['schedule_budget'])
        with self.assertRaisesRegex(ContractError, 'SCHEDULE_HALTED'):
            ReadOnlyObserver(self.f.source, driver_ref=self.driver_ref)

    def test_descriptor_cannot_renew_or_override_registered_budget(self):
        service = self.start()
        original = json_ref(service.descriptor)
        for key in ('maximum_seconds', 'maximum_requests'):
            value = copy.deepcopy(original);value[key] += 1
            with self.assertRaisesRegex(ContractError, 'SCHEDULE_BUDGET_CHANGED'):
                verify_descriptor_budget(value, self.f.source.runtime_ref)
        with self.assertRaisesRegex(ContractError, 'SCHEDULE_OVERRIDE_REFUSED'):
            ReadOnlyObserver(self.f.source, driver_ref=self.driver_ref, seconds=90000)
        value = copy.deepcopy(original);value['schedule_budget']['budget_renewal'] = True
        with self.assertRaisesRegex(ContractError, 'SCHEDULE_BUDGET_CHANGED'):
            verify_descriptor_budget(value, self.f.source.runtime_ref)

    def test_original_overall_cap_is_kept_and_foreign_runtime_is_refused(self):
        capped = fixture_driver(self.f.root/'capped', self.f.source.runtime_ref, timeout=32400)
        value = schedule_budget(capped, self.f.source.runtime_ref)
        self.assertEqual(value['maximum_seconds'], 32400)
        self.assertFalse(value['model_time_extension'])
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_RUNTIME_REQUIRED'):
            schedule_budget(capped, {'fixture': 'another runtime'})


if __name__ == '__main__':
    unittest.main()
