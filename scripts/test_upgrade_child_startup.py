"""Private opt-in child hook wiring; no native clients or model launches."""
from __future__ import annotations

import json
import contextlib
import io
import os
from pathlib import Path
import stat
import shlex
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from orchestration_common import ContractError, file_ref, json_ref, read_regular
import test_upgrade_live_startup as startup_fixtures
from upgrade_child_budget_protocol import SCHEMA, encode_packet, hook_command
import upgrade_child_service as service
from upgrade_startup_contract import validate_startup
from upgrade_child_service import register_service, validate_service_registration, serve
from upgrade_child_budget import budget_status, reserve_child
import test_upgrade_live_control as control_fixtures
from upgrade_live_runtime import authenticated_sessions
from orchestration_native import NativeReader
from test_orchestration_native import NativeFixture
from orchestration_common import digest, write_json, tree_manifest
from datetime import datetime, timezone
from upgrade_client_isolation import SANDBOX, clean_child_environment
from upgrade_live_control import control


class NativeChildDiscoveryTests(unittest.TestCase):
    def test_default_discovery_runs_only_own_tests_not_imported_fixture_test_classes(self):
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        def cases(items):
            for item in items:
                if isinstance(item, unittest.TestSuite):
                    yield from cases(item)
                else:
                    yield item
        selected = list(cases(suite))
        self.assertTrue(selected)
        self.assertTrue(all(test.__class__.__module__ == __name__ for test in selected),
                        [test.id() for test in selected if test.__class__.__module__ != __name__])


