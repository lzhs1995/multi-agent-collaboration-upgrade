"""Station launchers: frozen startup argv runs magpie `mg cc|cx`, which execs the pinned launcher.

No workspace, model client, gateway or credential is used. The real mg is exercised
only when it is installed; its station list comes from an inert magpie fixture.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from orchestration_common import ContractError, file_ref, json_ref
from upgrade_client_isolation import (SANDBOX, STATION_SELECTORS, clean_child_environment, station_argv,
                                      station_profile, station_server_argv)
from upgrade_experiment import init_experiment
from upgrade_live_materialize import materialize_live
from upgrade_startup_contract import validate_startup

SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
WRAPPERS = Path("/Applications/cmux.app/Contents/Resources/bin")
MG = (Path.home() / ".local/bin/mg").resolve()
HOOKS = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/fixture/harness-hook"}]}]}}
PROFILE = {"model": "fable", "effortLevel": "xhigh", "permissions": {"defaultMode": "bypassPermissions"},
           "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:3445", "ANTHROPIC_AUTH_TOKEN": "fixture-call-key",
                   "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "fixture/model[1M]", "HTTP_PROXY": "",
                   "HTTPS_PROXY": "", "ALL_PROXY": "", "NO_PROXY": "127.0.0.1,localhost",
                   "CLAUDE_CODE_SUBAGENT_MODEL": "fixture/model"},
           "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/account/global-hook"}]}]},
           "statusLine": {"type": "command", "command": "/account/status"}, "enabledPlugins": {"x@y": True}}
CODEX = {"model": "fixture-model", "model_provider": "custom",
         "model_providers": {"custom": {"name": "custom", "base_url": "https://upstream.invalid/v1",
                                        "wire_api": "responses"}}}


class StationArgvTests(unittest.TestCase):
    def test_claude_argv_drops_the_lane_settings_pair_and_keeps_every_other_argument(self):
        native = ["/w/claude", "--dangerously-skip-permissions", "--session-id", "S", "--setting-sources", "",
                  "--settings", "/n/settings.json", "--debug-file", "/n/log", "prompt"]
        self.assertEqual(station_argv("/py", "/mg", native, station="linxi", key="linxi-s1"),
                         ["/py", "-I", "-B", "/mg", "cc", "linxi", "-k", "linxi-s1", "--",
                          "--dangerously-skip-permissions", "--session-id", "S", "--setting-sources", "",
                          "--debug-file", "/n/log", "prompt"])

    def test_codex_argv_and_server_final_argv(self):
        native = ["/w/codex", "--remote", "unix:///s", "--cd", "/w", "--dangerously-bypass-hook-trust", "p"]
        self.assertEqual(station_argv("/py", "/mg", native, station="agentrouter-a"),
                         ["/py", "-I", "-B", "/mg", "cx", "agentrouter-a", "--", *native[1:]])
        self.assertEqual(station_server_argv(["/c", "app-server", "--listen", "unix:///s"], "http://127.0.0.1:3465"),
                         ["/c", "-c", 'model_providers.custom.base_url="http://127.0.0.1:3465/v1"',
                          "app-server", "--listen", "unix:///s"])

    def test_invalid_launch_shapes_refuse(self):
        for native, station, key in ((["/c", "--settings", "a", "--settings", "b"], "s", "k"),
                                     (["/c"], "s", "k"), (["/c", "--", "x"], "s", None),
                                     (["/c"], "../s", None), (["/c", "--settings", "a"], "s", "k/../x")):
            with self.subTest(native=native, station=station, key=key):
                with self.assertRaises(ContractError):
                    station_argv("/py", "/mg", native, station=station, key=key)
        for url in ("https://127.0.0.1:3465", "http://127.0.0.1:3465/v1", "http://example.invalid:80"):
            with self.subTest(url=url), self.assertRaises(ContractError):
                station_server_argv(["/c"], url)


class StationProfileTests(unittest.TestCase):
    def test_profile_keeps_routing_environment_and_replaces_account_hooks(self):
        value = json.loads(station_profile(PROFILE, HOOKS))
        self.assertEqual(value["env"], PROFILE["env"])
        self.assertEqual(value["hooks"], HOOKS["hooks"])
        self.assertTrue(value["skipDangerousModePermissionPrompt"])
        self.assertEqual(value["permissions"], PROFILE["permissions"])
        self.assertFalse({"statusLine", "enabledPlugins"} & set(value))

    def test_unknown_setting_or_harness_environment_refuses(self):
        cases = [dict(PROFILE, apiKeyHelper="/x"),
                 *[dict(PROFILE, env=dict(PROFILE["env"], **{name: "v"})) for name in
                   ("HOME", "CLAUDE_CONFIG_DIR", "NODE_OPTIONS", "CMUX_SURFACE_ID", "MG_CONFIG", "TMPDIR")],
                 dict(PROFILE, env=dict(PROFILE["env"], NO_PROXY="localhost"))]
        for value in cases:
            with self.subTest(value=sorted(set(value) ^ set(PROFILE)) or sorted(set(value["env"]) ^ set(PROFILE["env"]))):
                with self.assertRaises(ContractError):
                    station_profile(value, HOOKS)

    def test_inherited_station_selectors_are_dropped(self):
        with unittest.mock.patch.dict(os.environ, {name: "/inherited" for name in STATION_SELECTORS}):
            env = clean_child_environment({"MG_CONFIG": "/lane/mg.toml"})
        self.assertEqual(env["MG_CONFIG"], "/lane/mg.toml")
        self.assertFalse(set(STATION_SELECTORS[1:]) & set(env))


@unittest.skipUnless(SOURCE.is_dir() and WRAPPERS.is_dir(), "requires fixed local MAC and cmux wrappers")
class MaterializedStationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        cls.root = Path(cls.tmp.name).resolve()
        folder = cls.root / "station-folder"
        (folder / "magpie").mkdir(parents=True)
        (folder / "magpie/caller-keys.json").write_text(json.dumps([{"name": "linxi-s1", "secret": "fixture-call-key"}]))
        for state in ("up", "down"):
            (cls.root / (state + ".json")).write_text(json.dumps({"path": "/fixture/stations.yml", "root": "/fixture",
                "stations": [{"name": "linxi", "state": state, "baseURL": "http://127.0.0.1:3445", "folder": str(folder)},
                             {"name": "agentrouter-a", "state": state, "baseURL": "http://127.0.0.1:3465",
                              "folder": str(folder)}]}))
        cls.state = cls.root / "state"
        cls.state.write_text(str(cls.root / "up.json"))
        # Inert magpie: answers only `station ls --json`, from the selected file.
        cls.magpie = cls.root / "magpie"
        cls.magpie.write_text('#!/bin/bash\n[[ "$*" == "station ls --json" ]] || exit 64\n'
                              'exec /bin/cat -- "$(/bin/cat ' + shlex.quote(str(cls.state)) + ')"\n')
        cls.magpie.chmod(0o755)
        capture = cls.root / "capture-argv"
        capture.write_text("#!/bin/bash\nexec " + shlex.quote(sys.executable) +
                           " -I -B -c 'import json,sys; print(json.dumps(sys.argv[1:]))' \"$@\"\n")
        capture.chmod(0o755)
        binaries = {k: Path("/usr/bin/true") for k in ("claude", "cmux", "rtk", "node")}
        binaries["codex"] = capture
        binaries.update({p + "_wrapper": WRAPPERS / ("cmux-" + p + "-wrapper") for p in ("codex", "claude")})
        context = init_experiment(cls.root / "s", "station-startup", SOURCE)
        cls.mg = MG if MG.is_file() else Path("/usr/bin/true")
        cls.runtime = json_ref(materialize_live(context, "B", binaries=binaries,
            provider_configuration=(CODEX, PROFILE),
            station_launchers={"mg": cls.mg, "magpie": cls.magpie,
                               "codex": {"station": "agentrouter-a", "base_url": "http://127.0.0.1:3465"},
                               "claude": {"station": "linxi", "key": "linxi-s1"}}))
        cls.ref = cls.runtime["startup"]
        cls.spec, cls.binding = validate_startup(cls.ref)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_frozen_startup_runs_mg_for_every_lane_and_the_server(self):
        spec, pins = self.spec, self.spec["pins"]
        python, mg = pins["python"]["path"], str(self.mg)
        rpc = spec["rpc"]
        # mg-server.toml names the pinned binary as launcher; only its arguments follow `--`.
        self.assertEqual(rpc["launch_argv"], [python, "-I", "-B", mg, "cx", "agentrouter-a", "--",
                                              "app-server", "--listen", "unix://" + rpc["path"]])
        self.assertEqual(rpc["argv"][:3], [pins["codex"]["path"], "-c",
                                           'model_providers.custom.base_url="http://127.0.0.1:3465/v1"'])
        for lane in ("claude_a", "claude_b"):
            client = spec["clients"][lane]
            root = Path(client["native_root"])
            self.assertEqual(client["argv"][:9], [python, "-I", "-B", mg, "cc", "linxi", "-k", "linxi-s1", "--"])
            self.assertNotIn("--settings", client["argv"])
            self.assertEqual(client["environment"]["MG_CONFIG"], str(root / "mg.toml"))
            self.assertEqual(pins[lane + "/mg-profiles/linxi-s1.json"]["sha256"], pins[lane + "/settings.json"]["sha256"])
            settings = json.loads((root / "settings.json").read_text())
            self.assertEqual(settings["env"], PROFILE["env"])
            self.assertNotIn("/account/global-hook", json.dumps(settings))
        self.assertEqual(spec["clients"]["codex"]["argv"][:7], [python, "-I", "-B", mg, "cx", "agentrouter-a", "--"])
        self.assertEqual(self.binding["station_launchers"]["claude"], {"station": "linxi", "key": "linxi-s1"})

    def test_changed_station_launch_refuses(self):
        path = Path(self.ref["path"])
        original = path.read_bytes()
        mutations = [lambda x: x["rpc"]["launch_argv"].__setitem__(5, "baibei"),
                     lambda x: x["rpc"].pop("launch_environment"),
                     lambda x: x["clients"]["claude_a"]["argv"].__setitem__(5, "baibei"),
                     lambda x: x["clients"]["codex"]["environment"].pop("MG_CONFIG")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = json.loads(original)
                mutate(value)
                try:
                    path.chmod(0o600)
                    path.write_text(json.dumps(value))
                    with self.assertRaises((ContractError, ValueError)):
                        validate_startup(file_ref(path))
                finally:
                    path.write_bytes(original)
                    path.chmod(0o444)
        toml = Path(self.spec["pins"]["claude_b/mg.toml"]["path"])
        raw = toml.read_bytes()
        try:
            toml.chmod(0o600)
            toml.write_bytes(raw.replace(b"cmux-claude-wrapper", b"cmux-claude-wrapperx"))
            with self.assertRaisesRegex(ValueError, "ARTIFACT_PIN_CHANGED"):
                validate_startup(self.ref)
        finally:
            toml.write_bytes(raw)
            toml.chmod(0o444)

    def run_lane(self, lane, argv, *, extra_env=None):
        env = clean_child_environment(self.spec["clients"][lane]["environment"])
        env.update(extra_env or {})
        return subprocess.run([str(SANDBOX), "-f", self.spec["pins"][lane + "_sandbox"]["path"], *argv], env=env,
                              cwd=self.spec["work_root"], capture_output=True, text=True, timeout=30)

    @unittest.skipUnless(MG.is_file() and sys.platform == "darwin" and SANDBOX.exists(),
                         "requires the installed mg and macOS kernel isolation")
    def test_real_mg_resolves_the_pinned_launchers_inside_each_lane_sandbox(self):
        for lane in ("claude_a", "claude_b", "codex"):
            with self.subTest(lane=lane):
                argv = self.spec["clients"][lane]["argv"]
                cut = argv.index("--")
                result = self.run_lane(lane, [*argv[:cut], "--print", *argv[cut:]])
                self.assertEqual(result.returncode, 0, result.stderr)
                proxy, lower, final = result.stdout.split("\n", 2)
                self.assertEqual((proxy, lower), ("  NO_PROXY=127.0.0.1,localhost,::1", "  no_proxy=127.0.0.1,localhost,::1"))
                root = Path(self.spec["clients"][lane]["native_root"])
                pins = self.spec["pins"]
                routing = (["--settings", str(root / "mg-profiles/linxi-s1.json")] if lane != "codex" else
                           ["-c", 'model_providers.custom.base_url="http://127.0.0.1:3465/v1"'])
                provider = "codex" if lane == "codex" else "claude"
                self.assertEqual(shlex.split(final), [pins[provider + "_wrapper"]["path"], *routing, *argv[cut + 1:]])

    @unittest.skipUnless(MG.is_file() and sys.platform == "darwin" and SANDBOX.exists(),
                         "requires the installed mg and macOS kernel isolation")
    def test_real_mg_execs_the_server_with_its_frozen_final_argv(self):
        rpc = self.spec["rpc"]
        result = self.run_lane("codex", rpc["launch_argv"], extra_env=rpc["launch_environment"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), rpc["argv"][1:])

    @unittest.skipUnless(MG.is_file() and sys.platform == "darwin" and SANDBOX.exists(),
                         "requires the installed mg and macOS kernel isolation")
    def test_real_mg_refuses_a_station_that_is_not_up(self):
        self.state.write_text(str(self.root / "down.json"))
        try:
            result = self.run_lane("codex", self.spec["rpc"]["launch_argv"],
                                   extra_env=self.spec["rpc"]["launch_environment"])
        finally:
            self.state.write_text(str(self.root / "up.json"))
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("agentrouter-a", result.stderr)


if __name__ == "__main__":
    unittest.main()
