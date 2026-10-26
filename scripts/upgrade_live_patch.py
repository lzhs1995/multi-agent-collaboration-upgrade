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
    "upgrade_standalone_identity", "upgrade_claude_registration", "upgrade_handshake_capability",
    "upgrade_body_read", "upgrade_toml", "upgrade_toml_re",
    "upgrade_child_hook", "upgrade_child_budget_protocol", "upgrade_native_outcomes", "upgrade_startup_continuity",
    "upgrade_readiness_evidence", "upgrade_stage_fence")}
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
        inventory_prefix = (
            '    listing = subprocess.run(["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,comm="],\n'
            '                             capture_output=True, text=True, timeout=3, check=True)\n'
            '    matches = []\n'
            '    for line in listing.stdout.splitlines():\n'
            '        fields = line.strip().split(None, 1)')
        modern_prefix = inventory_prefix.replace('["/bin/ps", "-U",', '["/bin/ps", "-ww", "-U",')
        shell_prefix = modern_prefix.replace('    matches = []\n', '    matches, shells = [], []\n')
        shell_diagnostics = (text.count(shell_prefix) == 1
                             and inventory_prefix not in text and modern_prefix not in text)
        if shell_diagnostics:
            inventory_prefix = shell_prefix
        elif text.count(modern_prefix) == 1 and inventory_prefix not in text:
            inventory_prefix = modern_prefix
        inventory_replacement = (
            '    candidate = private_roots.candidate_process_matcher()\n'
            '    matches = []\n    for fields in private_process.inventory_for_tty(candidate, row["tty"]):')
        if shell_diagnostics:
            # Shell rows are diagnostics only. Agent selection below still
            # requires the frozen executable matcher and private provider roots.
            inventory_replacement = (
                '    candidate = private_roots.candidate_process_matcher()\n'
                "    diagnostic_candidate = lambda value: candidate(value) or Path(value).name in ('sh', 'bash', 'zsh', 'fish')\n"
                '    matches, shells = [], []\n'
                '    for fields in private_process.inventory_for_tty(diagnostic_candidate, row["tty"]):')
        text = replace_once(text,
            inventory_prefix, inventory_replacement)
        text = _terminal_reads(text, shell_diagnostics=shell_diagnostics)
        provider = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "_provider")
        text = edit_nodes(text, [(provider, "def _provider(p):\n    return private_roots.executable_provider(p)")])
        modern_filter = (
            '        name = Path(fields[1]).name\n'
            '        if (name not in ("codex", "claude", "claude.exe", "node")\n'
            '                and _CLAUDE_NATIVE_VERSION.fullmatch(name) is None):')
        if shell_diagnostics:
            text = replace_once(text,
                '        if (name not in ("codex", "claude", "claude.exe", "node")\n'
                '                and _CLAUDE_NATIVE_VERSION.fullmatch(name) is None):',
                '        if not candidate(fields[1]):')
        elif text.count(modern_filter) == 1:
            text = replace_once(text, modern_filter, '        if not candidate(fields[1]):')
        else:
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
    elif name in ("executor_reask_identity.py", "executor_reply_advisory.py"):
        # Native delivery now requires the selected process to locate its
        # private root. These separate original consumer/advisory entrypoints
        # must carry that same pin; our own environment cannot locate a peer.
        text = replace_once(text, "import cmux_native_delivery as native",
                            "import cmux_native_delivery as native\n"
                            "from upgrade_native_roots import process_root, binding_root")
        provider = "provider" if name == "executor_reask_identity.py" else "'claude'"
        text = replace_once(text, f"native._native_root({provider}).resolve()",
                            f"native._native_root({provider}, binding=binding).resolve()")
        text = replace_once(text, f"native._transcript_path({provider}, session)",
                            f"native._transcript_path({provider}, session, process)")
        if name == "executor_reply_advisory.py":
            text = replace_once(text, "session_id=session, captured_epoch=time.time(),",
                                f"session_id=session, captured_epoch=time.time(),\n"
                                f"                  native_config_root=str(process_root({provider}, process)),")
        else:
            text = replace_once(text, "captured_epoch=time.time())",
                                "captured_epoch=time.time(),\n"
                                f"                  native_config_root=str(process_root({provider}, process)))")
        anchor = "    process = native.identity.process(binding['process']['pid'], validate_argv=False)"
        text = replace_once(text, anchor, anchor + "\n"
            f"    if process_root({provider}, process) != binding_root(binding):\n"
            "        raise ValueError('original consumer private configuration changed')")
        if name == "executor_reask_identity.py":
            text = replace_once(text,
                "proof.get('source') == 'embedded-native-client-v1'",
                "proof.get('source') in ('embedded-native-client-v1', 'private-standalone-native-client-v1')")
        # u13: the lane sandbox denies exec of setuid /bin/ps, so start/consume
        # failed before registration. Read the same tty from the kernel.
        text = replace_once(text, "from upgrade_native_roots import process_root, binding_root",
                            "from upgrade_native_roots import process_root, binding_root\n"
                            "import upgrade_process_inventory as private_process")
        text = _terminal_reads(text, subjects=("process",))
    elif name == "reconcile_handshake_ack.py":
        text = _embedded_supervisor_terminal(text)
    return text


