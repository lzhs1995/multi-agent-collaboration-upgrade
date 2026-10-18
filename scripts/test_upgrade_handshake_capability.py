"""Busy handshake contract, actual generated parser/harness, offline transport."""
from __future__ import annotations

import ast
import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest.mock import Mock, patch

from orchestration_common import ContractError
import upgrade_handshake_capability as cap
from upgrade_live_patch import readonly_handshake, transform_live
from test_upgrade_bridge_probe_evidence import SOURCE, definition, parser_namespace, screen
from test_upgrade_identity import fixture, uid, uuid_selector_fixture

TEXT = "MESSAGE_REFERENCE_V2 original-pinned-challenge"


@unittest.skipUnless(SOURCE.is_dir(), "requires fixed local MAC source")
class CapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path("/offline/capability")
        cls.paths = {n: root / n for n in ("state", "controllers", "tmp", "config", "bin")}
        cls.original = (SOURCE / "scripts/mac_harness.py").read_text()
        cls.generated, _ = transform_live("mac_harness.py", cls.original, cls.paths,
                                          root / "controller", compatibility=True)
        bridge, _ = transform_live("cmux_bridge.py", (SOURCE / "scripts/cmux_bridge.py").read_text(),
                                   cls.paths, root / "controller", compatibility=True)
        cls.parsers = parser_namespace(bridge)

    def setUp(self):
        rows, self.members, self.gate, self.binding = fixture()
        self.row = rows[0]
        self.binding.update(tty="ttys99", process={"pid": 123, "birth": [100, 1]},
                            native_config_root="/private/test/claude", bound_at_epoch=1,
                            payload_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
                            transcript=dict(path="/private/test/native.jsonl", device=1, inode=2, offset=10))
        self.bridge = types.SimpleNamespace(**self.parsers, read_screen=Mock(return_value=screen()),
                    send_text=Mock(), send_key=Mock(), focus_surface=Mock(), submit_text=Mock())
        self.native = types.SimpleNamespace(bind_target=Mock(return_value=self.binding),
                    require_bound=Mock(), _validate_binding=Mock(), _check_file=Mock(),
                    _open=Mock(side_effect=lambda _: io.BytesIO(b"original native data")))

    def observe(self):
        with patch.object(cap, "check_input") as check, patch.object(cap.time, "time", return_value=2):
            proof = cap.observe(self.bridge, self.native, self.gate, self.members, self.members[0], TEXT)
        self.assertEqual(check.call_count, 2)
        return proof

    def assert_zero_input(self):
        for method in ("send_text", "send_key", "focus_surface", "submit_text"):
            getattr(self.bridge, method).assert_not_called()

    def original_attempt(self, proof):
        selected = dict(self.row, handshake_precondition=proof)
        actual = copy.deepcopy(self.binding)
        actual["bound_at_epoch"] = 3
        actual["transcript"]["offset"] = 20
        return selected, actual

    def reconcile(self, selected, actual):
        with patch.dict(sys.modules, {"cmux_bridge": self.bridge}):
            cap.require_original(selected, self.gate, actual, TEXT, self.native)

    def test_active_goal_and_changing_output_allow_empty_composer_without_input(self):
        self.bridge.read_screen.side_effect = [screen(status="✻ Thinking…\n◎ /goal active (2m)"),
                                               screen(status="✻ Working…\n◎ /goal active (3m)")]
        proof = self.observe()
        self.assertEqual(proof["input_operations"], 0)
        self.assertTrue(all(o["active_or_queued"] for o in proof["capability"]["observations"]))
        self.assertNotIn("bridge_clear_confirmed", proof)
        self.reconcile(*self.original_attempt(proof))
        self.assert_zero_input()

    def test_uuid_selector_with_original_ui_proof_observes_and_reconciles(self):
        for uppercase in (False, True):
            rows, self.members, self.gate, binding = uuid_selector_fixture(uppercase)
            self.row = rows[0]
            self.binding.update(binding)
            with self.subTest(uppercase=uppercase):
                proof = self.observe()
                self.assertEqual(proof["executor"], self.members[0]["surface_ref"])
                self.reconcile(*self.original_attempt(proof))
                self.assert_zero_input()

    def test_draft_unknown_partial_or_changed_composer_refuse(self):
        cases = ([screen("user draft")], ["truncated ❯"],
                 [screen(), screen("new draft")], [screen(), screen(" ")],
                 [screen(), screen(footer_suffix=" unknown")],
                 [screen("/goal"), screen()])
        for screens in cases:
            with self.subTest(screens=screens), patch.object(cap, "check_input"):
                self.bridge.read_screen.side_effect = screens
                with self.assertRaisesRegex(ValueError, "DRAFT_OR_UNKNOWN|COMPOSER_CHANGED"):
                    cap.observe(self.bridge, self.native, self.gate, self.members, self.members[0], TEXT)
                self.assert_zero_input()

    def test_wrong_panel_identity_and_stop_refuse_before_screen_or_input(self):
        self.binding["session_id"] = uid(99)
        with self.assertRaisesRegex(ValueError, "SESSION_CHANGED"):
            self.observe()
        self.bridge.read_screen.assert_not_called()
        self.binding["session_id"] = uid(5)
        with patch.object(cap, "check_input", side_effect=ValueError("EXPERIMENT_STOP_REQUESTED")):
            with self.assertRaisesRegex(ValueError, "STOP_REQUESTED"):
                cap.observe(self.bridge, self.native, self.gate, self.members, self.members[0], TEXT)
        self.bridge.read_screen.assert_not_called()
        self.assert_zero_input()

    def test_final_identity_and_stop_recheck_are_required(self):
        for boundary in ("identity", "stop"):
            with self.subTest(boundary=boundary):
                self.native.require_bound.side_effect = ValueError("identity changed") if boundary == "identity" else None
                with patch.object(cap, "check_input", side_effect=[None, ValueError("stop changed")]):
                    with self.assertRaisesRegex(ValueError, "changed"):
                        cap.observe(self.bridge, self.native, self.gate, self.members, self.members[0], TEXT)
                self.assert_zero_input()

    def test_codex_uses_real_prepare_metadata_only_and_never_reads_composer(self):
        for record in (self.binding, self.members[0], self.gate["native_participants"][0]):
            record["provider"] = "codex"
        codex = types.ModuleType("cmux_codex_input")
        codex.__dict__.update(native=self.native, json=json, hashlib=hashlib, Path=Path,
                              uuid=uuid, time=time, TRANSPORT="codex-native-input-v1", DaemonRPC=None)
        source = (SOURCE / "scripts/cmux_codex_input.py").read_text()
        nodes = [definition(source, name) for name in ("_canonical", "_turn_id", "prepare", "validate_request")]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "original-codex-metadata", "exec"), codex.__dict__)
        for active in (True, False):
            client = Mock()
            client.socket_identity = dict(uid=os.geteuid(), peer_uid=os.geteuid(), mode=0o600,
                                          device=1, inode=2, path="/private/test/control.sock")
            client.request.side_effect = [dict(thread=dict(id=uid(5), path=self.binding["transcript"]["path"],
                    canAcceptDirectInput=True, status=dict(type="active" if active else "idle"))),
                    dict(data=[dict(id=uid(7), status="inProgress" if active else "completed")])]
            rpc = Mock()
            rpc.return_value.__enter__ = Mock(return_value=client)
            rpc.return_value.__exit__ = Mock(return_value=False)
            with self.subTest(active=active), patch.object(codex, "DaemonRPC", rpc), patch.dict(
                    sys.modules, {"cmux_codex_input": codex}):
                proof = self.observe()
                self.assertEqual([c.args[0] for c in client.request.call_args_list],
                                 ["thread/read", "thread/turns/list"])
                self.assertEqual(proof["capability"]["request"]["method"], "turn/steer" if active else "turn/start")
                self.reconcile(*self.original_attempt(proof))
        self.bridge.read_screen.assert_not_called()
        self.assert_zero_input()

    def test_original_capability_cannot_be_rebound_or_promoted_to_bridge_clear(self):
        proof = self.observe()
        mutations = [lambda s, b: s.update(bridge_clear_confirmed=True),
                     lambda s, b: s["handshake_precondition"].update(schema="unknown"),
                     lambda s, b: s["handshake_precondition"].update(input_operations=True),
                     lambda s, b: s["handshake_precondition"].update(gate_sha256="0" * 64),
                     lambda s, b: s["handshake_precondition"].update(payload_sha256="0" * 64),
                     lambda s, b: b.update(session_id=uid(99)),
                     lambda s, b: b["process"].update(birth=[101, 1]),
                     lambda s, b: b["transcript"].update(inode=9),
                     lambda s, b: b["transcript"].update(offset=1),
                     lambda s, b: b.update(bound_at_epoch=1),
                     lambda s, b: s["handshake_precondition"]["capability"]["observations"][0].update(screen="forged")]
        for mutation in mutations:
            selected, actual = self.original_attempt(copy.deepcopy(proof))
            mutation(selected, actual)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.reconcile(selected, actual)
        self.assert_zero_input()

    def test_legacy_clear_required_and_unknown_new_proof_never_falls_back(self):
        cap.require_original(dict(bridge_clear_confirmed=True), {}, {}, "", self.native)
        for selected in ({}, {"bridge_clear_confirmed": False},
                         {"bridge_clear_confirmed": True, "handshake_precondition": None}):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                cap.require_original(selected, {}, {}, "", self.native)

    def test_generated_preflight_skips_probe_but_keeps_real_handshake(self):
        calls = []
        names = ("cmd_setup_check", "cmd_helper_parity", "cmd_identity_gate", "cmd_name_surfaces",
                 "cmd_bridge_test", "cmd_handshake", "cmd_validate")
        namespace = {n: lambda args, n=n: calls.append(n) for n in names}
        namespace["print"] = Mock()
        node = definition(self.generated, "cmd_preflight")
        exec(compile(ast.Module(body=[node], type_ignores=[]), "generated-preflight", "exec"), namespace)
        namespace["cmd_preflight"](object())
        self.assertEqual(calls, [n for n in names if n != "cmd_bridge_test"])

    def test_generated_handshake_refuses_existing_or_recovery_before_new_nonce(self):
        node = definition(self.generated, "cmd_handshake")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            namespace = dict(os=os, sys=sys, _artifact_root=lambda _: root, _fail=Mock(), _read=Mock())
            exec(compile(ast.Module(body=[node], type_ignores=[]), "generated-handshake", "exec"), namespace)
            for name in ("handshake-receipt.json", "task-pack.json"):
                (root / name).write_text("original")
                with self.assertRaises(SystemExit):
                    namespace["cmd_handshake"](types.SimpleNamespace(bridge_clear_recovery=None))
                self.assertEqual((root / name).read_text(), "original")
                (root / name).unlink()
            with self.assertRaises(SystemExit):
                namespace["cmd_handshake"](types.SimpleNamespace(bridge_clear_recovery="old"))
            namespace["_read"].assert_not_called()

    def generated_cycle(self, root):
        """Actual generated command, challenge, receipt writer and ACK parser."""
        class Unconfirmed(RuntimeError):
            def __init__(self, text, *, state):
                super().__init__(text)
                self.state = state
        bridge = Mock(DispatchUnconfirmed=Unconfirmed, SUPERVISOR_DID_NOT_SUBMIT="refused",
                      DELIVERY_QUEUED_AT_RECEIVER="queued", DELIVERY_UNVERIFIED_BY_DETECTOR="unknown")
        bridge.read_screen.side_effect = lambda *a, **kw: "⏺ " + json.loads(
            (root / "handshake-receipt.json").read_text())["ack_line_expected"]
        ack = dict(re=re, time=time, read_screen=bridge.read_screen)
        exec(compile(ast.Module(body=[definition((SOURCE / "scripts/cmux_bridge.py").read_text(),
                    "wait_for_ack")], type_ignores=[]), "original-ack-parser", "exec"), ack)
        bridge.wait_for_ack = Mock(side_effect=ack["wait_for_ack"])
        namespace = dict(os=os, sys=sys, cmux=bridge, _artifact_root=lambda _: root,
            _read=Mock(return_value=dict(self.gate, status="PASS")), _recheck_workspace_gate=Mock(),
            secrets=types.SimpleNamespace(token_hex=Mock(side_effect=lambda _: uuid.uuid4().hex[:8])),
            _effective_timeout=lambda *a: 600, PHASE_MINIMUM_BUDGET_SECONDS={"handshake": 600},
            _now=lambda: "2026-10-19T00:00:00Z", _info=Mock(), _ok=Mock(), _fail=Mock(),
            _write=lambda path, value: path.write_text(json.dumps(value)),
            COLLABORATION_SKILL_PATH="/private/skill.md", _gate_executors=lambda _: self.members)
        nodes = [definition(self.generated, name) for name in
                 ("cmd_handshake", "_handshake_one", "_write_handshake_receipt")]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "generated-locked-cycle", "exec"), namespace)
        prompt = types.SimpleNamespace(plan=Mock(return_value={"text": TEXT, "reference": None}),
            persist=Mock(), digest=lambda text: hashlib.sha256(text.encode()).hexdigest())
        observer = Mock(return_value={"schema": cap.SCHEMA, "input_operations": 0})
        modules = {"cmux_prompt_reference": prompt,
                   "mac_upgrade_handshake_capability": types.SimpleNamespace(observe=observer),
                   "cmux_native_delivery": self.native}
        args = types.SimpleNamespace(task_id="original", lines=200, bridge_clear_recovery=None)
        return namespace, bridge, observer, modules, args

    def test_actual_generated_concurrent_calls_have_one_nonce_send_and_original_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            namespace, bridge, _, modules, args = self.generated_cycle(root)
            entered, release = threading.Event(), threading.Event()
            outcomes = []
            def gate(_):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test gate release missing")
            namespace["_recheck_workspace_gate"].side_effect = gate
            original_read = bridge.read_screen.side_effect
            def read_locked(*a, **kw):
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
                return original_read(*a, **kw)
            bridge.read_screen.side_effect = read_locked
            def run():
                try:
                    namespace["cmd_handshake"](args)
                    outcomes.append("PASS")
                except BaseException as exc:
                    outcomes.append(type(exc).__name__)
            with patch.dict(sys.modules, modules):
                first = threading.Thread(target=run)
                first.start()
                try:
                    self.assertTrue(entered.wait(5))
                    second = threading.Thread(target=run)
                    second.start()
                    second.join(2)
                    self.assertFalse(second.is_alive(), "contended invocation must not wait")
                finally:
                    release.set()
                    first.join(5)
                self.assertFalse(first.is_alive())
                self.assertCountEqual(outcomes, ["PASS", "SystemExit"])
                namespace["secrets"].token_hex.assert_called_once()
                bridge.submit_text.assert_called_once()
                bridge.wait_for_ack.assert_called_once()
                raw = (root / "handshake-receipt.json").read_bytes()
                self.assertEqual(json.loads(raw)["status"], "PASS")
                with self.assertRaises(SystemExit):
                    namespace["cmd_handshake"](args)
                self.assertEqual((root / "handshake-receipt.json").read_bytes(), raw)
                bridge.submit_text.assert_called_once()
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)

    def test_generated_lock_rechecks_receipt_after_acquisition_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            namespace, bridge, _, modules, args = self.generated_cycle(root)
            original_flock = fcntl.flock
            def raced_acquire(fd, flags):
                original_flock(fd, flags)
                (root / "handshake-receipt.json").write_text("original-won-before-lock")
            with patch.dict(sys.modules, modules), patch.object(fcntl, "flock", side_effect=raced_acquire):
                with self.assertRaises(SystemExit):
                    namespace["cmd_handshake"](args)
            self.assertEqual((root / "handshake-receipt.json").read_text(), "original-won-before-lock")
            namespace["secrets"].token_hex.assert_not_called()
            bridge.submit_text.assert_not_called()
            alias = root.parent / "alias"
            alias.symlink_to(root, target_is_directory=True)
            namespace["_artifact_root"] = lambda _: alias
            with self.assertRaises(OSError):
                namespace["cmd_handshake"](args)
            namespace["_read"].assert_not_called()

    def test_generated_root_swap_after_gate_or_capability_refuses_before_input(self):
        for boundary in ("gate", "capability"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "root"
                root.mkdir()
                namespace, bridge, observer, modules, args = self.generated_cycle(root)
                old = root.with_name("original-root")
                def swap(*a, **kw):
                    root.rename(old)
                    root.mkdir()
                    return {"schema": cap.SCHEMA, "input_operations": 0}
                target = namespace["_recheck_workspace_gate"] if boundary == "gate" else observer
                target.side_effect = swap
                with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, "ROOT_CHANGED"):
                    namespace["cmd_handshake"](args)
                bridge.submit_text.assert_not_called()
                self.assertFalse((root / "handshake-receipt.json").exists())
                self.assertEqual((old / "handshake-receipt.json").exists(), boundary == "capability")
                fd = os.open(old, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)

    def test_generated_uncertain_timeout_and_exception_keep_attempt_and_release_lock(self):
        for outcome in ("queued", "timeout", "exception"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                namespace, bridge, _, modules, args = self.generated_cycle(root)
                if outcome == "queued":
                    bridge.submit_text.side_effect = bridge.DispatchUnconfirmed("queued", state="queued")
                elif outcome == "timeout":
                    bridge.wait_for_ack.side_effect = TimeoutError("original timeout")
                else:
                    bridge.submit_text.side_effect = RuntimeError("original transport exception")
                with patch.dict(sys.modules, modules):
                    if outcome == "queued":
                        namespace["cmd_handshake"](args)
                    else:
                        with self.assertRaises(SystemExit if outcome == "timeout" else RuntimeError):
                            namespace["cmd_handshake"](args)
                    raw = (root / "handshake-receipt.json").read_bytes()
                    if outcome == "queued":
                        self.assertEqual(json.loads(raw)["submission_state"], "queued")
                    with self.assertRaises(SystemExit):
                        namespace["cmd_handshake"](args)
                    self.assertEqual((root / "handshake-receipt.json").read_bytes(), raw)
                bridge.submit_text.assert_called_once()
                namespace["secrets"].token_hex.assert_called_once()
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)

    def test_generated_challenge_sends_once_and_keeps_original_ack_and_uncertain_path(self):
        node = definition(self.generated, "_handshake_one")
        class Unconfirmed(RuntimeError):
            def __init__(self, text, *, state):
                super().__init__(text)
                self.state = state
        for state in (None, "queued", "refused", "capability-refused"):
            receipts = []
            bridge = Mock(DispatchUnconfirmed=Unconfirmed, SUPERVISOR_DID_NOT_SUBMIT="refused",
                          DELIVERY_QUEUED_AT_RECEIVER="queued", DELIVERY_UNVERIFIED_BY_DETECTOR="unknown")
            if state in ("queued", "refused"):
                bridge.submit_text.side_effect = Unconfirmed(state, state=state)
            bridge.wait_for_ack.side_effect = lambda *a, **kw: receipts[0]["ack_line_expected"]
            prompt = types.SimpleNamespace(plan=Mock(return_value={"text": TEXT, "reference": None}),
                                           persist=Mock(), digest=lambda text: hashlib.sha256(text.encode()).hexdigest())
            observer = Mock(return_value={"schema": cap.SCHEMA, "input_operations": 0})
            if state == "capability-refused":
                observer.side_effect = ValueError("draft")
            namespace = dict(cmux=bridge, secrets=types.SimpleNamespace(token_hex=Mock(return_value="1234abcd")),
                _effective_timeout=lambda *a: 600, PHASE_MINIMUM_BUDGET_SECONDS={"handshake": 600},
                _now=lambda: "2026-10-19T00:00:00Z", _write_handshake_receipt=Mock(),
                _info=Mock(), _ok=Mock(), _fail=Mock(), COLLABORATION_SKILL_PATH="/private/skill.md",
                _gate_executors=lambda gate: self.members)
            exec(compile(ast.Module(body=[node], type_ignores=[]), "generated-cycle", "exec"), namespace)
            with self.subTest(state=state), patch.dict(sys.modules, {"cmux_prompt_reference": prompt,
                    "mac_upgrade_handshake_capability": types.SimpleNamespace(observe=observer),
                    "cmux_native_delivery": self.native}):
                ok = namespace["_handshake_one"](types.SimpleNamespace(task_id="original", lines=200),
                            Path("/offline"), self.gate, self.members[0], receipts, root_recheck=lambda: None)
                self.assertEqual(ok, state in (None, "queued"))
                self.assertEqual(bridge.submit_text.call_count, 0 if state == "capability-refused" else 1)
                self.assertEqual(bridge.wait_for_ack.call_count, 1 if ok else 0)
                self.assertNotIn("bridge_clear_confirmed", receipts[0])
                self.assertNotIn("bridge_clear_recovery", receipts[0])
                namespace["secrets"].token_hex.assert_called_once()

    def test_source_shape_drift_and_reapplication_refuse(self):
        patched = readonly_handshake("mac_harness.py", self.original)
        duplicate = ast.get_source_segment(self.original, definition(self.original, "_handshake_one"))
        duplicate_command = ast.get_source_segment(self.original, definition(self.original, "cmd_handshake"))
        for source in (patched, self.original.replace("    cmd_bridge_test(args)\n", ""),
                       self.original + "\n\n" + duplicate,
                       self.original + "\n\n" + duplicate_command,
                       self.original.replace("def cmd_handshake(", "def unknown_command("),
                       self.original.replace("def _handshake_one(", "def unknown_handshake("),
                       self.original.replace('        "bridge_clear_confirmed": True,', "")):
            with self.subTest(length=len(source)), self.assertRaises((ContractError, ValueError)):
                readonly_handshake("mac_harness.py", source)
        reconcile = (SOURCE / "scripts/reconcile_handshake_ack.py").read_text()
        generated, changes = transform_live("reconcile_handshake_ack.py", reconcile, self.paths,
                                            Path("/offline/controller"), compatibility=True)
        self.assertIn("from mac_upgrade_handshake_capability import require_original", generated)
        self.assertIn("require_original(selected, gate, binding, text, native)", generated)
        self.assertIn("original_readonly_handshake_capability_reconciliation", changes)


if __name__ == "__main__":
    unittest.main()
