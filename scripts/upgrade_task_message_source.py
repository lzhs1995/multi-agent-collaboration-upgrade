"""Original registered Claude messages, independent of completion callbacks.

A source file is a claim, never input authority. The receiving service must
also authenticate the connected Claude descendant and the original live run.
"""
from pathlib import Path
import shlex
import subprocess

from orchestration_common import file_ref, json_ref, require, write_json

from upgrade_task_message_wire import SCHEMA,REQUEST_SCHEMA,ROUTE_SCHEMA


def sample_value(campaign, kind, run_id, request_id):
    if kind == 'ordinary':
        from upgrade_communication_plan import campaign_plan, resolve_sample
        values = campaign_plan(campaign)
    else:
        require(kind == 'semantic', 'TASK_MESSAGE_KIND_INVALID')
        from upgrade_semantic_inputs import campaign_slots
        values = campaign_slots(campaign)
    matches = [v for v in values if v['run_id'] == run_id and v['request_id'] == request_id]
    require(len(matches) == 1, 'TASK_MESSAGE_ORIGINAL_SLOT_REQUIRED')
    sample = matches[0]
    if kind == 'ordinary':
        sample = resolve_sample(campaign, sample)
    require(sample['sender'] in ('claude_a', 'claude_b') and sample['receiver'] == 'codex'
            and campaign['sessions'][sample['sender']]['provider'] == 'claude'
            and 'task_controls' in campaign, 'TASK_MESSAGE_ORIGINAL_CLAUDE_REQUIRED')
    return sample


def boundary(campaign, sample, kind):
    from orchestration_coordinator import Coordinator, State
    from upgrade_communication_plan import _campaign_pin
    co = Coordinator(Path(campaign['root'])/'runs'/sample['run_id'], _campaign_pin(campaign))
    with co.state_lock(new_work=True):
        co._work_budget()
        require(not (co.root/'finish.json').exists(), 'TASK_MESSAGE_RUN_CLOSED')
        states, data = co.state()
        if kind == 'semantic':
            require(all(v == State.PENDING for v in states.values()), 'TASK_MESSAGE_SEMANTIC_BOUNDARY_PASSED')
        else:
            node = sample['node_id']
            from orchestration_binding import claim_lane
            require(sample['stage'] in ('before_business', 'after_business')
                    and states[node] == State.DISPATCHED and data[node]
                    and claim_lane(co.nodes[node], data[node]) == sample['sender'],
                    'TASK_MESSAGE_ORIGINAL_DISPATCH_REQUIRED')


def source_path(campaign, sample):
    return Path(campaign['root'])/'runs'/sample['run_id']/'message-inputs'/sample['request_id']/'source.json'


def validate_source(ref):
    from upgrade_communication_plan import _campaign_pin
    value = json_ref(ref); campaign = json_ref(value['campaign'])
    require(set(value) == {'schema', 'campaign', 'kind', 'sample'} and value['schema'] == SCHEMA
            and _campaign_pin(campaign) == value['campaign'], 'TASK_MESSAGE_SOURCE_CHANGED')
    sample = value['sample']
    require(sample_value(campaign, value['kind'], sample['run_id'], sample['request_id']) == sample
            and str(source_path(campaign, sample)) == ref['path'], 'TASK_MESSAGE_REGISTERED_SOURCE_CHANGED')
    return value, campaign, sample


def helper_request(campaign, sample):
    from upgrade_task_message_wire import helper_request as request
    from orchestration_contracts import state_root
    return request(campaign,sample,state_root(campaign))


def send(campaign, sample, kind):
    """One original helper subprocess, still executed by the original Claude."""
    from upgrade_communication_plan import _campaign_pin, _require_original_sender
    require(sample_value(campaign, kind, sample['run_id'], sample['request_id']) == sample,
            'TASK_MESSAGE_REGISTERED_SOURCE_CHANGED')
    boundary(campaign, sample, kind); _require_original_sender(campaign, sample)
    ref = write_json(source_path(campaign, sample), dict(schema=SCHEMA,
        campaign=_campaign_pin(campaign), kind=kind, sample=sample), exclusive=True)
    argv = (list(sample['helper_argv']) if kind == 'semantic'
            else shlex.split(sample['helper_command']))
    index = argv.index('send') + 1
    argv[index:index] = ['--registered-message', ref['path'], '--registered-message-sha256', ref['sha256']]
    return subprocess.run(argv, check=False).returncode
