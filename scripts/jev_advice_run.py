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
STOP_REASON = "STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER"


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


def read_credential(fd: int) -> str:
    """Read the on-arm key once from an inherited pipe/FIFO fd, then close it (R545 P2).

    The key arrives only in process: never argv, environment, file or log.
    A regular file, socket or tty is refused before any byte is read (R546-F1).
    """
    import os
    import stat
    chunks = []
    try:
        require(stat.S_ISFIFO(os.fstat(fd).st_mode), "the credential fd must be an inherited pipe or FIFO")
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    value = b"".join(chunks).decode("ascii").strip()
    require(value and not any(c.isspace() for c in value), "the inherited credential fd held no single key")
    return value


def registered_connection(source_ref):
    """The one caller-held connection, built by the same pinned transport bytes that ``bind`` runs."""
    from orchestration_common import read_ref
    raw = read_ref(source_ref, "registered transport source")
    namespace = dict(__name__="jev_registered_transport", __file__=source_ref["path"])
    exec(compile(raw, source_ref["path"], "exec"), namespace)
    require(callable(namespace.get("connect")), "the registered transport source has no connect")
    return namespace["connect"]()


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
    if "semantic_input" in co.campaign:
        from upgrade_benchmark import validate_registration
        paired = validate_registration(co.campaign["registration"])
        require(reg["arm"] == paired["groups"][co.campaign["session_group"]]["advisory"],
                "DP1 advice arm differs from the original paired group")
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
                notice=published["notice"] if published else None,
                read_command=advice_read_command(co.campaign, published["notice"]) if published else None)


def advice_read_command(campaign, notice) -> str:
    """The one complete body read the bind gate requires before publish-decision (R556).

    The same frozen reader and arguments that ``Coordinator`` re-derives for the
    advice chain, so the supervisor's original call is the evidence it checks.
    """
    from upgrade_body_read import read_command
    return read_command(campaign["python"]["path"], campaign["controller"]["path"], notice)


INBOX_DIRECTORY = "jev-inbox"


