"""Versioned, source-shape-checked changes for new isolated clients only."""
from __future__ import annotations

import ast

from upgrade_controller_patch import edit_nodes, replace_once, transform_python


PRIVATE_MODULES = {name: "mac_" + name for name in (
    "upgrade_controller_scope", "upgrade_handshake_identity", "upgrade_native_roots",
    "upgrade_client_isolation", "upgrade_live_entry", "upgrade_live_integrity",
    "upgrade_client_probe", "upgrade_startup_contract", "upgrade_hook_observer",
    "upgrade_input_gate", "upgrade_live_inspect", "upgrade_bootstrap",
    "upgrade_process_inventory", "upgrade_socket_boundary", "upgrade_claude_ps",
    "upgrade_standalone_identity", "upgrade_claude_registration")}
PRIVATE_MODULES["orchestration_common"] = "mac_upgrade_common"


def private_imports(text):
    """Separate the controller's complete support closure from the adapter.

    Keep module-origin checks strict. No sys.modules swapping or import hook is
    needed when the adapter and controller coexist in a process.
    """
    edits = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.module in PRIVATE_MODULES:
            edits.append((node, "from " + PRIVATE_MODULES[node.module] + " import " +
                          ", ".join(a.name + (" as " + a.asname if a.asname else "") for a in node.names)))
        elif isinstance(node, ast.Import) and any(a.name in PRIVATE_MODULES for a in node.names):
            edits.append((node, "import " + ", ".join(
                PRIVATE_MODULES.get(a.name, a.name) +
                (" as " + (a.asname or a.name) if a.name in PRIVATE_MODULES else
                 (" as " + a.asname if a.asname else "")) for a in node.names)))
    return edit_nodes(text, edits)


def native_readers(name, text):
    if name == "cmux_codex_rpc.py":
        return replace_once(text,
            "DEFAULT_SOCKET = Path.home() / '.codex/app-server-control/app-server-control.sock'",
            "from upgrade_native_roots import codex_control_socket\nDEFAULT_SOCKET = codex_control_socket()")
    if name == "cmux_daemon_identity.py":
        text = replace_once(text,
            "'HOME', 'CODEX_HOME', 'CODEX_CLIENT_THREAD_OBSERVER')",
            "'HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'CODEX_CLIENT_THREAD_OBSERVER')")
        text = replace_once(text, "import cmux_identity_budget as budget",
                            "import cmux_identity_budget as budget\nimport upgrade_process_inventory as private_process")
        node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "client_candidates")
        text = edit_nodes(text, [(node, 'def client_candidates():\n'
            '    return [pid for pid, path in private_process.inventory(lambda name: Path(name).name == "codex")]')])
        return standalone_identity(_terminal_reads(text))
    if name == "cmux_native_delivery.py":
        text = replace_once(text,
            "            births = {time.strftime('%a %b %e %H:%M:%S %Y', clock(p[\"birth\"][0]))\n"
            "                      for clock in (time.gmtime, time.localtime)}\n"
            '            if (not isinstance(reg, dict) or reg.get("pid") != p["pid"]\n'
            '                    or reg.get("kind") != "interactive" or reg.get("procStart") not in births):\n'
            '                raise NativeDeliveryError("NATIVE_REGISTRATION_PROCESS_MISMATCH")',
            '            from upgrade_claude_registration import matches_process\n'
            '            if not matches_process(reg, p, observed_at=time.time()):\n'
            '                raise NativeDeliveryError("NATIVE_REGISTRATION_PROCESS_MISMATCH")\n'
            '            if identity.process(p["pid"]) != p:\n'
            '                raise NativeDeliveryError("NATIVE_PROCESS_CHANGED")')
        text = replace_once(text, "import cmux_foreground_thread as foreground",
                            "import cmux_foreground_thread as foreground\nimport upgrade_native_roots as private_roots\n"
                            "import upgrade_process_inventory as private_process")
        text = replace_once(text,
            '    listing = subprocess.run(["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,comm="],\n'
            '                             capture_output=True, text=True, timeout=3, check=True)\n'
            '    matches = []\n'
            '    for line in listing.stdout.splitlines():\n'
            '        fields = line.strip().split(None, 1)',
            '    candidate = private_roots.candidate_process_matcher()\n'
            '    matches = []\n    for fields in private_process.inventory_for_tty(candidate, row["tty"]):')
        text = _terminal_reads(text)
        provider = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "_provider")
        text = edit_nodes(text, [(provider, "def _provider(p):\n    return private_roots.executable_provider(p)")])
        text = replace_once(text,
            'if Path(fields[1]).name not in ("codex", "claude", "claude.exe", "node"):',
            'if not candidate(fields[1]):')
        text = replace_once(text, 'Path.home() / ".claude/sessions" / (str(p["pid"])+".json")',
                            'private_roots.process_root("claude", p) / "sessions" / (str(p["pid"])+".json")')
        start = text.index("def _native_root(provider):")
        end = text.index("def _transcript_path(provider, session):", start)
        text = text[:start] + (
            "def _native_root(provider, *, process=None, binding=None):\n"
            "    return private_roots.transcript_root(provider, process=process, binding=binding)\n\n"
        ) + text[end:]
        text = replace_once(text, "def _transcript_path(provider, session):",
                            "def _transcript_path(provider, session, process):")
        text = replace_once(text, "_native_root(provider).rglob(pattern)",
                            "_native_root(provider, process=process).rglob(pattern)")
        text = replace_once(text, "path = _transcript_path(provider, session)",
                            "path = _transcript_path(provider, session, p)")
        text = replace_once(text, "provider=provider, session_id=session, transcript=_pin_file(path, provider, session),",
                            "provider=provider, session_id=session, transcript=_pin_file(path, provider, session),\n"
                            "        native_config_root=str(private_roots.process_root(provider, p)),")
        text = replace_once(text, '_native_root(b["provider"]).resolve()',
                            '_native_root(b["provider"], binding=b).resolve()')
        text = replace_once(text, '    if _public_process(p) != b["process"]:',
                            '    if private_roots.process_root(b["provider"], p) != private_roots.binding_root(b):\n'
                            '        raise NativeDeliveryError("NATIVE_PROCESS_CONFIGURATION_CHANGED")\n'
                            '    if _public_process(p) != b["process"]:')
    elif name == "cmux_hook_thread.py":
        text = replace_once(text,
            "bases = (Path.home() / '.codex/sessions', Path.home() / '.codex/archived_sessions')",
            "from upgrade_native_roots import hook_codex_roots\n        bases = hook_codex_roots()")
    elif name == "callback_native_evidence.py":
        text = replace_once(text, "import cmux_native_delivery as delivery",
                            "import cmux_native_delivery as delivery\nfrom upgrade_native_roots import configured_root")
        text = replace_once(text, "(Path.home() / '.codex/sessions').resolve()",
                            "(configured_root('codex') / 'sessions').resolve()")
    elif name == "claude_maintenance_context.py":
        text = replace_once(text, "if (Path.home() / '.claude/projects') not in log.parents:",
                            "from upgrade_native_roots import transcript_root\n"
                            "    if transcript_root('claude', binding=binding) not in log.parents:")
    return text


