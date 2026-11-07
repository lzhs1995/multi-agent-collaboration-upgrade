"""Source observation fixtures: early hooks, original scope, STOP and drift.

Kernel/native/RPC inputs are explicit fixtures. Transport is tested separately
with real Unix peers; these tests are not live-client adoption evidence.
"""
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, write_json
import upgrade_task_observation as source
from upgrade_task_observer_protocol import PROCESS_FIELDS
from upgrade_task_network import OBSERVER_SCHEMA


class ObservationSourceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()
        self.startup=write_json(self.root/'startup.json',{'fixture':True})
        self.ws=str(uuid.uuid4());self.surfaces={lane:str(uuid.uuid4()) for lane in ('codex','claude_a','claude_b')}
        self.binding=dict(state_root=str(self.root/'state'),controller_root=str(self.root/'controller'))
        self.spec=dict(stop_path=str(self.root/'STOP'),clients={lane:dict(native_root=str(self.root/lane))
            for lane in self.surfaces},pins={'cmux':{'path':'/fixture/cmux'}})
        self.rows={};self.launches={};self.endpoints={};panes=[]
        for n,lane in enumerate(self.surfaces,1):
            row=dict(pid=100+n,ppid=90,birth=[1,n],executable='/fixture/'+lane,file_identity=[1,n],
                argv=['/fixture/'+lane],env=dict(CMUX_SURFACE_ID=self.surfaces[lane],CMUX_WORKSPACE_ID=self.ws))
            self.rows[row['pid']]=row
            launch=dict(startup=self.startup,pid=row['pid'],birth=row['birth'],
                        surface_uuid=self.surfaces[lane],workspace_uuid=self.ws)
            self.launches[lane]=launch
            write_json(self.root/'state/startup'/(lane+'.json'),launch)
            pane=str(uuid.uuid4());tty='tty-fixture'+str(n)
            self.endpoints[self.surfaces[lane]]=dict(target_pane_uuid=pane,tty=tty,pid=row['pid'])
            panes.append(dict(id=pane,ref='pane:'+str(n),surfaces=[dict(id=self.surfaces[lane],
                ref='surface:'+str(n),type='terminal',dock_scope='workspace',tty=tty)]))
        self.tree=dict(windows=[dict(id=str(uuid.uuid4()),ref='window:1',workspaces=[dict(
            id=self.ws,ref='workspace:1',panes=panes)])])
        self.source=object.__new__(source.ObservationSource)
        self.source.spec=self.spec;self.source.binding=self.binding;self.source.runtime=dict(startup=self.startup)
        self.source.runtime_ref=write_json(self.root/'runtime.json',{'fixture':True})
        self.source.process=lambda pid:copy.deepcopy(self.rows[pid])
        def verify():
            if Path(self.spec['stop_path']).exists():raise ContractError('EXPERIMENT_STOP_REQUESTED')
        self.source.verify=verify
        def forbidden(*a,**k):raise AssertionError('early tree query must not read other journal/task/foreground')
        def process_root(provider,row):
            lane=next(lane for lane,l in self.launches.items() if l['pid']==row['pid'])
            return self.root/lane
        self.source.native=SimpleNamespace(
            private_roots=SimpleNamespace(process_root=process_root),
            private_process=SimpleNamespace(terminal=lambda row:self.endpoints[row['env']['CMUX_SURFACE_ID']]['tty']),
            _tree_row=lambda bridge,surface:copy.deepcopy(self.endpoints[surface]),
            _target_process=lambda endpoint:copy.deepcopy(self.rows[endpoint['pid']]),
            _provider=lambda row:'claude' if 'claude' in row['executable'] else 'codex',
            _public_process=lambda row:{k:row[k] for k in ('pid','birth','executable','file_identity')},
            _live_session=forbidden,_pin_file=forbidden,_transcript_path=forbidden)
        lane='claude_a';row=self.rows[self.launches[lane]['pid']]
        self.who=dict(peer=dict(pid=999,uid=os.geteuid()),lane=lane,
                      ancestry=[{k:row[k] for k in PROCESS_FIELDS}])
        for p in (patch.object(source,'caller',return_value=self.who),
            patch('upgrade_live_inspect.ReadOnlyCmux',side_effect=lambda *a:SimpleNamespace(
                _run=lambda *args:__import__('json').dumps(self.tree)))):
            p.start();self.addCleanup(p.stop)

    def test_early_tree_does_not_wait_for_server_task_or_other_lane_journal(self):
        result=self.source.read(None,'tree')
        self.assertEqual(result['tree'],self.tree)
        self.assertEqual(set(result['sessions']),{'claude_a'})
        self.assertIsNone(result['task']);self.assertEqual(result['input_operations'],0)
        self.assertFalse(result['adoption_proven'])

    def test_task_query_requires_server_and_never_falls_back_to_tree(self):
        with self.assertRaisesRegex(ContractError,'ORIGINAL_SERVER_REQUIRED'):
            self.source.read(None,'task')

    def test_original_endpoint_kernel_scope_and_birth_are_not_environment_selection(self):
        row=self.rows[self.launches['claude_a']['pid']]
        row['env']['CMUX_WORKSPACE_ID']=str(uuid.uuid4())
        with self.assertRaisesRegex(ContractError,'ORIGINAL_PROCESS_CHANGED'):self.source.read(None,'tree')
        row['env']['CMUX_WORKSPACE_ID']=self.ws;row['birth']=[2,2]
        with self.assertRaisesRegex(ContractError,'ORIGINAL_PROCESS_CHANGED'):self.source.read(None,'tree')

    def test_stop_before_read_and_after_observation_rejects_result(self):
        Path(self.spec['stop_path']).touch()
        with self.assertRaisesRegex(ContractError,'EXPERIMENT_STOP_REQUESTED'):self.source.read(None,'tree')
        Path(self.spec['stop_path']).unlink()
        def calls(*a):
            Path(self.spec['stop_path']).touch();return self.who
        with patch.object(source,'caller',side_effect=calls):
            with self.assertRaisesRegex(ContractError,'EXPERIMENT_STOP_REQUESTED'):self.source.read(None,'tree')

    def test_tree_removes_text_and_foreign_surface_and_refuses_original_movement(self):
        p=self.tree['windows'][0]['workspaces'][0]['panes'][0]
        p['surfaces'][0]['text']='private terminal text'
        p['surfaces'].append(dict(id=str(uuid.uuid4()),type='terminal',text='foreign'))
        result=source.workspace_tree(self.tree,self.launches)
        self.assertNotIn('text',str(result));self.assertEqual(len(result['windows'][0]['workspaces'][0]['panes'][0]['surfaces']),1)
        p['surfaces'][0]['dock_scope']='global'
        with self.assertRaisesRegex(ContractError,'ORIGINAL_ENDPOINT_CHANGED'):
            source.workspace_tree(self.tree,self.launches)


if __name__=='__main__':unittest.main()
