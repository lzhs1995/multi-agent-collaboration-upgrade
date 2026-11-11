"""External startup contracts; fake model processes, real immutable records.

The Darwin test also uses a real sandboxed Python socket child, without any
model or provider request. That test does not establish native CLI adoption.
"""
import copy
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
import upgrade_task_server as server
from upgrade_task_tui import wait_original_server


class ExternalServerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='srv', dir='/tmp'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root/'state'; self.startup = self.state/'startup'; self.startup.mkdir(parents=True)
        self.control = self.root/'control'; self.control.mkdir()
        self.profile = self.root/'server.sb'; self.profile.write_text('(version 1)\n(allow default)\n')
        self.ref = write_json(self.root/'runtime.json', {'fixture': 'runtime'})
        self.spec = dict(schema='upgrade-startup-task-v1', work_root=str(self.root),
            stop_path=str(self.control/'stop.json'), adoption_path=str(self.control/'adoption.json'),
            controller=str(self.root/'controller'),
            pins=dict(codex_server_sandbox=file_ref(self.profile), codex={'path':'/fixture/codex'}),
            rpc=dict(path=str(self.root/'s.sock'), argv=['/fixture/codex','app-server'],
                     launch_argv=['/fixture/mg','cx','original'], launch_environment={'FIXED_ROUTE':'yes'}),
            clients={'codex':{'environment':{}}})
        self.startup_ref = write_json(self.root/'startup.json', self.spec)
        self.runtime = dict(state_root=str(self.state), startup=self.startup_ref)
        Path(self.spec['controller']).mkdir()
        write_json(Path(self.spec['controller'])/'upgrade-binding.json', {'fixture':'binding'})
        self.registration = write_json(self.root/'registration.json', {'fixture':'registration'})
        self.receipt = write_json(self.root/'receipt.json', {'fixture':'receipt'})
        self.step = dict(group='original', ordinal=1)
        self.permit = write_json(self.root/'permit.json', dict(runtime=self.ref, step=self.step))
        self.lanes = {}; held={}; request_held={}; self.processes={}
        for i,lane in enumerate(('codex','claude_a','claude_b')):
            value = dict(startup=self.startup_ref,lane=lane,pid=100+i,birth=[1,i],
                         surface_uuid='surface-'+lane,workspace_uuid='original-workspace')
            launched=write_json(self.startup/(lane+'.json'),value); self.lanes[lane]=value
            held[lane]=write_json(self.startup/(lane+'-held.json'),dict(schema='upgrade-client-held-v1',
                startup=self.startup_ref,lane=lane,workspace_uuid=value['workspace_uuid'],
                surface_uuid=value['surface_uuid'],launched=launched))
            request_held[lane]={k:value[k] for k in ('pid','birth','surface_uuid')}
            self.processes[value['pid']]=value
        self.release = write_json(self.startup/'release.json',dict(schema='upgrade-client-release-v1',
            startup=self.startup_ref,workspace_uuid='original-workspace',exclusion_receipt=self.receipt,held=held))
        self.driver_ref = write_json(self.root/'driver.json', {'fixture':'driver'})
        owner_intent=write_json(self.root/'owner-intent.json',dict(driver=self.driver_ref,
            registration=self.registration,group='original'))
        self.owner={'intent':owner_intent}
        self.request=write_json(self.control/'launch-request.json',dict(runtime=self.ref,
            startup=self.startup_ref,task_owner=self.owner,held_release=dict(permit=self.step,
                group='original',receipt=self.receipt,held=request_held)))
        self.driver=SimpleNamespace(registration_ref=self.registration,
            config={'runtimes':{'original':self.ref}},_check_overall_deadline=lambda:None)
        targets=[('upgrade_live_runtime.validate_live_runtime',dict(return_value=({},self.runtime))),
            ('upgrade_live_control.external_publisher',dict()),
            ('orchestration_permit.require_running',dict()),
            ('upgrade_schedule.status',dict(return_value=dict(status='ACTIVE',active=self.step))),
            ('orchestration_permit.read_permit',dict(return_value=(self.permit,json_ref(self.permit)))),
            ('upgrade_task_launcher.observe_launch',dict(return_value=self.owner)),
            ('upgrade_driver.Driver',dict(return_value=self.driver)),
            ('upgrade_socket_boundary.binding_boundary',dict()),
            ('cmux_daemon_identity.process',dict(side_effect=lambda pid,**kw:self.processes[pid]))]
        self.mocks={}
        for target,kw in targets:
            p=patch(target,**kw);self.mocks[target]=p.start();self.addCleanup(p.stop)

    def plan(self):
        return server.launch_plan(self.ref,self.registration,'original',self.request,self.receipt)

    def launch(self):
        return server.launch(self.ref,self.registration,'original',self.request,self.receipt)

    def test_plan_binds_release_permit_receipt_and_original_lane(self):
        _,_,authority=self.plan()
        self.assertEqual(authority['codex_lane'],self.lanes['codex'])
        self.assertEqual(authority['release'],self.release)
        self.assertEqual(authority['permit'],self.permit)
        self.assertFalse((self.startup/'codex-server-intent.json').exists())

    def test_stop_halt_owner_and_group_change_refuse_before_spawn(self):
        with patch.object(server.subprocess,'Popen') as spawn:
            Path(self.spec['stop_path']).touch()
            with self.assertRaisesRegex(ContractError,'STOP_REQUESTED'):self.launch()
            Path(self.spec['stop_path']).unlink()
            self.mocks['upgrade_schedule.status'].return_value=dict(status='HALTED',active=self.step)
            with self.assertRaisesRegex(ContractError,'ACTIVE_PERMIT'):self.launch()
            self.mocks['upgrade_schedule.status'].return_value=dict(status='ACTIVE',active=dict(group='other',ordinal=1))
            with self.assertRaisesRegex(ContractError,'ACTIVE_PERMIT'):self.launch()
            self.mocks['upgrade_schedule.status'].return_value=dict(status='ACTIVE',active=self.step)
            self.mocks['upgrade_task_launcher.observe_launch'].return_value={'different':True}
            with self.assertRaisesRegex(ContractError,'OWNER_CHANGED'):self.launch()
            spawn.assert_not_called()

    def test_pre_release_and_changed_receipt_cannot_launch(self):
        with patch.object(server.subprocess,'Popen') as spawn:
            original=Path(self.release['path']).read_bytes();Path(self.release['path']).unlink()
            with self.assertRaises((ContractError,OSError)):self.launch()
            Path(self.release['path']).write_bytes(original)
            value=json_ref(self.release);value['exclusion_receipt']={'changed':True}
            write_json(self.release['path'],value)
            with self.assertRaisesRegex(ContractError,'ORIGINAL_RELEASE'):self.launch()
            spawn.assert_not_called()

    def test_changed_runtime_validation_prevents_intent_or_spawn(self):
        self.mocks['upgrade_live_runtime.validate_live_runtime'].side_effect=ContractError('profile/model/pin changed')
        with patch.object(server.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ContractError,'pin changed'):self.launch()
            spawn.assert_not_called()
        self.assertFalse((self.startup/'codex-server-intent.json').exists())

    def test_failed_spawn_keeps_terminal_result_and_never_retries(self):
        with patch.object(server.subprocess,'Popen',side_effect=OSError('fixture spawn failure')) as spawn:
            with self.assertRaisesRegex(OSError,'fixture spawn failure'):self.launch()
            result=json_ref(file_ref(self.startup/'codex-server-result.json'))
            self.assertEqual(result['status'],'UNKNOWN');self.assertIsNone(result['pid'])
            with self.assertRaisesRegex(ContractError,'NO_REPLAY'):self.launch()
            self.assertEqual(spawn.call_count,1)

    def test_stale_held_process_refuses(self):
        self.processes[100]=dict(self.processes[100],birth=[99,99])
        with self.assertRaisesRegex(ContractError,'HELD_PROCESS_CHANGED'):self.plan()

    def test_task_tui_refuses_failed_result_without_starting_a_server(self):
        write_json(self.startup/'codex-server-result.json',dict(schema='upgrade-server-result-v2',
            startup=self.startup_ref,status='UNKNOWN',returncode=71))
        with patch.object(server.subprocess,'Popen') as spawn:
            with self.assertRaisesRegex(ContractError,'ORIGINAL_START_FAILED'):
                wait_original_server(self.startup_ref,self.spec,{'state_root':str(self.state)})
            spawn.assert_not_called()

    @unittest.skipUnless(sys.platform=='darwin','Darwin Seatbelt execution')
    def test_actual_single_sandbox_socket_child_has_no_inherited_extra_fd(self):
        _, _, authority = self.plan()
        original_reader=self.mocks['cmux_daemon_identity.process']
        # Recover the real reader held by unittest's patch, for our actual child.
        actual=original_reader.side_effect
        import cmux_daemon_identity
        # The module's function was patched; the patcher's saved original is
        # obtained from the process module source via a separate module name.
        import importlib.util
        module_spec=importlib.util.spec_from_file_location('server_test_native_identity',cmux_daemon_identity.__file__)
        native=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(native)
        original_reader.side_effect=lambda pid,**kw:actual(pid,**kw) if pid in self.processes else native.process(pid,**kw)
        python=Path(native.process(os.getpid())['executable'])
        # Single complete policy plus a real socket; no native model is run.
        blocked=self.root/'blocked.txt'; scratch=self.root/'scratch';scratch.mkdir()
        sockpath=scratch/'s.sock'
        self.profile.write_text('(version 1)\n(allow default)\n(deny file-write*)\n'
            f'(allow file-write* (subpath "{scratch}"))\n(allow file-write* (literal "/dev/null"))\n')
        fd=os.open(self.root/'credential',os.O_CREAT|os.O_RDWR,0o600);os.set_inheritable(fd,True)
        self.addCleanup(os.close,fd)
        code=('import os,socket,time\n'
              f'try:os.fstat({fd})\nexcept OSError:pass\nelse:raise RuntimeError("inherited credential FD")\n'
              f'try:open({str(blocked)!r},"w")\nexcept PermissionError:pass\nelse:raise RuntimeError("outside write allowed")\n'
              f's=socket.socket(socket.AF_UNIX);s.bind({str(sockpath)!r});s.listen();time.sleep(10)\n')
        argv=[str(python),'-I','-B','-c',code]
        self.spec['rpc']=dict(path=str(sockpath),argv=argv,launch_argv=argv)
        self.spec['pins']['codex']={'path':str(python)}
        children=[];real_popen=subprocess.Popen
        def spawn(*args,**kw):
            self.assertTrue(kw['close_fds']);self.assertNotIn('pass_fds',kw)
            self.assertEqual(kw['env']['CMUX_SURFACE_ID'],'surface-codex')
            child=real_popen(*args,**kw);children.append(child);return child
        try:
            # Authorization uses the explicit fixture above. Only the process,
            # sandbox, socket and descriptor boundary are real in this test.
            with patch.object(server,'launch_plan',return_value=(self.runtime,self.spec,authority)), \
                    patch.object(server.subprocess,'Popen',side_effect=spawn), \
                    patch.dict(os.environ, {'CMUX_SURFACE_ID':'caller-must-not-leak'}):
                ref=self.launch()
            self.assertEqual(json_ref(ref)['status'],'SOCKET_CREATED')
            self.assertEqual(len(children),1)
            self.assertFalse(blocked.exists())
            result=wait_original_server(self.startup_ref,self.spec,{'state_root':str(self.state)})
            self.assertEqual(result['pid'],children[0].pid)
        finally:
            for child in children:
                if child.poll() is None:child.terminate()
                child.wait(timeout=5)


if __name__=='__main__':unittest.main()
