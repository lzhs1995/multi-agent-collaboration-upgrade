"""Read-only TASK owner contracts shared by the driver and launch planner.

Keep the executing owner, its private import scope and model connections out of
business adapter dependency closures. These functions never start an owner.
"""
from pathlib import Path

from orchestration_common import ContractError, file_ref, json_ref, require
from upgrade_task_observer_protocol import PROCESS_FIELDS


def original_server_result(runtime):
    """A recorded failed start is terminal even before the TUI publishes origin.

    The origin record is written only after server startup succeeds. Waiting
    for it before inspecting a failed server result hides that original failure.
    Missing results remain a wait; this reader never retries the launch.
    """
    path = Path(runtime['state_root'])/'startup/codex-server-result.json'
    if not path.exists():
        return None
    result = json_ref(file_ref(path))
    require(result.get('schema') == 'upgrade-server-result-v2'
            and result.get('startup') == runtime['startup'],
            'TASK_OWNER_ORIGINAL_STARTUP_CHANGED')
    require(result.get('status') == 'SOCKET_CREATED',
            'TASK_OWNER_ORIGINAL_SERVER_FAILED')
    return result


def _owner_process(process, pid):
    """Preserve a lost or unreadable original owner as a driver fault.

    Only the kernel reader's explicit ProcessExited proves absence. Other
    identity failures remain unknown; neither outcome permits a new owner.
    """
    import cmux_daemon_identity as identity
    try:
        return process(pid)
    except identity.ProcessExited as exc:
        raise ContractError('TASK_DRIVER_OWNER_EXITED_NO_REPLAY') from exc
    except identity.IdentityError as exc:
        raise ContractError('TASK_DRIVER_OWNER_IDENTITY_UNAVAILABLE_NO_REPLAY') from exc


def _require_owner_open(root):
    for directory in (root/'task-owner', root/'task-lifecycle'):
        require(not any((directory/name).exists() for name in ('closed.json', 'unknown.json')),
                'TASK_DRIVER_OWNER_CLOSED_NO_REPLAY')


def observe_owner(runtime_ref, runtime, spec, campaign_path, *, driver_ref, original=None, process=None):
    """Read the retained owner; never attach, adopt, reconnect or send input.

    The driver pins the first owner reference and checks that same process on
    later ticks. A missing first publication is a wait, but a lost/replaced
    owner after publication is an error. Attachment proves service setup only,
    never a completed model turn or accepted business result.
    """
    original_server_result(runtime)
    root = Path(spec['adoption_path']).parent
    path = root/'task-owner/owner.json'
    if not path.exists():
        require(original is None, 'TASK_DRIVER_ORIGINAL_OWNER_MISSING')
        return dict(status='WAITING_ORIGINAL_TASK_OWNER', owner=None,
                    campaign_attachment=None, input_operations=0)
    owner_ref = file_ref(path)
    require(original is None or original == owner_ref, 'TASK_DRIVER_ORIGINAL_OWNER_CHANGED')
    owner = json_ref(owner_ref)
    require(owner.get('schema') == 'upgrade-task-external-owner-v1'
            and owner.get('runtime') == runtime_ref
            and owner.get('driver') == driver_ref
            and owner.get('campaign_path') == str(campaign_path)
            and owner.get('reconnect_allowed') is False
            and owner.get('model_operations_by_operator') == 0,
            'TASK_DRIVER_OWNER_SCOPE_CHANGED')
    _require_owner_open(root)
    if process is None:
        import cmux_daemon_identity as identity
        process = identity.process
    row = _owner_process(process, owner['process']['pid'])
    require({k:row[k] for k in PROCESS_FIELDS} == owner['process'],
            'TASK_DRIVER_OWNER_PROCESS_CHANGED')
    _require_owner_open(root)
    lifecycle_path = root/'task-lifecycle/owner.json'
    attachment_path = root/'task-lifecycle/campaign-result.json'
    result = dict(status='WAITING_ORIGINAL_TASK_ADOPTION', owner=owner_ref,
                  campaign_attachment=None, input_operations=0)
    if lifecycle_path.exists():
        lifecycle_ref = file_ref(lifecycle_path)
        lifecycle = json_ref(lifecycle_ref)
        require(lifecycle.get('schema') == 'upgrade-task-lifecycle-v1'
                and lifecycle.get('runtime') == runtime_ref
                and lifecycle.get('startup') == runtime['startup']
                and lifecycle.get('owner_pid') == row['pid']
                and lifecycle.get('reconnect_allowed') is False,
                'TASK_DRIVER_LIFECYCLE_OWNER_CHANGED')
        if attachment_path.exists():
            attachment_ref = file_ref(attachment_path)
            attachment = json_ref(attachment_ref)
            require(attachment.get('owner') == lifecycle_ref
                    and attachment.get('operation_budget_reset') is False
                    and attachment.get('model_inputs_sent') == 0,
                    'TASK_DRIVER_ATTACHMENT_CHANGED')
            service = json_ref(attachment['runtime_owner'])
            enrollment = json_ref(service['enrollment'])
            campaign_ref = file_ref(campaign_path)
            campaign = json_ref(campaign_ref)
            require(service.get('schema') == 'upgrade-task-runtime-owner-v1'
                    and service.get('owner_pid') == row['pid']
                    and service.get('campaign') == campaign_ref
                    and enrollment.get('schema') == 'upgrade-task-campaign-enrollment-v1'
                    and enrollment.get('controller_pid') == row['pid']
                    and enrollment.get('campaign') == campaign_ref
                    and enrollment.get('runtime') == runtime_ref
                    and enrollment.get('startup') == runtime['startup']
                    and enrollment.get('server') == lifecycle['server']
                    and enrollment.get('adoption') == campaign['client_isolation']
                    and enrollment.get('ledger') == service['ledger'],
                    'TASK_DRIVER_ORIGINAL_CAMPAIGN_ATTACHMENT_CHANGED')
            result.update(status='TASK_CAMPAIGN_ATTACHED', campaign_attachment=attachment_ref)
    else:
        require(not attachment_path.exists(), 'TASK_DRIVER_LIFECYCLE_OWNER_MISSING')
    again = _owner_process(process, row['pid'])
    require(file_ref(path) == owner_ref
            and {k:again[k] for k in PROCESS_FIELDS} == owner['process'],
            'TASK_DRIVER_OWNER_CHANGED_DURING_READ')
    _require_owner_open(root)
    return result


def advice_requirements(plan):
    """Validate original advice bindings without opening transport or reading keys."""
    from jev_registration import load
    producers = {}
    by_run = {}
    for name, operation in plan['operations'].items():
        payload = operation['payload']
        if operation['kind'] != 'campaign-control' or payload.get('action') != 'advice':
            continue
        registration = load(payload['advisory_registration'], run_id=payload['run_id'],
                            node_id=payload['node_id'])
        if registration['arm'] == 'advisory-off':
            continue
        identity = {k:registration[k] for k in ('provider', 'model', 'transport_source')}
        prior = by_run.setdefault(payload['run_id'], identity)
        require(prior == identity, 'TASK_OWNER_ADVICE_RUN_CONNECTION_CHANGED')
        producers[name] = (payload['run_id'], payload['advisory_registration'])
    return producers, by_run
