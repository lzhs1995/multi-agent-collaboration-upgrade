"""New-task Seatbelt restriction against agent self-connections.

The controller and remote TUI do NOT use this additional restriction. Apply it
to the app-server and every experimental agent lane BEFORE they start. The
server can bind/listen/accept, but it and descendants cannot open an outgoing
connection to the task endpoint or rename/unlink the physical socket. Existing
filesystem isolation must also protect the physical socket's parent directory.

This renderer is not deployment proof, kernel peer proof or task enrollment.
It neither changes existing profiles nor authenticates already-open/inherited
descriptors. A live admission must verify the expected process tree, private
profiles, no inherited RPC descriptors, the exact endpoint and all agent lanes.
Unconfined trusted operators are outside this boundary.
Raw command brokers (cmux, Herdr, remote exec, external MCP) are not confined by
these rules. No exclusive responder/complete agent isolation claim is valid
until those additional deployment paths are constrained separately.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from orchestration_common import contract_errors, require


@contract_errors("TASK_SOCKET_POLICY_REFUSED")
def restrict_agent_connections(profile, physical, advertised):
    require(isinstance(profile, bytes) and profile.startswith(b"(version 1)\n")
            and profile.endswith(b"\n"), "TASK_SOCKET_BASE_PROFILE_REQUIRED")
    paths = []
    for value in (physical, advertised):
        require(isinstance(value, (str, Path)), "TASK_SOCKET_PATH_INVALID")
        path = Path(value)
        # The advertised path may already be a symlink. Resolve its parent,
        # not its final component; the physical path must not be an alias.
        require(path.is_absolute() and str(path) == os.path.normpath(str(path))
                and path.parent == path.parent.resolve() and len(path.parts) >= 4
                and "\x00" not in str(path) and len(os.fsencode(path)) <= 103,
                "TASK_SOCKET_EXACT_PATH_REQUIRED")
        paths.append(path)
    require(paths[0] == paths[0].resolve(), "TASK_SOCKET_PHYSICAL_ALIAS_REFUSED")
    result = profile
    for path in dict.fromkeys(paths):
        quoted = json.dumps(str(path))
        result += f"(deny network-outbound (remote unix-socket (literal {quoted})))\n".encode()
    # A path-based connect rule can otherwise be evaded by renaming the socket
    # into a different writable location. Bind/create is deliberately retained.
    result += f"(deny file-write-unlink (literal {json.dumps(str(paths[0]))}))\n".encode()
    return result
