"""Bounded live kernel policy observations for the original execution lanes.

No-filter sandbox_check reports the default decision, not every exception.
Exact exceptions are independently reconstructed by startup validation; real
model/readiness and observer activity demonstrate the required allowed paths.
No connection, Mach lookup, AppleEvent, process mutation or input is performed.
"""
import ctypes as C
import sys

from orchestration_common import require
from upgrade_task_fd_contract import enabled as descriptors_enabled
from upgrade_task_observer_protocol import PROCESS_FIELDS

SCHEMA = 'upgrade-task-runtime-policy-v1'
OPERATIONS = ('network-outbound', 'mach-lookup', 'appleevent-send')


def contract():
    return dict(schema=SCHEMA, default_denied=list(OPERATIONS),
                scope='original_execution_lanes_and_startup_requester_ancestry',
                all_exceptions_proven=False, inherited_mach_rights_proven=False)


def enabled(binding):
    if 'task_runtime_policy' not in binding:
        return False
    require(binding['task_runtime_policy'] == contract() and descriptors_enabled(binding),
            'TASK_RUNTIME_POLICY_CONTRACT_CHANGED')
    return True


def default_decisions(pid):
    require(sys.platform == 'darwin' and type(pid) is int and pid > 1,
            'TASK_RUNTIME_POLICY_DARWIN_PID_REQUIRED')
    library = C.CDLL('/usr/lib/libsandbox.dylib', use_errno=True)
    check = library.sandbox_check
    check.argtypes = (C.c_int, C.c_char_p, C.c_int)
    check.restype = C.c_int
    no_report = C.c_int.in_dll(library, 'SANDBOX_CHECK_NO_REPORT').value
    result = {operation: check(pid, operation.encode(), no_report) for operation in OPERATIONS}
    require(all(type(v) is int and v in (0, 1) for v in result.values()),
            'TASK_RUNTIME_POLICY_QUERY_FAILED')
    return result


def observe(rows, *, process):
    require(isinstance(rows, list) and 0 < len(rows) <= 32
            and len({r['pid'] for r in rows}) == len(rows), 'TASK_RUNTIME_POLICY_PROCESS_LIST_CHANGED')
    expected = [{k: r[k] for k in PROCESS_FIELDS} for r in rows]
    decisions = []
    def current(pid):
        row = process(pid)
        return {k: row[k] for k in PROCESS_FIELDS}
    for row in expected:
        pid = row['pid']
        require(current(pid) == row,
                'TASK_RUNTIME_POLICY_PROCESS_CHANGED')
        first = default_decisions(pid)
        require(first == {operation: 1 for operation in OPERATIONS},
                'TASK_RUNTIME_POLICY_DEFAULT_ALLOW:'+str(pid))
        require(default_decisions(pid) == first
                and current(pid) == row,
                'TASK_RUNTIME_POLICY_CHANGED_DURING_READ')
        decisions.append(dict(process=row, default_decisions=first))
    require(all(current(r['pid']) == r for r in expected),
            'TASK_RUNTIME_POLICY_CHANGED_DURING_READ')
    return dict(schema=SCHEMA, processes=decisions, input_operations=0,
                default_policy_checked=True, all_exceptions_proven=False,
                inherited_mach_rights_proven=False, whole_client_isolation_proven=False)


def verify(value, rows):
    expected = dict(schema=SCHEMA,
        processes=[dict(process={k:r[k] for k in PROCESS_FIELDS},
                        default_decisions={op:1 for op in OPERATIONS}) for r in rows],
        input_operations=0, default_policy_checked=True, all_exceptions_proven=False,
        inherited_mach_rights_proven=False, whole_client_isolation_proven=False)
    require(value == expected, 'TASK_RUNTIME_POLICY_EVIDENCE_CHANGED')
    return value
