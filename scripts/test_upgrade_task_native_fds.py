"""Real Darwin requester/socket/fileport observations; deployment is a fixture."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, file_ref, write_json
from upgrade_task_fd_contract import verify, contract, enabled
from upgrade_task_native_fds import observe
from upgrade_task_observation import ObservationSource
import test_upgrade_task_observer_transport as transport


@unittest.skipUnless(sys.platform == 'darwin', 'Darwin libproc required')
class NativeDescriptorTests(unittest.TestCase):
    def setUp(self):
        self.f = transport.ObserverTransportTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        original = ObservationSource.read
        def read(source, connection, kind):
            value = original(source, connection, kind)
            value['requester_descriptors'] = observe(connection, value['caller'],
                source.binding['task_model_network']['observer'], source.process)
            return value
        patcher = patch.object(ObservationSource, 'read', read)
        patcher.start(); self.addCleanup(patcher.stop)
        self.service = self.f.start()

    def child(self, prefix='', fds=()):
        code = '''import sys,json,os
sys.path.insert(0,sys.argv[1]);binding=json.loads(sys.argv[2])
'''+prefix+'''
import cmux_daemon_identity as identity
from upgrade_task_observer_client import read
result=read(binding,'tree',process=identity.process)
print(json.dumps(result))
'''
        return subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(__file__).parent),
            json.dumps(self.f.binding)],capture_output=True,text=True,timeout=20,
            close_fds=True,pass_fds=fds)

    def test_exact_kernel_connected_socket_and_historical_snapshot(self):
        result=self.child()
        self.assertEqual((result.returncode,result.stderr),(0,''))
        value=json.loads(result.stdout)['observation']
        fd=value['requester_descriptors']; self.service.stop()
        self.assertEqual(verify(fd,value['caller'],self.f.binding['task_model_network']['observer']),fd)
        self.assertFalse(fd['whole_client_isolation_proven'])
        self.assertEqual(fd['before']['fileports'],[])
        self.assertEqual(len(fd['before']['fds']),4)
        changed=dict(fd,requester_socket=dict(fd['requester_socket'],connected_socket=1))
        with self.assertRaises(ContractError):verify(changed,value['caller'],fd['endpoint'])

    def test_inherited_live_socket_is_rejected_without_closing_parent_channel(self):
        first,second=socket.socketpair()
        with first,second:
            result=self.child(f'os.fstat({first.fileno()})\n',(first.fileno(),))
            self.assertNotEqual(result.returncode,0)
            failure=json.loads((self.service.root/'000001/failure.json').read_text())
            self.assertIn('TASK_NATIVE_DESCRIPTOR_EXTRA_CHANNEL',failure['error'])
            first.sendall(b'OK');self.assertEqual(second.recv(2),b'OK')

    def test_extra_high_file_descriptor_is_rejected(self):
        result=self.child("fd=os.open('/dev/null',os.O_RDONLY);os.dup2(fd,4096);os.close(fd)\n")
        self.assertNotEqual(result.returncode,0)
        failure=json.loads((self.service.root/'000001/failure.json').read_text())
        self.assertIn('TASK_NATIVE_DESCRIPTOR_EXTRA_CHANNEL',failure['error'])

    def test_fileport_hidden_from_fd_list_is_rejected(self):
        result=self.child('''import ctypes as C
lib=C.CDLL(None,use_errno=True)
make=lib.fileport_makeport;make.argtypes=(C.c_int,C.POINTER(C.c_uint));make.restype=C.c_int
fd=os.open('/dev/null',os.O_RDONLY);port=C.c_uint(0)
assert make(fd,C.byref(port))==0 and port.value
os.close(fd)
''')
        self.assertNotEqual(result.returncode,0)
        failure=json.loads((self.service.root/'000001/failure.json').read_text())
        self.assertIn('TASK_NATIVE_DESCRIPTOR_EXTRA_CHANNEL',failure['error'])


@unittest.skipUnless(sys.platform == 'darwin', 'Darwin libproc required')
class DescriptorReadinessTests(unittest.TestCase):
    def setUp(self):
        from upgrade_task_mach_policy import contract as mach_contract
        self.f=transport.ObserverTransportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.binding.update(task_startup={'fixture':True},task_mach_boundary=mach_contract(),
            task_native_descriptors=contract(),state_root=str(self.f.root/'state'))
        write_json(self.f.root/'state/startup/claude_a.json',
                   dict(self.f.launch,startup=self.f.source.runtime['startup']))
        original=ObservationSource.read
        def read(source,connection,kind):
            value=original(source,connection,kind)
            if kind=='startup':
                value['requester_descriptors']=observe(connection,value['caller'],
                    source.binding['task_model_network']['observer'],source.process)
            return value
        patcher=patch.object(ObservationSource,'read',read);patcher.start();self.addCleanup(patcher.stop)
        self.service=self.f.start()

    def test_startup_capture_and_generated_private_reader_use_same_exact_fd_evidence(self):
        from upgrade_task_observer_evidence import verify as verify_observation
        from upgrade_live_materialize import render_private_support
        result=self.f.child("from upgrade_task_observer_evidence import capture;"
            "from orchestration_common import file_ref;from pathlib import Path;"
            "startup=file_ref(Path(binding['config_root'])/'startup-v2.json');"
            "print(json.dumps(capture(binding,startup,'claude_a')))")
        self.assertEqual((result.returncode,result.stderr),(0,''))
        ref=json.loads(result.stdout);startup=self.f.source.runtime['startup']
        saved=json_ref(ref)
        self.assertEqual(json_ref(saved['request'])['kind'],'startup')
        value=verify_observation(ref,self.f.binding,startup,'claude_a')
        self.assertEqual(value['requester_descriptors']['before']['fileports'],[])
        private=self.f.root/'private'
        buffers,_=render_private_support(Path(__file__).parent)
        for name,(raw,_) in buffers.items():
            path=private/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        self.service.stop()
        code='''import sys,json
sys.path.insert(0,sys.argv[1])
from mac_upgrade_task_observer_evidence import verify
ref,binding,startup=json.loads(sys.argv[2]);value=verify(ref,binding,startup,'claude_a')
assert not value['requester_descriptors']['whole_client_isolation_proven']
assert 'upgrade_task_native_fds' not in sys.modules
assert not any(n.startswith('upgrade_') for n in sys.modules)
print('ORIGINAL_STARTUP_DESCRIPTORS_VERIFIED')
'''
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(private/'scripts'),
            json.dumps([ref,self.f.binding,startup])],cwd=private,capture_output=True,text=True,timeout=20)
        self.assertEqual((result.returncode,result.stderr),(0,''))
        self.assertEqual(result.stdout.strip(),'ORIGINAL_STARTUP_DESCRIPTORS_VERIFIED')
        del saved['response']['observation']['requester_descriptors']
        write_json(Path(ref['path']),saved)
        with self.assertRaises((KeyError,ContractError)):
            verify_observation(file_ref(ref['path']),self.f.binding,startup,'claude_a')

    def test_metadata_request_with_other_fds_does_not_count_as_startup_or_break_callback_reader(self):
        from upgrade_task_observer_evidence import verify as verify_observation
        result=self.f.child("import os;fd=os.open('/dev/null',os.O_RDONLY);os.dup2(fd,4096);os.close(fd);"
            "import cmux_daemon_identity as i;from upgrade_task_observer_client import read;"
            "print(json.dumps(read(binding,'tree',process=i.process)))")
        self.assertEqual((result.returncode,result.stderr),(0,''))
        value=json.loads(result.stdout)
        self.assertNotIn('requester_descriptors',value['observation'])
        with self.assertRaisesRegex(ContractError,'ORIGINAL_RESPONSE_CHANGED'):
            verify_observation(value['evidence'],self.f.binding,self.f.source.runtime['startup'],'claude_a')


class DescriptorContractTests(unittest.TestCase):
    def test_opt_in_cannot_claim_observation_without_mach_contract(self):
        self.assertFalse(enabled({}))
        with self.assertRaises(ContractError):enabled({'task_native_descriptors':contract()})
        from upgrade_live_materialize import materialize_live
        with self.assertRaisesRegex(ContractError,'TASK_DESCRIPTORS_REQUIRE_MACH_BOUNDARY'):
            materialize_live({},'B',binaries={},task_native_descriptors=True)


class DescriptorMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_upgrade_task_topology import TaskTopologyTests,SOURCE,WRAPPERS,PACKAGE
        if not all(p.is_dir() for p in (SOURCE,WRAPPERS,PACKAGE)):
            raise unittest.SkipTest('local frozen packages required')
        cls.f=type('DescriptorFixture',(TaskTopologyTests,),dict(model_network=True,callback_network=True,
            observer_network=True,mach_boundary=True,native_descriptors=True))
        cls.f.setUpClass()

    @classmethod
    def tearDownClass(cls):cls.f.tearDownClass()

    def test_materialized_contract_and_generated_startup_validation(self):
        f=self.f
        self.assertTrue(enabled(f.binding))
        root=Path(f.runtime['controller']['path'])/'scripts'
        self.assertFalse((root/'mac_upgrade_task_native_fds.py').exists())
        code='''import sys
sys.path.insert(0,sys.argv[1])
from mac_upgrade_startup_contract import validate_startup
from mac_upgrade_common import file_ref
from mac_upgrade_task_observer_protocol import allowed_kinds
_,binding=validate_startup(file_ref(sys.argv[2]))
assert allowed_kinds(binding)==('tree','task','startup')
assert 'upgrade_task_native_fds' not in sys.modules
'''
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(root),f.runtime['startup']['path']],
            capture_output=True,text=True,timeout=25,close_fds=True)
        self.assertEqual((result.returncode,result.stderr),(0,''))


if __name__=='__main__':unittest.main()
