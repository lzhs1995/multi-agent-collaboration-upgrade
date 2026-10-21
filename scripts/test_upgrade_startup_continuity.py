"""Offline startup-continuity contracts; never native adoption or live input."""
from pathlib import Path
import copy
import json
import shlex
import subprocess
import time
import uuid
import tempfile
import unittest

from orchestration_common import digest, file_ref, json_ref, write_json
from orchestration_common import ContractError
from upgrade_experiment import init_experiment
from upgrade_live_materialize import materialize_live
from upgrade_startup_contract import validate_startup
import test_upgrade_live_startup as startup_fixtures
from test_orchestration_native import NativeFixture
from upgrade_startup_contract import readiness_prompt
import cmux_native_delivery as native

ORIGINAL_SUPERVISOR_SID = "01a159cf-6685-76b0-b779-b4572afab31b"
ORIGINAL_USER_PATH = (Path.home() / ".codex/sessions/2026/10/20" /
    ("rollout-2026-10-20T22-55-26-" + ORIGINAL_SUPERVISOR_SID + ".jsonl"))


def existing_original_user_authorization():
    """Read-only local integration source, never create a global native fixture."""
    if not ORIGINAL_USER_PATH.is_file():
        raise unittest.SkipTest("requires the existing pinned original supervisor user record")
    identity = dict(path=str(ORIGINAL_USER_PATH), device=16777232, inode=212964986)
    return [dict(session=dict(provider="codex", session_id=ORIGINAL_SUPERVISOR_SID,
        transcript=identity), record=dict(identity, offset=72986593, length=709,
            sha256="ec9ed575ab2cd15af0c4cba107f05d8a3567f824a29d911087fd04eea4495f8d"),
        text_sha256="402e7bd9e9ccdf83295c60b784e00b3eb08c9711d9a165df71bbb2d3d8628e08")]


