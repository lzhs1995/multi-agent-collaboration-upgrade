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


def require(value, message):
    if not value:
        raise ContractError(message)


def task_state(co, node_id: str) -> str:
    """The original frozen task as the worker's state text, with its pins.

    Each campaign input is read through its pin (``read_ref`` re-checks bytes
    and sha256), so ``request.state`` carries the exact text beside the pin it
    must hash back to. A non-UTF-8 input keeps its pin with ``text`` None.
    """
    import json
    from orchestration_common import read_ref
    from orchestration_evidence import task_identity
    units = []
    for ref in co.campaign["inputs"]:
        raw = read_ref(ref, "original frozen input")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        units.append(dict(pin=dict(ref), text=text))
    return json.dumps(dict(schema="jev_task_state_v1", task=task_identity(co.campaign, co.header, co.nodes[node_id]),
                           node=co.nodes[node_id], state="PENDING_UNBOUND", inputs=units),
                      ensure_ascii=False, sort_keys=True)


def load_transport(source_ref, connection, credential, *, model):
    """Execute exactly the registered transport bytes and bind them.

    ``read_ref`` re-checks the pinned bytes and sha256 and those bytes are what
    runs, so no cached module or later edit can stand in for the registration.
    """
    from orchestration_common import read_ref
    raw = read_ref(source_ref, "registered transport source")
    namespace = dict(__name__="jev_registered_transport", __file__=source_ref["path"])
    exec(compile(raw, source_ref["path"], "exec"), namespace)
    require(callable(namespace.get("bind")), "the registered transport source has no bind")
    return namespace["bind"](connection, credential, model=model)


def record_advice(co, node_id: str, *, registration=None, connection=None, credential=None) -> dict:
    """Production off-arm worker advice for one deliberately unbound node, before its decision.

    The caller of ``run_and_record`` on a real run. It lives here, outside the
    frozen adapter closure (design Q9 keeps the worker out), and calls the
    coordinator instead of being imported by it. A registered campaign forbids
    third-party model calls (``third_party_api`` is False), so its arm is
    advisory-off: no transport, no budget, zero requests, and only the
    worker's exact journal is written; no advice body is published. It takes
    the runner and state locks like publish-decision, requires a PENDING node
    with no claim or decision, fixes the ledger directory per run and uses the
    bind path's own ``lane_facts``. An existing journal refuses re-entry before
    the worker runs again. An on arm needs its own explicit JEV registration
    (``jev_registration``); its arm comes from that pinned file, and its send
    and parse are injected in process by the caller only. The CLI injects
    neither, so an on registration there is refused before any request.
    """
    import jev_registration
    import orchestration_binding
    from jev_transport import build_transport
    from orchestration_common import exclusive_lock
    from orchestration_coordinator import State
    require(node_id in co.nodes, "unknown frozen node")
    reg = jev_registration.load(registration, run_id=co.spec["run_id"], node_id=node_id)
    on = reg["arm"] == "advisory-on"
    require(not on or (connection is not None and isinstance(credential, str) and bool(credential)),
            "an on registration needs an in-process send: a caller-held connection and credential; "
            "nothing is read from the environment")
    require(on or (connection is None and credential is None), "the off arm takes no transport")
    send, parse = (load_transport(reg["transport_source"], connection, credential, model=reg["model"])
                   if on else (None, None))
    require(orchestration_binding.unbound_plan(co.plan) and orchestration_binding.is_unbound(co.nodes[node_id]),
            "only a deliberately unbound frozen node gets worker advice")
    require(co.campaign.get("third_party_api") is False,
            "this registration forbids third-party calls; an on arm needs its own JEV registration")
    run_id = co.spec["run_id"]
    with exclusive_lock(co.root / "nodes" / node_id / "runner.lock"), co.state_lock(new_work=True):
        identity = co._gate(new_work=True)
        states, data = co.state()
        require(states[node_id] == State.PENDING, "worker advice precedes the claim")
        require(not (co.root / "claims" / (node_id + ".json")).exists(), "original claim intent exists; observe only")
        require(not (co.root / "decisions" / (node_id + ".json")).exists(),
                "original published decision exists; observe only")
        lanes = orchestration_binding.lane_facts(co, node_id, identity, states, data)
        record = jev_registration.record_path(co.root, node_id)
        if not (co.root / "nodes" / node_id / "advice-worker-journal.json").exists():
            # The arm is pinned before the worker runs; the consumer re-derives
            # it from this record, never from whether an advice file exists.
            require(not (record.exists() or record.is_symlink()),
                    "a registration record exists without a journal; observe only")
            write_json(record, dict(schema=jev_registration.RECORD_SCHEMA, run_id=run_id, node_id=node_id,
                                    arm=reg["arm"], registration=reg["registration"]), exclusive=True)
        answer = run_and_record(co.root / "nodes" / node_id, ledger_root=co.root / LEDGER_DIRECTORY, run_id=run_id,
                                decision=node_id, state_text=task_state(co, node_id), lanes=lanes, bound=False,
                                arm=reg["arm"], marker="JEV_ADVICE_%s_%s" % (run_id, node_id), provider=reg["provider"],
                                budget=reg["budget"], model=reg["model"],
                                transport=build_transport(send, model=reg["model"], parse=parse) if on else None)
        published = None
        if on:
            # The original publisher: a pinned reference the supervisor reads
            # and the decision cites. The off arm publishes nothing.
            from upgrade_advice_publish import advice_body, advice_marker, advice_tag, publish_advice
            body = advice_body(co.campaign_ref, run_id, node_id, answer["advice"], answer["request"],
                               marker=advice_marker(run_id, node_id, advice_tag(answer["advice"])),
                               input_units=co.campaign["inputs"])
            published = publish_advice(co.root, run_id, node_id, body)
    require(on or (answer["model_requests"] == 0 and answer["ledger"] is None), "the off arm made a request")
    return dict(schema="jev-worker-advice-record-v1", run_id=run_id, node_id=node_id, arm=reg["arm"],
                registration=reg["registration"], registration_record=str(record),
                worker_status=answer["advice"]["status"], model_requests=answer["model_requests"],
                journal=answer["journal"], ledger=answer["ledger"],
                advice_sha256=published["body_sha256"] if published else None,
                notice=published["notice"] if published else None)


