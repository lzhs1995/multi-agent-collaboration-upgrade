"""Real Darwin sandbox: accept works; descendant connection/rename is refused."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

from orchestration_common import ContractError
from upgrade_task_socket_policy import restrict_agent_connections


class PolicyTests(unittest.TestCase):
    def test_exact_paths_required(self):
        with tempfile.TemporaryDirectory(prefix="sp", dir="/tmp") as tmp:
            root = Path(tmp).resolve()
            for physical, advertised in (("relative", root/"a"), ("/", root/"a"),
                                         (root/"../s", root/"a"), (root/"s", "../a")):
                with self.subTest(physical=physical), self.assertRaises(ContractError):
                    restrict_agent_connections(b"(version 1)\n(allow default)\n", physical, advertised)
            (root/"alias").symlink_to(root/"s")
            with self.assertRaisesRegex(ContractError, "PHYSICAL_ALIAS"):
                restrict_agent_connections(b"(version 1)\n", root/"alias", root/"a")

    @unittest.skipUnless(sys.platform == "darwin", "requires Darwin Seatbelt")
    def test_actual_accept_child_denial_aliases_rename_and_unrelated_ipc(self):
        with tempfile.TemporaryDirectory(prefix="sp", dir="/tmp") as tmp:
            root = Path(tmp).resolve()
            physical, advertised = root/"server", root/"alias"
            advertised.symlink_to(physical)
            profile = restrict_agent_connections(b"(version 1)\n(allow default)\n", physical, advertised)
            (root/"policy.sb").write_bytes(profile)
            script = r'''import errno,json,os,socket,subprocess,sys
s=socket.socket(socket.AF_UNIX);s.bind(sys.argv[1]);s.listen(2)
print('READY',flush=True)
c,_=s.accept();c.sendall(b'ACCEPTED')
code=r"""import errno,json,os,socket,sys
rows=[]
for name in ('physical','alias','extra_alias','renamed'):
    p=sys.argv[1]
    try:
        if name=='alias':p=sys.argv[2]
        if name=='extra_alias':
            p=sys.argv[1]+'.extra';os.symlink(sys.argv[1],p)
        if name=='renamed':
            p=sys.argv[1]+'.moved';os.rename(sys.argv[1],p)
        with socket.socket(socket.AF_UNIX) as s:s.settimeout(2);s.connect(p)
        rows.append(dict(name=name,connected=True))
    except OSError as e:rows.append(dict(name=name,errno=e.errno))
try:
    os.link(sys.argv[1],sys.argv[1]+'.hard')
    with socket.socket(socket.AF_UNIX) as s:s.settimeout(2);s.connect(sys.argv[1]+'.hard')
    rows.append(dict(name='hardlink',connected=True))
except OSError as e:rows.append(dict(name='hardlink',errno=e.errno))
with socket.socket(socket.AF_UNIX) as s:s.connect(sys.argv[3]);s.sendall(b'IPC')
with socket.socket() as s:s.connect(('127.0.0.1',int(sys.argv[4])));s.sendall(b'TCP')
print(json.dumps(rows))
"""
r=subprocess.run([sys.executable,'-I','-B','-c',code,*sys.argv[1:]],capture_output=True,text=True,timeout=10)
print(json.dumps(dict(rc=r.returncode,out=r.stdout,err=r.stderr)),flush=True)
print(json.dumps(dict(listen_fd_inheritable=os.get_inheritable(s.fileno()),accepted_fd_inheritable=os.get_inheritable(c.fileno()))),flush=True)
c.close();s.close()
'''
            with socket.socket(socket.AF_UNIX) as unrelated, socket.socket() as tcp:
                unrelated.bind(str(root/"unrelated")); unrelated.listen(1); unrelated.settimeout(5)
                tcp.bind(("127.0.0.1", 0)); tcp.listen(1); tcp.settimeout(5)
                child = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(root/"policy.sb"),
                    sys.executable, "-I", "-B", "-c", script, str(physical), str(advertised),
                    str(root/"unrelated"), str(tcp.getsockname()[1])], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline(), "READY\n")
                    with socket.socket(socket.AF_UNIX) as controller:
                        controller.settimeout(5); controller.connect(str(advertised))
                        self.assertEqual(controller.recv(8), b"ACCEPTED")
                        out, err = child.communicate(timeout=15)
                    self.assertEqual((child.returncode, err), (0, ""))
                    data, fds = map(json.loads, out.splitlines())
                    self.assertEqual((data["rc"], data["err"]), (0, ""))
                    rows = json.loads(data["out"])
                    self.assertEqual([r["name"] for r in rows],
                                     ["physical", "alias", "extra_alias", "renamed", "hardlink"])
                    self.assertTrue(all(r.get("errno") == 1 for r in rows), rows)
                    self.assertFalse(any(fds.values()))
                    for listener, expected in ((unrelated, b"IPC"), (tcp, b"TCP")):
                        conn, _ = listener.accept()
                        with conn:self.assertEqual(conn.recv(3), expected)
                finally:
                    if child.poll() is None: child.terminate()
                    child.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
