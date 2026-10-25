"""Offline tests of publication/stop and private-native authentication boundaries.

Synthetic observations below never count as live adoption or benchmark evidence.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, file_ref, json_ref, utc_now, write_json
from orchestration_native import NativeReader
import test_upgrade_live_startup as startup_fixtures
from upgrade_client_isolation import LANES
import upgrade_live_control as control
import upgrade_live_runtime as runtime


@unittest.skipUnless(startup_fixtures.SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(),
                     "requires fixed MAC and cmux wrappers")
class LiveControlTests(unittest.TestCase):
    def setUp(self):
        # Reuse only fixture construction, not the earlier test cases.
        startup_fixtures.MaterializedStartupTests.setUpClass.__func__(self)
        self.addCleanup(self.tmp.cleanup)
        self.assertEqual(self.source_controller, startup_fixtures.SOURCE)

    def observation(self):
        workspace = str(uuid.uuid4())
        sessions, readiness, kernel = {}, {}, {}
        for index, lane in enumerate(LANES):
            provider = "codex" if lane == "codex" else "claude"
            sid = str(uuid.uuid4())
            native_root = Path(self.spec["clients"][lane]["native_root"])
            path = native_root / ("sessions" if lane == "codex" else "projects") / (sid + ".jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            records = ([dict(type="session_meta", payload=dict(id=sid)),
                        dict(type="turn_context", payload=dict(model="fixture-codex"))]
                       if provider == "codex" else
                       [dict(type="assistant", sessionId=sid,
                             message=dict(role="assistant", model="fixture-claude", content=[]))])
            path.write_text("".join(json.dumps(row) + "\n" for row in records))
            st = path.stat()
            session = dict(provider=provider, session_id=sid, workspace_uuid=workspace,
                surface_uuid=str(uuid.uuid4()), pane_uuid=str(uuid.uuid4()), tty="/dev/ttys999",
                process=dict(pid=8000 + index, birth=[1, index], executable="/offline/" + provider),
                native_config_root=str(native_root), model="fixture-" + provider,
                transcript=dict(path=str(path), device=st.st_dev, inode=st.st_ino))
            reader = NativeReader(session, fixtures=True)
            session["freeze_fence"] = reader.snapshot()
            session["transcript"] = copy.deepcopy(session["freeze_fence"])
            session["model_record"] = list(reader.records(0, st.st_size))[-1][0]
            sessions[lane] = session
            readiness[lane] = dict(model=session["model"], model_record=session["model_record"],
                                   hooks={"PreToolUse": [{"fixture": True}], "PostToolUse": [{"fixture": True}]})
            kernel[lane] = dict(pid=session["process"]["pid"], kernel_checked=True)
        kernel["codex-server"] = dict(pid=9999, kernel_checked=True)
        result = dict(schema="upgrade-live-inspection-v2", startup=self.ref, ready=True,
            stopped=False, errors={}, observed_at_ns=time.time_ns(), input_operations=0,
            sessions=sessions, readiness=readiness, kernel=kernel,
            inspector=file_ref(Path(self.spec["controller"]) / "scripts/mac_upgrade_live_inspect.py"),
            codex_capability=dict(thread_id=sessions["codex"]["session_id"],
                path=sessions["codex"]["transcript"]["path"], can_accept_direct_input=True,
                methods=["thread/read", "thread/turns/list"], input_operations=0))
        value = dict(runtime=self.runtime_ref, startup=self.ref, command="experiment-inspect",
            argv=control.harness_argv(self.runtime, self.spec, "experiment-inspect"),
            returncode=0, result=result, input_operations=0)
        ref = write_json(Path(self.spec["adoption_path"]).parent / "observations/fixture/result.json", value)
        proof = dict(schema="upgrade-live-adoption-v2", runtime=self.runtime_ref, startup=self.ref,
                     verified=True, observed_at=utc_now(), inspection=ref, sessions=sessions, input_operations=0)
        return ref, value, proof

    def publish_fixture(self):
        _, _, proof = self.observation()
        runtime.validate_adoption_candidate(self.runtime_ref, proof, runtime=self.runtime)
        return write_json(self.spec["adoption_path"], proof, exclusive=True)

    def test_private_native_reader_accepts_bound_growing_original_only(self):
        proof = self.publish_fixture()
        sessions = runtime.authenticated_sessions(self.runtime_ref, proof)
        for lane, session in sessions.items():
            with self.subTest(lane=lane):
                reader = NativeReader(session)
                first = reader.snapshot()["offset"]
                with Path(session["transcript"]["path"]).open("a") as stream:
                    stream.write(json.dumps(dict(type="fixture_append")) + "\n")
                self.assertGreater(NativeReader(session).snapshot()["offset"], first)
                changed = copy.deepcopy(session)
                changed["native_config_root"] = str(self.root / "forged")
                with self.assertRaisesRegex(ContractError, "NOT_ENROLLED"):
                    NativeReader(changed)
                changed = copy.deepcopy(session)
                changed["private_native"]["adoption"]["path"] = str(self.root / "forged-adoption.json")
                with self.assertRaisesRegex(ContractError, "EXTERNAL_ADOPTION_REQUIRED"):
                    NativeReader(changed)
                changed = copy.deepcopy(session)
                del changed["private_native"]
                with self.assertRaisesRegex(ContractError, "private adoption required"):
                    NativeReader(changed)

    def test_task_pack_adoption_binds_every_original_session_and_keeps_live_fixture_boundary(self):
        from orchestration_pack_inputs import pin_inputs, verify_inputs
        from orchestration_test_support import CampaignFixture
        proof = self.publish_fixture()
        fixture = CampaignFixture(self.root / "task-pack-fixture", version=3)
        co = fixture.start()
        info = co.claim("review")
        prepared = fixture.prepare(co, "review")
        pack = json_ref(prepared["pack"])
        fixture.campaign.update(runtime=self.runtime_ref, client_isolation=proof,
                                sessions=runtime.authenticated_sessions(self.runtime_ref, proof))
        for lane, session in fixture.campaign["sessions"].items():
            fixture.clients[lane].session = session
        gate = fixture.identity_gate(info["task_id"], "claude_a")
        write_json(pack["role_map"], fixture.role_map(info["task_id"], "claude_a"))
        write_json(pack["pane_inventory"], fixture.surface_inventory(info["task_id"]))
        pack["preparation_inputs"] = pin_inputs(fixture.campaign, co.header, co.nodes["review"], pack)
        pack["finalized_at"] = utc_now()
        args = (fixture.campaign, co.header, co.nodes["review"], info, pack, gate)
        verify_inputs(*args)
        with self.assertRaisesRegex(ContractError, "cannot impersonate live adoption"):
            verify_inputs(*args, fixtures=True)
        for lane in fixture.campaign["sessions"]:
            session = fixture.campaign["sessions"][lane]
            original_model = session["model"]
            session["model"] = "changed-model"
            with self.subTest(lane=lane), self.assertRaisesRegex(ContractError, "adoption original sessions changed"):
                verify_inputs(*args)
            session["model"] = original_model
        pack["preparation_inputs"]["client_adoption"]["executor_id"] = "claude_b"
        with self.assertRaisesRegex(ContractError, "adoption differs"):
            verify_inputs(*args)
        pack["preparation_inputs"]["client_adoption"]["executor_id"] = "claude_a"
        # A copied verified flag does not waive the original inspection proof.
        Path(proof["path"]).write_text('{"verified":true}')
        with self.assertRaises(ContractError):
            verify_inputs(*args)

    def test_changed_session_or_replaced_transcript_is_rejected(self):
        proof = self.publish_fixture()
        session = runtime.authenticated_sessions(self.runtime_ref, proof)["codex"]
        changed = copy.deepcopy(session)
        changed["surface_uuid"] = str(uuid.uuid4())
        with self.assertRaisesRegex(ContractError, "NOT_ENROLLED"):
            NativeReader(changed)
        path = Path(session["transcript"]["path"])
        backup = path.with_suffix(".original")
        path.rename(backup)
        path.write_bytes(backup.read_bytes())
        with self.assertRaisesRegex(ContractError, "transcript replaced"):
            NativeReader(session)

    def test_adoption_failure_does_not_publish_final_proof(self):
        ref, value, _ = self.observation()
        write_json(self.paths["state"] / "startup/intent.json", {"fixture": True})
        with patch.object(control, "invocation", return_value=(ref, value)), \
             patch.object(control, "external_publisher"), \
             patch.object(control, "validate_adoption_candidate", side_effect=ContractError("fixture failure")):
            with self.assertRaisesRegex(ContractError, "fixture failure"):
                control.control(self.runtime_ref, "adopt")
        self.assertFalse(Path(self.spec["adoption_path"]).exists())

    def test_adoption_validates_before_publication_and_status_sends_nothing(self):
        ref, value, _ = self.observation()
        write_json(self.paths["state"] / "startup/intent.json", {"fixture": True})
        original = control.validate_adoption_candidate

        def validate(*args, **kwargs):
            self.assertFalse(Path(self.spec["adoption_path"]).exists())
            return original(*args, **kwargs)

        with patch.object(control, "invocation", return_value=(ref, value)) as invoke, \
             patch.object(control, "external_publisher"), \
             patch.object(control, "validate_adoption_candidate", side_effect=validate):
            adopted = control.control(self.runtime_ref, "adopt")
            self.assertEqual(adopted["status"], "ADOPTED")
            observed = control.control(self.runtime_ref, "status")
            self.assertEqual(observed["input_operations"], 0)
            self.assertEqual(invoke.call_args.args[-1], "experiment-inspect")
            self.assertIs(invoke.call_args.kwargs["persist"], False)
        runtime.validate_adoption(self.runtime_ref, adopted["adoption"])

    def test_stop_scope_names_the_pinned_controller_for_the_real_publisher_check(self):
        # u10's stop refused with KeyError 'controller': external_publisher()
        # was never reached unpatched. Only the kernel query is replaced here.
        _, scope = control.stop_scope(self.runtime_ref)
        self.assertEqual(scope["controller"], self.spec["controller"])
        with patch.object(control, "sandbox_check", return_value=False) as query:
            stopped = control.control(self.runtime_ref, "stop")
        self.assertEqual(stopped["status"], "STOPPED_INPUT")
        self.assertEqual(query.call_args.args[1:], (self.spec["controller"], "file-write-create"))
        with patch.object(control, "sandbox_check", return_value=True):
            with self.assertRaisesRegex(ContractError, "CLIENT_CANNOT_PUBLISH_ADOPTION"):
                control.control(self.runtime_ref, "stop")

    def test_client_cannot_publish_adoption(self):
        with patch.object(control, "sandbox_check", return_value=True):
            with self.assertRaisesRegex(ContractError, "CLIENT_CANNOT_PUBLISH_ADOPTION"):
                control.external_publisher(self.spec)

    def test_launch_timeout_keeps_original_attempt_no_retry(self):
        with patch.object(control, "external_publisher"), \
             patch.object(control, "invocation", return_value=(None, {"returncode": None})) as invoke:
            self.assertEqual(control.control(self.runtime_ref, "launch")["status"], "LAUNCH_UNCONFIRMED")
            with self.assertRaises((FileExistsError, ContractError)):
                control.control(self.runtime_ref, "launch")
            self.assertEqual(invoke.call_count, 1)

    def test_stop_precedes_integrity_diagnostics_and_preserves_evidence(self):
        proof = self.publish_fixture()
        original = Path(proof["path"]).read_bytes()

        def fail(_):
            self.assertTrue(Path(self.spec["stop_path"]).is_file())
            raise ContractError("changed inspector")

        with patch.object(control, "validate_live_runtime", side_effect=fail), \
             patch.object(control, "external_publisher"):
            stopped = control.control(self.runtime_ref, "stop")
            self.assertEqual(stopped["status"], "STOPPED_INPUT")
            self.assertEqual(stopped["runtime_diagnostic"], "changed inspector")
            self.assertEqual(control.control(self.runtime_ref, "stop")["stop"], stopped["stop"])
        self.assertEqual(Path(proof["path"]).read_bytes(), original)
        session = copy.deepcopy(json_ref(proof)["sessions"]["codex"])
        session["private_native"] = dict(runtime=self.runtime_ref, adoption=proof)
        NativeReader(session)  # Original evidence remains readable after stop.
        with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
            runtime.authenticated_sessions(self.runtime_ref, proof)

    def test_adoption_rejects_input_rpc_and_unverified_server_kernel(self):
        _, _, proof = self.observation()
        for mutation in (lambda v: v["result"]["codex_capability"].update(methods=["turn/start"]),
                         lambda v: v["result"]["kernel"]["codex-server"].update(kernel_checked=False),
                         lambda v: v.update(input_operations=1)):
            value = json_ref(proof["inspection"])
            mutation(value)
            path = Path(proof["inspection"]["path"]).with_name(str(uuid.uuid4()) + ".json")
            candidate = dict(proof, inspection=write_json(path, value))
            with self.assertRaises(ContractError):
                runtime.validate_adoption_candidate(self.runtime_ref, candidate, runtime=self.runtime)


if __name__ == "__main__":
    unittest.main()
