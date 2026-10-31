"""Production entry that runs the JEV advisory worker and records it.

This is the caller ``jev_advice_worker.advise`` lacked outside test support. It
changes no judgment: the worker still decides what to answer. It only (1)
wraps a supplied transport in ``jev_request_ledger`` when the on arm has a
positive budget, and (2) publishes the worker's own journal exactly where
``orchestration_coordinator._worker_journal`` reads it.

The default is zero calls: with no transport, the off arm, or a budget that is
missing or not positive, the worker answers offline (NOT_CONFIGURED), records
model_requests=0, and no ledger directory is created. A transport exception
propagates after the ledger has recorded UNKNOWN; nothing here retries.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import jev_advice_worker
from jev_request_ledger import decision_attempted, ledger_summary, ledgered_transport
from orchestration_common import ContractError, write_json

JOURNAL_SCHEMA = "jev-advice-worker-journal-v1"
LEDGER_DIRECTORY = "jev-request-ledger"


def _positive_budget(budget) -> bool:
    limit = budget.get("max_model_requests") if isinstance(budget, dict) else None
    return isinstance(limit, int) and not isinstance(limit, bool) and limit > 0


def run_and_record(node_root, *, ledger_root, run_id: str, decision: str, state_text: str,
                   lanes: dict | None, bound: bool, arm: str, marker: str, provider: str,
                   budget: dict | None, model: str | None,
                   transport: Callable[[dict], dict] | None = None,
                   now: Callable[[], float] | None = None) -> dict:
    """Advise once for this decision and publish the journal the coordinator reads.

    ``ledger_root`` is the run's one ledger, shared by every node. Re-entry is
    refused before any transport: an existing journal, or any earlier intent
    for this decision (including UNKNOWN or a crash window), is never a new
    request. A 401/403 raises after its journal is written, so no fallback
    advice is returned in place of a dead key.
    """
    node_root = Path(node_root)
    journal_path = node_root / "advice-worker-journal.json"
    # A 401/403 anywhere in the run is reported first, so a later node or a
    # repeated decision is told STOP, not a duplicate-decision reason.
    if Path(ledger_root).is_dir() and ledger_summary(ledger_root)["stopped"]:
        raise ContractError("STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER")
    if journal_path.exists():
        raise ContractError("DECISION_ALREADY_RECORDED")
    if decision_attempted(ledger_root, decision):
        raise ContractError("DECISION_ALREADY_ATTEMPTED")
    ledger = None
    wrapped = None
    if transport is not None and arm == "advisory-on" and _positive_budget(budget) and not bound:
        ledger = Path(ledger_root)
        wrapped = ledgered_transport(transport, ledger_root=ledger, run_id=run_id, arm=arm, decision=decision,
                                     marker=marker, provider=provider, budget=budget)
    try:
        answer = jev_advice_worker.advise(state_text=state_text, lanes=lanes, bound=bound,
                                          budget=budget if wrapped is not None else None,
                                          transport=wrapped, model=model, now=now)
    except Exception as exc:
        # A raised 401/403 takes the same STOP path as a returned one.
        if ledger is not None and ledger_summary(ledger)["stopped"]:
            raise ContractError("STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER") from exc
        raise
    journal = write_json(journal_path, dict(schema=JOURNAL_SCHEMA, entries=list(answer["journal"])),
                         exclusive=True)
    summary = ledger_summary(ledger) if ledger is not None else None
    if summary is not None and summary["stopped"]:
        raise ContractError("STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER")
    return dict(advice=answer["advice"], request=answer["request"], model_requests=answer["model_requests"],
                journal=journal, ledger=summary)
