# Predicate excision, restoration, and the pinned first-run verdicts

R62 asks for three things about every new judgment predicate: excise it one at a
time and require the matching negative to flip (`checks.excision`), restore the
original and reproduce the first run's verdicts exactly (`checks.restore`), and
register those first-run verdicts once (`checks.control`). This note says where
each one runs and what it is allowed to claim.

## The gate is a discovered test, not this procedure

`scripts/test_orchestration_advice_excision.py` is an ordinary `test_*.py`, so
`scripts/test_all.py` discovers it and CI's single `python -B scripts/test_all.py`
runs it. It holds the registry and the protocol. A vacuous predicate, a patched
attribute that is not restored, or a predicate added without a mapped negative
fails there - in CI, on every push, not in a manual step somebody has to
remember. The harness below adds only the pinned artefact.

## What the protocol does, per predicate

For each name in `ADVICE_PREDICATES | BIND_PREDICATES` plus
`decision_provider.deliberately_unbound`:

1. **Control.** Run that predicate's mapped negative unchanged. It must pass. A
   red control means nothing about the predicate is judged, and the protocol
   fails rather than reporting an excision result on a broken baseline.
2. **Semantics-preserving wrapper.** Install a wrapper that still calls the
   original and run the same negative. It must still pass, which is what rules
   out the protocol itself producing the refusal.
3. **Excision.** Install the predicate made to admit everything (or return its
   neutral value) and run the same negative again. It must stop refusing as it
   did. If it still passes unchanged, the predicate is reported `VACUOUS`.
4. **Restore.** Put the original back, assert `getattr(module, name) is original`,
   and re-measure every predicate-bearing module's source sha256. The excision
   is in memory only; no source file is ever edited.

The mapped negative is a committed test, run by name. That is deliberate: it
proves the predicate is load-bearing for a refusal the suite already asserts,
rather than for a probe written next to the protocol. Five negatives live in the
excision module itself because no existing test isolates the predicate they map
to, and each one says so in its docstring.

The gate covers registered predicates only. It cannot detect a judgment written
inline instead of as a registered predicate, so each owning module's docstring
says which of its checks are registered and which are record-shape `require`
calls. Callers reach a registered predicate through its module attribute (or
`globals()` inside its own module), never through a name imported at import
time, because an imported name is a second reference the in-memory excision
could not replace.

### Two honest excision outcomes

`NEGATIVE_FLIPS` is the strong form: with the predicate excised, the negative no
longer refuses as it did. For five predicates a second, independent layer
refuses instead, so the flip appears as that other layer's reason; the registry
records the expected reason rather than leaving the layering implicit. Those
five are `publish_follows_the_read`, `lane_idle_write_domain`,
`decision_route_recomputed`, `node_is_unbound` and `decision_work_kind_known`.

`POSITIVE_BREAKS` is the weaker form and is used for exactly one predicate,
`advice_read_evidence`. It returns evidence rather than a verdict, so its
neutral value ("no evidence") refuses *more* than the real answer, and no
negative can flip. There the mapped test is the complete-chain positive, and
excision must break its `CONSUMED` assertion. That positive is the natively
joined, counted run-level chain
(`test_orchestration_publish_decision.test_on_arm_publish_joins_after_the_full_read`):
the checker-level complete chain decides outside the publisher path, which
ROOT R94 section 2 keeps as a diagnostic, so it now asserts
`DIAGNOSTIC_NOT_NATIVELY_JOINED` and is not counted. That is weaker evidence and
is labelled as such in the registry, not presented as a flipped negative.

Four more predicates return evidence: `decision_publish_evidence`,
`bind_call_evidence`, `bind_result_evidence` and `live_bind_call`. Each keeps
the strong form through a shape-preserving excision that keeps the original's
own record search and drops its judgments, so it admits what the real function
refuses. For `decision_publish_evidence` that means the pairing rule, the
single-call, shared-call_id and single-result rules, the terminal rc0
extraction and the stamp order, over the same window bounded by the bind call;
it is mapped to a duplicate-result negative, where the records are present
either way and only that single-result rule can refuse. `bind_call_evidence`
excised believes whatever bind call and stamp the binding names;
`bind_result_evidence` excised accepts any output of the bind call's call_id
as its result; `live_bind_call` excised takes the last matching call whatever
its result state.

