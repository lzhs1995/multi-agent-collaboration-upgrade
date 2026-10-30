"""Bind a deliberately unbound frozen node before its claim, at run level.

The decision is published by the fixed closure-pinned publisher inside the run
tree, its pin and ``decided_at`` come from the native join of that publisher's
own call and result, and the bind control call is the original supervisor's.
Permission, identity, non-author, capacity and input version are recomputed in
code at bind time; the actual-context fact has no producer and says so.

Every refusal is asserted to leave no residue, and an already bound campaign
must behave byte-identically: the extra claim key and the extra run-header key
exist only under the explicit plan opt-in.
"""
import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import orchestration_binding
from orchestration_binding import (CONTEXT_FACT, POLICY, binding_path, bound_lane, claim_lane, claimed_lanes,
                                   read_binding, write_binding)
from orchestration_campaign import NO_ADVICE, control_script
from orchestration_common import (ContractError, file_ref, json_ref, parse_time, read_json, tree_manifest,
                                  utc_now, write_json)
from orchestration_coordinator import Coordinator, State, select_ready
from orchestration_native import NativeReader
from orchestration_observe import next_action, package_drafts, recorded_state
from orchestration_run import Runner, main
from orchestration_test_support import CampaignFixture
from upgrade_advice_chain import advice_chain


def suggesting_transport(request):
    return dict(answers=dict(work_kind="coding", work_kind_applies=0.9), http_status=200)


class BindFixtureMixin:
    """Shared run-level fixture steps; not a TestCase, so importing it
    into another test module discovers no extra tests."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = self.fixture()
        self.co = self.f.start()
        self.run = self.co.spec["run_id"]
        self.r = Runner(self.f.ref, fixtures=True)
        self.stamps, self.issued, self.bind_refs, self.bind_call_ids = [], None, [], []

    def fixture(self, *, extra_unbound=False, base=None):
        f = CampaignFixture(base or self.temp.name, strategies=("omo",), version=3, arm="B", unbound=True)
        if extra_unbound:
            f.plan["nodes"].append(dict(f.plan["nodes"][-1], id="unbound-b", title="unbound-b",
                                        write_scope="unbound-b"))
            f.campaign["plan"] = write_json(f.root / "plan.json", f.plan)
        # The publisher is the frozen adapter entrypoint, so the fixture's
        # adapter must contain it exactly as a real frozen campaign would.
        adapter = Path(f.campaign["adapter"]["path"])
        shutil.copyfile(Path(__file__).resolve().with_name("orchestration_run.py"),
                        adapter / "scripts/orchestration_run.py")
        f.campaign["adapter"] = tree_manifest(adapter)
        f.save()
        return f

    def next_stamp(self, *extra, gap_ms=1):
        """A stamp strictly after every stamp seen or issued so far, and after now.

        The increment is one millisecond, not one second: the chain requires
        read < decided < bound < claim, and the claim's own ``started_at`` is
        real wall time, so stamping the bind call a second ahead would place it
        after the claim it must precede.
        """
        moments = [parse_time(value) for value in list(self.stamps) + list(extra) if value]
        moments.append(parse_time(utc_now()))
        if self.issued is not None:
            moments.append(parse_time(self.issued))
        self.issued = (max(moments) + timedelta(milliseconds=gap_ms)).isoformat().replace("+00:00", "Z")
        return self.issued

    @staticmethod
    def wait_past(stamp):
        """Bounded wait until wall time passes a stamp; never a fixed sleep."""
        deadline = monotonic() + 10
        while parse_time(utc_now()) <= parse_time(stamp):
            if monotonic() > deadline:
                raise AssertionError("wall clock did not pass " + stamp)
            sleep(0.002)

    def fields(self, **changes):
        value = dict(work_kind="coding", actual_route="codex", action="accepted",
                     reason="the supervisor chose the idle codex lane", advice_sha256=NO_ADVICE)
        value.update(changes)
        return value

    def publish(self, fields=None, *, node_id="unbound", runner=None, co=None, f=None):
        """Append the supervisor's publisher call, publish, append its result."""
        f, co = f or self.f, co or self.co
        fields = self.fields() if fields is None else fields
        client = f.clients["codex"]
        script = control_script(f.campaign, f.ref, "publish-decision", co.spec["run_id"],
                                node_id=node_id, decision_fields=fields)
        when = self.next_stamp()
        call = client.append(dict(type="response_item", timestamp=when, payload=dict(
            type="custom_tool_call", name="exec", call_id="publish-" + node_id, input=script)))
        result = (runner or self.r).publish_decision(co.spec["run_id"], node_id, fields)
        later = self.next_stamp()
        printed = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        client.append(dict(type="response_item", timestamp=later, payload=dict(
            type="custom_tool_call_output", call_id="publish-" + node_id,
            output=json.dumps(dict(exit_code=0, output=printed + "\n", wall_time_seconds=0.1)))))
        self.stamps.append(later)
        return dict(result=result, call=call, result_at=later, script=script)

    def bind_call(self, *, node_id="unbound", decision=None, co=None, f=None, at=None, count=1, script=None):
        """Append the original supervisor's bind control call(s)."""
        f, co = f or self.f, co or self.co
        path = decision if decision is not None else str(co.root / "decisions" / (node_id + ".json"))
        exact = script if script is not None else control_script(
            f.campaign, f.ref, "bind-unbound", co.spec["run_id"], node_id=node_id, decision=path)
        refs, call_ids = [], []
        for index in range(count):
            when = at or self.next_stamp()
            # Unique per appended call: a real supervisor retry is a new call
            # with its own call_id, and an earlier attempt's output must never
            # be read as this one's.
            call_id = f"bind-{node_id}-{len(self.bind_call_ids)}-{index}"
            call_ids.append(call_id)
            refs.append(f.clients["codex"].append(dict(type="response_item", timestamp=when, payload=dict(
                type="custom_tool_call", name="exec", call_id=call_id, input=exact))))
            self.stamps.append(when)
        self.bind_refs = refs
        self.bind_call_ids.extend(call_ids)
        return dict(refs=refs, call_ids=call_ids, script=exact, path=path)

    def bind(self, *, node_id="unbound", co=None, f=None, decision=None):
        """Bind, then append the bind call's own result as the CLI's exit does.

        ``main`` prints the created binding's ``file_ref`` and the original CLI
        records that as the bind call's terminal rc0 output, so a claim can join
        it. Only a *successful* bind gets a result here: a refused attempt's
        failure output is appended explicitly by the retry cases, so every other
        refusal test keeps exactly today's transcript.
        """
        co = co or self.co
        f = f or self.f
        path = decision if decision is not None else str(co.root / "decisions" / (node_id + ".json"))
        ref = co.bind_unbound(node_id, path)
        self.bind_result(ref, node_id=node_id, co=co, f=f)
        return ref

    def bind_result(self, ref, *, node_id="unbound", co=None, f=None, exit_code=0, printed=None):
        """Append the bind call's terminal output for the call the binding pins."""
        f, co = f or self.f, co or self.co
        value = json_ref(ref, "binding")
        call_id = NativeReader(f.campaign["sessions"]["codex"], fixtures=True).record(
            value["bound_native"])["payload"]["call_id"]
        text = json.dumps(dict(ref) if printed is None else printed,
                          ensure_ascii=False, sort_keys=True, allow_nan=False)
        when = self.next_stamp()
        record = f.clients["codex"].append(dict(type="response_item", timestamp=when, payload=dict(
            type="custom_tool_call_output", call_id=call_id,
            output=json.dumps(dict(exit_code=exit_code, output=text + "\n", wall_time_seconds=0.1)))))
        self.stamps.append(when)
        return record

    def refused_bind_output(self, call, *, f=None, index=-1):
        """Append the failed output a refused bind control call really leaves."""
        f = f or self.f
        when = self.next_stamp()
        record = f.clients["codex"].append(dict(type="response_item", timestamp=when, payload=dict(
            type="custom_tool_call_output", call_id=call["call_ids"][index],
            output=json.dumps(dict(exit_code=1, output="REFUSED\n", wall_time_seconds=0.1)))))
        self.stamps.append(when)
        return record

    def advice(self, *, node_id="unbound", transport=None, budget=None, co=None, f=None, **kwargs):
        """Publish a worker answer as a pinned advice body and its journal."""
        f, co = f or self.f, co or self.co
        published = f.publish_fixture_advice(co, node_id, transport=transport, budget=budget,
                                             lanes=self.lane_facts(f), **kwargs)
        f.write_worker_journal(co, node_id, entries=published["worker_journal"])
        return published

    @staticmethod
    def lane_facts(f):
        return {lane: dict(kind="codex" if lane == "codex" else "claude", verified=True,
                           idle_write_domain=True, author=False)
                for lane in f.campaign["sessions"]}

    def read(self, advice, *, f=None, **kwargs):
        f = f or self.f
        observed = f.fake_advice_read_records(advice, at=self.next_stamp(), **kwargs)
        self.stamps.append(observed["result_at"])
        return observed

    def residue(self, *, node_id="unbound", co=None):
        """Nothing a retry could mistake for an original bind."""
        co = co or self.co
        self.assertFalse(binding_path(co.root, node_id).exists())
        self.assertIsNone(read_binding(co.root, node_id))
        self.assertEqual(co.state()[0][node_id], State.PENDING)
        self.assertEqual(co.events(), [])

    def readers(self):
        return {lane: NativeReader(session, fixtures=True)
                for lane, session in self.f.campaign["sessions"].items()}


