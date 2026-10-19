"""Exercise the production support pack without cmux or installed MAC artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from upgrade_live_materialize import render_private_support


class PrivateSupportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        buffers, _ = render_private_support(Path(__file__).resolve().parent)
        for name, (raw, _) in buffers.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes(raw)

    def probe(self, code):
        # No site packages, PYTHONPATH, repository or cwd import fallback.
        return subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
            "import sys\nsys.path.insert(0, sys.argv[1])\n" + code,
            str(self.scripts)], cwd=self.root, capture_output=True, text=True, timeout=15)

    def test_private_client_isolation_loads_packaged_toml(self):
        for name in ("licenses/TOMLI-LICENSE", "docs/VENDORED-TOMLI.md"):
            self.assertEqual((self.root / name).read_bytes(),
                             (Path(__file__).resolve().parent.parent / name).read_bytes())
        result = self.probe('''
import json
from pathlib import Path
import mac_upgrade_client_isolation as isolation
import mac_upgrade_toml as parser
import mac_upgrade_toml_re as parser_re
assert isolation.tomllib is parser
assert not any(name in sys.modules for name in ("upgrade_toml", "upgrade_toml_re", "tomllib"))
modules = {name: module.__file__ for name, module in sys.modules.items()
           if name.startswith("mac_upgrade")}
assert all(Path(path).parent == Path(sys.argv[1]) for path in modules.values()), modules
raw = isolation.codex_config({"model": "model-with-中文", "model_provider": "test",
    "model_providers": {"test": {"base_url": "https://example.invalid/v1"}}},
    Path(sys.argv[1]) / "native", {"hooks": {"Stop": []}})
config = parser.loads(raw.decode())
assert config["model"] == "model-with-中文"
assert config["model_providers"]["test"]["base_url"] == "https://example.invalid/v1"
assert config["allow_login_shell"] is False
try:
    parser.loads("duplicate = 1\\nduplicate = 2\\n")
except parser.TOMLDecodeError:
    pass
else:
    raise AssertionError("invalid TOML accepted")
print(json.dumps(modules, sort_keys=True))
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        modules = json.loads(result.stdout)
        self.assertEqual(modules["mac_upgrade_toml"], str(self.scripts / "mac_upgrade_toml.py"))
        self.assertEqual(modules["mac_upgrade_toml_re"], str(self.scripts / "mac_upgrade_toml_re.py"))

    def test_missing_private_parser_has_no_public_fallback(self):
        (self.scripts / "mac_upgrade_toml.py").unlink()
        result = self.probe("import mac_upgrade_client_isolation")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ModuleNotFoundError: No module named 'mac_upgrade_toml'", result.stderr)

    def test_missing_private_parser_dependency_has_no_public_fallback(self):
        (self.scripts / "mac_upgrade_toml_re.py").unlink()
        result = self.probe("import mac_upgrade_client_isolation")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ModuleNotFoundError: No module named 'mac_upgrade_toml_re'", result.stderr)


if __name__ == "__main__":
    unittest.main()
