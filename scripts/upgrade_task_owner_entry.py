#!/usr/bin/env python3
"""Fixed parent for one external TASK owner; never respawn or launch a model.

The owner's process proof includes ppid. Its short-lived launch caller cannot
be that parent: reparenting would invalidate the retained observer and RPC.
This single-threaded entry forks once and waits for that exact child. Only the
child imports the frozen owner and reads the one inherited credential pipe.
"""
import os
from pathlib import Path
import sys


def main():
    args = sys.argv[1:]
    credential = None
    if '--credential-fd' in args:
        credential = int(args[args.index('--credential-fd') + 1])
        if credential < 3:
            raise ValueError('TASK_OWNER_CREDENTIAL_FD_INVALID')
    child = os.fork()
    if child:
        if credential is not None:
            os.close(credential)
        while True:
            try:
                _, status = os.waitpid(child, 0)
                return os.waitstatus_to_exitcode(status) if os.WIFEXITED(status) else 2
            except InterruptedError:
                continue
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from upgrade_task_owner import main as owner_main
    return owner_main(args)


if __name__ == '__main__':
    raise SystemExit(main())
