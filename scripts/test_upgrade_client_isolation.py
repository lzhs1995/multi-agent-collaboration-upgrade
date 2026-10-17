"""Real local kernel probes; none of these tests launches a model client."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError
from upgrade_client_isolation import (SANDBOX, LANES, claude_config, client_argv,
    clean_child_environment, client_temp_base, codex_config, environment, patch_cmux_wrapper,
    prepare_client_temp, private_native_roots, require_kernel_boundary, sandbox_check,
    sandbox_profile, toml, verify_client_temp)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.paths = {name: self.root / name for name in ("state", "tmp")}
        self.hooks = {"hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "/pinned/hook"}]}]}}

    def test_filtered_configuration_roundtrips_without_global_hooks_or_preload(self):
        original = {"model": "preserved", "model_provider": "existing",
            "model_providers": {"existing": {"base_url": "https://example.invalid/v1", "env_key": "KEY"}},
            "hooks": {"bad": "global"}, "mcp_servers": {"bad": {"command": "/global"}}}
        parsed = tomllib.loads(codex_config(original, self.root, self.hooks).decode())
        self.assertEqual(parsed["model_providers"], original["model_providers"])
        self.assertEqual(parsed["model"], original["model"])
        self.assertNotIn("mcp_servers", parsed)
        self.assertNotIn("hooks", parsed)
        parsed = json.loads(claude_config({"model": "preserved", "hooks": {"bad": []},
            "env": {"ANTHROPIC_BASE_URL": "https://example.invalid", "NODE_OPTIONS": "--require bad",
                    "HOME": "/bad", "CLAUDE_CONFIG_DIR": "/bad"}}, self.hooks))
        self.assertEqual(parsed["hooks"], self.hooks["hooks"])
        self.assertEqual(parsed["env"], {"ANTHROPIC_BASE_URL": "https://example.invalid"})

    def test_distinct_native_roots_do_not_replace_real_home_or_session_selectors(self):
        roots = private_native_roots(self.paths)
        self.assertEqual(len(set(roots.values())), 3)
        for lane in LANES:
            overrides = environment(roots, self.paths, lane)
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "inherited", "NODE_OPTIONS": "bad",
                                        "CMUX_CODEX_HOOKS_DISABLED": "1", "CLAUDE_CODE_TMPDIR": "/unbound"}):
                child = clean_child_environment(overrides)
            self.assertEqual(child.get("HOME"), os.environ.get("HOME"))
            for key in ("CODEX_THREAD_ID", "NODE_OPTIONS", "CMUX_CODEX_HOOKS_DISABLED"):
                self.assertNotIn(key, child)
            self.assertEqual(overrides.get("CODEX_HOME", overrides.get("CLAUDE_CONFIG_DIR")), str(roots[lane]))
            if lane == "codex":
                self.assertNotIn("CLAUDE_CODE_TMPDIR", child)
            else:
                self.assertEqual(child["CLAUDE_CODE_TMPDIR"], overrides["TMPDIR"])
                self.assertLessEqual(len(os.fsencode(Path(child["CLAUDE_CODE_TMPDIR"]) / f"claude-{os.getuid()}")), 44)
        with self.assertRaises(ContractError):
            clean_child_environment({"HOME": "/bad"})
        with self.assertRaises(ContractError):
            clean_child_environment({"CMUX_SURFACE_ID": str(uuid.uuid4())})

    def test_cli_arguments_are_structured_and_do_not_duplicate_claude_hooks(self):
        odd = self.root / "spaces $(untrusted) `text`"
        args = client_argv("claude", odd, self.root, self.root, session_id=str(uuid.uuid4()))
        self.assertEqual(args[0], str(odd))
        self.assertEqual(args[args.index("--setting-sources") + 1], "")
        self.assertEqual(args.count("--settings"), 1)
        args = client_argv("codex", odd, self.root, self.root)
        self.assertIn("--no-daemon", args)
        self.assertNotIn("--resume", args)
        with self.assertRaises(ContractError):
            client_argv("codex", odd, self.root, self.root, session_id=str(uuid.uuid4()))

    def test_toml_values_are_data(self):
        value = {"strange.key": "a\n\" $(ignored)", "bool": True, "items": [1, "x"]}
        self.assertEqual(tomllib.loads("value = " + toml(value))["value"], value)
        with self.assertRaises(ContractError):
            toml(None)

    def test_unrecognised_wrapper_refuses_before_publication(self):
        with self.assertRaises(ContractError):
            patch_cmux_wrapper("codex", b"#!/bin/bash\nexec codex\n", Path("/usr/bin/true"), self.paths)

    @unittest.skipUnless(Path("/Applications/cmux.app/Contents/Resources/bin/cmux-codex-wrapper").exists(),
                         "installed cmux wrapper unavailable")
    def test_installed_wrapper_copies_preserve_valid_shell_and_pin_executables(self):
        for provider in ("codex", "claude"):
            original = Path("/Applications/cmux.app/Contents/Resources/bin/cmux-" + provider + "-wrapper").read_bytes()
            result = patch_cmux_wrapper(provider, original, Path("/usr/bin/true"), self.paths,
                                        node=Path("/usr/bin/true"))
            target = self.root / provider
            target.write_bytes(result)
            check = subprocess.run(["/bin/bash", "-n", str(target)], capture_output=True, text=True, timeout=5)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn(("REAL_" + provider.upper() + "=/usr/bin/true").encode(), result)
            if provider == "claude":
                self.assertNotIn(b'local cmux_dir="${HOME%/}/.cmuxterm"', result)
                self.assertNotIn(b"command -v node ", result)

    def test_temp_boundary_rejects_long_unicode_paths_links_and_unsafe_modes(self):
        self.paths["tmp"].mkdir()
        base = prepare_client_temp(self.paths, "claude_a")
        self.assertEqual(verify_client_temp(self.paths, "claude_a"), base)
        child = base / f"claude-{os.getuid()}"
        child.chmod(0o755)
        with self.assertRaisesRegex(ContractError, "TEMP_OWNER_OR_MODE"):
            verify_client_temp(self.paths, "claude_a")
        child.chmod(0o700)
        child.rmdir()
        child.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ContractError):
            verify_client_temp(self.paths, "claude_a")
        with self.assertRaisesRegex(ContractError, "TMP_PATH_TOO_LONG"):
            client_temp_base({"tmp": self.root / ("长" * 10)}, "claude_b")

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS Seatbelt")
    def test_actual_child_temporary_files_stay_private_and_outside_writes_fail(self):
        self.paths["tmp"].mkdir(mode=0o700)
        base = prepare_client_temp(self.paths, "claude_a")
        roots = private_native_roots(self.paths)
        profile = self.root / "temp.sb"
        profile.write_bytes(sandbox_profile([self.paths["tmp"]]))
        outside = self.root / "outside"
        outside.write_text("unchanged")
        code = '''
import os, sys, tempfile
from pathlib import Path
base = Path(os.environ["CLAUDE_CODE_TMPDIR"])
child = base / f"claude-{os.getuid()}"
assert len(os.fsencode(child)) <= 44
with tempfile.NamedTemporaryFile(dir=child) as f:
    f.write(b"actual private child write")
    f.flush()
    assert Path(f.name).read_bytes() == b"actual private child write"
with tempfile.NamedTemporaryFile() as f:
    assert Path(f.name).parent == base
try:
    Path(sys.argv[1]).write_text("pollution")
except PermissionError:
    pass
else:
    raise AssertionError("outside write was allowed")
print("PRIVATE_TEMP_CHILD_PASS")
'''
        result = subprocess.run([str(SANDBOX), "-f", str(profile), sys.executable, "-I", "-B", "-c",
                                 code, str(outside)], capture_output=True, text=True, timeout=15,
                                env=clean_child_environment(environment(roots, self.paths, "claude_a")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PRIVATE_TEMP_CHILD_PASS", result.stdout)
        self.assertEqual(outside.read_text(), "unchanged")
        self.assertEqual(verify_client_temp(self.paths, "claude_a"), base)

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists()
        and Path("/Applications/cmux.app/Contents/Resources/bin/cmux-claude-wrapper").is_file()
        and Path("/Users/lzhs/.nvm/versions/node/v24.16.0/bin/node").is_file(),
        "requires pinned local cmux, Node and Seatbelt")
    def test_real_wrapper_merge_with_pinned_node_preserves_both_hook_sources(self):
        node = Path("/Users/lzhs/.nvm/versions/node/v24.16.0/bin/node")
        self.paths["tmp"].mkdir(mode=0o700)
        base = prepare_client_temp(self.paths, "claude_a")
        original = Path("/Applications/cmux.app/Contents/Resources/bin/cmux-claude-wrapper").read_bytes()
        wrapper = patch_cmux_wrapper("claude", original, Path("/usr/bin/true"), self.paths, node=node).decode()
        # Execute the actual installed wrapper's file and merge functions only.
        # No wrapper entrypoint, native client, hook command or cmux RPC runs.
        functions = wrapper[wrapper.index("cmux_claude_wrapper_create_settings_file() {"):
                            wrapper.index("cmux_computer_use_json_escape() {")]
        merge = wrapper[wrapper.index('CMUX_SETTINGS_PATH=""'):
                        wrapper.index("# Capture only the compact post-merge argv.")]
        cmux_hook = {"hooks": [{"type": "command", "command": "/fixture/cmux-hook"}]}
        private_hook = {"hooks": [{"type": "command", "command": "/fixture/private-hook"}]}
        original_settings = {"model": "unchanged-model", "hooks": {"PostToolUse": [private_hook]}}
        user_settings = self.root / "user-settings.json"
        user_settings.write_text(json.dumps(original_settings))
        hooks = {"hooks": {"PostToolUse": [cmux_hook]}}
        script = ("set -u\n" + functions + "\nHOOKS_JSON=" + shlex.quote(json.dumps(hooks))
                  + "\nCMUX_FILTERED_ARGS=(--offline-fixture)\nCMUX_USER_SETTINGS=(" + shlex.quote(str(user_settings))
                  + ")\n" + merge + '\n[[ "$CMUX_SETTINGS_MERGE_SUCCEEDED" == true ]] || exit 71\n'
                  + 'printf "%s\\n" "$CMUX_SETTINGS_PATH"\n')
        profile = self.root / "merge.sb"
        profile.write_bytes(sandbox_profile([self.paths["tmp"]]))
        env = clean_child_environment({"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(base)})
        result = subprocess.run([str(SANDBOX), "-f", str(profile), "/bin/bash", "-c", script],
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("warning", result.stderr.lower())
        durable = Path(result.stdout.strip())
        self.assertTrue(durable.is_relative_to(self.paths["tmp"] / "cmux-wrapper"))
        self.assertEqual(durable.resolve(), durable)
        self.assertEqual(durable.stat().st_mode & 0o777, 0o600)
        settings = json.loads(durable.read_text())
        self.assertEqual(settings["hooks"]["PostToolUse"], [cmux_hook, private_hook])
        self.assertEqual(settings["model"], "unchanged-model")
        self.assertEqual(settings["__cmux"]["managed"], "claude-hooks")
        self.assertEqual(json.loads(user_settings.read_text()), original_settings)


@unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS Seatbelt")
class KernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.private = self.root / "private"
        self.private.mkdir()
        (self.private / "allowed").write_text("probe")
        self.native = self.private / "native"
        self.native.mkdir()
        self.pinned = self.native / "config.json"
        self.pinned.write_text("immutable")
        self.outside = self.root / "production-sentinel"
        self.outside.write_text("unchanged")
        (self.private / "symlink").symlink_to(self.outside)
        self.profile = self.root / "profile.sb"
        self.profile.write_bytes(sandbox_profile([self.private], [self.pinned]))

    def run_script(self, source):
        return subprocess.run([str(SANDBOX), "-f", str(self.profile), sys.executable, "-I", "-B", "-c", source],
                              capture_output=True, text=True, timeout=15)

    def test_real_writes_symlinks_renames_and_descendants_obey_boundary(self):
        result = self.run_script(f'''
from pathlib import Path
import subprocess, sys
private, pinned, outside = map(Path, {list(map(str, (self.private, self.pinned, self.outside)))!r})
(private / 'allowed').write_text('private')
def denied(action):
    try:
        action()
    except PermissionError:
        return
    raise AssertionError('write unexpectedly allowed')
denied(lambda: outside.write_text('polluted'))
denied(lambda: pinned.write_text('changed'))
denied(lambda: pinned.unlink())
denied(lambda: (private / 'symlink').write_text('polluted'))
denied(lambda: pinned.parent.rename(private / 'renamed'))
child = subprocess.run([sys.executable, '-I', '-B', '-c',
    "from pathlib import Path; Path(" + repr(str(outside)) + ").write_text('child-pollution')"], capture_output=True)
assert child.returncode != 0
assert pinned.read_text() == 'immutable'
assert outside.read_text() == 'unchanged'
print('OS_BOUNDARY_PASS')
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outside.read_text(), "unchanged")
        self.assertEqual(self.pinned.read_text(), "immutable")
        self.assertIn("OS_BOUNDARY_PASS", result.stdout)

    def test_kernel_queries_distinguish_live_sandboxed_and_unsandboxed_processes(self):
        args = [str(SANDBOX), "-f", str(self.profile), sys.executable, "-I", "-B", "-c",
                "import os,sys; print(os.getpid(), flush=True); sys.stdin.readline()"]
        with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as child:
            try:
                self.assertEqual(int(child.stdout.readline()), child.pid)
                proof = require_kernel_boundary(child.pid, denied=[self.outside, self.pinned],
                                                writable=[self.private / "allowed"])
                self.assertTrue(proof["kernel_checked"])
                self.assertFalse(sandbox_check(os.getpid(), self.outside))
                with self.assertRaises(ContractError):
                    require_kernel_boundary(os.getpid(), denied=[self.outside], writable=[self.private / "allowed"])
            finally:
                child.communicate("done\n", timeout=5)

    def test_readonly_ancestor_cannot_be_reopened_writable(self):
        with self.assertRaises(ContractError):
            sandbox_profile([self.private], [self.root])

    def test_external_publishers_work_before_outputs_exist_and_still_reject_clients(self):
        from upgrade_live_control import external_publisher
        from upgrade_schedule import external

        controller = self.root / "controller"
        controller.mkdir()
        registration = {"root": str(self.root)}
        spec = {"controller": str(controller),
                "adoption_path": str(self.root / "live-control/adoption.json")}
        outputs = [self.root / "schedule/publisher.lock", Path(spec["adoption_path"])]
        self.assertTrue(all(not p.exists() for p in outputs))
        external(registration)
        external_publisher(spec)
        self.assertTrue(all(not p.exists() for p in outputs))
        result = self.run_script(f'''
import sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
from orchestration_common import ContractError
from upgrade_live_control import external_publisher
from upgrade_schedule import external
for check, argument in [(external, {registration!r}), (external_publisher, {spec!r})]:
    try:
        check(argument)
    except ContractError:
        pass
    else:
        raise AssertionError("sandboxed client acquired external publishing authority")
print("EXTERNAL_PUBLISHER_BOUNDARY_PASS")
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EXTERNAL_PUBLISHER_BOUNDARY_PASS", result.stdout)
        self.assertTrue(all(not p.exists() for p in outputs))


if __name__ == "__main__":
    unittest.main()
