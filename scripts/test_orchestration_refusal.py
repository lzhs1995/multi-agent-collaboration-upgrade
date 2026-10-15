"""Historical refusal diagnostics use real offline journals and pinned trees.

No controller, interpreter, shell command or sender is executed. Synthetic
registered source exercises the positive path without trusting local releases.
"""
import copy
import io
import json
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, canonical, digest, file_ref, parse_time, tree_manifest
from orchestration_input_history import sender_history
from orchestration_refusal import ERROR, REPLY, HistoricalRefusal
from test_orchestration_inputs import RECEIVER, SENDER, TARGET
from test_orchestration_native import NativeFixture


class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "release/source"
        scripts = self.source / "scripts"
        scripts.mkdir(parents=True)
        self.modules = {}
        for name in ("successor_rebind_cli", "successor_native_delivery", "dependency"):
            path = scripts / (name + ".py")
            raw = ('"""Offline source fixture: ' + name + '."""\n').encode()
            path.write_bytes(raw)
            path.chmod(0o755 if name == "successor_rebind_cli" else 0o644)
            self.modules[name] = digest(raw)
        python = self.root / "python3"
        python.write_bytes(b"offline interpreter identity; never executed\n")
        self.context = dict(controller=tree_manifest(self.source), python=file_ref(python))
        self.release = dict(files={key: dict(sha256=value["sha256"], bytes=value["bytes"],
                executable=bool(value["mode"] & 0o111)) for key, value in self.context["controller"]["files"].items()})
        self.mapping = dict(entrypoint="scripts/successor_rebind_cli.py", site="scripts/successor_native_delivery.py:298",
                            modules=self.modules, review_sha256="a" * 64)
        self.registry = {}
        self.publish_release()
        mapping_patch = patch("orchestration_refusal.MAPPINGS", self.registry)
        mapping_patch.start()
        self.addCleanup(mapping_patch.stop)
        self.body = self.root / "absent-body.txt"
        self.artifact = self.root / "absent-artifact.json"
        self.argv = ["rtk", "proxy", str(python), "-B", str(scripts / "successor_rebind_cli.py"), "submit",
                     "--artifact", str(self.artifact), "--text-file", str(self.body), "--marker", "fixture-refusal"]
        self.args = dict(cmd=shlex.join(self.argv), login=False, workdir=str(self.root), max_output_tokens=1600)
        self.journal_count = 0

    def publish_release(self):
        path = self.source.parent / "MANIFEST.json"
        path.write_bytes(canonical(self.release))
        self.registry.clear()
        self.registry[file_ref(path)["sha256"]] = self.mapping

    def records(self, *, args=None):
        args = self.args if args is None else args
        turn = "02a1245b-673b-7d62-a332-f600c3878e4a"
        meta = dict(turn_id=turn)
        epoch = int(parse_time("2026-10-14T00:00:00Z").timestamp() * 1000)
        call = dict(type="response_item", timestamp="2026-10-14T00:00:00Z", payload=dict(
            type="custom_tool_call", call_id="refusal-call", name="exec",
            input="text(await tools.exec_command(" + json.dumps(args) + "));\n",
            internal_chat_message_metadata_passthrough=meta))
        execution = dict(type="event_msg", timestamp="2026-10-14T00:00:02Z", payload=dict(
            type="item_completed", thread_id=SENDER["session_id"], turn_id=turn,
            started_at_ms=epoch + 100, completed_at_ms=epoch + 1000,
            item=dict(type="CommandExecution", id="execution-id", process_id="12345",
                      command=["/bin/zsh", "-c", args["cmd"]], cwd=self.root.as_uri(),
                      source="unified_exec_startup", status="failed", exit_code=2,
                      stdout=REPLY, stderr="", aggregated_output=REPLY, formatted_output=REPLY)))
        result = dict(type="response_item", timestamp="2026-10-14T00:00:03Z", payload=dict(
            type="custom_tool_call_output", call_id="refusal-call",
            internal_chat_message_metadata_passthrough=meta,
            output=[dict(type="text", text="Script completed\nWall time 0.7 seconds\nOutput:\n"),
                    dict(type="text", text=json.dumps(dict(exit_code=2, output=REPLY)))]))
        return copy.deepcopy([call, execution, result])

    def journal(self, records=None):
        self.journal_count += 1
        root = self.root / ("journal-" + str(self.journal_count))
        root.mkdir()
        client = NativeFixture(root, "codex")
        start = client.reader.snapshot()["offset"]
        for record in self.records() if records is None else records:
            client.append(record)
        return client, start, client.reader.snapshot()["offset"]

    def observe(self, reader, start, end):
        return sender_history(reader, start, end, RECEIVER, SENDER, (TARGET,),
                              state_root=self.root / "absent-state", refusal_context=self.context)

    def diagnostic(self, records=None):
        client, start, end = self.journal(records)
        with self.assertRaises(HistoricalRefusal) as caught:
            self.observe(client.reader, start, end)
        value = caught.exception.diagnostic
        self.assertEqual(value["status"], "BLOCKED")
        for key in ("settlement_authorized", "input_retry_authorized"):
            self.assertIs(value[key], False)
        for key in ("original_invocation_zero_input", "startup_environment", "process_zero_persistent_writes", "prior_marker_input"):
            self.assertEqual(value[key], "UNKNOWN")
        for key in ("invocation_body_identity", "invocation_artifact_identity"):
            self.assertEqual(value[key], "NOT_PROVEN")
        self.assertEqual(value["diagnostic_input_operations"], 0)
        self.assertEqual(value["diagnostic_persistent_writes"], 0)
        return value

    def unmatched(self, records=None):
        result = self.diagnostic(records)
        self.assertEqual(result["mapping_status"], "UNMATCHED", result)
        return result

    def test_positive_registered_manifest_schema_matches_but_cannot_settle(self):
        value = self.diagnostic()
        self.assertEqual(value["mapping_status"], "MATCHED_STATIC_ONLY", value)
        self.assertEqual(value["controller_zero_input"], "CONDITIONAL")
        self.assertEqual(value["controller_zero_persistent_writes"], "CONDITIONAL")
        self.assertEqual(len(value["source"]["closure"]), 3)
        self.assertEqual(value["native"]["native_tool"]["arguments"], self.args)
        self.assertEqual(value["native"]["command"], ["/bin/zsh", "-c", self.args["cmd"]])
        self.assertEqual(value["native"]["shell"][0]["argv"], self.argv)
        self.assertFalse(self.body.exists())
        self.assertFalse(self.artifact.exists())

    def test_direct_registered_interpreter_and_literal_wait_are_supported(self):
        args = dict(self.args, cmd=shlex.join(self.argv[2:] + ["--wait-seconds", "30.5"]))
        self.assertEqual(self.diagnostic(self.records(args=args))["mapping_status"], "MATCHED_STATIC_ONLY")

    def test_current_body_or_artifact_cannot_supply_historical_identity(self):
        import orchestration_common as common
        self.body.write_text("a present-day body is not invocation-time evidence")
        self.artifact.write_text('{"current": "not historical"}')
        original = common.read_regular

        def guarded(path, *args, **kwargs):
            self.assertNotIn(Path(path).resolve(), (self.body, self.artifact))
            return original(path, *args, **kwargs)

        with patch.object(common, "read_regular", side_effect=guarded):
            self.assertEqual(self.diagnostic()["mapping_status"], "MATCHED_STATIC_ONLY")

    def test_caller_assertions_cannot_attest_startup_or_authorize_settlement(self):
        for key, value in (("startup_environment", "PROVEN"), ("settlement_authorized", True),
                           ("invocation_body_sha256", "a" * 64)):
            with self.subTest(key=key):
                self.context[key] = value
                self.unmatched()
                del self.context[key]

    def test_changed_wrappers_interpreter_flags_entrypoint_and_argv_rejected(self):
        variants = [
            ["env", "LANG=C"] + self.argv[2:],
            ["rtk"] + self.argv[2:],
            self.argv[:3] + ["-I", "-B"] + self.argv[4:],
            self.argv[:3] + ["-B", "-X", "dev"] + self.argv[4:],
            self.argv[:2] + ["python3"] + self.argv[3:],
            self.argv[:4] + [str(self.root / "other/successor_rebind_cli.py")] + self.argv[5:],
            self.argv + ["--unknown", "value"],
            self.argv + ["--wait-seconds", "NaN"],
        ]
        for argv in variants:
            with self.subTest(argv=argv):
                self.unmatched(self.records(args=dict(self.args, cmd=shlex.join(argv))))
        args = dict(self.args, cmd=shlex.join(["zsh", "-c", self.args["cmd"]]))
        self.unmatched(self.records(args=args))

    def test_unknown_or_missing_tool_options_do_not_match(self):
        for field, value in (("login", True), ("tty", True), ("shell", "/bin/bash"),
                             ("unrecognized", False), ("workdir", str(self.root / "different"))):
            with self.subTest(field=field):
                self.unmatched(self.records(args=dict(self.args, **{field: value})))
        args = dict(self.args)
        del args["login"]
        self.unmatched(self.records(args=args))

    def test_missing_duplicate_or_out_of_order_original_records_rejected(self):
        call, execution, result = self.records()
        variants = [[call], [call, result], [call, execution], [call, execution, result, result],
                    [call, call, execution, result], [call, execution, execution, result],
                    [result, call, execution], [execution, call, result], [call, result, execution]]
        for index, records in enumerate(variants):
            with self.subTest(case=index):
                self.unmatched(copy.deepcopy(records))

    def test_other_call_same_command_or_duplicate_execution_id_is_ambiguous(self):
        call, execution, result = self.records()
        other = copy.deepcopy(call)
        other["payload"]["call_id"] = "another-call"
        self.unmatched([call, other, execution, result])
        other = copy.deepcopy(execution)
        other["payload"]["thread_id"] = "another-thread"
        self.unmatched([call, execution, other, result])

    def test_wrong_call_result_turn_type_or_identity_rejected(self):
        for position, field, value in ((0, "type", "function_call"), (2, "call_id", "different-call"),
                                       (2, "type", "function_call_output"),
                                       (0, "internal_chat_message_metadata_passthrough", {}),
                                       (2, "internal_chat_message_metadata_passthrough", dict(turn_id="a" * 36))):
            with self.subTest(position=position, field=field):
                records = self.records()
                records[position]["payload"][field] = value
                self.unmatched(records)

    def test_execution_thread_turn_command_status_process_cwd_and_streams_rejected(self):
        changes = [("payload", "thread_id", "other"), ("payload", "turn_id", "other"),
                   ("item", "command", ["/bin/bash", "-c", self.args["cmd"]]),
                   ("item", "status", "completed"), ("item", "source", "unified_exec_interaction"),
                   ("item", "exit_code", 0), ("item", "exit_code", True),
                   ("item", "process_id", None), ("item", "process_id", "0"), ("item", "id", ""),
                   ("item", "cwd", "file:///different"), ("item", "stderr", "warning\n"),
                   ("item", "stdout", REPLY.rstrip()), ("item", "aggregated_output", ERROR),
                   ("item", "formatted_output", "unrelated\n"),
                   ("payload", "started_at_ms", 0), ("payload", "completed_at_ms", True)]
        for part, field, value in changes:
            with self.subTest(part=part, field=field, value=value):
                records = self.records()
                payload = records[1]["payload"]
                (payload if part == "payload" else payload["item"])[field] = value
                self.unmatched(records)

    def test_nested_and_outer_result_must_both_match(self):
        for change in (dict(exit_code=0), dict(exit_code=True), dict(output=REPLY.rstrip()),
                       dict(session_id=123), dict(cell_id="running")):
            with self.subTest(change=change):
                records = self.records()
                result = dict(exit_code=2, output=REPLY)
                result.update(change)
                records[2]["payload"]["output"][1]["text"] = json.dumps(result)
                self.unmatched(records)
        for mutate in (lambda p: p.update(is_error=True), lambda p: p["output"].append(dict(type="text", text="extra")),
                       lambda p: p["output"][0].update(text="Script running with cell ID x\n")):
            records = self.records()
            mutate(records[2]["payload"])
            self.unmatched(records)

    def test_changed_or_missing_release_dependency_and_python_pins_rejected(self):
        key = "scripts/dependency.py"
        path = self.source / key
        original = path.read_bytes()
        path.write_bytes(original + b"# changed\n")
        self.unmatched()
        self.context["controller"] = tree_manifest(self.source)
        self.unmatched()  # Re-pinning current bytes cannot rewrite a registered release.
        path.unlink()
        self.context["controller"] = tree_manifest(self.source)
        self.unmatched()
        path.write_bytes(original)
        path.chmod(0o644)
        self.context["controller"] = tree_manifest(self.source)
        Path(self.context["python"]["path"]).write_bytes(b"another interpreter\n")
        self.unmatched()

    def test_unregistered_missing_manifest_and_extra_tree_content_rejected(self):
        manifest = self.source.parent / "MANIFEST.json"
        original = manifest.read_bytes()
        manifest.write_bytes(original + b"\n")
        self.unmatched()
        manifest.unlink()
        self.unmatched()
        manifest.write_bytes(original)
        (self.source / ".unlisted").write_text("changed full tree")
        self.unmatched()

    def test_mode_and_release_schema_are_validated_separately(self):
        path = self.source / "scripts/dependency.py"
        path.chmod(0o744)
        self.unmatched()  # Original complete-tree mode changed.
        self.context["controller"] = tree_manifest(self.source)
        self.unmatched()  # Updated tree still conflicts with release executable=false.
        path.chmod(0o644)
        self.context["controller"] = tree_manifest(self.source)
        original = copy.deepcopy(self.release)
        for field, value in (("executable", 0), ("executable", True), ("bytes", True), ("sha256", "0" * 64)):
            with self.subTest(field=field, value=value):
                self.release = copy.deepcopy(original)
                self.release["files"]["scripts/dependency.py"][field] = value
                self.publish_release()  # Register malformed synthetic fixtures only.
                self.unmatched()

    def test_history_mutation_between_scans_cannot_match(self):
        client, start, end = self.journal()
        records = client.reader.records
        scans = 0

        def changed(*args):
            nonlocal scans
            scans += 1
            if scans == 2:
                raw = client.path.read_bytes()
                changed_raw = raw.replace(b'"process_id":"12345"', b'"process_id":"54321"')
                self.assertNotEqual(raw, changed_raw)
                client.path.write_bytes(changed_raw)
            yield from records(*args)

        with patch.object(client.reader, "records", side_effect=changed):
            with self.assertRaises(ContractError) as caught:
                self.observe(client.reader, start, end)
        if isinstance(caught.exception, HistoricalRefusal):
            self.assertEqual(caught.exception.diagnostic["mapping_status"], "UNMATCHED")
        self.assertGreaterEqual(scans, 2)

    def test_public_maintenance_cli_preserves_rejection_and_diagnostic(self):
        from orchestration_maintenance import main
        diagnostic = self.diagnostic()
        output = io.StringIO()
        with patch("orchestration_maintenance.anchored_ref", return_value={}), \
                patch("orchestration_maintenance.freeze_maintenance", side_effect=HistoricalRefusal(diagnostic)), \
                redirect_stderr(output):
            code = main(["freeze", "--campaign", str(self.root / "campaign.json"), "--campaign-sha256", "a" * 64,
                         "--run", "fixture-run", "--node", "fixture-node"])
        self.assertEqual(code, 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "REJECTED")
        self.assertIs(result["input_retry_authorized"], False)
        self.assertEqual(result["diagnostic"], diagnostic)


if __name__ == "__main__":
    unittest.main()
