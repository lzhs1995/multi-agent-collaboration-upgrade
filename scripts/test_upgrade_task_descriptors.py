"""Actual disposable PTY/FD probes; no Codex, Claude or production endpoint."""
import errno
import json
import os
from pathlib import Path
import pty
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack


@unittest.skipUnless(sys.platform=="darwin","Darwin launcher required")
class DescriptorTests(unittest.TestCase):
    def pty_run(self,program,args=(),fds=(),prefix=()):
        master,slave=pty.openpty()
        child=None
        try:
            child=subprocess.Popen([*prefix,sys.executable,"-B","-c",program,*args],
                stdin=slave,stdout=slave,stderr=slave,pass_fds=fds,
                cwd=Path(__file__).resolve().parent,close_fds=True)
            # Darwin can discard unread PTY output when the last slave closes.
            # Keep our own slave until the child's small response is drained.
            os.set_blocking(master,False)
            output=b""
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                ready,_,_=select.select([master],[],[],0.05)
                if ready:
                    try:block=os.read(master,8192)
                    except BlockingIOError:continue
                    except OSError as exc:
                        if exc.errno==errno.EIO:break
                        raise
                    if not block:break
                    output+=block
                    self.assertLess(len(output),65536,"unbounded fixture output")
                elif child.poll() is not None:break
            self.assertIsNotNone(child.poll(),output.decode())
            self.assertEqual(child.returncode,0,output.decode())
            return json.loads(output)
        finally:
            if slave is not None:os.close(slave)
            os.close(master)
            if child is not None and child.poll() is None:
                child.terminate();child.wait(timeout=5)

    def test_inherited_sockets_files_and_high_fd_close_without_harming_terminal(self):
        with ExitStack() as stack,tempfile.TemporaryDirectory(prefix="fd-",dir="/tmp") as tmp:
            listener=stack.enter_context(socket.socket(socket.AF_UNIX))
            listener.bind(str(Path(tmp)/"fixture"));listener.listen(1)
            client=stack.enter_context(socket.socket(socket.AF_UNIX));client.connect(str(Path(tmp)/"fixture"))
            accepted=stack.enter_context(listener.accept()[0])
            file=stack.enter_context(open(Path(tmp)/"data","w+b"))
            high=4096
            # Duplicate only inside the disposable child; never overwrite a
            # possibly live descriptor in the test runner.
            fds=tuple(s.fileno() for s in (listener,client,accepted,file))
            program='''import json,os,sys
from upgrade_task_descriptors import seal_terminal_descriptors
fds=json.loads(sys.argv[1]);high=int(sys.argv[2])
os.dup2(fds[-1],high,inheritable=True);fds.append(high)
before=[os.fstat(fd).st_mode for fd in fds]
seal=seal_terminal_descriptors()
remaining=[]
for fd in fds:
 try:os.fstat(fd);remaining.append(fd)
 except OSError as e:assert e.errno==9
print(json.dumps(dict(seal=seal,before=len(before),remaining=remaining,tty=[os.isatty(i) for i in (0,1,2)])))
'''
            result=self.pty_run(program,(json.dumps(fds),str(high)),fds)
            self.assertEqual(result["before"],len(fds)+1)
            self.assertEqual(result["remaining"],[])
            self.assertEqual(result["tty"],[True]*3)
            self.assertEqual(result["seal"]["extra_fds_remaining"],0)
            self.assertTrue(set(fds+(high,))<=set(result["seal"]["closed_fds"]))
            # Parent endpoints still work; the seal is confined to the child.
            client.sendall(b"OK");self.assertEqual(accepted.recv(2),b"OK")

    def test_seals_at_actual_exec_boundary(self):
        with ExitStack() as stack:
            first,second=socket.socketpair();stack.enter_context(first);stack.enter_context(second)
            target='''import json,os,sys
fd=int(sys.argv[1])
try:os.fstat(fd);closed=False
except OSError as e:closed=e.errno==9
print(json.dumps(dict(closed=closed,tty=[os.isatty(i) for i in (0,1,2)])))
'''
            program='''import os,sys
from upgrade_task_descriptors import exec_terminal_client
os.fstat(int(sys.argv[1]))
exec_terminal_client([sys.executable,'-I','-B','-c',sys.argv[2],sys.argv[1]],dict(os.environ))
'''
            result=self.pty_run(program,(str(first.fileno()),target),(first.fileno(),))
            self.assertEqual(result,dict(closed=True,tty=[True]*3))

    def test_network_profile_inherited_broker_counterexample_is_closed(self):
        from upgrade_task_network import model_only_profile
        from upgrade_client_isolation import sandbox_profile
        with tempfile.TemporaryDirectory(prefix="fd-network-",dir="/tmp") as tmp, \
                socket.socket() as broker,socket.socket() as model:
            root=Path(tmp).resolve()
            for s in (broker,model):s.bind(("127.0.0.1",0));s.listen(1)
            port=model.getsockname()[1];other=broker.getsockname()[1]
            contract=dict(schema="upgrade-task-model-network-v1",ports=dict(codex=port,claude_a=other,claude_b=other))
            policy=root/"policy.sb"
            policy.write_bytes(model_only_profile(sandbox_profile([root]),contract,"codex"))
            with socket.socket() as connected:
                connected.connect(broker.getsockname());receiver,_=broker.accept()
                with receiver:
                    receiver.settimeout(3)
                    program='''import json,os,sys
from upgrade_task_descriptors import seal_terminal_descriptors
fd=int(sys.argv[1]);os.write(fd,b'BEFORE')
seal=seal_terminal_descriptors()
try:os.write(fd,b'AFTER');closed=False
except OSError as e:closed=e.errno==9
print(json.dumps(dict(closed=closed,remaining=seal['extra_fds_remaining'])))
'''
                    result=self.pty_run(program,(str(connected.fileno()),),(connected.fileno(),),
                        ("/usr/bin/sandbox-exec","-f",str(policy)))
                    self.assertEqual(result,dict(closed=True,remaining=0))
                    connected.shutdown(socket.SHUT_WR)
                    data=b""
                    while True:
                        block=receiver.recv(64)
                        if not block:break
                        data+=block
                    self.assertEqual(data,b"BEFORE")

    def test_redirected_stdout_refuses_before_closing_any_inherited_channel(self):
        first,second=socket.socketpair()
        with first,second:
            program='''import json,os,sys
from orchestration_common import ContractError
from upgrade_task_descriptors import seal_terminal_descriptors
try:seal_terminal_descriptors();raise AssertionError('pipe accepted')
except ContractError as e:
 os.fstat(int(sys.argv[1]));print(json.dumps(dict(error=str(e),inherited_still_open=True)))
'''
            result=subprocess.run([sys.executable,"-B","-c",program,str(first.fileno())],
                capture_output=True,text=True,pass_fds=(first.fileno(),),timeout=10,
                cwd=Path(__file__).resolve().parent)
            self.assertEqual(result.returncode,0,result.stderr)
            value=json.loads(result.stdout)
            self.assertIn("REAL_TERMINAL_REQUIRED",value["error"])
            self.assertTrue(value["inherited_still_open"])

    def test_each_new_task_launcher_seals_again_after_pre_exec_work(self):
        program='''import json,os,sys,uuid
from pathlib import Path
from unittest.mock import patch
import upgrade_live_entry as entry
from orchestration_common import file_ref,write_json
root=Path(sys.argv[1]).resolve();lane=sys.argv[2];inherited=int(sys.argv[3])
control=root/'state/startup';control.mkdir(parents=True)
(root/'startup.json').write_text('{}');ref=file_ref(root/'startup.json')
write_json(control/'intent.json',dict(startup=ref,spawn=True,spawn_authorized=True))
os.environ['CMUX_SURFACE_ID']=str(uuid.uuid4());os.environ['CMUX_WORKSPACE_ID']=str(uuid.uuid4())
target="""import os,json,sys
r=[]
for fd in (int(sys.argv[1]),4096):
 try:os.fstat(fd);r.append(False)
 except OSError as e:r.append(e.errno==9)
print(json.dumps(dict(closed=r,tty=[os.isatty(i) for i in (0,1,2)])))
"""
argv=[sys.executable,'-I','-B','-c',target,str(inherited)]
spec=dict(stop_path=str(root/'STOP'),work_root=str(root),protected_surface_uuids=[],
    clients={lane:dict(environment={},argv=argv,native_root=str(root/lane),provider=lane)})
binding=dict(state_root=str(root/'state'),task_startup={'fixture':True})
def after_seal(env):
 # A descriptor opened during validation/hold/attachment must also be closed
 # at exec. This is an owned test file, not a production resource.
 fd=os.open(root/'later',os.O_CREAT|os.O_RDWR,0o600)
 os.dup2(fd,4096,inheritable=True);os.close(fd)
 return dict(os.environ)
with patch('cmux_daemon_identity.process',return_value={'pid':os.getpid(),'birth':[1,2]}), \\
     patch.object(entry,'start_server',side_effect=AssertionError('TASK must not start a nested server')), \\
     patch('upgrade_task_tui.wait_original_server'), \\
     patch('upgrade_task_tui.task_tui_argv',return_value=argv), \\
     patch.object(entry,'clean_child_environment',side_effect=after_seal):
 entry.start_client(ref,spec,binding,lane)
'''
        with tempfile.TemporaryDirectory(prefix="fd-launch-",dir="/tmp") as tmp:
            for lane in ("codex","claude_a","claude_b"):
                with self.subTest(lane=lane),socket.socket() as inherited:
                    root=Path(tmp)/lane
                    result=self.pty_run(program,(str(root),lane,str(inherited.fileno())),(inherited.fileno(),))
                    self.assertEqual(result,dict(closed=[True,True],tty=[True]*3))
                    record=json.loads((root/"state/startup"/(lane+".json")).read_bytes())
                    self.assertIn(inherited.fileno(),record["terminal_descriptors"]["closed_fds"])
                    self.assertEqual(record["terminal_descriptors"]["extra_fds_remaining"],0)


if __name__=="__main__":unittest.main()