def original_startup_fixture(root, *, codex_rollout_name=False, terminal_ready_outcomes=True):
    """Real frozen files with explicitly synthetic native/kernel observations."""
    context = init_experiment(root, "startup-continuity-fixture", startup_fixtures.MODERN_SOURCE)
    binaries = {name: Path("/usr/bin/true") for name in ("codex", "claude", "cmux", "rtk", "node")}
    binaries.update({provider + "_wrapper": startup_fixtures.WRAPPERS / ("cmux-" + provider + "-wrapper")
                     for provider in ("codex", "claude")})
    runtime_ref = materialize_live(context, "B", binaries=binaries,
        provider_configuration=({"model": "test-model"}, {"model": "fixture-claude"}),
        continuity_registration=True, child_budget=True, startup_continuity=True)
    runtime = json_ref(runtime_ref)
    spec, binding = validate_startup(runtime["startup"])
    sessions, readiness, kernels, launches, launch_refs = {}, {}, {}, {}, {}
    workspace = str(uuid.uuid4())
    startup_root = Path(binding["state_root"]) / "startup"
    for index, lane in enumerate(("codex", "claude_a", "claude_b")):
        provider = "codex" if lane == "codex" else "claude"
        sid = str(uuid.uuid4()) if lane == "codex" else spec["clients"][lane]["session_id"]
        native_root = Path(binding["native_roots"][lane])
        log_root = native_root / ("sessions" if lane == "codex" else "projects")
        log_root.mkdir(parents=True, exist_ok=True)
        journal = NativeFixture(log_root, provider, session_id=sid)
        if lane == "codex" and codex_rollout_name:
            rollout = log_root / ("rollout-2026-10-21T08-00-00-" + sid + ".jsonl")
            journal.path.rename(rollout)
            journal.path = rollout
            journal.session["transcript"]["path"] = str(rollout)
        surface = str(uuid.uuid4())
        launch = dict(startup=runtime["startup"], pid=8000 + index, birth=[1, index],
            surface_uuid=surface, workspace_uuid=workspace, native_root=str(native_root), provider=provider)
        launches[lane] = launch
        launch_refs[lane] = write_json(startup_root / (lane + ".json"), launch, exclusive=True)
        prompt = readiness_prompt(spec["controller"], spec["pins"]["python"]["path"],
                                  spec["pins"]["rtk"]["path"], lane)
        if lane == "codex":
            turn = str(uuid.uuid4())
            journal.append(dict(type="event_msg", timestamp="2026-10-21T00:00:00Z",
                                payload=dict(type="task_started", turn_id=turn)))
            model_ref = journal.append(dict(type="turn_context", timestamp="2026-10-21T00:00:01Z",
                                payload=dict(turn_id=turn, model="test-model")))
            prompt_ref = journal.append(journal.message("user", prompt, at="2026-10-21T00:00:02Z"))
            terminal = journal.append(dict(type="event_msg", timestamp="2026-10-21T00:00:03Z",
                payload=dict(type="task_complete", turn_id=turn, error=dict(message="Original quota failure",
                    codex_error_info={"http_connection_failed": {"http_status_code": 402}}))))
        else:
            from upgrade_startup_contract import readiness_command
            prompt_id, assistant_id = str(uuid.uuid4()), str(uuid.uuid4())
            prompt_ref = journal.append(journal.message("user", prompt, at="2026-10-21T00:00:02Z",
                **({"uuid": prompt_id} if terminal_ready_outcomes else {})))
            call_id = "toolu_startup_" + lane
            command = readiness_command(spec["controller"], spec["pins"]["python"]["path"],
                                         spec["pins"]["rtk"]["path"], lane)
            args = {"command": command}
            answer = journal.message("assistant", "unused", at="2026-10-21T00:00:03Z")
            if terminal_ready_outcomes:
                answer.update(uuid=assistant_id, parentUuid=prompt_id)
                answer["message"].update(type="message", id="msg_startup_" + lane, stop_reason="tool_use")
            answer["message"].update(model="fixture-claude", content=[dict(type="tool_use", id=call_id,
                name="Bash", input=args)])
            model_ref = journal.append(answer)
            probe = dict(status="UPGRADE_READINESS_TOOL_COMPLETED", lane=lane,
                native_root=str(native_root), controller=spec["controller"], adoption_proven=False, input_operations=0)
            result = journal.message("user", "unused", at="2026-10-21T00:00:04Z")
            if terminal_ready_outcomes:
                result.update(uuid=str(uuid.uuid4()), parentUuid=assistant_id)
            result["message"]["content"] = [dict(type="tool_result", tool_use_id=call_id, content=json.dumps(probe))]
            journal.append(result)
            for event in ("PreToolUse", "PostToolUse"):
                config = spec["pins"][lane + "/settings.json"]
                hooks = json_ref(config)["hooks"][event]
                advisory = next(h["command"] for group in hooks for h in group["hooks"]
                                if "/cmux_workflow_advisory.py" in h.get("command", ""))
                hook = shlex.split(advisory)[-1]
                write_json(Path(binding["state_root"]) / "hook-observations" / lane / (event + ".json"),
                    dict(schema="upgrade-automatic-hook-observation-v2", lane=lane, startup=runtime["startup"],
                        adoption_proven=False, direct_client=lane, hook=hook, configuration=config, command=advisory,
                        parents=[dict(pid=launch["pid"], ppid=1, birth=launch["birth"],
                                      executable=spec["pins"][provider]["path"])],
                        payload=dict(session_id=sid, transcript_path=str(journal.path), hook_event_name=event,
                                     tool_use_id=call_id, tool_name="Bash", tool_input=args)), exclusive=True)
        transcript = native._pin_file(journal.path, provider, sid)
        session = dict(journal.session, workspace_uuid=workspace, surface_uuid=surface, pane_uuid=str(uuid.uuid4()),
            native_config_root=str(native_root), model="test-model" if lane == "codex" else "fixture-claude",
            model_record=model_ref, process=dict(pid=launch["pid"], birth=launch["birth"],
                executable=spec["pins"][provider]["path"]), transcript=transcript, freeze_fence=transcript)
        sessions[lane] = session
        kernels[lane] = dict(pid=launch["pid"], kernel_checked=True)
    launches["codex-server"] = dict(startup=runtime["startup"], pid=9999, birth=[1, 3],
                                    argv=spec["rpc"]["argv"])
    launch_refs["codex-server"] = write_json(startup_root / "codex-server.json", launches["codex-server"], exclusive=True)
    kernels["codex-server"] = dict(pid=9999, kernel_checked=True)
    intent = write_json(startup_root / "intent.json", dict(startup=runtime["startup"], spawn=True,
                                                         spawn_authorized=True), exclusive=True)
    result = write_json(startup_root / "result.json", dict(startup=runtime["startup"], returncode=0,
        workspace_creation_attempts=1, stdout=workspace), exclusive=True)
    from upgrade_live_inspect import readiness_evidence
    from upgrade_startup_continuity import startup_failure_evidence
    for lane in ("claude_a", "claude_b"):
        readiness[lane] = readiness_evidence(native, sessions[lane], lane, spec, runtime["startup"], binding, launches)
    failure = startup_failure_evidence(native, sessions["codex"], "codex", spec, runtime["startup"],
                                       task_id="startup-continuity-fixture", now=time.time())
    value = dict(schema="upgrade-live-inspection-v2", startup=runtime["startup"], ready=False,
        stopped=False, errors={"codex": "READINESS_TOOL_NOT_UNIQUE_OR_NOT_OBSERVED"},
        input_operations=0, inspector=file_ref(Path(spec["controller"]) / "scripts/mac_upgrade_live_inspect.py"),
        observed_at_ns=time.time_ns(), sessions=sessions, readiness=readiness, kernel=kernels,
        launch_intent=intent, launch_result=result, launches=launch_refs,
        codex_capability=dict(thread_id=sessions["codex"]["session_id"], path=sessions["codex"]["transcript"]["path"],
            can_accept_direct_input=True, methods=["thread/read", "thread/turns/list"], input_operations=0),
        startup_failures={"codex": failure})
    from upgrade_live_control import harness_argv
    inspection = write_json(Path(spec["adoption_path"]).parent / "observations/startup-fixture/result.json",
        dict(runtime=runtime_ref, startup=runtime["startup"], command="experiment-inspect", returncode=0,
             argv=harness_argv(runtime, spec, "experiment-inspect"), input_operations=0, result=value), exclusive=True)
    authorizations = existing_original_user_authorization()
    return runtime_ref, runtime, spec, binding, inspection, sessions, authorizations


