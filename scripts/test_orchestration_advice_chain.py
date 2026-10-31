"""The advisory arms at run level: off-arm violations, fallbacks, incomplete chains.

Each case goes through the real publisher, the real bind and the real join, so
an arm violation is refused where a supervisor would actually hit it, and every
refusal is asserted to leave no binding behind. No case contacts any service:
the only worker transports are injected callables.
"""
import json
import unittest

from orchestration_binding import SCHEMA as BINDING_SCHEMA, binding_path, write_binding
from orchestration_common import ContractError, file_ref, json_ref, parse_time, utc_now
from orchestration_coordinator import State
from orchestration_verify import derive_run
from test_orchestration_bind_unbound import BindFixtureMixin, suggesting_transport
from upgrade_advice_chain import advice_chain


def malformed_transport(request):
    """A transport that answers, but not with an object: JEV_ERROR, one request."""
    return "not an object"


class AdviceArmTests(BindFixtureMixin, unittest.TestCase):
    def chain(self):
        return advice_chain(self.co, "unbound", {}, self.readers(), registration=None,
                            not_after=parse_time(utc_now()).timestamp() + 600,
                            binding_ref=file_ref(binding_path(self.co.root, "unbound")))

    # --- negative 1: off arm with advice, three separate conditions --------

    def test_off_arm_with_an_advice_file_is_an_arm_violation(self):
        self.publish()
        # The publisher refuses an off-arm decision beside published advice, so
        # the only way to reach this state is advice published afterwards.
        self.advice(transport=suggesting_transport, budget=dict(max_model_requests=80))
        self.bind_call()
        with self.assertRaisesRegex(ContractError, "ADVICE_ARM_VIOLATION: off arm published advice"):
            self.bind()
        self.residue()

    def test_off_arm_with_worker_requests_is_an_arm_violation(self):
        self.publish()
        self.f.write_worker_journal(self.co, "unbound", entries=[dict(
            schema="jev-advice-worker-journal-v1", authority="WORKER_SELF_AUTHORED",
            model_requests=1, http_status=200)])
        self.bind_call()
        with self.assertRaisesRegex(ContractError, "ADVICE_ARM_VIOLATION: off arm made worker requests"):
            self.bind()
        self.residue()
        # The same journal with zero requests binds, so the count decided it.
        path = self.co.root / "nodes" / "unbound" / "advice-worker-journal.json"
        path.unlink()
        self.f.write_worker_journal(self.co, "unbound", entries=[dict(
            schema="jev-advice-worker-journal-v1", authority="WORKER_SELF_AUTHORED",
            model_requests=0, http_status=None)])
        self.assertIsNotNone(self.bind())

    def test_off_arm_with_an_advice_read_is_an_arm_violation(self):
        self.publish()
        # A read attempt naming this node's advice directory, inside the window,
        # with no advice file at all: the attempt alone is the violation.
        target = str(self.co.root / "nodes" / "unbound" / "advice" / ("0" * 64 + ".txt"))
        args = dict(cmd="cat " + target, login=False, max_output_tokens=12000)
        self.f.clients["codex"].append(dict(type="response_item", timestamp=self.next_stamp(), payload=dict(
            type="custom_tool_call", name="exec", call_id="peek",
            input="text(await tools.exec_command(" + json.dumps(args) + "));\n")))
        self.bind_call()
        with self.assertRaisesRegex(ContractError, "ADVICE_ARM_VIOLATION: off arm read advice"):
            self.bind()
        self.residue()

    def test_off_arm_exposure_to_any_advice_body_is_an_arm_violation(self):
        """Exposure is recognised however the advice path is spelt.

        The check used to match only this run's own absolute advice directory,
        so a complete read of another run's or a sibling node's advice, or a
        relative-path read of this node's advice, bound as advisory-off.
        """
        import tempfile
        from upgrade_advice_publish import advice_marker
        from orchestration_test_support import CampaignFixture

        def peek(command, *, workdir=None, call_id="peek"):
            args = dict(cmd=command, login=False, max_output_tokens=12000)
            if workdir is not None:
                args["workdir"] = workdir
            self.f.clients["codex"].append(dict(type="response_item", timestamp=self.next_stamp(), payload=dict(
                type="custom_tool_call", name="exec", call_id=call_id,
                input="text(await tools.exec_command(" + json.dumps(args) + "));\n")))

        def other_run_full_read():
            # Another campaign's real advice, read in full with this campaign's
            # frozen reader: the shared matcher would call it one full read.
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            other = CampaignFixture(directory.name, strategies=("omo",), version=3, arm="B", unbound=True)
            elsewhere = other.publish_fixture_advice(other.start(), "unbound", transport=suggesting_transport,
                                                     budget=dict(max_model_requests=80),
                                                     lanes=self.lane_facts(other))
            self.assertNotIn(str(self.co.root), elsewhere["command"])
            self.read(elsewhere)

        cases = {
            "another run's advice, read in full": other_run_full_read,
            "a sibling node's advice": lambda: peek(
                "cat " + str(self.co.root / "nodes" / "unbound-b" / "advice" / ("0" * 64 + ".txt"))),
            "this node's advice by a relative path": lambda: peek(
                "cat nodes/unbound/advice/" + "0" * 64 + ".txt", workdir=str(self.co.root)),
            "a named advice marker": lambda: peek(
                "grep -r " + advice_marker(self.run, "unbound", "0123456789abcdef") + " ."),
        }
        for label, expose in cases.items():
            with self.subTest(case=label):
                self.doCleanups()
                self.setUp()
                self.publish()
                expose()
                self.bind_call()
                with self.assertRaisesRegex(ContractError, "ADVICE_ARM_VIOLATION: off arm read advice"):
                    self.bind()
                self.residue()
        # A near miss is not advice: a sibling journal file binds.
        self.doCleanups()
        self.setUp()
        self.publish()
        peek("cat " + str(self.co.root / "nodes" / "unbound-b" / "advice-worker-journal.json"))
        self.bind_call()
        self.assertIsNotNone(self.bind())

    # --- negative 6: a fallback is never counted as consumption ------------

    def test_fallback_or_stopped_auth_is_not_counted_as_consumption(self):
        cases = {"NOT_CONFIGURED": dict(),
                 "JEV_ERROR": dict(transport=malformed_transport, budget=dict(max_model_requests=80)),
                 "STOPPED_AUTH": None}
        for status, kwargs in cases.items():
            with self.subTest(status=status):
                self.doCleanups()
                self.setUp()
                advice = self.stopped_auth() if kwargs is None else self.advice(**kwargs)
                self.assertEqual(advice["worker_status"], status)
                self.read(advice)
                self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
                self.bind_call()
                value = json_ref(self.bind(), "binding")
                self.assertIs(value["advice"]["counted_as_advisory_consumption"], False)
                chain = self.chain()
                self.assertIs(chain["counted_as_advisory_consumption"], False)
                self.assertEqual(chain["status"], "NOT_CONFIGURED" if status == "NOT_CONFIGURED" else "JEV_FALLBACK")
                self.assertNotEqual(chain["status"], "CONSUMED")
        # The same shape with a real suggestion is counted, so the status decided it.
        self.doCleanups()
        self.setUp()
        advice = self.advice(transport=suggesting_transport, budget=dict(max_model_requests=80))
        self.read(advice)
        self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
        self.bind_call()
        self.bind()
        self.assertEqual(self.chain()["status"], "CONSUMED")

    def stopped_auth(self):
        """An advice body whose worker status is STOPPED_AUTH.

        STOPPED_AUTH is not a jev_advice status at this HEAD (R62 names it), so
        it is published by hand through the same publisher and reader.
        """
        from upgrade_advice_publish import advice_body, advice_marker, advice_tag, publish_advice
        from upgrade_body_read import read_command
        advice = dict(status="STOPPED_AUTH", reason="the advisor stopped on an authorization refusal",
                      candidates=(), fallback="rules")
        marker = advice_marker(self.run, "unbound", advice_tag(advice))
        body = advice_body(self.f.ref, self.run, "unbound", advice, None, marker=marker,
                           input_units=self.f.campaign["inputs"])
        published = publish_advice(self.co.root, self.run, "unbound", body)
        command = read_command(self.f.campaign["python"]["path"], self.f.campaign["controller"]["path"],
                               published["notice"])
        self.f.write_worker_journal(self.co, "unbound", entries=[])
        self.f.register_fixture_on_arm(self.co, "unbound")
        return dict(advice_sha256=published["body_sha256"], notice=published["notice"], command=command,
                    worker_status="STOPPED_AUTH")

    # --- an incomplete on-arm chain ----------------------------------------

    def test_on_arm_without_a_complete_chain_is_advice_not_consumed(self):
        advice = self.advice(transport=suggesting_transport, budget=dict(max_model_requests=80))
        # The supervisor publishes a decision citing the advice without ever
        # reading it, so the on arm never reaches its complete read.
        self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
        self.bind_call()
        with self.assertRaisesRegex(ContractError, "the on arm binds only after one complete original read"):
            self.bind()
        self.residue()
        # The chain itself classifies that join as ADVICE_NOT_CONSUMED: here it
        # is asked over a hand-written binding of the same evidence, which the
        # bind above refused to create.
        decision = file_ref(self.co.root / "decisions" / "unbound.json")
        published = dict(arm="advisory-on", advice_sha256=advice["advice_sha256"], notice=advice["notice"],
                         command=advice["command"], fence=self.co.header["native_start"]["codex"],
                         not_before_epoch=parse_time(self.co.header["started_at"]).timestamp(),
                         worker_status=advice["worker_status"], worker_journal=advice["worker_journal"])
        write_binding(self.co, "unbound", dict(
            schema=BINDING_SCHEMA, campaign=self.f.ref, run_id=self.run, node_id="unbound", lane="codex",
            execution_kind="local_cli", transport="codex_cli", mode="local_cli", arm=self.f.campaign["arm"],
            decision=decision, advice=published, eligible_lanes=["codex"], lane_facts={},
            bound_at=self.stamps[-1], bound_native=self.bind_refs[-1], input_units=self.f.campaign["inputs"],
            supervisor_session=self.f.campaign["sessions"]["codex"]["session_id"]))
        self.assertEqual(self.chain()["status"], "ADVICE_NOT_CONSUMED")
        # A run whose unbound node was never claimed cannot close as success:
        # derive_run requires every node accepted or a preserved failure.
        self.assertEqual(self.co.state()[0]["unbound"], State.PENDING)
        local = self.co.claim("local")
        self.f.work(local)
        self.f.accept(self.co, "local", self.f.candidate(self.co, "local"))
        with self.assertRaisesRegex(ContractError, "unfinished run has no verified failure boundary"):
            derive_run(self.co, {k: c.reader.snapshot() for k, c in self.f.clients.items()})


if __name__ == "__main__":
    unittest.main()
