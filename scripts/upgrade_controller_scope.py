"""Private-controller process guard (not an OS sandbox).

Installed by the materializer before controller imports. No HOME substitution.
External child processes are refused until a reviewed live execution boundary
supplies isolation evidence; Python children must use this same private tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import fcntl
import stat
import contextvars
from pathlib import Path
import sys

_installed = False
_open_directory = contextvars.ContextVar("upgrade_open_directory", default=None)


def fd_path(fd):
    """Authenticate an existing descriptor rather than treating its integer as a path."""
    if type(fd) is not int or fd < 0:
        raise PermissionError("UPGRADE_UNBOUND_WRITE_FD")
    held = os.fstat(fd)
    if sys.platform == "darwin":
        raw = fcntl.fcntl(fd, 50, b"\0" * 1024)  # Darwin F_GETPATH
        name = os.fsdecode(raw.split(b"\0", 1)[0])
    elif sys.platform.startswith("linux"):
        name = os.readlink("/proc/self/fd/" + str(fd))
    else:
        raise PermissionError("UPGRADE_FD_PATH_UNSUPPORTED")
    path = Path(name)
    if not path.is_absolute() or path != path.resolve():
        raise PermissionError("UPGRADE_UNBOUND_WRITE_FD")
    current = path.stat()
    if (held.st_dev, held.st_ino, stat.S_IFMT(held.st_mode)) != (
            current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode)):
        raise PermissionError("UPGRADE_FD_PATH_CHANGED")
    return path


def install_scope(*, live=False):
    global _installed
    if _installed:
        return
    scripts = Path(__file__).resolve().parent
    source = scripts.parent
    config = json.loads((source / "upgrade-binding.json").read_bytes())
    context_ref = config["context"]
    raw = Path(context_ref["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != context_ref["sha256"] or len(raw) != context_ref["bytes"]:
        raise RuntimeError("UPGRADE_CONTEXT_CHANGED")
    context = json.loads(raw)
    root = Path(context["root"])
    if (root != root.resolve() or source != Path(config["controller_root"])
            or context["skill"] != "multi-agent-collaboration-upgrade"
            or context["account_uid"] != os.getuid()
            or context["account_home"] != str(Path.home().resolve())):
        raise RuntimeError("UPGRADE_PRIVATE_BINDING_CHANGED")
    writable = [Path(config[name]) for name in ("state_root", "campaign_root", "evidence_root", "tmp_root")]
    if any(p != p.resolve() or root not in p.parents for p in writable):
        raise RuntimeError("UPGRADE_PRIVATE_WRITE_ROOT_CHANGED")
    if live:
        from upgrade_client_isolation import NATIVE_POLICY
        from upgrade_socket_boundary import SOCKET_POLICY, binding_boundary
        if (config.get("version") != 2 or config.get("execution_mode") != "live-isolated-v2"
                or config.get("native_policy") not in (NATIVE_POLICY, SOCKET_POLICY)):
            raise RuntimeError("UPGRADE_UNVERIFIED_LIVE_EXECUTION_MODE")
        binding_boundary(config)
    elif config.get("execution_mode") != "offline":
        raise RuntimeError("UPGRADE_UNVERIFIED_EXECUTION_MODE")
    python = context["python"]
    if (Path(sys.executable).resolve() != Path(python["path"])
            or hashlib.sha256(Path(python["path"]).read_bytes()).hexdigest() != python["sha256"]):
        raise RuntimeError("UPGRADE_PYTHON_CHANGED")
    manifest = json.loads((source.parent / "MANIFEST.json").read_bytes())
    files = {str(p.relative_to(source)): p for p in source.rglob("*") if p.is_file()}
    if set(files) != set(manifest["files"]):
        raise RuntimeError("UPGRADE_CONTROLLER_FILE_SET_CHANGED")
    for name, path in files.items():
        pin = manifest["files"][name]
        raw = path.read_bytes()
        if (path.is_symlink() or path.stat().st_mode & 0o222
                or len(raw) != pin["bytes"] or hashlib.sha256(raw).hexdigest() != pin["sha256"]):
            raise RuntimeError("UPGRADE_CONTROLLER_CHANGED:" + name)
    available = {p.stem for p in scripts.glob("*.py")}
    for name in available & set(sys.modules):
        module = sys.modules[name]
        if getattr(module, "__file__", None) and Path(module.__file__).resolve().parent != scripts:
            raise RuntimeError("UPGRADE_MIXED_CONTROLLER_IMPORT:" + name)
    if live:
        # The Python guard alone cannot constrain a CLI or its descendants.
        # Query the *current* process in the kernel before allowing child/IPC
        # operations. Merely supplying a v2 binding is insufficient.
        from upgrade_client_isolation import require_kernel_boundary
        isolation = config["isolation"]
        require_kernel_boundary(os.getpid(), denied=isolation["denied_probes"],
                                writable=isolation["write_probes"])
    else:
        isolation = {}

    def write_path(value, dir_fd=None):
        if isinstance(value, int):
            info = os.fstat(value)
            if value in (1, 2) and (stat.S_ISFIFO(info.st_mode) or stat.S_ISCHR(info.st_mode)):
                return
            value = fd_path(value)
        if not isinstance(value, (str, bytes, os.PathLike)):
            raise PermissionError("UPGRADE_UNBOUND_WRITE_PATH")
        path = Path(os.fsdecode(value))
        if dir_fd is not None and dir_fd != -1 and not path.is_absolute():
            base = fd_path(dir_fd)
            if not stat.S_ISDIR(os.fstat(dir_fd).st_mode):
                raise PermissionError("UPGRADE_RELATIVE_FD_NOT_DIRECTORY")
            path = base / path
        if not path.is_absolute():
            path = Path.cwd() / path
        if path != path.resolve() or not any(path == p or p in path.parents for p in writable):
            raise PermissionError("UPGRADE_WRITE_OUTSIDE_PRIVATE_STATE:" + str(path))
        if any(path == Path(p) or Path(p) in path.parents for p in isolation.get("immutable", ())):
            raise PermissionError("UPGRADE_WRITE_TO_IMMUTABLE_EVIDENCE:" + str(path))

    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                write_path(path, _open_directory.get())
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.chown", "os.utime", "os.truncate"):
            index = {"os.mkdir": 2, "os.remove": 1, "os.rmdir": 1, "os.chmod": 2,
                     "os.chown": 3, "os.utime": 3}.get(event)
            write_path(args[0], args[index] if index is not None and len(args) > index else None)
        elif event in ("os.rename", "os.link"):
            write_path(args[0], args[2] if len(args) > 2 else None)
            write_path(args[1], args[3] if len(args) > 3 else None)
        elif event == "os.symlink":
            raise PermissionError("UPGRADE_RUNTIME_SYMLINK_REFUSED")
        elif event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork", "pty.spawn"):
            if not live:
                raise PermissionError("UPGRADE_CHILD_EXECUTION_REQUIRES_LIVE_ISOLATION")
        elif event in ("socket.connect", "socket.bind"):
            if not live:
                raise PermissionError("UPGRADE_NATIVE_IO_REQUIRES_LIVE_ISOLATION")

    original_open = os.open

    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        # CPython's open audit event omits dir_fd. Carry it through this exact
        # call, and still let the kernel authenticate the original descriptor.
        token = _open_directory.set(dir_fd)
        try:
            return original_open(path, flags, mode, dir_fd=dir_fd)
        finally:
            _open_directory.reset(token)

    sys.dont_write_bytecode = True
    sys.addaudithook(audit)
    os.open = guarded_open
    _installed = True
