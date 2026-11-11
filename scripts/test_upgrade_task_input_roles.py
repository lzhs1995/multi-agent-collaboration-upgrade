"""Final sender gate invokes current TASK adoption and all four live roles.

Deployment and kernel are explicit doubles; these are zero-input checks.
"""
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, write_json
import test_upgrade_live_startup as fixtures
import upgrade_input_gate as gate


class TaskInputRoleTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InputGateTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        f.binding['task_startup']={'path':'/fixture/task','bytes':1,'sha256':'a'*64}
        controller=f.root/'controllers/releases/private/source';controller.mkdir(parents=True)
        f.binding['controller_root']=str(controller)
        self.runtime=write_json(controller.parents[2]/'runtime-v2.json',{'fixture':True})
        self.adoption_ref=f.adopt();self.adoption=json_ref(self.adoption_ref)
        self.adoption['schema']='upgrade-live-adoption-task-v1'
        self.adoption_ref=write_json(self.adoption_ref['path'],self.adoption)
        adopt=patch('upgrade_task_adoption.read',return_value=self.adoption)
        roles=patch('upgrade_task_roles.verify_roles',return_value={'fixture':True})
        self.adopt=adopt.start();self.addCleanup(adopt.stop)
        self.roles=roles.start();self.addCleanup(roles.stop)

    def test_fresh_role_check_includes_original_task_sessions_before_endpoint_gate(self):
        self.assertEqual(gate.check_input(self.f.proof),self.adoption)
        self.adopt.assert_called_once_with(self.f.binding)
        args,kwargs=self.roles.call_args
        self.assertEqual(args[0],self.f.startup);self.assertEqual(args[2],self.f.binding)
        self.assertEqual(args[3],self.f.sessions);self.assertEqual(kwargs['process'],self.f.identity.process)
        self.assertEqual(self.f.kernel.call_count,2)

    def test_adoption_or_server_failure_refuses_before_endpoint_input_checks(self):
        self.adopt.side_effect=ContractError('not adopted')
        with self.assertRaisesRegex(ContractError,'not adopted'):gate.check_input(self.f.proof)
        self.roles.assert_not_called();self.f.kernel.assert_not_called()
        self.adopt.side_effect=None;self.roles.side_effect=ContractError('server changed')
        with self.assertRaisesRegex(ContractError,'server changed'):gate.check_input(self.f.proof)
        self.f.kernel.assert_not_called()

    def test_stop_arriving_in_role_check_is_still_caught(self):
        self.roles.side_effect=lambda *a,**k:(self.f.control/'stop.json').touch()
        with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):gate.check_input(self.f.proof)

    def test_legacy_adoption_cannot_bypass_task_reader_or_live_roles(self):
        write_json(self.adoption_ref['path'],dict(self.adoption,schema='upgrade-live-adoption-v2'))
        with self.assertRaisesRegex(ContractError,'AUTOMATIC_ADOPTION_REQUIRED'):
            gate.check_input(self.f.proof)
        self.adopt.assert_not_called();self.roles.assert_not_called()


if __name__=='__main__':unittest.main()
