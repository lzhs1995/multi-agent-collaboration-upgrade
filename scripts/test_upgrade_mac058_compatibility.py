"""Portable behavioral tests over SHA-pinned, unchanged MAC 0.4.58 sources.

Fixtures are licensed source inputs, not an installable or live controller.
External transport/kernel observations are mocked; original transformed entry
points, input-intent ordering, file pins and partial-panel checks execute.
"""
from __future__ import annotations

import ast
import copy
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import cmux_evidence_io as evidence
from orchestration_common import ContractError
from test_upgrade_bridge_probe_evidence import parser_namespace
from test_upgrade_identity import fixture, uid
from upgrade_callback_wait_probe import run as callback_probe
from upgrade_controller_patch import patch_ack, relocate
from upgrade_handshake_identity import bound_rows
from upgrade_live_patch import (claude_measured_chrome, live_gates, native_readers,
                               transform_live)

FIXTURES = Path(__file__).with_name("fixtures") / "mac-0.4.58"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
BASE058 = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.58-cf3f6175840b/source"


def source(name):
    raw = (FIXTURES / name).read_bytes()
    pin = MANIFEST["files"][name]
    if len(raw) != pin["bytes"] or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        raise AssertionError("upstream fixture changed: " + name)
    return raw.decode("utf-8")


def definitions(text, names, namespace):
    nodes = [node for node in ast.parse(text).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError("required real entrypoint missing")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "pinned-upstream-entrypoints", "exec"), namespace)
    return namespace


def panel_namespace():
    module = types.ModuleType("offline_pinned_handshake_panel")
    exec(compile(source("handshake_panel.py"), str(FIXTURES / "handshake_panel.py"), "exec"), module.__dict__)
    return module


def panel_fixture():
    rows, members, gate, binding = fixture()
    second = dict(members[0], surface_ref="surface:46", surface_uuid=uid(7), ordinal=2)
    members.append(second)
    gate["workspace_proofs"][second["surface_ref"]] = dict(
        gate["workspace_proofs"]["surface:45"], target_surface_uuid=uid(7), target_surface_ref="surface:46")
    gate["native_participants"].append(dict(role="executor", surface_uuid=uid(7),
        native_session_id=uid(8), provider="claude"))
    gate["executors"] = members
    nonce = "1234abcd"
    expected = f"PREFLIGHT_ACK|original|claude:identity|READY|INLINE|{nonce}"
    rows[0].update(ack_nonce=nonce, ack_line_expected=expected, executor_ack=False,
        status="FAIL", lifecycle="EXPIRED", terminal_error_at="2026-10-20T00:00:00Z")
    return rows, members, gate, binding


