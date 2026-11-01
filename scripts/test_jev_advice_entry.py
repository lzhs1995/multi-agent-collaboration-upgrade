"""R545 P2 on-arm entry pieces: in-process key from an inherited fd, connection from pinned bytes."""
import os
import tempfile
import unittest
from pathlib import Path

import jev_advice_run as run
from orchestration_common import ContractError, file_ref


class CredentialFdTests(unittest.TestCase):
    def pipe_with(self, data):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, data)
        os.close(write_fd)
        return read_fd

    def test_key_is_read_once_and_the_fd_is_closed(self):
        fd = self.pipe_with(b"sk-test-offline\n")
        self.assertEqual(run.read_credential(fd), "sk-test-offline")
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_empty_or_multi_token_input_is_refused(self):
        for data in (b"", b"\n", b"two keys\n"):
            with self.subTest(data=data), self.assertRaises(ContractError):
                run.read_credential(self.pipe_with(data))


class RegisteredConnectionTests(unittest.TestCase):
    def source(self, body):
        root = Path(os.path.realpath(tempfile.mkdtemp(prefix="jevconn-")))
        self.addCleanup(lambda: [p.unlink() for p in root.iterdir()] and None or root.rmdir())
        path = root / "transport.py"
        path.write_text(body)
        return file_ref(path)

    def test_connection_comes_from_the_pinned_bytes(self):
        ref = self.source("def connect():\n    return 'offline-connection'\n\ndef bind(c, k, *, model):\n    return c\n")
        self.assertEqual(run.registered_connection(ref), "offline-connection")

    def test_changed_bytes_or_missing_connect_is_refused(self):
        ref = self.source("def bind(c, k, *, model):\n    return c\n")
        with self.assertRaises(ContractError):
            run.registered_connection(ref)
        Path(ref["path"]).write_text("def connect():\n    return 'other'\n")
        with self.assertRaises(Exception):
            run.registered_connection(ref)


if __name__ == "__main__":
    unittest.main()
