"""Original-controller transport for the two existing CLI sessions.

Every external phase has a durable intent. An interrupted phase is observation
only, never an instruction to replay input. This adapter owns no model calls,
terminal keys, replacement sessions, shared installation or runtime patches.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from orchestration_common import (ContractError, checked_path, contract_errors, digest, exclusive_lock, file_ref, json_ref,
                                  object_sha, parse_time, read_json, read_ref, utc_now, verify_tree, within,
                                  write_bytes_once, write_json)
from orchestration_coordinator import State
from orchestration_contracts import campaign_uuid, campaign_version, controller_uuid, state_root
from orchestration_disarm import (Recovery, freeze_original, invoke_disarm, invoke_recovery, observe_original,
                                  original_result, validate_frozen, validate_result)
from orchestration_evidence import (dispatch_journal, node_directory, pack_extension, task_identity,
                                    task_scope, task_sources, verify_dispatched, verify_prepared)
from orchestration_native import NativeReader, native_text, original_attempts, require, verify_delivery


class PendingOperation(ContractError):
    """The original operation must be observed, not replayed."""


class OperationJournal:
    def __init__(self, directory, campaign_ref, task_id, controller):
        self.root = checked_path(directory) / "operations"
        self.binding = dict(version=2, campaign=campaign_ref, task_id=task_id,
                            controller_sha256=object_sha(controller))

    def once(self, name, arguments, action):
        require(re.fullmatch(r"[a-z][a-z0-9-]*", name), "invalid operation name")
        root = self.root / name
        intent_path, result_path = root / "intent.json", root / "result.json"
        binding = dict(self.binding, operation=name, arguments=arguments)
        if intent_path.exists():
            intent = read_json(intent_path)
            require({k: v for k, v in intent.items() if k != "created_at"} == binding,
                    "original external operation binding changed")
            if not result_path.exists():
                raise PendingOperation(f"original {name} intent has no terminal result; observe {intent_path}")
            result = read_json(result_path)
            require(result.get("intent") == file_ref(intent_path), "operation result differs from original intent")
        else:
            require(not result_path.exists(), "operation result has no original intent")
            intent_ref = write_json(intent_path, dict(binding, created_at=utc_now()), exclusive=True)
            # BaseException deliberately propagates. A process crash cannot
            # manufacture a terminal error or reset the external input budget.
            try:
                value = action(root)
                result = dict(intent=intent_ref, status="RETURNED", value=value, completed_at=utc_now())
            except Exception as exc:
                result = dict(intent=intent_ref, status="ERROR", error_type=type(exc).__name__,
                              error=str(exc)[:4000], completed_at=utc_now())
            write_json(result_path, result, exclusive=True)
        if result.get("status") != "RETURNED":
            raise PendingOperation(f"original {name} returned {result.get('error_type')}: {result.get('error')}; no replay")
        return result["value"]

    def observation(self, name, arguments, action):
        """Number explicit zero-input reconciliations, preserving each result."""
        number = 1
        while (self.root / f"{name}-{number:04d}").exists():
            number += 1
        return self.once(f"{name}-{number:04d}", arguments, action)


class PinnedRuntime:
    def __init__(self, campaign):
        self.campaign = campaign
        self.root = Path(campaign["controller"]["path"])
        self.scripts = self.root / "scripts"

    @contextlib.contextmanager
    def modules(self, *names):
        verify_tree(self.campaign["controller"])
        read_ref(self.campaign["python"], "frozen Python")
        require(str(Path(sys.executable).resolve()) == self.campaign["python"]["path"], "Python changed")
        available = {p.stem for p in self.scripts.glob("*.py")}

        def provenance():
            for name in available & set(sys.modules):
                module = sys.modules[name]
                require(getattr(module, "__file__", None), f"unbound controller module: {name}")
                within(Path(module.__file__).resolve(), self.scripts)

        provenance()
        require(set(names) <= available, "module is absent from the pinned controller")
        sys.path.insert(0, str(self.scripts))
        try:
            loaded = tuple(importlib.import_module(name) for name in names)
            provenance()
            yield loaded
            provenance()
        finally:
            sys.path.pop(0)
            verify_tree(self.campaign["controller"])

    def harness_argv(self, phase, task_id, artifact_root, *, executor_id=None):
        if campaign_version(self.campaign) == 2:
            require(executor_id in (None, "claude"), "legacy campaign has one Claude lane")
            executor_id = "claude"
        require(executor_id in self.campaign["sessions"] and
                self.campaign["sessions"][executor_id]["provider"] == "claude",
                "an explicit original Claude executor is required")
        receiver = self.campaign["sessions"][executor_id]
        return [self.campaign["python"]["path"], "-B", str(self.scripts / "mac_harness.py"), phase,
                "--task-id", task_id, "--artifact-root", str(artifact_root), "--executor", "claude",
                "--supervisor", "codex", "--executor-surface", receiver["surface_uuid"],
                "--expected-workspace-uuid", receiver["workspace_uuid"],
                "--expected-executor-uuid", receiver["surface_uuid"]]

    def harness(self, argv, operation_root):
        verify_tree(self.campaign["controller"])
        read_ref(self.campaign["python"])
        stdout, stderr = operation_root / "stdout.txt", operation_root / "stderr.txt"
        with stdout.open("xb") as out, stderr.open("xb") as err:
            # The harness owns its 600-second handshake budget. A short tool
            # observation window must not kill it or start another preflight.
            result = subprocess.run(argv, cwd=self.campaign["root"], stdout=out, stderr=err, check=False)
            for stream in (out, err):
                stream.flush()
                os.fsync(stream.fileno())
        return dict(returncode=result.returncode, stdout=file_ref(stdout), stderr=file_ref(stderr))

    def notice(self, pack):
        with self.modules("cmux_bridge") as (bridge,):
            return bridge.task_pack_notice(pack)

    def submit(self, surface, notice, pack, *, reconcile_only):
        with self.modules("cmux_bridge") as (bridge,):
            return bridge.submit_task_pack(surface, notice, pack, confirm_lines=200,
                                           force_compose=False, reconcile_only=reconcile_only)

    def settle_callback(self, pack, transcript, line):
        with self.modules("cmux_bridge", "callback_native_evidence") as (bridge, callback):
            # The actual foreground supervisor and environment are retained.
            # Never invoke the executor's submit_completion_callback here.
            return callback.reconcile_received(pack, bridge, transcript=transcript, line=line)

    def freeze_disarm(self, task_id):
        with self.modules("mac_harness", "executor_reask_lifecycle", "executor_reask") as modules:
            return freeze_original(self.campaign, task_id, *modules)

    def observe_disarm(self, frozen):
        with self.modules("mac_harness", "executor_reask_lifecycle", "executor_reask") as modules:
            return observe_original(self.campaign, frozen, *modules)

    def disarm(self, frozen):
        with self.modules("mac_harness", "executor_reask_lifecycle", "executor_reask") as modules:
            return invoke_disarm(self.campaign, frozen, *modules)

    def recover_disarm(self, frozen, action):
        with self.modules("mac_harness", "executor_reask_lifecycle", "executor_reask") as modules:
            return invoke_recovery(self.campaign, frozen, action, *modules)


def disarm_sources(campaign, campaign_ref, directory, task_id, terminal):
    """Validate immutable sources without reading the lease (avoids a cycle)."""
    frozen_ref = file_ref(checked_path(directory / "disarm-frozen.json"))
    frozen = json_ref(frozen_ref)
    validate_frozen(frozen, task_id, campaign["sessions"]["codex"]["workspace_uuid"], object_sha(campaign["controller"]),
                    uuid_semantics=campaign_version(campaign) == 3)
    require(campaign_uuid(campaign, frozen["supervisor_uuid"]) ==
            campaign_uuid(campaign, campaign["sessions"]["codex"]["surface_uuid"]), "frozen marker owner changed")
    args = dict(task_id=task_id, terminal=terminal, frozen=frozen_ref)
    operation = directory / "operations/scoped-disarm"
    intent_ref = file_ref(checked_path(operation / "intent.json"))
    intent = json_ref(intent_ref)
    require(set(intent) == {"version", "campaign", "task_id", "controller_sha256", "operation", "arguments", "created_at"} and
            {k: v for k, v in intent.items() if k != "created_at"} == dict(
                version=2, campaign=campaign_ref, task_id=task_id,
                controller_sha256=object_sha(campaign["controller"]), operation="scoped-disarm", arguments=args),
            "disarm intent is not bound to the original terminal task/frozen marker")
    require(parse_time(json_ref(terminal)["at"]) <= parse_time(frozen["frozen_at"]) <= parse_time(intent["created_at"]),
            "frozen marker/disarm chronology differs from terminal evidence")
    return args, intent_ref, original_result(intent_ref)


@contract_errors("scoped disarm")
def verify_disarm(co, key, info, terminal):
    """An original return or bounded recovery must prove every reservation gone."""
    lease = co._lease(key, info)
    require(lease["released_at"] is not None and lease["released_by"] == terminal,
            "scoped disarm requires the exact terminal's released lease")
    directory = node_directory(co.campaign, co.header, co.nodes[key])
    ref = file_ref(checked_path(directory / "disarm.json"))
    record = json_ref(ref)
    args, intent_ref, result_ref = disarm_sources(co.campaign, co.campaign_ref, directory, info["task_id"], terminal)
    require(set(record) == {"version", "task_id", "terminal", "frozen", "operation_result", "proof_kind", "proof"} and
            type(record["version"]) is int and record["version"] == 3 and
            all(record[k] == v for k, v in args.items()) and record["operation_result"] == result_ref,
            "scoped disarm record binding changed")
    if record["proof_kind"] == "ORIGINAL_RETURN":
        require(not (directory / "disarm-recovery").exists(), "original return cannot hide recovery slots")
        require(result_ref is not None, "original disarm result absent")
        result = json_ref(result_ref)
        require(result["status"] == "RETURNED" and record["proof"] == result["value"], "original return proof changed")
        validate_result(result["value"], json_ref(args["frozen"]))
        require(parse_time(json_ref(intent_ref)["created_at"]) <= parse_time(result["value"]["before"]["observed_at"]) <=
                parse_time(result["value"]["observed_at"]) <= parse_time(result["completed_at"]),
                "disarm result outside original operation")
    else:
        require(record["proof_kind"] == "BOUNDED_RECOVERY", "unknown disarm proof kind")
        recovery = Recovery(directory / "disarm-recovery", args["frozen"], intent_ref, result_ref)
        require(record["proof"] == recovery.verify(), "disarm recovery proof changed")
    return ref


def journal_bundle(directory, receipt=None):
    directory = checked_path(directory)
    refs = [file_ref(path) for path in sorted(directory.glob("attempt-*.json"))]
    original_attempts(refs)
    return dict(receipt=file_ref(receipt or directory / "receipt.json"), attempts=refs)


def handshake_journal(campaign, nonce):
    caller = controller_uuid(campaign, campaign["sessions"]["codex"]["surface_uuid"])
    key = digest(json.dumps([caller, nonce], sort_keys=True).encode())
    return state_root(campaign) / "message-dispatch-v1" / key


def exact_native(reader, text, start, role):
    matches = [(ref, record) for ref, record in reader.records(start, reader.snapshot()["offset"])
               if native_text(record, reader.provider, role) == text]
    require(len(matches) == 1, "one exact original native message required")
    return matches[0][0]


class Transport:
    def __init__(self, coordinator, node_id, *, runtime=None):
        self.co = coordinator
        self.key = node_id
        self.node = coordinator.nodes[node_id]
        require(coordinator.fixtures or runtime is None, "runtime overrides are offline-test-only")
        self.campaign = coordinator.campaign
        self.receiver = self.campaign["sessions"][self.node["executor"]]
        require(self.receiver["provider"] == "claude", "transport only serves an original Claude executor")
        self.root = node_directory(self.campaign, coordinator.header, self.node)
        self.runtime = runtime if runtime is not None else PinnedRuntime(self.campaign)

    def current(self):
        states, data = self.co.state()
        require(data[self.key], "claim the original node before transport")
        return states[self.key], data[self.key]

    def journal(self, info):
        return OperationJournal(self.root, self.co.campaign_ref, info["task_id"], self.campaign["controller"])

    def _bundle(self, info):
        return journal_bundle(dispatch_journal(self.campaign, info))

    def _harness_argv(self, phase, task_id, artifact_root):
        if campaign_version(self.campaign) == 2:
            return self.runtime.harness_argv(phase, task_id, artifact_root)
        return self.runtime.harness_argv(phase, task_id, artifact_root, executor_id=self.node["executor"])

    def _preparation_proof(self, info, *, existing_only=False):
        preflight = self.root / "preflight"
        handshake = read_json(preflight / "handshake-receipt.json")
        original = handshake_journal(self.campaign, handshake["ack_nonce"])
        delivery = journal_bundle(original)
        receipt = json_ref(delivery["receipt"])
        reader = NativeReader(self.receiver, fixtures=self.co.fixtures)
        # The ordinary journal intentionally does not store the complete notice.
        # Recover its exact text only from the receipt's original native record.
        notice = native_text(reader.record(receipt["native_proof"]), "claude", "user")
        require(isinstance(notice, str), "handshake original native notice is missing")
        notice_path = self.root / "handshake-notice.txt"
        if existing_only:
            require(read_ref(file_ref(notice_path)) == notice.encode(), "original handshake notice differs")
        hs = dict(receipt=file_ref(preflight / "handshake-receipt.json"), delivery=delivery,
                  notice=file_ref(notice_path) if existing_only else write_bytes_once(notice_path, notice.encode()),
                  ack_native=exact_native(reader, handshake["ack_line"], info["native_start"]["offset"], "assistant"))
        if handshake.get("dispatch_body_reference"):
            hs["body"] = {k: handshake["dispatch_body_reference"][k] for k in ("path", "sha256", "bytes")}
            read_ref(hs["body"])
        return dict(pack=file_ref(preflight / "task-pack.json"), notice=file_ref(self.root / "task-notice.txt"),
                    handshake=hs, identity_gate=file_ref(preflight / "identity-gate.json"),
                    validation=file_ref(preflight / "validation.json"))

    def prepare(self):
        with exclusive_lock(self.root / "runner.lock"), exclusive_lock(self.root / "transport.lock"):
            self.co._gate()
            state, info = self.current()
            if "prepared" in info:
                verify_prepared(self.campaign, self.co.header, self.node, info, info["prepared"], fixtures=self.co.fixtures)
                return info["prepared"]
            require(state == State.PREPARING, "preparation is not active")
            self.co._work_budget()
            journal = self.journal(info)
            config_path = self.root / "transport.json"
            expected = dict(version=2, task_id=info["task_id"], campaign=self.co.campaign_ref)
            if not config_path.exists():
                write_json(config_path, dict(expected, completion_nonce=secrets.token_hex(12)), exclusive=True)
            config = read_json(config_path)
            require(set(config) == set(expected) | {"completion_nonce"} and
                    all(config.get(k) == v for k, v in expected.items()) and
                    re.fullmatch(r"[0-9a-f]{24}", config["completion_nonce"]), "original transport nonce/config changed")
            preflight = self.root / "preflight"
            for phase in ("preflight", "task-pack"):
                argv = self._harness_argv(phase, info["task_id"], preflight)
                result = journal.once(phase, {"argv": argv}, lambda op, argv=argv: self.runtime.harness(argv, op))
                require(result.get("returncode") == 0, f"original {phase} failed; inspect its saved output, do not repeat it")

            def fill(_):
                from orchestration_preparation import fill_scaffold
                path = preflight / "task-pack.json"
                pack = read_json(path)
                require(pack.get("draft") is True and pack.get("task_id") == info["task_id"], "original scaffold required")
                write_bytes_once(self.root / "original-scaffold.json", read_ref(file_ref(path)))
                handshake = read_json(preflight / "handshake-receipt.json")
                pack = fill_scaffold(self.campaign, self.co.header, self.node, info, pack, handshake,
                                     config["completion_nonce"])
                return write_json(path, pack)

            journal.once("fill-pack", {"transport": file_ref(config_path)}, fill)
            argv = self._harness_argv("finalize-pack", info["task_id"], preflight)
            result = journal.once("finalize-pack", {"argv": argv}, lambda op: self.runtime.harness(argv, op))
            require(result.get("returncode") == 0, "original finalization failed; preserve its result")
            pack_ref = file_ref(preflight / "task-pack.json")
            notice = journal.once("task-notice", {"pack": pack_ref}, lambda _: self.runtime.notice(pack_ref["path"]))
            require(isinstance(notice, str) and notice and not any(c in notice for c in "\r\n\t"), "invalid exact task notice")
            write_bytes_once(self.root / "task-notice.txt", notice.encode())
            proof = self._preparation_proof(info)
            verify_prepared(self.campaign, self.co.header, self.node, info, proof, fixtures=self.co.fixtures)
            self.co.transition(self.key, State.PREPARED, {"prepared": proof})
            return proof

    def _received(self, info):
        bundle = self._bundle(info)
        verify_dispatched(self.campaign, self.co.header, self.node, info, bundle, fixtures=self.co.fixtures)
        self.co.transition(self.key, State.DISPATCHED, {"delivery": bundle})
        return bundle

    def _uncertain(self, reason):
        state, info = self.current()
        if state == State.DISPATCH_INTENT:
            path = self.root / "uncertainty.json"
            if not path.exists():
                write_json(path, dict(task_id=info["task_id"], reason=str(reason), observed_at=utc_now()), exclusive=True)
            self.co.transition(self.key, State.UNCERTAIN, {"uncertainty": file_ref(path)})
        return dict(status="UNCERTAIN", reason=str(reason), action="reconcile_original_attempt_only")

    def dispatch(self):
        with exclusive_lock(self.root / "transport.lock"):
            self.co._gate()
            state, info = self.current()
            if "delivery" in info:
                return info["delivery"]
            if state in (State.DISPATCH_INTENT, State.UNCERTAIN):
                return self._observe(info, reconcile=False)
            require(state == State.PREPARED, "finalize the pack before dispatch")
            self.co._work_budget()
            intent_path = self.root / "dispatch-intent.json"
            # Even a crash before the lifecycle transition cannot grant a new
            # submit budget. The pre-existing intent becomes observe-only.
            existed = intent_path.exists()
            if not existed:
                value = dict(version=2, operation="submit_task_pack", **task_identity(self.campaign, self.co.header, self.node),
                             prepared=info["prepared"], controller_sha256=object_sha(self.campaign["controller"]),
                             created_at=utc_now(), native_fence=NativeReader(self.receiver,
                                                                          fixtures=self.co.fixtures).snapshot())
                write_json(intent_path, value, exclusive=True)
            self.co.transition(self.key, State.DISPATCH_INTENT, {"dispatch_intent": file_ref(intent_path)})
            _, info = self.current()
            if existed:
                return self._observe(info, reconcile=False)
            notice, pack = read_ref(info["prepared"]["notice"]).decode(), info["prepared"]["pack"]
            args = dict(surface=self.receiver["surface_uuid"], notice=info["prepared"]["notice"],
                        pack=pack, reconcile_only=False)
            try:
                self.journal(info).once("submit-task-pack", args,
                    lambda _: self.runtime.submit(args["surface"], notice, pack["path"], reconcile_only=False))
                return self._received(info)
            except Exception as exc:
                return self._uncertain(exc)

    def _observe(self, info, *, reconcile):
        directory = dispatch_journal(self.campaign, info)
        try:
            if (directory / "receipt.json").exists():
                return self._received(info)
            if reconcile:
                # No original attempts means there is nothing the bridge may
                # reconcile. In particular it may not create a fresh attempt.
                original_attempts([file_ref(p) for p in sorted(directory.glob("attempt-*.json"))])
                pack, notice = info["prepared"]["pack"], info["prepared"]["notice"]
                args = dict(pack=pack, notice=notice, reconcile_only=True)
                self.journal(info).observation("reconcile-task", args, lambda _: self.runtime.submit(
                    self.receiver["surface_uuid"], read_ref(notice).decode(),
                    pack["path"], reconcile_only=True))
                return self._received(info)
            return self._uncertain("original dispatch result is absent; no new input")
        except Exception as exc:
            return self._uncertain(exc)

    def reconcile(self):
        with exclusive_lock(self.root / "transport.lock"):
            self.co._gate()
            state, info = self.current()
            if "delivery" in info:
                return info["delivery"]
            require(state in (State.DISPATCH_INTENT, State.UNCERTAIN), "no original dispatch to reconcile")
            return self._observe(info, reconcile=True)

    def callback(self):
        with exclusive_lock(self.root / "transport.lock"):
            self.co._gate()
            _, info = self.current()
            require("delivery" in info, "task native receipt must precede callback settlement")
            pack_ref = info["prepared"]["pack"]
            pack = json_ref(pack_ref)
            receipt = Path(pack["completion_receipt"])
            journal = receipt.with_name(receipt.stem + "-attempts")
            if not receipt.exists():
                require((journal / "delivery.lock").is_file(), "original callback has not opened its delivery lock; keep waiting")
                reader = NativeReader(self.campaign["sessions"]["codex"], fixtures=self.co.fixtures)
                record = exact_native(reader, pack["completion_callback"], self.co.header["native_start"]["codex"]["offset"], "user")
                with reader.open() as stream:
                    # The supported receiver API uses a one-based original line.
                    line = 1
                    while stream.tell() < record["offset"]:
                        require(stream.readline(), "original callback line disappeared")
                        line += 1
                    require(stream.tell() == record["offset"], "callback is not a native line boundary")
                args = dict(pack=pack_ref, native_record=record, line=line, input_operations=0)
                self.journal(info).observation("settle-callback", args, lambda _: self.runtime.settle_callback(
                    pack_ref["path"], str(reader.path), line))
            bundle = journal_bundle(journal, receipt)
            verify_delivery(bundle, self.campaign["sessions"]["codex"], self.receiver,
                            pack["completion_callback"], fixtures=self.co.fixtures,
                            uuid_semantics=campaign_version(self.campaign) == 3)
            return bundle

    def disarm(self):
        with self.co.node_locks(self.key):
            state, info = self.current()
            require(state in (State.ACCEPTED, State.FAILED_PRESERVED), "only a verified terminal task may disarm")
            if not self.co.fixtures:
                from orchestration_campaign import require_live_identity
                require_live_identity(self.campaign)
            terminal = next(e for e in reversed(self.co.events()) if e["node_id"] == self.key)
            terminal_ref = file_ref(self.co.root / "events" / f"{terminal['seq']:06d}.json")
            lease = self.co._lease(self.key, info)
            require(lease["released_at"] is not None and lease["released_by"] == terminal_ref,
                    "scoped disarm requires the exact terminal's released lease")
            path = self.root / "disarm.json"
            if path.exists():
                return verify_disarm(self.co, self.key, info, terminal_ref)
            frozen_path = self.root / "disarm-frozen.json"
            operation = self.root / "operations/scoped-disarm"
            recovery_root = self.root / "disarm-recovery"
            if not frozen_path.exists():
                require(not operation.exists() and not recovery_root.exists(),
                        "cannot backfill a frozen marker after original disarm intent")
                write_json(frozen_path, self.runtime.freeze_disarm(info["task_id"]), exclusive=True)
            frozen_ref = file_ref(checked_path(frozen_path))
            frozen = json_ref(frozen_ref)
            validate_frozen(frozen, info["task_id"], self.campaign["sessions"]["codex"]["workspace_uuid"],
                            object_sha(self.campaign["controller"]), uuid_semantics=campaign_version(self.campaign) == 3)
            args = dict(task_id=info["task_id"], terminal=terminal_ref, frozen=frozen_ref)
            if not (operation / "intent.json").exists():
                require(not recovery_root.exists(), "recovery has no original disarm intent")
                try:
                    self.journal(info).once("scoped-disarm", args, lambda _: self.runtime.disarm(frozen))
                except PendingOperation:
                    pass  # Preserve ERROR exactly; recovery is separately numbered.
            args, intent_ref, result_ref = disarm_sources(self.campaign, self.co.campaign_ref, self.root,
                                                        info["task_id"], terminal_ref)
            proof, kind = None, None
            if result_ref is not None and not recovery_root.exists():
                result = json_ref(result_ref)
                if result["status"] == "RETURNED":
                    try:
                        validate_result(result["value"], frozen)
                        require(parse_time(json_ref(intent_ref)["created_at"]) <=
                                parse_time(result["value"]["before"]["observed_at"]) <=
                                parse_time(result["value"]["observed_at"]) <= parse_time(result["completed_at"]),
                                "disarm result outside original operation")
                        proof, kind = result["value"], "ORIGINAL_RETURN"
                    except (ContractError, KeyError, TypeError):
                        pass
            if kind is None:
                proof = Recovery(recovery_root, frozen_ref, intent_ref, result_ref, self.runtime).run()
                kind = "BOUNDED_RECOVERY"
            record = dict(version=3, **args, operation_result=result_ref, proof_kind=kind, proof=proof)
            write_json(path, record, exclusive=True)
            return verify_disarm(self.co, self.key, info, terminal_ref)