def _tty_runs(tree):
    """Every `tty = ...run(...)` assignment, by enclosing top-level function."""
    found = []
    for top in tree.body:
        for node in ast.walk(top):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "tty"
                    and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "run"
                            for c in ast.walk(node.value))):
                found.append((getattr(top, "name", None), node))
    return found


def _embedded_supervisor_terminal(text):
    """The embedded-supervisor ACK path reads its own tty once; bases without
    that function (0.4.34) must have no subprocess tty read at all."""
    tree = ast.parse(text)
    supervisors = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "embedded_supervisor"]
    reads = _tty_runs(tree)
    if not supervisors:
        if reads:
            raise ValueError("terminal read outside the embedded supervisor")
        return text
    argv0 = [[c.args[0].elts[0].value for c in ast.walk(node) if isinstance(c, ast.Call) and c.args
              and isinstance(c.args[0], ast.List) and c.args[0].elts and isinstance(c.args[0].elts[0], ast.Constant)]
             for _, node in reads]
    if (len(supervisors) != 1 or [owner for owner, _ in reads] != ["embedded_supervisor"]
            or argv0 != [["/bin/ps"]]):
        raise ValueError("expected embedded supervisor terminal read changed")
    text = replace_once(text, "    import cmux_daemon_identity as identity\n",
                        "    import cmux_daemon_identity as identity\n"
                        "    import upgrade_process_inventory as private_process\n")
    text = _terminal_reads(text, subjects=("client",))
    after = [(owner, ast.get_source_segment(text, node)) for owner, node in _tty_runs(ast.parse(text))]
    if after or text.count("tty = private_process.terminal(client)") != 1:
        raise ValueError("embedded supervisor terminal read not replaced")
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
        old = "if daemon is None:\n        return None"
        embedded = "if daemon is None:\n        return _collect_embedded(" + arguments + ")"
        if source.count(embedded) == 1 and old not in source:
            old = embedded
        replacement = replace_once(source, old,
            "if daemon is None:\n        return private_standalone.collect(" + arguments + ")")
        edits.append((node, replacement))
    text = edit_nodes(text, edits)
    modern_origin = "embedded = proof.get('source') == 'embedded-native-client-v1' and daemon is None"
    if text.count(modern_origin) == 1:
        text = replace_once(text, modern_origin,
            "embedded = (proof.get('source') in ('embedded-native-client-v1',\n"
            "                                        'private-standalone-native-client-v1')\n"
            "                and daemon is None)")
        text = replace_once(text, "if proof.get('source') == 'embedded-native-client-v1':",
            "if proof.get('source') in ('embedded-native-client-v1', 'private-standalone-native-client-v1'):")
        # The modern public proof already represents a genuinely absent daemon.
        expected = "daemon_pid=proof['daemon']['pid'] if proof['daemon'] is not None else None,"
        replace_once(text, expected, expected)
        return text
    text = replace_once(text, "source, caller = by_env(daemon['env']), by_env(client['env'])",
        "origin = daemon if daemon is not None else client\n"
        "    source, caller = by_env(origin['env']), by_env(client['env'])")
    return replace_once(text, "daemon_pid=proof['daemon']['pid'],",
        "daemon_pid=proof['daemon']['pid'] if proof['daemon'] is not None else None,")


