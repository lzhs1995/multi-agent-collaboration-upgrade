"""R586/R587: the private support closure, checked on the actual rendered bytes, lazy imports included.

The pilot-on hold failed with ModuleNotFoundError: _launch_existing imports upgrade_existing_workspace,
which was neither shipped nor renamed. These tests render the real support closure and require every
import of a private module to be renamed and to resolve inside the private controller.
"""
import ast
import unittest
import os
from pathlib import Path

import upgrade_live_materialize as materialize
from upgrade_live_patch import PRIVATE_MODULES

HERE = Path(materialize.__file__).resolve().parent
BASE = (Path(os.environ.get("MAC_TEST_MAC084_SOURCE") or "/opt/collaboration/test-fixtures/mac084/source") / "scripts")
# Lazy imports reached only by the external publisher side of startup continuity (startup_continuity=False
# in S2); they are not private modules and stay unrenamed by design.
EXTERNAL_ONLY = {("mac_upgrade_startup_continuity", "upgrade_live_runtime"),
                 ("mac_upgrade_startup_continuity", "upgrade_live_control"),
                 ("mac_upgrade_startup_continuity", "orchestration_campaign")}


def imports(raw):
    for node in ast.walk(ast.parse(raw)):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]


@unittest.skipUnless(BASE.is_dir(), "pinned complete MAC .84 source missing")
class RenderedSupportClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        buffers, _ = materialize.render_private_support(HERE)
        cls.rendered = {Path(name).stem: raw for name, (raw, _) in buffers.items() if name.endswith(".py")}
        cls.base = {p.stem for p in BASE.glob("*.py")}
        cls.upgrade_only = {p.stem for p in HERE.glob("*.py")} - cls.base

    def test_existing_workspace_ships_renamed_and_the_entry_imports_that_name(self):
        self.assertIn("mac_upgrade_existing_workspace", self.rendered)
        entry = set(imports(self.rendered["mac_upgrade_live_entry"]))
        self.assertIn("mac_upgrade_existing_workspace", entry)
        self.assertNotIn("upgrade_existing_workspace", entry)

    def test_every_rendered_import_is_renamed_and_resolves_in_the_private_controller(self):
        unrenamed, unresolved, external = [], [], set()
        for module, raw in self.rendered.items():
            for name in imports(raw):
                if name in PRIVATE_MODULES:
                    unrenamed.append((module, name))
                elif name.startswith("mac_") and name not in self.rendered and name not in self.base:
                    unresolved.append((module, name))
                elif name in self.upgrade_only:
                    external.add((module, name))
        self.assertEqual(unrenamed, [])
        self.assertEqual(unresolved, [])
        self.assertEqual(external, EXTERNAL_ONLY)


if __name__ == "__main__":
    unittest.main()
