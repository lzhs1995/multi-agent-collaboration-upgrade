"""Reviewable source transformations for private MAC controllers only.

All transformations run before publication. No installed module is monkeypatched,
no native identity is rewritten, and an unrecognized source shape is refused.
"""
from __future__ import annotations

import ast
from pathlib import Path

from orchestration_common import ContractError


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ContractError("private controller source shape changed: " + old[:85])
    return text.replace(old, new, 1)


def patch_ack(name, text):
    if name == "observe_handshake_ack.py":
        text = replace_once(text, "from repair_task_notice import controller_modules",
                            "from repair_task_notice import controller_modules\n"
                            "from upgrade_handshake_identity import bound_rows")
        text = replace_once(text,
            "paths = [Path(args.receipt), Path(args.attempt), Path(args.text_file)]",
            "paths = [Path(args.receipt), Path(args.attempt), Path(args.text_file), Path(args.gate)]")
        text = replace_once(text,
            "receipt_raw, attempt_raw, text_raw = [pin[0] for pin in pins]",
            "receipt_raw, attempt_raw, text_raw, gate_raw = [pin[0] for pin in pins]\n"
            "    if digest(gate_raw) != args.gate_sha256:\n"
            "        raise ValueError('ACK_ORIGINAL_GATE_CHANGED')\n"
            "    if paths[3] != paths[0].parent / 'identity-gate.json':\n"
            "        raise ValueError('ACK_ORIGINAL_GATE_PATH_CHANGED')")
        start = text.index("    if not isinstance(rows, list):", text.index("def observe("))
        end = text.index("    nonce, task, provider =", start)
        modern = "    gate_pin = None\n" in text[start:end]
        text = text[:start] + (
            "    harness = importlib.import_module('mac_harness')\n"
            "    if Path(harness.__file__).resolve().parent != Path(args.controller) / 'scripts':\n"
            "        raise ValueError('ACK_OBSERVATION_MIXED_CONTROLLER')\n"
            "    gate = json.loads(gate_raw)\n"
            "    _, row = bound_rows(rows, harness._gate_executors(gate), gate, binding" +
            (", allow_partial=True" if modern else "") + ")\n" +
            ("    gate_pin, gate_path = pins[3], paths[3]\n" if modern else "")
        ) + text[end:]
        text = replace_once(text, "'attempt-sha256', 'text-file'):",
                            "'attempt-sha256', 'text-file', 'gate', 'gate-sha256'):")
        text = replace_once(text, "original_attempt_sha256=digest(attempt_raw),",
                            "original_attempt_sha256=digest(attempt_raw),\n"
                            "        original_gate=str(paths[3]), original_gate_sha256=digest(gate_raw),")
    elif name == "reconcile_handshake_ack.py":
        text = replace_once(text, "import observe_handshake_ack as observation",
                            "import observe_handshake_ack as observation\n"
                            "from upgrade_handshake_identity import bound_rows")
        nodes = [n for n in ast.parse(text).body
                 if isinstance(n, ast.FunctionDef) and n.name == "validate_panel"]
        if len(nodes) != 1:
            raise ContractError("private ACK panel function shape changed")
        node = nodes[0]
        source = ast.get_source_segment(text, node)
        modern = "or not len(rows) <= len(members)" in source
        start = source.index("    if (")
        end = source.index("        nonce = row.get('ack_nonce')", start)
        source = source[:start] + (
            "    resolved, selected = bound_rows(rows, members, gate, binding" +
            (", allow_partial=True" if modern else "") + ")\n"
            "    for row, canonical_id, member in resolved:\n"
        ) + source[end:]
        selector = ("    selected = [r for r, m in zip(rows, members)" if modern else
                    "    selected = [r for r in rows")
        start = source.index(selector)
        end = source.index("    return rows, selected[0]", start) + len("    return rows, selected[0]")
        # bound_rows already authenticates the exact selected original member,
        # native session, provider, pane and gate aliases. Keep ACK/aggregate
        # validation above; never resolve an unsent member from today's tree.
        source = source[:start] + "    return rows, selected" + source[end:]
        text = edit_nodes(text, [(node, source)])
        text = replace_once(text,
            "gate = json.loads(originals[root / 'identity-gate.json'][0])",
            "gate_raw = originals[root / 'identity-gate.json'][0]\n"
            "                if (Path(args.gate) != root / 'identity-gate.json'\n"
            "                        or observation.digest(gate_raw) != args.gate_sha256):\n"
            "                    raise ValueError('ACK_ORIGINAL_GATE_CHANGED')\n"
            "                gate = json.loads(gate_raw)")
        text = replace_once(text, "'attempt-sha256', 'text-file'):",
                            "'attempt-sha256', 'text-file', 'gate', 'gate-sha256'):")
        text = replace_once(text, "payload_sha256=observation.digest(originals[text_path][0]))",
                            "payload_sha256=observation.digest(originals[text_path][0]),\n"
                            "                    gate=str(args.gate), gate_sha256=args.gate_sha256)")
    return text


