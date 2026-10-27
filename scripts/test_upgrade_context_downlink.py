"""Read-only MODEL_CONTEXT_CONSUMED gate; synthetic journals are not live-model evidence."""
import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

import upgrade_context_downlink as downlink
from cmux_prompt_reference import SINGLE_LINE_FORMAT, _wire
from orchestration_common import ContractError, canonical, digest, file_ref, json_ref, write_json

RELEASE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.76-f28b2b688eaf"
RELEASE_MANIFEST_SHA256 = "3f6a9647cde9d8b824f01d76c425ce2eedd5111a5806f971f45cb8666dc0e042"
OBSERVER_SHA256 = "79e058ae21ba8af81d359c29f6ebb6ffb46ecbb66871780088ec2bf69903f80e"
INTERPRETER = str(Path(sys.executable).resolve())
SESSION = "11111111-1111-4111-8111-111111111111"

# Stands in for the shared observer: records how it was run, then prints the verdict the test planned.
FAKE_OBSERVER = r'''
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[3]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(dict(argv=sys.argv[1:], ignore_environment=sys.flags.ignore_environment,
                              dont_write_bytecode=sys.dont_write_bytecode, executable=sys.executable)) + "\n")
plan = json.loads((root / "plan.json").read_text())
if plan.get("sleep"):
    import time; time.sleep(plan["sleep"])
print(plan["stdout"])
raise SystemExit(plan["rc"])
'''


