"""Synthetic journals for offline tests only; never used by live entrypoints."""
import copy
import contextlib
import json
import secrets
import sys
from pathlib import Path

from orchestration_campaign import METRICS, control_script
from orchestration_common import digest, file_ref, json_ref, object_sha, parse_time, read_ref, tree_manifest, utc_now, write_json
from orchestration_coordinator import Coordinator, State
from orchestration_evidence import (failure_marker, make_test_result, marker_for, output_refs, pack_extension,
                                    task_identity, task_notice, task_scope, task_sources)
from test_orchestration_native import NativeFixture


class OfflineDisarmRuntime:
    """Private marker/state files; never imports or calls a shared controller."""

    def __init__(self, campaign, root):
        from types import SimpleNamespace
        from orchestration_disarm import marker_callers
        self.campaign, self.root = campaign, Path(root).resolve()
        self.active, self.states = self.root / "active", self.root / "states"
        self.active.mkdir(parents=True, exist_ok=True)
        self.states.mkdir(parents=True, exist_ok=True)
        self.disarms, self.recoveries = [], []
        self.marker = None
        workspace = campaign["sessions"]["codex"]["workspace_uuid"]
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
                workspace_uuid=self.campaign["sessions"]["codex"]["workspace_uuid"],
                participants=[dict(role="supervisor" if lane == "codex" else "executor",
                                   surface_uuid=session["surface_uuid"])
                              for lane, session in self.campaign["sessions"].items()])
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
    def __init__(self, base, strategies=("omo",)):
        base = Path(base).resolve()
        self.root = base / "campaign"
        self.root.mkdir()
        self.clients = {}
        for lane in ("codex", "claude"):
            directory = base / lane
            directory.mkdir()
            client = NativeFixture(directory, lane)
            client.session.update(surface_uuid=lane + "-surface", pane_uuid=lane + "-pane",
                                  surface_ref="surface:1" if lane == "codex" else "surface:2")
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
        self.plan = dict(version=1, plan_id="fixture-plan", objective="Inspect a frozen input and review it independently",
            source={"generated_by": "supervisor", "revision": "fixture"}, decision_provider="rules",
            api_policy={"third_party_api": False, "model_call_owners": ["codex_cli", "claude_code_cli"]}, nodes=[])
        for key, lane, deps in (("local", "codex", []), ("review", "claude", []), ("closeout", "codex", ["local", "review"])):
            self.plan["nodes"].append(dict(id=key, title=key, executor=lane, depends_on=deps,
                execution_kind="local_cli" if lane == "codex" else "delegated_cli",
                transport="codex_cli" if lane == "codex" else "claude_code_cli", session_policy="reuse_existing",
                context="Inspect the input", source=[str(self.root / "input.json")], scope="Write assigned output",
                forbidden="No APIs or replacements", verify="Run the pinned checker", write_scope=key,
                expected_seconds=1, deliverables=["result.json", "report.md"], report="report.md"))
        adapter, controller = base / "adapter", base / "controller"
        adapter.mkdir()
        controller.mkdir()
        (adapter / "scripts").mkdir()
        (controller / "SKILL.md").write_text("offline fixture\n")
        checker = adapter / "scripts" / "checker.py"
        checker.write_text('import json,sys\nfrom pathlib import Path\nr=json.load(sys.stdin)\n'
            'v=json.loads((Path(r["output_root"])/"result.json").read_text())\n'
            'print(json.dumps({"checks":[{"id":"answer", "passed": v == {"answer":42}}]}))\n')
        self.campaign = dict(version=2, evidence_class="offline_fixture", campaign_id="fixture-campaign",
            root=str(self.root), created_at=utc_now(), lease_root=str(base / "leases"),
            plan=write_json(self.root / "plan.json", self.plan), sessions={k: c.session for k, c in self.clients.items()},
            runs=[dict(run_id=f"run-{i:02d}", strategy=s) for i, s in enumerate(strategies, 1)],
            inputs=[write_json(self.root / "input.json", {"question": "6 * 7"})], authorization=authorization,
            controller=tree_manifest(controller), adapter=tree_manifest(adapter), python=file_ref(Path(sys.executable).resolve()),
            checker=file_ref(checker), checker_timeout_seconds=10, decision_provider="rules",
            metrics=copy.deepcopy(METRICS),
            minimum_improvement_percent=15, third_party_api=False, replacement_sessions=False)
        self.ref = write_json(self.root / "campaign.json", self.campaign)

    def save(self):
        self.ref = write_json(self.root / "campaign.json", self.campaign)
        return self.ref

    def control(self, action, run_id, **kwargs):
        script = control_script(self.campaign, self.ref, action, run_id, **kwargs)
        return self.clients["codex"].append(dict(type="response_item", timestamp=utc_now(),
            payload={"type": "custom_tool_call", "name": "exec", "input": script}))

    def start(self, index=0):
        spec = self.campaign["runs"][index]
        self.control("start", spec["run_id"])
        return Coordinator.create(self.root / "runs" / spec["run_id"], self.ref, spec, fixtures=True)

    def disarm(self, co, key):
        from orchestration_transport import Transport
        runtime = OfflineDisarmRuntime(self.campaign, co.root / "offline-disarm")
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
        evidence = dict(version=2, evidence_class="offline_fixture", **task_identity(self.campaign, co.header, node),
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

    def prepare(self, co, key):
        _, data = co.state()
        info, node = data[key], co.nodes[key]
        directory = co.root / "nodes" / key
        preflight = directory / "preflight"
        client, sender = self.clients["claude"], self.clients["codex"].session
        nonce = secrets.token_hex(4)
        ack = f"PREFLIGHT_ACK|{info['task_id']}|claude:identity|READY|INLINE|{nonce}"
        notice = "PREFLIGHT " + ack
        preflight.mkdir()
        (preflight / "handshake-notice.txt").write_text(notice)
        delivery = client.delivery(notice, sender=sender, journal=preflight / "handshake-dispatch", at=utc_now())
        ack_ref = client.append(client.message("assistant", ack, at=utc_now()))
        handshake = write_json(preflight / "handshake-receipt.json", dict(task_id=info["task_id"], status="PASS",
            executor_ack=True, ack_nonce=nonce, ack_line=ack))
        gate = write_json(preflight / "identity-gate.json", dict(task_id=info["task_id"], status="PASS",
            native_participants=[dict(provider=k, surface_uuid=c.session["surface_uuid"], native_session_id=c.session["session_id"])
                                 for k, c in self.clients.items()]))
        validation = write_json(preflight / "validation.json", dict(task_id=info["task_id"], status="PASS"))
        completion = secrets.token_hex(12)
        report = str(Path(info["output_root"]) / node["report"])
        pack = dict(draft=False, task_id=info["task_id"], executor_uuid=client.session["surface_uuid"],
            supervisor=sender["surface_ref"], executor=client.session["surface_uuid"], callback_target=sender["surface_ref"],
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
                     created_at=utc_now(), native_fence=self.clients["claude"].reader.snapshot())
        ref = write_json(co.root / "nodes" / key / "dispatch-intent.json", value)
        co.transition(key, State.DISPATCH_INTENT, {"dispatch_intent": ref})
        return ref

    def dispatch(self, co, key):
        info = co.state()[1][key]
        notice = read_ref(info["prepared"]["notice"]).decode()
        bundle = self.clients["claude"].delivery(notice, sender=self.clients["codex"].session,
            journal=co.root / "nodes" / key / "task-dispatch", at=utc_now(), task_id=info["task_id"], marker=info["task_id"],
            task_pack_sha256=info["prepared"]["pack"]["sha256"], payload_sha256=digest(notice.encode()))
        co.transition(key, State.DISPATCHED, {"delivery": bundle})
        return bundle

    def callback(self, co, key):
        info = co.state()[1][key]
        pack = json_ref(info["prepared"]["pack"])
        report = file_ref(pack["report"])
        path = Path(pack["completion_receipt"])
        callback = self.clients["codex"].delivery(pack["completion_callback"], sender=self.clients["claude"].session,
            journal=path.with_name(path.stem + "-attempts"), receipt_path=path, at=utc_now(),
            task_id=info["task_id"], completion_nonce=pack["completion_nonce"], completion_callback=pack["completion_callback"],
            callback_target=self.clients["codex"].session["surface_ref"], task_pack_sha256=info["prepared"]["pack"]["sha256"],
            report=report["path"], report_sha256=report["sha256"], report_bytes=report["bytes"])
        return dict(prepared=info["prepared"], delivery=info["delivery"], callback=callback)

    def block(self, co, key, reason="observed failure"):
        info = co.state()[1][key]
        ref = write_json(co.root / "nodes" / key / "blocker.json", dict(task_id=info["task_id"], reason=reason, observed_at=utc_now()))
        co.transition(key, State.BLOCKED, {"blocker": ref})
        return ref

    def failure(self, co, key, boundary="local_boundary", **extra):
        info = co.state()[1][key]
        evidence = dict(version=2, evidence_class="offline_fixture", **task_identity(self.campaign, co.header, co.nodes[key]),
                        blocker=info["blocker"], boundary=boundary, output_tree=tree_manifest(info["output_root"], kind="output"), **extra)
        write_json(co.root / "nodes" / key / "failure-candidate.json", evidence)
        evidence["failure_native"] = self.control("settle", co.spec["run_id"], node_id=key, marker=failure_marker(info, evidence))
        evidence["native_end"] = {k: c.reader.snapshot() for k, c in self.clients.items()}
        return write_json(co.root / "nodes" / key / "settled-failure.json", evidence)