class UpstreamTransformTests(unittest.TestCase):
    def test_all_pinned_sources_transform_without_modifying_fixtures(self):
        paths = {name: Path("/offline/mac058") / name
                 for name in ("state", "controllers", "tmp", "config", "bin")}
        for name in MANIFEST["files"]:
            with self.subTest(source=name):
                before = source(name)
                transformed, changes = transform_live(name, before, paths,
                    Path("/offline/mac058/controller"), compatibility=True)
                compile(transformed, name, "exec", dont_inherit=True)
                self.assertIn("live_v2_kernel_boundary", changes)
                self.assertEqual(source(name), before)

    def test_lane_identity_reads_never_exec_setuid_ps_after_transform(self):
        # u13: both Claude inquiry starts failed with EPERM at /bin/ps (setuid,
        # denied by the lane sandbox) before registration. Every transformed
        # tty read on a lane/supervisor path must use the kernel reader.
        paths = {name: Path("/offline/mac058") / name
                 for name in ("state", "controllers", "tmp", "config", "bin")}
        continuity = json.loads((FIXTURES / "continuity-manifest.json").read_text())
        pinned = dict(MANIFEST["files"], **continuity["files"])
        scanned = []
        for name, pin in sorted(pinned.items()):
            raw = (FIXTURES / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (pin["bytes"], pin["sha256"]), name)
            if not name.endswith(".py"):
                continue
            text, _ = transform_live(name, raw.decode(), paths, Path("/offline/mac058/controller"),
                                     compatibility=True)
            self.assertEqual([n.lineno for n in ast.walk(ast.parse(text))
                              if isinstance(n, ast.Constant) and n.value == "/bin/ps"], [], name)
            scanned.append(name)
        # Both inquiry entrypoints come from the continuity fixture set.
        self.assertLessEqual({"executor_reask_identity.py", "executor_reply_advisory.py",
                              "reconcile_handshake_ack.py", "cmux_native_delivery.py"}, set(scanned))
        for name, subject in (("executor_reask_identity.py", "process"), ("executor_reply_advisory.py", "process"),
                              ("reconcile_handshake_ack.py", "client")):
            text, _ = transform_live(name, (FIXTURES / name).read_text(), paths,
                                     Path("/offline/mac058/controller"), compatibility=True)
            self.assertEqual(text.count("tty = private_process.terminal(" + subject + ")"), 1, name)
            self.assertIn("import mac_upgrade_process_inventory as private_process", text, name)

    @unittest.skipUnless(BASE058.is_dir(), "requires the complete fixed MAC 0.4.58 source")
    def test_complete_base_keeps_only_off_path_setuid_ps_reads(self):
        paths = {name: Path("/offline/mac058") / name
                 for name in ("state", "controllers", "tmp", "config", "bin")}
        residual = {}
        for path in sorted((BASE058 / "scripts").glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text, _ = transform_live(path.name, path.read_text(), paths, Path("/offline/mac058/controller"),
                                     compatibility=True)
            hits = sum(isinstance(n, ast.Constant) and n.value == "/bin/ps" for n in ast.walk(ast.parse(text)))
            if hits:
                residual[path.name] = hits
        # The refused successor route, the interrupted-Bash child listing and
        # offline test support; none is on the inquiry or handshake path.
        self.assertEqual(residual, {"cmux_claude_interruption.py": 1, "successor_native_delivery.py": 1,
                                    "native_test_support.py": 2})

    def test_modern_chrome_changes_only_measured_composer_not_transport_or_activity(self):
        before = source("cmux_bridge.py")
        after = claude_measured_chrome(before)
        old, new = ast.parse(before), ast.parse(after)
        changed = [a.name for a, b in zip(old.body, new.body) if ast.dump(a) != ast.dump(b)]
        self.assertEqual(changed, ["_claude_bordered_compose"])
        for name in ("_queued_or_active_input", "_submit_text_once", "_submit_terminal_once", "_observe_native"):
            snippets = [ast.get_source_segment(text, next(node for node in ast.parse(text).body
                if getattr(node, "name", None) == name)) for text in (before, after)]
            self.assertEqual(snippets[0], snippets[1], name)
        with self.assertRaises(ContractError):
            claude_measured_chrome(after)

    def test_measured_activity_never_hides_exact_draft_or_unknown_footer(self):
        parser = parser_namespace(claude_measured_chrome(source("cmux_bridge.py")))
        fixtures = Path(__file__).resolve().parent.parent / "verification/fixtures"
        for lane in ("a", "b"):
            original = (fixtures / f"claude-default-{lane}-20261018.txt").read_text()
            block = parser["_claude_bordered_compose"](original)
            self.assertEqual(block["body"], "")
            for draft in ("用户原稿  \t", "◎ /goal active (13m)", "  保留空格  "):
                rows = original.splitlines()
                rows[block["start"]] = "❯ " + draft
                changed = "\n".join(rows) + "\n"
                self.assertEqual(parser["_claude_bordered_compose"](changed)["body"], draft)
                self.assertFalse(parser["compose_block_is_empty"](changed))
            for text in ("✢ Brewing…", "✻ Compacting conversation…", "· Thinking..."):
                rows = original.splitlines()
                rows[block["start"] - 1:block["start"] - 1] = [text, "  ⎿  Tip: keep working", "◎ /goal active (59m)"]
                self.assertTrue(parser["_queued_or_active_input"]("\n".join(rows)))
            unknown = original.replace("⏵⏵ bypass permissions on", "unknown permissions")
            self.assertIsNone(parser["_claude_bordered_compose"](unknown))

    def test_busy_goal_hud_above_border_is_a_complete_empty_frame_only_in_its_exact_pair(self):
        # Original u12 handshake screens (both lanes refused 2026-10-26, zero input).
        pins = {"a": "9fa226394733797aad797e4d3afbe4a80c2d6f1693a0b7b1c5e8507a4cd2b2e7",
                "b": "65d64cf376cbd4374a1c60c9c7ba2d5c2fd59c28cfa3459e98ce269f1221bd2c"}
        base = parser_namespace(source("cmux_bridge.py"))
        parser = parser_namespace(claude_measured_chrome(source("cmux_bridge.py")))
        fixtures = Path(__file__).resolve().parent.parent / "verification/fixtures"
        bare = "⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt"
        for lane, pin in pins.items():
            raw = (fixtures / f"claude-busy-goal-split-{lane}-20261026.txt").read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), pin)
            original = raw.decode("utf-8")
            self.assertIsNone(base["_claude_bordered_compose"](original))
            block = parser["_claude_bordered_compose"](original)
            self.assertEqual((block["body"], block["footer"]), ("", [bare]))
            self.assertTrue(parser["compose_block_is_empty"](original))
            self.assertTrue(parser["_queued_or_active_input"](original))
            rows = original.splitlines()
            start = block["start"]
            self.assertRegex(rows[start - 2], r"^\s*◎ /goal active \(\d+m\)\s*$")
            changed = list(rows)
            changed[start] = "❯ 用户原稿  \t"
            self.assertEqual(parser["_claude_bordered_compose"]("\n".join(changed))["body"], "用户原稿  \t")
            self.assertFalse(parser["compose_block_is_empty"]("\n".join(changed)))
            mutants = {
                "no_goal_row": rows[:start - 2] + rows[start - 1:],
                "blank_between": rows[:start - 1] + [""] + rows[start - 1:],
                "goal_paused": rows[:start - 2] + [rows[start - 2].replace("active", "paused")] + rows[start - 1:],
                "goal_suffix": rows[:start - 2] + [rows[start - 2].rstrip() + " x"] + rows[start - 1:],
                "footer_suffix": [row + " x" if row.strip() == bare else row for row in rows],
                "extra_footer_row": rows + ["  unknown chrome"],
            }
            for name, value in mutants.items():
                with self.subTest(lane=lane, mutant=name):
                    self.assertIsNone(parser["_claude_bordered_compose"]("\n".join(value)))

    def test_private_identity_keeps_managed_process_reader_and_source_proof(self):
        before = source("cmux_daemon_identity.py")
        after = native_readers("cmux_daemon_identity.py", before)
        for name in ("process", "_ancestry"):
            snippets = [ast.get_source_segment(text, next(node for node in ast.parse(text).body
                if getattr(node, "name", None) == name)) for text in (before, after)]
            self.assertEqual(snippets[0], snippets[1])
        self.assertNotIn("/bin/ps", after)
        standalone = Mock(return_value=dict(source="private-standalone-native-client-v1", daemon=None))
        namespace = dict(private_standalone=types.SimpleNamespace(collect=standalone),
            sys=types.SimpleNamespace(platform="darwin"), _ancestry=lambda: ([], None))
        definitions(after, {"collect"}, namespace)
        env = {"CMUX_SURFACE_ID": uid(3), "CODEX_THREAD_ID": uid(5)}
        self.assertEqual(namespace["collect"](env), standalone.return_value)
        standalone.assert_called_once_with(env, [])

    def test_unknown_native_rpc_import_shape_is_refused_before_execution(self):
        before = source("cmux_codex_input.py")
        anchor = "from cmux_codex_rpc import DaemonRPC, RPCError"
        for bad in (before.replace(anchor, anchor + ", UnknownRPC"),
                    before.replace(anchor, anchor + "\n" + anchor),
                    before.replace(anchor, "from cmux_codex_rpc import DaemonRPC as changed")):
            with self.subTest(shape=bad[:80]), self.assertRaises(ContractError):
                live_gates("cmux_codex_input.py", bad)

    def test_private_native_input_rejects_second_patch_at_both_entrypoints(self):
        paths = {name: Path("/offline/mac058") / name
                 for name in ("state", "controllers", "tmp", "config", "bin")}
        controller = Path("/offline/mac058/controller")
        before = source("cmux_codex_input.py")
        for raw in (before, before + "\n# Source semantics are unchanged.\n"):
            with self.subTest(entrypoint="live_gates"):
                patched = live_gates("cmux_codex_input.py", raw)
                compile(patched, "cmux_codex_input.py", "exec", dont_inherit=True)
                with self.assertRaisesRegex(ContractError, "already patched"):
                    live_gates("cmux_codex_input.py", patched)
            with self.subTest(entrypoint="transform_live"):
                patched, _ = transform_live("cmux_codex_input.py", raw, paths, controller,
                                             compatibility=True)
                compile(patched, "cmux_codex_input.py", "exec", dont_inherit=True)
                with self.assertRaisesRegex(ContractError, "already patched"):
                    transform_live("cmux_codex_input.py", patched, paths, controller,
                                   compatibility=True)
        self.assertEqual(source("cmux_codex_input.py"), before)


