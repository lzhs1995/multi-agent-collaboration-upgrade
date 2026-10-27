"""Exact producer-journal joins; synthetic native files stay offline."""
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from orchestration_common import ContractError, canonical, digest, file_ref, utc_now, write_json
from test_orchestration_native import NativeFixture
from upgrade_input_provenance import (REGISTRATION_SCHEMA, ProvenanceJournal, classify_inputs, landing_refs,
                                      sender_command)

DRIVER = dict(kind="upgrade_driver", id="driver-1", entrypoint=["/fixture/bin/python3", "/fixture/send_notice.py"])
OPERATOR = dict(kind="operator_console", id="console-1", entrypoint=["/fixture/bin/console-relay"])


def iid(short):
    return "U2078-FIXTURE-" + short + "-0001"


class Run:
    """Two synthetic lanes and two registered producer sessions; every send goes through the journal."""

    def __init__(self, base):
        self.base = base = Path(base).resolve()
        self.lanes, self.controllers = {}, {}
        for group, name, provider in (("lanes", "codex", "codex"), ("lanes", "claude_a", "claude"),
                                      ("controllers", "driver-1", "claude"), ("controllers", "console-1", "claude")):
            (base / name).mkdir()
            fixture = NativeFixture(base / name, provider, session_id=str(uuid.uuid4()))
            fixture.session["surface_uuid"] = str(uuid.uuid4())
            getattr(self, group)[name] = fixture
        self.readers = {lane: f.reader for lane, f in self.lanes.items()}
        self.starts = {lane: r.snapshot() for lane, r in self.readers.items()}
        self.producer_starts = {pid: f.reader.snapshot() for pid, f in self.controllers.items()}
        self.registration = self.register("producers.json")
        self.not_before = utc_now()
        self.path = base / "provenance.jsonl"
        self.journal = ProvenanceJournal(self.path, "run-1", self.registration)

    def session_of(self, pid):
        f = self.controllers[pid]
        return dict(provider="claude", session_id=f.session["session_id"], transcript=f.session["transcript"])

    def register(self, name, run_id="run-1", extra=()):
        producers = [dict(p, session=self.session_of(p["id"])) for p in (DRIVER, OPERATOR)]
        return write_json(self.base / name, dict(schema=REGISTRATION_SCHEMA, run_id=run_id,
                                                 producers=[*producers, *extra]))

    def call(self, pid, command, *, at=None, call_id=None, name="Bash", args=None):
        """A tool call in the producer's own session; only the exact sender command evidences a send."""
        f = self.controllers[pid]
        return f.append(dict(type="assistant", sessionId=f.session["session_id"], timestamp=at or utc_now(),
            message=dict(role="assistant", model="test-model", content=[dict(
                type="tool_use", id=call_id or "toolu_" + uuid.uuid4().hex, name=name,
                input=args if args is not None else dict(command=command))])))

    def command(self, short, payload, *, lane="codex", producer=DRIVER):
        return sender_command(producer, iid(short), self.readers[lane].session["surface_uuid"],
                              digest(payload.encode()))

    def intent(self, short, *, lane, payload, reader=None, fence=None, producer=DRIVER, call=True):
        reader = reader or self.readers[lane]
        fence = fence or reader.snapshot()
        if call:
            self.call(producer["id"], sender_command(producer, iid(short), reader.session["surface_uuid"],
                                                     digest(payload.encode())))
        return self.journal.intent(intent_id=iid(short), producer=producer, lane=lane, reader=reader,
                                   fence=fence, payload=payload, purpose="fixture")

    def landed(self, short, refs):
        return self.journal.landed(intent_id=iid(short), refs=refs)

    def deliver(self, text):
        return lambda: self.user("codex", [dict(type="input_text", text=text)])

    def sessions(self):
        return {pid: dict(reader=f.reader, start=self.producer_starts[pid], end=f.reader.snapshot())
                for pid, f in self.controllers.items()}

    def append(self, lane, record):
        return self.lanes[lane].append(record)

    def user(self, lane, content):
        f = self.lanes[lane]
        if f.provider == "codex":
            return f.append(dict(type="response_item", timestamp=utc_now(),
                                 payload=dict(type="message", role="user", content=content)))
        return f.append(dict(type="user", sessionId=f.session["session_id"], timestamp=utc_now(),
                             message=dict(role="user", content=content)))

    def queued(self, text):
        sid = self.lanes["claude_a"].session["session_id"]
        enqueue = self.append("claude_a", dict(type="queue-operation", operation="enqueue", content=text,
                                               sessionId=sid, timestamp=utc_now()))
        self.append("claude_a", dict(type="queue-operation", operation="dequeue", sessionId=sid, timestamp=utc_now()))
        landing = self.append("claude_a", dict(type="attachment", sessionId=sid, timestamp=utc_now(),
                                               attachment=dict(type="queued_command", prompt=text)))
        return enqueue, landing

    def send(self, lane, short, payload, deliver, *, producer=DRIVER, landed=True, call=True):
        reader = self.readers[lane]
        fence = reader.snapshot()
        self.intent(short, lane=lane, payload=payload, fence=fence, producer=producer, call=call)
        deliver()
        refs = landing_refs(reader, fence, reader.snapshot(), payload)
        if landed:
            self.landed(short, refs)
        return refs

    def classify(self, journal=None, registration=None, sessions=None):
        ends = {lane: r.snapshot() for lane, r in self.readers.items()}
        return classify_inputs(journal or file_ref(self.path), registration or self.registration, "run-1",
                               self.readers, self.starts, ends, producer_sessions=sessions or self.sessions(),
                               not_before=self.not_before, not_after=utc_now())


