"""Task RPC request retention and guaranteed cleanup; peer itself tested separately."""
import socket
import unittest
from unittest.mock import patch

from orchestration_common import ContractError
from upgrade_task_rpc import task_rpc_type


class Base:
    def __init__(self, path, timeout):
        self.sock=socket.socket(socket.AF_UNIX);self.timeout=timeout;self._id=0
        self.messages=[];self.sent=[]
    def _receive_json(self):return self.messages.pop(0)
    def _send_frame(self,payload,opcode=1):self.sent.append((payload,opcode))
    def close(self):
        try:self._send_frame(b"close",8)
        except (OSError,TimeoutError):pass
        self.sock.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


class RPCTests(unittest.TestCase):
    def setUp(self):
        self.check=patch('upgrade_task_rpc.verify_peer',return_value={'fixture_peer':True})
        self.verify=self.check.start();self.addCleanup(self.check.stop)
        self.records=[]
        self.client=task_rpc_type(Base)('/fixture',{},None,
            record=lambda *row:self.records.append(row))
        self.addCleanup(self.client.close)

    def test_server_request_id_collision_is_retained_and_never_used_as_client_reply(self):
        incoming={'id':1,'method':'item/tool/call','params':{'fixture':True}}
        self.client.messages=[incoming,{'method':'thread/started','params':{}},{'id':1,'result':{'ok':True}}]
        self.assertEqual(self.client.request('thread/start',{}),{'ok':True})
        self.assertEqual(self.client.receive(),incoming)
        self.assertEqual(self.client.receive()['method'],'thread/started')
        self.assertEqual(len(self.client.messages),0)
        self.assertEqual(len([r for r in self.records if r[0]=='received']),3)

    def test_peer_refusal_closes_fd_without_masking_original_failure(self):
        self.verify.side_effect=ContractError('peer disappeared')
        with self.assertRaisesRegex(RuntimeError,'original operation failed'):
            with self.client:raise RuntimeError('original operation failed')
        self.assertEqual(self.client.sock.fileno(),-1)
        self.assertIn('ContractError',self.client.close_failure)
        self.assertEqual(self.client.sent,[])

    def test_response_send_intent_precedes_failed_write_and_is_not_retried(self):
        response={'id':8,'result':{'success':True,'contentItems':[]}}
        # First peer check records intent; second, immediately before send, refuses.
        self.verify.side_effect=[{},ContractError('changed before write')]
        with self.assertRaises(ContractError):self.client.respond(response)
        self.assertEqual(self.records[0][0],'response_send_intent')
        self.assertEqual(self.client.sent,[])
        self.verify.side_effect=None

    def test_buffered_request_keeps_its_own_record_not_later_response(self):
        sequence = []

        def record(direction, message, peer):
            ref = {"path": "/fixture/" + str(len(sequence)), "message": message}
            sequence.append(ref)
            return ref

        self.client.record = record
        incoming = {"id": 99, "method": "item/tool/call", "params": {"callId": "exact"}}
        self.client.messages = [incoming, {"id": 1, "result": {"turn": "original"}}]
        self.client.request("turn/start", {})
        self.assertEqual(self.client.last_exchange["intent"], sequence[0])
        self.assertEqual(self.client.last_exchange["response"], sequence[2])
        message, ref = self.client.receive_recorded()
        self.assertEqual(message, incoming)
        self.assertEqual(ref, sequence[1])


if __name__=='__main__':unittest.main()
