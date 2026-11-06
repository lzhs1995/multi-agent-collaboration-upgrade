"""Explicit task-only network egress: each execution lane reaches its model port.

V1 allows new TCP connections only to the lane's loopback model port. Explicit
V2 additionally allows the two Claude lanes to connect to the original fixed
callback ingress. Other new Unix/TCP/UDP connections are denied. This does not
authenticate inherited descriptors, Mach/Apple Events, the allowed HTTP service
or model tools. Those remain separate adoption checks. The remote TUI and the
trusted controller keep their original profiles; old task candidates opt out.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from orchestration_common import require

SCHEMA = "upgrade-task-model-network-v1"
CALLBACK_SCHEMA = "upgrade-task-model-callback-network-v2"


def callback_path(evidence_root):
    """Fixed private ingress for a NEW contract, not a caller-selected broker."""
    root = Path(evidence_root)
    path = root / "live-control/task-startup/callback-ingress/s"
    require(root.is_absolute() and str(path) == os.path.normpath(str(path))
            and path == path.resolve() and len(os.fsencode(path)) <= 103
            and all(ord(c) >= 32 and ord(c) != 127 for c in str(path)),
            "TASK_CALLBACK_NETWORK_EXACT_PATH_REQUIRED")
    return str(path)


def gateway_port(url):
    require(isinstance(url,str), "TASK_MODEL_GATEWAY_URL_REQUIRED")
    require(url==url.strip() and all(ord(c)>=32 and ord(c)!=127 for c in url),
            "TASK_MODEL_GATEWAY_URL_INVALID")
    try:
        parsed=urlsplit(url)
        port=parsed.port
    except ValueError:
        require(False, "TASK_MODEL_GATEWAY_URL_INVALID")
    require(parsed.scheme=="http" and parsed.hostname=="127.0.0.1"
            and parsed.username is None and parsed.password is None
            and parsed.path in ("", "/", "/v1") and not parsed.query and not parsed.fragment
            and type(port) is int and 1024 <= port <= 65535
            and parsed.netloc=="127.0.0.1:"+str(port), "TASK_MODEL_EXACT_LOOPBACK_REQUIRED")
    return port


def network_contract(station, claude, *, evidence_root=None):
    """Derive ports from original frozen routing, never from a peer's payload."""
    codex_port=gateway_port(station["codex"]["base_url"])
    claude_port=gateway_port(claude.get("env",{}).get("ANTHROPIC_BASE_URL"))
    require(codex_port!=claude_port, "TASK_MODEL_DISTINCT_PROVIDER_ROUTES_REQUIRED")
    value = dict(schema=SCHEMA, ports=dict(codex=codex_port,claude_a=claude_port,claude_b=claude_port))
    if evidence_root is not None:
        value.update(schema=CALLBACK_SCHEMA, callback=callback_path(evidence_root))
    return value


def model_only_profile(profile, contract, lane):
    require(isinstance(profile,bytes) and profile.startswith(b"(version 1)\n") and profile.endswith(b"\n"),
            "TASK_MODEL_BASE_PROFILE_REQUIRED")
    callback = isinstance(contract, dict) and contract.get("schema") == CALLBACK_SCHEMA
    require(isinstance(contract,dict) and set(contract)==({"schema","ports","callback"} if callback else {"schema","ports"})
            and contract["schema"] in (SCHEMA, CALLBACK_SCHEMA)
            and isinstance(contract["ports"],dict) and set(contract["ports"])=={"codex","claude_a","claude_b"}
            and all(type(p) is int and 1024<=p<=65535 for p in contract["ports"].values())
            and contract["ports"]["claude_a"]==contract["ports"]["claude_b"]!=contract["ports"]["codex"]
            and lane in contract["ports"], "TASK_MODEL_NETWORK_CONTRACT_CHANGED")
    # Seatbelt's IP grammar accepts localhost, not a numeric host. This permits
    # the one loopback port (including IPv6); it is not a 127.0.0.1-only rule.
    result = profile + ("(deny network-outbound)\n"
        '(allow network-outbound (remote tcp "localhost:%d"))\n' % contract["ports"][lane]).encode()
    if callback:
        require(isinstance(contract["callback"], str) and len(Path(contract["callback"]).parts) >= 6,
                "TASK_CALLBACK_NETWORK_PATH_CHANGED")
        path = Path(contract["callback"])
        require(str(path) == callback_path(path.parents[3]), "TASK_CALLBACK_NETWORK_PATH_CHANGED")
        # Only the two original executors submit callbacks. The app-server
        # accepts controller RPC, but never opens this outgoing connection.
        if lane in ("claude_a", "claude_b"):
            result += ('(allow network-outbound (remote unix-socket (literal %s)))\n'
                       % json.dumps(str(path))).encode()
    return result