def prompt_for_codex(spec):
    return readiness_prompt(spec["controller"], spec["pins"]["python"]["path"], spec["pins"]["rtk"]["path"], "codex")


class StartupFailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.journal = NativeFixture(self.root, "codex", session_id=str(uuid.uuid4()))
        self.config = self.root / "config.toml"
        self.config.write_bytes(b'model = "test-model"\n')
        self.spec = dict(controller=str(self.root / "controller"),
            clients={"codex": dict(native_root=str(self.root))},
            pins={"python": dict(path="/pinned/python"), "rtk": dict(path="/pinned/rtk"),
                  "codex/config.toml": file_ref(self.config)})
        self.startup = write_json(self.root / "startup.json", {"offline_fixture": True})

    def failure(self, info=None):
        turn = str(uuid.uuid4())
        self.journal.append(dict(type="event_msg", timestamp="2026-10-21T00:00:00Z",
            payload=dict(type="task_started", turn_id=turn)))
        self.journal.append(dict(type="turn_context", timestamp="2026-10-21T00:00:01Z",
            payload=dict(turn_id=turn, model="test-model")))
        prompt = readiness_prompt(self.spec["controller"], "/pinned/python", "/pinned/rtk", "codex")
        self.prompt = self.journal.append(self.journal.message("user", prompt,
            at="2026-10-21T00:00:02Z"))
        terminal = self.journal.append(dict(type="event_msg", timestamp="2026-10-21T00:00:03Z",
            payload=dict(type="task_complete", turn_id=turn, last_agent_message=None,
                error=dict(message="Original native failure", codex_error_info=(
                    {"http_connection_failed": {"http_status_code": 402}} if info is None else info)))))
        session = dict(self.journal.session, transcript=native._pin_file(self.journal.path, "codex",
                            self.journal.session["session_id"]))
        return session, terminal

    def test_actual_tagged_original_402_before_probe_proves_startup_failure_not_adoption(self):
        from upgrade_startup_continuity import startup_failure_evidence
        session, terminal = self.failure()
        value = startup_failure_evidence(native, session, "codex", self.spec, self.startup,
                                         task_id="original-task", now=1792540900)
        self.assertEqual(value["outcome"]["kind"], "API_ERROR")
        self.assertEqual(value["outcome"]["http_status"], 402)
        self.assertEqual(value["outcome"]["native_record"], terminal)
        self.assertEqual(value["prompt"], self.prompt)
        self.assertEqual(value["model"], "test-model")
        self.assertFalse(value["adoption_proven"])
        self.assertEqual(value["input_operations"], 0)

    def test_changed_model_or_unknown_native_terminal_never_grants_fallback(self):
        from upgrade_startup_continuity import startup_failure_evidence
        session, _ = self.failure()
        self.journal.append(dict(type="event_msg", timestamp="2026-10-21T00:00:04Z",
            payload=dict(type="task_started", turn_id="11111111-1111-4111-8111-111111111111")))
        self.journal.append(dict(type="turn_context", timestamp="2026-10-21T00:00:05Z",
            payload=dict(turn_id="11111111-1111-4111-8111-111111111111", model="changed-model")))
        self.journal.append(dict(type="event_msg", timestamp="2026-10-21T00:00:06Z",
            payload=dict(type="task_complete", turn_id="11111111-1111-4111-8111-111111111111",
                error=dict(message="changed model", codex_error_info={"http_status_code": 402}))))
        session["transcript"] = native._pin_file(self.journal.path, "codex", session["session_id"])
        with self.assertRaisesRegex(ContractError, "STARTUP_FAILURE_MODEL_CHANGED"):
            startup_failure_evidence(native, session, "codex", self.spec, self.startup,
                                     task_id="original-task", now=1792540900)

    def test_copying_authored_failure_into_a_different_inode_is_not_original_native_evidence(self):
        from upgrade_startup_continuity import startup_failure_evidence
        session, _ = self.failure()
        old = self.journal.path.with_suffix(".original")
        self.journal.path.rename(old)
        self.journal.path.write_bytes(old.read_bytes())
        with self.assertRaisesRegex(native.NativeDeliveryError, "NATIVE_TRANSCRIPT_REPLACED_OR_TRUNCATED"):
            startup_failure_evidence(native, session, "codex", self.spec, self.startup,
                                     task_id="original-task", now=1792540900)

    def test_unknown_string_bool_retryable_or_ambiguous_status_remains_insufficient(self):
        from upgrade_startup_continuity import startup_failure_evidence
        for status in ("402", True, 429, 502, 200, 600):
            with self.subTest(status=status):
                self.journal.path.write_bytes(self.journal.path.read_bytes().splitlines(keepends=True)[0])
                session, _ = self.failure({"http_connection_failed": {"http_status_code": status}})
                with self.assertRaisesRegex(ContractError, "STARTUP_FAILURE_UNAVAILABLE_NOT_PROVEN"):
                    startup_failure_evidence(native, session, "codex", self.spec, self.startup,
                                             task_id="original-task", now=1792540900)


