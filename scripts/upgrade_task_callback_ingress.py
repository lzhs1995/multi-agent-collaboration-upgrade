"""One local input endpoint owned by the retained campaign runtime.

Explicit construction only; installation/import does not open a listener.
No subprocess, thread, reconnect or second model-response writer is created.
The default accepts original callback pack/attempt references. An explicit new
contract additionally accepts registered ordinary-message source/attempt refs.
Neither route grants shell, arbitrary text or operation execution authority.
"""
from contextvars import ContextVar
import os
from pathlib import Path
import select
import socket
import stat
import time

from orchestration_common import canonical, file_ref, json_ref, require, strict_json, write_json
from upgrade_rpc_peer import kernel_peer

SCHEMA = 'upgrade-task-callback-ingress-v1'
REQUEST_SCHEMA = 'upgrade-task-callback-request-v1'
LIMIT = 16384
_dispatch = ContextVar('original_callback_ingress_dispatch', default=None)
PROCESS_FIELDS = ('pid', 'birth', 'executable', 'file_identity')


def process_pin(process, pid):
    row = process(pid)
    require(row.get('pid') == pid, 'TASK_CALLBACK_INGRESS_PROCESS_CHANGED')
    return {key: row[key] for key in PROCESS_FIELDS}


def socket_pin(path):
    path = Path(path); st = path.lstat()
    require(path.is_absolute() and path.parent == path.parent.resolve()
            and stat.S_ISSOCK(st.st_mode) and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) == 0o600,
            'TASK_CALLBACK_INGRESS_SOCKET_CHANGED')
    return dict(path=str(path), device=st.st_dev, inode=st.st_ino,
                uid=st.st_uid, mode=stat.S_IMODE(st.st_mode))


def active_context(service, connection, request):
    """Only the same runtime's current synchronous accepted request qualifies."""
    current = _dispatch.get()
    if current is None:
        return False
    owner, actual_connection, actual_request = current
    return (type(owner) is CallbackIngress and owner.runtime.service is service
            and owner.runtime.callback_ingress is owner and owner.runtime.running
            and not owner.runtime.closed and actual_connection is connection
            and actual_request == request and not owner.closed)


