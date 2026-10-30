"""Join one advice publication, its Codex read and the decision that cites it.

Every judgment here is a named module-level callable registered in
:data:`ADVICE_PREDICATES`, so each one can be excised, flipped and restored on
its own. The four shared matcher predicates (``valid_command``,
``calls_and_results``, ``codex_stdout``, ``exact_body_output``) are reused
*unchanged* and are deliberately **not** registered: excising them also flips
the attribution and input-provenance negatives, so a flip there would prove
nothing about this chain.

What the read proves and what it does not: the registered proof is the exact
``upgrade-body-read-v1`` envelope plus the ``body`` field's bytes and sha256 -
not the sha256 of the whole tool output, which can never equal the advice sha
because the body travels inside a JSON envelope. Exposure in a tool result is
not semantic acceptance; the adopted meaning shows up later, in the real
decision and its result.

What counts as advisory consumption: ``CONSUMED`` and
``counted_as_advisory_consumption`` require the whole native chain - one
complete original read, a non-fallback advisor, the publisher's own joined
call and result, and the binding's own natively verified bind control call. A
decision that lives outside the publisher's exclusive path is the older
exclusive-file shape; ROOT R94 section 2 keeps it as a diagnostic, so it is
reported as ``DIAGNOSTIC_NOT_NATIVELY_JOINED`` with ``decided_at: null`` and
its self-written stamp under ``diagnostic_decided_at``. ``order_basis`` names,
per stamp, which record supplied it, so a true ``order_verified`` can never be
read as a native proof of a leg that has none.

The window is fenced on both sides and bounded. It opens at the advice fence.
For a published decision it closes at the bind control call that
``bind_call_evidence`` re-derives (design Q25), so nothing appended after that
call changes the publish join or the read evidence; the bind call's own result
is then the first output for its call_id, which settles it once it exists. The
older diagnostic shape, which is never counted, keeps the binding's own offset
or the current snapshot as its bound. An interval that exceeds the bound
raises ``ADVICE_READ_WINDOW_PENDING`` instead of returning a false negative,
and the from-zero scan pattern of ``upgrade_child_budget`` is deliberately not
copied.

In the read < decided < bound < claim order of a published decision, the first
three stamps are native record times where the arm has them (the off arm has
no read): the read result, the fixed publisher's success result and the bind
control call. A claim has no one native control record to join: it is issued
either by a direct ``claim`` call or from inside an ``advance`` call, whose
argv also carries its step and time limits and which may take up to 32
actions, so no reconstructed script identifies one claim. Its leg is therefore
ordered by evidence rather than by a stamp. On the Codex lane the bind result
must end before the claim's own native start offset; on a Claude lane the
claim pins the sha256 of the binding that result printed. Its ``started_at`` is
the coordinator's wall clock, checked only for contradiction and labelled so in
``order_basis``.
"""
from __future__ import annotations

import re
from pathlib import Path

from orchestration_common import (ContractError, contract_errors, count, file_ref, json_ref, parse_time,
                                  require, strict_json)
from upgrade_body_read_evidence import RTK_NOTICE, body_read_evidence, calls_and_results, codex_stdout

SCHEMA = "upgrade-advice-chain-v1"
READ_PROOF = "exact_envelope_plus_body_sha256_not_whole_output_sha256"
DECISION_WRITE_PROOF = "NOT_RECOGNIZED_AT_THIS_HEAD"
WINDOW_RECORDS, WINDOW_BYTES = 65536, 32 * 1024 * 1024
# A worker status that is a fallback, not advice anybody acted on. STOPPED_AUTH
# is not a jev_advice status at this HEAD; it is named here because R62's
# negative list names it, and an unknown future label must not be counted.
FALLBACK_STATUSES = frozenset({"NOT_CONFIGURED", "JEV_ERROR", "NOT_CALLED", "STOPPED_AUTH"})


def advice_session_consistent(record, reader) -> bool:
    """No record in the window may name a different original native session.

    Same semantics as ``upgrade_communication_attribution._session_valid``, kept
    as a separate callable on purpose: ``NativeReader.records`` re-checks only
    Claude sessions, and widening it there would move ``orchestration_timeline``,
    ``orchestration_preparation`` and ``action_summary`` behaviour. A test
    asserts the two agree on the same fixtures.
    """
    if reader.provider == "claude":
        return record.get("sessionId") == reader.session["session_id"] and record.get("isSidechain") is not True
    if record.get("type") == "session_meta":
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            return False
        return all(payload[key] == reader.session["session_id"]
                   for key in ("id", "session_id") if key in payload)
    return True


