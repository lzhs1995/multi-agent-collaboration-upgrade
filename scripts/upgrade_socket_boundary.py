"""Opt-in exact physical socket scope for the verified Codex native build.

Preparation is not launch authorization. No function creates a shared directory,
touches another daemon, or starts/restarts a client. The original private-only
policy remains the default; native socket aliases have a separate exact contract.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from orchestration_common import checked_path, require

SOCKET_POLICY = "private-cli-roots-exact-codex-socket-v2"
CODEX_SHA256 = "56fe2ab47d01b97375f2d5f213562d53144e77dd03dd75a19bdf344ec1261921"
CODEX_BYTES = 646156856


def shared_directory():
    return Path("/tmp").resolve(strict=True) / ("codex-daemon-" + str(os.geteuid()))


def advertised_path(path):
    path = Path(path)
    require(path.is_absolute() and path.name == "c.sock", "SOCKET_ADVERTISED_PATH_INVALID")
    require(checked_path(path.parent) == path.parent and len(os.fsencode(path)) <= 103,
            "SOCKET_ADVERTISED_PARENT_OR_LENGTH_INVALID")
    return path


def _directory_identity(path):
    checked_path(path)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700, "SOCKET_DIRECTORY_OWNER_OR_MODE_CHANGED")
    return [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)]


def boundary_plan(advertised, binary_pin):
    """Compute a read-only proposal. Refuse unverified builds and occupied paths."""
    require(binary_pin.get("sha256") == CODEX_SHA256 and binary_pin.get("bytes") == CODEX_BYTES,
            "SOCKET_BINARY_LAYOUT_UNVERIFIED")
    advertised = advertised_path(advertised)
    directory = shared_directory()
    physical = directory / hashlib.sha256(os.fsencode(advertised)).hexdigest()
    require(len(os.fsencode(physical)) <= 103, "SOCKET_PHYSICAL_PATH_TOO_LONG")
    value = dict(schema="upgrade-exact-codex-socket-v1", advertised=str(advertised),
                 physical=str(physical), lock=str(physical.with_suffix(".lock")),
                 directory_identity=_directory_identity(directory), uid=os.geteuid(),
                 binary_sha256=CODEX_SHA256, binary_bytes=CODEX_BYTES)
    validate_boundary(value, advertised, binary_pin=binary_pin, fresh=True)
    return value


def validate_boundary(value, advertised, *, binary_pin=None, fresh=False, live=False):
    advertised = advertised_path(advertised)
    directory = shared_directory()
    physical = directory / hashlib.sha256(os.fsencode(advertised)).hexdigest()
    lock = physical.with_suffix(".lock")
    expected = dict(schema="upgrade-exact-codex-socket-v1", advertised=str(advertised),
                    physical=str(physical), lock=str(lock),
                    directory_identity=_directory_identity(directory), uid=os.geteuid(),
                    binary_sha256=CODEX_SHA256, binary_bytes=CODEX_BYTES)
    require(value == expected and len(os.fsencode(physical)) <= 103, "SOCKET_BOUNDARY_CHANGED")
    if binary_pin is not None:
        require(binary_pin.get("sha256") == CODEX_SHA256 and binary_pin.get("bytes") == CODEX_BYTES,
                "SOCKET_BINARY_LAYOUT_UNVERIFIED")
    if fresh:
        require(not any(os.path.lexists(p) for p in (advertised, physical, lock)),
                "SOCKET_ORIGINAL_PATH_ALREADY_EXISTS")
    for path, kind in ((physical, stat.S_ISSOCK), (lock, stat.S_ISREG)):
        if os.path.lexists(path):
            info = path.lstat()
            require(kind(info.st_mode) and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                    "SOCKET_ENDPOINT_TYPE_OWNER_OR_MODE_CHANGED")
    if os.path.lexists(advertised):
        info = advertised.lstat()
        require(stat.S_ISLNK(info.st_mode) and info.st_uid == os.geteuid()
                and os.readlink(advertised) == str(physical) and physical.is_socket(),
                "SOCKET_ADVERTISED_TARGET_CHANGED")
    if live:
        require(advertised.is_symlink() and physical.is_socket() and lock.is_file(),
                "SOCKET_NATIVE_ENDPOINT_NOT_READY")
    return advertised


def extend_profile(profile, value):
    """Only the Codex lane gets two literal writes; never a directory subpath."""
    validate_boundary(value, value["advertised"])
    return profile + "".join("(allow file-write* (literal " + json.dumps(value[k]) + "))\n"
                             for k in ("physical", "lock")).encode()


def verify_kernel(pid, value, *, writer):
    from upgrade_client_isolation import sandbox_check
    # Darwin sandbox_check reports deny for absent literal paths even when the
    # corresponding create/bind succeeds. Never treat that query as evidence
    # about creation rights: this is a live-only check of existing endpoints.
    validate_boundary(value, value["advertised"], live=True)
    identities = {k: (Path(value[k]).lstat().st_dev, Path(value[k]).lstat().st_ino)
                  for k in ("advertised", "physical", "lock")}
    directory = Path(value["physical"]).parent
    denied = (directory, directory / ("0" * 64), directory / ("0" * 64 + ".lock"))
    require(all(sandbox_check(pid, p, operation) for p in denied
                for operation in ("file-write-data", "file-write-create", "file-write-unlink")),
            "SOCKET_SHARED_DIRECTORY_WRITE_ALLOWED")
    require(all(sandbox_check(pid, value[k], operation) == (not writer)
                for k in ("physical", "lock")
                for operation in ("file-write-data", "file-write-create", "file-write-unlink")),
            "SOCKET_EXACT_KERNEL_SCOPE_CHANGED")
    validate_boundary(value, value["advertised"], live=True)
    require(identities == {k: (Path(value[k]).lstat().st_dev, Path(value[k]).lstat().st_ino)
                           for k in identities}, "SOCKET_CHANGED_DURING_KERNEL_CHECK")
    return dict(pid=pid, writer=writer, exact_paths=[value["physical"], value["lock"]],
                shared_directory_writable=False, kernel_checked=True,
                existing_endpoint_identities=identities)


def binding_boundary(binding, *, binary_pin=None, fresh=False, live=False):
    """An explicit policy is required; no implicit expansion of a v1 binding."""
    from upgrade_client_isolation import NATIVE_POLICY
    policy = binding.get("native_policy")
    require(policy in (NATIVE_POLICY, SOCKET_POLICY), "SOCKET_POLICY_UNKNOWN")
    if policy == NATIVE_POLICY:
        require("socket_boundary" not in binding, "SOCKET_UNDECLARED_EXCEPTION")
        return None
    value = binding.get("socket_boundary")
    require(isinstance(value, dict), "SOCKET_BOUNDARY_REQUIRED")
    advertised = Path(binding["tmp_root"]) / "c.sock"
    require(binding.get("codex_control_socket") == str(advertised), "SOCKET_BINDING_CHANGED")
    validate_boundary(value, advertised, binary_pin=binary_pin, fresh=fresh, live=live)
    return value