@unittest.skipUnless(startup_fixtures.MODERN_SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(), "requires the complete fixed modern MAC controller")
class NativeChildStartupTests(unittest.TestCase):
    source_controller = startup_fixtures.MODERN_SOURCE
    continuity_registration = True
    child_budget = True

    def setUp(self):
        startup_fixtures.MaterializedStartupTests.setUpClass.__func__(self)
        self.addCleanup(self.tmp.cleanup)

    def original_registration_inputs(self):
        # The existing private-adoption fixture is never a live model or
        # load-bearing hook proof. Exercise real production validators only.
        _, _, proof = control_fixtures.LiveControlTests.observation(self)
        adoption_ref = write_json(self.spec["adoption_path"], proof, exclusive=True)
        sessions = authenticated_sessions(self.runtime_ref, adoption_ref)
        journal = NativeFixture.__new__(NativeFixture)
        journal.path, journal.provider, journal.session = Path(sessions["codex"]["transcript"]["path"]), "codex", sessions["codex"]
        text = "Implement the original whole-task max-two child experiment. [offline fixture only]"
        record = journal.append(journal.message("user", text, at=datetime.now(timezone.utc).isoformat()))
        native_authorization = [dict(session=sessions["codex"], record=record, text_sha256=digest(text.encode()))]
        return adoption_ref, native_authorization, tree_manifest(Path(__file__).resolve().parent.parent)

    def test_private_pre_agent_hook_is_pinned_without_installing_a_service_or_children(self):
        self.assertEqual(self.binding["child_budget"], "whole-task-pre-agent-v1")
        controller = Path(self.spec["controller"])
        command = hook_command(self.spec["pins"]["python"]["path"], controller)
        for lane in ("claude_a", "claude_b", "codex"):
            name = "hooks.json" if lane == "codex" else "settings.json"
            ref = self.spec["pins"][lane + "/" + name]
            document = json.loads(read_regular(ref["path"]))
            events = ("PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop") if lane != "codex" else ("PreToolUse",)
            for event in events:
                self.assertEqual(sum(hook.get("command") == command for group in document["hooks"][event]
                    for hook in group["hooks"]), 1)
            self.assertFalse(any(hook.get("command") == command for group in document["hooks"].get("Stop", [])
                for hook in group["hooks"]))
        self.assertIn(str(self.paths["tmp"] / "child-budget.sock"), self.binding["isolation"]["immutable"])
        self.assertFalse((self.paths["tmp"] / "child-budget.sock").exists())
        self.assertFalse((self.paths["evidence"] / "live-control/native-child-budget").exists())
        self.assertTrue((controller / "scripts/mac_upgrade_child_hook.py").is_file())

    def test_opt_in_original_children_are_nonfork_foreground_without_importing_provider_overrides(self):
        for lane in ("claude_a", "claude_b"):
            environment = self.spec["clients"][lane]["environment"]
            self.assertEqual(environment.get("CLAUDE_CODE_FORK_SUBAGENT"), "0")
            self.assertEqual(environment.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"), "1")
            document = json.loads(read_regular(self.spec["pins"][lane + "/settings.json"]["path"]))
            self.assertNotIn("CLAUDE_CODE_FORK_SUBAGENT", document.get("env", {}))
            self.assertNotIn("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", document.get("env", {}))
        self.assertNotIn("CLAUDE_CODE_FORK_SUBAGENT", self.spec["clients"]["codex"]["environment"])
        self.assertNotIn("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", self.spec["clients"]["codex"]["environment"])

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_complete_private_hook_denies_creation_without_a_service_and_survives_post_observation_failure(self):
        lane = "claude_a"
        environment = clean_child_environment(self.spec["clients"][lane]["environment"])
        command = hook_command(self.spec["pins"]["python"]["path"], self.spec["controller"])
        argv = [str(SANDBOX), "-f", self.spec["pins"][lane + "_sandbox"]["path"]] + shlex.split(command)
        original = dict(session_id="11111111-1111-4111-8111-111111111111",
            transcript_path=str(Path(self.spec["clients"][lane]["native_root"]) / "projects/missing.jsonl"),
            tool_name="Agent", tool_use_id="toolu_no_service", tool_input=dict(description="Readonly probe",
                prompt="Do not execute a native child; this manually invoked Python probe tests failure handling.",
                subagent_type="general-purpose"))
        for event in ("PreToolUse", "PostToolUse"):
            result = subprocess.run(argv, input=json.dumps(dict(original, hook_event_name=event)),
                capture_output=True, text=True, env=environment, timeout=20)
            output = json.loads(result.stdout)
            if event == "PreToolUse":
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("CHILD_NATIVE_BUDGET_UNVERIFIED", output["hookSpecificOutput"]["permissionDecisionReason"])
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(set(output), {"hookSpecificOutput"})
                specific = output["hookSpecificOutput"]
                self.assertEqual(specific["hookEventName"], event)
                self.assertIn("occupied slot", specific["additionalContext"])
        ordinary = subprocess.run(argv, input=json.dumps(dict(original, hook_event_name="PreToolUse", tool_name="Read")),
            capture_output=True, text=True, env=environment, timeout=20)
        self.assertEqual((ordinary.returncode, ordinary.stdout), (0, ""), ordinary.stderr)
        self.assertFalse((self.paths["evidence"] / "live-control/native-child-budget").exists())

    def test_changed_child_hook_registration_is_refused_even_when_configuration_is_repinned(self):
        path = Path(self.spec["pins"]["claude_a/settings.json"]["path"])
        raw = path.read_bytes()
        try:
            document = json.loads(raw)
            document["hooks"]["PreToolUse"].pop(0)
            path.chmod(0o600)
            path.write_text(json.dumps(document))
            path.chmod(0o400)
            # verify_pins=False must still check the semantic opt-in contract.
            with self.assertRaisesRegex(ContractError, "STARTUP_CHILD_HOOK_CHANGED"):
                validate_startup(self.ref, verify_pins=False)
        finally:
            path.chmod(0o600)
            path.write_bytes(raw)
            path.chmod(0o400)

    def test_only_pinned_original_native_authorization_can_create_one_nonresettable_service_registration(self):
        # Synthetic private adoption uses the existing offline construction.
        # These bytes cannot be promoted as actual native/kernel adoption.
        adoption_ref, native_authorization, source = self.original_registration_inputs()
        with self.assertRaisesRegex(ContractError, "original native user authorization required"):
            register_service(self.runtime_ref, adoption_ref, task_id="original-registered-task",
                native_authorizations=[], source=source, child_budget_authorized=True)
        registered = register_service(self.runtime_ref, adoption_ref, task_id="original-registered-task",
            native_authorizations=native_authorization, source=source, child_budget_authorized=True)
        value, runtime = validate_service_registration(registered)
        self.assertEqual(value["task_id"], "original-registered-task")
        self.assertEqual(runtime, self.runtime)
        self.assertEqual(value["socket"], str(self.paths["tmp"] / "child-budget.sock"))
        self.assertFalse(Path(value["socket"]).exists())
        self.assertEqual(value["input_operations"], 0)
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_SERVICE_REGISTRATION_EXISTS"):
            register_service(self.runtime_ref, adoption_ref, task_id="changed-task",
                native_authorizations=native_authorization, source=source, child_budget_authorized=True)

    def test_new_launch_refuses_an_unsupported_native_hook_program_before_any_original_intent(self):
        # The explicitly non-model /usr/bin/true fixture cannot prove a native
        # Claude hook capability. Exercise the actual launch entrypoint, never
        # mock its own collaborators or invoke cmux/client creation.
        with self.assertRaisesRegex(ValueError, "CHILD_NATIVE_HOOK_VERSION_UNSUPPORTED"):
            control(self.runtime_ref, "launch")
        self.assertFalse((Path(self.spec["adoption_path"]).parent / "launch-request.json").exists())
        self.assertFalse((Path(self.runtime["state_root"]) / "startup/intent.json").exists())
        self.assertFalse((Path(self.spec["adoption_path"]).parent / "observations").exists())

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_original_private_harness_also_refuses_an_unsupported_hook_program_before_workspace_creation(self):
        argv = [str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
            self.spec["pins"]["python"]["path"], "-I", "-B",
            str(Path(self.spec["controller"]) / "scripts/mac_harness.py"), "experiment-launch",
            "--startup", self.ref["path"], "--startup-sha256", self.ref["sha256"],
            "--spawn", "--spawn-authorized"]
        result = subprocess.run(argv, capture_output=True, text=True, env=clean_child_environment({}), timeout=30)
        output = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(output["status"], "REFUSED")
        self.assertIn("CHILD_NATIVE_HOOK_VERSION_UNSUPPORTED", output["error"])
        self.assertFalse((Path(self.runtime["state_root"]) / "startup/intent.json").exists())
        self.assertFalse((Path(self.runtime["state_root"]) / "startup/result.json").exists())

    def test_bounded_real_service_exit_preserves_socket_intent_and_both_occupied_slots(self):
        adoption_ref, native_authorization, source = self.original_registration_inputs()
        registered = register_service(self.runtime_ref, adoption_ref, task_id="original-registered-task",
            native_authorizations=native_authorization, source=source, child_budget_authorized=True)
        registration = json_ref(registered)
        for lane in ("claude_a", "claude_b"):
            reserve_child(registration["authority"], parent=lane, tool_use_id="toolu_" + lane,
                tool_input=dict(prompt="Offline source fixture, not a native model launch."))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = serve(registered, timeout_seconds=1)
        ready = json.loads(output.getvalue())
        self.assertEqual(ready["status"], "CHILD_SERVICE_READY_NOT_ADOPTED")
        self.assertEqual((ready["model_requests"], ready["input_operations"]), (0, 0))
        self.assertEqual(result["status"], "CHILD_SERVICE_STOPPED_PRESERVED")
        self.assertEqual((result["handled"], result["inflight"]), (0, 2))
        endpoint = Path(registration["socket"])
        self.assertTrue(stat.S_ISSOCK(endpoint.lstat().st_mode))
        self.assertEqual(stat.S_IMODE(endpoint.lstat().st_mode), 0o600)
        intent = json_ref(file_ref(Path(registration["output_root"]) / "serve-intent.json"))
        self.assertEqual((intent["pid"], intent["timeout_seconds"]), (os.getpid(), 1))
        self.assertEqual(intent["registration"], registered)
        self.assertEqual(budget_status(registration["authority"])["inflight"], 2)
        with self.assertRaisesRegex(ContractError, "ORIGINAL_CHILD_SERVICE_SOCKET_EXISTS"):
            serve(registered, timeout_seconds=1)
        self.assertEqual(budget_status(registration["authority"])["inflight"], 2)

    def registered_service(self):
        adoption_ref, native_authorization, source = self.original_registration_inputs()
        return register_service(self.runtime_ref, adoption_ref, task_id="original-registered-task",
            native_authorizations=native_authorization, source=source, child_budget_authorized=True)

    def serve_in_background(self, registered, timeout_seconds):
        outcome, output = {}, io.StringIO()
        def run():
            with contextlib.redirect_stdout(output):
                outcome["result"] = service.serve(registered, timeout_seconds=timeout_seconds)
        worker = threading.Thread(target=run)
        worker.start()
        self.addCleanup(worker.join, 60)
        endpoint, deadline = Path(json_ref(registered)["socket"]), time.monotonic() + 120
        while not endpoint.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(endpoint.exists())
        return worker, outcome, endpoint

    def hook_request(self, endpoint, payload):
        # Mirror the installed mac_upgrade_child_hook.request(): a 10 s socket bound.
        started = time.monotonic()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(10)
            channel.connect(str(endpoint))
            channel.sendall(encode_packet(dict(schema=SCHEMA, lane="claude_a", payload=payload)))
            channel.shutdown(socket.SHUT_WR)
            raw = b""
            while b"\n" not in raw:
                chunk = channel.recv(16384)
                if not chunk:
                    break
                raw += chunk
        return json.loads(raw), time.monotonic() - started

    def test_slow_full_reverification_never_delays_a_hook_reply_past_its_socket_bound(self):
        # Live 2026-10-24: one full registration check took 8.5-11 s at load 74,
        # so checking it inside every event let the hook time out first.
        registered, original, calls = self.registered_service(), service.validate_service_registration, []
        def slow(ref):
            calls.append(time.monotonic())
            if len(calls) > 1:
                time.sleep(12)
            return original(ref)
        with mock.patch.object(service, "validate_service_registration", slow):
            worker, outcome, endpoint = self.serve_in_background(registered, 3)
            reply, elapsed = self.hook_request(endpoint, dict(hook_event_name="SubagentStart"))
            worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(reply["status"], "REFUSED")
        self.assertLess(elapsed, 10)
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome["result"]["handled"], 1)

    def test_failed_background_reverification_refuses_every_later_hook_event(self):
        registered, original, calls = self.registered_service(), service.validate_service_registration, []
        def failing(ref):
            calls.append(1)
            if len(calls) > 1:
                raise ContractError("CHILD_SERVICE_REGISTRATION_CHANGED")
            return original(ref)
        with mock.patch.object(service, "validate_service_registration", failing), \
                mock.patch.object(service, "REVERIFY_INTERVAL_SECONDS", 0.05):
            worker, outcome, endpoint = self.serve_in_background(registered, 3)
            deadline = time.monotonic() + 10
            while len(calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.3)
            reply, _ = self.hook_request(endpoint, dict(hook_event_name="SubagentStart"))
            worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(reply["status"], "REFUSED")
        self.assertIn("CHILD_SERVICE_REVERIFICATION_FAILED", reply["error"])
        self.assertIn("CHILD_SERVICE_REGISTRATION_CHANGED", reply["error"])
        self.assertEqual(len(calls), 2)

    def test_each_hook_event_synchronously_rereads_the_pinned_authority_files(self):
        registered = self.registered_service()
        adoption = Path(json_ref(registered)["adoption"]["path"])
        worker, outcome, endpoint = self.serve_in_background(registered, 3)
        adoption.chmod(0o644)
        with adoption.open("ab") as stream:
            stream.write(b" ")
        reply, _ = self.hook_request(endpoint, dict(hook_event_name="SubagentStart"))
        worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(reply["status"], "REFUSED")
        self.assertIn("original child service adoption: file hash/size mismatch", reply["error"])

    def test_successor_of_an_exited_service_continues_the_same_ledger_with_fixed_source(self):
        # Live 2026-10-24: the first service ran from a source whose per-event
        # check outlasted the hook. Its socket and ledger must not be reset.
        registered = self.registered_service()
        registration = json_ref(registered)
        reserve_child(registration["authority"], parent="claude_a", tool_use_id="toolu_before",
                      tool_input=dict(prompt="Offline source fixture, not a native model launch."))
        with contextlib.redirect_stdout(io.StringIO()):
            serve(registered, timeout_seconds=1)
        source = tree_manifest(Path(__file__).resolve().parent.parent)
        with self.assertRaisesRegex(ContractError, "CHILD_SERVICE_PREDECESSOR_STILL_RUNNING"):
            service.register_successor(registered, source=source)
        successor = service.register_successor(registered, source=source, process_present=lambda pid: False)
        value, _ = validate_service_registration(successor)
        self.assertEqual({k: v for k, v in value.items() if k not in ("source", "predecessor")},
                         {k: v for k, v in registration.items() if k != "source"})
        self.assertEqual(value["predecessor"], registered)
        self.assertFalse(Path(registration["socket"]).exists())
        with self.assertRaisesRegex(ContractError, "CHILD_SERVICE_SUCCESSOR_EXISTS"):
            service.register_successor(registered, source=source, process_present=lambda pid: False)
        with contextlib.redirect_stdout(io.StringIO()):
            result = serve(successor, timeout_seconds=1)
        self.assertEqual((result["handled"], result["inflight"]), (0, 1))
        intent = json_ref(file_ref(Path(successor["path"]).parent / "serve-intent.json"))
        self.assertEqual(intent["registration"], successor)
        self.assertEqual(budget_status(registration["authority"])["inflight"], 1)

    def test_successor_refuses_while_the_predecessor_socket_still_accepts(self):
        registered = self.registered_service()
        worker, outcome, endpoint = self.serve_in_background(registered, 3)
        source = tree_manifest(Path(__file__).resolve().parent.parent)
        with self.assertRaisesRegex(ContractError, "CHILD_SERVICE_PREDECESSOR_SOCKET_ACCEPTING"):
            service.register_successor(registered, source=source, process_present=lambda pid: False)
        worker.join(60)
        self.assertTrue(endpoint.exists())

    def test_stale_background_reverification_refuses_hook_events(self):
        registered = self.registered_service()
        with mock.patch.object(service, "MAX_VERIFIED_AGE_SECONDS", 0):
            worker, outcome, endpoint = self.serve_in_background(registered, 3)
            reply, _ = self.hook_request(endpoint, dict(hook_event_name="SubagentStart"))
            worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(reply["status"], "REFUSED")
        self.assertIn("CHILD_SERVICE_VERIFICATION_STALE", reply["error"])

    def test_agent_hook_reuses_the_reverified_native_root_instead_of_reauthenticating(self):
        # Live 2026-10-24 (live-3 C1): one private native-root authentication
        # took 2.8-6.7 s at load 160-200; the Agent hook constructed two native
        # readers, so the 6 s reply budget expired before any reservation.
        import upgrade_live_runtime as runtime
        registered = self.registered_service()
        session = json_ref(registered)["sessions"]["claude_a"]
        original, reads = runtime._read_native_authority, []
        def slow(authority):
            reads.append(time.monotonic())
            if len(reads) > 2:  # the serve start check reads at entry and exit
                time.sleep(12)
            return original(authority)
        payload = dict(hook_event_name="PreToolUse", session_id=session["session_id"],
                       transcript_path=session["transcript"]["path"], tool_name="Agent",
                       tool_use_id="toolu_not_in_the_original_journal",
                       tool_input=dict(description="d", prompt="p", subagent_type="general-purpose"))
        # Peer ancestry is covered by its own real-process tests; this one
        # measures only the native-root path behind it.
        with mock.patch.object(runtime, "_read_native_authority", slow), \
                mock.patch.object(service, "authenticate_hook_peer", lambda *a, **k: None):
            worker, outcome, endpoint = self.serve_in_background(registered, 3)
            reply, elapsed = self.hook_request(endpoint, payload)
            worker.join(60)
        self.assertFalse(worker.is_alive())
        self.assertEqual(reply["status"], "REFUSED")
        # The reader was built and scanned the original journal: past root auth.
        self.assertIn("missing or duplicate native invocation", reply["error"])
        self.assertLess(elapsed, service.REPLY_BUDGET_SECONDS)
        self.assertEqual(len(reads), 2)
        self.assertEqual(budget_status(json_ref(registered)["authority"])["inflight"], 0)


if __name__ == "__main__":
    unittest.main()
