"""An advice body is an ordinary pinned reference, read by the unchanged reader."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cmux_prompt_reference import MAX_INLINE_BYTES, _ordinary, plan, verify, wire_reference
from jev_advice_worker import advise
from upgrade_advice_publish import (SCHEMA, SCOPE, advice_body, advice_dir, advice_marker, advice_tag,
                                    normalised_advice, publish_advice)
from upgrade_body_read import read_result

INPUTS = [dict(path="/fixture/input.json", sha256="a" * 64, bytes=21)]
CAMPAIGN = dict(path="/fixture/campaign.json", sha256="b" * 64, bytes=900)


class AdvicePublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "runs" / "run-01"
        answer = advise(state_text="route this work", lanes={}, bound=False, budget=None)
        self.advice, self.request = answer["advice"], answer["request"]

    def body(self, run_id="run-01", node_id="unbound", advice=None):
        advice = self.advice if advice is None else advice
        marker = advice_marker(run_id, node_id, advice_tag(advice))
        return advice_body(CAMPAIGN, run_id, node_id, advice, self.request, marker=marker, input_units=INPUTS)

    def test_body_is_always_long_enough_for_a_reference(self):
        # Even the smallest advice is multi-line JSON, so plan() never inlines it.
        body = self.body()
        self.assertIn("\n", body)
        planned = plan(body, json.loads(body)["marker"], advice_dir(self.root, "unbound"))
        self.assertIsNotNone(planned["reference"])
        self.assertLessEqual(len(planned["text"].encode()), MAX_INLINE_BYTES)
        published = publish_advice(self.root, "run-01", "unbound", body)
        self.assertEqual(published["reference"]["bytes"], len(body.encode("utf-8")))
        self.assertEqual(published["body_sha256"], published["reference"]["sha256"])

    def test_body_first_line_is_not_a_task_or_callback(self):
        body = self.body()
        self.assertEqual(body.splitlines()[0], "{")
        _ordinary(body)

    def test_body_states_its_own_contract_and_carries_its_input_version(self):
        value = json.loads(self.body())
        self.assertEqual(set(value), {"schema", "scope", "reply_required", "marker", "campaign", "run_id",
                                      "node_id", "input_units", "advice", "request"})
        self.assertEqual((value["schema"], value["scope"], value["reply_required"]), (SCHEMA, SCOPE, False))
        self.assertEqual(value["input_units"], INPUTS)
        self.assertEqual((value["run_id"], value["node_id"], value["campaign"]), ("run-01", "unbound", CAMPAIGN))

    def test_published_file_is_sha_named_read_only_single_link(self):
        published = publish_advice(self.root, "run-01", "unbound", self.body())
        path = Path(published["reference"]["path"])
        self.assertEqual(path.parent, advice_dir(self.root, "unbound"))
        self.assertEqual(path.name, published["body_sha256"] + ".txt")
        self.assertEqual(verify(published["reference"])["sha256"], published["body_sha256"])
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        path.chmod(0o600)
        with self.assertRaises(ValueError):
            read_result(published["notice"])

    def test_notice_round_trips_through_wire_reference(self):
        published = publish_advice(self.root, "run-01", "unbound", self.body())
        self.assertEqual(wire_reference(published["notice"]), published["reference"])
        result = read_result(published["notice"])
        self.assertEqual(result["body"], self.body())
        self.assertEqual(result["reference"], published["reference"])

    def test_marker_is_inline_safe_and_present_in_the_body(self):
        body = self.body()
        marker = json.loads(body)["marker"]
        self.assertRegex(marker, r"\AJEV_ADVICE_run-01_unbound_[0-9a-f]{16}\Z")
        self.assertIn(marker, body)
        published = publish_advice(self.root, "run-01", "unbound", body)
        self.assertTrue(published["notice"].startswith("STATUS: MESSAGE_REFERENCE_V2 " + marker + " "))
        with self.assertRaisesRegex(ValueError, "MESSAGE_REFERENCE_MARKER_NOT_INLINE"):
            publish_advice(self.root, "run-01", "has space", self.body(node_id="has space"))
        self.assertFalse(advice_dir(self.root, "has space").exists())

    def test_candidates_normalise_between_tuple_and_list(self):
        advice = dict(self.advice, status="SUGGEST", candidates=("claude_b", "claude_a"))
        self.assertEqual(normalised_advice(advice)["candidates"], ["claude_a", "claude_b"])
        self.assertEqual(advice_tag(advice), advice_tag(dict(advice, candidates=["claude_a", "claude_b"])))
        value = json.loads(self.body(advice=advice))
        self.assertEqual(tuple(value["advice"]["candidates"]), ("claude_a", "claude_b"))

    def test_a_same_byte_copy_at_another_path_is_not_the_same_reference(self):
        published = publish_advice(self.root, "run-01", "unbound", self.body())
        other = Path(self.temp.name).resolve() / "copy"
        other.mkdir()
        copied = other / Path(published["reference"]["path"]).name
        shutil.copyfile(published["reference"]["path"], copied)
        copied.chmod(0o400)
        # The notice embeds the absolute path, so a readable same-byte copy has
        # its own notice and its own reference; neither equals the original's.
        replanned = plan(self.body(), published["marker"], other)
        self.assertNotEqual(replanned["text"], published["notice"])
        copy_read = read_result(replanned["text"])
        self.assertEqual(copy_read["body"], self.body())
        self.assertNotEqual(copy_read["reference"], published["reference"])
        self.assertEqual(wire_reference(published["notice"])["path"], published["reference"]["path"])

    def test_publication_is_exclusive_and_never_repairs_a_changed_body(self):
        body = self.body()
        first = publish_advice(self.root, "run-01", "unbound", body)
        self.assertEqual(publish_advice(self.root, "run-01", "unbound", body)["reference"], first["reference"])
        with self.assertRaisesRegex(ValueError, "ADVICE_BODY_RUN_NODE_MISMATCH"):
            publish_advice(self.root, "run-02", "unbound", body)
        path = Path(first["reference"]["path"])
        path.chmod(0o600)
        path.write_text("changed after publication", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "MESSAGE_BODY_CHANGED"):
            publish_advice(self.root, "run-01", "unbound", body)
        self.assertEqual(path.read_text(encoding="utf-8"), "changed after publication")


if __name__ == "__main__":
    unittest.main()
