"""Kernel IPC/process provenance tests. No model clients or real children."""
from __future__ import annotations

import copy
import ctypes
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest

from orchestration_common import ContractError
from upgrade_child_peer import authenticate_hook_peer, kernel_peer
from upgrade_child_budget_protocol import hook_command
from cmux_daemon_identity import _args
from upgrade_child_service import read_packet


class KernelChildPeerTests(unittest.TestCase):
    def test_socket_packets_are_bounded_exact_and_cannot_supply_service_authority_overrides(self):
        from upgrade_child_budget_protocol import MAX_PACKET, SCHEMA, encode_packet
        import threading
        good = dict(schema=SCHEMA, lane="claude_a", payload=dict(hook_event_name="PreToolUse"))
        for label, raw in (("good", encode_packet(good)),
                           ("authority_override", encode_packet(dict(good, authority={"path": "/forged"}))),
                           ("double_packet", encode_packet(good) * 2),
                           ("duplicate_key", b'{"schema":"first","schema":"second"}\n'),
                           ("partial", encode_packet(good)[:-3]),
                           ("oversize", b"x" * (MAX_PACKET + 1))):
            with self.subTest(shape=label):
                sender, receiver = socket.socketpair(socket.AF_UNIX)
                def send():
                    try:
                        sender.sendall(raw)
                        sender.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                with sender, receiver:
                    receiver.settimeout(2)
                    thread = threading.Thread(target=send)
                    thread.start()
                    if label == "good":
                        self.assertEqual(read_packet(receiver), good)
                    else:
                        with self.assertRaises(ContractError):
                            read_packet(receiver)
                    receiver.close()
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())

    def test_native_root_environment_selection_is_explicit_and_never_exposes_credentials(self):
        raw = (bytes(ctypes.c_int(1)) + b"/original/claude\0\0/original/claude\0"
               b"HOME=/original/home\0CLAUDE_CONFIG_DIR=/original/native-a\0CODEX_HOME=/original/native-c\0"
               b"ANTHROPIC_AUTH_TOKEN=do-not-expose\0CMUX_SURFACE_ID=original-surface\0\0")
        argv, original = _args(raw)
        self.assertEqual(argv, ["/original/claude"])
        self.assertEqual(original, {"CMUX_SURFACE_ID": "original-surface"})
        argv, scoped = _args(raw, native_roots=True)
        self.assertEqual(scoped, dict(original, HOME="/original/home", CLAUDE_CONFIG_DIR="/original/native-a",
                                       CODEX_HOME="/original/native-c"))
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", scoped)

    def test_real_unix_peer_is_bound_to_the_kernel_pid_not_a_claimed_parent(self):
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as directory:
            path = str(Path(directory).resolve() / "peer.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen(1)
                code = "import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); s.sendall(b'x'); s.recv(1)"
                with subprocess.Popen([sys.executable, "-I", "-B", "-c", code, path]) as child:
                    listener.settimeout(5)
                    with listener.accept()[0] as peer:
                        identity = kernel_peer(peer)
                        self.assertEqual(identity["uid"], os.getuid())
                        self.assertEqual(identity["pid"], child.pid)
                        peer.sendall(b"x")
                    self.assertEqual(child.wait(timeout=5), 0)


class OriginalHookAncestryTests(unittest.TestCase):
    def setUp(self):
        self.python = "/original/python"
        self.controller = Path("/original/controller")
        self.command = hook_command(self.python, self.controller)
        self.client = dict(pid=100, birth=[1000, 1], executable="/original/claude", file_identity=[1, 2, 3, 4])
        self.session = dict(provider="claude", session_id="11111111-1111-4111-8111-111111111111",
            workspace_uuid="22222222-2222-4222-8222-222222222222",
            surface_uuid="33333333-3333-4333-8333-333333333333",
            native_config_root="/original/native-a", process=self.client)
        base_env = dict(CMUX_WORKSPACE_ID=self.session["workspace_uuid"],
                        CMUX_SURFACE_ID=self.session["surface_uuid"], HOME="/original/home",
                        CLAUDE_CONFIG_DIR=self.session["native_config_root"])
        self.processes = {
            102: dict(pid=102, ppid=101, birth=[1001, 2], executable=self.python,
                      file_identity=[1, 20, 3, 4], argv=shlex.split(self.command), env=base_env),
            101: dict(pid=101, ppid=100, birth=[1001, 1], executable="/bin/sh",
                      file_identity=[1, 21, 3, 4], argv=["/bin/sh", "-c", self.command], env=base_env),
            100: dict(self.client, ppid=1, argv=["/original/claude"], env=base_env),
        }
        self.peer = dict(pid=102, uid=os.getuid())

    def authenticate(self):
        return authenticate_hook_peer(self.peer, session=self.session, account_home="/original/home",
            python=self.python, controller=self.controller,
            process_reader=lambda pid: copy.deepcopy(self.processes[pid]))

    def test_exact_automatic_shell_chain_and_fresh_original_parent_are_required(self):
        observed = self.authenticate()
        self.assertEqual(observed["original_parent_pid"], 100)
        self.assertEqual(observed["peer_pid"], 102)
        self.assertEqual(observed["command"], self.command)
        self.assertEqual(observed["input_operations"], 0)
        controls = [
            lambda ps: ps[101].update(argv=["/bin/sh", "-c", "echo manual; " + self.command]),
            lambda ps: ps[102].update(argv=shlex.split(self.command) + ["--session", self.session["session_id"]]),
            lambda ps: ps[100].update(birth=[1000, 2]),
            lambda ps: ps[100]["env"].update(CMUX_SURFACE_ID="other-surface"),
            lambda ps: ps[100]["env"].update(CLAUDE_CONFIG_DIR="/original/native-b"),
            lambda ps: ps[102].update(ppid=1),
        ]
        original = copy.deepcopy(self.processes)
        for index, mutate in enumerate(controls):
            with self.subTest(mutation=index):
                self.processes = copy.deepcopy(original)
                mutate(self.processes)
                with self.assertRaises(ContractError):
                    self.authenticate()


if __name__ == "__main__":
    unittest.main()
