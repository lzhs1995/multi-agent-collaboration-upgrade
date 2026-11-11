#!/usr/bin/env python3
"""Manage only this checkout's skill links and hook commands. Dry-run by default."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
GUARDS = {
    "cmux_executor_closeout_guard": "PreToolUse",
    "cmux_workspace_guard": "PreToolUse",
    "cmux_agent_panel_guard": "PreToolUse",
    "cmux_handshake_receipt_guard": "PreToolUse",
    "cmux_consensus_round_guard": "PreToolUse",
    "cmux_lease_guard": "PreToolUse",
    "cmux_consensus_stop_guard": "Stop",
    "cmux_executor_idle_guard": "Stop",
    "cmux_native_delivery_guard": "PostToolUse",
    "cmux_supervisor_report_guard": "PostToolUse",
}
RETIRED_GUARDS = {"cmux_submit_confirmation_guard", "cmux_send_proof_stop_guard"}


def command(name):
    return shlex.join([sys.executable, "-B", str(ROOT / "scripts" / (name + ".py"))])


def release_source(path, home):
    """Recognize only this package's exact, immutable release layout."""
    path = Path(path)
    base = Path(home) / '.local/share/multi-agent-collaboration/releases'
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        return False
    return len(parts) == 2 and parts[0] not in ('.', '..') and parts[1] == 'source'


def owned_command(value, home):
    try:
        words = shlex.split(value)
    except (ValueError, TypeError):
        return False
    while words and words[0] in ('rtk', 'proxy'):
        words.pop(0)
    if not words or not Path(words.pop(0)).name.startswith('python'):
        return False
    while words and words[0] in ('-B', '-u'):
        words.pop(0)
    if len(words) != 1:
        return False
    path = Path(words[0])
    if (not path.is_absolute() or '..' in path.parts or path.suffix != '.py'
            or path.stem not in set(GUARDS) | RETIRED_GUARDS
            or path.parent.name != 'scripts'):
        return False
    return path.parent.parent == ROOT or release_source(path.parent.parent, home)


