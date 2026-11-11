"""Fixed controller RPC loading and metadata-only adapter dependency closure."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, write_json
from upgrade_controller_rpc import rpc_type


class ControllerRPCTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()/'source'
        self.scripts=self.root/'scripts';self.scripts.mkdir(parents=True)
        self.path=self.scripts/'cmux_codex_rpc.py'
        self.path.write_text('class DaemonRPC: pass\n')
        pin=file_ref(self.path)
        write_json(self.root.parent/'MANIFEST.json',dict(files={
            'scripts/cmux_codex_rpc.py':dict(bytes=pin['bytes'],sha256=pin['sha256'])}))
        p=patch.dict(sys.modules);p.start();self.addCleanup(p.stop)
        sys.modules.pop('cmux_codex_rpc',None)
        sys.path.insert(0,str(self.scripts));self.addCleanup(sys.path.remove,str(self.scripts))
        importlib.invalidate_caches()

    def test_exact_pinned_controller_module_is_used_without_copy(self):
        loaded=rpc_type(str(self.root))
        self.assertIs(loaded,sys.modules['cmux_codex_rpc'].DaemonRPC)
        self.assertEqual(Path(sys.modules['cmux_codex_rpc'].__file__),self.path)

    def test_changed_bytes_are_refused_before_import(self):
        self.path.write_text('raise AssertionError("must not execute")\n')
        with self.assertRaises(ContractError):rpc_type(str(self.root))
        self.assertNotIn('cmux_codex_rpc',sys.modules)

    def test_already_loaded_foreign_controller_is_refused_and_preserved(self):
        import types
        other=types.ModuleType('cmux_codex_rpc');other.__file__='/different/source/scripts/cmux_codex_rpc.py'
        sys.modules['cmux_codex_rpc']=other
        with self.assertRaisesRegex(ContractError,'MIXED_CONTROLLER'):
            rpc_type(str(self.root))
        self.assertIs(sys.modules['cmux_codex_rpc'],other)


if __name__=='__main__':unittest.main()
