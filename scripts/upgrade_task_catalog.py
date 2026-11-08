"""Compile the complete finite business catalog before the first task turn.

The caller supplies the registered campaign graph, original advice registrations
and model-visible decision alternatives. This creates no ledger, starts no model
and selects no route. The freezer later checks these same operations against the
actual campaign. Generated acceptance and advice digests remain unbound slots.
"""
from copy import deepcopy
from pathlib import Path

from orchestration_common import checked_path, json_ref, require
from upgrade_task_bindings import SLOT_SCHEMA
from upgrade_task_campaign import KIND, operation_payload
from upgrade_task_startup import validate_plan_operations


def catalog(campaign, task_directory, *, observations=4,
            advice_registrations=None, decision_options=None, failures=None):
    """Return materialize_live's operations/initial scope/choices/slots fields.

    Read snapshots have distinct predeclared IDs; mutating actions never gain
    another attempt. Failure settlement is optional and must name a concrete
    reason and boundary in advance. Unknown outcomes are not fabricated into a
    preselected failure. Unbound nodes retain every supplied route alternative.
    """
    require(type(observations) is int and 1 <= observations <= 16,
            'TASK_CATALOG_OBSERVATION_BUDGET_INVALID')
    directory = checked_path(task_directory)
    plan = json_ref(campaign['plan'])
    nodes = plan['nodes']
    runs = [r['run_id'] for r in campaign['runs']]
    require(runs and len(set(runs)) == len(runs)
            and nodes and len({n['id'] for n in nodes}) == len(nodes),
            'TASK_CATALOG_GRAPH_AMBIGUOUS')
    unbound = {(run, n['id']) for run in runs for n in nodes if n['executor'] is None}
    advice = {} if advice_registrations is None else advice_registrations
    decisions = {} if decision_options is None else decision_options
    failures = {} if failures is None else failures
    require(set(advice) == unbound == set(decisions), 'TASK_CATALOG_UNBOUND_SCOPE_INCOMPLETE')
    endpoints = {(run, n['id']) for run in runs for n in nodes}
    require(set(failures) <= endpoints, 'TASK_CATALOG_FOREIGN_FAILURE')
    operations = {'ready': dict(kind='readiness', payload={})}
    slots, choices = {}, {}

    def add(run, action, node=None, *, suffix=None, **kwargs):
        # Names are opaque IDs. Labels and alternatives carry no priority.
        name = ':'.join(x for x in (run, node, action, suffix) if x is not None)
        require(name not in operations, 'TASK_CATALOG_DUPLICATE_OPERATION')
        payload = operation_payload(campaign, action, run, node_id=node, **kwargs)
        operations[name] = dict(kind=KIND, payload=payload)
        return name

    def marker(run, node, action, producer, resolver, filename):
        name = add(run, action, node, marker='unbound-template')
        operations[name]['payload']['marker'] = None
        slots[name] = dict(schema=SLOT_SCHEMA, resolver=resolver,
            source_path=str(Path(campaign['root'])/'runs'/run/'nodes'/node/filename),
            producer_operation=producer)

    for run in runs:
        add(run, 'start')
        add(run, 'finish')
        for action in ('status', 'recover', 'verify') + (('advance',) if campaign['arm'] == 'B' else ()):
            for index in range(observations):
                label = f'observation-{index + 1:02d}'
                add(run, action, suffix=label, observation=label)
        for node in nodes:
            key = node['id']
            endpoint = (run, key)
            if endpoint in unbound:
                from jev_registration import load
                from orchestration_campaign import NO_ADVICE
                from upgrade_task_advice import RESOLVER
                registration = load(advice[endpoint], run_id=run, node_id=key)
                on = registration['arm'] == 'advisory-on'
                add(run, 'advice', key, advisory_registration=advice[endpoint])
                reader = add(run, 'read-advice', key) if on else None
                alternatives = decisions[endpoint]
                require(isinstance(alternatives, list) and alternatives,
                        'TASK_CATALOG_MODEL_DECISIONS_REQUIRED')
                members = []
                for index, value in enumerate(alternatives):
                    require(isinstance(value, dict) and set(value) ==
                            {'work_kind', 'actual_route', 'action', 'reason'},
                            'TASK_CATALOG_DECISION_FIELDS_CHANGED')
                    from jev_advice import WORK_KINDS
                    from orchestration_binding import DECISION_ACTIONS
                    require(value['work_kind'] in WORK_KINDS
                            and value['actual_route'] in ('codex', 'claude_a', 'claude_b')
                            and value['action'] in DECISION_ACTIONS
                            and (on or value['action'] == 'supervisor_review'),
                            'TASK_CATALOG_DECISION_OPTION_INVALID')
                    fields = dict(value, advice_sha256='0'*64 if on else NO_ADVICE)
                    name = add(run, 'publish-decision', key, suffix=f'choice-{index + 1:03d}',
                               decision_fields=fields)
                    members.append(name)
                    if on:
                        operations[name]['payload']['decision_fields']['advice_sha256'] = None
                        slots[name] = dict(schema=SLOT_SCHEMA, resolver=RESOLVER,
                            source_path=str(directory/'campaign-results'/(reader+'.json')),
                            producer_operation=reader)
                if len(members) > 1:
                    choices[f'{run}:{key}:decision'] = members
                add(run, 'bind-unbound', key,
                    decision=str(Path(campaign['root'])/'runs'/run/'decisions'/(key+'.json')))
            add(run, 'claim', key)
            if node['executor'] in (None, 'codex'):
                add(run, 'local-execute', key)
            if node['executor'] in (None, 'claude_a', 'claude_b'):
                for action in ('prepare', 'dispatch', 'callback', 'disarm'):
                    add(run, action, key)
                for index in range(observations):
                    label = f'observation-{index + 1:02d}'
                    add(run, 'reconcile', key, suffix=label, observation=label)
            producer = add(run, 'candidate', key)
            marker(run, key, 'accept', producer, 'campaign-accept-marker-v1', 'candidate.json')
            if endpoint in failures:
                value = failures[endpoint]
                require(isinstance(value, dict) and set(value) == {'reason', 'boundary'},
                        'TASK_CATALOG_FAILURE_FIELDS_CHANGED')
                add(run, 'block', key, reason=value['reason'])
                producer = add(run, 'failure-candidate', key, boundary=value['boundary'])
                marker(run, key, 'settle', producer, 'campaign-settle-marker-v1', 'failure-candidate.json')
    # Reject duplicate payloads, including differently named route choices.
    from orchestration_common import object_sha
    fingerprints = [object_sha(v) for v in operations.values()]
    require(len(set(fingerprints)) == len(fingerprints), 'TASK_CATALOG_DUPLICATE_PAYLOAD')
    return dict(operations=operations, initial_operations=['ready'],
                choice_groups=choices, binding_slots=slots)


def startup_options(campaign, task_directory, *, task_id, controller_id, authority_ref,
                    startup_text, base_instructions, **catalog_options):
    """Exact input for materialization, validated before any runtime exists."""
    value = dict(task_id=task_id, controller_id=controller_id,
                 authority_ref=deepcopy(authority_ref), startup_text=startup_text,
                 base_instructions=base_instructions,
                 **catalog(campaign, task_directory, **catalog_options))
    validate_plan_operations(value)
    return value
