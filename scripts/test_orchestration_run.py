"""The operator can resume persisted evidence without replaying native input."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_campaign import control_script, find_control
from orchestration_common import ContractError, digest, file_ref, json_ref, parse_time, read_json, utc_now, write_json
from orchestration_coordinator import Coordinator, State
from orchestration_evidence import failure_marker, marker_for
from orchestration_native import delivery_identity
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture
from orchestration_verify import finish_run


class Crash(BaseException):
    pass


class NativeControlRequestTests(unittest.TestCase):
    def append_request(self, fixture, script):
        return fixture.clients["codex"].append(dict(type="response_item", timestamp=utc_now(),
            payload=dict(type="custom_tool_call", name="exec", input=script)))

    def test_returned_controls_match_native_input_with_one_terminal_lf(self):
        for action in ("start", "accept", "settle"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                f = CampaignFixture(directory)
                kwargs = {} if action == "start" else dict(node_id="local", marker="bound-marker")
                script = control_script(f.campaign, f.ref, action, "run-01", **kwargs)
                # Model the measured CLI input format independently of the generator.
                native_input = script.removesuffix("\n") + "\n"
                ref = self.append_request(f, native_input)
                self.assertTrue(script.endswith("));\n"))
                self.assertEqual(find_control(f.clients["codex"].reader, script), ref)

    def test_missing_extra_lf_or_changed_request_never_matches(self):
        mutations = (lambda s: s.removesuffix("\n"), lambda s: s + "\n",
                     lambda s: s.replace("bound-marker", "different-marker"), lambda s: " " + s)
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index), tempfile.TemporaryDirectory() as directory:
                f = CampaignFixture(directory)
                script = control_script(f.campaign, f.ref, "accept", "run-01",
                                        node_id="local", marker="bound-marker")
                self.append_request(f, mutate(script))
                with self.assertRaisesRegex(ContractError, "duplicate/absent request"):
                    find_control(f.clients["codex"].reader, script)

    def test_duplicate_start_requests_are_not_a_recovery_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            f = CampaignFixture(directory)
            script = control_script(f.campaign, f.ref, "start", "run-01")
            self.append_request(f, script)
            self.append_request(f, script)
            with self.assertRaisesRegex(ContractError, "duplicate/absent request"):
                find_control(f.clients["codex"].reader, script)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = CampaignFixture(self.temp.name)
        self.f.plan["nodes"] = [n for n in self.f.plan["nodes"] if n["id"] == "local"]
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()
        self.r = Runner(self.f.ref, fixtures=True)
        self.co = self.f.start()
        self.run = self.co.spec["run_id"]

    def tearDown(self):
        self.temp.cleanup()

    def completed_work(self):
        self.r.claim(self.run, "local")
        info = self.co.state()[1]["local"]
        self.f.work(info)
        return info

    def native_accept(self):
        info = self.co.state()[1]["local"]
        marker = marker_for(info, json_ref(info["candidate"]))
        self.f.control("accept", self.run, node_id="local", marker=marker)
        return marker

    def test_claim_resume_does_not_create_a_new_nonce_or_run_business(self):
        first = self.r.claim(self.run, "local")
        claim = file_ref(self.co.root / "claims/local.json")
        self.assertEqual(self.r.claim(self.run, "local"), first)
        self.assertEqual(file_ref(claim["path"]), claim)
        self.assertEqual(list(Path(first["output_root"]).iterdir()), [])
        with self.assertRaisesRegex(ContractError, "original run exists"):
            self.r.start_script(self.run)

    def test_frozen_candidate_recovers_transition_crash_without_rewriting_checks(self):
        self.completed_work()
        with patch.object(Coordinator, "transition", side_effect=Crash()), self.assertRaises(Crash):
            self.r.candidate(self.run, "local")
        candidate = file_ref(self.co.root / "nodes/local/candidate.json")
        tests = file_ref(self.co.root / "nodes/local/tests.json")
        result = self.r.candidate(self.run, "local")
        self.assertEqual(result["candidate"], candidate)
        self.assertEqual(result["tests"], tests)
        self.assertEqual(self.co.state()[0]["local"], State.EVIDENCE_READY)

    def test_acceptance_requires_original_control_and_recovers_its_result_window(self):
        self.completed_work()
        self.r.candidate(self.run, "local")
        with self.assertRaisesRegex(ContractError, "original supervisor control"):
            self.r.accept(self.run, "local", resume=True)
        marker = self.native_accept()
        with patch.object(Coordinator, "accept", side_effect=Crash()), self.assertRaises(Crash):
            self.r.accept(self.run, "local", marker)
        evidence = file_ref(self.co.root / "nodes/local/accepted-evidence.json")
        self.assertEqual(self.r.accept(self.run, "local", resume=True)["status"], "ACCEPTED")
        self.assertEqual(file_ref(evidence["path"]), evidence)
        self.assertEqual(self.r.accept(self.run, "local", resume=True)["status"], "ACCEPTED")
        first = finish_run(self.co)
        self.assertEqual(finish_run(self.co), first)
        self.assertIsNone(self.r.claim(self.run, "local")["business_command"])

    def test_duplicate_native_acceptance_is_not_a_recovery_budget(self):
        self.completed_work()
        self.r.candidate(self.run, "local")
        self.native_accept()
        self.native_accept()
        with self.assertRaisesRegex(ContractError, "duplicate/absent"):
            self.r.accept(self.run, "local", resume=True)
        self.assertIsNone(next(iter(self.co.leases.read().values()))["released_at"])

    def test_preexisting_wrong_checks_are_preserved_and_rejected(self):
        self.completed_work()
        ref = write_json(self.co.root / "nodes/local/tests.json", {"result": "PASS"})
        with self.assertRaises(ContractError):
            self.r.candidate(self.run, "local")
        self.assertEqual(file_ref(ref["path"]), ref)
        self.assertEqual(self.co.state()[0]["local"], State.LOCAL_RUNNING)

    def test_failure_settlement_uses_frozen_candidate_and_original_native_call(self):
        self.r.claim(self.run, "local")
        self.r.block(self.run, "local", "independent business command failed")
        result = self.r.failure_candidate(self.run, "local", "local_boundary")
        candidate = json_ref(result["candidate"])
        info = self.co.state()[1]["local"]
        marker = failure_marker(info, candidate)
        with self.assertRaisesRegex(ContractError, "original supervisor control"):
            self.r.settle(self.run, "local", resume=True)
        self.f.control("settle", self.run, node_id="local", marker=marker)
        with patch.object(Coordinator, "settle_failure", side_effect=Crash()), self.assertRaises(Crash):
            self.r.settle(self.run, "local", marker)
        ref = file_ref(self.co.root / "nodes/local/settled-failure.json")
        self.assertEqual(self.r.settle(self.run, "local", resume=True)["status"], "FAILED_PRESERVED")
        self.assertEqual(file_ref(ref["path"]), ref)
        self.assertEqual(finish_run(self.co)["status"], "FAILED_PRESERVED")
        candidate["boundary"] = "no_business_input"
        write_json(result["candidate"]["path"], candidate)
        with self.assertRaisesRegex(ContractError, "previously frozen candidate"):
            self.co.state()

    def test_recovery_only_observes_and_exposes_terminal_release_crash(self):
        self.completed_work()
        self.r.candidate(self.run, "local")
        marker = self.native_accept()
        with patch.object(Coordinator, "_release", side_effect=Crash()), self.assertRaises(Crash):
            self.r.accept(self.run, "local", marker)
        before = self.f.clients["codex"].reader.snapshot()
        result = self.r.recover(self.run)
        self.assertEqual(result["input_operations"], 0)
        self.assertIn("TERMINAL_RELEASE_PENDING", [v["state"] for v in result["observations"]])
        self.assertEqual(before, self.f.clients["codex"].reader.snapshot())
        self.assertEqual(self.co.repair_releases(), ["local"])
        self.assertEqual(finish_run(self.co)["status"], "PASS")

    def test_public_entrypoint_rejects_offline_campaign_and_has_no_fixture_switch(self):
        argv = [sys.executable, "-B", str(Path(__file__).with_name("orchestration_run.py")),
                "--campaign", self.f.ref["path"], "--campaign-sha256", self.f.ref["sha256"], "status"]
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("execution class mismatch", result.stderr)
        result = subprocess.run(argv + ["--fixtures"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


class RunnerFailureBoundaryTests(unittest.TestCase):
    def review(self, directory):
        f = CampaignFixture(directory)
        f.plan["nodes"] = [n for n in f.plan["nodes"] if n["id"] == "review"]
        f.campaign["plan"] = write_json(f.root / "plan.json", f.plan)
        f.save()
        r = Runner(f.ref, fixtures=True)
        co = f.start()
        co.claim("review")
        return f, r, co

    def test_unfinished_preparation_cannot_produce_a_release_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            f, r, co = self.review(directory)
            r.block(co.spec["run_id"], "review", "preflight has no proven terminal result")
            lease = file_ref(co.leases.root / "leases.json")
            with self.assertRaisesRegex(ContractError, "no proven preparation exit"):
                r.failure_candidate(co.spec["run_id"], "review", "no_business_input")
            self.assertFalse((co.root / "nodes/review/failure-candidate.json").exists())
            self.assertEqual(file_ref(lease["path"]), lease)
            self.assertEqual(co.state()[0]["review"], State.BLOCKED)

    def test_uncertain_input_and_missing_end_never_release_through_runner(self):
        for kind in ("uncertain_input", "missing_end"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                f, r, co = self.review(directory)
                f.prepare(co, "review")
                f.intent(co, "review")
                info = co.state()[1]["review"]
                client = f.clients["claude"]
                notice = Path(info["prepared"]["notice"]["path"]).read_text()
                epoch = parse_time(utc_now()).timestamp()
                identity = delivery_identity(client.session, f.clients["codex"].session)
                binding = dict(task_id=info["task_id"], marker=info["task_id"], payload_sha256=digest(notice.encode()),
                    task_pack_sha256=info["prepared"]["pack"]["sha256"], identity=identity)
                native = dict(schema="native-delivery-v1", identity=identity, provider="claude",
                    session_id=client.session["session_id"], process=client.session["process"], tty=client.session["tty"],
                    transcript=client.reader.snapshot(), bound_at_epoch=epoch, payload_sha256=digest(notice.encode()))
                attempt = dict(phase="NO_INPUT", binding=binding, native_binding=native, events=[],
                    started_at_epoch=epoch, ended_at_epoch=epoch, error="preserved original operation")
                if kind == "uncertain_input":
                    attempt.update(phase="UNKNOWN", events=[{"phase": "PASTE_INTENT"}])
                else:
                    attempt.pop("ended_at_epoch")
                journal = co.root / "nodes/review/fixture-dispatch"
                write_json(journal / "attempt-0001.json", attempt)
                uncertainty = write_json(co.root / "nodes/review/uncertainty.json",
                    dict(task_id=info["task_id"], reason="original dispatch outcome unresolved"))
                co.transition("review", State.UNCERTAIN, {"uncertainty": uncertainty})
                r.block(co.spec["run_id"], "review", "input or operation end is unknown")
                lease = file_ref(co.leases.root / "leases.json")
                with patch("orchestration_run.dispatch_journal", return_value=journal):
                    result = r.failure_candidate(co.spec["run_id"], "review", "no_business_input")
                marker = failure_marker(info, json_ref(result["candidate"]))
                f.control("settle", co.spec["run_id"], node_id="review", marker=marker)
                with self.assertRaises(ContractError):
                    r.settle(co.spec["run_id"], "review", marker)
                self.assertEqual(file_ref(result["candidate"]["path"]), result["candidate"])
                self.assertEqual(file_ref(lease["path"]), lease)
                self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])
                self.assertEqual(co.state()[0]["review"], State.BLOCKED)


if __name__ == "__main__":
    unittest.main()
