"""Observed standalone parent shape and adversarial identity boundaries."""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import cmux_daemon_identity as native
import upgrade_process_inventory as inventory
from upgrade_standalone_identity import collect
from upgrade_live_patch import standalone_identity
from orchestration_common import ContractError

SESSION, SURFACE, WORKSPACE, CHILD = [str(UUID(int=n)) for n in range(1, 5)]


class StandaloneIdentityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.scope = dict(CMUX_SURFACE_ID=SURFACE, CMUX_WORKSPACE_ID=WORKSPACE)
        self.env = dict(self.scope, CODEX_THREAD_ID=SESSION)
        def proc(pid, ppid, argv, env):
            return dict(pid=pid, ppid=ppid, birth=[1000, pid], executable=argv[0],
                        argv=argv, env=dict(env), file_identity=[1, pid, 50, 1])
        self.client = proc(44992, 43579, ["/native/codex", "--disable", "computer_use"], self.scope)
        self.server = proc(45091, 44992, ["/native/codex", "app-server", "--listen"], self.scope)
        self.wrapper = proc(64197, 45091, ["/native/rtk"], self.env)
        self.shell = proc(43579, 43573, ["/bin/zsh"], self.scope)
        self.chain = [self.wrapper, self.server, self.client, self.shell,
                      dict(pid=43573, ppid=1, system_login=True)]
        self.current = proc(os.getpid(), 64197, ["/native/python"], self.env)
        self.process = self.stack.enter_context(patch.object(native, "process",
            side_effect=lambda pid: copy.deepcopy(next(p for p in [self.current] + self.chain if p["pid"] == pid))))
        self.parents = self.stack.enter_context(patch.object(native, "_ancestry",
            side_effect=lambda: (copy.deepcopy(self.chain), None)))
        self.stack.enter_context(patch("upgrade_standalone_identity.os.getppid", return_value=64197))
        self.tty = self.stack.enter_context(patch.object(inventory, "terminal", return_value="ttys88"))
        self.selection = dict(thread_id=SESSION, client_epoch=CHILD, signature=(1, 2, 3))
        self.foreground = Mock(return_value=self.selection)
        self.stack.enter_context(patch.dict(sys.modules,
            cmux_foreground_thread=SimpleNamespace(read=self.foreground)))

    def test_real_parent_shape_accepts_tool_and_preserves_inputs(self):
        before = copy.deepcopy((self.env, self.chain))
        proof = collect(self.env, self.chain)
        self.assertEqual(proof["client"]["pid"], 44992)
        self.assertEqual(proof["foreground"]["thread_id"], SESSION)
        self.assertIsNone(proof["daemon"])
        self.assertEqual((self.env, self.chain), before)
        self.assertEqual(self.foreground.call_count, 2)
        self.tty.assert_called_once_with(self.client)

    def test_direct_tool_without_incidental_wrapper_accepts(self):
        self.chain.pop(0)
        self.current["ppid"] = self.server["pid"]
        with patch("upgrade_standalone_identity.os.getppid", return_value=self.server["pid"]):
            self.assertEqual(collect(self.env, self.chain)["session"], SESSION)

    def test_native_hook_uses_payload_and_foreground_without_inherited_selector(self):
        self.current["env"].pop("CODEX_THREAD_ID")
        self.wrapper["env"].pop("CODEX_THREAD_ID")
        for event in ("PreToolUse", "PostToolUse", "Stop", "SessionStart", "UserPromptSubmit"):
            with self.subTest(event=event):
                proof = collect(self.scope, self.chain, payload=dict(session_id=SESSION, hook_event_name=event))
                self.assertEqual(proof["session"], SESSION)

    def test_non_codex_ancestry_returns_no_proof(self):
        for p in (self.client, self.server):
            p["executable"] = "/native/other"
        self.assertIsNone(collect(self.env, self.chain))
        self.process.assert_not_called()

    def test_two_native_clients_are_ambiguous(self):
        self.chain.append(dict(self.client, pid=12345))
        with self.assertRaisesRegex(native.IdentityError, "ambiguous"):
            collect(self.env, self.chain)

    def test_supplied_selector_cannot_override_current_kernel(self):
        with self.assertRaisesRegex(native.IdentityError, "kernel thread"):
            collect(dict(self.env, CODEX_THREAD_ID=CHILD), self.chain)

    def test_missing_or_invalid_selector_refused(self):
        for value in (None, "", "invalid", 3):
            with self.subTest(value=value), self.assertRaises(native.IdentityError):
                collect(dict(self.env, CODEX_THREAD_ID=value), self.chain)

    def test_environment_and_each_kernel_scope_must_match(self):
        for key in self.scope:
            with self.subTest(key=key, source="argument"), self.assertRaises(native.IdentityError):
                collect(dict(self.env, **{key: CHILD}), self.chain)
            for p in (self.current, self.wrapper, self.server):
                original = p["env"][key]
                try:
                    p["env"][key] = CHILD
                    with self.subTest(key=key, pid=p["pid"]), self.assertRaisesRegex(native.IdentityError, "kernel scope"):
                        collect(self.env, self.chain)
                finally:
                    p["env"][key] = original

    def test_matching_but_malformed_scope_is_not_authentication(self):
        for key in self.scope:
            originals = copy.deepcopy((self.env, self.current, self.chain))
            try:
                self.env[key] = "invalid"
                for p in [self.current] + self.chain:
                    if "env" in p:
                        p["env"][key] = "invalid"
                with self.assertRaisesRegex(native.IdentityError, "valid scope UUID"):
                    collect(self.env, self.chain)
            finally:
                self.env, self.current, self.chain = originals
                self.client = self.chain[2]

    def test_wrong_parent_or_ancestor_thread_refused(self):
        self.current["ppid"] = 33
        with self.assertRaisesRegex(native.IdentityError, "parent"):
            collect(self.env, self.chain)
        self.current["ppid"] = 64197
        self.server["env"]["CODEX_THREAD_ID"] = CHILD
        with self.assertRaisesRegex(native.IdentityError, "ancestor thread"):
            collect(self.env, self.chain)

    def test_foreground_absent_null_or_other_thread_never_falls_back_to_argv(self):
        self.client["argv"] = ["/native/codex", "resume", SESSION]
        for value in (None, {}, dict(thread_id=None), dict(thread_id=CHILD)):
            self.foreground.return_value = value
            with self.subTest(value=value), self.assertRaisesRegex(native.IdentityError, "foreground"):
                collect(self.env, self.chain)

    def test_foreground_drift_and_read_error_refused(self):
        self.foreground.side_effect = [self.selection, dict(self.selection, signature=(2, 2, 3))]
        with self.assertRaisesRegex(native.IdentityError, "foreground changed"):
            collect(self.env, self.chain)
        self.foreground.side_effect = ValueError("untrusted foreground evidence")
        with self.assertRaises(ValueError):
            collect(self.env, self.chain)

    def test_process_birth_executable_scope_or_parent_drift_refused(self):
        original = self.process.side_effect
        for field, value in (("birth", [2, 3]), ("executable", "/other/python"),
                             ("ppid", 999), ("env", {})):
            calls = 0
            def drifting(pid):
                nonlocal calls
                p = original(pid)
                calls += 1
                if calls > 1:
                    p[field] = value
                return p
            self.process.side_effect = drifting
            with self.subTest(field=field), self.assertRaisesRegex(native.IdentityError, "identity drift"):
                collect(self.env, self.chain)

    def test_ancestry_or_daemon_appearing_during_recheck_refused(self):
        for result in ((self.chain[:-1], None), (self.chain, self.server)):
            self.parents.side_effect = None
            self.parents.return_value = result
            with self.subTest(result=result[1]), self.assertRaisesRegex(native.IdentityError, "ancestry drift"):
                collect(self.env, self.chain)

    def test_terminal_missing_refused(self):
        for value in (None, "", "?", "??", "-"):
            self.tty.return_value = value
            with self.subTest(value=value), self.assertRaisesRegex(native.IdentityError, "no terminal"):
                collect(self.env, self.chain)

    def test_child_hook_cannot_claim_foreground_root_mail(self):
        for inherited in (True, False):
            env = self.env if inherited else self.scope
            payload = dict(session_id=CHILD, hook_event_name="PostToolUse")
            with self.subTest(inherited=inherited), self.assertRaises(native.IdentityError):
                collect(env, self.chain, payload=payload)

    def test_hook_event_and_payload_fail_closed(self):
        for payload in ({}, dict(session_id=SESSION), dict(session_id=SESSION, hook_event_name="invented")):
            with self.subTest(payload=payload), self.assertRaises(native.IdentityError):
                collect(self.env, self.chain, payload=payload)

    def test_source_transform_refuses_unknown_shape(self):
        with self.assertRaises(ContractError):
            standalone_identity("# unknown controller\n")


if __name__ == "__main__":
    unittest.main()