def _window(reader, fence, until):
    """Pinned records of one fenced, bounded interval; never EOF-to-zero."""
    start = reader.verify_pin(fence) if isinstance(fence, dict) else count(fence, "advice fence offset")
    end = count(until, "advice window end")
    require(start <= end, "advice read window is inverted")
    records, scanned = [], 0
    for ref, record in reader.records(start, end):
        scanned += ref["length"]
        require(len(records) < WINDOW_RECORDS and scanned <= WINDOW_BYTES,
                "ADVICE_READ_WINDOW_PENDING: original interval exceeds bound")
        require(globals()["advice_session_consistent"](record, reader),
                "CONTRADICTORY_NATIVE_SESSION in the advice read window")
        records.append((ref, record))
    return records


def advice_read_evidence(reader, *, fence, until, notice, command, not_before, not_after) -> list:
    """Exact call/result pairs for this notice inside the fenced window.

    The shared matcher decides what a complete read is; this function decides
    the window, bounds it, and re-pins every matched record afterwards so a
    match cannot survive a journal that changed under it.
    """
    records = _window(reader, fence, until)
    evidence = body_read_evidence(records, provider=reader.provider,
                                  session_id=reader.session["session_id"], notice=notice,
                                  command=command, not_before=not_before, not_after=not_after)
    for observed in evidence:
        reader.record(observed["call_record"])
        reader.record(observed["result_record"])
    return evidence


def read_is_full(evidence) -> bool:
    """Exactly one valid call/result pair is a FULL read; two are ambiguous."""
    return len(evidence) == 1


def decision_cites_this_advice(decision, advice_sha256) -> bool:
    """The decision names this run's advice body, not another one."""
    return (isinstance(advice_sha256, str) and len(advice_sha256) == 64
            and decision.get("advice_sha256") == advice_sha256)


def binding_cites_decision(binding, decision_ref) -> bool:
    """The binding pins the same decision file by path, bytes and sha256."""
    pinned = binding.get("decision")
    if not isinstance(pinned, dict) or not isinstance(decision_ref, dict):
        return False
    return all(pinned.get(key) == decision_ref.get(key) for key in ("path", "bytes", "sha256"))


def route_within_recomputed(binding) -> bool:
    """The bound lane came from the candidates code recomputed at bind time."""
    return binding.get("lane") in tuple(binding.get("eligible_lanes") or ())


def ordered_chain(read_at, decided_at, bound_at, claim_started_at) -> bool:
    """read < decided < bound < claim, over whichever stamps exist yet.

    ``None`` means "not observed yet" - the off arm has no read and a node that
    is bound but not claimed has no claim start. A present stamp is never
    skipped, so an out-of-order pair always refuses.
    """
    stamps = [value for value in (read_at, decided_at, bound_at, claim_started_at) if value is not None]
    moments = [parse_time(value, "chain timestamp") for value in stamps]
    return all(earlier < later for earlier, later in zip(moments, moments[1:]))


def off_arm_has_no_advice_file(node_root) -> bool:
    """No advice body exists under this node at all on the off arm."""
    directory = Path(node_root) / "advice"
    return not directory.exists() or not any(directory.iterdir())


def off_arm_worker_requests_zero(journal) -> bool:
    """The worker made no model request; an absent journal is also zero."""
    if journal is None:
        return True
    if not isinstance(journal, list):
        return False
    return all(isinstance(entry, dict) and entry.get("model_requests") == 0 for entry in journal)


def off_arm_has_no_advice_read(evidence) -> bool:
    """No call in the fenced window named an advice body on the off arm.

    ``evidence`` is ``advice_read_attempts``'s list, which matches call
    arguments only; see that function for what it does not detect.
    """
    return not evidence


def fallback_not_counted_as_consumed(worker_status) -> bool:
    """A fallback or stopped advisor is never counted as advisory consumption."""
    return worker_status not in FALLBACK_STATUSES


# Any run's or node's advice directory, absolute or relative: "nodes/<id>/advice"
# followed by a path separator, a quote, whitespace or the end. A sibling file
# such as nodes/<id>/advice-worker-journal.json is not an advice body.
ADVICE_PATH = re.compile(r"(?:^|[\\/\"'\s=])nodes[\\/][A-Za-z0-9][A-Za-z0-9_.:-]{0,63}[\\/]advice(?=[\\/\"'\s]|$)")
# A concrete advice marker (upgrade_advice_publish.advice_marker): prefix, run,
# node and the 16-hex advice tag. Only a call that names one can match.
ADVICE_MARKER = re.compile(r"JEV_ADVICE_[A-Za-z0-9][A-Za-z0-9_.:-]*_[0-9a-f]{16}(?![0-9a-f])")


