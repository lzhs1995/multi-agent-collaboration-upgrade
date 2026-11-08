"""New materialization and old-mode refusal; no native clients are launched."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, write_json
from upgrade_experiment import init_experiment
from upgrade_live_materialize import materialize_live
from upgrade_startup_contract import TASK_SCHEMA, validate_startup
from upgrade_task_startup import MULTI_PLAN_SCHEMA, plan_value
from upgrade_socket_boundary import SOCKET_POLICY
import upgrade_live_control as control

SOURCE=Path.home()/".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
WRAPPERS=Path("/Applications/cmux.app/Contents/Resources/bin")
PACKAGE=Path("/Users/lzhs/Developer/codex-releases/0.164.0")


@unittest.skipUnless(SOURCE.is_dir() and WRAPPERS.is_dir() and PACKAGE.is_dir(), "local frozen packages required")
class TaskTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix="",dir="/tmp")
        cls.root=Path(cls.tmp.name).resolve()
        cls.context=init_experiment(cls.root/"l","task-topology-fixture",SOURCE)
        instructions={lane:write_json(cls.root/(lane+".json"),{"fixture":lane})
                      for lane in ("codex","claude_a","claude_b")}
        binaries={k:Path("/usr/bin/true") for k in ("claude","cmux","rtk","node")}
        binaries["codex"]=PACKAGE/"codex-aarch64-apple-darwin"
        binaries.update({p+"_wrapper":WRAPPERS/("cmux-"+p+"-wrapper") for p in ("codex","claude")})
        task={"task_id":"fixture-task","controller_id":"fixture-root","authority_ref":cls.context,
            "startup_text":"Use the registered readiness operation once.","base_instructions":"Fixture, no effects.",
            "operations":{"ready":{"kind":"readiness","payload":{}},
                "left":{"kind":"route","payload":{"lane":"claude_a"}},
                "right":{"kind":"route","payload":{"lane":"claude_b"}}},
            "initial_operations":["ready"],"choice_groups":{"route":["left","right"]}}
        if getattr(cls,"generated_slots",False):
            from upgrade_task_bindings import SLOT_SCHEMA
            task["operations"]["accept"]={"kind":"fixture","payload":{"marker":None}}
            task["binding_slots"]={"accept":dict(schema=SLOT_SCHEMA,resolver="campaign-accept-marker-v1",
                source_path=str(cls.root/"candidate.json"),producer_operation="left")}
        cls.runtime_ref=materialize_live(cls.context,"B",binaries=binaries,
            native_policy=SOCKET_POLICY,codex_package=PACKAGE,pre_exec_hold=True,
            provider_configuration=({"model":"fixture-codex","model_provider":"custom"},
                {"model":"fixture-claude","env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:3445"}}),
            station_launchers={"mg":"/usr/bin/true","magpie":"/usr/bin/true",
                "codex":{"station":"agentrouter-a","base_url":"http://127.0.0.1:3465"},
                "claude":{"station":"linxi","key":"linxi-s1"}},
            goal_startup={lane:"Complete this fixture only" for lane in ("claude_a","claude_b")},
            lane_instructions=instructions,
            task_startup=task,task_model_network=getattr(cls,"model_network",False),
            task_callback_network=getattr(cls,"callback_network",False),
            task_observer_network=getattr(cls,"observer_network",False),
            task_mach_boundary=getattr(cls,"mach_boundary",False),
            task_native_descriptors=getattr(cls,"native_descriptors",False),
            task_runtime_policy=getattr(cls,"runtime_policy",False))
        cls.runtime=json_ref(cls.runtime_ref)
        cls.spec,cls.binding=validate_startup(cls.runtime["startup"])

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_new_schema_has_empty_origin_tui_and_distinct_server_boundary(self):
        self.assertEqual(self.spec["schema"],TASK_SCHEMA)
        plan=plan_value(self.spec["task_startup"])
        self.assertEqual(plan["schema"],MULTI_PLAN_SCHEMA)
        self.assertEqual(plan["initial_operations"],["ready"])
        self.assertEqual(plan["choice_groups"],{"route":["left","right"]})
        import upgrade_toml as tomllib
        config=tomllib.loads(Path(self.spec["pins"]["codex/config.toml"]["path"]).read_text())
        self.assertFalse(config["features"]["shell_tool"])
        self.assertTrue(config["features"]["code_mode"])
        self.assertFalse(config["agents"]["enabled"])
        self.assertEqual(plan["model"],"fixture-codex")
        from upgrade_task_readiness import READY_INSTRUCTION
        self.assertTrue(plan["startup_text"].endswith(READY_INSTRUCTION))
        self.assertNotIn(plan["startup_text"],self.spec["clients"]["codex"]["argv"])
        self.assertEqual(self.spec["clients"]["codex"]["argv"][-1],"--dangerously-bypass-hook-trust")
        self.assertIn("/goal",self.spec["clients"]["claude_a"]["argv"][-1])
        server=Path(self.spec["pins"]["codex_server_sandbox"]["path"]).read_text()
        tui=Path(self.spec["pins"]["codex_sandbox"]["path"]).read_text()
        self.assertIn("deny network-outbound",server)
        self.assertNotIn("deny network-outbound",tui)
        self.assertEqual(self.binding["codex_package"]["root"],str(PACKAGE))
        self.assertFalse((Path(self.binding["state_root"])/"startup").exists())

    def test_support_modules_are_reachable_in_frozen_private_controller(self):
        root=Path(self.spec["controller"])/"scripts"
        self.assertIn("from mac_upgrade_task_tui import task_tui_argv",(root/"mac_upgrade_live_entry.py").read_text())
        self.assertIn("mac_upgrade_task_operations",(root/"mac_upgrade_task_startup.py").read_text())
        self.assertTrue((root/"mac_upgrade_task_creation.py").is_file())

    def test_old_runtime_cannot_use_new_bootstrap(self):
        with patch.object(control,"validate_live_runtime",return_value=({},self.runtime)), \
             patch.object(control,"json_ref",return_value={"schema":"upgrade-startup-v3"}), \
             patch.object(control,"external_publisher") as publisher:
            with self.assertRaisesRegex(ContractError,"EXPLICIT_NEW_TOPOLOGY"):
                control.bootstrap_task_thread(self.runtime_ref,object())
            publisher.assert_not_called()

    def test_task_requires_original_held_owner_before_formal_launch(self):
        with patch.object(control,"invocation") as invoke:
            for command,error in (("launch","HOLD_AND_RELEASE"),("hold","ORIGINAL_DRIVER_REQUIRED")):
                with self.subTest(command=command),self.assertRaisesRegex(ContractError,error):
                    control.control(self.runtime_ref,command)
            invoke.assert_not_called()
        self.assertFalse((Path(self.spec["adoption_path"]).parent/"launch-request.json").exists())
        self.assertFalse((Path(self.spec["adoption_path"]).parent/"held-request.json").exists())


class SlotTaskTopologyTests(TaskTopologyTests):
    generated_slots=True

    def test_new_schema_has_empty_origin_tui_and_distinct_server_boundary(self):
        from upgrade_task_startup import BINDING_PLAN_SCHEMA
        self.assertEqual(plan_value(self.spec["task_startup"])["schema"],BINDING_PLAN_SCHEMA)

    def test_generated_slot_is_materialized_and_not_launchable(self):
        from upgrade_task_startup import BINDING_PLAN_SCHEMA
        plan=plan_value(self.spec["task_startup"])
        self.assertEqual(plan["schema"],BINDING_PLAN_SCHEMA)
        self.assertEqual(plan["initial_operations"],["ready"])
        self.assertIsNone(plan["operations"]["accept"]["payload"]["marker"])
        root=Path(self.spec["controller"])/"scripts"
        self.assertTrue((root/"mac_upgrade_task_bindings.py").is_file())
        self.assertIn("from mac_upgrade_task_bindings import",(root/"mac_upgrade_task_operations.py").read_text())
        self.test_task_requires_original_held_owner_before_formal_launch()


class NetworkTaskTopologyTests(TaskTopologyTests):
    model_network=True

    def test_three_execution_profiles_bind_original_gateway_and_keep_tui_separate(self):
        from upgrade_task_network import model_only_profile
        contract=self.binding["task_model_network"]
        self.assertEqual(contract["ports"],dict(codex=3465,claude_a=3445,claude_b=3445))
        for lane,key in (("codex","codex_server_sandbox"),("claude_a","claude_a_sandbox"),("claude_b","claude_b_sandbox")):
            raw=Path(self.spec["pins"][key]["path"]).read_bytes()
            self.assertTrue(raw.endswith(model_only_profile(b"(version 1)\n",contract,lane).split(b"\n",1)[1]))
        for key in ("control_sandbox","codex_sandbox"):
            self.assertNotIn(b"(deny network-outbound)",Path(self.spec["pins"][key]["path"]).read_bytes())
        root=Path(self.spec["controller"])/"scripts"
        self.assertTrue((root/"mac_upgrade_task_network.py").is_file())
        self.test_task_requires_original_held_owner_before_formal_launch()

    def test_binding_cannot_retarget_gateway(self):
        path=Path(self.spec["controller"])/"upgrade-binding.json"
        original=path.read_bytes()
        mode=path.stat().st_mode&0o777
        value=json.loads(original)
        value["task_model_network"]["ports"]["codex"]=3446
        try:
            path.chmod(0o600)  # Owned disposable fixture, never a live release.
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ContractError,"TASK_MODEL_NETWORK_CHANGED"):
                validate_startup(self.runtime["startup"])
        finally:
            path.write_bytes(original)
            path.chmod(mode)

    def test_network_opt_in_cannot_change_a_legacy_startup(self):
        with self.assertRaisesRegex(ContractError,"TASK_NETWORK_REQUIRES_EXPLICIT_TASK"):
            materialize_live(self.context,"B",binaries={},task_model_network=True)


class CallbackNetworkTaskTopologyTests(NetworkTaskTopologyTests):
    callback_network=True

    def test_callback_exact_new_path_is_frozen_and_cannot_be_retargeted(self):
        from upgrade_task_network import CALLBACK_SCHEMA, callback_path
        contract=self.binding['task_model_network']
        self.assertEqual(contract['schema'],CALLBACK_SCHEMA)
        self.assertEqual(contract['callback'],callback_path(self.binding['evidence_root']))
        for key in ('codex_server_sandbox','codex_sandbox','control_sandbox'):
            self.assertNotIn(contract['callback'],Path(self.spec['pins'][key]['path']).read_text())
        path=Path(self.spec['controller'])/'upgrade-binding.json';original=path.read_bytes();mode=path.stat().st_mode&0o777
        value=json.loads(original);value['task_model_network']['callback']+='.other'
        try:
            path.chmod(0o600);path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ContractError,'TASK_MODEL_NETWORK_CHANGED'):
                validate_startup(self.runtime['startup'])
        finally:path.write_bytes(original);path.chmod(mode)


if __name__=="__main__":unittest.main()
