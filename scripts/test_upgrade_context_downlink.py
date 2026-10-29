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
import time
import unittest

import upgrade_context_downlink as downlink
from cmux_prompt_reference import SINGLE_LINE_FORMAT, _wire
from orchestration_common import ContractError, canonical, digest, file_ref, json_ref, write_json

RELEASE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.76-f28b2b688eaf"
RELEASE_MANIFEST_SHA256 = "3f6a9647cde9d8b824f01d76c425ce2eedd5111a5806f971f45cb8666dc0e042"
OBSERVER_SHA256 = "79e058ae21ba8af81d359c29f6ebb6ffb46ecbb66871780088ec2bf69903f80e"
INTERPRETER = str(Path(sys.executable).resolve())
SESSION = "11111111-1111-4111-8111-111111111111"
NONCE = "0123456789abcdef"
ACK = f"PREFLIGHT_ACK|u21-fixture|claude:identity|READY|INLINE|{NONCE}"

# Stands in for the shared observer: records how it was run, then prints the verdict the test planned
# (optionally per --snapshot-end, so a window sees the verdict planned for each endpoint).
FAKE_OBSERVER = r'''
import json, sys
from pathlib import Path
MAX_SCAN = 8 * 1024 * 1024
root = Path(__file__).resolve().parents[3]
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(dict(argv=sys.argv[1:], ignore_environment=sys.flags.ignore_environment,
                              dont_write_bytecode=sys.dont_write_bytecode, executable=sys.executable)) + "\n")
plan = json.loads((root / "plan.json").read_text())
plan = plan.get("by_end", {}).get(sys.argv[sys.argv.index("--snapshot-end") + 1], plan)
if plan.get("sleep"):
    import time; time.sleep(plan["sleep"])
print(plan["stdout"])
raise SystemExit(plan["rc"])
'''


class Fixture:
    """A fake release tree plus one original queued-only Claude attempt."""

    def __init__(self, root, sequential=False, causal=False):
        self.root = Path(root).resolve()
        self.source = self.root / "release" / "source"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "scripts" / "model_context_consumption.py").write_text(FAKE_OBSERVER)
        # (policy schema, evidence class, observer script, observer output schema) of the class under test.
        if causal:
            # The causal observer imports the sequential correlator, which imports the v1 module.
            (self.source / "scripts" / "model_context_sequential.py").write_text(FAKE_OBSERVER)
            (self.source / "scripts" / "model_context_causal.py").write_text(FAKE_OBSERVER)
            self.kind = (downlink.CAUSAL_POLICY_SCHEMA, downlink.CAUSAL_EVIDENCE_CLASS,
                         "model_context_causal.py", downlink.CAUSAL_OBSERVATION_SCHEMA)
        elif sequential:
            (self.source / "scripts" / "model_context_sequential.py").write_text(FAKE_OBSERVER)
            self.kind = (downlink.SEQUENTIAL_POLICY_SCHEMA, downlink.SEQUENTIAL_EVIDENCE_CLASS,
                         "model_context_sequential.py", downlink.SEQUENTIAL_OBSERVATION_SCHEMA)
        else:
            self.kind = (downlink.POLICY_SCHEMA, "MODEL_CONTEXT_CONSUMED", "model_context_consumption.py",
                         downlink.OBSERVATION_SCHEMA)
        (self.source / "README.md").write_text("fixture release\n")
        self.transcript = self.root / "claude" / "projects" / "-task" / (SESSION + ".jsonl")
        self.body_path = self.root / "bodies" / "body.txt"
        self.body_path.parent.mkdir()
        self.body_path.write_text("固定的完整正文。\n" + "body " * 40)
        body = self.body_path.read_bytes()
        self.body = dict(format=SINGLE_LINE_FORMAT, path=str(self.body_path), sha256=digest(body), bytes=len(body))
        self.marker = "U2078_U21_HS_claude_a_0123abcd"
        self.wire = _wire(self.marker, self.body)
        # A real original transcript: metadata before the fence, then the notice and one response after it.
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_bytes(raw(dict(type="system", sessionId=SESSION, subtype="native_metadata")))
        info = self.transcript.stat()
        self.pin = dict(path=str(self.transcript), device=info.st_dev, inode=info.st_ino, offset=info.st_size)
        self.append(self.notice(), self.response())
        self.binding = dict(schema="native-delivery-v1", provider="claude", session_id=SESSION,
                            receipt_contract="native-user-marker-v1", receipt_marker=self.marker,
                            payload_sha256=digest(self.wire.encode()), transcript=dict(self.pin))
        self.journal = self.root / "state" / "message-dispatch-v1" / ("ab" * 32)
        self.attempt = self.write_attempt(1)
        self.evidence = self.root / "evidence" / "claude_a"
        self.publish()

    def append(self, *records):
        with self.transcript.open("ab") as stream:
            for record in records:
                stream.write(record if isinstance(record, bytes) else raw(record))
        return self.eof()

    def eof(self):
        return self.transcript.stat().st_size

    def rewrite(self, *records):
        """Same file (device/inode) and fence; only the records after the fence change."""
        with self.transcript.open("r+b") as stream:
            stream.truncate(self.pin["offset"])
        return self.append(*records)

    def notice(self):
        return dict(type="attachment", sessionId=SESSION, attachment=dict(type="queued_command", prompt=self.wire))

    def response(self, **changes):
        value = dict(type="assistant", sessionId=SESSION, uuid="response-body",
                     message=dict(role="assistant", content=[dict(type="text", text="Full body received.")]))
        value.update(changes)
        return value

    def record(self, name="result.json"):
        """One consumed observation at the current EOF, recorded as the gate would keep it."""
        self.plan(self.consumed())
        return write_json(self.root / "evidence" / name, self.observe())

    def order(self, recorded, ack_end=None, **kwargs):
        receipt = kwargs.pop("handshake", None) or handshake(self.root / "handshake.json", self.binding)
        return downlink.ack_order(recorded, self.policy["sha256"], INTERPRETER, receipt,
                                  self.eof() if ack_end is None else ack_end, **kwargs)

    def snap(self, end=None):
        end = self.eof() if end is None else end
        return end, digest(self.transcript.read_bytes()[self.pin["offset"]:end])

    def slices(self, end):
        """One proof slice per original record between the fence and end."""
        data, offset, out = self.transcript.read_bytes(), self.pin["offset"], []
        for line in data[offset:end].split(b"\n")[:-1]:
            out.append(dict(path=str(self.transcript), offset=offset, bytes=len(line) + 1,
                            sha256=digest(line + b"\n")))
            offset += len(line) + 1
        return out

    def publish(self, **policy):
        files = {str(p.relative_to(self.source)): dict(bytes=p.stat().st_size, sha256=digest(p.read_bytes()))
                 for p in sorted(self.source.rglob("*")) if p.is_file()}
        manifest = write_json(self.root / "release" / "MANIFEST.json", dict(version="fixture", files=files))
        observer = file_ref(self.source / "scripts" / self.kind[2])
        if self.kind[1] == downlink.CAUSAL_EVIDENCE_CLASS:
            # owner40's v215 reply shape: a source publication with its own contract, no policy fields.
            self.contract = dict(class_name=downlink.CAUSAL_EVIDENCE_CLASS, scope="PROSPECTIVE_EXPLICIT_NEW_BINDING_ONLY",
                                 default_old_observers_unchanged=True)
            self.published = dict(schema="source-only-immutable-publication-v1", source=str(self.source),
                                  manifest=manifest, causal=observer)
            value = dict(schema=self.kind[0], status="PROSPECTIVE_SOURCE_PUBLISHED_REQUIRED_CHECKS_PASS",
                         source_publication=self.published, causal_contract=self.contract)
        else:
            value = dict(schema=self.kind[0], disposition="ACCEPTED_FOR_EXPLICIT_NEW_POLICY_BINDING",
                         evidence_class=self.kind[1], native_user_evidence_class="NATIVE_USER_RECEIVED",
                         complete_source=str(self.source), source_manifest=manifest, observer=observer)
        value.update(policy)
        self.policy = write_json(self.root / "policy.json", value)
        return self.policy

    def fence(self, **transcript):
        return dict(phase="PASTE_INTENT", native_paste_fence=dict(schema="native-paste-fence-v1",
                                                                  transcript=dict(self.pin, **transcript)))

    def write_attempt(self, number, **changes):
        value = dict(phase="ENTER_SENT", error="MESSAGE_NOT_CONFIRMED", binding=dict(identity="fixture"),
                     native_binding=self.binding, body_reference=self.body,
                     events=[self.fence(), dict(phase="ENTER_SENT")])
        value.update(changes)
        return write_json(self.journal / f"attempt-{number:04d}.json", value)

    def consumed(self, end=None, **changes):
        """The observer's verdict for the original bytes up to end (default: current EOF)."""
        end, sha = self.snap(end)
        value = dict(schema=self.kind[3], observation=self.kind[1], session_id=SESSION,
                     marker=self.marker, wire_sha256=self.binding["payload_sha256"], body=self.body,
                     notice_request_id="request-one", body_request_id="request-two",
                     body_api_message_sha256="0" * 64, parallel_tool_results=2, proof_slices=self.slices(end),
                     native_user_received=False, body_full_read=True, business_accepted=False,
                     official_receipt_published=False, attempt_changed=False, input_operations=0,
                     task_authorized=False, resource_released=False, replay_authorized=False,
                     binding_scope="RECORDED_ORIGINAL_BINDING_NOT_CURRENT_PROCESS_HEALTH",
                     attempt=dict(path=self.attempt["path"], sha256=self.attempt["sha256"]),
                     snapshot_end=end, snapshot_sha256=sha, historical_receipt_contract="native-user-marker-v1",
                     old_receipt_contract_unchanged=True, current_provider_health="NOT_MEASURED")
        value.update(changes)
        return value

    def unverified(self, reason):
        # The shared CLI's own refusal envelope.
        return dict(schema=self.kind[3], observation="UNVERIFIED", reason=reason,
                    native_user_received=False, official_receipt_published=False, attempt_changed=False,
                    input_operations=0, replay_authorized=False)

    @staticmethod
    def step(stdout, rc=0, **extra):
        text = stdout if isinstance(stdout, str) else json.dumps(stdout, ensure_ascii=False, indent=2)
        return dict(stdout=text, rc=rc, **extra)

    def plan(self, stdout, rc=0, by_end=None, **extra):
        value = self.step(stdout, rc, **extra)
        if by_end:
            value["by_end"] = {str(end): step for end, step in by_end.items()}
        (self.root / "plan.json").write_text(json.dumps(value))

    def observe(self, end=None, **kwargs):
        return downlink.observe(self.attempt, self.policy, INTERPRETER, self.evidence,
                                self.eof() if end is None else end, **kwargs)

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def files(self):
        return {str(p): p.read_bytes() for p in sorted(self.root.rglob("*"))
                if p.is_file() and p.name not in ("calls.jsonl", "plan.json")}