class Fixture:
    """A fake release tree plus one original queued-only Claude attempt."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.source = self.root / "release" / "source"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "scripts" / "model_context_consumption.py").write_text(FAKE_OBSERVER)
        (self.source / "README.md").write_text("fixture release\n")
        self.transcript = self.root / "claude" / "projects" / "-task" / (SESSION + ".jsonl")
        self.body_path = self.root / "bodies" / "body.txt"
        self.body_path.parent.mkdir()
        self.body_path.write_text("固定的完整正文。\n" + "body " * 40)
        raw = self.body_path.read_bytes()
        self.body = dict(format=SINGLE_LINE_FORMAT, path=str(self.body_path), sha256=digest(raw), bytes=len(raw))
        self.marker = "U2078_U21_HS_claude_a_0123abcd"
        self.wire = _wire(self.marker, self.body)
        self.binding = dict(schema="native-delivery-v1", provider="claude", session_id=SESSION,
                            receipt_contract="native-user-marker-v1", receipt_marker=self.marker,
                            payload_sha256=digest(self.wire.encode()), transcript=dict(path=str(self.transcript)))
        self.journal = self.root / "state" / "message-dispatch-v1" / ("ab" * 32)
        self.attempt = self.write_attempt(1)
        self.evidence = self.root / "evidence" / "claude_a"
        self.publish()

    def publish(self, **policy):
        files = {str(p.relative_to(self.source)): dict(bytes=p.stat().st_size, sha256=digest(p.read_bytes()))
                 for p in sorted(self.source.rglob("*")) if p.is_file()}
        manifest = write_json(self.root / "release" / "MANIFEST.json", dict(version="fixture", files=files))
        observer = file_ref(self.source / "scripts" / "model_context_consumption.py")
        value = dict(schema=downlink.POLICY_SCHEMA, disposition="ACCEPTED_FOR_EXPLICIT_NEW_POLICY_BINDING",
                     evidence_class="MODEL_CONTEXT_CONSUMED", native_user_evidence_class="NATIVE_USER_RECEIVED",
                     complete_source=str(self.source), source_manifest=manifest, observer=observer)
        value.update(policy)
        self.policy = write_json(self.root / "policy.json", value)
        return self.policy

    def write_attempt(self, number, **changes):
        value = dict(phase="ENTER_SENT", error="MESSAGE_NOT_CONFIRMED", binding=dict(identity="fixture"),
                     native_binding=self.binding, body_reference=self.body,
                     events=[dict(phase="PASTE_INTENT"), dict(phase="ENTER_SENT")])
        value.update(changes)
        return write_json(self.journal / f"attempt-{number:04d}.json", value)

    def consumed(self, **changes):
        value = dict(schema=downlink.OBSERVATION_SCHEMA, observation="MODEL_CONTEXT_CONSUMED", session_id=SESSION,
                     marker=self.marker, wire_sha256=self.binding["payload_sha256"], body=self.body,
                     notice_request_id="request-one", body_request_id="request-two",
                     body_api_message_sha256="0" * 64, parallel_tool_results=2,
                     proof_slices=[dict(path=str(self.transcript), offset=10, bytes=20, sha256="1" * 64)],
                     native_user_received=False, body_full_read=True, business_accepted=False,
                     official_receipt_published=False, attempt_changed=False, input_operations=0,
                     task_authorized=False, resource_released=False, replay_authorized=False,
                     binding_scope="RECORDED_ORIGINAL_BINDING_NOT_CURRENT_PROCESS_HEALTH",
                     attempt=dict(path=self.attempt["path"], sha256=self.attempt["sha256"]),
                     snapshot_end=30, snapshot_sha256="2" * 64, historical_receipt_contract="native-user-marker-v1",
                     old_receipt_contract_unchanged=True, current_provider_health="NOT_MEASURED")
        value.update(changes)
        return value

    def unverified(self, reason):
        # The shared CLI's own refusal envelope.
        return dict(schema=downlink.OBSERVATION_SCHEMA, observation="UNVERIFIED", reason=reason,
                    native_user_received=False, official_receipt_published=False, attempt_changed=False,
                    input_operations=0, replay_authorized=False)

    def plan(self, stdout, rc=0, **extra):
        text = stdout if isinstance(stdout, str) else json.dumps(stdout, ensure_ascii=False, indent=2)
        (self.root / "plan.json").write_text(json.dumps(dict(stdout=text, rc=rc, **extra)))

    def observe(self, **kwargs):
        return downlink.observe(self.attempt, self.policy, INTERPRETER, self.evidence, **kwargs)

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def files(self):
        return {str(p): p.read_bytes() for p in sorted(self.root.rglob("*"))
                if p.is_file() and p.name not in ("calls.jsonl", "plan.json")}


def copied_observer(f):
    # Same depth as the release copy, so a wrongly accepted copy would still find this fixture's plan.
    path = f.root / "copy" / "source" / "scripts" / "model_context_consumption.py"
    path.parent.mkdir(parents=True)
    path.write_bytes((f.source / "scripts" / "model_context_consumption.py").read_bytes())
    return file_ref(path)


class ContextDownlinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="context-downlink-")
        self.addCleanup(temp.cleanup)
        self.f = Fixture(temp.name)

    def test_consumed_notice_is_its_own_evidence_class_from_the_isolated_pinned_observer(self):
        f = self.f
        f.plan(f.consumed())
        before = f.files()
        result = f.observe()
        self.assertEqual((result["schema"], result["evidence_class"]),
                         (downlink.RESULT_SCHEMA, "MODEL_CONTEXT_CONSUMED"))
        self.assertIs(result["observation"]["native_user_received"], False)
        self.assertNotIn("native_user_received", result)
        self.assertIsNone(result["receipt"])
        wire = f.evidence / f"context-wire-{f.marker}.txt"
        self.assertEqual(wire.read_text(), f.wire)
        [call] = f.calls()
        self.assertEqual(call["argv"], ["--attempt", f.attempt["path"], "--attempt-sha256", f.attempt["sha256"],
                                        "--text-file", str(wire)])
        self.assertEqual((call["ignore_environment"], call["dont_write_bytecode"]), (1, True))
        self.assertEqual(result["argv"][:3], [INTERPRETER, "-BE", str(f.source / "scripts/model_context_consumption.py")])
        # The only write is the wire; attempts, body, policy and the release tree are byte-identical.
        after = f.files()
        self.assertEqual(set(after) - set(before), {str(wire)})
        self.assertEqual({k: v for k, v in after.items() if k != str(wire)}, before)
        self.assertEqual(f.observe(), result)
        self.assertEqual(len(f.calls()), 2)

    def test_unverified_or_malformed_observer_output_is_never_consumption(self):
        f = self.f
        cases = (
            ("unverified", f.unverified("MATCHING_REAL_RESPONSE_REQUIRED"), 2,
             "context not consumed: MATCHING_REAL_RESPONSE_REQUIRED"),
            ("traceback", "Traceback (most recent call last):", 1, "context observer failed: rc=1"),
            ("unverified_rc0", f.unverified("x"), 0, "unexpected context observer verdict"),
            ("consumed_rc2", f.consumed(), 2, "consumption verdict without a successful exit"),
            ("other_verdict", f.consumed(observation="NATIVE_USER_RECEIVED"), 0, "unexpected context observer verdict"),
            ("other_verdict_rc2", f.consumed(observation="NATIVE_USER_RECEIVED"), 2, "unexpected context observer verdict"),
            ("not_json", "MODEL_CONTEXT_CONSUMED", 0, "invalid JSON"),
            ("not_object", "[]", 0, "unexpected context observer output"),
            ("other_schema", f.consumed(schema="model-context-consumption-observation-v2"), 0,
             "unexpected context observer output"),
            ("duplicate_key", '{"schema": "x", "schema": "' + downlink.OBSERVATION_SCHEMA + '"}', 0, "duplicate key"),
            *(("unverified_claims_" + name, dict(f.unverified("x"), **{name: value}), 2,
               "context observer claimed more than an observation")
              for name, value in (("native_user_received", True), ("official_receipt_published", True),
                                  ("attempt_changed", True), ("replay_authorized", True), ("input_operations", 1))),
        )
        for name, stdout, rc, reason in cases:
            with self.subTest(case=name):
                f.plan(stdout, rc)
                with self.assertRaisesRegex(ContractError, reason):
                    f.observe()

    def test_observation_claiming_more_or_bound_elsewhere_is_refused(self):
        f = self.f
        overclaim = "context observer claimed more than an observation"
        bound = "does not bind this exact attempt, wire, full body and session"
        cases = [(name, {name: True}, overclaim) for name in downlink.NEVER]
        cases += [("input_operations", dict(input_operations=1), overclaim),
                  ("partial_body", dict(body_full_read=False), bound),
                  ("other_marker", dict(marker="U2078_U21_HS_claude_b_0123abcd"), bound),
                  ("other_wire", dict(wire_sha256="3" * 64), bound),
                  ("other_body", dict(body=dict(f.body, bytes=f.body["bytes"] + 1)), bound),
                  ("other_session", dict(session_id="22222222-2222-4222-8222-222222222222"), bound),
                  ("other_attempt", dict(attempt=dict(path=f.attempt["path"], sha256="4" * 64)), bound),
                  ("other_contract", dict(historical_receipt_contract="native-user-marker-v2"), bound),
                  ("no_slices", dict(proof_slices=[]), bound),
                  ("other_transcript", dict(proof_slices=[dict(path=str(f.root / "other.jsonl"))]), bound)]
        for name, change, reason in cases:
            with self.subTest(case=name):
                f.plan(f.consumed(**change))
                with self.assertRaisesRegex(ContractError, reason):
                    f.observe()
        for name in (*downlink.NEVER, "input_operations", "body_full_read"):
            with self.subTest(missing=name):
                value = f.consumed()
                value.pop(name)
                f.plan(value)
                with self.assertRaises(ContractError):
                    f.observe()

    def test_release_or_policy_drift_refuses_before_the_observer_runs(self):
        f = self.f
        observer = f.source / "scripts" / "model_context_consumption.py"
        cases = (
            ("changed_observer", lambda: observer.write_text(FAKE_OBSERVER + "# changed\n"), "differs from its manifest"),
            ("extra_file", lambda: (f.source / "scripts" / "extra.py").write_text(""), "file set"),
            ("missing_file", lambda: (f.source / "README.md").unlink(), "file set"),
            ("linked_directory", lambda: (f.source / "linked").symlink_to(f.source / "scripts"), "symlinked directory"),
            ("manifest_rewritten", lambda: write_json(f.root / "release" / "MANIFEST.json", dict(files={})),
             "release manifest: file hash/size mismatch"),
            ("policy_rewritten", lambda: write_json(f.root / "policy.json", {}), "context policy: file hash/size mismatch"),
            ("foreign_manifest", lambda: f.publish(source_manifest=write_json(
                f.root / "MANIFEST.json", json_ref(json_ref(f.policy)["source_manifest"]))),
             "not the release's own"),
            ("other_observer", lambda: f.publish(observer=file_ref(f.source / "README.md")), "not the manifest's observer"),
            # Same bytes outside the reviewed tree, and the reviewed path with a stale sha.
            ("copied_observer", lambda: f.publish(observer=copied_observer(f)), "not the manifest's observer"),
            ("stale_observer_pin", lambda: f.publish(observer=dict(file_ref(observer), sha256="8" * 64)),
             "not the manifest's observer"),
            ("other_schema", lambda: f.publish(schema=downlink.POLICY_SCHEMA + "-v2"), "not v124-shaped"),
            ("other_class", lambda: f.publish(evidence_class="NATIVE_USER_RECEIVED"), "not v124-shaped"),
            ("other_disposition", lambda: f.publish(disposition="REJECTED"), "not v124-shaped"),
        )
        for name, change, reason in cases:
            with self.subTest(case=name):
                self.setUp()
                f = self.f
                observer = f.source / "scripts" / "model_context_consumption.py"
                f.plan(f.consumed())
                change()
                with self.assertRaisesRegex(ContractError, reason):
                    f.observe()
                self.assertEqual(f.calls(), [])

    def test_only_the_latest_input_free_history_of_an_unreceipted_claude_attempt_is_observed(self):
        cases = (
            ("codex_binding", lambda f: f.write_attempt(1, native_binding=dict(f.binding, provider="codex")),
             "original Claude native binding"),
            ("no_binding", lambda f: f.write_attempt(1, native_binding=None), "original Claude native binding"),
            ("inline_notice", lambda f: f.write_attempt(1, body_reference=None), "MESSAGE_REFERENCE_V2"),
            ("v1_reference", lambda f: f.write_attempt(1, body_reference=dict(f.body, format="cmux-message-reference-v1")),
             "MESSAGE_REFERENCE_V2"),
            ("other_payload", lambda f: f.write_attempt(1, native_binding=dict(f.binding, payload_sha256="5" * 64)),
             "recomputed wire differs"),
            ("unsafe_marker", lambda f: f.write_attempt(1, native_binding=dict(f.binding, receipt_marker="../../x")),
             "plain token"),
            ("prior_input", lambda f: (f.write_attempt(1), f.write_attempt(2)), "earlier attempt may have input"),
            ("prior_other_binding", lambda f: (f.write_attempt(1, phase="NO_INPUT", events=[], binding=dict(identity="x")),
                                               f.write_attempt(2)), "attempt binding changed"),
            ("later_attempt", lambda f: f.write_attempt(2), "latest original attempt"),
            ("gap", lambda f: (f.write_attempt(1, phase="NO_INPUT", events=[]), f.write_attempt(3)), "not contiguous"),
            ("confirmed_receipt", lambda f: write_json(f.journal / "receipt.json", dict(confirmed=True)),
             "confirmed native receipt keeps its native evidence"),
            ("existing_other_wire", lambda f: (f.evidence.mkdir(parents=True),
                                               (f.evidence / f"context-wire-{f.marker}.txt").write_text("other")),
             "existing immutable bytes differ"),
        )
        for name, change, reason in cases:
            with self.subTest(case=name):
                self.setUp()
                f = self.f
                f.plan(f.consumed())
                written = change(f)
                if name in ("prior_input", "prior_other_binding", "gap"):
                    f.attempt = written[-1]
                elif name not in ("later_attempt", "confirmed_receipt", "existing_other_wire"):
                    f.attempt = written
                with self.assertRaisesRegex(ContractError, reason):
                    f.observe()
                self.assertEqual(f.calls(), [])

    def test_unconfirmed_receipt_is_pinned_not_upgraded(self):
        f = self.f
        receipt = write_json(f.journal / "receipt.json", dict(confirmed=False, state="NATIVE_QUEUED"))
        f.plan(f.consumed())
        result = f.observe()
        self.assertEqual(result["receipt"], receipt)
        self.assertEqual(json_ref(receipt), dict(confirmed=False, state="NATIVE_QUEUED"))

    def test_interpreter_and_wire_directory_are_canonical_absolute_paths(self):
        f = self.f
        f.plan(f.consumed())
        plain = f.root / "not-executable"
        plain.write_text("")
        for name, interpreter, wire_dir, reason in (
                ("relative_interpreter", "python3", f.evidence, "canonical absolute path"),
                ("not_executable", str(plain), f.evidence, "not executable"),
                ("relative_wire_dir", INTERPRETER, "evidence/claude_a", "canonical absolute path")):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ContractError, reason):
                    downlink.observe(f.attempt, f.policy, interpreter, wire_dir)
        self.assertEqual(f.calls(), [])

    def test_observer_that_does_not_return_in_time_is_refused(self):
        f = self.f
        f.plan(f.consumed(), sleep=5)
        with self.assertRaisesRegex(ContractError, "timed out"):
            f.observe(timeout=0.5)

    def test_cli_prints_one_json_result_and_refuses_unpinned_inputs(self):
        f = self.f
        f.plan(f.consumed())

        def cli(**pins):
            args = dict(attempt=f.attempt["path"], attempt_sha256=f.attempt["sha256"], policy=f.policy["path"],
                        policy_sha256=f.policy["sha256"], interpreter=INTERPRETER, wire_dir=str(f.evidence))
            args.update(pins)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = downlink.main(["observe", *[x for k, v in args.items() for x in ("--" + k.replace("_", "-"), v)]])
            return rc, json.loads(out.getvalue())

        rc, value = cli()
        self.assertEqual((rc, value["evidence_class"]), (0, "MODEL_CONTEXT_CONSUMED"))
        for name, pins, reason in (("attempt", dict(attempt_sha256="6" * 64), "attempt differs from its pin"),
                                   ("policy", dict(policy_sha256="7" * 64), "policy differs from its pin")):
            with self.subTest(pin=name):
                rc, value = cli(**pins)
                self.assertEqual((rc, value["observation"], value["reason"]), (2, "UNVERIFIED", reason))
                self.assertIs(value["native_user_received"], False)
        self.assertEqual(len(f.calls()), 1)


def raw(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def api_hash(message):
    return digest(json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode())


@unittest.skipUnless((RELEASE / "MANIFEST.json").is_file(), "pinned MAC 0.4.76 release is not installed here")
class PinnedObserverTests(unittest.TestCase):
    """Runs the real reviewed observer, unchanged, from its complete release tree."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="context-downlink-real-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        manifest = file_ref(RELEASE / "MANIFEST.json")
        self.assertEqual(manifest["sha256"], RELEASE_MANIFEST_SHA256)
        observer = file_ref(RELEASE / "source/scripts/model_context_consumption.py")
        self.assertEqual(observer["sha256"], OBSERVER_SHA256)
        # Same reviewed policy shape, pointing at the same release; the live v124 file is pinned at freeze time.
        self.policy = write_json(self.root / "policy.json", dict(
            schema=downlink.POLICY_SCHEMA, disposition="ACCEPTED_FOR_EXPLICIT_NEW_POLICY_BINDING",
            evidence_class="MODEL_CONTEXT_CONSUMED", native_user_evidence_class="NATIVE_USER_RECEIVED",
            complete_source=str(RELEASE / "source"), source_manifest=manifest, observer=observer))
        names = {}
        for node in ast.parse(Path(observer["path"]).read_bytes()).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("PREFIX", "SUFFIX", "VERSION"):
                    names[node.targets[0].id] = ast.literal_eval(node.value)
        self.prefix_text, self.suffix_text, self.version = names["PREFIX"], names["SUFFIX"], names["VERSION"]

    def attempt(self, name, cut=None):
        """One queued-only original attempt with the measured 2.1.303 notice/body chain (optionally cut)."""
        home = self.root / name
        native_root = home / "claude"
        transcript = native_root / "projects" / "-task" / (SESSION + ".jsonl")
        transcript.parent.mkdir(parents=True)
        body_path = home / "body.txt"
        body_path.write_bytes(("固定的完整正文。\n" + "body " * 180).encode())
        body = body_path.read_bytes()
        ref = dict(format=SINGLE_LINE_FORMAT, path=str(body_path), sha256=digest(body), bytes=len(body))
        marker = "CONTEXT_TEST_NOTICE_" + name
        wire = _wire(marker, ref)
        event = lambda kind, **kw: dict(type=kind, sessionId=SESSION, version=self.version, **kw)
        assistant = lambda rid, identity, content: event(
            "assistant", requestRef=rid, uuid=identity, isSidechain=False,
            message=dict(type="message", role="assistant", id="msg_" + identity, content=content))
        message = dict(role="user", content=[dict(type="text", text=self.prefix_text + wire + self.suffix_text)])
        blob = event("api-request-blob", hash=api_hash(message), message=message)
        request = event("api-request", id="request-one", querySource="repl_main_thread", tail=[blob["hash"]])
        quoted = shlex.quote(str(body_path))
        bash = dict(type="tool_use", id="tool-hash", name="Bash",
                    input=dict(command="shasum -a 256 -- " + quoted + " && wc -c < " + quoted))
        read = dict(type="tool_use", id="tool-read", name="Read", input=dict(file_path=str(body_path)))
        hash_block = dict(type="tool_result", tool_use_id="tool-hash", is_error=False,
                          content=f"{ref['sha256']}  {body_path}\n{len(body)}")
        read_block = dict(type="tool_result", tool_use_id="tool-read", content="1\t" + body.decode())
        hash_result = event("user", uuid="result-hash", parentUuid="call-hash",
                            message=dict(role="user", content=[hash_block]),
                            toolUseResult=dict(stdout=hash_block["content"], stderr="", interrupted=False))
        read_result = event("user", uuid="result-read", parentUuid="call-read",
                            message=dict(role="user", content=[read_block]),
                            toolUseResult=dict(type="text", file=dict(filePath=str(body_path), content=body.decode(),
                                                                      startLine=1, numLines=2, totalLines=2)))
        body_request = event("api-request", id="request-two", querySource="repl_main_thread",
                             tail=[api_hash(dict(role="user", content=[hash_block, read_block]))])
        events = [event("attachment", attachment=dict(type="queued_command", prompt=wire)),
                  blob, request, assistant("request-one", "call-hash", [bash]),
                  assistant("request-one", "call-read", [read]), hash_result, read_result,
                  body_request, assistant("request-two", "response-body", [dict(type="text", text="Full body received.")])]
        prefix = raw(dict(type="system", sessionId=SESSION, subtype="native_metadata"))
        transcript.write_bytes(prefix + b"".join(raw(obj) for obj in events[:cut]))
        info = transcript.stat()
        pin = dict(path=str(transcript), device=info.st_dev, inode=info.st_ino, offset=len(prefix),
                   prefix_size=len(prefix), prefix_sha256=digest(prefix), tail_at=0, tail_sha256=digest(prefix),
                   at_record_boundary=True)
        binding = dict(schema="native-delivery-v1", provider="claude", session_id=SESSION,
                       identity=dict(caller_surface_uuid="22222222-2222-4222-8222-222222222222",
                                     target_surface_uuid="33333333-3333-4333-8333-333333333333",
                                     workspace_uuid="44444444-4444-4444-8444-444444444444",
                                     target_pane_uuid="55555555-5555-4555-8555-555555555555"),
                       process=dict(pid=12345, birth=[1000, 1], executable="/original/claude", file_identity=[1, 2, 3, 4]),
                       tty="ttys123", native_config_root=str(native_root), transcript=copy.deepcopy(pin),
                       payload_sha256=digest(wire.encode()), receipt_contract="native-user-marker-v1",
                       receipt_marker=marker, bound_at_epoch=1000.)
        fence = dict(schema="native-paste-fence-v1", transcript=copy.deepcopy(pin), captured_at_epoch=1001.,
                     binding_sha256=digest(json.dumps(binding, sort_keys=True).encode()))
        attempt = write_json(home / "journal" / "attempt-0001.json", dict(
            phase="ENTER_SENT", error="MESSAGE_NOT_CONFIRMED", binding=dict(identity=binding["identity"]),
            native_binding=binding, body_reference=ref,
            events=[dict(phase="PASTE_INTENT", native_paste_fence=fence), dict(phase="ENTER_SENT")]))
        return attempt, home / "evidence"

    def test_reviewed_observer_reports_consumption_only_for_the_complete_chain(self):
        attempt, evidence = self.attempt("complete")
        result = downlink.observe(attempt, self.policy, INTERPRETER, evidence)
        self.assertEqual(result["evidence_class"], "MODEL_CONTEXT_CONSUMED")
        self.assertEqual((result["observation"]["parallel_tool_results"], result["observation"]["native_user_received"]),
                         (2, False))
        for name, cut, reason in (("queued_only", 1, "UNIQUE_EXACT_API_USER_WRAPPER_REQUIRED"),
                                  ("no_body_response", -1, "FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED")):
            with self.subTest(case=name):
                attempt, evidence = self.attempt(name, cut)
                with self.assertRaisesRegex(ContractError, "context not consumed: " + reason):
                    downlink.observe(attempt, self.policy, INTERPRETER, evidence)


if __name__ == "__main__":
    unittest.main()
