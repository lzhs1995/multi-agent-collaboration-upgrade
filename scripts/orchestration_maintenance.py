#!/usr/bin/env python3
"""Freeze and operate a bounded preparation-exit repair for an older campaign.

The old campaign, Python, controller, claim, lease and event prefix stay intact.
Only this separately pinned adapter understands the new failure boundary. Its
manifest never reads a candidate or settled file, so terminal replay is acyclic.
There is no sender, replacement session, lease override or public fixture mode.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

import orchestration_inputs
from orchestration_adapter import validate_adapter
from orchestration_campaign import anchored_ref, find_control, node_write_scope, validate_campaign
from orchestration_common import (ContractError, contract_errors, exclusive_lock, file_ref, json_ref, parse_time, read_ref,
                                  tree_manifest, utc_now, verify_tree, write_json)
from orchestration_coordinator import Coordinator, LeaseRegistry, State
from orchestration_evidence import (failure_marker, node_directory, validate_failure_candidate,
                                    verify_failure, verify_failure_value)
from orchestration_freeze import copy_runtime
from orchestration_native import NativeReader, require
from orchestration_preparation import freeze_failure_candidate_locked, freeze_preparation_proof_locked
from orchestration_transport import PendingOperation, Transport, verify_disarm


@contract_errors("preparation maintenance manifest")
def validate_maintenance(ref, campaign, header, node, info, preparation_exit, *, fixtures=False):
    value = json_ref(ref, "frozen preparation maintenance")
    root = node_directory(campaign, header, node) / "maintenance"
    fields = {"version", "evidence_class", "campaign", "run", "claim", "event_prefix", "preparation_exit",
              "adapter", "python", "authorization", "run_id", "node_id", "created_at", "lease"}
    require(set(value) == fields and type(value["version"]) is int and value["version"] == 1 and
            value["evidence_class"] == ("offline_fixture" if fixtures else "cli_preparation_maintenance_v1"),
            "maintenance fields or execution class changed")
    require(ref["path"] == str(root / "manifest.json") and value["adapter"]["path"] == str(root / "adapter"),
            "maintenance manifest/runtime location changed")
    require(value["campaign"] == header["campaign"] and json_ref(value["campaign"]) == campaign and
            value["run_id"] == header["run"]["run_id"] and value["node_id"] == node["id"] and
            value["python"] == campaign["python"] and value["authorization"] == campaign["authorization"] and
            value["preparation_exit"] == preparation_exit, "maintenance target/controller authority changed")
    proof = json_ref(preparation_exit)
    require(value["run"] == proof["history"]["run"] and value["claim"] == proof["history"]["claim"] and
            value["event_prefix"] == proof["history"]["events"] and json_ref(value["run"]) == header and
            all(info[k] == v for k, v in json_ref(value["claim"]).items()), "maintenance original prefix/claim changed")
    require(parse_time(value["created_at"]) >= parse_time(proof["observed_at"]), "maintenance predates preparation observation")
    # Leaf-only registry reads keep state → failure → maintenance validation
    # acyclic. Terminal ownership and release fences are verified by the caller.
    lease = value["lease"]
    LeaseRegistry(campaign["lease_root"]).verify_original(info["task_id"], lease)
    require(lease["session_id"] == campaign["sessions"][node["executor"]]["session_id"] and
            lease["scope"] == str(node_write_scope(Path(campaign["root"]) / "runs" / header["run"]["run_id"], node)) and
            lease["claim"] == value["claim"] and
            parse_time(info["started_at"]) <= parse_time(lease["reserved_at"]) <= parse_time(value["created_at"]),
            "maintenance original reservation target or chronology changed")
    validate_adapter(value["adapter"])
    read_ref(value["python"])
    read_ref(value["authorization"])
    return value


def maintenance_control(ref, marker):
    """Exact native functions.exec request, including one terminal LF."""
    manifest = json_ref(ref)
    validate_adapter(manifest["adapter"])
    campaign = json_ref(manifest["campaign"])
    argv = ["rtk", "proxy", manifest["python"]["path"], "-B",
            str(Path(manifest["adapter"]["path"]) / "scripts/orchestration_maintenance.py"), "settle",
            "--manifest", ref["path"], "--manifest-sha256", ref["sha256"], "--marker", marker]
    args = dict(cmd=shlex.join(argv), workdir=campaign["root"], login=False, max_output_tokens=1600)
    return "text(await tools.exec_command(" + json.dumps(args, ensure_ascii=False, separators=(",", ":")) + "));\n"


def freeze_maintenance(campaign_ref, run_id, key, *, fixtures=False, runtime=None):
    campaign = validate_campaign(campaign_ref, fixtures=fixtures)
    co = Coordinator(Path(campaign["root"]) / "runs" / run_id, campaign_ref, fixtures=fixtures)
    with co.node_locks(key):
        co._gate()
        states, data = co.state()
        require(states[key] == State.BLOCKED, "maintenance freeze requires the original blocked preparation")
        info, node = data[key], co.nodes[key]
        transport = Transport(co, key, runtime=runtime)
        proof_ref = freeze_preparation_proof_locked(transport, info)
        root = node_directory(campaign, co.header, node) / "maintenance"
        path = root / "manifest.json"
        if path.exists():
            ref = file_ref(path)
            with exclusive_lock(co.leases.root / "registry.lock"):
                validate_maintenance(ref, campaign, co.header, node, info, proof_ref, fixtures=fixtures)
            return ref
        require(not root.exists(), "incomplete maintenance freeze exists; preserve its original files")
        proof = json_ref(proof_ref)
        source = tree_manifest(Path(__file__).resolve().parent.parent)
        validate_adapter(source)
        require(Path(source["path"]) not in root.parents, "maintenance copy must be separate from its source")
        root.mkdir()
        adapter = copy_runtime(source, root / "adapter")
        validate_adapter(adapter)
        with exclusive_lock(co.leases.root / "registry.lock"):
            lease = co._lease(key, info)
            require(lease["released_at"] is None, "maintenance cannot freeze an already released reservation")
            value = dict(version=1, evidence_class="offline_fixture" if fixtures else "cli_preparation_maintenance_v1",
                         campaign=campaign_ref, run=proof["history"]["run"], claim=proof["history"]["claim"],
                         event_prefix=proof["history"]["events"], preparation_exit=proof_ref, adapter=adapter, lease=lease,
                         python=campaign["python"], authorization=campaign["authorization"], run_id=run_id, node_id=key,
                         created_at=utc_now())
            ref = write_json(path, value, exclusive=True)
            validate_maintenance(ref, campaign, co.header, node, info, proof_ref, fixtures=fixtures)
        return ref


class Maintenance:
    def __init__(self, ref, *, fixtures=False, runtime=None):
        self.ref, self.fixtures = ref, fixtures
        self.manifest = json_ref(ref)
        self.campaign_ref = self.manifest["campaign"]
        self.campaign = validate_campaign(self.campaign_ref, fixtures=fixtures)
        self.co = Coordinator(Path(self.campaign["root"]) / "runs" / self.manifest["run_id"], self.campaign_ref, fixtures=fixtures)
        self.key = self.manifest["node_id"]
        require(self.key in self.co.nodes, "unknown maintenance node")
        self.node = self.co.nodes[self.key]
        self.root = node_directory(self.campaign, self.co.header, self.node)
        self.transport = Transport(self.co, self.key, runtime=runtime)
        self._validate()

    def _validate(self):
        validate_adapter(self.manifest["adapter"])
        info = self.co.state()[1][self.key]
        validate_maintenance(self.ref, self.campaign, self.co.header, self.node, info,
                             self.manifest["preparation_exit"], fixtures=self.fixtures)
        if not self.fixtures:
            adapter, controller = self.manifest["adapter"], self.campaign["controller"]
            scripts = Path(adapter["path"]) / "scripts"
            require(Path(__file__).resolve() == scripts / "orchestration_maintenance.py",
                    "invoke the separately frozen maintenance entrypoint")
            require(str(Path(sys.executable).resolve()) == self.manifest["python"]["path"], "maintenance Python changed")

            def bound_source(module, manifest, name):
                key = "scripts/" + name + ".py"
                entry = manifest["files"].get(key)
                source = Path(manifest["path"]) / key
                origin = getattr(module, "__file__", None)
                require(isinstance(origin, str) and Path(origin).resolve() == source and entry is not None,
                        "loaded module differs from its exact frozen source: " + name)
                read_ref(dict(path=str(source), bytes=entry["bytes"], sha256=entry["sha256"]),
                         "loaded module " + name)

            verify_tree(controller)
            for name, module in tuple(sys.modules.items()):
                if name.startswith("orchestration_") or name == "decision_provider":
                    bound_source(module, adapter, name)
            # The adapter parser is a private object, not a sys.modules entry.
            # The plain name may legitimately be imported by the old controller
            # before a second maintenance operation in the same process.
            # Load this dependency at module import, including BLOCKED replay
            # paths that never inspect native inputs before validation.
            bound_source(getattr(orchestration_inputs, "_literal", None), adapter, "cmux_submission_inputs")
            if "cmux_submission_inputs" in sys.modules:
                bound_source(sys.modules["cmux_submission_inputs"], controller, "cmux_submission_inputs")
            verify_tree(adapter)
            verify_tree(controller)

    def candidate(self):
        with self.co.node_locks(self.key):
            self._validate()
            self.co._gate()
            ref = freeze_failure_candidate_locked(self.co, self.key, self.manifest["preparation_exit"], self.ref)
            info = self.co.state()[1][self.key]
            return dict(candidate=ref, exec_script=maintenance_control(self.ref, failure_marker(info, json_ref(ref))))

    def settle(self, marker=None, *, resume=False):
        with self.co.node_locks(self.key):
            self._validate()
            self.co._gate()
            states, data = self.co.state()
            require(states[self.key] in (State.BLOCKED, State.FAILED_PRESERVED), "no original preparation blocker")
            info = data[self.key]
            candidate = json_ref(file_ref(self.root / "failure-candidate.json"))
            require(candidate.get("maintenance") == self.ref, "candidate has a different maintenance controller")
            validate_failure_candidate(self.campaign, self.co.header, self.node, info, candidate,
                fixtures=self.fixtures, terminal_failure=info.get("failure"))
            expected = failure_marker(info, candidate)
            require(resume or marker == expected, "maintenance failure marker differs from frozen candidate")
            reader = NativeReader(self.campaign["sessions"]["codex"], fixtures=self.fixtures)
            proof = json_ref(candidate["preparation_exit"])
            native = find_control(reader, maintenance_control(self.ref, expected), after=proof["native_end"]["codex"]["offset"])
            path = self.root / "settled-failure.json"
            if not path.exists():
                require(states[self.key] == State.BLOCKED, "original terminal failure evidence is missing")
                value = dict(candidate, failure_native=native, native_end={lane: NativeReader(session, fixtures=self.fixtures).snapshot()
                             for lane, session in self.campaign["sessions"].items()})
                verify_failure_value(self.campaign, self.co.header, self.node, info, value, fixtures=self.fixtures)
                write_json(path, value, exclusive=True)
            ref = file_ref(path)
            require(json_ref(ref)["failure_native"] == native, "recovery must use the original maintenance control")
            return self.co._settle_failure_locked(self.key, ref)

    def verify(self):
        self._validate()
        states, data = self.co.state()
        info = data[self.key]
        require(states[self.key] == State.FAILED_PRESERVED, "preparation maintenance is not terminal")
        result = verify_failure(self.campaign, self.co.header, self.node, info, info["failure"], fixtures=self.fixtures)
        lease = self.co._lease(self.key, info)
        disarm = None
        if (self.root / "disarm.json").exists():
            disarm = verify_disarm(self.co, self.key, info, lease["released_by"])
        return dict(result, lease_released=lease["released_at"] is not None,
                    disarm=disarm,
                    adapter=self.manifest["adapter"]["path"], old_adapter_unchanged=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    freeze = subs.add_parser("freeze")
    for option in ("campaign", "campaign-sha256", "run", "node"):
        freeze.add_argument("--" + option, required=True)
    for action in ("candidate", "settle", "resume", "verify", "disarm", "repair-releases"):
        sub = subs.add_parser(action)
        sub.add_argument("--manifest", required=True)
        sub.add_argument("--manifest-sha256", required=True)
        if action == "settle":
            sub.add_argument("--marker", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "freeze":
            result = dict(manifest=freeze_maintenance(anchored_ref(args.campaign, args.campaign_sha256), args.run, args.node))
        else:
            maintenance = Maintenance(anchored_ref(args.manifest, args.manifest_sha256))
            if args.action in ("settle", "resume"):
                result = maintenance.settle(getattr(args, "marker", None), resume=args.action == "resume")
            elif args.action == "disarm":
                result = maintenance.transport.disarm()
            elif args.action == "repair-releases":
                result = dict(repaired=maintenance.co.repair_releases())
            else:
                result = getattr(maintenance, args.action)()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        result = dict(status="PENDING_OR_BLOCKED" if isinstance(exc, PendingOperation) else "REJECTED",
                      error=str(exc), input_retry_authorized=False)
        if getattr(exc, "diagnostic", None) is not None:
            result["diagnostic"] = exc.diagnostic
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 3 if isinstance(exc, PendingOperation) else 2


if __name__ == "__main__":
    raise SystemExit(main())
