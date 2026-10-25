"""Offline multiprocess coordinator pressure; actual fcntl, no live senders."""
import contextlib
import fcntl
import os
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orchestration_common import ContractError, boot_id, exclusive_lock, file_ref, read_json, utc_now, write_json
from orchestration_coordinator import State
from orchestration_campaign import node_output
from orchestration_test_support import CampaignFixture
from orchestration_transport import Transport
from test_orchestration_transport import FakeRuntime

MP = multiprocessing.get_context("fork")


def action_worker(f, co, node, action, result, *, held=None, release=None, contended=None, policy=None):
    runtime = FakeRuntime(f, co, node_id=node) if action not in ("claim", "accept") else None
    real_append, real_flock = co._append, fcntl.flock

    def append(*args, **kwargs):
        if held is not None:
            held.set()
            if not release.wait(20):
                raise RuntimeError("holder release was not signalled")
        return real_append(*args, **kwargs)

    def flock(fd, operation):
        try:
            return real_flock(fd, operation)
        except BlockingIOError:
            if contended is not None:
                contended.set()
            raise

    started = time.monotonic()
    try:
        with contextlib.ExitStack() as stack:
            if policy is not None:
                from orchestration_lock import new_work_guard
                guard = new_work_guard(policy)
                stack.enter_context(patch("orchestration_lock.new_work_guard", return_value=guard))
                stack.enter_context(patch.object(co, "_work_budget", side_effect=guard))
            stack.enter_context(patch.object(co, "_append", side_effect=append))
            stack.enter_context(patch("fcntl.flock", side_effect=flock))
            if action == "claim":
                co.claim(node)
            elif action == "accept":
                co.accept(node, file_ref(co.root / "nodes" / node / "accepted-evidence.json"))
            else:
                with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root), \
                     patch("orchestration_transport.dispatch_journal", return_value=runtime.dispatch_root):
                    getattr(Transport(co, node, runtime=runtime), action)()
        status, error = "PASS", None
    except BaseException as exc:
        status, error = "ERROR", repr(exc)
    root = co.root / "nodes" / node
    result.put(dict(node=node, action=action, status=status, error=error,
                    elapsed=time.monotonic()-started,
                    intent=(root / "dispatch-intent.json").exists(),
                    submit_operation=(root / "operations" / "submit-task-pack" / "intent.json").exists(),
                    submit_calls=runtime.calls.count(("submit", False)) if runtime else 0))


class CoordinatorContentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = CampaignFixture(self.temp.name, version=3)
        self.co = self.f.start()
        self.children = []

    def tearDown(self):
        for proc in self.children:
            proc.join(1)
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
        self.temp.cleanup()

    def start(self, node, action, result, **kwargs):
        proc = MP.Process(target=action_worker, args=(self.f, self.co, node, action, result), kwargs=kwargs)
        proc.start()
        self.children.append(proc)
        return proc

    def overlap(self, first, second):
        held, release, contended, result = MP.Event(), MP.Event(), MP.Event(), MP.Queue()
        self.start(*first, result, held=held, release=release)
        self.assertTrue(held.wait(20), "first operation must hold the actual coordinator lock")
        self.start(*second, result, contended=contended)
        try:
            self.assertTrue(contended.wait(20), "second operation must hit real flock contention")
        finally:
            release.set()
        rows = [result.get(timeout=30), result.get(timeout=30)]
        print("CONTENTION_EVIDENCE", rows, flush=True)
        return rows

    def test_different_node_claims_both_progress_after_real_contention(self):
        rows = self.overlap(("local", "claim"), ("review", "claim"))
        self.assertEqual([r["status"] for r in rows], ["PASS", "PASS"], rows)
        self.assertEqual(len(self.co.events()), 2)
        self.assertEqual(self.co.state()[0]["review"], State.PREPARING)

    def test_prepare_dispatch_overlap_does_not_strand_fresh_intent(self):
        self.co.claim("review")
        self.co.claim("parallel")
        runtime = FakeRuntime(self.f, self.co)
        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root):
            Transport(self.co, "review", runtime=runtime).prepare()
        rows = self.overlap(("parallel", "prepare"), ("review", "dispatch"))
        self.assertEqual([r["status"] for r in rows], ["PASS", "PASS"], rows)
        dispatched = next(r for r in rows if r["action"] == "dispatch")
        self.assertEqual(dispatched["submit_calls"], 1)
        states = self.co.state()[0]
        self.assertEqual((states["review"], states["parallel"]), (State.DISPATCHED, State.PREPARED))



def lock_holder(path, held, release):
    with exclusive_lock(path):
        held.set()
        if not release.wait(20):
            raise RuntimeError("test lock release timed out")


