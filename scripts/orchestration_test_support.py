"""Synthetic journals for offline tests only; never used by live entrypoints."""
import copy
import contextlib
import json
import secrets
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from orchestration_campaign import METRICS, METRICS_V3, control_script
from orchestration_contracts import campaign_version
from orchestration_common import digest, file_ref, json_ref, object_sha, parse_time, read_ref, tree_manifest, utc_now, write_json
from orchestration_coordinator import Coordinator, State
from orchestration_evidence import (failure_marker, make_test_result, marker_for, output_refs, pack_extension,
                                    task_identity, task_notice, task_scope, task_sources)
from test_orchestration_native import NativeFixture


class OfflineDisarmRuntime:
    """Private marker/state files; never imports or calls a shared controller."""

    def __init__(self, campaign, root, *, executor_id=None, native_uuid_uppercase=False):
        from types import SimpleNamespace
        from orchestration_disarm import marker_callers
        self.campaign, self.root = campaign, Path(root).resolve()
        self.executor_id = executor_id
        self.native_uuid = lambda value: value.upper() if native_uuid_uppercase else value
        self.active, self.states = self.root / "active", self.root / "states"
        self.active.mkdir(parents=True, exist_ok=True)
        self.states.mkdir(parents=True, exist_ok=True)
        self.disarms, self.recoveries = [], []
        self.marker = None
        workspace = self.native_uuid(campaign["sessions"]["codex"]["workspace_uuid"])
        self.disarm_harness = SimpleNamespace(_ACTIVE_DIR=self.active, _workspace_key=lambda: workspace,
                                              disarm_task=self._remove)
        self.lifecycle = SimpleNamespace(marker_callers=marker_callers, marker_locks=lambda marker: contextlib.nullcontext(),
                                         apply_boundary=self._preview, task_boundary=self._release)
        self.reask = SimpleNamespace(root=lambda: self.states,
            state_path=lambda caller: self.states / (digest(caller.encode()) + ".json"),
            stop_file=lambda caller: self.states / (digest(caller.encode()) + ".stop.json"))

    def freeze_disarm(self, task_id):
        from orchestration_disarm import freeze_original
        if self.marker is None:
            self.marker = dict(task_id=task_id,
                collaboration_id="fixture-" + digest(task_id.encode())[:20],
                workspace_uuid=self.native_uuid(self.campaign["sessions"]["codex"]["workspace_uuid"]),
                participants=[dict(role="supervisor" if lane == "codex" else "executor",
                                   surface_uuid=self.native_uuid(session["surface_uuid"]))
                              for lane, session in self.campaign["sessions"].items()
                              if self.executor_id is None or lane in ("codex", self.executor_id)])
            write_json(self.active / self.marker["workspace_uuid"] / (self.marker["collaboration_id"] + ".json"),
                       self.marker, exclusive=True)
        return freeze_original(self.campaign, task_id, self.disarm_harness, self.lifecycle, self.reask)

    @staticmethod
    def _preview(state, marker, boundary, now, *, publish=False):
        assert boundary == "RELEASED" and publish is False
        continuation = state.get("continuation", {})
        eligible = (state.get("state") == "WAITING_REPLY" or
                    continuation.get("enabled") is True and state.get("state") in ("ANSWERED", "CONSUMED"))
        if not eligible:
            return False
        key = dict(collaboration_id=marker["collaboration_id"], task_id=marker["task_id"])
        before = state.get("paused_tasks", [])
        state["paused_tasks"] = [p for p in before if p != key]
        return before != state["paused_tasks"]

    def _release(self, marker, boundary):
        from orchestration_disarm import marker_callers
        changed = 0
        for caller in marker_callers(marker):
            path = self.reask.state_path(caller)
            if path.exists():
                state = json_ref(file_ref(path))
                if self._preview(state, marker, boundary, 0):
                    write_json(path, state)
                    changed += 1
        return changed

    def _remove(self, task_id):
        assert self.marker["task_id"] == task_id
        self._release(self.marker, "RELEASED")
        removed = 0
        for path in (self.active / self.marker["workspace_uuid"] / (self.marker["collaboration_id"] + ".json"),
                     self.active / (self.marker["workspace_uuid"] + ".json")):
            if path.exists():
                path.unlink()
                removed += 1
        return removed

    def observe_disarm(self, frozen):
        from orchestration_disarm import observe_original
        return observe_original(self.campaign, frozen, self.disarm_harness, self.lifecycle, self.reask)

    def disarm(self, frozen):
        from orchestration_disarm import invoke_disarm
        self.disarms.append(frozen["task_id"])
        return invoke_disarm(self.campaign, frozen, self.disarm_harness, self.lifecycle, self.reask)

    def recover_disarm(self, frozen, action):
        from orchestration_disarm import invoke_recovery
        self.recoveries.append(action)
        return invoke_recovery(self.campaign, frozen, action, self.disarm_harness, self.lifecycle, self.reask)


