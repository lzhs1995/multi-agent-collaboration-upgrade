#!/usr/bin/env python3
"""Operate a frozen campaign without impersonating either CLI's business work.

Control scripts are returned as text for the original supervisor to execute.
Recovery consumes an existing original native invocation; it never appends a
synthetic transcript, repeats input or silently replaces a frozen artifact.
There is intentionally no fixture flag in the public CLI.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from orchestration_campaign import anchored_ref, control_script, find_control, validate_campaign
from orchestration_common import (ContractError, checked_path, exclusive_lock, file_ref, json_ref, read_json,
                                  tree_manifest, utc_now, write_json)
from orchestration_coordinator import Coordinator, State
from orchestration_contracts import campaign_version, evidence_class
from orchestration_evidence import (dispatch_journal, failure_marker, make_test_result, marker_for,
                                    node_directory, output_refs, task_identity, validate_candidate, validate_failure_candidate,
                                    verify_failure_value, verify_node)
from orchestration_native import NativeReader, require
from orchestration_transport import PendingOperation, Transport
from orchestration_verify import finish_run, verify_campaign, verify_run


def business_command(campaign, info):
    if "workload" not in campaign:
        return None
    return shlex.join(["rtk", "proxy", campaign["python"]["path"], "-B", campaign["workload"]["path"],
                      "--request", info["work_request"]["path"], "--request-sha256", info["work_request"]["sha256"],
                      "--output-root", info["output_root"]])


class Runner:
    def __init__(self, campaign_ref, *, fixtures=False, runtime=None):
        self.ref = campaign_ref
        self.campaign = validate_campaign(campaign_ref, fixtures=fixtures)
        self.fixtures = fixtures
        require(fixtures or runtime is None, "runtime injection is offline-only")
        self.runtime = runtime

    def spec(self, run_id):
        spec = next((r for r in self.campaign["runs"] if r["run_id"] == run_id), None)
        require(spec is not None, "unknown frozen run")
        return spec

    def co(self, run_id):
        self.spec(run_id)
        return Coordinator(Path(self.campaign["root"]) / "runs" / run_id, self.ref, fixtures=self.fixtures)

    def transport(self, co, key):
        require(key in co.nodes, "unknown frozen node")
        return Transport(co, key, runtime=self.runtime)

    def start_script(self, run_id):
        self.spec(run_id)
        require(not (Path(self.campaign["root"]) / "runs" / run_id).exists(),
                "original run exists; use status/recover, never repeat its start call")
        return dict(exec_script=control_script(self.campaign, self.ref, "start", run_id))

    def wait_permit(self, run_id, timeout_seconds=45):
        self.spec(run_id)
        require(campaign_version(self.campaign) == 3, "registered permit waits require a v3 campaign")
        from orchestration_permit import wait_permit
        return wait_permit(self.campaign, run_id, timeout_seconds)

    def start(self, run_id):
        spec = self.spec(run_id)
        root = Path(self.campaign["root"]) / "runs" / run_id
        Coordinator.create(root, self.ref, spec, fixtures=self.fixtures)
        return self.status(run_id)

    def status(self, run_id=None):
        if run_id is None:
            rows = []
            for spec in self.campaign["runs"]:
                root = Path(self.campaign["root"]) / "runs" / spec["run_id"]
                state = "CLOSED" if (root / "finish.json").exists() else "STARTED" if (root / "run.json").exists() else (
                    "INCOMPLETE_START" if root.exists() else "NOT_STARTED")
                rows.append(dict(spec, state=state))
            return dict(campaign=self.ref, evidence_class=self.campaign["evidence_class"], runs=rows)
        spec = self.spec(run_id)
        root = checked_path(Path(self.campaign["root"]) / "runs" / run_id)
        header = checked_path(root / "run.json")
        require(not root.exists() or root.is_dir(), "run root must be a directory")
        if not header.exists():
            # Status is also the entrypoint before the first native start.
            # An existing partial directory is never permission to restart it.
            return dict(run=spec, root=str(root), state="INCOMPLETE_START" if root.exists() else "NOT_STARTED",
                        closed=False, states={}, ready=[])
        co = self.co(run_id)
        states, _ = co.state()
        closed = (co.root / "finish.json").exists()
        return dict(run=co.spec, root=str(co.root), state="CLOSED" if closed else "STARTED",
                    closed=closed, states={k: s.value for k, s in states.items()},
                    ready=[] if closed else [n["id"] for n in co.ready()])

    def drafts(self, run_id):
        self._upgrade_features()
        from orchestration_observe import package_drafts
        return package_drafts(self.ref, self.campaign, self.spec(run_id))

    def publish_drafts(self, run_id):
        self._upgrade_features()
        from orchestration_drafts import publish_drafts
        return publish_drafts(self, run_id)

    def next_action(self, run_id):
        self._upgrade_features()
        from orchestration_observe import next_action
        return next_action(self, run_id)

    def publish_continuation(self, run_id, key):
        self._upgrade_features()
        from orchestration_continuation import publish_continuation_locked
        co = self.co(run_id)
        require(key in co.nodes, "unknown frozen node")
        with exclusive_lock(node_directory(self.campaign, co.header, co.nodes[key]) / "runner.lock"):
            _, data = co.state()
            return publish_continuation_locked(co, key, data[key])

    def _upgrade_features(self):
        require(campaign_version(self.campaign) != 3 or self.campaign["arm"] == "B",
                "A1 uses native supervisor scheduling; upgrade drafts/advice/continuation are B-only")

    def claim(self, run_id, key):
        co = self.co(run_id)
        require(key in co.nodes, "unknown frozen node")
        states, data = co.state()
        info = co.claim(key) if states[key] == State.PENDING else data[key]
        state = co.state()[0][key]
        return dict(task_id=info["task_id"], state=state.value, output_root=info["output_root"],
                    work_request=info["work_request"], business_command=business_command(self.campaign, info)
                    if state == State.LOCAL_RUNNING else None)

    def _snapshots(self):
        return {k: NativeReader(s, fixtures=self.fixtures).snapshot() for k, s in self.campaign["sessions"].items()}

    def _protocol(self, co, key, info):
        return dict(prepared=info["prepared"], delivery=info["delivery"], callback=self.transport(co, key).callback())

    def candidate(self, run_id, key):
        co = self.co(run_id)
        require(key in co.nodes, "unknown frozen node")
        directory = node_directory(self.campaign, co.header, co.nodes[key])
        with exclusive_lock(directory / "runner.lock"):
            states, data = co.state()
            info, node = data[key], co.nodes[key]
            require(states[key] in (State.LOCAL_RUNNING, State.DISPATCHED, State.EVIDENCE_READY, State.ACCEPTED),
                    "node has no completed local work or original task delivery")
            path = directory / "candidate.json"
            if not path.exists():
                co._gate()
                require(states[key] in (State.LOCAL_RUNNING, State.DISPATCHED), "persisted candidate is missing")
                protocol = self._protocol(co, key, info) if info["mode"] == "delegated_cli" else None
                if protocol is not None and campaign_version(self.campaign) == 3 and self.campaign["arm"] == "B":
                    from orchestration_continuation import publish_continuation_locked
                    publish_continuation_locked(co, key, info, protocol=protocol)
                manifest, outputs, report = output_refs(self.campaign, co.header, node, info)
                tests = directory / "tests.json"
                if not tests.exists():
                    write_json(tests, make_test_result(self.campaign, co.header, node, info, outputs), exclusive=True)
                value = dict(version=campaign_version(self.campaign), evidence_class=evidence_class(self.campaign, "cli_node", self.fixtures),
                    **task_identity(self.campaign, co.header, node), execution_kind=info["mode"],
                    output_tree=manifest, output_files=outputs, report=report, tests=file_ref(tests),
                    candidate_native_end=self._snapshots())
                value.update(protocol=protocol) if protocol is not None else value.update(review_kind="solo_self_review")
                write_json(path, value, exclusive=True)
            ref = file_ref(path)
            value = validate_candidate(self.campaign, co.header, node, info, ref, fixtures=self.fixtures)
            if states[key] in (State.LOCAL_RUNNING, State.DISPATCHED):
                co.transition(key, State.EVIDENCE_READY, {"candidate": ref})
            else:
                require(info["candidate"] == ref, "original candidate pin changed")
            result = dict(candidate=ref, tests=value["tests"], report=value["report"], state=co.state()[0][key].value)
            if states[key] != State.ACCEPTED:
                result["exec_script"] = control_script(self.campaign, self.ref, "accept", run_id,
                    node_id=key, marker=marker_for(info, value))
            return result

    def accept(self, run_id, key, marker=None, *, resume=False):
        co = self.co(run_id)
        require(key in co.nodes, "unknown frozen node")
        directory = node_directory(self.campaign, co.header, co.nodes[key])
        with exclusive_lock(directory / "runner.lock"):
            states, data = co.state()
            info, node = data[key], co.nodes[key]
            require(states[key] in (State.EVIDENCE_READY, State.ACCEPTED), "node has no frozen candidate")
            candidate = validate_candidate(self.campaign, co.header, node, info, info["candidate"], fixtures=self.fixtures)
            expected = marker_for(info, candidate)
            require(resume or marker == expected, "native acceptance marker differs from frozen report/tests")
            script = control_script(self.campaign, self.ref, "accept", run_id, node_id=key, marker=expected)
            reader = NativeReader(self.campaign["sessions"]["codex"], fixtures=self.fixtures)
            native = find_control(reader, script, after=candidate["candidate_native_end"]["codex"]["offset"])
            path = directory / "accepted-evidence.json"
            if not path.exists():
                co._gate()
                require(states[key] == State.EVIDENCE_READY, "original accepted evidence is missing")
                write_json(path, dict(candidate, acceptance_native=native, native_end=self._snapshots()), exclusive=True)
            ref = file_ref(path)
            value = json_ref(ref)
            require(value.get("acceptance_native") == native, "recovery must use the original native acceptance")
            if states[key] == State.ACCEPTED:
                require(info["accepted_evidence"] == ref, "original accepted evidence pin changed")
                return verify_node(self.campaign, co.header, node, info, value, fixtures=self.fixtures)
            return co.accept(key, ref)

    def block(self, run_id, key, reason):
        co = self.co(run_id)
        require(key in co.nodes and isinstance(reason, str) and reason.strip(), "node and concrete blocker reason required")
        directory = node_directory(self.campaign, co.header, co.nodes[key])
        with exclusive_lock(directory / "runner.lock"):
            states, data = co.state()
            info = data[key]
            require(info, "unclaimed task has no owned blocker")
            path = directory / "blocker.json"
            if not path.exists():
                co._gate()
                write_json(path, dict(task_id=info["task_id"], reason=reason, observed_at=utc_now()), exclusive=True)
            ref = file_ref(path)
            require(json_ref(ref).get("reason") == reason, "original blocker reason changed")
            if states[key] not in (State.BLOCKED, State.FAILED_PRESERVED):
                co.transition(key, State.BLOCKED, {"blocker": ref})
            else:
                require(info["blocker"] == ref, "original blocker pin changed")
            return dict(blocker=ref, state=co.state()[0][key].value, lease="retained_until_verified_terminal_boundary")

    def failure_candidate(self, run_id, key, boundary):
        co = self.co(run_id)
        require(key in co.nodes, "unknown frozen node")
        directory = node_directory(self.campaign, co.header, co.nodes[key])
        if boundary == "preparation_exited":
            from orchestration_preparation import freeze_failure_candidate_locked, freeze_preparation_proof_locked
            require(self.fixtures or Path(__file__).resolve() == Path(self.campaign["adapter"]["path"]) / "scripts/orchestration_run.py",
                    "old adapter needs its separately frozen maintenance entrypoint")
            with co.node_locks(key):
                co._gate()
                states, data = co.state()
                require(states[key] == State.BLOCKED, "failure candidate requires an original blocker")
                proof_ref = freeze_preparation_proof_locked(self.transport(co, key), data[key])
                ref = freeze_failure_candidate_locked(co, key, proof_ref)
                return dict(candidate=ref, boundary=boundary, exec_script=control_script(
                    self.campaign, self.ref, "settle", run_id, node_id=key, marker=failure_marker(data[key], json_ref(ref))))
        with exclusive_lock(directory / "runner.lock"):
            states, data = co.state()
            info, node = data[key], co.nodes[key]
            require(states[key] == State.BLOCKED, "failure candidate requires an original blocker")
            path = directory / "failure-candidate.json"
            if not path.exists():
                co._gate()
                value = dict(version=campaign_version(self.campaign), evidence_class=evidence_class(self.campaign, "cli_failure", self.fixtures),
                    **task_identity(self.campaign, co.header, node), blocker=info["blocker"], boundary=boundary,
                    output_tree=tree_manifest(info["output_root"], kind="output"))
                if boundary == "local_boundary":
                    require(info["mode"] == "local_cli", "local failure boundary requires the original Codex lane")
                elif boundary == "callback_boundary":
                    require(info["mode"] == "delegated_cli" and "delivery" in info, "original delivery required")
                    value.update(report=file_ref(Path(info["output_root"]) / node["report"]),
                                 protocol=self._protocol(co, key, info))
                else:
                    require(boundary == "no_business_input" and info["mode"] == "delegated_cli" and
                            "dispatch_intent" in info and "delivery" not in info,
                            "no proven preparation exit; preserve the lease")
                    value["no_input_attempts"] = [file_ref(p) for p in sorted(dispatch_journal(self.campaign, info).glob("attempt-*.json"))]
                    require(value["no_input_attempts"], "no original zero-input attempts; preserve the lease")
                validate_failure_candidate(self.campaign, co.header, node, info, value, fixtures=self.fixtures)
                write_json(path, value, exclusive=True)
            value = read_json(path)
            require(value.get("boundary") == boundary and value.get("blocker") == info["blocker"], "frozen failure candidate changed")
            return dict(candidate=file_ref(path), boundary=boundary, exec_script=control_script(
                self.campaign, self.ref, "settle", run_id, node_id=key, marker=failure_marker(info, value)))

    def settle(self, run_id, key, marker=None, *, resume=False):
        co = self.co(run_id)
        require(key in co.nodes, "unknown frozen node")
        directory = node_directory(self.campaign, co.header, co.nodes[key])
        with co.node_locks(key):
            states, data = co.state()
            require(states[key] in (State.BLOCKED, State.FAILED_PRESERVED), "no original blocker to settle")
            info = data[key]
            candidate = read_json(directory / "failure-candidate.json")
            expected = failure_marker(info, candidate)
            require(resume or marker == expected, "native failure marker differs from frozen candidate")
            script = control_script(self.campaign, self.ref, "settle", run_id, node_id=key, marker=expected)
            reader = NativeReader(self.campaign["sessions"]["codex"], fixtures=self.fixtures)
            native = find_control(reader, script, after=co.header["native_start"]["codex"]["offset"])
            path = directory / "settled-failure.json"
            if not path.exists():
                co._gate()
                require(states[key] == State.BLOCKED, "original failure evidence is missing")
                value = dict(candidate, failure_native=native, native_end=self._snapshots())
                verify_failure_value(self.campaign, co.header, co.nodes[key], info, value, fixtures=self.fixtures)
                write_json(path, value, exclusive=True)
            ref = file_ref(path)
            require(json_ref(ref).get("failure_native") == native, "recovery must use original native failure settlement")
            return co._settle_failure_locked(key, ref)

    def recover(self, run_id):
        co = self.co(run_id)
        observations = co.recover()
        states, _ = co.state()
        for key, state in states.items():
            directory = node_directory(self.campaign, co.header, co.nodes[key])
            files = {name: file_ref(directory / name) for name in (
                "tests.json", "candidate.json", "accepted-evidence.json", "failure-candidate.json", "settled-failure.json", "continuation.json")
                if (directory / name).is_file()}
            if files:
                observations.append(dict(node_id=key, state="PERSISTED_EVIDENCE", files=files,
                    action="validate_original_files; candidate/resume-accept/resume-settle; never repeat native control"))
            if co.campaign["sessions"][co.nodes[key]["executor"]]["provider"] == "claude" and state in (State.ACCEPTED, State.FAILED_PRESERVED) and not (directory / "disarm.json").exists():
                observations.append(dict(node_id=key, state="TERMINAL_DISARM_PENDING", action="disarm_original_task"))
        return dict(status=self.status(run_id), observations=observations, input_operations=0)


def exception_trace(exc):
    """Preserve failure locations and causal links without source text or locals."""
    pending, indexes, result = [exc], {id(exc): 0}, []
    for error in pending:
        frames, trace = [], error.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            frames.append(dict(file=code.co_filename, line=trace.tb_lineno, function=code.co_name))
            trace = trace.tb_next
        row = dict(exception_type=type(error).__name__, errno=getattr(error, "errno", None),
                   frames=frames, suppress_context=error.__suppress_context__)
        for relation in ("cause", "context"):
            linked = getattr(error, "__" + relation + "__")
            if linked is None:
                row[relation] = None
            else:
                if id(linked) not in indexes:
                    indexes[id(linked)] = len(pending)
                    pending.append(linked)
                row[relation] = indexes[id(linked)]
        result.append(row)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--campaign-sha256", required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    actions = ("status", "wait-permit", "next-action", "drafts", "publish-drafts", "publish-continuation", "start-script", "start", "claim", "prepare", "dispatch", "reconcile", "callback", "candidate",
               "accept", "resume-accept", "block", "failure-candidate", "settle", "resume-settle", "recover",
               "repair-releases", "disarm", "finish", "verify", "verify-campaign")
    node_actions = {"claim", "prepare", "dispatch", "reconcile", "callback", "candidate", "publish-continuation", "accept", "resume-accept",
                    "block", "failure-candidate", "settle", "resume-settle", "disarm"}
    for action in actions:
        sub = commands.add_parser(action)
        if action != "verify-campaign":
            sub.add_argument("--run", required=action != "status")
        if action in node_actions:
            sub.add_argument("--node", required=True)
        if action in ("accept", "settle"):
            sub.add_argument("--marker", required=True)
        if action == "block":
            sub.add_argument("--reason", required=True)
        if action == "failure-candidate":
            sub.add_argument("--boundary", required=True, choices=("local_boundary", "callback_boundary", "no_business_input", "preparation_exited"))
        if action == "wait-permit":
            sub.add_argument("--timeout-seconds", type=int, default=45)
    args = parser.parse_args(argv)
    try:
        ref = anchored_ref(args.campaign, args.campaign_sha256)
        runner = Runner(ref)
        require(Path(__file__).resolve() == Path(runner.campaign["adapter"]["path"]) / "scripts/orchestration_run.py",
                "invoke the complete frozen adapter, not the mutable checkout")
        action = args.action
        if action in ("prepare", "dispatch", "reconcile", "callback", "disarm"):
            result = getattr(runner.transport(runner.co(args.run), args.node), action)()
        elif action == "verify-campaign":
            result = verify_campaign(ref)
        elif action == "verify":
            result = verify_run(ref, args.run)
        elif action == "finish":
            result = finish_run(runner.co(args.run))
        elif action == "repair-releases":
            result = dict(repaired=runner.co(args.run).repair_releases())
        elif action == "wait-permit":
            result = runner.wait_permit(args.run, args.timeout_seconds)
        elif action in ("accept", "settle"):
            result = getattr(runner, action)(args.run, args.node, args.marker)
        elif action.startswith("resume-"):
            result = getattr(runner, action[7:])(args.run, args.node, resume=True)
        elif action == "block":
            result = runner.block(args.run, args.node, args.reason)
        elif action == "failure-candidate":
            result = runner.failure_candidate(args.run, args.node, args.boundary)
        elif action in node_actions:
            result = getattr(runner, action.replace("-", "_"))(args.run, args.node)
        else:
            result = getattr(runner, action.replace("-", "_"))(args.run)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 3 if isinstance(result, dict) and result.get("status") == "UNCERTAIN" else 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        result = dict(status="PENDING_OR_BLOCKED" if isinstance(exc, PendingOperation) else "REJECTED",
                      error=str(exc), input_retry_authorized=False, exception_trace=exception_trace(exc))
        if getattr(exc, "diagnostic", None) is not None:
            result["diagnostic"] = exc.diagnostic
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 3 if isinstance(exc, PendingOperation) else 2


if __name__ == "__main__":
    raise SystemExit(main())
