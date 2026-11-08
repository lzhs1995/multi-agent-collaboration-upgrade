"""Read-only observation limits derived once from the original full schedule.

These limits grant no business attempt, permit or extra model time. The owner
and driver keep the original per-run and overall deadlines. Legacy observers
retain their 1800-second/2048-request contract; scheduled observers use a new
descriptor schema and the same endpoint for all original warm rounds.
"""
from pathlib import Path

from orchestration_common import json_ref, require
from upgrade_task_observer_protocol import SCHEMA as LEGACY_SCHEMA

SCHEMA = 'upgrade-task-observer-endpoint-v2'
BUDGET_SCHEMA = 'upgrade-task-observer-schedule-budget-v1'


def schedule_budget(driver_ref, runtime_ref):
    config = json_ref(driver_ref)
    require(config.get('schema') in ('upgrade-driver-config-v1', 'upgrade-driver-config-v2',
                                     'upgrade-driver-config-v3'), 'TASK_OBSERVER_DRIVER_SCHEMA_CHANGED')
    request = json_ref(config['request'])
    keys = ('registration', 'runtimes', 'source', 'corpus', 'revision', 'native_authorizations')
    require(request == dict(schema='upgrade-driver-request-v1', **{k:config[k] for k in keys}),
            'TASK_OBSERVER_ORIGINAL_DRIVER_REQUEST_CHANGED')
    registration = json_ref(config['registration'])
    root = Path(registration['root'])
    require(root.is_absolute() and root == root.resolve()
            and config['registration']['path'] == str(root/'registration.json')
            and driver_ref['path'] == str(root/'driver/config.json'), 'TASK_OBSERVER_DRIVER_LOCATION_CHANGED')
    groups = registration['groups']
    require(set(groups) == set(config['runtimes']), 'TASK_OBSERVER_DRIVER_GROUPS_CHANGED')
    matching = [g for g, ref in config['runtimes'].items() if ref == runtime_ref]
    require(len(matching) == 1, 'TASK_OBSERVER_ORIGINAL_RUNTIME_REQUIRED')
    group = matching[0]
    counts = {g:len(v['runs']) for g,v in groups.items()}
    require(len(groups) == 7 and sorted(counts.values()) == [3,5,5,5,5,5,5]
            and registration['contract']['run_timeout_seconds'] == 1800,
            'TASK_OBSERVER_ORIGINAL_33_RUN_SCHEDULE_REQUIRED')
    # Include intervening groups, each original 900s startup window, and one
    # bounded pre-release interval. This is an observation ceiling, never an
    # extension of the active run's 1800s or the driver's fixed overall cap.
    seconds = sum(counts.values()) * (1800 + 900) + 1800
    deadline = config.get('overall_deadline')
    if deadline is not None:
        require(type(deadline['timeout_seconds']) is int and deadline['timeout_seconds'] > 0,
                'TASK_OBSERVER_ORIGINAL_DEADLINE_CHANGED')
        seconds = min(seconds, deadline['timeout_seconds'])
    return dict(schema=BUDGET_SCHEMA, driver=driver_ref, registration=config['registration'],
                runtime=runtime_ref, group=group, original_runs=counts[group],
                maximum_seconds=seconds, maximum_requests=2048*(counts[group]+1),
                budget_renewal=False, model_time_extension=False)


def verify_descriptor_budget(value, runtime_ref):
    if value.get('schema') == LEGACY_SCHEMA:
        require('schedule_budget' not in value
                and type(value['maximum_seconds']) in (int,float) and 0 < value['maximum_seconds'] <= 1800
                and type(value['maximum_requests']) is int and 0 < value['maximum_requests'] <= 2048,
                'TASK_OBSERVER_LEGACY_BUDGET_CHANGED')
        return None
    require(value.get('schema') == SCHEMA, 'TASK_OBSERVER_DESCRIPTOR_SCHEMA_CHANGED')
    budget = value['schedule_budget']
    require(budget == schedule_budget(budget['driver'], runtime_ref)
            and value['maximum_seconds'] == budget['maximum_seconds']
            and value['maximum_requests'] == budget['maximum_requests'],
            'TASK_OBSERVER_SCHEDULE_BUDGET_CHANGED')
    return budget


def require_schedule_open(budget):
    if budget is None:
        return
    # Historical evidence readers deliberately do not call this live check.
    # The stop file is outside every model client's writable state.
    root = Path(budget['registration']['path']).parent
    require(not (root/'schedule/halt.json').exists(), 'TASK_OBSERVER_ORIGINAL_SCHEDULE_HALTED')
