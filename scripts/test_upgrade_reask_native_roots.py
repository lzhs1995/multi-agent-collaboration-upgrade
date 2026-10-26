"""Exercise original inquiry capture/consume and automatic-hook identity code.

Only kernel and host observations are fixture dependencies. The private native
root policy, original source functions, metadata and fd/file pins execute in a
fresh process under the genuine generated controller's Seatbelt boundary.
"""
from pathlib import Path
import json
import copy
import tempfile
import time
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import uuid

import test_upgrade_live_startup as startup
from upgrade_client_isolation import SANDBOX
from upgrade_materialize import hook_config
from orchestration_common import ContractError, digest
from test_upgrade_mac058_compatibility import definitions, source as native_source
from upgrade_live_patch import native_readers, transform_live
import cmux_native_delivery as original_native
import upgrade_native_roots as private_roots
from upgrade_startup_contract import (CONTINUITY_CURRENT_PIN, continuity_registration_enabled,
                                      validate_startup)

FIXTURES = Path(__file__).with_name("fixtures") / "mac-0.4.58"
MANIFEST = json.loads((FIXTURES / "continuity-manifest.json").read_bytes())


def source(name):
    raw = (FIXTURES / name).read_bytes()
    pin = MANIFEST["files"][name]
    if len(raw) != pin["bytes"] or digest(raw) != pin["sha256"]:
        raise AssertionError("unchanged continuity fixture required: " + name)
    return raw.decode()


@unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists()
                     and startup.MODERN_SOURCE.is_dir() and startup.WRAPPERS.is_dir(),
                     "requires fixed complete MAC 0.4.58 and macOS isolation")
class InquiryNativeRootTests(unittest.TestCase):
    source_controller = startup.MODERN_SOURCE
    continuity_registration = True

    @classmethod
    def setUpClass(cls):
        startup.MaterializedStartupTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        startup.MaterializedStartupTests.tearDownClass.__func__(cls)

    def test_original_scheduler_approves_only_manifest_bound_private_current(self):
        code = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, CONTROLLER)
import executor_reask_scheduler as scheduler
import executor_ready as ready
current, pins = scheduler.guardian.verify_release(Path.home(), ready.state_root() / "CURRENT.json")
assert current["source"] == CONTROLLER_ROOT
assert scheduler.approved_release() == current
assert "scripts/executor_proactive_hook.py" in pins["files"]
assert "mac_upgrade_private_current" in PINS
print("ORIGINAL_SCHEDULER_PRIVATE_CURRENT_PASS")
'''.replace("CONTROLLER_ROOT", repr(self.spec["controller"]))
        code = code.replace("CONTROLLER", repr(str(Path(self.spec["controller"]) / "scripts")))
        code = code.replace("PINS", repr(self.spec["pins"]))
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
            sys.executable, "-I", "-B", "-c", code], text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORIGINAL_SCHEDULER_PRIVATE_CURRENT_PASS", result.stdout)

    def test_private_current_is_immutable_and_tampered_content_refuses_startup(self):
        path = Path(self.spec["pins"][CONTINUITY_CURRENT_PIN]["path"])
        saved, mode = path.read_bytes(), path.stat().st_mode & 0o777
        current = json.loads(saved)
        try:
            for key, value in (("source", str(Path.home() / ".local/share/multi-agent-collaboration")),
                               ("python", "/offline/different-python"), ("manifest_sha256", "0" * 64)):
                path.chmod(mode | 0o200)
                path.write_text(json.dumps(dict(current, **{key: value})))
                path.chmod(mode)
                with self.subTest(field=key), self.assertRaisesRegex(
                        ContractError, "STARTUP_CONTINUITY_CURRENT_CHANGED"):
                    validate_startup(self.ref)
        finally:
            path.chmod(mode | 0o200)
            path.write_bytes(saved)
            path.chmod(mode)
        self.assertEqual(validate_startup(self.ref)[0], self.spec)
        code = "from pathlib import Path\npath = Path(" + repr(str(path)) + ")\n"
        code += ("try:\n    path.write_text('forbidden')\nexcept PermissionError:\n"
                 "    print('KERNEL_PRIVATE_CURRENT_IMMUTABLE_PASS')\nelse:\n"
                 "    raise AssertionError('private CURRENT was writable')\n")
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
            sys.executable, "-I", "-B", "-c", code], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("KERNEL_PRIVATE_CURRENT_IMMUTABLE_PASS", result.stdout)
        self.assertEqual(path.read_bytes(), saved)

    def test_original_stop_hook_records_parent_provenance_not_adoption(self):
        code = r'''
