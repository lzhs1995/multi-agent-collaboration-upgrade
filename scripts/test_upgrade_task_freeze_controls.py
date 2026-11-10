"""Original operation registration attachment; no model/client launch."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, object_sha, write_json
from upgrade_task_campaign import original_controls, SCHEMA, BINDING_SCHEMA, validate_controls
from test_upgrade_task_campaign import CampaignActorTests


class OriginalControlsTests(unittest.TestCase):
    def fixture(self, *, slots=False):
        f=CampaignActorTests();f.binding_mode=slots;f.setUp();self.addCleanup(f.doCleanups)
        reg=copy.deepcopy(f.registration)
        reg['operations']['ready']=dict(kind='readiness',payload={})
        reg['initial_operations']=['ready']
        reg_ref=write_json(f.control/'startup-operations.json',reg)
        spec=write_json(f.control/'startup.json',dict(schema='upgrade-startup-task-v1'))
        runtime=dict(startup=spec)
        runtime_ref=write_json(f.control/'runtime.json',runtime)
        campaign=copy.deepcopy(f.c.campaign);campaign.pop('task_controls')
        campaign['runtime']=runtime_ref
        result=dict(operations=reg_ref,registration_sha256=object_sha(reg))
        p=patch('upgrade_task_readiness.task_startup_result',return_value=(None,result,None))
        p.start();self.addCleanup(p.stop)
        return f,reg,result,campaign,runtime

    def test_original_ready_registration_is_attached_without_new_operations(self):
        f,reg,startup,campaign,runtime=self.fixture()
        before=Path(startup['operations']['path']).read_bytes()
        value=original_controls(campaign,runtime)
        self.assertEqual(value['schema'],SCHEMA)
        self.assertEqual(value['registration'],startup['operations'])
        self.assertNotIn('task_controls',campaign)
        validated,ops=validate_controls(dict(campaign,task_controls=value))
        self.assertEqual(validated,reg);self.assertEqual(set(ops),{'start','claim'})
        self.assertEqual(before,Path(startup['operations']['path']).read_bytes())
        self.assertEqual(list(Path(value['evidence_directory']).iterdir()), [])

    def test_original_generated_slots_remain_unbound_and_keep_same_sources(self):
        f,reg,startup,campaign,runtime=self.fixture(slots=True)
        value=original_controls(campaign,runtime)
        self.assertEqual(value['schema'],BINDING_SCHEMA)
        actual=json_ref(value['registration'])
        self.assertEqual(actual['binding_slots'],reg['binding_slots'])
        self.assertIsNone(actual['operations']['accept']['payload']['marker'])

    def test_changed_hash_thread_scope_or_extra_operation_refuses(self):
        f,reg,startup,campaign,runtime=self.fixture()
        original=copy.deepcopy(reg)
        for mutate in (
            lambda r:r.update(thread_id='11111111-0000-4000-8000-000000000000'),
            lambda r:r['operations']['claim']['payload'].update(campaign_id='foreign'),
            lambda r:r['operations'].update(shell=dict(kind='shell',payload={})),
            lambda r:r.update(initial_operations=['start'])):
            changed=copy.deepcopy(original);mutate(changed)
            startup['operations']=write_json(f.control/'changed-operations.json',changed)
            startup['registration_sha256']=object_sha(changed)
            with self.assertRaises(ContractError):original_controls(campaign,runtime)
        startup['operations']=write_json(f.control/'changed-operations.json',original)
        startup['registration_sha256']='0'*64
        with self.assertRaisesRegex(ContractError,'ORIGINAL_READINESS_REQUIRED'):
            original_controls(campaign,runtime)

    def test_legacy_has_no_controls_and_cannot_be_enrolled_by_extra_field(self):
        f,reg,startup,campaign,runtime=self.fixture()
        runtime['startup']=write_json(f.control/'legacy.json',dict(schema='upgrade-startup-v3'))
        with patch('upgrade_task_readiness.task_startup_result') as read:
            self.assertIsNone(original_controls(campaign,runtime))
            with self.assertRaisesRegex(ContractError,'LEGACY_CAMPAIGN'):
                original_controls(dict(campaign,task_controls={}),runtime)
            read.assert_not_called()


if __name__=='__main__':unittest.main()