class CallbackIngress:
    def __init__(self, runtime, *, allow_busy=False, allow_messages=False):
        from upgrade_task_runtime import CampaignRuntime
        require(type(runtime) is CampaignRuntime and not runtime.closed and not runtime.running
                and getattr(runtime, 'callback_ingress', None) is None,
                'TASK_CALLBACK_INGRESS_ORIGINAL_IDLE_OWNER_REQUIRED')
        runtime._verify(); runtime.gate._observe()
        self.runtime = runtime; self.pid = os.getpid(); self.closed = False
        require(type(allow_busy) is bool,'TASK_CALLBACK_BUSY_OPT_IN_REQUIRED')
        self.busy_enabled=allow_busy
        require(type(allow_messages) is bool and (not allow_messages or allow_busy),
                'TASK_MESSAGE_INGRESS_BUSY_CONTRACT_REQUIRED')
        self.messages_enabled=allow_messages
        if allow_busy:
            from upgrade_task_network import OBSERVER_SCHEMA
            binding=json_ref(file_ref(Path(runtime.gate.spec['controller'])/'upgrade-binding.json'))
            require(binding.get('task_model_network',{}).get('schema')==OBSERVER_SCHEMA,
                    'TASK_CALLBACK_BUSY_OBSERVER_CONTRACT_REQUIRED')
            require(not allow_messages or binding['task_model_network'].get('registered_messages') is True,
                    'TASK_MESSAGE_EXPLICIT_NETWORK_CONTRACT_REQUIRED')
        self.sequence = 0
        self.process = runtime.client.process
        self.owner = process_pin(self.process, self.pid)
        require(process_pin(self.process, self.pid) == self.owner,
                'TASK_CALLBACK_INGRESS_OWNER_CHANGED')
        self.root = runtime.gate.proof_path.parent / 'callback-ingress'
        self.path = self.root / 's'
        require(len(os.fsencode(self.path)) <= 103 and self.root.parent == self.root.parent.resolve(),
                'TASK_CALLBACK_INGRESS_SHORT_CANONICAL_PATH_REQUIRED')
        # Preserve this exclusive directory after failure or normal close. A
        # new object cannot take over an old request/endpoint by unlinking it.
        self.root.mkdir(mode=0o700, exist_ok=False)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.set_inheritable(False)
            self.listener.bind(str(self.path)); self.path.chmod(0o600)
            self.socket = socket_pin(self.path)
            from upgrade_task_callback_contract import BUSY_SCHEMA, MESSAGE_SCHEMA
            from upgrade_task_message_source import REQUEST_SCHEMA as message_schema
            self.proof = write_json(self.root/'descriptor.json', dict(schema=MESSAGE_SCHEMA if allow_messages else BUSY_SCHEMA if self.busy_enabled else SCHEMA,
                enrollment=runtime.gate.proof, campaign=runtime.gate.runner.ref,
                registration=runtime.gate.campaign['task_controls']['registration'],
                runtime_owner=runtime.proof, owner=self.owner, socket=self.socket,
                task_thread_id=runtime.ledger.registration['thread_id'],
                allowed_request=REQUEST_SCHEMA, reconnect_allowed=False,
                **(dict(allowed_message_request=message_schema) if allow_messages else {}),
                **(dict(allowed_input_methods=['turn/start','turn/steer']) if self.busy_enabled else {})), exclusive=True)
            self.listener.listen(4)
            self.listener_fd = self.listener.fileno()
            runtime.callback_ingress = self
        except BaseException:
            self.closed = True; self.listener.close(); raise

    def _verify(self):
        require(not self.closed and self.pid == os.getpid()
                and self.runtime.callback_ingress is self,
                'TASK_CALLBACK_INGRESS_OWNER_CHANGED')
        self.runtime._verify()
        require(file_ref(self.root/'descriptor.json') == self.proof
                and process_pin(self.process, self.pid) == self.owner
                and socket_pin(self.path) == self.socket
                and self.listener.getsockname() == str(self.path)
                and self.listener.fileno() == self.listener_fd
                and self.listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM,
                'TASK_CALLBACK_INGRESS_ENDPOINT_CHANGED')

    def poll(self, *, timeout=0):
        """At most one request on the owner's thread and current turn budget."""
        require(type(timeout) in (int,float) and 0 <= timeout <= 1
                and self.runtime.running and (self.runtime.actor.closed or self.busy_enabled),
                'TASK_CALLBACK_INGRESS_RUNTIME_DISPATCH_REQUIRED')
        self._verify()
        ready, _, _ = select.select([self.listener], [], [], timeout)
        self._verify()
        if not ready:
            return None
        connection, _ = self.listener.accept()
        connection.set_inheritable(False); connection.settimeout(1)
        self.sequence += 1
        directory = self.root / f'{self.sequence:06d}'
        directory.mkdir(mode=0o700, exist_ok=False)
        called = False; response_intended = False
        response_schema='upgrade-task-callback-response-v1'
        with connection:
            try:
                peer = kernel_peer(connection)
                data = bytearray(); deadline = time.monotonic()+1
                while b'\n' not in data:
                    require(time.monotonic() < deadline, 'TASK_CALLBACK_INGRESS_FRAME_TIMEOUT')
                    connection.settimeout(max(.001,deadline-time.monotonic()))
                    chunk = connection.recv(min(4096, LIMIT+1-len(data)))
                    require(chunk, 'TASK_CALLBACK_INGRESS_INCOMPLETE_FRAME')
                    data.extend(chunk)
                    require(len(data) <= LIMIT, 'TASK_CALLBACK_INGRESS_FRAME_TOO_LARGE')
                require(data.endswith(b'\n') and data.count(b'\n') == 1
                        and kernel_peer(connection) == peer,
                        'TASK_CALLBACK_INGRESS_ORIGINAL_FRAME_CHANGED')
                value = strict_json(bytes(data), 'callback request')
                from upgrade_task_message_source import REQUEST_SCHEMA as message_schema
                ordinary=isinstance(value,dict) and value.get('schema')==message_schema
                fields=('source','attempt') if ordinary else ('pack','attempt')
                require(not ordinary or self.messages_enabled,'TASK_MESSAGE_INGRESS_NOT_ENABLED')
                require(isinstance(value,dict) and set(value) == {'schema',*fields}
                        and value['schema'] == (message_schema if ordinary else REQUEST_SCHEMA),
                        'TASK_CALLBACK_INGRESS_REQUEST_INVALID')
                if ordinary:response_schema='upgrade-task-message-response-v1'
                request = {k:value[k] for k in fields}
                for pin in request.values():
                    require(isinstance(pin,dict) and set(pin) == {'path','bytes','sha256'},
                            'TASK_CALLBACK_INGRESS_EXACT_REFERENCE_REQUIRED')
                incoming = write_json(directory/'request.json', dict(request=value,
                    ingress=self.proof, peer=peer, input_sent=False), exclusive=True)
                self._verify()
                require(_dispatch.get() is None, 'TASK_CALLBACK_INGRESS_REENTRANT_REQUEST')
                token = _dispatch.set((self, connection, request))
                try:
                    if ordinary:
                        from upgrade_task_message_turn import MessageInput, deliver_prepared
                        incoming_message=MessageInput(self.runtime.service,connection,request,
                                                      busy=not self.runtime.actor.closed)
                        called=True
                        result=deliver_prepared(incoming_message)
                    else:
                        called=True
                        if self.runtime.actor.closed:
                            result = self.runtime.service.start_callback_turn(connection, request)
                        else:
                            from upgrade_task_callback_busy import deliver_busy
                            result = deliver_busy(self.runtime.service, connection, request)
                finally:
                    _dispatch.reset(token)
                # The source result preserves enrollment independently of this
                # socket response. A failed response never grants another input.
                proof = write_json(directory/'result.json', dict(request=incoming,
                    result=result, retry_allowed=False), exclusive=True)
                self._verify()
                require(kernel_peer(connection) == peer, 'TASK_CALLBACK_INGRESS_PEER_CHANGED')
                response = dict(schema=response_schema, status='ENROLLED',
                    result=result, evidence=proof, native_user_receipt=False, retry_allowed=False)
                write_json(directory/'response-intent.json', response, exclusive=True)
                response_intended = True
                connection.settimeout(1); connection.sendall(canonical(response))
                self._response_ack(connection,directory)
                return dict(result, ingress_result=proof)
            except BaseException as exc:
                failure = dict(schema=response_schema,
                    status='UNKNOWN_NO_REPLAY' if called else 'REFUSED_BEFORE_SERVICE',
                    error_type=type(exc).__name__, error=str(exc)[:1000], retry_allowed=False)
                write_json(directory/'failure.json', failure, exclusive=True)
                if not response_intended:
                    try:
                        connection.settimeout(.25); connection.sendall(canonical(failure))
                        self._response_ack(connection,directory)
                    except (OSError,ValueError):
                        pass
                if called:
                    raise
                return dict(status='REFUSED_BEFORE_SERVICE', model_inputs_sent=0,
                            failure=file_ref(directory/'failure.json'))

    @staticmethod
    def _response_ack(connection,directory):
        # Darwin can lose peer credentials after remote close. Retain the FD
        # while the caller checks the response's actual peer. A transport ACK
        # is explicitly not model consumption or native user reception.
        value=dict(transport_ack=False,native_user_receipt=False,model_consumption=False)
        try:
            connection.settimeout(1)
            value['transport_ack']=connection.recv(5)==b'ACK\n'
        except OSError as exc:
            value['error_type']=type(exc).__name__
        write_json(directory/'response-observation.json',value,exclusive=True)

    def close(self):
        """Close only this FD. Preserve the descriptor, socket path and journal."""
        require(self.pid == os.getpid(), 'TASK_CALLBACK_INGRESS_OWNER_CHANGED')
        if not self.closed:
            self.closed = True
            self.listener.close()
