"""Whole-lifecycle regressions; all generated transcripts are offline fixtures."""
import copy
from datetime import timedelta
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from orchestration_campaign import METRICS, ORDER, collect_live_sessions, require_live_identity, validate_campaign
from orchestration_common import ContractError, digest, file_ref, json_ref, parse_time, read_json, utc_now, write_json
from orchestration_coordinator import Coordinator, State, strategy_probe
from orchestration_evidence import verify_node
from orchestration_native import delivery_identity
from orchestration_test_support import CampaignFixture, OfflineDisarmRuntime
from orchestration_transport import Transport
from orchestration_verify import derive_run, finish_run, select_promotion, verify_campaign, verify_run


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = CampaignFixture(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def only(self, *keys):
        self.f.plan["nodes"] = [n for n in self.f.plan["nodes"] if n["id"] in keys]
        self.f.campaign["plan"] = write_json(self.f.root / "plan.json", self.f.plan)
        self.f.save()

    def local(self, co, key="local"):
        info = co.claim(key)
        self.f.work(info)
        return self.f.accept(co, key, self.f.candidate(co, key))

    def delegated(self, co):
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        self.f.work(info, "claude")
        protocol = self.f.callback(co, "review")
        result = self.f.accept(co, "review", self.f.candidate(co, "review", protocol))
        self.f.disarm(co, "review")
        return result

    def test_complete_delegated_local_and_dependency_run(self):
        co = self.f.start()
        self.local(co)
        self.delegated(co)
        self.local(co, "closeout")
        result = finish_run(co)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["nodes_accepted"], 3)
        self.assertEqual(result["metrics"]["transport_retries"], 0)
        self.assertEqual(result["metrics"]["human_interventions"], 0)
        self.assertEqual(result["metrics"]["provider_auto_retries"], 0)
        self.assertEqual(result["evidence_class"], "offline_fixture")
        self.assertFalse(verify_campaign(self.f.ref, fixtures=True)["promotion"]["promoted"])

    def test_parallel_finish_uses_event_order_not_node_order(self):
        self.only("local", "review")
        co = self.f.start()
        local = co.claim("local")
        self.delegated(co)
        self.f.work(local)
        self.f.accept(co, "local", self.f.candidate(co, "local"))
        result = finish_run(co)
        self.assertEqual(co.events()[-1]["node_id"], "local")
        expected = (parse_time(co.events()[-1]["at"]) - parse_time(co.header["started_at"])).total_seconds()
        self.assertEqual(result["metrics"]["elapsed_wall_seconds"], expected)

    def test_cross_boot_preserves_transition_but_does_not_claim_elapsed(self):
        self.only("local")
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        evidence = self.f.candidate(co, "local")
        previous = co.events()[-1]
        with patch("orchestration_coordinator.boot_id", return_value="next-boot"), \
             patch("orchestration_coordinator.time.monotonic_ns", return_value=7):
            self.f.accept(co, "local", evidence)
        self.assertEqual(co.events()[-1]["clock_transition"], dict(from_boot=previous["boot_id"],
            to_boot="next-boot", previous_monotonic_ns=previous["monotonic_ns"]))
        metrics = finish_run(co)["metrics"]
        self.assertEqual(metrics["clock_status"], "cross_boot_unmeasured")
        self.assertIsNone(metrics["elapsed_seconds"])

    def test_forward_wall_jump_is_preserved_as_unmeasured(self):
        self.only("local")
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        evidence = self.f.candidate(co, "local")
        future = parse_time(utc_now()) + timedelta(hours=1)
        with patch("orchestration_coordinator.utc_now", return_value=future.isoformat()):
            self.f.accept(co, "local", evidence)
        with patch("orchestration_verify.utc_now", return_value=(future + timedelta(seconds=1)).isoformat()):
            metrics = finish_run(co)["metrics"]
        self.assertEqual(metrics["clock_status"], "wall_clock_discontinuity_unmeasured")
        self.assertIsNone(metrics["elapsed_seconds"])
        self.assertGreater(metrics["elapsed_wall_seconds"], 3600)

    def test_missing_middle_or_final_event_cannot_verify_closed_run(self):
        self.only("local")
        co = self.f.start()
        self.local(co)
        finish_run(co)
        for name in ("000002.json", "000003.json"):
            path = co.root / "events" / name
            original = path.read_bytes()
            with self.subTest(name=name):
                path.unlink()
                with self.assertRaisesRegex(ContractError, "event history"):
                    verify_run(self.f.ref, co.spec["run_id"], fixtures=True)
                path.write_bytes(original)
        self.assertEqual(verify_run(self.f.ref, co.spec["run_id"], fixtures=True)["status"], "PASS")

    def test_terminal_lease_without_original_disarm_cannot_close(self):
        self.only("review")
        co = self.f.start()
        with patch.object(self.f, "disarm"):
            self.delegated(co)
        with self.assertRaisesRegex(ContractError, "cannot read.*disarm.json"):
            finish_run(co)
        self.assertFalse((co.root / "finish.json").exists())
        self.f.disarm(co, "review")
        self.assertEqual(len(finish_run(co)["disarms"]), 1)

    def assert_disarm_closeout(self, co, proof_kind):
        result = finish_run(co)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["disarms"]), 1)
        disarm = json_ref(result["disarms"][0])
        self.assertEqual(disarm["proof_kind"], proof_kind)
        terminal_at = co.events()[-1]["at"]
        self.assertLess(parse_time(terminal_at), parse_time(disarm["proof"]["observed_at"]))
        path = co.root / "finish.json"
        original_ref, original = file_ref(path), read_json(path)
        write_json(path, dict(original, closed_at=terminal_at))
        with self.assertRaisesRegex(ContractError, "closeout precedes scoped disarm"):
            verify_run(self.f.ref, co.spec["run_id"], fixtures=True)
        write_json(path, original)
        self.assertEqual(file_ref(path), original_ref)
        self.assertEqual(verify_run(self.f.ref, co.spec["run_id"], fixtures=True)["status"], "PASS")

    def test_original_return_closeout_uses_verified_observation(self):
        self.only("review")
        co = self.f.start()
        self.delegated(co)
        self.assert_disarm_closeout(co, "ORIGINAL_RETURN")

    def test_bounded_recovery_closeout_uses_verified_observation(self):
        self.only("review")
        co = self.f.start()
        with patch.object(self.f, "disarm"):
            self.delegated(co)
        runtime = OfflineDisarmRuntime(self.f.campaign, co.root / "offline-disarm")
        with patch.object(runtime, "disarm", side_effect=RuntimeError("simulated original refusal")):
            ref = Transport(co, "review", runtime=runtime).disarm()
        operation = json_ref(ref)["operation_result"]
        self.assertEqual(json_ref(operation)["status"], "ERROR")
        self.assertEqual(runtime.recoveries, ["DISARM"])
        self.assert_disarm_closeout(co, "BOUNDED_RECOVERY")
        self.assertEqual(file_ref(operation["path"]), operation)

    def test_disarm_postcondition_and_original_intent_are_required(self):
        self.only("review")
        co = self.f.start()
        self.delegated(co)
        root = co.root / "nodes/review"
        operation = root / "operations/scoped-disarm/result.json"
        original = read_json(operation)
        original_record = read_json(root / "disarm.json")
        for mutation, reason in (
                (lambda v: v["value"].update(after=v["value"]["before"]), "original marker/reservation release unproven"),
                (lambda v: v["value"].update(workspace_uuid="wrong-workspace"), "invalid scoped disarm result"),
                (lambda v: v["value"].update(removed_markers=0), "scoped disarm removal count changed"),
                (lambda v: v["value"].update(observed_at="2000-01-01T00:00:00Z"), "disarm observation chronology changed")):
            with self.subTest(reason=reason):
                value = copy.deepcopy(original)
                mutation(value)
                result_ref = write_json(operation, value)
                write_json(root / "disarm.json", dict(original_record, operation_result=result_ref, proof=value["value"]))
                with self.assertRaisesRegex(ContractError, reason):
                    finish_run(co)
        write_json(operation, original)
        write_json(root / "disarm.json", original_record)
        self.assertEqual(finish_run(co)["status"], "PASS")

    def test_saved_metrics_cannot_replace_native_measurements(self):
        self.only("local")
        co = self.f.start()
        self.local(co)
        finish_run(co)
        value = read_json(co.root / "finish.json")
        value["summary"]["metrics"]["elapsed_seconds"] = 0
        write_json(co.root / "finish.json", value)
        with self.assertRaisesRegex(ContractError, "stored metrics differ"):
            verify_run(self.f.ref, co.spec["run_id"], fixtures=True)

    def test_release_crash_repairs_only_original_verified_terminal(self):
        self.only("local")
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        candidate = self.f.candidate(co, "local")
        with patch.object(co, "_release", side_effect=RuntimeError("simulated crash")):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                self.f.accept(co, "local", candidate)
        resumed = Coordinator(co.root, self.f.ref, fixtures=True)
        self.assertEqual(resumed.recover()[0]["state"], "TERMINAL_RELEASE_PENDING")
        with self.assertRaisesRegex(ContractError, "needs exact release"):
            finish_run(resumed)
        self.assertFalse((co.root / "finish.json").exists())
        self.assertEqual(resumed.repair_releases(), ["local"])
        self.assertEqual(resumed.repair_releases(), [])
        self.assertEqual(finish_run(resumed)["status"], "PASS")

    def test_active_lease_cannot_be_marked_released(self):
        co = self.f.start()
        info = co.claim("local")
        registry = co.leases.read()
        lease = registry[info["task_id"]]
        lease.update(released_at=utc_now(), released_by=file_ref(co.root / "events/000001.json"))
        write_json(co.leases.root / "leases.json", registry)
        with self.assertRaisesRegex(ContractError, "prematurely released"):
            co.state()

    def test_accepted_result_must_extend_the_exact_frozen_candidate(self):
        co = self.f.start()
        info = co.claim("local")
        self.f.work(info)
        candidate = self.f.candidate(co, "local")
        self.f.accept(co, "local", candidate)
        info = co.state()[1]["local"]
        accepted = json_ref(info["accepted_evidence"])
        accepted["review_kind"] = "independent_executor_review"
        with self.assertRaisesRegex(ContractError, "differs from the previously frozen candidate"):
            verify_node(self.f.campaign, co.header, co.nodes["local"], info, accepted, fixtures=True)

    def append_late_work(self, info, lane="codex"):
        client = self.f.clients[lane]
        if lane == "codex":
            client.append(dict(type="response_item", timestamp=utc_now(), payload=dict(type="custom_tool_call",
                name="exec", call_id="late-work", input="write " + info["output_root"] + "/result.json")))
            client.append(dict(type="response_item", timestamp=utc_now(), payload=dict(type="custom_tool_call_output",
                call_id="late-work", output={"exit_code": 0})))
        else:
            call = client.message("assistant", "", at=utc_now())
            call["message"]["content"] = [dict(type="tool_use", id="late-work", name="Write",
                input={"file_path": info["output_root"] + "/result.json"})]
            client.append(call)
            result = client.message("user", "", at=utc_now())
            result["message"]["content"] = [dict(type="tool_result", tool_use_id="late-work", content="ok")]
            client.append(result)

    def review_with_late_work(self):
        self.only("review")
        co = self.f.start()
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        self.f.work(info, "claude")
        protocol = self.f.callback(co, "review")
        self.append_late_work(info, "claude")
        return co, info, protocol

    def test_native_output_after_callback_cannot_be_accepted(self):
        co, info, protocol = self.review_with_late_work()
        candidate = self.f.candidate(co, "review", protocol)
        with self.assertRaisesRegex(ContractError, "writes continued after callback"):
            self.f.accept(co, "review", candidate)
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])
        self.assertEqual(co.state()[0]["review"], State.EVIDENCE_READY)

    def test_native_output_after_callback_cannot_settle_failure(self):
        co, info, protocol = self.review_with_late_work()
        self.f.block(co, "review", "observed output after callback")
        proof = self.f.failure(co, "review", "callback_boundary", protocol=protocol,
                               report=file_ref(Path(info["output_root"]) / "report.md"))
        with self.assertRaisesRegex(ContractError, "work continued after callback"):
            co.settle_failure("review", proof)
        self.assertIsNone(co.leases.read()[info["task_id"]]["released_at"])
        self.assertEqual(co.state()[0]["review"], State.BLOCKED)

    def test_native_work_after_acceptance_prevents_closeout(self):
        self.only("local")
        co = self.f.start()
        self.local(co)
        self.append_late_work(co.state()[1]["local"])
        with self.assertRaisesRegex(ContractError, "continued after terminal boundary"):
            finish_run(co)

    def test_unknown_human_input_is_unmeasured_not_zero(self):
        self.only("local")
        co = self.f.start()
        client = self.f.clients["codex"]
        client.append(client.message("user", "status?", at=utc_now()))
        client.append(client.message("user", "Please adjust the result", at=utc_now()))
        self.local(co)
        metrics = finish_run(co)["metrics"]
        self.assertIsNone(metrics["human_interventions"])
        self.assertIsNone(metrics["reasks"])
        self.assertEqual(len(metrics["unclassified_inputs"]), 1)
        self.assertEqual(len(metrics["status_queries"]), 1)

    def test_local_failed_work_is_preserved_and_releases_after_settlement(self):
        self.only("local")
        co = self.f.start()
        co.claim("local")
        self.f.block(co, "local")
        proof = self.f.failure(co, "local")
        co.settle_failure("local", proof)
        result = finish_run(co)
        self.assertEqual(result["status"], "FAILED_PRESERVED")
        self.assertFalse(result["contract_pass"])
        self.assertEqual(co.ready(), [])

    def test_delegated_callback_can_settle_a_failed_result(self):
        self.only("review")
        co = self.f.start()
        info = co.claim("review")
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        self.f.dispatch(co, "review")
        self.f.work(info, "claude")
        protocol = self.f.callback(co, "review")
        self.f.block(co, "review", "independent checker rejected the reported result")
        proof = self.f.failure(co, "review", "callback_boundary", protocol=protocol,
                               report=file_ref(Path(info["output_root"]) / "report.md"))
        co.settle_failure("review", proof)
        self.f.disarm(co, "review")
        self.assertEqual(finish_run(co)["status"], "FAILED_PRESERVED")

    def zero_input(self, co):
        self.f.prepare(co, "review")
        self.f.intent(co, "review")
        info = co.state()[1]["review"]
        client = self.f.clients["claude"]
        notice = Path(info["prepared"]["notice"]["path"]).read_text()
        epoch = parse_time(utc_now()).timestamp()
        identity = delivery_identity(client.session, self.f.clients["codex"].session)
        binding = dict(task_id=info["task_id"], marker=info["task_id"], payload_sha256=digest(notice.encode()),
                       task_pack_sha256=info["prepared"]["pack"]["sha256"], identity=identity)
        native = dict(schema="native-delivery-v1", identity=identity, provider="claude",
                      session_id=client.session["session_id"], process=client.session["process"],
                      tty=client.session["tty"], transcript=client.reader.snapshot(),
                      bound_at_epoch=epoch, payload_sha256=digest(notice.encode()))
        ref = write_json(co.root / "nodes/review/task-dispatch/attempt-0001.json", dict(
            phase="NO_INPUT", binding=binding, native_binding=native, events=[], started_at_epoch=epoch,
            ended_at_epoch=epoch, error="existing draft preserved before input"))
        uncertainty = write_json(co.root / "nodes/review/uncertainty.json", dict(task_id=info["task_id"],
            reason="original submit returned without input"))
        co.transition("review", State.UNCERTAIN, {"uncertainty": uncertainty})
        self.f.block(co, "review")
        return ref

    def test_completed_zero_input_operation_can_release_empty_scope(self):
        self.only("review")
        co = self.f.start()
        co.claim("review")
        attempt = self.zero_input(co)
        proof = self.f.failure(co, "review", "no_business_input", no_input_attempts=[attempt])
        co.settle_failure("review", proof)
        self.f.disarm(co, "review")
        result = finish_run(co)
        self.assertEqual(result["status"], "FAILED_PRESERVED")
        self.assertIsNone(result["metrics"]["transport_retries"])

    def test_zero_input_missing_end_and_unknown_events_preserve_lease(self):
        self.only("review")
        co = self.f.start()
        co.claim("review")
        attempt = self.zero_input(co)
        original = json_ref(attempt)
        for mutation in (lambda v: v.pop("ended_at_epoch"),
                         lambda v: v.update(events=[{"phase": "PASTE_INTENT"}]),
                         lambda v: v["binding"].update(task_id="other-task")):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(original)
                mutation(value)
                changed = write_json(attempt["path"], value)
                proof = self.f.failure(co, "review", "no_business_input", no_input_attempts=[changed])
                with self.assertRaises(ContractError):
                    co.settle_failure("review", proof)
                self.assertIsNone(next(iter(co.leases.read().values()))["released_at"])

    def test_unfinished_preparation_cannot_claim_no_business_input(self):
        self.only("review")
        co = self.f.start()
        co.claim("review")
        self.f.block(co, "review")
        proof = self.f.failure(co, "review", "no_business_input", no_input_attempts=[])
        with self.assertRaisesRegex(ContractError, "preparation completion is unproven"):
            co.settle_failure("review", proof)
        with self.assertRaisesRegex(ContractError, "active/uncertain"):
            finish_run(co)

    def test_frozen_metric_definition_cannot_be_weakened(self):
        self.f.campaign["metrics"]["zero_baseline"] = "count_zero_as_improvement"
        self.f.save()
        with self.assertRaisesRegex(ContractError, "measurement definitions"):
            validate_campaign(self.f.ref, fixtures=True)


