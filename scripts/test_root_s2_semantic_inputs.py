"""New S2 input/native controls only. All generated native rows are OFFLINE fixtures."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import upgrade_jev_benchmark as jb
from orchestration_common import ContractError, file_ref, object_sha, tree_manifest
from test_upgrade_jev_benchmark import PLAN, factors


class SemanticRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="s2semantic-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.corpus = self.base / "corpus"
        self.corpus.mkdir()
        (self.corpus / "fixture.txt").write_text("OFFLINE INPUT FACTOR FIXTURE\n")
        self.source_tree = tree_manifest(self.corpus)
        self.bundle = dict(schema="u2078-s2-semantic-business-input-v1",
            scope="NEW_S2_ONLY_NEVER_REWRITE_OR_RELABEL_ORIGINAL_V3",
            base_corpus=dict(path=str(self.corpus), files=1,
                             tree_object_sha256=object_sha(self.source_tree)),
            origin="ROOT experimental input, not native proof or HUMAN",
            messages_by_class={kind: [dict(id=kind + "-" + str(i),
                body="先记录后续文档建议。" if i == 1 else "保留本轮原输入与写域规则。" if i == 2 else
                     "请现在检查本轮既定依赖，不追加任务。") for i in (1, 2, 3)]
                for kind in ("uneven_successor", "fan_in", "write_conflict")},
            delivery_specification=dict(per_run=3, source_artifact_is_native_proof=False))
        self.path = self.base / "INPUT.json"
        self.path.write_text(json.dumps(self.bundle, ensure_ascii=False))
        self.ref = file_ref(self.path)
        self.root = self.base / "registration"
        _, self.pairs = jb.layout(self.root, file_ref(PLAN), compact=False)

    def factor(self):
        return object_sha(dict(schema="s2-semantic-input-factor-v1",
            corpus_sha256=self.bundle["base_corpus"]["tree_object_sha256"],
            semantic_sha256=self.ref["sha256"], semantic_bytes=self.ref["bytes"],
            sender="claude_a", receiver="codex", boundary="after_native_run_start_before_business",
            bodies_per_run=3))

    def register(self, **change):
        fs = factors(self.pairs)
        for pair in fs.values():
            for value in pair.values():
                value["input_sha256"] = self.factor()
        kwargs = dict(factors=fs, opportunities={kind: dict(dp1=3 if kind == "uneven_successor" else 2, dp2=3)
            for kind in ("uneven_successor", "fan_in", "write_conflict")},
            compact=False, semantic_input=self.ref)
        kwargs.update(change)
        return jb.register(self.root, "s2-semantic-offline-control", PLAN, **kwargs)

    def test_new_input_registration_is_supported_and_derived(self):
        ref = self.register()
        value = jb.validate_registration(ref)
        self.assertEqual(value["semantic_input"], self.ref)
        self.assertEqual(value["opportunities"]["uneven_successor"], dict(dp1=3, dp2=3))
        self.assertEqual(len(value["pairs"]), 15)
        from upgrade_semantic_inputs import input_factor, registration_slots
        self.assertEqual(input_factor(self.ref), self.factor())
        slots = registration_slots(value)
        self.assertEqual(len(slots), 99)
        self.assertEqual(len({s["request_id"] for s in slots}), 99)
        from upgrade_communication_plan import registration_plan
        ordinary = registration_plan(value)
        self.assertEqual(ordinary["total_samples"], 192)
        self.assertFalse({s["request_id"] for s in slots} &
                         {s["request_id"] for s in ordinary["samples"]})

    def test_caller_counts_do_not_replace_derived_sites(self):
        with self.assertRaises(ContractError):
            self.register(opportunities={kind: dict(dp1=2, dp2=1)
                for kind in ("uneven_successor", "fan_in", "write_conflict")})
        self.assertFalse(self.root.exists())

    def test_only_corpus_hash_is_not_the_complete_input_factor(self):
        bad = factors(self.pairs)
        for pair in bad.values():
            for value in pair.values():
                value["input_sha256"] = self.bundle["base_corpus"]["tree_object_sha256"]
        with self.assertRaises(ContractError):
            self.register(factors=bad)
        self.assertFalse(self.root.exists())

    def test_changed_bundle_or_base_bytes_are_refused(self):
        self.register()
        (self.corpus / "fixture.txt").write_text("CHANGED OFFLINE FIXTURE")
        with self.assertRaises(ContractError):
            jb.validate_registration(file_ref(self.root / "registration.json"))

    def test_changed_body_pin_fixed_protocol_or_duplicate_refuses_before_registration(self):
        from upgrade_semantic_inputs import load_input
        original = self.path.read_bytes()
        for change in (lambda v: v["messages_by_class"]["fan_in"][0].update(body="STATUS: COMM_SAMPLE fixed"),
                       lambda v: v["messages_by_class"]["fan_in"][1].update(
                           id=v["messages_by_class"]["fan_in"][0]["id"]),
                       lambda v: v["messages_by_class"]["fan_in"].pop()):
            bad = copy.deepcopy(self.bundle)
            change(bad)
            self.path.write_text(json.dumps(bad))
            with self.subTest(change=change), self.assertRaises(ContractError):
                load_input(file_ref(self.path))
        self.path.write_bytes(original + b" ")
        with self.assertRaises(ContractError):
            load_input(self.ref)

    def test_copied_bundle_stays_outside_original_corpus_and_must_match(self):
        from upgrade_semantic_inputs import BODY_PATH, check_campaign
        value = jb.validate_registration(self.register())
        root = self.base / "campaign"
        root.mkdir()
        copied = root / BODY_PATH
        copied.parent.mkdir()
        copied.write_bytes(self.path.read_bytes())
        campaign = dict(version=3, root=str(root), semantic_input=file_ref(copied),
                        input_tree=dict(files=self.source_tree["files"]))
        self.assertEqual(check_campaign(value, campaign), self.bundle)
        copied.write_bytes(b"CHANGED OFFLINE BUNDLE")
        with self.assertRaises(ContractError):
            check_campaign(value, campaign)
        with self.assertRaises(ContractError):
            check_campaign({"schema": jb.SCHEMA}, campaign)


class SemanticNativeTests(unittest.TestCase):
    def setUp(self):
        from test_upgrade_communication_attribution import OriginalFixture
        self.tmp = tempfile.TemporaryDirectory(prefix="semantic-native-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.f = OriginalFixture(self.root, "codex")
        self.samples, self.notices, self.full_bodies = [], {}, {}
        self.f.header.update(campaign={"fixture_campaign": True})
        self.f.campaign.update(runs=[self.f.header["run"]], python={"path": "/fixture/python"},
                               controller={"path": "/fixture/controller"})
        template = copy.deepcopy(self.f.attempt)
        from cmux_prompt_reference import persist, plan
        from upgrade_communication_observer import canonical, expected_intent, sha
        for index, body in enumerate(("后续文档先记下。", "沿用原来的输入与写域。", "请现在检查既定依赖。")):
            sample = dict(self.f.sample, request_id="semantic-offline-" + str(index),
                body=body, command="rtk proxy /fixture/semantic send " + str(index),
                body_sha256=sha(body), body_bytes=len(body.encode()), position=index)
            spec, marker, channel = expected_intent(sample, self.f.identity)
            full = "[CMUX-AGENT][delivery:" + marker + "][from:" + self.f.sender["surface_uuid"] + "]\n" + body
            planned = plan(full, marker, root=self.f.state / "message-bodies-v1")
            notice, reference = planned["text"], planned["reference"]
            persist(reference, full)
            intent = dict(spec, marker=marker, payload=notice, payload_sha256=sha(notice),
                original_payload_sha256=sha(full), body_reference=reference)
            ip = self.f.state / "cmux-agent-adapter-v1/targets" / channel / "intents" / (marker + ".json")
            ip.parent.mkdir(parents=True, exist_ok=True)
            ip.write_bytes(self.f.encoded(intent))
            attempt = copy.deepcopy(template)
            attempt["native_binding"]["payload_sha256"] = sha(notice)
            attempt["binding"].update(marker=marker, payload_sha256=sha(notice))
            event = attempt["events"][0]
            request = event["native_request"]
            request["params"]["input"][0]["text"] = notice
            fence = event["native_input_fence"]
            fence["native_request_sha256"] = sha(canonical(request))
            fence["binding_sha256"] = sha(json.dumps(attempt["native_binding"], sort_keys=True))
            journal = self.f.state / "message-dispatch-v1" / sha(json.dumps(
                [self.f.sender["surface_uuid"], marker], sort_keys=True))
            journal.mkdir()
            (journal / "attempt-0001.json").write_bytes(self.f.encoded(attempt))
            self.samples.append(sample)
            self.notices[sample["request_id"]] = notice
            self.full_bodies[sample["request_id"]] = full
        self.stack = __import__("contextlib").ExitStack()
        self.addCleanup(self.stack.close)
        import upgrade_communication_attribution as attr
        import upgrade_semantic_inputs as semantic
        self.stack.enter_context(patch.object(attr, "state_root", return_value=self.f.state))
        self.stack.enter_context(patch.object(semantic, "campaign_slots", return_value=self.samples))
        self.stack.enter_context(patch.object(semantic, "_campaign_pin", return_value=self.f.header["campaign"]))

    def complete(self):
        for index, sample in enumerate(self.samples):
            self.f.sender_append(self.f.call(call_id="semantic-call-" + str(index),
                                              command=sample["command"], at=-4 + index * .1))
            self.f.notice = self.notices[sample["request_id"]]
            self.f.append(self.f.inbound())

    def batch(self):
        from upgrade_semantic_inputs import native_batch
        return native_batch(self.f.campaign, self.f.header, self.f.ends(), self.f.readers,
                            not_after=self.f.stamp(5))

    def test_registered_full_inner_bodies_need_three_original_native_sources(self):
        self.complete()
        out = self.batch()
        self.assertTrue(out["ready"], out)
        self.assertEqual([m["body"] for m in out["messages"]], [s["body"] for s in self.samples])
        self.assertEqual(len(out["attribution"]["inputs"]), 3)
        self.assertTrue(all(m["eligible"] is True for m in out["messages"]))
        self.assertTrue(all("native_record" in m["source"] and "sender_call" in m["source"]
                            for m in out["messages"]))

    def test_notice_without_original_sender_call_never_qualifies(self):
        for sample in self.samples:
            self.f.notice = self.notices[sample["request_id"]]
            self.f.append(self.f.inbound())
        out = self.batch()
        self.assertFalse(out["ready"])
        self.assertEqual(out["messages"], [])

    def test_duplicated_notice_does_not_become_a_source(self):
        self.complete()
        self.f.notice = self.notices[self.samples[0]["request_id"]]
        self.f.append(self.f.inbound())
        out = self.batch()
        self.assertFalse(out["ready"])
        self.assertEqual(out["messages"], [])

    def test_duplicate_sender_call_id_invalidates_original_join(self):
        self.complete()
        self.f.sender_append(self.f.call(call_id="semantic-call-0", command="unrelated command", at=2))
        self.assertFalse(self.batch()["ready"])

    def test_foreign_sender_session_is_not_the_registered_lane(self):
        self.complete()
        raw = self.f.sender_path.read_bytes()
        changed = raw.replace(self.f.sender["session_id"].encode(), b"foreign-offline-session")
        self.f.sender_path.write_bytes(changed)
        self.assertFalse(self.batch()["ready"])

    def test_full_native_body_exposure_order_is_separate_from_action(self):
        from upgrade_semantic_inputs import observe_body_exposure, read_requests
        from upgrade_body_read import read_command
        self.complete()
        batch = self.batch()
        order = [self.samples[i]["request_id"] for i in (2, 0, 1)]
        record = dict(order={"order": order}, semantic_inputs=batch["attribution"],
                      read_requests=read_requests(self.f.campaign, batch, order))
        for index, request_id in enumerate(order):
            self.f.notice = self.notices[request_id]
            self.f.full = self.full_bodies[request_id]
            from cmux_prompt_reference import wire_reference
            self.f.reference = wire_reference(self.f.notice)
            self.f.command = read_command("/fixture/python", "/fixture/controller", self.f.notice)
            from test_upgrade_communication_observer import ObserverTests
            call, result = ObserverTests.pair(self.f)
            call["payload"]["call_id"] = "body-" + str(index)
            result["payload"]["call_id"] = "body-" + str(index)
            self.f.append(call, result)
        out = observe_body_exposure(self.f.campaign, self.f.header, record, self.f.ends(), self.f.readers,
                                   not_after=self.f.stamp(5))
        self.assertTrue(out["all_bodies_exposed"], out)
        self.assertEqual(out["native_exposure_order"], order)
        self.assertTrue(out["exposure_matches_advice"])
        self.assertFalse(out["business_accepted"])
        self.assertEqual(out["semantic_action"], "NOT_PROVEN_BY_BODY_EXPOSURE")

    def test_no_read_result_means_no_complete_body_exposure(self):
        from upgrade_semantic_inputs import observe_body_exposure, read_requests
        self.complete()
        batch = self.batch()
        order = [s["request_id"] for s in self.samples]
        record = dict(order={"order": order}, semantic_inputs=batch["attribution"],
                      read_requests=read_requests(self.f.campaign, batch, order))
        out = observe_body_exposure(self.f.campaign, self.f.header, record, self.f.ends(), self.f.readers,
                                   not_after=self.f.stamp(5))
        self.assertFalse(out["all_bodies_exposed"])
        self.assertEqual(len(out["unresolved"]), 3)


class SemanticEntryTests(unittest.TestCase):
    """Actual entry/gate/ledger with a stand-in connection; no live client or API."""
    def setUp(self):
        from contextlib import ExitStack, nullcontext
        from types import SimpleNamespace
        from unittest.mock import Mock
        import jev_on_call
        from test_jev_inbox import MODEL, OfflineInboxConnection, msg
        self.tmp = tempfile.TemporaryDirectory(prefix="semantic-entry-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.conn = OfflineInboxConnection()
        self.reg = dict(arm="advisory-on", registration={"offline_fixture": True},
            budget={"max_model_requests": 20}, model=MODEL, provider="typesafe-fixture",
            transport_source=file_ref(Path(jev_on_call.__file__).resolve()))
        self.co = SimpleNamespace(root=self.root, spec={"run_id": "warm-01"},
            nodes={"inventory": {}}, header={}, fixtures=True,
            campaign=dict(semantic_input={"offline_fixture": True},
                          registration={"offline_fixture": True}, session_group="fixture", sessions={}),
            state_lock=lambda **kw: nullcontext(), _gate=Mock())
        self.batch = dict(ready=True, messages=[msg(i, body) for i, body in enumerate(
            ("note", "constraint", "priority ACT"))], attribution={"offline_fixture": True})
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("jev_registration.load", side_effect=lambda *a, **kw: self.reg))
        self.stack.enter_context(patch("upgrade_benchmark.validate_registration",
            side_effect=lambda *a: {"groups": {"fixture": {"advisory": self.reg["arm"]}}}))
        self.stack.enter_context(patch("upgrade_semantic_inputs.native_batch",
            side_effect=lambda *a, **kw: self.batch))
        self.stack.enter_context(patch("upgrade_semantic_inputs.read_requests", return_value=[]))

    def order(self, event="event-1", messages=None):
        from jev_advice_run import record_inbox_order
        return record_inbox_order(self.co, "inventory", event, messages,
            connection=self.conn if self.reg["arm"] == "advisory-on" else None,
            credential="placeholder-not-a-key" if self.reg["arm"] == "advisory-on" else None)

    def test_caller_eligible_label_is_refused_before_transport(self):
        from test_jev_inbox import msg
        with self.assertRaises(ContractError):
            self.order(messages=[msg(0, "caller forged ACT")])
        self.assertEqual(len(self.conn.sent), 0)
        self.assertFalse((self.root / "jev-inbox/event-1.json").exists())

    def test_incomplete_native_batch_does_not_call_or_publish_order(self):
        self.batch = dict(ready=False, messages=[], attribution={"unresolved": ["fixture"]})
        with self.assertRaises(ContractError):
            self.order()
        self.assertEqual(len(self.conn.sent), 0)
        self.assertFalse((self.root / "jev-inbox/event-1.json").exists())

    def test_source_backed_on_uses_one_ledgered_call_per_body(self):
        from jev_request_ledger import ledger_summary
        out = self.order()
        self.assertEqual(len(self.conn.sent), 3)
        self.assertEqual(out["order"]["order"], ["m2", "m0", "m1"])
        self.assertEqual(out["evidence_class"], "offline_fixture")
        self.assertEqual(out["semantic_inputs"], self.batch["attribution"])
        summary = ledger_summary(self.root / "jev-request-ledger")
        self.assertEqual(summary["reserved_attempts"], 3)
        self.assertEqual(summary["transport_returned"], 3)
        self.co._gate.assert_called_once_with(new_work=True)
        with self.assertRaises(ContractError):
            self.order()
        self.assertEqual(len(self.conn.sent), 3)

    def test_same_source_in_off_arm_has_zero_requests(self):
        self.reg["arm"] = "advisory-off"
        out = self.order()
        self.assertEqual(out["order"]["order"], ["m0", "m1", "m2"])
        self.assertEqual(out["order"]["model_requests"], 0)
        self.assertEqual(len(self.conn.sent), 0)
        self.assertFalse((self.root / "jev-request-ledger").exists())

    def test_401_is_preserved_then_raises_stop_and_never_asks_next_body(self):
        from jev_request_ledger import ledger_summary
        self.conn.statuses = [401]
        with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST"):
            self.order()
        self.assertEqual(len(self.conn.sent), 1)
        self.assertTrue(ledger_summary(self.root / "jev-request-ledger")["stopped"])
        self.assertTrue((self.root / "jev-inbox/event-1.json").exists())
        with self.assertRaisesRegex(ContractError, "STOP_AFFECTED_JEV_TEST"):
            self.order(event="event-2")
        self.assertEqual(len(self.conn.sent), 1)
        self.assertFalse((self.root / "jev-inbox/event-2.json").exists())


class SemanticSenderTests(unittest.TestCase):
    def test_one_direct_argv_preserves_unicode_and_pending_without_supervisor_identity_bypass(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest.mock import Mock
        from orchestration_coordinator import State
        import upgrade_semantic_inputs as sem
        with tempfile.TemporaryDirectory(prefix="semantic-send-fixture-") as tmp:
            root = Path(tmp).resolve()
            co = SimpleNamespace(root=root, state_lock=lambda **kw: nullcontext(),
                _work_budget=Mock(), state=lambda: ({"inventory": State.PENDING}, {}))
            argv = ["rtk", "proxy", "/fixture/helper", "send", "original-uuid",
                    "--request-id", "semantic-1", "正文完整保留，不做 shell 展开 $1。"]
            sample = dict(request_id="semantic-1", run_id="warm-01", helper_argv=argv)
            with patch.object(sem, "campaign_slots", return_value=[sample]), \
                 patch.object(sem, "_campaign_pin", return_value={"offline_fixture": True}), \
                 patch("orchestration_coordinator.Coordinator", return_value=co), \
                 patch.object(sem, "_require_original_sender") as guard, \
                 patch.object(sem.subprocess, "run", return_value=SimpleNamespace(returncode=75)) as process:
                self.assertEqual(sem.send_body({"root": str(root)}, "warm-01", "semantic-1"), 75)
                process.assert_called_once_with(argv, check=False)
                guard.assert_called_once()
                co._work_budget.assert_called_once()

    def test_foreign_sender_never_calls_helper_or_retries(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest.mock import Mock
        from orchestration_coordinator import State
        import upgrade_semantic_inputs as sem
        with tempfile.TemporaryDirectory(prefix="semantic-deny-fixture-") as tmp:
            root = Path(tmp).resolve()
            co = SimpleNamespace(root=root, state_lock=lambda **kw: nullcontext(),
                _work_budget=Mock(), state=lambda: ({"inventory": State.PENDING}, {}))
            sample = dict(request_id="semantic-1", run_id="warm-01", helper_argv=["fixture"])
            with patch.object(sem, "campaign_slots", return_value=[sample]), \
                 patch.object(sem, "_campaign_pin", return_value={"offline_fixture": True}), \
                 patch("orchestration_coordinator.Coordinator", return_value=co), \
                 patch.object(sem, "_require_original_sender", side_effect=ContractError("foreign caller")), \
                 patch.object(sem.subprocess, "run") as process:
                self.assertEqual(sem.send_body({"root": str(root)}, "warm-01", "semantic-1"), 75)
                process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
