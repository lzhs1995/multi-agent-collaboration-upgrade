"""Exercise private startup wiring without creating a workspace or model client."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_client_isolation import LANES, SANDBOX, clean_child_environment
from upgrade_experiment import arm_paths, init_experiment, validate_context
from upgrade_hook_observer import hook_parents
import upgrade_input_gate as gate
from upgrade_live_integrity import artifact_ref
from upgrade_live_materialize import materialize_live
from upgrade_live_patch import live_gates, private_imports
import upgrade_native_roots as roots
from upgrade_startup_contract import validate_startup

SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
MODERN_SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.58-cf3f6175840b/source"
WRAPPERS = Path("/Applications/cmux.app/Contents/Resources/bin")


@unittest.skipUnless(SOURCE.is_dir() and WRAPPERS.is_dir(), "requires fixed local MAC and cmux wrappers")
class MaterializedStartupTests(unittest.TestCase):
    source_controller = SOURCE

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        cls.root = Path(cls.tmp.name).resolve()
        cls.context_ref = init_experiment(cls.root / "l", "focused-startup", cls.source_controller)
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

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_actual_controller_default_footer_and_tip_input_boundaries(self):
        controller = Path(self.spec["controller"]) / "scripts"
        fixtures = Path(__file__).resolve().parent.parent / "verification/fixtures"
        screens = [(fixtures / ("claude-default-" + lane + "-20261018.txt")).read_text()
                   for lane in ("a", "b")]
        code = r'''
import ast, json, sys, time, uuid, hashlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, CONTROLLER)
import cmux_bridge as b
import cmux_native_delivery as native
assert Path(b.__file__).parent == Path(CONTROLLER)
screens = SCREENS
permissions = "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
tip = "  ⎿ \u00a0Tip: Use /clear to start fresh when switching topics and free up context"
def before(screen, text):
    rows = screen.splitlines()
    start = b._claude_bordered_compose(screen)["start"]
    rows[start - 1:start - 1] = text.splitlines()
    return "\n".join(rows) + "\n"
def draft(screen, text):
    rows = screen.splitlines()
    start = b._claude_bordered_compose(screen)["start"]
    rows[start] = "❯ " + text
    return "\n".join(rows) + "\n"
with patch.object(b, "send_text") as paste, patch.object(b, "send_key") as key:
    for screen in screens:
        assert b.receiver_input_kind(screen) == "AGENT_TUI"
        assert b.compose_block_text(screen) == ""
        assert not b._queued_or_active_input(screen)
        b.require_clearable_agent_input(screen, "offline-peer")
        for spinner in ("✢ Brewing… (1m 2s)", "✻ Compacting conversation…", "✢ Brewing…", "· Thinking..."):
            for hints in (tip, tip + "\n\n◎ /goal active (59m)"):
                active = before(screen, spinner + "\n" + hints)
                assert b.compose_block_is_empty(active)
                assert b._queued_or_active_input(active)
                try:
                    b.require_clearable_agent_input(active, "offline-peer")
                except b.DispatchUnconfirmed:
                    pass
                else:
                    raise AssertionError("active turn accepted")
        for done in ("✻ Brewed for 3m", "✢ Brewing…\nFinished reviewing."):
            assert not b._queued_or_active_input(before(screen, done + "\n" + tip))
        for text in ("retain draft", permissions, "\n  " + tip, "  spaced\t正文  "):
            pending = draft(screen, text)
            assert b.compose_block_text(pending) == text
            with patch.object(b, "read_screen", return_value=pending):
                try:
                    b._submit_terminal_once("offline-peer", "STATUS: new-token")
                except b.DispatchUnconfirmed:
                    pass
                else:
                    raise AssertionError("draft accepted")
        for suffix in (" unknown", "\n  " + permissions):
            unknown = screen.replace(permissions, permissions + suffix)
            assert b._claude_bordered_compose(unknown) is None
        assert b._queued_or_active_input(before(screen, "Messages to be submitted after completion:\n  queued"))
    paste.assert_not_called()
    key.assert_not_called()
# Real transformed receipt reader, real fixture files, original EOF fences.
# This checks receipt semantics only; no live process or live reception claim.
native_root = Path(NATIVE_ROOT)
log_root = native_root / "projects/offline-chrome-receipts"
log_root.mkdir(parents=True)
payload = "STATUS: materialized-exact-receipt"
for kind in ("none", "changed", "queue", "stale", "exact"):
    sid = str(uuid.uuid4())
    log = log_root / (sid + ".jsonl")
    def append(record):
        with log.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    def user(text):
        return dict(type="user", sessionId=sid, timestamp=datetime.now(timezone.utc).isoformat(),
                    message=dict(role="user", content=text))
    append(dict(type="system", sessionId=sid))
    binding = dict(schema=native.SCHEMA, provider="claude", session_id=sid,
        native_config_root=str(native_root), bound_at_epoch=time.time(),
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        identity={k:str(uuid.uuid4()) for k in native.IDENTITY_KEYS},
        transcript=native._pin_file(log, "claude", sid))
    if kind == "stale":
        append(user(payload))
    fence = native.capture_paste_fence(binding)
    intent_at = time.time()
    if kind in ("exact", "changed"):
        append(user(payload if kind == "exact" else payload + " changed"))
    elif kind == "queue":
        append(dict(type="attachment", sessionId=sid, timestamp=datetime.now(timezone.utc).isoformat(),
                    attachment=dict(type="queued_command", prompt=payload)))
    result = native.probe(binding, payload, paste_fence=fence, not_before=intent_at)
    assert result["confirmed"] is (kind == "exact"), (kind, result)
    if kind == "queue":
        assert result["state"] == "NATIVE_QUEUED"
    if kind == "exact":
        assert native.validate_proof(binding, payload, result["native_proof"],
                                     paste_fence=fence, not_before=intent_at)
# Keep the fixed base native route byte-for-byte after full materialization.
source = Path(b.__file__).read_text()
base = Path(BASE).read_text()
for name in ("_submit_text_once", "_submit_terminal_once", "_observe_native"):
    snippets = [ast.get_source_segment(t, next(n for n in ast.parse(t).body
                if getattr(n, "name", None) == name)) for t in (source, base)]
    assert snippets[0] == snippets[1], name
print("MATERIALIZED_CLAUDE_CHROME_PASS: default/Tip/draft/queue/native-route/receipt-fences")
'''.replace("CONTROLLER", repr(str(controller))).replace("SCREENS", repr(screens)).replace(
            "BASE", repr(str(self.source_controller / "scripts/cmux_bridge.py"))).replace(
            "NATIVE_ROOT", repr(self.spec["clients"]["claude_a"]["native_root"]))
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MATERIALIZED_CLAUDE_CHROME_PASS", result.stdout)

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
    def test_materialized_body_reader_complete_result_inside_private_scope(self):
        # Run the actual generated executable, not a copied parser or a mocked read.
        from cmux_prompt_reference import plan, persist
        from upgrade_body_read import reader_instructions
        body = "STATUS: body-read-probe\n完整正文 ' $() ` end\n"
        value = plan(body, "body-read-probe", self.paths["state"] / "body-read-fixture")
        persist(value["reference"], body)
        reader = Path(self.spec["controller"]) / "scripts/mac_upgrade_body_read.py"
        self.assertIn("_upgrade_install_scope(live=True)", reader.read_text())
        argv = [str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                sys.executable, "-B", str(reader), "--notice", value["text"]]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["body"], body)
        self.assertEqual(observed["reference"], value["reference"])
        for name in ("AGENTS.md", "CLAUDE.md"):
            policy = Path(self.spec["pins"]["work_" + name]["path"]).read_text()
            self.assertIn(reader_instructions(self.spec["pins"]["python"]["path"],
                          self.spec["controller"], self.spec["pins"]["rtk"]["path"]), policy)
        changed = value["text"].replace("BYTES=" + str(value["reference"]["bytes"]), "BYTES=1")
        result = subprocess.run(argv[:-1] + [changed], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_materialized_readonly_handshake_and_original_native_fences(self):
        controller = Path(self.spec["controller"]) / "scripts"
        probe = Path(__file__).with_name("upgrade_handshake_probe.py").read_text()
        code = (probe + "\nimport sys\n" + f"sys.path.insert(0, {str(controller)!r})\n" +
                f"print(json.dumps(run({str(controller)!r}, " +
                f"{self.spec['clients']['claude_a']['native_root']!r})))\n")
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads(result.stdout)
        self.assertEqual(counts['checks'], 12)
        self.assertEqual(counts['model_requests'], 0)
        self.assertEqual(counts['native_inputs'], 0)

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_materialized_handshake_contract_errors_keep_original_journals(self):
        controller = Path(self.spec["controller"]) / "scripts"
        probe = Path(__file__).with_name("upgrade_handshake_probe.py").read_text()
        code = (probe + "\nimport sys\n" + f"sys.path.insert(0, {str(controller)!r})\n" +
                f"print(json.dumps(run_settlement({str(controller)!r}, " +
                f"{self.spec['clients']['claude_a']['native_root']!r})))\n")
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads(result.stdout)
        self.assertEqual(counts['checks'], ['before_input', 'paste_intent', 'missing_details'])
        self.assertEqual(counts['model_requests'], 0)
        self.assertEqual(counts['native_inputs'], 0)

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_materialized_callback_wait_keeps_single_attempt_and_fresh_gates(self):
        controller = Path(self.spec["controller"]) / "scripts"
        probe = Path(__file__).with_name("upgrade_callback_wait_probe.py").read_text()
        source = self.source_controller / "scripts/cmux_callback_journal.py"
        before = source.read_bytes()
        code = ("import sys, json\nfrom pathlib import Path\n"
                f"sys.path.insert(0, {str(controller)!r})\n"
                "import cmux_callback_journal as journal\n"
                f"assert Path(journal.__file__).parent == Path({str(controller)!r})\n" +
                probe + "\n" +
                f"result = run(journal, {str(self.paths['tmp'] / 'callback-wait-probe')!r}, "
                f"{str(self.paths['state'] / 'deliveries-v1')!r})\n"
                f"result['pressure'] = pressure(journal, {str(self.paths['tmp'] / 'callback-wait-pressure')!r}, "
                f"{str(self.paths['state'] / 'deliveries-v1')!r})\n"
                "print(json.dumps(result))\n")
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads(result.stdout)
        self.assertEqual(len(counts["checks"]), 16 if self.source_controller == SOURCE else 17)
        self.assertEqual(counts["model_requests"], 0)
        self.assertEqual(counts["native_inputs"], 0)
        self.assertEqual(counts["pressure"]["original_invocations"], 80)
        self.assertEqual(counts["pressure"]["workers"], 8)
        self.assertEqual(source.read_bytes(), before)
        changes = json_ref(self.runtime["patches"])["changes"]
        callback = [c for c in changes if c["path"] == "scripts/cmux_callback_journal.py"]
        self.assertEqual(len(callback), 1)
        self.assertIn("bounded_first_callback_pane_wait_fresh_gates", callback[0]["transformations"])

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_materialized_claude_peer_registration_and_pid_reuse(self):
        controller = Path(self.spec["controller"]) / "scripts"
        root = Path(self.spec["clients"]["claude_a"]["native_root"])
        code = f'''
import copy, json, sys, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, {str(controller)!r})
import cmux_native_delivery as native
import mac_upgrade_claude_registration as registration
assert registration.__file__ == {str(controller / 'mac_upgrade_claude_registration.py')!r}
root = Path({str(root)!r})
sid = '00000000-0000-0000-0000-000000000001'
p = dict(pid=987654, birth=[int(time.time())-10, 500000], executable='/fixture/claude',
         env=dict(HOME=str(Path.home()), CLAUDE_CONFIG_DIR=str(root)))
path = root / 'sessions/987654.json'
reg = dict(pid=p['pid'], kind='interactive', sessionId=sid, version='2.1.170',
           peerProtocol=1, entrypoint='cli', startedAt=(p['birth'][0]+4)*1000)
path.write_text(json.dumps(reg))
with patch.object(native.identity, 'process', side_effect=lambda pid: copy.deepcopy(p)):
    assert native._live_session(p, 'claude') == sid
    reg['startedAt'] = (p['birth'][0]-1)*1000
    path.write_text(json.dumps(reg))
    try:
        native._live_session(p, 'claude')
    except native.NativeDeliveryError as exc:
        assert str(exc) == 'NATIVE_REGISTRATION_PROCESS_MISMATCH'
    else:
        raise AssertionError('stale PID registration accepted')
print('MATERIALIZED_CLAUDE_REGISTRATION_PASS')
'''
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MATERIALIZED_CLAUDE_REGISTRATION_PASS", result.stdout)

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_materialized_standalone_tool_hook_and_public_proof(self):
        # Execute the actual transformed collectors in a fresh interpreter.
        # Only kernel/foreground observations are fixtures; dispatch is absent.
        controller = Path(self.spec["controller"]) / "scripts"
        code = f'''
import copy, os, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, {str(controller)!r})
import cmux_daemon_identity as d
import cmux_foreground_thread as foreground
import mac_upgrade_standalone_identity as standalone
import mac_upgrade_process_inventory as inventory
assert standalone.__file__ == {str(controller / 'mac_upgrade_standalone_identity.py')!r}
scope = dict(CMUX_SURFACE_ID='00000000-0000-0000-0000-000000000002',
             CMUX_WORKSPACE_ID='00000000-0000-0000-0000-000000000003')
session = '00000000-0000-0000-0000-000000000001'
env = dict(scope, CODEX_THREAD_ID=session)
client = dict(pid=44992, ppid=1, birth=[1000, 1], executable='/native/codex',
              argv=['/native/codex', '--disable', 'computer_use'], env=scope)
server = dict(pid=45091, ppid=44992, birth=[1001, 1], executable='/native/codex',
              argv=['/native/codex', 'app-server', '--listen'], env=scope)
current = dict(pid=os.getpid(), ppid=45091, birth=[1002, 1], executable='/native/python',
               argv=['/native/python'], env=env)
chain = [server, client]
with patch.object(d.sys, 'platform', 'darwin'), \\
     patch.object(d.os, 'getppid', return_value=45091), \\
     patch.object(d, '_ancestry', side_effect=lambda: (copy.deepcopy(chain), None)), \\
     patch.object(d, 'process', side_effect=lambda pid: copy.deepcopy(next(p for p in [current]+chain if p['pid']==pid))), \\
     patch.object(foreground, 'read', return_value=dict(thread_id=session)), \\
     patch.object(inventory, 'terminal', return_value='ttys88'):
    tool = d.collect(env)
    hook = d.collect_hook(env, dict(session_id=session, hook_event_name='PostToolUse'))
    assert tool == hook
    public = d.public_proof(tool)
    assert public['daemon_pid'] is None and public['foreground']['thread_id'] == session
    tree = dict(windows=[dict(ref='window:1',workspaces=[dict(id=scope['CMUX_WORKSPACE_ID'],ref='workspace:1',panes=[dict(id='pane-uuid',ref='pane:1',surfaces=[dict(id=scope['CMUX_SURFACE_ID'],ref='surface:1',type='terminal',tty='ttys88')])])])])
    identity = dict(caller=dict(surface_ref='surface:1',workspace_ref='workspace:1',pane_ref='pane:1'))
    resolved, resolved_env = d.resolve(identity, tree, env, tool)
    assert resolved['caller']['surface_id'] == scope['CMUX_SURFACE_ID']
    assert resolved_env == env
    bad = copy.deepcopy(tree)
    bad['windows'][0]['workspaces'][0]['panes'][0]['surfaces'][0]['tty'] = 'ttys99'
    try:
        d.resolve(identity, bad, env, tool)
    except d.IdentityError:
        pass
    else:
        raise AssertionError('wrong surface TTY accepted')
print('MATERIALIZED_STANDALONE_TOOL_HOOK_PASS')
'''
        result = subprocess.run([str(SANDBOX), "-f", self.spec["pins"]["codex_sandbox"]["path"],
                                 sys.executable, "-I", "-B", "-c", code],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MATERIALIZED_STANDALONE_TOOL_HOOK_PASS", result.stdout)

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_full_codex_wrapper_joins_real_startup_argv_once_without_model(self):
        # Run the entire materialized wrapper and final exec inside Seatbelt.
        # Only the external CLI emitter/model executable are replaced by inert
        # fixtures; no live cmux command, native client or credential is used.
        capture = self.root / "capture-argv"
        capture.write_text("#!/bin/bash\nexec " + shlex.quote(sys.executable) +
                           " -I -B -c 'import json,sys; print(json.dumps(sys.argv[1:]))' \"$@\"\n")
        capture.chmod(0o755)
        emitter = self.root / "emit-hooks"
        emitter.write_text("#!/bin/bash\n" +
            '[[ "$*" == "hooks codex inject-args" ]] || exit 73\n' +
            '[[ "$UPGRADE_TEST_EMIT" == "empty" ]] && exit 0\n' +
            '[[ "$UPGRADE_TEST_EMIT" == "error" ]] && exit 1\n' +
            "printf '%s\\0' --enable hooks --dangerously-bypass-hook-trust -c " +
            shlex.quote('hooks.PreToolUse=[{hooks=[{type="command",command="/fixture/cmux-hook"}]}]') + "\n")
        emitter.chmod(0o755)
        context = init_experiment(self.root / "j", "argv-join", self.source_controller)
        binaries = dict(self.binaries, codex=capture, cmux=emitter)
        runtime = json_ref(materialize_live(context, "B", binaries=binaries, provider_configuration=({}, {})))
        spec, _ = validate_startup(runtime["startup"])
        client = spec["clients"]["codex"]
        env = clean_child_environment(client["environment"])
        env.update(CMUX_SURFACE_ID=str(uuid.uuid4()), CMUX_WORKSPACE_ID=str(uuid.uuid4()),
                   CMUX_BUNDLED_CLI_PATH=str(emitter), CMUX_COMPUTER_USE_APP_ENABLED="0",
                   CMUX_COMPUTER_USE_MCP_DISABLED="1")
        env.pop("CMUX_SOCKET_PATH", None)
        for mode in ("normal", "empty", "error"):
            with self.subTest(mode=mode):
                env["UPGRADE_TEST_EMIT"] = mode
                result = subprocess.run([str(SANDBOX), "-f", spec["pins"]["codex_sandbox"]["path"],
                                         *client["argv"]], env=env, cwd=spec["work_root"],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(result.stdout)
                flag = "--dangerously-bypass-hook-trust"
                self.assertEqual(args.count(flag), 1)
                self.assertEqual(args[-1], client["argv"][-1])  # exact original readiness prompt
                self.assertEqual(args[args.index("--remote") + 1], "unix://" + spec["rpc"]["path"])
                for arg in client["argv"][1:]:
                    self.assertIn(arg, args)
                self.assertEqual("--enable" in args, mode == "normal")
                self.assertEqual(any("/fixture/cmux-hook" in arg for arg in args), mode == "normal")
        self.assertFalse(Path(spec["rpc"]["path"]).exists())
        self.assertFalse(Path(spec["adoption_path"]).exists())

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists(), "requires macOS kernel isolation")
    def test_materialized_inventory_and_controlling_tty_inside_seatbelt(self):
        controller = Path(self.spec["controller"]) / "scripts"
        support = Path(__file__).resolve().parent / "upgrade_process_inventory.py"
        self.assertEqual((controller / "mac_upgrade_process_inventory.py").read_text(),
                         private_imports(support.read_text()))
        def function_tree(path, name):
            return next(node for node in ast.parse(path.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == name)
        # Discovery changes must not replace the pinned base's strict reader.
        self.assertEqual(ast.dump(function_tree(controller / "cmux_daemon_identity.py", "process")),
                         ast.dump(function_tree(self.source_controller / "scripts/cmux_daemon_identity.py", "process")))
        for module, entrypoint, discovery in (("cmux_daemon_identity.py", "client_candidates", "inventory"),
                                   ("cmux_native_delivery.py", "_target_process", "inventory_for_tty")):
            calls = [node for node in ast.walk(function_tree(controller / module, entrypoint))
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and isinstance(node.func.value, ast.Name)
                     and node.func.value.id == "private_process" and node.func.attr == discovery]
            self.assertEqual(len(calls), 1, (module, entrypoint))
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
import cmux_native_delivery as delivery
assert inventory.__file__ == {str(controller / 'mac_upgrade_process_inventory.py')!r}
assert identity.private_process is inventory
assert delivery.private_process is inventory
assert identity.private_process.inventory is inventory.inventory
assert delivery.private_process.inventory_for_tty is inventory.inventory_for_tty
p = identity.process(os.getpid())
rows = inventory.inventory(lambda name: Path(name).name == Path(p['executable']).name)
assert sum(int(pid) == os.getpid() for pid, path in rows) == 1
expected_tty = os.environ['TEST_CONTROLLING_TTY']
assert inventory.terminal(p) == expected_tty
if expected_tty != '?':
    rows = inventory.inventory_for_tty(lambda name: Path(name).name == Path(p['executable']).name, expected_tty)
    assert rows == [[str(os.getpid()), p['executable']]], rows
try:
    inventory.terminal(dict(p, birth=[1, 2]))
except identity.IdentityError:
    pass
else:
    raise AssertionError('changed birth accepted')
# Exercise the materialized delivery gate with real kernel identity/TTY.
delivery.private_process.inventory_for_tty = lambda candidate, tty: [[str(os.getpid()), p['executable']]]
delivery.private_roots.candidate_process_matcher = lambda: lambda path: True
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

    @unittest.skipUnless(sys.platform == "darwin" and SANDBOX.exists()
                         and Path("/Users/lzhs/.nvm/versions/node/v24.16.0/bin/node").is_file(),
                         "requires macOS and local Node; no model client is launched")
    def test_native_node_self_query_inside_both_claude_profiles(self):
        # Match Claude's observed execFile query using a real native process.
        # Compare its answer with system ps outside Seatbelt while the same PID
        # is still alive. No fabricated registry or model adoption is involved.
        outside = self.root / "ps-outside-sentinel"
        outside.write_text("unchanged")
        helper = self.spec["pins"]["claude_ps"]["path"]
        node = "/Users/lzhs/.nvm/versions/node/v24.16.0/bin/node"
        script = r'''
const cp = require('child_process'), fs = require('fs');
const args = ['-o', 'lstart=', '-p', String(process.pid)];
const result = cp.spawnSync('ps', args, {encoding: 'utf8', timeout: 10000});
const system = cp.spawnSync('/bin/ps', args, {encoding: 'utf8', timeout: 10000});
const denied = [];
for (const path of [process.argv[1], process.argv[2]]) {
  try { fs.writeFileSync(path, 'escape'); denied.push(false); }
  catch (error) { denied.push(error.code === 'EPERM' || error.code === 'EACCES'); }
}
console.log(JSON.stringify({pid: process.pid, code: result.status,
  stdout: result.stdout, stderr: result.stderr, error: result.error?.message,
  systemError: system.error?.code, denied}));
fs.readSync(0, Buffer.alloc(1), 0, 1, null);
'''
        for lane in ("claude_a", "claude_b"):
            with self.subTest(lane=lane):
                env = clean_child_environment(self.spec["clients"][lane]["environment"])
                with subprocess.Popen([str(SANDBOX), "-f", self.spec["pins"][lane + "_sandbox"]["path"],
                        node, "-e", script, str(outside), helper], env=env, cwd=self.spec["work_root"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
                    try:
                        import selectors
                        with selectors.DefaultSelector() as selector:
                            selector.register(child.stdout, selectors.EVENT_READ)
                            self.assertTrue(selector.select(15), "native self-query timed out")
                        row = json.loads(child.stdout.readline())
                        self.assertEqual(row["pid"], child.pid)
                        self.assertEqual(row["code"], 0, row)
                        self.assertEqual(row["systemError"], "EPERM")
                        self.assertEqual(row["denied"], [True, True])
                        oracle = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(child.pid)],
                                                capture_output=True, text=True, timeout=5, check=True)
                        # System ps pads its display column; Claude stores the
                        # trimmed value. The helper emits just that value + LF.
                        self.assertEqual(row["stdout"], oracle.stdout.strip() + "\n")
                    finally:
                        child.communicate("x", timeout=5)
                self.assertEqual(child.returncode, 0)
        self.assertEqual(outside.read_text(), "unchanged")
        self.assertNotIn("claude-tools", self.spec["clients"]["codex"]["environment"]["PATH"])

    def test_rehashed_changed_ps_helper_and_removed_private_path_refuse(self):
        path, helper = Path(self.ref["path"]), Path(self.spec["pins"]["claude_ps"]["path"])
        original_spec, original_helper = path.read_bytes(), helper.read_bytes()
        try:
            helper.chmod(0o755)
            helper.write_text("#!/bin/sh\nprintf 'invented timestamp\\n'\n")
            helper.chmod(0o555)
            altered = copy.deepcopy(self.spec)
            altered["pins"]["claude_ps"] = artifact_ref(helper)
            path.chmod(0o644)
            path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ContractError, "STARTUP_CLAUDE_PS_CHANGED"):
                validate_startup(file_ref(path))
            helper.chmod(0o755)
            helper.write_bytes(original_helper)
            helper.chmod(0o555)
            altered = copy.deepcopy(self.spec)
            altered["clients"]["claude_a"]["environment"]["PATH"] = altered["clients"]["codex"]["environment"]["PATH"]
            path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ContractError, "STARTUP_ARGUMENTS_OR_ENV_CHANGED"):
                validate_startup(file_ref(path))
        finally:
            helper.chmod(0o755)
            helper.write_bytes(original_helper)
            helper.chmod(0o555)
            path.write_bytes(original_spec)
            path.chmod(0o444)

    def test_long_temp_path_refuses_before_materializing_arm_artifacts(self):
        context_ref = init_experiment(self.root / ("long-root-" * 3), "long-startup", self.source_controller)
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


@unittest.skipUnless(MODERN_SOURCE.is_dir() and WRAPPERS.is_dir(), "requires complete MAC 0.4.58 and cmux wrappers")
class MaterializedMAC058StartupTests(MaterializedStartupTests):
    """Run the same real generated-controller probes against current upstream."""
    __unittest_skip__ = False
    source_controller = MODERN_SOURCE


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
    def test_frozen_name_matcher_does_not_cache_selected_executable_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / '2.1.226'
            path.write_bytes(b'original content')
            pin = artifact_ref(path)
            st = path.stat()
            proc = dict(pid=23, birth=[1, 2], executable=str(path),
                        file_identity=[st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns])
            with patch.object(roots, 'executable_pins', return_value={'claude': pin}), \
                    patch.object(roots, 'process_root', return_value=path.parent):
                candidate = roots.candidate_process_matcher()
                self.assertEqual(roots.executable_provider(proc), 'claude')
                self.assertIsNone(roots.executable_provider(dict(proc, executable='/other/2.1.226')))
                path.write_bytes(b'modified content')
                os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
                self.assertTrue(candidate(str(path)))
                with self.assertRaises((ContractError, ValueError)):
                    roots.executable_provider(proc)

    def test_discovery_names_are_captured_once_but_next_inventory_is_fresh(self):
        pins = {'codex': {'path': '/pinned/codex'}, 'claude': {'path': '/pinned/2.1.226'}}
        with patch.object(roots, 'executable_pins', return_value=pins) as read:
            candidate = roots.candidate_process_matcher()
            for _ in range(10):
                self.assertTrue(candidate('/pinned/codex'))
                self.assertTrue(candidate('2.1.226'))
                self.assertFalse(candidate('/unrelated/sleep'))
            pins['claude']['path'] = '/pinned/2.1.227'
            self.assertTrue(candidate('2.1.226'))
            self.assertFalse(candidate('2.1.227'))
            read.assert_called_once()
            fresh = roots.candidate_process_matcher()
            self.assertTrue(fresh('2.1.227'))
            self.assertFalse(fresh('2.1.226'))
            self.assertEqual(read.call_count, 2)

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
