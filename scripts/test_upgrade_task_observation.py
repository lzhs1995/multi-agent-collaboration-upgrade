"""Original .84 registration/file readers, mocked kernel/deployment observations.

No input, launch, foreground impersonation or live enrollment occurs here.
"""
import ast
import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import cmux_native_delivery as original_namespace
from orchestration_common import ContractError
from upgrade_live_patch import native_readers
from upgrade_task_enrollment import _observe_claude
from test_upgrade_task_target import SOURCE,WS,SURFACE,PANE,SID


@unittest.skipUnless(SOURCE.is_dir(),"original .84 controller required")
class ClaudeObservationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        (self.root/"sessions").mkdir();(self.root/"projects").mkdir()
        self.path=self.root/"projects"/(SID+".jsonl")
        self.path.write_text(json.dumps(dict(sessionId=SID,type="assistant",message={"content":"fixture"}))+"\n")
        self.process=dict(pid=1010,birth=[int(time.time())-60,1],executable="/fixture/claude",file_identity=[1,2,3,4],
            argv=["/fixture/claude","--resume",SID],env=dict(CMUX_SURFACE_ID=SURFACE,CMUX_WORKSPACE_ID=WS))
        self.registration=self.root/"sessions/1010.json"
        self.registration.write_text(json.dumps(dict(pid=1010,kind="interactive",sessionId=SID,
            procStart=time.strftime('%a %b %e %H:%M:%S %Y',time.localtime(self.process["birth"][0])))))
        self.row=dict(workspace_uuid=WS,target_surface_uuid=SURFACE,target_pane_uuid=PANE,tty="ttys001",kind="terminal",dock=None)
        self.bridge=SimpleNamespace(pin_workspace=Mock(side_effect=AssertionError("sender authority is forbidden")))
        namespace=dict(original_namespace.__dict__)
        raw=(SOURCE/"scripts/cmux_native_delivery.py").read_text()
        rendered=native_readers("cmux_native_delivery.py",raw)
        functions=[n for n in ast.parse(rendered).body if isinstance(n,ast.FunctionDef)]
        exec(compile(ast.Module(body=functions,type_ignores=[]),"original-84-observation-readers","exec"),namespace)
        namespace.update(identity=SimpleNamespace(process=lambda pid:copy.deepcopy(self.process)),
            private_roots=SimpleNamespace(process_root=lambda *a:self.root,transcript_root=lambda *a,**k:self.root/"projects"),
            private_process=SimpleNamespace(terminal=lambda p:"ttys001"),
            _provider=lambda p:"claude",
            _tree_row=lambda *a:copy.deepcopy(self.row),_target_process=lambda row:copy.deepcopy(self.process))
        self.native=SimpleNamespace(**namespace)
        self.native.bind_target=Mock(side_effect=AssertionError("delivery binding is forbidden"))
        st=self.path.stat()
        self.expected=dict(provider="claude",session_id=SID,tty="ttys001",process=self.native._public_process(self.process),
            native_config_root=str(self.root),workspace_uuid=WS,surface_uuid=SURFACE,pane_uuid=PANE,
            transcript=dict(path=str(self.path),device=st.st_dev,inode=st.st_ino))

    def observe(self):return _observe_claude(self.bridge,self.native,self.expected)

    def test_original_registration_and_journal_observation_never_call_sender_binding(self):
        self.assertEqual(self.observe(),self.expected)
        self.bridge.pin_workspace.assert_not_called();self.native.bind_target.assert_not_called()
        self.assertEqual(set(self.observe()),set(self.expected))

    def test_resume_argv_cannot_replace_missing_or_foreign_native_registration(self):
        saved=self.registration.read_bytes();self.registration.unlink()
        with self.assertRaisesRegex(RuntimeError,"NATIVE_CLIENT_SESSION_REQUIRED"):self.observe()
        self.registration.write_bytes(saved)
        value=json.loads(saved);value["pid"]+=1;self.registration.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError,"NATIVE_REGISTRATION_PROCESS_MISMATCH"):self.observe()

    def test_process_replacement_and_new_ambiguity_are_rechecked(self):
        changed=copy.deepcopy(self.process);changed["birth"][1]+=1
        with patch.object(self.native,"_target_process",side_effect=[copy.deepcopy(self.process),changed]):
            with self.assertRaisesRegex(ContractError,"PROCESS_OR_TREE_CHANGED"):self.observe()
        with patch.object(self.native,"_target_process",side_effect=[copy.deepcopy(self.process),RuntimeError("NATIVE_TARGET_PROCESS_NOT_UNIQUE")]):
            with self.assertRaisesRegex(RuntimeError,"NOT_UNIQUE"):self.observe()

    def test_moved_surface_changed_tty_or_environment_cannot_supply_a_lane(self):
        moved=dict(self.row,target_pane_uuid="88888888-8888-4888-8888-888888888888")
        with patch.object(self.native,"_tree_row",side_effect=[copy.deepcopy(self.row),moved]):
            with self.assertRaisesRegex(ContractError,"PROCESS_OR_TREE_CHANGED"):self.observe()
        with patch.object(self.native.private_process,"terminal",return_value="ttys999"):
            with self.assertRaisesRegex(ContractError,"ENDPOINT_CHANGED"):self.observe()
        self.process["env"]["CMUX_WORKSPACE_ID"]="88888888-8888-4888-8888-888888888888"
        with self.assertRaisesRegex(ContractError,"ENDPOINT_CHANGED"):self.observe()

    def test_session_change_during_observation_is_refused(self):
        with patch.object(self.native,"_live_session",side_effect=[SID,"88888888-8888-4888-8888-888888888888"]):
            with self.assertRaisesRegex(ContractError,"SESSION_OR_ROOT_CHANGED"):self.observe()

    def test_wrong_metadata_partial_record_and_duplicate_history_are_refused(self):
        saved=self.path.read_bytes()
        self.path.write_bytes(saved.replace(SID.encode(),b"88888888-8888-4888-8888-888888888888"))
        with self.assertRaisesRegex(RuntimeError,"METADATA_SESSION_MISMATCH"):self.observe()
        self.path.write_bytes(saved+b"{")
        with self.assertRaisesRegex(ContractError,"INCOMPLETE_JOURNAL"):self.observe()
        self.path.write_bytes(saved)
        other=self.root/"projects/other";other.mkdir();(other/self.path.name).write_bytes(saved)
        with self.assertRaisesRegex(RuntimeError,"TRANSCRIPT_NOT_UNIQUE"):self.observe()

    def test_journal_replacement_or_inplace_change_after_pin_is_refused(self):
        original=self.native._pin_file
        def mutate(*args):
            pin=original(*args)
            self.path.write_bytes(self.path.read_bytes().replace(b"fixture",b"changed"))
            return pin
        with patch.object(self.native,"_pin_file",side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError,"TRANSCRIPT_PREFIX_CHANGED"):self.observe()
        def replace(*args):
            pin=original(*args);other=self.path.with_suffix(".new")
            other.write_bytes(self.path.read_bytes());other.replace(self.path)
            return pin
        with patch.object(self.native,"_pin_file",side_effect=replace):
            with self.assertRaisesRegex(RuntimeError,"TRANSCRIPT_REPLACED_OR_TRUNCATED"):self.observe()

    def test_complete_append_is_observation_only_and_preserves_original_identity(self):
        original=self.native._pin_file
        def append(*args):
            pin=original(*args)
            with self.path.open("a") as f:f.write(json.dumps(dict(sessionId=SID,type="progress"))+"\n")
            return pin
        with patch.object(self.native,"_pin_file",side_effect=append):
            self.assertEqual(self.observe(),self.expected)
        self.bridge.pin_workspace.assert_not_called();self.native.bind_target.assert_not_called()


if __name__=="__main__":unittest.main()
