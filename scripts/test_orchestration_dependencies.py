"""Dependency artifacts must come from accepted producers in this exact run."""
import copy
import tempfile
import unittest
from pathlib import Path

from orchestration_common import ContractError, json_ref, write_json
from orchestration_evidence import (dependency_bindings, task_sources, verify_dependencies,
                                    work_request)
from orchestration_test_support import CampaignFixture


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = CampaignFixture(self.temp.name)
        self.f.plan["nodes"] = [n for n in self.f.plan["nodes"] if n["id"] != "review"]
        self.f.plan["nodes"][1]["depends_on"] = ["local"]
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()
        self.co = self.f.start()
        info = self.co.claim("local")
        self.f.work(info)
        self.f.accept(self.co, "local", self.f.candidate(self.co, "local"))
        self.info = self.co.claim("closeout")
        self.node = self.co.nodes["closeout"]

    def tearDown(self):
        self.temp.cleanup()

    def test_request_and_sources_pin_all_accepted_outputs(self):
        request = json_ref(self.info["work_request"])
        self.assertEqual(request, work_request(self.f.campaign, self.co.header, self.node, self.info))
        dependency = request["dependencies"][0]
        self.assertEqual(dependency["node_id"], "local")
        sources = task_sources(self.f.campaign, self.co.header, self.node, self.info)
        self.assertTrue(all(ref["path"] in sources for ref in dependency["output_files"]))
        self.assertIn(self.info["work_request"]["path"], sources)

    def test_missing_added_or_cross_run_dependencies_are_rejected(self):
        variants = []
        value = copy.deepcopy(self.info)
        value["dependencies"] = []
        variants.append(value)
        value = copy.deepcopy(self.info)
        value["dependencies"].append(copy.deepcopy(value["dependencies"][0]))
        variants.append(value)
        value = copy.deepcopy(self.info)
        value["dependencies"][0]["attempt_id"] = "another-run:local:a0001"
        variants.append(value)
        value = copy.deepcopy(self.info)
        value["dependencies"][0]["accepted_evidence"]["path"] = str(self.f.root / "other-acceptance.json")
        variants.append(value)
        for variant in variants:
            with self.subTest(dependencies=variant["dependencies"]), self.assertRaises(ContractError):
                verify_dependencies(self.f.campaign, self.co.header, self.node, variant)

    def test_pending_producer_report_is_not_an_accepted_dependency(self):
        data = copy.deepcopy(self.co.state()[1])
        data["local"].pop("verification")
        with self.assertRaisesRegex(ContractError, "not accepted"):
            dependency_bindings(self.node, data)

    def test_changed_producer_bytes_invalidate_consumer_and_lifecycle(self):
        path = Path(self.info["dependencies"][0]["output_files"][0]["path"])
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(ContractError):
            verify_dependencies(self.f.campaign, self.co.header, self.node, self.info)
        with self.assertRaises(ContractError):
            self.co.state()

    def test_extra_empty_directory_invalidates_complete_dependency_tree(self):
        Path(self.info["dependencies"][0]["output_tree"]["path"], "extra").mkdir()
        with self.assertRaisesRegex(ContractError, "tree changed"):
            task_sources(self.f.campaign, self.co.header, self.node, self.info)

    def test_rewritten_request_cannot_hide_dependency_rebinding(self):
        value = json_ref(self.info["work_request"])
        value["dependencies"] = []
        forged = copy.deepcopy(self.info)
        forged["work_request"] = write_json(self.info["work_request"]["path"], value)
        with self.assertRaisesRegex(ContractError, "request changed"):
            task_sources(self.f.campaign, self.co.header, self.node, forged)


if __name__ == "__main__":
    unittest.main()