def fifo_attempt(f):
    path = f.root / "fifo.jsonl"
    os.mkfifo(path)
    info = os.stat(path)
    pin = dict(f.pin, path=str(path), device=info.st_dev, inode=info.st_ino)
    return f.write_attempt(1, native_binding=dict(f.binding, transcript=pin),
                           events=[f.fence(path=pin["path"], device=pin["device"], inode=pin["inode"])])


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
                                        "--text-file", str(wire), "--snapshot-end", str(f.eof())])
        end, sha = f.snap()
        self.assertEqual(result["snapshot"], dict(path=str(f.transcript), device=f.pin["device"], inode=f.pin["inode"],
                                                  start=f.pin["offset"], end=end, bytes=end - f.pin["offset"], sha256=sha))
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
                  ("other_transcript", dict(proof_slices=[dict(path=str(f.root / "other.jsonl"))]), bound),
                  # The observer's endpoint and hash must be the ones pinned here before it ran.
                  ("other_end", dict(snapshot_end=f.eof() - 1), "not of the pinned original snapshot"),
                  ("other_snapshot", dict(snapshot_sha256="9" * 64), "not of the pinned original snapshot"),
                  ("slice_past_end", dict(proof_slices=[dict(f.slices(f.eof())[-1], bytes=10 ** 6)]),
                   "outside the pinned snapshot"),
                  ("slice_before_fence", dict(proof_slices=[dict(f.slices(f.eof())[0], offset=0)]),
                   "outside the pinned snapshot"),
                  ("slice_offset_text", dict(proof_slices=[dict(f.slices(f.eof())[0], offset="1")]),
                   "outside the pinned snapshot")]
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
            # The endpoint bound checked here must be the observer's own.
            ("other_scan_limit", lambda: (observer.write_text(FAKE_OBSERVER.replace("8 * 1024", "4 * 1024")), f.publish()),
             "different bounded-snapshot limit"),
            ("no_scan_limit", lambda: (observer.write_text(FAKE_OBSERVER.replace("MAX_SCAN = ", "LIMIT = ")), f.publish()),
             "has no MAX_SCAN"),
            ("computed_scan_limit", lambda: (observer.write_text(FAKE_OBSERVER.replace("8 * 1024 * 1024", "2 ** 23")),
                                             f.publish()), "not a plain integer"),
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
            # The snapshot is read through the one original fence pin, never another file or offset.
            ("no_fence", lambda f: f.write_attempt(1, events=[dict(phase="ENTER_SENT")]), "one original paste-intent fence"),
            ("two_fences", lambda f: f.write_attempt(1, events=[f.fence(), f.fence()]), "one original paste-intent fence"),
            ("other_fence_schema", lambda f: f.write_attempt(1, events=[dict(phase="PASTE_INTENT", native_paste_fence=dict(
                f.fence()["native_paste_fence"], schema="native-input-fence-v1"))]), "one original paste-intent fence"),
            ("fence_without_inode", lambda f: f.write_attempt(1, events=[f.fence(inode=None)]),
             "original fence transcript pin required"),
            ("fence_other_path", lambda f: f.write_attempt(1, events=[f.fence(path=str(f.root / "other.jsonl"))]),
             "fence and binding name different transcripts"),
            ("fence_other_inode", lambda f: f.write_attempt(1, events=[f.fence(inode=f.pin["inode"] + 1)]),
             "pinned original transcript replaced"),
            ("fence_mid_record", lambda f: f.write_attempt(1, events=[f.fence(offset=f.pin["offset"] + 3)]),
             "snapshot start is inside a record"),
            # A pinned non-regular file (here a FIFO with its own device/inode) is never read as a transcript.
            ("fence_fifo", lambda f: fifo_attempt(f), "owner/type mismatch"),
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
                    downlink.observe(f.attempt, f.policy, interpreter, wire_dir, f.eof())
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
                        policy_sha256=f.policy["sha256"], interpreter=INTERPRETER, wire_dir=str(f.evidence),
                        snapshot_end=str(f.eof()))
            args.update(pins)
            return run_cli("observe", **args)

        rc, value = cli()
        self.assertEqual((rc, value["evidence_class"]), (0, "MODEL_CONTEXT_CONSUMED"))
        for name, pins, reason in (("attempt", dict(attempt_sha256="6" * 64), "attempt differs from its pin"),
                                   ("policy", dict(policy_sha256="7" * 64), "policy differs from its pin")):
            with self.subTest(pin=name):
                rc, value = cli(**pins)
                self.assertEqual((rc, value["observation"], value["reason"]), (2, "UNVERIFIED", reason))
                self.assertIs(value["native_user_received"], False)
        self.assertEqual(len(f.calls()), 1)
        # No endpoint, no observation: the CLI never falls back to the current EOF.
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            downlink.main(["observe", *cli_args(dict(attempt=f.attempt["path"], attempt_sha256=f.attempt["sha256"],
                                                     policy=f.policy["path"], policy_sha256=f.policy["sha256"],
                                                     interpreter=INTERPRETER, wire_dir=str(f.evidence)))])
        self.assertEqual(len(f.calls()), 1)

    def test_snapshot_endpoint_is_explicit_bounded_and_a_complete_record_boundary(self):
        f = self.f
        f.plan(f.consumed())
        start = f.pin["offset"]
        for name, end, reason in (
                ("none", None, "outside the bounded window"), ("text", str(f.eof()), "outside the bounded window"),
                ("bool", True, "outside the bounded window"), ("at_fence", start, "outside the bounded window"),
                ("before_fence", start - 1, "outside the bounded window"),
                ("past_window", start + downlink.MAX_SCAN + 1, "outside the bounded window"),
                ("past_file", f.eof() + 1, "past the original file"),
                ("mid_record", f.eof() - 1, "not a complete record boundary")):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ContractError, reason):
                    downlink.observe(f.attempt, f.policy, INTERPRETER, f.evidence, end)
        self.assertEqual(f.calls(), [])
        # An earlier complete-record endpoint is observed as itself, not widened to EOF.
        first = start + len(f.transcript.read_bytes()[start:].split(b"\n")[0]) + 1
        f.plan(f.consumed(first))
        self.assertEqual(f.observe(first)["snapshot"]["end"], first)

    def test_verify_reproduces_a_proof_at_its_pinned_endpoint_after_unrelated_growth(self):
        f = self.f
        f.plan(f.consumed())
        recorded = write_json(f.root / "evidence" / "result.json", f.observe())
        # Later unrelated records neither retract nor widen the recorded proof.
        f.append(*[dict(type="assistant", sessionId=SESSION, uuid=f"later-{i}") for i in range(3)])
        self.assertEqual(downlink.verify(recorded, f.policy["sha256"], INTERPRETER), json_ref(recorded))
        self.assertEqual(f.calls()[-1]["argv"][-2:], ["--snapshot-end", str(json_ref(recorded)["snapshot"]["end"])])
        value = json_ref(recorded)
        tampered = dict(
            other_policy=(dict(value), "8" * 64, INTERPRETER, "bound to another policy"),
            other_interpreter=(dict(value), f.policy["sha256"], str(f.source / "README.md"), "another interpreter"),
            no_class=(dict(value, evidence_class=None), f.policy["sha256"], INTERPRETER, "not a recorded context"),
            no_snapshot=(dict(value, snapshot=None), f.policy["sha256"], INTERPRETER, "no pinned snapshot"),
            moved_end=(dict(value, snapshot=dict(value["snapshot"], end=f.eof())), f.policy["sha256"], INTERPRETER,
                       "does not reproduce|not of the pinned"),
            other_slices=(dict(value, observation=dict(value["observation"], proof_slices=f.slices(f.eof()))),
                          f.policy["sha256"], INTERPRETER, "does not reproduce"),
            # A forged wire location would make re-observation write elsewhere; it is refused first.
            other_wire_dir=(dict(value, wire=dict(value["wire"], path=str(f.root / "elsewhere" / "w.txt"))),
                            f.policy["sha256"], INTERPRETER, "cannot read .*elsewhere"))
        for name, (result, policy, interpreter, reason) in tampered.items():
            with self.subTest(case=name):
                ref = write_json(f.root / "evidence" / f"tampered-{name}.json", result)
                with self.assertRaisesRegex(ContractError, reason):
                    downlink.verify(ref, policy, interpreter)
        self.assertFalse((f.root / "elsewhere").exists())

    def test_journal_facts_that_are_not_observer_inputs_do_not_retract_a_recorded_proof(self):
        """1A38 O1: an unconfirmed receipt is no evidence either way, before or after the observation."""
        f = self.f
        f.plan(f.consumed())
        recorded = write_json(f.root / "evidence" / "result.json", f.observe())
        end = json_ref(recorded)["snapshot"]["end"]
        receipt = f.journal / "receipt.json"
        for state in ("NATIVE_QUEUED", "NATIVE_PENDING"):
            write_json(receipt, dict(confirmed=False, state=state))
            self.assertEqual(downlink.verify(recorded, f.policy["sha256"], INTERPRETER), json_ref(recorded))
        # The ACK order is judged against the same reproduced proof.
        f.append(ack_record())
        self.assertEqual(f.order(recorded)["ordering"], "ACK_AFTER_FULL_BODY_CONSUMPTION")
        # A proof recorded beside an unconfirmed receipt keeps reproducing once that receipt is gone.
        beside = write_json(f.root / "evidence" / "result-beside.json", f.observe(end))
        self.assertEqual(json_ref(beside)["receipt"], file_ref(receipt))
        receipt.unlink()
        self.assertEqual(downlink.verify(beside, f.policy["sha256"], INTERPRETER), json_ref(beside))
        self.assertEqual(len(f.calls()), 6)
        value = json_ref(beside)
        for name, forged in (("text", "receipt.json"), ("extra_key", dict(value["receipt"], confirmed=False))):
            with self.subTest(receipt=name):
                ref = write_json(f.root / "evidence" / f"forged-receipt-{name}.json", dict(value, receipt=forged))
                with self.assertRaisesRegex(ContractError, "recorded receipt is not a pinned file reference"):
                    downlink.verify(ref, f.policy["sha256"], INTERPRETER)
        self.assertEqual(len(f.calls()), 6)

    def test_a_changed_or_superseded_attempt_or_a_later_confirmed_receipt_is_refused_by_name(self):
        for name, change, reason in (
                ("confirmed_receipt", lambda f: write_json(f.journal / "receipt.json", dict(confirmed=True)),
                 "confirmed native receipt keeps its native evidence"),
                # Stranded-compose recovery appends to the same original attempt file.
                ("attempt_rewritten", lambda f: f.write_attempt(1, events=[
                    f.fence(), dict(phase="ENTER_SENT"), dict(phase="EXTRA_ENTER_INTENT", recovery="stranded_compose")]),
                 "original attempt changed or was removed"),
                ("attempt_removed", lambda f: Path(f.attempt["path"]).unlink(), "original attempt changed or was removed"),
                ("later_attempt", lambda f: f.write_attempt(2), "journal gained a later attempt")):
            with self.subTest(case=name):
                self.setUp()
                f = self.f
                f.plan(f.consumed())
                recorded = write_json(f.root / "evidence" / "result.json", f.observe())
                f.append(ack_record())
                change(f)
                with self.assertRaisesRegex(ContractError, reason):
                    downlink.verify(recorded, f.policy["sha256"], INTERPRETER)
                with self.assertRaisesRegex(ContractError, reason):
                    f.order(recorded)
                # Refused before the observer runs again.
                self.assertEqual(len(f.calls()), 1)
        self.setUp()
        f = self.f
        f.plan(f.consumed())
        value = json_ref(write_json(f.root / "evidence" / "result.json", f.observe()))
        ref = write_json(f.root / "evidence" / "no-attempt.json", dict(value, attempt=None))
        with self.assertRaisesRegex(ContractError, "observation has no pinned attempt"):
            downlink.verify(ref, f.policy["sha256"], INTERPRETER)

    def window(self, hooks=(), deadline=60, **kwargs):
        """observe_window on a fake clock; each sleep runs the next hook (transcript growth etc.)."""
        f, clock, pending = self.f, [1000.0], list(hooks)

        def sleep(seconds):
            self.assertGreater(seconds, 0, "a window never busy-waits")
            clock[0] += seconds
            if pending:
                pending.pop(0)()
        window = f.root / "evidence" / "window"
        terminal = downlink.observe_window(f.attempt, f.policy, INTERPRETER, window, deadline_epoch=1000.0 + deadline,
                                           now=lambda: clock[0], sleep=sleep, **kwargs)
        return terminal, window

    def test_window_keeps_each_provisional_observation_and_terminalizes_on_the_complete_proof(self):
        f = self.f
        early = f.eof()
        f.plan(f.unverified("FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED"), 2)

        def grow():
            f.append(dict(type="assistant", sessionId=SESSION, uuid="response-two"))
            f.plan(f.unverified("x"), 2, by_end={early: f.step(f.unverified("FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED"), 2),
                                                 f.eof(): f.step(f.consumed())})
        terminal, window = self.window([lambda: None, grow])
        self.assertEqual((terminal["evidence_class"], terminal["observation"], terminal["reason"]),
                         ("MODEL_CONTEXT_CONSUMED", "MODEL_CONTEXT_CONSUMED", None))
        first, second = (json_ref(ref) for ref in terminal["increments"])
        self.assertEqual((first["seq"], first["observation"], first["provisional"], first["snapshot_end"]),
                         (1, "UNVERIFIED", True, early))
        self.assertIn("FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED", first["reason"])
        self.assertEqual((second["seq"], second["observation"], second["provisional"], second["reason"]),
                         (2, "MODEL_CONTEXT_CONSUMED", False, None))
        self.assertEqual(second["previous_sha256"], terminal["increments"][0]["sha256"])
        self.assertEqual(first["snapshot_sha256"], f.snap(early)[1])
        # No re-observation without growth; nothing is ever sent.
        self.assertEqual([c["argv"][-1] for c in f.calls()], [str(early), str(f.eof())])
        self.assertTrue(all(json_ref(r)["input_operations"] == 0 for r in terminal["increments"]))
        self.assertEqual(terminal["snapshot_end"], f.eof())
        self.assertEqual(downlink.verify(terminal["result"], f.policy["sha256"], INTERPRETER), json_ref(terminal["result"]))
        self.assertEqual(sorted(p.name for p in window.iterdir()),
                         [f"context-wire-{f.marker}.txt", "observation-0001.json", "observation-0002.json",
                          "result.json", "terminal.json"])
        with self.assertRaisesRegex(ContractError, "never reopened"):
            self.window()

    def test_window_without_proof_ends_unverified_at_the_real_deadline_or_budget(self):
        f = self.f
        f.plan(f.unverified("MATCHING_REAL_RESPONSE_REQUIRED"), 2)
        terminal, _ = self.window(deadline=12)
        self.assertEqual((terminal["evidence_class"], terminal["observation"], terminal["result"]), (None, "UNVERIFIED", None))
        self.assertTrue(terminal["reason"].startswith("deadline reached; last: context not consumed"), terminal["reason"])
        self.assertEqual((len(terminal["increments"]), len(f.calls())), (1, 1))
        self.setUp()
        f = self.f
        f.plan(f.unverified("MATCHING_REAL_RESPONSE_REQUIRED"), 2)
        tick = lambda: f.append(dict(type="assistant", sessionId=SESSION, uuid="tick"))
        terminal, _ = self.window([tick, tick, tick], max_observations=2)
        self.assertTrue(terminal["reason"].startswith("observation budget exhausted"), terminal["reason"])
        self.assertEqual((len(terminal["increments"]), len(f.calls())), (2, 2))

    def test_window_terminalizes_at_once_on_any_refusal_other_than_not_yet_consumed(self):
        cases = (
            ("resend", lambda f: f.write_attempt(2), "latest original attempt"),
            ("confirmed_receipt", lambda f: write_json(f.journal / "receipt.json", dict(confirmed=True)),
             "confirmed native receipt keeps its native evidence"),
            ("observer_crash", lambda f: f.plan("Traceback", 1), "context observer failed: rc=1"),
        )
        for name, change, reason in cases:
            with self.subTest(case=name):
                self.setUp()
                f = self.f
                f.plan(f.unverified("MATCHING_REAL_RESPONSE_REQUIRED"), 2)
                grow = lambda f=f, change=change: (f.append(dict(type="assistant", sessionId=SESSION, uuid="x")),
                                                   change(f))
                terminal, _ = self.window([grow])
                self.assertEqual(terminal["observation"], "UNVERIFIED")
                self.assertTrue(terminal["reason"].startswith("refused: "), terminal["reason"])
                self.assertIn(reason, terminal["reason"])
                second = json_ref(terminal["increments"][-1])
                self.assertEqual((len(terminal["increments"]), second["provisional"]), (2, True))

    def test_window_ends_once_the_bounded_original_window_is_full(self):
        f = self.f
        f.plan(f.unverified("MATCHING_REAL_RESPONSE_REQUIRED"), 2)
        filler = raw(dict(type="assistant", sessionId=SESSION, uuid="filler", pad="x" * downlink.MAX_SCAN))
        terminal, _ = self.window([lambda: f.append(filler)])
        self.assertTrue(terminal["reason"].startswith("bounded original window exhausted"), terminal["reason"])
        self.assertEqual((len(terminal["increments"]), len(f.calls())), (1, 1))

    def test_window_bounds_are_checked_before_anything_is_written(self):
        f = self.f
        for name, kwargs, reason in (
                ("past_deadline", dict(deadline=0), "deadline must be in the next hour"),
                ("too_far", dict(deadline=3601), "deadline must be in the next hour"),
                ("fast_interval", dict(interval=0.5), "interval out of bounds"),
                ("slow_interval", dict(interval=61), "interval out of bounds"),
                ("no_budget", dict(max_observations=0), "budget out of bounds"),
                ("big_budget", dict(max_observations=721), "budget out of bounds"),
                ("float_budget", dict(max_observations=2.0), "budget out of bounds")):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ContractError, reason):
                    self.window(**kwargs)
                self.assertFalse((f.root / "evidence" / "window").exists())
        self.assertEqual(f.calls(), [])

    def test_ack_after_full_body_consumption_is_ordered_in_the_observed_transcript_only(self):
        f = self.f
        recorded = f.record()
        consumed_at = json_ref(recorded)["observation"]["proof_slices"][-1]["offset"]
        at = f.eof()
        f.append(ack_record())
        order = f.order(recorded)
        self.assertEqual((order["ordering"], order["coordinate_system"], order["receiver_offsets_compared"]),
                         ("ACK_AFTER_FULL_BODY_CONSUMPTION", "observed_claude_transcript", False))
        self.assertEqual((order["ack"]["offset"], order["ack"]["path"], order["ack"]["inode"], order["body_consumed_at_offset"]),
                         (at, str(f.transcript), f.pin["inode"], consumed_at))
        self.assertEqual((order["native_user_received"], order["input_operations"]), (False, 0))
        # The response that consumed the full body may itself be the ACK.
        f.rewrite(f.notice(), ack_record(uuid="response-body"))
        order = f.order(f.record("result-same.json"))
        self.assertEqual(order["ack"]["offset"], order["body_consumed_at_offset"])

    def test_ack_before_full_body_consumption_or_ambiguous_is_refused(self):
        f = self.f
        for name, records, reason in (
                ("before", lambda: [f.notice(), ack_record(), f.response()], "ACK precedes full body consumption"),
                ("before_and_after", lambda: [f.notice(), ack_record(), f.response(), ack_record(uuid="ack-2")],
                 "more than one exact ACK"),
                ("twice_after", lambda: [f.notice(), f.response(), ack_record(), ack_record(uuid="ack-2")],
                 "more than one exact ACK"),
                ("none", lambda: [f.notice(), f.response()], "exact ACK not in the pinned ACK range")):
            with self.subTest(case=name):
                f.rewrite(*records())
                recorded = f.record(f"result-{name}.json")
                with self.assertRaisesRegex(ContractError, reason):
                    f.order(recorded)

    def test_ack_order_first_reproduces_the_observation_it_orders_against(self):
        f = self.f
        f.rewrite(f.notice(), ack_record(), f.response())
        value = json_ref(f.record())
        # A forged proof that stops before the early ACK must not make that ACK "after" the body.
        forged = dict(value, observation=dict(value["observation"], proof_slices=value["observation"]["proof_slices"][:1]))
        with self.assertRaisesRegex(ContractError, "does not reproduce"):
            f.order(write_json(f.root / "evidence" / "forged.json", forged))

    def test_only_the_exact_native_assistant_ack_counts(self):
        f = self.f
        text = lambda line: [dict(type="text", text=line)]
        near = dict(
            with_tool=ack_record(message=dict(role="assistant", content=[*text(ACK), dict(type="tool_use", id="t")])),
            quoted_in_read=dict(type="user", sessionId=SESSION, message=dict(role="user", content=[
                dict(type="tool_result", tool_use_id="t", content="reply exactly: " + ACK)])),
            sidechain=ack_record(isSidechain=True), meta=ack_record(isMeta=True), api_error=ack_record(isApiErrorMessage=True),
            other_session=ack_record(sessionId="22222222-2222-4222-8222-222222222222"),
            other_nonce=ack_record(ACK.replace(NONCE, "f" * 16)), user_role=ack_record(type="user"),
            role_user=ack_record(message=dict(role="user", content=[dict(type="text", text=ACK)])),
            two_texts=ack_record(message=dict(role="assistant", content=[*text(ACK), *text(ACK)])))
        for name, record in near.items():
            with self.subTest(near_miss=name):
                f.rewrite(f.notice(), record, f.response())
                recorded = f.record(f"result-{name}.json")
                f.append(ack_record(uuid="true-ack"))
                self.assertEqual(f.order(recorded)["ordering"], "ACK_AFTER_FULL_BODY_CONSUMPTION")
        for name, content in (("plain_string", ACK),
                              ("with_thinking", [dict(type="thinking", thinking="…", signature="s"), *text(ACK)]),
                              ("with_redacted", [dict(type="redacted_thinking", data="d"), *text(ACK)])):
            with self.subTest(accepted=name):
                f.rewrite(f.notice(), f.response())
                recorded = f.record(f"result-{name}.json")
                f.append(ack_record(message=dict(role="assistant", content=content)))
                self.assertEqual(f.order(recorded)["ordering"], "ACK_AFTER_FULL_BODY_CONSUMPTION")

    def test_ack_range_is_explicit_and_a_complete_record_boundary(self):
        f = self.f
        recorded = f.record()
        at = f.eof()
        f.append(ack_record())
        for name, end, reason in (("before_ack", at, "exact ACK not in the pinned ACK range"),
                                  ("text", str(f.eof()), "explicit ACK endpoint required"),
                                  ("mid_record", f.eof() - 1, "does not end at a record boundary"),
                                  ("past_file", f.eof() + 1, "past the original file"),
                                  ("before_window", f.pin["offset"] - 1, "outside the bounded original window")):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ContractError, reason):
                    f.order(recorded, ack_end=end)
        # An ACK range is bounded like the snapshot: nothing past the window is searched.
        f.append(raw(dict(type="system", sessionId=SESSION, pad="x" * downlink.MAX_SCAN)), ack_record(uuid="late"))
        with self.assertRaisesRegex(ContractError, "outside the bounded original window"):
            f.order(recorded)

    def test_a_claimed_ack_from_another_file_or_coordinate_is_never_compared(self):
        f = self.f
        recorded = f.record()
        at = f.eof()
        f.append(ack_record())
        line = f.transcript.read_bytes()[at:]
        exact = dict(path=str(f.transcript), offset=at, length=len(line), sha256=digest(line), ack_line=ACK)
        order = f.order(recorded, observed_ack=write_json(f.root / "ack-observed.json", exact))
        self.assertEqual(order["observed_ack"]["sha256"], file_ref(f.root / "ack-observed.json")["sha256"])
        # A Codex receiver record lives in another file; its offset is never ordered against Claude's.
        codex = dict(exact, path=str(f.root / "codex" / "rollout.jsonl"), offset=at + 10 ** 6)
        for name, claimed in (("codex_coordinates", codex), ("other_path", dict(exact, path=codex["path"])),
                              ("other_offset", dict(exact, offset=at - 1)),
                              ("other_length", dict(exact, length=len(line) + 1)), ("other_sha", dict(exact, sha256="0" * 64)),
                              ("other_line", dict(exact, ack_line=ACK + " "))):
            with self.subTest(case=name):
                with self.assertRaisesRegex(ContractError, "not this exact record of the observed transcript"):
                    f.order(recorded, observed_ack=write_json(f.root / f"ack-{name}.json", claimed))

    def test_ack_is_joined_to_this_notice_by_payload_and_nonce(self):
        f = self.f
        recorded = f.record()
        f.append(ack_record())
        cases = dict(
            other_payload=dict(handshake_precondition=dict(payload_sha256="0" * 64)), no_precondition=dict(handshake_precondition=None),
            codex_executor=dict(executor_provider="codex"), other_marker=dict(marker="U2078_U21_HS_claude_b_0123abcd"),
            short_nonce=dict(ack_nonce="0123"), nonce_not_hex=dict(ack_nonce="Z" * 16),
            nonce_not_hex_consistent=dict(ack_nonce="Z" * 16, ack_line_expected=ACK.replace(NONCE, "Z" * 16)),
            line_other_nonce=dict(ack_line_expected=ACK.replace(NONCE, "f" * 16)),
            other_prefix=dict(ack_line_expected=ACK.replace("PREFLIGHT_ACK", "PREFLIGHT_OK")),
            not_printable=dict(ack_line_expected=ACK.replace("|READY", "\tREADY")))
        for name, change in cases.items():
            with self.subTest(case=name):
                receipt = handshake(f.root / f"handshake-{name}.json", f.binding, **change)
                with self.assertRaisesRegex(ContractError, "not this notice's original ACK contract"):
                    f.order(recorded, handshake=receipt)
        receipt = handshake(f.root / "handshake-unmarked.json", f.binding)
        value = json_ref(receipt)
        value.pop("marker")
        self.assertEqual(f.order(recorded, handshake=write_json(receipt["path"], value))["ordering"],
                         "ACK_AFTER_FULL_BODY_CONSUMPTION")

    def test_verify_window_and_ack_cli_print_one_json_result(self):
        f = self.f
        recorded = f.record()
        f.append(ack_record())
        receipt = handshake(f.root / "handshake.json", f.binding)
        pins = dict(result=recorded["path"], result_sha256=recorded["sha256"], policy_sha256=f.policy["sha256"],
                    interpreter=INTERPRETER)
        self.assertEqual(run_cli("verify", **pins)[0], 0)
        order = dict(pins, handshake_receipt=receipt["path"], handshake_receipt_sha256=receipt["sha256"], ack_end=f.eof())
        rc, value = run_cli("ack-order", **order)
        self.assertEqual((rc, value["ordering"]), (0, "ACK_AFTER_FULL_BODY_CONSUMPTION"))
        for name, change, reason in (("result_pin", dict(result_sha256="6" * 64), "observation differs from its pin"),
                                     ("receipt_pin", dict(handshake_receipt_sha256="6" * 64), "handshake receipt differs"),
                                     ("half_observed", dict(observed_ack=receipt["path"]), "go together")):
            with self.subTest(case=name):
                rc, value = run_cli("ack-order", **dict(order, **change))
                self.assertEqual((rc, value["observation"], value["schema"]), (2, "UNVERIFIED", downlink.ACK_SCHEMA))
                self.assertIn(reason, value["reason"])
        f.plan(f.consumed())
        window = dict(attempt=f.attempt["path"], attempt_sha256=f.attempt["sha256"], policy=f.policy["path"],
                      policy_sha256=f.policy["sha256"], interpreter=INTERPRETER)
        rc, value = run_cli("observe-window", **window, window_dir=f.root / "evidence" / "w1", deadline_epoch=time.time() + 30)
        self.assertEqual((rc, value["evidence_class"], len(value["increments"])), (0, "MODEL_CONTEXT_CONSUMED", 1))
        f.plan(f.unverified("MATCHING_REAL_RESPONSE_REQUIRED"), 2)
        rc, value = run_cli("observe-window", **window, window_dir=f.root / "evidence" / "w2",
                            deadline_epoch=time.time() + 1.2, interval=1)
        self.assertEqual((rc, value["observation"], value["schema"]), (2, "UNVERIFIED", downlink.WINDOW_SCHEMA))
        self.assertTrue(value["reason"].startswith("deadline reached"), value["reason"])


