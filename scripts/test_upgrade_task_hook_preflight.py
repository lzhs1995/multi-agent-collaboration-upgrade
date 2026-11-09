"""Hook inventory matching uses realistic official metadata, no model calls."""
import copy
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from orchestration_common import ContractError, write_json
from upgrade_task_hook_preflight import validate_inventory


class HookPreflightTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.pin = write_json(self.root/'hooks.json', {'hooks': {
            'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': '/fixed/pre'}]}],
            'PostToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': '/fixed/post'}]}]}})
        self.spec = {'pins': {'codex/hooks.json': self.pin}, 'work_root': str(self.root)}
        self.items = [dict(key=f'{self.pin["path"]}:{event}:0:0', sourcePath=self.pin['path'],
            command=command, eventName=rpc, matcher='*', source='user', pluginId=None,
            handlerType='command', enabled=True, isManaged=False, currentHash='sha256:'+'a'*64,
            trustStatus='trusted') for event, rpc, command in (
            ('pre_tool_use', 'preToolUse', '/fixed/pre'), ('post_tool_use', 'postToolUse', '/fixed/post'))]
        self.response = {'data': [{'cwd': str(self.root), 'errors': [], 'warnings': [], 'hooks': self.items}]}

    def test_trusted_inventory_does_not_claim_automatic_execution(self):
        result = validate_inventory(self.spec, self.response)
        self.assertTrue(result['ready'])
        self.assertFalse(result['automatic_execution_proven'])
        self.assertEqual(result['configuration_writes'], 0)

    def test_untrusted_and_modified_handlers_require_review(self):
        for state in ('untrusted', 'modified'):
            self.items[1]['trustStatus'] = state
            result = validate_inventory(self.spec, self.response)
            self.assertFalse(result['ready'])
            self.assertEqual(result['pending'], [self.items[1]['key']])

    def test_missing_duplicate_foreign_or_disabled_hooks_never_pass(self):
        for change in ('missing', 'duplicate', 'command', 'sourcePath', 'enabled', 'source', 'eventName'):
            response = copy.deepcopy(self.response); items = response['data'][0]['hooks']
            if change == 'missing': items.pop()
            elif change == 'duplicate': items[1] = items[0]
            else: items[0][change] = False if change == 'enabled' else 'foreign'
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_inventory(self.spec, response)

    def test_errors_warnings_and_other_cwd_never_pass(self):
        for key, value in (('errors', [{'message': 'parse failure'}]), ('warnings', ['ignored file']),
                           ('cwd', '/other')):
            response = copy.deepcopy(self.response); response['data'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_inventory(self.spec, response)

    def test_materialized_preflight_imports_without_adapter_checkout(self):
        from upgrade_live_materialize import render_private_support
        buffers, _ = render_private_support(Path(__file__).resolve().parent)
        for name, (raw, _) in buffers.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        script = ('import sys; sys.path.insert(0, sys.argv[1]); '
                  'import mac_upgrade_task_hook_preflight as h; '
                  'assert callable(h.preflight); '
                  'assert "upgrade_task_hook_preflight" not in sys.modules')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                                 str(self.root / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()