import io, json, os, runpy, sys, uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, CONTROLLER)
import cmux_daemon_identity as identity
source, state = Path(CONTROLLER).parent, Path(STATE_ROOT)
launch = dict(pid=77802, birth=[123, 456])
startup = state / "startup"
startup.mkdir(exist_ok=True)
(startup / "claude_a.json").write_text(json.dumps(launch))
client = dict(pid=77802, ppid=1, birth=launch["birth"], executable="/usr/bin/true",
              file_identity=[1, 2, 3, 4], argv=["/usr/bin/true"])
sid = str(uuid.uuid4())
payload = dict(session_id=sid, hook_event_name="Stop", transcript_path=str(
    Path(NATIVE_ROOT) / "projects" / (sid + ".jsonl")))
script = str(source / "scripts/cmux_workflow_advisory.py")
with patch.dict(os.environ, {"UPGRADE_CLIENT_LANE": "claude_a"}), \
     patch.object(identity, "process", return_value=client), \
     patch.object(os, "getppid", return_value=client["pid"]), \
     patch.object(sys, "argv", [script, "--hook", "cmux_executor_idle_guard.py"]), \
     patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as result:
        assert result.code == 0
observations = list((state / "hook-observations/claude_a").glob("*.json"))
assert len(observations) == 1, observations
value = json.loads(observations[0].read_bytes())
assert value["payload"]["hook_event_name"] == "Stop"
assert value["hook"] == "cmux_executor_idle_guard.py"
assert value["scope_status"] == "ADVISORY_OBSERVED"
assert value["direct_client"] == "claude_a"
assert value["parents"][0]["birth"] == launch["birth"]
assert value["adoption_proven"] is False and value["native_reception"] is False
print("ORIGINAL_STOP_OBSERVATION_NOT_ADOPTION_PASS")
'''
        code = code.replace("CONTROLLER", repr(str(Path(self.spec["controller"]) / "scripts")))
        code = code.replace("STATE_ROOT", repr(str(self.paths["state"])))
        code = code.replace("NATIVE_ROOT", repr(self.spec["clients"]["claude_a"]["native_root"]))
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
            sys.executable, "-I", "-B", "-c", code], text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORIGINAL_STOP_OBSERVATION_NOT_ADOPTION_PASS", result.stdout)

    def test_actual_inquiry_capture_check_consume_auth_and_advisory_keep_private_root(self):
        code = r'''
import copy, json, os, sys, uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, CONTROLLER)
import cmux_native_delivery as native
import executor_reask_identity as identity
import executor_reply_advisory as advisory
import executor_reask as reask
from mac_upgrade_native_roots import process_root
provider, sid = "claude", str(uuid.uuid4())
surface, workspace, pane = [str(uuid.uuid4()).upper() for _ in range(3)]
root = Path(NATIVE_ROOT)
log = root / "projects" / (sid + ".jsonl")
log.parent.mkdir(parents=True, exist_ok=True)
log.write_text(json.dumps(dict(type="assistant", sessionId=sid,
    message=dict(role="assistant", content=[]))) + "\n")
p = dict(pid=77801, ppid=1, birth=[123, 456], executable="/offline/claude",
         file_identity=[1, 2, 3, 4], env=dict(HOME=str(Path.home().resolve()),
         CLAUDE_CONFIG_DIR=str(root), CMUX_SURFACE_ID=surface,
         CMUX_WORKSPACE_ID=workspace))