def advice_read_attempts(records, node_root, provider) -> list:
    """Window calls that name an advice body, however it is spelled.

    Deliberately broader than ``valid_command``: this feeds a refusal, so an
    unparsed or unusual wrapper must not let an off-arm read slip through. A
    call counts when its arguments contain this node's advice directory, *any*
    run's or node's advice directory in absolute or relative form, or a
    concrete advice marker. A hit is therefore evidence of an attempt, never
    proof of a complete read - proof is only ever ``advice_read_evidence``'s
    exact pair.

    Only call arguments are matched, never tool results. Advice bodies and
    markers also occur in this repository's own test fixtures and can appear in
    their output, and an off-arm bind refused by its own window can never be
    retried (the offending record stays inside every later window), so a
    result-content rule would turn ordinary test runs on the off arm into
    permanently unbindable nodes. The trade-offs are stated rather than hidden:
    a read whose argv names no advice path or marker (for example a path
    assembled at run time) is not detected, and a call whose argv names a
    concrete ``nodes/<id>/advice`` path for another reason (a literal code
    search, say) is refused.
    """
    target = str(Path(node_root) / "advice")
    found = []
    for ref, record in records:
        calls, _ = calls_and_results(record, provider)
        for call_id, name, arguments in calls:
            text = str(arguments)
            if target in text or ADVICE_PATH.search(text) or ADVICE_MARKER.search(text):
                found.append(dict(kind="ADVICE_READ_ATTEMPT", call_id=call_id, name=name, call_record=ref))
    return found


PUBLICATION_SCHEMA = "orchestration-decision-publication-v1"
PUBLICATION_FIELDS = {"schema", "run_id", "node_id", "supervisor_session", "decision", "publisher", "inputs"}


def publish_call_is_the_frozen_script(arguments, script) -> bool:
    """The native call input equals the reconstructed publisher script exactly.

    One byte comparison pins the frozen Python, the frozen entrypoint path, the
    campaign path and sha, run, node, workdir, the output cap and every decision
    field in its fixed order. Nothing about the call is parsed or normalised.
    """
    return isinstance(arguments, str) and isinstance(script, str) and arguments == script


def publication_pins_the_decision(envelope, expected) -> bool:
    """The printed envelope is complete and pins exactly this decision file.

    ``expected`` carries the run, node, supervisor session, the decision file's
    *current* path/bytes/sha256, the frozen publisher pin and the input pins. A
    missing, extra or different field refuses, so a truncated or foreign
    envelope never joins.
    """
    if not isinstance(envelope, dict) or set(envelope) != PUBLICATION_FIELDS:
        return False
    if envelope.get("schema") != PUBLICATION_SCHEMA:
        return False
    return all(envelope.get(name) == expected.get(name) for name in PUBLICATION_FIELDS - {"schema"})


def publish_follows_the_read(read, publish) -> bool:
    """On the on arm the publish call starts after the read result ends.

    Both the record order (offsets) and the native timestamps must agree, so a
    publish that the transcript places before its read can never be reordered
    by a copied or rounded timestamp.
    """
    if not isinstance(read, dict) or not isinstance(publish, dict):
        return False
    try:
        read_end = read["result_record"]["offset"] + read["result_record"]["length"]
        read_at = parse_time(read["timestamp"], "read result timestamp")
        call_at = parse_time(publish["call_timestamp"], "publish call timestamp")
    except (KeyError, TypeError, ContractError):
        return False
    return publish["call_record"]["offset"] >= read_end and call_at > read_at


