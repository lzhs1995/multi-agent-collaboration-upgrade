"""Real Unix peer/framing/descendant transport; runtime and sources are fixtures.

No production endpoints, agents, API calls, adoption or business inputs.
"""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import cmux_daemon_identity as identity
from orchestration_common import ContractError, canonical, json_ref, require, write_json
from upgrade_task_network import network_contract, observer_path, model_only_profile
from upgrade_task_observation import ObservationSource, caller
from upgrade_task_observer import ReadOnlyObserver
from upgrade_task_observer_protocol import (SCHEMA, OBSERVATION, REQUEST, MAX_REPLY,
    PROCESS_FIELDS, frame, request_value)
import upgrade_task_observer_client as client

# Capture before test_all installs its ordinary-client default. These tests
# explicitly exercise the Darwin reader on this test process and its children.
KERNEL_PROCESS = identity.process


class ObserverTransportTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin':
            self.skipTest('Darwin process reader required for real observer transport')
        kernel = patch.object(identity, 'process', KERNEL_PROCESS)
        kernel.start(); self.addCleanup(kernel.stop)
        tmp=tempfile.TemporaryDirectory(prefix='ob',dir='/tmp');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        self.binding=dict(evidence_root=str(self.root), config_root=str(self.root/'config'),
                          controller_root=str(self.root/'controllers/releases/private-live-v2/source'))
        self.binding['task_model_network']=network_contract(dict(codex=dict(base_url='http://127.0.0.1:3465')),
            dict(env=dict(ANTHROPIC_BASE_URL='http://127.0.0.1:3445')),evidence_root=self.root,observer=True)
        runtime=write_json(self.root/'controllers/runtime-v2.json',{'runtime_fixture':True})
        startup=write_json(self.root/'config/startup-v2.json',{'startup_fixture':True})
        self.source=object.__new__(ObservationSource)
        self.source.binding=self.binding;self.source.runtime_ref=runtime
        self.source.runtime=dict(startup=startup);self.source.spec=dict(stop_path=str(self.root/'STOP'))
        self.source.process=identity.process;self.source.owner_pid=os.getpid()
        row=identity.process(os.getpid())
        self.source.owner={k:row[k] for k in PROCESS_FIELDS}
        self.launch=dict(pid=row['pid'],birth=row['birth'])
        def verify(source):
            require(not Path(source.spec['stop_path']).exists(),'EXPERIMENT_STOP_REQUESTED')
        def read(source,connection,kind):
            verify(source)
            who=caller(connection,{'claude_a':self.launch},identity.process)
            return dict(schema=OBSERVATION,runtime=runtime,startup=startup,caller=who,
                tree={'windows':[]},task=None,sessions={},input_operations=0,adoption_proven=False,
                requester_direct_rpc_peer=False,identity_source='original_controller_read_only_observation')
        for p in (patch.object(ObservationSource,'verify',verify),patch.object(ObservationSource,'read',read)):
            p.start();self.addCleanup(p.stop)

    def start(self,requests=8):
        self.service=ReadOnlyObserver(self.source,seconds=15,max_requests=requests)
        self.service.start();self.addCleanup(self.service.stop)
        return self.service

    def child(self,code):
        header="import sys,json;sys.path.insert(0,sys.argv[1]);binding=json.loads(sys.argv[2]);"
        return subprocess.run([sys.executable,'-I','-B','-c',header+code,
            str(Path(__file__).parent),json.dumps(self.binding)],capture_output=True,text=True,
            timeout=20,close_fds=True)

    def test_client_kernel_peer_and_original_evidence_roundtrip(self):
        service=self.start()
        result=self.child("import cmux_daemon_identity as i;from upgrade_task_observer_client import read;"
                          "r=read(binding,'tree',process=i.process);print(json.dumps(r))")
        self.assertEqual((result.returncode,result.stderr),(0,''))
        value=json.loads(result.stdout)
        self.assertEqual(value['observation']['caller']['lane'],'claude_a')
        self.assertNotEqual(value['observation']['caller']['peer']['pid'],os.getpid())
        self.assertFalse(value['observation']['requester_direct_rpc_peer'])
        self.assertEqual(json_ref(value['evidence'])['response'],{k:v for k,v in value.items() if k!='evidence'})
        self.assertTrue(service.stop()['closed'])
        transport=json.loads((service.root/'000001/transport.json').read_text())
        self.assertTrue(transport['ack']);self.assertFalse(transport['model_consumption'])
        self.assertTrue(service.path.exists())  # old exclusive endpoint retained
        with self.assertRaises(FileExistsError):ReadOnlyObserver(self.source)

    def test_foreign_or_root_process_cannot_impersonate_descendant(self):
        self.start()
        with self.assertRaisesRegex(ContractError,'UNAVAILABLE_OR_CHANGED'):
            client.read(self.binding,'tree',process=identity.process)
        failure=json.loads((self.service.root/'000001/failure.json').read_text())
        self.assertIn('TOOL_DESCENDANT_REQUIRED',failure['error'])

    def test_stop_rejects_response_and_ends_idle_service(self):
        service=self.start()
        Path(self.source.spec['stop_path']).touch()
        service.thread.join(timeout=2)
        self.assertFalse(service.thread.is_alive())
        terminal=json.loads((service.root/'terminal.json').read_text())
        self.assertEqual(terminal['reason'],'FAILED')
        self.assertIn('EXPERIMENT_STOP_REQUESTED',terminal['error'])

    def test_endpoint_replacement_is_detected_before_next_request(self):
        service=self.start()
        with socket.socket(socket.AF_UNIX) as other:
            alternate=self.root/'replacement';other.bind(str(alternate));alternate.chmod(0o600)
            os.replace(alternate,service.path)
            with self.assertRaisesRegex(ContractError,'ENDPOINT_CHANGED'):service._verify_endpoint()
        service.thread.join(timeout=2)
        self.assertFalse(service.thread.is_alive())

    def test_frame_budget_invalid_kind_extra_method_and_multi_frame_refused(self):
        for value in ({'schema':REQUEST,'kind':'turn/start','nonce':'a'*48},
                      {'schema':REQUEST,'kind':'tree','nonce':'a'*48,'method':'send'},
                      {'schema':REQUEST,'kind':'tree','nonce':'short'}):
            with self.subTest(value=value),self.assertRaises(ContractError):request_value(value)
        for raw in (b'{}\n{}\n',b'x'*33,b'{}\ntrailing'):
            left,right=socket.socketpair()
            with left,right:
                left.sendall(raw);left.shutdown(socket.SHUT_WR)
                with self.assertRaises(ContractError):frame(right,limit=32,seconds=.2)

    def test_duplicate_nonce_has_no_second_observation(self):
        service=self.start()
        result=self.child("""
import socket
from upgrade_task_observer_protocol import frame,REQUEST
from orchestration_common import canonical
rows=[]
for _ in range(2):
 with socket.socket(socket.AF_UNIX) as c:
  c.connect(binding['task_model_network']['observer'])
  c.sendall(canonical(dict(schema=REQUEST,kind='tree',nonce='a'*48)))
  rows.append(frame(c,limit=262144,seconds=10));c.sendall(b'ACK\\n')
print(json.dumps(rows))
""")
        self.assertEqual((result.returncode,result.stderr),(0,''))
        first,second=json.loads(result.stdout)
        self.assertEqual(first['status'],'OBSERVED');self.assertEqual(second['status'],'REFUSED')
        self.assertIn('NONCE_ALREADY_USED',second['error'])
        self.assertFalse((service.root/'000002/observation.json').exists())

    def test_client_module_does_not_import_service_or_actor(self):
        result=self.child("import upgrade_task_observer_client;"
            "print(json.dumps([n for n in sys.modules if n in ('upgrade_task_observer',"
            "'upgrade_task_actor','upgrade_task_runtime','upgrade_task_callback_ingress')]))")
        self.assertEqual((result.returncode,result.stderr),(0,''));self.assertEqual(json.loads(result.stdout),[])

    @unittest.skipUnless(sys.platform=='darwin','Darwin sandbox required')
    def test_three_lanes_and_descendants_only_reach_fixed_observer_and_callback(self):
        contract=self.binding['task_model_network']
        paths=[Path(contract['observer']),Path(contract['callback']),self.root/'broker',self.root/'rpc']
        listeners=[]
        try:
            for path in paths:
                path.parent.mkdir(parents=True,exist_ok=True)
                s=socket.socket(socket.AF_UNIX);listeners.append(s);s.bind(str(path));s.listen(16)
            (self.root/'broker-alias').symlink_to(paths[2])
            code="""import json,socket,sys
result=[]
for p in sys.argv[1:]:
 try:
  with socket.socket(socket.AF_UNIX) as s:s.settimeout(1);s.connect(p)
  result.append(True)
 except OSError as e:result.append(e.errno)
print(json.dumps(result))
"""
            wrapper="import subprocess,sys;r=subprocess.run([sys.executable,'-I','-B','-c',sys.argv[1],*sys.argv[2:]],capture_output=True,text=True,timeout=8);print(r.stdout,end='');print(r.stderr,end='',file=sys.stderr);sys.exit(r.returncode)"
            for lane in ('codex','claude_a','claude_b'):
                policy=self.root/(lane+'.sb');policy.write_bytes(model_only_profile(b'(version 1)\n(allow default)\n',contract,lane))
                for descendant in (False,True):
                    with self.subTest(lane=lane,descendant=descendant):
                        program=['-c',wrapper,code] if descendant else ['-c',code]
                        result=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy),sys.executable,'-I','-B',
                            *program,*map(str,paths),str(self.root/'broker-alias')],capture_output=True,text=True,timeout=12)
                        self.assertEqual((result.returncode,result.stderr),(0,''))
                        self.assertEqual(json.loads(result.stdout),[True,1 if lane=='codex' else True,1,1,1])
        finally:
            for s in listeners:s.close()


if __name__=='__main__':unittest.main()