row = dict(target_surface_uuid=surface, workspace_uuid=workspace,
           target_pane_uuid=pane, tty="ttys998")
state = dict(caller_surface_uuid=surface, workspace_uuid=workspace,
             supervisor=str(uuid.uuid4()).upper(), task_id="same-task",
             episode_id="episode", controller=reask.controller())
live = dict(caller_surface_uuid=surface, workspace_uuid=workspace, caller_pane_uuid=pane)
def ancestry(binding):
    assert binding["process"] == native._public_process(p)
    return dict(kind="OFFLINE_EXTERNAL_ANCESTRY")
with patch.object(native, "_tree_row", return_value=row), \
     patch.object(native, "_target_process", return_value=p), \
     patch.object(native.identity, "process", return_value=p), \
     patch.object(native, "_provider", return_value=provider), \
     patch.object(native, "_live_session", return_value=sid), \
     patch.object(identity, "_claude_ancestry", side_effect=ancestry), \
     patch.object(identity.private_process, "terminal", return_value=row["tty"]), \
     patch.object(identity.subprocess, "run", side_effect=AssertionError("setuid /bin/ps must not run")), \
     patch.object(advisory.maintenance, "caller_matches", return_value=True):
    binding = identity.capture_executor(live, state=state)
    assert binding["native_config_root"] == str(root)
    assert binding["transcript"]["path"] == str(log)
    state["executor_identity"] = binding
    assert identity.check_live(binding) == p
    proof = identity.authenticate(state)
    assert proof["session_id"] == sid and proof["episode_id"] == state["episode_id"]
    assert advisory.check_executor(binding, dict(session_id=sid, transcript_path=str(log)))
    old_style = advisory.capture_target(surface, workspace, pane)
    assert old_style["native_config_root"] == str(root)
    assert old_style["transcript"]["path"] == str(log)
    assert advisory.check_executor(old_style)
    # Neither another private lane nor the production root is this original
    # consumer, even with the same session/process labels.
    for changed in (OTHER_ROOT, str(Path.home() / ".claude")):
        bad = copy.deepcopy(binding)
        bad["native_config_root"] = changed
        try:
            identity.check_live(bad)
        except (ValueError, native.NativeDeliveryError):
            pass
        else:
            raise AssertionError("foreign consumer root accepted")
    original = copy.deepcopy(p)
    p["env"]["CLAUDE_CONFIG_DIR"] = OTHER_ROOT
    try:
        identity.check_live(binding)
    except (ValueError, native.NativeDeliveryError):
        pass
    else:
        raise AssertionError("changed process configuration accepted")
    p.clear(); p.update(original)
    raw = log.read_bytes()
    log.write_bytes(raw + b"{}\n")
    assert identity.check_live(binding) == p
    log.write_bytes(b" " + raw[1:])
    try:
        identity.check_live(binding)
    except native.NativeDeliveryError:
        pass
    else:
        raise AssertionError("changed original journal accepted")
print("ORIGINAL_INQUIRY_CAPTURE_CONSUME_ADVISORY_PRIVATE_ROOT_PASS")
'''
        code = code.replace("CONTROLLER", repr(str(Path(self.spec["controller"]) / "scripts")))
        code = code.replace("NATIVE_ROOT", repr(self.spec["clients"]["claude_a"]["native_root"]))
        code = code.replace("OTHER_ROOT", repr(self.spec["clients"]["claude_b"]["native_root"]))
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
            sys.executable, "-I", "-B", "-c", code], text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORIGINAL_INQUIRY_CAPTURE_CONSUME_ADVISORY_PRIVATE_ROOT_PASS", result.stdout)

    def test_lane_sandbox_denies_setuid_ps_but_the_kernel_terminal_reader_works(self):
        # u13: both original Claude inquiry starts failed here before registration.
        # No mocks: the genuine lane profile, the generated controller modules.
        code = r'''
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, CONTROLLER)
import cmux_daemon_identity as kernel
import mac_upgrade_process_inventory as private_process
import executor_reask_identity as identity
import executor_reply_advisory as advisory
try:
    subprocess.run(["/bin/ps", "-p", str(os.getpid()), "-o", "tty="], capture_output=True, check=True, timeout=3)