def decision_publish_evidence(reader, *, fence, until, script) -> dict:
    """The one original publisher call/result pair for this exact script.

    Requires one supervisor ``custom_tool_call`` whose input equals the script,
    no other call record of any input sharing its call_id, exactly one output
    record for that call_id, a terminal rc0 result whose complete stdout is the
    JSON envelope, and all of it inside the fenced window.

    A transiently refused publish (a busy lock, a gate) leaves its own call and
    failed result, and the retry is a byte-identical call, because the decision
    is published once and never republished. Several identical calls are
    therefore accepted only when every one already has exactly one result and
    exactly one of those results is a terminal rc0 success: that one call is
    the publisher call. An identical call without a result may still be
    running, and two successes cannot both have created one exclusive file, so
    both refuse.

    The window ends at ``until`` - the bind control call - and never at the
    live EOF (design Q25). A frozen binding is re-derived on every claim and
    replay, so a record appended after the bind (for example a repeated
    publish command the publisher itself refused) must not change the verdict
    of a binding that already passed: the verdict depends on the frozen
    evidence, not on when it is evaluated. The stdout is extracted with the
    shared ``codex_stdout`` exactly as the advice read does; ``codex_stdout``
    itself is unchanged.
    """
    end = count(until, "decision publish window end")
    records = _window(reader, fence, end)
    calls, results, call_ids = [], {}, {}
    for ref, record in records:
        found, outputs = calls_and_results(record, "codex")
        for call_id, name, arguments in found:
            if isinstance(call_id, str) and call_id:
                call_ids[call_id] = call_ids.get(call_id, 0) + 1
            if (record.get("payload", {}).get("type") == "custom_tool_call"
                    and name in ("exec", "functions.exec")
                    and globals()["publish_call_is_the_frozen_script"](arguments, script)):
                calls.append((ref, record, call_id))
        for call_id, value, _ in outputs:
            if isinstance(call_id, str) and call_id:
                results.setdefault(call_id, []).append((ref, record, value))
    require(calls, "DECISION_PUBLISH_CALL: exactly one original publisher call required")
    for _ref, _record, found_id in calls:
        require(isinstance(found_id, str) and found_id, "DECISION_PUBLISH_CALL: the call has no call_id")
        require(call_ids.get(found_id) == 1,
                "DECISION_PUBLISH_CALL: another call record shares the publisher call's call_id")
    if len(calls) > 1:
        # A refused publish leaves its call and its failed result behind, and
        # the decision is never republished, so a retry is one more identical
        # call. Each must already have exactly one result (an unanswered
        # identical call may still be running), and exactly one of them may be
        # the terminal rc0 success that created the decision.
        require(all(len(results.get(found_id, [])) == 1 for _ref, _record, found_id in calls),
                "DECISION_PUBLISH_CALL: exactly one original publisher call required; "
                "an identical call has no single result")
        succeeded = [entry for entry in calls
                     if isinstance(codex_stdout(results[entry[2]][0][2]), str)]
        require(len(succeeded) == 1,
                "DECISION_PUBLISH_CALL: exactly one original publisher call required; "
                "identical calls must include exactly one success")
        calls = succeeded
    call_ref, call_record, call_id = calls[0]
    require(call_ref["offset"] + call_ref["length"] <= end, "DECISION_PUBLISH_CALL: outside the fenced window")
    matched = results.get(call_id, [])
    require(len(matched) == 1, "DECISION_PUBLISH_RESULT: exactly one output record for the call required")
    result_ref, result_record, value = matched[0]
    payload = result_record.get("payload", {})
    require(payload.get("type") == "custom_tool_call_output",
            "DECISION_PUBLISH_RESULT: the output belongs to another tool family")
    require(result_ref["offset"] >= call_ref["offset"] + call_ref["length"]
            and result_ref["offset"] + result_ref["length"] <= end,
            "DECISION_PUBLISH_RESULT: the output is outside the fenced window or precedes its call")
    stdout = codex_stdout(value)
    require(isinstance(stdout, str), "DECISION_PUBLISH_RESULT: not a complete terminal rc0 result")
    if stdout.startswith(RTK_NOTICE):
        stdout = stdout[len(RTK_NOTICE):]
    try:
        envelope = strict_json(stdout, "decision publication envelope")
    except ContractError as exc:
        raise ContractError("DECISION_PUBLISH_RESULT: the envelope is truncated or not JSON") from exc
    call_at = parse_time(call_record.get("timestamp"), "publish call timestamp")
    result_at = parse_time(result_record.get("timestamp"), "publish result timestamp")
    require(result_at >= call_at, "DECISION_PUBLISH_RESULT: the output is stamped before its call")
    reader.record(call_ref)
    reader.record(result_ref)
    return dict(kind="DECISION_PUBLISH_OBSERVED", call_id=call_id, call_record=call_ref,
                result_record=result_ref, call_timestamp=call_record.get("timestamp"),
                timestamp=result_record.get("timestamp"), envelope=envelope)


def _bound_window(reader, binding):
    """Upper bound: the bind control call, else the current EOF snapshot.

    Q25: the window's upper bound is the bind call, not EOF. A binding written
    before any bind control record exists (an offline fixture, or a diagnostic
    read of a partially built run) falls back to the current snapshot, which is
    still a fence, not an unbounded tail scan.
    """
    bound = binding.get("bound_native")
    if isinstance(bound, dict):
        return count(bound.get("offset"), "bind call offset")
    return reader.snapshot()["offset"]


def published_decision_path(co, key):
    """Where the fixed publisher, and only it, creates a decision."""
    return co.root / "decisions" / (str(key) + ".json")


def decision_is_published(co, key, decision_ref) -> bool:
    """The decision lives at the fixed publisher's exclusive path for this node.

    Only such a decision can be natively joined. Any other path is the older
    exclusive-file shape, which ROOT R94 section 2 keeps as a diagnostic only:
    it is evaluated and reported, never counted as proven consumption.
    """
    return Path(decision_ref["path"]) == published_decision_path(co, key)


