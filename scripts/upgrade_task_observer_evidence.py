"""Join a startup tool/hook to its original protected observer receipt.

Historical reading verifies a past read, not current service liveness, direct
RPC peer identity, native hook invocation or permission to adopt/send input.
The readiness readers independently correlate native tool and hook records.
"""
import os
from pathlib import Path
import re

from orchestration_common import file_ref, json_ref, require
from upgrade_task_network import OBSERVER_SCHEMA, observer_path
from upgrade_task_observer_protocol import OBSERVATION, RESPONSE, SCHEMA, request_value, allowed_kinds
from upgrade_task_fd_contract import enabled as descriptors_enabled, verify as verify_descriptors


def enabled(binding):
    return binding.get('task_model_network', {}).get('schema') == OBSERVER_SCHEMA


def verify(ref, binding, startup, lane, *, pid=None, parents=None):
    require(enabled(binding) and lane in ('codex-server', 'claude_a', 'claude_b'),
            'TASK_OBSERVER_EVIDENCE_CONTRACT_REQUIRED')
    root = Path(observer_path(binding['evidence_root'])).parent
    path = Path(ref['path'])
    require(path == path.resolve() and path.parent.parent == root
            and re.fullmatch(r'[0-9]{6}', path.parent.name) and path.name == 'observation.json',
            'TASK_OBSERVER_EVIDENCE_ORIGINAL_PATH_REQUIRED')
    saved = json_ref(ref)
    request = json_ref(saved['request'])
    request_value(request)
    expected_kind = 'startup' if descriptors_enabled(binding) else 'tree'
    response = saved['response']
    descriptor_ref = file_ref(root/'descriptor.json')
    descriptor = json_ref(descriptor_ref)
    runtime_ref = file_ref(Path(binding['controller_root']).parents[2]/'runtime-v2.json')
    require(saved['request']['path'] == str(path.parent/'request.json')
            and request['kind'] == expected_kind
            and response['schema'] == RESPONSE and response['kind'] == expected_kind
            and response['nonce'] == request['nonce'] and response['status'] == 'OBSERVED'
            and response['descriptor'] == descriptor_ref and response['input_operations'] == 0
            and descriptor['schema'] == SCHEMA and descriptor['runtime'] == runtime_ref
            and descriptor['startup'] == startup and descriptor['socket']['path'] == str(root/'s')
            and descriptor['allowed_kinds'] == list(allowed_kinds(binding))
            and descriptor['input_operations'] == 0 and descriptor['restart_allowed'] is False
            and descriptor['adoption_proven'] is False,
            'TASK_OBSERVER_EVIDENCE_ORIGINAL_RESPONSE_CHANGED')
    value = response['observation']
    who = value['caller']; ancestry = who['ancestry']
    require(value['schema'] == OBSERVATION and value['runtime'] == runtime_ref
            and value['startup'] == startup and value['input_operations'] == 0
            and value['adoption_proven'] is False and value['requester_direct_rpc_peer'] is False
            and value['identity_source'] == 'original_controller_read_only_observation'
            and value['task'] is None and who['lane'] == lane
            and len(ancestry) > 1 and who['peer']['pid'] == ancestry[0]['pid']
            and who['peer']['uid'] == os.geteuid()
            and all(a['ppid'] == b['pid'] for a, b in zip(ancestry, ancestry[1:])),
            'TASK_OBSERVER_EVIDENCE_CALLER_CHANGED')
    if pid is not None:
        require(who['peer']['pid'] == pid, 'TASK_OBSERVER_EVIDENCE_HOOK_PID_CHANGED')
    launch = json_ref(file_ref(Path(binding['state_root'])/'startup'/(lane+'.json')))
    require(launch['startup'] == startup
            and ancestry[-1]['pid'] == launch['pid'] and ancestry[-1]['birth'] == launch['birth'],
            'TASK_OBSERVER_EVIDENCE_ORIGINAL_LAUNCH_CHANGED')
    if parents is not None:
        require(len(ancestry) == len(parents)+1
                and all(all(a[k] == b[k] for k in ('pid', 'ppid', 'birth', 'executable', 'file_identity'))
                        for a, b in zip(ancestry[1:], parents)),
                'TASK_OBSERVER_EVIDENCE_HOOK_ANCESTRY_CHANGED')
    if descriptors_enabled(binding):
        verify_descriptors(value['requester_descriptors'], who, str(root/'s'))
    return value


def capture(binding, startup, lane, *, parents=None):
    """One real tree read by this tool/hook. No alternate endpoint or retry."""
    import cmux_daemon_identity as identity
    from upgrade_task_observer_client import read
    response = read(binding, 'startup' if descriptors_enabled(binding) else 'tree', process=identity.process)
    ref = response['evidence']
    verify(ref, binding, startup, lane, pid=os.getpid(), parents=parents)
    return ref


def hook(value, binding, startup):
    require(value.get('direct_client') in ('codex-server', 'claude_a', 'claude_b'),
            'TASK_OBSERVER_EVIDENCE_NATIVE_HOOK_REQUIRED')
    return verify(value['task_observer'], binding, startup, value['direct_client'],
                  pid=value['pid'], parents=value['parents'])