except PermissionError:
    denied = True
else:
    denied = False
assert denied, "lane profile no longer denies setuid /bin/ps; this control is stale"
tty = private_process.terminal(kernel.process(os.getpid()))
assert isinstance(tty, str) and tty, tty
for module in (identity, advisory):
    assert "/bin/ps" not in Path(module.__file__).read_text(), module.__file__
print("LANE_SETUID_PS_DENIED_KERNEL_TTY_PASS", tty)
'''.replace("CONTROLLER", repr(str(Path(self.spec["controller"]) / "scripts")))
        for lane in ("claude_a", "claude_b"):
            with self.subTest(lane=lane):
                result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"][lane + "_sandbox"]["path"],
                    sys.executable, "-I", "-B", "-c", code], text=True, capture_output=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("LANE_SETUID_PS_DENIED_KERNEL_TTY_PASS", result.stdout)

    def test_original_codex_consumer_requires_exact_private_standalone_foreground_proof(self):
        code = r'''
import copy, json, sys, uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, CONTROLLER)
import cmux_native_delivery as native
import executor_reask_identity as identity
import executor_reask as reask
provider, sid = "codex", str(uuid.uuid4())
surface, workspace, pane = [str(uuid.uuid4()).upper() for _ in range(3)]
root = Path(NATIVE_ROOT)
log = root / "sessions" / ("rollout-offline-" + sid + ".jsonl")
log.parent.mkdir(parents=True, exist_ok=True)
log.write_text(json.dumps(dict(type="session_meta", payload=dict(id=sid))) + "\n")
process = dict(pid=77805, ppid=1, birth=[123, 456], executable="/offline/codex",
    file_identity=[1, 2, 3, 4], env=dict(HOME=str(Path.home().resolve()), CODEX_HOME=str(root),
                                      CMUX_SURFACE_ID=surface, CMUX_WORKSPACE_ID=workspace))
row = dict(target_surface_uuid=surface, workspace_uuid=workspace, target_pane_uuid=pane, tty="ttys998")
proof = dict(source="private-standalone-native-client-v1", daemon_pid=None,
    pid=process["pid"], birth=process["birth"], executable=process["executable"],
    tty=row["tty"], surface_uuid=surface, session=sid, foreground=dict(thread_id=sid), argv_sha256="a" * 64)
caller = dict(caller=dict(surface_id=surface, workspace_id=workspace, pane_id=pane, surface_type="terminal"))
state = dict(caller_surface_uuid=surface, workspace_uuid=workspace, supervisor=str(uuid.uuid4()).upper(),
             task_id="same-private-task", episode_id="original-episode", controller=reask.controller())
with patch.object(native, "_tree_row", return_value=row), \
     patch.object(native, "_target_process", return_value=process), \
     patch.object(native.identity, "process", return_value=process), \
     patch.object(native, "_provider", return_value=provider), \
     patch.object(native, "_live_session", return_value=sid), \
     patch.object(identity.private_process, "terminal", return_value=row["tty"]), \
     patch.object(identity.subprocess, "run", side_effect=AssertionError("setuid /bin/ps must not run")), \
     patch.object(identity.workspace, "caller_snapshot", side_effect=lambda: (caller, {}, {}, copy.deepcopy(proof))):
    binding = identity.capture_executor(dict(caller_surface_uuid=surface, workspace_uuid=workspace,
                                             caller_pane_uuid=pane), state=state)
    state["executor_identity"] = binding
    assert binding["native_config_root"] == str(root)
    assert identity.authenticate(state)["session_id"] == sid
    original = copy.deepcopy(proof)
    for key, value in (("source", "unknown-private-client"), ("daemon_pid", 100),
                       ("pid", process["pid"] + 1), ("birth", [123, 457]),
                       ("foreground", dict(thread_id=str(uuid.uuid4()))), ("argv_sha256", "bad")):
        proof[key] = value
        try:
            identity.authenticate(state)
        except ValueError:
            pass
        else:
            raise AssertionError("foreign or changed foreground consumer proof accepted: " + key)
        proof.clear(); proof.update(copy.deepcopy(original))
    assert identity.authenticate(state)["session_id"] == sid