## Scope: the shared matcher predicates are not excised

`valid_command`, `calls_and_results`, `codex_stdout` and `exact_body_output` are
reused unchanged and are asserted absent from every registry
(`test_shared_predicates_are_not_in_the_excision_registry`). Excising them would
also flip the attribution and input-provenance negatives, so a flip there would
prove nothing about this chain. The old observers and the generic reader are
likewise untouched.

## The pinned artefact: register and verify

`scripts/jev_advice_control_harness.py` runs the same protocol over the same
registry and writes the verdicts once. It is not discovered by `test_all.py`
(its name does not match `test_*.py`) and it is not a gate.

    cd scripts
    PYTHONDONTWRITEBYTECODE=1 python3.14 -B jev_advice_control_harness.py \
        --register --out /ABS/PATH/CONTROL-VERDICTS-v1.json

`--register` creates the file exclusively, chmods it `0444`, and refuses if it
already exists: registration happens once. The verdict object records, per
predicate, its module, its mapped negative, the excision kind, the expected
other-layer reason, the three outcomes with their test counts, whether the
attribute was restored by identity, and whether it was vacuous. It also records
every predicate-bearing module's source sha256 before and after the whole run.

It registers R62 `checks.control` as well, after every excision has been
restored:

- every test the gate's `CONTROL_CHECKS` names, run once by name: the natively
  joined complete-chain positive and its run-level bind and claim, the
  exclusive-file shape that is diagnostic and not counted, and each of R62's
  seven `new_negatives` as design 4.3's table maps them;
- the v1 plan's `synthetic_self_check` values (plan sha256 `da34a9c5...`):
  the valid join, the 9 join rejections, the 3 estimator rejections and the
  estimator cases, including the pass-case median `0.19999999999999996`, each
  next to what the ported checker produces now. The v1 plan froze the values
  but not their inputs, so those fixtures are reconstructed from the ported
  R32 fixtures and labelled `RECONSTRUCTED_NOT_V1_ORIGINAL`;
- for `checks.restore`, the sha256 of every fixture-bearing module (each module
  holding a mapped negative or a control check, plus
  `orchestration_test_support`) before and after.

The gate itself does not run the control checks (they are ordinary discovered
tests), but it proves every mapped and control name still resolves to exactly
one real test method, so a rename cannot silently drop a check from the
artefact.

    PYTHONDONTWRITEBYTECODE=1 python3.14 -B jev_advice_control_harness.py \
        --verify --out /ABS/PATH/CONTROL-VERDICTS-v1.json

`--verify` recomputes everything and requires equality with the registered file
on every field a checker's state leakage would change: the outcomes, the test
counts, the expectations, the restoration flags, the control results, the v1
values, and the module and fixture source hashes.
Failure-detail text is compared separately and reported as
`detail_text_differs_only`, because a traceback's last line carries the absolute
path of a temporary fixture directory, which differs between runs for reasons
that have nothing to do with the checker. A difference anywhere else is reported
as `PAUSE_REGISTRATION_AND_INVESTIGATE`: the response is to investigate the
leakage, never to re-register over it.

`--out` must be absolute and is chosen by the operator. Design Q46 names the
design directory as the artefact's home; that directory is outside this
repository, so the files this increment measured were written to scratch
directories, and the owner places the registered artefact there as a manual
step outside this repository.

## What passing this proves, and what it does not

It proves each named predicate decides something the offline suite asserts, that
no predicate is a no-op, and that the protocol leaves no residue in memory or on
disk. It does not prove the chain was ever exercised live, that any advice was
consumed in a real run, or anything at all about efficiency. Offline green is
not live acceptance.
