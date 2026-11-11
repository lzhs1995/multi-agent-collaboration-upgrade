"""R32 output-effect regressions; all journals and campaigns are offline fixtures."""
import copy
import json
import shlex
import tempfile
import unittest
from pathlib import Path

import orchestration_campaign as contracts
from orchestration_common import ContractError, canonical, digest, file_ref, parse_time, utc_now, write_json
from orchestration_coordinator import State
from orchestration_evidence import task_scope
from orchestration_native import delivery_identity
from orchestration_test_support import CampaignFixture
from orchestration_verify import finish_run, verify_run
from test_orchestration_native import NativeFixture, STAMP

LATE = "2026-10-14T00:00:04+00:00"
LEGACY = (
    (contracts.METRICS, 2, "35d7e06556bfd4f72f840a2c7ffda4250cbeaaf967c9007357980e8726159230"),
    (contracts.METRICS_V3, 3, "452dad3b28d32e177e47f30f31b4f18d766f2d46f2287888eb08ba8d285192e8"),
    (contracts.METRICS_V3_COMMUNICATION, 3, "9dbc72f0a995256dc93ab722588750d12f8c70b4ee703824230cf4318d37e406"),
)


def new_metrics():
    return contracts.METRICS_V3_OUTPUT_EFFECTS


def effect_policy():
    return new_metrics()["output_effect_policy"]


def readonly_shell(program):
    return "/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /bin/sh -f -c " + shlex.quote(program)


def call_record(client, name, args, key, at):
    if client.provider == "codex":
        return dict(type="response_item", timestamp=at, payload=dict(type="function_call",
            name=name, call_id=key, arguments=json.dumps(args)))
    record = client.message("assistant", "", at=at)
    record["message"]["content"] = [dict(type="tool_use", id=key, name=name, input=args)]
    return record


def result_record(client, key, at, *, output="ok", error=False):
    if client.provider == "codex":
        return dict(type="response_item", timestamp=at, payload=dict(type="function_call_output",
            call_id=key, output=output, is_error=error))
    record = client.message("user", "", at=at)
    record["message"]["content"] = [dict(type="tool_result", tool_use_id=key, content=output, is_error=error)]
    return record


def append_action(client, name, args, *, at=None, end=None, error=False, output="ok", complete=True):
    at = at or utc_now()
    key = "effect-" + str(client.path.stat().st_size)
    call = client.append(call_record(client, name, args, key, at))
    if complete:
        client.append(result_record(client, key, end or utc_now(), output=output, error=error))
    return key, call


