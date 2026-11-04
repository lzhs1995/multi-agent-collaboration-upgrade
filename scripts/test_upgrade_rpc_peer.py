"""Actual local socket peer checks plus adversarial server incarnation tests."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

from orchestration_common import ContractError
from upgrade_rpc_peer import kernel_peer, server_pin, verify_peer


@unittest.skipUnless(sys.platform == "darwin" or sys.platform.startswith("linux"), "native peer support")
class PeerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rp", dir="/tmp")
        self.addCleanup(temp.cleanup)
        path = str(Path(temp.name).resolve() / "s")
        code = '''import socket,sys
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.bind(sys.argv[1]);s.listen(1);print("READY",flush=True)
c,_=s.accept();sys.stdin.buffer.read(1);c.close();s.close()
'''
        self.child = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, path],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.close_child)
        self.assertEqual(self.child.stdout.readline(), b"READY\n")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.sock.close)
        self.sock.settimeout(2)
        self.sock.connect(path)
        self.row = dict(pid=self.child.pid, uid=os.getuid(), birth=[1234, 567],
                        executable="/pinned/codex", file_identity=[1, 2, 3, 4],
                        argv=["/pinned/codex", "app-server", "--listen", "unix://" + path])
        self.pin = server_pin(self.row, uid=os.getuid())

    def close_child(self):
        if self.child.poll() is None:
            self.child.communicate(b"x", timeout=5)

    def test_actual_peer_is_server_child_and_same_uid_is_not_enough(self):
        self.assertEqual(kernel_peer(self.sock), {"pid": self.child.pid, "uid": os.getuid()})
        proof = verify_peer(self.sock, self.pin, lambda pid: dict(self.row))
        self.assertFalse(proof["foreground_authentication"])
        self.assertFalse(proof["enrollment_proven"])
        wrong = dict(self.pin, pid=os.getpid())
        with self.assertRaisesRegex(ContractError, "SERVER_PEER_CHANGED"):
            verify_peer(self.sock, wrong, lambda pid: dict(self.row))

    def test_restarted_or_replaced_server_and_changed_argv_refuse(self):
        changes = {"birth": [1234, 568], "executable": "/other/codex",
                   "file_identity": [1, 9, 3, 4],
                   "argv": ["/pinned/codex", "app-server", "--different"]}
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, "SERVER_INSTANCE_CHANGED"):
                verify_peer(self.sock, self.pin, lambda pid: dict(self.row, **{key: value}))

    def test_mid_observation_drift_refuses(self):
        reads = iter([self.row, dict(self.row, birth=[1235, 1])])
        with self.assertRaisesRegex(ContractError, "CHANGED_DURING_READ"):
            verify_peer(self.sock, self.pin, lambda pid: next(reads))

    def test_invalid_pin_and_non_server_are_rejected(self):
        for value in (None, {}, dict(self.pin, foreground_thread="invented"), dict(self.pin, pid=True),
                      dict(self.pin, birth=[1234, 1000000]), dict(self.pin, argv_sha256="not-a-pin"),
                      dict(self.pin, executable="relative"), dict(self.pin, file_identity=[1, True, 3, 4])):
            with self.subTest(value=value), self.assertRaises(ContractError):
                verify_peer(self.sock, value, lambda pid: self.row)
        with self.assertRaisesRegex(ContractError, "APP_SERVER_REQUIRED"):
            server_pin(dict(self.row, argv=["/pinned/codex", "--remote"]), uid=os.getuid())
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            with self.assertRaisesRegex(ContractError, "UNIX_STREAM_REQUIRED"):
                kernel_peer(tcp)


if __name__ == "__main__":
    unittest.main()