class ModernInputTests(unittest.TestCase):
    def exercise(self, *, active=False, failure=None, reject_gate_at=None, no_method=False, mutate_import=False):
        calls, events, gates = [], [], []
        sid, turn = uid(101), uid(102)
        text = "exact  中文 input"
        binding = dict(provider="codex", session_id=sid, transcript={"path": "/offline/original.jsonl"}, identity={})
        class RPCError(RuntimeError):
            method = "turn/start"
            error = {"code": -1}
        class RPC:
            socket_identity = dict(path="/offline/c.sock", uid=os.geteuid(), peer_uid=os.geteuid(), mode=0o600, device=1, inode=2)
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def request(self, method, params):
                calls.append((method, params))
                if method == "thread/read":
                    return {"thread": dict(id=sid, path=binding["transcript"]["path"],
                        canAcceptDirectInput=not no_method, status={"type": "active" if active else "idle"})}
                if method == "thread/turns/list":
                    return {"data": [dict(id=turn, status="inProgress" if active else "completed")]}
                if not events or events[-1][0] != "NATIVE_INPUT_INTENT":
                    raise AssertionError("original durable intent must precede mutation")
                if failure is not None:
                    raise failure
                return {"turnId": turn} if active else {"turn": {"id": turn}}
        def check(*args, **kwargs):
            gates.append((args, kwargs))
            if len(gates) == reject_gate_at:
                self.assertEqual([phase for phase, _ in events], ["NATIVE_INPUT_INTENT"] if reject_gate_at == 2 else [])
                self.assertFalse([method for method, _ in calls if method.startswith("turn/")])
                fallback.assert_not_called()
                raise ValueError("EXPERIMENT_STOP_REQUESTED")
        native = types.SimpleNamespace(NativeDeliveryError=RuntimeError, _validate_binding=Mock())
        confirmation = types.ModuleType("cmux_confirmation")
        exec(compile(source("cmux_confirmation.py"), str(FIXTURES / "cmux_confirmation.py"), "exec"), confirmation.__dict__)
        rpc = types.SimpleNamespace(DaemonRPC=RPC, RPCError=RPCError)
        gate = types.SimpleNamespace(check_input=check)
        namespace = types.ModuleType("pinned_modern_native_input")
        fallback = Mock(return_value={"confirmed": False, "offline_fallback": True})
        changed = live_gates("cmux_codex_input.py", source("cmux_codex_input.py"))
        if mutate_import:
            good = "from cmux_codex_rpc import DaemonRPC, RPCError\nfrom upgrade_input_gate import check_input"
            bad = "from cmux_codex_rpc import DaemonRPC\nfrom upgrade_input_gate import check_input, RPCError"
            self.assertEqual(changed.count(good), 1)
            changed = changed.replace(good, bad)
        with patch.dict(sys.modules, {"cmux_native_delivery": native, "cmux_codex_rpc": rpc,
                "upgrade_input_gate": gate, "cmux_confirmation": confirmation}):
            exec(compile(changed, "pinned_modern_native_input", "exec"), namespace.__dict__)
            self.assertIs(namespace.RPCError, RPCError)
            result = namespace.send_once(binding, text, recheck=Mock(),
                emit=lambda phase, **kwargs: events.append((phase, kwargs)),
                observe=lambda: {"confirmed": False}, wait_seconds=0, fallback=fallback)
        return result, calls, events, gates, fallback, text, binding, turn

    def test_rpc_error_import_is_preserved_and_native_input_is_exactly_once(self):
        for active in (False, True):
            for failure in (None, TimeoutError("RPC outcome unknown"), OSError(errno.ECONNREFUSED, "after intent")):
                with self.subTest(active=active, failure=failure):
                    result, calls, events, gates, fallback, text, binding, turn = self.exercise(active=active, failure=failure)
                    mutations = [(method, params) for method, params in calls if method.startswith("turn/")]
                    expected = dict(threadId=binding["session_id"], input=[dict(type="text", text=text)])
                    if active:
                        expected["expectedTurnId"] = turn
                    self.assertEqual(mutations, [("turn/steer" if active else "turn/start", expected)])
                    self.assertEqual([phase for phase, _ in events], ["NATIVE_INPUT_INTENT", "NATIVE_INPUT_RESULT"])
                    self.assertEqual(len(gates), 2)
                    self.assertEqual(result["native_rpc_outcome"], "RPC_ACCEPTED" if failure is None else "RPC_ERROR_OR_UNKNOWN")
                    self.assertEqual((result["retries"], result["input_operations"], result["terminal_input_operations"]), (0, 1, 0))
                    self.assertFalse(result["confirmed"])
                    fallback.assert_not_called()

    def test_no_method_before_intent_allows_only_the_original_fallback(self):
        result, calls, events, gates, fallback, *_ = self.exercise(no_method=True)
        self.assertTrue(result["offline_fallback"])
        self.assertFalse(events)
        self.assertFalse(gates)
        self.assertEqual([method for method, _ in calls], ["thread/read", "thread/turns/list"])
        fallback.assert_called_once_with()

    def test_post_intent_private_stop_never_falls_back_or_retries(self):
        with self.assertRaisesRegex(ValueError, "EXPERIMENT_STOP_REQUESTED"):
            self.exercise(reject_gate_at=2)

    def test_previous_prefix_import_mutation_is_detected_by_real_module_import(self):
        with self.assertRaises(ImportError):
            self.exercise(mutate_import=True)