class OutputEffectNativeTests(unittest.TestCase):
    def fixture(self, provider="claude"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        f = NativeFixture(temp.name, provider)
        if provider == "codex":
            f.append(dict(type="turn_context", timestamp=STAMP, payload={"model":"test-model"}))
        else:
            f.append(f.message("assistant", "ready", at=STAMP))
        return f, f.reader.snapshot()["offset"]

    def summary(self, f, start, *, policy=True, **options):
        if policy:
            options["effect_policy"] = effect_policy()
        return f.reader.action_summary(start, f.reader.snapshot()["offset"], task_id="task",
            output_root="/task/output", **options)

    def test_legacy_metric_constants_keep_exact_frozen_bytes(self):
        for metrics, _, expected in LEGACY:
            with self.subTest(version=metrics["version"]):
                self.assertEqual(digest(canonical(metrics)), expected)

    def test_legacy_summary_keeps_exact_shape_and_read_timing(self):
        f, start = self.fixture()
        append_action(f, "Write", {"file_path":"/task/output/report.md"}, at=STAMP, end=STAMP)
        append_action(f, "Read", {"file_path":"/task/output/report.md"}, at=LATE, end=LATE)
        expected = dict(first_action_at=STAMP, last_action_at=LATE, failed_api_attempts=0,
            provider_auto_retries=0, user_inputs=[], attempted_task_calls=2, failed_task_calls=0,
            completed_task_calls=2, models=["test-model"])
        self.assertEqual(canonical(self.summary(f,start,policy=False)),canonical(expected))
        for metrics,version,_ in LEGACY:
            with self.subTest(metric_version=metrics["version"]):
                selected=contracts.campaign_effect_policy({"version":version,"metrics":metrics})
                self.assertIsNone(selected)
                self.assertEqual(canonical(self.summary(f,start,policy=False,effect_policy=selected)),canonical(expected))

    def test_read_is_activity_but_not_potential_output_mutation(self):
        f, start = self.fixture()
        append_action(f,"Read",{"file_path":"/task/output/report.md"},at=LATE,end=LATE)
        result = self.summary(f,start,include_evidence=True)
        self.assertEqual(result["completed_task_calls"],1)
        self.assertEqual(result["last_action_at"],LATE)
        self.assertEqual(result["attempted_potential_write_calls"],0)
        self.assertIsNone(result["last_potential_write_at"])
        self.assertIn("effect",result["actions"][0])

    def test_r32_two_ls_program_in_fixed_readonly_environment_for_both_cli_schemas(self):
        command = readonly_shell("ls -la /task/nodes/inventory/preflight/ /task/nodes/inventory/; ls -la /task/output")
        for provider in ("claude","codex"):
            with self.subTest(provider=provider):
                f,start=self.fixture(provider)
                name,args=("Bash",{"command":command}) if provider=="claude" else ("functions.exec_command",{"cmd":command,"login":False})
                append_action(f,name,args,at=LATE,end=LATE)
                result=self.summary(f,start)
                self.assertEqual(result["attempted_task_calls"],1)
                self.assertEqual(result["attempted_potential_write_calls"],0)
                self.assertIsNone(result["last_potential_write_at"])

    def test_codex_shell_requires_explicit_nonlogin_environment(self):
        for extras in ({},{"login":True},{"login":False,"tty":True}):
            with self.subTest(options=extras):
                f,start=self.fixture("codex")
                append_action(f,"functions.exec_command",dict(cmd=readonly_shell("ls -la /task/output"),**extras),at=LATE,end=LATE)
                self.assertEqual(self.summary(f,start)["attempted_potential_write_calls"],1)

    def test_single_rtk_proxy_template_is_readonly_for_both_native_cli_tools(self):
        command = "rtk proxy " + readonly_shell("ls -la /task/output; ls -la /task/output/subdir")
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                f,start = self.fixture(provider)
                name,args = ("Bash", {"command": command}) if provider == "claude" else (
                    "functions.exec_command", {"cmd": command, "login": False, "tty": False})
                append_action(f,name,args,at=LATE,end=LATE)
                result = self.summary(f,start,include_evidence=True)
                self.assertEqual(result["attempted_task_calls"],1)
                self.assertEqual(result["attempted_potential_write_calls"],0)
                self.assertEqual(result["actions"][0]["effect"],"READ_ONLY")

    def test_nested_rtk_or_other_outer_wrapper_cannot_prove_readonly(self):
        command = readonly_shell("ls -la /task/output")
        for prefix in ("rtk proxy rtk proxy ", "rtk ", "env rtk proxy ", "/tmp/rtk proxy ", "sudo "):
            with self.subTest(prefix=prefix):
                f,start = self.fixture()
                append_action(f,"Bash",{"command":prefix+command},at=LATE,end=LATE)
                self.assertEqual(self.summary(f,start)["attempted_potential_write_calls"],1)

    def test_codex_javascript_exec_is_unknown_even_with_readonly_command_text(self):
        f,start = self.fixture("codex")
        command = "rtk proxy " + readonly_shell("ls -la /task/output")
        append_action(f,"functions.exec",{"code":"await tools.exec_command("+json.dumps(
            {"cmd":command,"login":False,"tty":False})+")"},at=LATE,end=LATE)
        self.assertEqual(self.summary(f,start)["attempted_potential_write_calls"],1)

    def test_readonly_wrapper_rejects_environment_and_shell_program_escape(self):
        valid=readonly_shell("ls -la /task/output")
        commands=(valid.replace("PATH=/usr/bin:/bin","PATH=/tmp:/usr/bin:/bin"),
                  valid.replace("/bin/sh -f -c","/bin/sh -c"),
                  readonly_shell("ls -la /task/output; unknown /task/output"),
                  readonly_shell("ls -la /task/output$(touch /task/output/report)"),
                  valid+"; touch /task/output/report")
        for command in commands:
            with self.subTest(command=command):
                f,start=self.fixture()
                append_action(f,"Bash",{"command":command},at=LATE,end=LATE)
                self.assertEqual(self.summary(f,start)["attempted_potential_write_calls"],1)

    def test_semicolon_workload_is_new_policy_work_and_preserves_legacy_omission(self):
        f,start=self.fixture()
        command='python /controller/orchestration_workload.py --output-root /task/output; echo "EXIT=$?"'
        append_action(f,"Bash",{"command":command},at=LATE,end=LATE)
        old=self.summary(f,start,policy=False,allow_empty=True)
        self.assertEqual(old["attempted_task_calls"],0)
        result=self.summary(f,start)
        self.assertEqual(result["attempted_task_calls"],1)
        self.assertEqual(result["attempted_potential_write_calls"],1)
        self.assertEqual(result["last_potential_write_at"],LATE)

    def test_shell_write_unknown_and_mixed_control_cannot_hide_as_readonly(self):
        commands=(
            "ls -la /task/output > /task/output/report.md",
            readonly_shell("ls -la /task/output > /task/output/report.md"),
            readonly_shell("ls -la /task/output; touch /task/output/report.md"),
            "ls -la /task/nodes/inventory/preflight/ /task/nodes/inventory/; ls -la /task/output",
            "ls -la /task/output; touch /task/output/report.md",
            "ls -la /task/output && mystery /task/output",
            "ls -la /task/output$(touch /task/output/report.md)",
            "ls -la /task/output; echo cmux_bridge.py; touch /task/output/report.md",
            "python cmux_bridge.py submit-completion-callback --reconcile-only; touch /task/output/report.md",
            "unknown-tool /task/output",
            "python -c 'open(\"/task/output/report.md\",\"w\").write(\"bad\")'",
        )
        for command in commands:
            with self.subTest(command=command):
                f,start=self.fixture()
                append_action(f,"Bash",{"command":command},at=LATE,end=LATE)
                result=self.summary(f,start)
                self.assertEqual(result["attempted_potential_write_calls"],1)
                self.assertEqual(result["last_potential_write_at"],LATE)

    def test_native_writers_and_unknown_tools_are_potential_mutations(self):
        for name in ("Write","Edit","UnknownTool"):
            with self.subTest(tool=name):
                f,start=self.fixture()
                append_action(f,name,{"file_path":"/task/output/report.md"},at=LATE,end=LATE)
                result=self.summary(f,start)
                self.assertEqual(result["attempted_potential_write_calls"],1)
                self.assertEqual(result["last_potential_write_at"],LATE)

    def test_failure_cannot_be_used_as_readonly_or_success_evidence(self):
        for name,args in (("Read",{"file_path":"/task/output/report.md"}),
                          ("Bash",{"command":readonly_shell("ls -la /task/output")}),
                          ("Write",{"file_path":"/task/output/report.md"})):
            with self.subTest(tool=name):
                f,start=self.fixture()
                append_action(f,name,args,at=LATE,end=LATE,error=True,output="failed")
                result=self.summary(f,start,allow_empty=True)
                self.assertEqual(result["completed_task_calls"],0)
                self.assertEqual(result["failed_task_calls"],1)
                self.assertEqual(result["attempted_potential_write_calls"],1)
                self.assertEqual(result["last_potential_write_at"],LATE)

    def test_pending_or_missing_result_never_proves_readonly_completion(self):
        for output in (None,{"status":"pending"},"Script running with cell ID 42"):
            with self.subTest(output=output):
                f,start=self.fixture()
                append_action(f,"Bash",{"command":readonly_shell("ls -la /task/output")},at=STAMP,end=LATE,
                    output=output,complete=output is not None)
                with self.assertRaisesRegex(ContractError,"unfinished task tool call"):
                    self.summary(f,start,allow_empty=True)

    def test_incomplete_readonly_observation_keeps_potential_write_attempt(self):
        f,start=self.fixture()
        append_action(f,"Read",{"file_path":"/task/output/report.md"},at=STAMP,complete=False)
        result=self.summary(f,start,allow_empty=True,allow_incomplete=True,include_evidence=True)
        self.assertEqual(result["attempted_potential_write_calls"],1)
        self.assertEqual(result["completed_potential_write_calls"],0)
        self.assertEqual(result["actions"][0]["effect"],"UNKNOWN")
        self.assertEqual(result["pending_task_calls"],1)

    def test_failed_nested_result_keeps_possible_write_for_both_providers(self):
        for provider in ("claude","codex"):
            with self.subTest(provider=provider):
                f,start=self.fixture(provider)
                name,args=("Bash",{"command":readonly_shell("ls -la /task/output")}) if provider=="claude" else (
                    "functions.exec_command",{"cmd":readonly_shell("ls -la /task/output"),"login":False})
                append_action(f,name,args,at=STAMP,end=LATE,output={"content":[{"text":'{"exit_code":7}'}]})
                result=self.summary(f,start,allow_empty=True)
                self.assertEqual(result["failed_task_calls"],1)
                self.assertEqual(result["attempted_potential_write_calls"],1)
                self.assertEqual(result["last_potential_write_at"],LATE)

    def test_unknown_read_arguments_cannot_claim_readonly(self):
        f,start=self.fixture()
        append_action(f,"Read",{"file_path":"/task/output/report.md","command":"touch /task/output/report.md"},at=LATE,end=LATE)
        result=self.summary(f,start,include_evidence=True)
        self.assertEqual(result["attempted_potential_write_calls"],1)
        self.assertEqual(result["actions"][0]["effect"],"UNKNOWN")

    def test_missing_call_identity_rejects_new_policy_without_rewriting_legacy(self):
        for provider in ("claude","codex"):
            with self.subTest(provider=provider):
                f,start=self.fixture(provider)
                record=call_record(f,"Read",{"file_path":"/task/output/report.md"},"missing",STAMP)
                if provider=="claude":record["message"]["content"][0].pop("id")
                else:record["payload"].pop("call_id")
                f.append(record)
                self.assertEqual(self.summary(f,start,policy=False,allow_empty=True)["attempted_task_calls"],0)
                with self.assertRaises(ContractError):self.summary(f,start,allow_empty=True)

    def test_output_prefix_collision_does_not_match_own_scope(self):
        f,start=self.fixture()
        append_action(f,"Write",{"file_path":"/task/output-other/report.md"},at=LATE,end=LATE)
        result=self.summary(f,start,allow_empty=True)
        self.assertEqual(result["attempted_task_calls"],0)
        self.assertEqual(result["attempted_potential_write_calls"],0)

    def test_reconcile_without_output_scope_is_not_business_work(self):
        f,start=self.fixture()
        append_action(f,"Bash",{"command":"python /controller/cmux_bridge.py submit-completion-callback --task-pack /task/preflight/pack.json --reconcile-only"},at=LATE,end=LATE)
        result=self.summary(f,start,allow_empty=True)
        self.assertEqual(result["attempted_task_calls"],0)
        self.assertEqual(result["attempted_potential_write_calls"],0)


class OutputEffectLifecycleTests(unittest.TestCase):
    def fixture(self, metrics=None, *, only="review"):
        metrics = new_metrics() if metrics is None else metrics
        version = 2 if metrics == contracts.METRICS else 3
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        f=CampaignFixture(temp.name,version=version)
        f.plan["nodes"]=[n for n in f.plan["nodes"] if n["id"]==only]
        f.campaign["plan"]=write_json(f.root/"plan.json",f.plan)
        f.campaign["metrics"]=copy.deepcopy(metrics);f.save()
        return f

    def delegated(self,f):
        co=f.start();info=co.claim("review")
        f.prepare(co,"review");f.intent(co,"review");f.dispatch(co,"review")
        lane=co.nodes["review"]["executor"]
        f.work(info,lane)
        return co,info,f.clients[lane]

    def late_ls(self,client,info,*,error=False):
        root=info["output_root"]
        return append_action(client,"Bash",{"command":readonly_shell(f"ls -la {root}/../nodes/; ls -la {root}")},error=error)

    def accept(self,f,co,protocol):
        return f.accept(co,"review",f.candidate(co,"review",protocol))

    def settle(self,f,co,info,protocol):
        f.block(co,"review","checker rejection fixture")
        proof=f.failure(co,"review","callback_boundary",protocol=protocol,
            report=file_ref(Path(info["output_root"])/"report.md"))
        return co.settle_failure("review",proof)

    def test_new_task_scope_inspection_template_is_accepted_by_both_native_tools(self):
        f=self.fixture();co=f.start();info=co.claim("review")
        scope=task_scope(f.campaign,co.header,co.nodes["review"],info)
        marker="the bounded read-only shell form is exactly: "
        self.assertEqual(scope.count(marker),1)
        command=scope.split(marker,1)[1].split("\n",1)[0]
        self.assertTrue(command.startswith("rtk proxy /usr/bin/env -i "))
        self.assertIn("functions.exec JavaScript wrappers remain unknown",scope)
        for provider,lane in (("claude","claude_a"),("codex","codex")):
            with self.subTest(provider=provider):
                client=f.clients[lane];start=client.reader.snapshot()["offset"]
                name,args=("Bash",{"command":command}) if provider=="claude" else (
                    "exec_command",{"cmd":command,"login":False,"tty":False})
                append_action(client,name,args)
                result=client.reader.action_summary(start,client.reader.snapshot()["offset"],
                    task_id=info["task_id"],output_root=info["output_root"],effect_policy=effect_policy())
                self.assertEqual(result["completed_task_calls"],1)
                self.assertEqual(result["attempted_potential_write_calls"],0)

    def test_legacy_task_scopes_do_not_receive_new_readonly_exemption_instructions(self):
        for metrics,_,_ in LEGACY:
            with self.subTest(metrics_version=metrics["version"]):
                f=self.fixture(metrics);co=f.start();info=co.claim("review")
                scope=task_scope(f.campaign,co.header,co.nodes["review"],info)
                self.assertNotIn("the bounded read-only shell form is exactly",scope)
                self.assertNotIn("exempt from the output-write boundary",scope)

    def test_new_metrics_accept_read_and_isolated_ls_after_callback(self):
        f=self.fixture();co,info,client=self.delegated(f);protocol=f.callback(co,"review")
        append_action(client,"Read",{"file_path":info["output_root"]+"/report.md"})
        self.late_ls(client,info)
        self.accept(f,co,protocol);f.disarm(co,"review")
        result=finish_run(co)
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(canonical(verify_run(f.ref,co.spec["run_id"],fixtures=True)),canonical(result))

    def test_all_three_old_metrics_retain_original_readonly_callback_rejection(self):
        for metrics,_,_ in LEGACY:
            with self.subTest(version=metrics["version"]):
                f=self.fixture(metrics);co,info,client=self.delegated(f);protocol=f.callback(co,"review")
                self.late_ls(client,info)
                with self.assertRaisesRegex(ContractError,"writes continued after callback"):
                    self.accept(f,co,protocol)
                self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_mixed_unknown_write_and_failed_readonly_after_callback_reject(self):
        for kind in ("write","mixed","unknown","failed_ls"):
            with self.subTest(kind=kind):
                f=self.fixture();co,info,client=self.delegated(f);protocol=f.callback(co,"review")
                root=info["output_root"]
                if kind=="write": append_action(client,"Write",{"file_path":root+"/result.json"})
                elif kind=="mixed": append_action(client,"Bash",{"command":f"ls -la {root}; echo cmux_bridge.py; touch {root}/result.json"})
                elif kind=="unknown": append_action(client,"Bash",{"command":f"unknown-tool {root}"})
                else:self.late_ls(client,info,error=True)
                with self.assertRaisesRegex(ContractError,"writes continued after callback"):
                    self.accept(f,co,protocol)
                self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_write_started_before_callback_but_completed_after_still_rejects(self):
        f=self.fixture();co,info,client=self.delegated(f)
        key,_=append_action(client,"Write",{"file_path":info["output_root"]+"/report.md"},complete=False)
        protocol=f.callback(co,"review")
        client.append(result_record(client,key,utc_now()))
        with self.assertRaisesRegex(ContractError,"writes continued after callback"):
            self.accept(f,co,protocol)

    def test_pending_readonly_at_callback_closeout_still_rejects(self):
        f=self.fixture();co,info,client=self.delegated(f);protocol=f.callback(co,"review")
        append_action(client,"Read",{"file_path":info["output_root"]+"/report.md"},complete=False)
        with self.assertRaisesRegex(ContractError,"unfinished task tool call"):
            self.accept(f,co,protocol)

    def test_old_failure_boundaries_still_reject_readonly_after_callback(self):
        for metrics,_,_ in LEGACY:
            with self.subTest(version=metrics["version"]):
                f=self.fixture(metrics);co,info,client=self.delegated(f);protocol=f.callback(co,"review")
                append_action(client,"Read",{"file_path":info["output_root"]+"/report.md"})
                with self.assertRaisesRegex(ContractError,"work continued after callback"):
                    self.settle(f,co,info,protocol)

    def test_callback_failure_settlement_allows_completed_readonly(self):
        f=self.fixture();co,info,client=self.delegated(f);protocol=f.callback(co,"review")
        self.late_ls(client,info)
        self.settle(f,co,info,protocol);f.disarm(co,"review")
        self.assertEqual(finish_run(co)["status"],"FAILED_PRESERVED")

    def test_callback_failure_settlement_rejects_write_and_failed_read(self):
        for name,error in (("Write",False),("Read",True)):
            with self.subTest(name=name):
                f=self.fixture();co,info,client=self.delegated(f);protocol=f.callback(co,"review")
                append_action(client,name,{"file_path":info["output_root"]+"/report.md"},error=error)
                with self.assertRaisesRegex(ContractError,"work continued after callback"):
                    self.settle(f,co,info,protocol)
                self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_new_terminal_closeout_allows_readonly_for_local_and_delegated(self):
        for key in ("local","review"):
            with self.subTest(node=key):
                f=self.fixture(only=key)
                if key=="review":
                    co,info,client=self.delegated(f)
                    self.accept(f,co,f.callback(co,"review"));f.disarm(co,"review")
                    self.late_ls(client,info)
                else:
                    co=f.start();info=co.claim(key);f.work(info)
                    f.accept(co,key,f.candidate(co,key))
                    append_action(f.clients["codex"],"functions.exec_command",{"cmd":readonly_shell("ls -la "+info["output_root"]),"login":False})
                self.assertEqual(finish_run(co)["status"],"PASS")

    def test_terminal_closeout_unknown_write_and_failed_readonly_preserve_rejection(self):
        for kind in ("unknown","write","failed_ls"):
            with self.subTest(kind=kind):
                f=self.fixture();co,info,client=self.delegated(f)
                self.accept(f,co,f.callback(co,"review"));f.disarm(co,"review")
                if kind=="write":append_action(client,"Write",{"file_path":info["output_root"]+"/result.json"})
                elif kind=="failed_ls":self.late_ls(client,info,error=True)
                else:append_action(client,"Bash",{"command":"unknown-tool "+info["output_root"]})
                with self.assertRaisesRegex(ContractError,"continued after terminal boundary"):
                    finish_run(co)

    def test_real_r32_bare_ls_remains_unknown_without_environment_proof(self):
        f=self.fixture();co,info,client=self.delegated(f);protocol=f.callback(co,"review")
        root=info["output_root"]
        append_action(client,"Bash",{"command":f"ls -la {root}/../nodes/; ls -la {root}"})
        with self.assertRaisesRegex(ContractError,"writes continued after callback"):
            self.accept(f,co,protocol)

    def test_all_three_old_metrics_reject_reads_after_terminal(self):
        for metrics,_,_ in LEGACY:
            with self.subTest(version=metrics["version"]):
                f=self.fixture(metrics);co,info,client=self.delegated(f)
                self.accept(f,co,f.callback(co,"review"));f.disarm(co,"review")
                append_action(client,"Read",{"file_path":info["output_root"]+"/report.md"})
                with self.assertRaisesRegex(ContractError,"continued after terminal boundary"):
                    finish_run(co)

    def test_failed_node_terminal_still_checks_output_effects(self):
        for name in ("Read","Write"):
            with self.subTest(tool=name):
                f=self.fixture();co,info,client=self.delegated(f);protocol=f.callback(co,"review")
                self.settle(f,co,info,protocol);f.disarm(co,"review")
                append_action(client,name,{"file_path":info["output_root"]+"/report.md"})
                if name=="Read":self.assertEqual(finish_run(co)["status"],"FAILED_PRESERVED")
                else:
                    with self.assertRaisesRegex(ContractError,"continued after terminal boundary|work continued after callback"):
                        finish_run(co)

    def zero_input_attempt(self,f,co):
        f.prepare(co,"review");f.intent(co,"review")
        info=co.state()[1]["review"];lane=co.nodes["review"]["executor"];client=f.clients[lane]
        notice=Path(info["prepared"]["notice"]["path"]).read_text();epoch=parse_time(utc_now()).timestamp()
        identity=delivery_identity(client.session,f.clients["codex"].session)
        binding=dict(task_id=info["task_id"],marker=info["task_id"],payload_sha256=digest(notice.encode()),
                     task_pack_sha256=info["prepared"]["pack"]["sha256"],identity=identity)
        native=dict(schema="native-delivery-v1",identity=identity,provider="claude",
                    session_id=client.session["session_id"],process=client.session["process"],
                    tty=client.session["tty"],transcript=client.reader.snapshot(),bound_at_epoch=epoch,
                    payload_sha256=digest(notice.encode()))
        attempt=write_json(co.root/"nodes/review/task-dispatch/attempt-0001.json",dict(phase="NO_INPUT",
                    binding=binding,native_binding=native,events=[],started_at_epoch=epoch,
                    ended_at_epoch=epoch,error="offline zero-input fixture"))
        uncertainty=write_json(co.root/"nodes/review/uncertainty.json",dict(task_id=info["task_id"],reason="no input"))
        co.transition("review",State.UNCERTAIN,{"uncertainty":uncertainty});f.block(co,"review")
        return attempt,client

    def test_no_business_input_still_rejects_read_activity_for_every_contract(self):
        for metrics in [m for m,_,_ in LEGACY]+[new_metrics()]:
            with self.subTest(version=metrics["version"]):
                f=self.fixture(metrics);co=f.start();info=co.claim("review")
                attempt,client=self.zero_input_attempt(f,co)
                append_action(client,"Read",{"file_path":info["output_root"]+"/report.md"})
                proof=f.failure(co,"review","no_business_input",no_input_attempts=[attempt])
                with self.assertRaisesRegex(ContractError,"business action contradicts zero-input claim"):
                    co.settle_failure("review",proof)
                self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])

    def test_new_metric_schema_cannot_be_partially_opted_in(self):
        f=self.fixture()
        self.assertEqual(contracts.validate_campaign(f.ref,fixtures=True)["metrics"],new_metrics())
        f.campaign["metrics"].pop("output_effect_policy");f.save()
        with self.assertRaisesRegex(ContractError,"measurement definitions"):
            contracts.validate_campaign(f.ref,fixtures=True)


if __name__=="__main__":
    unittest.main()
