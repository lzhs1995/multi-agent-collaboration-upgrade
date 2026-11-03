"""Harmless native tool action used to correlate automatic startup hooks.

This is a tool probe, never a hook/adoption proof producer or an input sender.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from upgrade_native_roots import configuration, configured_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True, choices=("codex", "claude_a", "claude_b"))
    args = parser.parse_args()
    binding, _ = configuration()
    provider = "codex" if args.lane == "codex" else "claude"
    root = configured_root(provider)
    if str(root) != binding["native_roots"][args.lane] or os.environ.get("UPGRADE_CLIENT_LANE") != args.lane:
        raise ValueError("READINESS_LANE_CHANGED")
    print(json.dumps({"status": "UPGRADE_READINESS_TOOL_COMPLETED", "lane": args.lane,
                      "native_root": str(root), "controller": binding["controller_root"],
                      "adoption_proven": False, "input_operations": 0}))


if __name__ == "__main__":
    main()
