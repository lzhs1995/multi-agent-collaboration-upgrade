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
    "upgrade_body_read", "upgrade_toml", "upgrade_toml_re")}
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
    if len(footer) == 1 and (footer[0] in (
            "⏵⏵ bypass permissions on (shift+tab to cycle)",
            "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents")
            or re.fullmatch(
                r"⏵⏵ bypass permissions on \(shift\+tab to cycle\) · esc to interrupt"
                rf" +◎ /goal active \({_CLAUDE_DURATION}\)", footer[0])):
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
        return replace_once(text,
            "                if selected.get('bridge_clear_confirmed') is not True:\n"
            "                    raise ValueError('ACK_ORIGINAL_BRIDGE_CLEAR_REQUIRED')",
            "                from upgrade_handshake_capability import require_original\n"
            "                require_original(selected, gate, binding, text, native)")
    if name != "mac_harness.py":
        return text
    text = replace_once(text, "    cmd_bridge_test(args)\n    cmd_handshake(args)",
                        "    # Each original challenge performs a read-only capability check.\n"
                        "    cmd_handshake(args)")
    commands = [n for n in ast.parse(text).body
                if isinstance(n, ast.FunctionDef) and n.name == "cmd_handshake"]
    if len(commands) != 1:
        raise ContractError("readonly handshake requires one original cmd_handshake")
    command = commands[0]
    original = ast.get_source_segment(text, command)
    body = replace_once(original, "def cmd_handshake(args):\n    root = _artifact_root(args)\n", "")
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
        "            receipt['handshake_precondition'] = observe_capability(\n"
        "                cmux, capability_native, gate, _gate_executors(gate), item,\n"
        "                prepared['text'], lines=args.lines)\n"
        "        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:\n"
        "            diagnostic = getattr(exc, 'diagnostic', None)\n"
        "            if isinstance(diagnostic, dict):\n"
        "                receipt['handshake_capability_refusal'] = diagnostic\n"
        "            raise cmux.DispatchUnconfirmed(\n"
        "                'READONLY_HANDSHAKE_CAPABILITY_REFUSED: ' + str(exc),\n"
        "                state=cmux.SUPERVISOR_DID_NOT_SUBMIT) from exc\n")
    if source.count("_write_handshake_receipt(root, per_executor)") != 6:
        raise ContractError("readonly handshake receipt writer shape changed")
    source = "\n".join(line[:len(line) - len(line.lstrip())] + "root_recheck()\n" + line
                       if line.strip() == "_write_handshake_receipt(root, per_executor)" else line
                       for line in source.splitlines())
    source = replace_once(source, "        cmux.submit_text(executor_ref, prepared['text'], **submit_kwargs)",
                          "        root_recheck()\n"
                          "        cmux.submit_text(executor_ref, prepared['text'], **submit_kwargs)")
    return edit_nodes(text, [(node, source)])


def callback_pane_wait(text):
    """Wait before a first input only; never replay a journal or reopen a lock."""
    text = replace_once(text, "def deliver(bridge, task_pack_path, confirm_lines=200, *, reconcile_only=False, resume_queue_only=False):", r'''CALLBACK_PANE_WAIT_SECONDS = 15.0


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


def deliver(bridge, task_pack_path, confirm_lines=200, *, reconcile_only=False, resume_queue_only=False):''')
    text = replace_once(text, "        old = json.loads(read_bytes(attempts[-1])) if attempts else None\n",
        "        old = json.loads(read_bytes(attempts[-1])) if attempts else None\n"
        "        fresh_callback = not attempts and not reconcile_only and not resume_queue_only\n"
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
        text = readonly_handshake(name, bridge_probe_evidence(text))
    elif name == "reconcile_handshake_ack.py":
        text = readonly_handshake(name, text)
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
        changes.append("exact_bridge_probe_screen_evidence")
        changes.append("readonly_initial_handshake_capability")
    if name == "reconcile_handshake_ack.py":
        changes.append("original_readonly_handshake_capability_reconciliation")
    if name == "cmux_bridge.py":
        changes.append("measured_claude_default_footer_and_tip")
    if name == "cmux_callback_journal.py":
        changes.append("bounded_first_callback_pane_wait_fresh_gates")
    compile(text, name, "exec", dont_inherit=True)
    return text, changes