def registered_policy(root, original_header, *, remaining_seconds=30):
    """Pinned auth-shaped records; no live authentication or adoption is claimed."""
    from orchestration_permit import elapsed
    root = Path(root).resolve()
    root.mkdir()
    header = dict(original_header)
    registration = write_json(root / "registration.json", dict(root=str(root), contract=dict(
        run_timeout_seconds=elapsed(original_header)["seconds"] + remaining_seconds)), exclusive=True)
    startup = write_json(root / "startup.json", dict(stop_path=str(root / "STOP")), exclusive=True)
    runtime = write_json(root / "runtime.json", dict(startup=startup), exclusive=True)
    header["run_permit"] = write_json(root / "permit.json", dict(step=dict(ordinal=1)), exclusive=True)
    write_json(root / "run.json", header, exclusive=True)
    return SimpleNamespace(root=root, header=header, fixtures=False,
                           campaign=dict(registration=registration, runtime=runtime))


class CoordinatorBoundaryTests(CoordinatorContentionTests):
    # Inherited overlap regressions intentionally run here as well under the
    # same actual fcntl strategy, rather than substituting a lock implementation.
    def hold(self, path=None):
        held, release = MP.Event(), MP.Event()
        process = MP.Process(target=lock_holder, args=(path or self.co.root / "coordinator.lock", held, release))
        process.start()
        self.children.append(process)
        self.assertTrue(held.wait(10))
        return release

    def prepared(self):
        self.co.claim("review")
        runtime = FakeRuntime(self.f, self.co)
        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root):
            Transport(self.co, "review", runtime=runtime).prepare()
        return runtime

    def test_accept_and_independent_claim_both_progress(self):
        info = self.co.claim("local")
        self.f.work(info)
        evidence = self.f.candidate(self.co, "local")
        # Materialize original native acceptance before forking; no concurrent
        # append to a shared transcript is part of this lock test.
        with patch.object(self.co, "accept", side_effect=lambda node, ref: ref):
            self.f.accept(self.co, "local", evidence)
        rows = self.overlap(("local", "accept"), ("review", "claim"))
        self.assertEqual([r["status"] for r in rows], ["PASS", "PASS"], rows)
        self.assertEqual(self.co.state()[0]["local"], State.ACCEPTED)
        self.assertEqual(self.co.state()[0]["review"], State.PREPARING)
        self.assertIsNotNone(self.co.leases.read()[info["task_id"]]["released_at"])

    def test_changed_header_pin_or_clock_refuses_new_work_while_waiting(self):
        for mutation in ("header", "registration", "clock"):
            with self.subTest(mutation=mutation):
                policy = registered_policy(Path(self.temp.name) / mutation, self.co.header)
                result, contended = MP.Queue(), MP.Event()
                release = self.hold()
                try:
                    if mutation == "clock":
                        # Inherited fake clock returns unknown after an actual
                        # flock conflict; fcntl and Stop/pin checks remain real.
                        from orchestration_permit import elapsed
                        def changing_clock(header):
                            return dict(seconds=None) if contended.is_set() else elapsed(header)
                        with patch("orchestration_permit.elapsed", side_effect=changing_clock):
                            self.start("local", "claim", result, contended=contended, policy=policy)
                    else:
                        self.start("local", "claim", result, contended=contended, policy=policy)
                    self.assertTrue(contended.wait(10))
                    if mutation == "header":
                        path = policy.root / "run.json"
                        header = read_json(path)
                        header["created_at"] = "2099-01-01T00:00:00Z"
                        path.write_text(__import__("json").dumps(header))
                    elif mutation == "registration":
                        path = policy.root / "registration.json"
                        path.write_text(path.read_text() + " ")
                    row = result.get(timeout=10)
                finally:
                    release.set()
                self.assertEqual(row["status"], "ERROR", row)
                expected = {"header": "header changed", "registration": "hash/size mismatch", "clock": "clock is unknown"}
                self.assertIn(expected[mutation], row["error"])
                self.assertFalse((self.co.root / "claims/local.json").exists())
                self.assertEqual(self.co.events(), [])

    def test_stop_or_deadline_after_claim_validation_prevents_claim(self):
        from orchestration_lock import new_work_guard
        from orchestration_permit import elapsed
        for boundary in ("stop", "deadline"):
            with self.subTest(boundary=boundary):
                root = Path(self.temp.name) / ("validation-" + boundary)
                root.mkdir()
                self.f = CampaignFixture(root, version=3)
                self.co = self.f.start()
                policy = registered_policy(root / "policy", self.co.header)
                validated = []
                limit = read_json(policy.campaign["registration"]["path"])["contract"]["run_timeout_seconds"]
                check_claim = self.co._check_claim
                def check(*args, **kwargs):
                    result = check_claim(*args, **kwargs)
                    validated.append(True)
                    if boundary == "stop":
                        (policy.root / "STOP").write_text("Stop during claim validation")
                    return result
                def measured(header):
                    result = elapsed(header)
                    if validated and boundary == "deadline":
                        result["seconds"] = limit + 1
                    return result
                with patch("orchestration_permit.elapsed", side_effect=measured):
                    guard = new_work_guard(policy)
                    with patch("orchestration_lock.new_work_guard", return_value=guard), \
                         patch.object(self.co, "_work_budget", side_effect=guard), \
                         patch.object(self.co, "_check_claim", side_effect=check):
                        expected = "stopped or closed" if boundary == "stop" else "time cap reached"
                        with self.assertRaisesRegex(ContractError, expected):
                            self.co.claim("local")
                self.assertTrue(validated, "the original expensive claim validation must run")
                self.assertFalse((self.co.root / "claims/local.json").exists())
                self.assertEqual(self.co.events(), [])
                self.assertEqual(self.co.leases.read(), {})
                self.assertFalse(node_output(self.co.root, self.co.nodes["local"]).exists())

    def test_same_node_claim_and_dispatch_remain_fail_fast(self):
        rows = self.overlap(("review", "claim"), ("review", "claim"))
        self.assertEqual(sorted(r["status"] for r in rows), ["ERROR", "PASS"])
        self.assertEqual(len(self.co.events()), 1)
        runtime = FakeRuntime(self.f, self.co)
        with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root):
            Transport(self.co, "review", runtime=runtime).prepare()
        rows = self.overlap(("review", "dispatch"), ("review", "dispatch"))
        self.assertEqual(sorted(r["status"] for r in rows), ["ERROR", "PASS"])
        self.assertEqual(sum(r["submit_calls"] for r in rows), 1)
        self.assertEqual(self.co.state()[0]["review"], State.DISPATCHED)

    def test_wait_beyond_five_seconds_preserves_original_clock(self):
        original = (self.co.root / "run.json").read_bytes()
        policy = registered_policy(Path(self.temp.name) / "policy", self.co.header)
        result, contended = MP.Queue(), MP.Event()
        release = self.hold()
        self.start("local", "claim", result, contended=contended, policy=policy)
        try:
            self.assertTrue(contended.wait(10))
            time.sleep(5.3)
        finally:
            release.set()
        row = result.get(timeout=15)
        print("LONG_CONTENTION", row, flush=True)
        self.assertEqual(row["status"], "PASS", row)
        self.assertGreater(row["elapsed"], 5)
        self.assertEqual((self.co.root / "run.json").read_bytes(), original)

    def test_timeout_during_wait_creates_no_dispatch_or_input_intent(self):
        from orchestration_permit import elapsed
        self.prepared()
        policy = registered_policy(Path(self.temp.name) / "policy", self.co.header)
        limit = read_json(policy.campaign["registration"]["path"])["contract"]["run_timeout_seconds"]
        result, contended = MP.Queue(), MP.Event()
        release = self.hold()
        # The registered cap expires only after an actual flock conflict. A
        # short real cap could instead expire during fork before the first
        # flock, and then no contention would ever be observed.
        def expiring_clock(header):
            measured = elapsed(header)
            if contended.is_set():
                measured["seconds"] = limit + 1
            return measured
        try:
            with patch("orchestration_permit.elapsed", side_effect=expiring_clock):
                self.start("review", "dispatch", result, contended=contended, policy=policy)
            self.assertTrue(contended.wait(10))
            row = result.get(timeout=10)
        finally:
            release.set()
        print("EXPIRED_WAIT", row, flush=True)
        self.assertEqual(row["status"], "ERROR", row)
        self.assertIn("time cap reached while waiting", row["error"])
        self.assertFalse(row["intent"])
        self.assertFalse(row["submit_operation"])
        self.assertEqual(row["submit_calls"], 0)
        self.assertEqual(self.co.state()[0]["review"], State.PREPARED)

    def test_stop_and_halt_during_wait_create_no_claim_intent(self):
        for filename in ("STOP", "schedule/halt.json"):
            with self.subTest(filename=filename):
                policy = registered_policy(Path(self.temp.name) / filename.replace("/", "-"), self.co.header)
                result, contended = MP.Queue(), MP.Event()
                release = self.hold()
                try:
                    self.start("local", "claim", result, contended=contended, policy=policy)
                    self.assertTrue(contended.wait(10))
                    path = policy.root / filename
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("stop")
                    row = result.get(timeout=10)
                finally:
                    release.set()
                self.children[-1].join(5)
                self.assertEqual(row["status"], "ERROR", row)
                self.assertIn("stopped or closed", row["error"])
                self.assertFalse((self.co.root / "claims/local.json").exists())
                self.assertEqual(self.co.events(), [])

    def test_replaced_lock_inode_refuses_waiter_without_input(self):
        self.prepared()
        result, contended = MP.Queue(), MP.Event()
        release = self.hold()
        lock = self.co.root / "coordinator.lock"
        try:
            self.start("review", "dispatch", result, contended=contended)
            self.assertTrue(contended.wait(10))
            lock.rename(lock.with_name("original-held.lock"))
            lock.write_text("")
            row = result.get(timeout=10)
        finally:
            release.set()
        self.assertEqual(row["status"], "ERROR", row)
        self.assertIn("lock path changed", row["error"])
        self.assertFalse(row["intent"])
        self.assertFalse(row["submit_operation"])

    def test_existing_orphan_intent_after_stop_never_submits(self):
        runtime = self.prepared()
        transport = Transport(self.co, "review", runtime=runtime)
        append = self.co._append
        class SimulatedCrash(BaseException):
            pass
        def crash(node, target, data):
            if target == State.DISPATCH_INTENT:
                raise SimulatedCrash()
            return append(node, target, data)
        with patch.object(self.co, "_append", side_effect=crash), self.assertRaises(SimulatedCrash):
            transport.dispatch()
        intent = (transport.root / "dispatch-intent.json").read_bytes()
        with patch("orchestration_lock.new_work_guard", side_effect=AssertionError("historical path requested new work")), \
             patch.object(self.co, "_work_budget", side_effect=ContractError("Stop")), \
             patch("orchestration_transport.dispatch_journal", return_value=runtime.dispatch_root):
            result = transport.dispatch()
        self.assertEqual(result["status"], "UNCERTAIN")
        self.assertEqual((transport.root / "dispatch-intent.json").read_bytes(), intent)
        self.assertEqual(runtime.calls.count(("submit", False)), 0)
        self.assertFalse((transport.root / "operations/submit-task-pack/intent.json").exists())

    def test_stop_after_intent_prevents_external_operation(self):
        runtime = self.prepared()
        transport = Transport(self.co, "review", runtime=runtime)
        gate, count = self.co._gate, []
        def stop_before_input(*, new_work=False):
            if new_work:
                count.append(True)
                if len(count) == 2:
                    raise ContractError("Stop after intent")
            return gate(new_work=new_work)
        with patch.object(self.co, "_gate", side_effect=stop_before_input):
            result = transport.dispatch()
        self.assertEqual(result["status"], "UNCERTAIN")
        self.assertEqual(len(count), 2)
        self.assertEqual(runtime.calls.count(("submit", False)), 0)
        self.assertFalse((transport.root / "operations/submit-task-pack/intent.json").exists())

    def test_historical_acquisition_cap_uses_original_deadline(self):
        import orchestration_lock
        clock = [100.0]
        entered = []
        def monotonic():
            return clock[0]
        def sleep(_):
            clock[0] += 301
        release = self.hold()
        try:
            with patch.object(orchestration_lock.time, "monotonic", side_effect=monotonic), \
                 patch.object(orchestration_lock.time, "sleep", side_effect=sleep), \
                 self.assertRaisesRegex(ContractError, "timed out"):
                with self.co.state_lock():
                    entered.append(True)
        finally:
            release.set()
        self.assertEqual(entered, [])
        self.assertEqual(orchestration_lock.HISTORICAL_LOCK_ACQUIRE_SECONDS, 300)

    def test_v2_coordinator_and_create_campaign_locks_stay_fail_fast(self):
        from orchestration_coordinator import Coordinator
        original = self.co.campaign["version"]
        release = self.hold()
        try:
            self.co.campaign["version"] = 2
            with self.assertRaisesRegex(ContractError, "already active"):
                with self.co.state_lock():
                    self.fail("v2 acquired contended state")
        finally:
            self.co.campaign["version"] = original
            release.set()
        release = self.hold(self.f.root / "campaign.lock")
        try:
            with self.assertRaisesRegex(ContractError, "already active"):
                Coordinator.create(self.co.root, self.f.ref, self.co.spec, fixtures=True)
        finally:
            release.set()

    def test_twenty_forced_overlaps_keep_complete_event_history(self):
        results = []
        for number in range(20):
            with self.subTest(round=number):
                root = Path(self.temp.name) / f"pressure-{number:02d}"
                root.mkdir()
                self.f = CampaignFixture(root, version=3)
                self.co = self.f.start()
                if number % 2:
                    self.test_different_node_claims_both_progress_after_real_contention()
                    results.append(dict(round=number, kind="claim", events=len(self.co.events())))
                else:
                    self.test_prepare_dispatch_overlap_does_not_strand_fresh_intent()
                    results.append(dict(round=number, kind="prepare_dispatch", events=len(self.co.events())))
        print("PRESSURE_ROUNDS", results, flush=True)
        self.assertEqual(len(results), 20)


if __name__ == "__main__":
    unittest.main()
