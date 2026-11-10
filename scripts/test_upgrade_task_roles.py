"""Role/STOP substitutions with explicit kernel and startup fixture doubles."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from orchestration_common import ContractError, write_json
from upgrade_rpc_peer import server_pin
import upgrade_task_roles as roles


class RoleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve();state=self.root/'state/startup';state.mkdir(parents=True)
        self.startup={'path':str(self.root/'startup.json'),'sha256':'a'*64,'bytes':1}
        self.task={'path':str(self.root/'task.json'),'sha256':'b'*64,'bytes':1}
        self.sid=str(uuid.uuid4());ws=str(uuid.uuid4())
        self.binding=dict(task_startup=self.task,controller_root=str(self.root),state_root=str(state.parent),
                          isolation=dict(denied_probes=['/frozen'],write_probes=['/private']))
        self.spec=dict(schema='upgrade-startup-task-v1',task_startup=self.task,controller=str(self.root),
                       stop_path=str(self.root/'STOP'),clients={},pins=dict(codex=dict(path='/official/codex')),
                       rpc=dict(path='/fixture/c.sock',argv=['/official/codex','app-server']))
        self.rows={};self.sessions={}
        for i,lane in enumerate(('codex','claude_a','claude_b','codex-server')):
            pid=100+i;surface=str(uuid.uuid4());root=str(self.root/('codex' if lane=='codex-server' else lane))
            row=dict(pid=pid,birth=[100,i],executable='/official/codex' if lane.startswith('codex') else '/official/claude',
                     file_identity=[1,2,3,4],argv=['/official/claude'],root=root,
                     env=dict(CMUX_SURFACE_ID=surface,CMUX_WORKSPACE_ID=ws))
            if lane=='codex':row['argv']=['/official/codex','--remote','unix:///fixture/c.sock','resume',self.sid]
            if lane=='codex-server':row['argv']=self.spec['rpc']['argv']
            self.rows[pid]=row
            write_json(state/(lane+'.json'),dict(startup=self.startup,pid=pid,birth=row['birth'],surface_uuid=surface,workspace_uuid=ws))
            if lane!='codex-server':
                sid=self.sid if lane=='codex' else str(uuid.uuid4())
                self.spec['clients'][lane]=dict(native_root=root,session_id=sid)
                self.sessions[lane]=dict(provider='codex' if lane=='codex' else 'claude',session_id=sid,
                    surface_uuid=surface,workspace_uuid=ws,native_config_root=root,
                    process={k:row[k] for k in ('pid','birth','executable','file_identity')},
                    transcript=dict(path='/fixture/journal',device=1,inode=2))
        self.anchor=dict(schema='upgrade-task-thread-read-anchor-v1',server=server_pin(self.rows[103],uid=os.geteuid()),
                         thread_id=self.sid,native_root=self.rows[100]['root'],socket=dict(path='/fixture/c.sock'),
                         journal=self.sessions['codex']['transcript'])
        self.result=dict(thread_id=self.sid,anchor={'path':'/fixture/anchor','bytes':1,'sha256':'c'*64})
        self.initial=({'path':'/fixture/result','bytes':1,'sha256':'d'*64},self.result,self.anchor)
        for name,kwargs in (
            ('task_startup_result',dict(return_value=self.initial)),
            ('process_root',dict(side_effect=lambda provider,row:row['root'])),
            ('binding_boundary',dict(return_value={'fixture':True})),
            ('require_kernel_boundary',dict(side_effect=lambda pid,**k:dict(pid=pid,kernel_checked=True))),
            ('verify_kernel',dict(side_effect=lambda pid,b,**k:dict(pid=pid,**k)))):
            p=patch.object(roles,name,**kwargs);p.start();self.addCleanup(p.stop)

    def process(self,pid):return copy.deepcopy(self.rows[pid])
    def check(self):return roles.verify_roles(self.startup,self.spec,self.binding,self.sessions,process=self.process)

    def test_server_and_display_have_separate_live_kernel_proofs(self):
        value=self.check()
        self.assertEqual(value['display'],dict(pid=100,execution_owner=False))
        self.assertEqual(value['execution_server']['pid'],103)
        self.assertEqual(set(value['kernels']),{'codex','codex-server','claude_a','claude_b'})
        self.assertTrue(value['kernels']['codex-server']['socket_boundary']['physical_unlink_denied'])
        self.assertFalse(value['kernels']['codex']['socket_boundary']['physical_unlink_denied'])
        self.assertFalse(value['broker_isolation_proven']);self.assertEqual(value['input_operations'],0)

    def test_server_replacement_or_changed_argv_cannot_borrow_display_identity(self):
        for key,value in (('birth',[101,0]),('argv',['/official/codex','app-server','--other'])):
            with self.subTest(key=key):
                old=self.rows[103][key];self.rows[103][key]=value
                try:
                    with self.assertRaises(ContractError):self.check()
                finally:self.rows[103][key]=old

    def test_wrong_tui_server_or_journal_or_executor_is_rejected(self):
        self.rows[100]['argv'][2]='unix:///fixture/other.sock'
        with self.assertRaisesRegex(ContractError,'REMOTE_TARGET_CHANGED'):self.check()
        self.rows[100]['argv'][2]='unix:///fixture/c.sock'
        self.sessions['codex']['transcript']=dict(self.anchor['journal'],inode=99)
        with self.assertRaisesRegex(ContractError,'JOURNAL_CHANGED'):self.check()
        self.sessions['codex']['transcript']=self.anchor['journal']
        self.sessions['claude_a']['session_id']=str(uuid.uuid4())
        with self.assertRaisesRegex(ContractError,'EXECUTOR_SESSION_CHANGED'):self.check()

    def test_stop_or_process_change_during_kernel_checks_refuses(self):
        def stop(pid,**kw):
            Path(self.spec['stop_path']).touch();return dict(pid=pid)
        with patch.object(roles,'require_kernel_boundary',side_effect=stop):
            with self.assertRaisesRegex(ContractError,'STOPPED'):self.check()
        Path(self.spec['stop_path']).unlink()
        def changed(pid,b,**kw):
            if pid==103:self.rows[103]['birth']=[200,0]
            return dict(pid=pid)
        with patch.object(roles,'verify_kernel',side_effect=changed):
            with self.assertRaisesRegex(ContractError,'CHANGED_DURING_READ'):self.check()


if __name__=='__main__':unittest.main()