class InputProvenanceTests(unittest.TestCase):
    def run_fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Run(temp.name)

    def test_every_input_shape_is_a_unit_and_unjournaled_input_is_unknown(self):
        run = self.run_fixture()
        sid = run.lanes["claude_a"].session["session_id"]
        image = dict(type="image", source=dict(type="base64", media_type="image/png", data="AA=="))
        # Inputs: native_text returns None for every one after the plain string, so
        # the v3 human count skipped them instead of keeping them unknown.
        run.user("claude_a", "plain")
        run.user("claude_a", [dict(type="text", text="typed"), image])
        run.user("claude_a", [dict(type="text", text="one"), dict(type="text", text="two")])
        run.queued("busy lane input")
        # Not inputs: the lane's own tool traffic, output and queue bookkeeping.
        run.user("claude_a", [dict(type="tool_result", tool_use_id="t1", content="ok")])
        run.append("claude_a", dict(type="assistant", sessionId=sid, timestamp=utc_now(),
                                    message=dict(role="assistant", content=[dict(type="text", text="reply")])))
        run.append("claude_a", dict(type="user", sessionId=sid, isSidechain=True, timestamp=utc_now(),
                                    message=dict(role="user", content="subagent prompt")))
        run.user("codex", [dict(type="input_text", text="plain")])
        run.user("codex", [dict(type="input_text", text="one"), dict(type="input_text", text="two")])
        run.user("codex", [dict(type="input_text", text="look"), dict(type="input_image", image_url="data:,")])
        run.append("codex", dict(type="response_item", timestamp=utc_now(), payload=dict(
            type="message", role="developer", content=[dict(type="input_text", text="harness instructions")])))
        result = run.classify()
        self.assertEqual([u["kind"] for u in result["units"]],
                         ["message"] * 3 + ["user", "user", "user", "enqueue", "queued_command"])
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=8))
        self.assertIsNone(result["human_interventions"])
        self.assertEqual(result["unresolved"], [])

    def test_exact_machine_joins_close_the_gate_at_zero(self):
        run = self.run_fixture()
        run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
        run.send("claude_a", "n2", "NOTICE n2", lambda: run.queued("NOTICE n2"))
        result = run.classify()
        self.assertEqual([(u["kind"], u["provenance"], u["intent_id"]) for u in result["units"]],
                         [("message", "MACHINE", iid("n1")), ("enqueue", "MACHINE", iid("n2")),
                          ("queued_command", "MACHINE", iid("n2"))])
        driver = run.controllers["driver-1"].session["transcript"]["path"]
        self.assertTrue(all(u["producer_call"]["path"] == driver for u in result["units"]))
        self.assertEqual(result["human_interventions"], 0)
        self.assertEqual((result["unresolved"], result["unlanded_intents"]), ([], []))

    def test_only_a_journaled_operator_input_counts_as_human(self):
        run = self.run_fixture()
        run.send("claude_a", "h1", "operator h1", lambda: run.queued("operator h1"), producer=OPERATOR)
        run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
        result = run.classify()
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=2, MACHINE=1, UNKNOWN=0))
        # The enqueue and its landing are one intervention.
        self.assertEqual(result["human_interventions"], 1)

    def test_unjournaled_input_beside_joined_input_keeps_the_gate_unknown(self):
        run = self.run_fixture()
        run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
        run.user("codex", [dict(type="input_text", text="typed by someone")])
        result = run.classify()
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=1, UNKNOWN=1))
        self.assertIsNone(result["human_interventions"])

    def test_any_join_key_change_attributes_nothing(self):
        for field, value in (("offset", 1), ("length", 1), ("device", 1), ("inode", 1),
                             ("sha256", "0" * 64), ("path", "/elsewhere.jsonl")):
            with self.subTest(field=field):
                run = self.run_fixture()
                refs = run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"), landed=False)
                forged = dict(refs[0])
                forged[field] = forged[field] + value if isinstance(value, int) else value
                run.landed("n1", [forged])
                result = run.classify()
                self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
                self.assertEqual([u["reason"] for u in result["unresolved"]], ["REF_IS_NOT_A_UNIT_OF_THIS_LANE"])

    def test_lane_session_surface_and_producer_must_match_the_pinned_lane(self):
        def lane_swap(run):
            other = run.readers["codex"]
            fence = other.snapshot()
            run.intent("n1", lane="claude_a", payload="NOTICE n1")
            run.user("codex", [dict(type="input_text", text="NOTICE n1")])
            run.landed("n1", landing_refs(other, fence, other.snapshot(), "NOTICE n1"))

        def session_swap(run):
            claude = run.readers["claude_a"]
            fence = claude.snapshot()
            run.intent("n1", lane="codex", reader=claude, fence=fence, payload="NOTICE n1")
            run.queued("NOTICE n1")
            run.landed("n1", landing_refs(claude, fence, claude.snapshot(), "NOTICE n1"))

        def surface_swap(run):
            run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
            # The run header pins another surface for this lane.
            run.lanes["codex"].session["surface_uuid"] = str(uuid.uuid4())

        for case, reason in ((lane_swap, "REF_IS_NOT_A_UNIT_OF_THIS_LANE"), (session_swap, "SESSION_MISMATCH"),
                             (surface_swap, "SURFACE_MISMATCH"),
                             (lambda run: self.forge(run, dict(kind="self_declared", id="driver-1")),
                              "UNREGISTERED_PRODUCER")):
            with self.subTest(case=getattr(case, "__name__", "forged_kind")):
                run = self.run_fixture()
                case(run)
                result = run.classify()
                self.assertEqual(result["counts"]["HUMAN_REGISTERED"] + result["counts"]["MACHINE"], 0)
                self.assertIsNone(result["human_interventions"])
                self.assertEqual([u["reason"] for u in result["unresolved"]], [reason])

    def forge(self, run, producer, *, call=True, ident=None):
        """An intent the writer would refuse, appended as a forger could; driver-1 still runs the exact send."""
        reader, ident = run.readers["codex"], ident or iid("n1")
        fence = reader.snapshot()
        if call:
            run.call("driver-1", sender_command(DRIVER, ident, reader.session["surface_uuid"], digest(b"NOTICE n1")))
        run.journal._append(dict(kind="intent", intent_id=ident, producer=producer, lane="codex",
            session_id=reader.session["session_id"], surface_uuid=reader.session["surface_uuid"],
            fence=fence, payload_sha256=digest(b"NOTICE n1"), payload_bytes=9, purpose="", at=utc_now()))
        run.user("codex", [dict(type="input_text", text="NOTICE n1")])
        run.journal.landed(intent_id=ident, refs=landing_refs(reader, fence, reader.snapshot(), "NOTICE n1"))

    def test_producer_must_be_registered_and_have_invoked_the_send(self):
        later = "2099-01-01T00:00:00Z"

        def exact(run):
            return run.command("n1", "NOTICE n1")

        def unsent(run):
            run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"), call=False)

        def call_in_other_session(run):
            run.call("console-1", exact(run))
            unsent(run)

        def typed_not_invoked(run):
            f = run.controllers["driver-1"]
            f.append(f.message("user", exact(run), at=utc_now()))
            unsent(run)

        def call_after_intent(run):
            unsent(run)
            run.call("driver-1", exact(run), at=later)

        def retried(run):
            run.call("driver-1", exact(run))
            run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))

        # R44: one call that prints, echoes, searches or writes the id, runs the command in the
        # background, or runs lookalike argv is not the send; each is checked alone.
        mentions = {
            "printf_literal": lambda run: run.call("driver-1", "printf '%s' 'literal reference " + iid("n1") + "'"),
            "echo_command": lambda run: run.call("driver-1", "echo " + exact(run)),
            "grep_journal": lambda run: run.call("driver-1", "grep " + iid("n1") + " /fixture/provenance.jsonl"),
            "other_payload": lambda run: run.call("driver-1", run.command("n1", "another payload")),
            "other_surface": lambda run: run.call("driver-1", run.command("n1", "NOTICE n1", lane="claude_a")),
            "other_intent": lambda run: run.call("driver-1", run.command("n1X", "NOTICE n1")),
            "trailing_space": lambda run: run.call("driver-1", exact(run) + " "),
            "extra_flag": lambda run: run.call("driver-1", exact(run) + " --dry-run"),
            "background": lambda run: run.call("driver-1", None, args=dict(command=exact(run), run_in_background=True)),
            "written_not_run": lambda run: run.call("driver-1", None, name="Write",
                                                     args=dict(file_path="/fixture/x", content=exact(run))),
        }

        def only(mention):
            return lambda run: (mention(run), unsent(run))

        def stale_call(run):
            run.call("driver-1", exact(run), at="2026-01-01T00:00:00Z")
            unsent(run)

        def replayed_call_id(run):
            run.call("driver-1", exact(run), call_id="toolu_fixed")
            unsent(run)
            run.call("driver-1", None, call_id="toolu_fixed", name="Read", args=dict(file_path="/fixture/x"))

        cases = (
            # R43: a recognized kind with an id no registration holds, and a registered id with another kind.
            ("unregistered_driver_id", lambda run: self.forge(run, dict(kind="upgrade_driver", id="not-in-any-registry")),
             "UNREGISTERED_PRODUCER"),
            ("unregistered_console_id", lambda run: self.forge(run, dict(kind="operator_console", id="not-in-any-registry")),
             "UNREGISTERED_PRODUCER"),
            ("registered_id_other_kind", lambda run: self.forge(run, dict(kind="operator_console", id="driver-1")),
             "UNREGISTERED_PRODUCER"),
            ("short_intent_id", lambda run: self.forge(run, dict(kind="upgrade_driver", id="driver-1"), ident="n1"),
             "INTENT_ID_NOT_RUN_UNIQUE"),
            ("no_call", lambda run: self.forge(run, dict(kind="upgrade_driver", id="driver-1"), call=False),
             "ONE_SENDING_INVOCATION_REQUIRED"),
            ("call_in_other_session", call_in_other_session, "ONE_SENDING_INVOCATION_REQUIRED"),
            ("typed_not_invoked", typed_not_invoked, "ONE_SENDING_INVOCATION_REQUIRED"),
            ("retried", retried, "ONE_SENDING_INVOCATION_REQUIRED"),
            *(("mention_" + name, only(mention), "ONE_SENDING_INVOCATION_REQUIRED") for name, mention in mentions.items()),
            ("call_after_intent", call_after_intent, "SENDING_INVOCATION_OUTSIDE_INTENT_WINDOW"),
            ("stale_call", stale_call, "SENDING_INVOCATION_OUTSIDE_INTENT_WINDOW"),
            ("replayed_call_id", replayed_call_id, "ONE_ORIGINAL_CALL_ID_REQUIRED"),
        )
        for name, case, reason in cases:
            with self.subTest(case=name):
                run = self.run_fixture()
                case(run)
                result = run.classify()
                self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
                self.assertIsNone(result["human_interventions"])
                self.assertEqual([u["reason"] for u in result["unresolved"]], [reason])
        # Control: the exact send is the evidence, and later calls that only quote the id leave it valid.
        run = self.run_fixture()
        sent = run.call("driver-1", exact(run))
        unsent(run)
        run.call("driver-1", "grep " + iid("n1") + " /fixture/provenance.jsonl", at=later)
        result = run.classify()
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=1, UNKNOWN=0))
        self.assertEqual(result["units"][0]["producer_call"], sent)
        reader = run.readers["codex"]
        for producer, ident, message in ((dict(kind="upgrade_driver", id="not-in-any-registry"), iid("n2"),
                                          "registered producer"),
                                         (dict(kind="operator_console", id="driver-1"), iid("n2"), "registered producer"),
                                         (DRIVER, "n2", "run-unique intent id")):
            with self.subTest(writer=producer["id"] + ":" + ident):
                with self.assertRaisesRegex(ContractError, message):
                    run.journal.intent(intent_id=ident, producer=producer, lane="codex", reader=reader,
                                       fence=reader.snapshot(), payload="x")

    def test_producer_session_must_be_supplied_and_be_the_registered_one(self):
        for name in ("missing", "swapped"):
            with self.subTest(case=name):
                run = self.run_fixture()
                run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"), call=False)
                run.call("console-1", run.command("n1", "NOTICE n1"))
                sessions = run.sessions()
                if name == "missing":
                    del sessions["driver-1"]
                else:
                    sessions["driver-1"] = sessions["console-1"]
                result = run.classify(sessions=sessions)
                self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
                first = "PRODUCER_SESSION_NOT_SUPPLIED" if name == "missing" else "producer reader is not the registered session"
                self.assertEqual([u["reason"] for u in result["unresolved"]], [first, "PRODUCER_SESSION_UNREADABLE"])

    def test_registration_must_be_the_one_the_journal_was_opened_with(self):
        run = self.run_fixture()
        run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
        other = run.register("producers-2.json", extra=[dict(DRIVER, id="driver-2", session=run.session_of("driver-1"))])
        foreign = run.register("producers-3.json", run_id="run-2")
        # A bare command name would bind whatever PATH resolves at send time.
        relative = run.register("producers-4.json", extra=[dict(DRIVER, id="driver-3", entrypoint=["send_notice.py"],
                                                                session=run.session_of("driver-1"))])
        with self.assertRaisesRegex(ContractError, "absolute argv"):
            ProvenanceJournal(run.base / "relative.jsonl", "run-1", relative)
        for registration, reason in ((other, "another registration"), (foreign, "foreign producer registration"),
                                     (relative, "absolute argv")):
            with self.subTest(reason=reason):
                result = run.classify(registration=registration)
                self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
                self.assertEqual(result["unresolved"][0]["intent_id"], None)
                self.assertIn(reason, result["unresolved"][0]["reason"])
        with self.assertRaisesRegex(ContractError, "another registration"):
            ProvenanceJournal(run.path, "run-1", other)

    def test_backfilled_or_reordered_intent_attributes_nothing(self):
        def fence_after_record(run):
            ref = run.user("codex", [dict(type="input_text", text="NOTICE n1")])
            run.intent("n1", lane="codex", payload="NOTICE n1")
            run.landed("n1", [ref])

        def record_older_than_intent(run):
            run.intent("n1", lane="codex", payload="NOTICE n1")
            ref = run.append("codex", dict(type="response_item", timestamp="2026-01-01T00:00:00Z", payload=dict(
                type="message", role="user", content=[dict(type="input_text", text="NOTICE n1")])))
            run.landed("n1", [ref])

        def landed_before_intent(run):
            fence = run.readers["codex"].snapshot()
            ref = run.user("codex", [dict(type="input_text", text="NOTICE n1")])
            run.landed("n1", [ref])
            run.intent("n1", lane="codex", fence=fence, payload="NOTICE n1")

        for case, reason in ((fence_after_record, "REF_BEFORE_FENCE_OR_UNORDERED"),
                             (record_older_than_intent, "NATIVE_RECORD_OUTSIDE_INTENT_WINDOW"),
                             (landed_before_intent, "LANDED_BEFORE_INTENT")):
            with self.subTest(case=case.__name__):
                run = self.run_fixture()
                case(run)
                result = run.classify()
                self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
                self.assertEqual([u["reason"] for u in result["unresolved"]], [reason])

    def test_identical_text_never_picks_a_winner(self):
        run = self.run_fixture()
        typed = lambda: run.user("codex", [dict(type="input_text", text="same")])
        run.send("codex", "n1", "same", typed)
        run.send("codex", "n2", "same", typed)
        result = run.classify()
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=2))
        self.assertEqual([u["reason"] for u in result["unresolved"]], ["PAYLOAD_NOT_RUN_UNIQUE"] * 2)
        # A copy typed between the fence and the producer's own record keeps the gate unknown
        # whichever record the producer names; the journal cannot tell the two apart.
        for pick, machine in ((0, 1), (1, 0)):
            with self.subTest(pick=pick):
                run = self.run_fixture()
                run.intent("n1", lane="codex", payload="NOTICE n1")
                refs = [run.user("codex", [dict(type="input_text", text="NOTICE n1")]) for _ in range(2)]
                run.landed("n1", [refs[pick]])
                result = run.classify()
                self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=machine, UNKNOWN=2 - machine))
                self.assertIsNone(result["human_interventions"])

    def test_foreign_torn_edited_or_stale_journal_attributes_nothing(self):
        def foreign(run):
            lines = run.path.read_bytes().split(b"\n")[:-1]
            run.path.write_bytes(b"".join(canonical(dict(json.loads(line), run_id="run-2")) for line in lines))

        def torn(run):
            run.path.write_bytes(run.path.read_bytes()[:-1])

        def edited(run):
            lines = run.path.read_bytes().split(b"\n")[:-1]
            entry = json.loads(lines[1])
            entry["producer"] = OPERATOR
            lines[1] = canonical(entry)[:-1]
            run.path.write_bytes(b"\n".join(lines) + b"\n")

        def reformatted(run):
            # The chain cannot see a rewrite of the last entry; one canonical form per entry can.
            lines = run.path.read_bytes().split(b"\n")[:-1]
            lines[-1] = json.dumps(json.loads(lines[-1])).encode()
            run.path.write_bytes(b"\n".join(lines) + b"\n")

        for case, reason in ((foreign, "foreign"), (torn, "entry boundary"), (edited, "chain broken"),
                             (reformatted, "noncanonical")):
            with self.subTest(case=case.__name__):
                run = self.run_fixture()
                run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
                case(run)
                result = run.classify()
                self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
                self.assertEqual(result["unresolved"][0]["intent_id"], None)
                self.assertIn(reason, result["unresolved"][0]["reason"])
        run = self.run_fixture()
        run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
        pinned = file_ref(run.path)
        run.intent("n2", lane="codex", payload="late")
        result = run.classify(pinned)
        self.assertEqual(result["counts"]["UNKNOWN"], 1)
        self.assertIn("hash/size mismatch", result["unresolved"][0]["reason"])

    def test_unlanded_or_enqueue_only_intent_attributes_nothing(self):
        run = self.run_fixture()
        run.send("claude_a", "n1", "NOTICE n1", lambda: run.queued("NOTICE n1"), landed=False)
        result = run.classify()
        self.assertEqual(result["unlanded_intents"], [iid("n1")])
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=2))
        run = self.run_fixture()
        sid = run.lanes["claude_a"].session["session_id"]
        enqueue = lambda: run.append("claude_a", dict(type="queue-operation", operation="enqueue",
                                                       content="NOTICE n1", sessionId=sid, timestamp=utc_now()))
        self.assertEqual(len(run.send("claude_a", "n1", "NOTICE n1", enqueue)), 1)
        result = run.classify()
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
        self.assertEqual([u["reason"] for u in result["unresolved"]], ["ENQUEUE_THEN_LANDING_REQUIRED"])

    def test_text_or_metadata_shaped_harness_input_gets_no_exemption(self):
        run = self.run_fixture()
        sid = run.lanes["claude_a"].session["session_id"]
        run.user("codex", [dict(type="input_text", text="# AGENTS.md instructions for /repo\n<INSTRUCTIONS>x</INSTRUCTIONS>")])
        run.user("codex", [dict(type="input_text", text="<environment_context>\n</environment_context>")])
        run.append("claude_a", dict(type="user", sessionId=sid, isMeta=True, timestamp=utc_now(),
                                    message=dict(role="user", content="<system-reminder>x</system-reminder>")))
        run.append("claude_a", dict(type="user", sessionId=sid, isCompactSummary=True, timestamp=utc_now(),
                                    message=dict(role="user", content="summary")))
        result = run.classify()
        self.assertEqual(result["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=4))
        self.assertIsNone(result["human_interventions"])

    def test_replaced_native_transcript_fails_closed(self):
        run = self.run_fixture()
        run.send("codex", "n1", "NOTICE n1", run.deliver("NOTICE n1"))
        ends = {lane: r.snapshot() for lane, r in run.readers.items()}
        sessions = run.sessions()
        path = run.lanes["codex"].path
        twin = path.with_name(path.name + ".twin")
        shutil.copyfile(path, twin)
        os.replace(twin, path)
        with self.assertRaisesRegex(ContractError, "replaced"):
            classify_inputs(file_ref(run.path), run.registration, "run-1", run.readers, run.starts, ends,
                            producer_sessions=sessions, not_before=run.not_before, not_after=utc_now())

    def test_writer_chains_entries_and_never_stores_payload_text(self):
        run = self.run_fixture()
        run.send("codex", "n1", "payload text stays out",
                 lambda: run.user("codex", [dict(type="input_text", text="payload text stays out")]))
        raw = run.path.read_bytes()
        entries = [json.loads(line) for line in raw.split(b"\n")[:-1]]
        self.assertEqual([(e["kind"], e["seq"]) for e in entries], [("header", 0), ("intent", 1), ("landed", 2)])
        self.assertNotIn(b"payload text stays out", raw)
        self.assertEqual(os.stat(run.path).st_mode & 0o777, 0o600)
        intent = entries[1]
        self.assertEqual((intent["producer"], intent["payload_sha256"]),
                         (dict(id="driver-1", kind="upgrade_driver"), digest(b"payload text stays out")))
        ProvenanceJournal(run.path, "run-1", run.registration)
        with self.assertRaisesRegex(ContractError, "foreign"):
            ProvenanceJournal(run.path, "run-2", run.registration)


if __name__ == "__main__":
    unittest.main()
