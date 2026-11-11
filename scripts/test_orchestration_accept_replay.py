"""New acceptance runs the checker once; replay retains every evidence gate."""
import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, json_ref, write_json
from orchestration_coordinator import State
from orchestration_evidence import marker_for
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture


class AcceptanceReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.initialize(Path(self.temp.name) / "initial")

    def initialize(self, root, version=3):
        root.mkdir()
        self.f = CampaignFixture(root, version=version)
        self.f.plan["nodes"] = [n for n in self.f.plan["nodes"] if n["id"] == "local"]
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()
        self.r = Runner(self.f.ref, fixtures=True)
        self.co = self.f.start()
        self.run = self.co.spec["run_id"]
        self.info = self.co.claim("local")
        self.f.work(self.info)
        self.node = self.co.root / "nodes/local"

    def ready(self):
        result = self.r.candidate(self.run, "local")
        self.candidate = json_ref(result["candidate"])
        self.marker = marker_for(self.info, self.candidate)
        self.f.control("accept", self.run, node_id="local", marker=self.marker)
        return result

    def events(self):
        return {p.name: p.read_bytes() for p in sorted((self.co.root / "events").glob("*.json"))}

    @contextlib.contextmanager
    def checker_calls(self, after=None):
        """Count the actual pinned subprocess, preserving unrelated clock probes."""
        actual, calls = subprocess.run, []
        expected = [self.f.campaign["python"]["path"], "-I", "-B", self.f.campaign["checker"]["path"]]
        def run(argv, *args, **kwargs):
            value = actual(argv, *args, **kwargs)
            if argv == expected:
                calls.append(dict(argv=argv, returncode=value.returncode))
                if after is not None:
                    after(value)
            return value
        with patch("orchestration_evidence.subprocess.run", side_effect=run):
            yield calls

    def assert_unaccepted(self, original_events):
        self.assertEqual(self.events(), original_events)
        self.assertFalse(any(json.loads(raw)["state"] == State.ACCEPTED.value
                             for raw in self.events().values()))
        self.assertIsNone(self.co.leases.read()[self.info["task_id"]]["released_at"])

    def mutate(self, kind):
        path = {
            "input": self.f.root / "input.json",
            "checker": Path(self.f.campaign["checker"]["path"]),
            "output": Path(self.info["output_root"]) / "result.json",
            "candidate": self.node / "candidate.json",
            "tests": self.node / "tests.json",
            "accepted_evidence": self.node / "accepted-evidence.json",
        }[kind]
        # Preserve JSON semantics while violating the exact immutable byte pin.
        path.write_bytes(path.read_bytes() + b" ")

    def test_new_candidate_once_and_existing_candidate_without_checker(self):
        with self.checker_calls() as calls:
            first = self.r.candidate(self.run, "local")
        self.assertEqual(len(calls), 1)
        with self.checker_calls() as calls:
            again = Runner(self.f.ref, fixtures=True).candidate(self.run, "local")
        self.assertEqual(calls, [])
        self.assertEqual(first, again)

    def test_new_runner_accept_executes_checker_exactly_once(self):
        for version in (2, 3):
            with self.subTest(version=version):
                self.initialize(Path(self.temp.name) / f"v{version}", version=version)
                self.ready()
                with self.checker_calls() as calls:
                    value = self.r.accept(self.run, "local", self.marker)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["returncode"], 0)
                self.assertEqual(value["status"], "ACCEPTED")
                self.assertEqual(sum(json.loads(v)["state"] == "ACCEPTED" for v in self.events().values()), 1)
                self.assertIsNotNone(self.co.leases.read()[self.info["task_id"]]["released_at"])
                print("ACCEPT_CHECKER_EVIDENCE", json.dumps(dict(version=version, entrypoint="Runner.accept",
                    checker_processes=len(calls), status=value["status"], lease_released=True)), flush=True)

    def test_direct_coordinator_accept_executes_checker_exactly_once(self):
        self.ready()
        # Use the original native call already appended by ready(), not a second
        # fixture acceptance call that would duplicate that native request.
        with patch.object(self.co, "accept", return_value=None):
            with patch.object(self.r, "co", return_value=self.co):
                self.r.accept(self.run, "local", self.marker)
        from orchestration_common import file_ref
        with self.checker_calls() as calls:
            value = self.co.accept("local", file_ref(self.node / "accepted-evidence.json"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(value["status"], "ACCEPTED")
        print("ACCEPT_CHECKER_EVIDENCE", json.dumps(dict(version=3, entrypoint="Coordinator.accept",
            checker_processes=len(calls), status=value["status"])), flush=True)

    def test_accepted_state_status_and_original_resume_are_read_only_without_checker(self):
        self.ready()
        self.r.accept(self.run, "local", self.marker)
        events = self.events()
        lease = self.co.leases.read()
        native = {key: c.path.read_bytes() for key, c in self.f.clients.items()}
        with self.checker_calls() as calls:
            for _ in range(3):
                runner = Runner(self.f.ref, fixtures=True)
                self.assertEqual(runner.co(self.run).state()[0]["local"], State.ACCEPTED)
                self.assertEqual(runner.status(self.run)["states"]["local"], "ACCEPTED")
                self.assertEqual(runner.accept(self.run, "local", resume=True)["status"], "ACCEPTED")
        self.assertEqual(calls, [])
        self.assertEqual(self.events(), events)
        self.assertEqual(self.co.leases.read(), lease)
        self.assertEqual({key: c.path.read_bytes() for key, c in self.f.clients.items()}, native)
        self.assertEqual(list(self.f.root.glob(".checker-request-*")), [])
        print("ACCEPT_REPLAY_EVIDENCE", json.dumps(dict(version=3, state_reads=3, status_reads=3,
            original_resumes=3, checker_processes=len(calls), events_unchanged=True,
            leases_unchanged=True, native_logs_unchanged=True)), flush=True)

    def test_checker_failure_or_mismatch_never_accepts_or_releases(self):
        for failure in ("exit", "failed_check", "mismatch"):
            with self.subTest(failure=failure):
                self.initialize(Path(self.temp.name) / failure)
                self.ready()
                events = self.events()
                def fail(result):
                    if failure == "exit":
                        result.returncode, result.stderr = 2, b"injected original checker failure"
                    elif failure == "failed_check":
                        result.stdout = b'{"checks":[{"id":"answer","passed":false}]}'
                    else:
                        result.stdout = b'{"checks":[{"id":"different-check","passed":true}]}'
                with self.checker_calls(fail) as calls, self.assertRaises(ContractError):
                    self.r.accept(self.run, "local", self.marker)
                self.assertEqual(len(calls), 1)
                self.assert_unaccepted(events)
                self.assertEqual(self.co.state()[0]["local"], State.EVIDENCE_READY)

    def test_mutated_pins_before_accept_never_run_checker_accept_or_release(self):
        for kind in ("input", "checker", "output", "candidate", "tests"):
            with self.subTest(kind=kind):
                self.initialize(Path(self.temp.name) / kind)
                self.ready()
                events = self.events()
                self.mutate(kind)
                with self.checker_calls() as calls, self.assertRaises(ContractError):
                    self.r.accept(self.run, "local", self.marker)
                self.assertEqual(calls, [])
                self.assert_unaccepted(events)

    def test_mutation_during_checker_never_appends_acceptance_or_releases(self):
        for kind in ("input", "checker", "output", "tests", "candidate", "accepted_evidence"):
            with self.subTest(kind=kind):
                self.initialize(Path(self.temp.name) / kind)
                self.ready()
                events = self.events()
                with self.checker_calls(lambda result: self.mutate(kind)) as calls, self.assertRaises(ContractError):
                    self.r.accept(self.run, "local", self.marker)
                self.assertEqual(len(calls), 1)
                self.assert_unaccepted(events)

    def test_accepted_replay_rejects_changed_evidence_without_checker(self):
        for kind in ("input", "checker", "output", "tests", "candidate", "accepted_evidence"):
            with self.subTest(kind=kind):
                self.initialize(Path(self.temp.name) / kind)
                self.ready()
                self.r.accept(self.run, "local", self.marker)
                events, lease = self.events(), self.co.leases.read()
                self.mutate(kind)
                with self.checker_calls() as calls:
                    with self.assertRaises(ContractError):
                        self.co.state()
                    with self.assertRaises(ContractError):
                        self.r.status(self.run)
                self.assertEqual(calls, [])
                self.assertEqual(self.events(), events)
                self.assertEqual(self.co.leases.read(), lease)


if __name__ == "__main__":
    unittest.main()