class PartialPanelTests(unittest.TestCase):
    def validate(self, rows, members, gate, binding, *, altered=False):
        namespace = dict(re=re, panel=panel_namespace(), bound_rows=bound_rows)
        text = patch_ack("reconcile_handshake_ack.py", source("reconcile_handshake_ack.py"))
        definitions(text, {"aggregate_rows", "validate_panel", "is_stranded_draft_row", "is_direct_pending_row"}, namespace)
        harness = types.SimpleNamespace(_gate_executors=lambda g: g["executors"])
        native = types.SimpleNamespace(_timestamp=lambda stamp: 1 if stamp else None)
        receipt = namespace["aggregate_rows"](rows, members)
        if altered:
            receipt["all_executors_acked"] = True
        return namespace["validate_panel"](harness, native, receipt, gate, binding), receipt

    def test_sent_prefix_preserves_unsent_member_and_never_credits_it(self):
        args = panel_fixture()
        before = copy.deepcopy(args)
        (rows, selected), receipt = self.validate(*args)
        self.assertIs(selected, args[0][0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(receipt["unstarted_executors"], ["surface:46"])
        self.assertFalse(receipt["all_executors_acked"])
        self.assertEqual(args, before)

    def test_unsent_target_changed_aggregate_and_missing_original_proof_refuse(self):
        for mode in ("target", "aggregate", "proof", "nonce"):
            args = panel_fixture()
            if mode == "target":
                args[3]["identity"]["target_surface_uuid"] = uid(7)
            elif mode == "proof":
                args[2]["workspace_proofs"].pop("surface:46")
            elif mode == "nonce":
                args[0][0]["ack_nonce"] = "unknown"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.validate(*args, altered=mode == "aggregate")


class OriginalGateObservationTests(unittest.TestCase):
    def observe_fixture(self, root, *, change_gate=False, excise_pin=False):
        rows, members, gate, binding = panel_fixture()
        controller = root / "controller"
        receipt_path = controller / "task/receipt.json"
        receipt_path.parent.mkdir(parents=True)
        gate_path = receipt_path.parent / "identity-gate.json"
        text_path = receipt_path.parent / "dispatch.txt"
        text = "STATUS: original 1234abcd"
        sha = hashlib.sha256(text.encode()).hexdigest()
        rows[0].update(dispatch_payload_sha256=sha, dispatch_body_reference=None)
        binding["payload_sha256"] = sha
        key = hashlib.sha256(json.dumps([binding["identity"]["caller_surface_uuid"], "1234abcd"], sort_keys=True).encode()).hexdigest()
        attempt_path = root / ".local/state/multi-agent-collaboration/message-dispatch-v1" / key / "attempt-0001.json"
        attempt_path.parent.mkdir(parents=True)
        attempt = dict(native_binding=binding, binding=dict(identity=binding["identity"], marker="1234abcd", payload_sha256=sha))
        receipt_path.write_text(json.dumps(panel_namespace().aggregate(rows, members)))
        gate_path.write_text(json.dumps(gate))
        attempt_path.write_text(json.dumps(attempt))
        text_path.write_text(text)
        originals = {p: p.read_bytes() for p in (receipt_path, attempt_path, text_path, gate_path)}
        def wait(*args, **kwargs):
            if change_gate:
                gate_path.write_bytes(originals[gate_path] + b" ")
            raise TimeoutError("ACK not visible")
        harness = types.SimpleNamespace(__file__=str(controller / "scripts/mac_harness.py"),
            _gate_executors=lambda g: g["executors"])
        native = types.SimpleNamespace(MAX_SCAN=8192, require_bound=Mock(),
            _original_intent=lambda *args: (1, {"offset": 10}), probe=Mock(return_value={"confirmed": False}),
            validate_proof=Mock())
        journal = types.SimpleNamespace(original_body_pin=Mock(return_value=None),
            prompt_reference=types.SimpleNamespace(wire_reference=lambda text: None))
        args = types.SimpleNamespace(controller=str(controller), receipt=str(receipt_path),
            receipt_sha256=hashlib.sha256(originals[receipt_path]).hexdigest(), attempt=str(attempt_path),
            attempt_sha256=hashlib.sha256(originals[attempt_path]).hexdigest(), text_file=str(text_path),
            gate=str(gate_path), gate_sha256=hashlib.sha256(originals[gate_path]).hexdigest(), timeout=1, lines=200)
        text = patch_ack("observe_handshake_ack.py", source("observe_handshake_ack.py"))
        if excise_pin:
            anchor = "    gate_pin, gate_path = pins[3], paths[3]\n"
            self.assertEqual(text.count(anchor), 1)
            text = text.replace(anchor, "")
        namespace = dict(Path=Path, hashlib=hashlib, json=json, re=re, time=time,
            importlib=importlib, bound_rows=bound_rows)
        definitions(text, {"digest", "observe", "exclude_generated_receipt"}, namespace)
        with patch.dict(sys.modules, {"mac_harness": harness}), patch.object(Path, "home", return_value=root):
            result = namespace["observe"](args, modules=(types.SimpleNamespace(wait_for_ack=wait), native, evidence, journal))
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)
        self.assertEqual(native.require_bound.call_count, 2)
        native.validate_proof.assert_not_called()
        return result

    def test_actual_partial_observer_rechecks_original_gate_without_new_input_or_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.observe_fixture(Path(folder).resolve())
        self.assertEqual((result["input_operations"], result["journal_writes"]), (0, 0))
        self.assertEqual(result["ack"]["state"], "ACK_NOT_VISIBLE")
        self.assertEqual(result["business_acceptance"], "NOT_EVALUATED")
        self.assertEqual(len(result["original_gate_sha256"]), 64)

    def test_original_gate_changed_during_observation_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, "EVIDENCE_CHANGED_DURING_OBSERVATION"):
            self.observe_fixture(Path(folder).resolve(), change_gate=True)

    def test_gate_pin_excision_counterexample_fails_in_actual_observer(self):
        with tempfile.TemporaryDirectory() as folder, \
                self.assertRaisesRegex(NameError, "^name 'gate_pin' is not defined$") as caught:
            self.observe_fixture(Path(folder).resolve(), excise_pin=True)
        self.assertEqual(caught.exception.name, "gate_pin")


