"""Private CLI configuration and inherited macOS write isolation.

This is a new-client boundary, not a way to rebind an existing session. HOME is
never changed. The user authorized private CODEX_HOME / CLAUDE_CONFIG_DIR only
for newly created experimental clients. Native readers use the actual process's
pinned configuration
root; a configuration file, manual hook call or sandbox probe is not adoption.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tomllib

from orchestration_common import ContractError, checked_path, read_regular, require

LANES = ("codex", "claude_a", "claude_b")
NATIVE_POLICY = "private-cli-roots-v1"
SANDBOX = Path("/usr/bin/sandbox-exec")


def toml(value):
    """Render only the JSON-compatible TOML types accepted by CLI overrides."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return json.dumps(value, allow_nan=False)
    if isinstance(value, list):
        return "[" + ", ".join(toml(x) for x in value) + "]"
    if isinstance(value, dict):
        require(all(isinstance(k, str) for k in value), "TOML keys must be strings")
        return "{ " + ", ".join(toml(k) + " = " + toml(v) for k, v in value.items()) + " }"
    raise ContractError("unsupported CLI configuration value")


def private_native_roots(paths):
    # Two executors use separate native directories even within the same arm.
    return {lane: paths["state"] / "clients" / lane for lane in LANES}


def codex_config(original, native_root, hook_config):
    """Copy provider/model configuration, never global hooks, tools or policy.

    Authentication material is handled as a separate private, mode-0600 file.
    An unrecognised provider is preserved as configuration, not executed here.
    """
    allowed = {"model", "model_provider", "model_providers", "model_reasoning_effort",
               "model_reasoning_summary", "model_verbosity", "service_tier",
               "preferred_auth_method", "openai_base_url"}
    config = {k: v for k, v in original.items() if k in allowed}
    config.update(sqlite_home=str(native_root / "sqlite"), log_dir=str(native_root / "log"),
                  history={"persistence": "none"}, check_for_update_on_startup=False,
                  allow_login_shell=False, approval_policy="never", sandbox_mode="danger-full-access",
                  projects={str(native_root.parent.parent / "work"): {"trust_level": "trusted"}})
    # The mandatory inherited Seatbelt is the actual write boundary. A second
    # cwd-only sandbox would deny this arm's sibling evidence/campaign paths.
    # Automatic discovery is from the CLI's own, separately configured native
    # root. Inline hooks would duplicate hooks.json; leave their declaration out.
    require(isinstance(hook_config.get("hooks"), dict) and hook_config["hooks"],
            "private Codex hooks are required")
    return ("\n".join(k + " = " + toml(v) for k, v in config.items()) + "\n").encode()


def claude_config(original, hook_config):
    allowed = {"model", "effortLevel", "env"}
    config = {k: v for k, v in original.items() if k in allowed}
    if "env" in config:
        env = config["env"]
        require(isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str)
                                           for k, v in env.items()), "invalid Claude provider environment")
        # Keep API routing/model choices. Never import environment hooks, shell
        # preload code, HOME or another configuration-directory override.
        config["env"] = {k: v for k, v in env.items() if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))}
    config["hooks"] = hook_config["hooks"]
    config["skipDangerousModePermissionPrompt"] = True
    return (json.dumps(config, sort_keys=True, indent=2) + "\n").encode()


def client_temp_base(paths, lane):
    """Keep Claude's real child TMPDIR under 44 bytes, inside the arm boundary.

    Claude 2.1.170 appends claude-UID to CLAUDE_CODE_TMPDIR. Longer paths
    silently fall back to /tmp; accepting one would defeat private startup.
    This is a real directory, never a symlink or an extra writable root.
    """
    require(lane in LANES, "unknown client lane")
    base = checked_path(paths["tmp"] / {"codex": "codex", "claude_a": "a", "claude_b": "b"}[lane])
    if lane != "codex":
        require(len(os.fsencode(base / ("claude-" + str(os.getuid())))) <= 44,
                "CLAUDE_PRIVATE_TMP_PATH_TOO_LONG: use a shorter experiment root and compact registration")
    return base


