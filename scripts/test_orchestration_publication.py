"""Process-crash publication checks in private directories; no durability claim.

Every crash case runs an actual child process and terminates with os._exit(77).
It verifies complete visible files after a process crash, not power-loss safety.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import (ContractError, evidence_names, read_regular,
                                  write_bytes_once, write_json)


CHILD = r'''
import os
import stat
import sys
from pathlib import Path
from orchestration_common import _publish_bytes

target, point, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
payload = b"complete-new-evidence\n" * 8192
original_open, original_fdopen = os.open, os.fdopen
original_fsync, original_link = os.fsync, os.link
original_replace, original_unlink = os.replace, os.unlink

def crash(name):
    if point == name:
        os._exit(77)

def opened(path, flags, *args, **kwargs):
    fd = original_open(path, flags, *args, **kwargs)
    if flags & os.O_CREAT:
        crash("after_stage_open")
    return fd

class Writer:
    def __init__(self, stream):
        self.stream = stream
    def __enter__(self):
        self.stream.__enter__()
        return self
    def __exit__(self, *args):
        return self.stream.__exit__(*args)
    def fileno(self):
        return self.stream.fileno()
    def write(self, raw):
        if point == "partial_write":
            self.stream.write(raw[:len(raw) // 2])
            self.stream.flush()
            os._exit(77)
        value = self.stream.write(raw)
        crash("after_write")
        return value
    def flush(self):
        self.stream.flush()

def synced(fd):
    original_fsync(fd)
    crash("after_directory_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "after_file_fsync")

def published(fn, *args, **kwargs):
    crash("before_publish")
    result = fn(*args, **kwargs)
    crash("after_publish")
    return result

def unlinked(path, *args, **kwargs):
    result = original_unlink(path, *args, **kwargs)
    crash("after_unlink")
    return result

os.open = opened
os.fdopen = lambda *args, **kwargs: Writer(original_fdopen(*args, **kwargs))
os.fsync = synced
os.link = lambda *args, **kwargs: published(original_link, *args, **kwargs)
os.replace = lambda *args, **kwargs: published(original_replace, *args, **kwargs)
os.unlink = unlinked
_publish_bytes(target, payload, exclusive=mode == "exclusive")
raise SystemExit("requested crash point was not reached")
'''


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_real_process_crashes_never_expose_torn_final_or_adopt_stage(self):
        points = ("after_stage_open", "partial_write", "after_write", "after_file_fsync",
                  "before_publish", "after_publish", "after_unlink", "after_directory_fsync")
        installed = {"after_publish", "after_unlink", "after_directory_fsync"}
        old, new = b"previous-complete-evidence\n", b"complete-new-evidence\n" * 8192
        for mode in ("exclusive", "replace"):
            for point in points:
                if mode == "replace" and point == "after_unlink":
                    continue  # replace consumes the staging name itself.
                with self.subTest(mode=mode, point=point):
                    directory = self.root / mode / point
                    directory.mkdir(parents=True)
                    target = directory / "result.json"
                    if mode == "replace":
                        target.write_bytes(old)
                    result = subprocess.run([sys.executable, "-B", "-c", CHILD, str(target), point, mode],
                        cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 77, result.stderr + result.stdout)
                    if point in installed:
                        self.assertEqual(read_regular(target), new)
                    elif mode == "replace":
                        self.assertEqual(read_regular(target), old)
                    else:
                        self.assertFalse(target.exists())
                    self.assertEqual(evidence_names(directory, {"result.json"}),
                                     {"result.json"} if target.exists() else set())
                    for stage in directory.glob(".*.tmp"):
                        self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o600)
                    if point == "after_publish" and mode == "exclusive":
                        # The surviving name shares the final inode: no claim of
                        # immutable independent stage storage or automatic GC.
                        stage, = directory.glob(".*.tmp")
                        self.assertEqual(stage.stat().st_ino, target.stat().st_ino)

    def test_exclusive_publication_preserves_existing_and_idempotent_bytes(self):
        target = self.root / "result.json"
        original = write_json(target, {"old": True}, exclusive=True)
        raw = read_regular(target)
        with self.assertRaises(FileExistsError):
            write_json(target, {"old": True}, exclusive=True)
        with self.assertRaises(FileExistsError):
            write_json(target, {"old": False}, exclusive=True)
        self.assertEqual(write_bytes_once(target, raw), original)
        with self.assertRaises(ContractError):
            write_bytes_once(target, b"changed")
        self.assertEqual(read_regular(target), raw)
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_only_owned_regular_private_stages_for_allowed_names_are_ignored(self):
        stage = self.root / (".result.json.stage-" + "a" * 32 + ".tmp")
        stage.write_bytes(b"unfinished")
        stage.chmod(0o600)
        self.assertEqual(evidence_names(self.root, {"result.json"}), set())
        self.assertEqual(evidence_names(self.root, {"other.json"}), {stage.name})
        stage.chmod(0o644)
        with self.assertRaises(ContractError):
            evidence_names(self.root, {"result.json"})
        stage.unlink()
        stage.symlink_to(self.root / "absent")
        with self.assertRaises(ContractError):
            evidence_names(self.root, {"result.json"})
        stage.unlink()
        stage.mkdir(mode=0o700)
        with self.assertRaises(ContractError):
            evidence_names(self.root, {"result.json"})

    def test_path_replacement_during_open_read_is_rejected(self):
        target, replacement = self.root / "result.json", self.root / "replacement"
        target.write_bytes(b"same bytes")
        replacement.write_bytes(b"same bytes")
        original_fstat, calls = os.fstat, 0

        def inspect(fd):
            nonlocal calls
            calls += 1
            value = original_fstat(fd)
            if calls == 2:
                replacement.replace(target)
            return value

        with patch("orchestration_common.os.fstat", side_effect=inspect):
            with self.assertRaisesRegex(ContractError, "file changed during read"):
                read_regular(target)


if __name__ == "__main__":
    unittest.main()
