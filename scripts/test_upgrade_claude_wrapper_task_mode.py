"""Execute the real wrapper with a recording fixture, never a model client.

The fixture proves launch environment/argument preservation only. Real automatic
private hooks and task adoption still require the original live acceptance.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from upgrade_client_isolation import clean_child_environment, patch_cmux_wrapper
from upgrade_startup_contract import client_environment

WRAPPER = Path('/Applications/cmux.app/Contents/Resources/bin/cmux-claude-wrapper')


@unittest.skipUnless(WRAPPER.is_file(), 'real local cmux wrapper required')
class ClaudeTaskWrapperTests(unittest.TestCase):
    def test_restricted_task_keeps_endpoint_and_private_settings_without_raw_cmux(self):
        with tempfile.TemporaryDirectory(prefix='cw', dir='/tmp') as tmp:
            root = Path(tmp).resolve()
            paths = {name: root/name for name in ('state', 'tmp', 'bin')}
            for path in paths.values():
                path.mkdir()
            roots = {lane: paths['state']/lane for lane in ('codex', 'claude_a', 'claude_b')}
            for path in roots.values():
                path.mkdir()
            client = root/'record-claude'
            client.write_text('#!/bin/sh\nprintf \'%s\\0\' "${CMUX_SURFACE_ID-unset}" '
                              '"${CMUX_WORKSPACE_ID-unset}" "$@"\n')
            client.chmod(0o700)
            wrapper = paths['bin']/'claude'
            wrapper.write_bytes(patch_cmux_wrapper('claude', WRAPPER.read_bytes(), client,
                                                   paths, node=Path('/usr/bin/true')))
            wrapper.chmod(0o700)
            settings = roots['claude_a']/'settings.json'
            original = json.dumps({'hooks': {'PreToolUse': [{'hooks': [{'type': 'command',
                'command': '/fixture/private-pre-hook'}]}], 'PostToolUse': [{'hooks': [
                {'type': 'command', 'command': '/fixture/private-post-hook'}]}]}}).encode()
            settings.write_bytes(original)
            args = ['--settings', str(settings), '--session-id',
                    '11111111-1111-4111-8111-111111111111', 'fixture prompt']
            # A missing endpoint has the same wrapper branch as a denied ping.
            # These labels are test data, never a native identity assertion.
            inherited = dict(os.environ, CMUX_SURFACE_ID='fixture-surface',
                             CMUX_WORKSPACE_ID='fixture-workspace',
                             CMUX_SOCKET_PATH=str(root/'absent.sock'))
            for restricted in (False, True):
                overrides = client_environment(roots, paths, 'claude_a', '/usr/bin/true',
                                               task_model_network=restricted)
                with patch.dict(os.environ, inherited, clear=True):
                    env = clean_child_environment(overrides)
                result = subprocess.run([str(wrapper), *args], env=env, capture_output=True,
                                        timeout=10, check=True)
                fields = result.stdout.decode().split('\0')[:-1]
                expected = ['fixture-surface', 'fixture-workspace'] if restricted else ['unset', 'unset']
                self.assertEqual(fields[:2], expected)
                self.assertEqual(fields[2:], args)
                self.assertEqual(settings.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