def _terminal_reads(text, *, shell_diagnostics=False, subjects=("p", "client")):
    edits, allowed = [], tuple(subjects)
    tree = ast.parse(text)
    target_nodes = set()
    if shell_diagnostics:
        targets = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "_target_process"]
        if len(targets) != 1:
            raise ValueError("expected native shell diagnostic function changed")
        target_nodes = set(ast.walk(targets[0]))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "tty"
                and "/bin/ps" in ast.get_source_segment(text, node)):
            found = [child.value.id for child in ast.walk(node.value)
                     if isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name)
                     and isinstance(child.slice, ast.Constant) and child.slice.value == "pid"]
            # The managed inventory names its already-read process `p` but
            # uses the original candidate variable for the ps argument.
            if not found and any(isinstance(child, ast.Name) and child.id == "candidate"
                                 for child in ast.walk(node.value)):
                found = ["p"]
            allowed_shell = (found == ["shell"] and shell_diagnostics and node in target_nodes)
            if len(found) != 1 or (found[0] not in allowed and not allowed_shell):
                raise ValueError("expected native terminal process subject changed")
            edits.append((node, "tty = private_process.terminal(" + found[0] + ")"))
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
    if len(footer) == 1 and (footer[0] in (
            "⏵⏵ bypass permissions on (shift+tab to cycle)",
            "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents")
            or re.fullmatch(
                r"⏵⏵ bypass permissions on \(shift\+tab to cycle\) · esc to interrupt"
                rf" +◎ /goal active \({_CLAUDE_DURATION}\)", footer[0])
            # Measured 2026-10-26 (u12, both lanes): a busy Goal session moves
            # the HUD to the row directly above the top border and leaves the
            # bare interrupt hint below. Accept only that exact pair.
            or (footer[0] == "⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt"
                and start >= 2 and re.fullmatch(
                    rf"\s*◎ /goal active \({_CLAUDE_DURATION}\)\s*", rows[start - 2]))):
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
        if name == "_queued_or_active_input" and old not in original:
            # 0.4.58 already includes both complete hints and timerless activity.
            # Authenticate that measured span; do not replace newer transport.
            modern = r'''        before = list(claude["before"])
        while before and (not before[-1].strip()
                          or _CLAUDE_GOAL_ROW_RE.fullmatch(before[-1])
                          or re.fullmatch(r"\s*⎿\s+Tip:[ \t]+[^\r\n]+", before[-1])):
            before.pop()
        screen = before[-1] if before else ""
'''
            activity = r'''        if re.fullmatch(r"\s*[·✻✢✳✶✽◐◑◒◓]\s+(?:[A-Za-z][A-Za-z-]*|Compacting (?:conversation|context))(?:…|\.\.\.)(?:\s*\([^\n]+\))?\s*", screen):
            return True
'''
            replace_once(original, modern, modern)
            replace_once(original, activity, activity)
            continue
        if new in original:
            raise ContractError("private Claude chrome already patched: " + name)
        edits.append((node, replace_once(original, old, new)))
    return edit_nodes(text, edits)


