"""Real materialization/publication/private-reader path with explicit native doubles.

Only original process/model/hook observations are fixtures. Startup validation,
complete source trees, external publication and the isolated generated reader
run normally. These tests neither launch clients nor count as business runs.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
import upgrade_live_control as control
import upgrade_live_runtime as runtime_reader
import upgrade_task_adoption as adoption
import upgrade_task_runtime_policy as policy


class FullAdoptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_upgrade_task_topology import TaskTopologyTests, SOURCE, WRAPPERS, PACKAGE
        if not all(p.is_dir() for p in (SOURCE, WRAPPERS, PACKAGE)):
            raise unittest.SkipTest('original local packages required')
        cls.materialized = type('FullAdoptionFixture', (TaskTopologyTests,), dict(
            model_network=True, callback_network=True, observer_network=True,
            mach_boundary=True, native_descriptors=True, runtime_policy=True))
        cls.materialized.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.materialized.tearDownClass()

    def setUp(self):
        from test_upgrade_task_adoption_evidence import AdoptionStartupEvidenceTests
        from upgrade_rpc_peer import server_pin
        self.original = original = AdoptionStartupEvidenceTests()
        original.setUp(); self.addCleanup(original.doCleanups)
        f, m = original.f, self.materialized
        self.runtime_ref, self.runtime = m.runtime_ref, m.runtime
        self.spec, self.binding = m.spec, m.binding
        self.control = Path(self.spec['adoption_path']).parent
        self.launch_root = Path(self.binding['state_root'])/'startup'
        # Only directories in this test's disposable materialization are reset.
        for path in (self.control, self.launch_root):
            if path.exists(): shutil.rmtree(path)
            path.mkdir(parents=True)
        f.startup, f.spec, f.binding = m.runtime['startup'], m.spec, m.binding
        self.value = original.value
        sessions = self.value['sessions']
        for i, lane in enumerate(('codex', 'claude_a', 'claude_b', 'codex-server')):
            row = f.rows[100+i]
            row['ppid'] = 50
            if lane == 'codex-server':
                row.update(root=m.spec['clients']['codex']['native_root'],
                           executable=m.spec['pins']['codex']['path'])
                continue
            session = sessions[lane]
            root = Path(m.spec['clients'][lane]['native_root'])
            session.update(native_config_root=str(root), pane_uuid=str(uuid.uuid4()))
            if lane != 'codex': session['session_id'] = m.spec['clients'][lane]['session_id']
            transcript = root/('sessions' if lane == 'codex' else 'projects')/(session['session_id']+'.jsonl')
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text('native-observation-fixture\n')
            st = transcript.stat()
            session['transcript'] = dict(path=str(transcript), device=st.st_dev, inode=st.st_ino)
            row['root'] = str(root)
        f.anchor.update(server=server_pin(f.rows[103], uid=os.geteuid()),
                        native_root=f.rows[100]['root'], journal=sessions['codex']['transcript'])
        original.journal = dict(f.anchor['journal'], metadata={'fixture': 'metadata'})
        original.journal_evidence.return_value = original.journal
        original.task_startup_result.return_value = f.initial
        launches = {}
        for i, lane in enumerate(('codex', 'claude_a', 'claude_b', 'codex-server')):
            row = f.rows[100+i]
            launch = dict(startup=f.startup, pid=row['pid'], birth=row['birth'],
                surface_uuid=row['env']['CMUX_SURFACE_ID'], workspace_uuid=row['env']['CMUX_WORKSPACE_ID'])
            if lane != 'codex-server':
                launch['terminal_descriptors'] = dict(schema='upgrade-task-launch-descriptors-v1',
                    extra_fds_remaining=0, closed_fds=[3, 4],
                    terminal=[dict(fd=fd, device=1, inode=10+i, rdev=20+i) for fd in range(3)])
            launches[lane] = write_json(self.launch_root/(lane+'.json'), launch)
        roles = self.value['task_roles']
        roles.update(startup=f.startup, launches=launches, execution_server=f.anchor['server'])
        with patch.object(policy, 'default_decisions', return_value={op:1 for op in policy.OPERATIONS}):
            roles['runtime_policy'] = policy.observe([f.rows[p] for p in (103, 101, 102)], process=f.process)
        capability = self.value['codex_capability']
        thread = capability['task_thread']
        capability.update(path=f.anchor['journal']['path'])
        thread.update(journal=original.journal,
                      descriptor=dict(f.anchor['journal'], fd=12, access='w'))
        thread['server_peer'].update(server=f.anchor['server'],
                                    peer={k:f.anchor['server'][k] for k in ('pid','uid')})
        self.value.update(startup=f.startup, launches=launches, ready=True, startup_ready=True,
            business_adoption_supported=True, observed_at_ns=time.time_ns(),
            inspector=file_ref(Path(m.spec['controller'])/'scripts/mac_upgrade_live_inspect.py'))
        self.value['task_startup_evidence'] = original.collect()
        write_json(self.launch_root/'intent.json', {'fixture': 'original launch'})
        self.observation_path = self.control/'observations'/str(uuid.uuid4())/'result.json'
        self.save_observation()
        # Preserve the real independent process and sandbox. Inject only the
        # same explicit native doubles used by the isolated private reader.
        import upgrade_task_adoption_publisher as publisher
        run = subprocess.run
        def native_fixture_run(argv, **kwargs):
            if '-c' in argv and argv[argv.index('-c')+1] == publisher.CHECK_CODE:
                v = dict(initial=f.initial, journal=original.journal, readiness=original.readiness)
                prelude = ('from unittest.mock import patch\n'
                    'import mac_upgrade_task_adoption_evidence as e\n'
                    'v=json.loads('+repr(json.dumps(v))+');r=v["readiness"]\n'
                    'patch.object(e,"task_startup_result",return_value=tuple(v["initial"])).start()\n'
                    'patch.object(e,"journal_evidence",return_value=v["journal"]).start()\n'
                    'patch.object(e,"task_readiness",return_value=r["codex"]).start()\n'
                    'patch.object(e,"readiness_evidence",side_effect=lambda n,s,lane,*a:r[lane]).start()\n')
                argv=list(argv);i=argv.index('-c')+1
                argv[i]=argv[i].replace('try:\n',prelude+'try:\n',1)
            return run(argv, **kwargs)
        p=patch.object(publisher.subprocess,'run',side_effect=native_fixture_run)
        p.start();self.addCleanup(p.stop)

    def save_observation(self):
        self.envelope = dict(runtime=self.runtime_ref, startup=self.runtime['startup'],
            command='experiment-inspect', argv=control.harness_argv(self.runtime, self.spec, 'experiment-inspect'),
            returncode=0, input_operations=0, result=self.value)
        self.observation = write_json(self.observation_path, self.envelope)
        self.proof = dict(schema=adoption.SCHEMA, runtime=self.runtime_ref, startup=self.runtime['startup'],
            verified=True, observed_at='fixture-original-observation', inspection=self.observation,
            sessions=self.value['sessions'], input_operations=0)

    def publish(self):
        # The subprocess inspection is a fixture; its persisted envelope is
        # validated by the actual candidate validator before actual publication.
        with patch.object(control, 'invocation', return_value=(self.observation, self.envelope)):
            return control.control(self.runtime_ref, 'adopt')

    def private_read(self, expected_error=None, *, allow_stopped=False):
        original = self.original
        fixture = write_json(original.f.root/'private-reader-fixture.json', dict(
            binding=self.binding, initial=original.f.initial, journal=original.journal,
            readiness=original.readiness, expected=json_ref(file_ref(self.spec['adoption_path'])),
            expected_error=expected_error, allow_stopped=allow_stopped))
        code = '''import sys,json
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
import mac_upgrade_task_adoption as subject
import mac_upgrade_task_adoption_evidence as evidence
from mac_upgrade_common import ContractError
v=json.load(open(sys.argv[2]));r=v['readiness']
with patch.object(evidence,'task_startup_result',return_value=tuple(v['initial'])), \\
     patch.object(evidence,'journal_evidence',return_value=v['journal']), \\
     patch.object(evidence,'task_readiness',return_value=r['codex']), \\
     patch.object(evidence,'readiness_evidence',side_effect=lambda n,s,lane,*a:r[lane]):
 try: result=subject.read(v['binding'],allow_stopped=v['allow_stopped'])
 except ContractError as exc:
  assert v['expected_error'] and v['expected_error'] in str(exc),str(exc)
 else:
  assert v['expected_error'] is None and result==v['expected']
assert not any(n.startswith('upgrade_') for n in sys.modules)
assert 'mac_upgrade_live_control' not in sys.modules
print('PRIVATE_ADOPTION_READER_CHECKED')
'''
        scripts = Path(self.spec['controller'])/'scripts'
        result = subprocess.run(['/usr/bin/sandbox-exec', '-f', self.spec['pins']['claude_a_sandbox']['path'],
                                 sys.executable, '-I', '-S', '-B', '-c', code, str(scripts), fixture['path']],
                                cwd=scripts, capture_output=True, text=True, timeout=45, close_fds=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.stdout.strip(), 'PRIVATE_ADOPTION_READER_CHECKED')

    def test_external_publication_and_isolated_private_reader_share_full_validation(self):
        result = self.publish()
        self.assertEqual(result['status'], 'ADOPTED')
        self.assertEqual(result['input_operations'], 0)
        proof = runtime_reader.validate_adoption(self.runtime_ref, result['adoption'])
        self.assertEqual(proof['schema'], adoption.SCHEMA)
        self.private_read()
        with patch.object(control, 'invocation') as invoke:
            self.assertEqual(control.control(self.runtime_ref, 'adopt')['status'], 'ALREADY_ADOPTED')
            invoke.assert_not_called()

    def test_changed_runtime_policy_hook_evidence_or_descriptor_blocks_publication(self):
        original = copy.deepcopy(self.value)
        changes = (
            ('policy', lambda v:v['task_roles']['runtime_policy']['processes'][1]['default_decisions'].update({'network-outbound':0})),
            ('process', lambda v:v['task_roles']['runtime_policy']['processes'][0]['process'].update(pid=100)),
            ('readiness', lambda v:v['readiness']['claude_a'].update(model='changed')),
            ('historical evidence', lambda v:v['task_startup_evidence']['server'].update(pid=100)),
        )
        for label, mutate in changes:
            with self.subTest(label=label):
                self.value = copy.deepcopy(original); mutate(self.value); self.save_observation()
                with self.assertRaises(ContractError): self.publish()
                self.assertFalse(Path(self.spec['adoption_path']).exists())
        self.value = original
        path = self.launch_root/'claude_a.json'; launch = json_ref(file_ref(path))
        launch['terminal_descriptors']['extra_fds_remaining'] = 1
        self.value['launches']['claude_a'] = write_json(path, launch)
        self.original.value = self.value
        self.value['task_startup_evidence'] = self.original.collect()
        self.save_observation()
        with self.assertRaisesRegex(ContractError, 'DESCRIPTOR_SEAL_CHANGED'): self.publish()
        self.assertFalse(Path(self.spec['adoption_path']).exists())

    def test_inspector_command_cannot_be_rebound(self):
        self.envelope['argv'][-1] = '0'*64
        self.observation = write_json(self.observation_path, self.envelope)
        with self.assertRaisesRegex(ContractError, 'INSPECTOR_INVOCATION_CHANGED'): self.publish()
        self.assertFalse(Path(self.spec['adoption_path']).exists())

    def test_private_reader_rejects_rewritten_proof_and_changed_frozen_inspector(self):
        self.publish()
        path=Path(self.spec['adoption_path']); original=path.read_bytes();path.chmod(0o600)
        value=json.loads(original);value['verified']=False;write_json(path,value)
        self.private_read('ORIGINAL_PROOF_CHANGED')
        path.write_bytes(original)
        inspector=Path(self.spec['controller'])/'scripts/mac_upgrade_live_inspect.py'
        raw=inspector.read_bytes();mode=inspector.stat().st_mode&0o777
        try:
            inspector.chmod(0o600);inspector.write_bytes(raw+b'\n# changed frozen fixture\n')
            self.private_read('runtime tree changed')
        finally:
            inspector.write_bytes(raw);inspector.chmod(mode)

    def test_stop_blocks_new_publication_and_input_but_allows_explicit_historical_read(self):
        self.publish()
        stop=Path(self.spec['stop_path']);stop.touch();self.addCleanup(stop.unlink)
        self.private_read('EXPERIMENT_STOP_REQUESTED')
        self.private_read(allow_stopped=True)
        with self.assertRaisesRegex(ContractError,'EXPERIMENT_STOP_REQUESTED'):self.publish()


if __name__ == '__main__': unittest.main()