class CampaignFixture:
    def __init__(self, base, strategies=("omo",), *, version=2, arm="B", unbound=False):
        base = Path(base).resolve()
        assert version in (2, 3)
        # The opt-in field is plan v2 only, so the unbound variant exists for a
        # v3 campaign alone. Existing node tuples and every existing claim call
        # site keep today's shape; the variant only appends one node.
        assert not unbound or version == 3
        private = self._private_runtime(base, arm) if version == 3 else None
        self.root = private["paths"]["campaigns"] / "campaign" if private else base / "campaign"
        self.root.mkdir()
        self.clients = {}
        self.surface_refs = {}
        self.pane_refs = {}
        lanes = ("codex", "claude_a", "claude_b") if version == 3 else ("codex", "claude")
        workspace = str(uuid.uuid4())
        for index, lane in enumerate(lanes, 1):
            directory = base / lane
            directory.mkdir()
            provider = "codex" if lane == "codex" else "claude"
            client = NativeFixture(directory, provider, session_id=str(uuid.uuid4()) if version == 3 else None)
            client.session.update(surface_uuid=str(uuid.uuid4()) if version == 3 else lane + "-surface",
                                  pane_uuid=str(uuid.uuid4()) if version == 3 else lane + "-pane")
            self.surface_refs[lane] = f"surface:{index}"
            self.pane_refs[lane] = f"pane:{index}"
            if version == 2:
                client.session["surface_ref"] = self.surface_refs[lane]
            if version == 3:
                client.session.update(workspace_uuid=workspace, tty=f"ttys{index:03d}",
                                      process=dict(pid=100 + index, birth=[123456, index], executable="/fixture/cli"))
            if lane == "codex":
                model = client.append(dict(type="turn_context", payload={"model": "test-model"}, timestamp=utc_now()))
            else:
                model = client.append(client.message("assistant", "ready", at=utc_now()))
            client.session["model_record"] = model
            self.clients[lane] = client
        auth_record = self.clients["codex"].append(self.clients["codex"].message("user", "Implement the plan.", at=utc_now()))
        from orchestration_common import digest
        authorization = write_json(self.root / "authorization.json", dict(source="user_message", approved_root=str(self.root),
            session_ids={k: c.session["session_id"] for k, c in self.clients.items()},
            native_records=[dict(session=copy.deepcopy(self.clients["codex"].session), record=auth_record,
                                 text_sha256=digest(b"Implement the plan."))]))
        for client in self.clients.values():
            client.session["freeze_fence"] = client.reader.snapshot()
        self.plan = dict(version=version - 1, plan_id="fixture-plan", objective="Inspect a frozen input and review it independently",
            source={"generated_by": "supervisor", "revision": "fixture"}, decision_provider="rules",
            api_policy={"third_party_api": False, "model_call_owners": ["codex_cli", "claude_code_cli"]}, nodes=[])
        if version == 3:
            self.plan["executor_providers"] = {"codex": "codex", "claude_a": "claude", "claude_b": "claude"}
        nodes = (("local", "codex", []), ("review", "claude", []), ("closeout", "codex", ["local", "review"]))
        if version == 3:
            nodes = (("local", "codex", []), ("review", "claude_a", []), ("parallel", "claude_b", []),
                     ("followup", "claude_a", ["review"]), ("closeout", "codex", ["local", "followup", "parallel"]))
        for key, lane, deps in nodes:
            self.plan["nodes"].append(dict(id=key, title=key, executor=lane, depends_on=deps,
                execution_kind="local_cli" if lane == "codex" else "delegated_cli",
                transport="codex_cli" if lane == "codex" else "claude_code_cli", session_policy="reuse_existing",
                context="Inspect the input", source=[str(self.root / "input.json")], scope="Write assigned output",
                forbidden="No APIs or replacements", verify="Run the pinned checker", write_scope=key,
                expected_seconds=1, deliverables=["result.json", "report.md"], report="report.md"))
        if unbound:
            from orchestration_binding import POLICY
            self.plan["unbound_executor_policy"] = POLICY
            self.plan["nodes"].append(dict(self.plan["nodes"][0], id="unbound", title="unbound",
                executor=None, execution_kind=None, transport=None, depends_on=[], write_scope="unbound"))
        adapter = private["paths"]["controllers"] / "adapter" if private else base / "adapter"
        controller = Path(private["runtime"]["controller"]["path"]) if private else base / "controller"
        adapter.mkdir()
        controller.mkdir(exist_ok=True)
        (adapter / "scripts").mkdir()
        if not private:
            (controller / "SKILL.md").write_text("offline fixture\n")
        checker = adapter / "scripts" / "checker.py"
        checker.write_text('import json,sys\nfrom pathlib import Path\nr=json.load(sys.stdin)\n'
            'v=json.loads((Path(r["output_root"])/"result.json").read_text())\n'
            'print(json.dumps({"checks":[{"id":"answer", "passed": v == {"answer":42}}]}))\n')
        self.campaign = dict(version=version, evidence_class="offline_fixture", campaign_id="fixture-campaign",
            root=str(self.root), created_at=utc_now(),
            lease_root=str(private["paths"]["state"] / "orchestration-leases" if private else base / "leases"),
            plan=write_json(self.root / "plan.json", self.plan), sessions={k: c.session for k, c in self.clients.items()},
            runs=[dict(run_id=f"run-{i:02d}", strategy=s) for i, s in enumerate(strategies, 1)],
            inputs=[write_json(self.root / "input.json", {"question": "6 * 7"})], authorization=authorization,
            controller=tree_manifest(controller), adapter=tree_manifest(adapter), python=file_ref(Path(sys.executable).resolve()),
            checker=file_ref(checker), checker_timeout_seconds=10, decision_provider="rules",
            metrics=copy.deepcopy(METRICS_V3 if version == 3 else METRICS),
            minimum_improvement_percent=15, third_party_api=False, replacement_sessions=False)
        if private:
            self.campaign.update(experiment=private["context_ref"], runtime=private["runtime_ref"],
                                 arm=arm, client_isolation=None)
        self.ref = write_json(self.root / "campaign.json", self.campaign)

    @staticmethod
    def _private_runtime(base, arm):
        """Minimal explicitly offline manifest; no client-adoption or live bypass."""
        from upgrade_experiment import arm_paths, init_experiment, validate_context
        source = base / "original-controller"
        (source / "scripts").mkdir(parents=True)
        (source / "SKILL.md").write_text("offline fixture\n")
        (source / "scripts/mac_harness.py").write_text("# offline fixture only\n")
        context_ref = init_experiment(base / "lab", "three-lane-fixture", source)
        context = validate_context(context_ref)
        paths = arm_paths(context, arm)
        for path in paths.values():
            path.mkdir()
        controller = paths["controllers"] / "releases/private-v1/source"
        (controller / "scripts").mkdir(parents=True)
        (controller / "SKILL.md").write_text("offline fixture\n")
        (controller / "scripts/mac_harness.py").write_text("# offline fixture only\n")
        helper = paths["bin"] / "cmux-agent"
        helper.write_text("# offline fixture; no transport\n")
        configs = {name: write_json(paths["config"] / name, {"offline_fixture": True})
                   for name in ("codex-hooks.json", "claude-settings.json")}
        runtime = dict(version=1, skill=context["skill"], context=context_ref, arm=arm, execution_mode="offline",
            source_controller=context["source_controller"], controller=tree_manifest(controller),
            release_manifest=write_json(controller.parent / "MANIFEST.json", {"offline_fixture": True}),
            patches=write_json(paths["controllers"] / "patches.json", {"offline_fixture": True}),
            helper=file_ref(helper), configs=configs, state_root=str(paths["state"]), config_root=str(paths["config"]),
            python=context["python"], production_adoption=False)
        runtime_ref = write_json(paths["controllers"] / "runtime.json", runtime)
        return dict(context_ref=context_ref, paths=paths, runtime=runtime, runtime_ref=runtime_ref)

    def save(self):
        self.ref = write_json(self.root / "campaign.json", self.campaign)
        return self.ref

    def write_decision(self, run_id, node_id="unbound", *, path=None, **changes):
        """A supervisor-authored bind decision outside every run tree.

        Defaults describe the off arm (no advice) and the Codex-first route a
        coding node gets when every lane is idle; tests override any field.
        """
        from orchestration_binding import DECISION_SCHEMA
        value = dict(schema=DECISION_SCHEMA, run_id=run_id, node_id=node_id, work_kind="coding",
                     advice_sha256=None, actual_route="codex", action="accepted",
                     reason="offline fixture: the supervisor chose this lane", decided_at=utc_now(),
                     supervisor_session=self.campaign["sessions"]["codex"]["session_id"])
        value.update(changes)
        target = Path(path) if path is not None else self.root / "decisions" / f"{run_id}--{node_id}.json"
        return write_json(target, value, exclusive=True)

    def write_worker_journal(self, co, node_id="unbound", *, entries=None):
        """The worker's own journal; self-authored, never a service receipt."""
        return write_json(co.root / "nodes" / node_id / "advice-worker-journal.json",
                          dict(schema="jev-advice-worker-journal-v1", entries=list(entries or ())),
                          exclusive=True)

    def publish_fixture_advice(self, co, node_id="unbound", *, transport=None, budget=None,
                               model="jev-1.14.0", lanes=None, state_text="route the unbound node"):
        """Run the worker offline and publish its advice as a pinned reference.

        With no transport the worker can only answer NOT_CONFIGURED, which is
        the only reachable live answer in E3. A test may inject a callable to
        exercise the on-arm shape; nothing here contacts any service.
        """
        from jev_advice_worker import advise
        from upgrade_advice_publish import advice_body, advice_marker, advice_tag, publish_advice
        from upgrade_body_read import read_command
        reader = self.clients["codex"].reader
        fence = reader.snapshot()
        answer = advise(state_text=state_text, lanes=lanes if lanes is not None else {},
                        bound=False, budget=budget, transport=transport, model=model)
        advice = answer["advice"]
        run_id, key = co.spec["run_id"], node_id
        marker = advice_marker(run_id, key, advice_tag(advice))
        body = advice_body(self.ref, run_id, key, advice, answer["request"], marker=marker,
                           input_units=self.campaign["inputs"])
        published = publish_advice(co.root, run_id, key, body)
        command = read_command(self.campaign["python"]["path"], self.campaign["controller"]["path"],
                               published["notice"])
        return dict(arm="advisory-off" if advice["status"] == "NOT_CALLED" else "advisory-on",
                    advice_sha256=published["body_sha256"], notice=published["notice"],
                    reference=published["reference"], command=command, fence=fence,
                    not_before_epoch=parse_time(utc_now()).timestamp() - 1,
                    worker_status=advice["status"], worker_journal=answer["journal"],
                    model_requests=answer["model_requests"], body=body)

    def fake_advice_read_records(self, advice, *, call_id="advice-read", at=None, wrapper=True,
                                 output=None, exit_code=0, lane="codex", raw_envelope=None,
                                 command=None, name=None):
        """Append one original-shaped Codex read call/result pair for the advice.

        The output defaults to the real reader's own result for that notice, so
        a positive fixture is produced by the frozen reader rather than by a
        hand-written envelope. Negatives pass ``output`` explicitly.
        """
        from upgrade_body_read import read_result
        client = self.clients[lane]
        when = at or utc_now()
        later = (parse_time(when) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        if output is None:
            output = json.dumps(read_result(advice["notice"]), ensure_ascii=False, sort_keys=True) + "\n"
        args = dict(cmd=command if command is not None else advice["command"],
                    login=False, yield_time_ms=10000, max_output_tokens=12000)
        if lane == "codex":
            call = client.append(dict(type="response_item", timestamp=when, payload=dict(
                type="custom_tool_call" if wrapper else "function_call", call_id=call_id,
                name=name if name is not None else ("exec" if wrapper else "exec_command"),
                **({"input": "text(await tools.exec_command(" + json.dumps(args) + "));\n"} if wrapper
                   else {"arguments": json.dumps(args)}))))
            # raw_envelope is placed in the result verbatim: a measured terminal
            # or token-count envelope must not be re-wrapped by the fixture.
            envelope = raw_envelope if raw_envelope is not None else (
                output if not isinstance(output, str) else
                json.dumps(dict(exit_code=exit_code, output=output, wall_time_seconds=0.1)))
            if wrapper and raw_envelope is None and isinstance(envelope, str):
                envelope = [dict(type="input_text", text="Script completed\nWall time 0.1 seconds\nOutput:\n"),
                            dict(type="input_text", text=envelope)]
            result = client.append(dict(type="response_item", timestamp=later, payload=dict(
                type="custom_tool_call_output" if wrapper else "function_call_output",
                call_id=call_id, output=envelope)))
        else:
            call = client.append(dict(type="assistant", timestamp=when, sessionId=client.session["session_id"],
                message=dict(role="assistant", content=[dict(type="tool_use", id=call_id,
                             name=name if name is not None else "Bash",
                             input=dict(command=command if command is not None else advice["command"]))])))
            result = client.append(dict(type="user", timestamp=later, sessionId=client.session["session_id"],
                message=dict(role="user", content=[dict(type="tool_result", tool_use_id=call_id,
                             is_error=False, content=output)])))
        return dict(call=call, result=result, call_id=call_id, at=when, result_at=later)

    def control(self, action, run_id, **kwargs):
        script = control_script(self.campaign, self.ref, action, run_id, **kwargs)
        return self.clients["codex"].append(dict(type="response_item", timestamp=utc_now(),
            payload={"type": "custom_tool_call", "name": "exec", "input": script,
                     "call_id": "fixture-control-" + str(self.clients["codex"].path.stat().st_size)}))

    def start(self, index=0):
        spec = self.campaign["runs"][index]
        self.control("start", spec["run_id"])
        return Coordinator.create(self.root / "runs" / spec["run_id"], self.ref, spec, fixtures=True)

    def disarm(self, co, key):
        from orchestration_transport import Transport
        runtime = OfflineDisarmRuntime(self.campaign, co.root / "offline-disarm", executor_id=co.nodes[key]["executor"])
        return Transport(co, key, runtime=runtime).disarm()

    def work(self, info, lane="codex"):
        client = self.clients[lane]
        root = Path(info["output_root"])
        call_id = info["task_id"] + "-work"
        if lane == "codex":
            client.append(dict(type="response_item", timestamp=utc_now(), payload=dict(type="custom_tool_call",
                name="exec", call_id=call_id, input=f"write result and report in {root}/result.json")))
        else:
            client.append(dict(type="assistant", timestamp=utc_now(), sessionId=client.session["session_id"],
                message=dict(role="assistant", model="test-model", content=[dict(type="tool_use", name="Write",
                             id=call_id, input={"file_path": str(root / "result.json"), "content": '{"answer":42}'})])))
        write_json(root / "result.json", {"answer": 42})
        (root / "report.md").write_text("Checked the input: 6 * 7 = 42.\n")
        if lane == "codex":
            client.append(dict(type="response_item", timestamp=utc_now(), payload=dict(type="custom_tool_call_output",
                                call_id=call_id, output={"exit_code": 0})))
        else:
            client.append(dict(type="user", timestamp=utc_now(), sessionId=client.session["session_id"],
                message=dict(role="user", content=[dict(type="tool_result", tool_use_id=call_id, content="ok")])) )

    def candidate(self, co, key, protocol=None):
        _, data = co.state()
        node, info = co.nodes[key], data[key]
        manifest, outputs, report = output_refs(self.campaign, co.header, node, info)
        tests = write_json(co.root / "nodes" / key / "tests.json",
                           make_test_result(self.campaign, co.header, node, info, outputs), exclusive=True)
        evidence = dict(version=campaign_version(self.campaign), evidence_class="offline_fixture", **task_identity(self.campaign, co.header, node),
            execution_kind=info["mode"], output_tree=manifest, output_files=outputs, report=report, tests=tests,
            candidate_native_end={k: c.reader.snapshot() for k, c in self.clients.items()})
        if protocol is None:
            evidence["review_kind"] = "solo_self_review"
        else:
            evidence["protocol"] = protocol
        ref = write_json(co.root / "nodes" / key / "candidate.json", evidence, exclusive=True)
        co.transition(key, State.EVIDENCE_READY, {"candidate": ref})
        return evidence

    def accept(self, co, key, evidence):
        _, data = co.state()
        evidence = copy.deepcopy(evidence)
        evidence["acceptance_native"] = self.control("accept", co.spec["run_id"], node_id=key,
                                                     marker=marker_for(data[key], evidence))
        evidence["native_end"] = {k: c.reader.snapshot() for k, c in self.clients.items()}
        ref = write_json(co.root / "nodes" / key / "accepted-evidence.json", evidence, exclusive=True)
        return co.accept(key, ref)

    def identity_gate(self, task_id, lane):
        """Model the original harness gate separately from UUID-only v3 sessions."""
        sender, receiver = self.clients["codex"].session, self.clients[lane].session
        workspace_ref, selector = "workspace:1", receiver["surface_uuid"]
        native_uuid = lambda value: value.upper() if campaign_version(self.campaign) == 3 else value
        proof = dict(caller_surface_uuid=native_uuid(sender["surface_uuid"]),
                     caller_pane_uuid=native_uuid(sender["pane_uuid"]),
                     workspace_uuid=native_uuid(sender["workspace_uuid"]),
                     target_surface_uuid=native_uuid(receiver["surface_uuid"]),
                     target_pane_uuid=native_uuid(receiver["pane_uuid"]),
                     caller_surface_ref=self.surface_refs["codex"],
                     target_surface_ref=self.surface_refs[lane], workspace_ref=workspace_ref)
        return dict(task_id=task_id, status="PASS", workspace_ref=workspace_ref,
                    workspace_uuid=native_uuid(sender["workspace_uuid"]),
                    supervisor=self.surface_refs["codex"], supervisor_provider="codex",
                    supervisor_surface_uuid=native_uuid(sender["surface_uuid"]),
                    supervisor_pane_ref=self.pane_refs["codex"], executor=selector,
                    executor_provider="claude", executor_surface_uuid=native_uuid(receiver["surface_uuid"]),
                    executor_workspace_ref=workspace_ref, executor_pane_ref=self.pane_refs[lane],
                    executors=[dict(surface_ref=selector, provider="claude",
                                    surface_uuid=native_uuid(receiver["surface_uuid"]),
                                    workspace_ref=workspace_ref, pane_ref=self.pane_refs[lane])],
                    workspace_proofs={selector: proof},
                    native_participants=[dict(role="supervisor" if key == "codex" else "executor",
                                              provider=client.session["provider"],
                                              surface_uuid=native_uuid(client.session["surface_uuid"]),
                                              native_session_id=client.session["session_id"])
                                         for key, client in self.clients.items() if key in ("codex", lane)])

    def role_map(self, task_id, lane):
        return dict(task_id=task_id, workspace_ref="workspace:1", updated_at=utc_now(),
                    supervisor=dict(surface_ref=self.surface_refs["codex"], provider="codex", display="SUPERVISOR"),
                    executor=dict(surface_ref=self.clients[lane].session["surface_uuid"], provider="claude", display="EXECUTOR_1"))

    def surface_inventory(self, task_id):
        rows = []
        for lane, client in self.clients.items():
            row = dict(ref=self.surface_refs[lane], surface_id=client.session["surface_uuid"].upper(),
                       workspace_id=client.session["workspace_uuid"].upper(), workspace_ref="workspace:1",
                       pane_ref=self.pane_refs[lane], title=lane, selected=lane == "codex",
                       surface_type="terminal", is_terminal=True, provider=client.session["provider"])
            if lane == "codex":
                row["role"] = "supervisor_candidate"
            rows.append(row)
        return dict(task_id=task_id, workspace_ref="workspace:1", updated_at=utc_now(),
                    surfaces_same_workspace=rows, cross_workspace_rejected=[])

    def prepare(self, co, key):
        _, data = co.state()
        info, node = data[key], co.nodes[key]
        directory = co.root / "nodes" / key
        preflight = directory / "preflight"
        client, sender = self.clients[node["executor"]], self.clients["codex"].session
        nonce = secrets.token_hex(4)
        ack = f"PREFLIGHT_ACK|{info['task_id']}|claude:identity|READY|INLINE|{nonce}"
        notice = "PREFLIGHT " + ack
        preflight.mkdir()
        (preflight / "handshake-notice.txt").write_text(notice)
        delivery = client.delivery(notice, sender=sender, journal=preflight / "handshake-dispatch", at=utc_now())
        ack_ref = client.append(client.message("assistant", ack, at=utc_now()))
        handshake = write_json(preflight / "handshake-receipt.json", dict(task_id=info["task_id"], status="PASS",
            executor_ack=True, ack_nonce=nonce, ack_line=ack))
        gate_body = self.identity_gate(info["task_id"], node["executor"])
        gate = write_json(preflight / "identity-gate.json", gate_body)
        validation = write_json(preflight / "validation.json", dict(task_id=info["task_id"], status="PASS"))
        completion = secrets.token_hex(12)
        report = str(Path(info["output_root"]) / node["report"])
        pack = dict(draft=False, task_id=info["task_id"], executor_uuid=client.session["surface_uuid"],
            supervisor=gate_body["supervisor"], executor=client.session["surface_uuid"], callback_target=gate_body["supervisor"],
            executors=[dict(display="EXECUTOR_1", ordinal=1, provider="claude", surface_ref=client.session["surface_uuid"])],
            orchestration=pack_extension(self.campaign, co.header, node, info), authorization_source="user_message",
            authorization_record=self.campaign["authorization"]["path"],
            required_skill=str(Path(self.campaign["controller"]["path"]) / "SKILL.md"), report=report,
            context=node["context"], source=task_sources(self.campaign, co.header, node, info),
            scope=task_scope(self.campaign, co.header, node, info), forbidden=node["forbidden"], verify=node["verify"],
            executor_model="preserve-current-model", model_command=None,
            completion_delivery=dict(transport="cmux_bridge.submit_completion_callback", require_confirmed=True),
            completion_receipt=str(preflight / "completion-callback-receipt.json"), completion_nonce=completion,
            completion_callback=f"DONE|{info['task_id']}|{completion}|REPORT={report}",
            callback=ack, identity_gate=gate["path"], validation_evidence=validation["path"], finalized_at=utc_now())
        if campaign_version(self.campaign) == 3:
            from orchestration_pack_inputs import pin_inputs
            pack.update(role_map=str(preflight / "role-map.json"), pane_inventory=str(preflight / "surface-inventory.json"))
            write_json(pack["role_map"], self.role_map(info["task_id"], node["executor"]))
            write_json(pack["pane_inventory"], self.surface_inventory(info["task_id"]))
            pack["preparation_inputs"] = pin_inputs(self.campaign, co.header, node, pack)
            pack["finalized_at"] = utc_now()
        pack_ref = write_json(preflight / "task-pack.json", pack)
        (directory / "task-notice.txt").write_text(task_notice(pack_ref, pack))
        proof = dict(pack=pack_ref, notice=file_ref(directory / "task-notice.txt"),
            handshake=dict(receipt=handshake, delivery=delivery, notice=file_ref(preflight / "handshake-notice.txt"), ack_native=ack_ref),
            identity_gate=gate, validation=validation)
        co.transition(key, State.PREPARED, {"prepared": proof})
        return proof

    def intent(self, co, key):
        info = co.state()[1][key]
        value = dict(version=2, operation="submit_task_pack", **task_identity(self.campaign, co.header, co.nodes[key]),
                     prepared=info["prepared"], controller_sha256=object_sha(self.campaign["controller"]),
                     created_at=utc_now(), native_fence=self.clients[co.nodes[key]["executor"]].reader.snapshot())
        ref = write_json(co.root / "nodes" / key / "dispatch-intent.json", value)
        co.transition(key, State.DISPATCH_INTENT, {"dispatch_intent": ref})
        return ref

    def dispatch(self, co, key):
        info = co.state()[1][key]
        notice = read_ref(info["prepared"]["notice"]).decode()
        bundle = self.clients[co.nodes[key]["executor"]].delivery(notice, sender=self.clients["codex"].session,
            journal=co.root / "nodes" / key / "task-dispatch", at=utc_now(), task_id=info["task_id"], marker=info["task_id"],
            task_pack_sha256=info["prepared"]["pack"]["sha256"], payload_sha256=digest(notice.encode()))
        co.transition(key, State.DISPATCHED, {"delivery": bundle})
        return bundle

    def callback(self, co, key):
        info = co.state()[1][key]
        pack = json_ref(info["prepared"]["pack"])
        report = file_ref(pack["report"])
        path = Path(pack["completion_receipt"])
        callback = self.clients["codex"].delivery(pack["completion_callback"], sender=self.clients[co.nodes[key]["executor"]].session,
            journal=path.with_name(path.stem + "-attempts"), receipt_path=path, at=utc_now(),
            task_id=info["task_id"], completion_nonce=pack["completion_nonce"], completion_callback=pack["completion_callback"],
            callback_target=pack["callback_target"], task_pack_sha256=info["prepared"]["pack"]["sha256"],
            report=report["path"], report_sha256=report["sha256"], report_bytes=report["bytes"])
        return dict(prepared=info["prepared"], delivery=info["delivery"], callback=callback)

    def block(self, co, key, reason="observed failure"):
        info = co.state()[1][key]
        ref = write_json(co.root / "nodes" / key / "blocker.json", dict(task_id=info["task_id"], reason=reason, observed_at=utc_now()))
        co.transition(key, State.BLOCKED, {"blocker": ref})
        return ref

    def failure(self, co, key, boundary="local_boundary", **extra):
        info = co.state()[1][key]
        evidence = dict(version=campaign_version(self.campaign), evidence_class="offline_fixture", **task_identity(self.campaign, co.header, co.nodes[key]),
                        blocker=info["blocker"], boundary=boundary, output_tree=tree_manifest(info["output_root"], kind="output"), **extra)
        write_json(co.root / "nodes" / key / "failure-candidate.json", evidence)
        evidence["failure_native"] = self.control("settle", co.spec["run_id"], node_id=key, marker=failure_marker(info, evidence))
        evidence["native_end"] = {k: c.reader.snapshot() for k, c in self.clients.items()}
        return write_json(co.root / "nodes" / key / "settled-failure.json", evidence)