def bind_call_evidence(co, key, reader, binding, decision_ref) -> dict:
    """The binding's own bind control call, re-derived from the journal.

    A published decision is bound only by ``bind-unbound``, so its binding must
    pin the exact original bind call for that decision path, and ``bound_at``
    must be that record's native timestamp. Nothing is taken from the binding's
    own fields: a missing pin, a pin to any other record, or a self-written
    ``bound_at`` refuses. There is no EOF fallback on this path.
    """
    from orchestration_campaign import control_script, native_control
    pin = binding.get("bound_native")
    require(isinstance(pin, dict), "BIND_CALL: the binding of a published decision pins no bind control call")
    # Exactly the path expression Coordinator.bind_unbound passes, so the two
    # scripts can only ever agree byte for byte.
    script = control_script(co.campaign, co.campaign_ref, "bind-unbound", co.spec["run_id"],
                            node_id=key, decision=str(published_decision_path(co, key)))
    try:
        at = native_control(reader, pin, script)
        stamped = parse_time(binding.get("bound_at"), "binding bound_at")
    except ContractError as exc:
        raise ContractError("BIND_CALL: the pinned record is not the original bind control call") from exc
    require(stamped == at, "BIND_CALL: bound_at is not the bind call's own native timestamp")
    require(count(pin.get("offset"), "bind call offset") >= co.header["native_start"]["codex"]["offset"],
            "BIND_CALL: the bind call predates this run")
    record = reader.record(pin)
    return dict(kind="BIND_CALL_OBSERVED", call_id=record["payload"].get("call_id"), call_record=pin,
                timestamp=record.get("timestamp"))


def live_bind_call(reader, script, *, after) -> dict:
    """The one bind control call that is running now: the only one with no result.

    ``find_control`` requires exactly one matching call in the whole journal.
    That is right for a step whose proof is a past invocation, but a bind is
    retried: a refused attempt leaves its own call record behind, so a second,
    byte-identical attempt would make every later bind refuse as a duplicate
    and leave the node permanently unbindable - the published decision cannot
    be republished either.

    A refused attempt has returned, so the original CLI has already recorded
    its terminal output. The invocation executing *now* is therefore the one
    matching call with no output record. Exactly one such call is required: two
    unanswered identical calls are genuinely ambiguous, so they still refuse.
    A *successful* earlier attempt cannot be among them because its binding
    exists, which ``bind_unbound`` refuses long before reaching here.
    """
    from orchestration_campaign import native_control
    end = reader.snapshot()["offset"]
    matches, answered = [], set()
    for ref, record in reader.records(count(after, "bind search offset"), end):
        payload = record.get("payload", {})
        if (record.get("type") == "response_item" and payload.get("type") == "custom_tool_call"
                and payload.get("input") == script):
            native_control(reader, ref, script)
            matches.append((ref, payload.get("call_id")))
        _calls, outputs = calls_and_results(record, "codex")
        answered.update(call_id for call_id, _value, _error in outputs
                        if isinstance(call_id, str) and call_id)
    live = [ref for ref, call_id in matches if call_id not in answered]
    require(len(live) == 1, "one original supervisor control invocation required; duplicate/absent request")
    return live[0]