def transform(doc, uninstall=False, *, home=None):
    result = copy.deepcopy(doc)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    home = Path(home or Path.home())
    # Remove this package's historical registrations before adding exactly one
    # current entry. A same-named hook outside our release tree is foreign.
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            raise ValueError("hook event must be a list")
        cleaned = []
        for entry in entries:
            old = entry.get('hooks', [])
            kept = [h for h in old if not owned_command(h.get('command'), home)]
            if len(kept) == len(old) or kept:
                entry['hooks'] = kept
                cleaned.append(entry)
        hooks[event] = cleaned
    for name, event in GUARDS.items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError("hook event must be a list")
        cmd = command(name)
        if not uninstall:
            entries.append({"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
    return result


def read(path):
    if path.is_symlink():
        raise ValueError("refusing symlinked configuration: " + str(path))
    raw = path.read_bytes() if path.exists() else None
    doc = json.loads(raw) if raw is not None else {}
    if not isinstance(doc, dict):
        raise ValueError("configuration must be an object")
    return raw, doc


def replace(path, old, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path.read_bytes() if path.exists() else None) != old:
        raise RuntimeError("configuration changed concurrently; rerun at a quiet boundary")
    if old is not None:
        backup = path.with_name(path.name + ".multi-agent-backup-" + uuid.uuid4().hex)
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(old)
            handle.flush()
            os.fsync(handle.fileno())
    raw = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
    fd, pending = tempfile.mkstemp(prefix=".multi-agent-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if (path.read_bytes() if path.exists() else None) != old:
            raise RuntimeError("configuration changed during write; refused replacement")
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def skill_snapshot(path):
    """Fingerprint the whole old directory; rename preserves every resource."""
    if path.is_symlink():
        return ('link', os.readlink(path))
    if not path.exists():
        return ('absent',)
    if not path.is_dir():
        raise RuntimeError('foreign skill installation preserved: ' + str(path))
    entries = []
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = Path(parent) / name
            st = item.lstat()
            value = (os.readlink(item) if item.is_symlink() else
                     hashlib.sha256(item.read_bytes()).hexdigest() if item.is_file() else None)
            entries.append((str(item.relative_to(path)), st.st_ino, st.st_mode,
                            st.st_size, st.st_mtime_ns, value))
    return ('directory', sorted(entries))


def skill_plan(path, home):
    snapshot = skill_snapshot(path)
    if snapshot[0] == 'absent':
        return snapshot, False
    if snapshot[0] == 'link':
        source = path.resolve()
        if source == ROOT:
            return snapshot, True
        if release_source(source, home) and (source / 'SKILL.md').is_file():
            return snapshot, False
    elif snapshot[0] == 'directory':
        entry = path / 'SKILL.md'
        if entry.is_file() and not entry.is_symlink():
            body = entry.read_text()
            refs = [Path(p).parent for p in re.findall(r'\]\((/[^\n)]+/SKILL\.md)\)', body)
                    if release_source(Path(p).parent, home)]
            if (re.search(r'^name: multi-agent-collaboration\s*$', body, re.M)
                    and refs and len(set(refs)) == 1 and (refs[0] / 'SKILL.md').is_file()):
                return snapshot, False
    raise RuntimeError('foreign skill installation preserved: ' + str(path))


def replace_skill(path, snapshot, *, uninstall=False):
    if skill_snapshot(path) != snapshot:
        raise RuntimeError('skill changed concurrently; preserved: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if snapshot[0] != 'absent':
        backup = path.with_name(path.name + '.multi-agent-backup-' + uuid.uuid4().hex)
        path.rename(backup)
        # Catch an edit between the first comparison and rename before publishing.
        if skill_snapshot(backup) != snapshot:
            if not path.exists() and not path.is_symlink():
                backup.rename(path)
            raise RuntimeError('skill changed during migration; preserved backup')
    try:
        if not uninstall:
            path.symlink_to(ROOT, target_is_directory=True)
    except BaseException:
        if backup and not path.exists() and not path.is_symlink():
            backup.rename(path)
        raise
    return str(backup) if backup else None


def manage(home, mode, apply=False):
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10+ required; do not wire macOS Python 3.9")
    home = Path(home).expanduser().resolve()
    # Retained only for historical offline fixtures. Even doctor invokes hooks
    # with side effects, so it is not a safe live-account inspection operation.
    actual_home = Path.home().resolve()
    if (apply or mode == "doctor") and (home == actual_home or home in actual_home.parents
                                        or actual_home in home.parents):
        raise RuntimeError("UPGRADE_PRODUCTION_INSTALL_REFUSED: use a private experiment; "
                           "production MAC settings, hooks and links are separately owned")
    plans = []
    for client, filename in (("codex", "hooks.json"), ("claude", "settings.json")):
        config = home / ("." + client) / filename
        link = home / ("." + client) / "skills/multi-agent-collaboration"
        snapshot, owned = skill_plan(link, home)
        old, doc = read(config)
        new = transform(doc, mode == "uninstall", home=home)
        plans.append((config, link, snapshot, owned, old, doc, new))
    checks = {}
    # Check both clients before changing either one.
    for config, link, snapshot, owned, old, doc, new in plans:
        if read(config)[0] != old or skill_snapshot(link) != snapshot:
            raise RuntimeError('installation changed concurrently; rerun')
    for config, link, snapshot, owned, old, doc, new in plans:
        checks[str(config)] = {"linked": owned, "configured": doc == transform(doc, home=home),
                               "changeNeeded": doc != new}
        if mode != "doctor" and apply:
            if doc != new:
                replace(config, old, new)
            if mode == "install" and not owned:
                checks[str(config)]['skillBackup'] = replace_skill(link, snapshot)
            elif mode == "uninstall" and snapshot[0] != 'absent':
                checks[str(config)]['skillBackup'] = replace_skill(link, snapshot, uninstall=True)
    if mode == "doctor":
        for name in GUARDS:
            run = subprocess.run(shlex.split(command(name)), input="{}", text=True,
                                 capture_output=True, timeout=15)
            checks[name] = {"benignExitZero": run.returncode == 0}
        ok = all(v.get("benignExitZero", v.get("linked") and v.get("configured")) for v in checks.values())
    else:
        ok = True
    return {"ok": bool(ok), "mode": mode, "applied": apply and mode != "doctor",
            "checkout": str(ROOT), "checks": checks,
            "scope": "configuration and benign execution, not live client reload proof"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("install", "doctor", "uninstall"))
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = manage(args.home, args.mode, args.apply)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 2)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(2)