def raw(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def api_hash(message):
    return digest(json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode())


def cli_args(args):
    return [x for k, v in args.items() for x in ("--" + k.replace("_", "-"), str(v))]


def run_cli(command, **args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = downlink.main([command, *cli_args(args)])
    return rc, json.loads(out.getvalue())


def ack_record(line=ACK, **changes):
    value = dict(type="assistant", sessionId=SESSION, uuid="ack",
                 message=dict(role="assistant", content=[dict(type="text", text=line)]))
    value.update(changes)
    return value


def handshake(path, binding, **changes):
    """The original handshake receipt fields ack-order joins on (mac_harness / upgrade_handshake_capability)."""
    value = dict(task_id="u21-fixture", executor_provider="claude", ack_nonce=NONCE, ack_line_expected=ACK,
                 marker=binding["receipt_marker"], handshake_precondition=dict(payload_sha256=binding["payload_sha256"]))
    value.update(changes)
    return write_json(path, value)


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
        self.chains = {}

    def attempt(self, name, cut=None, insert=None, final_text="Full body received."):
        """One queued-only original attempt with the measured 2.1.303 notice/body chain.

        cut keeps only events[:cut]; insert=(index, record) adds one record; the
        chain's records are kept in self.chains[name] for later appends."""
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
                  body_request, assistant("request-two", "response-body", [dict(type="text", text=final_text)])]
        self.chains[name] = dict(events=events, transcript=transcript, blob=blob, request=request,
                                 assistant=assistant, event=event)
        written = events[:cut]
        if insert is not None:
            written.insert(*insert)
        prefix = raw(dict(type="system", sessionId=SESSION, subtype="native_metadata"))
        transcript.write_bytes(prefix + b"".join(raw(obj) for obj in written))
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

    def eof(self, name):
        return self.chains[name]["transcript"].stat().st_size

    def append(self, name, *records):
        with self.chains[name]["transcript"].open("ab") as stream:
            for record in records:
                stream.write(record if isinstance(record, bytes) else raw(record))
        return self.eof(name)

    def observe(self, name, attempt, evidence, end=None):
        return downlink.observe(attempt, self.policy, INTERPRETER, evidence, self.eof(name) if end is None else end)

    def test_reviewed_observer_reports_consumption_only_for_the_complete_chain(self):
        attempt, evidence = self.attempt("complete")
        result = self.observe("complete", attempt, evidence)
        self.assertEqual(result["evidence_class"], "MODEL_CONTEXT_CONSUMED")
        self.assertEqual((result["observation"]["parallel_tool_results"], result["observation"]["native_user_received"]),
                         (2, False))
        self.assertEqual(result["snapshot"]["sha256"], result["observation"]["snapshot_sha256"])
        for name, cut, reason in (("queued_only", 1, "UNIQUE_EXACT_API_USER_WRAPPER_REQUIRED"),
                                  ("no_body_response", -1, "FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED")):
            with self.subTest(case=name):
                attempt, evidence = self.attempt(name, cut)
                with self.assertRaisesRegex(ContractError, "context not consumed: " + reason):
                    self.observe(name, attempt, evidence)

    def test_a_completed_proof_is_frozen_at_its_endpoint_against_later_growth_and_recarriage(self):
        """ROOT R54-F1/F3 counterexamples against the real observer: the pinned endpoint reproduces."""
        for name, later, reason in (
                ("growth", lambda c: raw(dict(type="system", sessionId=SESSION, pad="x" * downlink.MAX_SCAN)),
                 "outside the bounded window"),
                ("recarried_tail", lambda c: c["event"]("api-request", id="request-three", querySource="repl_main_thread",
                                                         tail=[c["blob"]["hash"]]),
                 "context not consumed: CORRESPONDING_WIRE_REQUEST_REQUIRED"),
                ("rematerialized_wrapper", lambda c: c["blob"], "context not consumed: UNIQUE_EXACT_API_USER_WRAPPER_REQUIRED")):
            with self.subTest(case=name):
                attempt, evidence = self.attempt(name)
                recorded = write_json(evidence / "result.json", self.observe(name, attempt, evidence))
                end = json_ref(recorded)["snapshot"]["end"]
                self.append(name, later(self.chains[name]))
                with self.assertRaisesRegex(ContractError, reason):
                    self.observe(name, attempt, evidence)
                self.assertEqual(downlink.verify(recorded, self.policy["sha256"], INTERPRETER), json_ref(recorded))
                with self.assertRaisesRegex(ContractError, "not a complete record boundary"):
                    self.observe(name, attempt, evidence, end - 1)

    def test_a_frozen_proof_survives_unconfirmed_receipt_drift_but_not_a_confirmed_native_receipt(self):
        """1A38 O1 with the real observer, which never reads receipt.json."""
        attempt, evidence = self.attempt("receipt")
        recorded = write_json(evidence / "result.json", self.observe("receipt", attempt, evidence))
        receipt = Path(attempt["path"]).parent / "receipt.json"
        write_json(receipt, dict(confirmed=False, state="NATIVE_QUEUED"))
        self.assertEqual(downlink.verify(recorded, self.policy["sha256"], INTERPRETER), json_ref(recorded))
        write_json(receipt, dict(confirmed=True))
        with self.assertRaisesRegex(ContractError, "confirmed native receipt keeps its native evidence"):
            downlink.verify(recorded, self.policy["sha256"], INTERPRETER)

    def test_an_early_refusal_is_provisional_and_the_window_terminalizes_on_the_later_proof(self):
        """ROOT R54-F2: refused before the final body response, consumed after it; no input either way."""
        attempt, evidence = self.attempt("early", -1)
        with self.assertRaisesRegex(ContractError, "FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED"):
            self.observe("early", attempt, evidence)
        clock = [time.time()]

        def sleep(seconds):
            self.assertGreater(seconds, 0, "a window never busy-waits")
            clock[0] += seconds
            self.append("early", self.chains["early"]["events"][-1])
        terminal = downlink.observe_window(attempt, self.policy, INTERPRETER, evidence / "window",
                                           deadline_epoch=clock[0] + 60, now=lambda: clock[0], sleep=sleep)
        first, second = (json_ref(ref) for ref in terminal["increments"])
        self.assertEqual((terminal["evidence_class"], first["provisional"], second["observation"]),
                         ("MODEL_CONTEXT_CONSUMED", True, "MODEL_CONTEXT_CONSUMED"))
        self.assertIn("FULL_BODY_REQUEST_AND_MATCHING_RESPONSE_REQUIRED", first["reason"])
        self.assertEqual(terminal["snapshot_end"], self.eof("early"))
        self.assertEqual(downlink.verify(terminal["result"], self.policy["sha256"], INTERPRETER)["evidence_class"],
                         "MODEL_CONTEXT_CONSUMED")

    def test_ack_order_with_the_real_observer_proof(self):
        """ROOT R53 cases: an ACK before the body response is refused even though consumption is proven."""
        def run(name, **attempt_options):
            attempt, evidence = self.attempt(name, **attempt_options)
            chain = self.chains[name]
            recorded = write_json(evidence / "result.json", self.observe(name, attempt, evidence))
            receipt = handshake(evidence / "handshake.json", json_ref(attempt)["native_binding"])
            return chain, recorded, receipt
        ack = lambda chain, rid: chain["assistant"](rid, "ack", [dict(type="text", text=ACK)])
        chain, recorded, receipt = run("ack_after")
        self.append("ack_after", ack(chain, "request-two"))
        order = downlink.ack_order(recorded, self.policy["sha256"], INTERPRETER, receipt, self.eof("ack_after"))
        self.assertEqual(order["ordering"], "ACK_AFTER_FULL_BODY_CONSUMPTION")
        self.assertGreater(order["ack"]["offset"], order["body_consumed_at_offset"])
        chain, recorded, receipt = run("ack_same_response", final_text=ACK)
        order = downlink.ack_order(recorded, self.policy["sha256"], INTERPRETER, receipt, self.eof("ack_same_response"))
        self.assertEqual(order["ack"]["offset"], order["body_consumed_at_offset"])
        # The early ACK answers the notice request, before the body was read; consumption is still proven.
        early = dict(type="assistant", sessionId=SESSION, version=self.version, requestRef="request-one", uuid="ack",
                     isSidechain=False, message=dict(type="message", role="assistant", id="msg_ack",
                                                     content=[dict(type="text", text=ACK)]))
        chain, recorded, receipt = run("ack_before", insert=(3, early))
        self.assertEqual(json_ref(recorded)["evidence_class"], "MODEL_CONTEXT_CONSUMED")
        with self.assertRaisesRegex(ContractError, "ACK precedes full body consumption"):
            downlink.ack_order(recorded, self.policy["sha256"], INTERPRETER, receipt, self.eof("ack_before"))


class SequentialContextClassTests(unittest.TestCase):
    """The owner40 0.4.78 sequential class is bound only by its own policy; v1 records keep their exact shape."""

    def fixture(self, sequential=True):
        temp = tempfile.TemporaryDirectory(prefix="context-downlink-seq-")
        self.addCleanup(temp.cleanup)
        return Fixture(temp.name, sequential=sequential)

    def test_sequential_policy_runs_its_own_observer_and_records_its_own_class(self):
        f = self.fixture()
        f.plan(f.consumed())
        result = f.observe()
        self.assertEqual((result["schema"], result["evidence_class"]),
                         (downlink.RESULT_SCHEMA, downlink.SEQUENTIAL_EVIDENCE_CLASS))
        self.assertEqual(result["argv"][:3], [INTERPRETER, "-BE", str(f.source / "scripts/model_context_sequential.py")])
        self.assertEqual((result["release"]["evidence_class"], result["release"]["observation_schema"]),
                         (downlink.SEQUENTIAL_EVIDENCE_CLASS, downlink.SEQUENTIAL_OBSERVATION_SCHEMA))
        [call] = f.calls()
        self.assertEqual((call["ignore_environment"], call["dont_write_bytecode"]), (1, True))
        recorded = write_json(f.root / "evidence" / "result.json", result)
        self.assertEqual(downlink.verify(recorded, f.policy["sha256"], INTERPRETER), json_ref(recorded))
        f.append(ack_record())
        self.assertEqual(f.order(recorded)["ordering"], "ACK_AFTER_FULL_BODY_CONSUMPTION")

    def test_classes_never_cross_between_policies_and_observer_outputs(self):
        def v1_output(f):
            return dict(f.consumed(), schema=downlink.OBSERVATION_SCHEMA, observation="MODEL_CONTEXT_CONSUMED")
        cases = (
            # A sequential policy accepts only the sequential observer's own schema and verdict.
            ("v1_output_under_v2_policy", True, lambda f: f.plan(v1_output(f)), "unexpected context observer output"),
            ("v1_verdict_in_v2_schema", True, lambda f: f.plan(f.consumed(observation="MODEL_CONTEXT_CONSUMED")),
             "unexpected context observer verdict"),
            ("v2_policy_with_v1_class", True,
             lambda f: (f.plan(f.consumed()), f.publish(evidence_class="MODEL_CONTEXT_CONSUMED")),
             "not v124-shaped or v168-sequential-shaped"),
            ("v2_policy_pointing_at_v1_observer", True,
             lambda f: (f.plan(f.consumed()), f.publish(observer=file_ref(f.source / "scripts/model_context_consumption.py"))),
             "not the manifest's observer"),
            ("v2_release_without_v1_module", True,
             lambda f: ((f.source / "scripts/model_context_consumption.py").unlink(), f.plan(f.consumed()), f.publish()),
             "release has no v1 observer module"),
            # A v1 policy never yields the sequential class, even when its observer prints it.
            ("v2_output_under_v1_policy", False,
             lambda f: f.plan(dict(f.consumed(), schema=downlink.SEQUENTIAL_OBSERVATION_SCHEMA,
                                   observation=downlink.SEQUENTIAL_EVIDENCE_CLASS)), "unexpected context observer output"),
            ("v2_verdict_in_v1_schema", False,
             lambda f: f.plan(f.consumed(observation=downlink.SEQUENTIAL_EVIDENCE_CLASS)),
             "unexpected context observer verdict"),
            ("v1_policy_with_v2_class", False,
             lambda f: (f.plan(f.consumed()), f.publish(evidence_class=downlink.SEQUENTIAL_EVIDENCE_CLASS)),
             "not v124-shaped or v168-sequential-shaped"),
        )
        for name, sequential, change, reason in cases:
            with self.subTest(case=name):
                f = self.fixture(sequential)
                change(f)
                with self.assertRaisesRegex(ContractError, reason):
                    f.observe()

    def test_recorded_sequential_proof_reproduces_only_under_its_own_policy_and_class(self):
        f = self.fixture()
        recorded = f.record()
        value = json_ref(recorded)
        self.assertEqual(value["evidence_class"], downlink.SEQUENTIAL_EVIDENCE_CLASS)
        calls = len(f.calls())
        with self.assertRaisesRegex(ContractError, "bound to another policy"):
            downlink.verify(recorded, "7" * 64, INTERPRETER)
        self.assertEqual(len(f.calls()), calls)
        # Relabeling a recorded sequential proof as the v1 class cannot reproduce.
        forged = write_json(f.root / "evidence" / "forged.json", dict(value, evidence_class="MODEL_CONTEXT_CONSUMED"))
        with self.assertRaisesRegex(ContractError, "does not reproduce"):
            downlink.verify(forged, f.policy["sha256"], INTERPRETER)
        forged = write_json(f.root / "evidence" / "forged-unknown.json", dict(value, evidence_class="OTHER"))
        with self.assertRaisesRegex(ContractError, "not a recorded context observation"):
            downlink.verify(forged, f.policy["sha256"], INTERPRETER)

    def test_v1_records_keep_their_exact_original_release_shape(self):
        f = self.fixture(sequential=False)
        result = json_ref(f.record())
        self.assertEqual(set(result["release"]), {"policy", "manifest", "observer", "source"})
        self.assertEqual(result["evidence_class"], "MODEL_CONTEXT_CONSUMED")

    def test_window_and_cli_terminalize_on_the_sequential_class(self):
        f = self.fixture()
        f.plan(f.consumed())
        window = dict(attempt=f.attempt["path"], attempt_sha256=f.attempt["sha256"], policy=f.policy["path"],
                      policy_sha256=f.policy["sha256"], interpreter=INTERPRETER)
        rc, value = run_cli("observe-window", **window, window_dir=f.root / "evidence" / "w1", deadline_epoch=time.time() + 30)
        self.assertEqual((rc, value["evidence_class"], value["observation"]),
                         (0, downlink.SEQUENTIAL_EVIDENCE_CLASS, downlink.SEQUENTIAL_EVIDENCE_CLASS))
        [increment] = value["increments"]
        self.assertEqual(json_ref(increment)["observation"], downlink.SEQUENTIAL_EVIDENCE_CLASS)
        f.plan(f.unverified("COMPLETE_READ_HASH_CHAIN_REQUIRED_WITHIN_BATCH_BUDGET"), 2)
        rc, value = run_cli("observe-window", **window, window_dir=f.root / "evidence" / "w2",
                            deadline_epoch=time.time() + 1.2, interval=1)
        self.assertEqual((rc, value["observation"], value["evidence_class"]), (2, "UNVERIFIED", None))
        self.assertTrue(value["reason"].startswith("deadline reached"), value["reason"])


class CausalContextClassTests(unittest.TestCase):
    """The owner40 0.4.80 causal class is bound only by owner40's own v215 publication reply."""

    def fixture(self, sequential=False, causal=True):
        temp = tempfile.TemporaryDirectory(prefix="context-downlink-causal-")
        self.addCleanup(temp.cleanup)
        return Fixture(temp.name, sequential=sequential, causal=causal)

    def test_causal_publication_runs_its_own_observer_and_records_its_own_class(self):
        f = self.fixture()
        f.plan(f.consumed())
        result = f.observe()
        self.assertEqual((result["schema"], result["evidence_class"]),
                         (downlink.RESULT_SCHEMA, downlink.CAUSAL_EVIDENCE_CLASS))
        self.assertEqual(result["argv"][:3], [INTERPRETER, "-BE", str(f.source / "scripts/model_context_causal.py")])
        release = result["release"]
        self.assertEqual((release["evidence_class"], release["observation_schema"], release["observer"],
                          release["manifest"], release["source"]),
                         (downlink.CAUSAL_EVIDENCE_CLASS, downlink.CAUSAL_OBSERVATION_SCHEMA, f.published["causal"],
                          f.published["manifest"], f.published["source"]))
        [call] = f.calls()
        self.assertEqual((call["ignore_environment"], call["dont_write_bytecode"]), (1, True))
        recorded = write_json(f.root / "evidence" / "result.json", result)
        self.assertEqual(downlink.verify(recorded, f.policy["sha256"], INTERPRETER), json_ref(recorded))
        f.append(ack_record())
        self.assertEqual(f.order(recorded)["ordering"], "ACK_AFTER_FULL_BODY_CONSUMPTION")

    def test_causal_class_never_crosses_with_the_other_policies_or_observer_outputs(self):
        output = lambda f, schema, observation: dict(f.consumed(), schema=schema, observation=observation)
        republish = lambda f, **fields: (f.plan(f.consumed()), f.publish(**fields))
        cases = (
            # The causal publication accepts only the causal observer's own schema and verdict.
            ("sequential_output_under_causal", "causal", lambda f: f.plan(output(
                f, downlink.SEQUENTIAL_OBSERVATION_SCHEMA, downlink.SEQUENTIAL_EVIDENCE_CLASS)),
             "unexpected context observer output"),
            ("sequential_verdict_in_causal_schema", "causal",
             lambda f: f.plan(f.consumed(observation=downlink.SEQUENTIAL_EVIDENCE_CLASS)),
             "unexpected context observer verdict"),
            ("publication_pointing_at_the_sequential_observer", "causal", lambda f: republish(
                f, source_publication=dict(f.published, causal=file_ref(f.source / "scripts/model_context_sequential.py"))),
             "not the manifest's observer"),
            # Only the published-and-checked reply binds; the earlier authorization (v1 ruling) does not.
            ("authorized_but_unpublished_ruling", "causal", lambda f: republish(
                f, status="PROSPECTIVE_CAUSAL_JOIN_FIX_AUTHORIZED_OLD_U24_UNCHANGED"),
             "not the v215 causal successor publication"),
            ("contract_of_another_class", "causal", lambda f: republish(
                f, causal_contract=dict(f.contract, class_name=downlink.SEQUENTIAL_EVIDENCE_CLASS)),
             "not the v215 causal successor publication"),
            ("contract_beyond_new_bindings", "causal", lambda f: republish(
                f, causal_contract=dict(f.contract, scope="HISTORICAL_WINDOWS")),
             "not the v215 causal successor publication"),
            ("contract_replacing_old_observers", "causal", lambda f: republish(
                f, causal_contract=dict(f.contract, default_old_observers_unchanged=False)),
             "not the v215 causal successor publication"),
            ("contract_missing", "causal", lambda f: republish(f, causal_contract=None),
             "not the v215 causal successor publication"),
            ("publication_of_another_schema", "causal", lambda f: republish(
                f, source_publication=dict(f.published, schema="installed-release-v1")),
             "not the v215 causal successor publication"),
            ("publication_missing", "causal", lambda f: republish(f, source_publication=None),
             "not the v215 causal successor publication"),
            # A policy-shaped file restating the publication cannot stand in for owner40's reply.
            ("policy_fields_in_place_of_the_publication", "causal", lambda f: republish(
                f, status=None, source_publication=None, causal_contract=None,
                disposition="ACCEPTED_FOR_EXPLICIT_NEW_POLICY_BINDING", evidence_class=downlink.CAUSAL_EVIDENCE_CLASS,
                native_user_evidence_class="NATIVE_USER_RECEIVED", complete_source=str(f.source),
                source_manifest=f.published["manifest"], observer=f.published["causal"]),
             "not the v215 causal successor publication"),
            ("release_without_sequential_module", "causal", lambda f: (
                (f.source / "scripts/model_context_sequential.py").unlink(), republish(f)),
             "release has no sequential observer module"),
            ("release_without_v1_module", "causal", lambda f: (
                (f.source / "scripts/model_context_consumption.py").unlink(), republish(f)),
             "release has no v1 observer module"),
            # The sequential and v1 policies never yield the causal class, even when their observers print it.
            ("causal_output_under_sequential_policy", "sequential", lambda f: f.plan(output(
                f, downlink.CAUSAL_OBSERVATION_SCHEMA, downlink.CAUSAL_EVIDENCE_CLASS)),
             "unexpected context observer output"),
            ("causal_verdict_in_sequential_schema", "sequential",
             lambda f: f.plan(f.consumed(observation=downlink.CAUSAL_EVIDENCE_CLASS)),
             "unexpected context observer verdict"),
            ("sequential_policy_claiming_the_causal_class", "sequential",
             lambda f: republish(f, evidence_class=downlink.CAUSAL_EVIDENCE_CLASS),
             "not v124-shaped or v168-sequential-shaped"),
            ("causal_output_under_v1_policy", "v1", lambda f: f.plan(output(
                f, downlink.CAUSAL_OBSERVATION_SCHEMA, downlink.CAUSAL_EVIDENCE_CLASS)),
             "unexpected context observer output"),
            ("v1_policy_claiming_the_causal_class", "v1",
             lambda f: republish(f, evidence_class=downlink.CAUSAL_EVIDENCE_CLASS),
             "not v124-shaped or v168-sequential-shaped"),
        )
        for name, mode, change, reason in cases:
            with self.subTest(case=name):
                f = self.fixture(sequential=mode == "sequential", causal=mode == "causal")
                change(f)
                with self.assertRaisesRegex(ContractError, reason):
                    f.observe()

    def test_recorded_causal_proof_reproduces_only_under_its_own_publication_and_class(self):
        f = self.fixture()
        recorded = f.record()
        value = json_ref(recorded)
        self.assertEqual(value["evidence_class"], downlink.CAUSAL_EVIDENCE_CLASS)
        calls = len(f.calls())
        with self.assertRaisesRegex(ContractError, "bound to another policy"):
            downlink.verify(recorded, "7" * 64, INTERPRETER)
        self.assertEqual(len(f.calls()), calls)
        # Relabeling a recorded causal proof as either older class cannot reproduce.
        for name, label in (("as-sequential", downlink.SEQUENTIAL_EVIDENCE_CLASS), ("as-v1", "MODEL_CONTEXT_CONSUMED")):
            with self.subTest(label=label):
                forged = write_json(f.root / "evidence" / f"forged-{name}.json", dict(value, evidence_class=label))
                with self.assertRaisesRegex(ContractError, "does not reproduce"):
                    downlink.verify(forged, f.policy["sha256"], INTERPRETER)
        # The recorded reply is pinned by its bytes; a changed publication cannot re-verify the old record.
        f.publish(causal_contract=dict(f.contract, scope="HISTORICAL_WINDOWS"))
        with self.assertRaisesRegex(ContractError, "context policy: file hash/size mismatch"):
            downlink.verify(recorded, value["release"]["policy"]["sha256"], INTERPRETER)

    def test_window_and_cli_terminalize_on_the_causal_class(self):
        f = self.fixture()
        f.plan(f.consumed())
        window = dict(attempt=f.attempt["path"], attempt_sha256=f.attempt["sha256"], policy=f.policy["path"],
                      policy_sha256=f.policy["sha256"], interpreter=INTERPRETER)
        rc, value = run_cli("observe-window", **window, window_dir=f.root / "evidence" / "w1", deadline_epoch=time.time() + 30)
        self.assertEqual((rc, value["evidence_class"], value["observation"]),
                         (0, downlink.CAUSAL_EVIDENCE_CLASS, downlink.CAUSAL_EVIDENCE_CLASS))
        [increment] = value["increments"]
        self.assertEqual(json_ref(increment)["observation"], downlink.CAUSAL_EVIDENCE_CLASS)
        rc, again = run_cli("verify", result=value["result"]["path"], result_sha256=value["result"]["sha256"],
                            policy_sha256=f.policy["sha256"], interpreter=INTERPRETER)
        self.assertEqual((rc, again["evidence_class"]), (0, downlink.CAUSAL_EVIDENCE_CLASS))


# owner40's exact v215 reply; its source publication is the complete 0.4.80 tree it pins.
CAUSAL_REPLY = (Path.home() / "Documents/cnm/tasks/02_Word_review/handoff/claude29-wip-20261018/u2078/r13"
                / "SURFACE40-REPLY-u24-observer-causal-order-v2.json")
CAUSAL_REPLY_SHA256 = "7bcbf02558e1d814bc2a48e9d4fc1910bfcb5e841c513b1c85576cb2360a6cbd"


@unittest.skipUnless(CAUSAL_REPLY.is_file(), "owner40's v215 causal publication is not present here")
class PinnedCausalObserverTests(unittest.TestCase):
    """Runs owner40's published 0.4.80 causal observer, unchanged, on the measured two-batch chain."""

    # The same original attempt/transcript builder as the 0.4.76 tests; only the chain after the fence differs.
    attempt, eof, append = PinnedObserverTests.attempt, PinnedObserverTests.eof, PinnedObserverTests.append

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="context-downlink-causal-real-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.policy = file_ref(CAUSAL_REPLY)
        self.assertEqual(self.policy["sha256"], CAUSAL_REPLY_SHA256)
        release = downlink.pinned_release(self.policy)
        names = {}
        for node in ast.parse(Path(release["source"], "scripts/model_context_consumption.py").read_bytes()).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("PREFIX", "SUFFIX", "VERSION"):
                    names[node.targets[0].id] = ast.literal_eval(node.value)
        self.prefix_text, self.suffix_text, self.version = names["PREFIX"], names["SUFFIX"], names["VERSION"]
        self.chains = {}
        # The same release under the separately bound sequential class, as the contrast observer.
        sequential = file_ref(Path(release["source"], "scripts/model_context_sequential.py"))
        self.sequential = write_json(self.root / "sequential-policy.json", dict(
            schema=downlink.SEQUENTIAL_POLICY_SCHEMA, disposition="ACCEPTED_FOR_EXPLICIT_NEW_POLICY_BINDING",
            evidence_class=downlink.SEQUENTIAL_EVIDENCE_CLASS, native_user_evidence_class="NATIVE_USER_RECEIVED",
            complete_source=release["source"], source_manifest=release["manifest"], observer=sequential))

    def observe(self, name, attempt, evidence, policy=None):
        return downlink.observe(attempt, policy or self.policy, INTERPRETER, evidence, self.eof(name))

    def chain(self, name, inverted=True, change=None):
        """One original attempt whose transcript carries the measured two-batch chain (hash, then Read).

        inverted writes each next api-request before the result it carries, as Claude 2.1.303 did in u24;
        change(events) edits that written order before it is appended."""
        attempt, evidence = self.attempt(name, 0)
        c = self.chains[name]
        notice, blob, request, call_hash, call_read, hash_result, read_result, body_request, response = c["events"]
        body = Path(json_ref(attempt)["body_reference"]["path"]).read_bytes().decode()
        read_block = dict(read_result["message"]["content"][0],
                          content="\n".join(f"{n}\t{line}" for n, line in enumerate(body.splitlines(), 1)))
        read_result = dict(read_result, message=dict(role="user", content=[read_block]))
        read_request = c["event"]("api-request", id="request-read", base=request["id"], querySource="repl_main_thread",
                                  tail=[api_hash(dict(role="user", content=hash_result["message"]["content"]))])
        call_read = dict(call_read, requestRef=read_request["id"])
        body_request = dict(body_request, base=read_request["id"],
                            tail=[api_hash(dict(role="user", content=[read_block]))])
        ordered = [notice, blob, request, call_hash, hash_result, read_request, call_read, read_result, body_request,
                   response]
        events = [ordered[n] for n in (0, 1, 2, 3, 5, 4, 6, 8, 7, 9)] if inverted else ordered
        if change:
            change(events)
        self.append(name, *events)
        return attempt, evidence

    def test_inverted_append_order_is_causally_consumed_and_still_refused_by_the_sequential_observer(self):
        """u24's claude_a shape: each next api-request is written before the result it carries."""
        attempt, evidence = self.chain("inverted")
        result = self.observe("inverted", attempt, evidence)
        observation = result["observation"]
        self.assertEqual((result["evidence_class"], observation["schema"], observation["parallel_tool_results"]),
                         (downlink.CAUSAL_EVIDENCE_CLASS, downlink.CAUSAL_OBSERVATION_SCHEMA, 2))
        self.assertEqual([b["result_file_order_between_requests"] for b in observation["sequential_batches"]],
                         [False, False])
        self.assertEqual((observation["historical_window_reclassified"], observation["native_user_received"]),
                         (False, False))
        recorded = write_json(evidence / "result.json", result)
        self.assertEqual(downlink.verify(recorded, self.policy["sha256"], INTERPRETER), json_ref(recorded))
        with self.assertRaisesRegex(ContractError, "context not consumed: SUCCESSFUL_ORDERED_NATIVE_RESULT_REQUIRED"):
            self.observe("inverted", attempt, evidence, self.sequential)
        receipt = handshake(evidence / "handshake.json", json_ref(attempt)["native_binding"])
        self.append("inverted", self.chains["inverted"]["assistant"]("request-two", "ack", [dict(type="text", text=ACK)]))
        order = downlink.ack_order(recorded, self.policy["sha256"], INTERPRETER, receipt, self.eof("inverted"))
        self.assertEqual(order["ordering"], "ACK_AFTER_FULL_BODY_CONSUMPTION")

    def test_in_order_chain_satisfies_both_the_causal_and_the_sequential_class(self):
        attempt, evidence = self.chain("ordered", inverted=False)
        causal = self.observe("ordered", attempt, evidence)
        sequential = self.observe("ordered", attempt, evidence, self.sequential)
        self.assertEqual((causal["evidence_class"], sequential["evidence_class"]),
                         (downlink.CAUSAL_EVIDENCE_CLASS, downlink.SEQUENTIAL_EVIDENCE_CLASS))
        self.assertEqual([b["result_file_order_between_requests"] for b in causal["observation"]["sequential_batches"]],
                         [True, True])
        self.assertEqual(causal["observation"]["proof_slices"], sequential["observation"]["proof_slices"])

    def test_a_broken_causal_link_stays_unverified_through_the_gate(self):
        def set_field(index, **fields):
            return lambda events: events[index].update(fields)
        # Inverted order: [4] is request-read, [7] request-two, [8] the Read result.
        for name, change, reason in (
                ("foreign_parent", set_field(8, parentUuid="foreign"), "SUCCESSFUL_CAUSAL_NATIVE_RESULT_REQUIRED"),
                ("foreign_base", set_field(4, base="foreign"), "IMMEDIATE_CAUSAL_REQUEST_BASE_REQUIRED"),
                ("missing_batch_sha", set_field(7, tail=[]), "IMMEDIATE_COMPLETE_RESULT_BATCH_HASH_REQUIRED")):
            with self.subTest(case=name):
                attempt, evidence = self.chain(name, change=change)
                with self.assertRaisesRegex(ContractError, "context not consumed: " + reason):
                    self.observe(name, attempt, evidence)


if __name__ == "__main__":
    unittest.main()
