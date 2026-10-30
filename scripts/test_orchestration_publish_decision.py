"""The fixed decision publisher and the native join of its own call and result.

The decision file is created by one closure-pinned entrypoint inside the run
tree, not authored by the supervisor outside it, and ``decided_at`` comes from
the native timestamp of the publisher's own success record. The join reconstructs
the expected script from the decision file's own fields, so a forged call, a
failed or truncated output, a duplicate call or result, a publish before the
read, or a decision edited before binding all refuse.
"""
import contextlib
import io
import json
import shlex
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import upgrade_advice_chain
from orchestration_binding import DECISION_FIELDS, DECISION_SCHEMA, SCHEMA as BINDING_SCHEMA, write_binding
from orchestration_campaign import DECISION_ARGUMENTS, NO_ADVICE, control_script, decision_argument_fields
from orchestration_common import (ContractError, file_ref, parse_time, read_json, tree_manifest, utc_now,
                                  write_json)
from orchestration_native import NativeReader
from orchestration_run import Runner, main
from orchestration_test_support import CampaignFixture
from upgrade_advice_chain import (PUBLICATION_FIELDS, advice_chain, decision_publish_evidence,
                                  publication_pins_the_decision, publish_call_is_the_frozen_script,
                                  publish_follows_the_read, published_decision_path)
from upgrade_body_read_evidence import RTK_NOTICE


def suggesting_transport(request):
    return dict(answers=dict(work_kind="coding", work_kind_applies=0.9), http_status=200)


class PublishDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, strategies=("omo",), version=3, arm="B", unbound=True)
        # The publisher is the frozen adapter entrypoint, so the fixture adapter
        # has to contain it exactly as a real frozen campaign would.
        adapter = Path(self.f.campaign["adapter"]["path"])
        shutil.copyfile(Path(__file__).resolve().with_name("orchestration_run.py"),
                        adapter / "scripts/orchestration_run.py")
        self.f.campaign["adapter"] = tree_manifest(adapter)
        self.f.save()
        self.co = self.f.start()
        self.run = self.co.spec["run_id"]
        self.r = Runner(self.f.ref, fixtures=True)
        self.reads, self.issued = [], None

    def readers(self):
        return {lane: NativeReader(session, fixtures=True)
                for lane, session in self.f.campaign["sessions"].items()}

    def lane_facts(self):
        return {lane: dict(kind="codex" if lane == "codex" else "claude", verified=True,
                           idle_write_domain=True, author=False)
                for lane in self.f.campaign["sessions"]}

    def fields(self, **changes):
        value = dict(work_kind="coding", actual_route="codex", action="supervisor_review",
                     reason="the supervisor chose the idle codex lane", advice_sha256=NO_ADVICE)
        value.update(changes)
        return value

    def publish_advice(self):
        advice = self.f.publish_fixture_advice(self.co, "unbound", transport=suggesting_transport,
                                               budget=dict(max_model_requests=80), lanes=self.lane_facts())
        return advice

    def read(self, advice, **kwargs):
        observed = self.f.fake_advice_read_records(advice, **kwargs)
        self.reads.append(observed)
        return observed

    def next_stamp(self, *extra):
        """Strictly after every stamp seen or issued so far, and after now.

        Remembering the last issued stamp keeps two consecutive calls from
        colliding when a read's result is already stamped in the future.
        """
        moments = [parse_time(value) for value in extra if value]
        moments += [parse_time(r["result_at"]) for r in self.reads] + [parse_time(utc_now())]
        if self.issued is not None:
            moments.append(parse_time(self.issued))
        self.issued = (max(moments) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        return self.issued

    def publish(self, fields=None, *, node_id="unbound", script=None, call_at=None, append_result=True,
                envelope=None, exit_code=0, raw_envelope=None, call_id="publish-decision",
                banner=False, extra_calls=0, extra_results=0, name="exec", between=None):
        """Append the supervisor's call, run the publisher, append its result."""
        fields = self.fields() if fields is None else fields
        client = self.f.clients["codex"]
        exact = script if script is not None else control_script(
            self.f.campaign, self.f.ref, "publish-decision", self.run, node_id=node_id,
            decision_fields=fields)
        when = call_at or self.next_stamp()
        calls = [client.append(dict(type="response_item", timestamp=when, payload=dict(
            type="custom_tool_call", name=name, call_id=call_id if index == 0 else call_id + f"-{index}",
            input=exact))) for index in range(1 + extra_calls)]
        result = self.r.publish_decision(self.run, node_id, fields)
        if between is not None:
            between()
        if callable(envelope):
            envelope = envelope(result)
        printed = json.dumps(result if envelope is None else envelope,
                             ensure_ascii=False, sort_keys=True, allow_nan=False)
        if banner:
            printed = RTK_NOTICE + printed
        body = raw_envelope if raw_envelope is not None else json.dumps(
            dict(exit_code=exit_code, output=printed + "\n", wall_time_seconds=0.1))
        records = []
        later = (parse_time(when) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        if append_result:
            for _ in range(1 + extra_results):
                records.append(client.append(dict(type="response_item", timestamp=later, payload=dict(
                    type="custom_tool_call_output", call_id=call_id, output=body))))
            self.reads.append(dict(result_at=later))
        return dict(result=result, script=exact, call=calls[0], calls=calls, results=records,
                    call_at=when, result_at=later)

    def binding(self, advice, decision_ref, *, node_id="unbound", lane="codex", **changes):
        value = dict(schema=BINDING_SCHEMA, campaign=self.f.ref, run_id=self.run, node_id=node_id,
                     lane=lane, execution_kind="local_cli", transport="codex_cli", mode="local_cli",
                     arm=self.f.campaign.get("arm"), decision=decision_ref, advice=advice,
                     eligible_lanes=["codex", "claude_a", "claude_b"], lane_facts=self.lane_facts(),
                     bound_at=self.next_stamp(), bound_native=None,
                     input_units=self.f.campaign["inputs"],
                     supervisor_session=self.f.campaign["sessions"]["codex"]["session_id"])
        value.update(changes)
        return value

    def off_arm(self):
        return dict(arm="advisory-off", advice_sha256=None, worker_status=None, worker_journal=None,
                    fence=self.co.header["native_start"]["codex"])

    def on_arm(self, advice):
        return {key: advice[key] for key in ("arm", "advice_sha256", "notice", "command", "fence",
                                             "not_before_epoch", "worker_status", "worker_journal")}

    def chain(self, advice, published, *, node_id="unbound", binding=None, decision_ref=None, bind=True, **kw):
        """Bind the published decision the way bind-unbound does, then join.

        ``bind`` appends the original supervisor's bind control call and pins it
        in the binding, because a published decision's window ends at that call
        and its ``bound_at`` must be that record's own native timestamp. A case
        that deliberately has no bind call passes ``bind=False``.
        """
        if decision_ref is None:
            decision_ref = {name: published["result"]["decision"][name] for name in ("path", "bytes", "sha256")}
        pinned = self.bind_call(node_id) if bind else None
        changes = dict(bound_native=pinned["ref"], bound_at=pinned["at"]) if bind else {}
        value = binding if binding is not None else self.binding(advice, decision_ref, node_id=node_id, **changes)
        ref = write_binding(self.co, node_id, value)
        later = parse_time(utc_now()).timestamp() + 600
        return advice_chain(self.co, node_id, {}, self.readers(), registration=None,
                            not_after=later, binding_ref=ref, **kw)

    # --- positives -------------------------------------------------------

    def test_on_arm_publish_joins_after_the_full_read(self):
        advice = self.publish_advice()
        self.read(advice)
        published = self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
        result = self.chain(self.on_arm(advice), published)
        self.assertEqual((result["arm"], result["status"]), ("advisory-on", "CONSUMED"))
        self.assertIs(result["decision_publish_natively_joined"], True)
        self.assertIs(result["bind_natively_verified"], True)
        # This is the one natively joined complete chain: the only shape that
        # may be counted as proven advisory consumption.
        self.assertIs(result["counted_as_advisory_consumption"], True)
        self.assertIs(result["decision_write_natively_recognized"], False)
        self.assertEqual(result["decision_write_proof"], "NOT_RECOGNIZED_AT_THIS_HEAD")
        publish = result["decision_publish"]
        self.assertEqual(publish["kind"], "DECISION_PUBLISH_OBSERVED")
        # decided_at is the publisher's own native success record time.
        self.assertEqual(result["decided_at"], published["result_at"])
        self.assertEqual(result["decided_at"], publish["timestamp"])
        self.assertIsNone(result["diagnostic_decided_at"])
        self.assertIsNone(read_json(published["result"]["decision"]["path"])["decided_at"])
        # bound_at is the bind control record's own native timestamp.
        self.assertEqual(result["bound_at"], result["bind_call"]["timestamp"])
        self.assertEqual(result["order_basis"], dict(read_at="native_read_result_record",
                                                     decided_at="native_publish_result_record",
                                                     bound_at="native_bind_control_record",
                                                     claim_started_at=None, claim_after_bind=None))
        self.assertTrue(result["order_verified"])
        self.assertEqual(publish["envelope"]["inputs"]["advice"]["sha256"], advice["advice_sha256"])

    def test_off_arm_publish_joins_with_no_advice_read_or_request(self):
        published = self.publish()
        result = self.chain(self.off_arm(), published)
        self.assertEqual((result["arm"], result["status"]), ("advisory-off", "ADVICE_NOT_CONSUMED"))
        self.assertIs(result["decision_publish_natively_joined"], True)
        self.assertIsNone(result["advice"])
        self.assertIsNone(result["read"])
        self.assertIsNone(published["result"]["inputs"]["advice"])
        self.assertEqual(result["decided_at"], published["result_at"])
        self.assertFalse((self.co.root / "nodes/unbound/advice").exists())

    def test_the_measured_rtk_banner_before_the_envelope_still_joins(self):
        published = self.publish(banner=True)
        self.assertIs(self.chain(self.off_arm(), published)["decision_publish_natively_joined"], True)

    def test_published_decision_lives_in_the_run_tree_with_the_exact_fields(self):
        published = self.publish()
        path = Path(published["result"]["decision"]["path"])
        self.assertEqual(path, published_decision_path(self.co, "unbound"))
        self.assertEqual(path, self.co.root / "decisions" / "unbound.json")
        value = read_json(path)
        self.assertEqual(set(value), DECISION_FIELDS)
        self.assertEqual(value["schema"], DECISION_SCHEMA)
        self.assertEqual((value["run_id"], value["node_id"]), (self.run, "unbound"))
        self.assertIsNone(value["advice_sha256"])
        self.assertIsNone(value["decided_at"])
        self.assertEqual(value["supervisor_session"], self.f.campaign["sessions"]["codex"]["session_id"])
        self.assertEqual(decision_argument_fields(value), self.fields())
        self.assertEqual(set(published["result"]), PUBLICATION_FIELDS)
        publisher = published["result"]["publisher"]
        self.assertEqual(publisher["sha256"],
                         self.f.campaign["adapter"]["files"]["scripts/orchestration_run.py"]["sha256"])

    def test_the_cli_action_prints_one_envelope_from_the_frozen_entrypoint(self):
        fields = self.fields()
        argv = ["publish-decision", "--run", self.run, "--node", "unbound"]
        for name in DECISION_ARGUMENTS:
            argv += ["--" + name.replace("_", "-"), fields[name]]
        out, err = io.StringIO(), io.StringIO()
        frozen = str(Path(self.f.campaign["adapter"]["path"]) / "scripts/orchestration_run.py")
        with patch("orchestration_run.Runner", return_value=self.r), \
                patch("orchestration_run.__file__", frozen), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--campaign", self.f.ref["path"], "--campaign-sha256", self.f.ref["sha256"], *argv])
        self.assertEqual((code, err.getvalue()), (0, ""))
        printed = out.getvalue()
        self.assertEqual(printed.count("\n"), 1)
        self.assertEqual(set(json.loads(printed)), PUBLICATION_FIELDS)
        # The generated script runs exactly this argv, in this order.
        script = control_script(self.f.campaign, self.f.ref, "publish-decision", self.run,
                                node_id="unbound", decision_fields=fields)
        inner = script[len("text(await tools.exec_command("):-len("));\n")]
        self.assertEqual(shlex.split(json.loads(inner)["cmd"])[-len(argv):], argv)

    def test_the_cli_requires_every_decision_field(self):
        for missing in DECISION_ARGUMENTS:
            argv = ["publish-decision", "--run", self.run, "--node", "unbound"]
            for name in DECISION_ARGUMENTS:
                if name != missing:
                    argv += ["--" + name.replace("_", "-"), self.fields()[name]]
            with self.subTest(missing=missing), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                main(["--campaign", self.f.ref["path"], "--campaign-sha256", self.f.ref["sha256"], *argv])
        self.assertFalse(published_decision_path(self.co, "unbound").exists())

    # --- negatives: the native pair ---------------------------------------

    def test_a_missing_publish_call_is_refused(self):
        # The decision exists, published by the entrypoint, but no supervisor
        # call for it is in the transcript: the file alone proves nothing.
        fields = self.fields()
        result = self.r.publish_decision(self.run, "unbound", fields)
        published = dict(result=result, result_at=None)
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_CALL"):
            self.chain(self.off_arm(), published)

    def test_a_missing_output_record_is_refused(self):
        published = self.publish(append_result=False)
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_RESULT"):
            self.chain(self.off_arm(), published)

    def test_a_failed_truncated_or_nonterminal_output_is_refused(self):
        cases = {
            "exit_1": dict(exit_code=1),
            "truncated": dict(raw_envelope=json.dumps(dict(exit_code=0, wall_time_seconds=0.1,
                                                           output='{"schema":"orchestration-dec'))),
            "yielded": dict(raw_envelope=json.dumps(dict(session_id="42", output="running"))),
            "error_text": dict(raw_envelope=json.dumps(dict(exit_code=0, wall_time_seconds=0.1,
                                                            output="Error: publish failed\n"))),
            "no_envelope": dict(raw_envelope=json.dumps(dict(exit_code=0, wall_time_seconds=0.1,
                                                             output="published\n"))),
        }
        for label, kwargs in cases.items():
            with self.subTest(label=label):
                self.fresh()
                published = self.publish(**kwargs)
                with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_RESULT"):
                    self.chain(self.off_arm(), published)

    def test_a_call_that_is_not_the_frozen_script_is_refused(self):
        exact = control_script(self.f.campaign, self.f.ref, "publish-decision", self.run,
                               node_id="unbound", decision_fields=self.fields())
        forgeries = {
            "other_action": control_script(self.f.campaign, self.f.ref, "claim", self.run, node_id="unbound"),
            "reordered_fields": exact.replace('--work-kind coding --actual-route codex',
                                              '--actual-route codex --work-kind coding'),
            "other_reason": exact.replace("the supervisor chose the idle codex lane", "a different reason"),
            "other_node": exact.replace("--node unbound", "--node local"),
            "extra_space": " " + exact,
            "no_terminal_lf": exact.removesuffix("\n"),
        }
        for label, script in forgeries.items():
            with self.subTest(label=label):
                self.fresh()
                self.assertNotEqual(script, exact)
                published = self.publish(script=script)
                with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_CALL"):
                    self.chain(self.off_arm(), published)

    def test_a_duplicate_call_or_result_is_refused(self):
        for label, kwargs, reason in (("duplicate_call", dict(extra_calls=1), "DECISION_PUBLISH_CALL"),
                                      ("duplicate_result", dict(extra_results=1), "DECISION_PUBLISH_RESULT")):
            with self.subTest(label=label):
                self.fresh()
                published = self.publish(**kwargs)
                with self.assertRaisesRegex(ContractError, reason):
                    self.chain(self.off_arm(), published)

    def test_a_second_call_record_sharing_the_publisher_call_id_is_refused(self):
        # One exact publisher call and one rc0 output, plus a second call record
        # with another input under the same call_id: the output could belong to
        # either call, so it is credited to neither.
        client = self.f.clients["codex"]
        args = json.dumps(dict(cmd="cat envelope.json", login=False))

        def foreign_call():
            client.append(dict(type="response_item", timestamp=self.next_stamp(), payload=dict(
                type="custom_tool_call", name="exec", call_id="publish-decision",
                input="text(await tools.exec_command(" + args + "));\n")))
        published = self.publish(between=foreign_call)
        with self.assertRaisesRegex(ContractError, "shares the publisher call's call_id"):
            self.chain(self.off_arm(), published)

    def test_a_late_repeated_publish_after_the_bind_does_not_change_a_frozen_verdict(self):
        # The window ends at the bind call (design Q25), so what the transcript
        # gains afterwards cannot change the verdict of a binding that passed.
        published = self.publish()
        bind = self.bind_call()
        pin = {k: published["result"]["decision"][k] for k in ("path", "bytes", "sha256")}
        ref = write_binding(self.co, "unbound", self.binding(self.off_arm(), pin, bound_native=bind["ref"],
                                                             bound_at=bind["at"]))
        later = parse_time(utc_now()).timestamp() + 600

        def chain():
            return advice_chain(self.co, "unbound", {}, self.readers(), registration=None,
                                not_after=later, binding_ref=ref)
        first = chain()
        self.assertIs(first["decision_publish_natively_joined"], True)
        client = self.f.clients["codex"]
        # The supervisor repeats the exact publish command; the publisher
        # refuses it, but the call and its failed result stay in the transcript.
        client.append(dict(type="response_item", timestamp=self.next_stamp(), payload=dict(
            type="custom_tool_call", name="exec", call_id="publish-again", input=published["script"])))
        with self.assertRaisesRegex(ContractError, "original published decision exists"):
            self.r.publish_decision(self.run, "unbound", self.fields())
        client.append(dict(type="response_item", timestamp=self.next_stamp(), payload=dict(
            type="custom_tool_call_output", call_id="publish-again",
            output=json.dumps(dict(exit_code=1, output="refused\n", wall_time_seconds=0.1)))))
        # A late second output for the original call_id is outside the window too.
        client.append(dict(type="response_item", timestamp=self.next_stamp(), payload=dict(
            type="custom_tool_call_output", call_id="publish-decision",
            output=json.dumps(dict(exit_code=1, output="late\n", wall_time_seconds=0.1)))))
        self.assertEqual(chain(), first)
        # The same repeated call *inside* the window, before any bind call,
        # still refuses: the bound is the bind call, not a weaker rule.
        self.fresh()
        published = self.publish()
        self.f.clients["codex"].append(dict(type="response_item", timestamp=self.next_stamp(), payload=dict(
            type="custom_tool_call", name="exec", call_id="publish-again", input=published["script"])))
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_CALL"):
            self.chain(self.off_arm(), published)

    def bind_call(self, node_id="unbound"):
        """Append the original supervisor's bind control call for the published decision."""
        script = control_script(self.f.campaign, self.f.ref, "bind-unbound", self.run, node_id=node_id,
                                decision=str(published_decision_path(self.co, node_id)))
        when = self.next_stamp()
        ref = self.f.clients["codex"].append(dict(type="response_item", timestamp=when, payload=dict(
            type="custom_tool_call", name="exec", call_id="bind-" + node_id, input=script)))
        self.reads.append(dict(result_at=when))
        return dict(ref=ref, at=when, script=script)

    def test_publishing_before_the_read_is_refused(self):
        advice = self.publish_advice()
        fields = self.fields(advice_sha256=advice["advice_sha256"])
        # The publish call is stamped well after the read, so every timestamp
        # is in order and only the transcript order is wrong: the call record
        # precedes the read. Only the record offsets can catch this.
        late = (parse_time(utc_now()) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        published = self.publish(fields, call_at=late, between=lambda: self.read(advice))
        read_result_at = parse_time(self.reads[0]["result_at"])
        self.assertLess(read_result_at, parse_time(late))
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_ORDER"):
            self.chain(self.on_arm(advice), published)

    def test_a_publish_stamped_before_the_read_is_refused(self):
        advice = self.publish_advice()
        read = self.read(advice)
        # Record order is right; the copied/rounded timestamp is not.
        early = (parse_time(read["result_at"]) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        published = self.publish(self.fields(advice_sha256=advice["advice_sha256"]), call_at=early)
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_ORDER"):
            self.chain(self.on_arm(advice), published)

    # --- negatives: the envelope ------------------------------------------

    def test_an_envelope_that_pins_another_decision_is_refused(self):
        advice = self.publish_advice()
        forgeries = {
            "wrong_sha": lambda r: dict(r, decision=dict(r["decision"], sha256="0" * 64)),
            "wrong_bytes": lambda r: dict(r, decision=dict(r["decision"], bytes=r["decision"]["bytes"] + 1)),
            "wrong_path": lambda r: dict(r, decision=dict(r["decision"], path=r["decision"]["path"] + ".bak")),
            "wrong_session": lambda r: dict(r, supervisor_session="another-session"),
            "wrong_run": lambda r: dict(r, run_id="run-99"),
            "wrong_node": lambda r: dict(r, node_id="local"),
            "wrong_publisher": lambda r: dict(r, publisher=dict(r["publisher"], sha256="0" * 64)),
            "wrong_campaign": lambda r: dict(r, inputs=dict(r["inputs"], campaign=dict(
                r["inputs"]["campaign"], sha256="0" * 64))),
            "missing_field": lambda r: {k: v for k, v in r.items() if k != "inputs"},
            "extra_field": lambda r: dict(r, extra="added"),
            "other_schema": lambda r: dict(r, schema="orchestration-decision-publication-v2"),
        }
        for label, forge in forgeries.items():
            with self.subTest(label=label):
                self.fresh()
                published = self.publish(envelope=forge)
                with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_ENVELOPE"):
                    self.chain(self.off_arm(), published)

    def test_an_envelope_citing_the_wrong_advice_is_refused(self):
        advice = self.publish_advice()
        self.read(advice)
        published = self.publish(self.fields(advice_sha256=advice["advice_sha256"]),
                                 envelope=lambda r: dict(r, inputs=dict(r["inputs"], advice=None)))
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_ENVELOPE"):
            self.chain(self.on_arm(advice), published)

    def test_a_decision_changed_before_binding_is_refused(self):
        published = self.publish()
        path = Path(published["result"]["decision"]["path"])
        value = read_json(path)
        path.unlink()
        changed = write_json(path, dict(value, decided_at=utc_now()), exclusive=True)
        self.assertNotEqual(changed["sha256"], published["result"]["decision"]["sha256"])
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_ENVELOPE"):
            self.chain(self.off_arm(), published,
                       decision_ref={k: changed[k] for k in ("path", "bytes", "sha256")})

    def test_a_decision_outside_the_publisher_path_is_not_joined(self):
        # The older diagnostic shape: honest about having no join, and
        # deliberately not counted as proven consumption (ROOT R94 section 2).
        advice = self.publish_advice()
        self.read(advice)
        outside = self.f.write_decision(self.run, "unbound", work_kind="coding", actual_route="codex",
                                        advice_sha256=advice["advice_sha256"],
                                        decided_at=self.next_stamp(),
                                        path=self.f.root / "decisions" / "outside.json")
        result = self.chain(self.on_arm(advice), dict(result=dict(decision=outside), result_at=None),
                            decision_ref={k: outside[k] for k in ("path", "bytes", "sha256")}, bind=False)
        self.assertIs(result["decision_publish_natively_joined"], False)
        self.assertIsNone(result["decision_publish"])
        self.assertIs(result["bind_natively_verified"], False)
        self.assertEqual(result["status"], "DIAGNOSTIC_NOT_NATIVELY_JOINED")
        self.assertIs(result["counted_as_advisory_consumption"], False)
        # The file's self-written stamp stays under its own diagnostic key.
        self.assertIsNone(result["decided_at"])
        self.assertEqual(result["diagnostic_decided_at"], read_json(outside["path"])["decided_at"])

    def test_a_published_decision_must_pin_its_own_bind_control_call(self):
        """A published chain is joined only through the bind step's own call.

        Each case keeps a real publisher call and result, so only the bind pin
        decides. R62 `native_joins.rule` forbids a self-written stamp as proof,
        and there is no EOF fallback on the published path.
        """
        cases = ("absent", "another_record", "forged_stamp")
        for label in cases:
            with self.subTest(label=label):
                self.fresh()
                published = self.publish()
                pin = {k: published["result"]["decision"][k] for k in ("path", "bytes", "sha256")}
                if label == "absent":
                    changes = dict(bound_native=None,
                                   bound_at="2099-01-01T00:00:00+00:00")
                elif label == "another_record":
                    record = self.f.clients["codex"].append(dict(
                        type="response_item", timestamp=self.next_stamp(),
                        payload=dict(type="message", role="assistant",
                                     content=[dict(type="output_text", text="binding it now")])))
                    changes = dict(bound_native=record, bound_at=self.issued)
                else:
                    bind = self.bind_call()
                    changes = dict(bound_native=bind["ref"], bound_at=self.next_stamp())
                with self.assertRaisesRegex(ContractError, "BIND_CALL"):
                    self.chain(self.off_arm(), published, bind=False,
                               binding=self.binding(self.off_arm(), pin, **changes))

    # --- negatives: the publisher refuses ---------------------------------

    def test_the_publisher_refuses_a_bound_node_and_a_second_publication(self):
        with self.assertRaisesRegex(ContractError, "deliberately unbound"):
            self.r.publish_decision(self.run, "local", self.fields())
        self.assertFalse((self.co.root / "decisions").exists())
        self.publish()
        with self.assertRaisesRegex(ContractError, "original published decision exists"):
            self.r.publish_decision(self.run, "unbound", self.fields())

    def test_the_publisher_refuses_an_unknown_node_and_a_wrong_field_set(self):
        with self.assertRaisesRegex(ContractError, "unknown frozen node"):
            self.r.publish_decision(self.run, "absent", self.fields())
        for change in ({"extra": "x"}, None):
            fields = self.fields()
            if change:
                fields.update(change)
            else:
                del fields["reason"]
            with self.subTest(change=change), self.assertRaisesRegex(ContractError, "frozen field set"):
                self.r.publish_decision(self.run, "unbound", fields)
        self.assertFalse(published_decision_path(self.co, "unbound").exists())

    def test_the_publisher_refuses_an_unknown_work_kind_and_an_unpublished_advice(self):
        with self.assertRaisesRegex(ContractError, "work_kind"):
            self.r.publish_decision(self.run, "unbound", self.fields(work_kind="deploying"))
        with self.assertRaisesRegex(ContractError, "sha256"):
            self.r.publish_decision(self.run, "unbound", self.fields(advice_sha256="not-a-sha"))
        with self.assertRaisesRegex(ContractError, "cannot read|not the advice this run published"):
            self.r.publish_decision(self.run, "unbound", self.fields(advice_sha256="a" * 64))
        self.assertFalse(published_decision_path(self.co, "unbound").exists())

    def test_the_publisher_refuses_an_off_arm_decision_beside_a_published_advice(self):
        self.publish_advice()
        with self.assertRaisesRegex(ContractError, "citing no advice"):
            self.r.publish_decision(self.run, "unbound", self.fields())
        self.assertFalse(published_decision_path(self.co, "unbound").exists())

    def test_the_publisher_refuses_an_action_r62_does_not_name(self):
        """R62 decision.on_arm: accepted, rejected or supervisor_review, nothing else.

        The off arm has no advice to accept or reject, so there only the
        supervisor's own review is a true statement. Each refusal precedes the
        exclusive write, so no decision file is left for a retry to trip on.
        """
        for action in ("ignored-it", "accepted", "rejected"):
            with self.subTest(action=action), \
                    self.assertRaisesRegex(ContractError, "action must be accepted, rejected or supervisor_review"):
                self.r.publish_decision(self.run, "unbound", self.fields(action=action))
            self.assertFalse(published_decision_path(self.co, "unbound").exists())
        # The one off-arm action that is true publishes.
        self.assertEqual(self.r.publish_decision(self.run, "unbound", self.fields())["node_id"], "unbound")
        self.assertEqual(read_json(published_decision_path(self.co, "unbound"))["action"], "supervisor_review")

    # --- the pinned call shape and the pure predicates --------------------

    def test_the_pinned_call_shape_requires_its_exact_field_set(self):
        fields = self.fields()
        exact = control_script(self.f.campaign, self.f.ref, "publish-decision", self.run,
                               node_id="unbound", decision_fields=fields)
        shuffled = {name: fields[name] for name in reversed(DECISION_ARGUMENTS)}
        self.assertEqual(control_script(self.f.campaign, self.f.ref, "publish-decision", self.run,
                                        node_id="unbound", decision_fields=shuffled), exact)
        for bad in ({k: v for k, v in fields.items() if k != "reason"}, dict(fields, extra="x"), {}):
            with self.subTest(bad=sorted(bad)), self.assertRaisesRegex(ContractError, "frozen field set"):
                control_script(self.f.campaign, self.f.ref, "publish-decision", self.run,
                               node_id="unbound", decision_fields=bad)
        for value in (None, "", 7):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "original text"):
                control_script(self.f.campaign, self.f.ref, "publish-decision", self.run,
                               node_id="unbound", decision_fields=dict(fields, reason=value))
        # Existing callers are unchanged byte for byte.
        self.assertEqual(control_script(self.f.campaign, self.f.ref, "start", self.run),
                         control_script(self.f.campaign, self.f.ref, "start", self.run,
                                        decision_fields=None))

    def test_the_new_predicates_each_decide_one_thing(self):
        self.assertTrue(publish_call_is_the_frozen_script("abc", "abc"))
        self.assertFalse(publish_call_is_the_frozen_script("abc", "abc "))
        self.assertFalse(publish_call_is_the_frozen_script(dict(cmd="abc"), "abc"))
        self.assertFalse(publish_call_is_the_frozen_script(None, None))
        expected = dict(run_id="run-01", node_id="unbound", supervisor_session="s",
                        decision=dict(path="/p", bytes=3, sha256="c" * 64),
                        publisher=dict(path="/q", sha256="d" * 64), inputs=dict(campaign=None, advice=None))
        envelope = dict(expected, schema="orchestration-decision-publication-v1")
        self.assertTrue(publication_pins_the_decision(envelope, expected))
        self.assertFalse(publication_pins_the_decision(dict(envelope, node_id="local"), expected))
        self.assertFalse(publication_pins_the_decision(expected, expected))
        self.assertFalse(publication_pins_the_decision("{}", expected))
        read = dict(result_record=dict(offset=100, length=50), timestamp="2026-10-29T00:00:01Z")
        publish = dict(call_record=dict(offset=150), call_timestamp="2026-10-29T00:00:02Z")
        self.assertTrue(publish_follows_the_read(read, publish))
        self.assertFalse(publish_follows_the_read(read, dict(publish, call_record=dict(offset=149))))
        self.assertFalse(publish_follows_the_read(read, dict(publish, call_timestamp="2026-10-29T00:00:00Z")))
        self.assertFalse(publish_follows_the_read(read, dict(publish, call_timestamp="not a time")))
        self.assertFalse(publish_follows_the_read(None, publish))

    def fresh(self):
        self.doCleanups()
        self.setUp()


if __name__ == "__main__":
    unittest.main()
