"""Read-only contract for an original hook/probe's observed descriptors."""
from orchestration_common import require
from upgrade_task_mach_policy import enabled as mach_enabled

SCHEMA = 'upgrade-task-requester-descriptors-v1'


def contract():
    return dict(schema=SCHEMA, standard_fds=[0, 1, 2], extra_fds='original_observer_socket_only',
                fileports='none', observation='kernel_peer_during_original_read')


def enabled(binding):
    if 'task_native_descriptors' not in binding:
        return False
    require(binding['task_native_descriptors'] == contract() and mach_enabled(binding),
            'TASK_NATIVE_DESCRIPTOR_CONTRACT_CHANGED')
    return True


def verify(value, caller, endpoint):
    require(value.get('schema') == SCHEMA and value.get('requester') == caller['ancestry'][0]
            and value['requester']['pid'] == caller['peer']['pid']
            and value.get('endpoint') == endpoint and value.get('input_operations') == 0
            and value.get('whole_client_isolation_proven') is False,
            'TASK_NATIVE_DESCRIPTOR_SCOPE_CHANGED')
    before, after = value['before'], value['after']
    require(before == after and before['fileports'] == [], 'TASK_NATIVE_DESCRIPTOR_SNAPSHOT_CHANGED')
    rows = before['fds']
    require(len(rows) == 4 and all(set(r) == {'fd', 'type'} and type(r['fd']) is int
            and type(r['type']) is int for r in rows)
            and [r['fd'] for r in rows[:3]] == [0, 1, 2]
            and all(r['type'] in (1, 6) for r in rows[:3])
            and rows[3]['fd'] > 2 and rows[3]['type'] == 2,
            'TASK_NATIVE_DESCRIPTOR_EXTRA_CHANNEL')
    peer, observer = value['requester_socket'], value['observer_socket']
    require(peer['fd'] == rows[3]['fd'] and observer['fd'] >= 0
            and all(row['family'] == 1 and row['type'] == 1 and row['kind'] == 3
                    and type(row['socket']) is int and row['socket'] > 0
                    and type(row['connected_socket']) is int and row['connected_socket'] > 0
                    for row in (peer, observer))
            and peer['socket'] == observer['connected_socket']
            and peer['connected_socket'] == observer['socket']
            and peer['socket'] != observer['socket'],
            'TASK_NATIVE_DESCRIPTOR_ORIGINAL_CONNECTION_CHANGED')
    return value