def prepare_client_temp(paths, lane):
    base = client_temp_base(paths, lane)
    base.mkdir(mode=0o700)
    if lane != "codex":
        (base / ("claude-" + str(os.getuid()))).mkdir(mode=0o700)
    return base


def verify_client_temp(paths, lane):
    base = client_temp_base(paths, lane)
    directories = [base]
    if lane != "codex":
        directories.append(base / f"claude-{os.getuid()}")
    for path in directories:
        checked_path(path)
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700, "CLIENT_TEMP_OWNER_OR_MODE_CHANGED")
    return base


def environment(native_roots, paths, lane):
    require(lane in LANES, "unknown client lane")
    native = native_roots[lane]
    temp = client_temp_base(paths, lane)
    value = {"TMPDIR": str(temp), "PYTHONDONTWRITEBYTECODE": "1",
             "XDG_CACHE_HOME": str(native / "cache"), "XDG_STATE_HOME": str(native / "xdg-state"),
             "UPGRADE_CLIENT_LANE": lane,
             "CMUX_CUA_STATE_DIR": str(native / "cua"),
             "CMUX_COMPUTER_USE_INSTALL_GLOBAL_SKILL": "0"}
    if lane == "codex":
        value.update(CODEX_HOME=str(native), CODEX_CLIENT_THREAD_OBSERVER="1")
    else:
        value.update(CLAUDE_CONFIG_DIR=str(native), CLAUDE_CODE_TMPDIR=str(temp),
                     CCC_CLAUDE_REQUEST_OBSERVATIONS_DIR=str(native / "request-observations"))
    require("HOME" not in value, "HOME substitution is forbidden")
    return value


def control_socket(paths, *, socket_boundary=None):
    """Keep the Unix endpoint inside this arm and below Darwin's sun_path limit."""
    path = paths["tmp"] / "c.sock"
    if socket_boundary is not None:
        from upgrade_socket_boundary import validate_boundary
        return validate_boundary(socket_boundary, path)
    path = checked_path(path)
    require(len(os.fsencode(path)) <= 103, "private RPC path exceeds the Unix socket limit")
    return path


def client_argv(provider, wrapper, cwd, native_root, *, session_id=None, remote_socket=None,
                socket_boundary=None):
    require(provider in ("codex", "claude"), "unsupported client provider")
    wrapper, cwd, native_root = map(checked_path, (wrapper, cwd, native_root))
    if provider == "codex":
        require(session_id is None, "never invent a Codex session identity")
        # The reviewed private hook tree is the sole hook source. The wrapper's
        # official cmux hooks remain enabled. There is no daemon reuse, model
        # switch, HOME override, login action, ephemeral session or history edit.
        if socket_boundary is not None:
            from upgrade_socket_boundary import validate_boundary
            require(remote_socket is not None, "SOCKET_REMOTE_PATH_REQUIRED")
            remote_socket = validate_boundary(socket_boundary, remote_socket)
        mode = (["--remote", "unix://" + str(remote_socket if socket_boundary is not None
                                            else checked_path(remote_socket))]
                if remote_socket is not None else ["--no-daemon"])
        return [str(wrapper), *mode, "--cd", str(cwd),
                "--dangerously-bypass-hook-trust"]
    require(remote_socket is None and socket_boundary is None, "Claude cannot use the Codex control endpoint")
    require(isinstance(session_id, str), "new Claude launch requires its frozen session UUID")
    import uuid
    require(str(uuid.UUID(session_id)) == session_id, "noncanonical Claude session UUID")
    # The wrapper merges --settings with its own cmux hooks. Do not also read
    # the same user settings source: that can register each hook twice.
    return [str(wrapper), "--dangerously-skip-permissions", "--session-id", session_id, "--setting-sources", "",
            "--settings", str(native_root / "settings.json"),
            "--debug-file", str(native_root / "log" / "client.log")]