class BindUnboundTests(BindFixtureMixin, unittest.TestCase):
    # --- positives -------------------------------------------------------

    def test_bind_then_claim_resolves_the_lane_end_to_end(self):
        self.publish()
        self.bind_call()
        binding = self.bind()
        value = json_ref(binding, "binding")
        self.assertEqual((value["lane"], value["mode"], value["execution_kind"], value["transport"]),
                         ("codex", "local_cli", "local_cli", "codex_cli"))
        # The decision pin and decided_at come from the native publish join.
        self.assertEqual(value["decision"]["source"], "native_publish_result")
        self.assertEqual(value["decision"]["decided_at"], self.stamps[0])
        self.assertIsNone(read_json(value["decision"]["path"])["decided_at"])
        self.assertEqual(value["decision"]["diagnostic_file"],
                         {k: value["decision"][k] for k in ("path", "bytes", "sha256")})
        self.assertEqual(value["bound_native"], self.bind_refs[-1])
        # The actual-context fact has no producer anywhere in this repository.
        self.assertEqual(value["lane_facts"]["context"], CONTEXT_FACT)
        self.assertEqual(sorted(value["lane_facts"]["lanes"]), ["claude_a", "claude_b", "codex"])
        # The node is still PENDING: a binding appends no lifecycle event.
        self.assertEqual(self.co.state()[0]["unbound"], State.PENDING)
        self.assertEqual(self.co.events(), [])
        self.wait_past(value["bound_at"])
        info = self.co.claim("unbound")
        self.assertEqual(info["executor_binding"], binding)
        self.assertEqual(info["mode"], "local_cli")
        self.assertEqual(self.co.state()[0]["unbound"], State.LOCAL_RUNNING)
        self.assertEqual(claim_lane(self.co.nodes["unbound"], info), "codex")
        self.assertEqual(bound_lane(self.co, "unbound", info), "codex")
        # next_action/status resolve the lane instead of raising KeyError: None.
        view = next_action(self.r, self.run)
        row = next(r for r in view["nodes"] if r["node_id"] == "unbound")
        self.assertEqual(row["recorded_state"], "LOCAL_RUNNING")
        self.assertIsNotNone(row["timeline"]["boundaries"]["claimed"])
        self.assertEqual(self.r.status(self.run)["states"]["unbound"], "LOCAL_RUNNING")
        # The whole claimed node reaches acceptance on its bound lane.
        self.f.work(info)
        evidence = self.f.candidate(self.co, "unbound")
        result = self.f.accept(self.co, "unbound", evidence)
        self.assertEqual(result["status"], "ACCEPTED")
        chain = advice_chain(self.co, "unbound", self.co.state()[1]["unbound"], self.readers(),
                             registration=None, not_after=parse_time(utc_now()).timestamp() + 600)
        self.assertEqual((chain["arm"], chain["decided_at"]), ("advisory-off", value["decision"]["decided_at"]))
        self.assertIs(chain["decision_publish_natively_joined"], True)

    def test_bind_then_claim_on_a_claude_lane_resolves_the_delegated_mode(self):
        # Codex is first for coding/debugging, so a Claude lane is a candidate
        # only for the review/literature branch of the same truth table.
        self.publish(self.fields(work_kind="review", actual_route="claude_b"))
        self.bind_call()
        value = json_ref(self.bind(), "binding")
        self.assertEqual((value["lane"], value["mode"], value["transport"]),
                         ("claude_b", "delegated_cli", "claude_code_cli"))
        self.wait_past(value["bound_at"])
        info = self.co.claim("unbound")
        self.assertEqual((info["mode"], self.co.state()[0]["unbound"].value),
                         ("delegated_cli", "PREPARING"))
        # Every later consumer takes this lane, not the frozen plan's None.
        self.assertEqual(self.r.transport(self.co, "unbound").lane, "claude_b")
        self.assertEqual(self.r.transport(self.co, "unbound").receiver,
                         self.f.campaign["sessions"]["claude_b"])
        from orchestration_evidence import pack_extension, prepared_supervisor
        from orchestration_pack_inputs import adoption_reference
        self.assertEqual(pack_extension(self.f.campaign, self.co.header,
                                        self.co.nodes["unbound"], info)["executor_id"], "claude_b")
        # An offline campaign has no client isolation, so adoption stays None
        # and no lane is resolved for it at all.
        self.assertIsNone(adoption_reference(self.f.campaign, self.co.nodes["unbound"], info))
        self.assertIsNone(adoption_reference(self.f.campaign, self.co.nodes["unbound"]))
        for name, call in (("prepared_supervisor", lambda: prepared_supervisor(
                               self.f.campaign, self.co.nodes["unbound"], {})),
                           ("pack_extension", lambda: pack_extension(
                               self.f.campaign, self.co.header, self.co.nodes["unbound"],
                               dict(info, executor_binding=None)))):
            with self.subTest(name=name):
                # Without the claim's pin there is no lane to resolve, and the
                # refusal is typed instead of silently reading the frozen None.
                with self.assertRaisesRegex(ContractError, "claimed executor binding"):
                    call()

    def test_the_on_arm_binds_after_one_complete_original_advice_read(self):
        advice = self.advice(transport=suggesting_transport, budget=dict(max_model_requests=80))
        self.read(advice)
        self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
        self.bind_call()
        value = json_ref(self.bind(), "binding")
        self.assertEqual(value["advice"]["arm"], "advisory-on")
        self.assertEqual(value["advice"]["advice_sha256"], advice["advice_sha256"])
        self.assertEqual(value["advice"]["read"]["kind"], "BODY_READ_OBSERVED")
        self.assertIs(value["advice"]["counted_as_advisory_consumption"], True)
        chain = advice_chain(self.co, "unbound", {}, self.readers(), registration=None,
                             not_after=parse_time(utc_now()).timestamp() + 600,
                             binding_ref=file_ref(binding_path(self.co.root, "unbound")))
        self.assertEqual(chain["status"], "CONSUMED")
        self.assertEqual(chain["read"], value["advice"]["read"])

    def test_a_not_configured_worker_binds_without_counting_as_consumption(self):
        advice = self.advice()
        self.assertEqual(advice["worker_status"], "NOT_CONFIGURED")
        self.read(advice)
        self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
        self.bind_call()
        value = json_ref(self.bind(), "binding")
        self.assertIs(value["advice"]["counted_as_advisory_consumption"], False)
        self.assertEqual(value["advice"]["worker_status"], "NOT_CONFIGURED")

    def test_run_header_carries_unbound_nodes_only_under_the_opt_in(self):
        header = read_json(self.co.root / "run.json")
        self.assertEqual(header["unbound_nodes"], ["unbound"])
        self.assertEqual(header["queues"], {"codex": ["local", "closeout"], "claude_a": ["review", "followup"],
                                            "claude_b": ["parallel"]})
        # Re-reading the same bytes verifies, and the key is part of the exact
        # field set only because this plan opted in.
        Coordinator(self.co.root, self.f.ref, fixtures=True)
        with tempfile.TemporaryDirectory() as other:
            plain = CampaignFixture(other, strategies=("omo",), version=3, arm="B")
            co = plain.start()
            plain_header = read_json(co.root / "run.json")
            self.assertNotIn("unbound_nodes", plain_header)
            self.assertEqual(set(plain_header), {"version", "campaign", "run", "queues", "started_at",
                                                 "created_at", "started_monotonic_ns", "boot_id",
                                                 "native_start", "start_native"})
            Coordinator(co.root, plain.ref, fixtures=True)
            # A non-opted run that somehow carried the key fails at construction.
            write_json(co.root / "run.json", dict(plain_header, unbound_nodes=[]))
            with self.assertRaisesRegex(ContractError, "run header fields changed"):
                Coordinator(co.root, plain.ref, fixtures=True)

    def test_claim_mode_matches_the_frozen_null_execution_kind(self):
        self.publish()
        self.bind_call()
        value = json_ref(self.bind(), "binding")
        self.wait_past(value["bound_at"])
        info = self.co.claim("unbound")
        self.assertIsNone(self.co.nodes["unbound"]["execution_kind"])
        self.assertEqual(info["mode"], "local_cli")
        self.co._check_claim("unbound", info, State.LOCAL_RUNNING, {})
        self.assertTrue(orchestration_binding.claim_mode_matches_frozen_kind(
            self.co.nodes["unbound"], info, "local_cli"))
        self.assertFalse(orchestration_binding.claim_mode_matches_frozen_kind(
            self.co.nodes["unbound"], info, "delegated_cli"))
        # The conditional does not weaken the bound gate: a bound node whose
        # recorded mode and frozen execution_kind disagree still fails.
        bound = self.co.nodes["local"]
        self.assertTrue(orchestration_binding.claim_mode_matches_frozen_kind(
            bound, {"mode": "local_cli"}, "local_cli"))
        self.assertFalse(orchestration_binding.claim_mode_matches_frozen_kind(
            bound, {"mode": "delegated_cli"}, "local_cli"))
        # "local" shares the codex lane with the node just claimed, so the
        # bound-node half runs on its own fixture.
        with tempfile.TemporaryDirectory() as other:
            plain = CampaignFixture(other, strategies=("omo",), version=3, arm="B")
            co = plain.start()
            local = co.claim("local")
            co._check_claim("local", local, State.LOCAL_RUNNING, {})
            with self.assertRaisesRegex(ContractError, "execution ownership changed"):
                co._check_claim("local", dict(local, mode="delegated_cli"), State.LOCAL_RUNNING, {})

    def test_claim_before_bind_escalates_with_the_unbound_reason(self):
        with self.assertRaisesRegex(ContractError,
                                    "the frozen node is deliberately unbound; bind it before claiming"):
            self.co.claim("unbound")
        self.assertFalse((self.co.root / "claims" / "unbound.json").exists())
        self.assertFalse((self.co.root / "nodes" / "unbound" / "claim-gates.json").exists())
        self.assertEqual(self.co.state()[0]["unbound"], State.PENDING)

    def test_binding_a_bound_node_is_refused(self):
        self.publish()
        self.bind_call()
        for key in ("local", "review"):
            with self.subTest(node=key):
                with self.assertRaisesRegex(ContractError, "only a deliberately unbound frozen node"):
                    self.co.bind_unbound(key, str(self.co.root / "decisions" / "unbound.json"))
                self.assertFalse(binding_path(self.co.root, key).exists())
        with self.assertRaisesRegex(ContractError, "unknown frozen node"):
            self.co.bind_unbound("absent", str(self.co.root / "decisions" / "unbound.json"))

    def test_bind_refuses_a_decision_outside_the_publisher(self):
        self.publish()
        self.bind_call()
        elsewhere = self.f.write_decision(self.run, "unbound", decided_at=None,
                                          path=self.f.root / "decisions" / "outside.json")
        with self.assertRaisesRegex(ContractError, "only the decision the fixed publisher created"):
            self.bind(decision=elsewhere["path"])
        self.residue()

    def test_bind_refuses_a_changed_input_version(self):
        advice = self.advice(transport=suggesting_transport, budget=dict(max_model_requests=80))
        self.read(advice)
        self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
        self.bind_call()
        # The advice body's recorded input units are inside its sha, so the
        # change is applied to the run's frozen campaign inputs instead.
        changed = dict(self.f.campaign["inputs"][0], sha256="0" * 64)
        with patch.dict(self.co.campaign, {"inputs": [changed]}):
            with self.assertRaisesRegex(ContractError, "input version changed"):
                self.bind()
        self.residue()
        # Unchanged inputs still bind, so the refusal came from the comparison.
        self.assertIsNotNone(self.bind())

    def test_bind_refuses_an_unknown_or_missing_work_kind(self):
        self.publish()
        self.bind_call()
        path = self.co.root / "decisions" / "unbound.json"
        value = read_json(path)
        for case, change in (("unknown", dict(work_kind="refactoring")), ("missing", None)):
            with self.subTest(case=case):
                broken = dict(value)
                if change is None:
                    del broken["work_kind"]
                else:
                    broken.update(change)
                path.unlink()
                write_json(path, broken)
                with patch("orchestration_coordinator.eligible_candidates",
                           side_effect=AssertionError("candidates must not be recomputed")):
                    with self.assertRaises(ContractError) as caught:
                        self.bind()
                self.assertRegex(str(caught.exception), "work_kind|bind decision fields changed")
                self.residue()

    def test_second_bind_is_refused_and_leaves_the_first_bytes(self):
        self.publish()
        self.bind_call()
        first = self.bind()
        self.assertEqual(first, file_ref(binding_path(self.co.root, "unbound")))
        with self.assertRaisesRegex(ContractError, "original executor binding exists; observe only"):
            self.bind()
        self.assertEqual(file_ref(binding_path(self.co.root, "unbound")), first)

    def test_route_not_in_recomputed_candidates_is_refused_before_any_write(self):
        # Coding is codex-first while codex is idle, and review/literature
        # never offer codex at all, so each of these routes is outside the
        # candidates code recomputes for its own work kind.
        for work_kind, route in (("coding", "claude_a"), ("coding", "claude_b"),
                                 ("review", "codex"), ("literature", "codex")):
            with self.subTest(work_kind=work_kind, route=route):
                self.setUp()
                self.publish(self.fields(work_kind=work_kind, actual_route=route))
                self.bind_call()
                with self.assertRaisesRegex(ContractError, "actual route must come from the candidates"):
                    self.bind()
                self.residue()

    def test_decision_for_another_run_or_node_is_refused(self):
        self.publish()
        self.bind_call()
        path = self.co.root / "decisions" / "unbound.json"
        for case, change in (("run", dict(run_id="run-99")), ("node", dict(node_id="local")),
                             ("session", dict(supervisor_session="another-session"))):
            with self.subTest(case=case):
                broken = dict(read_json(path))
                broken.update(change)
                path.unlink()
                write_json(path, broken)
                with self.assertRaises(ContractError) as caught:
                    self.bind()
                self.assertRegex(str(caught.exception),
                                 "another run or frozen node|another supervisor session")
                self.residue()

    def test_bind_requires_the_original_supervisor_control_call(self):
        self.publish()
        with self.subTest(case="absent"):
            with self.assertRaisesRegex(ContractError, "one original supervisor control invocation required"):
                self.bind()
            self.residue()
        with self.subTest(case="wrong decision path in the call"):
            self.bind_call(decision=str(self.co.root / "decisions" / "other.json"))
            with self.assertRaisesRegex(ContractError, "one original supervisor control invocation required"):
                self.bind()
            self.residue()
        with self.subTest(case="wrong argv order"):
            script = control_script(self.f.campaign, self.f.ref, "bind-unbound", self.run,
                                    node_id="unbound")
            script = script.replace("bind-unbound", "bind-unbound --decision x", 1)
            self.bind_call(script=script)
            with self.assertRaisesRegex(ContractError, "one original supervisor control invocation required"):
                self.bind()
            self.residue()
        with self.subTest(case="duplicated"):
            self.bind_call(count=2)
            with self.assertRaisesRegex(ContractError, "one original supervisor control invocation required"):
                self.bind()
            self.residue()

    def test_a_transiently_refused_bind_is_retried_with_a_new_call(self):
        """The published decision is never republished, so the bind must retry.

        The route is recomputed at bind time, so a lane taken at that moment
        refuses a bind that would have been admissible a moment earlier. The
        refused attempt's own call stays in the transcript; a retry appends a
        new call, and the bind joins the invocation that has not returned.
        """
        self.publish()
        # The codex lane is taken by another node of this run, so the published
        # route is not among the candidates recomputed now.
        taken = self.co.claim("local")
        first = self.bind_call()
        with self.assertRaisesRegex(ContractError, "actual route must come from the candidates"):
            self.bind()
        # No binding, and the node is untouched. (``residue`` also asserts an
        # empty event log, which the claim above legitimately filled.)
        self.assertFalse(binding_path(self.co.root, "unbound").exists())
        self.assertEqual(self.co.state()[0]["unbound"], State.PENDING)
        self.refused_bind_output(first)
        # The lane is released; the same decision is now bindable again.
        self.f.work(taken)
        self.f.accept(self.co, "local", self.f.candidate(self.co, "local"))
        # Without a new call there is no running invocation to join.
        with self.assertRaisesRegex(ContractError, "one original supervisor control invocation required"):
            self.bind()
        self.assertFalse(binding_path(self.co.root, "unbound").exists())
        self.bind_call()
        binding = self.bind()
        value = json_ref(binding, "binding")
        self.assertEqual(value["bound_native"], self.bind_refs[-1])
        self.assertEqual(value["lane"], "codex")
        # The retry's own call is the proof, not the refused attempt's.
        self.assertNotEqual(value["bound_native"], first["refs"][0])
        self.wait_past(value["bound_at"])
        self.assertEqual(self.co.claim("unbound")["executor_binding"], binding)

    def test_two_bind_invocations_still_running_are_ambiguous(self):
        # Only a *returned* attempt can be told apart from the live one, so two
        # unanswered identical calls refuse exactly as a duplicate always did.
        self.publish()
        call = self.bind_call(count=2)
        with self.assertRaisesRegex(ContractError, "one original supervisor control invocation required"):
            self.bind()
        self.residue()
        # Once the first attempt's failure is recorded, one invocation is live.
        self.refused_bind_output(call, index=0)
        self.assertIsNotNone(self.bind())

    def test_bind_is_new_work_under_stop_and_preserved_failure(self):
        self.publish()
        self.bind_call()
        with patch.object(Coordinator, "_work_budget",
                          side_effect=ContractError("experiment Stop forbids new work")):
            with self.assertRaisesRegex(ContractError, "experiment Stop forbids new work"):
                self.bind()
        self.residue()
        # A preserved failure on another lane forbids new work for this one.
        info = self.co.claim("review")
        blocker = write_json(self.co.root / "nodes" / "review" / "blocker.json",
                             dict(task_id=info["task_id"], reason="offline fixture blocker"))
        self.co.transition("review", State.BLOCKED, {"blocker": blocker})
        with self.assertRaisesRegex(ContractError, "preserved run failure forbids new work"):
            self.bind()
        self.assertFalse(binding_path(self.co.root, "unbound").exists())

    def test_a_binding_bind_unbound_never_wrote_is_refused_by_claim_and_replay(self):
        """A hand-written binding file routes nothing, however well-formed.

        The run tree is writable, so "the claim pins a binding" is not a proof
        that `bind_unbound` wrote it. Each case here is the shape a supervisor
        could place beside a real published decision; all of them are refused
        before any claim write, and the node stays claimable afterwards.
        """
        published = self.publish()["result"]["decision"]
        pin = {k: published[k] for k in ("path", "bytes", "sha256")}
        base = dict(schema=orchestration_binding.SCHEMA, campaign=self.f.ref, run_id=self.run,
                    node_id="unbound", lane="codex", execution_kind="local_cli", transport="codex_cli",
                    mode="local_cli", arm=self.f.campaign.get("arm"), decision=pin, advice=dict(
                        arm="advisory-off", advice_sha256=None, worker_status=None, worker_journal=None,
                        fence=self.co.header["native_start"]["codex"]),
                    eligible_lanes=["codex"], lane_facts=dict(lanes=self.lane_facts(self.f), context=CONTEXT_FACT),
                    bound_at="2000-01-01T00:00:00+00:00", bound_native=None,
                    input_units=self.f.campaign["inputs"],
                    supervisor_session=self.f.campaign["sessions"]["codex"]["session_id"])
        call = self.bind_call()
        cases = {
            # No bind control call at all: forged from nothing.
            "no_bind_call": (dict(base), "BIND_CALL"),
            # A real bind call that was refused: its call exists in the
            # transcript, but it never printed this binding.
            "refused_bind_call": (dict(base, bound_native=call["refs"][0], bound_at=self.stamps[-1]),
                                  "no result that created this binding"),
        }
        for label, (value, reason) in cases.items():
            with self.subTest(label=label):
                path = binding_path(self.co.root, "unbound")
                if path.exists():
                    path.chmod(0o600)
                    path.unlink()
                write_binding(self.co, "unbound", value)
                with self.assertRaisesRegex(ContractError, reason):
                    self.co.claim("unbound")
                # Refused before any claim write: no residue, still PENDING.
                self.assertFalse((self.co.root / "claims" / "unbound.json").exists())
                self.assertFalse((self.co.root / "nodes" / "unbound" / "claim-gates.json").exists())
                self.assertEqual(self.co.state()[0]["unbound"], State.PENDING)
        # The same node binds and claims normally once bind_unbound writes it.
        path = binding_path(self.co.root, "unbound")
        path.chmod(0o600)
        path.unlink()
        binding = self.bind()
        self.wait_past(json_ref(binding, "binding")["bound_at"])
        self.assertEqual(self.co.claim("unbound")["executor_binding"], binding)

    def test_a_bind_result_printing_another_binding_is_refused(self):
        # The bind call's result is joined by what it printed, so a result that
        # prints any other pin cannot stand in for this binding's own.
        self.publish()
        self.bind_call()
        ref = self.co.bind_unbound("unbound", str(self.co.root / "decisions" / "unbound.json"))
        self.bind_result(ref, printed=dict(ref, sha256="0" * 64))
        with self.assertRaisesRegex(ContractError, "BIND_RESULT: the bind call did not print this"):
            self.co.claim("unbound")
        self.assertFalse((self.co.root / "claims" / "unbound.json").exists())

    def test_a_claim_before_the_bind_result_is_refused_and_succeeds_after_it(self):
        # A crash between the exclusive write and the CLI's own exit leaves a
        # binding whose creation no result proves yet. Honest refusal, then the
        # ordinary claim once the original call returns.
        self.publish()
        self.bind_call()
        ref = self.co.bind_unbound("unbound", str(self.co.root / "decisions" / "unbound.json"))
        with self.assertRaisesRegex(ContractError, "no result that created this binding yet"):
            self.co.claim("unbound")
        self.bind_result(ref)
        self.wait_past(json_ref(ref, "binding")["bound_at"])
        self.assertEqual(self.co.claim("unbound")["executor_binding"], ref)

    def test_the_claim_leg_names_the_evidence_that_orders_it(self):
        # R62 wants the chain's order from native records. The claim's own
        # started_at is the coordinator's wall clock, so the chain says which
        # evidence orders the claim instead of calling that stamp native.
        from upgrade_advice_chain import advice_chain
        for node_id, route, work_kind, expected in (
                ("unbound", "codex", "coding",
                 "native_codex_offset_after_the_bind_result_and_coordinator_wall_clock"),
                ("unbound", "claude_b", "review",
                 "binding_sha256_pin_of_the_bind_result_and_coordinator_wall_clock")):
            with self.subTest(route=route):
                self.doCleanups()
                self.setUp()
                self.publish(self.fields(work_kind=work_kind, actual_route=route), node_id=node_id)
                self.bind_call(node_id=node_id)
                binding = self.bind(node_id=node_id)
                self.wait_past(json_ref(binding, "binding")["bound_at"])
                info = self.co.claim(node_id)
                chain = advice_chain(self.co, node_id, info, self.readers(), registration=None,
                                     not_after=parse_time(utc_now()).timestamp() + 600)
                self.assertIs(chain["bind_result_joined"], True)
                self.assertEqual(chain["order_basis"]["claim_after_bind"], expected)
                self.assertEqual(chain["order_basis"]["bound_at"], "native_bind_control_record")
                self.assertEqual(chain["order_basis"]["claim_started_at"], expected)
                self.assertTrue(chain["order_verified"])

    def test_claim_replay_recomputes_the_same_decision_after_restart(self):
        self.publish()
        self.bind_call()
        value = json_ref(self.bind(), "binding")
        self.wait_past(value["bound_at"])
        info = self.co.claim("unbound")
        fresh = Coordinator(self.co.root, self.f.ref, fixtures=True)
        states, data = fresh.state()
        self.assertEqual(states["unbound"], State.LOCAL_RUNNING)
        self.assertEqual(data["unbound"], info)
        self.assertEqual(claimed_lanes(fresh, data)["unbound"], "codex")
        recorded = recorded_state(fresh, fresh.events())
        self.assertEqual(recorded[0]["unbound"], State.LOCAL_RUNNING)
        self.assertEqual(recorded[1]["unbound"], info)

    def test_editing_the_binding_after_the_claim_fails_replay(self):
        self.publish()
        self.bind_call()
        value = json_ref(self.bind(), "binding")
        self.wait_past(value["bound_at"])
        self.co.claim("unbound")
        path = binding_path(self.co.root, "unbound")
        path.chmod(0o600)
        path.unlink()
        write_json(path, dict(value, lane="claude_b"))
        fresh = Coordinator(self.co.root, self.f.ref, fixtures=True)
        with self.assertRaisesRegex(ContractError, "hash/size mismatch"):
            fresh.state()
        with self.assertRaisesRegex(ContractError, "hash/size mismatch"):
            recorded_state(fresh, fresh.events())

    def test_recover_reports_a_binding_without_a_claim(self):
        self.publish()
        self.bind_call()
        binding = self.bind()
        rows = self.co.recover()
        row = next(r for r in rows if r["node_id"] == "unbound")
        self.assertEqual((row["state"], row["action"]),
                         ("UNBOUND_BOUND_NOT_CLAIMED", "inspect_original_binding"))
        self.assertEqual(row["binding"], binding)
        self.assertIn("unbound", self.r.recover(self.run)["status"]["states"])
        # Once claimed the binding is no longer an unclaimed pre-claim artefact.
        self.wait_past(json_ref(binding, "binding")["bound_at"])
        self.co.claim("unbound")
        self.assertFalse(any(r["state"] == "UNBOUND_BOUND_NOT_CLAIMED" for r in self.co.recover()))

    def test_drafts_do_not_crash_on_an_unbound_node(self):
        drafts = package_drafts(self.f.ref, self.f.campaign, self.co.spec)["drafts"]
        row = next(d["draft"] for d in drafts if d["draft"]["node_id"] == "unbound")
        self.assertIsNone(row["executor"])
        self.assertIsNone(row["original_session"])
        self.assertIs(row["binding_required"], True)
        bound = next(d["draft"] for d in drafts if d["draft"]["node_id"] == "local")
        self.assertEqual(bound["executor"], "codex")
        self.assertNotIn("binding_required", bound)
        self.assertEqual(self.r.drafts(self.run)["drafts"], drafts)

    def test_observation_recommends_bind_unbound_and_advance_never_executes_it(self):
        view = next_action(self.r, self.run)
        row = next(r for r in view["nodes"] if r["node_id"] == "unbound")
        self.assertEqual(row["recommendation"]["action"], "bind-original-unbound-node")
        self.assertEqual(row["recommendation"]["gated_command"], "bind-unbound")
        self.assertNotIn("unbound", view["recorded_claim_candidates"])
        with patch.object(Coordinator, "bind_unbound",
                          side_effect=AssertionError("advance must never bind")):
            result = self.r.advance(self.run, max_steps=4)
        waiting = next(r for r in result["waiting"] if r["node_id"] == "unbound")
        self.assertEqual(waiting["action"], "bind-original-unbound-node")
        self.assertNotIn("exec_script", waiting)
        self.assertIn("--decision", waiting["instruction"])
        self.assertFalse(any(r["node_id"] == "unbound" for r in result["actions"]))

    def test_two_unbound_nodes_do_not_block_each_other_under_omo(self):
        f, co, r = self.two_unbound()
        # Neither is bound yet, so both lanes are unresolved. With the mapping
        # they do not collide on None == None and are selected together.
        ready = {node["id"] for node in co.ready()}
        self.assertTrue({"unbound", "unbound-b"} <= ready)
        # Without a mapping the comparison is exactly today's, and that
        # collision is what makes a forgotten call site fail loudly.
        states, _ = co.state()
        plain = {node["id"] for node in select_ready(co.plan, states, "omo", co.root / "work",
                                                     co.header["queues"])}
        self.assertEqual(len({"unbound", "unbound-b"} & plain), 1)
        first = self.bind_and_claim("unbound", "codex", f=f, co=co, r=r)
        second = self.bind_and_claim("unbound-b", "claude_b", f=f, co=co, r=r,
                                     work_kind="review")
        states, data = co.state()
        self.assertEqual((states["unbound"], states["unbound-b"]),
                         (State.LOCAL_RUNNING, State.PREPARING))
        self.assertEqual(claimed_lanes(co, data)["unbound"], "codex")
        self.assertEqual(claimed_lanes(co, data)["unbound-b"], "claude_b")
        self.assertEqual((first["mode"], second["mode"]), ("local_cli", "delegated_cli"))

    def test_two_unbound_claims_replay_after_restart(self):
        f, co, r = self.two_unbound()
        self.bind_and_claim("unbound", "codex", f=f, co=co, r=r)
        self.bind_and_claim("unbound-b", "claude_b", f=f, co=co, r=r, work_kind="review")
        fresh = Coordinator(co.root, f.ref, fixtures=True)
        states, data = fresh.state()
        self.assertEqual({k: s.value for k, s in states.items() if k.startswith("unbound")},
                         {"unbound": "LOCAL_RUNNING", "unbound-b": "PREPARING"})
        recorded = recorded_state(fresh, fresh.events())
        self.assertEqual(recorded[0]["unbound-b"], State.PREPARING)
        self.assertEqual(recorded[1]["unbound-b"], data["unbound-b"])
        # Both lanes resolve from the claims, and neither replay reports a
        # scheduling or gate-context contradiction.
        self.assertEqual(claimed_lanes(fresh, recorded[1])["unbound-b"], "claude_b")

    def test_recorded_state_replays_an_unbound_claim(self):
        self.publish()
        self.bind_call()
        value = json_ref(self.bind(), "binding")
        self.wait_past(value["bound_at"])
        info = self.co.claim("unbound")
        fresh = Coordinator(self.co.root, self.f.ref, fixtures=True)
        recorded = recorded_state(fresh, fresh.events())
        self.assertEqual(recorded[1]["unbound"], info)
        # The same gate context, so no false "claim gate context differs".
        gates = json_ref(info["gates"], "gates")
        self.assertEqual(gates["context"]["executor"], "codex")
        self.assertEqual(gates["context"]["executor_binding"], "codex")
        fresh._check_decision("unbound", info, {k: State(v) for k, v in gates["states"].items()},
                              {k: {} for k in fresh.nodes})
        with self.assertRaises(TypeError):
            # data is required-positional: a defaulted None would resolve a
            # different lane mapping than the claim was decided with.
            fresh._check_decision("unbound", info, {k: State(v) for k, v in gates["states"].items()})

    def test_codeg_with_an_unbound_node_is_refused(self):
        states = {node["id"]: State.PENDING for node in self.f.plan["nodes"]}
        queues = read_json(self.co.root / "run.json")["queues"]
        with self.assertRaisesRegex(ContractError, "codeg cannot schedule a node that is not in a lane queue"):
            select_ready(self.f.plan, states, "codeg", "/offline-scope", queues)
        # omo and serial still schedule the same opted-in plan.
        self.assertTrue(select_ready(self.f.plan, states, "omo", "/offline-scope", queues))
        self.assertTrue(select_ready(self.f.plan, states, "serial", "/offline-scope", queues))
        # A plan without the opt-in keeps today's codeg selection.
        plain = {key: value for key, value in self.f.plan.items() if key != "unbound_executor_policy"}
        plain["nodes"] = [node for node in self.f.plan["nodes"] if node["id"] != "unbound"]
        plain_states = {node["id"]: State.PENDING for node in plain["nodes"]}
        self.assertTrue(select_ready(plain, plain_states, "codeg", "/offline-scope", queues))

    def test_a_bound_campaign_keeps_its_exact_claim_and_context_shape(self):
        with tempfile.TemporaryDirectory() as other:
            plain = CampaignFixture(other, strategies=("omo",), version=3, arm="B")
            co = plain.start()
            states, data = co.state()
            lanes = claimed_lanes(co, data)
            self.assertEqual(lanes, {key: node["executor"] for key, node in co.nodes.items()})
            for strategy in ("serial", "omo", "codeg"):
                with self.subTest(strategy=strategy):
                    self.assertEqual(
                        select_ready(co.plan, states, strategy, co.root / "work", co.header["queues"]),
                        select_ready(co.plan, states, strategy, co.root / "work", co.header["queues"],
                                     lanes=lanes))
            info = co.claim("local")
            self.assertEqual(set(info), {"task_id", "attempt_id", "output_root", "mode", "acceptance_nonce",
                                         "native_start", "started_at", "gates", "decision", "dependencies",
                                         "work_request"})
            gates = json_ref(info["gates"], "gates")
            self.assertNotIn("executor_binding", gates["context"])
            self.assertEqual(gates["context"]["executor"], "codex")
            # A bound node that somehow carried the extra key fails replay.
            with self.assertRaisesRegex(ContractError, "claim fields changed"):
                co._check_claim("local", dict(info, executor_binding={}), State.LOCAL_RUNNING, {})
            states, data = co.state()
            self.assertEqual(claimed_lanes(co, data)["local"], "codex")

    def test_a_bound_lane_taken_before_the_claim_refuses_without_residue(self):
        self.publish()
        self.bind_call()
        self.bind()
        # Another node of this run claims the same lane between bind and claim.
        self.co.claim("local")
        self.assertIn("unbound", {node["id"] for node in self.co.ready()})
        with self.assertRaisesRegex(ContractError, "bound lane or write scope is held by another attempt"):
            self.co.claim("unbound")
        self.assertFalse((self.co.root / "claims" / "unbound.json").exists())
        self.assertFalse((self.co.root / "nodes" / "unbound" / "claim-gates.json").exists())
        self.assertFalse((self.co.root / "nodes" / "unbound" / "work-request.json").exists())
        self.assertEqual(self.co.state()[0]["unbound"], State.PENDING)
        self.assertEqual([event["node_id"] for event in self.co.events()], ["local"])

    def test_bind_refuses_a_publish_before_the_advice_read(self):
        advice = self.advice(transport=suggesting_transport, budget=dict(max_model_requests=80))
        self.publish(self.fields(advice_sha256=advice["advice_sha256"]))
        self.read(advice)
        self.bind_call()
        with self.assertRaisesRegex(ContractError, "DECISION_PUBLISH_ORDER"):
            self.bind()
        self.residue()

    def test_bind_recomputes_identity_in_code(self):
        self.publish()
        self.bind_call()
        partial = {lane: session for lane, session in self.f.campaign["sessions"].items() if lane != "claude_b"}
        # An identity that did not cover every original lane verifies no lane,
        # so no candidate exists, whatever the decision names.
        with patch.object(Coordinator, "_gate", return_value=partial):
            with self.assertRaisesRegex(ContractError, "actual route must come from the candidates"):
                self.bind()
        self.residue()
        self.assertIsNotNone(self.bind())

    def test_the_cli_action_binds_and_requires_its_decision(self):
        import contextlib
        import io
        import shlex
        self.publish()
        call = self.bind_call()
        frozen = str(Path(self.f.campaign["adapter"]["path"]) / "scripts/orchestration_run.py")
        argv = ["bind-unbound", "--run", self.run, "--node", "unbound", "--decision", call["path"]]
        prefix = ["--campaign", self.f.ref["path"], "--campaign-sha256", self.f.ref["sha256"]]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(prefix + argv[:-2])
        self.residue()
        out, err = io.StringIO(), io.StringIO()
        with patch("orchestration_run.Runner", return_value=self.r), patch("orchestration_run.__file__", frozen), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(prefix + argv)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertEqual(json.loads(out.getvalue()), file_ref(binding_path(self.co.root, "unbound")))
        # The generated script runs exactly this argv, in this order.
        inner = call["script"][len("text(await tools.exec_command("):-len("));\n")]
        self.assertEqual(shlex.split(json.loads(inner)["cmd"])[-len(argv):], argv)

    def two_unbound(self):
        """A second fixture whose plan carries two deliberately unbound nodes."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        f = self.fixture(extra_unbound=True, base=directory.name)
        co = f.start()
        self.assertEqual(read_json(co.root / "run.json")["unbound_nodes"], ["unbound", "unbound-b"])
        return f, co, Runner(f.ref, fixtures=True)

    def bind_and_claim(self, node_id, route, *, f, co, r, work_kind="coding"):
        self.publish(self.fields(work_kind=work_kind, actual_route=route), node_id=node_id,
                     runner=r, co=co, f=f)
        self.bind_call(node_id=node_id, co=co, f=f)
        value = json_ref(self.bind(node_id=node_id, co=co, f=f), "binding")
        self.wait_past(value["bound_at"])
        return co.claim(node_id)


class BindClosureTests(unittest.TestCase):
    def test_the_bind_path_keeps_the_publisher_and_worker_outside_the_adapter_closure(self):
        # validate_adapter walks deferred imports too, so a bind-time import of
        # the publisher would carry it into every frozen adapter (design Q9).
        from orchestration_adapter import validate_adapter
        from orchestration_coordinator import ADVICE_BODY_SCHEMA
        from upgrade_advice_publish import SCHEMA
        self.assertEqual(ADVICE_BODY_SCHEMA, SCHEMA)
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as other:
            root = Path(other).resolve() / "source"
            (root / "scripts").mkdir(parents=True)
            for path in scripts.glob("*.py"):
                shutil.copyfile(path, root / "scripts" / path.name)
            closure = validate_adapter(tree_manifest(root), purpose="campaign")
        self.assertIn("scripts/upgrade_advice_chain.py", closure)
        for outside in ("upgrade_advice_publish", "jev_advice_worker"):
            self.assertNotIn("scripts/" + outside + ".py", closure)


if __name__ == "__main__":
    unittest.main()
