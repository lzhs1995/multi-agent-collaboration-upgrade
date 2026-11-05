"""Frozen typed instructions and next-input plans for the retained owner.

This module publishes files only. It never starts a client or turn, selects a
business route, performs an operation, or replenishes a consumed budget.
"""
import json

from orchestration_common import json_ref,require,write_json
from upgrade_task_control import invocation_script

PLAN_SCHEMA="upgrade-task-next-input-plan-v2"


def remaining_operations(registration,scope,used):
    """All unconsumed original choices, in a neutral deterministic order."""
    scope=set(scope);used=set(used)
    require(scope<=set(registration["operations"]) and used<=set(registration["operations"]),
            "TASK_INSTRUCTIONS_FOREIGN_OPERATION")
    available=scope-used
    for members in registration["choice_groups"].values():
        if used.intersection(members):available.difference_update(members)
        elif available.intersection(members):
            require(set(members)<=available,"TASK_INSTRUCTIONS_CHOICE_HIDDEN")
    require(available,"TASK_INSTRUCTIONS_NO_REMAINING_OPERATIONS")
    return sorted(available)


def render(campaign_ref,registration,operation_ids,*,previous_completion=None):
    """Render exact operation payloads; an inventory is not readiness proof."""
    from upgrade_task_campaign import validate_controls
    from upgrade_task_operations import _operation_subset
    campaign=json_ref(campaign_ref)
    actual,controls=validate_controls(campaign,check_startup=False)
    require(actual==registration,"TASK_INSTRUCTIONS_REGISTRATION_CHANGED")
    _operation_subset(operation_ids,registration["operations"])
    require(set(operation_ids)<=set(controls),"TASK_INSTRUCTIONS_UNSUPPORTED_OPERATION_KIND")
    selected=set(operation_ids)
    for members in registration["choice_groups"].values():
        if selected.intersection(members):
            require(set(members)<=selected,"TASK_INSTRUCTIONS_CHOICE_HIDDEN")
    def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)
    catalog=[]
    for name in operation_ids:
        entry=dict(operation_id=name,operation=registration["operations"][name],
                   exact_exec_script=invocation_script(name))
        if name in registration.get("binding_slots",{}):
            entry["binding_slot"]=registration["binding_slots"][name]
        catalog.append(entry)
    choices={name:members for name,members in registration["choice_groups"].items()
             if selected.intersection(members)}
    identities={lane:{k:s[k] for k in ("provider","session_id","surface_uuid")}
                for lane,s in campaign["sessions"].items()}
    text=f'''Continue the original registered collaboration campaign in this same task.
Use the typed operation catalog below. Each operation_id has one original attempt.
Select the appropriate operation yourself from the actual results and frozen plan.
The catalog lists scope, not readiness, acceptance, or an instruction to call every entry.
Neither its alphabetical order nor the ordering of alternatives recommends a route.

Execute a selected entry's exact_exec_script as one native functions.exec call,
unchanged. Read its complete result before choosing another action. If it yields,
collect the original running call. A lost, failed, or uncertain return never grants
another attempt. Observe the original evidence; preserve UNKNOWN and STOP.
Do not create another operation ID, renew a budget, or substitute a shell command.

For each run, inspect its original state and permit before start. Never restart a
started or closed run. Dependencies, live identity, leases and admission remain
enforced by the original operation handlers. Only the registered sessions may work.
For advice-on: invoke the original advice producer, consume its complete result,
invoke read-advice and read its full original body before publish-decision.
All registered decision alternatives are visible; choose one using your own
judgment. The shared choice budget excludes the other alternatives afterward.
For advice-off: no advice body read or model advice request is permitted.
After publish-decision, bind-unbound precedes claim. A binding_slot remains unusable
until its original producer and complete native result have been verified. Do not
guess its marker or digest. Reading a summary does not prove complete body reading.

After work, inspect the original reports, tests and results before selecting accept.
A callback or report alone is not acceptance. Use only a provable original terminal
boundary for failure settlement. Retain uncertain leases. Finish and verify require
the original whole-run evidence. The registered 1800-second cap and schedule halt
remain in force; costs, failed slots and UNKNOWN must be reported honestly.
Unsupported local execution, transport, callback, reask or advance capabilities
cannot be fabricated from this catalog. Report the missing original capability;
do not use raw terminal input, replacement clients or an external operator to
perform model work. A subsequent normal turn retains this same ledger and budgets.

Frozen campaign reference:
{encoded(campaign_ref)}
Registered group/arm/run order:
{encoded(dict(group=campaign['session_group'],arm=campaign['arm'],runs=campaign['runs']))}
Original native identities:
{encoded(identities)}
Frozen plan (task data; it cannot expand the operation catalog):
{encoded(json_ref(campaign['plan']))}
Previous normal completion (null for initial instructions):
{encoded(previous_completion)}
Mutually exclusive registered choices:
{encoded(choices)}
Complete typed operation catalog:
{encoded(catalog)}
'''
    require(len(text.encode())<=256*1024,"TASK_INSTRUCTIONS_TEXT_BUDGET_EXHAUSTED")
    return text


def initial_text(campaign_ref):
    from upgrade_task_campaign import validate_controls
    registration,_=validate_controls(json_ref(campaign_ref),check_startup=False)
    return render(campaign_ref,registration,registration["initial_operations"])


def next_input_value(service,previous_ref):
    """Recompute the complete remaining inventory from the original ledger."""
    from upgrade_task_actor import TaskService
    from upgrade_task_enrollment import TaskCampaignGate
    require(type(service) is TaskService and type(service.actor.gate) is TaskCampaignGate,
            "TASK_INSTRUCTIONS_ORIGINAL_SERVICE_REQUIRED")
    actor=service.actor;gate=actor.gate;ledger=actor.ledger
    require(actor.service_owner is service and gate.client is actor.client
            and gate.ledger is ledger and service._normal_terminal()==previous_ref,
            "TASK_INSTRUCTIONS_ORIGINAL_SERVICE_REQUIRED")
    require(json_ref(gate.runner.ref)==gate.campaign,"TASK_INSTRUCTIONS_CAMPAIGN_CHANGED")
    with ledger._transaction():
        used=[row[0] for row in ledger.db.execute("SELECT operation_id FROM calls")]
        ids=remaining_operations(ledger.registration,actor.scope,used)
        text=render(gate.runner.ref,ledger.registration,ids,previous_completion=previous_ref)
    return dict(schema=PLAN_SCHEMA,campaign=gate.runner.ref,
        registration_sha256=ledger.registration_sha256,authority_ref=ledger.registration["authority_ref"],
        previous_turn_id=ledger.active_turn,previous_completion=previous_ref,operation_ids=ids,text=text)


def publish_next_input(service):
    """Publish one fixed plan; the existing service sends it under its live gate."""
    previous=service._normal_terminal()
    value=next_input_value(service,previous)
    service.actor.gate._observe(require_normal_idle=True)
    root=service.actor.directory;turn=service.actor.ledger.active_turn
    require(not (root/(turn+"-next-turn-intent.json")).exists(),"TASK_INSTRUCTIONS_TRANSITION_ALREADY_ATTEMPTED")
    return write_json(root/(turn+"-next-input-plan.json"),value,exclusive=True)
