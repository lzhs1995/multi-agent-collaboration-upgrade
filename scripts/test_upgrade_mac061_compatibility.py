"""Portable checks of an unchanged MAC 0.4.61 source, not a live adoption claim."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from upgrade_client_isolation import SANDBOX
from upgrade_live_patch import transform_live
from orchestration_common import ContractError
import test_upgrade_live_startup as startup

FIXTURES = Path(__file__).with_name("fixtures") / "mac-0.4.61"
COMPLETE_SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.61-d14ae7c2b757/source"


def source():
    name = "cmux_native_delivery.py"
    raw = (FIXTURES / name).read_bytes()
    pin = json.loads((FIXTURES / "manifest.json").read_bytes())["files"][name]
    if len(raw) != pin["bytes"] or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        raise AssertionError("fixed native source fixture changed")
    return raw.decode("utf-8")


class NativeDeliveryCompatibilityTests(unittest.TestCase):
    def test_current_native_delivery_transforms_for_private_controller(self):
        original = source()
        paths = {name: Path("/offline/mac061") / name
                 for name in ("state", "controllers", "tmp", "config", "bin")}
        transformed, changes = transform_live("cmux_native_delivery.py", original, paths,
            Path("/offline/mac061/controller"), compatibility=True)
        compile(transformed, "cmux_native_delivery.py", "exec", dont_inherit=True)
        self.assertIn("authenticated_private_native_roots", changes)
        self.assertIn("live_v2_kernel_boundary", changes)
        self.assertEqual(source(), original)

    def test_unknown_shell_diagnostic_source_is_refused(self):
        original = source()
        paths = {name: Path("/offline/mac061") / name
                 for name in ("state", "controllers", "tmp", "config", "bin")}
        changed_subject = original.replace("str(shell['pid'])", "str(unknown['pid'])")
        duplicate_target = original + "\ndef _target_process(row):\n    return None\n"
        unscoped_shell = original + "\ndef unscoped():\n    tty = subprocess.run(['/bin/ps', '-p', str(shell['pid']), '-o', 'tty=']).stdout.strip()\n"
        for changed in (changed_subject, duplicate_target, unscoped_shell):
            with self.subTest(shape=changed[-80:]):
                self.assertNotEqual(changed, original)
                with self.assertRaisesRegex((ContractError, ValueError),
                        "native terminal process subject changed|native shell diagnostic function changed"):
                    transform_live("cmux_native_delivery.py", changed, paths,
                        Path("/offline/mac061/controller"), compatibility=True)


@unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists()
                     and COMPLETE_SOURCE.is_dir() and startup.WRAPPERS.is_dir(),
                     "requires complete fixed MAC 0.4.61 and Darwin isolation")
class MaterializedNativeDeliveryTests(unittest.TestCase):
    source_controller = COMPLETE_SOURCE
    continuity_registration = True

    @classmethod
    def setUpClass(cls):
        startup.MaterializedStartupTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        startup.MaterializedStartupTests.tearDownClass.__func__(cls)

    def binding_probe(self, *, include_agent=False, foreign_root=False):
        code = r'''
import ctypes, json, os, struct, sys, time, uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, CONTROLLER)
import cmux_native_delivery as native
assert Path(native.__file__).parent == Path(CONTROLLER)
surface, workspace, pane, caller = [str(uuid.uuid4()).upper() for _ in range(4)]
tty = "ttys073"
device = Path("/dev", tty).lstat().st_rdev & 0xFFFFFFFF
pid, executable = 77801, str(Path("/bin/zsh").resolve(strict=True))
environment = dict(HOME=str(Path.home().resolve()), CMUX_SURFACE_ID=surface,
                   CMUX_WORKSPACE_ID=workspace)
processes = {pid: dict(executable=executable, argv=[executable], env=environment)}
if INCLUDE_AGENT:
    agent, session = 77802, str(uuid.uuid4())
    native_root = Path(NATIVE_ROOT)
    agent_executable = str(Path("/usr/bin/true").resolve(strict=True))
    processes[agent] = dict(executable=agent_executable,
        argv=[agent_executable, "resume", session],
        env=dict(environment, CODEX_HOME=__FOREIGN_CONFIGURATION__ if __FOREIGN_CONFIGURATION__ else str(native_root),
                 CODEX_CLIENT_THREAD_OBSERVER="1"))
    logs = native_root / "sessions" / ("offline-binding-" + session)
    logs.mkdir(parents=True)
    log = logs / ("rollout-offline-" + session + ".jsonl")
    log.write_text(json.dumps(dict(type="session_meta", payload=dict(id=session))) + "\n")
    observations = native_root / "credential-observations"
    assert (observations / "enabled-v1").read_bytes() == b"ccc-request-credentials-v1\n"
    selection = observations / ("client-" + str(agent) + "-thread.json")
    selection.write_text(json.dumps(dict(schema=1, pid=agent, purpose="client_foreground_thread",
        client_epoch=str(uuid.uuid4()), published_at_ms=int(time.time() * 1000), thread_id=session)))
    selection.chmod(0o600)
def arguments(selected):
    value = processes[selected]
    return (struct.pack("i", len(value["argv"])) + value["executable"].encode() + b"\0\0"
            + b"\0".join(arg.encode() for arg in value["argv"]) + b"\0"
            + b"\0".join((key + "=" + item).encode() for key, item in value["env"].items()) + b"\0\0")
def bsd(requested_pid, kind, arg, pointer, size):
    assert requested_pid in processes and kind == 3
    value = ctypes.cast(pointer, ctypes.POINTER(native.identity.BsdInfo)).contents
    value.pid, value.ppid, value.uid, value.status = requested_pid, 1, os.getuid(), 2
    value.start_sec, value.start_usec = 10000, 1
    value.name = Path(processes[requested_pid]["executable"]).name.encode()
    value.misc[3] = device
    ctypes.set_errno(0)
    return size
def path(requested_pid, pointer, size):
    assert requested_pid in processes
    raw = processes[requested_pid]["executable"].encode() + b"\0"
    ctypes.memmove(pointer, raw, len(raw))
    ctypes.set_errno(0)
    return len(raw)
def pids(kind, selector, pointer, size):
    assert (kind, selector) in ((3, device), (4, os.getuid()))
    for index, selected in enumerate(processes):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[index] = selected
    return len(processes) * ctypes.sizeof(ctypes.c_int)
def sysctl(mib, count, pointer, length, replacement, replacement_size):
    assert count == 3 and tuple(mib[:2]) == (1, 49) and mib[2] in processes
    raw = arguments(mib[2])
    ctypes.memmove(pointer, raw, len(raw))
    ctypes.cast(length, ctypes.POINTER(ctypes.c_size_t)).contents.value = len(raw)
    return 0
lib = SimpleNamespace(proc_pidinfo=bsd, proc_pidpath=path, proc_listpids=pids)
system = SimpleNamespace(sysctl=sysctl)
real_load = ctypes.CDLL
def load(name, *args, **kwargs):
    if name == "/usr/lib/libproc.dylib":
        return lib
    if name is None:
        return system
    return real_load(name, *args, **kwargs)
identity = dict(workspace_uuid=workspace, caller_surface_uuid=caller,
                target_surface_uuid=surface, target_pane_uuid=pane)
tree = dict(windows=[dict(workspaces=[dict(id=workspace, panes=[dict(id=pane,
    surfaces=[dict(id=surface, type="terminal", tty=tty)])])])])
calls = []
def host_command(*args):
    calls.append(args)
    assert args == ("tree", "--all", "--json", "--id-format", "both"), args
    return json.dumps(tree)
host = SimpleNamespace(pin_workspace=lambda selected: identity, _run=host_command)
with patch.object(ctypes, "CDLL", side_effect=load):
    if __FOREIGN_CONFIGURATION__:
        try:
            native.bind_target(host, surface, "STATUS: original-scope-foreign-root")
        except native.NativeDeliveryError as error:
            assert str(error).startswith("NATIVE_OBSERVATION_UNAVAILABLE: ValueError;"), str(error)
            assert type(error.__cause__) is ValueError
            assert str(error.__cause__) == "UPGRADE_NATIVE_PROCESS_ROOT_MISMATCH", str(error.__cause__)
        else:
            raise AssertionError("foreign-root process accepted as a private agent")
        assert calls == [("tree", "--all", "--json", "--id-format", "both")]
        print("PUBLIC_FOREIGN_ROOT_REFUSED_WITH_DIAGNOSTIC_SHELL_ZERO_INPUT")
    elif INCLUDE_AGENT:
        binding = native.bind_target(host, surface, "STATUS: original-scope-agent-binding")
        assert binding["process"]["pid"] == agent
        assert binding["process"]["executable"] == agent_executable
        assert binding["provider"] == "codex"
        assert binding["session_id"] == session
        assert binding["native_config_root"] == str(native_root)
        assert binding["transcript"]["path"] == str(log)
        assert binding["transcript"]["at_record_boundary"] is True
        assert calls == [("tree", "--all", "--json", "--id-format", "both")] * 2
        print("PUBLIC_PINNED_AGENT_BOUND_WITH_DIAGNOSTIC_SHELL_ZERO_INPUT")
    else:
        try:
            native.bind_target(host, surface, "STATUS: original-scope-shell-diagnostic")
        except native.NativeDeliveryError as error:
            assert str(error) == "NATIVE_TARGET_RETURNED_TO_SHELL", str(error)
            assert error.details["input_operations"] == 0
            assert error.details["native_agent_count"] == 0
            assert error.details["automatic_target_selection"] is False
            assert error.details["shell_processes"] == [dict(pid=pid, birth=[10000, 1], executable=executable)]
        else:
            raise AssertionError("shell accepted as a native agent")
        assert calls == [("tree", "--all", "--json", "--id-format", "both")]
        print("PUBLIC_RETURNED_TO_SHELL_REFUSED_ZERO_INPUT")
'''.replace("CONTROLLER", repr(str(Path(self.spec["controller"]) / "scripts"))).replace(
            "INCLUDE_AGENT", repr(include_agent)).replace("NATIVE_ROOT", repr(self.spec["clients"]["codex"]["native_root"])).replace(
            "__FOREIGN_CONFIGURATION__", repr(self.spec["clients"]["claude_a"]["native_root"] if foreign_root else None))
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_public_binding_refuses_returned_shell_without_input(self):
        output = self.binding_probe()
        self.assertIn("PUBLIC_RETURNED_TO_SHELL_REFUSED_ZERO_INPUT", output)

    def test_public_binding_selects_pinned_agent_not_diagnostic_shell(self):
        output = self.binding_probe(include_agent=True)
        self.assertIn("PUBLIC_PINNED_AGENT_BOUND_WITH_DIAGNOSTIC_SHELL_ZERO_INPUT", output)

    def test_public_binding_refuses_pinned_executable_with_foreign_root(self):
        output = self.binding_probe(include_agent=True, foreign_root=True)
        self.assertIn("PUBLIC_FOREIGN_ROOT_REFUSED_WITH_DIAGNOSTIC_SHELL_ZERO_INPUT", output)


if __name__ == "__main__":
    unittest.main()
