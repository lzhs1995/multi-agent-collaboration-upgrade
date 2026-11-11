"""Read the original server's hook inventory before starting a model turn.

This never trusts a handler, changes configuration or runs a hook. Actual
automatic Pre/Post execution remains a separate readiness requirement.
"""
from pathlib import Path
import re

from orchestration_common import json_ref, read_ref, require, write_json
from upgrade_rpc_peer import verify_peer
from upgrade_task_creation import exchange

EVENTS = {'PreToolUse': ('preToolUse', 'pre_tool_use'),
          'PostToolUse': ('postToolUse', 'post_tool_use'), 'Stop': ('stop', 'stop')}


def validate_inventory(spec, response):
    pin = spec['pins']['codex/hooks.json']
    configured = json_ref(pin)['hooks']
    require(set(configured) <= set(EVENTS) and {'PreToolUse', 'PostToolUse'} <= set(configured),
            'TASK_HOOK_PREFLIGHT_CONFIG_EVENTS_CHANGED')
    data = response.get('data') if isinstance(response, dict) else None
    require(isinstance(data, list) and len(data) == 1 and data[0].get('cwd') == spec['work_root']
            and data[0].get('errors') == [] and data[0].get('warnings') == [],
            'TASK_HOOK_PREFLIGHT_INVENTORY_UNAVAILABLE')
    actual = data[0].get('hooks')
    require(isinstance(actual, list) and 0 < len(actual) <= 256,
            'TASK_HOOK_PREFLIGHT_INVENTORY_INVALID')
    expected = {}
    for event, groups in configured.items():
        rpc_event, key_event = EVENTS[event]
        for group_index, group in enumerate(groups):
            for hook_index, handler in enumerate(group['hooks']):
                require(handler.get('type') == 'command' and isinstance(handler.get('command'), str),
                        'TASK_HOOK_PREFLIGHT_COMMAND_REQUIRED')
                key = f'{pin["path"]}:{key_event}:{group_index}:{hook_index}'
                expected[key] = dict(command=handler['command'], eventName=rpc_event,
                                     matcher=None if event == 'Stop' else group.get('matcher'))
    require(len(actual) == len(expected) and len({item.get('key') for item in actual}) == len(actual)
            and {item.get('key') for item in actual} == set(expected),
            'TASK_HOOK_PREFLIGHT_MEMBERS_CHANGED')
    pending = []
    for item in actual:
        require(all(item.get(k) == v for k, v in expected[item['key']].items())
                and item.get('sourcePath') == pin['path'] and item.get('source') == 'user'
                and item.get('pluginId') is None and item.get('handlerType') == 'command'
                and item.get('enabled') is True and item.get('isManaged') is False
                and isinstance(item.get('currentHash'), str)
                and re.fullmatch(r'sha256:[0-9a-f]{64}', item['currentHash']) is not None,
                'TASK_HOOK_PREFLIGHT_HANDLER_CHANGED')
        require(item.get('trustStatus') in ('trusted', 'untrusted', 'modified'),
                'TASK_HOOK_PREFLIGHT_TRUST_STATE_INVALID')
        if item['trustStatus'] != 'trusted':
            pending.append(item['key'])
    return dict(ready=not pending, pending=pending, handler_count=len(actual),
                automatic_execution_proven=False, configuration_writes=0, model_inputs=0)


def preflight(client, spec, directory):
    """One recorded read on the retained authenticated connection; no retry."""
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    verify_peer(client.sock, client.server, client.process)
    pin = spec['pins']['codex/hooks.json']
    read_ref(pin)
    params = {'cwds': [spec['work_root']]}
    write_json(directory/'intent.json', dict(method='hooks/list', params=params,
        server=client.server, configuration=pin, model_inputs=0, configuration_writes=0), exclusive=True)
    response = client.request('hooks/list', params)
    original = client.last_exchange
    sent, received = exchange(original, 'hooks/list', client.server)
    require(sent == params and received == response, 'TASK_HOOK_PREFLIGHT_EXCHANGE_CHANGED')
    verify_peer(client.sock, client.server, client.process)
    read_ref(pin)
    result = validate_inventory(spec, response)
    if 'codex_hook_review' in spec['pins']:
        review = json_ref(spec['pins']['codex_hook_review'])
        require({item['key']: {'trusted_hash': item['currentHash']}
                 for item in response['data'][0]['hooks']} == review['states'],
                'TASK_HOOK_PREFLIGHT_REVIEW_HASH_CHANGED')
    proof = write_json(directory/'result.json', dict(result, exchange=original,
        configuration=pin, server=client.server), exclusive=True)
    require(result['ready'], 'TASK_HOOK_REVIEW_REQUIRED_BEFORE_FIRST_TURN: ' + str(proof['path']))
    return proof