class PromotionTests(unittest.TestCase):
    def runs(self):
        return [dict(strategy=s, status="PASS", contract_pass=True, nodes_total=4, nodes_accepted=4,
                     metrics=dict(elapsed_seconds={"serial": 100, "omo": 80, "codeg": 90}[s],
                                  human_interventions=0, retries=0)) for s in ORDER]

    def test_counterbalanced_time_improvement_uses_medians(self):
        runs = self.runs()
        runs[1]["metrics"]["elapsed_seconds"] = 10000
        result = select_promotion(runs, live=True)
        self.assertEqual(result["selected"], "omo")
        self.assertEqual(result["improvements"]["omo"]["elapsed_seconds"], 20)
        self.assertIsNone(result["improvements"]["omo"]["retries"])

    def test_zero_baselines_missing_metrics_and_fixtures_never_invent_gain(self):
        runs = self.runs()
        self.assertFalse(select_promotion(runs, live=False)["promoted"])
        for run in runs:
            run["metrics"]["elapsed_seconds"] = None
        self.assertFalse(select_promotion(runs, live=True)["promoted"])

    def test_any_contract_failure_incomplete_run_or_wrong_order_retains_baseline(self):
        for mode in ("failed", "incomplete", "wrong_order", "missing"):
            runs = self.runs()
            if mode == "failed":
                runs[4]["contract_pass"] = False
            elif mode == "incomplete":
                runs[4]["nodes_accepted"] = 3
            elif mode == "wrong_order":
                runs.reverse()
            else:
                runs.pop()
            self.assertFalse(select_promotion(runs, live=True)["promoted"])

    def test_interventions_or_retries_require_a_positive_baseline(self):
        for field in ("human_interventions", "retries"):
            runs = self.runs()
            for run in runs:
                run["metrics"].update(elapsed_seconds=100)
                run["metrics"][field] = 10 if run["strategy"] == "serial" else 8
            result = select_promotion(runs, live=True)
            self.assertTrue(result["promoted"])
            self.assertEqual(result["selected"], "omo")


