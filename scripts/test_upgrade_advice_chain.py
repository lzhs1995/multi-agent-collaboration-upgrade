"""The advice/read/decision/binding join, at checker level, on frozen evidence."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import upgrade_advice_chain
from orchestration_binding import POLICY, SCHEMA as BINDING_SCHEMA, write_binding
from orchestration_common import ContractError, file_ref, json_ref, parse_time, utc_now, write_json
from orchestration_native import NativeReader
from orchestration_test_support import CampaignFixture
from upgrade_advice_chain import (ADVICE_PREDICATES, DECISION_WRITE_PROOF, READ_PROOF, SCHEMA,
                                  advice_chain, advice_session_consistent, binding_cites_decision,
                                  decision_cites_this_advice, fallback_not_counted_as_consumed,
                                  off_arm_has_no_advice_file, off_arm_has_no_advice_read,
                                  off_arm_worker_requests_zero, ordered_chain, read_is_full,
                                  route_within_recomputed)
from upgrade_body_read import read_result
from upgrade_communication_attribution import _session_valid

DOCUMENTED_PREDICATES = {"advice_session_consistent", "advice_read_evidence", "read_is_full",
                         "decision_cites_this_advice", "binding_cites_decision", "route_within_recomputed",
                         "ordered_chain", "off_arm_has_no_advice_file", "off_arm_worker_requests_zero",
                         "off_arm_has_no_advice_read", "fallback_not_counted_as_consumed",
                         "advice_read_attempts", "decision_publish_evidence",
                         "publish_call_is_the_frozen_script", "publication_pins_the_decision",
                         "publish_follows_the_read", "decision_is_published", "bind_call_evidence"}


def suggesting_transport(request):
    return dict(answers=dict(work_kind="coding", work_kind_applies=0.9), http_status=200)


class AdviceChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, strategies=("omo",), version=3, arm="B", unbound=True)
        self.co = self.f.start()
        self.run = self.co.spec["run_id"]
        self.reads, self.decided = [], None

    @staticmethod
    def later_than(*stamps):
        """A stamp strictly after every given stamp and after now.

        The fixture's native read result is stamped one second after its call,
        so a decision stamped with a bare ``utc_now()`` would precede it. Every
        default stamp is derived from what it must follow instead.
        """
        moments = [parse_time(value) for value in stamps if value] + [parse_time(utc_now())]
        return (max(moments) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")

    def read(self, advice, **kwargs):
        observed = self.f.fake_advice_read_records(advice, **kwargs)
        self.reads.append(observed)
        return observed

    def readers(self):
        return {lane: NativeReader(session, fixtures=True)
                for lane, session in self.f.campaign["sessions"].items()}

    def on_arm(self, *, lanes=None, node_id="unbound"):
        return self.f.publish_fixture_advice(self.co, node_id, transport=suggesting_transport,
                                             budget=dict(max_model_requests=80),
                                             lanes=lanes if lanes is not None else self.lane_facts())

    def lane_facts(self):
        return {lane: dict(kind="codex" if lane == "codex" else "claude", verified=True,
                           idle_write_domain=True, author=False)
                for lane in self.f.campaign["sessions"]}

    def binding(self, advice, decision_ref, *, node_id="unbound", lane="codex", **changes):
        value = dict(schema=BINDING_SCHEMA, campaign=self.f.ref, run_id=self.run, node_id=node_id,
                     lane=lane, execution_kind="local_cli", transport="codex_cli", mode="local_cli",
                     arm=self.f.campaign.get("arm"), decision=decision_ref, advice=advice,
                     eligible_lanes=["codex", "claude_a", "claude_b"], lane_facts=self.lane_facts(),
                     bound_at=self.later_than(self.decided, *(r["result_at"] for r in self.reads)),
                     bound_native=None, input_units=self.f.campaign["inputs"],
                     supervisor_session=self.f.campaign["sessions"]["codex"]["session_id"])
        value.update(changes)
        return value

    def decision(self, advice, *, name="decision", node_id="unbound", **changes):
        """One supervisor-authored decision file per name, outside the run tree."""
        fields = dict(work_kind="coding", actual_route="codex",
                      advice_sha256=advice.get("advice_sha256") if advice.get("arm") == "advisory-on" else None,
                      decided_at=self.later_than(*(r["result_at"] for r in self.reads)))
        fields.update(changes)
        self.decided = fields["decided_at"]
        return self.f.write_decision(self.run, node_id,
                                     path=self.f.root / "decisions" / f"{name}.json", **fields)

    def chain(self, advice, *, decision=None, binding=None, node_id="unbound", not_after=None, **kw):
        decision_ref = decision if decision is not None else self.decision(advice, node_id=node_id)
        value = binding if binding is not None else self.binding(advice, decision_ref, node_id=node_id)
        ref = write_binding(self.co, node_id, value)
        later = parse_time(utc_now()).timestamp() + 60
        return advice_chain(self.co, node_id, {}, self.readers(), registration=None,
                            not_after=not_after if not_after is not None else later,
                            binding_ref=ref, **kw)

    def fresh(self):
        """A brand-new fixture for one subTest; nothing is reused across cases."""
        self.doCleanups()
        self.setUp()

    def test_complete_chain_is_valid_and_claims_nothing_more(self):
        """Read, decision and binding all join - and that is still not consumption.

        Every decision in this checker-level file is authored outside the run
        tree, which is the older exclusive-file shape. It is evaluated and
        reported, never counted: ROOT R94 section 2 keeps that shape as a
        diagnostic. The natively joined positive lives at run level
        (``test_orchestration_publish_decision.test_on_arm_publish_joins_after_the_full_read``).
        """
        advice = self.on_arm()
        self.read(advice)
        result = self.chain(advice)
        self.assertEqual(result["schema"], SCHEMA)
        self.assertEqual(result["status"], "DIAGNOSTIC_NOT_NATIVELY_JOINED")
        self.assertEqual(result["arm"], "advisory-on")
        self.assertEqual(result["read_proof"], READ_PROOF)
        self.assertIs(result["decision_write_natively_recognized"], False)
        self.assertEqual(result["decision_write_proof"], "NOT_RECOGNIZED_AT_THIS_HEAD")
        self.assertEqual(result["decision_write_proof"], DECISION_WRITE_PROOF)
        self.assertIs(result["counted_as_advisory_consumption"], False)
        self.assertIs(result["decision_publish_natively_joined"], False)
        self.assertIs(result["bind_natively_verified"], False)
        # The file's own stamp never lands in decided_at.
        self.assertIsNone(result["decided_at"])
        self.assertEqual(result["diagnostic_decided_at"], self.decided)
        self.assertEqual(result["order_basis"]["decided_at"],
                         "decision_file_self_written_consistency_only")
        self.assertEqual(result["order_basis"]["read_at"], "native_read_result_record")
        self.assertEqual(result["advice"], advice["advice_sha256"])
        self.assertTrue(result["order_verified"])
        self.assertIsNone(result["registration"])
        self.assertEqual(result["read"]["kind"], "BODY_READ_OBSERVED")
        self.assertEqual(sorted(result["predicates"]), sorted(DOCUMENTED_PREDICATES))
        # The proof is the envelope plus the body field, never the output sha.
        envelope = read_result(advice["notice"])
        self.assertEqual(envelope["reference"]["sha256"], advice["advice_sha256"])
        self.assertEqual(result["read"]["reference"], envelope["reference"])

    def test_partial_or_truncated_codex_read_is_not_full(self):
        # Each case derives its mutation from its *own* fixture's complete read.
        mutations = {
            "truncated": lambda complete: complete[:-12],
            "sha_only": lambda complete: json.dumps(dict(sha256="0" * 64)) + "\n",
            "notice_only": lambda complete: "STATUS: read it yourself\n",
            "short_body": lambda complete: json.dumps(
                dict(json.loads(complete), body=json.loads(complete)["body"][:-1])) + "\n",
            "duplicate_key": lambda complete: complete.replace('"schema":', '"schema":"bad","schema":', 1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.fresh()
                advice = self.on_arm()
                complete = json.dumps(read_result(advice["notice"]), ensure_ascii=False, sort_keys=True) + "\n"
                self.read(advice, output=mutate(complete))
                result = self.chain(advice)
                self.assertEqual(result["status"], "ADVICE_NOT_CONSUMED")
                self.assertIsNone(result["read"])
                self.assertIs(result["counted_as_advisory_consumption"], False)

    def test_failed_or_nonterminal_read_output_is_not_full(self):
        for label, kwargs in (("exit_1", dict(exit_code=1)),
                              ("yielded", dict(output=json.dumps(dict(session_id="42", output="pending"))))):
            with self.subTest(label=label):
                self.fresh()
                advice = self.on_arm()
                self.read(advice, **kwargs)
                self.assertEqual(self.chain(advice)["status"], "ADVICE_NOT_CONSUMED")

    def test_two_valid_pairs_are_ambiguous_not_consumed(self):
        advice = self.on_arm()
        self.read(advice, call_id="first")
        self.read(advice, call_id="second")
        with self.assertRaisesRegex(ContractError, "AMBIGUOUS_ADVICE_READ"):
            self.chain(advice)

    def test_binding_without_a_matching_decision_is_refused(self):
        cases = {"absent": (lambda ref: None, "does not pin a decision file"),
                 "null_path": (lambda ref: dict(ref, path=None), "does not pin a decision file"),
                 "changed_sha": (lambda ref: dict(ref, sha256="0" * 64), "current bytes"),
                 "changed_bytes": (lambda ref: dict(ref, bytes=ref["bytes"] + 1), "current bytes")}
        for label, (forge, reason) in cases.items():
            with self.subTest(label=label):
                self.fresh()
                advice = self.on_arm()
                self.read(advice)
                real = self.decision(advice)
                with self.assertRaisesRegex(ContractError, reason):
                    self.chain(advice, decision=real, binding=self.binding(advice, forge(real)))

    def test_a_decision_edited_after_binding_fails_the_pin(self):
        advice = self.on_arm()
        self.read(advice)
        real = self.decision(advice)
        binding = self.binding(advice, real)
        write_json(Path(real["path"]), dict(json.loads(Path(real["path"]).read_text()), reason="edited later"))
        with self.assertRaisesRegex(ContractError, "current bytes"):
            self.chain(advice, decision=real, binding=binding)

    def test_decision_citing_another_advice_is_refused(self):
        advice = self.on_arm()
        self.read(advice)
        other = self.f.publish_fixture_advice(self.co, "unbound", transport=suggesting_transport,
                                              budget=dict(max_model_requests=80),
                                              lanes=self.lane_facts(), state_text="a different question")
        self.assertNotEqual(other["advice_sha256"], advice["advice_sha256"])
        decision_ref = self.decision(advice, advice_sha256=other["advice_sha256"])
        with self.assertRaisesRegex(ContractError, "cites a different advice"):
            self.chain(advice, decision=decision_ref, binding=self.binding(advice, decision_ref))

    def test_route_outside_the_recomputed_candidates_is_refused(self):
        advice = self.on_arm()
        self.read(advice)
        decision_ref = self.decision(advice, actual_route="claude_b")
        with self.assertRaisesRegex(ContractError, "outside the candidates recomputed"):
            self.chain(advice, decision=decision_ref,
                       binding=self.binding(advice, decision_ref, lane="claude_b",
                                            eligible_lanes=["codex"]))

    def test_a_bound_lane_that_differs_from_the_decision_is_refused(self):
        advice = self.on_arm()
        self.read(advice)
        decision_ref = self.decision(advice, actual_route="claude_a")
        with self.assertRaisesRegex(ContractError, "differs from the decision's actual route"):
            self.chain(advice, decision=decision_ref, binding=self.binding(advice, decision_ref))

    def test_order_requires_read_then_decide_then_bind(self):
        advice = self.on_arm()
        read = self.read(advice)
        earlier = (parse_time(read["result_at"]) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        decision_ref = self.decision(advice, decided_at=earlier)
        with self.assertRaisesRegex(ContractError, "read/decide/bind/claim order"):
            self.chain(advice, decision=decision_ref, binding=self.binding(advice, decision_ref))

    def test_window_beyond_the_budget_is_pending_not_absent(self):
        for constant, value in (("WINDOW_RECORDS", 1), ("WINDOW_BYTES", 8)):
            with self.subTest(bound=constant):
                self.fresh()
                advice = self.on_arm()
                self.read(advice)
                with patch.object(upgrade_advice_chain, constant, value), \
                        self.assertRaisesRegex(ContractError, "ADVICE_READ_WINDOW_PENDING"):
                    self.chain(advice)

    def test_contradictory_codex_session_meta_invalidates_the_window(self):
        advice = self.on_arm()
        self.f.clients["codex"].append(dict(type="session_meta", timestamp=utc_now(),
                                            payload=dict(id="another-session")))
        self.read(advice)
        with self.assertRaisesRegex(ContractError, "CONTRADICTORY_NATIVE_SESSION"):
            self.chain(advice)

    def test_advice_session_consistency_agrees_with_the_attribution_predicate(self):
        readers = self.readers()
        cases = [dict(type="session_meta", payload=dict(id=readers["codex"].session["session_id"])),
                 dict(type="session_meta", payload=dict(session_id=readers["codex"].session["session_id"])),
                 dict(type="session_meta", payload=dict(id="other")),
                 dict(type="response_item", payload=dict(type="message")),
                 dict(type="user", sessionId=readers["claude_a"].session["session_id"]),
                 dict(type="user", sessionId="other"),
                 dict(type="user", sessionId=readers["claude_a"].session["session_id"], isSidechain=True)]
        for record in cases:
            for lane in ("codex", "claude_a"):
                reader = readers[lane]
                with self.subTest(record=record, lane=lane):
                    mine = advice_session_consistent(record, reader)
                    try:
                        theirs = _session_valid(record, reader)
                    except Exception:
                        theirs = False
                    self.assertEqual(mine, theirs)

    def off_arm(self, **changes):
        value = dict(arm="advisory-off", advice_sha256=None, worker_status=None, worker_journal=None,
                     fence=self.f.clients["codex"].reader.snapshot())
        value.update(changes)
        return value

    def test_off_arm_is_valid_and_consumes_nothing(self):
        advice = self.off_arm()
        decision_ref = self.decision(advice)
        result = self.chain(advice, decision=decision_ref, binding=self.binding(advice, decision_ref))
        self.assertEqual((result["arm"], result["status"]), ("advisory-off", "ADVICE_NOT_CONSUMED"))
        self.assertIs(result["counted_as_advisory_consumption"], False)
        self.assertIsNone(result["advice"])
        self.assertTrue(result["order_verified"])

    def test_off_arm_with_an_advice_file_requests_or_a_read_is_an_arm_violation(self):
        expected = {"advice_file": "off arm published advice",
                    "worker_requests": "off arm made worker requests",
                    "advice_read": "off arm read advice"}
        for label, reason in expected.items():
            with self.subTest(label=label):
                self.fresh()
                advice = self.off_arm()
                if label == "worker_requests":
                    advice["worker_journal"] = [dict(schema="jev-advice-worker-journal-v1", model_requests=1)]
                if label in ("advice_file", "advice_read"):
                    published = self.on_arm()
                    if label == "advice_read":
                        advice["fence"] = published["fence"]
                        self.read(published)
                        # Remove the file so the read, not the file, is the violation.
                        for path in sorted((self.co.root / "nodes/unbound/advice").iterdir()):
                            path.unlink()
                decision_ref = self.decision(advice)
                with self.assertRaisesRegex(ContractError, "ADVICE_ARM_VIOLATION: " + reason):
                    self.chain(advice, decision=decision_ref, binding=self.binding(advice, decision_ref))

    def test_fallback_or_stopped_auth_is_not_counted_as_consumption(self):
        for status, expected in (("NOT_CONFIGURED", "NOT_CONFIGURED"), ("JEV_ERROR", "JEV_FALLBACK"),
                                 ("STOPPED_AUTH", "JEV_FALLBACK"), ("NOT_CALLED", "JEV_FALLBACK")):
            with self.subTest(status=status):
                self.setUp()
                advice = self.on_arm()
                self.read(advice)
                result = self.chain(dict(advice, worker_status=status))
                self.assertEqual(result["status"], expected)
                self.assertIs(result["counted_as_advisory_consumption"], False)
                self.assertFalse(fallback_not_counted_as_consumed(status))

    def test_the_only_reachable_live_worker_answer_is_not_configured(self):
        advice = self.f.publish_fixture_advice(self.co, "unbound")
        self.assertEqual(advice["worker_status"], "NOT_CONFIGURED")
        self.assertEqual(advice["model_requests"], 0)
        self.read(advice)
        result = self.chain(advice)
        self.assertEqual(result["status"], "NOT_CONFIGURED")
        self.assertIs(result["counted_as_advisory_consumption"], False)
        self.assertIsNotNone(result["read"])

    def test_every_predicate_is_named_and_registered(self):
        self.assertEqual(set(ADVICE_PREDICATES), DOCUMENTED_PREDICATES)
        for name, value in ADVICE_PREDICATES.items():
            with self.subTest(name=name):
                self.assertIs(getattr(upgrade_advice_chain, name), value)
                self.assertEqual(value.__module__, "upgrade_advice_chain")
                self.assertTrue(callable(value))

    def test_shared_predicates_are_not_in_the_excision_registry(self):
        for name in ("valid_command", "calls_and_results", "codex_stdout", "exact_body_output"):
            self.assertNotIn(name, ADVICE_PREDICATES)

    def test_predicate_units_decide_exactly_one_thing_each(self):
        self.assertTrue(read_is_full([object()]))
        self.assertFalse(read_is_full([]))
        self.assertFalse(read_is_full([object(), object()]))
        self.assertTrue(decision_cites_this_advice(dict(advice_sha256="a" * 64), "a" * 64))
        self.assertFalse(decision_cites_this_advice(dict(advice_sha256="a" * 64), "b" * 64))
        self.assertFalse(decision_cites_this_advice(dict(advice_sha256=None), None))
        pin = dict(path="/p", bytes=3, sha256="c" * 64)
        self.assertTrue(binding_cites_decision(dict(decision=pin), dict(pin)))
        self.assertFalse(binding_cites_decision(dict(decision=None), dict(pin)))
        self.assertTrue(route_within_recomputed(dict(lane="codex", eligible_lanes=["codex"])))
        self.assertFalse(route_within_recomputed(dict(lane="codex", eligible_lanes=[])))
        self.assertTrue(ordered_chain("2026-10-29T00:00:00Z", None, "2026-10-29T00:00:02Z", None))
        self.assertFalse(ordered_chain("2026-10-29T00:00:02Z", None, "2026-10-29T00:00:01Z", None))
        self.assertTrue(off_arm_worker_requests_zero(None))
        self.assertTrue(off_arm_worker_requests_zero([dict(model_requests=0)]))
        self.assertFalse(off_arm_worker_requests_zero([dict(model_requests=1)]))
        self.assertTrue(off_arm_has_no_advice_read([]))
        self.assertFalse(off_arm_has_no_advice_read([dict(kind="ADVICE_READ_ATTEMPT")]))
        self.assertTrue(off_arm_has_no_advice_file(self.co.root / "nodes/unbound"))
        self.on_arm()
        self.assertFalse(off_arm_has_no_advice_file(self.co.root / "nodes/unbound"))


if __name__ == "__main__":
    unittest.main()
