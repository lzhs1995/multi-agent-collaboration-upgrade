"""Actual Darwin default-policy queries; no deployment or universal-isolation claim."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from orchestration_common import ContractError
import upgrade_task_runtime_policy as policy
from cmux_daemon_identity import process as kernel_process


@unittest.skipUnless(sys.platform == 'darwin', 'Darwin kernel required')
class RuntimePolicyTests(unittest.TestCase):
    def test_actual_external_query_distinguishes_unsandboxed_and_confined_process(self):
        self.assertEqual(policy.default_decisions(os.getpid()), {k:0 for k in policy.OPERATIONS})
        profile = '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote tcp "localhost:3445"))(deny mach-lookup)(deny appleevent-send)'
        code = 'import os,sys;print(os.getpid(),flush=True);sys.stdin.readline()'
        child = subprocess.Popen(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-I','-B','-c',code],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,close_fds=True)
        try:
            self.assertEqual(int(child.stdout.readline()),child.pid)
            row = kernel_process(child.pid)
            observed = policy.observe([row],process=kernel_process)
            self.assertEqual(policy.verify(observed,[row]),observed)
            self.assertFalse(observed['all_exceptions_proven'])
            self.assertFalse(observed['inherited_mach_rights_proven'])
            with self.assertRaisesRegex(ContractError,'DEFAULT_ALLOW'):
                policy.observe([kernel_process(os.getpid())],process=kernel_process)
            changed = copy.deepcopy(observed)
            changed['processes'][0]['default_decisions']['network-outbound'] = 0
            with self.assertRaises(ContractError):policy.verify(changed,[row])
            out,err=child.communicate('\n',timeout=10)
            self.assertEqual((child.returncode,out,err),(0,'',''))
        finally:
            if child.poll() is None:
                child.communicate('\n',timeout=10)

    def test_pid_change_during_query_refuses(self):
        row=kernel_process(os.getpid());changed=dict(row,birth=[1,1])
        with patch.object(policy,'default_decisions',return_value={k:1 for k in policy.OPERATIONS}):
            with self.assertRaisesRegex(ContractError,'CHANGED_DURING_READ'):
                policy.observe([row],process=unittest.mock.Mock(side_effect=[row,changed]))


class RuntimeMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_upgrade_task_topology import TaskTopologyTests,SOURCE,WRAPPERS,PACKAGE
        if not all(p.is_dir() for p in (SOURCE,WRAPPERS,PACKAGE)):
            raise unittest.SkipTest('original local packages required')
        cls.f=type('RuntimePolicyFixture',(TaskTopologyTests,),dict(model_network=True,
            callback_network=True,observer_network=True,mach_boundary=True,native_descriptors=True,runtime_policy=True))
        cls.f.setUpClass()

    @classmethod
    def tearDownClass(cls):cls.f.tearDownClass()

    def test_generated_validator_and_private_reader_require_same_explicit_contract(self):
        f=self.f
        self.assertTrue(policy.enabled(f.binding))
        code='''import sys
sys.path.insert(0,sys.argv[1])
from mac_upgrade_startup_contract import validate_startup
from mac_upgrade_common import file_ref
from mac_upgrade_task_runtime_policy import enabled
import mac_upgrade_task_adoption
_,b=validate_startup(file_ref(sys.argv[2]));assert enabled(b)
assert not any(n.startswith('upgrade_') for n in sys.modules)
assert 'mac_upgrade_live_control' not in sys.modules
'''
        root=Path(f.runtime['controller']['path'])/'scripts'
        r=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(root),f.runtime['startup']['path']],
            cwd=root,capture_output=True,text=True,timeout=30)
        self.assertEqual((r.returncode,r.stderr),(0,''))

    def test_materialization_cannot_opt_in_without_original_descriptor_contract(self):
        from upgrade_live_materialize import materialize_live
        with self.assertRaisesRegex(ContractError,'REQUIRES_DESCRIPTORS'):
            materialize_live({},'B',binaries={},task_runtime_policy=True)


if __name__=='__main__':unittest.main()