def bridge_probe_evidence(text):
    """Retain exact probe observations without changing ownership or input gates.

    The screen-only busy field is diagnostic. The original input_busy call may
    additionally enforce an authenticated interrupted-Bash boundary; neither
    its evaluation nor the key budget is replaced by this observation.
    """
    from orchestration_common import ContractError
    nodes = [n for n in ast.parse(text).body
             if isinstance(n, ast.FunctionDef) and n.name == "_bridge_test_one_locked"]
    if len(nodes) != 1:
        raise ContractError("private bridge probe function shape changed")
    node = nodes[0]
    original = ast.get_source_segment(text, node)
    if "pre_read_observation" in original:
        raise ContractError("private bridge probe evidence already patched")
    owned = (
        '        owned = (\n'
        '            bool(body) and token.startswith(body)\n'
        '            and not input_busy(post_clear_screen)\n'
        '        )\n')
    if original.count(owned) != 1:
        raise ContractError("private bridge probe ownership shape changed")
    changed = replace_once(original,
        '    pre_screen = cmux.read_screen(executor_ref, lines=args.lines)\n'
        '    forced_compose = bool(getattr(args, "force_compose", False))',
        '    pre_screen = cmux.read_screen(executor_ref, lines=args.lines)\n'
        '    def screen_observation(value):\n'
        '        return {"screen": value,\n'
        '                "screen_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}\n'
        '    pre_read_observation = screen_observation(pre_screen)\n'
        '    forced_compose = bool(getattr(args, "force_compose", False))')
    # These are the two pre-paste refusal returns and the final probe result.
    # Match whole physical lines so one indentation cannot match another.
    for indent, final in ((12, "pre_screen"), (24, "pre_screen"), (8, "post_clear_screen")):
        pad = " " * indent
        marker = '\n' + pad + '"pre_read_performed": True,\n'
        changed = replace_once(changed, marker, marker +
            pad + '"pre_read_observation": pre_read_observation,\n' +
            pad + '"final_observation": screen_observation(' + final + '),\n')
    changed = replace_once(changed,
        '        clear_observations.append({\n'
        '            "attempt": attempt,\n',
        '        clear_observations.append({\n'
        '            "attempt": attempt,\n'
        '            "screen": post_clear_screen,\n'
        '            "body": body,\n'
        '            "prefix_matches": bool(body) and token.startswith(body),\n'
        '            "active_or_queued": cmux._queued_or_active_input(post_clear_screen),\n')
    return edit_nodes(text, [(node, changed)])