class OriginalUserAuthorizationTests(unittest.TestCase):
    def test_original_supervisor_user_source_is_outside_experimental_startup_sessions(self):
        from upgrade_startup_continuity import original_user_authorizations
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            source = home / ".codex/sessions/2026/10/21"
            source.mkdir(parents=True)
            journal = NativeFixture(source, "codex", session_id=str(uuid.uuid4()))
            text = "Implement the original continuity plan. [offline source fixture only]"
            record = journal.append(journal.message("user", text))
            authorization = dict(session={key: journal.session[key]
                for key in ("provider", "session_id", "transcript")}, record=record,
                text_sha256=digest(text.encode()))
            originals = {lane: dict(session_id=str(uuid.uuid4()))
                         for lane in ("codex", "claude_a", "claude_b")}
            checked = original_user_authorizations(native, [authorization], account_home=home,
                experiment_root=home / "experiment", original_sessions=originals)
            self.assertEqual(checked, [authorization])
            self.assertNotIn(checked[0]["session"]["session_id"],
                             {session["session_id"] for session in originals.values()})

    def test_original_source_refuses_copy_hash_role_session_and_record_boundaries(self):
        from upgrade_startup_continuity import original_user_authorizations
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            source = home / ".codex/sessions/2026/10/21"
            source.mkdir(parents=True)
            originals = {lane: dict(session_id=str(uuid.uuid4()))
                         for lane in ("codex", "claude_a", "claude_b")}
            journal = NativeFixture(source, "codex", session_id=str(uuid.uuid4()))
            text = "Original user scope. [offline source fixture only]"
            record = journal.append(journal.message("user", text))
            item = dict(session={key: journal.session[key]
                for key in ("provider", "session_id", "transcript")}, record=record,
                text_sha256=digest(text.encode()))
            assistant = journal.append(journal.message("assistant", text))
            def check(value):
                return original_user_authorizations(native, [value], account_home=home,
                    experiment_root=home / "experiment", original_sessions=originals)
            mutations = [("STARTUP_CONTINUITY_ORIGINAL_USER_MESSAGE_REQUIRED", dict(item, text_sha256="0" * 64)),
                ("STARTUP_CONTINUITY_ORIGINAL_USER_RECORD_CHANGED", dict(item, record=dict(record, sha256="0" * 64))),
                ("STARTUP_CONTINUITY_USER_RECORD_BOUNDARY_CHANGED", dict(item, record=dict(record, offset=record["offset"] + 1))),
                ("STARTUP_CONTINUITY_ORIGINAL_SUPERVISOR_USER_REQUIRED", dict(item,
                    session=dict(item["session"], session_id=originals["codex"]["session_id"])))]
            for error, changed in mutations:
                with self.subTest(error=error), self.assertRaisesRegex(ContractError, error):
                    check(changed)
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_ORIGINAL_USER_MESSAGE_REQUIRED"):
                check(dict(item, record=assistant))
            fake = home / "experiment/copied"
            fake.mkdir(parents=True)
            copied = fake / journal.path.name
            copied.write_bytes(journal.path.read_bytes())
            identity = dict(path=str(copied), device=copied.stat().st_dev, inode=copied.stat().st_ino)
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_ORIGINAL_ACCOUNT_USER_REQUIRED"):
                check(dict(item, session=dict(item["session"], transcript=identity), record=dict(record, **identity)))
            held = journal.path.with_suffix(".original")
            journal.path.rename(held)
            journal.path.write_bytes(held.read_bytes())
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_ORIGINAL_USER_FILE_CHANGED"):
                check(item)

    def test_claude_sidechain_and_tool_results_cannot_authorize_an_original_task(self):
        from upgrade_startup_continuity import original_user_authorizations
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            source = home / ".claude/projects/original-project"
            source.mkdir(parents=True)
            journal = NativeFixture(source, "claude", session_id=str(uuid.uuid4()))
            text = "Original user continuity scope. [offline source fixture only]"
            record = journal.append(journal.message("user", text))
            item = dict(session={key: journal.session[key]
                for key in ("provider", "session_id", "transcript")}, record=record,
                text_sha256=digest(text.encode()))
            def check(value):
                return original_user_authorizations(native, [value], account_home=home,
                    experiment_root=home / "experiment", original_sessions={})
            self.assertEqual(check(item), [item])
            side = journal.append(journal.message("user", text, isSidechain=True))
            tool = journal.message("user", "unused")
            tool["message"]["content"] = [dict(type="tool_result", tool_use_id="toolu_fixture", content=text)]
            result = journal.append(tool)
            for ref in (side, result):
                with self.subTest(ref=ref["offset"]), self.assertRaisesRegex(ContractError,
                        "STARTUP_CONTINUITY_ORIGINAL_USER_MESSAGE_REQUIRED"):
                    check(dict(item, record=ref))


