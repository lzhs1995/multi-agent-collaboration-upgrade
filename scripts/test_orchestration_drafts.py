"""Draft publication is immutable preparation, never a claim or input operation."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, write_json
from orchestration_drafts import draft_path, published_drafts
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture
from orchestration_transport import Transport


class DraftPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.f = CampaignFixture(self.root, version=3)
        self.r = Runner(self.f.ref, fixtures=True)
        self.run = "run-01"

    def snapshot(self):
        return {str(p): file_ref(p) for p in self.root.rglob("*") if p.is_file()}

    def test_publication_precedes_run_and_never_reserves_a_lane(self):
        before = self.snapshot()
        with patch.object(Transport, "__init__", side_effect=AssertionError("no transport")), \
             patch.object(Runner, "co", side_effect=AssertionError("no coordinator")):
            result = self.r.publish_drafts(self.run)
        self.assertFalse(result["dispatch_authorized"])
        self.assertEqual((result["nodes"], result["input_operations"]), (5, 0))
        self.assertEqual(json_ref(result["publication"]), self.r.drafts(self.run))
        self.assertEqual(set(self.snapshot()) - set(before), {
            str(draft_path(self.r, self.run)), str(draft_path(self.r, self.run).with_suffix(".lock"))})
        self.assertEqual(self.r.next_action(self.run)["status"], "NOT_STARTED")
        self.f.start()
        self.assertEqual(self.r.next_action(self.run)["recorded_claim_candidates"], ["local", "review", "parallel"])

    def test_idempotent_publication_preserves_original_bytes_and_inode(self):
        first = self.r.publish_drafts(self.run)
        path = Path(first["publication"]["path"])
        stat = path.stat()
        before = self.snapshot()
        self.assertEqual(self.r.publish_drafts(self.run), first)
        self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns), (stat.st_ino, stat.st_mtime_ns))
        self.assertEqual(self.snapshot(), before)

    def test_changed_drafts_are_preserved_and_rejected(self):
        path = draft_path(self.r, self.run)
        changed = self.r.drafts(self.run)
        changed["drafts"][0]["draft"]["scope"] = "changed scope"
        write_json(path, changed)
        before = file_ref(path)
        with self.assertRaisesRegex(ContractError, "published drafts differ"):
            self.r.publish_drafts(self.run)
        self.assertEqual(file_ref(path), before)

    def test_partial_publication_is_never_overwritten(self):
        path = draft_path(self.r, self.run)
        path.parent.mkdir()
        path.write_text('{"schema":')
        before = file_ref(path)
        with self.assertRaises(ContractError):
            self.r.publish_drafts(self.run)
        self.assertEqual(file_ref(path), before)

    def test_missing_publication_is_a_read_only_observation(self):
        before = self.snapshot()
        self.assertIsNone(published_drafts(self.r, self.run))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(draft_path(self.r, self.run).parent.exists())


if __name__ == "__main__":
    unittest.main()
