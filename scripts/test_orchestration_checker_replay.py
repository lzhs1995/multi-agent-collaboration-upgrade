"""Offline proof replay stays read-only; new acceptance still runs its checker."""
import copy
from datetime import timedelta
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import orchestration_evidence as evidence_module
from orchestration_common import ContractError, file_ref, json_ref, parse_time, tree_manifest, write_json
from orchestration_evidence import validate_candidate, verify_checks, verify_node
from orchestration_test_support import CampaignFixture


class CheckerReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name)
        self.co = self.f.start()
        info = self.co.claim("local")
        self.f.work(info)
        self.evidence = self.f.candidate(self.co, "local")
        self.info = self.co.state()[1]["local"]
        self.node = self.co.nodes["local"]

    def check(self, *, rerun=False):
        return verify_checks(self.f.campaign, self.co.header, self.node, self.info, self.evidence, rerun=rerun)

    def rewrite_tests(self, mutate):
        value = json_ref(self.evidence["tests"])
        mutate(value)
        self.evidence["tests"] = write_json(self.evidence["tests"]["path"], value)

    def replace_checker(self, source):
        path = Path(self.f.campaign["checker"]["path"])
        path.write_text(source)
        self.f.campaign["checker"] = file_ref(path)
        self.f.campaign["adapter"] = tree_manifest(self.f.campaign["adapter"]["path"])
        self.rewrite_tests(lambda t: t.update(checker=self.f.campaign["checker"]))

    def test_saved_checks_and_candidate_launch_nothing_and_create_no_scratch(self):
        with patch.object(evidence_module.subprocess, "run", side_effect=AssertionError("unexpected child")) as child, \
             patch.object(evidence_module.tempfile, "TemporaryDirectory", side_effect=AssertionError("unexpected scratch")) as scratch:
            self.assertEqual(self.check()["checks"], 1)
            self.assertEqual(validate_candidate(self.f.campaign, self.co.header, self.node, self.info,
                self.info["candidate"], fixtures=True, rerun=False), self.evidence)
        child.assert_not_called()
        scratch.assert_not_called()

    def test_actual_rerun_launches_exactly_one_real_checker(self):
        with patch.object(evidence_module.subprocess, "run", wraps=subprocess.run) as child:
            self.assertEqual(self.check(rerun=True)["checks"], 1)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(list(self.f.root.glob(".checker-request-*")), [])

    def test_candidate_defaults_to_one_real_checker(self):
        with patch.object(evidence_module.subprocess, "run", wraps=subprocess.run) as child:
            validate_candidate(self.f.campaign, self.co.header, self.node, self.info,
                               self.info["candidate"], fixtures=True)
        self.assertEqual(child.call_count, 1)

    def test_final_node_runs_once_and_historical_node_runs_zero(self):
        self.f.accept(self.co, "local", self.evidence)
        info = self.co.state()[1]["local"]
        accepted = json_ref(info["accepted_evidence"])
        with patch.object(evidence_module.subprocess, "run", wraps=subprocess.run) as child:
            self.assertEqual(verify_node(self.f.campaign, self.co.header, self.node, info, accepted,
                                       fixtures=True)["status"], "ACCEPTED")
        self.assertEqual(child.call_count, 1)
        with patch.object(evidence_module.subprocess, "run", side_effect=AssertionError("unexpected child")) as child, \
             patch.object(evidence_module.tempfile, "TemporaryDirectory", side_effect=AssertionError("unexpected scratch")) as scratch:
            verify_node(self.f.campaign, self.co.header, self.node, info, accepted, fixtures=True, rerun=False)
        child.assert_not_called()
        scratch.assert_not_called()

    def test_malformed_saved_results_are_rejected_without_execution(self):
        original = json_ref(self.evidence["tests"])
        values = [None, [], {}, {"checks": []}, {"checks": {}}, {"checks": [None]},
                  {"checks": [{"id": "", "passed": True}]},
                  {"checks": [{"id": 7, "passed": True}]},
                  {"checks": [{"id": "answer", "passed": False}]},
                  {"checks": [{"id": "answer", "passed": 1}]},
                  {"checks": [{"id": "answer", "passed": "true"}]},
                  {"checks": [{"id": "answer"}]},
                  {"checks": [{"id": "answer", "passed": True}] * 2}]
        with patch.object(evidence_module.subprocess, "run", side_effect=AssertionError("unexpected child")):
            for result in values:
                with self.subTest(result=result):
                    value = copy.deepcopy(original)
                    value["result"] = result
                    self.evidence["tests"] = write_json(self.evidence["tests"]["path"], value)
                    with self.assertRaises(ContractError):
                        self.check()

    def test_saved_schema_request_and_time_tampering_are_rejected(self):
        original = json_ref(self.evidence["tests"])
        before_task = (parse_time(self.info["started_at"]) - timedelta(seconds=1)).isoformat()
        mutations = [lambda t: t.update(schema="orchestration-check-result-v1"),
                     lambda t: t.update(request_sha256="0" * 64),
                     lambda t: t.update(checker={}),
                     lambda t: t.update(extra=True),
                     lambda t: t.pop("result"),
                     lambda t: t.update(started_at=before_task),
                     lambda t: t.update(finished_at=before_task),
                     lambda t: t.update(started_at="2026-10-19T00:00:00")]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(original)
                mutation(value)
                self.evidence["tests"] = write_json(self.evidence["tests"]["path"], value)
                with self.assertRaises(ContractError):
                    self.check()

    def test_pinned_output_input_checker_and_python_tampering_are_rejected(self):
        refs = [self.evidence["output_files"][0], self.f.campaign["inputs"][0], self.f.campaign["checker"]]
        with patch.object(evidence_module.subprocess, "run", side_effect=AssertionError("unexpected child")):
            for ref in refs:
                path = Path(ref["path"])
                original = path.read_bytes()
                with self.subTest(path=path):
                    try:
                        path.write_bytes(original + b"\n")
                        with self.assertRaises(ContractError):
                            self.check()
                    finally:
                        path.write_bytes(original)
            self.f.campaign["python"] = dict(self.f.campaign["python"], sha256="0" * 64)
            with self.assertRaises(ContractError):
                self.check()

    def test_additional_output_and_adapter_files_are_rejected(self):
        for root in (self.info["output_root"], self.f.campaign["adapter"]["path"]):
            path = Path(root) / "unexpected.txt"
            with self.subTest(root=root):
                try:
                    path.write_text("unexpected\n")
                    with self.assertRaises(ContractError):
                        self.check()
                finally:
                    path.unlink()

    def test_saved_evidence_hash_is_rechecked(self):
        path = Path(self.evidence["tests"]["path"])
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(ContractError):
            self.check()

    def test_read_only_replay_rechecks_pins_after_source_validation(self):
        original = evidence_module._verify_checker_sources
        def mutate(campaign):
            original(campaign)
            Path(self.evidence["report"]["path"]).write_text("changed during observation\n")
        with patch.object(evidence_module, "_verify_checker_sources", side_effect=mutate):
            with self.assertRaises(ContractError):
                self.check()

    def test_saved_result_must_match_the_real_checker(self):
        self.rewrite_tests(lambda t: t["result"]["checks"][0].update(id="invented"))
        with patch.object(evidence_module.subprocess, "run", wraps=subprocess.run) as child:
            with self.assertRaisesRegex(ContractError, "differs from independent rerun"):
                self.check(rerun=True)
        self.assertEqual(child.call_count, 1)

    def test_post_run_output_and_input_changes_are_rejected(self):
        original_run = subprocess.run
        for ref in (self.evidence["report"], self.f.campaign["inputs"][0]):
            path = Path(ref["path"])
            original = path.read_bytes()
            def run_and_mutate(*args, **kwargs):
                result = original_run(*args, **kwargs)
                path.write_bytes(original + b"\n")
                return result
            with self.subTest(path=path):
                try:
                    with patch.object(evidence_module.subprocess, "run", side_effect=run_and_mutate) as child:
                        with self.assertRaises(ContractError):
                            self.check(rerun=True)
                    self.assertEqual(child.call_count, 1)
                finally:
                    path.write_bytes(original)

    def test_post_run_added_deliverable_is_rejected(self):
        original_run = subprocess.run
        def run_and_add(*args, **kwargs):
            result = original_run(*args, **kwargs)
            (Path(self.info["output_root"]) / "extra.txt").write_text("extra\n")
            return result
        with patch.object(evidence_module.subprocess, "run", side_effect=run_and_add):
            with self.assertRaises(ContractError):
                self.check(rerun=True)

    def test_post_run_source_change_is_rejected(self):
        original_run = subprocess.run
        def run_and_mutate(*args, **kwargs):
            result = original_run(*args, **kwargs)
            with Path(self.f.campaign["checker"]["path"]).open("a") as stream:
                stream.write("# changed after checker returned\n")
            return result
        with patch.object(evidence_module.subprocess, "run", side_effect=run_and_mutate):
            with self.assertRaises(ContractError):
                self.check(rerun=True)

    def test_dependency_pins_are_checked_before_and_after_execution(self):
        self.f.accept(self.co, "local", self.evidence)
        review = self.co.claim("review")
        self.f.prepare(self.co, "review")
        self.f.intent(self.co, "review")
        self.f.dispatch(self.co, "review")
        self.f.work(review, "claude")
        protocol = self.f.callback(self.co, "review")
        self.f.accept(self.co, "review", self.f.candidate(self.co, "review", protocol))
        self.f.disarm(self.co, "review")
        info = self.co.claim("closeout")
        self.f.work(info)
        candidate = self.f.candidate(self.co, "closeout")
        info = self.co.state()[1]["closeout"]
        node = self.co.nodes["closeout"]
        path = Path(info["dependencies"][0]["output_files"][0]["path"])
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        try:
            with patch.object(evidence_module.subprocess, "run", side_effect=AssertionError("unexpected child")) as child:
                with self.assertRaises(ContractError):
                    verify_checks(self.f.campaign, self.co.header, node, info, candidate, rerun=False)
            child.assert_not_called()
        finally:
            path.write_bytes(original)
        original_run = subprocess.run
        def run_and_mutate(*args, **kwargs):
            result = original_run(*args, **kwargs)
            path.write_bytes(original + b"\n")
            return result
        with patch.object(evidence_module.subprocess, "run", side_effect=run_and_mutate) as child:
            with self.assertRaises(ContractError):
                verify_checks(self.f.campaign, self.co.header, node, info, candidate, rerun=True)
        self.assertEqual(child.call_count, 1)

    def test_real_checker_failure_is_never_accepted_or_retried(self):
        self.replace_checker("raise SystemExit(3)\n")
        with patch.object(evidence_module.subprocess, "run", wraps=subprocess.run) as child:
            with self.assertRaisesRegex(ContractError, "trusted checker failed"):
                self.check(rerun=True)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(list(self.f.root.glob(".checker-request-*")), [])

    def test_real_checker_malformed_output_is_never_accepted(self):
        self.replace_checker('print("not JSON")\n')
        with patch.object(evidence_module.subprocess, "run", wraps=subprocess.run) as child:
            with self.assertRaises(ContractError):
                self.check(rerun=True)
        self.assertEqual(child.call_count, 1)

    def test_checker_timeout_is_never_accepted_or_retried(self):
        with patch.object(evidence_module.subprocess, "run", side_effect=subprocess.TimeoutExpired("checker", 1)) as child:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.check(rerun=True)
        self.assertEqual(child.call_count, 1)
        self.assertEqual(list(self.f.root.glob(".checker-request-*")), [])

    def test_nonboolean_rerun_policy_is_rejected(self):
        for value in (None, 0, 1, "false", "true"):
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.check(rerun=value)


if __name__ == "__main__":
    unittest.main()
