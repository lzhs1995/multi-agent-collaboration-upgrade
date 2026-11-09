"""Failure-boundary tests for exact, non-replayed native trust writes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from orchestration_common import ContractError, write_json
from upgrade_hook_review import apply_review


class ReviewWriteTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root=Path(tmp.name).resolve(); self.config=root/'config.toml'
        self.hooks={'PreToolUse':[{'matcher':'*','hooks':[{'type':'command','command':'/fixed/pre'}]}],
                    'PostToolUse':[{'matcher':'*','hooks':[{'type':'command','command':'/fixed/post'}]}]}
        pin=write_json(root/'hooks.json',{'hooks':self.hooks})
        self.hooks=json.loads(Path(pin['path']).read_text())['hooks']
        self.spec={'pins':{'codex/hooks.json':pin},'work_root':str(root)}
        self.reviewed=[{'event':event,'matcher':g['matcher'],'handler':h}
                       for event,groups in self.hooks.items() for g in groups for h in g['hooks']]
        self.inventory={'data':[{'cwd':str(root),'errors':[],'warnings':[],'hooks':[
            dict(key=f'{pin["path"]}:{event}:0:0',sourcePath=pin['path'],
                 eventName=rpc,matcher='*',command=command,source='user',pluginId=None,
                 handlerType='command',enabled=True,isManaged=False,
                 currentHash='sha256:'+digit*64,trustStatus='untrusted')
            for event,rpc,command,digit in [('pre_tool_use','preToolUse','/fixed/pre','a'),
                                           ('post_tool_use','postToolUse','/fixed/post','b')]]}]}

    def peer(self, *, unknown=False, mutate=False):
        outer=self
        class Peer:
            writes=[]
            def request(self, method, params):
                if method=='hooks/list':
                    value=copy.deepcopy(outer.inventory)
                    for h in value['data'][0]['hooks']:
                        if any(h['key'] in w['edits'][0]['value'] for w in self.writes):
                            h['trustStatus']='trusted'
                    if mutate and self.writes:
                        value['data'][0]['hooks'][0]['currentHash']='sha256:'+'f'*64
                    return value
                if method=='config/read':
                    return {'layers':[{'name':{'type':'user','file':str(outer.config)},'version':'v0'}]}
                if method=='config/batchWrite':
                    self.writes.append(params)
                    if unknown: raise TimeoutError('write result unknown')
                    return dict(status='ok',filePath=str(outer.config),
                                version=f'v{len(self.writes)}',overriddenMetadata=None)
                raise AssertionError(method)
        return Peer()

    def test_reviewed_commands_must_match_before_any_write(self):
        peer=self.peer(); reviewed=copy.deepcopy(self.reviewed)
        reviewed[0]['handler']['command']='/other'
        with self.assertRaisesRegex(ContractError,'EXACT_REVIEW'):
            apply_review(peer,self.spec,self.config,reviewed)
        self.assertEqual(peer.writes,[])

    def test_one_native_key_per_write_with_config_version_cas(self):
        peer=self.peer(); states,result=apply_review(peer,self.spec,self.config,self.reviewed)
        self.assertTrue(result['ready']); self.assertEqual(len(states),2)
        self.assertEqual([p['expectedVersion'] for p in peer.writes],['v0','v1'])
        self.assertTrue(all(len(p['edits'])==1 and len(p['edits'][0]['value'])==1 for p in peer.writes))

    def test_unknown_write_has_no_retry_or_next_handler(self):
        peer=self.peer(unknown=True)
        with self.assertRaises(TimeoutError):
            apply_review(peer,self.spec,self.config,self.reviewed)
        self.assertEqual(len(peer.writes),1)

    def test_changed_native_hash_never_returns_success(self):
        peer=self.peer(mutate=True)
        with self.assertRaisesRegex(ContractError,'READBACK_CHANGED'):
            apply_review(peer,self.spec,self.config,self.reviewed)
        self.assertEqual(len(peer.writes),2)


if __name__=='__main__': unittest.main()
