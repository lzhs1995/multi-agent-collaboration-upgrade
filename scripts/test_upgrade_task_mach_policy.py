"""Actual Darwin policy behavior; no model calls or production policy writes."""
import json
from contextlib import ExitStack
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid

from orchestration_common import ContractError
from upgrade_task_mach_policy import contract, enabled, restrict, PREFERENCE_SERVICES

PROBE = '''import ctypes,json,sys
lib=ctypes.CDLL(None)
lookup=lib.bootstrap_look_up
lookup.argtypes=(ctypes.c_uint,ctypes.c_char_p,ctypes.POINTER(ctypes.c_uint));lookup.restype=ctypes.c_int
bootstrap=ctypes.c_uint.in_dll(lib,'bootstrap_port').value
port=ctypes.c_uint(0)
rc=lookup(bootstrap,(sys.argv[1] if len(sys.argv)>1 else 'com.apple.cfprefsd.daemon').encode(),ctypes.byref(port))
if rc==0 and port.value:
 lib.mach_port_deallocate(ctypes.c_uint.in_dll(lib,'mach_task_self_').value,port.value)
sb=ctypes.CDLL('/usr/lib/libsandbox.dylib')
check=sb.sandbox_check;check.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int);check.restype=ctypes.c_int
no_report=ctypes.c_int.in_dll(sb,'SANDBOX_CHECK_NO_REPORT').value
import os
event=check(os.getpid(),b'appleevent-send',no_report)
print(json.dumps(dict(lookup_returncode=rc,port_present=bool(port.value),appleevent_denied=event)))
'''


class MachPolicyTests(unittest.TestCase):
    def test_legacy_contract_and_profiles_keep_exact_denial(self):
        profile=b'(version 1)\n(allow default)\n'
        legacy=contract(version=1)
        self.assertTrue(enabled(dict(task_startup={},task_mach_boundary=legacy,
            task_model_network={'schema':'upgrade-task-observer-network-v3'})))
        for lane in ('codex-server','claude_a','claude_b'):
            self.assertEqual(restrict(profile,boundary=legacy,lane=lane),restrict(profile))
        current=contract()
        current['codex_managed_preferences']['services'].append('com.apple.securityd')
        with self.assertRaisesRegex(ContractError,'TASK_MACH_PROFILE_CONTRACT_CHANGED'):
            restrict(profile,boundary=current,lane='codex-server')

    def test_explicit_contract_requires_original_observer_topology(self):
        self.assertFalse(enabled({}))
        value=dict(task_startup={},task_model_network={'schema':'upgrade-task-observer-network-v3'},
                   task_mach_boundary=contract())
        self.assertTrue(enabled(value))
        for key in ('task_startup','task_model_network'):
            bad=dict(value);del bad[key]
            with self.assertRaises(ContractError): enabled(bad)
        value['task_mach_boundary']['denied_operations']=[]
        with self.assertRaises(ContractError):enabled(value)
        from upgrade_live_materialize import materialize_live
        with self.assertRaisesRegex(ContractError, 'TASK_MACH_REQUIRES_OBSERVER_NETWORK'):
            materialize_live({}, 'B', binaries={}, task_mach_boundary=True)

    @unittest.skipUnless(sys.platform=='darwin','Darwin sandbox required')
    def test_actual_lookup_and_kernel_event_check_apply_to_descendants(self):
        baseline=subprocess.run([sys.executable,'-I','-B','-c',PROBE],capture_output=True,text=True,timeout=10)
        self.assertEqual((baseline.returncode,baseline.stderr),(0,''))
        self.assertEqual(json.loads(baseline.stdout),dict(lookup_returncode=0,port_present=True,appleevent_denied=0))
        with tempfile.TemporaryDirectory() as tmp:
            policy=Path(tmp)/'policy.sb';policy.write_bytes(restrict(b'(version 1)\n(allow default)\n'))
            wrapper='import subprocess,sys;p=subprocess.run([sys.executable,"-I","-B","-c",sys.argv[1]],capture_output=True,text=True,timeout=10,close_fds=True);print(p.stdout,end="");print(p.stderr,end="",file=sys.stderr);sys.exit(p.returncode)'
            for descendant in (False,True):
                argv=['-c',wrapper,PROBE] if descendant else ['-c',PROBE]
                result=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy),sys.executable,'-I','-B',*argv],
                    capture_output=True,text=True,timeout=15,close_fds=True)
                self.assertEqual((result.returncode,result.stderr),(0,''))
                value=json.loads(result.stdout)
                self.assertNotEqual(value['lookup_returncode'],0)
                self.assertFalse(value['port_present'])
                self.assertEqual(value['appleevent_denied'],1)

    @unittest.skipUnless(sys.platform=='darwin','Darwin sandbox required')
    def test_combined_policy_preserves_model_observer_and_exact_callback_connections(self):
        from upgrade_task_network import network_contract,model_only_profile
        with tempfile.TemporaryDirectory(prefix='mb',dir='/tmp') as tmp, ExitStack() as stack:
            root=Path(tmp).resolve()
            model=stack.enter_context(socket.socket());model.bind(('127.0.0.1',0));model.listen(8)
            other=stack.enter_context(socket.socket());other.bind(('127.0.0.1',0));other.listen(8)
            ports=[s.getsockname()[1] for s in (model,other)]
            net=network_contract({'codex':{'base_url':f'http://127.0.0.1:{ports[0]}'}},
                {'env':{'ANTHROPIC_BASE_URL':f'http://127.0.0.1:{ports[1]}'}},evidence_root=root,observer=True)
            paths=[Path(net['observer']),Path(net['callback']),root/'raw-broker']
            for path in paths:
                path.parent.mkdir(parents=True,exist_ok=True)
                listener=stack.enter_context(socket.socket(socket.AF_UNIX));listener.bind(str(path));listener.listen(8)
            probe='''import json,socket,sys
rows=[]
for family,target in [(socket.AF_INET,('127.0.0.1',int(sys.argv[1]))),*[(socket.AF_UNIX,x) for x in sys.argv[2:]]]:
 try:
  with socket.socket(family) as s:s.settimeout(2);s.connect(target)
  rows.append(True)
 except OSError as e:rows.append(e.errno)
print(json.dumps(rows))
'''
            wrapper='import subprocess,sys;p=subprocess.run([sys.executable,"-I","-B","-c",sys.argv[1],*sys.argv[2:]],capture_output=True,text=True,timeout=10,close_fds=True);print(p.stdout,end="");print(p.stderr,end="",file=sys.stderr);sys.exit(p.returncode)'
            for lane in ('codex','claude_a','claude_b'):
                policy=root/(lane+'.sb')
                policy.write_bytes(restrict(model_only_profile(b'(version 1)\n(allow default)\n',net,lane)))
                for descendant in (False,True):
                    argv=['-c',wrapper,probe] if descendant else ['-c',probe]
                    result=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy),sys.executable,'-I','-B',
                        *argv,str(net['ports'][lane]),*map(str,paths)],capture_output=True,text=True,timeout=15,close_fds=True)
                    self.assertEqual((result.returncode,result.stderr),(0,''))
                    self.assertEqual(json.loads(result.stdout),[True,True,1 if lane=='codex' else True,1])


class MaterializedMachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_upgrade_task_topology import TaskTopologyTests,SOURCE,WRAPPERS,PACKAGE
        if not all(p.is_dir() for p in (SOURCE,WRAPPERS,PACKAGE)):
            raise unittest.SkipTest('local frozen packages required')
        cls.f=type('MachFixture',(TaskTopologyTests,),dict(model_network=True,callback_network=True,
                   observer_network=True,mach_boundary=True))
        cls.f.setUpClass()

    @classmethod
    def tearDownClass(cls):cls.f.tearDownClass()

    def test_generated_validation_and_exact_execution_profiles(self):
        f=self.f
        self.assertTrue(enabled(f.binding))
        for lane in ('claude_a','claude_b'):
            self.assertTrue(Path(f.spec['pins'][lane+'_sandbox']['path']).read_bytes().endswith(
                b'(deny mach-lookup)\n(deny appleevent-send)\n'))
        server=Path(f.spec['pins']['codex_server_sandbox']['path']).read_bytes()
        self.assertIn(b'(deny user-preference-write)\n',server)
        self.assertIn(b'(deny user-preference-read)\n',server)
        self.assertIn(b'(allow user-preference-read (preference-domain "com.openai.codex"))\n',server)
        for lane in ('codex','control'):
            self.assertNotIn(b'deny mach-lookup',Path(f.spec['pins'][lane+'_sandbox']['path']).read_bytes())
        root=Path(f.runtime['controller']['path'])/'scripts'
        code='import sys;sys.path.insert(0,sys.argv[1]);from mac_upgrade_startup_contract import validate_startup;from mac_upgrade_common import file_ref;validate_startup(file_ref(sys.argv[2]));assert "upgrade_task_mach_policy" not in sys.modules'
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(root),f.runtime['startup']['path']],
            capture_output=True,text=True,timeout=20,close_fds=True)
        self.assertEqual((result.returncode,result.stderr),(0,''))

    def test_official_native_startup_loads_real_generated_configuration(self):
        from upgrade_client_isolation import clean_child_environment
        f=self.f
        env=clean_child_environment(f.spec['clients']['codex']['environment'])
        argv=['/usr/bin/sandbox-exec','-f',f.spec['pins']['codex_server_sandbox']['path'],
              f.spec['pins']['codex']['path'],'app-server','-c',
              'model_providers.custom.base_url="http://127.0.0.1:3465/v1"']
        preflight=subprocess.run([*argv,'--print-startup-config'],input='',env=env,
            cwd=f.spec['work_root'],capture_output=True,text=True,timeout=20)
        self.assertEqual(preflight.returncode,0,preflight.stderr+'\n'+preflight.stdout)
        value=json.loads(preflight.stdout)
        self.assertEqual(value['appServerVersion'],'0.164.0')
        self.assertIsNone(value['configRequirements']['requirements'])
        # EOF closes a real native server without creating any thread/model turn.
        server=subprocess.run(argv,input='',env=env,cwd=f.spec['work_root'],
            capture_output=True,text=True,timeout=20)
        self.assertEqual(server.returncode,0,server.stderr)
        self.assertNotIn('using defaults',server.stderr)

    def test_native_server_services_and_preference_writes_remain_restricted(self):
        f=self.f
        prefix=['/usr/bin/sandbox-exec','-f',f.spec['pins']['codex_server_sandbox']['path']]
        for service in [*PREFERENCE_SERVICES,'com.apple.securityd']:
            result=subprocess.run([*prefix,sys.executable,'-I','-B','-c',PROBE,service],
                capture_output=True,text=True,timeout=10)
            self.assertEqual((result.returncode,result.stderr),(0,''))
            observed=json.loads(result.stdout)
            self.assertEqual(observed['lookup_returncode']==0,service in PREFERENCE_SERVICES)
            self.assertEqual(observed['appleevent_denied'],1)
        # A new disposable domain must not be writable via cfprefsd outside the
        # task's file boundary. No existing user preference is touched.
        domain='com.codex-upgrade.pref-denial-'+uuid.uuid4().hex
        baseline=subprocess.run(['/usr/bin/defaults','read',domain],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(baseline.returncode,0)
        changed=subprocess.run([*prefix,'/usr/bin/defaults','write',domain,'probe','-string','denied'],
            capture_output=True,text=True,timeout=10)
        after=subprocess.run(['/usr/bin/defaults','read',domain],capture_output=True,text=True,timeout=10)
        try:
            self.assertNotEqual(changed.returncode,0,changed.stdout+changed.stderr)
            self.assertNotEqual(after.returncode,0,after.stdout)
        finally:
            if after.returncode==0:
                subprocess.run(['/usr/bin/defaults','delete',domain],capture_output=True,timeout=10)

    def test_missing_policy_rule_is_rejected_even_with_updated_file_pin(self):
        from orchestration_common import file_ref
        from upgrade_live_integrity import artifact_ref
        from upgrade_startup_contract import validate_startup
        f=self.f;path=Path(f.spec['pins']['claude_a_sandbox']['path']);raw=path.read_bytes()
        # Fixed source bytes matter even if a caller re-pins a broader profile.
        startup_path=Path(f.runtime['startup']['path'])
        original_startup=startup_path.read_bytes();startup_mode=startup_path.stat().st_mode&0o777
        try:
            path.chmod(0o600);path.write_bytes(raw.replace(b'(deny mach-lookup)\n',b''));path.chmod(0o444)
            spec=json.loads(original_startup);spec['pins']['claude_a_sandbox']=artifact_ref(path)
            startup_path.chmod(0o600);startup_path.write_text(json.dumps(spec))
            with self.assertRaisesRegex(ContractError,'STARTUP_SOCKET_PROFILE_CHANGED:claude_a'):
                validate_startup(file_ref(startup_path))
        finally:
            path.chmod(0o600);path.write_bytes(raw);path.chmod(0o444)
            startup_path.write_bytes(original_startup);startup_path.chmod(startup_mode)


if __name__=='__main__':unittest.main()
