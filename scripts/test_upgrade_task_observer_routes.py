"""Generated original .84 readers consume explicit proxy metadata fixtures.

Real source transformations/imports; identity/runtime/RPC values are fixtures.
"""
import ast
import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, write_json
from test_upgrade_task_target import SOURCE, SID, TURN
from test_upgrade_task_topology import TaskTopologyTests
from upgrade_task_network import OBSERVER_SCHEMA
from upgrade_task_observer_patch import task_observer_paths
from upgrade_task_observer_routes import ObserverMetadata, annotate_metadata, require_input_route
import upgrade_task_observer_routes as routes


class ObserverMetadataTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name).resolve()
        self.anchor=dict(thread_id=SID,journal=dict(path=str(root/'journal')),
                         socket={'path':str(root/'rpc')})
        proof=dict(thread_id=SID,socket=self.anchor['socket'],input_operations=0,
            can_accept_direct_input=True,thread_status='idle',latest_turn_id=TURN,
            latest_turn_status='completed')
        self.response=dict(evidence=write_json(root/'receipt.json',{'fixture':True}),
            observation=dict(task=dict(anchor=write_json(root/'anchor.json',self.anchor),proof=proof),
            identity_source='original_controller_read_only_observation',requester_direct_rpc_peer=False))

    def test_fixed_metadata_projection_cannot_dispatch_or_select_other_thread(self):
        client=ObserverMetadata(self.response)
        with self.assertRaisesRegex(ContractError,'FIXED_METADATA_ONLY'):
            client.request('turn/start',dict(threadId=SID))
        with self.assertRaisesRegex(ContractError,'FIXED_METADATA_ONLY'):
            client.request('thread/read',dict(threadId='foreign',includeTurns=False))
        self.assertEqual(client.request('thread/read',dict(threadId=SID,includeTurns=False))['thread']['id'],SID)
        result=client.request('thread/turns/list',dict(threadId=SID,limit=1,sortDirection='desc',itemsView='notLoaded'))
        self.assertEqual(result['data'],[dict(id=TURN,status='completed')])
        with self.assertRaisesRegex(ContractError,'FIXED_METADATA_ONLY'):
            client.request('thread/read',dict(threadId=SID,includeTurns=False))
        self.assertFalse(hasattr(client,'sock'))

    def test_only_callback_methods_can_leave_readonly_projection(self):
        client=ObserverMetadata(self.response)
        with self.assertRaisesRegex(ContractError,'CALLBACK_ROUTE_UNAVAILABLE'):
            require_input_route(client,dict(method='thread/resume'))
        require_input_route(client,dict(method='turn/steer'))
        require_input_route(client,dict(method='turn/start'))
        result=annotate_metadata(client,{'metadata_rpc_status':'AVAILABLE'})
        self.assertFalse(result['requester_direct_rpc_peer'])
        self.assertFalse(result['provider_error_details_observed'])

    def test_metadata_expiry_is_not_refreshed_by_reads(self):
        client=ObserverMetadata(self.response)
        client.received_at-=31
        with self.assertRaisesRegex(ContractError,'METADATA_EXPIRED'):
            client.request('thread/read',dict(threadId=SID,includeTurns=False))

    def test_saved_response_cannot_bind_another_anchor_or_receipt(self):
        client=ObserverMetadata(self.response)
        binding=dict(session_id=SID,transcript=self.anchor['journal'])
        with patch.object(routes,'client_binding',return_value={'fixture':True}):
            client.require_binding(binding,self.anchor)
            with self.assertRaisesRegex(ContractError,'BINDING_CHANGED'):
                client.require_binding(dict(binding,session_id=TURN),self.anchor)
            Path(self.response['evidence']['path']).write_text('{}')
            with self.assertRaisesRegex(ContractError,'EVIDENCE_CHANGED'):
                client.require_binding(binding,self.anchor)

    @unittest.skipUnless(SOURCE.is_dir(),'original .84 required')
    def test_original_metadata_context_uses_projection_without_raw_rpc(self):
        raw=(SOURCE/'scripts/cmux_codex_input.py').read_text()
        rendered=task_observer_paths('cmux_codex_input.py',raw)
        node=next(n for n in ast.parse(rendered).body if isinstance(n,ast.FunctionDef) and n.name=='_metadata_client')
        import contextlib
        namespace=dict(contextlib=contextlib)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'original84-observer-context','exec'),namespace)
        client=ObserverMetadata(self.response)
        with patch.object(routes,'metadata_client',return_value=client):
            with namespace['_metadata_client']() as actual:self.assertIs(actual,client)
        # There is deliberately no DaemonRPC name in the execution namespace.

    @unittest.skipUnless(SOURCE.is_dir(),'original .84 required')
    def test_generated_bridge_and_guard_call_fixed_observer_routes(self):
        values={'tree':json.dumps({'windows':[]}), 'caller':({'caller':{}},{'windows':[]},{},None)}
        for name,function,route,args,answer in (
            ('cmux_bridge.py','_run','tree_command',('tree','--all','--json','--id-format','both'),values['tree']),
            ('cmux_workspace_guard.py','caller_snapshot','caller_snapshot',(),values['caller'])):
            with self.subTest(name=name):
                rendered=task_observer_paths(name,(SOURCE/'scripts'/name).read_text())
                node=next(n for n in ast.parse(rendered).body if isinstance(n,ast.FunctionDef) and n.name==function)
                namespace={'_UNCOLLECTED':object()}
                exec(compile(ast.Module(body=[node],type_ignores=[]),'original84-fixed-observer','exec'),namespace)
                with patch.object(routes,route,return_value=answer) as observed:
                    self.assertEqual(namespace[function](*args),answer)
                    observed.assert_called_once()


class ObserverTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture=type('ObserverTopologyFixture',(TaskTopologyTests,),dict(model_network=True,
                     callback_network=True,observer_network=True))
        cls.fixture=fixture
        if not SOURCE.is_dir():raise unittest.SkipTest('original packages required')
        fixture.setUpClass()
    @classmethod
    def tearDownClass(cls):cls.fixture.tearDownClass()

    def test_generated_network_and_client_closure_validate_without_service_capability(self):
        f=self.fixture;self.assertEqual(f.binding['task_model_network']['schema'],OBSERVER_SCHEMA)
        root=Path(f.runtime['controller']['path'])/'scripts'
        for name in ('protocol','client','routes'):
            self.assertTrue((root/f'mac_upgrade_task_observer_{name}.py').exists())
        self.assertFalse((root/'mac_upgrade_task_observer.py').exists())
        import subprocess,sys
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',
            'import sys;sys.path.insert(0,sys.argv[1]);import mac_upgrade_task_observer_routes;'
            'assert not any(n in sys.modules for n in ("upgrade_task_observer","upgrade_task_runtime",'
            '"upgrade_task_enrollment","mac_upgrade_task_observer"))',str(root)],
            capture_output=True,text=True,timeout=15)
        self.assertEqual((result.returncode,result.stderr),(0,''))


if __name__=='__main__':unittest.main()