def bind_result_evidence(reader, bind, binding_pin, *, until) -> dict | None:
    """The bind call's own terminal rc0 result, printing exactly this binding.

    ``bind-unbound`` creates the binding record with an exclusive create and
    prints its ``path``/``bytes``/``sha256``, so a result that prints this
    binding's current pin is proof that this call is the one that wrote this
    file. A refused bind leaves its call in the transcript but never such a
    result, so a binding that cites a refused attempt cannot pass here.

    ``None`` means "no output record for this call yet" - the bind is still in
    flight, or it died between its exclusive write and its own exit. That is
    reported as ``bind_result_joined: false`` and is a refusal only where the
    binding is relied on (``Coordinator.verify_claimed_binding``), never a
    silently invented join. A result that exists but is wrong, ambiguous,
    non-terminal or prints any other pin refuses here.

    The scan stops at the *first* output record carrying the call's call_id, so
    the verdict is fixed as soon as that record exists and cannot change as the
    journal grows. A second call record re-using the call_id before that output
    makes the pairing ambiguous and refuses.
    """
    call = bind["call_record"]
    start = count(call["offset"], "bind call offset") + count(call["length"], "bind call length")
    end = count(until, "bind result window end")
    require(start <= end, "BIND_RESULT: the bind result window is inverted")
    call_id = bind.get("call_id")
    require(isinstance(call_id, str) and call_id, "BIND_RESULT: the bind call has no call_id")
    found, scanned = None, 0
    for ref, record in reader.records(start, end):
        scanned += ref["length"]
        require(scanned <= WINDOW_BYTES, "BIND_RESULT_PENDING: original interval exceeds bound")
        require(globals()["advice_session_consistent"](record, reader),
                "CONTRADICTORY_NATIVE_SESSION in the bind result window")
        calls, outputs = calls_and_results(record, "codex")
        require(not any(found_id == call_id for found_id, _name, _args in calls),
                "BIND_RESULT: another call record re-uses the bind call's call_id")
        match = next((value for found_id, value, _error in outputs if found_id == call_id), None)
        if match is not None:
            found = (ref, record, match)
            break
    if found is None:
        return None
    result_ref, result_record, value = found
    require(result_record.get("payload", {}).get("type") == "custom_tool_call_output",
            "BIND_RESULT: the output belongs to another tool family")
    stdout = codex_stdout(value)
    require(isinstance(stdout, str), "BIND_RESULT: not a complete terminal rc0 result")
    if stdout.startswith(RTK_NOTICE):
        stdout = stdout[len(RTK_NOTICE):]
    try:
        printed = strict_json(stdout, "bind result")
    except ContractError as exc:
        raise ContractError("BIND_RESULT: the printed result is truncated or not JSON") from exc
    require(isinstance(printed, dict) and all(printed.get(name) == binding_pin.get(name)
                                              for name in ("path", "bytes", "sha256"))
            and set(printed) == {"path", "bytes", "sha256"},
            "BIND_RESULT: the bind call did not print this executor binding")
    require(parse_time(result_record.get("timestamp"), "bind result timestamp")
            >= parse_time(bind["timestamp"], "bind call timestamp"),
            "BIND_RESULT: the output is stamped before its call")
    reader.record(result_ref)
    return dict(kind="BIND_RESULT_OBSERVED", call_id=call_id, result_record=result_ref,
                timestamp=result_record.get("timestamp"))


def _publish_join(co, key, reader, decision, decision_ref, binding, advice, read, until):
    """Join the publisher's own native call and result, or state that none exists.

    A decision that does not live at the publisher's exclusive path was authored
    outside the run tree by the older diagnostic shape; there is nothing to join
    and the chain says so with ``decision_publish_natively_joined: false``
    rather than inventing a join. For a published decision the join is
    mandatory: a missing or broken pair refuses.
    """
    from orchestration_campaign import control_script, decision_argument_fields
    if not globals()["decision_is_published"](co, key, decision_ref):
        return None
    script = control_script(co.campaign, co.campaign_ref, "publish-decision", co.spec["run_id"],
                            node_id=key, decision_fields=decision_argument_fields(decision))
    publish = globals()["decision_publish_evidence"](
        reader, fence=(advice.get("fence") or co.header["native_start"]["codex"]),
        until=until, script=script)
    expected = dict(run_id=co.spec["run_id"], node_id=key,
                    supervisor_session=decision.get("supervisor_session"),
                    decision={name: decision_ref[name] for name in ("path", "bytes", "sha256")},
                    publisher=_publisher_expectation(co),
                    inputs=dict(campaign=co.campaign_ref, advice=_advice_expectation(co, key, decision)))
    require(globals()["publication_pins_the_decision"](publish["envelope"], expected),
            "DECISION_PUBLISH_ENVELOPE: the printed envelope does not pin this decision's current bytes")
    if read is not None:
        require(globals()["publish_follows_the_read"](read, publish),
                "DECISION_PUBLISH_ORDER: the decision was published before its advice was read")
    return publish


def _publisher_expectation(co):
    """The frozen adapter entrypoint, measured now, as the envelope must name it."""
    entry = co.campaign["adapter"]["files"].get("scripts/orchestration_run.py")
    require(isinstance(entry, dict), "the frozen adapter must contain its own run entrypoint")
    path = Path(co.campaign["adapter"]["path"]) / "scripts/orchestration_run.py"
    pin = file_ref(path)
    require(pin["sha256"] == entry["sha256"], "the publisher differs from the frozen adapter closure")
    return dict(path=pin["path"], sha256=pin["sha256"])


def _advice_expectation(co, key, decision):
    """The advice pin the envelope must carry, re-measured from the run tree."""
    if decision.get("advice_sha256") is None:
        return None
    return file_ref(co.root / "nodes" / str(key) / "advice" / (decision["advice_sha256"] + ".txt"))


