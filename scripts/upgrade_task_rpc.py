"""Persistent task RPC retaining server requests; no task authority by itself.

Use with the explicitly pinned same-release DaemonRPC implementation. The base
readonly client intentionally drops notifications, whereas an actor must retain
them. This factory changes neither that original source nor existing attempts.
The caller journals each input and response intent before requesting a send.
"""
from collections import deque
import time

from orchestration_common import canonical, require
from upgrade_dynamic_route import envelope
from upgrade_rpc_peer import verify_peer


def task_rpc_type(base):
    class TaskRPC(base):
        def __init__(self, path, server, process, *, record, timeout=20):
            self.server = server
            self.process = process
            self.record = record
            self.pending = deque()
            self.last_exchange = None
            self.last_received_record = None
            super().__init__(path, timeout=timeout)
            self.initialize_exchange = self.last_exchange

        def _upgrade(self):
            verify_peer(self.sock, self.server, self.process)
            super()._upgrade()
            verify_peer(self.sock, self.server, self.process)

        def _receive_json(self):
            message = envelope(super()._receive_json())
            peer = verify_peer(self.sock, self.server, self.process)
            self.last_received_record = self.record("received", message, peer)
            return message

        def _send_frame(self, payload, opcode=1):
            verify_peer(self.sock, self.server, self.process)
            super()._send_frame(payload, opcode)

        def request(self, method, params=None):
            self.last_exchange = None
            self._deadline = time.monotonic() + self.timeout
            self._id += 1
            outgoing = {"id": self._id, "method": method}
            if params is not None:
                outgoing["params"] = params
            intent = self.record("send_intent", outgoing, verify_peer(self.sock, self.server, self.process))
            self._send_frame(canonical(outgoing))
            while True:
                msg = self._receive_json()
                if "method" in msg:
                    require(len(self.pending) < 1024, "TASK_RPC_PENDING_BUDGET_EXHAUSTED")
                    self.pending.append((msg, self.last_received_record))
                    continue
                require(type(msg["id"]) is int and msg["id"] == self._id, "TASK_RPC_UNEXPECTED_RESPONSE")
                require("error" not in msg, "TASK_RPC_SERVER_ERROR: " + str(msg.get("error")))
                # A controller that returns immutable file refs from record()
                # can bind this exact exchange to a new task registration.
                # Existing diagnostic recorders returning None remain valid;
                # they cannot manufacture a startup exchange proof.
                self.last_exchange = {"intent": intent, "response": self.last_received_record}
                return msg["result"]

        def receive(self, timeout=20):
            return self.receive_recorded(timeout)[0]

        def receive_recorded(self, timeout=20):
            """Return the original message with its own immutable record ref.

            A callback buffered while waiting for turn/start must not inherit
            the later turn/start response's evidence reference.
            """
            require(0 < timeout <= 120, "TASK_RPC_TIMEOUT_INVALID")
            verify_peer(self.sock, self.server, self.process)
            if self.pending:
                return self.pending.popleft()
            self._deadline = time.monotonic() + timeout
            msg = self._receive_json()
            return msg, self.last_received_record

        def respond(self, intended_response):
            response = envelope(intended_response)
            require("method" not in response, "TASK_RPC_RESPONSE_REQUIRED")
            self.record("response_send_intent", response, verify_peer(self.sock, self.server, self.process))
            self._deadline = time.monotonic() + self.timeout
            self._send_frame(canonical(response))
            # A successful socket write is deliberately not native completion.

        def close(self):
            # The base client's narrow exception handler predates peer checks.
            # A refused close-frame write must still close the descriptor, and
            # cleanup must not replace the original operation failure.
            try:
                super().close()
            except Exception as exc:
                self.close_failure = type(exc).__name__ + ": " + str(exc)
            finally:
                self.sock.close()

    return TaskRPC
