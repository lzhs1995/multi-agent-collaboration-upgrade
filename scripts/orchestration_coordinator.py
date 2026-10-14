"""Durable CLI orchestration. Scheduling never sends messages or infers success.

All three strategies use the same transport and verifier. Unknown delivery
retains both its lane and scope. Restarting a coordinator cannot resend it.
"""
from __future__ import annotations

import secrets
import time
from enum import Enum
from pathlib import Path

from decision_provider import Decision, Outcome, decide
from orchestration_common import (ContractError, boot_id, checked_path, contract_errors, count, exclusive_lock, file_ref,
                                  json_ref, object_sha, parse_time, read_json, read_ref, scopes_conflict,
                                  utc_now, verify_tree, write_json)
from orchestration_campaign import (control_script, find_control, native_control, node_output,
                                    require_live_identity, validate_campaign)
from orchestration_native import NativeReader, require
from orchestration_planner import ID_RE, validate_plan

STRATEGIES = ("serial", "omo", "codeg")


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


def select_ready(plan, states, strategy, scope_root, queues):
    require(strategy in STRATEGIES, "unsupported strategy")
    nodes = topological_nodes(validate_plan(plan))
    accepted = {key for key, value in states.items() if value == State.ACCEPTED}
    busy = [n for n in nodes if states[n["id"]] in ACTIVE]
    pending = [n for n in nodes if states[n["id"]] == State.PENDING]
    if strategy == "serial":
        pending = pending[:1] if not busy else []
    if strategy == "codeg":
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
        occupied = busy + selected
        if any(other["executor"] == node["executor"] or
               scopes_conflict(other["write_scope"], node["write_scope"], scope_root) for other in occupied):
            continue
        selected.append(node)
    return selected