@contract_errors("advice chain")
def advice_chain(co, key, info, readers, *, registration, not_after, binding_ref=None) -> dict:
    """Recompute the advice/read/decision/binding join from frozen evidence.

    No proof is taken from a self-reported field: the binding is read through
    its pin, the decision through the pin the binding carries, and the read is
    re-derived from the original native journal inside a fenced window. For a
    published decision the decision and bind times are the publisher result's
    and the bind call's own native timestamps. The self-written stamps that
    remain (a diagnostic decision's ``decided_at``, the claim's ``started_at``)
    are only checked for contradiction and are labelled in ``order_basis``.
    A frozen binding also has its bind call's own result joined, which is what
    separates the binding ``bind-unbound`` wrote from one that merely cites a
    refused attempt.
    """
    require(key in co.nodes, "unknown frozen node")
    if binding_ref is None:
        require(isinstance(info, dict) and isinstance(info.get("executor_binding"), dict),
                "the claim must pin its executor binding, or a binding reference must be supplied")
        binding_ref = info["executor_binding"]
    binding = json_ref(binding_ref, "original executor binding")
    return chain_from_binding(co, key, info, readers, binding, registration=registration, not_after=not_after,
                              binding_pin=lambda: file_ref(binding_ref["path"]),
                              result_pin=file_ref(binding_ref["path"]))


