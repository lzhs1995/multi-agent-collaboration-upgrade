"""Offline native and journal fixtures, never model-load or efficiency proof."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from orchestration_native import NativeReader
import test_upgrade_communication_observer as fixtures
import upgrade_communication_attribution as attribution


class OriginalFixture:
    # Reuse only the original file/journal builders, not their TestCase methods.
    make = fixtures.ObserverTests.make
    stamp = fixtures.ObserverTests.stamp
    encoded = staticmethod(fixtures.ObserverTests.encoded)
    pin = fixtures.ObserverTests.pin
    append = fixtures.ObserverTests.append
    inbound = fixtures.ObserverTests.inbound
    materialize_attempt = fixtures.ObserverTests.materialize_attempt

    def __init__(self, root, provider):
        self.root, self.at = root, time.time() - 20
        self.make(provider)
        self.sample.update(run_id="warm-01", command="rtk proxy /fixture/helper send ORIGINAL")
        sender_provider = "codex" if provider == "claude" else "claude"
        session_id = str(uuid.uuid4())
        self.sender_path = self.root / (session_id + ".jsonl")
        header = (dict(type="session_meta", payload={"id": session_id}) if sender_provider == "codex"
                  else dict(type="user", sessionId=session_id, message=dict(role="user", content="initial")))
        header["timestamp"] = self.stamp(-5)
        self.sender_path.write_bytes(self.encoded(header))
        st = self.sender_path.stat()
        self.sender.update(provider=sender_provider, session_id=session_id,
            transcript=dict(path=str(self.sender_path), device=st.st_dev, inode=st.st_ino))
        self.campaign = dict(version=3, sessions={self.sample["sender"]: self.sender,
                                                 self.sample["receiver"]: self.receiver})
        self.readers = {lane: NativeReader(session, fixtures=True) for lane, session in self.campaign["sessions"].items()}
        self.header = dict(run={"run_id": "warm-01"}, started_at=self.stamp(-5),
                           native_start={lane: reader.snapshot() for lane, reader in self.readers.items()})
        self.ip, self.ap, _ = self.materialize_attempt()

    def call(self, *, call_id="send-1", command=None, at=-4):
        command = command or self.sample["command"]
        if self.sender["provider"] == "codex":
            return dict(type="response_item", timestamp=self.stamp(at), payload=dict(type="function_call",
                call_id=call_id, name="exec_command", arguments=json.dumps(dict(cmd=command, login=False))))
        return dict(type="assistant", timestamp=self.stamp(at), sessionId=self.sender["session_id"],
            message=dict(role="assistant", content=[dict(type="tool_use", id=call_id, name="Bash",
                                                       input={"command": command})]))

    def sender_append(self, *records):
        with self.sender_path.open("ab") as stream:
            for record in records:
                stream.write(self.encoded(record))

    def ends(self):
        return {lane: reader.snapshot() for lane, reader in self.readers.items()}

    def evaluate(self, *, ends=None):
        with patch.object(attribution, "campaign_plan", return_value=[self.sample]), \
             patch.object(attribution, "state_root", return_value=self.state):
            return attribution.attribute_inputs(self.campaign, self.header, ends or self.ends(), self.readers,
                                                 not_after=self.stamp(5))


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def make(self, provider="claude"):
        return OriginalFixture(self.root, provider)

    def complete(self, fixture):
        fixture.sender_append(fixture.call())
        fixture.append(fixture.inbound())

    def assert_unclassified(self, fixture, **kwargs):
        result = fixture.evaluate(**kwargs)
        self.assertEqual(result["inputs"], [], result)
        self.assertTrue(result["unresolved"], result)

    def test_both_directions_require_exact_original_records(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                f = self.make(provider)
                self.complete(f)
                result = f.evaluate()
                self.assertEqual(result["unresolved"], [], result)
                self.assertEqual(len(result["inputs"]), 1, result)
                item = result["inputs"][0]
                self.assertEqual(item["sender"], f.sample["sender"])
                self.assertEqual(item["receiver"], f.sample["receiver"])
                f.readers[item["receiver"]].record(item["native_record"])
                f.readers[item["sender"]].record(item["sender_call"])
                self.assertEqual(item["call_id"], "send-1")

    def test_no_sender_call_no_exemption(self):
        f = self.make()
        f.append(f.inbound())
        self.assert_unclassified(f)

    def test_real_helper_producer_and_uppercase_bridge_journal_preserve_hashes(self):
        import cmux_agent_adapter as adapter
        import cmux_native_delivery as native
        import cmux_prompt_reference as reference
        observation = fixtures.observation
        for provider in ("codex", "claude"):
            with self.subTest(provider=provider):
                f = self.make(provider)
                raw_identity = {key: value.upper() for key, value in f.identity.items()}
                actual = adapter.Adapter(home=f.root)
                row = dict(surface_id=raw_identity["target_surface_uuid"],
                           workspace_id=raw_identity["workspace_uuid"], surface_type="terminal", ref="surface:42")
                with patch.object(adapter.bridge, "pin_workspace", return_value=raw_identity), \
                     patch.object(adapter.bridge, "list_surfaces", return_value=[row]):
                    identity, _ = actual.live(raw_identity["target_surface_uuid"])
                self.assertEqual(identity, f.identity)
                with patch.object(reference, "body_root", return_value=f.state / "message-bodies-v1"):
                    produced = actual.request(identity, "send", f.sample["body"], f.sample["request_id"])
                self.assertEqual(produced, json.loads(f.ip.read_text()))
                self.assertEqual(actual.channel(identity).name, f.channel)
                journal = f.ap.parent.with_name(observation.sha(json.dumps(
                    [raw_identity["caller_surface_uuid"], f.marker], sort_keys=True)))
                f.ap.parent.rename(journal)
                f.ap = journal / "attempt-0001.json"
                attempt = json.loads(f.ap.read_text())
                attempt["binding"]["identity"] = raw_identity
                # Use the native producer itself for raw identity and original
                # transcript pins. Only platform discovery is a fixture seam.
                native_row = dict(raw_identity, tty=f.receiver["tty"])
                with ExitStack() as stack:
                    stack.enter_context(patch.object(adapter.bridge, "pin_workspace", return_value=raw_identity))
                    for name, value in {
                        "_tree_row": native_row, "_target_process": f.receiver["process"],
                        "_provider": provider, "_live_session": f.session,
                        "_transcript_path": f.path, "_public_process": f.receiver["process"],
                        "require_bound": None,
                    }.items():
                        stack.enter_context(patch.object(native, name, return_value=value))
                    stack.enter_context(patch.object(native.time, "time", return_value=f.at - 3))
                    attempt["native_binding"] = native.bind_target(adapter.bridge, raw_identity["target_surface_uuid"], f.notice)
                self.assertEqual(attempt["native_binding"]["identity"], raw_identity)
                event = attempt["events"][0]
                fence = event["native_input_fence" if provider == "codex" else "native_paste_fence"]
                fence["binding_sha256"] = observation.sha(json.dumps(attempt["native_binding"], sort_keys=True))
                f.ap.write_bytes(f.encoded(attempt))
                self.complete(f)
                original = f.ap.read_bytes()
                result = f.evaluate()
                self.assertEqual(len(result["inputs"]), 1, result)
                info = dict(state_root=str(f.state), campaign=f.campaign, python={"path": "/fixture/python"},
                            controller={"path": "/fixture/controller"}, rtk={"path": "/fixture/rtk"})
                row = observation.observe_sample(observation.Observer(), f.sample, info)
                self.assertEqual(row["native_reception"], "NATIVE_NOTICE_OBSERVED", row)
                self.assertEqual(f.ap.read_bytes(), original)
                # A second journal in another UUID case cannot be selected to
                # make a duplicated/ambiguous request look original.
                lower_journal = journal.with_name(observation.sha(json.dumps(
                    [identity["caller_surface_uuid"], f.marker], sort_keys=True)))
                lower_journal.mkdir()
                (lower_journal / "attempt-0001.json").write_bytes(original)
                self.assert_unclassified(f)
                row = observation.observe_sample(observation.Observer(), f.sample, info)
                self.assertEqual(row["native_reception"], "UNKNOWN", row)

    def test_malformed_original_evidence_remains_unknown(self):
        for mutation in (lambda a: a.update(native_binding=None),
                         lambda a: a.update(events=[None]),
                         lambda a: a["events"][0].update(native_paste_fence=None)):
            f = self.make()
            self.complete(f)
            attempt = json.loads(f.ap.read_text())
            mutation(attempt)
            f.ap.write_bytes(f.encoded(attempt))
            self.assert_unclassified(f)

    def test_prefix_or_forged_body_cannot_borrow_ordinary_intent(self):
        for payload in ("STATUS: COMM_SAMPLE forged", "same notice with extra instructions"):
            with self.subTest(payload=payload):
                f = self.make()
                f.sender_append(f.call())
                record = f.inbound()
                record["message"]["content"] = (payload if payload.startswith("STATUS:") else f.notice + " injected")
                f.append(record)
                self.assert_unclassified(f)
        f = self.make()
        self.complete(f)
        Path(f.reference["path"]).chmod(0o600)
        Path(f.reference["path"]).write_text(f.full + " forged")
        self.assert_unclassified(f)

    def test_wrong_original_sender_receiver_and_session_are_refused(self):
        for mutation in (lambda a: a["binding"]["identity"].update(caller_surface_uuid=str(uuid.uuid4())),
                         lambda a: a["native_binding"].update(session_id=str(uuid.uuid4())),
                         lambda a: a["native_binding"].update(process={"pid": 99}),
                         lambda a: a["native_binding"].update(tty="/dev/other")):
            f = self.make()
            self.complete(f)
            attempt = json.loads(f.ap.read_text())
            mutation(attempt)
            f.ap.write_bytes(f.encoded(attempt))
            self.assert_unclassified(f)

    def test_missing_changed_or_multiple_input_fences_are_refused(self):
        for mutation in (lambda a: a.update(events=[]),
                         lambda a: a["events"].append(copy.deepcopy(a["events"][0])),
                         lambda a: a["events"][0]["native_paste_fence"].update(binding_sha256="0" * 64),
                         lambda a: a["events"][0]["native_paste_fence"]["transcript"].update(tail_sha256="0" * 64)):
            f = self.make()
            self.complete(f)
            attempt = json.loads(f.ap.read_text())
            mutation(attempt)
            f.ap.write_bytes(f.encoded(attempt))
            self.assert_unclassified(f)

    def test_native_request_cannot_change_after_original_fence(self):
        f = self.make("codex")
        self.complete(f)
        attempt = json.loads(f.ap.read_text())
        attempt["events"][0]["native_request"]["params"]["threadId"] = str(uuid.uuid4())
        f.ap.write_bytes(f.encoded(attempt))
        self.assert_unclassified(f)

    def test_second_attempt_or_replacement_attempt_is_not_original(self):
        for replacement in (False, True):
            f = self.make()
            self.complete(f)
            f.ap.with_name("attempt-0002.json").write_bytes(f.ap.read_bytes())
            if replacement:
                f.ap.unlink()
            self.assert_unclassified(f)

    def test_duplicate_notice_even_with_invalid_timestamp_invalidates_exemption(self):
        for timestamp in (None, "no-date", "1900-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"):
            f = self.make()
            self.complete(f)
            duplicate = f.inbound()
            duplicate["timestamp"] = timestamp
            f.append(duplicate)
            self.assert_unclassified(f)

    def test_duplicate_call_id_or_second_send_cannot_be_hidden(self):
        for call_id, command in (("send-1", "unrelated command"), ("send-2", None)):
            f = self.make()
            self.complete(f)
            duplicate = f.call(call_id=call_id, command=command)
            duplicate["timestamp"] = None
            f.sender_append(duplicate)
            self.assert_unclassified(f)

    def test_foreign_session_and_sidechain_cannot_supply_notice_or_sender(self):
        for provider in ("codex", "claude"):
            f = self.make(provider)
            f.sender_append(f.call())
            record = f.inbound()
            if provider == "claude":
                record["sessionId"] = str(uuid.uuid4())
            else:
                f.append(dict(type="session_meta", payload={"id": str(uuid.uuid4())}))
            f.append(record)
            self.assert_unclassified(f)
        f = self.make("codex")
        call = f.call()
        call["isSidechain"] = True
        f.sender_append(call)
        f.append(f.inbound())
        self.assert_unclassified(f)
        f = self.make()
        f.sender_append(f.call())
        notice = f.inbound()
        notice["isSidechain"] = True
        f.append(notice)
        self.assert_unclassified(f)

    def test_exact_call_from_after_input_is_not_its_provenance(self):
        f = self.make()
        f.sender_append(f.call(at=2))
        f.append(f.inbound())
        self.assert_unclassified(f)

    def test_queued_only_is_not_a_native_user_input_exemption(self):
        f = self.make()
        f.sender_append(f.call())
        f.append(f.inbound(queued=True))
        self.assert_unclassified(f)

    def test_pinned_run_end_ignores_later_appends_including_duplicates(self):
        f = self.make()
        self.complete(f)
        ends = f.ends()
        expected = f.evaluate(ends=ends)
        self.assertEqual(len(expected["inputs"]), 1, expected)
        f.sender_append(f.call())
        f.append(f.inbound())
        with f.path.open("ab") as stream:
            stream.write(b'{"incomplete":')
        self.assertEqual(f.evaluate(ends=ends), expected)

    def test_notice_after_original_end_is_not_backfilled(self):
        f = self.make()
        f.sender_append(f.call())
        ends = f.ends()
        f.append(f.inbound())
        self.assert_unclassified(f, ends=ends)

    def test_partial_native_range_and_changed_support_pins_are_refused(self):
        f = self.make()
        self.complete(f)
        ends = f.ends()
        ends[f.sample["receiver"]]["offset"] -= 1
        self.assert_unclassified(f, ends=ends)
        original_scan = attribution._scan
        def changed(*args):
            original_scan(*args)
            f.ip.write_text(f.ip.read_text() + " ")
        with patch.object(attribution, "_scan", side_effect=changed):
            self.assert_unclassified(f)

    def test_pilot_and_cold_do_not_gain_ordinary_input_exemptions(self):
        f = self.make()
        self.complete(f)
        for run_id in ("pilot-01", "cold-01"):
            f.header["run"]["run_id"] = run_id
            with patch.object(attribution, "campaign_plan", side_effect=AssertionError("not a warm run")):
                result = attribution.attribute_inputs(f.campaign, f.header, f.ends(), f.readers, not_after=f.stamp(5))
            self.assertEqual(result["inputs"], [])
            self.assertEqual(result["unresolved"], [])


if __name__ == "__main__":
    unittest.main()