def readonly_handshake(name, text):
    """Change only the new handshake precondition; keep the diagnostic probe."""
    from orchestration_common import ContractError
    if name == "reconcile_handshake_ack.py":
        legacy = (
            "                if selected.get('bridge_clear_confirmed') is not True:\n"
            "                    raise ValueError('ACK_ORIGINAL_BRIDGE_CLEAR_REQUIRED')")
        replacement = ("                from upgrade_handshake_capability import require_original\n"
                       "                require_original(selected, gate, binding, text, native)")
        if text.count(legacy) == 1:
            return replace_once(text, legacy, replacement)
        modern = '''                if selected.get('bridge_clear_confirmed') is not True:
                    # Capability belongs to the complete original controller.
                    # Old releases retain their clear requirement; a new label
                    # cannot waive a failed/missing original probe retroactively.
                    without_probe = getattr(harness, 'handshake_without_probe', None)
                    if (not callable(without_probe) or not without_probe(selected,
                            bridge_evidence_present=originals[root / 'bridge-test-evidence.json'] is not None)):
                        raise ValueError('ACK_ORIGINAL_BRIDGE_CLEAR_REQUIRED')'''
        gated = ("                if 'handshake_precondition' in selected:\n" +
                 "\n".join("    " + line for line in replacement.splitlines()) +
                 "\n                else:\n" +
                 "\n".join("    " + line for line in modern.splitlines()))
        return replace_once(text, modern, gated)
    if name != "mac_harness.py":
        return text
    legacy_preflight = "    cmd_bridge_test(args)\n    cmd_handshake(args)"
    modern = legacy_preflight not in text
    if modern:
        direct_preflight = (
            "    # Optional `bridge-test` is deliberately not run: /goal and ordinary work\n"
            "    # may continue while the guarded sender delivers the handshake prompt.\n"
            "    cmd_handshake(args)\n    cmd_validate(args)")
        replace_once(text, direct_preflight, direct_preflight)
    else:
        text = replace_once(text, legacy_preflight,
                            "    # Each original challenge performs a read-only capability check.\n"
                            "    cmd_handshake(args)")
    commands = [n for n in ast.parse(text).body
                if isinstance(n, ast.FunctionDef) and n.name == "cmd_handshake"]
    if len(commands) != 1:
        raise ContractError("readonly handshake requires one original cmd_handshake")
    command = commands[0]
    original = ast.get_source_segment(text, command)
    body = replace_once(original, "def cmd_handshake(args):\n    root = _artifact_root(args)\n", "")
    if modern:
        body = replace_once(body,
            '    if any(os.path.lexists(root / name) for name in ("handshake-receipt.json", "task-pack.json")):\n'
            '        _fail("original handshake already started; reconcile its original ACK or use continue_handshake.py; no replay")\n'
            '        sys.exit(1)\n', "")
    else:
        body = replace_once(body,
            '    if (getattr(args, "bridge_clear_recovery", None) and\n'
            '            any((root / name).exists() for name in ("handshake-receipt.json", "task-pack.json"))):\n'
            '        _fail("original handshake already started; use its existing receipt, not clear recovery")\n'
            '        sys.exit(1)\n', "")
    body = replace_once(body, "    _recheck_workspace_gate(gate)\n",
                        "    _recheck_workspace_gate(gate)\n    root_recheck()\n")
    body = replace_once(body, "        if not _handshake_one(args, root, gate, item, per_executor):",
                        "        root_recheck()\n"
                        "        if not _handshake_one(args, root, gate, item, per_executor,\n"
                        "                              root_recheck=root_recheck):")
    body = "\n".join(line[:len(line) - len(line.lstrip())] + "root_recheck()\n" + line
                     if line.strip() == "_write_handshake_receipt(root, per_executor)" else line
                     for line in body.splitlines())
    prefix = '''def cmd_handshake(args):
    import fcntl
    import stat
    root = _artifact_root(args)
    # Share the original reconciler's directory lock, without creating sidecars.
    # Keep ownership through every receipt and ACK; errors never reopen a nonce.
    held = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _fail('ORIGINAL_HANDSHAKE_IN_PROGRESS: observe its original attempt; no new challenge')
            sys.exit(1)
        pinned = os.fstat(held)
        def root_recheck():
            current, opened = os.lstat(root), os.fstat(held)
            expected = (pinned.st_dev, pinned.st_ino, pinned.st_mode)
            if (not stat.S_ISDIR(current.st_mode) or opened.st_nlink < 1
                    or (current.st_dev, current.st_ino, current.st_mode) != expected
                    or (opened.st_dev, opened.st_ino, opened.st_mode) != expected):
                raise ValueError('ORIGINAL_HANDSHAKE_ROOT_CHANGED')
        root_recheck()
        if (getattr(args, 'bridge_clear_recovery', None) or any(
                os.path.lexists(root / name) for name in ('handshake-receipt.json', 'task-pack.json'))):
            _fail('ORIGINAL_HANDSHAKE_EXISTS: observe its original attempt; no new challenge')
            sys.exit(1)
        root_recheck()
'''
    wrapped = prefix + "\n".join("    " + line if line else line for line in body.splitlines())
    wrapped += "\n    finally:\n        os.close(held)"
    text = edit_nodes(text, [(command, wrapped)])
    nodes = [n for n in ast.parse(text).body
             if isinstance(n, ast.FunctionDef) and n.name == "_handshake_one"]
    if len(nodes) != 1:
        raise ContractError("readonly handshake requires one original _handshake_one")
    node = nodes[0]
    source = ast.get_source_segment(text, node)
    source = replace_once(source, "def _handshake_one(args, root, gate, item, per_executor):",
                          "def _handshake_one(args, root, gate, item, per_executor, *, root_recheck):")
    if modern:
        # Keep upstream validation of any explicitly requested old probe.
        replace_once(source,
            "        bridge_context = _handshake_bridge_context(args, root, gate, executor_ref)",
            "        bridge_context = _handshake_bridge_context(args, root, gate, executor_ref)")
        source = replace_once(source, "    if below_minimum:\n        _info(\n",
            '    if receipt.get("bridge_clear_confirmed") is not True:\n'
            '        receipt["handshake_precondition"] = None\n    if below_minimum:\n        _info(\n')
    else:
        begin = source.index("    # Hard precondition: bridge-test must have proven its own token is gone.")
        end = source.index("    # One fresh nonce per executor.", begin)
        source = source[:begin] + (
            "    # No write-and-clear probe: capability is checked against the exact\n"
            "    # prepared challenge immediately before its one original send.\n\n") + source[end:]
        source = replace_once(source,
            '        "bridge_clear_confirmed": True,\n        "bridge_clear_recovery": clear_recovery,',
            '        "handshake_precondition": None,')
    source = replace_once(source, "        prepared = prompt_reference.plan(hello_msg, ack_nonce)\n",
        "        prepared = prompt_reference.plan(hello_msg, ack_nonce)\n"
        "        from upgrade_handshake_capability import observe as observe_capability\n"
        "        import cmux_native_delivery as capability_native\n"
        "        try:\n"
        "            observed_capability = observe_capability(\n"
        "                cmux, capability_native, gate, _gate_executors(gate), item,\n"
        "                prepared['text'], lines=args.lines)\n"
        "            if 'handshake_precondition' in receipt:\n"
        "                receipt['handshake_precondition'] = observed_capability\n"
        "        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:\n"
        "            diagnostic = getattr(exc, 'diagnostic', None)\n"
        "            if isinstance(diagnostic, dict):\n"
        "                receipt['handshake_capability_refusal'] = diagnostic\n"
        "            raise cmux.DispatchUnconfirmed(\n"
        "                'READONLY_HANDSHAKE_CAPABILITY_REFUSED: ' + str(exc),\n"
        "                state=cmux.SUPERVISOR_DID_NOT_SUBMIT) from exc\n")
    if source.count("_write_handshake_receipt(root, per_executor)") != (7 if modern else 6):
        raise ContractError("readonly handshake receipt writer shape changed")
    source = "\n".join(line[:len(line) - len(line.lstrip())] + "root_recheck()\n" + line
                       if line.strip() == "_write_handshake_receipt(root, per_executor)" else line
                       for line in source.splitlines())
    send = ("        result = cmux.submit_text(executor_ref, prepared['text'], **submit_kwargs)" if modern else
            "        cmux.submit_text(executor_ref, prepared['text'], **submit_kwargs)")
    source = replace_once(source, send, "        root_recheck()\n" + send)
    return edit_nodes(text, [(node, source)])


