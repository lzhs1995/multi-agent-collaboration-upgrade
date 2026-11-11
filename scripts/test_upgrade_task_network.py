"""Actual Darwin egress/descendant probes; no model or production service calls."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack

from orchestration_common import ContractError
from upgrade_task_network import gateway_port,model_only_profile,network_contract


class NetworkTests(unittest.TestCase):
    def test_only_frozen_explicit_loopback_routes_are_accepted(self):
        for url in ("https://127.0.0.1:3445","http://localhost:3445","http://127.0.0.2:3445",
                    "http://127.0.0.1:3445/admin","http://x@127.0.0.1:3445","http://127.0.0.1:3445?x=1",
                    "http://127.0.0.1","http://127.0.0.1:0","http://127.0.0.1:65536","http://127.0.0.1:03445",
                    " http://127.0.0.1:3445","http://127.0.0.1:3445\n","http://127.0.0.1:34\t45"):
            with self.subTest(url=url),self.assertRaises(ContractError):gateway_port(url)
        self.assertEqual(gateway_port("http://127.0.0.1:3445/v1"),3445)
        station={"codex":{"base_url":"http://127.0.0.1:3465"}}
        contract=network_contract(station,{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3445"}})
        self.assertEqual(contract["ports"],dict(codex=3465,claude_a=3445,claude_b=3445))
        with self.assertRaises(ContractError):network_contract(station,{"env":{}})
        with self.assertRaises(ContractError):network_contract(station,{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3465"}})

    @unittest.skipUnless(sys.platform=="darwin","Darwin sandbox required")
    def test_each_lane_and_descendant_denies_brokers_and_reaches_only_own_port(self):
        with tempfile.TemporaryDirectory(prefix="tn",dir="/tmp") as tmp:
            root=Path(tmp).resolve()
            with ExitStack() as stack, socket.socket() as codex,socket.socket() as claude,socket.socket() as broker,socket.socket(socket.AF_UNIX) as unix:
                for server in (codex,claude,broker):server.bind(("127.0.0.1",0));server.listen(8);server.settimeout(3)
                unix.bind(str(root/"raw-broker"));unix.listen(8)
                (root/"alias").symlink_to(root/"raw-broker")
                cport,hport,bport=[server.getsockname()[1] for server in (codex,claude,broker)]
                ipv6={}
                for port in (cport,hport):
                    udp=stack.enter_context(socket.socket(type=socket.SOCK_DGRAM))
                    udp.bind(("127.0.0.1",port));udp.settimeout(0.05)
                    stream=stack.enter_context(socket.socket(socket.AF_INET6))
                    stream.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
                    stream.bind(("::1",port));stream.listen(8);stream.settimeout(3)
                    ipv6[port]=stream
                contract=dict(schema="upgrade-task-model-network-v1",ports=dict(codex=cport,claude_a=hport,claude_b=hport))
                probe=r'''import json,socket,sys
rows=[]
for family,kind,target in [(socket.AF_INET,socket.SOCK_STREAM,('127.0.0.1',int(sys.argv[1]))),
    (socket.AF_INET,socket.SOCK_STREAM,('127.0.0.1',int(sys.argv[2]))),
    (socket.AF_INET,socket.SOCK_STREAM,('127.0.0.1',int(sys.argv[3]))),
    (socket.AF_UNIX,socket.SOCK_STREAM,sys.argv[4]),(socket.AF_UNIX,socket.SOCK_STREAM,sys.argv[5]),
    (socket.AF_INET,socket.SOCK_DGRAM,('127.0.0.1',int(sys.argv[1]))),
    (socket.AF_INET6,socket.SOCK_STREAM,('::1',int(sys.argv[1]))),
    (socket.AF_INET6,socket.SOCK_STREAM,('::1',int(sys.argv[2]))),
    (socket.AF_INET6,socket.SOCK_DGRAM,('::1',int(sys.argv[1])))]:
 try:
  with socket.socket(family,kind) as s:s.settimeout(1);s.connect(target);s.sendall(b'OK')
  rows.append({'connected':True})
 except OSError as e:rows.append({'errno':e.errno})
print(json.dumps(rows))
'''
                wrapper="import subprocess,sys\nr=subprocess.run([sys.executable,'-I','-B','-c',sys.argv[1],*sys.argv[2:]],capture_output=True,text=True,timeout=8)\nprint(r.stdout,end='');print(r.stderr,end='',file=sys.stderr);sys.exit(r.returncode)"
                for lane in ("codex","claude_a","claude_b"):
                    own=contract["ports"][lane];other=hport if lane=="codex" else cport
                    policy=root/(lane+".sb")
                    policy.write_bytes(model_only_profile(b"(version 1)\n(allow default)\n",contract,lane))
                    for descendant in (False,True):
                        with self.subTest(lane=lane,descendant=descendant):
                            program=["-c",wrapper,probe] if descendant else ["-c",probe]
                            result=subprocess.run(["/usr/bin/sandbox-exec","-f",str(policy),sys.executable,"-I","-B",
                                *program,str(own),str(other),str(bport),str(root/"raw-broker"),str(root/"alias")],
                                capture_output=True,text=True,timeout=12,close_fds=True)
                            self.assertEqual((result.returncode,result.stderr),(0,""))
                            self.assertEqual(json.loads(result.stdout),
                                [{"connected":True}]+[{"errno":1}]*5+[{"connected":True}]+[{"errno":1}]*2)
                            accepted,_=(codex if lane=="codex" else claude).accept()
                            with accepted:self.assertEqual(accepted.recv(2),b"OK")
                            accepted,_=ipv6[own].accept()
                            with accepted:self.assertEqual(accepted.recv(2),b"OK")
                # The unconfined controller still reaches the original broker.
                with socket.socket(socket.AF_UNIX) as trusted:trusted.connect(str(root/"raw-broker"))
                with socket.socket() as trusted:trusted.connect(("127.0.0.1",bport))


if __name__=="__main__":unittest.main()