print("ORIGINAL_CODEX_PRIVATE_FOREGROUND_CONSUMER_PASS")
'''
        code = code.replace("CONTROLLER", repr(str(Path(self.spec["controller"]) / "scripts")))
        code = code.replace("NATIVE_ROOT", repr(self.spec["clients"]["codex"]["native_root"]))
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
            sys.executable, "-I", "-B", "-c", code], text=True, capture_output=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORIGINAL_CODEX_PRIVATE_FOREGROUND_CONSUMER_PASS", result.stdout)

    def test_original_registered_inquiry_cli_consumes_exact_body_and_rearms_twice(self):
        code = r'''
import copy, io, json, os, sys, uuid
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, CONTROLLER)
import cmux_native_delivery as native
import executor_reask_identity as identity
import executor_reply_advisory as advisory
import executor_reask as reask
import executor_reask_scheduler as scheduler
import executor_ready as ready
import executor_proactive_hook as proactive
provider, sid = "claude", str(uuid.uuid4())
surface, supervisor, workspace, pane = [str(uuid.uuid4()).upper() for _ in range(4)]
root = Path(NATIVE_ROOT)
log = root / "projects" / (sid + ".jsonl")
log.parent.mkdir(parents=True, exist_ok=True)
log.write_text(json.dumps(dict(type="assistant", sessionId=sid,
    message=dict(role="assistant", content=[]))) + "\n")
process = dict(pid=77804, ppid=1, birth=[123, 456], executable="/offline/claude",
    file_identity=[1, 2, 3, 4], env=dict(HOME=str(Path.home().resolve()), CLAUDE_CONFIG_DIR=str(root),
                                      CMUX_SURFACE_ID=surface, CMUX_WORKSPACE_ID=workspace))
row = dict(target_surface_uuid=surface, workspace_uuid=workspace, target_pane_uuid=pane, tty="ttys998")
live = dict(caller_surface_uuid=surface, target_surface_uuid=supervisor, workspace_uuid=workspace,
            caller_pane_uuid=pane, target_pane_uuid=pane)
auth = ready.state_root() / "continuity-test-authorization.txt"
auth.write_text("Continue this isolated original task; require complete reply consumption before rearm.")
class HostBridge:
    def pin_workspace(self, target, **kwargs):
        assert target == supervisor
        return live.copy()
    def __getattr__(self, name):
        raise AssertionError("no external input in file-only inquiry test: " + name)
