"""Offline boundary tests; these fixtures are not live adoption evidence."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import uuid

from orchestration_common import ContractError, file_ref, write_json
from upgrade_client_isolation import LANES
import upgrade_live_entry as entry
import upgrade_live_inspect as subject
from upgrade_startup_contract import readiness_command, readiness_prompt


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.controller = str(self.root / "controller")
        self.workspace = str(uuid.uuid4())
        self.spec = dict(controller=self.controller, pins={
            "python": {"path": "/pinned/python"}, "rtk": {"path": "/pinned/rtk"},
            "codex": {"path": "/pinned/codex"}, "claude": {"path": "/pinned/claude"}},
            clients={lane: {"native_root": str(self.root / lane)} for lane in LANES})
        self.startup = write_json(self.root / "startup.json", {"offline_fixture": True})
        self.binding = dict(state_root=str(self.root))

    def fixture(self, lane):
        provider = "codex" if lane == "codex" else "claude"
        sid = str(uuid.uuid4())
        session = dict(provider=provider, session_id=sid,
                       transcript=dict(path=str(self.root / (sid + ".jsonl"))))
        command = readiness_command(self.controller, "/pinned/python", "/pinned/rtk", lane)
        prompt = readiness_prompt(self.controller, "/pinned/python", "/pinned/rtk", lane)
        arguments = ("text(await tools.exec_command(" + json.dumps({"cmd": command, "login": False}) + "));\n"
                     if lane == "codex" else {"command": command})
        call = dict(id="readiness-call", name="functions.exec" if lane == "codex" else "Bash",
                    arguments=arguments, startup=self.startup)
        probe = dict(status="UPGRADE_READINESS_TOOL_COMPLETED", lane=lane,
            native_root=self.spec["clients"][lane]["native_root"], controller=self.controller,
            adoption_proven=False, input_operations=0)
        if lane == "codex":
            rows = [
                {"type": "turn_context", "payload": {"model": "frozen-model"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": prompt}]}},
                {"type": "response_item", "payload": {"type": "custom_tool_call",
                    "name": call["name"], "input": arguments, "call_id": call["id"]}},
                {"type": "response_item", "payload": {"type": "custom_tool_call_output",
                    "call_id": call["id"], "output": "Script completed\nOutput:\n" +
                    json.dumps({"exit_code": 0, "output": json.dumps(probe)})}},
            ]
        else:
            rows = [
                {"type": "user", "sessionId": sid, "message": {"role": "user", "content": prompt}},
                {"type": "assistant", "sessionId": sid, "message": {"role": "assistant",
                    "model": "frozen-model", "content": [{"type": "tool_use", "id": call["id"],
                        "name": "Bash", "input": arguments}]}},
                {"type": "user", "sessionId": sid, "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(probe)}]}},
            ]
        records = [(dict(path=session["transcript"]["path"], offset=100 * n, length=100), row)
                   for n, row in enumerate(rows)]
        hook = str(Path(self.controller) / "scripts/observer.py")
        hook_command = shlex.join(["/pinned/python", "-I", "-B",
            str(Path(self.controller) / "scripts/cmux_workflow_advisory.py"), "--hook", hook])
        config = write_json(self.root / (lane + "-hooks.json"), {"hooks": {
            event: [{"hooks": [{"type": "command", "command": hook_command}]}]
            for event in ("PreToolUse", "PostToolUse")}})
        self.spec["pins"]["codex/hooks.json" if lane == "codex" else lane + "/settings.json"] = config
        launch = dict(pid=20, birth=[1, 2])
        hooks = []
        for event in ("PreToolUse", "PostToolUse"):
            value = dict(schema="upgrade-automatic-hook-observation-v2", lane=lane,
                startup=self.startup, adoption_proven=False, direct_client=lane, hook=hook,
                configuration=config, command=hook_command, parents=[dict(pid=20, ppid=1,
                    birth=[1, 2], executable="/pinned/" + provider)], payload=dict(
                    session_id=sid, transcript_path=session["transcript"]["path"],
                    hook_event_name=event, tool_use_id=call["id"], tool_name=call["name"],
                    tool_input=arguments))
            path = self.root / "hook-observations" / lane / (event + ".json")
            write_json(path, value)
            hooks.append((path, value))
        return session, records, call, hooks, {lane: launch}, probe

    def test_complete_native_tool_and_automatic_pre_post_for_each_provider(self):
        for lane in ("codex", "claude_a"):
            with self.subTest(lane=lane):
                session, rows, call, hooks, launches, probe = self.fixture(lane)
                with patch.object(subject, "native_records", return_value=iter(rows)):
                    result = subject.readiness_evidence(None, session, lane, self.spec,
                                                       self.startup, self.binding, launches)
                self.assertEqual(result["call_id"], call["id"])
                self.assertEqual(result["model"], "frozen-model")
                self.assertEqual(len(result["hooks"]["PreToolUse"]), 1)

    def test_prose_comment_failure_or_pending_result_is_not_success(self):
        session, rows, call, hooks, launches, probe = self.fixture("codex")
        command = readiness_command(self.controller, "/pinned/python", "/pinned/rtk", "codex")
        self.assertTrue(subject.command_in_tool(call["arguments"], command))
        for text in ("// " + call["arguments"], json.dumps(call["arguments"]),
                     call["arguments"] + call["arguments"]):
            self.assertFalse(subject.command_in_tool(text, command))
        for output in (probe["status"], {"exit_code": 1, "output": json.dumps(probe)},
                       {"session_id": 9, "output": json.dumps(probe)},
                       "Process exited with code 2\n" + json.dumps(probe)):
            self.assertFalse(subject.completed_probe(output, "codex", self.spec))

    def test_manual_hook_wrong_session_pid_birth_config_or_call_fails(self):
        session, rows, call, hooks, launches, _ = self.fixture("claude_a")
        mutations = [lambda v: v.update(direct_client=None),
            lambda v: v["parents"][-1].update(pid=99),
            lambda v: v["parents"][-1].update(birth=[9, 9]),
            lambda v: v["payload"].update(session_id=str(uuid.uuid4())),
            lambda v: v["payload"].update(transcript_path="/copied/transcript.jsonl"),
            lambda v: v["payload"].update(tool_use_id="other-call"),
            lambda v: v.update(configuration={"path": "/global/hooks.json"})]
        for mutate in mutations:
            value = copy.deepcopy(hooks[0][1])
            mutate(value)
            self.assertFalse(subject.hook_matches(value, call, session, "claude_a", self.spec, launches))
        hooks[1][0].unlink()
        with patch.object(subject, "native_records", return_value=iter(rows)):
            with self.assertRaisesRegex(ContractError, "AUTOMATIC_PRE_POST_HOOKS"):
                subject.readiness_evidence(None, session, "claude_a", self.spec,
                                           self.startup, self.binding, launches)

    def test_replaced_or_partial_native_log_rejected(self):
        session, _, _, _, _, _ = self.fixture("claude_a")
        path = Path(session["transcript"]["path"])
        path.write_bytes(b'{"type":"user"}\n')
        session["transcript"].update(at_record_boundary=True, offset=path.stat().st_size)
        native = types.SimpleNamespace(_open=lambda p: open(p, "rb"), MAX_RECORD=1024,
                                       _check_file=lambda *a: (_ for _ in ()).throw(ValueError("replaced")))
        with self.assertRaisesRegex(ValueError, "replaced"):
            list(subject.native_records(native, session))
        session["transcript"]["at_record_boundary"] = False
        with self.assertRaisesRegex(ContractError, "NATIVE_EOF_INCOMPLETE"):
            list(subject.native_records(native, session))


class InspectorEntryTests(unittest.TestCase):
    def test_inspect_is_read_only_and_does_not_fall_through_to_client(self):
        with patch.object(entry, "load_startup", return_value=({}, {}, {})), \
             patch.object(entry, "start_client") as client, patch.object(entry, "launch") as launch, \
             patch.object(subject, "inspect", return_value={"ready": False, "input_operations": 0}) as inspect, \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(entry.main(["experiment-inspect", "--startup", "/fixture", "--startup-sha256", "x"]), 0)
        inspect.assert_called_once()
        client.assert_not_called()
        launch.assert_not_called()

    def test_cmux_wrapper_rejects_all_input_commands(self):
        bridge = subject.ReadOnlyCmux("/pinned/cmux")
        with patch.object(subject.subprocess, "run") as run:
            for args in (("send", "--text", "hi"), ("new-workspace",), ("tree",)):
                with self.assertRaisesRegex(ContractError, "METADATA_ONLY"):
                    bridge._run(*args)
            run.assert_not_called()


class WorkspaceResponseTests(unittest.TestCase):
    def setUp(self):
        self.workspace = str(uuid.uuid4())
        self.other = str(uuid.uuid4())
        self.tree = {"windows": [{"workspaces": [
            {"id": self.workspace.upper(), "ref": "workspace:42", "panes": []},
            {"id": self.other, "ref": "workspace:43", "panes": []}]}]}

    def bridge(self, tree=None):
        bridge = subject.ReadOnlyCmux("/fixture/cmux")
        bridge.snapshot = json.dumps(self.tree if tree is None else tree)
        return bridge

    def test_original_ref_uuid_and_structured_response_resolve_exactly(self):
        for response in ("OK workspace:42\n", "OK " + self.workspace.upper() + "\n", self.workspace,
                         json.dumps({"workspace_id": self.workspace, "workspace_ref": "workspace:42"}),
                         json.dumps({"id": self.workspace, "ref": "workspace:42"})):
            with self.subTest(response=response):
                self.assertEqual(subject.launch_workspace(response, self.bridge()), self.workspace)

    def test_mismatched_ambiguous_missing_and_substring_only_responses_rejected(self):
        responses = ["OK workspace:4", "OK workspace:42\nOK workspace:43", "prefix " + self.workspace,
            'warning: ' + self.workspace + '\nOK workspace:42', "OK workspace:42 extra", "", None,
            json.dumps({"workspace_id": self.other, "workspace_ref": "workspace:42"}),
            json.dumps({"workspace_id": self.workspace, "id": self.workspace}),
            json.dumps({"workspace_ref": True}), '{"workspace_ref":"workspace:42","workspace_ref":"workspace:43"}',
            json.dumps({"message": self.workspace})]
        for response in responses:
            with self.subTest(response=response), self.assertRaises((ContractError, ValueError)):
                subject.launch_workspace(response, self.bridge())
        for duplicate in ({"id": self.other, "ref": "workspace:42"},
                          {"id": self.workspace, "ref": "workspace:44"}):
            tree = copy.deepcopy(self.tree)
            tree["windows"][0]["workspaces"].append(duplicate)
            with self.assertRaisesRegex(ContractError, "NOT_UNIQUE"):
                subject.launch_workspace("OK workspace:42", self.bridge(tree))

    def test_workspace_and_surface_use_one_fresh_snapshot(self):
        bridge = subject.ReadOnlyCmux("/fixture/cmux")
        with patch.object(subject.subprocess, "run", return_value=types.SimpleNamespace(stdout=json.dumps(self.tree))) as run:
            self.assertEqual(subject.launch_workspace("OK workspace:42", bridge), self.workspace)
            self.assertEqual(subject.launch_workspace(self.workspace, bridge), self.workspace)
            bridge._run("tree", "--all", "--json", "--id-format", "both")
            self.assertEqual(run.call_count, 1)


class InspectorFlowTests(unittest.TestCase):
    """Exercise the real inspection wiring with inert native-process fixtures."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace, self.other = str(uuid.uuid4()), str(uuid.uuid4())
        self.startup = write_json(self.root / "spec.json", {"offline_fixture": True})
        self.spec = dict(pins={"cmux": {"path": "/fixture/cmux"}, "codex": {"path": "/fixture/codex"}},
            clients={}, protected_surface_uuids=[], stop_path=str(self.root / "stop"),
            rpc={"path": str(self.root / "socket"), "argv": ["/fixture/codex", "server"]})
        self.binding = dict(state_root=str(self.root), isolation={"denied_probes": [], "write_probes": []})
        self.tree = {"windows": [{"workspaces": [
            {"id": self.workspace, "ref": "workspace:42", "panes": []},
            {"id": self.other, "ref": "workspace:43", "panes": []}]}]}
        self.clients, self.launches = {}, {}
        for index, lane in enumerate(LANES):
            surface, pane, sid = (str(uuid.uuid4()) for _ in range(3))
            provider = "codex" if lane == "codex" else "claude"
            self.spec["clients"][lane] = dict(native_root=str(self.root / lane), session_id=sid)
            self.clients[surface] = dict(pid=100 + index, birth=[1, index], executable="/fixture/" + provider,
                file_identity={}, session_id=sid, lane=lane)
            launch = dict(startup=self.startup, surface_uuid=surface, workspace_uuid=self.workspace,
                          pid=100 + index, birth=[1, index])
            self.launches[lane] = launch
            write_json(self.root / "startup" / (lane + ".json"), launch)
            self.tree["windows"][0]["workspaces"][0]["panes"].append(dict(id=pane, surfaces=[dict(
                id=surface, tty="ttys" + str(index), type="terminal", dock_scope="workspace")]))
        self.server = dict(pid=999, birth=[1, 99], executable="/fixture/codex", argv=self.spec["rpc"]["argv"])
        write_json(self.root / "startup/codex-server.json", dict(self.server, startup=self.startup))
        write_json(self.root / "startup/intent.json", dict(startup=self.startup, spawn=True, spawn_authorized=True))
        write_json(self.root / "startup/result.json", dict(startup=self.startup, returncode=0,
                   workspace_creation_attempts=1, stdout="OK workspace:42\n"))

    def inspect_fixture(self):
        import cmux_daemon_identity as identity
        import cmux_native_delivery as native
        # The RPC adapter belongs to the pinned base controller and is copied
        # during materialization; this source-level wiring test uses metadata only.
        rpc = types.SimpleNamespace(DaemonRPC=Mock())
        sid = self.spec["clients"]["codex"]["session_id"]
        transcript = str(self.root / (sid + ".jsonl"))
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, cmux_codex_rpc=rpc))
            cmux = stack.enter_context(patch.object(subject.subprocess, "run",
                return_value=types.SimpleNamespace(stdout=json.dumps(self.tree))))
            stack.enter_context(patch.object(native, "_target_process",
                side_effect=lambda row: self.clients[row["target_surface_uuid"]]))
            stack.enter_context(patch.object(native, "_live_session", side_effect=lambda p, provider: p["session_id"]))
            stack.enter_context(patch.object(native, "_transcript_path", side_effect=lambda provider, s, p: self.root / (s + ".jsonl")))
            stack.enter_context(patch.object(native, "_pin_file", side_effect=lambda p, *a: {"path": str(p)}))
            stack.enter_context(patch.object(subject, "process_root", side_effect=lambda provider, p: self.root / p["lane"]))
            stack.enter_context(patch.object(subject, "require_kernel_boundary", return_value={"offline_fixture": True}))
            stack.enter_context(patch.object(subject, "binding_boundary", return_value=None))
            readiness = stack.enter_context(patch.object(subject, "readiness_evidence",
                return_value={"model": "fixture-model", "model_record": {"offline_fixture": True}}))
            stack.enter_context(patch.object(identity, "process", return_value=self.server))
            client = stack.enter_context(patch.object(rpc, "DaemonRPC")).return_value
            client.socket_identity = {"offline_fixture": True}
            client.request.side_effect = [{"thread": {"id": sid, "path": transcript,
                "canAcceptDirectInput": True}}, {"data": []}]
            result = subject.inspect(self.startup, self.spec, self.binding)
            self.assertEqual(cmux.call_count, 1)
            self.assertEqual(result["input_operations"], 0)
            self.assertTrue(all(call.args[0] in ("thread/read", "thread/turns/list")
                                for call in client.request.call_args_list))
            return result, [call.args[2] for call in readiness.call_args_list]

    def test_ref_response_reaches_each_readiness_and_metadata_gate(self):
        result, lanes = self.inspect_fixture()
        self.assertTrue(result["ready"], result["errors"])
        self.assertEqual(set(lanes), set(LANES))
        self.assertEqual({s["workspace_uuid"] for s in result["sessions"].values()}, {self.workspace})

    def test_workspace_movement_and_pid_or_birth_changes_fail_before_readiness(self):
        lane = "claude_a"
        target = self.launches[lane]["surface_uuid"]
        original_tree, original_client = copy.deepcopy(self.tree), copy.deepcopy(self.clients[target])
        for mutation in ("workspace", "pid", "birth"):
            with self.subTest(mutation=mutation):
                self.tree, self.clients[target] = copy.deepcopy(original_tree), copy.deepcopy(original_client)
                if mutation == "workspace":
                    workspaces = self.tree["windows"][0]["workspaces"]
                    moved = next(p for p in workspaces[0]["panes"] if p["surfaces"][0]["id"] == target)
                    workspaces[0]["panes"].remove(moved)
                    workspaces[1]["panes"].append(moved)
                else:
                    self.clients[target][mutation] = 777 if mutation == "pid" else [9, 9]
                result, lanes = self.inspect_fixture()
                self.assertFalse(result["ready"])
                self.assertEqual(result["errors"][lane], "LAUNCH_WORKSPACE_CHANGED" if mutation == "workspace"
                                 else "ORIGINAL_CLIENT_PID_OR_BIRTH_CHANGED")
                self.assertNotIn(lane, result["sessions"])
                self.assertNotIn(lane, lanes)


if __name__ == "__main__":
    unittest.main()
