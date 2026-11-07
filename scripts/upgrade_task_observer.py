"""Explicit read-only Unix service, independent of the campaign actor's waits.

One bounded background thread handles fixed observation requests. It owns no
campaign ledger or actor RPC. Installation/import does not start the service;
an external controller must construct and start it for a newly frozen task.
"""
import os
from pathlib import Path
import select
import socket
import threading
import time

from orchestration_common import canonical, file_ref, require, write_json
from upgrade_task_observation import ObservationSource
from upgrade_task_observer_protocol import (SCHEMA, REQUEST, RESPONSE, MAX_REPLY, KINDS,
    socket_pin, request_value, frame)


class ReadOnlyObserver:
    def __init__(self, source, *, seconds=1800, max_requests=2048):
        require(type(source) is ObservationSource,"TASK_OBSERVER_ORIGINAL_SOURCE_REQUIRED")
        require(type(seconds) in (int,float) and 0<seconds<=1800
                and type(max_requests) is int and 0<max_requests<=2048,"TASK_OBSERVER_BUDGET_INVALID")
        source.verify()
        self.source=source;self.pid=os.getpid();self.seconds=seconds;self.max_requests=max_requests
        self.path=Path(source.binding['task_model_network']['observer']);self.root=self.path.parent
        self.root.mkdir(mode=0o700,parents=True,exist_ok=False)
        self.closed=False;self.thread=None;self.stop_event=threading.Event();self.sequence=0
        self.seen=set()
        self.deadline=None
        self.listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            self.listener.set_inheritable(False);self.listener.bind(str(self.path));self.path.chmod(0o600)
            self.socket=socket_pin(self.path)
            self.descriptor=write_json(self.root/'descriptor.json',dict(schema=SCHEMA,
                runtime=source.runtime_ref,startup=source.runtime['startup'],owner=source.owner,
                socket=self.socket,allowed_kinds=list(KINDS),input_operations=0,
                maximum_seconds=seconds,maximum_requests=max_requests,
                maximum_observation_seconds=30,
                restart_allowed=False,adoption_proven=False),exclusive=True)
            self.listener.listen(8)
        except BaseException:
            self.closed=True;self.listener.close();raise

    def start(self):
        require(os.getpid()==self.pid and self.thread is None and not self.closed,
                "TASK_OBSERVER_ALREADY_STARTED_OR_CLOSED")
        self.source.verify()
        write_json(self.root/'start-intent.json',dict(descriptor=self.descriptor,owner_pid=self.pid),exclusive=True)
        self.deadline=time.monotonic()+self.seconds
        self.thread=threading.Thread(target=self._run,name='task-read-only-observer',daemon=True)
        self.thread.start()
        return self.descriptor

    def _verify_endpoint(self):
        require(os.getpid()==self.pid and not self.closed
                and file_ref(self.descriptor['path'])==self.descriptor
                and socket_pin(self.path)==self.socket
                and self.listener.getsockname()==str(self.path),"TASK_OBSERVER_ENDPOINT_CHANGED")

    def _serve(self, connection):
        self.sequence+=1;directory=self.root/f'{self.sequence:06d}';directory.mkdir(mode=0o700)
        value=None
        try:
            self._verify_endpoint()
            value=request_value(frame(connection,limit=1024,seconds=1))
            request=write_json(directory/'request.json',value,exclusive=True)
            require(value['nonce'] not in self.seen, 'TASK_OBSERVER_NONCE_ALREADY_USED')
            self.seen.add(value['nonce'])
            began=time.monotonic()
            observation=self.source.read(connection,value['kind'])
            require(time.monotonic()-began<=30 and not self.stop_event.is_set(),
                    "TASK_OBSERVER_OBSERVATION_EXPIRED_OR_STOPPED")
            response=dict(schema=RESPONSE,nonce=value['nonce'],kind=value['kind'],status='OBSERVED',
                descriptor=self.descriptor,observation=observation,input_operations=0)
            require(len(canonical(response))<=MAX_REPLY,"TASK_OBSERVER_RESPONSE_TOO_LARGE")
            receipt=write_json(directory/'observation.json',dict(request=request,response=response),exclusive=True)
            self._verify_endpoint()
            response['evidence']=receipt
            require(len(canonical(response))<=MAX_REPLY,"TASK_OBSERVER_RESPONSE_TOO_LARGE")
            self.source.verify()
            require(not self.stop_event.is_set() and time.monotonic()<self.deadline,
                    "TASK_OBSERVER_BUDGET_OR_STOP_REACHED")
            connection.settimeout(2);connection.sendall(canonical(response))
            # Hold this connection while the client rechecks Darwin peer PID.
            connection.settimeout(1)
            try:ack=connection.recv(4)==b'ACK\n'
            except OSError:ack=False
            write_json(directory/'transport.json',dict(ack=ack,input_operations=0,
                model_consumption=False,native_user_receipt=False),exclusive=True)
        except (OSError,RuntimeError,ValueError,KeyError,TypeError) as exc:
            failure=dict(schema=RESPONSE,status='REFUSED',nonce=value.get('nonce') if isinstance(value,dict) else None,
                error_type=type(exc).__name__,error=str(exc)[:1000],input_operations=0)
            write_json(directory/'failure.json',failure,exclusive=True)
            try:
                connection.settimeout(1);connection.sendall(canonical(failure))
                connection.recv(4)
            except OSError:pass

    def _run(self):
        deadline=self.deadline
        try:
            write_json(self.root/'started.json',dict(descriptor=self.descriptor,owner_pid=self.pid,
                thread_id=threading.get_ident(),input_operations=0),exclusive=True)
            while not self.stop_event.is_set() and time.monotonic()<deadline and self.sequence<self.max_requests:
                self._verify_endpoint()
                # Stop is checked even when no client is connected. Long
                # synchronous reads are collected; this is not hard cancellation.
                require(not os.path.lexists(self.source.spec['stop_path']), 'EXPERIMENT_STOP_REQUESTED')
                ready,_,_=select.select([self.listener],[],[],min(.25,max(0,deadline-time.monotonic())))
                if not ready:continue
                connection,_=self.listener.accept()
                with connection:
                    connection.set_inheritable(False);self._serve(connection)
            reason='STOPPED' if self.stop_event.is_set() else 'BUDGET_EXHAUSTED'
            write_json(self.root/'terminal.json',dict(reason=reason,requests=self.sequence,input_operations=0),exclusive=True)
        except BaseException as exc:
            write_json(self.root/'terminal.json',dict(reason='FAILED',error_type=type(exc).__name__,
                error=str(exc)[:1000],requests=self.sequence,input_operations=0),exclusive=True)
        finally:self.closed=True;self.listener.close()

    def stop(self):
        require(os.getpid()==self.pid,"TASK_OBSERVER_OWNER_CHANGED")
        self.stop_event.set()
        if self.thread is None:
            self.closed=True;self.listener.close()
        else:
            self.thread.join(timeout=1)
        return dict(closed=self.closed,thread_alive=self.thread is not None and self.thread.is_alive(),
                    input_operations=0)


def start_observer(runtime_ref, *, seconds=1800, max_requests=2048):
    """Explicit external-owner entry; no automatic enrollment or model launch.

    The returned object must be retained and stopped by that same owner. A
    terminated instance cannot take over its exclusive original directory.
    """
    observer = ReadOnlyObserver(ObservationSource(runtime_ref), seconds=seconds,
                                max_requests=max_requests)
    try:
        observer.start()
    except BaseException:
        observer.stop()
        raise
    return observer