with patch.object(ready, "_bridge", return_value=HostBridge()), \
     patch.object(native, "_tree_row", return_value=row), \
     patch.object(native, "_target_process", return_value=process), \
     patch.object(native.identity, "process", return_value=process), \
     patch.object(native, "_provider", return_value=provider), \
     patch.object(native, "_live_session", return_value=sid), \
     patch.object(identity, "_claude_ancestry", return_value=dict(kind="OFFLINE_EXTERNAL_ANCESTRY")), \
     patch.object(identity.private_process, "terminal", return_value=row["tty"]), \
     patch.object(identity.subprocess, "run", side_effect=AssertionError("setuid /bin/ps must not run")), \
     patch.object(advisory.maintenance, "caller_matches", return_value=True):
    clock = 2000000000.
    original = scheduler.start(live, task_id="same-private-task", now=clock, authenticate_executor=True,
        continuing=True, continuation=dict(enabled=True, overall_task="isolated registered continuity",
        owner=supervisor, authorization_file=str(auth), reservation_inquiries=True, proactive_hook=True))
    assert original["state"] == reask.WAITING
    assert scheduler.registration_observation(original) == "REGISTERED"
    assert original["executor_identity"]["native_config_root"] == str(root)
    previous = None
    for index in range(2):
        now = clock + index * 60 + 1
        observation = scheduler.sweep(now=now)
        assert observation["status"] == "SWEPT" and observation["input_operations"] == 0, observation
        request = ready.read_json(reask.inbox_path(reask.load(surface)))
        assert request["marker"] != previous
        previous = request["marker"]
        payload = dict(session_id=sid, transcript_path=str(log), hook_event_name="Stop")
        with patch.dict(os.environ, {"CMUX_SURFACE_ID": surface}):
            prompt = proactive.decide(payload, now=now, clock=lambda: now)
        assert prompt["decision"] == "block"
        assert "--max-seconds 60" in prompt["reason"]
        body = dict(request["reply_template"], status="ACK", trigger="完整正文：继续同一原任务。" * 200,
                    next_due_epoch=now + 60, deadline_epoch=now + 120)
        ready.write_json(Path(request["reply_mailbox"]), body)
        scheduler.sweep(now=now + 1)
        answered = reask.load(surface)
        assert answered["state"] == reask.ANSWERED
        assert answered["rearm_count"] == index
        with patch.dict(os.environ, {"CMUX_SURFACE_ID": surface}):
            prompt = proactive.decide(payload, now=now + 2, clock=lambda: now + 2)
        assert prompt["decision"] == "block"
        assert "COMPLETE" in prompt["reason"] and answered["reply"]["body_sha256"] in prompt["reason"]
        assert reask.load(surface)["rearm_count"] == index  # hook never consumes for the model
        # No matching hash, no consumption receipt or rearm.
        try:
            reask.main(["consume", "--caller-uuid", surface, "--reply-sha256", "0" * 64])
        except ValueError:
            pass
        else:
            raise AssertionError("wrong body SHA was consumed")
        assert reask.load(surface)["rearm_count"] == index
        complete_body = ready.read_json(reask.state_path(surface))["reply"]["body"]
        assert complete_body == body
        output = io.StringIO()
        with redirect_stdout(output):
            rc = reask.main(["consume", "--caller-uuid", surface, "--reply-sha256",
                             answered["reply"]["body_sha256"]])
        result = json.loads(output.getvalue())
        assert rc == 0 and result["consume_result"]["consumed"] is True
        assert result["consume_result"]["input_operations"] == 0
        receipt = ready.read_json(Path(result["consume_result"]["receipt"]))
        assert receipt["body"] == body and receipt["executor_authentication"]["session_id"] == sid
        assert reask.load(surface)["rearm_count"] == index + 1
        assert reask.load(surface)["state"] == reask.WAITING
        assert reask.load(surface)["episode_id"] == original["episode_id"]
    assert reask.load(surface)["terminal_sends"] == 0
