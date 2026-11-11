"""Pin a complete local Codex package before new experimental client startup.

This checks files and resolution, not publisher authenticity or tool execution.
Official download/signature verification and the real readiness probe remain
separate requirements. No program is executed and no package is copied.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from orchestration_common import ContractError, checked_path, read_regular, require
from upgrade_live_integrity import artifact_ref

POLICY = "codex-complete-package-v1"
MAX_MEMBERS = 4096
MAX_BYTES = 2 * 1024 ** 3


def _resolve(path, failure):
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContractError(failure) from exc


def _inventory(root):
    """Do not follow directory links or accept devices/FIFOs as package files."""
    result = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            mode = path.lstat().st_mode
            key = path.relative_to(root).as_posix()
            require(len(result) < MAX_MEMBERS, "CODEX_PACKAGE_TOO_MANY_MEMBERS")
            if stat.S_ISLNK(mode):
                target = _resolve(path, "CODEX_PACKAGE_LINK_ESCAPES_OR_IS_NOT_FILE:" + key)
                require(target.is_relative_to(root) and target.is_file(),
                        "CODEX_PACKAGE_LINK_ESCAPES_OR_IS_NOT_FILE:" + key)
                result[key] = {"kind": "symlink", "target": os.readlink(path),
                               "resolved": target.relative_to(root).as_posix()}
            elif stat.S_ISDIR(mode):
                result[key] = {"kind": "directory", "mode": stat.S_IMODE(mode)}
                pending.append(path)
            else:
                require(stat.S_ISREG(mode), "CODEX_PACKAGE_NOT_REGULAR:" + key)
                result[key] = {"kind": "file", "mode": stat.S_IMODE(mode),
                               "bytes": path.stat().st_size}
    require(sum(x.get("bytes", 0) for x in result.values()) <= MAX_BYTES,
            "CODEX_PACKAGE_TOO_LARGE")
    return result


def package_contract(package, codex_pin):
    root = checked_path(package)
    require(root.is_dir(), "CODEX_PACKAGE_DIRECTORY_MISSING")
    binary = checked_path(codex_pin["path"])
    require(binary.is_relative_to(root), "CODEX_BINARY_OUTSIDE_PACKAGE")
    inventory = _inventory(root)
    metadata = json.loads(read_regular(root / "codex-package.json"))
    require(isinstance(metadata, dict) and type(metadata.get("layoutVersion")) is int
            and metadata["layoutVersion"] == 1 and metadata.get("entrypoint") == "bin/codex"
            and metadata.get("variant") == "codex"
            and isinstance(metadata.get("version"), str) and metadata["version"]
            and metadata.get("resourcesDir") == "codex-resources"
            and metadata.get("pathDir") == "codex-path", "CODEX_PACKAGE_LAYOUT_UNSUPPORTED")
    # Do not execute a shell launcher to guess its entrypoint. App-bundle
    # launchers need their own supported resolver before this policy can admit.
    require(_resolve(root / "bin/codex", "CODEX_PACKAGE_ENTRYPOINT_MISMATCH") == binary,
            "CODEX_PACKAGE_ENTRYPOINT_MISMATCH")
    for name in ("codex-resources", "codex-path"):
        require(inventory.get(name, {}).get("kind") == "directory",
                "CODEX_PACKAGE_DIRECTORY_MISSING:" + name)
    helper_path = root / "bin/codex-code-mode-host"
    require(helper_path.is_file(), "CODEX_PACKAGE_CODE_MODE_HOST_MISSING")
    helper = _resolve(helper_path, "CODEX_PACKAGE_CODE_MODE_HOST_MISSING")
    require(helper.is_relative_to(root), "CODEX_PACKAGE_HELPER_ESCAPES")
    # The registered direct executable must have the same adjacent host. A
    # valid bin/host elsewhere in the archive cannot fix a broken launch path.
    require(_resolve(binary.parent / "codex-code-mode-host",
                     "CODEX_PACKAGE_ADJACENT_HOST_MISMATCH") == helper,
            "CODEX_PACKAGE_ADJACENT_HOST_MISMATCH")
    for path in (binary, helper):
        require(os.access(path, os.X_OK) and path.stat().st_mode & 0o111,
                "CODEX_PACKAGE_EXECUTABLE_MISSING:" + path.name)
    members = {}
    for name, entry in inventory.items():
        if entry["kind"] == "file":
            pin = artifact_ref(root / name)
            require(pin["mode"] == entry["mode"] and pin["bytes"] == entry["bytes"],
                    "CODEX_PACKAGE_CHANGED_DURING_READ")
            members[name] = {"kind": "file", **pin}
        else:
            members[name] = entry
    require(_inventory(root) == inventory, "CODEX_PACKAGE_CHANGED_DURING_READ")
    require(members[binary.relative_to(root).as_posix()] == {"kind": "file", **codex_pin},
            "CODEX_PACKAGE_BINARY_PIN_CHANGED")
    return {"schema": POLICY, "root": str(root), "binary": codex_pin,
            "helper": members[helper.relative_to(root).as_posix()],
            "version": metadata["version"], "members": members}


def verify_package_contract(contract, codex_pin):
    require(isinstance(contract, dict) and contract.get("schema") == POLICY,
            "CODEX_PACKAGE_CONTRACT_INVALID")
    require(package_contract(contract["root"], codex_pin) == contract,
            "CODEX_PACKAGE_CONTENT_CHANGED")

