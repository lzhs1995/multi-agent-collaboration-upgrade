"""Explicit new-task Mach lookup and AppleEvent denial.

Only the execution server and Claude lanes use this additional policy. The
display and external controller retain their existing profiles. This blocks
new Mach service lookup and AppleEvent sends; it does not attest inherited
Mach rights, native helper descriptors or full live adoption.
"""
from orchestration_common import require
from upgrade_task_network import OBSERVER_SCHEMA

SCHEMA = 'upgrade-task-mach-denial-v1'


def contract():
    return dict(schema=SCHEMA, denied_operations=['mach-lookup', 'appleevent-send'],
                lanes=['codex-server', 'claude_a', 'claude_b'])


def enabled(binding):
    if 'task_mach_boundary' not in binding:
        return False
    require(binding['task_mach_boundary'] == contract()
            and 'task_startup' in binding
            and binding.get('task_model_network', {}).get('schema') == OBSERVER_SCHEMA,
            'TASK_MACH_BOUNDARY_CONTRACT_CHANGED')
    return True


def restrict(profile):
    require(isinstance(profile, bytes) and profile.startswith(b'(version 1)\n')
            and profile.endswith(b'\n'), 'TASK_MACH_BASE_PROFILE_REQUIRED')
    return profile + b'(deny mach-lookup)\n(deny appleevent-send)\n'
