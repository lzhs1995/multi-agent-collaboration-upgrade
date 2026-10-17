"""Exercise private startup wiring without creating a workspace or model client."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_client_isolation import LANES, SANDBOX
from upgrade_experiment import arm_paths, init_experiment, validate_context
from upgrade_hook_observer import hook_parents
import upgrade_input_gate as gate
from upgrade_live_integrity import artifact_ref
from upgrade_live_materialize import materialize_live
from upgrade_live_patch import live_gates
import upgrade_native_roots as roots
from upgrade_startup_contract import validate_startup

SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
WRAPPERS = Path("/Applications/cmux.app/Contents/Resources/bin")


@unittest.skipUnless(SOURCE.is_dir() and WRAPPERS.is_dir(), "requires fixed local MAC and cmux wrappers")
class MaterializedStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        cls.root = Path(cls.tmp.name).resolve()
        cls.context_ref = init_experiment(cls.root / "l", "focused-startup", SOURCE)
        # Small executable pins keep these offline tests independent of provider credentials.
        cls.binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
        cls.binaries.update({p + "_wrapper": WRAPPERS / ("cmux-" + p + "-wrapper")
                             for p in ("codex", "claude")})
        cls.runtime_ref = materialize_live(cls.context_ref, "B", binaries=cls.binaries,
                                           provider_configuration=({}, {}))
        cls.runtime = json_ref(cls.runtime_ref)
        cls.ref = cls.runtime["startup"]
        cls.spec, cls.binding = validate_startup(cls.ref)
        cls.paths = arm_paths(validate_context(cls.context_ref), "B")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_full_materialization_has_distinct_roots_and_real_observer_marker_only(self):
        self.assertEqual(set(self.spec["clients"]), set(LANES))
        for lane, client in self.spec["clients"].items():
            self.assertNotIn("HOME", client["environment"])
            self.assertEqual(client["environment"]["UPGRADE_CLIENT_LANE"], lane)
        marker = Path(self.spec["pins"]["codex/credential-observations/enabled-v1"]["path"])
        self.assertEqual(marker.read_bytes(), b"ccc-request-credentials-v1\n")
        self.assertEqual(marker.stat().st_mode & 0o777, 0o400)
        self.assertEqual(marker.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(marker.parent.glob("client-*-thread.json")), [])
        self.assertLessEqual(len(os.fsencode(self.spec["rpc"]["path"])), 103)
        self.assertIn("--remote", self.spec["clients"]["codex"]["argv"])
        self.assertFalse((self.paths["state"] / "startup").exists())
        self.assertFalse(Path(self.spec["adoption_path"]).exists())

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_materialized_inventory_and_controlling_tty_inside_seatbelt(self):
        controller = Path(self.spec["controller"]) / "scripts"
        for name in ("cmux_daemon_identity.py", "cmux_native_delivery.py"):
            self.assertNotIn("/bin/ps", (controller / name).read_text())
            self.assertIn("mac_upgrade_process_inventory", (controller / name).read_text())
        outside = self.root / "inventory-outside-sentinel"
        outside.write_text("unchanged")
        code = f'''
import os, sys
from pathlib import Path
sys.path.insert(0, {str(controller)!r})
import cmux_daemon_identity as identity
import mac_upgrade_process_inventory as inventory
assert inventory.__file__ == {str(controller / 'mac_upgrade_process_inventory.py')!r}
p = identity.process(os.getpid())
rows = inventory.inventory(lambda name: Path(name).name == Path(p['executable']).name)
assert sum(int(pid) == os.getpid() for pid, path in rows) == 1
expected_tty = os.environ['TEST_CONTROLLING_TTY']
assert inventory.terminal(p) == expected_tty
try:
    inventory.terminal(dict(p, birth=[1, 2]))
except identity.IdentityError:
    pass
else:
    raise AssertionError('changed birth accepted')
# Exercise the materialized delivery gate with real kernel identity/TTY.
import cmux_native_delivery as delivery
delivery.private_process.inventory = lambda candidate: [[str(os.getpid()), p['executable']]]
delivery.private_roots.candidate_process_name = lambda path: True
delivery._provider = lambda proc: 'claude'
row = dict(target_surface_uuid='', workspace_uuid='', tty='ttys999999')
try:
    delivery._target_process(row)
except delivery.NativeDeliveryError as error:
    expected_error = 'NATIVE_TARGET_PROCESS_NOT_UNIQUE' if expected_tty == '?' else 'NATIVE_PROCESS_TTY_MISMATCH'
    assert str(error) == expected_error
else:
    raise AssertionError('wrong controlling TTY accepted')
try:
    Path({str(outside)!r}).write_text('escaped')
except PermissionError:
    pass
else:
    raise AssertionError('private write boundary weakened')
print('MATERIALIZED_LIBPROC_TTY_PASS', flush=True)
'''
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CMUX_", "CODEX_"))}
        command = [str(SANDBOX), "-f", self.spec["pins"]["codex_sandbox"]["path"],
                   sys.executable, "-I", "-B", "-c", code]
        for with_tty in (False, True):
            # cmux establishes the controlling PTY before applying Seatbelt.
            # Reproduce that boundary without touching a live cmux surface.
            launcher = f'''
import os, fcntl, termios
from pathlib import Path
os.environ['TEST_CONTROLLING_TTY'] = '?'
if {with_tty!r}:
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    os.set_inheritable(master, True)
    os.set_inheritable(slave, True)
    os.environ['TEST_CONTROLLING_TTY'] = Path(os.ttyname(slave)).name
os.execve({command[0]!r}, {command!r}, os.environ)
'''
            result = subprocess.run([sys.executable, "-I", "-B", "-c", launcher],
                capture_output=True, text=True, env=env, start_new_session=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("MATERIALIZED_LIBPROC_TTY_PASS", result.stdout)
        self.assertEqual(outside.read_text(), "unchanged")

    def test_changed_argv_environment_rpc_or_marker_fails(self):
        path = Path(self.ref["path"])
        original = path.read_bytes()
        mutations = [
            lambda x: x["clients"]["codex"]["argv"].append("--resume"),
            lambda x: x["clients"]["claude_a"]["environment"].update(HOME="/tmp/false-home"),
            lambda x: x["rpc"].update(path="/tmp/production.sock"),
            lambda x: x["clients"]["claude_b"].update(session_id=x["clients"]["claude_a"]["session_id"]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = copy.deepcopy(self.spec)
                mutate(value)
                try:
                    path.chmod(0o600)
                    path.write_text(json.dumps(value))
                    with self.assertRaises((ContractError, ValueError)):
                        validate_startup(file_ref(path))
                finally:
                    path.write_bytes(original)
                    path.chmod(0o444)
        marker = Path(self.spec["pins"]["codex/credential-observations/enabled-v1"]["path"])
        original_marker = marker.read_bytes()
        try:
            marker.chmod(0o600)
            marker.write_bytes(b"fabricated\n")
            with self.assertRaisesRegex(ValueError, "ARTIFACT_PIN_CHANGED"):
                validate_startup(self.ref)
        finally:
            marker.write_bytes(original_marker)
            marker.chmod(0o400)

    def test_long_temp_path_refuses_before_materializing_arm_artifacts(self):
        context_ref = init_experiment(self.root / ("long-root-" * 3), "long-startup", SOURCE)
        paths = arm_paths(validate_context(context_ref), "B")
        self.assertTrue(all(not p.exists() for p in paths.values()))
        with self.assertRaisesRegex(ContractError, "TMP_PATH_TOO_LONG"):
            materialize_live(context_ref, "B", binaries=self.binaries, provider_configuration=({}, {}))
        self.assertTrue(all(not p.exists() for p in paths.values()))

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_real_server_stdin_and_socket_under_private_audit_and_kernel_boundary(self):
        controller = str(Path(self.spec["controller"]) / "scripts")
        outside = self.root / "server-outside-sentinel"
        outside.write_text("unchanged")
        child_code = '''
import socket, sys
from pathlib import Path
assert sys.stdin.buffer.read() == b""
outside = Path(sys.argv[2])
try:
    outside.write_text("child-pollution")
except PermissionError:
    pass
else:
    raise AssertionError("child escaped kernel boundary")
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
    server.bind(sys.argv[1])
    server.listen(1)
    server.settimeout(10)
    with server.accept()[0] as connection:
        assert connection.recv(16) == b"finish"
print("PRIVATE_SERVER_CHILD_PASS", flush=True)
'''
        code = f'''
import json, os, socket, sys
from pathlib import Path
sys.path.insert(0, {controller!r})
from mac_upgrade_live_entry import start_server
import mac_upgrade_controller_scope as scope
from mac_upgrade_common import ContractError
assert scope._installed
try:
    os.open(os.devnull, os.O_RDWR)
except PermissionError as error:
    assert 'UPGRADE_WRITE_OUTSIDE_PRIVATE_STATE:/dev/null' in str(error)
else:
    raise AssertionError('original write audit was relaxed')
spec = {self.spec!r}
spec['rpc']['argv'] = [sys.executable, '-I', '-B', '-c', {child_code!r},
                        spec['rpc']['path'], {str(outside)!r}]
binding = {self.binding!r}
env = dict(os.environ, CMUX_SURFACE_ID={str(uuid.uuid4())!r}, CMUX_WORKSPACE_ID={str(uuid.uuid4())!r})
control = Path(binding['state_root']) / 'startup'
try:
    start_server({self.ref!r}, spec, binding, env)
    result = json.loads((control / 'codex-server-result.json').read_text())
    assert result['status'] == 'SOCKET_CREATED' and result['input_operations'] == 0
    try:
        start_server({self.ref!r}, spec, binding, env)
    except ContractError as error:
        assert 'ORIGINAL_CONTROL_SOCKET_ALREADY_EXISTS' in str(error)
    else:
        raise AssertionError('server was restarted')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(spec['rpc']['path'])
        connection.sendall(b'finish')
finally:
    record = control / 'codex-server.json'
    if record.exists():
        pid = json.loads(record.read_text())['pid']
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
assert 'PRIVATE_SERVER_CHILD_PASS' in (control / 'codex-server.log').read_text()
assert Path({str(outside)!r}).read_text() == 'unchanged'
print('AUDITED_PRIVATE_SERVER_SOCKET_PASS')
'''
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["codex_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AUDITED_PRIVATE_SERVER_SOCKET_PASS", result.stdout)
        self.assertEqual(outside.read_text(), "unchanged")

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_controller_and_adapter_share_process_without_module_replacement(self):
        adapter = str(Path(__file__).resolve().parent)
        controller = str(Path(self.spec["controller"]) / "scripts")
        code = f'''
import sys
from pathlib import Path
sys.path.insert(0, {adapter!r})
import orchestration_common, orchestration_native, orchestration_campaign, orchestration_run
from orchestration_transport import PinnedRuntime
original = orchestration_common
runtime = PinnedRuntime(orchestration_common.json_ref({self.runtime_ref!r}))
with runtime.modules('cmux_bridge', 'cmux_native_delivery', 'mac_harness',
                     'mac_upgrade_common', 'executor_reask_lifecycle', 'executor_reask') as modules:
    cmux_bridge, cmux_native_delivery, mac_harness, mac_upgrade_common, _, _ = modules
    assert cmux_native_delivery.private_roots.__file__.startswith({controller!r})
assert sys.modules['orchestration_common'] is original
assert mac_upgrade_common is not original
assert str(Path(cmux_bridge.__file__).parent) == {controller!r}
assert str(Path(original.__file__).parent) == {adapter!r}
print('PRIVATE_CONTROLLER_AND_ADAPTER_COEXIST')
'''
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PRIVATE_CONTROLLER_AND_ADAPTER_COEXIST", result.stdout)


class InputGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.config = self.root / "config"
        self.control = self.root / "evidence/live-control"
        self.config.mkdir()
        self.control.mkdir(parents=True)
        self.startup = write_json(self.config / "startup-v2.json", {"fixture": True})
        self.workspace = str(uuid.uuid4())
        self.sessions = {}
        for index, lane in enumerate(LANES):
            self.sessions[lane] = dict(provider="codex" if lane == "codex" else "claude",
                session_id=str(uuid.uuid4()), surface_uuid=str(uuid.uuid4()), pane_uuid=str(uuid.uuid4()),
                workspace_uuid=self.workspace, process=dict(pid=9000 + index, birth=[1, index],
                executable="/fixture/client", file_identity=[1, 2, 3, 4]),
                transcript=dict(path="/fixture/log", device=1, inode=2))
        self.binding = dict(config_root=str(self.config), evidence_root=str(self.root / "evidence"),
            native_policy="private-cli-roots-v1",
            native_roots={lane: str(self.root / lane) for lane in LANES},
            isolation=dict(denied_probes=["/fixture/production"], write_probes=["/fixture/private"]))
        self.proof = dict(workspace_uuid=self.workspace,
            caller_surface_uuid=self.sessions["codex"]["surface_uuid"],
            target_surface_uuid=self.sessions["claude_a"]["surface_uuid"],
            target_pane_uuid=self.sessions["claude_a"]["pane_uuid"])
        self.addCleanup(patch.stopall)
        patch.object(gate, "configuration", return_value=(self.binding, {})).start()
        self.kernel = patch.object(gate, "require_kernel_boundary").start()
        patch.object(gate, "process_root", side_effect=lambda provider, p: Path(p["root"])).start()
        self.identity = types.SimpleNamespace(process=self.process)
        patch.dict(sys.modules, {"cmux_daemon_identity": self.identity}).start()

    def process(self, pid):
        lane, session = next((lane, s) for lane, s in self.sessions.items() if s["process"]["pid"] == pid)
        return dict(session["process"], root=self.binding["native_roots"][lane],
                    env=dict(CMUX_SURFACE_ID=session["surface_uuid"], CMUX_WORKSPACE_ID=self.workspace))

    def adopt(self):
        return write_json(self.control / "adoption.json", dict(schema="upgrade-live-adoption-v2",
            startup=self.startup, verified=True, sessions=self.sessions))

    def test_missing_adoption_or_stop_is_zero_input(self):
        with self.assertRaises((OSError, ContractError)):
            gate.check_input(self.proof)
        self.kernel.assert_not_called()
        self.adopt()
        (self.control / "stop.json").write_text("stop")
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            gate.check_input(self.proof)
        self.kernel.assert_not_called()

    def test_external_endpoint_and_changed_native_receiver_are_rejected(self):
        self.adopt()
        with self.assertRaisesRegex(ContractError, "ENDPOINT_NOT_ENROLLED"):
            gate.check_input(dict(self.proof, target_surface_uuid=str(uuid.uuid4())))
        self.kernel.assert_not_called()
        session = self.sessions["claude_a"]
        native = {k: copy.deepcopy(session[k]) for k in ("provider", "session_id", "process", "transcript")}
        gate.check_input(self.proof, native_binding=native)
        self.assertEqual(self.kernel.call_count, 2)
        native["transcript"]["inode"] += 1
        with self.assertRaisesRegex(ContractError, "NATIVE_RECEIVER_CHANGED"):
            gate.check_input(self.proof, native_binding=native)

    def test_stop_arriving_during_recheck_is_not_ignored(self):
        self.adopt()
        self.kernel.side_effect = lambda *a, **k: (self.control / "stop.json").write_text("stop")
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            gate.check_input(self.proof)


class NativeRootTests(unittest.TestCase):
    def test_version_named_claude_requires_real_pin_and_private_process_root(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "2.1.226"
            path.write_bytes(b"offline executable fixture")
            pin = artifact_ref(path)
            st = path.stat()
            binding = dict(native_roots={"claude_a": str(path.parent), "claude_b": str(path.parent / "b")})
            proc = dict(pid=1, birth=[1, 2], executable=str(path),
                file_identity=[st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns],
                env={"HOME": str(Path.home()), "CLAUDE_CONFIG_DIR": str(path.parent)})
            with patch.object(roots, "executable_pins", return_value={"claude": pin}), \
                 patch.object(roots, "configuration", return_value=(binding, {"account_home": str(Path.home())})):
                self.assertTrue(roots.candidate_process_name(str(path)))
                self.assertEqual(roots.executable_provider(proc), "claude")
                self.assertEqual(roots.transcript_root("claude", process=proc), path.parent / "projects")
                with self.assertRaisesRegex(ValueError, "PROCESS_ROOT_MISMATCH"):
                    roots.executable_provider(dict(proc, env={"HOME": str(Path.home())}))
                with self.assertRaisesRegex(ValueError, "EXACT_NATIVE_ROOT_SOURCE"):
                    roots.transcript_root("claude")


class AutomaticHookTests(unittest.TestCase):
    def test_direct_client_or_exact_hook_shell_only(self):
        command = "/pinned/python -I -B /pinned/hook --hook /pinned/original.py"
        client = dict(pid=10, ppid=1, birth=[1, 2], executable="/pinned/client", file_identity=[1, 2], argv=[])
        shell = dict(pid=20, ppid=10, birth=[2, 3], executable="/bin/sh", file_identity=[2, 3],
                     argv=["/bin/sh", "-c", command])
        launches = {"claude_a": {"pid": 10, "birth": [1, 2]}}
        identity = types.SimpleNamespace(process=lambda pid, **k: {10: client, 20: shell}[pid])
        with patch.dict(sys.modules, {"cmux_daemon_identity": identity}), patch("os.getppid", return_value=20):
            self.assertEqual(hook_parents(command, launches)[1], "claude_a")
            shell["argv"][-1] = "python manual-hook.py"
            self.assertIsNone(hook_parents(command, launches)[1])
        with patch.dict(sys.modules, {"cmux_daemon_identity": identity}), patch("os.getppid", return_value=10):
            self.assertEqual(hook_parents(command, launches)[1], "claude_a")
            launches["claude_a"]["birth"] = [99, 99]
            self.assertIsNone(hook_parents(command, launches)[1])


@unittest.skipUnless(SOURCE.is_dir(), "requires fixed MAC source")
class OriginalInputTests(unittest.TestCase):
    def test_original_once_input_keeps_unknown_without_retry_or_fallback(self):
        # Execute the real send_once body with only its added gate; replace
        # external RPC, not send_once, prepare, validate_request or its journal.
        native = types.SimpleNamespace(NativeDeliveryError=RuntimeError, _validate_binding=lambda *a: None)
        rpc_module = types.SimpleNamespace(DaemonRPC=None)
        sid, turn = str(uuid.uuid4()), str(uuid.uuid4())
        binding = dict(provider="codex", session_id=sid, transcript={"path": "/fixture/original.jsonl"}, identity={})
        for active in (False, True):
            with self.subTest(active=active):
                calls, events = [], []

                class RPC:
                    socket_identity = dict(path="/fixture/c.sock", uid=os.getuid(), peer_uid=os.getuid(),
                                           mode=0o600, device=1, inode=2)
                    def __init__(self, **kwargs): pass
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def request(self, method, params):
                        calls.append((method, params))
                        if method == "thread/read":
                            return {"thread": dict(id=sid, path=binding["transcript"]["path"],
                                canAcceptDirectInput=True, status={"type": "active" if active else "idle"})}
                        if method == "thread/turns/list":
                            return {"data": [dict(id=turn, status="inProgress" if active else "completed")]}
                        self_case.assertEqual(events[-1][0], "NATIVE_INPUT_INTENT")
                        raise TimeoutError("original input outcome unknown")

                self_case = self
                rpc_module.DaemonRPC = RPC
                fake_gate = types.SimpleNamespace(check_input=lambda *a, **k: None)
                with patch.dict(sys.modules, {"cmux_native_delivery": native, "cmux_codex_rpc": rpc_module,
                                             "upgrade_input_gate": fake_gate}):
                    module = types.ModuleType("offline_original_input")
                    text = live_gates("cmux_codex_input.py", (SOURCE / "scripts/cmux_codex_input.py").read_text())
                    exec(compile(text, "offline_original_input", "exec"), module.__dict__)
                    result = module.send_once(binding, "exact  原文", recheck=lambda: None,
                        emit=lambda phase, **kw: events.append((phase, kw)),
                        observe=lambda: {"confirmed": False}, wait_seconds=0)
                mutations = [(m, p) for m, p in calls if m.startswith("turn/")]
                self.assertEqual(len(mutations), 1)
                self.assertEqual(mutations[0][0], "turn/steer" if active else "turn/start")
                expected = dict(threadId=sid, input=[dict(type="text", text="exact  原文")])
                if active:
                    expected["expectedTurnId"] = turn
                self.assertEqual(mutations[0][1], expected)
                self.assertEqual(result["native_rpc_outcome"], "RPC_ERROR_OR_UNKNOWN")
                self.assertFalse(result["confirmed"])
                self.assertEqual(result["retries"], 0)
                self.assertEqual(result["terminal_input_operations"], 0)


if __name__ == "__main__":
    unittest.main()