def _span(lines, node):
    # AST columns are UTF-8 offsets, not Python character offsets.
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    return (starts[node.lineno - 1] + node.col_offset,
            starts[node.end_lineno - 1] + node.end_col_offset)


def edit_nodes(text, replacements):
    raw = text.encode("utf-8")
    lines = raw.splitlines(keepends=True)
    edits = [(*_span(lines, node), value.encode()) for node, value in replacements]
    for start, end, value in sorted(edits, reverse=True):
        raw = raw[:start] + value + raw[end:]
    return raw.decode("utf-8")


def relocate(text, paths, controller):
    """Rewrite path literals, preserving native logs and the real account HOME.

    Path(home) / an_absolute_path resolves to that absolute private path. This
    also covers defaults whose callers pass an explicit home in the old API.
    """
    roots = {
        ".local/state/multi-agent-collaboration": str(paths["state"]),
        ".local/state/cmux-agent": str(paths["state"] / "legacy-helper"),
        ".local/share/multi-agent-collaboration/releases": str(paths["controllers"] / "releases"),
        ".local/share/multi-agent-collaboration": str(paths["controllers"]),
        "/tmp/multi-agent-collaboration": str(paths["tmp"]),
        "Library/LaunchAgents": str(paths["tmp"] / "disabled-launchd"),
        ".codex/hooks.json": str(paths["config"] / "codex-hooks.json"),
        ".claude/settings.json": str(paths["config"] / "claude-settings.json"),
        "/opt/homebrew/bin/cmux-agent": str(paths["bin"] / "cmux-agent"),
        "/usr/local/bin/cmux-agent": str(paths["bin"] / "cmux-agent"),
    }
    edits = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for old, new in roots.items():
            if node.value == old or node.value.startswith(old + "/"):
                edits.append((node, repr(new + node.value[len(old):])))
                break
    text = edit_nodes(text, edits)
    # The few split literals cannot be safely changed one component at a time:
    # .codex and .claude also identify the genuine, read-only native logs.
    for old, new in {
        'Path.home() / ".codex" / "hooks.json"': f'Path({str(paths["config"] / "codex-hooks.json")!r})',
        'Path.home() / ".claude" / "settings.json"': f'Path({str(paths["config"] / "claude-settings.json")!r})',
        'Path.home() / ".local" / "bin" / "cmux-agent"': f'Path({str(paths["bin"] / "cmux-agent")!r})',
        'Path.home() / ".local/bin/cmux-agent"': f'Path({str(paths["bin"] / "cmux-agent")!r})',
        'shutil.which("cmux-agent")': repr(str(paths["bin"] / "cmux-agent")),
    }.items():
        text = text.replace(old, new)
    return text


BOOTSTRAP = '''# Private upgrade controller: install the file guard before controller imports.
import os as _upgrade_os
import sys as _upgrade_sys
_upgrade_sys.path.insert(0, _upgrade_os.path.dirname(_upgrade_os.path.realpath(__file__)))
from upgrade_controller_scope import install_scope as _upgrade_install_scope
_upgrade_install_scope()
'''


def add_bootstrap(text):
    tree = ast.parse(text)
    line = 0
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)) or (
                isinstance(node, ast.ImportFrom) and node.module == "__future__"):
            line = node.end_lineno
        else:
            break
    lines = text.splitlines(keepends=True)
    if line == 0 and lines and lines[0].startswith("#!"):
        line = 1
    lines.insert(line, BOOTSTRAP)
    return "".join(lines)


def block_function(text, name):
    nodes = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(nodes) != 1:
        raise ContractError("private disabled function not found: " + name)
    node = nodes[0]
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    if not body:
        raise ContractError("private disabled function has no body: " + name)
    lines = text.splitlines(keepends=True)
    lines.insert(body[0].lineno - 1,
                 "    raise RuntimeError('UPGRADE_GLOBAL_INSTALLATION_DISABLED')\n")
    return "".join(lines)


def transform_python(name, text, paths, controller, *, compatibility):
    changes = ["private_path_literals", "pre_import_scope_guard"]
    text = relocate(text, paths, controller)
    disabled = {"manage_install.py": ("manage",),
                "configuration_guardian.py": ("reconcile", "install_job"),
                "executor_reask_scheduler.py": ("install_job",)}
    for function in disabled.get(name, ()):
        text = block_function(text, function)
        changes.append("disabled:" + function)
    if compatibility and name in ("observe_handshake_ack.py", "reconcile_handshake_ack.py"):
        text = patch_ack(name, text)
        changes.append("original_gate_canonical_ack")
    text = add_bootstrap(text)
    compile(text, name, "exec", dont_inherit=True)
    return text, changes
