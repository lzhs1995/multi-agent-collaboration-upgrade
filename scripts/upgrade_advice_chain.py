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

The window is fenced on both sides and bounded. An interval that exceeds the
bound raises ``ADVICE_READ_WINDOW_PENDING`` instead of returning a false
negative, and the from-zero scan pattern of ``upgrade_child_budget`` is
deliberately not copied.
"""
from __future__ import annotations

from pathlib import Path

from orchestration_common import contract_errors, count, file_ref, json_ref, parse_time, require
from upgrade_body_read_evidence import body_read_evidence, calls_and_results

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
    """Nothing in the fenced window read an advice body on the off arm."""
    return not evidence


def fallback_not_counted_as_consumed(worker_status) -> bool:
    """A fallback or stopped advisor is never counted as advisory consumption."""
    return worker_status not in FALLBACK_STATUSES


def advice_read_attempts(records, node_root, provider) -> list:
    """Window calls that *mention* this node's advice directory.

    Deliberately broader than ``valid_command``: this feeds a refusal, so an
    unparsed or unusual wrapper must not let an off-arm read slip through. A hit
    is therefore evidence of an attempt, never proof of a complete read - proof
    is only ever ``advice_read_evidence``'s exact pair.
    """
    target = str(Path(node_root) / "advice")
    found = []
    for ref, record in records:
        calls, _ = calls_and_results(record, provider)
        for call_id, name, arguments in calls:
            if target in str(arguments):
                found.append(dict(kind="ADVICE_READ_ATTEMPT", call_id=call_id, name=name, call_record=ref))
    return found


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


@contract_errors("advice chain")
def advice_chain(co, key, info, readers, *, registration, not_after, binding_ref=None) -> dict:
    """Recompute the advice/read/decision/binding join from frozen evidence.

    Nothing here is taken from a self-reported field: the binding is read
    through its pin, the decision through the pin the binding carries, and the
    read is re-derived from the original native journal inside a fenced window.
    """
    require(key in co.nodes, "unknown frozen node")
    if binding_ref is None:
        require(isinstance(info, dict) and isinstance(info.get("executor_binding"), dict),
                "the claim must pin its executor binding, or a binding reference must be supplied")
        binding_ref = info["executor_binding"]
    binding = json_ref(binding_ref, "original executor binding")
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
    require(globals()["route_within_recomputed"](binding),
            "bound lane is outside the candidates recomputed at binding time")
    require(decision.get("actual_route") == binding.get("lane"),
            "bound lane differs from the decision's actual route")
    reader = readers["codex"]
    node_root = co.root / "nodes" / key
    advice = binding.get("advice") or {}
    arm = advice.get("arm") or ("advisory-off" if decision.get("advice_sha256") is None else "advisory-on")
    worker_status = advice.get("worker_status")
    journal = advice.get("worker_journal")
    read, read_at, evidence = None, None, []
    status = "ADVICE_NOT_CONSUMED"
    if arm == "advisory-off":
        require(decision.get("advice_sha256") is None, "the off arm must cite no advice")
        records = _window(reader, advice.get("fence") or co.header["native_start"]["codex"],
                          _bound_window(reader, binding))
        attempts = globals()["advice_read_attempts"](records, node_root, reader.provider)
        require(globals()["off_arm_has_no_advice_file"](node_root), "ADVICE_ARM_VIOLATION: off arm published advice")
        require(globals()["off_arm_worker_requests_zero"](journal), "ADVICE_ARM_VIOLATION: off arm made worker requests")
        require(globals()["off_arm_has_no_advice_read"](attempts), "ADVICE_ARM_VIOLATION: off arm read advice")
    else:
        require(globals()["decision_cites_this_advice"](decision, advice.get("advice_sha256")),
                "the decision cites a different advice than this run published")
        evidence = globals()["advice_read_evidence"](
            reader, fence=advice.get("fence"), until=_bound_window(reader, binding),
            notice=advice["notice"], command=advice["command"],
            not_before=advice["not_before_epoch"], not_after=not_after)
        if globals()["read_is_full"](evidence):
            read = evidence[0]
            read_at = read["timestamp"]
            status = "CONSUMED"
        else:
            require(not evidence, "AMBIGUOUS_ADVICE_READ: more than one valid pair is not a FULL read")
    counted = status == "CONSUMED" and globals()["fallback_not_counted_as_consumed"](worker_status)
    if status == "CONSUMED" and not counted:
        status = "NOT_CONFIGURED" if worker_status == "NOT_CONFIGURED" else "JEV_FALLBACK"
    order_verified = globals()["ordered_chain"](read_at, decision.get("decided_at"), binding.get("bound_at"),
                                                info.get("started_at") if isinstance(info, dict) else None)
    require(order_verified, "the advice chain is not in read/decide/bind/claim order")
    return dict(schema=SCHEMA, arm=arm, status=status, read_proof=READ_PROOF,
                decision_write_natively_recognized=False, decision_write_proof=DECISION_WRITE_PROOF,
                counted_as_advisory_consumption=counted, worker_status=worker_status,
                advice=advice.get("advice_sha256"), decision=decision_ref,
                binding=file_ref(binding_ref["path"]), read=read, order_verified=order_verified,
                registration=registration, predicates=sorted(ADVICE_PREDICATES))


ADVICE_PREDICATES = {name: value for name, value in sorted(globals().items())
                     if name in ("advice_session_consistent", "advice_read_evidence", "read_is_full",
                                 "decision_cites_this_advice", "binding_cites_decision",
                                 "route_within_recomputed", "ordered_chain", "off_arm_has_no_advice_file",
                                 "off_arm_worker_requests_zero", "off_arm_has_no_advice_read",
                                 "fallback_not_counted_as_consumed", "advice_read_attempts")}
