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

``--register`` writes the verdict file once and refuses to overwrite it.
``--verify`` recomputes every verdict and requires byte equality with the
registered file, plus equality of every recorded source sha256. Any difference
is reported as checker state leakage and is a reason to pause registration, not
to re-register.

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


def verdicts():
    """Run the protocol once and return its canonical verdict object."""
    gate = protocol()
    before = gate.module_sources()
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
    after = gate.module_sources()
    return dict(schema=SCHEMA, predicates=len(rows),
                registries=dict(advice=sorted(gate.ADVICE_PREDICATES),
                                bind=sorted(gate.BIND_PREDICATES),
                                provider=sorted(gate.PROVIDER_PREDICATES)),
                shared_predicates_excluded=list(gate.SHARED_MATCHER_PREDICATES),
                module_sources_before=before, module_sources_after=after,
                module_sources_unchanged=before == after,
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
    return dict(schema=value["schema"], predicates=value["predicates"], registries=value["registries"],
                shared_predicates_excluded=value["shared_predicates_excluded"],
                module_sources_before=value["module_sources_before"],
                module_sources_after=value["module_sources_after"],
                module_sources_unchanged=value["module_sources_unchanged"],
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
    return dict(action="verify", path=str(path), reproduced=same,
                differing_predicates=differences, detail_text_differs_only=detail_only,
                stored_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                module_sources_equal=stored["module_sources_before"] == fresh["module_sources_before"],
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
    if result.get("registered", result.get("recomputed", {})).get("vacuous"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