def standalone_identity(text):
    """Patch only the two absent-daemon branches; keep managed proof intact."""
    text = replace_once(text, "import cmux_foreground_thread as foreground",
                        "import cmux_foreground_thread as foreground\n"
                        "import upgrade_standalone_identity as private_standalone")
    edits = []
    for name, arguments in (("collect", "env, chain"),
                            ("collect_hook", "env, chain, payload=payload")):
        nodes = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == name]
        if len(nodes) != 1:
            raise ValueError("expected native collector missing: " + name)
        node = nodes[0]
        source = ast.get_source_segment(text, node)
        replacement = replace_once(source, "if daemon is None:\n        return None",
            "if daemon is None:\n        return private_standalone.collect(" + arguments + ")")
        edits.append((node, replacement))
    text = edit_nodes(text, edits)
    text = replace_once(text, "source, caller = by_env(daemon['env']), by_env(client['env'])",
        "origin = daemon if daemon is not None else client\n"
        "    source, caller = by_env(origin['env']), by_env(client['env'])")
    return replace_once(text, "daemon_pid=proof['daemon']['pid'],",
        "daemon_pid=proof['daemon']['pid'] if proof['daemon'] is not None else None,")


def _terminal_reads(text):
    edits = []
    for node in ast.walk(ast.parse(text)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "tty"
                and "/bin/ps" in ast.get_source_segment(text, node)):
            edits.append((node, "tty = private_process.terminal(p)"))
    if not edits:
        raise ValueError("expected native terminal reads missing")
    return edit_nodes(text, edits)


