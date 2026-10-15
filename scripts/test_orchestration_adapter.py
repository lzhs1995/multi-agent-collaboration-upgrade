"""Dependency completeness checks use private source trees and never exec them."""
import tempfile
import unittest
import py_compile
from pathlib import Path
from unittest.mock import patch

from orchestration_adapter import CAMPAIGN_MODULES, MAINTENANCE_MODULES, validate_adapter
from orchestration_common import ContractError, tree_manifest, write_json
from orchestration_freeze import copy_runtime
from orchestration_maintenance import maintenance_control


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "source"
        (self.root / "scripts").mkdir(parents=True)
        for name in CAMPAIGN_MODULES:
            self.source(name, "# private dependency fixture\n")

    def source(self, name, value):
        path = self.root / "scripts" / (name + ".py")
        path.write_text(value, encoding="utf-8")
        return path

    def check(self, **kwargs):
        return validate_adapter(tree_manifest(self.root), **kwargs)

    def test_complete_dependencies_are_checked_without_executing_sources(self):
        self.source("orchestration_inputs", "raise RuntimeError('must never execute')\n")
        self.assertEqual(self.check(), sorted("scripts/" + n + ".py" for n in MAINTENANCE_MODULES))
        self.assertEqual(len(self.check(purpose="campaign")), len(CAMPAIGN_MODULES))

    def test_missing_explicit_dependency_is_rejected_even_after_rehash(self):
        for name in MAINTENANCE_MODULES:
            with self.subTest(name=name):
                path = self.root / "scripts" / (name + ".py")
                previous = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ContractError, "dependency missing"):
                    self.check()
                path.write_bytes(previous)

    def test_campaign_requires_the_read_only_observer_even_after_rehash(self):
        (self.root / "scripts/orchestration_observe.py").unlink()
        with self.assertRaisesRegex(ContractError, "orchestration_observe"):
            self.check(purpose="campaign")
        self.check(purpose="maintenance")

    def test_bytecode_is_rejected_even_when_the_tree_hash_is_valid(self):
        source = self.source("orchestration_inputs", "WHO = 'bytecode'\n")
        cached = Path(py_compile.compile(str(source), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH))
        self.source("orchestration_inputs", "WHO = 'source'\n")
        with self.assertRaisesRegex(ContractError, "bytecode/cache"):
            self.check()
        cached.unlink()
        # Even an empty cache directory prevents a source-only validation claim.
        with self.assertRaisesRegex(ContractError, "bytecode/cache"):
            self.check()
        cached.parent.rmdir()
        for name in ("orchestration_inputs.pyc", "legacy.pyo"):
            path = self.root / "scripts" / name
            path.write_bytes(b"not source")
            with self.assertRaisesRegex(ContractError, "bytecode/cache"):
                self.check()
            path.unlink()
        self.check()

    def test_deferred_transitive_imports_require_the_complete_closure(self):
        self.source("orchestration_inputs", "def later():\n    import extra_one\n")
        self.source("extra_one", "def deeper():\n    from extra_two import value\n")
        with self.assertRaisesRegex(ContractError, "extra_two"):
            self.check()
        self.source("extra_two", "value = 1\n")
        self.assertIn("scripts/extra_two.py", self.check())

    def test_literal_dynamic_and_sibling_loader_dependencies_are_required(self):
        variants = (
            "import importlib\nimportlib.import_module('extra')\n",
            "from importlib import import_module as load\nload('extra')\n",
            "import importlib\nload = importlib.import_module\nload('extra')\n",
            "__import__('extra')\n",
            "import importlib.util\nfrom pathlib import Path\n"
            "importlib.util.spec_from_file_location('private', Path(__file__).with_name('extra.py'))\n",
        )
        for source in variants:
            with self.subTest(source=source):
                self.source("orchestration_inputs", source)
                with self.assertRaisesRegex(ContractError, "extra.py"):
                    self.check()
                path = self.source("extra", "# present\n")
                self.assertIn("scripts/extra.py", self.check())
                path.unlink()

    def test_unresolved_dynamic_imports_fail_closed(self):
        variants = (
            "import importlib\nimportlib.import_module(name)\n",
            "from importlib import import_module as load\nload(name)\n",
            "__import__(name)\n",
            "import importlib\nload = importlib.import_module\nload = __import__\nload(name)\n",
            "import importlib.util\nimportlib.util.spec_from_file_location('private', path)\n",
        )
        for source in variants:
            with self.subTest(source=source):
                self.source("orchestration_inputs", source)
                with self.assertRaisesRegex(ContractError, "unresolved|ambiguous"):
                    self.check()

    def test_controller_import_exception_is_limited_to_original_function_scope(self):
        allowed = "import importlib\ndef controller_modules():\n    return importlib.import_module(name)\n"
        self.source("orchestration_campaign", allowed)
        self.check()
        self.source("orchestration_campaign", allowed.replace("controller_modules", "another_function"))
        with self.assertRaisesRegex(ContractError, "unresolved dynamic"):
            self.check()

    def test_relative_or_local_package_imports_are_not_silently_ignored(self):
        for source in ("from . import extra\n", "__import__('.extra')\n", "import extra.child\n"):
            with self.subTest(source=source):
                self.source("orchestration_inputs", source)
                with self.assertRaises(ContractError):
                    self.check()
        self.source("orchestration_inputs", "import json\n")
        package = self.root / "scripts/json"
        package.mkdir()
        (package / "__init__.py").write_text("# stdlib shadow\n")
        with self.assertRaisesRegex(ContractError, "local packages"):
            self.check()

    def test_change_during_dependency_read_is_caught_by_final_tree_check(self):
        from orchestration_adapter import read_ref
        def changed(ref, label):
            result = read_ref(ref, label)
            (self.root / "new-file").write_bytes(b"added during traversal")
            return result
        with patch("orchestration_adapter.read_ref", side_effect=changed):
            with self.assertRaisesRegex(ContractError, "tree changed"):
                self.check()

    def test_copy_requires_original_source_and_complete_destination(self):
        original = tree_manifest(self.root)
        self.source("orchestration_inputs", "# changed after pin\n")
        with self.assertRaises(ContractError):
            copy_runtime(original, self.base / "changed-copy")
        copied = copy_runtime(tree_manifest(self.root), self.base / "copy")
        self.assertEqual(len(validate_adapter(copied)), len(MAINTENANCE_MODULES))
        (Path(copied["path"]) / "scripts/cmux_submission_inputs.py").unlink()
        with self.assertRaisesRegex(ContractError, "dependency missing"):
            validate_adapter(tree_manifest(copied["path"]))

    def test_control_rendering_rejects_rehashed_incomplete_adapter_before_campaign_read(self):
        (self.root / "scripts/cmux_submission_inputs.py").unlink()
        manifest = write_json(self.base / "maintenance.json", dict(adapter=tree_manifest(self.root)))
        with self.assertRaisesRegex(ContractError, "dependency missing"):
            maintenance_control(manifest, "no-input")


if __name__ == "__main__":
    unittest.main()
