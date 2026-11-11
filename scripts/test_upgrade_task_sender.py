"""Original .84 sender production functions with explicit offline observations.

Real temporary journals and typed-operation provenance are used; process/tree,
startup and adoption observations are fixtures, not live client evidence.
"""
import ast
from contextvars import copy_context
import copy
from pathlib import Path
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import patch

from orchestration_common import ContractError,file_ref,json_ref,write_json
import upgrade_task_bridge_context as context
import upgrade_task_sender as sender
from upgrade_task_sender_evidence import verify_delivery_source,verify_source
from upgrade_task_bridge_patch import task_bridge_paths
from test_upgrade_task_target import SOURCE
import test_upgrade_task_dispatch as fixtures


@unittest.skipUnless(SOURCE.is_dir(),"original .84 source required")
class SenderTests(unittest.TestCase):
    def setUp(self):
        helper=fixtures.DispatchTests();helper.setUp();self.addCleanup(helper.doCleanups)
        helper.f.call("dispatch");helper.f.complete("dispatch")
        self.helper=helper;self.f=helper.f;self.original=helper.binding
        self.proof=self.original["task_sender"]["task_actor"]
        self.sender=self.f.c.campaign["sessions"]["codex"]
        self.receiver=self.f.c.campaign["sessions"]["claude_a"]
        self.text=Path(json_ref(self.proof)["arguments"]["notice"]["path"]).read_text()
        root=helper.transport.root/"sender-fixture"
        startup=write_json(root/"config/startup-v2.json",dict(schema="upgrade-startup-task-v1"))
        write_json(root/"state/startup/codex.json",dict(startup=startup,surface_uuid=self.sender["surface_uuid"]))
        config=dict(config_root=str(root/"config"),state_root=str(root/"state"),task_startup={"fixture":True})
        patcher=patch.object(sender,"configuration",return_value=(config,{}))
        patcher.start();self.addCleanup(patcher.stop)
        self.value=dict(identity_source=context.SOURCE,foreground_authentication=False,task_actor=self.proof)
        self.live=dict(self.original["identity"],identity_source=context.SOURCE,task_actor=self.proof)
        self.bridge=SimpleNamespace(pin_workspace=lambda *a:copy.deepcopy(self.live))
        import cmux_native_delivery as original
        self.native=ModuleType("original84_sender_fixture")
        self.native.__dict__.update(original.__dict__)
        raw=task_bridge_paths("cmux_native_delivery.py",(SOURCE/"scripts/cmux_native_delivery.py").read_text())
        functions=[n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef)]
        exec(compile(ast.Module(body=functions,type_ignores=[]),"original84-task-sender","exec"),self.native.__dict__)
        process=dict(self.receiver["process"],env=dict(CMUX_SURFACE_ID=self.receiver["surface_uuid"],
            CMUX_WORKSPACE_ID=self.receiver["workspace_uuid"]))
        for name,value in dict(_tree_row=dict(self.original["identity"],tty=self.receiver["tty"]),
                _target_process=process,_provider="claude",_live_session=self.receiver["session_id"],
                _transcript_path=Path(self.original["transcript"]["path"]),
                _public_process=self.receiver["process"],_process_tty=self.receiver["tty"],
                _native_root=Path(self.original["transcript"]["path"]).parent).items():
            patcher=patch.object(self.native,name,return_value=value)
            patcher.start();self.addCleanup(patcher.stop)
        self.native.identity=SimpleNamespace(process=lambda *a:copy.deepcopy(process))

    def bind(self):return self.native.bind_target(self.bridge,self.receiver["surface_uuid"],self.text)

    def test_original_producer_preserves_source_and_readonly_does_not_activate_sender(self):
        with context.activate(lambda:copy.deepcopy(self.value)):
            binding=self.bind()
            self.assertEqual(binding["task_sender"],self.original["task_sender"])
            self.assertEqual(set(binding["identity"]),set(self.native.IDENTITY_KEYS))
            self.native.require_bound(self.bridge,self.receiver["surface_uuid"],binding,self.text)
            copied=copy_context()
        self.native.require_bound(self.bridge,self.receiver["surface_uuid"],binding,self.text,read_only=True)
        with self.assertRaisesRegex(ContractError,"ACTIVE_OPERATION_REQUIRED"):
            self.native.require_bound(self.bridge,self.receiver["surface_uuid"],binding,self.text)
        with self.assertRaisesRegex(ContractError,"OWNER_CHANGED_OR_CLOSED"):
            copied.run(sender.require_live,binding)
        verify_source(binding,self.sender,self.receiver,fixtures=True)

    def test_missing_changed_and_foreign_sources_refuse_original_reader(self):
        with context.activate(lambda:copy.deepcopy(self.value)):binding=self.bind()
        missing=copy.deepcopy(binding);missing.pop("task_sender")
        with self.assertRaisesRegex(ContractError,"DOWNGRADE_OR_FOREIGN_SOURCE"):
            self.native._validate_binding(missing,self.text)
        changed=copy.deepcopy(binding);changed["task_sender"]["identity_source"]="foreground"
        with self.assertRaisesRegex(ContractError,"SOURCE_CHANGED"):
            self.native._validate_binding(changed,self.text)
        foreign=copy.deepcopy(binding);foreign["identity"]["caller_surface_uuid"]=self.receiver["surface_uuid"]
        with self.assertRaisesRegex(ContractError,"DOWNGRADE_OR_FOREIGN_SOURCE"):
            self.native._validate_binding(foreign,self.text)
        value=json_ref(self.proof);value.update(schema="upgrade-task-harness-operation-v1",phase="map")
        binding["task_sender"]["task_actor"]=write_json(self.helper.transport.root/"invalid-phase.json",value)
        with self.assertRaisesRegex(ContractError,"PHASE_CANNOT_SEND"):
            self.native._validate_binding(binding,self.text)

    def test_current_actor_cannot_borrow_another_saved_source(self):
        with context.activate(lambda:copy.deepcopy(self.value)):binding=self.bind()
        wrong=dict(self.value,task_actor=file_ref(self.helper.transport.root/"dispatch-intent.json"))
        with context.activate(lambda:wrong):
            with self.assertRaisesRegex(ContractError,"ACTIVE_OPERATION_REQUIRED"):
                sender.require_live(binding)
            with self.assertRaisesRegex(ContractError,"CURRENT_SOURCE_CHANGED"):
                self.bind()

    def test_historical_adopted_task_requires_source_and_exact_delivery_operation(self):
        binding=copy.deepcopy(self.original);binding.pop("task_sender")
        startup=write_json(self.helper.transport.root/"adoption-startup-fixture.json",dict(schema="upgrade-startup-task-v1"))
        adopted=dict(self.sender,private_native=dict(runtime={"fixture":True},adoption={"fixture":True}))
        with patch("upgrade_live_runtime.session_authority",return_value=({},dict(startup=startup),
                dict(sessions=dict(codex=adopted)))):
            with self.assertRaisesRegex(ContractError,"PROVENANCE_REQUIRED"):
                verify_source(binding,adopted,self.receiver)
        delivery=dict(attempt=dict(native_binding=self.original))
        verify_delivery_source(delivery,self.proof)
        with self.assertRaisesRegex(ContractError,"DELIVERY_OPERATION_CHANGED"):
            verify_delivery_source(delivery,file_ref(self.helper.transport.root/"dispatch-intent.json"))

    def test_legacy_and_callback_sender_without_actor_remain_context_free(self):
        # Callback target selection is tested separately using the real .84
        # TASK target functions. Sender source must not replace target source.
        callback=copy.deepcopy(self.original);callback.pop("task_sender")
        callback["identity"]["caller_surface_uuid"]=self.receiver["surface_uuid"]
        sender.check(callback)
        self.assertIsNone(context.snapshot())
        with patch.object(sender,"configuration",return_value=({},{})):
            legacy=copy.deepcopy(self.original);legacy.pop("task_sender")
            sender.check(legacy)


if __name__=="__main__":unittest.main()
