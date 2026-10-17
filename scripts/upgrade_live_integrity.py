"""Streaming pins for native CLI executables, without copying large binaries."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat


def artifact_ref(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("STARTUP_ARTIFACT_PATH_NOT_CANONICAL")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 ** 3:
            raise ValueError("STARTUP_ARTIFACT_NOT_BOUNDED_REGULAR_FILE")
        digest = hashlib.sha256()
        size = 0
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
        signature = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (signature(before) != signature(os.fstat(handle.fileno()))
                or signature(before) != signature(path.lstat()) or size != before.st_size):
            raise ValueError("STARTUP_ARTIFACT_CHANGED_DURING_READ")
    return {"path": str(path), "sha256": digest.hexdigest(), "bytes": size,
            "mode": stat.S_IMODE(before.st_mode)}


def verify_artifact(ref):
    actual = artifact_ref(ref["path"])
    if any(actual.get(key) != value for key, value in ref.items()) or not {
            "path", "sha256", "bytes"}.issubset(ref):
        raise ValueError("STARTUP_ARTIFACT_PIN_CHANGED: " + str(ref.get("path")))
    return actual
