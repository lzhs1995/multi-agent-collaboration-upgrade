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
rather than for a probe written next to the protocol. Four negatives live in the
excision module itself because no existing test isolates the predicate they map
to, and each one says so in its docstring.

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
excision must break its `CONSUMED` assertion. That is weaker evidence and is
labelled as such in the registry, not presented as a flipped negative.

`decision_publish_evidence` also returns evidence. Its excised form keeps the
original's own search over the same fenced window and drops every `require` in
it - the pairing rule, the single-call and single-result rules, the terminal rc0
extraction, the window bound and the stamp order - so it admits a pair the real
function refuses. It is mapped to a duplicate-result negative, where the records
are present either way and only that single-result rule can refuse.

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

    PYTHONDONTWRITEBYTECODE=1 python3.14 -B jev_advice_control_harness.py \
        --verify --out /ABS/PATH/CONTROL-VERDICTS-v1.json

`--verify` recomputes everything and requires equality with the registered file
on every field a checker's state leakage would change: the outcomes, the test
counts, the expectations, the restoration flags and the module source hashes.
Failure-detail text is compared separately and reported as
`detail_text_differs_only`, because a traceback's last line carries the absolute
path of a temporary fixture directory, which differs between runs for reasons
that have nothing to do with the checker. A difference anywhere else is reported
as `PAUSE_REGISTRATION_AND_INVESTIGATE`: the response is to investigate the
leakage, never to re-register over it.

`--out` must be absolute and is chosen by the operator. The file this increment
measured was written to a scratch directory; the owner decides where the
registered artefact finally lives, and that placement is a manual step outside
this repository.

## What passing this proves, and what it does not

It proves each named predicate decides something the offline suite asserts, that
no predicate is a no-op, and that the protocol leaves no residue in memory or on
disk. It does not prove the chain was ever exercised live, that any advice was
consumed in a real run, or anything at all about efficiency. Offline green is
not live acceptance.
