"""Small, offline primitives shared by the orchestration adapter and verifier."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import contextlib
import fcntl
import functools
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ContractError(ValueError):
    pass


def contract_errors(label):
    """Keep malformed external evidence on the same fail-closed error path."""
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except ContractError:
                raise
            except (KeyError, TypeError, IndexError, AttributeError, ValueError, OSError) as exc:
                raise ContractError(f"{label}: malformed or unavailable evidence ({type(exc).__name__})") from exc
        return wrapped
    return decorate


def boot_id() -> str:
    """Kernel boot identity; never infer it from wall-clock/uptime subtraction."""
    if sys.platform == "darwin":
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                                capture_output=True, text=True, timeout=5, check=False)
        if result.returncode:
            raise ContractError("kernel boot identity unavailable")
        value = result.stdout.strip()
    elif sys.platform.startswith("linux"):
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    else:
        raise ContractError("boot identity is unsupported on this platform")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ContractError("invalid kernel boot identity") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def object_sha(value: Any) -> str:
    return digest(canonical(value))


def strict_json(raw: str | bytes, label: str = "JSON") -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ContractError(f"{label}: duplicate key {key!r}")
            value[key] = item
        return value

    def constant(value):
        raise ContractError(f"{label}: nonfinite constant {value}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label}: invalid JSON") from exc


def read_json(path: str | Path) -> dict:
    value = strict_json(read_regular(path), str(path))
    if not isinstance(value, dict):
        raise ContractError(f"{path}: JSON object required")
    return value


def parse_time(value: Any, label: str = "timestamp") -> datetime:
    if not isinstance(value, str):
        raise ContractError(f"{label}: timezone-qualified timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{label}: timezone required")
    return parsed


def count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{label}: nonnegative integer required")
    return value


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{label}: finite number required")
    return float(value)


def scope_path(value: str, root: str | Path) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ContractError("write_scope: nonempty filesystem path required")
    path = Path(value)
    if not path.is_absolute():
        path = Path(root) / path
    # Resolve existing symlink ancestors even when the final output does not exist.
    # Case-fold conservatively: macOS workspaces are normally case insensitive.
    return os.path.normcase(str(path.resolve())).casefold()


def scopes_conflict(left: str, right: str, root: str | Path) -> bool:
    a, b = scope_path(left, root), scope_path(right, root)
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def checked_path(path: str | Path) -> Path:
    if not isinstance(path, (str, Path)) or not str(path) or "\x00" in str(path):
        raise ContractError("nonempty canonical filesystem path required")
    path = Path(path)
    if not path.is_absolute() or path != path.resolve():
        raise ContractError(f"canonical absolute path without symlink aliases required: {path}")
    return path


def read_regular(path: str | Path, limit: int = 64 * 1024 * 1024) -> bytes:
    path = checked_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ContractError(f"regular bounded file required: {path}")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ContractError(f"file changed during read: {path}")
            if len(raw) != before.st_size:
                raise ContractError(f"incomplete read: {path}")
            return raw
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc


def file_ref(path: str | Path) -> dict:
    path = Path(path).resolve()
    raw = read_regular(path)
    return {"path": str(path), "sha256": digest(raw), "bytes": len(raw)}


def read_ref(ref: Any, label: str = "proof") -> bytes:
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        raise ContractError(f"{label}: pinned file reference required")
    raw = read_regular(ref["path"])
    if count(ref.get("bytes"), f"{label}.bytes") != len(raw) or ref.get("sha256") != digest(raw):
        raise ContractError(f"{label}: file hash/size mismatch")
    return raw


def json_ref(ref: Any, label: str = "proof") -> dict:
    value = strict_json(read_ref(ref, label), label)
    if not isinstance(value, dict):
        raise ContractError(f"{label}: JSON object required")
    return value


def write_json(path: str | Path, value: Any, *, exclusive: bool = False) -> dict:
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"path": str(path), "sha256": digest(raw), "bytes": len(raw)}


def write_bytes_once(path: str | Path, raw: bytes) -> dict:
    """Durable exact bytes; an interrupted/changed file is never overwritten."""
    path = checked_path(path)
    if path.exists():
        if read_regular(path) != raw:
            raise ContractError(f"existing immutable bytes differ: {path}")
        return file_ref(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return file_ref(path)


def within(path: str | Path, root: str | Path) -> Path:
    path, root = checked_path(path), checked_path(root)
    if path != root and root not in path.parents:
        raise ContractError(f"path escapes authorized root: {path}")
    return path


def tree_manifest(root: str | Path, *, kind: str = "runtime") -> dict:
    """Pin complete output trees and all loadable runtime bytes, including pyc.

    Runtime excludes only the root Git metadata. Output excludes nothing: hidden
    files, bytecode, empty directories and mode changes remain observable.
    Python -B prevents writes but does not prevent reading existing bytecode.
    """
    if kind not in ("runtime", "output"):
        raise ContractError("tree kind must be runtime or output")
    root = checked_path(root)
    if not root.is_dir():
        raise ContractError(f"runtime directory missing: {root}")
    files, dirs = {}, []
    for parent, directories, names in os.walk(root, followlinks=False):
        if kind == "runtime" and Path(parent) == root:
            directories[:] = [d for d in directories if d != ".git"]
            names = [n for n in names if n != ".git"]
        directories.sort()
        for name in directories + names:
            path = Path(parent) / name
            if path.is_symlink():
                raise ContractError(f"runtime symlink refused: {path}")
        dirs.extend(str((Path(parent) / name).relative_to(root)) for name in directories)
        for name in sorted(names):
            path = Path(parent) / name
            ref = file_ref(path)
            files[str(path.relative_to(root))] = dict(sha256=ref["sha256"], bytes=ref["bytes"],
                                                    mode=stat.S_IMODE(path.stat().st_mode))
    if not files and kind == "runtime":
        raise ContractError("empty runtime manifest")
    return {"version": 2, "kind": kind, "path": str(root), "files": files, "directories": sorted(dirs)}


@contract_errors("complete tree")
def verify_tree(manifest: dict) -> None:
    if tree_manifest(manifest["path"], kind=manifest["kind"]) != manifest:
        raise ContractError(f"complete runtime tree changed: {manifest['path']}")


@contextlib.contextmanager
def exclusive_lock(path: str | Path):
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise ContractError("lock owner/type mismatch")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError("coordinator is already active; observe the original run") from exc
        yield
    finally:
        os.close(fd)
