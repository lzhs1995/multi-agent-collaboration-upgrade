"""v5 closeout consumer of the producer journal; synthetic clients are not live-model evidence."""
import copy
import json
import shlex
import tempfile
import unittest
import uuid
from unittest.mock import patch

from orchestration_campaign import (METRICS_V3_INPUT_PROVENANCE, METRICS_V3_OUTPUT_EFFECTS, campaign_effect_policy,
                                    validate_campaign)
from orchestration_common import ContractError, json_ref, utc_now, write_json
from orchestration_test_support import CampaignFixture
from orchestration_verify import finish_run, verify_run
from upgrade_input_provenance import REGISTRATION_SCHEMA, ProvenanceJournal, expected_payload, landing_refs, unit_payload

HELPER = "/fixture/mac/scripts/cmux_agent_adapter.py"
HOME = "/fixture/home"
EMPTY = dict(schema="upgrade-communication-attribution-v1", unresolved=[], inputs=[])
# lane -> registered producer of that lane's own native session
PRODUCERS = {"codex": "lane_agent", "claude_b": "operator_console"}


def ident(short):
    return "U2078-FIXTURE-" + short + "-0001"


class InputProvenanceMetricsTests(unittest.TestCase):
    def fixture(self, metrics=METRICS_V3_INPUT_PROVENANCE, *, register=True, only="local"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        f = CampaignFixture(temp.name, version=3)
        f.plan["nodes"] = [n for n in f.plan["nodes"] if n["id"] == only]
        f.campaign["plan"] = write_json(f.root / "plan.json", f.plan)
        f.campaign["metrics"] = copy.deepcopy(metrics)
        if register:
            run_id = f.campaign["runs"][0]["run_id"]
            producers = [dict(id=lane + "-producer", kind=kind,
                              session=dict(provider=f.clients[lane].provider,
                                           session_id=f.clients[lane].session["session_id"],
                                           transcript=f.clients[lane].session["transcript"]),
                              transport=dict(kind="cmux_helper", helper=HELPER, home=HOME,
                                             workspace_uuid=f.clients[lane].session["workspace_uuid"],
                                             caller_surface_uuid=f.clients[lane].session["surface_uuid"]))
                         for lane, kind in PRODUCERS.items()]
            f.registration = write_json(f.root / "producers.json",
                                        dict(schema=REGISTRATION_SCHEMA, run_id=run_id, producers=producers))
            f.campaign["input_provenance"] = dict(registrations={run_id: f.registration})
        f.save()
        return f

    def run_local(self, f):
        co = f.start()
        info = co.claim("local")
        f.work(info)
        f.accept(co, "local", f.candidate(co, "local"))
        return co

    def tool_call(self, client, command, call_id, at=None):
        if client.provider == "codex":
            return client.append(dict(type="response_item", timestamp=at or utc_now(), payload=dict(
                type="function_call", name="exec_command", call_id=call_id,
                arguments=json.dumps(dict(cmd=command, login=False)))))
        return client.append(dict(type="assistant", sessionId=client.session["session_id"], timestamp=at or utc_now(),
            message=dict(role="assistant", model="test-model", content=[dict(
                type="tool_use", id=call_id, name="Bash", input=dict(command=command))])))

    def send(self, f, co, short, *, sender="codex", receiver="claude_a", message="NOTICE", queued=False, call_at=None):
        """One real helper send: producer call, journaled intent behind a fence, landing, landed entry."""
        reader = f.clients[receiver].reader
        journal = ProvenanceJournal(co.root / "input-provenance.jsonl", co.spec["run_id"], f.registration)
        producer = journal.producers[sender + "-producer"]
        command = shlex.join(["rtk", "proxy", HELPER, "send", reader.session["surface_uuid"],
                              "--request-id", ident(short), message])
        call = self.tool_call(f.clients[sender], command, "call-" + uuid.uuid4().hex, at=call_at)
        fence = reader.snapshot()
        journal.intent(intent_id=ident(short), producer=dict(id=producer["id"], kind=producer["kind"]),
                       lane=receiver, reader=reader, fence=fence, mode="send", message=message)
        notice = expected_payload(producer, reader, mode="send", message=message, request_id=ident(short))
        if queued:
            self.queued(f, receiver, notice)
        else:
            f.clients[receiver].append(f.clients[receiver].message("user", notice, at=utc_now()))
        journal.landed(intent_id=ident(short), refs=landing_refs(reader, fence, reader.snapshot(), notice))
        return call

    def queued(self, f, lane, text):
        client, sid = f.clients[lane], f.clients[lane].session["session_id"]
        enqueue = client.append(dict(type="queue-operation", operation="enqueue", content=text,
                                     sessionId=sid, timestamp=utc_now()))
        landing = client.append(dict(type="attachment", sessionId=sid, timestamp=utc_now(),
                                     attachment=dict(type="queued_command", prompt=text)))
        return [enqueue, landing]

    def finish(self, co):
        with patch("upgrade_communication_attribution.attribute_inputs", return_value=EMPTY):
            return finish_run(co)

    def reverify(self, f, co):
        with patch("upgrade_communication_attribution.attribute_inputs", return_value=EMPTY):
            return verify_run(f.ref, co.spec["run_id"], fixtures=True)

    def test_registered_send_is_machine_and_an_unjournaled_neighbor_stays_unknown(self):
        # R46 at fixture level: one registered send through the production helper argv, its landing
        # ref and consumer attribution; the unjournaled neighbouring input keeps the count unknown.
        f = self.fixture()
        co = self.run_local(f)
        call = self.send(f, co, "n1")
        neighbour = f.clients["claude_a"].append(f.clients["claude_a"].message("user", "typed by someone", at=utc_now()))
        result = self.finish(co)
        provenance = result["metrics"]["input_provenance"]
        self.assertEqual(provenance["counts"], dict(HUMAN_REGISTERED=0, MACHINE=1, UNKNOWN=1))
        machine = [u for u in provenance["units"] if u["provenance"] == "MACHINE"]
        self.assertEqual((machine[0]["intent_id"], machine[0]["producer_call"]), (ident("n1"), call))
        self.assertEqual(result["metrics"]["unclassified_inputs"], [neighbour])
        self.assertIsNone(result["metrics"]["human_interventions"])
        self.assertIsNone(result["metrics"]["reasks"])
        self.assertEqual(result["metrics"]["automated_communication_inputs"], EMPTY)
        self.assertEqual(self.reverify(f, co)["metrics"], result["metrics"])

    def test_protocol_texts_and_status_queries_keep_their_v4_roles(self):
        # Real delegated protocol and real communication attribution: for inputs native_text can read,
        # v5 reproduces v4 exactly, so the exact notice and callback stay exempt and a status query
        # is reported separately.
        for metrics in (METRICS_V3_OUTPUT_EFFECTS, METRICS_V3_INPUT_PROVENANCE):
            with self.subTest(metrics_version=metrics["version"]):
                f, co = self.delegated(metrics)
                query = f.clients["codex"].append(f.clients["codex"].message("user", "进度", at=utc_now()))
                result = finish_run(co)
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(result["metrics"]["status_queries"], [query])
                self.assertEqual(result["metrics"]["unclassified_inputs"], [])
                self.assertEqual(result["metrics"]["human_interventions"], 0)
                if metrics["version"] == 5:
                    self.assertGreaterEqual(result["metrics"]["input_provenance"]["counts"]["UNKNOWN"], 3)
                    # One count per run: the journal-level one would read these exempt records as unknown.
                    self.assertNotIn("human_interventions", result["metrics"]["input_provenance"])

    def delegated(self, metrics):
        """One complete, real delegated protocol: handshake, task notice, work, callback, accept, disarm."""
        f = self.fixture(metrics, register=metrics["version"] == 5, only="review")
        co = f.start()
        info = co.claim("review")
        f.prepare(co, "review")
        f.intent(co, "review")
        f.dispatch(co, "review")
        f.work(info, "claude_a")
        protocol = f.callback(co, "review")
        f.accept(co, "review", f.candidate(co, "review", protocol))
        f.disarm(co, "review")
        return f, co

    def delivered(self, client, prefix):
        """The exact text of the one input this lane received that starts with prefix."""
        texts = [found[1] for _, record in client.reader.records(0, client.reader.snapshot()["offset"])
                 for found in [unit_payload(record, client.provider)] if found and found[1].startswith(prefix)]
        self.assertEqual(len(texts), 1)
        return texts[0]

    def test_duplicate_protocol_body_as_a_user_record_is_refused_by_the_delivery_gate(self):
        # ROOT R51's outer gate: verify_delivery requires one native delivery per original payload,
        # so a duplicate user record cannot close the run at all, in v4 or v5.
        for metrics in (METRICS_V3_OUTPUT_EFFECTS, METRICS_V3_INPUT_PROVENANCE):
            for lane, prefix in (("claude_a", "TASK_PACK_V2 "), ("codex", "DONE|")):
                with self.subTest(metrics_version=metrics["version"], body=prefix.strip()):
                    f, co = self.delegated(metrics)
                    text = self.delivered(f.clients[lane], prefix)
                    f.clients[lane].append(f.clients[lane].message("user", text, at=utc_now()))
                    with self.assertRaisesRegex(ContractError, "duplicate native delivery"):
                        finish_run(co)

    def test_queued_duplicate_of_a_verified_protocol_body_stays_unknown_only_in_v5(self):
        # The delivery gate reads user records, so a busy lane's queued duplicate passes it. v4
        # exempted it by body text and measured zero interventions; v5 exempts only the exact
        # verified records, so the duplicate stays unknown (ROOT R51-F1). v4 stays unchanged.
        for metrics, unknown in ((METRICS_V3_OUTPUT_EFFECTS, False), (METRICS_V3_INPUT_PROVENANCE, True)):
            with self.subTest(metrics_version=metrics["version"]):
                f, co = self.delegated(metrics)
                refs = self.queued(f, "claude_a", self.delivered(f.clients["claude_a"], "TASK_PACK_V2 "))
                result = finish_run(co)
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(result["metrics"]["unclassified_inputs"], refs if unknown else [])
                self.assertEqual(result["metrics"]["human_interventions"], None if unknown else 0)
                self.assertEqual(verify_run(f.ref, co.spec["run_id"], fixtures=True)["metrics"], result["metrics"])

    def test_fully_attributed_run_measures_registered_operator_inputs(self):
        f = self.fixture()
        co = self.run_local(f)
        self.send(f, co, "n1")
        # A queued operator send to a busy lane is two units (enqueue + landing) and one intervention.
        self.send(f, co, "h1", sender="claude_b", receiver="claude_a", message="operator notice", queued=True)
        result = self.finish(co)
        self.assertEqual(result["metrics"]["input_provenance"]["counts"], dict(HUMAN_REGISTERED=2, MACHINE=1, UNKNOWN=0))
        self.assertEqual(result["metrics"]["unclassified_inputs"], [])
        self.assertEqual(result["metrics"]["human_interventions"], 1)
        self.assertEqual(result["metrics"]["reasks"], 0)

    def test_queued_input_native_text_cannot_read_is_unknown_only_under_v5(self):
        # The frozen v4 count skips what native_text cannot read; v5 keeps both queued units unknown.
        for metrics, unknown, humans in ((METRICS_V3_OUTPUT_EFFECTS, 0, 0), (METRICS_V3_INPUT_PROVENANCE, 2, None)):
            with self.subTest(metrics_version=metrics["version"]):
                f = self.fixture(metrics, register=metrics["version"] == 5)
                co = self.run_local(f)
                refs = self.queued(f, "claude_a", "busy lane input")
                result = self.finish(co)
                self.assertEqual(result["metrics"]["unclassified_inputs"], refs[:unknown])
                self.assertEqual(result["metrics"]["human_interventions"], humans)

    def test_consumer_bounds_the_join_by_run_start_and_closeout_clock(self):
        # The pure join tests its window; this pins the window the consumer passes in: a producer
        # call stamped before the run started, or an intent after the closeout clock, attributes nothing.
        def early_call(f, co):
            self.send(f, co, "n1", call_at="2026-01-01T00:00:00Z")
            return self.finish(co)

        def after_closeout(f, co):
            boundary = utc_now()
            self.send(f, co, "n1")
            with patch("orchestration_verify.utc_now", return_value=boundary):
                return self.finish(co)

        for case, reason in ((early_call, "SENDING_INVOCATION_OUTSIDE_INTENT_WINDOW"),
                             (after_closeout, "INTENT_OUTSIDE_RUN")):
            with self.subTest(case=case.__name__):
                f = self.fixture()
                co = self.run_local(f)
                result = case(f, co)
                provenance = result["metrics"]["input_provenance"]
                self.assertEqual(provenance["counts"], dict(HUMAN_REGISTERED=0, MACHINE=0, UNKNOWN=1))
                self.assertEqual([u["reason"] for u in provenance["unresolved"]], [reason])
                self.assertIsNone(result["metrics"]["human_interventions"])
                self.assertEqual(self.reverify(f, co)["metrics"], result["metrics"])

    def test_journal_changed_after_closeout_fails_reverification(self):
        f = self.fixture()
        co = self.run_local(f)
        self.send(f, co, "n1")
        self.finish(co)
        path = co.root / "input-provenance.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ContractError, "stored metrics differ"):
            self.reverify(f, co)

    def test_missing_journal_keeps_every_unit_unknown(self):
        f = self.fixture()
        co = self.run_local(f)
        ref = f.clients["claude_a"].append(f.clients["claude_a"].message("user", "NOTICE", at=utc_now()))
        result = self.finish(co)
        provenance = result["metrics"]["input_provenance"]
        self.assertEqual((provenance["journal"], provenance["counts"]["UNKNOWN"]), (None, 1))
        self.assertIn("no provenance journal", provenance["unresolved"][0]["reason"])
        self.assertEqual(result["metrics"]["unclassified_inputs"], [ref])
        self.assertIsNone(result["metrics"]["human_interventions"])

    def test_v5_requires_one_frozen_registration_per_run_and_only_v5_may_carry_one(self):
        f = self.fixture()
        self.assertEqual(validate_campaign(f.ref, fixtures=True)["metrics"], METRICS_V3_INPUT_PROVENANCE)
        self.assertIsNotNone(campaign_effect_policy(f.campaign))
        frozen = copy.deepcopy(f.campaign["input_provenance"])
        one = "one frozen producer registration per run"
        cases = (("missing", lambda c: c.pop("input_provenance"), one),
                 ("other_run", lambda c: c.update(input_provenance=dict(registrations={"run-99": f.registration})), one),
                 ("extra_key", lambda c: c["input_provenance"].update(journal="x"), one),
                 ("changed_file", lambda c: c["input_provenance"]["registrations"].update(
                     {k: dict(v, sha256="0" * 64) for k, v in frozen["registrations"].items()}), "mismatch"),
                 ("foreign_registration", lambda c: c["input_provenance"]["registrations"].update(
                     {k: write_json(f.root / "foreign.json", dict(json_ref(v), run_id="run-99"))
                      for k, v in frozen["registrations"].items()}), "foreign producer registration"))
        for name, change, reason in cases:
            with self.subTest(case=name):
                f.campaign["input_provenance"] = copy.deepcopy(frozen)
                change(f.campaign)
                f.save()
                with self.assertRaisesRegex(ContractError, reason):
                    validate_campaign(f.ref, fixtures=True)
        f.campaign["input_provenance"] = copy.deepcopy(frozen)
        f.campaign["metrics"] = copy.deepcopy(METRICS_V3_OUTPUT_EFFECTS)
        f.save()
        with self.assertRaisesRegex(ContractError, "its own metric definition"):
            validate_campaign(f.ref, fixtures=True)

    def test_v5_is_v4_plus_only_its_input_fields(self):
        changed = {k for k in METRICS_V3_INPUT_PROVENANCE
                   if METRICS_V3_OUTPUT_EFFECTS.get(k) != METRICS_V3_INPUT_PROVENANCE[k]}
        self.assertEqual(changed, {"version", "interventions", "input_provenance"})
        self.assertEqual(set(METRICS_V3_OUTPUT_EFFECTS) - set(METRICS_V3_INPUT_PROVENANCE), set())


if __name__ == "__main__":
    unittest.main()
