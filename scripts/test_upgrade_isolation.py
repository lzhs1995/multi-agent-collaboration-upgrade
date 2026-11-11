"""Exercise filesystem boundaries in fresh processes, without any cmux input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from orchestration_common import ContractError, file_ref, tree_manifest, write_json
from upgrade_controller_patch import add_bootstrap, relocate
from upgrade_experiment import (PROTECTED_REFS, arm_paths, init_experiment, private_root,
                                live_gate, validate_context, validate_sessions)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "original"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "SKILL.md").write_text("fixture only\n")
        (self.source / "scripts/mac_harness.py").write_text("# offline fixture\n")
        self.context_ref = init_experiment(self.root / "lab", "offline-test", self.source)
        self.context = validate_context(self.context_ref)
        self.paths = arm_paths(self.context, "A1")
        for path in self.paths.values():
            path.mkdir()
        self.controller = self.paths["controllers"] / "releases/private-v1/source"
        scripts = self.controller / "scripts"
        scripts.mkdir(parents=True)
        shutil.copyfile(Path(__file__).parent / "upgrade_controller_scope.py", scripts / "upgrade_controller_scope.py")
        self.outside = self.root / "production-sentinel.txt"
        self.outside.write_text("preserve\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_guarded(self, body):
        script = self.controller / "scripts/probe.py"
        script.write_text(add_bootstrap(body))
        write_json(self.controller / "upgrade-binding.json", dict(
            context=self.context_ref, controller_root=str(self.controller), execution_mode="offline",
            **{name + "_root": str(self.paths[key]) for name, key in (
                ("state", "state"), ("campaign", "campaigns"), ("evidence", "evidence"), ("tmp", "tmp"))}))
        for path in self.controller.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        manifest = tree_manifest(self.controller)
        write_json(self.controller.parent / "MANIFEST.json", dict(files=manifest["files"]))
        return subprocess.run([sys.executable, "-I", "-B", str(script)], text=True,
                              capture_output=True, timeout=15, cwd=self.root)

    def test_private_fd_and_relative_directory_publication(self):
        result = self.run_guarded(f'''
import os
from pathlib import Path
base = Path({str(self.paths["state"])!r})
directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
os.mkdir('child', dir_fd=directory)
fd = os.open('child/result', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
with os.fdopen(fd, 'wb') as stream:
    stream.write(b'verified')
    stream.flush()
    os.fsync(stream.fileno())
os.rename('child/result', 'published', src_dir_fd=directory, dst_dir_fd=directory)
os.close(directory)
assert (base / 'published').read_bytes() == b'verified'
print('PRIVATE_FD_OK')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PRIVATE_FD_OK", result.stdout)

    def test_outside_symlink_descriptor_and_subprocess_refused(self):
        link = self.paths["state"] / "escape"
        link.symlink_to(self.root, target_is_directory=True)
        result = self.run_guarded(f'''
import os, subprocess, socket
from pathlib import Path
outside = Path({str(self.outside)!r})
assert outside.read_text() == 'preserve\\n'
def refused(action):
    try:
        action()
    except PermissionError:
        return
    raise AssertionError('operation escaped the private scope')
refused(lambda: outside.write_text('changed'))
refused(lambda: Path({str(link / self.outside.name)!r}).write_text('changed'))
directory = os.open(outside.parent, os.O_RDONLY | os.O_DIRECTORY)
refused(lambda: os.open(outside.name, os.O_WRONLY, dir_fd=directory))
refused(lambda: os.mkdir('unowned-directory', dir_fd=directory))
os.close(directory)
refused(lambda: subprocess.run(['/usr/bin/true']))
refused(lambda: socket.socket(socket.AF_UNIX).connect('/tmp/unowned.sock'))
assert outside.read_text() == 'preserve\\n'
print('ESCAPES_REFUSED')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ESCAPES_REFUSED", result.stdout)
        self.assertEqual(self.outside.read_text(), "preserve\n")

    def test_unmanifested_controller_file_is_refused_before_work(self):
        result = self.run_guarded("print('unexpected')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.controller / "scripts/unexpected.py").write_text("# injected\n")
        script = self.controller / "scripts/probe.py"
        result = subprocess.run([sys.executable, "-I", "-B", str(script)], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UPGRADE_CONTROLLER_FILE_SET_CHANGED", result.stderr)
        self.assertNotIn("unexpected", result.stdout)

    def test_context_cannot_overlap_production_or_be_reinitialized(self):
        for path in (Path.home(), Path.home() / ".local/state/multi-agent-collaboration/test",
                     Path.home() / ".local", Path.home() / ".claude/settings.json"):
            with self.subTest(path=path), self.assertRaises(ContractError):
                private_root(path)
        with self.assertRaises(ContractError):
            init_experiment(self.root / "lab", "another-id", self.source)

    def test_path_relocation_keeps_native_identity_paths(self):
        original = '''from pathlib import Path
STATE = Path.home() / ".local/state/multi-agent-collaboration/message-dispatch-v1"
NATIVE = Path.home() / ".claude/projects"
HOOKS = Path.home() / ".codex" / "hooks.json"
'''
        namespace = {}
        exec(relocate(original, self.paths, self.controller), namespace)
        self.assertEqual(namespace["STATE"], self.paths["state"] / "message-dispatch-v1")
        self.assertEqual(namespace["NATIVE"], Path.home() / ".claude/projects")
        self.assertEqual(namespace["HOOKS"], self.paths["config"] / "codex-hooks.json")


class EndpointTests(unittest.TestCase):
    def sessions(self):
        import uuid
        workspace = str(uuid.uuid4())
        return {lane: dict(provider="codex" if lane == "codex" else "claude",
                           session_id=str(uuid.uuid4()), surface_uuid=str(uuid.uuid4()),
                           pane_uuid=str(uuid.uuid4()), workspace_uuid=workspace,
                           surface_ref="surface:" + str(i))
                for i, lane in enumerate(("codex", "claude_a", "claude_b"), 1)}

    def test_two_claude_ids_are_distinct_from_provider(self):
        sessions = self.sessions()
        self.assertIs(validate_sessions(sessions), sessions)
        sessions["claude_b"]["provider"] = "codex"
        with self.assertRaises(ContractError):
            validate_sessions(sessions)

    def test_protected_or_aliased_endpoints_are_refused(self):
        for ref in PROTECTED_REFS:
            sessions = self.sessions()
            sessions["claude_a"]["surface_ref"] = ref
            with self.subTest(ref=ref), self.assertRaises(ContractError):
                validate_sessions(sessions)
        sessions = self.sessions()
        sessions["claude_b"]["surface_uuid"] = sessions["claude_a"]["surface_uuid"].upper()
        with self.assertRaises(ContractError):
            validate_sessions(sessions)


class LiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        from orchestration_test_support import CampaignFixture
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.private = CampaignFixture._private_runtime(Path(self.tmp.name).resolve(), "B")
        self.sessions = EndpointTests().sessions()

    def test_offline_runtime_refuses_even_a_supplied_adoption_claim(self):
        proof = write_json(self.private["paths"]["evidence"] / "claimed-adoption.json",
                           {"status": "PASS", "automatic_hooks": True, "live": True})
        runtime_before = file_ref(self.private["runtime_ref"]["path"])
        for supplied in (None, proof):
            with self.subTest(supplied=supplied), self.assertRaisesRegex(
                    ContractError, "LIVE_ISOLATION_UNVERIFIED: runtime v1 is offline"):
                live_gate(self.private["context_ref"], self.private["runtime_ref"],
                          self.sessions, supplied)
        self.assertEqual(file_ref(self.private["runtime_ref"]["path"]), runtime_before)

    def test_changing_offline_mode_does_not_create_a_live_runtime(self):
        runtime = dict(self.private["runtime"], execution_mode="live")
        ref = write_json(Path(self.private["runtime_ref"]["path"]), runtime)
        with self.assertRaisesRegex(ContractError, "unverified runtime execution mode"):
            live_gate(self.private["context_ref"], ref, self.sessions)


if __name__ == "__main__":
    unittest.main()
