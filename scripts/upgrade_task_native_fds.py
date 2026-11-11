"""Observe a blocked original requester using Darwin libproc, without mutation.

The ABI follows the installed Darwin sys/proc_info.h structures. Unsupported
sizes, incomplete lists, changed peers and unexpected channels are refused.
Only the querying hook/probe is inspected, not every process in a live client.
"""
import ctypes as C
import os
import sys

from orchestration_common import require
from upgrade_rpc_peer import kernel_peer
from upgrade_task_observer_protocol import PROCESS_FIELDS
from upgrade_task_fd_contract import SCHEMA, verify


class FileInfo(C.Structure):
    _fields_ = [('flags', C.c_uint32), ('status', C.c_uint32), ('offset', C.c_int64),
                ('type', C.c_int32), ('guardflags', C.c_uint32)]


class Stat(C.Structure):
    _fields_ = [('dev', C.c_uint32), ('mode', C.c_uint16), ('nlink', C.c_uint16),
                ('ino', C.c_uint64), ('uid', C.c_uint32), ('gid', C.c_uint32),
                ('times', C.c_int64 * 8), ('size', C.c_int64), ('blocks', C.c_int64),
                ('blksize', C.c_int32), ('flags', C.c_uint32), ('gen', C.c_uint32),
                ('rdev', C.c_uint32), ('spare', C.c_int64 * 2)]


class SockBuf(C.Structure):
    _fields_ = [('counts', C.c_uint32 * 5), ('flags', C.c_int16), ('timeout', C.c_int16)]


class UnixInfo(C.Structure):
    _fields_ = [('connected_socket', C.c_uint64), ('connected_pcb', C.c_uint64),
                ('address', C.c_char * 255), ('connected_address', C.c_char * 255)]


class SocketProtocol(C.Union):
    _fields_ = [('unix', UnixInfo), ('alignment', C.c_uint64)]


class SocketInfo(C.Structure):
    _fields_ = [('stat', Stat), ('socket', C.c_uint64), ('pcb', C.c_uint64),
                ('type', C.c_int32), ('protocol', C.c_int32), ('family', C.c_int32),
                ('shorts', C.c_int16 * 8), ('oobmark', C.c_uint32),
                ('receive', SockBuf), ('send', SockBuf), ('kind', C.c_int32),
                ('reserved', C.c_uint32), ('detail', SocketProtocol)]


class SocketFD(C.Structure):
    _fields_ = [('file', FileInfo), ('socket', SocketInfo)]


class FD(C.Structure):
    _fields_ = [('fd', C.c_int32), ('type', C.c_uint32)]


def api():
    require(sys.platform == 'darwin' and C.sizeof(C.c_void_p) == 8
            and sys.byteorder == 'little' and C.sizeof(Stat) == 136
            and C.sizeof(FileInfo) == 24 and C.sizeof(SocketFD) == 792,
            'TASK_NATIVE_DESCRIPTOR_ABI_UNSUPPORTED')
    lib = C.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pidinfo.argtypes = (C.c_int, C.c_int, C.c_uint64, C.c_void_p, C.c_int)
    lib.proc_pidinfo.restype = C.c_int
    lib.proc_pidfdinfo.argtypes = (C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_int)
    lib.proc_pidfdinfo.restype = C.c_int
    return lib


def entries(pid, flavor):
    require(type(pid) is int and pid > 1 and flavor in (1, 14),
            'TASK_NATIVE_DESCRIPTOR_QUERY_INVALID')
    # A full buffer is ambiguous, never evidence that the list is complete.
    rows = (FD * 4096)()
    C.set_errno(0)
    size = api().proc_pidinfo(pid, flavor, 0, C.byref(rows), C.sizeof(rows))
    require(C.get_errno() == 0 and 0 <= size < C.sizeof(rows) and size % C.sizeof(FD) == 0,
            'TASK_NATIVE_DESCRIPTOR_ENUMERATION_FAILED')
    value = [dict(fd=row.fd, type=row.type) for row in rows[:size // C.sizeof(FD)]]
    require(len({r['fd'] for r in value}) == len(value)
            and all(r['fd'] >= 0 for r in value), 'TASK_NATIVE_DESCRIPTOR_ENUMERATION_CHANGED')
    if flavor == 1:
        require(len(value) >= 3, 'TASK_NATIVE_DESCRIPTOR_STANDARD_STREAMS_MISSING')
    return sorted(value, key=lambda row: row['fd'])


def snapshot(pid):
    return dict(fds=entries(pid, 1), fileports=entries(pid, 14))


def socket_info(pid, fd):
    row = SocketFD()
    C.set_errno(0)
    size = api().proc_pidfdinfo(pid, fd, 3, C.byref(row), C.sizeof(row))
    require(C.get_errno() == 0 and size == C.sizeof(row), 'TASK_NATIVE_DESCRIPTOR_SOCKET_READ_FAILED')
    value = row.socket
    require(value.kind == 3 and value.family == 1 and value.type == 1,
            'TASK_NATIVE_DESCRIPTOR_UNIX_STREAM_REQUIRED')
    return dict(fd=fd, socket=value.socket, connected_socket=value.detail.unix.connected_socket,
                family=value.family, type=value.type, kind=value.kind)


def observe(connection, caller, endpoint, process):
    pid = caller['peer']['pid']
    def same_requester():
        row = process(pid)
        require(pid != os.getpid() and kernel_peer(connection) == caller['peer']
                and {k: row[k] for k in PROCESS_FIELDS} == caller['ancestry'][0]
                and connection.getsockname() == endpoint,
                'TASK_NATIVE_DESCRIPTOR_REQUESTER_CHANGED')
    same_requester()
    before = snapshot(pid)
    sockets = [row['fd'] for row in before['fds'] if row['type'] == 2]
    require(len(sockets) == 1 and before['fileports'] == [], 'TASK_NATIVE_DESCRIPTOR_EXTRA_CHANNEL')
    peer = socket_info(pid, sockets[0])
    observer = socket_info(os.getpid(), connection.fileno())
    after = snapshot(pid)
    require(socket_info(pid, sockets[0]) == peer
            and socket_info(os.getpid(), connection.fileno()) == observer,
            'TASK_NATIVE_DESCRIPTOR_CONNECTION_CHANGED_DURING_READ')
    same_requester()
    return verify(dict(schema=SCHEMA, requester=caller['ancestry'][0], endpoint=endpoint,
        before=before, after=after, requester_socket=peer, observer_socket=observer,
        input_operations=0, whole_client_isolation_proven=False), caller, endpoint)