def record_inbox_order(co, node_id: str, event_id: str, messages: list | None, *, registration=None,
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
        if on and ledger_summary(co.root / LEDGER_DIRECTORY)["stopped"]:
            raise ContractError("STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER")
        require(not (path.exists() or path.is_symlink()), "this inbox event is already ordered; observe only")
        batch = None
        if "semantic_input" in co.campaign:
            from orchestration_native import NativeReader
            from orchestration_common import utc_now
            from upgrade_benchmark import validate_registration
            from upgrade_semantic_inputs import native_batch
            paired = validate_registration(co.campaign["registration"])
            require(reg["arm"] == paired["groups"][co.campaign["session_group"]]["advisory"],
                    "DP2 transport arm differs from the original paired group")
            require(messages is None, "registered S2 eligibility is derived from native sources, not caller labels")
            readers = {lane: NativeReader(session, fixtures=co.fixtures)
                       for lane, session in co.campaign["sessions"].items()}
            native_end = {lane: reader.snapshot() for lane, reader in readers.items()}
            batch = native_batch(co.campaign, co.header, native_end, readers, not_after=utc_now())
            require(batch["ready"], "semantic inputs remain pending or UNKNOWN; no JEV request")
            messages = batch["messages"]
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
        if batch is not None:
            from upgrade_semantic_inputs import read_requests
            value.update(semantic_inputs=batch["attribution"], native_end=native_end,
                         semantic_input=co.campaign["semantic_input"],
                         read_requests=read_requests(co.campaign, batch, order["order"]),
                         evidence_class="offline_fixture" if co.fixtures else "native_s2")
        write_json(path, value, exclusive=True)
        if on and ledger_summary(co.root / LEDGER_DIRECTORY)["stopped"]:
            raise ContractError("STOP_AFFECTED_JEV_TEST_AND_REPORT_TO_USER")
    require(on or order["model_requests"] == 0, "the off arm made a request")
    return value


def handle(co_for, request: dict, *, connection, credential, state: dict) -> dict:
    """One on-arm request against the run's single held connection and credential (R545/R548 P2).

    ``co_for(run_id)`` returns the run's coordinator. Each request names a
    separately pinned per-node registration; when the worker holds a
    transport, that registration must name the same pinned transport source.
    The record functions raise the ledger's STOP on a 401/403; that latches,
    and every later request is answered STOPPED with no call.
    """
    from orchestration_common import file_ref
    if state.get("stopped"):
        return dict(status="STOPPED", reason=state["stopped"], model_requests=0)
    require(isinstance(request, dict) and request.get("op") in ("advice", "inbox"), "unknown worker request")
    for key in ("campaign_sha256", "run"):
        require(state.get(key) is None or request.get(key) == state[key], "this worker serves another campaign or run")
    registration = file_ref(request["registration"])
    require(registration["sha256"] == request["registration_sha256"], "registration hash mismatch")
    if state.get("transport_source") is not None:
        import jev_registration
        reg = jev_registration.load(registration, run_id=request["run"], node_id=request["node"])
        require(reg["transport_source"] == state["transport_source"],
                "this registration names another transport than the one this worker holds")
    co = co_for(request["run"])
    try:
        if request["op"] == "advice":
            return record_advice(co, request["node"], registration=registration,
                                 connection=connection, credential=credential)
        return record_inbox_order(co, request["node"], request["event"], None, registration=registration,
                                  connection=connection, credential=credential)
    except ContractError as exc:
        if str(exc) == STOP_REASON:
            state["stopped"] = STOP_REASON
        raise


def serve(listener, co_for, *, connection, credential, bound=None, max_requests=None) -> int:
    """Answer one JSON request per accepted socket connection until ``close``.

    ``bound`` pins the served campaign hash, run and transport source. The
    connection and credential references are dropped as soon as a STOP
    latches; later requests are answered STOPPED without them.
    """
    import json
    state, served = dict(bound or {}), 0
    held = dict(connection=connection, credential=credential)
    del connection, credential
    while max_requests is None or served < max_requests:
        peer, _ = listener.accept()
        closing = False
        with peer, peer.makefile("rwb") as stream:
            try:
                request = json.loads(stream.readline())
                closing = isinstance(request, dict) and request.get("op") == "close"
                reply = dict(status="CLOSED", served=served) if closing else handle(co_for, request, state=state, **held)
            except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
                reply = dict(status="REJECTED", error=str(exc), input_retry_authorized=False)
            if state.get("stopped"):
                held.update(connection=None, credential=None)
            stream.write(json.dumps(reply, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b"\n")
        served += 1
        if closing:
            break
    return served


def bind_private_socket(path):
    """A fresh AF_UNIX listener in a directory only this user can enter."""
    import os
    import socket
    import stat
    path = Path(path)
    require(path.is_absolute() and len(os.fsencode(str(path))) <= 103, "the socket path must be absolute and short")
    require(not (path.exists() or path.is_symlink()), "the socket path already exists; observe only")
    parent = os.lstat(path.parent)
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and not parent.st_mode & 0o077,
            "the socket directory must be a private directory of this user")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        listener.bind(str(path))
    except OSError:
        listener.close()
        raise
    finally:
        os.umask(old)
    listener.listen(1)
    return listener


def ask(path, request: dict) -> dict:
    """Send one request line to the run's worker and return its reply line."""
    import json
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(path))
        with client.makefile("rwb") as stream:
            stream.write(json.dumps(request, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b"\n")
            stream.flush()
            line = stream.readline()
    require(line.endswith(b"\n"), "the worker closed without a complete reply")
    return json.loads(line)


def main(argv=None):
    """``record-advice`` for a real frozen campaign: off arm, or a pinned registration.

    Same campaign anchoring, Runner and frozen-adapter self-check as
    ``orchestration_run``. The off arm reads no key. An on arm reads its key
    once from ``--credential-fd``: directly for one call, or with
    ``--serve-socket`` as the run's one worker, which the supervisor's
    ``--worker-socket`` calls then reach without any key.
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
    parser.add_argument("--credential-fd", type=int,
                        help="on arm only: inherited pipe/FIFO fd holding the key (never argv/env/file)")
    parser.add_argument("--inbox-event", help="record the registered DP2 inbox order for this event id")
    parser.add_argument("--serve-socket", help="on arm: hold the key and connection as this run's worker here")
    parser.add_argument("--worker-socket", help="send this DP1/DP2 request to the run's worker; no key here")
    args = parser.parse_args(argv)
    try:
        require(not (args.serve_socket and args.worker_socket), "serve or ask, not both")
        if args.worker_socket is not None:
            require(args.registration is not None and args.credential_fd is None,
                    "a worker request names its pinned registration and carries no key")
            request = dict(op="inbox" if args.inbox_event is not None else "advice", campaign_sha256=args.campaign_sha256,
                           run=args.run, node=args.node, registration=args.registration,
                           registration_sha256=args.registration_sha256, event=args.inbox_event)
            reply = ask(args.worker_socket, request)
            print(json.dumps(reply, ensure_ascii=False, sort_keys=True, allow_nan=False))
            return 2 if reply.get("status") in ("REJECTED", "STOPPED") else 0
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
        co = runner.co(args.run)
        if args.serve_socket is not None:
            import jev_registration
            require(registration is not None and args.credential_fd is not None,
                    "the run's worker needs its pinned on registration and an inherited key fd")
            reg = jev_registration.load(registration, run_id=co.spec["run_id"], node_id=args.node)
            require(reg["arm"] == "advisory-on", "the off arm reads no credential and opens no transport")
            # Bind first: a refused path leaves the key unread in its pipe.
            listener = bind_private_socket(args.serve_socket)
            try:
                # Keyword arguments evaluate in order: pinned bytes are checked before the key is read.
                served = serve(listener, lambda run_id: co, connection=registered_connection(reg["transport_source"]),
                               credential=read_credential(args.credential_fd),
                               bound=dict(campaign_sha256=args.campaign_sha256, run=co.spec["run_id"],
                                          transport_source=reg["transport_source"]))
            finally:
                listener.close()
                Path(args.serve_socket).unlink(missing_ok=True)
            print(json.dumps(dict(status="CLOSED", served=served), sort_keys=True))
            return 0
        connection = credential = None
        if args.credential_fd is not None:
            import jev_registration
            reg = jev_registration.load(registration, run_id=co.spec["run_id"], node_id=args.node)
            require(reg["arm"] == "advisory-on", "the off arm reads no credential and opens no transport")
            credential = read_credential(args.credential_fd)
            connection = registered_connection(reg["transport_source"])
        result = (record_inbox_order(co, args.node, args.inbox_event, None, registration=registration,
                                     connection=connection, credential=credential)
                  if args.inbox_event is not None else
                  record_advice(co, args.node, registration=registration,
                                connection=connection, credential=credential))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="REJECTED", error=str(exc), input_retry_authorized=False), ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
