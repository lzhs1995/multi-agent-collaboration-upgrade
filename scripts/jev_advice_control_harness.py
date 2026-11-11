#!/usr/bin/env python3
"""Produce the pinned first-run verdict artefact for the E3 control checks.

This is the *artefact* producer, never the gate. The gate is
``scripts/test_orchestration_advice_excision.py``, which ``test_all.py``
discovers and CI therefore runs: a vacuous predicate or a leaking checker fails
there, not only here. This harness exists because CI runs one command and the
verdict file is an exclusive-create read-only file, which a test must not write.

It runs exactly the protocol that test runs, over exactly that registry:

* the mapped negative under the original predicate (the control),
* the same negative under a semantics-preserving wrapper,
* the same negative under the excised predicate,
* restoration by ``is`` identity, with every predicate-bearing module's source
  sha256 measured before and after.

It also registers R62 ``checks.control`` itself: every test named in the
gate's ``CONTROL_CHECKS`` (the natively joined complete-chain positive, the
diagnostic shape that is not counted, and each of R62's seven new negatives),
run once by name, and the v1 plan's ``synthetic_self_check`` values (the valid
join, the 9 join and 3 estimator rejections, the estimator cases) with what the
ported checker produces now. The v1 plan froze those values but not their
inputs, so their fixtures are reconstructed and labelled so. For R62
``checks.restore`` it records the sha256 of every fixture-bearing module (each
module holding a mapped negative or a control check, plus
``orchestration_test_support``) before and after.

``--register`` writes the verdict file once and refuses to overwrite it.
``--verify`` recomputes every verdict and requires equality with the
registered file on the outcomes, test counts, control results, v1 values and
every recorded source and fixture sha256. Any difference is reported as
checker state leakage and is a reason to pause registration, not to
re-register. Design Q46 names the design directory, which is outside this
repository, as the artefact's home; its owner places the registered file
there.

Zero model, JEV or network requests; no HTTP or SDK client; nothing here reads a
budget, a limit or a key from a file or an environment variable. The verdict
file's path is an explicit argument, so the operator decides where it lands.

    cd scripts
    PYTHONDONTWRITEBYTECODE=1 python3.14 -B jev_advice_control_harness.py \
        --register --out /ABS/PATH/CONTROL-VERDICTS-v1.json
    PYTHONDONTWRITEBYTECODE=1 python3.14 -B jev_advice_control_harness.py \
        --verify --out /ABS/PATH/CONTROL-VERDICTS-v1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "jev-advice-control-verdicts-v1"


def protocol():
    """The one registry and the one runner, imported from the discovered gate."""
    import test_orchestration_advice_excision as gate
    return gate


def fixture_sources(gate):
    """sha256 of every module that holds a mapped negative or a control check.

    These are the fixtures R62 checks.restore names: the committed tests the
    protocol runs, plus the shared fixture support module they build on.
    """
    import importlib
    names = {row[2].rsplit(".", 2)[0] for row in gate.REGISTRY.values()}
    names |= {test.rsplit(".", 2)[0] for group in gate.CONTROL_CHECKS.values() for test in group}
    names |= {"orchestration_test_support"}
    return {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
            for name in sorted(names)}


def control_checks(gate):
    """R62 checks.control: every named control test, run once by name."""
    return {group: {name: gate.run_mapped(name) for name in tests}
            for group, tests in sorted(gate.CONTROL_CHECKS.items())}


def v1_self_check():
    """The v1 first-run values, and what the ported checker produces now.

    The fixtures are reconstructed, not v1's (the v1 plan froze the values
    only); see ``test_jev_s2_checks.reconstruct_v1_self_check``.
    """
    import test_jev_s2_checks as s2
    observed = s2.reconstruct_v1_self_check()
    return dict(plan_sha256=s2.V1_PLAN_SHA256, fixtures="RECONSTRUCTED_NOT_V1_ORIGINAL",
                expected=s2.V1_SYNTHETIC_SELF_CHECK, observed=observed,
                reproduced=observed == s2.V1_SYNTHETIC_SELF_CHECK)


def verdicts():
    """Run the protocol once and return its canonical verdict object."""
    gate = protocol()
    before = gate.module_sources()
    fixtures_before = fixture_sources(gate)
    rows = {}
    for name, (module, excised, test_name, kind, other) in sorted(gate.REGISTRY.items()):
        original = getattr(module, name)
        control = gate.run_mapped(test_name)
        wrapper = (lambda fn: (lambda *a, **k: fn(*a, **k)))(original)
        setattr(module, name, wrapper)
        try:
            wrapped = gate.run_mapped(test_name)
        finally:
            setattr(module, name, original)
        setattr(module, name, excised)
        try:
            cut = gate.run_mapped(test_name)
        finally:
            setattr(module, name, original)
        restored = getattr(module, name) is original
        rows[name] = dict(module=module.__name__, mapped_negative=test_name, excision_kind=kind,
                          expected_other_refusal=other, control=control, wrapper=wrapped,
                          excised=cut, restored_by_identity=restored,
                          vacuous=cut["outcome"] == "PASSED")
    # R62 checks.restore: after every excision has been restored, the control
    # checks and the v1 values must still come out as they do on a clean run.
    controls = control_checks(gate)
    first_run = v1_self_check()
    after = gate.module_sources()
    fixtures_after = fixture_sources(gate)
    return dict(schema=SCHEMA, predicates=len(rows),
                registries=dict(advice=sorted(gate.ADVICE_PREDICATES),
                                bind=sorted(gate.BIND_PREDICATES),
                                provider=sorted(gate.PROVIDER_PREDICATES)),
                shared_predicates_excluded=list(gate.SHARED_MATCHER_PREDICATES),
                module_sources_before=before, module_sources_after=after,
                module_sources_unchanged=before == after,
                fixture_sources_before=fixtures_before, fixture_sources_after=fixtures_after,
                fixture_sources_unchanged=fixtures_before == fixtures_after,
                controls=controls,
                controls_all_passed=all(result["outcome"] == "PASSED"
                                        for group in controls.values() for result in group.values()),
                r62_new_negatives=list(gate.R62_NEW_NEGATIVES),
                v1_self_check=first_run,
                vacuous=[name for name, row in rows.items() if row["vacuous"]],
                all_restored=all(row["restored_by_identity"] for row in rows.values()),
                rows=rows)


def canonical(value) -> bytes:
    return (json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def comparable(value):
    """Everything in a verdict except what legitimately varies between runs.

    Elapsed time is not recorded at all, and a failure's last traceback line
    carries absolute scratch paths, so the comparison keeps the outcome, the
    test count and the expectations - the parts a checker's state leakage would
    actually change - and reports a detail difference separately.
    """
    rows = {name: {key: row[key] for key in
                   ("module", "mapped_negative", "excision_kind", "expected_other_refusal",
                    "restored_by_identity", "vacuous")}
            | {phase: row[phase]["outcome"] for phase in ("control", "wrapper", "excised")}
            | {phase + "_ran": row[phase]["ran"] for phase in ("control", "wrapper", "excised")}
            for name, row in value["rows"].items()}
    controls = {group: {name: dict(outcome=result["outcome"], ran=result["ran"])
                        for name, result in tests.items()}
                for group, tests in value["controls"].items()}
    return dict(schema=value["schema"], predicates=value["predicates"], registries=value["registries"],
                shared_predicates_excluded=value["shared_predicates_excluded"],
                module_sources_before=value["module_sources_before"],
                module_sources_after=value["module_sources_after"],
                module_sources_unchanged=value["module_sources_unchanged"],
                fixture_sources_before=value["fixture_sources_before"],
                fixture_sources_after=value["fixture_sources_after"],
                fixture_sources_unchanged=value["fixture_sources_unchanged"],
                controls=controls, controls_all_passed=value["controls_all_passed"],
                r62_new_negatives=value["r62_new_negatives"], v1_self_check=value["v1_self_check"],
                vacuous=value["vacuous"], all_restored=value["all_restored"], rows=rows)


def register(path: Path):
    value = verdicts()
    data = canonical(value)
    handle = None
    try:
        handle = path.open("xb")
    except FileExistsError:
        raise SystemExit("REFUSED: the verdict file exists; registration happens once\n" + str(path))
    with handle:
        handle.write(data)
    path.chmod(0o444)
    return dict(action="register", path=str(path), bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(), registered=value)


def verify(path: Path):
    stored = json.loads(path.read_bytes().decode("utf-8"))
    fresh = verdicts()
    same = comparable(stored) == comparable(fresh)
    differences = [name for name in sorted(set(stored["rows"]) | set(fresh["rows"]))
                   if comparable(stored)["rows"].get(name) != comparable(fresh)["rows"].get(name)]
    detail_only = [name for name, row in fresh["rows"].items()
                   if name in stored["rows"] and name not in differences
                   and any(row[phase].get("detail") != stored["rows"][name][phase].get("detail")
                           for phase in ("control", "wrapper", "excised"))]
    stored_view, fresh_view = comparable(stored), comparable(fresh)
    differing_controls = sorted(group for group in set(stored_view["controls"]) | set(fresh_view["controls"])
                                if stored_view["controls"].get(group) != fresh_view["controls"].get(group))
    return dict(action="verify", path=str(path), reproduced=same,
                differing_predicates=differences, differing_control_groups=differing_controls,
                detail_text_differs_only=detail_only,
                stored_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                module_sources_equal=stored["module_sources_before"] == fresh["module_sources_before"],
                fixture_sources_equal=stored["fixture_sources_before"] == fresh["fixture_sources_before"],
                v1_self_check_equal=stored["v1_self_check"] == fresh["v1_self_check"],
                state_leakage="NONE_OBSERVED" if same else "PAUSE_REGISTRATION_AND_INVESTIGATE",
                recomputed=fresh)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true", help="write the verdict file once, 0444")
    mode.add_argument("--verify", action="store_true", help="recompute and require byte equality")
    parser.add_argument("--out", required=True, help="absolute path of the verdict file")
    args = parser.parse_args(argv)
    path = Path(args.out)
    if not path.is_absolute():
        raise SystemExit("REFUSED: --out must be an absolute path")
    result = register(path) if args.register else verify(path)
    print(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False))
    if args.verify and not result["reproduced"]:
        return 1
    value = result.get("registered", result.get("recomputed", {}))
    if (value.get("vacuous") or not value.get("controls_all_passed")
            or not value.get("v1_self_check", {}).get("reproduced")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
