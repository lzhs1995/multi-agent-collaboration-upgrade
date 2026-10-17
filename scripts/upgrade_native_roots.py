"""Private native directories, authenticated separately from session identity.

Only new live-v2 controllers use this module. There is no production fallback,
log migration, guessed session or retroactive repair of an old native binding.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from upgrade_client_isolation import NATIVE_POLICY
from upgrade_socket_boundary import SOCKET_POLICY, binding_boundary


def configuration():
    source = Path(__file__).resolve().parent.parent
    value = json.loads((source / "upgrade-binding.json").read_bytes())
    ref = value["context"]
    raw = Path(ref["path"]).read_bytes()
    if len(raw) != ref["bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("UPGRADE_NATIVE_CONTEXT_CHANGED")
    context = json.loads(raw)
    if (value.get("version") != 2 or value.get("execution_mode") != "live-isolated-v2"
            or value.get("native_policy") not in (NATIVE_POLICY, SOCKET_POLICY)
            or value.get("controller_root") != str(source)
            or context.get("account_home") != str(Path.home().resolve())
            or context.get("account_uid") != os.getuid()):
        raise ValueError("UPGRADE_NATIVE_SCOPE_CHANGED")
    roots = value.get("native_roots", {})
    expected = {lane: str(Path(value["state_root"]) / "clients" / lane)
                for lane in ("codex", "claude_a", "claude_b")}
    if roots != expected or any(Path(p).resolve() != Path(p) for p in roots.values()):
        raise ValueError("UPGRADE_NATIVE_ROOTS_CHANGED")
    binding_boundary(value)
    return value, context


def allowed_roots(provider):
    binding, _ = configuration()
    if provider not in ("codex", "claude"):
        raise ValueError("UPGRADE_NATIVE_PROVIDER_UNKNOWN")
    lanes = ("codex",) if provider == "codex" else ("claude_a", "claude_b")
    return tuple(Path(binding["native_roots"][lane]) for lane in lanes)


def executable_pins():
    binding, _ = configuration()
    spec = json.loads((Path(binding["config_root"]) / "startup-v2.json").read_bytes())
    return {provider: spec["pins"][provider] for provider in ("codex", "claude")}


def candidate_process_name(name):
    # ps is only a discovery filter. The actual kernel executable and file
    # identity must match below, including version-named native Claude builds.
    return Path(name).name in {Path(pin["path"]).name for pin in executable_pins().values()}


def executable_provider(process):
    for provider, pin in executable_pins().items():
        if process["executable"] != pin["path"]:
            continue
        from upgrade_live_integrity import verify_artifact
        verify_artifact(pin)
        current = Path(pin["path"]).stat()
        if process["file_identity"] != [current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns]:
            raise ValueError("UPGRADE_NATIVE_EXECUTABLE_CHANGED")
        process_root(provider, process)
        return provider
    return None


def codex_control_socket():
    # The callback normally runs in a Claude process. Its own environment is
    # not the supervisor's identity or configuration. Use this arm's frozen root.
    binding, _ = configuration()
    expected = Path(binding["tmp_root"]) / "c.sock"
    boundary = binding_boundary(binding)
    if boundary is not None:
        # Separate exact native alias validation; generic path checks stay strict.
        return expected
    if (binding.get("codex_control_socket") != str(expected)
            or expected.resolve() != expected or len(os.fsencode(expected)) > 103):
        raise ValueError("UPGRADE_CONTROL_SOCKET_CHANGED")
    return expected


def configured_root(provider, env=None):
    """Validate configuration only. This function does not prove a live caller."""
    env = os.environ if env is None else env
    _, context = configuration()
    key = "CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR"
    name = env.get(key)
    if (env.get("HOME") != context["account_home"] or not isinstance(name, str)
            or Path(name) not in allowed_roots(provider) or Path(name).resolve() != Path(name)):
        raise ValueError("UPGRADE_NATIVE_PROCESS_ROOT_MISMATCH")
    return Path(name)


def process_root(provider, process):
    # The caller obtains this row from cmux_daemon_identity.process(), including
    # a fresh PID/birth/executable/TTY check. Never use our own environment to
    # locate another client's transcript.
    if (not isinstance(process, dict) or type(process.get("pid")) is not int
            or not process.get("birth") or not process.get("executable")):
        raise ValueError("UPGRADE_NATIVE_PROCESS_REQUIRED")
    return configured_root(provider, process.get("env", {}))


def binding_root(binding):
    name = binding.get("native_config_root")
    if not isinstance(name, str) or Path(name) not in allowed_roots(binding.get("provider")):
        raise ValueError("UPGRADE_ORIGINAL_NATIVE_ROOT_REQUIRED")
    if Path(name).resolve() != Path(name):
        raise ValueError("UPGRADE_NATIVE_ROOT_SYMLINK")
    return Path(name)


def transcript_root(provider, *, process=None, binding=None):
    if (process is None) == (binding is None):
        raise ValueError("UPGRADE_EXACT_NATIVE_ROOT_SOURCE_REQUIRED")
    root = process_root(provider, process) if process is not None else binding_root(binding)
    return root / ("sessions" if provider == "codex" else "projects")


def hook_codex_roots():
    # Native metadata and actual hook-parent provenance remain independent
    # checks in the original hook implementation.
    root = configured_root("codex")
    return (root / "sessions", root / "archived_sessions")
