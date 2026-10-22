"""Versioned closeout wiring; synthetic clients are not live-model evidence."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from orchestration_campaign import METRICS_V3, METRICS_V3_COMMUNICATION, validate_campaign
from orchestration_common import ContractError, utc_now, write_json
from orchestration_test_support import CampaignFixture
from orchestration_verify import finish_run, verify_run


class CommunicationMetricsTests(unittest.TestCase):
    def fixture(self, metrics):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        f = CampaignFixture(temp.name, version=3)
        f.plan["nodes"] = [n for n in f.plan["nodes"] if n["id"] == "local"]
        f.campaign["plan"] = write_json(f.root / "plan.json", f.plan)
        f.campaign["runs"][0]["run_id"] = "warm-01"
        f.campaign["metrics"] = copy.deepcopy(metrics)
        f.save()
        return f

    def complete(self, f):
        co = f.start()
        info = co.claim("local")
        f.work(info)
        f.accept(co, "local", f.candidate(co, "local"))
        client = f.clients["codex"]
        ref = client.append(client.message("user", "STATUS: MESSAGE_REFERENCE_V2 ordinary fixture", at=utc_now()))
        return co, ref

    def test_historical_metrics_do_not_reinterpret_ordinary_input(self):
        f = self.fixture(METRICS_V3)
        co, ref = self.complete(f)
        with patch("upgrade_communication_attribution.attribute_inputs", side_effect=AssertionError("historical contract")):
            result = finish_run(co)
            self.assertEqual(verify_run(f.ref, co.spec["run_id"], fixtures=True), result)
        self.assertIsNone(result["metrics"]["human_interventions"])
        self.assertEqual(result["metrics"]["unclassified_inputs"], [ref])
        self.assertNotIn("automated_communication_inputs", result["metrics"])

    def test_new_metrics_filter_only_exact_original_record_and_keep_summary_stable(self):
        f = self.fixture(METRICS_V3_COMMUNICATION)
        co, ref = self.complete(f)
        evidence = dict(schema="upgrade-communication-attribution-v1", unresolved=[],
                        inputs=[dict(request_id="fixture", native_record=ref)])
        with patch("upgrade_communication_attribution.attribute_inputs", return_value=evidence) as classify:
            result = finish_run(co)
            self.assertEqual(verify_run(f.ref, co.spec["run_id"], fixtures=True), result)
            self.assertEqual(result["metrics"]["human_interventions"], 0)
            self.assertEqual(result["metrics"]["unclassified_inputs"], [])
            self.assertEqual(result["metrics"]["automated_communication_inputs"], evidence)
            self.assertEqual(classify.call_args.kwargs["not_after"], result["closeout_clock"]["at"])
        # Recomputed attribution is part of the original frozen summary.
        with patch("upgrade_communication_attribution.attribute_inputs", return_value=dict(evidence, inputs=[])):
            with self.assertRaisesRegex(ContractError, "stored metrics differ"):
                verify_run(f.ref, co.spec["run_id"], fixtures=True)

    def test_forged_or_duplicate_native_record_is_still_unknown(self):
        f = self.fixture(METRICS_V3_COMMUNICATION)
        co, ref = self.complete(f)
        client = f.clients["codex"]
        duplicate = client.append(client.message("user", "STATUS: MESSAGE_REFERENCE_V2 ordinary fixture", at=utc_now()))
        evidence = dict(schema="upgrade-communication-attribution-v1", unresolved=[],
                        inputs=[dict(request_id="fixture", native_record=ref)])
        with patch("upgrade_communication_attribution.attribute_inputs", return_value=evidence):
            result = finish_run(co)
        self.assertEqual(result["metrics"]["unclassified_inputs"], [duplicate])
        self.assertIsNone(result["metrics"]["human_interventions"])
        self.assertIsNone(result["metrics"]["reasks"])

    def test_unknown_attribution_does_not_default_unknown_input_to_zero(self):
        f = self.fixture(METRICS_V3_COMMUNICATION)
        co, ref = self.complete(f)
        evidence = dict(schema="upgrade-communication-attribution-v1", inputs=[],
                        unresolved=[dict(request_id="fixture", reason="Uncertain")])
        with patch("upgrade_communication_attribution.attribute_inputs", return_value=evidence):
            result = finish_run(co)
        self.assertEqual(result["metrics"]["unclassified_inputs"], [ref])
        self.assertIsNone(result["metrics"]["human_interventions"])

    def test_only_registered_metric_versions_validate(self):
        f = self.fixture(METRICS_V3_COMMUNICATION)
        self.assertEqual(validate_campaign(f.ref, fixtures=True)["metrics"], METRICS_V3_COMMUNICATION)
        f.campaign["metrics"]["version"] = 4
        f.save()
        with self.assertRaisesRegex(ContractError, "measurement definitions"):
            validate_campaign(f.ref, fixtures=True)


if __name__ == "__main__":
    unittest.main()
