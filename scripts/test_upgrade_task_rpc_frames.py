"""Real local WebSocket frames; peer/enrollment are separate fixture checks."""
from collections import deque
import json
import os
import socket
import struct
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError
from upgrade_task_rpc import task_rpc_type
import upgrade_task_rpc as rpc


class DaemonRPC:
    """Socket I/O fixture; same-release parser integration is checked separately."""
    def _read(self,length):
        while len(self._buffer)<length:
            left=self._deadline-time.monotonic()
            if left<=0:raise TimeoutError('fixture frame deadline')
            self.sock.settimeout(left)
            raw=self.sock.recv(length-len(self._buffer))
            if not raw:raise ConnectionError('fixture peer closed')
            self._buffer.extend(raw)
        result=bytes(self._buffer[:length]);del self._buffer[:length]
        return result

    def _send_frame(self,payload,opcode=1):
        mask=os.urandom(4)
        self.sock.settimeout(max(0.001,self._deadline-time.monotonic()))
        self.sock.sendall(bytes([0x80|opcode,0x80|len(payload)])+mask+
                          bytes(v^mask[i%4] for i,v in enumerate(payload)))


def frame(payload,opcode=1,*,final=True):
    first=(0x80 if final else 0)|opcode
    return (bytes([first,len(payload)]) if len(payload)<126 else
            bytes([first,126])+struct.pack('!H',len(payload)))+payload


class FrameTests(unittest.TestCase):
    def setUp(self):
        a,self.peer=socket.socketpair(socket.AF_UNIX)
        self.addCleanup(a.close);self.addCleanup(self.peer.close)
        cls=task_rpc_type(DaemonRPC);self.client=cls.__new__(cls)
        self.client.sock=a;self.client.server={};self.client.process=None
        self.client._buffer=bytearray();self.client.pending=deque();self.client.timeout=0.05
        self.records=[]
        def record(direction,message,peer):
            ref=dict(index=len(self.records),direction=direction,message=message)
            self.records.append(ref);return ref
        self.client.record=record
        check=patch('upgrade_task_rpc.verify_peer',return_value={'fixture':True})
        check.start();self.addCleanup(check.stop)

    def poll(self):return self.client.poll_recorded(timeout=0,frame_timeout=0.03)

    def test_ping_and_pong_without_data_are_idle_and_only_ping_gets_pong(self):
        self.peer.sendall(frame(b'P',9)+frame(b'Q',10))
        self.assertIsNone(self.poll())
        self.peer.settimeout(0.03);reply=self.peer.recv(64)
        self.assertEqual(reply[0],0x8a);self.assertEqual(reply[1],0x81)
        self.assertEqual(bytes([reply[6]^reply[2]]),b'P')
        with self.assertRaises(TimeoutError):self.peer.recv(1)
        self.assertEqual(self.records,[])

    def test_control_frames_do_not_reorder_json_or_invent_receive_records(self):
        messages=[{'method':'fixture/one'},{'method':'fixture/two'}]
        self.peer.sendall(frame(b'ping',9)+frame(json.dumps(messages[0]).encode())+
                          frame(b'pong',10)+frame(json.dumps(messages[1]).encode()))
        got=[self.poll() for _ in messages]
        self.assertEqual([message for message,_ in got],messages)
        self.assertEqual([ref for _,ref in got],self.records)
        self.assertIsNone(self.poll())

    def test_fragmented_json_with_interleaved_ping_is_one_original_message(self):
        message={'method':'fixture/fragmented','params':{'text':'中文'}}
        raw=json.dumps(message).encode();cut=len(raw)//2
        self.peer.sendall(frame(raw[:cut],final=False)+frame(b'P',9)+frame(raw[cut:],0))
        self.assertEqual(self.poll()[0],message)
        self.assertEqual(len(self.records),1)

    def test_complete_control_does_not_start_deadline_for_later_data(self):
        message={'method':'fixture/after-idle'}
        self.peer.sendall(frame(b'',10))
        # Advance the parser clock between real socket frames. A thread timer
        # can miss the idle window on a loaded runner without testing the parser.
        clock_value=[time.monotonic()]
        clock=SimpleNamespace(monotonic=lambda:clock_value[0])
        original_select=rpc.select.select
        waits=[]
        def after_control(readers,writers,errors,timeout):
            waits.append(timeout)
            if len(waits)==2:
                clock_value[0]+=0.06
                self.peer.sendall(frame(json.dumps(message).encode()))
            return original_select(readers,writers,errors,0)
        with patch.object(rpc,'time',clock), patch(__name__+'.time',clock), \
                patch.object(rpc,'select',SimpleNamespace(select=after_control)):
            got=self.client.poll_recorded(timeout=0.2,frame_timeout=0.03)
        self.assertEqual(len(waits),2)
        self.assertIsNotNone(got)
        self.assertEqual(got[0],message)

    def test_incomplete_data_fragments_and_partial_frames_never_become_idle(self):
        for raw in (b'\x81',b'\x81\x04ab',frame(b'{',final=False)+frame(b'P',9),b'\x89\x02P'):
            with self.subTest(raw=raw):
                a,b=socket.socketpair(socket.AF_UNIX)
                previous=self.client.sock;self.client.sock=a;self.client._buffer=bytearray()
                try:
                    b.sendall(raw)
                    with self.assertRaises(TimeoutError):self.poll()
                finally:a.close();b.close();self.client.sock=previous
        self.assertEqual(self.records,[])

    def test_invalid_control_frame_and_disconnect_are_not_idle(self):
        self.peer.sendall(frame(b'P',9,final=False))
        with self.assertRaisesRegex(ContractError,'INVALID_CONTROL_FRAME'):self.poll()
        self.peer.close()
        with self.assertRaises(ConnectionError):self.poll()

    def test_control_flood_has_finite_pulse_and_preserves_next_json(self):
        message={'method':'fixture/after-controls'}
        self.peer.sendall(frame(b'',10)*65+frame(json.dumps(message).encode()))
        self.assertIsNone(self.poll())
        self.assertEqual(self.poll()[0],message)
        self.assertEqual(len(self.records),1)

    def test_control_only_ping_does_not_close_original_service_or_reset_budget(self):
        import test_upgrade_task_service as fixture
        f=fixture.ServiceTests();f.setUp();self.addCleanup(f.doCleanups)
        f.complete_candidate()
        # Use original fixture peer proof for the actor; the parser uses the
        # actual local socket. No production enrollment is claimed here.
        self.client.server=f.client.server;self.client.process=f.client.process
        f.f.actor.client=self.client
        self.peer.sendall(frame(b'P',9))
        result=f.service.pulse(seconds=0.03,max_messages=1)
        self.assertEqual(result['status'],'ACTIVE')
        self.assertFalse(f.f.actor.closed)
        self.assertEqual(result['pending_binding_calls'],['inner-candidate'])
        self.assertEqual(len(f.f.sent),3)
        self.assertEqual(f.f.ledger.db.execute('SELECT count(*) FROM calls').fetchone()[0],3)
        self.assertEqual(self.records,[])


if __name__=='__main__':unittest.main()
