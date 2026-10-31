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


ON_CALL = Path(__file__).resolve().with_name("jev_on_call.py")
PLACEHOLDER = "offline-placeholder-not-a-key"


class OfflineConnection:
    """Offline stand-in for http.client.HTTPSConnection; never touches the network.

    It proves the attempt count and the S1 wire shape, not a real service or trio run.
    """
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def request(self, method, path, body, headers):
        self.calls.append(dict(method=method, path=path, body=json.loads(body), headers=headers))

    def getresponse(self):
        payload, status = self.calls[-1]["body"], self.status
        body = json.dumps(dict(model=payload["model"], usage=dict(input_tokens=10, output_tokens=2), answers=dict(
            work_kind=dict(type="choice", choice="coding", confidence=0.9,
                           probabilities=dict(coding=0.97, debugging=0.01, review=0.01, literature=0.01)),
            work_kind_applies=dict(type="noul", noul=0.9))))

        class Response:
            def read(self):
                return body.encode()
        response = Response()
        response.status = status
        return response


class RegistrationTests(BindFixtureMixin, unittest.TestCase):
    def reg(self, name="reg.json", *, run=None, co=None, **change):
        co = co or self.co
        value = dict(schema=jev_registration.SCHEMA, run_id=run or self.run, node_id="unbound", arm="advisory-on",
                     external_advice_exception=jev_registration.EXCEPTION, model="m", provider="p",
                     budget=dict(max_model_requests=1, max_cost_usd=0.03),
                     transport_source=file_ref(ON_CALL))
        value.update(change)
        if "authorization" not in value:
            grant = co.root.parent / ("auth-" + name)
            grant.write_text(json.dumps({k: value[k] for k in ("run_id", "node_id", "arm", "provider", "model", "budget")}))
            value["authorization"] = file_ref(grant)
        path = co.root.parent / name
        path.write_text(json.dumps(value))
        return file_ref(path)

    def test_one_offline_send_reaches_the_ledger_journal_and_original_publisher(self):
        connection = OfflineConnection()
        value = record_advice(self.r.co(self.run), "unbound", registration=self.reg(), connection=connection,
                              credential=PLACEHOLDER)
        self.assertEqual((value["arm"], value["model_requests"], len(connection.calls)), ("advisory-on", 1, 1))
        self.assertEqual((connection.calls[0]["method"], connection.calls[0]["path"]), ("POST", "/v1/systemone"))
        self.assertEqual(value["ledger"]["reserved_attempts"], 1)
        journal = json.loads(self.journal().read_text())
        self.assertEqual([entry["model_requests"] for entry in journal["entries"]], [1])
        self.assertTrue((self.co.root / "nodes/unbound/advice" / (value["advice_sha256"] + ".txt")).exists())
        self.assertEqual(jev_registration.registered_arm(self.co.root, self.run, "unbound")[0], "advisory-on")
        record = json.loads(jev_registration.record_path(self.co.root, "unbound").read_text())
        loaded = jev_registration.load(record["registration"], run_id=self.run, node_id="unbound")
        self.assertEqual(loaded["budget"], dict(max_model_requests=1, max_cost_usd=0.03))

    def test_two_tasks_with_the_same_node_and_lanes_send_states_that_hash_back(self):
        import hashlib
        import orchestration_binding
        from orchestration_run import Runner
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        f2 = self.fixture(base=Path(other.name).resolve(), question="5 * 8")
        co2 = f2.start()
        seen, real = [], orchestration_binding.lane_facts

        def spy(*args, **kwargs):
            seen.append(real(*args, **kwargs))
            return seen[-1]
        connection = OfflineConnection()
        with patch.object(orchestration_binding, "lane_facts", spy):
            record_advice(self.r.co(self.run), "unbound", registration=self.reg(), connection=connection,
                          credential=PLACEHOLDER)
            record_advice(Runner(f2.ref, fixtures=True).co(co2.spec["run_id"]), "unbound",
                          registration=self.reg(run=co2.spec["run_id"], co=co2), connection=connection,
                          credential=PLACEHOLDER)
        sent = [call["body"]["state"]["task"] for call in connection.calls]
        self.assertEqual(len(sent), 2)
        self.assertEqual(seen[0], seen[1])
        self.assertNotEqual(sent[0], sent[1])
        questions = []
        for task in sent:
            state = json.loads(task)
            self.assertEqual(state["task"]["node_id"], "unbound")
            for unit in state["inputs"]:
                raw = unit["text"].encode("utf-8")
                self.assertEqual(hashlib.sha256(raw).hexdigest(), unit["pin"]["sha256"])
                self.assertEqual(Path(unit["pin"]["path"]).read_bytes(), raw)
                questions.append(json.loads(unit["text"])["question"])
        self.assertEqual(questions, ["6 * 7", "5 * 8"])

    def test_a_registered_source_without_bind_is_refused_before_any_write(self):
        source = self.co.root.parent / "no-bind.py"
        source.write_text("VALUE = 1\n")
        with self.assertRaisesRegex(ContractError, "has no bind"):
            record_advice(self.r.co(self.run), "unbound", registration=self.reg(transport_source=file_ref(source)),
                          connection=OfflineConnection(), credential=PLACEHOLDER)
        self.assertFalse(self.journal().exists())
        self.assertFalse(jev_registration.record_path(self.co.root, "unbound").exists())

    def test_401_and_403_stop_after_exactly_one_attempt(self):
        from jev_request_ledger import ledger_summary
        from orchestration_run import Runner
        for status in (401, 403):
            with self.subTest(status=status):
                other = tempfile.TemporaryDirectory()
                self.addCleanup(other.cleanup)
                f2 = self.fixture(base=Path(other.name).resolve())
                co2 = f2.start()
                connection = OfflineConnection(status)
                with self.assertRaisesRegex(ContractError, "STOP"):
                    record_advice(Runner(f2.ref, fixtures=True).co(co2.spec["run_id"]), "unbound",
                                  registration=self.reg(run=co2.spec["run_id"], co=co2), connection=connection,
                                  credential=PLACEHOLDER)
                self.assertEqual(len(connection.calls), 1)
                self.assertTrue(ledger_summary(co2.root / "jev-request-ledger")["stopped"])
                self.assertFalse((co2.root / "nodes/unbound/advice").exists())

    def test_an_authorization_naming_another_budget_is_refused(self):
        grant = self.co.root.parent / "auth-other.json"
        grant.write_text(json.dumps(dict(run_id=self.run, node_id="unbound", arm="advisory-on", provider="p", model="m",
                                         budget=dict(max_model_requests=80, max_cost_usd=0.03))))
        with self.assertRaisesRegex(ContractError, "authorization does not name"):
            jev_registration.load(self.reg("reg-other.json", authorization=file_ref(grant)), run_id=self.run,
                                  node_id="unbound")

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
        self.assertFalse(jev_registration.record_path(self.co.root, "unbound").exists())

    def test_the_record_pins_the_arm_and_is_re_derived(self):
        self.assertEqual(jev_registration.registered_arm(self.co.root, self.run, "unbound"), ("advisory-off", None))
        record_advice(self.r.co(self.run), "unbound")
        arm, ref = jev_registration.registered_arm(self.co.root, self.run, "unbound")
        self.assertEqual((arm, ref["path"]), ("advisory-off", str(jev_registration.record_path(self.co.root, "unbound"))))

    def test_a_record_claiming_on_without_its_registration_is_refused(self):
        path = jev_registration.record_path(self.co.root, "unbound")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(schema=jev_registration.RECORD_SCHEMA, run_id=self.run, node_id="unbound",
                                        arm="advisory-on", registration=None)))
        with self.assertRaisesRegex(ContractError, "record arm differs"):
            jev_registration.registered_arm(self.co.root, self.run, "unbound")

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
    def test_the_consumer_freezes_the_registration_check_but_not_the_caller(self):
        """``upgrade_advice_chain`` re-derives the arm through ``jev_registration``,
        so the validator is frozen with every adapter; the worker, its caller and
        the publisher stay outside (design Q9)."""
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
            with self.subTest(purpose=purpose):
                self.assertIn("scripts/jev_registration.py", closure)
                for outside in ("jev_advice_run", "jev_advice_worker", "upgrade_advice_publish", "jev_transport",
                                "jev_on_call"):
                    self.assertNotIn("scripts/" + outside + ".py", closure)



if __name__ == "__main__":
    unittest.main()
