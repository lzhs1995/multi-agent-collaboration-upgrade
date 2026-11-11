"""Close inherited side channels in a new TASK terminal launcher.

Call only in the dedicated, single-threaded launcher, never in a supervisor or
service process. The server's separate Popen already uses close_fds=True and
explicit stdin/log descriptors. This does not prove the native clients' later
tool/helper FD handling; those runtime paths require their own evidence.
"""
import errno
import fcntl
import os
import stat
import sys
import threading

from orchestration_common import require


def _terminal():
    rows=[]
    for fd in (0,1,2):
        info=os.fstat(fd)
        require(stat.S_ISCHR(info.st_mode) and os.isatty(fd),
                "TASK_LAUNCH_REAL_TERMINAL_REQUIRED")
        rows.append(dict(fd=fd,device=info.st_dev,inode=info.st_ino,rdev=info.st_rdev))
    require(len({(r["device"],r["inode"],r["rdev"]) for r in rows})==1,
            "TASK_LAUNCH_ONE_TERMINAL_REQUIRED")
    return rows


def _extras():
    # listdir closes its own enumeration descriptor before returning. An EBADF
    # entry is that already-closed descriptor, not an inherited open channel.
    result=[]
    for name in os.listdir("/dev/fd"):
        require(name.isdecimal(),"TASK_LAUNCH_FD_ENUMERATION_CHANGED")
        fd=int(name)
        if fd<3:continue
        try:fcntl.fcntl(fd,fcntl.F_GETFD)
        except OSError as exc:
            if exc.errno==errno.EBADF:continue
            raise
        result.append(fd)
    return sorted(result)


def seal_terminal_descriptors():
    require(sys.platform=="darwin","TASK_LAUNCH_DARWIN_REQUIRED")
    require(threading.active_count()==1,"TASK_LAUNCH_SINGLE_THREADED_REQUIRED")
    before=_terminal()
    extra=_extras()
    for fd in extra:os.close(fd)
    require(not _extras(),"TASK_LAUNCH_INHERITED_FD_REMAINS")
    require(_terminal()==before,"TASK_LAUNCH_TERMINAL_CHANGED")
    return dict(schema="upgrade-task-launch-descriptors-v1",terminal=before,
                closed_fds=extra,extra_fds_remaining=0)


def exec_terminal_client(argv,env):
    # Repeat at the actual exec boundary: validation/hold/TUI attachment may
    # have opened descriptors since the initial launcher seal.
    seal_terminal_descriptors()
    os.execve(argv[0],argv,env)