def strategy_probe(plan):
    """Exercise the actual selection algorithm with estimates, never live time."""
    result = {}
    queues = {lane: [n["id"] for n in topological_nodes(plan) if n["executor"] == lane]
              for lane in ("codex", "claude")}
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
        with exclusive_lock(self.root / "registry.lock"):
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

    def release(self, owner, *, session, scope, claim, terminal):
        with exclusive_lock(self.root / "registry.lock"):
            path = self.root / "leases.json"
            value = self.read()
            require(owner in value, "original lease missing")
            require(all(value[owner][k] == v for k, v in dict(session_id=session, scope=scope, claim=claim).items()),
                    "original lease ownership/session/scope/claim changed")
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
    def __init__(self, root, campaign_ref, *, fixtures=False):
        self.root = checked_path(root)
        self.campaign_ref = campaign_ref
        self.campaign = validate_campaign(campaign_ref, fixtures=fixtures)
        self.fixtures = fixtures
        require(self.campaign.get("evidence_class") == ("offline_fixture" if fixtures else "cli_campaign_v2"), "campaign execution class mismatch")
        self.plan = validate_plan(json_ref(self.campaign["plan"], "frozen plan"))
        self.nodes = {n["id"]: n for n in self.plan["nodes"]}
        self.header = read_json(self.root / "run.json")
        require(set(self.header) == {"version", "campaign", "run", "queues", "started_at",
                                    "created_at", "started_monotonic_ns", "boot_id", "native_start", "start_native"},
                "run header fields changed")
        require(type(self.header["version"]) is int and self.header["version"] == 2 and
                self.header["campaign"] == campaign_ref, "run belongs to another frozen campaign")
        require(self.header["run"] in self.campaign["runs"], "run is not in frozen order")
        self.spec = self.header["run"]
        require(self.root == Path(self.campaign["root"]) / "runs" / self.spec["run_id"], "run root changed")
        queues = {lane: [n["id"] for n in topological_nodes(self.plan) if n["executor"] == lane]
                  for lane in self.campaign["sessions"]}
        require(self.header["queues"] == queues, "frozen FIFO queues changed")
        count(self.header["started_monotonic_ns"], "run clock")
        require(isinstance(self.header["boot_id"], str) and self.header["boot_id"], "run boot identity missing")
        reader = NativeReader(self.campaign["sessions"]["codex"], fixtures=fixtures)
        started = native_control(reader, self.header["start_native"],
                                 control_script(self.campaign, campaign_ref, "start", self.spec["run_id"]))
        require(parse_time(self.header["started_at"]) == started <= parse_time(self.header["created_at"]),
                "run start is not the original native invocation")
        require(set(self.header["native_start"]) == set(self.campaign["sessions"]), "native run lanes changed")
        for lane, session in self.campaign["sessions"].items():
            reader = NativeReader(session, fixtures=fixtures)
            require(reader.verify_pin(self.header["native_start"][lane]) >= reader.verify_pin(session["freeze_fence"]),
                    "run predates campaign freeze")
        start_ref = self.header["start_native"]
        require(start_ref["offset"] >= self.campaign["sessions"]["codex"]["freeze_fence"]["offset"] and
                start_ref["offset"] + start_ref["length"] <= self.header["native_start"]["codex"]["offset"],
                "native start call outside campaign/run fences")
        self.leases = LeaseRegistry(self.campaign["lease_root"])

    def _gate(self):
        validate_campaign(self.campaign_ref, fixtures=self.fixtures)
        require(not (self.root / "finish.json").exists(), "run is closed; original evidence is immutable")
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
        require(spec["strategy"] in STRATEGIES, "invalid strategy")
        require(root == Path(campaign["root"]) / "runs" / spec["run_id"], "run must use its frozen directory")
        if not fixtures:
            require_live_identity(campaign)
        reader = NativeReader(campaign["sessions"]["codex"], fixtures=fixtures)
        script = control_script(campaign, campaign_ref, "start", spec["run_id"])
        proof = find_control(reader, script, after=campaign["sessions"]["codex"]["freeze_fence"]["offset"])
        started_at = native_control(reader, proof, script).isoformat()
        with exclusive_lock(Path(campaign["root"]) / "campaign.lock"):
            for previous in campaign["runs"][:campaign["runs"].index(spec)]:
                path = Path(campaign["root"]) / "runs" / previous["run_id"] / "finish.json"
                require(path.exists(), "earlier frozen trial is unfinished")
                from orchestration_verify import verify_run
                verify_run(campaign_ref, previous["run_id"], fixtures=fixtures)
            root.mkdir(parents=True, exist_ok=False)
            ordered = topological_nodes(plan)
            queues = {lane: [n["id"] for n in ordered if n["executor"] == lane] for lane in campaign["sessions"]}
            starts = {lane: NativeReader(session, fixtures=fixtures).snapshot() for lane, session in campaign["sessions"].items()}
            header = dict(version=2, campaign=campaign_ref, run=spec, queues=queues, started_at=started_at,
                          created_at=utc_now(), started_monotonic_ns=time.monotonic_ns(), boot_id=boot_id(), native_start=starts,
                          start_native=proof)
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
                                                             self.root / "work", self.header["queues"])},
                        "persisted claim violates dependencies/session/scope/queue")
                self._check_claim(key, event["data"], target, data)
                self._check_decision(key, event["data"], states)
                require(parse_time(event["data"]["started_at"]) <= parse_time(event["at"]), "claim begins after event")
                intent = json_ref(file_ref(self.root / "claims" / (key + ".json")))
                require(intent == event["data"], "claim intent changed or was not persisted")
            else:
                require(not set(event["data"]) & set(data[key]), "immutable lifecycle data was overwritten")
                require(set(event["data"]) == self._fields(target), "unexpected transition evidence")
                self._check_transition(key, target, event["data"], data[key])
            states[key] = target
            data[key].update(event["data"])
        for key, info in data.items():
            if info:
                lease = self._lease(key, info)
                if states[key] in ACTIVE:
                    require(lease["released_at"] is None, "active/uncertain lease was prematurely released")
                elif lease["released_at"] is not None:
                    event = next(e for e in reversed(self.events()) if e["node_id"] == key)
                    require(lease["released_by"] == file_ref(self.root / "events" / f"{event['seq']:06d}.json"),
                            "terminal lease released by a different event")
        return states, data

    @staticmethod
    def _fields(target):
        return {State.PREPARED: {"prepared"}, State.DISPATCH_INTENT: {"dispatch_intent"},
                State.UNCERTAIN: {"uncertainty"}, State.DISPATCHED: {"delivery"},
                State.EVIDENCE_READY: {"candidate"}, State.ACCEPTED: {"accepted_evidence", "verification"},
                State.BLOCKED: {"blocker"}, State.FAILED_PRESERVED: {"failure", "verification"}}.get(target, set())

    def _check_claim(self, key, info, target, prior):
        from orchestration_evidence import dependency_bindings, task_identity, work_request
        node = self.nodes[key]
        require(set(info) == {"task_id", "attempt_id", "output_root", "mode", "acceptance_nonce", "native_start", "started_at", "gates", "decision", "dependencies", "work_request"},
                "claim fields changed")
        expected = task_identity(self.campaign, self.header, node)
        require(all(info.get(k) == expected[k] for k in ("task_id", "attempt_id")), "claim task/attempt changed")
        mode = "local_cli" if node["executor"] == "codex" else "delegated_cli"
        require(info["mode"] == node["execution_kind"] == mode and
                target == (State.LOCAL_RUNNING if mode == "local_cli" else State.PREPARING), "execution ownership changed")
        require(info["output_root"] == str(node_output(self.root, node)), "claim output scope changed")
        require(info["dependencies"] == dependency_bindings(node, prior), "claim dependency acceptance changed")
        require(info["work_request"]["path"] == str(self.root / "nodes" / key / "work-request.json") and
                json_ref(info["work_request"]) == work_request(self.campaign, self.header, node, info), "claim work request changed")
        require(isinstance(info["acceptance_nonce"], str) and len(info["acceptance_nonce"]) == 24 and
                all(c in "0123456789abcdef" for c in info["acceptance_nonce"]), "claim nonce invalid")
        reader = NativeReader(self.campaign["sessions"][node["executor"]], fixtures=self.fixtures)
        require(reader.verify_pin(info["native_start"]) >= self.header["native_start"][node["executor"]]["offset"],
                "claim predates original run")
        require(parse_time(info["started_at"]) >= parse_time(self.header["created_at"]), "claim predates run creation")

    def _check_decision(self, key, info, states):
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
        context = self._decision_context(key, gates)
        require(gates["context"] == context, "claim gate context differs from verified evidence")
        value = info["decision"]
        parsed = Decision(Outcome(value["outcome"]), value["reason"], value["confidence"],
                          tuple(value["evidence_refs"]), value["route"], value["provider"])
        require(parsed.record() == value and parsed.outcome is Outcome.ALLOW and parsed.route == self.nodes[key]["executor"],
                "claim decision is invalid")
        if not self.fixtures or parsed.provider == "rules":
            require(parsed.record() == decide(context).record(), "claim decision/provenance differs from frozen rules")

    def _decision_context(self, key, gates):
        authorization = json_ref(gates["authorization"])
        identity = gates["identity"]
        ready = select_ready(self.plan, {k: State(v) for k, v in gates["states"].items()},
                             self.spec["strategy"], self.root / "work", self.header["queues"])
        return dict(purpose="route", executor=self.nodes[key]["executor"], available_executors=sorted(identity),
                    authorized=authorization.get("approved_root") == self.campaign["root"],
                    identity_verified=set(identity) == set(self.campaign["sessions"]) and all(
                        all(self.campaign["sessions"][lane].get(k) == v for k, v in actual.items())
                        for lane, actual in identity.items()),
                    scope_valid=key in {n["id"] for n in ready} and gates["scope"] == str(node_output(self.root, self.nodes[key])),
                    evidence_verified=gates["inputs"] == self.campaign["inputs"] and gates["campaign"] == self.campaign_ref,
                    evidence_refs=[gates["campaign"]["path"] + "#" + gates["campaign"]["sha256"],
                                   gates["authorization"]["path"] + "#" + gates["authorization"]["sha256"]])

    def _lease(self, key, info):
        lease = self.leases.read().get(info["task_id"])
        require(lease is not None and lease["session_id"] == self.campaign["sessions"][self.nodes[key]["executor"]]["session_id"] and
                lease["scope"] == info["output_root"] and lease["claim"] == file_ref(self.root / "claims" / (key + ".json")),
                "original lease ownership/session/scope/claim changed")
        require(parse_time(lease["reserved_at"]) >= parse_time(info["started_at"]), "lease predates claim")
        return lease

    def _check_transition(self, key, target, extra, info):
        from orchestration_evidence import (validate_candidate, verify_prepared, verify_dispatch_intent,
                                            verify_dispatched, verify_node, verify_failure)
        if target == State.PREPARED:
            verify_prepared(self.campaign, self.header, self.nodes[key], info, extra["prepared"], fixtures=self.fixtures)
        elif target == State.DISPATCH_INTENT:
            verify_dispatch_intent(self.campaign, self.header, self.nodes[key], info, extra["dispatch_intent"], fixtures=self.fixtures)
        elif target == State.DISPATCHED:
            verify_dispatched(self.campaign, self.header, self.nodes[key], info, extra["delivery"], fixtures=self.fixtures)
        elif target == State.EVIDENCE_READY:
            validate_candidate(self.campaign, self.header, self.nodes[key], info, extra["candidate"], fixtures=self.fixtures)
        elif target == State.ACCEPTED:
            value = verify_node(self.campaign, self.header, self.nodes[key], info, json_ref(extra["accepted_evidence"]), fixtures=self.fixtures)
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
        if any(state in (State.BLOCKED, State.FAILED_PRESERVED) for state in states.values()):
            return []
        for key, state in states.items():
            if state == State.ACCEPTED:
                # Accepted artifacts remain immutable when a dependency advances.
                accepted = json_ref(data[key]["accepted_evidence"])
                verify_tree(accepted["output_tree"])
                for ref in accepted["output_files"] + [accepted["report"], accepted["tests"]]:
                    read_ref(ref)
        return select_ready(self.plan, states, self.spec["strategy"], self.root / "work", self.header["queues"])

    def claim(self, node_id, *, provider=None):
        with exclusive_lock(self.root / "coordinator.lock"):
            identity = self._gate()
            node = next((n for n in self.ready() if n["id"] == node_id), None)
            require(node is not None, "node is not ready; preserve active attempts")
            lane = node["executor"]
            mode = node["execution_kind"]
            require((mode, lane) in (("local_cli", "codex"), ("delegated_cli", "claude")), "unsupported execution ownership")
            require(self.fixtures or provider is None, "live campaigns use the frozen rules provider")
            output = node_output(self.root, node)
            require(not output.exists(), "existing node output must be preserved")
            require(not (self.root / "claims" / (node_id + ".json")).exists(), "original claim intent exists; observe only")
            states, prior = self.state()
            gates = dict(campaign=self.campaign_ref, authorization=self.campaign["authorization"],
                         identity_source="offline_fixture" if self.fixtures else "live_kernel_and_native_foreground",
                         identity=identity, states={k: v.value for k, v in states.items()}, scope=str(output), inputs=self.campaign["inputs"])
            gates["context"] = self._decision_context(node_id, gates)
            verdict = decide(gates["context"], provider)
            require(verdict.outcome is Outcome.ALLOW, verdict.reason)
            task_id = f"{self.plan['plan_id']}--{self.spec['run_id']}--{node_id}--a0001"
            attempt_id = f"{self.spec['run_id']}:{node_id}:a0001"
            native = NativeReader(self.campaign["sessions"][lane], fixtures=self.fixtures).snapshot()
            info = dict(task_id=task_id, attempt_id=attempt_id, output_root=str(output), mode=mode,
                        acceptance_nonce=secrets.token_hex(12), native_start=native, started_at=utc_now(),
                        gates=write_json(self.root / "nodes" / node_id / "claim-gates.json", gates, exclusive=True),
                        decision=verdict.record())
            from orchestration_evidence import dependency_bindings, work_request
            info["dependencies"] = dependency_bindings(node, prior)
            info["work_request"] = write_json(self.root / "nodes" / node_id / "work-request.json",
                work_request(self.campaign, self.header, node, info), exclusive=True)
            target = State.LOCAL_RUNNING if mode == "local_cli" else State.PREPARING
            self._check_claim(node_id, info, target, prior)
            # Intent precedes lease/output mutation. A crash never creates a
            # new nonce, task id or automatic second dispatch on restart.
            claim = write_json(self.root / "claims" / (node_id + ".json"), info, exclusive=True)
            self.leases.reserve(task_id, self.campaign["sessions"][lane]["session_id"], str(output), claim)
            output.mkdir(parents=True)
            self._append(node_id, target, info)
            return info

    def transition(self, node_id, state, data):
        require(state not in (State.LOCAL_RUNNING, State.PREPARING, State.ACCEPTED, State.FAILED_PRESERVED), "use claim/accept/settle for gated transitions")
        with exclusive_lock(self.root / "coordinator.lock"):
            self._gate()
            return self._append(node_id, state, data)

    def accept(self, node_id, evidence_ref):
        from orchestration_evidence import verify_node
        with exclusive_lock(self.root / "coordinator.lock"):
            self._gate()
            states, data = self.state()
            require(states[node_id] == State.EVIDENCE_READY, "node is not ready for acceptance")
            evidence = json_ref(evidence_ref)
            require(evidence_ref["path"] == str(self.root / "nodes" / node_id / "accepted-evidence.json"),
                    "acceptance evidence path changed")
            result = verify_node(self.campaign, self.header, self.nodes[node_id], data[node_id], evidence,
                                 fixtures=self.fixtures, rerun=True)
            terminal = self._append(node_id, State.ACCEPTED, dict(accepted_evidence=evidence_ref, verification=result))
            self._release(node_id, data[node_id], terminal)
            return result

    def _release(self, key, info, terminal):
        self.leases.release(info["task_id"], session=self.campaign["sessions"][self.nodes[key]["executor"]]["session_id"],
                            scope=info["output_root"], claim=file_ref(self.root / "claims" / (key + ".json")), terminal=terminal)

    def repair_releases(self):
        """Repair only a validated terminal-event/lease crash window; never send."""
        with exclusive_lock(self.root / "coordinator.lock"):
            if not self.fixtures:
                require_live_identity(self.campaign)
            states, data = self.state()
            repaired = []
            for event in self.events():
                key = event["node_id"]
                if event["state"] in (State.ACCEPTED, State.FAILED_PRESERVED) and self._lease(key, data[key])["released_at"] is None:
                    self._release(key, data[key], file_ref(self.root / "events" / f"{event['seq']:06d}.json"))
                    repaired.append(key)
            return repaired

    def settle_failure(self, node_id, failure_ref):
        from orchestration_evidence import verify_failure
        with exclusive_lock(self.root / "coordinator.lock"):
            self._gate()
            states, data = self.state()
            require(states.get(node_id) == State.BLOCKED, "only a preserved blocker can be settled")
            result = verify_failure(self.campaign, self.header, self.nodes[node_id], data[node_id], failure_ref, fixtures=self.fixtures)
            terminal = self._append(node_id, State.FAILED_PRESERVED, dict(failure=failure_ref, verification=result))
            self._release(node_id, data[node_id], terminal)
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
        for key, state in states.items():
            if state in (State.ACCEPTED, State.FAILED_PRESERVED) and self._lease(key, data[key])["released_at"] is None:
                observed.append(dict(node_id=key, state="TERMINAL_RELEASE_PENDING", action="repair_verified_terminal_release"))
        return observed