INBOX_DIRECTORY = "jev-inbox"


def record_inbox_order(co, node_id: str, event_id: str, messages: list, *, registration=None,
                       connection=None, credential=None) -> dict:
    """DP2: order one supervisor inbox event under the coordinator gate (R505 D1).

    Same arm source, gate, run-wide ledger and registered transport bytes as
    ``record_advice``; the node's one registration covers DP1 and DP2. The
    order record is published exclusively per event, so a repeated event is
    refused, and bodies judged in earlier events of this run are reused, never
    asked again. Eligibility comes from the caller's code checks; each body is
    re-verified against its sha256/bytes. The off arm makes 0 requests.
    """
    import json
    import jev_inbox
    import jev_registration
    from jev_transport import build_transport
    from orchestration_common import exclusive_lock
    require(node_id in co.nodes, "unknown frozen node")
    require(isinstance(event_id, str) and 0 < len(event_id) <= 80
            and all(c.isascii() and (c.isalnum() or c in "_.-") for c in event_id), "inbox event id required")
    reg = jev_registration.load(registration, run_id=co.spec["run_id"], node_id=node_id)
    on = reg["arm"] == "advisory-on"
    require(not on or (connection is not None and isinstance(credential, str) and bool(credential)),
            "an on registration needs an in-process send: a caller-held connection and credential; "
            "nothing is read from the environment")
    require(on or (connection is None and credential is None), "the off arm takes no transport")
    send, parse = (load_transport(reg["transport_source"], connection, credential, model=reg["model"])
                   if on else (None, None))
    run_id, directory = co.spec["run_id"], co.root / INBOX_DIRECTORY
    path = directory / (event_id + ".json")
    with exclusive_lock(co.root / "nodes" / node_id / "runner.lock"), co.state_lock(new_work=True):
        co._gate(new_work=True)
        require(not (path.exists() or path.is_symlink()), "this inbox event is already ordered; observe only")
        directory.mkdir(exist_ok=True)
        judged = {}
        for prior in sorted(directory.glob("*.json")):
            for decision in json.loads(prior.read_text())["order"]["decisions"]:
                judged.setdefault(decision["sha256"], dict(front=decision["front"]))
        transport = build_transport(send, model=reg["model"], parse=parse) if on else None
        transport_for = (lambda decision: ledgered_transport(
            transport, ledger_root=co.root / LEDGER_DIRECTORY, run_id=run_id, arm=reg["arm"], decision=decision,
            marker="JEV_INBOX_%s_%s" % (run_id, event_id), provider=reg["provider"], budget=reg["budget"])) if on else None
        order = jev_inbox.order_inbox(messages, arm=reg["arm"], transport_for=transport_for,
                                      model=reg["model"] if on else None, judged=judged)
        value = dict(schema="jev-inbox-order-record-v1", run_id=run_id, node_id=node_id, event_id=event_id,
                     arm=reg["arm"], registration=reg["registration"], order=order)
        write_json(path, value, exclusive=True)
    require(on or order["model_requests"] == 0, "the off arm made a request")
    return value


def main(argv=None):
    """``record-advice`` for a real frozen campaign: off arm, or a pinned registration.

    Same campaign anchoring, Runner and frozen-adapter self-check as
    ``orchestration_run``. No key, connection or credential is ever read here,
    so only the off arm can run from this CLI.
    """
    import argparse
    import json
    import sys
    from orchestration_campaign import anchored_ref
    from orchestration_run import Runner
    parser = argparse.ArgumentParser(description=main.__doc__)
    for name in ("--campaign", "--campaign-sha256", "--run", "--node"):
        parser.add_argument(name, required=True)
    parser.add_argument("--registration")
    parser.add_argument("--registration-sha256")
    args = parser.parse_args(argv)
    try:
        require((args.registration is None) == (args.registration_sha256 is None),
                "a registration needs its separately supplied SHA256")
        registration = None
        if args.registration is not None:
            from orchestration_common import file_ref
            registration = file_ref(args.registration)
            require(registration["sha256"] == args.registration_sha256, "registration hash mismatch")
        runner = Runner(anchored_ref(args.campaign, args.campaign_sha256))
        require(Path(__file__).resolve() == Path(runner.campaign["adapter"]["path"]) / "scripts/jev_advice_run.py",
                "invoke the complete frozen adapter, not the mutable checkout")
        result = record_advice(runner.co(args.run), args.node, registration=registration)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REJECTED", error=str(exc), input_retry_authorized=False), ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