class ModernCallbackTests(unittest.TestCase):
    def test_original_deliver_wait_rechecks_all_fresh_gates_and_never_waits_for_recovery(self):
        native = types.SimpleNamespace(bind_target=Mock(return_value={}),
            IDENTITY_KEYS=("workspace_uuid", "caller_surface_uuid", "target_surface_uuid", "target_pane_uuid"))
        definitions(source("cmux_native_delivery.py"), {"marker_binding"},
            namespace := {"NativeDeliveryError": RuntimeError})
        native.marker_binding = namespace["marker_binding"]
        confirmation = types.ModuleType("cmux_confirmation")
        exec(compile(source("cmux_confirmation.py"), str(FIXTURES / "cmux_confirmation.py"), "exec"), confirmation.__dict__)
        journal = types.ModuleType("offline_modern_callback")
        journal.__file__ = str(FIXTURES / "cmux_callback_journal.py")
        text = live_gates("cmux_callback_journal.py", source("cmux_callback_journal.py"))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            paths = {name: root / name for name in ("state", "controllers", "tmp", "config", "bin")}
            # Use the actual private path transform too: no production state
            # path may be touched by a portable offline test.
            text = relocate(text, paths, root / "controller")
            with patch.dict(sys.modules, {"cmux_native_delivery": native, "cmux_confirmation": confirmation}):
                exec(compile(text, journal.__file__, "exec"), journal.__dict__)
                result = callback_probe(journal, root / "cases", paths["state"] / "deliveries-v1")
        self.assertEqual(len(result["checks"]), 17)
        self.assertIn("recover_stranded_retains_fail_fast", result["checks"])
        self.assertEqual((result["model_requests"], result["native_inputs"]), (0, 0))
        self.assertEqual(result["simulated_transport_calls"], 1)


if __name__ == "__main__":
    unittest.main()
