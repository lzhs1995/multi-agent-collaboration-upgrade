"""Offline comparison-contract fixtures, never native/model efficiency evidence.

All registration, campaign, startup, runtime, finish, permit and closure refs
are real pinned temporary files. Only native campaign/runtime validation is
substituted; the permit chain and all file/hash checks remain real. The v2
measurement labels below deliberately simulate claims at the report boundary.
"""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchestration_common import ContractError, file_ref, json_ref, read_ref, require, write_json
from orchestration_permit import read_permit, schedule_path, steps
from upgrade_benchmark import (ARM_STRATEGIES, REGISTRATION_V3, register,
                               validate_registered_campaign, validate_registration)
from upgrade_measurements import compare, next_allowed, observation_checks
from upgrade_startup_contract import RESPONSE_OBSERVER_PIN


class ObserverComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="observer-comparison-offline-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "registered"
        self.ref = register(self.root, "offline-observer-comparison", response_observation=True)
        self.registration = validate_registration(self.ref)
        self.assertEqual(self.registration["schema"], REGISTRATION_V3)
        self.groups = {}
        self.rows = []
        observer_bytes = Path(__file__).with_name("upgrade_claude_response_observer.cjs").read_bytes()
        for key, group in self.registration["groups"].items():
            directory = Path(group["context_path"]).parent
            context = write_json(directory / "experiment.json", dict(offline_fixture=True))
            module = directory / "observer.cjs"
            module.write_bytes(observer_bytes)
            startup = write_json(directory / "startup.json",
                                 dict(offline_fixture=True, pins={RESPONSE_OBSERVER_PIN: file_ref(module)}))
            runtime = write_json(directory / "runtime.json", dict(offline_fixture=True, context=context,
                                 arm=group["arm"], startup=startup))
            campaign_root = directory / "campaigns" / group["campaign_id"]
            campaign = write_json(campaign_root / "campaign.json", dict(offline_fixture=True,
                registration=self.ref, session_group=key, runtime=runtime, experiment=context,
                arm=group["arm"], workload_class=group["workload_class"], campaign_id=group["campaign_id"],
                root=str(campaign_root), runs=[dict(run_id=r["run_id"], strategy=ARM_STRATEGIES[group["arm"]])
                                             for r in group["runs"]]))
            self.groups[key] = dict(context=context, startup=startup, runtime=runtime,
                                    campaign=campaign, module=module)
        for step in steps(self.registration):
            group = self.registration["groups"][step["group"]]
            artifacts = self.groups[step["group"]]
            campaign = json_ref(artifacts["campaign"])
            finish = write_json(Path(campaign["root"]) / "runs" / step["run_id"] / "finish.json",
                                dict(offline_fixture=True, run_id=step["run_id"]))
            self.rows.append(dict(**step, arm=group["arm"], workload_class=group["workload_class"],
                status="PASS", contract_pass=True, within_cap=True,
                elapsed_seconds=100 if group["arm"] == "A1" else 80,
                human_interventions=0, usage=dict(tokens=None, cost=None),
                evidence_class="registered_live_measurement_v2", campaign=artifacts["campaign"], finish=finish,
                observer_configuration=self.descriptor(step["group"])))
        self.publish_ledger()
        campaign_patch = patch("orchestration_campaign.validate_campaign", side_effect=self.native_campaign)
        runtime_patch = patch("upgrade_live_runtime.validate_live_runtime", side_effect=self.native_runtime)
        self.campaign_validation = campaign_patch.start()
        self.runtime_validation = runtime_patch.start()
        self.addCleanup(campaign_patch.stop)
        self.addCleanup(runtime_patch.stop)

    def descriptor(self, group):
        artifacts = self.groups[group]
        startup = json_ref(artifacts["startup"])
        pin = startup["pins"].get(RESPONSE_OBSERVER_PIN)
        policy = (dict(mode="consumed-response-v1", sha256=pin["sha256"], bytes=pin["bytes"])
                  if pin is not None else dict(mode="disabled", sha256=None, bytes=None))
        return dict(schema="upgrade-runtime-observation-v1", runtime=artifacts["runtime"],
                    startup=artifacts["startup"], policy=policy)

    @staticmethod
    def native_campaign(ref, **_):
        """Replace native adoption only; retain pinned file and registered identity checks."""
        campaign = json_ref(ref)
        require(campaign["offline_fixture"] is True, "fixture cannot validate a real campaign")
        validate_registered_campaign(campaign)
        json_ref(campaign["runtime"])
        return campaign

    @staticmethod
    def native_runtime(ref, **_):
        """Offline substitute, preserving startup and observer content pin checks."""
        runtime = json_ref(ref)
        require(runtime["offline_fixture"] is True, "fixture cannot validate a real runtime")
        context = json_ref(runtime["context"])
        startup = json_ref(runtime["startup"])
        for pin in startup["pins"].values():
            read_ref(pin)
        return context, runtime

    def publish_ledger(self):
        previous = None
        when = "2026-10-19T00:00:00+00:00"
        for row in self.rows:
            group = self.groups[row["group"]]
            step = steps(self.registration)[row["ordinal"] - 1]
            permit = write_json(schedule_path(self.registration, "permits", row["ordinal"]),
                dict(schema="upgrade-run-permit-v1", registration=self.ref, step=step,
                     runtime=group["runtime"], context=group["context"], issued_at=when, previous=previous))
            previous = write_json(schedule_path(self.registration, "closures", row["ordinal"]),
                dict(schema="upgrade-run-closure-v1", registration=self.ref, permit=permit,
                     campaign=row["campaign"], finish=row["finish"], verified_at=when,
                     measurement=row, next_allowed=next_allowed(row)))

    def comparison(self, rows=None):
        return compare(self.registration, self.rows if rows is None else rows, registration_ref=self.ref)

    def assert_ineligible(self, result):
        self.assertFalse(result["timing_criterion_met"])
        self.assertEqual(result["qualification_scope"], "observer_configuration_unverified")
        self.assertEqual(result["conclusion"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual((len(result["runs"]), len(result["pairs"])), (33, 15))

    def replace_group_observer(self, group, *, disabled=False, changed_bytes=False):
        """Create internally pinned alternative fixture evidence, not stale refs."""
        artifacts = self.groups[group]
        startup = json_ref(artifacts["startup"])
        if disabled:
            startup["pins"].pop(RESPONSE_OBSERVER_PIN)
        else:
            data = artifacts["module"].read_bytes()
            artifacts["module"].write_bytes(data + b"\n// offline alternate observer\n" if changed_bytes else
                                            bytes([data[0] ^ 1]) + data[1:])
            startup["pins"][RESPONSE_OBSERVER_PIN] = file_ref(artifacts["module"])
        artifacts["startup"] = write_json(artifacts["startup"]["path"], startup)
        runtime = json_ref(artifacts["runtime"])
        runtime["startup"] = artifacts["startup"]
        artifacts["runtime"] = write_json(artifacts["runtime"]["path"], runtime)
        campaign = json_ref(artifacts["campaign"])
        campaign["runtime"] = artifacts["runtime"]
        artifacts["campaign"] = write_json(artifacts["campaign"]["path"], campaign)
        for row in self.rows:
            if row["group"] == group:
                row.update(campaign=artifacts["campaign"], observer_configuration=self.descriptor(group))
        self.publish_ledger()

    def test_complete_33_slots_and_15_pairs_verify_once_per_group(self):
        # Exercise the real recursive chain before the comparison invokes it.
        self.assertEqual(read_permit(self.ref, 33)[1]["step"]["ordinal"], 33)
        result = self.comparison()
        self.assertEqual(result["schema"], "upgrade-paired-report-v2")
        self.assertEqual((len(result["runs"]), len(result["pairs"]), result["pilot_passes"]), (33, 15, 3))
        self.assertEqual((result["verified_runs"], result["measured_runs"], result["passing_runs"]), (33, 33, 33))
        self.assertEqual(result["paired_warm_median_improvement_percent"], 20)
        self.assertTrue(result["timing_criterion_met"])
        self.assertEqual(result["qualification_scope"], "observer_configuration_verified")
        self.assertEqual(result["observer_configuration"]["activation"], "NOT_ESTABLISHED_BY_CONFIGURATION")
        self.assertEqual(self.campaign_validation.call_count, 7)
        self.assertEqual(self.runtime_validation.call_count, 7)
        self.assertFalse(result["production_adoption"])

    def test_all_missing_preserves_denominator_without_native_validation(self):
        result = self.comparison([])
        self.assert_ineligible(result)
        self.assertEqual((result["expected_runs"], result["observed_runs"], result["verified_runs"]), (33, 0, 0))
        self.assertTrue(all(row["status"] == "MISSING" for row in result["runs"]))
        self.campaign_validation.assert_not_called()

    def test_missing_original_registration_ref_refuses_even_empty_rows(self):
        for rows in (self.rows, []):
            with self.subTest(rows=len(rows)), self.assertRaisesRegex(ContractError, "pinned original registration"):
                compare(self.registration, rows)

    def test_changed_unanchored_registration_dict_refuses(self):
        registration = copy.deepcopy(self.registration)
        registration["response_observation"]["bytes"] += 1
        with self.assertRaisesRegex(ContractError, "pinned original registration"):
            compare(registration, self.rows, registration_ref=self.ref)

    def test_legacy_relabel_cannot_gain_new_qualification(self):
        registration = copy.deepcopy(self.registration)
        registration["schema"] = "upgrade-paired-registration-v1"
        registration.pop("response_observation")
        registration.pop("compact")
        result = compare(registration, self.rows)
        self.assertEqual(result["schema"], "upgrade-paired-report-v1")
        self.assertEqual(result["qualification_scope"], "legacy_unverified")
        self.assertIsNone(result["observer_configuration"])

    def test_missing_descriptor_downgrade_and_fixture_claims_are_unverified(self):
        for kind in ("missing", "legacy", "fixture"):
            rows = copy.deepcopy(self.rows)
            if kind == "missing":
                rows[-1].pop("observer_configuration")
            else:
                rows[-1]["evidence_class"] = ("registered_live_measurement_v1" if kind == "legacy" else "offline_fixture")
            with self.subTest(kind=kind):
                self.assert_ineligible(self.comparison(rows))

    def test_unverified_legacy_claims_do_not_count_as_pilot_or_passing_runs(self):
        rows = copy.deepcopy(self.rows)
        for row in rows[:3]:
            row["evidence_class"] = "registered_live_measurement_v1"
        result = self.comparison(rows)
        self.assert_ineligible(result)
        self.assertEqual(result["pilot_passes"], 0)
        self.assertEqual(result["passing_runs"], 30)

    def test_copied_descriptor_boolean_or_hash_is_not_a_proof(self):
        for policy_change in ({"sha256": "0" * 64}, {"bytes": 1},
                              {"mode": "disabled", "sha256": None, "bytes": None}):
            rows = copy.deepcopy(self.rows)
            rows[-1]["observer_configuration"]["policy"].update(policy_change)
            rows[-1]["observer_symmetric"] = True
            with self.subTest(change=policy_change):
                self.assert_ineligible(self.comparison(rows))

    def test_validly_repinned_different_observer_hash_is_rejected(self):
        self.replace_group_observer("pilot-B")
        self.assert_ineligible(self.comparison())

    def test_validly_repinned_different_observer_bytes_are_rejected(self):
        self.replace_group_observer("fan_in-A1", changed_bytes=True)
        self.assert_ineligible(self.comparison())

    def test_validly_repinned_disabled_observer_is_rejected(self):
        self.replace_group_observer("write_conflict-B", disabled=True)
        self.assert_ineligible(self.comparison())

    def test_startup_bytes_drift_invalidates_original_ref(self):
        startup = Path(self.groups["pilot-B"]["startup"]["path"])
        startup.write_bytes(startup.read_bytes() + b"\n")
        self.assert_ineligible(self.comparison())

    def test_observer_source_drift_invalidates_original_pin(self):
        module = self.groups["uneven_successor-A1"]["module"]
        module.write_bytes(module.read_bytes() + b"\n")
        self.assert_ineligible(self.comparison())

    def test_campaign_from_another_group_is_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[0]["campaign"] = self.groups["uneven_successor-A1"]["campaign"]
        self.assert_ineligible(self.comparison(rows))

    def test_campaign_registration_ref_must_be_the_original(self):
        rows = copy.deepcopy(self.rows)
        campaign = json_ref(rows[0]["campaign"])
        campaign["registration"] = register(Path(self.tmp.name).resolve() / "other-registration", "offline-other")
        rows[0]["campaign"] = write_json(self.root / "foreign-registration-campaign.json", campaign)
        self.assert_ineligible(self.comparison(rows))

    def test_later_row_cannot_replace_group_cached_campaign(self):
        rows = copy.deepcopy(self.rows)
        original = json_ref(rows[1]["campaign"])
        rows[1]["campaign"] = write_json(self.root / "other-campaign.json", original)
        result = self.comparison(rows)
        self.assert_ineligible(result)
        check = next(c for c in result["observer_configuration"]["rows"] if c["run_id"] == "pilot-02")
        self.assertIn("observer configuration changed", check["reason"])

    def test_copied_pins_cannot_authenticate_changed_elapsed_or_interventions(self):
        for changes in (dict(elapsed_seconds=1), dict(human_interventions=1),
                        dict(status="TIMEOUT"), dict(within_cap=False)):
            rows = copy.deepcopy(self.rows)
            rows[-1].update(changes)
            with self.subTest(changes=changes):
                result = self.comparison(rows)
                self.assert_ineligible(result)
                self.assertIn("external closeout", result["observer_configuration"]["rows"][-1]["reason"])

    def test_cost_requires_original_external_closure_not_caller_claims(self):
        for row in self.rows:
            row["usage"]["cost"] = 1 if row["arm"] == "A1" else 1.2
        self.publish_ledger()
        authentic = self.comparison()
        self.assertTrue(authentic["timing_criterion_met"])
        self.assertEqual(authentic["cost_status"], "MEASURED")
        self.assertEqual(len(authentic["cost_increase_over_10_percent"]), 15)
        for pair in authentic["pairs"]:
            self.assertAlmostEqual(pair["cost_increase_percent"], 20)

        forged = copy.deepcopy(self.rows)
        for row in forged:
            row["usage"]["cost"] = 1 if row["arm"] == "A1" else 4
        result = self.comparison(forged)
        self.assert_ineligible(result)
        self.assertEqual(result["cost_status"], "UNKNOWN")
        self.assertEqual(result["cost_increase_over_10_percent"], [])
        self.assertTrue(all(pair["cost_increase_percent"] is None for pair in result["pairs"]))
        original = json_ref(file_ref(schedule_path(self.registration, "closures", 33)))
        self.assertEqual(original["measurement"], self.rows[-1])

    def test_alternative_finish_ref_and_original_finish_drift_are_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[-1]["finish"] = write_json(self.root / "alternative-finish.json", dict(offline_fixture=True))
        self.assert_ineligible(self.comparison(rows))
        finish = Path(self.rows[-1]["finish"]["path"])
        finish.write_bytes(finish.read_bytes() + b"\n")
        self.assert_ineligible(self.comparison())

    def test_original_external_closure_is_required_even_with_valid_pins(self):
        schedule_path(self.registration, "closures", 33).unlink()
        result = self.comparison()
        self.assert_ineligible(result)
        self.assertEqual(result["verified_runs"], 32)
        self.assertFalse(result["observer_configuration"]["rows"][-1]["verified"])

    def test_original_permit_is_required_even_with_valid_closure(self):
        schedule_path(self.registration, "permits", 33).unlink()
        self.assert_ineligible(self.comparison())

    def test_descriptor_startup_or_runtime_from_another_group_is_rejected(self):
        for field in ("runtime", "startup"):
            rows = copy.deepcopy(self.rows)
            rows[-1]["observer_configuration"][field] = self.groups["pilot-B"][field]
            with self.subTest(field=field):
                self.assert_ineligible(self.comparison(rows))

    def test_observation_checks_retain_explicit_rejection_reason(self):
        rows = copy.deepcopy(self.rows)
        rows[-1]["observer_configuration"]["policy"]["bytes"] += 1
        checks = observation_checks(self.registration, self.ref, rows)
        self.assertEqual(len(checks), 33)
        failed = checks[(rows[-1]["group"], rows[-1]["run_id"])]
        self.assertFalse(failed["verified"])
        self.assertTrue(failed["reason"])


if __name__ == "__main__":
    unittest.main()
