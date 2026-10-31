"""Durable CLI orchestration. Scheduling never sends messages or infers success.

All three strategies use the same transport and verifier. Unknown delivery
retains both its lane and scope. Restarting a coordinator cannot resend it.
"""
from __future__ import annotations

import os
import secrets
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from decision_provider import UNBOUND_PENDING_BIND, Decision, Outcome, decide
from orchestration_common import (ContractError, boot_id, checked_path, contract_errors, count, exclusive_lock, file_ref,
                                  json_ref, object_sha, parse_time, read_json, read_ref, scopes_conflict,
                                  utc_now, verify_tree, write_json)
import orchestration_binding
from orchestration_binding import (bind_facts, binding_path, binding_ref, bound_lane, claim_lane, claimed_lanes,
                                   eligible_candidates, is_unbound, lane_facts, read_binding, resolved_mode,
                                   resolved_transport, unbound_nodes, validate_decision,
                                   write_binding)
from orchestration_campaign import (control_script, find_control, native_control, node_output, node_write_scope,
                                    require_live_identity, validate_campaign)
from orchestration_contracts import campaign_version, evidence_class
from orchestration_native import NativeReader, native_verification, require
from orchestration_planner import ID_RE, executor_providers, validate_plan

STRATEGIES = ("serial", "omo", "codeg")
# The advice publisher stays outside the adapter closure (design Q9), and
# validate_adapter walks deferred imports too, so the published body's schema
# is mirrored here instead of imported; a test pins it to the publisher's value.
ADVICE_BODY_SCHEMA = "upgrade-advice-body-v1"


class State(str, Enum):
    PENDING = "PENDING"
    PREPARING = "PREPARING"
    PREPARED = "PREPARED"
    DISPATCH_INTENT = "DISPATCH_INTENT"
    UNCERTAIN = "UNCERTAIN"
    DISPATCHED = "DISPATCHED"
    LOCAL_RUNNING = "LOCAL_RUNNING"
    EVIDENCE_READY = "EVIDENCE_READY"
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"
    FAILED_PRESERVED = "FAILED_PRESERVED"


ACTIVE = set(State) - {State.PENDING, State.ACCEPTED, State.FAILED_PRESERVED}
TRANSITIONS = {
    State.PENDING: {State.PREPARING, State.LOCAL_RUNNING},
    State.PREPARING: {State.PREPARED, State.BLOCKED},
    State.PREPARED: {State.DISPATCH_INTENT, State.BLOCKED},
    State.DISPATCH_INTENT: {State.DISPATCHED, State.UNCERTAIN},
    State.UNCERTAIN: {State.DISPATCHED, State.BLOCKED},
    State.DISPATCHED: {State.EVIDENCE_READY, State.BLOCKED},
    State.LOCAL_RUNNING: {State.EVIDENCE_READY, State.BLOCKED},
    State.EVIDENCE_READY: {State.ACCEPTED, State.BLOCKED},
    State.BLOCKED: {State.FAILED_PRESERVED},
    State.ACCEPTED: set(),
    State.FAILED_PRESERVED: set(),
}


def topological_nodes(plan):
    remaining = list(plan["nodes"])
    ordered = []
    while remaining:
        ready = next((n for n in remaining if set(n["depends_on"]) <= {x["id"] for x in ordered}), None)
        require(ready is not None, "dependency cycle")
        remaining.remove(ready)
        ordered.append(ready)
    return ordered


def select_ready(plan, states, strategy, scope_root, queues, *, lanes=None):
    """Select schedulable nodes; ``lanes`` resolves a deliberately unbound node.

    ``lanes=None`` reproduces the frozen-plan comparison exactly. With a mapping
    (always built from frozen claim records, never from live disk state) the
    lane conflict compares *resolved* lanes: a claimed unbound node conflicts on
    the lane its claim pinned, like any bound node, and two nodes with no lane
    yet do not collide on ``None == None``. A node bound but not yet claimed
    has no lane here; ``Coordinator.claim`` refuses it before any write when
    its bound lane is already held.
    """
    require(strategy in STRATEGIES or plan.get("version") == 2 and strategy == "mac_native",
            "unsupported strategy")
    nodes = topological_nodes(validate_plan(plan))
    accepted = {key for key, value in states.items() if value == State.ACCEPTED}
    busy = [n for n in nodes if states[n["id"]] in ACTIVE]
    pending = [n for n in nodes if states[n["id"]] == State.PENDING]
    lane_of = (lambda node: node["executor"]) if lanes is None else (
        lambda node: lanes.get(node["id"], node["executor"]))
    # Without a mapping the comparison stays exactly today's, including two
    # unresolved nodes colliding on None == None: a call site that forgets
    # ``lanes`` must fail loudly in replay, not pass with new semantics.
    unresolved_conflicts = lanes is None
    if strategy == "serial":
        pending = pending[:1] if not busy else []
    if strategy == "codeg":
        # An unbound node joins no lane FIFO queue by design, so codeg could
        # never schedule it. That was a silent empty selection; it is a typed
        # refusal now. No validated v3 campaign can present this combination
        # (every run's strategy is mac_native or omo), so this is reachable
        # only through a direct module-level call.
        require(not (orchestration_binding.unbound_plan(plan) and unbound_nodes(plan)),
                "codeg cannot schedule a node that is not in a lane queue")
        # Persistent FIFO per original session, never a queue of terminal input.
        heads = set()
        for queue in queues.values():
            head = next((key for key in queue if key not in accepted), None)
            if head:
                heads.add(head)
        pending = [n for n in pending if n["id"] in heads]
    selected = []
    for node in pending:
        if not set(node["depends_on"]) <= accepted:
            continue
        # A1 exposes all individually eligible choices to the native supervisor.
        # Its next actual claim rechecks the updated state and lease registry.
        occupied = busy if strategy == "mac_native" else busy + selected
        lane = lane_of(node)
        if any(((lane is not None or unresolved_conflicts) and lane_of(other) == lane) or
               scopes_conflict(other["write_scope"], node["write_scope"], scope_root) for other in occupied):
            continue
        selected.append(node)
    return selected


def strategy_probe(plan):
    """Exercise the actual selection algorithm with estimates, never live time."""
    result = {}
    queues = {lane: [n["id"] for n in topological_nodes(plan) if n["executor"] == lane]
              for lane in executor_providers(plan)}
    for strategy in STRATEGIES:
        states = {n["id"]: State.PENDING for n in plan["nodes"]}
        now, active, trace = 0.0, {}, []
        while any(s != State.ACCEPTED for s in states.values()):
            for node in select_ready(plan, states, strategy, "/offline-scope", queues):
                states[node["id"]] = State.LOCAL_RUNNING
                active[node["id"]] = now + node["expected_seconds"]
                trace.append(dict(node_id=node["id"], start=now, end=active[node["id"]]))
            require(active, "workload cannot make scheduling progress")
            now = min(active.values())
            for key in [k for k, t in active.items() if t == now]:
                states[key] = State.ACCEPTED
                del active[key]
        result[strategy] = dict(estimated_seconds=now, trace=trace)
    return dict(evidence_class="offline_schedule_estimate", strategies=result,
                distinguishes_all=len({object_sha(x["trace"]) for x in result.values()}) == 3)


