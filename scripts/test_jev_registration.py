"""Separate versioned JEV registration and the record-advice CLI; offline fixtures only."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jev_registration
from jev_advice_run import main, record_advice
from orchestration_common import ContractError, file_ref, tree_manifest
from test_orchestration_bind_unbound import BindFixtureMixin


class RegistrationTests(BindFixtureMixin, unittest.TestCase):
    def reg(self, name="reg.json", **change):
        value = dict(schema=jev_registration.SCHEMA, run_id=self.run, node_id="unbound", arm="advisory-on",
                     external_advice_exception=jev_registration.EXCEPTION, model="m", provider="p",
                     budget=dict(max_model_requests=1), transport_source=file_ref(Path(__file__).resolve()),
                     authorization=file_ref(Path(__file__).resolve()))
        value.update(change)
        path = self.co.root.parent / name
        path.write_text(json.dumps(value))
        return file_ref(path)

    def journal(self):
        return self.co.root / "nodes" / "unbound" / "advice-worker-journal.json"

    def test_absent_registration_is_the_off_arm(self):
        self.assertEqual(jev_registration.load(None, run_id=self.run, node_id="unbound")["arm"], "advisory-off")

    def test_an_on_registration_missing_a_pin_is_refused(self):
        for name, change in (("budget0", dict(budget=dict(max_model_requests=0))), ("nobudget", dict(budget=None)),
                             ("noexception", dict(external_advice_exception=None)), ("othernode", dict(node_id="x")),
                             ("badpin", dict(transport_source=dict(path=str(Path(__file__).resolve()), sha256="0" * 64, bytes=1)))):
            with self.subTest(name=name), self.assertRaises(ContractError):
                jev_registration.load(self.reg(name + ".json", **change), run_id=self.run, node_id="unbound")

    def test_an_on_registration_without_injected_send_writes_nothing(self):
        with self.assertRaisesRegex(ContractError, "in-process send"):
            record_advice(self.r.co(self.run), "unbound", registration=self.reg())
        self.assertFalse(self.journal().exists())
        self.assertFalse((self.co.root / "jev-request-ledger").exists())

    def cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with patch("orchestration_run.Runner", return_value=self.r), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = main(["--campaign", self.f.ref["path"], "--campaign-sha256", self.f.ref["sha256"],
                         "--run", self.run, "--node", "unbound", *extra])
        return code, out.getvalue(), err.getvalue()

    def test_the_cli_runs_the_off_arm(self):
        code, out, _ = self.cli()
        self.assertEqual(code, 0)
        value = json.loads(out)
        self.assertEqual((value["arm"], value["model_requests"], value["ledger"]), ("advisory-off", 0, None))
        self.assertTrue(self.journal().exists())

    def test_the_cli_refuses_an_on_registration_before_any_write(self):
        ref = self.reg()
        code, _, err = self.cli("--registration", ref["path"], "--registration-sha256", ref["sha256"])
        self.assertEqual(code, 2)
        self.assertIn("in-process send", err)
        self.assertFalse(self.journal().exists())


class RegistrationClosureTests(unittest.TestCase):
    def test_registration_and_run_entry_stay_outside_both_closures(self):
        from orchestration_adapter import validate_adapter
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as other:
            root = Path(other).resolve() / "source"
            (root / "scripts").mkdir(parents=True)
            for path in scripts.glob("*.py"):
                shutil.copyfile(path, root / "scripts" / path.name)
            manifest = tree_manifest(root)
            closures = {p: validate_adapter(manifest, purpose=p) for p in ("maintenance", "campaign")}
        for purpose, closure in closures.items():
            for outside in ("jev_registration", "jev_advice_run", "jev_advice_worker", "upgrade_advice_publish"):
                with self.subTest(purpose=purpose, module=outside):
                    self.assertNotIn("scripts/" + outside + ".py", closure)


if __name__ == "__main__":
    unittest.main()