def handshake_contract_settlement(text):
    """Settle a new supervisor refusal without changing the original sender."""
    from orchestration_common import ContractError
    nodes = [n for n in ast.parse(text).body
             if isinstance(n, ast.FunctionDef) and n.name == "_handshake_one"]
    if len(nodes) != 1 or "root_recheck" not in {a.arg for a in nodes[0].args.kwonlyargs}:
        raise ContractError("handshake settlement requires the original locked challenge")
    node = nodes[0]
    source = ast.get_source_segment(text, node)
    if source.count("except cmux.DispatchUnconfirmed as e:") != 1:
        raise ContractError("handshake contract settlement source shape changed")
    if "except cmux.TaskPackContractError" in source:
        modern = '''        # A contract failure may precede the journal, or follow actual input.
        # Settle this supervisor workflow without inventing NO_INPUT evidence
        # or allowing a retry. Only preserve details returned by the sender.
        handshake_evidence.record_dispatch(receipt, getattr(e, "details", None), failed=True)
'''
        extra = ("        from upgrade_handshake_capability import record_dispatch_failure\n"
                 '        record_dispatch_failure(receipt, getattr(e, "details", None))\n')
        return edit_nodes(text, [(node, replace_once(source, modern, modern + extra))])
    anchor = '    _info(f"Polling for PREFLIGHT_ACK (timeout={handshake_timeout}s, poll=3s)...")'
    handler = '''    except cmux.TaskPackContractError as e:
        # A contract error can occur before a journal or after actual input.
        # Preserve only original sender evidence; no NO_INPUT inference or retry.
        from upgrade_handshake_capability import record_dispatch_failure
        record_dispatch_failure(receipt, getattr(e, "details", None))
        failed_at = _now()
        receipt.update(
            status="FAIL", lifecycle="REJECTED", detector_side="supervisor",
            attributable_to_executor=False, dispatch_error_type=type(e).__name__,
            dispatch_error=str(e), error=str(e), dispatch_error_at=failed_at,
            terminal_error_at=failed_at, updated_at=failed_at,
        )
        root_recheck()
        _write_handshake_receipt(root, per_executor)
        _fail(f"SUPERVISOR_DISPATCH_CONTRACT_ERROR — {e}")
        return False

'''
    source = replace_once(source, anchor, handler + anchor)
    return edit_nodes(text, [(node, source)])


