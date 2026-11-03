#!/usr/bin/env python3
"""Compare private A0/A1 against copied historical evidence, with zero input.

This does not repair the historical receipt, settle its task, or authorize live
work. The native journal is read only through its original binding and fence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from orchestration_campaign import anchored_ref
from orchestration_common import (ContractError, file_ref, read_ref, read_regular,
                                  verify_tree, within, write_bytes_once, write_json)
from orchestration_native import require
from upgrade_experiment import arm_paths, validate_context, validate_runtime


# No repository modules enter a private-controller interpreter. Its own scope
# bootstrap runs before imports that could open runtime files or start children.
REPLAY = r'''
import hashlib, importlib, json, sys
from pathlib import Path
controller, bundle_path = map(Path, sys.argv[1:3])
sys.path.insert(0, str(controller / 'scripts'))
import reconcile_handshake_ack as reconcile
import cmux_native_delivery as native
import mac_harness as harness
bundle = json.loads(bundle_path.read_bytes())
def read(pin):
    raw = Path(pin['path']).read_bytes()
    if len(raw) != pin['bytes'] or hashlib.sha256(raw).hexdigest() != pin['sha256']:
        raise ValueError('REPLAY_COPY_CHANGED')
    return raw
receipt, gate, attempt = [json.loads(read(bundle['copies'][n]))
                          for n in ('receipt', 'gate', 'attempt')]
text = read(bundle['copies']['wire']).decode('utf-8')
binding = attempt['native_binding']
row = receipt['executors'][0]
expected = row['ack_line_expected']
if (row['dispatch_payload_sha256'] != hashlib.sha256(text.encode()).hexdigest()
        or binding['payload_sha256'] != row['dispatch_payload_sha256']
        or attempt['binding'] != dict(identity=binding['identity'], marker=row['ack_nonce'],
                                    payload_sha256=binding['payload_sha256'])):
    raise ValueError('REPLAY_CHALLENGE_BINDING_CHANGED')
try:
    rows, selected = reconcile.validate_panel(harness, native, receipt, gate, binding)
    panel = dict(status='PASS', executor=selected['executor'], executor_count=len(rows))
except ValueError as exc:
    panel = dict(status='REFUSED', error=str(exc))
intent_at, fence = native._original_intent(attempt, binding, text)
notice = native.probe(binding, text, not_before=intent_at, paste_fence=fence,
                      max_bytes=min(native.MAX_SCAN, 4 * 1024 * 1024))
if notice.get('confirmed') is not True or not native.validate_proof(
        binding, text, notice.get('native_proof'), not_before=intent_at, paste_fence=fence):
    raise ValueError('REPLAY_ORIGINAL_NATIVE_NOTICE_UNVERIFIED')
ack = reconcile.scan_ack(native, binding, text, expected, intent_at, fence)
if ack.get('observed') is not True:
    raise ValueError('REPLAY_ORIGINAL_NATIVE_ACK_UNVERIFIED')
reconcile.require_ack_proof(native, binding, text, expected, intent_at, fence, ack['proof'])
modules = {}
for name, module in tuple(sys.modules.items()):
    filename = getattr(module, '__file__', None)
    if filename and Path(filename).resolve().parent == controller / 'scripts':
        path = Path(filename).resolve()
        raw = path.read_bytes()
        modules[name] = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
print(json.dumps(dict(schema='copied-historical-ack-replay-v1', panel=panel,
    native_notice=notice, native_ack=ack, original_intent_at=intent_at,
    original_fence=fence, loaded_modules=modules,
    original_receipt_status=receipt['status'], input_operations=0,
    original_receipt_writes=0, business_acceptance='NOT_EVALUATED', live_trial=False)))
'''


def replay(context_ref, evidence, output):
    context = validate_context(context_ref)
    output = within(output, context["paths"]["evidence"])
    require(not output.exists(), "replay output already exists; preserve its original evidence")
    require(set(evidence) == {"receipt", "gate", "attempt", "wire"}, "four original evidence paths required")
    originals = {name: file_ref(path) for name, path in evidence.items()}
    runtimes = {}
    for arm in ("A0", "A1"):
        pin = file_ref(arm_paths(context, arm)["controllers"] / "runtime.json")
        _, runtime = validate_runtime(pin, context_ref=context_ref)
        runtimes[arm] = pin, runtime
    output.mkdir(mode=0o700)
    copies = {name: write_bytes_once(output / (name + ".original"), read_ref(pin))
              for name, pin in originals.items()}
    bundle = write_json(output / "copied-inputs.json", dict(
        schema="copied-historical-ack-inputs-v1", context=context_ref,
        originals=originals, copies=copies), exclusive=True)
    result_refs, results = {}, {}
    for arm, (runtime_ref, runtime) in runtimes.items():
        result = subprocess.run([runtime["python"]["path"], "-I", "-B", "-c", REPLAY,
                                 runtime["controller"]["path"], bundle["path"]],
                                capture_output=True, text=True, timeout=45, cwd=output)
        # Preserve failures as evidence; never silently rerun a comparison.
        write_json(output / (arm + "-process.json"), dict(
            runtime=runtime_ref, returncode=result.returncode,
            stdout=result.stdout, stderr=result.stderr), exclusive=True)
        require(result.returncode == 0, arm + " replay process failed; inspect its process evidence")
        value = json.loads(result.stdout)
        results[arm] = value
        result_refs[arm] = write_json(output / (arm + "-result.json"), value, exclusive=True)
        verify_tree(runtime["controller"])
    require(results["A0"]["panel"] == dict(status="REFUSED",
            error="ACK_ORIGINAL_PANEL_ORDER_OR_BINDING_CHANGED"), "A0 did not reproduce the original defect")
    require(results["A1"]["panel"]["status"] == "PASS", "A1 did not validate the original panel")
    require(results["A0"]["native_ack"] == results["A1"]["native_ack"] and
            results["A0"]["native_notice"] == results["A1"]["native_notice"],
            "arms did not verify identical original native evidence")
    for pin in originals.values():
        read_ref(pin, "unchanged original evidence")
    verify_tree(context["source_controller"])
    return write_json(output / "comparison.json", dict(
        schema="isolated-historical-ack-comparison-v1", status="PASS", context=context_ref,
        inputs=bundle, results=result_refs, original_evidence_unchanged=True,
        input_operations=0, original_receipt_writes=0, live_trial=False,
        conclusion="A0 reproduces ref/UUID rejection; A1 accepts the same original panel and native ACK.",
        excluded_claims=["historical_receipt_repaired", "active_client_adoption", "business_acceptance",
                         "live_collaboration", "measured_efficiency"]), exclusive=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("experiment", "experiment-sha256", "receipt", "gate", "attempt", "wire", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        result = replay(anchored_ref(args.experiment, args.experiment_sha256),
                        {n: getattr(args, n) for n in ("receipt", "gate", "attempt", "wire")}, args.output)
        print(json.dumps(result, indent=2))
        return 0
    except (ContractError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps(dict(status="REFUSED", error=str(exc), input_operations=0)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