print("ORIGINAL_REGISTERED_CLI_TWO_FILE_CONSUMPTIONS_REARM_PASS")
'''
        code = code.replace("CONTROLLER", repr(str(Path(self.spec["controller"]) / "scripts")))
        code = code.replace("NATIVE_ROOT", repr(self.spec["clients"]["claude_a"]["native_root"]))
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
            sys.executable, "-I", "-B", "-c", code], text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORIGINAL_REGISTERED_CLI_TWO_FILE_CONSUMPTIONS_REARM_PASS", result.stdout)


class InquiryHookConfigurationTests(unittest.TestCase):
    def test_registration_is_versioned_opt_in_not_truthy_or_an_alias(self):
        self.assertFalse(continuity_registration_enabled({}))
        self.assertTrue(continuity_registration_enabled({"continuity_registration": "private-current-v1"}))
        for value in (None, True, False, 1, "private-current-v2", "installed"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                continuity_registration_enabled({"continuity_registration": value})
    def test_private_hook_retains_reviewed_proactive_timeout_only_if_original_has_it(self):
        for enabled in (False, True):
            manager = ("GUARDS = {'cmux_executor_idle_guard': 'Stop'}\n"
                       "def normalize(item, name):\n"
                       + ("    if name == 'cmux_executor_idle_guard':\n"
                          "        item['timeout'] = 75\n" if enabled else "    pass\n"))
            advisory = "WORKFLOW_HOOKS = frozenset({'cmux_executor_idle_guard.py'})\n"
            with patch("upgrade_materialize.read_regular", side_effect=[manager.encode(), advisory.encode()]):
                hooks = hook_config(Path("/offline/source"), Path("/offline/controller"), "/offline/python")
            item = hooks["hooks"]["Stop"][0]["hooks"][0]
            self.assertEqual(item.get("timeout"), 75 if enabled else None)
            self.assertEqual(len(hooks["hooks"]["Stop"]), 1)

    def test_actual_installer_timeout_unknown_variants_are_refused_not_dropped(self):
        manager, advisory = source("manage_install.py"), b"WORKFLOW_HOOKS = frozenset({'cmux_executor_idle_guard.py'})\n"
        for raw in (manager, manager + "\n# semantic-preserving comment\n"):
            with patch("upgrade_materialize.read_regular", side_effect=[raw.encode(), advisory]):
                hooks = hook_config(Path("/offline/source"), Path("/offline/controller"), "/offline/python")
            stops = [h for group in hooks["hooks"]["Stop"] for h in group["hooks"]
                     if "cmux_executor_idle_guard.py" in h["command"]]
            self.assertEqual(len(stops), 1)
            self.assertEqual(stops[0]["timeout"], 75)
        for raw in (manager.replace("item['timeout'] = 75", "item['timeout'] = 74"),
                    manager.replace("name == 'cmux_executor_idle_guard'", "name == 'another_hook'"),
                    manager.replace("item['timeout'] = 75", "item['timeout']: int = 75"),
                    manager.replace("item['timeout'] = 75", "item['timeout'] += 75"),
                    manager + "\nitem['timeout'] = 75\n"):
            with self.subTest(timeout=next((line.strip() for line in raw.splitlines()
                                           if "timeout" in line and "item" in line), "unknown")), \
                    patch("upgrade_materialize.read_regular", side_effect=[raw.encode(), advisory]), \
                    self.assertRaises(ContractError):
                hook_config(Path("/offline/source"), Path("/offline/controller"), "/offline/python")


class InquiryConsumerPortableTests(unittest.TestCase):
    def test_five_additional_exact_sources_transform_but_are_not_a_controller(self):
        paths = {n: Path("/offline/continuity") / n for n in ("state", "controllers", "tmp", "config", "bin")}
        for name in MANIFEST["files"]:
            before = source(name)
            transformed, changes = transform_live(name, before, paths, Path("/offline/continuity/controller"),
                                                   compatibility=True)
            compile(transformed, name, "exec", dont_inherit=True)
            self.assertEqual(source(name), before)
            self.assertIn("live_v2_kernel_boundary", changes)
        self.assertNotIn("mac_harness.py", MANIFEST["files"])
        self.assertNotIn("SKILL.md", MANIFEST["files"])

    def consumer(self, root, *, excised=False, semantic_variation=False):
        raw = source("executor_reask_identity.py")
        if semantic_variation:
            raw += "\n# A comment preserves all original runtime semantics.\n"
        transformed = raw if excised else native_readers("executor_reask_identity.py", raw)
        sid, surface, workspace, pane = [str(uuid.uuid4()) for _ in range(4)]
        native_root = root / "clients/claude_a"
        log = native_root / "projects" / (sid + ".jsonl")
        log.parent.mkdir(parents=True)
        log.write_text(json.dumps(dict(type="assistant", sessionId=sid,
                                     message=dict(role="assistant", content=[]))) + "\n")
        binding, context = dict(native_roots=dict(codex=str(root / "clients/codex"),
            claude_a=str(native_root), claude_b=str(root / "clients/claude_b"))), \
            dict(account_home=str(Path.home().resolve()))
        native_defs = definitions(native_readers("cmux_native_delivery.py", native_source("cmux_native_delivery.py")),
            {"_native_root", "_transcript_path"}, dict(Path=Path, private_roots=private_roots,
                                                       NativeDeliveryError=original_native.NativeDeliveryError))
        process = dict(pid=77803, ppid=1, birth=[123, 456], executable="/offline/claude",
            file_identity=[1, 2, 3, 4], env=dict(HOME=context["account_home"], CLAUDE_CONFIG_DIR=str(native_root),
                                                CMUX_SURFACE_ID=surface, CMUX_WORKSPACE_ID=workspace))
        row = dict(target_surface_uuid=surface, workspace_uuid=workspace, target_pane_uuid=pane, tty="ttys998")
        native = SimpleNamespace(_tree_row=lambda *a: row, _target_process=lambda *a: process,
            _provider=lambda *a: "claude", _live_session=lambda *a, **kw: sid,
            _native_root=native_defs["_native_root"], _transcript_path=native_defs["_transcript_path"],
            _public_process=original_native._public_process, _pin_file=original_native._pin_file,
            _open=original_native._open, _check_file=original_native._check_file,
            identity=SimpleNamespace(process=lambda *a, **kw: process))
        ns = definitions(transformed, {"capture_executor", "check_live", "_same_uuid"},
            dict(native=native, ready=SimpleNamespace(_bridge=lambda: None), Path=Path, uuid=uuid, time=time,
                subprocess=SimpleNamespace(run=lambda *a, **kw: self.fail("setuid /bin/ps must not run")),
                private_process=SimpleNamespace(terminal=lambda p: row["tty"] if p is process else self.fail("foreign terminal subject")),
                SCHEMA="executor-reask-consumer-identity-v1", _episode=lambda s: s.copy(),
                _caller=lambda b: dict(kind="OFFLINE_EXTERNAL_ANCESTRY"),
                process_root=private_roots.process_root, binding_root=private_roots.binding_root))
        state = dict(caller_surface_uuid=surface, workspace_uuid=workspace)
        with patch.object(private_roots, "configuration", return_value=(binding, context)):
            result = ns["capture_executor"](dict(state, caller_pane_uuid=pane), state=state)
            self.assertEqual(result["native_config_root"], str(native_root))
            self.assertEqual(result["transcript"]["path"], str(log))
            self.assertEqual(ns["check_live"](result), process)
            for key, value in (("native_config_root", str(root / "clients/claude_b")),
                               ("native_config_root", str(Path.home() / ".claude"))):
                bad = copy.deepcopy(result)
                bad[key] = value
                with self.assertRaises((ValueError, original_native.NativeDeliveryError)):
                    ns["check_live"](bad)
        return result

    def test_original_consumer_control_semantic_variation_and_all_adapter_excision(self):
        before = source("executor_reask_identity.py")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            self.consumer(base / "control")
            self.consumer(base / "same-semantics", semantic_variation=True)
            # Execute the complete original consumer entrypoint with every
            # private consumer adaptation absent, not a mocked checker label.
            try:
                self.consumer(base / "excised", excised=True)
            except TypeError as exc:
                # Neither a different missing argument nor an unrelated
                # dependency's TypeError may satisfy the excision witness.
                self.assertEqual(str(exc),
                    "_transcript_path() missing 1 required positional argument: 'process'")
                frame = exc.__traceback__
                while frame.tb_next is not None:
                    frame = frame.tb_next
                self.assertEqual(
                    (frame.tb_frame.f_code.co_filename, frame.tb_frame.f_code.co_name),
                    ("pinned-upstream-entrypoints", "capture_executor"))
            else:
                self.fail("complete consumer excision did not reject the missing process argument")
            self.consumer(base / "restored")
        self.assertEqual(source("executor_reask_identity.py"), before)

    def test_double_patch_and_unknown_consumer_source_shapes_fail_closed(self):
        for name in ("executor_reask_identity.py", "executor_reply_advisory.py"):
            raw = source(name)
            patched = native_readers(name, raw)
            with self.subTest(file=name), self.assertRaises(ContractError):
                native_readers(name, patched)
            changed = raw.replace("native._transcript_path(", "native.unknown_transcript_path(")
            with self.subTest(file=name), self.assertRaises(ContractError):
                native_readers(name, changed)


if __name__ == "__main__":
    unittest.main()