def sandbox_profile(writable, readonly=(), *, tty=None):
    """Deny writes by the client and all descendants outside explicit roots.

    This controls filesystem writes, not model honesty or every possible remote
    service. MAC still controls cmux delivery and endpoint scope. Native stores
    are wholly private, so no wildcard in a production journal tree is needed.
    """
    roots = sorted({str(checked_path(p)) for p in writable})
    denied = sorted({str(checked_path(p)) for p in readonly})
    require(roots, "at least one private writable root required")
    require(all(x not in ("/", str(Path.home())) for x in roots), "broad writable root refused")
    require(not any(Path(p) == Path(root) or Path(p) in Path(root).parents
                    for root in roots for p in denied),
            "writable root is inside an immutable boundary")
    lines = ["(version 1)", "(allow default)", "(deny file-write*)"]
    for root in roots:
        nested = [p for p in denied if Path(root) in Path(p).parents]
        filters = ["(subpath " + toml(root) + ")"] + [
            "(require-not (subpath " + toml(p) + "))" for p in nested]
        lines.append("(allow file-write* (require-all " + " ".join(filters) + "))")
    # These are device paths, never general /dev or /private/tmp exceptions.
    devices = ["/dev/null", "/dev/tty"]
    if tty is not None:
        require(re.fullmatch(r"/dev/ttys[0-9]+", tty) is not None, "unbound terminal device")
        devices.append(tty)
    for path in devices:
        lines.append("(allow file-write* (literal " + toml(path) + "))")
    # Exclusions in the allow rules make the boundary independent of precedence
    # between a broad allow and a later deny. Also prevent renaming an ancestor
    # directory to replace a pinned file indirectly.
    ancestors = set()
    for path in denied:
        lines.append("(deny file-write* (subpath " + toml(path) + "))")
        for parent in Path(path).parents:
            if any(parent == Path(root) or Path(root) in parent.parents for root in roots):
                ancestors.add(str(parent))
    for path in sorted(ancestors):
        lines.append("(deny file-write-unlink (literal " + toml(path) + "))")
    return ("\n".join(lines) + "\n").encode()


def sandbox_check(pid, path, operation="file-write-data"):
    require(sys.platform == "darwin" and type(pid) is int and pid > 0,
            "macOS process identity required for kernel sandbox check")
    require(operation in {"file-write-data", "file-write-create", "file-write-unlink"},
            "unsupported sandbox check")
    lib = ctypes.CDLL("/usr/lib/libsandbox.dylib", use_errno=True)
    func = lib.sandbox_check
    func.restype = ctypes.c_int
    # Darwin arm64 uses a different calling convention for variadic arguments.
    # Declare the fixed prefix so ctypes puts the path in the varargs area.
    func.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
    # sandbox_check is variadic: filter PATH | NO_REPORT, followed by a C path.
    no_report = ctypes.c_int.in_dll(lib, "SANDBOX_CHECK_NO_REPORT").value
    result = func(ctypes.c_int(pid), ctypes.c_char_p(operation.encode()),
                  ctypes.c_int(1 | no_report), ctypes.c_char_p(os.fsencode(path)))
    require(result in (0, 1), "kernel sandbox check failed: " + operation +
            " result=" + str(result) + " errno=" + str(ctypes.get_errno()))
    return result == 1


def require_kernel_boundary(pid, *, denied, writable):
    failures = []
    for path in denied:
        for operation in ("file-write-data", "file-write-create", "file-write-unlink"):
            if not sandbox_check(pid, path, operation):
                failures.append({"path": str(path), "operation": operation})
    for path in writable:
        require(checked_path(path).is_file(), "kernel write probe must be an existing private file")
        if sandbox_check(pid, path):
            failures.append({"private_write_denied": str(path)})
    require(not failures, "KERNEL_WRITE_BOUNDARY_UNVERIFIED: " + json.dumps(failures))
    return {"pid": pid, "denied": [str(p) for p in denied],
            "writable": [str(p) for p in writable], "kernel_checked": True}