def callback_pane_wait(text):
    """Wait before a first input only; never replay a journal or reopen a lock."""
    signature = "def deliver(bridge, task_pack_path, confirm_lines=200, *, reconcile_only=False, resume_queue_only=False):"
    modern_signature = ("def deliver(bridge, task_pack_path, confirm_lines=200, *, reconcile_only=False,\n"
                        "            resume_queue_only=False, wait_seconds=None, recover_stranded=False):")
    modern = text.count(modern_signature) == 1 and signature not in text
    if modern:
        signature = modern_signature
    helpers = r'''CALLBACK_PANE_WAIT_SECONDS = 15.0


def _callback_lock_identity(bridge, held, path):
    import stat
    opened, current = os.fstat(held.fileno()), os.lstat(path)
    if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode)
            or opened.st_nlink != 1 or current.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
        raise bridge.TaskPackContractError('CALLBACK_LOCK_CHANGED_BEFORE_INPUT')


def _callback_acquire_pane(bridge, pane_lock, pane_path, task_lock, task_path, *, fresh):
    deadline = time.monotonic() + CALLBACK_PANE_WAIT_SECONDS
    contended = False
    while True:
        for held, path in ((task_lock, task_path), (pane_lock, pane_path)):
            _callback_lock_identity(bridge, held, path)
        if contended and time.monotonic() >= deadline:
            raise bridge.TaskPackContractError('CALLBACK_PANE_TIMEOUT_BEFORE_INPUT')
        try:
            fcntl.flock(pane_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError as exc:
            if not fresh:
                raise bridge.TaskPackContractError('CALLBACK_PANE_IN_PROGRESS') from exc
            contended = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise bridge.TaskPackContractError('CALLBACK_PANE_TIMEOUT_BEFORE_INPUT') from exc
            time.sleep(min(0.1, remaining))
    for held, path in ((task_lock, task_path), (pane_lock, pane_path)):
        _callback_lock_identity(bridge, held, path)


'''
    text = replace_once(text, signature, helpers + signature)
    text = replace_once(text, "        old = json.loads(read_bytes(attempts[-1])) if attempts else None\n",
        "        old = json.loads(read_bytes(attempts[-1])) if attempts else None\n"
        "        fresh_callback = not attempts and not reconcile_only and not resume_queue_only"
        + (" and not recover_stranded" if modern else "") + "\n"
        "        if fresh_callback:\n"
        "            from cmux_evidence_io import snapshot as callback_snapshot\n"
        "            callback_pins = {Path(task_pack_path): callback_snapshot(task_pack_path),\n"
        "                             report: callback_snapshot(report)}\n")
    text = replace_once(text,
        "        try:\n            fcntl.flock(pane_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "        except BlockingIOError as exc:\n"
        "            raise bridge.TaskPackContractError('CALLBACK_PANE_IN_PROGRESS') from exc\n",
        "        _callback_acquire_pane(bridge, pane_lock, pane_lock_path, lock, lock_path,\n"
        "                               fresh=fresh_callback)\n")
    text = replace_once(text, "        if (not pack.get('executor_uuid') or\n", r'''        if fresh_callback:
            # Waiting is not authority. Recheck all mutable inputs before PREPARED.
            if (bridge.validate_task_pack_contract(task_pack_path) != pack
                    or any(callback_snapshot(path) != pin for path, pin in callback_pins.items())):
                raise bridge.TaskPackContractError('CALLBACK_INPUT_CHANGED_DURING_WAIT')
            require_action(pack['task_id'], 'callback', pack)
            if current_identity() != proof:
                raise bridge.TaskPackContractError('CALLBACK_IDENTITY_CHANGED_DURING_WAIT')
            if receipt_path.exists() or legacy.exists() or attempt_paths(journal) != attempts:
                raise bridge.TaskPackContractError('CALLBACK_JOURNAL_CHANGED_DURING_WAIT')
            for held, path in ((lock, lock_path), (pane_lock, pane_lock_path),
                               (target_lock, target_lock_path)):
                _callback_lock_identity(bridge, held, path)
        if (not pack.get('executor_uuid') or
''')
    compile(text, "cmux_callback_journal.py", "exec", dont_inherit=True)
    return text