class LeaseRegistry:
    """Cross-run session/scope reservations; releases retain their history."""
    def __init__(self, root):
        self.root = checked_path(root)

    def read(self):
        path = self.root / "leases.json"
        value = read_json(path) if path.exists() else {}
        for owner, lease in value.items():
            require(isinstance(owner, str) and owner and isinstance(lease, dict), "malformed lease registry")
            require(set(lease) == {"session_id", "scope", "claim", "reserved_at", "released_at", "released_by"}, "malformed lease fields")
            require(isinstance(lease["session_id"], str) and lease["session_id"], "lease session missing")
            checked_path(lease["scope"])
            read_ref(lease["claim"], "original lease claim")
            reserved = parse_time(lease["reserved_at"])
            if lease["released_at"] is not None:
                require(parse_time(lease["released_at"]) >= reserved, "lease release predates reservation")
                read_ref(lease["released_by"], "lease terminal event")
            else:
                require(lease["released_by"] is None, "unreleased lease has a terminal receipt")
        return value

    def reserve(self, owner, session, scope, claim):
        with exclusive_lock(self.root / "registry.lock", wait_seconds=5):
            path = self.root / "leases.json"
            checked_path(scope)
            read_ref(claim, "original claim")
            value = self.read()
            if owner in value:
                raise ContractError("existing lease is observe-only; do not repeat a claim")
            for lease in value.values():
                if lease["released_at"] is None and (lease["session_id"] == session or scopes_conflict(lease["scope"], scope, "/")):
                    raise ContractError("original session or write scope is already reserved")
            value[owner] = dict(session_id=session, scope=scope, claim=claim,
                                reserved_at=utc_now(), released_at=None, released_by=None)
            write_json(path, value)

    def verify_original(self, owner, original):
        """Read-only reservation comparison; never replay run/failure history.

        The snapshot records the unreleased reservation. Only released_at and
        released_by may advance; callers independently verify the terminal event.
        A release retains the exact original reserved_at, session, scope and claim.
        """
        fields = {"session_id", "scope", "claim", "reserved_at", "released_at", "released_by"}
        require(isinstance(original, dict) and set(original) == fields and
                original["released_at"] is None and original["released_by"] is None,
                "maintenance requires its original unreleased lease snapshot")
        current = self.read().get(owner)
        require(current is not None and all(current[k] == original[k] for k in
                fields - {"released_at", "released_by"}), "original lease reservation identity changed")
        return current

    def release(self, owner, *, session, scope, claim, terminal, reserved_at):
        with exclusive_lock(self.root / "registry.lock", wait_seconds=5):
            path = self.root / "leases.json"
            value = self.read()
            require(owner in value, "original lease missing")
            require(all(value[owner][k] == v for k, v in dict(session_id=session, scope=scope, claim=claim).items()),
                    "original lease ownership/session/scope/claim changed")
            require(value[owner]["reserved_at"] == reserved_at,
                    "original lease reservation identity changed before release")
            event = json_ref(terminal, "verified terminal event")
            require(event.get("state") in (State.ACCEPTED, State.FAILED_PRESERVED) and
                    event.get("data", {}).get("verification", {}).get("task_id") == owner,
                    "lease release requires its verified terminal event")
            if value[owner]["released_at"] is None:
                value[owner]["released_at"] = utc_now()
                value[owner]["released_by"] = terminal
                write_json(path, value)
            else:
                require(value[owner]["released_by"] == terminal, "lease was released by another terminal event")


