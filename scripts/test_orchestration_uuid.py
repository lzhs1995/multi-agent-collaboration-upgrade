"""Uppercase controller evidence against lowercase v3 campaigns; offline only."""
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from orchestration_common import ContractError, digest, file_ref, json_ref, object_sha, read_json, utc_now, write_json
from orchestration_disarm import (classify, marker_paths, reservation_key, snapshot, state_paths, validate_frozen)
from orchestration_evidence import dispatch_journal
from orchestration_native import delivery_identity, verify_delivery, verify_no_input
from orchestration_test_support import CampaignFixture, OfflineDisarmRuntime
from orchestration_transport import Transport, handshake_journal
from orchestration_verify import finish_run, verify_campaign
from test_orchestration_native import EPOCH, NativeFixture
from test_orchestration_transport import FakeRuntime


class UpperRuntime(FakeRuntime):
    """Match guard-produced UUIDs while retaining argv selectors verbatim."""
    def __init__(self, fixture, co, *, node_id="review"):
        super().__init__(fixture, co, node_id=node_id)
        workspace = fixture.campaign["sessions"]["codex"]["workspace_uuid"].upper()
        self.native_uuid = str.upper
        self.disarm_harness._workspace_key = lambda: workspace
        task = co.state()[1][node_id]["task_id"]
        self.handshake_nonce = digest(task.encode())[:16]
        # Independent producer spelling/hash; do not mock either lookup helper.
        from orchestration_contracts import state_root
        root = state_root(fixture.campaign)
        caller = fixture.campaign["sessions"]["codex"]["surface_uuid"].upper()
        self.handshake_root = root / "message-dispatch-v1" / digest(json.dumps([caller, self.handshake_nonce], sort_keys=True).encode())
        self.dispatch_root = root / "task-dispatch-v1" / digest(json.dumps([caller, task]).encode())

    def harness(self, argv, operation_root):
        result = super().harness(argv, operation_root)
        root = Path(argv[argv.index("--artifact-root") + 1])
        if argv[3] == "preflight":
            value = read_json(root / "identity-gate.json")
            for row in value["native_participants"]:
                row["surface_uuid"] = row["surface_uuid"].upper()
            write_json(root / "identity-gate.json", value)
        elif argv[3] == "task-pack":
            value = read_json(root / "task-pack.json")
            value["executor_uuid"] = value["executor_uuid"].upper()
            write_json(root / "task-pack.json", value)
        return result


class UUIDNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def fixture(self, provider="claude"):
        root = self.root / provider
        root.mkdir(exist_ok=True)
        f = NativeFixture(root, provider, native_uuid_uppercase=True)
        f.session.update(workspace_uuid=str(uuid.uuid4()), surface_uuid=str(uuid.uuid4()), pane_uuid=str(uuid.uuid4()))
        f.sender.update(workspace_uuid=f.session["workspace_uuid"], surface_uuid=str(uuid.uuid4()), pane_uuid=str(uuid.uuid4()))
        return f

    def test_both_directions_accept_case_only_and_preserve_original_hashes(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                f = self.fixture(provider)
                bundle = f.delivery()
                before = {r["path"]: Path(r["path"]).read_bytes() for r in [bundle["receipt"], *bundle["attempts"]]}
                result = verify_delivery(bundle, f.session, f.sender, "TASK|exact", fixtures=True, uuid_semantics=True)
                self.assertEqual(result["transport_retries"], 0)
                self.assertEqual(before, {p: Path(p).read_bytes() for p in before})
                with self.assertRaisesRegex(ContractError, "binding mismatch"):
                    verify_delivery(bundle, f.session, f.sender, "TASK|exact", fixtures=True)

    def test_changed_malformed_and_missing_native_identities_are_rejected(self):
        f = self.fixture()
        bundle = f.delivery()
        for field in ("workspace_uuid", "surface_uuid", "pane_uuid"):
            for replacement in (str(uuid.uuid4()), "bad-uuid", None):
                with self.subTest(field=field, replacement=replacement):
                    receiver = dict(f.session, **{field: replacement})
                    with self.assertRaises(ContractError):
                        verify_delivery(bundle, receiver, f.sender, "TASK|exact", fixtures=True, uuid_semantics=True)

    def test_case_normalization_does_not_repair_a_changed_original_fence(self):
        f = self.fixture()
        f.delivery()
        for field in f.attempt["native_binding"]["identity"]:
            f.attempt["native_binding"]["identity"][field] = f.attempt["native_binding"]["identity"][field].lower()
        with self.assertRaisesRegex(ContractError, "fence not bound"):
            verify_delivery(f.save(), f.session, f.sender, "TASK|exact", fixtures=True, uuid_semantics=True)

    def test_zero_input_compares_uuid_values_but_keeps_pack_and_notice_exact(self):
        f = self.fixture()
        f.delivery(task_id="task", task_pack_sha256="pack", payload_sha256=digest(b"TASK|exact"))
        f.path.write_bytes(f.path.read_bytes()[:f.attempt["native_binding"]["transcript"]["offset"]])
        f.attempt.update(phase="NO_INPUT", events=[], error="fixture zero-input refusal")
        expected = dict(f.attempt["binding"], identity=delivery_identity(f.session, f.sender))
        refs = [write_json(f.journal / "attempt-0001.json", f.attempt)]
        kwargs = dict(not_before=EPOCH, not_after=EPOCH + 1, fixtures=True, uuid_semantics=True)
        self.assertEqual(verify_no_input(refs, f.session, f.sender, "TASK|exact", expected, **kwargs)["attempts"], 1)
        for field in ("task_id", "task_pack_sha256", "payload_sha256"):
            with self.subTest(field=field), self.assertRaises(ContractError):
                verify_no_input(refs, f.session, f.sender, "TASK|exact", dict(expected, **{field: "changed"}), **kwargs)


class UUIDLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, version=3)
        for client in self.f.clients.values():
            client.native_uuid_uppercase = True

    def test_both_claudes_dispatch_callback_release_and_successor_use_original_uppercase_evidence(self):
        co = self.f.start()
        campaign_before = Path(self.f.ref["path"]).read_bytes()
        runtimes = {}

        def begin(key):
            info = co.claim(key)
            runtime = UpperRuntime(self.f, co, node_id=key)
            transport = Transport(co, key, runtime=runtime)
            transport.prepare()
            self.assertEqual(handshake_journal(self.f.campaign, runtime.handshake_nonce), runtime.handshake_root)
            self.assertEqual(dispatch_journal(self.f.campaign, info), runtime.dispatch_root)
            transport.dispatch()
            runtimes[key] = runtime, transport
            return info

        def complete(key, info):
            self.f.work(info, co.nodes[key]["executor"])
            protocol = self.f.callback(co, key)
            self.f.accept(co, key, self.f.candidate(co, key, protocol))
            _, transport = runtimes[key]
            transport.disarm()
            frozen = read_json(transport.root / "disarm-frozen.json")
            self.assertEqual(frozen["workspace_uuid"], self.f.campaign["sessions"]["codex"]["workspace_uuid"].upper())
            self.assertTrue(all(c == c.upper() for c in frozen["callers"]))

        def local(key):
            info = co.claim(key)
            self.f.work(info)
            self.f.accept(co, key, self.f.candidate(co, key))

        a, b = begin("review"), begin("parallel")
        local("local")
        complete("review", a)
        self.assertIn("followup", [n["id"] for n in co.ready()])
        complete("followup", begin("followup"))
        complete("parallel", b)
        local("closeout")
        result = finish_run(co)
        self.assertEqual((result["status"], result["nodes_accepted"], len(result["disarms"])), ("PASS", 5, 3))
        self.assertFalse(verify_campaign(self.f.ref, fixtures=True)["promotion"]["promoted"])
        self.assertEqual(campaign_before, Path(self.f.ref["path"]).read_bytes())

    def test_disarm_keeps_marker_paths_and_case_sensitive_reservation_keys(self):
        runtime = OfflineDisarmRuntime(self.f.campaign, self.f.root / "upper-disarm", native_uuid_uppercase=True)
        frozen = runtime.freeze_disarm("original-task")
        path = marker_paths(frozen)[0]
        original = path.read_bytes()
        caller = frozen["callers"][0]
        held = state_paths(frozen, caller)[0]
        self.assertNotEqual(held, state_paths(frozen, caller.lower())[0])
        write_json(held, dict(caller_surface_uuid=caller, workspace_uuid=frozen["workspace_uuid"],
            supervisor=frozen["supervisor_uuid"], state="WAITING_REPLY", paused_tasks=[reservation_key(runtime.marker)]))
        self.assertEqual(classify(runtime.observe_disarm(frozen), frozen), "ORIGINAL_MARKER_PRESENT")
        self.assertEqual(path.read_bytes(), original)
        result = runtime.disarm(frozen)
        self.assertEqual(classify(result["after"], frozen), "RELEASED_PROVEN_NOW")
        self.assertEqual(read_json(held)["paused_tasks"], [])
        self.assertFalse(state_paths(frozen, caller.lower())[0].exists())

    def test_campaign_case_tolerance_does_not_allow_marker_or_workspace_substitution(self):
        runtime = OfflineDisarmRuntime(self.f.campaign, self.f.root / "upper-disarm", native_uuid_uppercase=True)
        frozen = runtime.freeze_disarm("original-task")
        workspace = self.f.campaign["sessions"]["codex"]["workspace_uuid"]
        sha = object_sha(self.f.campaign["controller"])
        validate_frozen(frozen, "original-task", workspace, sha, uuid_semantics=True)
        with self.assertRaises(ContractError):
            validate_frozen(frozen, "original-task", str(uuid.uuid4()), sha, uuid_semantics=True)
        marker = copy.deepcopy(runtime.marker)
        marker["workspace_uuid"] = workspace
        write_json(marker_paths(frozen)[0], marker)
        changed = dict(frozen, marker=snapshot(marker_paths(frozen)[0]))
        with self.assertRaisesRegex(ContractError, "marker target changed"):
            validate_frozen(changed, "original-task", workspace, sha, uuid_semantics=True)


if __name__ == "__main__":
    unittest.main()