def live_gates(name, text):
    if name == "cmux_bridge.py":
        text = claude_measured_chrome(text)
        for original in ('proof = pin_workspace(params["surface_id"])', 'proof = pin_workspace(args[index])'):
            text = replace_once(text, original, original +
                "\n        from upgrade_input_gate import check_input\n        check_input(proof)")
        node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "_run_successor")
        text = edit_nodes(text, [(node, 'def _run_successor(*args, artifact, check=True, capture=True):\n'
            '    raise WorkspaceScopeError("EXPERIMENT_SUCCESSOR_ROUTE_REFUSED: dedicated workspace only")')])
    elif name == "cmux_callback_journal.py":
        text = callback_pane_wait(text)
    elif name == "mac_harness.py":
        text = handshake_contract_settlement(readonly_handshake(name, bridge_probe_evidence(text)))
    elif name == "reconcile_handshake_ack.py":
        text = readonly_handshake(name, text)
    elif name == "cmux_codex_input.py":
        # Match the complete import, not a prefix which would move a newer
        # RPCError import into the private input gate's module.
        tree = ast.parse(text)
        gate_modules = {"upgrade_input_gate", PRIVATE_MODULES["upgrade_input_gate"]}
        if any((isinstance(node, ast.ImportFrom) and node.module in gate_modules)
               or (isinstance(node, ast.Import)
                   and any(item.name in gate_modules for item in node.names))
               for node in ast.walk(tree)):
            from orchestration_common import ContractError
            raise ContractError("private native input already patched")
        imports = [node for node in tree.body
                   if isinstance(node, ast.ImportFrom) and node.module == "cmux_codex_rpc"]
        if (len(imports) != 1 or any(item.asname is not None for item in imports[0].names)
                or [item.name for item in imports[0].names] not in
                (["DaemonRPC"], ["DaemonRPC", "RPCError"])):
            from orchestration_common import ContractError
            raise ContractError("private native input RPC import shape changed")
        node = imports[0]
        text = edit_nodes(text, [(node, ast.get_source_segment(text, node) +
                                 "\nfrom upgrade_input_gate import check_input")])
        text = replace_once(text, '        emit(INPUT_PHASE, evidence={"native_request": request})',
            '        check_input(binding["identity"], native_binding=binding)\n'
            '        emit(INPUT_PHASE, evidence={"native_request": request})')
        text = replace_once(text,
            '        try:\n            result = client.request(request["method"], request["params"])',
            '        check_input(binding["identity"], native_binding=binding)\n'
            '        try:\n            result = client.request(request["method"], request["params"])')
    elif name == "cmux_hook_runtime_audit.py":
        marker = "        session = str(uuid.UUID(payload.get('session_id') or payload.get('sessionId')))"
        text = replace_once(text, marker,
            "        if advisory and hook:\n"
            "            try:\n"
            "                from upgrade_hook_observer import record as observe_upgrade_hook\n"
            "                observe_upgrade_hook(payload, original_hook=hook, scope_status=scope_status)\n"
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
        changes.append("exact_bridge_probe_screen_evidence")
        changes.append("readonly_initial_handshake_capability")
        changes.append("original_handshake_contract_failure_settlement")
    if name == "reconcile_handshake_ack.py":
        changes.append("original_readonly_handshake_capability_reconciliation")
    if name == "cmux_bridge.py":
        changes.append("measured_claude_default_footer_and_tip")
    if name == "cmux_callback_journal.py":
        changes.append("bounded_first_callback_pane_wait_fresh_gates")
    compile(text, name, "exec", dont_inherit=True)
    return text, changes
