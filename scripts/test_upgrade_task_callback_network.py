"""Real sandboxed Unix callback reachability, no production sockets or models."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

from orchestration_common import ContractError
from upgrade_task_network import callback_path, model_only_profile, network_contract


class CallbackNetworkTests(unittest.TestCase):
    def test_contract_requires_new_exact_short_path(self):
        station=dict(codex=dict(base_url='http://127.0.0.1:3465'))
        claude=dict(env=dict(ANTHROPIC_BASE_URL='http://127.0.0.1:3445'))
        old=network_contract(station,claude)
        self.assertEqual(old['schema'],'upgrade-task-model-network-v1')
        for root in ('relative','/tmp/../tmp/x','/tmp/'+'x'*100):
            with self.subTest(root=root),self.assertRaises(ContractError):callback_path(root)
        with tempfile.TemporaryDirectory(prefix='cb',dir='/tmp') as tmp:
            root=Path(tmp).resolve();contract=network_contract(station,claude,evidence_root=root)
            self.assertEqual(contract['schema'],'upgrade-task-model-callback-network-v2')
            contract['callback']=str(root/'other')
            with self.assertRaises(ContractError):model_only_profile(b'(version 1)\n',contract,'claude_a')

    @unittest.skipUnless(sys.platform=='darwin','Darwin Seatbelt required')
    def test_original_claude_and_child_reach_callback_but_not_raw_broker(self):
        with tempfile.TemporaryDirectory(prefix='cb',dir='/tmp') as tmp:
            root=Path(tmp).resolve();path=Path(callback_path(root));path.parent.mkdir(parents=True)
            with socket.socket(socket.AF_UNIX) as callback,socket.socket(socket.AF_UNIX) as broker:
                callback.bind(str(path));callback.listen(12);callback.settimeout(3)
                raw=root/'raw';broker.bind(str(raw));broker.listen(12);broker.settimeout(.05)
                alias=root/'raw-alias';alias.symlink_to(raw)
                contract=network_contract(dict(codex=dict(base_url='http://127.0.0.1:3465')),
                    dict(env=dict(ANTHROPIC_BASE_URL='http://127.0.0.1:3445')),evidence_root=root)
                code="""import json,socket,sys
rows=[]
for path in sys.argv[1:]:
 try:
  with socket.socket(socket.AF_UNIX) as s:s.settimeout(1);s.connect(path);s.sendall(b'CALLBACK')
  rows.append('connected')
 except OSError as exc:rows.append(exc.errno)
print(json.dumps(rows))
"""
                child="import subprocess,sys; r=subprocess.run([sys.executable,'-I','-B','-c',sys.argv[1],*sys.argv[2:]],capture_output=True,text=True,timeout=5);print(r.stdout,end='');print(r.stderr,end='',file=sys.stderr);sys.exit(r.returncode)"
                for lane in ('codex','claude_a','claude_b'):
                    policy=root/(lane+'.sb');policy.write_bytes(model_only_profile(b'(version 1)\n(allow default)\n',contract,lane))
                    for descendant in (False,True):
                        with self.subTest(lane=lane,descendant=descendant):
                            argv=['-c',child,code] if descendant else ['-c',code]
                            result=subprocess.run(['/usr/bin/sandbox-exec','-f',str(policy),sys.executable,'-I','-B',
                                *argv,str(path),str(raw),str(alias)],capture_output=True,text=True,timeout=8,close_fds=True)
                            self.assertEqual((result.returncode,result.stderr),(0,''))
                            self.assertEqual(json.loads(result.stdout),[1 if lane=='codex' else 'connected',1,1])
                            if lane!='codex':
                                c,_=callback.accept()
                                with c:self.assertEqual(c.recv(8),b'CALLBACK')
                with self.assertRaises(TimeoutError):broker.accept()


if __name__=='__main__':unittest.main()