class Coordinator:
    @contract_errors("coordinator")
    @native_verification
    def __init__(self, root, campaign_ref, *, fixtures=False):
        self.root = checked_path(root)
        self.campaign_ref = campaign_ref
        self.campaign = validate_campaign(campaign_ref, fixtures=fixtures)
        self.fixtures = fixtures
        require(self.campaign.get("evidence_class") == evidence_class(self.campaign, "cli_campaign", fixtures),
                "campaign execution class mismatch")
        self.plan = validate_plan(json_ref(self.campaign["plan"], "frozen plan"))
        self.nodes = {n["id"]: n for n in self.plan["nodes"]}
        self.header = read_json(self.root / "run.json")
        live_v3 = campaign_version(self.campaign) == 3 and not fixtures
        # The field set is exact, not a superset, so a new key fails here at
        # construction. It grows only for a plan that opted in, which is why
        # every existing run.json still verifies with today's bytes.
        require(set(self.header) == {"version", "campaign", "run", "queues", "started_at",
                                    "created_at", "started_monotonic_ns", "boot_id", "native_start", "start_native"}
                                    | ({"run_permit", "launch_start"} if live_v3 else set())
                                    | ({"unbound_nodes"} if orchestration_binding.unbound_plan(self.plan) else set()),
                "run header fields changed")
        require(type(self.header["version"]) is int and self.header["version"] == campaign_version(self.campaign) and
                self.header["campaign"] == campaign_ref, "run belongs to another frozen campaign")
        require(self.header["run"] in self.campaign["runs"], "run is not in frozen order")
        self.spec = self.header["run"]
        if live_v3:
            from orchestration_permit import campaign_permit, launch_start
            _, permit = campaign_permit(self.campaign, self.spec["run_id"], ref=self.header["run_permit"])
            require(parse_time(self.header["started_at"]) >= parse_time(permit["issued_at"]),
                    "native run started before its registered permit")
            require(self.header["launch_start"] == launch_start(self.campaign, permit), "cold launch clock changed")
            if self.header["launch_start"] is not None:
                require(parse_time(json_ref(self.header["launch_start"])["requested_at"]) <=
                        parse_time(self.header["started_at"]), "cold launch follows native run start")
        require(self.root == Path(self.campaign["root"]) / "runs" / self.spec["run_id"], "run root changed")
        queues = {lane: [n["id"] for n in topological_nodes(self.plan) if n["executor"] == lane]
                  for lane in self.campaign["sessions"]}
        require(self.header["queues"] == queues, "frozen FIFO queues changed")
        # The queues keep excluding an unbound node (it is in no lane FIFO).
        # The exclusion stops being silent: the header states which nodes it is.
        if orchestration_binding.unbound_plan(self.plan):
            require(self.header["unbound_nodes"] == unbound_nodes(self.plan),
                    "frozen unbound nodes changed")
        count(self.header["started_monotonic_ns"], "run clock")
        require(isinstance(self.header["boot_id"], str) and self.header["boot_id"], "run boot identity missing")
        codex_reader = NativeReader(self.campaign["sessions"]["codex"], fixtures=fixtures)
        started = native_control(codex_reader, self.header["start_native"],
                                 control_script(self.campaign, campaign_ref, "start", self.spec["run_id"]))
        require(parse_time(self.header["started_at"]) == started <= parse_time(self.header["created_at"]),
                "run start is not the original native invocation")
        require(set(self.header["native_start"]) == set(self.campaign["sessions"]), "native run lanes changed")
        for lane, session in self.campaign["sessions"].items():
            if lane == "codex":
                reader = codex_reader
                reader.verify_metadata()
            else:
                reader = NativeReader(session, fixtures=fixtures)
            require(reader.verify_pin(self.header["native_start"][lane]) >= reader.verify_pin(session["freeze_fence"]),
                    "run predates campaign freeze")
        start_ref = self.header["start_native"]
        require(start_ref["offset"] >= self.campaign["sessions"]["codex"]["freeze_fence"]["offset"] and
                start_ref["offset"] + start_ref["length"] <= self.header["native_start"]["codex"]["offset"],
                "native start call outside campaign/run fences")
        self.leases = LeaseRegistry(self.campaign["lease_root"])

    def _work_budget(self):
        if campaign_version(self.campaign) == 3 and not self.fixtures:
            from orchestration_permit import work_budget
            work_budget(self.campaign, self.header)
            runtime = json_ref(self.campaign["runtime"])
            startup = json_ref(runtime["startup"])
            require(not os.path.lexists(startup["stop_path"]), "experiment Stop forbids new work")
        if campaign_version(self.campaign) == 3:
            # Keep original halt/deadline/Stop diagnostics ahead of event
            # inspection. Otherwise collect original results after a business
            # failure, while refusing fresh claims/preparation/input. This
            # action boundary cannot cancel work already in flight.
            require(not any(e["state"] in (State.BLOCKED.value, State.FAILED_PRESERVED.value)
                            for e in self.events()), "preserved run failure forbids new work")

    @contextmanager
    def state_lock(self, *, new_work=False):
        """v3 shared writers wait; v2 and node action locks retain fail-fast semantics."""
        if campaign_version(self.campaign) == 2:
            with exclusive_lock(self.root / "coordinator.lock"):
                yield
        else:
            from orchestration_lock import coordinator_lock, new_work_guard
            with coordinator_lock(self.root / "coordinator.lock",
                                  remaining=new_work_guard(self) if new_work else None):
                yield

    def _gate(self, *, new_work=False):
        validate_campaign(self.campaign_ref, fixtures=self.fixtures)
        require(not (self.root / "finish.json").exists(), "run is closed; original evidence is immutable")
        if campaign_version(self.campaign) == 3 and not self.fixtures:
            from orchestration_permit import campaign_permit
            campaign_permit(self.campaign, self.spec["run_id"], ref=self.header["run_permit"], active=True)
        if new_work:
            self._work_budget()
        if not self.fixtures:
            return require_live_identity(self.campaign)
        return self.campaign["sessions"]

    @classmethod
    @contract_errors("create run")
    def create(cls, root, campaign_ref, spec, *, fixtures=False):
        root = checked_path(root)
        campaign = validate_campaign(campaign_ref, fixtures=fixtures)
        require(spec in campaign["runs"] and ID_RE.fullmatch(spec["run_id"]) is not None, "unknown run")
        plan = validate_plan(json_ref(campaign["plan"]))
        require(spec["strategy"] in STRATEGIES or campaign_version(campaign) == 3 and
                spec["strategy"] == "mac_native", "invalid strategy")
        require(root == Path(campaign["root"]) / "runs" / spec["run_id"], "run must use its frozen directory")
        if not fixtures:
            require_live_identity(campaign)
        permit_fields = {}
        if campaign_version(campaign) == 3 and not fixtures:
            from orchestration_permit import campaign_permit, launch_start, require_running
            require_running(campaign["registration"])
            permit_ref, permit = campaign_permit(campaign, spec["run_id"], active=True)
            permit_fields = dict(run_permit=permit_ref, launch_start=launch_start(campaign, permit))
        reader = NativeReader(campaign["sessions"]["codex"], fixtures=fixtures)
        script = control_script(campaign, campaign_ref, "start", spec["run_id"])
        proof = find_control(reader, script, after=campaign["sessions"]["codex"]["freeze_fence"]["offset"])
        started_at = native_control(reader, proof, script).isoformat()
        if permit_fields:
            require(parse_time(started_at) >= parse_time(permit["issued_at"]), "native start predates registered permit")
        with exclusive_lock(Path(campaign["root"]) / "campaign.lock"):
            for previous in campaign["runs"][:campaign["runs"].index(spec)]:
                path = Path(campaign["root"]) / "runs" / previous["run_id"] / "finish.json"
                require(path.exists(), "earlier frozen trial is unfinished")
                from orchestration_verify import verify_run
                verify_run(campaign_ref, previous["run_id"], fixtures=fixtures)
            root.mkdir(parents=True, exist_ok=False)
            ordered = topological_nodes(plan)
            queues = {lane: [n["id"] for n in ordered if n["executor"] == lane] for lane in campaign["sessions"]}
            unbound = dict(unbound_nodes=unbound_nodes(plan)) if orchestration_binding.unbound_plan(plan) else {}
            starts = {lane: NativeReader(session, fixtures=fixtures).snapshot() for lane, session in campaign["sessions"].items()}
            header = dict(version=campaign_version(campaign), campaign=campaign_ref, run=spec, queues=queues, started_at=started_at,
                          created_at=utc_now(), started_monotonic_ns=time.monotonic_ns(), boot_id=boot_id(), native_start=starts,
                          start_native=proof, **unbound, **permit_fields)
            write_json(root / "run.json", header, exclusive=True)
        return cls(root, campaign_ref, fixtures=fixtures)

    @contract_errors("event history")
    def events(self):
        directory = self.root / "events"
        paths = sorted(directory.glob("*.json"))
        require([p.name for p in paths] == [f"{i:06d}.json" for i in range(1, len(paths) + 1)], "event history omitted or malformed")
        previous = object_sha(self.header)
        result, last_clock, last_boot = [], self.header["started_monotonic_ns"], self.header["boot_id"]
        last_at = parse_time(self.header["created_at"])
        for index, path in enumerate(paths, 1):
            event = read_json(path)
            require(set(event) == {"seq", "previous", "at", "monotonic_ns", "boot_id", "clock_transition", "node_id", "state", "data"},
                    "unknown persisted event fields")
            require(type(event.get("seq")) is int and event["seq"] == index and event.get("previous") == previous, "event chain changed")
            clock = count(event.get("monotonic_ns"), "event clock")
            require(isinstance(event["boot_id"], str) and event["boot_id"], "event boot identity missing")
            if event["boot_id"] == last_boot:
                require(clock >= last_clock and event["clock_transition"] is None, "same-boot clock or event order changed")
            else:
                require(event["clock_transition"] == dict(from_boot=last_boot, to_boot=event["boot_id"],
                                                         previous_monotonic_ns=last_clock), "cross-boot transition missing")
            require(parse_time(event["at"]) >= last_at, "event wall clock moved backwards")
            require(event["node_id"] in self.nodes and event["state"] in State._value2member_map_ and
                    isinstance(event["data"], dict), "invalid persisted lifecycle fields")
            previous, last_clock = object_sha(event), event["monotonic_ns"]
            last_boot = event["boot_id"]
            last_at = parse_time(event["at"])
            result.append(event)
        return result

    @contract_errors("run lifecycle")
    @native_verification
    def state(self):
        states = {key: State.PENDING for key in self.nodes}
        data = {key: {} for key in self.nodes}
        for event in self.events():
            key, target = event["node_id"], State(event["state"])
            require(key in states and target in TRANSITIONS[states[key]], "illegal persisted lifecycle transition")
            if states[key] == State.PENDING:
                require(not any(s in (State.BLOCKED, State.FAILED_PRESERVED) for s in states.values()),
                        "new claim after preserved run failure")
                require(key in {n["id"] for n in select_ready(self.plan, states, self.spec["strategy"],
                                                             self.root / "work", self.header["queues"],
                                                             lanes=claimed_lanes(self, data))},
                        "persisted claim violates dependencies/session/scope/queue")
                self._check_claim(key, event["data"], target, data)
                self._check_decision(key, event["data"], states, data)
                require(parse_time(event["data"]["started_at"]) <= parse_time(event["at"]), "claim begins after event")
                intent = json_ref(file_ref(self.root / "claims" / (key + ".json")))
                require(intent == event["data"], "claim intent changed or was not persisted")
            else:
                require(not set(event["data"]) & set(data[key]), "immutable lifecycle data was overwritten")
                require(set(event["data"]) == self._fields(target), "unexpected transition evidence")
                self._check_transition(key, target, event["data"], data[key], replay=True)
            states[key] = target
            data[key].update(event["data"])
        for key, info in data.items():
            if info:
                lease = self._lease(key, info)
                if states[key] in ACTIVE:
                    require(lease["released_at"] is None, "active/uncertain lease was prematurely released")
                elif lease["released_at"] is not None:
                    event = next(e for e in reversed(self.events()) if e["node_id"] == key)
                    terminal = file_ref(self.root / "events" / f"{event['seq']:06d}.json")
                    require(lease["released_by"] == terminal,
                            "terminal lease released by a different event")
                    if states[key] == State.FAILED_PRESERVED and json_ref(info["failure"])["boundary"] == "preparation_exited":
                        from orchestration_preparation import verify_release_check
                        verify_release_check(self, key, info, terminal, lease)
        return states, data

    @staticmethod
    def _fields(target):
        return {State.PREPARED: {"prepared"}, State.DISPATCH_INTENT: {"dispatch_intent"},
                State.UNCERTAIN: {"uncertainty"}, State.DISPATCHED: {"delivery"},
                State.EVIDENCE_READY: {"candidate"}, State.ACCEPTED: {"accepted_evidence", "verification"},
                State.BLOCKED: {"blocker"}, State.FAILED_PRESERVED: {"failure", "verification"}}.get(target, set())

    def verify_claimed_binding(self, key, pin, info):
        """Re-derive an unbound node's binding from frozen evidence.

        A binding file is an ordinary file in a run tree both the supervisor and
        the runner can write, so "the claim pins a binding" is not by itself a
        proof that ``bind_unbound`` wrote it. This runs the same join the bind
        ran, and then the one check the bind could not run yet: the bind control
        call's own terminal rc0 result, which prints the created binding's
        path, bytes and sha256. ``bind-unbound`` creates that record with an
        exclusive create, so only the call that actually created *this* binding
        can have printed *this* pin - a refused attempt never does.

        That is what makes the route, the eligible lanes and the recomputed
        lane facts inside the record evidence rather than self-report. It runs
        on the claim itself and on every ``state()`` replay, so a binding that
        appeared beside the run tree by any other means is refused at the claim
        instead of being executed, replayed and accepted.
        """
        from upgrade_advice_chain import advice_chain
        readers = {"codex": NativeReader(self.campaign["sessions"]["codex"], fixtures=self.fixtures)}
        binding = json_ref(pin, "claimed executor binding")
        chain = advice_chain(self, key, info, readers, registration=None, binding_ref=pin,
                             not_after=parse_time(binding["bound_at"]).timestamp())
        require(chain["decision_publish_natively_joined"] is True,
                "the claimed binding's decision has no joined native publisher call and result")
        require(chain["bind_natively_verified"] is True,
                "the claimed binding does not pin its own native bind control call")
        require(chain["bind_result_joined"] is True,
                "the bind control call has no result that created this binding yet; claim after it returns")
        require(chain["arm"] == "advisory-off" or chain["read"] is not None,
                "the on arm is claimed only after one complete original read of its advice")
        return chain

    def _check_claim(self, key, info, target, prior):
        from orchestration_evidence import dependency_bindings, task_identity, work_request
        node = self.nodes[key]
        unbound = is_unbound(node)
        # Exactly one extra key, and only for a node frozen unbound, so every
        # existing claim keeps verifying byte for byte and a bound node that
        # somehow carried the key still fails here.
        require(set(info) == {"task_id", "attempt_id", "output_root", "mode", "acceptance_nonce", "native_start", "started_at", "gates", "decision", "dependencies", "work_request"}
                | ({"executor_binding"} if unbound else set()),
                "claim fields changed")
        expected = task_identity(self.campaign, self.header, node)
        require(all(info.get(k) == expected[k] for k in ("task_id", "attempt_id")), "claim task/attempt changed")
        if unbound:
            pin = info["executor_binding"]
            require(isinstance(pin, dict) and pin.get("path") == str(binding_path(self.root, key)),
                    "claim pins another executor binding")
            # Read through the claim's own pin: a binding edited after the claim
            # fails the pin here instead of re-routing the claim.
            binding = json_ref(pin, "claimed executor binding")
            require(binding.get("run_id") == self.spec["run_id"] and binding.get("node_id") == key and
                    binding.get("campaign") == self.campaign_ref, "claimed executor binding belongs to another run")
            lane = claim_lane(node, info)
            mode = resolved_mode(self.campaign, lane)
            require(binding.get("mode") == binding.get("execution_kind") == mode and
                    binding.get("transport") == resolved_transport(self.campaign, lane),
                    "claimed executor binding resolved another execution ownership")
            # Not "a binding exists" but "bind_unbound wrote this binding":
            # re-derived here, so every replay re-derives it too.
            self.verify_claimed_binding(key, pin, info)
        else:
            lane = node["executor"]
            mode = "local_cli" if node["executor"] == "codex" else "delegated_cli"
        require(orchestration_binding.claim_mode_matches_frozen_kind(node, info, mode) and
                target == (State.LOCAL_RUNNING if mode == "local_cli" else State.PREPARING), "execution ownership changed")
        require(info["output_root"] == str(node_output(self.root, node)), "claim output scope changed")
        require(info["dependencies"] == dependency_bindings(node, prior), "claim dependency acceptance changed")
        require(info["work_request"]["path"] == str(self.root / "nodes" / key / "work-request.json") and
                json_ref(info["work_request"]) == work_request(self.campaign, self.header, node, info), "claim work request changed")
        require(isinstance(info["acceptance_nonce"], str) and len(info["acceptance_nonce"]) == 24 and
                all(c in "0123456789abcdef" for c in info["acceptance_nonce"]), "claim nonce invalid")
        reader = NativeReader(self.campaign["sessions"][lane], fixtures=self.fixtures)
        require(reader.verify_pin(info["native_start"]) >= self.header["native_start"][lane]["offset"],
                "claim predates original run")
        require(parse_time(info["started_at"]) >= parse_time(self.header["created_at"]), "claim predates run creation")

    def _check_decision(self, key, info, states, data):
        """Replay the claim's gate decision.

        ``data`` is the claim data accumulated *before* this claim and is a
        required positional argument on purpose: a defaulted None at any caller
        would resolve a different lane mapping than the claim was decided with
        and report a false "claim gate context differs from verified evidence".
        """
        gates = json_ref(info["gates"], "claim hard-gate evidence")
        require(gates["campaign"] == self.campaign_ref and gates["authorization"] == self.campaign["authorization"],
                "claim authorization evidence changed")
        read_ref(gates["authorization"])
        require(gates["identity_source"] == ("offline_fixture" if self.fixtures else "live_kernel_and_native_foreground"),
                "claim identity provenance changed")
        for lane, actual in gates["identity"].items():
            require(lane in self.campaign["sessions"] and
                    all(self.campaign["sessions"][lane].get(k) == v for k, v in actual.items()),
                    "claim original identity changed")
        require(set(gates["identity"]) == set(self.campaign["sessions"]), "claim identity lanes missing")
        require(gates["states"] == {k: v.value for k, v in states.items()} and
                gates["scope"] == info["output_root"] and gates["inputs"] == self.campaign["inputs"],
                "claim scope/dependency/input gates changed")
        for ref in gates["inputs"]:
            read_ref(ref)
        # The lane comes from the claim's own binding pin, never from the plan
        # alone or from a binding file read now; the lane mapping comes from
        # the claims recorded before this one, exactly as claim() used them.
        lane = claim_lane(self.nodes[key], info)
        context = self._decision_context(key, gates, lane=lane, lanes=claimed_lanes(self, data))
        require(gates["context"] == context, "claim gate context differs from verified evidence")
        value = info["decision"]
        parsed = Decision(Outcome(value["outcome"]), value["reason"], value["confidence"],
                          tuple(value["evidence_refs"]), value["route"], value["provider"])
        require(parsed.record() == value and parsed.outcome is Outcome.ALLOW and parsed.route == lane,
                "claim decision is invalid")
        if not self.fixtures or parsed.provider == "rules":
            require(parsed.record() == decide(context).record(), "claim decision/provenance differs from frozen rules")

    def _decision_context(self, key, gates, *, lane, lanes):
        """The gate context ``decide`` sees; recomputed byte for byte on replay.

        ``lane`` is the node's resolved lane (its frozen executor, or the lane
        its binding names, or None for an unbound node with no binding).
        ``context["executor_binding"]`` exists only for a node frozen unbound,
        so every recorded context of a bound node stays exactly as it was.
        """
        authorization = json_ref(gates["authorization"])
        identity = gates["identity"]
        ready = select_ready(self.plan, {k: State(v) for k, v in gates["states"].items()},
                             self.spec["strategy"], self.root / "work", self.header["queues"], lanes=lanes)
        context = dict(purpose="route", executor=lane, available_executors=sorted(identity),
                    authorized=authorization.get("approved_root") == self.campaign["root"],
                    identity_verified=set(identity) == set(self.campaign["sessions"]) and all(
                        all(self.campaign["sessions"][lane].get(k) == v for k, v in actual.items())
                        for lane, actual in identity.items()),
                    scope_valid=key in {n["id"] for n in ready} and gates["scope"] == str(node_output(self.root, self.nodes[key])),
                    evidence_verified=gates["inputs"] == self.campaign["inputs"] and gates["campaign"] == self.campaign_ref,
                    evidence_refs=[gates["campaign"]["path"] + "#" + gates["campaign"]["sha256"],
                                   gates["authorization"]["path"] + "#" + gates["authorization"]["sha256"]])
        # Historical v2 decisions retain their exact recorded context.
        if campaign_version(self.campaign) == 3:
            context["executor_providers"] = executor_providers(self.plan)
        if is_unbound(self.nodes[key]):
            context["executor_binding"] = UNBOUND_PENDING_BIND if lane is None else lane
        return context

    def _lease(self, key, info):
        lease = self.leases.read().get(info["task_id"])
        lane = claim_lane(self.nodes[key], info)
        require(lease is not None and lease["session_id"] == self.campaign["sessions"][lane]["session_id"] and
                lease["scope"] == str(node_write_scope(self.root, self.nodes[key])) and
                lease["claim"] == file_ref(self.root / "claims" / (key + ".json")),
                "original lease ownership/session/scope/claim changed")
        require(parse_time(lease["reserved_at"]) >= parse_time(info["started_at"]), "lease predates claim")
        # Do not call validate_maintenance/state here: terminal replay already
        # verifies that manifest through the failure evidence. This leaf check
        # also protects BLOCKED state before a failure candidate has been written.
        path = self.root / "nodes" / key / "maintenance/manifest.json"
        if path.exists() or path.is_symlink():
            manifest = json_ref(file_ref(path))
            require(manifest["campaign"] == self.campaign_ref and manifest["run_id"] == self.spec["run_id"] and
                    manifest["node_id"] == key and manifest["claim"] == lease["claim"],
                    "maintenance lease belongs to another original claim")
            lease = self.leases.verify_original(info["task_id"], manifest["lease"])
        return lease

    @native_verification
    def _check_transition(self, key, target, extra, info, *, replay=False):
        from orchestration_evidence import (validate_candidate, verify_prepared, verify_dispatch_intent,
                                            verify_dispatched, verify_node, verify_failure)
        if target == State.PREPARED:
            verify_prepared(self.campaign, self.header, self.nodes[key], info, extra["prepared"], fixtures=self.fixtures)
        elif target == State.DISPATCH_INTENT:
            verify_dispatch_intent(self.campaign, self.header, self.nodes[key], info, extra["dispatch_intent"], fixtures=self.fixtures)
        elif target == State.DISPATCHED:
            verify_dispatched(self.campaign, self.header, self.nodes[key], info, extra["delivery"], fixtures=self.fixtures)
        elif target == State.EVIDENCE_READY:
            # Candidate publication is not acceptance. Its original checker
            # result stays pinned; the gated ACCEPT below independently reruns it.
            validate_candidate(self.campaign, self.header, self.nodes[key], info, extra["candidate"],
                               fixtures=self.fixtures, rerun=False)
        elif target == State.ACCEPTED:
            value = verify_node(self.campaign, self.header, self.nodes[key], info,
                                json_ref(extra["accepted_evidence"]), fixtures=self.fixtures,
                                rerun=not replay)
            # The synchronous checker may take time. Recheck the exact original
            # evidence pins before publishing acceptance, not just their earlier
            # decoded values. No replacement evidence or extra checker run.
            json_ref(info["candidate"], "candidate after independent verification")
            json_ref(extra["accepted_evidence"], "acceptance after independent verification")
            require(value == extra["verification"], "accepted verification differs from independent evidence")
        elif target == State.FAILED_PRESERVED:
            value = verify_failure(self.campaign, self.header, self.nodes[key], info, extra["failure"], fixtures=self.fixtures)
            require(value == extra["verification"], "failure settlement differs from verified evidence")
        else:
            field = "blocker" if target == State.BLOCKED else "uncertainty"
            value = json_ref(extra[field], field)
            require(value.get("task_id") == info["task_id"] and isinstance(value.get("reason"), str) and value["reason"],
                    "bound failure observation required")

    def _append(self, node_id, target, data):
        events = self.events()
        states, current = self.state()
        require(node_id in states and target in TRANSITIONS[states[node_id]], "illegal lifecycle transition")
        require(isinstance(data, dict), "transition data must be an object")
        if states[node_id] != State.PENDING:
            require(not set(data) & set(current[node_id]) and set(data) == self._fields(target),
                    "immutable claim data or unexpected transition evidence")
            self._check_transition(node_id, target, data, current[node_id])
        previous_event = events[-1] if events else self.header
        current_boot = boot_id()
        previous_clock = previous_event.get("monotonic_ns", self.header["started_monotonic_ns"])
        current_clock = count(time.monotonic_ns(), "event clock")
        current_at = utc_now()
        require(isinstance(current_boot, str) and current_boot, "event boot identity missing")
        if current_boot == previous_event["boot_id"]:
            require(current_clock >= previous_clock, "same-boot clock moved backwards; event not appended")
        require(parse_time(current_at) >= parse_time(previous_event.get("at", self.header["created_at"])),
                "event wall clock moved backwards; event not appended")
        transition = None if current_boot == previous_event["boot_id"] else dict(
            from_boot=previous_event["boot_id"], to_boot=current_boot, previous_monotonic_ns=previous_clock)
        event = dict(seq=len(events) + 1, previous=object_sha(events[-1] if events else self.header),
                     at=current_at, monotonic_ns=current_clock, boot_id=current_boot, clock_transition=transition,
                     node_id=node_id, state=target.value, data=data)
        return write_json(self.root / "events" / f"{len(events) + 1:06d}.json", event, exclusive=True)

    def ready(self):
        states, data = self.state()
        return self._ready_from_state(states, data)

    def _ready_from_state(self, states, data):
        """Select from this observation's verified state; never an action gate."""
        if any(state in (State.BLOCKED, State.FAILED_PRESERVED) for state in states.values()):
            return []
        for key, state in states.items():
            if state == State.ACCEPTED:
                # Accepted artifacts remain immutable when a dependency advances.
                accepted = json_ref(data[key]["accepted_evidence"])
                verify_tree(accepted["output_tree"])
                for ref in accepted["output_files"] + [accepted["report"], accepted["tests"]]:
                    read_ref(ref)
        # The same lane mapping the replay gate builds, so a claim selected now
        # is exactly the selection every later state() replays.
        return select_ready(self.plan, states, self.spec["strategy"], self.root / "work", self.header["queues"],
                            lanes=claimed_lanes(self, data))

    def claim(self, node_id, *, provider=None):
        require(node_id in self.nodes, "unknown frozen node")
        # Claim has no outer Runner action lock. Acquire it here exactly once.
        with exclusive_lock(self.root / "nodes" / node_id / "runner.lock"), self.state_lock(new_work=True):
            identity = self._gate(new_work=True)
            node = next((n for n in self.ready() if n["id"] == node_id), None)
            require(node is not None, "node is not ready; preserve active attempts")
            unbound = is_unbound(node)
            binding_pin = None
            if not unbound:
                lane = node["executor"]
                mode = node["execution_kind"]
            else:
                # The binding is a pre-claim artefact. Its pin, taken once here,
                # is both the lane source now and the claim's replay source.
                binding = read_binding(self.root, node_id)
                binding_pin = binding_ref(self.root, node_id) if binding is not None else None
                lane = binding["lane"] if binding is not None else None
                mode = resolved_mode(self.campaign, lane) if lane is not None else None
            # A never-bound node reaches decide() below and escalates with its
            # own reason; the pairing is checked as soon as a lane exists.
            if lane is not None:
                require((mode, executor_providers(self.plan)[lane]) in
                        (("local_cli", "codex"), ("delegated_cli", "claude")), "unsupported execution ownership")
            require(self.fixtures or provider is None, "live campaigns use the frozen rules provider")
            output = node_output(self.root, node)
            require(not output.exists(), "existing node output must be preserved")
            require(not (self.root / "claims" / (node_id + ".json")).exists(), "original claim intent exists; observe only")
            states, prior = self.state()
            lanes = claimed_lanes(self, prior)
            if unbound and lane is not None:
                # select_ready resolves lanes from frozen claims only, so it
                # cannot see this node's own lane before the claim exists. The
                # lane may have been taken since the bind. Refuse here, before
                # any write, with exactly the condition reserve() would refuse
                # (the same named predicate bind used); otherwise the lease
                # would refuse only after the claim intent was written.
                occupied = {other for key, other in lanes.items() if other is not None and states[key] in ACTIVE}
                require(orchestration_binding.lane_idle_write_domain(
                            lane, self.leases.read(), self.campaign["sessions"], occupied,
                            scope=str(node_write_scope(self.root, node)), scope_root="/"),
                        "bound lane or write scope is held by another attempt; preserve it")
                # Before any write: a binding bind_unbound did not create is
                # refused here, so a refusal leaves no claim-gates residue.
                # _check_claim re-derives it again, with the claim's own pins.
                self.verify_claimed_binding(node_id, binding_pin, {})
            gates = dict(campaign=self.campaign_ref, authorization=self.campaign["authorization"],
                         identity_source="offline_fixture" if self.fixtures else "live_kernel_and_native_foreground",
                         identity=identity, states={k: v.value for k, v in states.items()}, scope=str(output), inputs=self.campaign["inputs"])
            gates["context"] = self._decision_context(node_id, gates, lane=lane, lanes=lanes)
            verdict = decide(gates["context"], provider)
            require(verdict.outcome is Outcome.ALLOW, verdict.reason)
            require(lane is not None, "unbound node requires its original executor binding")
            task_id = f"{self.plan['plan_id']}--{self.spec['run_id']}--{node_id}--a0001"
            attempt_id = f"{self.spec['run_id']}:{node_id}:a0001"
            native = NativeReader(self.campaign["sessions"][lane], fixtures=self.fixtures).snapshot()
            self._work_budget()
            info = dict(task_id=task_id, attempt_id=attempt_id, output_root=str(output), mode=mode,
                        acceptance_nonce=secrets.token_hex(12), native_start=native, started_at=utc_now(),
                        gates=write_json(self.root / "nodes" / node_id / "claim-gates.json", gates, exclusive=True),
                        decision=verdict.record())
            if unbound:
                # The one extra claim key, present only for a node frozen
                # unbound: every later consumer resolves the lane from it.
                info["executor_binding"] = binding_pin
            from orchestration_evidence import dependency_bindings, work_request
            info["dependencies"] = dependency_bindings(node, prior)
            info["work_request"] = write_json(self.root / "nodes" / node_id / "work-request.json",
                work_request(self.campaign, self.header, node, info), exclusive=True)
            target = State.LOCAL_RUNNING if mode == "local_cli" else State.PREPARING
            self._check_claim(node_id, info, target, prior)
            # Intent precedes lease/output mutation. A crash never creates a
            # new nonce, task id or automatic second dispatch on restart.
            self._work_budget()
            claim = write_json(self.root / "claims" / (node_id + ".json"), info, exclusive=True)
            self.leases.reserve(task_id, self.campaign["sessions"][lane]["session_id"],
                                str(node_write_scope(self.root, node)), claim)
            output.mkdir(parents=True)
            self._append(node_id, target, info)
            return info

    def bind_unbound(self, node_id, decision_path):
        """Bind a deliberately unbound frozen node to one original lane, before its claim.

        No lifecycle event is appended: the node stays PENDING and the binding is
        a pre-claim artefact the claim then pins. Every refusal precedes the one
        exclusive write, so a refused bind leaves no file, lease or event a
        retry could mistake for an original. It does leave its own native call
        and that call's failed result in the Codex transcript, which is what
        makes a retry possible: the bind joins the one invocation that has not
        returned yet (``live_bind_call``), so a transiently refused bind - a
        lane taken at that moment, a busy lock - can be retried with a new
        call instead of leaving the node permanently unbindable.

        The decision must be the one the fixed publisher created in this run
        tree. Its pin and ``decided_at`` come from the native join of that
        publisher's own call and result (status increment section 3), never from
        a self-written field. The decision-file measurement and the bind
        control call are kept beside it as diagnostic fields only.

        Recomputed here in code, never taken from the advice or from JEV:
        permission (``_gate`` -> ``validate_campaign`` re-binds the original user
        authorization to this campaign root and its sessions), identity
        (``lane_verified`` over the live identity ``_gate`` returns), non-author
        and capacity (``lane_is_author``, ``lane_idle_write_domain`` inside
        ``eligible_candidates``), and input version (``advice_inputs_unchanged``).
        The actual-context fact has no producer and is recorded as such.
        """
        from upgrade_advice_chain import chain_from_binding, live_bind_call, published_decision_path
        require(node_id in self.nodes, "unknown frozen node")
        node = self.nodes[node_id]
        require(orchestration_binding.unbound_plan(self.plan) and is_unbound(node),
                "only a deliberately unbound frozen node is bound before claiming")
        with exclusive_lock(self.root / "nodes" / node_id / "runner.lock"), self.state_lock(new_work=True):
            identity = self._gate(new_work=True)
            states, data = self.state()
            ready = next((n for n in self._ready_from_state(states, data) if n["id"] == node_id), None)
            require(ready is not None, "node is not ready; preserve active attempts")
            require(read_binding(self.root, node_id) is None, "original executor binding exists; observe only")
            require(not (self.root / "claims" / (node_id + ".json")).exists(),
                    "original claim intent exists; observe only")
            self._work_budget()
            published = published_decision_path(self, node_id)
            require(str(decision_path) == str(published),
                    "bind takes only the decision the fixed publisher created for this node")
            decision_ref = file_ref(checked_path(published))
            decision = validate_decision(decision_ref, self.spec["run_id"], node_id)
            require(decision["decided_at"] is None,
                    "a published decision carries no self-written decision time")
            require(decision["supervisor_session"] == self.campaign["sessions"]["codex"]["session_id"],
                    "the decision was published for another supervisor session")
            facts = lane_facts(self, node_id, identity, states, data)
            candidates = eligible_candidates(self, node_id, identity, states, data, work_kind=decision["work_kind"])
            require(orchestration_binding.decision_route_recomputed(decision, candidates),
                    "actual route must come from the candidates recomputed at binding time")
            advice, body = self._bind_advice(node_id, decision)
            require(orchestration_binding.advice_inputs_unchanged(decision, body, self.campaign, self.spec["run_id"]),
                    "input version changed between advice generation and binding")
            script = control_script(self.campaign, self.campaign_ref, "bind-unbound", self.spec["run_id"],
                                    node_id=node_id, decision=str(published))
            reader = NativeReader(self.campaign["sessions"]["codex"], fixtures=self.fixtures)
            # The invocation running now, not "the only one ever": a refused
            # bind leaves its own returned call behind, and a retry must not be
            # refused as that call's duplicate.
            proof = live_bind_call(reader, script, after=self.header["native_start"]["codex"]["offset"])
            bound_at = native_control(reader, proof, script).isoformat()
            lane = decision["actual_route"]
            value = dict(schema=orchestration_binding.SCHEMA, campaign=self.campaign_ref, run_id=self.spec["run_id"],
                         node_id=node_id, lane=lane, execution_kind=resolved_mode(self.campaign, lane),
                         transport=resolved_transport(self.campaign, lane), mode=resolved_mode(self.campaign, lane),
                         arm=self.campaign.get("arm"), decision=dict(decision_ref), advice=advice,
                         eligible_lanes=list(candidates), lane_facts=bind_facts(facts), bound_at=bound_at,
                         bound_native=proof, input_units=self.campaign["inputs"],
                         supervisor_session=self.campaign["sessions"]["codex"]["session_id"])
            # The same join the verifier runs later, over the value about to be
            # written, so a bind can only create what will re-derive identically.
            chain = chain_from_binding(self, node_id, {}, {"codex": reader}, value, registration=None,
                                       not_after=parse_time(bound_at).timestamp(), binding_pin=lambda: None)
            require(chain["decision_publish_natively_joined"] is True and chain["decision_publish"] is not None,
                    "the published decision has no joined native publisher call and result")
            require(chain["bind_natively_verified"] is True,
                    "the binding does not pin this bind step's own native control call")
            require(chain["arm"] == "advisory-off" or chain["read"] is not None,
                    "the on arm binds only after one complete original read of its advice")
            publish = chain["decision_publish"]
            envelope = publish["envelope"]["decision"]
            value["decision"] = dict(path=envelope["path"], bytes=envelope["bytes"], sha256=envelope["sha256"],
                                     decided_at=chain["decided_at"], source="native_publish_result",
                                     publish_call=publish["call_record"], publish_result=publish["result_record"],
                                     diagnostic_file=dict(decision_ref))
            value["advice"] = dict(advice, read=chain["read"],
                                   counted_as_advisory_consumption=chain["counted_as_advisory_consumption"])
            return write_binding(self, node_id, value)

    def _bind_advice(self, node_id, decision):
        """The arm facts a binding records, measured from the run tree and the plan.

        Returns ``(advice, body)``: the dict the chain re-derives from, and the
        sha-pinned advice body (None on the off arm) whose recorded input units
        ``advice_inputs_unchanged`` compares. The read window starts at this
        run's own native start: no producer at this HEAD persists the worker's
        pre-publication fence, so the wider, still bounded run fence is used
        rather than an unrecorded one.
        """
        from cmux_prompt_reference import plan as reference_plan
        from upgrade_body_read import read_command
        directory = self.root / "nodes" / node_id / "advice"
        journal = self._worker_journal(node_id)
        fence = self.header["native_start"]["codex"]
        if decision["advice_sha256"] is None:
            return dict(arm="advisory-off", advice_sha256=None, worker_status=None, worker_journal=journal,
                        fence=fence), None
        ref = file_ref(checked_path(directory / (decision["advice_sha256"] + ".txt")))
        require(ref["sha256"] == decision["advice_sha256"], "the cited advice is not the advice this run published")
        text = read_ref(ref, "published advice body").decode("utf-8")
        body = json_ref(ref, "published advice body")
        require(body.get("schema") == ADVICE_BODY_SCHEMA and body.get("campaign") == self.campaign_ref and
                body.get("run_id") == self.spec["run_id"] and body.get("node_id") == node_id,
                "the published advice belongs to another campaign, run or node")
        advice = body.get("advice")
        require(isinstance(advice, dict) and isinstance(advice.get("status"), str), "the published advice has no status")
        notice = reference_plan(text, body["marker"], directory)["text"]
        command = read_command(self.campaign["python"]["path"], self.campaign["controller"]["path"], notice)
        return dict(arm="advisory-on", advice_sha256=decision["advice_sha256"], notice=notice, command=command,
                    fence=fence, not_before_epoch=parse_time(self.header["started_at"]).timestamp(),
                    worker_status=advice["status"], worker_journal=journal), body

    def record_advice(self, node_id):
        """Run the JEV worker for one deliberately unbound node before its decision.

        The production caller of ``jev_advice_run.run_and_record``. This
        registered campaign forbids third-party model calls (``third_party_api``
        is False), so its arm is advisory-off: no transport, no budget, zero
        requests, and only the worker's exact journal is written. No advice
        body is published, so the off arm keeps an empty advice directory. The
        ledger directory is fixed per run here, never chosen by a node, and the
        lanes are the bind path's own ``lane_facts``. An existing journal or
        intent refuses re-entry before the worker runs again.
        """
        from jev_advice_run import LEDGER_DIRECTORY, run_and_record
        require(node_id in self.nodes, "unknown frozen node")
        require(orchestration_binding.unbound_plan(self.plan) and is_unbound(self.nodes[node_id]),
                "only a deliberately unbound frozen node gets worker advice")
        require(self.campaign.get("third_party_api") is False,
                "this registration forbids third-party calls; an on arm needs its own JEV registration")
        run_id = self.spec["run_id"]
        with exclusive_lock(self.root / "nodes" / node_id / "runner.lock"), self.state_lock(new_work=True):
            identity = self._gate(new_work=True)
            states, data = self.state()
            require(states[node_id] == State.PENDING, "worker advice precedes the claim")
            require(not (self.root / "claims" / (node_id + ".json")).exists(),
                    "original claim intent exists; observe only")
            require(not (self.root / "decisions" / (node_id + ".json")).exists(),
                    "original published decision exists; observe only")
            lanes = lane_facts(self, node_id, identity, states, data)
            answer = run_and_record(self.root / "nodes" / node_id, ledger_root=self.root / LEDGER_DIRECTORY,
                                    run_id=run_id, decision=node_id, state_text="unbound node " + node_id,
                                    lanes=lanes, bound=False, arm="advisory-off",
                                    marker="JEV_ADVICE_%s_%s" % (run_id, node_id), provider="none",
                                    budget=None, model=None, transport=None)
        require(answer["model_requests"] == 0 and answer["ledger"] is None, "the off arm made a request")
        return dict(schema="orchestration-worker-advice-v1", run_id=run_id, node_id=node_id, arm="advisory-off",
                    worker_status=answer["advice"]["status"], model_requests=0, journal=answer["journal"],
                    ledger=None)

    def _worker_journal(self, node_id):
        """The worker's own self-authored journal entries, or None when absent."""
        path = self.root / "nodes" / node_id / "advice-worker-journal.json"
        if not (path.exists() or path.is_symlink()):
            return None
        value = json_ref(file_ref(checked_path(path)), "advice worker journal")
        require(value.get("schema") == "jev-advice-worker-journal-v1" and isinstance(value.get("entries"), list),
                "advice worker journal shape changed")
        return value["entries"]

    def transition(self, node_id, state, data):
        require(state not in (State.LOCAL_RUNNING, State.PREPARING, State.ACCEPTED, State.FAILED_PRESERVED), "use claim/accept/settle for gated transitions")
        new_work = state in (State.PREPARED, State.DISPATCH_INTENT)
        with self.state_lock(new_work=new_work):
            self._gate(new_work=new_work)
            return self._append(node_id, state, data)

    def accept(self, node_id, evidence_ref):
        from orchestration_evidence import verify_node
        with self.state_lock():
            self._gate()
            states, data = self.state()
            require(states[node_id] == State.EVIDENCE_READY, "node is not ready for acceptance")
            evidence = json_ref(evidence_ref)
            require(evidence_ref["path"] == str(self.root / "nodes" / node_id / "accepted-evidence.json"),
                    "acceptance evidence path changed")
            # Reconstruct the pinned result without executing a checker here.
            # _append's new ACCEPTED transition independently reruns it once,
            # compares the result, and only then publishes the terminal event.
            result = verify_node(self.campaign, self.header, self.nodes[node_id], data[node_id], evidence,
                                 fixtures=self.fixtures, rerun=False)
            terminal = self._append(node_id, State.ACCEPTED, dict(accepted_evidence=evidence_ref, verification=result))
            self._release(node_id, data[node_id], terminal)
            return result

    def _release(self, key, info, terminal):
        lease = self._lease(key, info)
        lane = claim_lane(self.nodes[key], info)
        self.leases.release(info["task_id"], session=self.campaign["sessions"][lane]["session_id"],
                            scope=str(node_write_scope(self.root, self.nodes[key])),
                            claim=file_ref(self.root / "claims" / (key + ".json")), terminal=terminal,
                            reserved_at=lease["reserved_at"])

    @contextmanager
    def node_locks(self, key):
        """Own runner → transport → coordinator locks for one synchronous action.

        Node locks are nonblocking/nonreentrant; shared v3 state acquisition waits.
        Lock files are persistent and must never
        be replaced/unlinked. A fork-only child can prolong the inherited lock;
        a surviving fork/exec descendant can outlive this ownership.
        Internal recovery helpers consume this ownership without reacquiring it.
        """
        require(key in self.nodes, "unknown frozen node")
        root = self.root / "nodes" / key
        with exclusive_lock(root / "runner.lock"), exclusive_lock(root / "transport.lock"), \
                self.state_lock():
            yield

    def _repair_release_locked(self, key, info, terminal):
        if self._lease(key, info)["released_at"] is not None:
            return False
        event = json_ref(terminal)
        if event["state"] == State.FAILED_PRESERVED and json_ref(event["data"]["failure"])["boundary"] == "preparation_exited":
            from orchestration_preparation import check_fresh_release
            check_fresh_release(self, key, info, event["data"]["failure"], terminal)
        self._release(key, info, terminal)
        return True

    def repair_releases(self):
        """Repair only a validated terminal-event/lease crash window; never send."""
        repaired = []
        for key in self.nodes:
            with self.node_locks(key):
                if not self.fixtures:
                    require_live_identity(self.campaign)
                states, data = self.state()
                if states[key] in (State.ACCEPTED, State.FAILED_PRESERVED):
                    event = next(e for e in reversed(self.events()) if e["node_id"] == key)
                    terminal = file_ref(self.root / "events" / f"{event['seq']:06d}.json")
                    if self._repair_release_locked(key, data[key], terminal):
                        repaired.append(key)
        return repaired

    def settle_failure(self, node_id, failure_ref):
        with self.node_locks(node_id):
            return self._settle_failure_locked(node_id, failure_ref)

    def _settle_failure_locked(self, node_id, failure_ref):
        from orchestration_evidence import verify_failure
        self._gate()
        states, data = self.state()
        require(states.get(node_id) in (State.BLOCKED, State.FAILED_PRESERVED), "only a preserved blocker can be settled")
        result = verify_failure(self.campaign, self.header, self.nodes[node_id], data[node_id], failure_ref, fixtures=self.fixtures)
        if states[node_id] == State.BLOCKED:
            terminal = self._append(node_id, State.FAILED_PRESERVED, dict(failure=failure_ref, verification=result))
        else:
            require(data[node_id]["failure"] == failure_ref, "original terminal failure changed")
            event = next(e for e in reversed(self.events()) if e["node_id"] == node_id)
            terminal = file_ref(self.root / "events" / f"{event['seq']:06d}.json")
        self._repair_release_locked(node_id, data[node_id], terminal)
        return result

    def recover(self):
        """Discovery only. This method has no transport, timeout reset or resend."""
        states, data = self.state()
        observed = [{"node_id": key, "state": state.value, "action":
                 "observe_original_attempt" if state in (State.DISPATCH_INTENT, State.UNCERTAIN, State.PREPARING, State.BLOCKED)
                 else "inspect_report_and_callback" if state == State.DISPATCHED
                 else "continue_original_local_work" if state == State.LOCAL_RUNNING
                 else "review_frozen_evidence", "task": data[key]}
                for key, state in states.items() if state in ACTIVE]
        for path in sorted((self.root / "claims").glob("*.json")):
            require(path.stem in states, "unknown claim intent")
            if states[path.stem] == State.PENDING:
                observed.append(dict(node_id=path.stem, state="INCOMPLETE_CLAIM_INTENT",
                                     action="inspect_original_intent_and_lease", task=read_json(path)))
        # A binding is a pre-claim artefact with no lifecycle event, so a bound
        # node that was never claimed is visible only here. Discovery only.
        for path in sorted((self.root / "bindings").glob("*.json")):
            require(path.stem in states and is_unbound(self.nodes[path.stem]), "unknown executor binding")
            if states[path.stem] == State.PENDING:
                observed.append(dict(node_id=path.stem, state="UNBOUND_BOUND_NOT_CLAIMED",
                                     action="inspect_original_binding", binding=binding_ref(self.root, path.stem)))
        for key, state in states.items():
            if state in (State.ACCEPTED, State.FAILED_PRESERVED) and self._lease(key, data[key])["released_at"] is None:
                observed.append(dict(node_id=key, state="TERMINAL_RELEASE_PENDING", action="repair_verified_terminal_release"))
        return observed
