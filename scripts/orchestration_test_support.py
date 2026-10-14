"""Synthetic journals for offline tests only; never used by live entrypoints."""
import copy
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
        from types import SimpleNamespace
        from orchestration_transport import Transport
        task = co.state()[1][key]["task_id"]
        marker = dict(path=str(co.root / "offline-active" / (task + ".json")), marker={"task_id": task})
        runtime = SimpleNamespace(disarm=lambda task_id: dict(task_id=task_id,
            workspace_uuid=self.campaign["sessions"]["codex"]["workspace_uuid"],
            removed_markers=1, before=[marker], after=[], observed_at=utc_now()))
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
            supervisor=sender["surface_ref"], executor=client.session["surface_ref"], callback_target=sender["surface_ref"],
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
        ref = write_json(co.root / "nodes" / key / "blocker.json", dict(task_id=info["task_id"], reason=reason))
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
