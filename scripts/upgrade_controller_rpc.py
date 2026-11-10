"""Load the RPC reader from the separately pinned private controller.

Never copy its implementation into the campaign adapter or substitute another
already imported controller. Callers validate their original startup/runtime.
"""
import importlib
from pathlib import Path

from orchestration_common import file_ref, json_ref, read_ref, require


def rpc_type(controller):
    root = Path(controller)
    require(root.is_absolute() and root == root.resolve(), 'TASK_RPC_CONTROLLER_PATH_CHANGED')
    manifest = json_ref(file_ref(root.parent/'MANIFEST.json'))
    expected = manifest['files']['scripts/cmux_codex_rpc.py']
    path = root/'scripts/cmux_codex_rpc.py'
    read_ref(dict(path=str(path), bytes=expected['bytes'], sha256=expected['sha256']))
    name = 'cmux_codex_rpc'
    module = importlib.import_module(name)
    require(Path(module.__file__).resolve() == path, 'TASK_RPC_MIXED_CONTROLLER_IMPORT')
    return module.DaemonRPC