def claude_measured_chrome(text):
    """Add measured default-footer/Tip support without replacing the base bridge.

    The fixed base owns native Codex delivery and newer HUD layouts. Only two
    exact source spans may change; unknown or already-patched shapes refuse.
    """
    from orchestration_common import ContractError
    markers = {
        "_claude_bordered_compose": (
            '    if not footer:\n        return None\n',
            '    if not footer:\n        return None\n' + r'''    # Exact measured default chrome, strictly outside both complete borders.
    if len(footer) == 1 and footer[0] in (
            "⏵⏵ bypass permissions on (shift+tab to cycle)",
            "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"):
        first = re.sub(r"^\s*❯[ \u00a0]?", "", rows[start], count=1)
        return {"body": "\n".join([first, *rows[start + 1:end]]),
                "footer": footer, "before": rows[:start - 1], "start": start, "end": end}
'''),
        "_queued_or_active_input": (
            '        screen = next((row for row in reversed(claude["before"]) if row.strip()), "")\n',
            r'''        # Skip only complete hint chrome before the composer, never prose/drafts.
        before = list(claude["before"])
        while before and (not before[-1].strip()
                          or re.fullmatch(r"\s*⎿\s+Tip:[ \t]+[^\r\n]+", before[-1])
                          or re.fullmatch(rf"\s*◎\s+/goal active\s+\({_CLAUDE_DURATION}\)\s*", before[-1])):
            before.pop()
        screen = before[-1] if before else ""
        if re.fullmatch(r"\s*[·✻✢✳✶✽◐◑◒◓]\s+(?:[A-Za-z][A-Za-z-]*|Compacting (?:conversation|context))(?:…|\.\.\.)\s*", screen):
            return True
'''),
    }
    edits = []
    for name, (old, new) in markers.items():
        nodes = [n for n in ast.parse(text).body
                 if isinstance(n, ast.FunctionDef) and n.name == name]
        if len(nodes) != 1:
            raise ContractError("private Claude chrome function shape changed: " + name)
        node = nodes[0]
        original = ast.get_source_segment(text, node)
        if new in original:
            raise ContractError("private Claude chrome already patched: " + name)
        edits.append((node, replace_once(original, old, new)))
    return edit_nodes(text, edits)


def live_gates(name, text):
    if name == "cmux_bridge.py":
        text = claude_measured_chrome(text)
        for original in ('proof = pin_workspace(params["surface_id"])', 'proof = pin_workspace(args[index])'):
            text = replace_once(text, original, original +
                "\n        from upgrade_input_gate import check_input\n        check_input(proof)")
        node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "_run_successor")
        text = edit_nodes(text, [(node, 'def _run_successor(*args, artifact, check=True, capture=True):\n'
            '    raise WorkspaceScopeError("EXPERIMENT_SUCCESSOR_ROUTE_REFUSED: dedicated workspace only")')])
    elif name == "cmux_codex_input.py":
        text = replace_once(text, "from cmux_codex_rpc import DaemonRPC", "from cmux_codex_rpc import DaemonRPC\n"
                            "from upgrade_input_gate import check_input")
        text = replace_once(text, '        emit(INPUT_PHASE, evidence={"native_request": request})',
            '        check_input(binding["identity"], native_binding=binding)\n'
            '        emit(INPUT_PHASE, evidence={"native_request": request})')
        text = replace_once(text, '            result = client.request(request["method"], request["params"])',
            '            check_input(binding["identity"], native_binding=binding)\n'
            '            result = client.request(request["method"], request["params"])')
    elif name == "cmux_hook_runtime_audit.py":
        marker = "        session = str(uuid.UUID(payload.get('session_id') or payload.get('sessionId')))"
        text = replace_once(text, marker,
            "        if advisory and hook:\n"
            "            try:\n"
            "                from upgrade_hook_observer import record as observe_upgrade_hook\n"
            "                observe_upgrade_hook(payload, original_hook=hook)\n"
            "            except Exception:\n"
            "                pass  # Observation failure never blocks ordinary tools.\n" + marker)
    return text


def transform_live(name, text, paths, controller, *, compatibility):
    original = text
    text = native_readers(name, text)
    native_changed = text != original
    before_gates = text
    text = live_gates(name, text)
    gates_changed = text != before_gates
    if name == "mac_harness.py":
        # This remains the harness double gate, with a structured, pinned
        # startup contract. It never uses the legacy paste-to-shell launcher.
        text = replace_once(text, "def main():\n", "def main():\n"
            "    if len(sys.argv) > 1 and sys.argv[1] in ('experiment-launch', 'experiment-client', 'experiment-inspect'):\n"
            "        from upgrade_live_entry import main as experiment_main\n"
            "        raise SystemExit(experiment_main(sys.argv[1:]))\n")
    text, changes = transform_python(name, text, paths, controller, compatibility=compatibility)
    text = replace_once(text, "_upgrade_install_scope()", "_upgrade_install_scope(live=True)")
    text = private_imports(text)
    changes.append("live_v2_kernel_boundary")
    if native_changed:
        changes.append("authenticated_private_native_roots")
    if gates_changed:
        changes.append("original_input_adoption_gate_or_automatic_hook_observer")
    if name == "mac_harness.py":
        changes.append("structured_experiment_startup_double_gate")
    if name == "cmux_bridge.py":
        changes.append("measured_claude_default_footer_and_tip")
    compile(text, name, "exec", dont_inherit=True)
    return text, changes