class StartupContinuityPublicEntryTests(unittest.TestCase):
    def test_public_entry_requires_pinned_request_without_fixture_time_home_or_status_override(self):
        import sys
        script = str(Path(__file__).with_name("upgrade_startup_continuity.py"))
        help_result = subprocess.run([sys.executable, "-B", script, "--help"],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("register", help_result.stdout)
        for extra in ("--fixtures", "--now", "--home", "--status", "--model"):
            with self.subTest(extra=extra):
                result = subprocess.run([sys.executable, "-B", script, "register", "--request",
                    "/no/original-request.json", "--request-sha256", "0" * 64, extra],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("unrecognized arguments", result.stderr)


@unittest.skipUnless(startup_fixtures.MODERN_SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(),
                     "requires complete fixed MAC 0.4.58 and original cmux wrappers")
class StartupContinuityMaterializationTests(unittest.TestCase):
    def test_explicit_startup_continuity_is_frozen_without_adopting_or_starting_clients(self):
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            root = Path(temporary).resolve() / "l"
            context = init_experiment(root, "startup-continuity-fixture", startup_fixtures.MODERN_SOURCE)
            binaries = {name: Path("/usr/bin/true")
                        for name in ("codex", "claude", "cmux", "rtk", "node")}
            binaries.update({provider + "_wrapper": startup_fixtures.WRAPPERS / ("cmux-" + provider + "-wrapper")
                             for provider in ("codex", "claude")})
            runtime_ref = materialize_live(context, "B", binaries=binaries,
                provider_configuration=({}, {}), continuity_registration=True,
                child_budget=True, startup_continuity=True)
            runtime = json_ref(runtime_ref)
            spec, binding = validate_startup(runtime["startup"])
            self.assertEqual(binding.get("startup_continuity"), "original-startup-failure-v1")
            self.assertEqual(binding["continuity_registration"], "private-current-v1")
            self.assertEqual(binding["child_budget"], "whole-task-pre-agent-v1")
            self.assertFalse(Path(spec["adoption_path"]).exists())
            self.assertFalse((Path(spec["adoption_path"]).parent / "startup-continuity.json").exists())
            self.assertFalse((root / "state/B/startup/intent.json").exists())

    def test_original_startup_failure_registers_authenticated_claude_peers_without_fake_trio_adoption(self):
        from upgrade_startup_continuity import register_startup_continuity, validate_startup_continuity
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            value = validate_startup_continuity(runtime_ref, ref)
            self.assertEqual(value["healthy_lanes"], ["claude_a", "claude_b"])
            self.assertEqual(value["failed_lanes"], ["codex"])
            self.assertEqual(value["sessions"], sessions)
            self.assertTrue(value["source_verified"])
            self.assertFalse(Path(spec["adoption_path"]).exists())
            self.assertFalse(value["production_adoption"])
            self.assertEqual(value["input_operations"], 0)
            self.assertFalse((Path(binding["tmp_root"]) / "child-budget.sock").exists())

    def test_experimental_startup_user_prompt_cannot_self_authorize_fallback(self):
        from upgrade_startup_continuity import register_startup_continuity
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, _ = original_startup_fixture(
                Path(temporary).resolve() / "l")
            failure = json_ref(inspection)["result"]["startup_failures"]["codex"]
            own = {key: sessions["codex"][key] for key in ("provider", "session_id")}
            own["transcript"] = {key: sessions["codex"]["transcript"][key]
                                 for key in ("path", "device", "inode")}
            authorization = dict(session=own, record=failure["prompt"],
                                 text_sha256=digest(prompt_for_codex(spec).encode()))
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_ORIGINAL_SUPERVISOR_USER_REQUIRED"):
                register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                    native_authorizations=[authorization], automatic_fallback_authorized=True)
            self.assertFalse((Path(spec["adoption_path"]).parent / "startup-continuity.json").exists())

    def test_actual_codex_rollout_filename_still_binds_exact_original_session(self):
        from upgrade_startup_continuity import register_startup_continuity, validate_startup_continuity
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l", codex_rollout_name=True)
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            checked = validate_startup_continuity(runtime_ref, ref)
            self.assertEqual(checked["sessions"]["codex"], sessions["codex"])
            self.assertTrue(Path(checked["sessions"]["codex"]["transcript"]["path"]).name.startswith("rollout-"))

    def test_ready_claude_cannot_reuse_the_original_startup_proof_after_model_change(self):
        from orchestration_common import canonical
        from upgrade_startup_continuity import register_startup_continuity, validate_startup_continuity
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            session = sessions["claude_a"]
            with Path(session["transcript"]["path"]).open("ab") as stream:
                stream.write(canonical(dict(type="assistant", sessionId=session["session_id"],
                    timestamp="2026-10-21T00:00:06Z", message=dict(role="assistant", model="changed-model",
                        content=[dict(type="text", text="New model output; offline refusal fixture.")]))))
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_READY_MODEL_CHANGED"):
                validate_startup_continuity(runtime_ref, ref)

    def test_startup_contract_requires_original_read_only_codex_capability(self):
        from upgrade_startup_continuity import register_startup_continuity
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            value = json_ref(inspection)
            value["result"]["codex_capability"]["thread_id"] = str(uuid.uuid4())
            changed = write_json(Path(inspection["path"]), value)
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_CODEX_CAPABILITY_CHANGED"):
                register_startup_continuity(runtime_ref, changed, task_id="startup-continuity-fixture",
                    native_authorizations=authorizations, automatic_fallback_authorized=True)
            self.assertFalse((Path(spec["adoption_path"]).parent / "startup-continuity.json").exists())

    def test_readiness_return_without_native_parented_api_terminal_is_not_availability(self):
        from upgrade_startup_continuity import register_startup_continuity
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l", terminal_ready_outcomes=False)
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_READY_API_OUTCOME_REQUIRED"):
                register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                    native_authorizations=authorizations, automatic_fallback_authorized=True)
            self.assertFalse((Path(spec["adoption_path"]).parent / "startup-continuity.json").exists())

    def test_materialized_original_input_gate_admits_only_original_ready_claude_peer_endpoints(self):
        from upgrade_startup_continuity import register_startup_continuity
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            code = r'''
import copy, json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(SPEC['controller']) / 'scripts'))
import cmux_daemon_identity as identity
import mac_upgrade_input_gate as original
sessions = SESSIONS
def process(pid):
    lane, session = next((lane, session) for lane, session in sessions.items() if session['process']['pid'] == pid)
    return dict(session['process'], env=dict(HOME=ACCOUNT_HOME_LITERAL, CMUX_SURFACE_ID=session['surface_uuid'],
        CMUX_WORKSPACE_ID=session['workspace_uuid'],
        **({'CODEX_HOME': session['native_config_root']} if lane == 'codex' else
           {'CLAUDE_CONFIG_DIR': session['native_config_root']})))
peer = dict(workspace_uuid=sessions['claude_a']['workspace_uuid'],
    caller_surface_uuid=sessions['claude_a']['surface_uuid'], target_surface_uuid=sessions['claude_b']['surface_uuid'],
    target_pane_uuid=sessions['claude_b']['pane_uuid'])
with patch.object(identity, 'process', side_effect=process), patch.object(original, 'require_kernel_boundary'):
    actual = original.check_input(peer)
    assert actual['schema'] == 'upgrade-startup-continuity-v1'
    for target in (sessions['codex']['surface_uuid'], 'ffffffff-ffff-4fff-8fff-ffffffffffff'):
        try:
            original.check_input(dict(peer, target_surface_uuid=target))
        except ValueError as exc:
            assert 'STARTUP_CONTINUITY_ENDPOINT_NOT_READY' in str(exc), str(exc)
        else:
            raise AssertionError('unready or external native endpoint admitted')
print('ORIGINAL_STARTUP_PEER_INPUT_GATE_PASS')
'''.replace("ACCOUNT_HOME_LITERAL", repr(str(Path.home().resolve()))).replace("SPEC", repr(spec)).replace("SESSIONS", repr(sessions))
            result = subprocess.run(["/usr/bin/sandbox-exec", "-f", spec["pins"]["control_sandbox"]["path"],
                                     runtime["python"]["path"], "-I", "-B", "-c", code],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ORIGINAL_STARTUP_PEER_INPUT_GATE_PASS", result.stdout)
            self.assertFalse(Path(spec["adoption_path"]).exists())

    def test_original_child_service_uses_same_whole_task_ledger_under_startup_continuity(self):
        from upgrade_startup_continuity import register_startup_continuity
        from upgrade_child_service import register_service, validate_service_registration
        from upgrade_child_budget import budget_status, reserve_child
        from orchestration_common import tree_manifest
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            registered = register_service(runtime_ref, ref, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, source=tree_manifest(Path(__file__).resolve().parent.parent),
                child_budget_authorized=True)
            value, _ = validate_service_registration(registered)
            for lane in ("claude_a", "claude_b"):
                reserve_child(value["authority"], parent=lane, tool_use_id="toolu_" + lane,
                    tool_input=dict(description="Readonly original task", prompt="Review only; offline fixture.",
                                    subagent_type="general-purpose"))
            self.assertEqual(budget_status(value["authority"])["inflight"], 2)
            with self.assertRaisesRegex(ContractError, "WHOLE_TASK_CHILD_LIMIT: third unconsumed child refused"):
                reserve_child(value["authority"], parent="claude_a", tool_use_id="toolu_third",
                    tool_input=dict(description="Third claim", prompt="Offline refusal probe.", subagent_type="general-purpose"))
            self.assertFalse(Path(spec["adoption_path"]).exists())
            self.assertFalse(Path(value["socket"]).exists())

    def test_startup_child_service_cannot_change_the_original_authorized_task_id(self):
        from upgrade_startup_continuity import register_startup_continuity
        from upgrade_child_service import register_service, service_root
        from orchestration_common import tree_manifest
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            with self.assertRaisesRegex(ContractError, "STARTUP_CONTINUITY_CHILD_AUTHORIZATION_CHANGED"):
                register_service(runtime_ref, ref, task_id="different-original-task",
                    native_authorizations=authorizations,
                    source=tree_manifest(Path(__file__).resolve().parent.parent), child_budget_authorized=True)
            self.assertFalse(service_root(runtime).exists())

    def test_registered_native_continuity_can_read_original_startup_failure_authority(self):
        from upgrade_startup_continuity import register_startup_continuity
        from upgrade_continuity_native import register_native_continuity, validate_native_registration
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            registered = register_native_continuity(runtime_ref, ref, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            value, _ = validate_native_registration(registered)
            self.assertEqual(value["adoption"], ref)
            self.assertEqual(value["task_id"], "startup-continuity-fixture")
            self.assertEqual(set(value["sessions"]), {"codex", "claude_a", "claude_b"})
            self.assertFalse(Path(spec["adoption_path"]).exists())

    def test_original_evaluator_includes_the_separately_named_startup_interval_not_old_fences(self):
        from upgrade_startup_continuity import register_startup_continuity
        from upgrade_continuity_native import register_native_continuity, observe_registered_native
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temporary:
            runtime_ref, runtime, spec, binding, inspection, sessions, authorizations = original_startup_fixture(
                Path(temporary).resolve() / "l")
            ref = register_startup_continuity(runtime_ref, inspection, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            registration = register_native_continuity(runtime_ref, ref, task_id="startup-continuity-fixture",
                native_authorizations=authorizations, automatic_fallback_authorized=True)
            before = json_ref(registration)
            result = observe_registered_native(registration, now=time.time())
            self.assertEqual(result["original"]["plan"]["mode"], "CLAUDE_PEER")
            self.assertEqual(result["observations"]["schema"], "upgrade-startup-native-continuity-observations-v1")
            self.assertEqual(result["observations"]["observation_scope"], "ORIGINAL_STARTUP_NOT_ADOPTION_EPISODE")
            self.assertEqual(result["observations"]["startup_continuity"], ref)
            self.assertEqual(len(result["observations"]["observations"]), 3)
            self.assertEqual(json_ref(registration), before)
            self.assertEqual(result["input_operations"], 0)
            self.assertEqual(result["resource_releases"], 0)
            self.assertFalse(Path(spec["adoption_path"]).exists())


if __name__ == "__main__":
    unittest.main()