@contract_errors("advice chain")
def chain_from_binding(co, key, info, readers, binding, *, registration, not_after, binding_pin,
                       result_pin=None) -> dict:
    """The one join, over a binding *value*.

    ``advice_chain`` calls it on a frozen binding; ``Coordinator.bind_unbound``
    calls it on the value it is about to create, so a bind can only ever write
    a record this same code will later re-derive identically. ``binding_pin``
    is called last, only once every check has passed.

    ``result_pin`` is the frozen binding's own path/bytes/sha256. It is absent
    at bind time, where the bind call's result cannot exist yet, and present
    for every frozen binding, where the result is joined and reported in
    ``bind_result_joined``.
    """
    require(key in co.nodes, "unknown frozen node")
    require(binding.get("run_id") == co.spec["run_id"] and binding.get("node_id") == key,
            "executor binding belongs to another run or frozen node")
    pinned = binding.get("decision")
    require(isinstance(pinned, dict) and isinstance(pinned.get("path"), str),
            "executor binding does not pin a decision file")
    # Measured independently of the pin, so the comparison is not the pin
    # against itself: a decision file edited after binding fails here.
    decision_ref = file_ref(pinned["path"])
    require(globals()["binding_cites_decision"](binding, decision_ref),
            "executor binding does not pin the decision file's current bytes")
    decision = json_ref(decision_ref, "original bind decision")
    require(decision.get("run_id") == co.spec["run_id"] and decision.get("node_id") == key,
            "bind decision belongs to another run or frozen node")
    # The field, schema, work-kind and reason checks the publisher and the bind
    # run, so a diagnostic decision outside the publisher path is not read
    # unchecked either.
    from orchestration_binding import check_decision
    check_decision(decision, co.spec["run_id"], key)
    require(decision.get("supervisor_session") == binding.get("supervisor_session")
            == co.campaign["sessions"]["codex"]["session_id"],
            "the decision and its binding name another supervisor session")
    require(globals()["route_within_recomputed"](binding),
            "bound lane is outside the candidates recomputed at binding time")
    require(decision.get("actual_route") == binding.get("lane"),
            "bound lane differs from the decision's actual route")
    reader = readers["codex"]
    node_root = co.root / "nodes" / key
    # A published decision's window ends at its natively verified bind call.
    # Only the older diagnostic shape, which is never counted, may still fall
    # back to the binding's own offset or the snapshot.
    published = globals()["decision_is_published"](co, key, decision_ref)
    bind = globals()["bind_call_evidence"](co, key, reader, binding, decision_ref) if published else None
    until = bind["call_record"]["offset"] if bind is not None else _bound_window(reader, binding)
    advice = binding.get("advice") or {}
    arm = advice.get("arm") or ("advisory-off" if decision.get("advice_sha256") is None else "advisory-on")
    worker_status = advice.get("worker_status")
    journal = advice.get("worker_journal")
    read, read_at, evidence = None, None, []
    status = "ADVICE_NOT_CONSUMED"
    if arm == "advisory-off":
        require(decision.get("advice_sha256") is None, "the off arm must cite no advice")
        records = _window(reader, advice.get("fence") or co.header["native_start"]["codex"], until)
        attempts = globals()["advice_read_attempts"](records, node_root, reader.provider)
        require(globals()["off_arm_has_no_advice_file"](node_root), "ADVICE_ARM_VIOLATION: off arm published advice")
        require(globals()["off_arm_worker_requests_zero"](journal), "ADVICE_ARM_VIOLATION: off arm made worker requests")
        require(globals()["off_arm_has_no_advice_read"](attempts), "ADVICE_ARM_VIOLATION: off arm read advice")
    else:
        require(globals()["decision_cites_this_advice"](decision, advice.get("advice_sha256")),
                "the decision cites a different advice than this run published")
        evidence = globals()["advice_read_evidence"](
            reader, fence=advice.get("fence"), until=until,
            notice=advice["notice"], command=advice["command"],
            not_before=advice["not_before_epoch"], not_after=not_after)
        if globals()["read_is_full"](evidence):
            read = evidence[0]
            read_at = read["timestamp"]
            status = "CONSUMED"
        else:
            require(not evidence, "AMBIGUOUS_ADVICE_READ: more than one valid pair is not a FULL read")
    publish = _publish_join(co, key, reader, decision, decision_ref, binding, advice, read, until)
    if status == "CONSUMED" and not globals()["fallback_not_counted_as_consumed"](worker_status):
        # A fallback or stopped advisor is reported as such first: its own
        # status is more informative than the missing-join one.
        status = "NOT_CONFIGURED" if worker_status == "NOT_CONFIGURED" else "JEV_FALLBACK"
    elif status == "CONSUMED" and publish is None:
        status = "DIAGNOSTIC_NOT_NATIVELY_JOINED"
    counted = status == "CONSUMED"
    # Only the native publish result may supply a decision time. The older
    # shape's self-written stamp is kept under its own diagnostic key and is
    # still order-checked for contradiction, never used as proof (R94 2.4).
    decided_at = publish["timestamp"] if publish is not None else None
    diagnostic_decided_at = None if publish is not None else decision.get("decided_at")
    bound_at = bind["timestamp"] if bind is not None else binding.get("bound_at")
    claim_started_at = info.get("started_at") if isinstance(info, dict) else None
    # The claim leg. A claim on the Codex lane is in the same transcript, so
    # the bind result must end before the claim's own native start offset. A
    # Claude-lane claim has no position in this transcript: it is ordered after
    # the bind by the claim's sha256 pin of the binding these very bytes
    # created, which no record could predate. The claim's ``started_at`` is the
    # coordinator's wall clock, never a native record, and is labelled as such.
    claim_native = info.get("native_start") if isinstance(info, dict) else None
    on_codex = binding.get("lane") == "codex" and isinstance(claim_native, dict)
    result_until = (reader.verify_pin(claim_native) if on_codex else reader.snapshot()["offset"])
    bind_result = (globals()["bind_result_evidence"](reader, bind, result_pin, until=result_until)
                   if bind is not None and result_pin is not None else None)
    claim_basis = None if claim_started_at is None else (
        "native_codex_offset_after_the_bind_result_and_coordinator_wall_clock" if on_codex
        else "binding_sha256_pin_of_the_bind_result_and_coordinator_wall_clock")
    order_verified = globals()["ordered_chain"](read_at, decided_at if publish is not None
                                                else diagnostic_decided_at, bound_at, claim_started_at)
    require(order_verified, "the advice chain is not in read/decide/bind/claim order")
    return dict(schema=SCHEMA, arm=arm, status=status, read_proof=READ_PROOF,
                decision_write_natively_recognized=False, decision_write_proof=DECISION_WRITE_PROOF,
                decision_publish_natively_joined=publish is not None,
                bind_natively_verified=bind is not None, bind_call=bind,
                bind_result_joined=bind_result is not None, bind_result=bind_result,
                counted_as_advisory_consumption=counted, worker_status=worker_status,
                advice=advice.get("advice_sha256"), decision=decision_ref,
                decided_at=decided_at, diagnostic_decided_at=diagnostic_decided_at,
                bound_at=bound_at, decision_publish=publish,
                binding=binding_pin(), read=read, order_verified=order_verified,
                order_basis=dict(
                    read_at="native_read_result_record" if read_at is not None else None,
                    decided_at=("native_publish_result_record" if publish is not None else
                                "decision_file_self_written_consistency_only"),
                    bound_at=("native_bind_control_record" if bind is not None else
                              "binding_self_reported_consistency_only"),
                    claim_started_at=claim_basis, claim_after_bind=claim_basis),
                registration=registration, predicates=sorted(ADVICE_PREDICATES))


ADVICE_PREDICATES = {name: value for name, value in sorted(globals().items())
                     if name in ("advice_session_consistent", "advice_read_evidence", "read_is_full",
                                 "decision_cites_this_advice", "binding_cites_decision",
                                 "route_within_recomputed", "ordered_chain", "off_arm_has_no_advice_file",
                                 "off_arm_worker_requests_zero", "off_arm_has_no_advice_read",
                                 "fallback_not_counted_as_consumed", "advice_read_attempts",
                                 "decision_publish_evidence", "publish_call_is_the_frozen_script",
                                 "publication_pins_the_decision", "publish_follows_the_read",
                                 "decision_is_published", "bind_call_evidence", "bind_result_evidence",
                                 "live_bind_call")}
