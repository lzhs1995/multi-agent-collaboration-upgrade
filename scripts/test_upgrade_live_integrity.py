"""Complete pin validation remains strict when large reads overlap."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import upgrade_live_integrity as integrity


class ParallelPinsTests(unittest.TestCase):
    def test_real_large_files_match_serial_and_detect_later_same_size_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            paths = [root / name for name in ("one", "two", "small")]
            for path, size in zip(paths, (16 * 1024 ** 2, 17 * 1024 ** 2, 31)):
                with path.open("wb") as handle:
                    handle.truncate(size)
            refs = [integrity.artifact_ref(path) for path in paths]
            self.assertEqual(integrity.verify_artifacts(refs), refs)
            with paths[1].open("r+b") as handle:
                handle.write(b"changed")
            with self.assertRaisesRegex(ValueError, "STARTUP_ARTIFACT_PIN_CHANGED"):
                integrity.verify_artifacts(refs)

    def test_all_errors_retained_and_all_workers_drained(self):
        refs = [{"path": str(i), "bytes": 16 * 1024 ** 2} for i in range(5)]
        refs.insert(1, {"path": "small", "bytes": 1})
        barrier, lock = threading.Barrier(3), threading.Lock()
        seen, active, peak = [], 0, 0

        def check(ref):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                if ref["path"] in ("0", "1", "2"):
                    barrier.wait(timeout=10)
                if ref["path"] in ("0", "small", "4"):
                    raise ValueError("bad:" + ref["path"])
                return ref
            finally:
                with lock:
                    seen.append(ref["path"])
                    active -= 1

        with mock.patch.object(integrity, "verify_artifact", side_effect=check):
            with self.assertRaisesRegex(ValueError, "bad:0") as caught:
                integrity.verify_artifacts(refs)
        self.assertCountEqual(seen, [ref["path"] for ref in refs])
        self.assertEqual(active, 0)
        self.assertGreaterEqual(peak, 3)
        self.assertEqual([(i, str(exc)) for i, exc in caught.exception.pin_failures],
                         [(0, "bad:0"), (1, "bad:small"), (5, "bad:4")])

    def test_three_worker_bound_and_original_order(self):
        refs = [{"path": str(i), "bytes": 16 * 1024 ** 2} for i in range(8)]
        with mock.patch.object(integrity, "ThreadPoolExecutor", wraps=ThreadPoolExecutor) as pool:
            with mock.patch.object(integrity, "verify_artifact", side_effect=lambda ref: ref):
                self.assertEqual(integrity.verify_artifacts(refs), refs)
        self.assertEqual(pool.call_args.kwargs["max_workers"], 3)

    def test_small_or_single_large_batch_needs_no_pool(self):
        for refs in ([], [{"bytes": 2}], [{"bytes": 16 * 1024 ** 2}, {"bytes": 2}]):
            with mock.patch.object(integrity, "ThreadPoolExecutor") as pool:
                with mock.patch.object(integrity, "verify_artifact", side_effect=lambda ref: ref):
                    self.assertEqual(integrity.verify_artifacts(refs), refs)
            pool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
