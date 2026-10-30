"""R62 ``checks.excision`` and ``checks.restore`` as a test `test_all.py` discovers.

Every named judgment predicate E3 added is a module-level callable registered
in ``ADVICE_PREDICATES``, ``BIND_PREDICATES`` or, for the one predicate that
lives beside the rules provider, named here. This module cannot detect a
judgment written inline instead of as a registered predicate; the two owning
modules' docstrings state which checks are registered. For each registered
predicate this module runs ROOT's R39 protocol in process:

1. the mapped negative must refuse with its own reason (control);
2. a semantics-preserving wrapper that still calls the original must keep
   refusing, so the protocol itself is not what produces the refusal;
3. the excised predicate - made to admit everything, or to return its neutral
   value - must stop that negative from refusing as it did. A predicate whose
   negative is unchanged by excision is reported as ``VACUOUS``;
4. the attribute is restored by ``is`` identity and every predicate-bearing
   module's source sha256 is unchanged, because the excision is in memory only.

The mapped negative is an ordinary committed test, run by name, not a probe
written here: that way a predicate is proved load-bearing for a refusal the
suite actually asserts. Five negatives are defined in this module because no
existing test isolates the predicate they map to - each is noted in the registry.

Excision has two honest outcomes and the registry records which one applies.
``NEGATIVE_FLIPS`` is the strong form: the negative no longer refuses as it did.
Where a second, independent layer also refuses, the flip shows up as that other
layer's reason rather than as acceptance, and the expected reason is written
down instead of being hidden. ``POSITIVE_BREAKS`` is the weaker form, used for
exactly one predicate, ``advice_read_evidence``, whose neutral value ("no
evidence") is *less* admitting than its real answer: there the mapped test is
the natively joined complete chain's own positive, and excision must break it.
The other evidence-returning predicates (``decision_publish_evidence``,
``bind_call_evidence``, ``bind_result_evidence``, ``live_bind_call``) keep the
strong form through a shape-preserving excision that drops their judgments
but keeps their record search.

The four shared matcher predicates (``valid_command``, ``calls_and_results``,
``codex_stdout``, ``exact_body_output``) are reused unchanged and are asserted
to be absent from both registries: excising them would also flip the attribution
and input-provenance negatives, so a flip there would prove nothing about this
chain.
"""
import hashlib
import io
import json
import unittest
from pathlib import Path

import decision_provider
import orchestration_binding
import upgrade_advice_chain
from orchestration_binding import (BINDING_FIELDS, BIND_PREDICATES, DECISION_SCHEMA, SCHEMA as BINDING_SCHEMA,
                                   check_decision, read_binding)
from orchestration_common import ContractError, read_json, write_json
from test_orchestration_bind_unbound import BindFixtureMixin
from upgrade_advice_chain import ADVICE_PREDICATES

SHARED_MATCHER_PREDICATES = ("valid_command", "calls_and_results", "codex_stdout", "exact_body_output")
# The one named predicate outside the two registries: it decides the rules
# provider's unbound branch, and C2 introduced it.
PROVIDER_PREDICATES = {"deliberately_unbound": decision_provider.deliberately_unbound}
NEGATIVE_FLIPS, POSITIVE_BREAKS = "NEGATIVE_FLIPS", "POSITIVE_BREAKS"
LOCAL = "test_orchestration_advice_excision.ExcisionNegatives."

ADMIT_EVERYTHING = lambda *args, **kwargs: True
ADMIT_NOTHING_EXCLUDED = lambda *args, **kwargs: False
NO_EVIDENCE = lambda *args, **kwargs: []


