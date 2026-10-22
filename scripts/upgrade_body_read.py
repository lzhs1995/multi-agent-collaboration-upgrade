"""Read one pinned ordinary-message body into the invoking client's tool result.

This command does not send, ACK, accept work or issue a reception receipt. Only
its complete successful result in the original receiver's native journal can
support the separate body-exposure observation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex

from cmux_prompt_reference import read_body, wire_reference

SCHEMA = "upgrade-body-read-v1"
SCOPE = "complete_body_exposed_in_tool_result_not_semantic_acceptance"


def reader_instructions(python, controller, rtk):
    prefix = shlex.join([str(rtk), "proxy", str(python), "-B",
                        str(Path(controller) / "scripts/mac_upgrade_body_read.py"), "--notice"])
    return ("\nFor each newly received ordinary MESSAGE_REFERENCE_V2 notice, read its complete body "
            "with this frozen reader: " + prefix + " followed by the exact notice as one shell-quoted "
            "argument. Preserve every notice character. Codex: invoke through exactly "
            "text(await tools.exec_command({\"cmd\":\"THE_COMPLETE_COMMAND\",\"login\":false,"
            "\"yield_time_ms\":10000,\"max_output_tokens\":12000})); in functions.exec "
            "(replace THE_COMPLETE_COMMAND with the correctly JSON-escaped complete command). "
            "Claude: use one foreground Bash call with command set to that complete command. "
            "Read the complete tool result and decide how the information affects ongoing work. "
            "A failed, truncated or yielded read is unproven; preserve its original result. "
            "This read sends no message, creates no ACK, and does not accept a task. "
            "Do not pause /goal to receive information or send any extra sampling replies.\n")


def read_command(python, controller, notice, rtk="rtk"):
    if wire_reference(notice) is None:
        raise ValueError("ORDINARY_REFERENCE_REQUIRED")
    if not Path(python).is_absolute() or not Path(controller).is_absolute():
        raise ValueError("ABSOLUTE_FROZEN_READER_REQUIRED")
    return shlex.join([str(rtk), "proxy", str(python), "-B",
        str(Path(controller) / "scripts/mac_upgrade_body_read.py"), "--notice", notice])


def read_result(notice):
    reference = wire_reference(notice)
    if reference is None:
        raise ValueError("ORDINARY_REFERENCE_REQUIRED")
    body, _ = read_body(reference)
    return dict(schema=SCHEMA, scope=SCOPE, notice=notice, reference=reference, body=body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notice", required=True)
    args = parser.parse_args()
    print(json.dumps(read_result(args.notice), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
