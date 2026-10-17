"""Read Claude's own kernel start time without Darwin's setuid /bin/ps.

Only the native registration query is supported. This helper never writes a
registry or supplies a session identity; the native client consumes its output.
The receiver still independently checks PID/birth/root/session and its journal.
"""
from __future__ import annotations

import os
import re
import sys
import time

from cmux_daemon_identity import IdentityError, process


def start_time(argv):
    if (len(argv) != 4 or argv[:3] != ["-o", "lstart=", "-p"]
            or re.fullmatch(r"[1-9][0-9]{0,9}", argv[3]) is None):
        raise IdentityError("only the native self lstart query is supported")
    pid = int(argv[3])
    if pid <= 1 or pid != os.getppid():
        raise IdentityError("lstart query must name the direct parent")
    before = process(pid, arguments=False)
    value = time.strftime("%a %b %e %H:%M:%S %Y", time.localtime(before["birth"][0]))
    if process(pid, arguments=False) != before or os.getppid() != pid:
        raise IdentityError("lstart parent changed during query")
    return value


def main(argv=None):
    try:
        value = start_time(sys.argv[1:] if argv is None else argv)
    except (IdentityError, ValueError, OverflowError) as exc:
        print("private ps: " + str(exc), file=sys.stderr)
        return 2
    print(value, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