def publish_evidence_asserting_nothing(reader, *, fence, until, script):
    """``decision_publish_evidence`` with its record search and no judgment.

    This predicate returns evidence, not a verdict, so "admit everything" cannot
    be a constant: the caller needs a pair to compare against the expected
    envelope. The excision therefore keeps the original's own search over the
    same fenced window and drops every ``require`` in it - the pairing, the
    single-call, shared-call_id and single-result rules, the terminal rc0
    extraction and the stamp order. It takes the first call and the first output
    it sees. Nothing here reconstructs or repairs an envelope: if the window
    holds no usable pair this raises, exactly as an absent join should.
    """
    from upgrade_body_read_evidence import RTK_NOTICE, calls_and_results, codex_stdout
    from orchestration_common import strict_json
    records = upgrade_advice_chain._window(reader, fence, until)
    calls, results = [], {}
    for ref, record in records:
        found, outputs = calls_and_results(record, "codex")
        for call_id, name, _arguments in found:
            if record.get("payload", {}).get("type") == "custom_tool_call":
                calls.append((ref, record, call_id))
        for call_id, value, _ in outputs:
            results.setdefault(call_id, []).append((ref, record, value))
    call_ref, call_record, call_id = calls[0]
    result_ref, result_record, value = results[call_id][0]
    stdout = codex_stdout(value)
    if isinstance(stdout, str) and stdout.startswith(RTK_NOTICE):
        stdout = stdout[len(RTK_NOTICE):]
    return dict(kind="DECISION_PUBLISH_OBSERVED", call_id=call_id, call_record=call_ref,
                result_record=result_ref, call_timestamp=call_record.get("timestamp"),
                timestamp=result_record.get("timestamp"),
                envelope=strict_json(stdout, "decision publication envelope"))


def any_matching_bind_call(reader, script, *, after):
    """``live_bind_call`` with its record search and no judgment.

    The real predicate requires exactly one *unanswered* matching call, so two
    invocations still running are ambiguous. This excision returns the last
    matching call whatever its result state, so a genuinely ambiguous pair is
    accepted and the bind proceeds on an arbitrary one of them.
    """
    found = None
    for ref, record in reader.records(after, reader.snapshot()["offset"]):
        payload = record.get("payload", {})
        if (record.get("type") == "response_item" and payload.get("type") == "custom_tool_call"
                and payload.get("input") == script):
            found = ref
    if found is None:
        raise ContractError("one original supervisor control invocation required; duplicate/absent request")
    return found


def bind_result_asserting_nothing(reader, bind, binding_pin, *, until):
    """``bind_result_evidence`` with its record search and no judgment.

    This predicate returns evidence, not a verdict, so "admit everything"
    cannot be a constant: the caller needs the pair. The excision keeps the
    original's own forward search for the first output of the bind call's
    call_id and drops every ``require`` in it - the re-used call_id rule, the
    terminal rc0 extraction, the printed-pin comparison and the stamp order.
    Any output is accepted as that call's result, whatever it printed.
    """
    from upgrade_body_read_evidence import calls_and_results
    call = bind["call_record"]
    start = call["offset"] + call["length"]
    call_id = bind.get("call_id")
    for ref, record in reader.records(start, until):
        _calls, outputs = calls_and_results(record, "codex")
        if any(found_id == call_id for found_id, _value, _error in outputs):
            return dict(kind="BIND_RESULT_OBSERVED", call_id=call_id, result_record=ref,
                        timestamp=record.get("timestamp"))
    return None


def bind_call_taken_from_the_binding_itself(co, key, reader, binding, decision_ref):
    """``bind_call_evidence`` with its judgment removed, not its shape.

    The real predicate re-derives the bind control call from the journal and
    requires ``bound_at`` to be that record's own native timestamp. This
    excision believes the binding instead: whatever it names as its bind call,
    with whatever stamp, is accepted. A missing pin becomes "no window bound
    from a call", which is the most admitting value this can return.
    """
    pin = binding.get("bound_native")
    if not isinstance(pin, dict):
        return dict(kind="BIND_CALL_OBSERVED", call_id=None,
                    call_record=dict(offset=reader.snapshot()["offset"], length=0),
                    timestamp=binding.get("bound_at"))
    return dict(kind="BIND_CALL_OBSERVED", call_id=None, call_record=pin,
                timestamp=binding.get("bound_at"))


def _chain(name):
    return "test_upgrade_advice_chain.AdviceChainTests." + name


def _arms(name):
    return "test_orchestration_advice_chain.AdviceArmTests." + name


def _bind(name):
    return "test_orchestration_bind_unbound.BindUnboundTests." + name