class IdentityAdapterTests(unittest.TestCase):
    def private_identity(self):
        import uuid
        workspace = str(uuid.uuid4())
        sessions = {}
        for i, lane in enumerate(("codex", "claude_a", "claude_b"), 1):
            sessions[lane] = dict(provider="codex" if lane == "codex" else "claude",
                session_id=str(uuid.uuid4()), tty=f"ttys{i:03d}",
                process=dict(pid=100 + i, birth=[12345, i], executable="/native/cli",
                             file_identity=dict(device=1, inode=10 + i)),
                workspace_uuid=workspace, surface_uuid=str(uuid.uuid4()), pane_uuid=str(uuid.uuid4()),
                transcript=dict(path=f"/native/{lane}.jsonl", device=1, inode=20 + i),
                native_config_root=f"/private/{lane}")
        campaign = dict(version=3, controller={"path": "/pinned/controller"}, sessions=sessions)
        live = copy.deepcopy(sessions)
        for index, row in enumerate(live.values()):
            for key in ("workspace_uuid", "surface_uuid", "pane_uuid"):
                row[key] = row[key].upper() if index != 1 else row[key][:18].upper() + row[key][18:]
        return campaign, live

    def collect_and_require(self, campaign, live):
        codex = live["codex"]
        raw = {"client": codex["process"]}
        public = dict(session=codex["session_id"], tty=codex["tty"],
                      foreground={"thread_id": codex["session_id"]})
        caller = {a: codex[b] for a, b in (("workspace_id", "workspace_uuid"),
                   ("surface_id", "surface_uuid"), ("pane_id", "pane_uuid"))}
        def target(_bridge, surface, purpose):
            self.assertEqual(purpose, "ORCHESTRATION_IDENTITY_READ_ONLY")
            lane = next(k for k, v in campaign["sessions"].items() if v["surface_uuid"] == surface)
            row = live[lane]
            return dict(row, identity=dict(workspace_uuid=row["workspace_uuid"],
                target_surface_uuid=row["surface_uuid"], target_pane_uuid=row["pane_uuid"]))
        native = SimpleNamespace(_public_process=Mock(return_value=codex["process"]),
            _transcript_path=Mock(return_value=codex["transcript"]["path"]),
            _pin_file=Mock(return_value=codex["transcript"]), bind_target=Mock(side_effect=target),
            private_roots=SimpleNamespace(__file__="/pinned/controller/scripts/upgrade_native_roots.py",
                process_root=Mock(return_value=codex["native_config_root"])))
        guard = SimpleNamespace(caller_snapshot=Mock(return_value=({"caller": caller}, None, None, public)))
        daemon = SimpleNamespace(collect=Mock(return_value=raw))
        with patch("orchestration_campaign.controller_modules", return_value=(object(), native, guard, daemon)):
            return require_live_identity(campaign)

    def test_v3_three_lane_uppercase_and_mixed_uuid_round_trip(self):
        campaign, live = self.private_identity()
        before = copy.deepcopy(campaign)
        self.assertEqual(self.collect_and_require(campaign, live), campaign["sessions"])
        self.assertEqual(campaign, before)

    def test_v3_changed_uuid_process_session_tty_or_transcript_still_refused(self):
        import uuid
        mutations = {
            **{key: (lambda row, k=key: row.update({k: str(uuid.uuid4())}))
               for key in ("workspace_uuid", "surface_uuid", "pane_uuid", "session_id")},
            "pid": lambda row: row["process"].update(pid=999),
            "birth": lambda row: row["process"].update(birth=[999, 1]),
            "executable": lambda row: row["process"].update(executable="/different/cli"),
            "file_identity": lambda row: row["process"]["file_identity"].update(inode=999),
            "tty": lambda row: row.update(tty="ttys999"),
            "native_config_root": lambda row: row.update(native_config_root="/different/root"),
            **{f"transcript_{key}": (lambda row, k=key, v=value: row["transcript"].update({k: v}))
               for key, value in (("path", "/different/log"), ("device", 999), ("inode", 999))},
        }
        for lane in ("codex", "claude_a", "claude_b"):
            for field, mutate in mutations.items():
                with self.subTest(lane=lane, field=field):
                    campaign, live = self.private_identity()
                    mutate(live[lane])
                    with self.assertRaises(ContractError):
                        self.collect_and_require(campaign, live)

    def test_v3_invalid_live_uuids_fail_closed_through_collection(self):
        for lane in ("codex", "claude_a", "claude_b"):
            for key in ("workspace_uuid", "surface_uuid", "pane_uuid"):
                for value in (None, True, 12, "", "not-a-uuid"):
                    with self.subTest(lane=lane, key=key, value=value):
                        campaign, live = self.private_identity()
                        live[lane][key] = value
                        with self.assertRaisesRegex(ContractError, f"invalid live {key}"):
                            self.collect_and_require(campaign, live)

    def test_raw_proof_goes_to_snapshot_without_second_public_conversion(self):
        raw = {"client": {"pid": 123}}
        public = dict(session="original-codex", tty="ttys073", foreground={"thread_id": "original-codex"})
        caller = dict(workspace_id="workspace", surface_id="codex", pane_id="pane-one")
        guard = SimpleNamespace(caller_snapshot=Mock(return_value=({"caller": caller}, None, None, public)),
                                public_proof=Mock(side_effect=AssertionError("already public")))
        native = SimpleNamespace(_public_process=Mock(return_value={"pid": 123}),
            _transcript_path=Mock(return_value="/native/codex.jsonl"),
            _pin_file=Mock(return_value=dict(path="/native/codex.jsonl", device=1, inode=2)),
            bind_target=Mock(return_value=dict(provider="claude", session_id="original-claude", tty="ttys079",
                process={"pid": 456}, identity=dict(workspace_uuid="workspace", target_surface_uuid="claude",
                target_pane_uuid="pane-two"), transcript=dict(path="/native/claude.jsonl", device=1, inode=3))))
        daemon = SimpleNamespace(collect=Mock(return_value=raw))
        campaign = dict(controller={"path": "/pinned/controller"}, sessions=dict(
            codex={"session_id": "original-codex"}, claude={"surface_uuid": "claude"}))
        with patch("orchestration_campaign.controller_modules", return_value=(object(), native, guard, daemon)):
            result = collect_live_sessions(campaign)
        guard.caller_snapshot.assert_called_once_with(initial_proof=raw)
        guard.public_proof.assert_not_called()
        native._public_process.assert_called_once_with(raw["client"])
        self.assertEqual(result["codex"]["session_id"], "original-codex")
        self.assertEqual(result["claude"]["surface_uuid"], "claude")


if __name__ == "__main__":
    unittest.main()
