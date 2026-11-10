"""Explicit task Mach policy, with narrowly scoped native preferences reads.

Only the execution server and Claude lanes use this additional policy. The
display and external controller retain their existing profiles. This blocks
new Mach service lookup and AppleEvent sends. Version 2 permits only the Codex
server to read its managed preferences through the two cfprefsd endpoints;
preference writes remain denied. Version 1 retains its exact original policy.
Neither version attests inherited Mach rights or full live adoption.
"""
from orchestration_common import require
from upgrade_task_network import OBSERVER_SCHEMA

LEGACY_SCHEMA = 'upgrade-task-mach-denial-v1'
SCHEMA = 'upgrade-task-mach-preferences-v2'
LANES = ['codex-server', 'claude_a', 'claude_b']
PREFERENCE_SERVICES = ['com.apple.cfprefsd.daemon', 'com.apple.cfprefsd.agent']
PREFERENCE_DOMAIN = 'com.openai.codex'


def contract(*, version=2):
    require(type(version) is int and version in (1, 2), 'TASK_MACH_VERSION_UNKNOWN')
    value = dict(schema=LEGACY_SCHEMA if version == 1 else SCHEMA,
                 denied_operations=['mach-lookup', 'appleevent-send'], lanes=list(LANES))
    if version == 2:
        value['codex_managed_preferences'] = dict(
            lane='codex-server', services=list(PREFERENCE_SERVICES),
            read_domains=[PREFERENCE_DOMAIN], write_allowed=False)
    return value


def enabled(binding):
    if 'task_mach_boundary' not in binding:
        return False
    require(binding['task_mach_boundary'] in (contract(version=1), contract())
            and 'task_startup' in binding
            and binding.get('task_model_network', {}).get('schema') == OBSERVER_SCHEMA,
            'TASK_MACH_BOUNDARY_CONTRACT_CHANGED')
    return True


def restrict(profile, *, boundary=None, lane=None):
    require(isinstance(profile, bytes) and profile.startswith(b'(version 1)\n')
            and profile.endswith(b'\n'), 'TASK_MACH_BASE_PROFILE_REQUIRED')
    # The historical one-argument API continues to mean exact v1 denial.
    if boundary is None:
        require(lane is None, 'TASK_MACH_LANE_WITHOUT_CONTRACT')
        boundary = contract(version=1)
    else:
        require(boundary in (contract(version=1), contract()) and lane in LANES,
                'TASK_MACH_PROFILE_CONTRACT_CHANGED')
    result = profile + b'(deny mach-lookup)\n(deny appleevent-send)\n'
    if boundary['schema'] == SCHEMA and lane == 'codex-server':
        # CFPreferencesAppSynchronize is required by the official startup
        # loader. Keep other Mach services and all preference mutation denied.
        result += b'(deny user-preference-write)\n(deny user-preference-read)\n'
        result += ('(allow user-preference-read (preference-domain "' +
                   PREFERENCE_DOMAIN + '"))\n').encode()
        for service in PREFERENCE_SERVICES:
            result += ('(allow mach-lookup (global-name "' + service + '"))\n').encode()
    return result