def clean_child_environment(overrides):
    """Drop inherited session selectors; cmux owns new endpoint environment."""
    env = dict(os.environ)
    # Never transplant the parent session's thread/turn/control connection.
    for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_SESSION_ID",
                 "CODEX_TURN_ID", "CODEX_CLIENT_PID", "CMUX_CODEX_PID", "CMUX_CLAUDE_PID",
                 "CMUX_AGENT_HOOK_INVOKER_PID", "CMUX_AGENT_ROOT_PID",
                 "CODEX_HOME", "CLAUDE_CONFIG_DIR", "BASH_ENV", "ENV", "PYTHONPATH",
                 "NODE_OPTIONS", "BUN_OPTIONS", "DYLD_INSERT_LIBRARIES",
                 "CMUX_CODEX_HOOKS_DISABLED", "CMUX_CLAUDE_HOOKS_DISABLED",
                 "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE", "CLAUDE_CODE_TMPDIR"):
        env.pop(name, None)
    require("HOME" not in overrides and not any(k in overrides for k in
            ("CMUX_SURFACE_ID", "CMUX_WORKSPACE_ID", "CODEX_THREAD_ID", "CLAUDE_SESSION_ID")),
            "identity/environment override refused")
    env.update(overrides)
    return env


def patch_cmux_wrapper(provider, original, real_binary, paths, *, node=None):
    """Pin a cmux wrapper copy and relocate its incidental cache writes only.

    No global wrapper is installed/replaced. Preserve cmux's native hook producer
    and CLI argument handling; don't implement another input sender.
    """
    require(provider in ("codex", "claude"), "unsupported wrapper")
    binary = checked_path(real_binary)
    require(binary.is_file() and os.access(binary, os.X_OK), "client executable unavailable")
    text = original.decode("utf-8")
    function = "find_real_" + provider
    # Retain the original detector for audit, but the private copy must not pick
    # a changed PATH shim or another binary on a later launch.
    marker = "REAL_" + provider.upper() + '=\"$(' + function + ')\"'
    require(text.count(marker) == 1, "unrecognised cmux wrapper executable resolver")
    text = text.replace(marker, "REAL_" + provider.upper() + "=" + shlex.quote(str(binary)), 1)
    if provider == "claude":
        require(node is not None, "explicit Node executable required")
        node = checked_path(node)
        require(node.is_file() and os.access(node, os.X_OK), "explicit Node executable required")
        # The account's Node may be managed by NVM and absent from the bounded
        # client PATH. Keep the original merge, but pin both Node call sites.
        for before, after in (("command -v node >/dev/null 2>&1",
                               "command -v " + shlex.quote(str(node)) + " >/dev/null 2>&1"),
                              ("node -e '", shlex.quote(str(node)) + " -e '")):
            require(text.count(before) == 2, "unrecognised cmux Node invocation")
            text = text.replace(before, after)
        old = 'local cmux_dir="${HOME%/}/.cmuxterm"'
        require(text.count(old) == 1, "unrecognised cmux settings directory")
        text = text.replace(old, "local cmux_dir=" + shlex.quote(str(paths["tmp"] / "cmux-wrapper")), 1)
        old = 'local guard_dir="${HOME%/}/.cmuxterm/cmux-claude-node-options"'
        require(text.count(old) == 1, "unrecognised cmux preload directory")
        text = text.replace(old, "local guard_dir=" + shlex.quote(str(paths["tmp"] / "cmux-node-options")), 1)
    return text.encode()


def read_provider_configuration(home=None):
    home = Path.home() if home is None else checked_path(home)
    codex_path, claude_path = home / ".codex/config.toml", home / ".claude/settings.json"
    return (tomllib.loads(read_regular(codex_path).decode()) if codex_path.exists() else {},
            json.loads(read_regular(claude_path)) if claude_path.exists() else {})
