"""R559/R561 P3: S2 closeout joins read -> disposition call/result -> named node acceptance in one Codex transcript."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orchestration_common import ContractError, file_ref
from test_upgrade_communication_attribution import OriginalFixture
import jev_advice_run as run
import upgrade_semantic_inputs as sem

IDS = ("us-note-1", "us-note-2", "us-note-3")


class SemanticCloseoutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="semclose-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.f = OriginalFixture(self.root, "codex")
        self.run_root = self.root / "run"
        (self.run_root / "jev-inbox").mkdir(parents=True)
        self.samples = [dict(run_id="warm-01", request_id=rid, body_sha256=str(i) * 64) for i, rid in enumerate(IDS)]
        self.co = SimpleNamespace(root=self.run_root, spec={"run_id": "warm-01"}, header=self.f.header,
                                  campaign=dict(semantic_input={"offline_fixture": True}, python={"path": "/fx/python"},
                                                adapter={"path": "/fx/adapter"}),
                                  campaign_ref={"path": "/fx/campaign.json", "sha256": "c" * 64},
                                  plan={"offline_fixture": True}, nodes={"inventory": {}, "merge": {}})
        self.reads, self.data = {}, {}
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(sem, "campaign_slots", return_value=self.samples))
        stack.enter_context(patch.object(sem, "observe_body_exposure", side_effect=self.exposure))
        stack.enter_context(patch("orchestration_campaign.control_script",
                                  side_effect=lambda *a, node_id, marker, **k: "ACCEPT " + node_id))
        stack.enter_context(patch("orchestration_evidence.marker_for", return_value="M"))
        stack.enter_context(patch("orchestration_binding.unbound_nodes", return_value=["inventory"]))
        (self.run_root / "jev-inbox/semantic-warm-01.json").write_text(json.dumps(dict(read_requests=[
            dict(request_id=s["request_id"], inner_body_sha256=s["body_sha256"]) for s in self.samples])))

    def append(self, payload):
        reader = self.f.readers["codex"]
        before = reader.snapshot()["offset"]
        self.f.append(dict(type="response_item", timestamp=self.f.stamp(1), payload=payload))
        return list(reader.records(before, reader.snapshot()["offset"]))[0][0]

    def tool(self, call_id, text):
        return self.append(dict(type="custom_tool_call", call_id=call_id, name="exec", input=text))

    def output(self, call_id, text):
        return self.append(dict(type="custom_tool_call_output", call_id=call_id, output=text))

    def read(self, rid):
        call = self.tool("read-" + rid, "read " + rid)
        self.reads[rid] = dict(call_record=call, result_record=self.output("read-" + rid, "complete body"))

    def dispose(self, rid, disposition="defer", work=(), *, mode="ok", between=None):
        """The generated recorder command as one exec_command call and its exit-0 result; or an R562 negative."""
        import shlex
        reason = "real reason for " + rid
        ref = run.record_disposition(self.co, rid, disposition, reason, work)
        argv = sem.advice_argv(self.co.campaign, self.co.campaign_ref) + [
            "--run", "warm-01", "--node", "inventory", "--semantic-disposition", rid,
            "--disposition", disposition, "--reason", reason] + [x for n in work for x in ("--work", n)]
        cmd = shlex.join(argv) if mode != "echo" else "echo " + shlex.join(argv)
        name = "web.run" if mode == "other_tool" else "exec_command"
        call = dict(type="function_call", call_id="disp-" + rid, name=name,
                    arguments=json.dumps(dict(cmd=cmd, login=False)))
        result = dict(type="function_call_output", call_id="disp-" + rid, output=json.dumps(
            dict(exit_code=1 if mode == "failed" else 0, output=json.dumps(ref))))
        for payload in ((result, call) if mode == "result_first" else (call, result)):
            if mode != "no_output" or payload is call:
                self.append(payload)
            if payload is call and between is not None:
                between()
        return ref

    def accept(self, node, *, ref=None):
        ref = ref or self.tool("accept-" + node, "ACCEPT " + node)
        evidence = self.run_root / (node + "-accepted.json")
        evidence.write_text(json.dumps(dict(acceptance_native=ref)))
        self.data[node] = dict(accepted_evidence=file_ref(evidence))
        return ref

    def exposure(self, campaign, header, record, native_end, readers, *, not_after):
        return dict(body_reads=[dict(request_id=rid, proof=proof) for rid, proof in self.reads.items()],
                    native_exposure_order=list(self.reads), exposure_matches_advice=len(self.reads) == 3)

    def closeout(self):
        return sem.semantic_closeout(self.co, self.data, self.f.readers, self.f.ends(), not_after=self.f.stamp(5))

    def by_id(self, out):
        return {m["request_id"]: m for m in out["messages"]}

    def test_read_then_disposition_then_named_acceptance_qualifies_each_message(self):
        for rid in IDS[:2]:
            self.read(rid)
            self.dispose(rid)
        self.read(IDS[2])
        self.dispose(IDS[2], "adopt", ["inventory"])
        self.accept("inventory")
        out = self.closeout()
        self.assertEqual((out["denominator"], out["qualified"], out["unqualified"]), (3, 3, []))
        self.assertEqual(out["semantic_correctness"], "NOT_JUDGED")
        third = self.by_id(out)[IDS[2]]
        self.assertEqual(third["disposition"]["disposition"], "adopt")
        self.assertEqual(third["work"][0]["node_id"], "inventory")
        self.assertIsNotNone(third["work"][0]["acceptance_native"])
        self.assertEqual(self.by_id(out)[IDS[0]]["work"], [])

    def test_missing_read_or_disposition_before_read_stays_unqualified(self):
        self.dispose(IDS[0])
        self.read(IDS[0])
        self.read(IDS[1])
        out = self.by_id(self.closeout())
        self.assertIn("disposition precedes the complete read", out[IDS[0]]["gaps"])
        self.assertIn("disposition missing", out[IDS[1]]["gaps"])
        self.assertEqual(out[IDS[2]]["gaps"], ["complete native read missing", "disposition missing"])
        self.assertFalse(any(m["qualified"] for m in out.values()))

    def test_acceptance_before_disposition_or_from_another_transcript_does_not_join(self):
        early = self.accept("inventory")
        self.read(IDS[0])
        self.dispose(IDS[0], "adopt", ["inventory"])
        self.read(IDS[1])
        self.dispose(IDS[1], "adopt", ["merge"])
        self.accept("merge", ref=dict(self.tool("accept-merge", "ACCEPT merge"), inode=early["inode"] + 1))
        out = self.by_id(self.closeout())
        self.assertIn("work inventory has no original same-Codex acceptance after the disposition", out[IDS[0]]["gaps"])
        self.assertIn("work merge has no original same-Codex acceptance after the disposition", out[IDS[1]]["gaps"])
        self.assertIsNone(out[IDS[1]]["work"][0]["acceptance_native"])

    def assert_not_joined(self, mode):
        self.read(IDS[0])
        self.dispose(IDS[0], mode=mode)
        message = self.by_id(self.closeout())[IDS[0]]
        self.assertIn("one native disposition call with its own output required", message["gaps"])
        self.assertIsNone(message["disposition_call"])

    # R562: each of these must not count as the recorder's original call/result.
    def test_call_without_its_own_result_is_not_joined(self):
        self.assert_not_joined("no_output")

    def test_unrelated_tool_carrying_the_argv_is_not_joined(self):
        self.assert_not_joined("other_tool")

    def test_failed_result_is_not_joined(self):
        self.assert_not_joined("failed")

    def test_result_before_call_is_not_joined(self):
        self.assert_not_joined("result_first")

    def test_echo_of_the_command_is_not_joined(self):
        self.assert_not_joined("echo")

    def test_generated_call_and_result_join_and_record_their_refs(self):
        self.read(IDS[0])
        self.dispose(IDS[0])
        message = self.by_id(self.closeout())[IDS[0]]
        self.assertTrue(message["qualified"], message["gaps"])
        self.assertLess(message["disposition_call"]["call_record"]["offset"],
                        message["disposition_call"]["result_record"]["offset"])

    def test_work_accepted_before_the_recorder_result_does_not_join(self):
        self.read(IDS[0])
        self.dispose(IDS[0], "adopt", ["inventory"], between=lambda: self.accept("inventory"))
        self.assertIn("work inventory has no original same-Codex acceptance after the disposition",
                      self.by_id(self.closeout())[IDS[0]]["gaps"])

    def test_no_inbox_order_keeps_all_three_in_the_denominator(self):
        (self.run_root / "jev-inbox/semantic-warm-01.json").unlink()
        out = self.closeout()
        self.assertEqual((out["exposure_status"], out["denominator"], out["qualified"]), ("NOT_RECORDED", 3, 0))

    def test_recorder_refuses_bad_input_and_a_second_disposition(self):
        for args in (("unknown-id", "defer", "r"), (IDS[0], "maybe", "r"), (IDS[0], "defer", "  "),
                     (IDS[0], "adopt", "r", ["new-task"])):
            with self.subTest(args=args), self.assertRaises(ContractError):
                run.record_disposition(self.co, *args)
        run.record_disposition(self.co, IDS[0], "defer", "r")
        with self.assertRaises(Exception):
            run.record_disposition(self.co, IDS[0], "refuse", "other")


class DeriveRunEntryTests(unittest.TestCase):
    """R559/R562: the real closeout caller; non-S2 output keeps its shape, S2 keeps the fixed denominator."""
    from test_orchestration_v3 import ThreeLaneTests
    only, begin, complete = ThreeLaneTests.only, ThreeLaneTests.begin, ThreeLaneTests.complete

    def setUp(self):
        from orchestration_test_support import CampaignFixture
        from orchestration_verify import finish_run
        from orchestration_common import read_json
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.f = CampaignFixture(temp.name, version=3)
        self.only("review")
        self.co = self.f.start()
        self.complete(self.co, "review", self.begin(self.co, "review"))
        self.baseline = finish_run(self.co)
        self.clock = read_json(self.co.root / "finish.json")["closeout_clock"]
        self.end = {k: c.reader.snapshot() for k, c in self.f.clients.items()}
        run_id = self.co.spec["run_id"]
        self.samples = [dict(run_id=run_id, request_id=rid, body_sha256=str(i) * 64) for i, rid in enumerate(IDS)]

    def derive(self, **patches):
        from orchestration_verify import derive_run
        self.co.campaign = dict(self.co.campaign, semantic_input={"offline_fixture": True})
        with patch.object(sem, "campaign_slots", return_value=self.samples), \
                patch("orchestration_binding.unbound_nodes", return_value=["review"]), \
                patch.object(sem, "observe_body_exposure", **patches):
            return derive_run(self.co, self.end, closeout_clock=self.clock)

    def test_non_s2_closeout_has_no_semantic_section(self):
        self.assertNotIn("semantic_inputs", self.baseline["metrics"])

    def test_s2_closeout_without_inbox_order_keeps_three_unqualified(self):
        out = self.derive(side_effect=AssertionError("no inbox order, no exposure call"))["metrics"]["semantic_inputs"]
        self.assertEqual((out["exposure_status"], out["denominator"], out["qualified"]), ("NOT_RECORDED", 3, 0))
        self.assertEqual(out["unqualified"], list(IDS))

    def test_s2_failed_exposure_observation_stays_in_the_denominator(self):
        inbox = self.co.root / "jev-inbox"
        inbox.mkdir()
        (inbox / ("semantic-" + self.co.spec["run_id"] + ".json")).write_text("{}")
        out = self.derive(side_effect=ContractError("fixture lineage changed"))["metrics"]["semantic_inputs"]
        self.assertTrue(out["exposure_status"].startswith("OBSERVATION_FAILED: fixture lineage changed"))
        self.assertEqual((out["denominator"], out["qualified"]), (3, 0))


class DispositionCliTests(unittest.TestCase):
    def test_disposition_carries_no_key_registration_or_partial_fields(self):
        base = ["--campaign", "/x", "--campaign-sha256", "0" * 64, "--run", "r", "--node", "n"]
        for extra in (["--semantic-disposition", "m"], ["--semantic-disposition", "m", "--disposition", "defer",
                      "--reason", "r", "--credential-fd", "0"], ["--work", "n"]):
            with self.subTest(extra=extra), patch("orchestration_run.Runner", side_effect=AssertionError("ran")):
                self.assertEqual(run.main(base + extra), 2)


if __name__ == "__main__":
    unittest.main()
