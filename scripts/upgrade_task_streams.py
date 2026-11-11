"""Capture synchronous owner output without capturing another thread's output.

Only Python stream writes are supported. An owner requesting a raw descriptor
is refused instead of silently writing outside its evidence file. Other threads
and asyncio tasks retain their original streams. Nested capture is refused.
"""
import asyncio
from contextlib import contextmanager
import io
import sys
import threading

from orchestration_common import require

_lock=threading.Lock()


def execution_owner():
    try:task=asyncio.current_task()
    except RuntimeError:task=None
    return threading.get_ident(),task


class _Stream:
    def __init__(self,original,target,owner):
        self.original,self.target,self.owner=original,target,owner

    def _selected(self):return self.target if execution_owner()==self.owner else self.original
    def write(self,value):return self._selected().write(value)
    def flush(self):return self._selected().flush()
    def isatty(self):return self._selected().isatty()
    def fileno(self):
        if execution_owner()==self.owner:raise io.UnsupportedOperation("TASK_HARNESS_RAW_STREAM_FD_REFUSED")
        return self.original.fileno()
    @property
    def buffer(self):return _Stream(self.original.buffer,self.target.buffer,self.owner)
    def __getattr__(self,name):return getattr(self._selected(),name)


@contextmanager
def capture(stdout,stderr):
    require(_lock.acquire(blocking=False),"TASK_HARNESS_CAPTURE_ALREADY_ACTIVE")
    original=(sys.stdout,sys.stderr);owner=execution_owner()
    streams=(_Stream(original[0],stdout,owner),_Stream(original[1],stderr,owner))
    try:
        sys.stdout,sys.stderr=streams
        yield
        require(sys.stdout is streams[0] and sys.stderr is streams[1],"TASK_HARNESS_STREAM_CHANGED")
    finally:
        # Do not overwrite a foreign replacement; restore only our own proxies.
        if sys.stdout is streams[0]:sys.stdout=original[0]
        if sys.stderr is streams[1]:sys.stderr=original[1]
        _lock.release()