# predicate -> (owning module, excised callable, mapped negative, kind, expected reason)
# The expected reason is None when excision makes the negative stop refusing
# altogether, and the other layer's reason when a second layer still refuses.
REGISTRY = {
    # --- upgrade_advice_chain -------------------------------------------------
    "advice_session_consistent": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _chain("test_contradictory_codex_session_meta_invalidates_the_window"), NEGATIVE_FLIPS, None),
    "advice_read_evidence": (
        # Its neutral value is "no evidence", which refuses *more*, so the
        # complete-chain positive is what excision must break. That positive
        # is the natively joined, counted run-level chain: the checker-level
        # complete chain is the diagnostic shape and is not counted.
        upgrade_advice_chain, NO_EVIDENCE,
        "test_orchestration_publish_decision.PublishDecisionTests."
        "test_on_arm_publish_joins_after_the_full_read", POSITIVE_BREAKS, None),
    "read_is_full": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _chain("test_two_valid_pairs_are_ambiguous_not_consumed"), NEGATIVE_FLIPS, None),
    "decision_cites_this_advice": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _chain("test_decision_citing_another_advice_is_refused"), NEGATIVE_FLIPS, None),
    "binding_cites_decision": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _chain("test_binding_without_a_matching_decision_is_refused"), NEGATIVE_FLIPS, None),
    "route_within_recomputed": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _chain("test_route_outside_the_recomputed_candidates_is_refused"), NEGATIVE_FLIPS, None),
    "ordered_chain": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _chain("test_order_requires_read_then_decide_then_bind"), NEGATIVE_FLIPS, None),
    "off_arm_has_no_advice_file": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _chain("test_off_arm_with_an_advice_file_requests_or_a_read_is_an_arm_violation"),
        NEGATIVE_FLIPS, None),
    "off_arm_worker_requests_zero": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _arms("test_off_arm_with_worker_requests_is_an_arm_violation"), NEGATIVE_FLIPS, None),
    "off_arm_has_no_advice_read": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _arms("test_off_arm_with_an_advice_read_is_an_arm_violation"), NEGATIVE_FLIPS, None),
    "fallback_not_counted_as_consumed": (
        # Mapped at run level, where this predicate decides `counted` on a
        # natively joined chain. At checker level the decision is outside the
        # publisher path, so nothing there is counted whatever this returns.
        upgrade_advice_chain, ADMIT_EVERYTHING,
        _arms("test_fallback_or_stopped_auth_is_not_counted_as_consumption"), NEGATIVE_FLIPS, None),
    "advice_read_attempts": (
        upgrade_advice_chain, NO_EVIDENCE,
        _arms("test_off_arm_with_an_advice_read_is_an_arm_violation"), NEGATIVE_FLIPS, None),
    "decision_publish_evidence": (
        # Mapped to the duplicate-result negative: the records are found either
        # way, and only this function's own single-result rule refuses them.
        upgrade_advice_chain, publish_evidence_asserting_nothing,
        LOCAL + "test_a_duplicate_publisher_result_is_refused", NEGATIVE_FLIPS, None),
    "publish_call_is_the_frozen_script": (
        # The committed negative runs six forgeries; one is enough for the gate
        # and keeps this protocol's own cost proportionate.
        upgrade_advice_chain, ADMIT_EVERYTHING,
        LOCAL + "test_a_forged_publisher_call_is_refused", NEGATIVE_FLIPS, None),
    "publication_pins_the_decision": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        LOCAL + "test_an_envelope_pinning_another_decision_is_refused", NEGATIVE_FLIPS, None),
    "publish_follows_the_read": (
        upgrade_advice_chain, ADMIT_EVERYTHING,
        "test_orchestration_publish_decision.PublishDecisionTests."
        "test_a_publish_stamped_before_the_read_is_refused", NEGATIVE_FLIPS,
        "the advice chain is not in read/decide/bind/claim order"),
    "decision_is_published": (
        # "Admit everything" here is to recognise no decision as published, so
        # the mandatory publisher join and bind check are never reached and a
        # published decision with no publisher call becomes a mere diagnostic.
        upgrade_advice_chain, ADMIT_NOTHING_EXCLUDED,
        "test_orchestration_publish_decision.PublishDecisionTests."
        "test_a_missing_publish_call_is_refused", NEGATIVE_FLIPS, None),
    "bind_call_evidence": (
        upgrade_advice_chain, bind_call_taken_from_the_binding_itself,
        "test_orchestration_publish_decision.PublishDecisionTests."
        "test_a_published_decision_must_pin_its_own_bind_control_call", NEGATIVE_FLIPS, None),
    "bind_result_evidence": (
        upgrade_advice_chain, bind_result_asserting_nothing,
        _bind("test_a_bind_result_printing_another_binding_is_refused"), NEGATIVE_FLIPS, None),
    "live_bind_call": (
        upgrade_advice_chain, any_matching_bind_call,
        _bind("test_two_bind_invocations_still_running_are_ambiguous"), NEGATIVE_FLIPS, None),
    # --- orchestration_binding ----------------------------------------------
    "unbound_plan": (
        # Excised every plan counts as opted in, so a plain v3 run's header
        # grows the unbound_nodes key the opt-in alone may add.
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_run_header_carries_unbound_nodes_only_under_the_opt_in"), NEGATIVE_FLIPS, None),
    "unbound_claim_started": (
        # Excised it reports no claim as started, so a real interrupted claim
        # artefact on an unbound node would no longer be reported incomplete.
        orchestration_binding, ADMIT_NOTHING_EXCLUDED,
        _bind("test_publishing_and_binding_do_not_look_like_an_incomplete_claim"), NEGATIVE_FLIPS, None),
    "lane_verified": (
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_bind_recomputes_identity_in_code"), NEGATIVE_FLIPS, None),
    "lane_idle_write_domain": (
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_a_bound_lane_taken_before_the_claim_refuses_without_residue"), NEGATIVE_FLIPS,
        "original session or write scope is already reserved"),
    "lane_is_author": (
        # "Admit everything" here means excluding nobody as an author.
        orchestration_binding, ADMIT_NOTHING_EXCLUDED,
        "test_orchestration_binding.BindingHelperTests."
        "test_eligible_candidates_exclude_the_author_for_review_and_literature", NEGATIVE_FLIPS, None),
    "decision_route_recomputed": (
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_route_not_in_recomputed_candidates_is_refused_before_any_write"), NEGATIVE_FLIPS,
        "bound lane is outside the candidates recomputed at binding time"),
    "decision_matches_run_node": (
        # At run level the chain re-checks run/node independently, so the bind
        # negative cannot isolate this predicate; `check_decision` can.
        orchestration_binding, ADMIT_EVERYTHING,
        LOCAL + "test_a_decision_for_another_run_or_node_is_refused_in_memory", NEGATIVE_FLIPS, None),
    "node_is_unbound": (
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_binding_a_bound_node_is_refused"), NEGATIVE_FLIPS,
        "bind takes only the decision the fixed publisher created for this node"),
    "binding_absent": (
        # The second-bind negative is also refused by the exclusive create with
        # the same message, so that test cannot isolate this predicate.
        orchestration_binding, ADMIT_EVERYTHING,
        LOCAL + "test_a_malformed_binding_record_is_refused_when_it_is_read", NEGATIVE_FLIPS, None),
    "decision_work_kind_known": (
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_bind_refuses_an_unknown_or_missing_work_kind"), NEGATIVE_FLIPS,
        "candidates must not be recomputed"),
    "decision_action_known": (
        # The publisher's in-memory check is the only gate before the write:
        # excised, an action R62 does not name is published.
        orchestration_binding, ADMIT_EVERYTHING,
        "test_orchestration_publish_decision.PublishDecisionTests."
        "test_the_publisher_refuses_an_action_r62_does_not_name", NEGATIVE_FLIPS, None),
    "advice_inputs_unchanged": (
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_bind_refuses_a_changed_input_version"), NEGATIVE_FLIPS, None),
    "claim_mode_matches_frozen_kind": (
        orchestration_binding, ADMIT_EVERYTHING,
        _bind("test_claim_mode_matches_the_frozen_null_execution_kind"), NEGATIVE_FLIPS, None),
    # --- decision_provider ---------------------------------------------------
    "deliberately_unbound": (
        decision_provider, ADMIT_NOTHING_EXCLUDED,
        "test_decision_provider.DecisionProviderTests.test_routes_only_to_assigned_existing_session",
        NEGATIVE_FLIPS, None),
}


class ExcisionNegatives(BindFixtureMixin, unittest.TestCase):
    """Negatives no existing test isolates, each mapped by one registry row.

    They are ordinary discovered tests as well: each asserts a real refusal on
    its own, independently of the excision protocol that also runs it.
    """

    def test_a_decision_for_another_run_or_node_is_refused_in_memory(self):
        """`check_decision` is where `decision_matches_run_node` decides alone.

        At run level `chain_from_binding` re-reads the decision and checks its
        run and node again, so the bind-level negative stays refused even with
        this predicate excised. The publisher's own in-memory check is the one
        place the predicate is the only gate - and it is the check that runs
        before anything is written.
        """
        value = dict(schema=DECISION_SCHEMA, run_id=self.run, node_id="unbound", work_kind="coding",
                     advice_sha256=None, actual_route="codex", action="supervisor_review",
                     reason="the supervisor chose the idle codex lane", decided_at=None,
                     supervisor_session=self.f.campaign["sessions"]["codex"]["session_id"])
        self.assertEqual(check_decision(value, self.run, "unbound"), value)
        for label, change in (("run", dict(run_id="run-99")), ("node", dict(node_id="local"))):
            with self.subTest(label=label), \
                    self.assertRaisesRegex(ContractError, "belongs to another run or frozen node"):
                check_decision(dict(value, **change), self.run, "unbound")

    def test_a_malformed_binding_record_is_refused_when_it_is_read(self):
        """`binding_absent` decides whether a record is read at all.

        The second-bind negative cannot isolate it: the exclusive create refuses
        with the same message even when the predicate admits everything. Here
        the record exists but carries an extra field, so the field check runs
        only because the predicate reported the record present.
        """
        path = orchestration_binding.binding_path(self.co.root, "unbound")
        value = {name: None for name in BINDING_FIELDS}
        value.update(schema=BINDING_SCHEMA, lane="codex", node_id="unbound", run_id=self.run)
        write_json(path, dict(value, extra="added"))
        with self.assertRaisesRegex(ContractError, "executor binding fields changed"):
            read_binding(self.co.root, "unbound")

    def test_a_forged_publisher_call_is_refused(self):
        """One forgery, the cheap half of the committed six-forgery negative."""
        fields = self.fields()
        forged = self.control_text(fields).replace("--node unbound", "--node local")
        published = self.publish_with_call(fields, forged)
        self.assertNotEqual(forged, published["script"])
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_CALL"):
            self.join(published)

    def test_an_envelope_pinning_another_decision_is_refused(self):
        """One forged envelope field, the cheap half of the committed eleven."""
        fields = self.fields()
        published = self.publish_with_call(
            fields, envelope=lambda result: dict(result, decision=dict(result["decision"], sha256="0" * 64)))
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_ENVELOPE"):
            self.join(published)

    def test_a_duplicate_publisher_result_is_refused(self):
        """Two outputs for one publisher call: the records are there, the pair is not.

        This isolates ``decision_publish_evidence``'s own single-result rule.
        The committed negative pairs this with the duplicate-call case; one
        output record is appended twice here, so only the uniqueness rule can
        refuse, and the duplicate is what a replayed or re-emitted result looks
        like.
        """
        published = self.publish_with_call(self.fields(), duplicate_result=True)
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_RESULT"):
            self.join(published)

    # --- helpers for the two publisher negatives ---------------------------

    def control_text(self, fields, *, node_id="unbound"):
        from orchestration_campaign import control_script
        return control_script(self.f.campaign, self.f.ref, "publish-decision", self.run,
                              node_id=node_id, decision_fields=fields)

    def publish_with_call(self, fields, script=None, *, envelope=None, node_id="unbound",
                          duplicate_result=False):
        """Append the supervisor's call, publish, append the printed result."""
        exact = self.control_text(fields, node_id=node_id)
        client = self.f.clients["codex"]
        when = self.next_stamp()
        client.append(dict(type="response_item", timestamp=when, payload=dict(
            type="custom_tool_call", name="exec", call_id="publish-forged",
            input=exact if script is None else script)))
        result = self.r.publish_decision(self.run, node_id, fields)
        printed = json.dumps(result if envelope is None else envelope(result),
                             ensure_ascii=False, sort_keys=True, allow_nan=False)
        later = self.next_stamp()
        for _ in range(2 if duplicate_result else 1):
            client.append(dict(type="response_item", timestamp=later, payload=dict(
                type="custom_tool_call_output", call_id="publish-forged",
                output=json.dumps(dict(exit_code=0, output=printed + "\n", wall_time_seconds=0.1)))))
        self.stamps.append(later)
        return dict(result=result, script=exact)

    def join(self, published, *, node_id="unbound"):
        """Run the real join over a binding of this published decision.

        The bind control call is appended and pinned, because a published
        decision's window ends at that call and its ``bound_at`` must be that
        record's own native timestamp.
        """
        from orchestration_binding import write_binding
        from orchestration_common import file_ref, parse_time, utc_now
        from orchestration_native import NativeReader
        from upgrade_advice_chain import advice_chain
        pin = {name: published["result"]["decision"][name] for name in ("path", "bytes", "sha256")}
        bind = self.bind_call(node_id=node_id)
        value = dict(schema=BINDING_SCHEMA, campaign=self.f.ref, run_id=self.run, node_id=node_id,
                     lane="codex", execution_kind="local_cli", transport="codex_cli", mode="local_cli",
                     arm=self.f.campaign.get("arm"), decision=pin,
                     advice=dict(arm="advisory-off", advice_sha256=None, worker_status=None,
                                 worker_journal=None, fence=self.co.header["native_start"]["codex"]),
                     eligible_lanes=["codex"], lane_facts={}, bound_at=self.stamps[-1],
                     bound_native=bind["refs"][-1], input_units=self.f.campaign["inputs"],
                     supervisor_session=self.f.campaign["sessions"]["codex"]["session_id"])
        ref = write_binding(self.co, node_id, value)
        readers = {lane: NativeReader(session, fixtures=True)
                   for lane, session in self.f.campaign["sessions"].items()}
        return advice_chain(self.co, node_id, {}, readers, registration=None,
                            not_after=parse_time(utc_now()).timestamp() + 600, binding_ref=ref)


def registries():
    """Every new named predicate E3 introduced, with its owning module."""
    return dict(**{name: upgrade_advice_chain for name in ADVICE_PREDICATES},
                **{name: orchestration_binding for name in BIND_PREDICATES},
                **{name: decision_provider for name in PROVIDER_PREDICATES})


def module_sources():
    """sha256 of every predicate-bearing module's source, measured now."""
    modules = {upgrade_advice_chain, orchestration_binding, decision_provider}
    return {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in sorted(modules, key=lambda m: m.__name__)}


def run_mapped(test_name):
    """Run one committed test by name and classify what it did.

    ``PASSED`` means its assertions held; ``REFUSAL_ABSENT`` means an assertion
    failed, which for a negative is exactly "it no longer refuses as it did";
    ``OTHER_ERROR`` means some other exception, carried with its last line so a
    second refusing layer is reported rather than hidden.
    """
    suite = unittest.TestLoader().loadTestsFromName(test_name)
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    if result.wasSuccessful():
        return dict(outcome="PASSED", ran=result.testsRun, detail=None)
    problems = result.failures + result.errors
    detail = problems[0][1].strip().splitlines()[-1] if problems else None
    return dict(outcome="REFUSAL_ABSENT" if result.failures else "OTHER_ERROR",
                ran=result.testsRun, detail=detail)


class PredicateExcisionTests(unittest.TestCase):
    """R62 checks.excision and checks.restore, in memory, over the registry."""

    def setUp(self):
        self.sources = module_sources()
        self.patched = []
        self.addCleanup(self.restore_all)

    def restore_all(self):
        for module, name, original in reversed(self.patched):
            setattr(module, name, original)
        self.patched.clear()

    def install(self, module, name, value):
        original = getattr(module, name)
        self.patched.append((module, name, original))
        setattr(module, name, value)
        return original

    def test_every_new_predicate_has_a_flipping_negative(self):
        for name, (module, excised, test_name, kind, other) in sorted(REGISTRY.items()):
            with self.subTest(predicate=name):
                original = getattr(module, name)
                control = run_mapped(test_name)
                self.assertEqual(control["outcome"], "PASSED",
                                 f"red control for {name}: {control['detail']}")
                self.assertGreaterEqual(control["ran"], 1)

                # A wrapper that still calls the original must not change it.
                wrapper = (lambda fn: (lambda *a, **k: fn(*a, **k)))(original)
                self.install(module, name, wrapper)
                try:
                    wrapped = run_mapped(test_name)
                finally:
                    self.restore_all()
                self.assertEqual(wrapped["outcome"], "PASSED",
                                 f"the wrapper, not the predicate, decided {name}: {wrapped['detail']}")

                self.install(module, name, excised)
                try:
                    cut = run_mapped(test_name)
                finally:
                    self.restore_all()
                self.assertIs(getattr(module, name), original)
                self.assertNotEqual(
                    cut["outcome"], "PASSED",
                    f"VACUOUS: excising {name} left {test_name.split('.')[-1]} green, so that test "
                    f"does not depend on this predicate ({kind})")
                if other is not None:
                    # A second independent layer refuses instead; its reason is
                    # written down in the registry rather than left implicit.
                    self.assertIn(other, cut["detail"] or "")

    def test_the_predicate_to_negative_map_covers_the_registry(self):
        self.assertEqual(set(REGISTRY), set(registries()))
        self.assertEqual(set(ADVICE_PREDICATES) | set(BIND_PREDICATES) | set(PROVIDER_PREDICATES),
                         set(REGISTRY))
        for name, (module, excised, test_name, kind, other) in REGISTRY.items():
            with self.subTest(predicate=name):
                self.assertIs(registries()[name], module)
                self.assertTrue(callable(getattr(module, name)))
                self.assertEqual(getattr(module, name).__module__, module.__name__)
                self.assertTrue(callable(excised))
                self.assertIn(kind, (NEGATIVE_FLIPS, POSITIVE_BREAKS))
                self.assertEqual(unittest.TestLoader().loadTestsFromName(test_name).countTestCases(), 1)
        # Exactly one predicate, advice_read_evidence, may use the weaker form.
        self.assertEqual({name for name, row in REGISTRY.items() if row[3] == POSITIVE_BREAKS},
                         {"advice_read_evidence"})

    def test_every_patched_attribute_is_restored_by_identity(self):
        for name, (module, excised, _test, _kind, _other) in sorted(REGISTRY.items()):
            with self.subTest(predicate=name):
                original = getattr(module, name)
                self.install(module, name, excised)
                self.assertIs(getattr(module, name), excised)
                self.restore_all()
                self.assertIs(getattr(module, name), original)
        # Nothing is left patched, and each name is still the module's own
        # attribute rather than a copy this protocol installed.
        for name, module in registries().items():
            with self.subTest(predicate=name):
                self.assertIs(getattr(module, name), vars(module)[name])
                self.assertEqual(getattr(module, name).__module__, module.__name__)

    def test_module_source_sha256_is_unchanged_by_the_protocol(self):
        before = module_sources()
        self.assertEqual(before, self.sources)
        for name, (module, excised, _test, _kind, _other) in sorted(REGISTRY.items()):
            self.install(module, name, excised)
        self.assertEqual(module_sources(), before)
        self.restore_all()
        self.assertEqual(module_sources(), before)

    def test_shared_predicates_are_not_in_the_excision_registry(self):
        for name in SHARED_MATCHER_PREDICATES:
            with self.subTest(name=name):
                self.assertNotIn(name, ADVICE_PREDICATES)
                self.assertNotIn(name, BIND_PREDICATES)
                self.assertNotIn(name, PROVIDER_PREDICATES)
                self.assertNotIn(name, REGISTRY)


if __name__ == "__main__":
    unittest.main()
