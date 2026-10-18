"""Offline behavior controls for construction reuse; no live model or transport calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestration_common import ContractError, canonical, file_ref, parse_time, read_json, write_json
from orchestration_coordinator import Coordinator
from orchestration_native import NativeReader
from orchestration_run import Runner
from orchestration_test_support import CampaignFixture
from orchestration_transport import PinnedRuntime, Transport
from test_orchestration_native import NativeFixture
from test_orchestration_transport import FakeRuntime


class ConstructionReuseBoundaries(unittest.TestCase):
    @staticmethod
    def native_bytes(f):
        return {lane: c.path.read_bytes() for lane, c in f.clients.items()}

    def test_original_start_clocks_fences_and_native_input_are_preserved(self):
        for version in (2, 3):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                f = CampaignFixture(directory, version=version)
                r = Runner(f.ref, fixtures=True)
                proof = f.control("start", "run-01")
                native_before = self.native_bytes(f)
                starts = {lane: c.reader.snapshot() for lane, c in f.clients.items()}
                original_create, created = Coordinator.create, []
                def create(*args, **kwargs):
                    co = original_create(*args, **kwargs)
                    created.append(co)
                    return co
                with patch("orchestration_run.Coordinator.create", side_effect=create), \
                     patch("orchestration_coordinator.time.monotonic_ns", return_value=123456789):
                    state = r.start("run-01")
                self.assertEqual(len(created), 1)
                co = created[0]
                header_before = file_ref(co.root / "run.json")
                header = read_json(co.root / "run.json")
                self.assertEqual(header, co.header)
                self.assertEqual(header["start_native"], proof)
                self.assertEqual(header["native_start"], starts)
                self.assertEqual(header["started_monotonic_ns"], 123456789)
                original_at = f.clients["codex"].reader.record(proof)["timestamp"]
                self.assertEqual(parse_time(header["started_at"]), parse_time(original_at))
                self.assertGreaterEqual(parse_time(header["created_at"]), parse_time(original_at))
                self.assertEqual(state, r.status("run-01"))
                self.assertEqual(header_before, file_ref(co.root / "run.json"))
                self.assertEqual(native_before, self.native_bytes(f))
                self.assertFalse((co.root / "events").exists())
                with self.assertRaisesRegex(ContractError, "original run exists"):
                    r.start_script("run-01")
                self.assertEqual(header_before, file_ref(co.root / "run.json"))
                self.assertEqual(native_before, self.native_bytes(f))

    def test_start_rereads_changed_or_symlinked_header_after_create(self):
        for version in (2, 3):
            for mutation in ("content", "symlink"):
                with self.subTest(version=version, mutation=mutation), tempfile.TemporaryDirectory() as directory:
                    f = CampaignFixture(directory, version=version)
                    r = Runner(f.ref, fixtures=True)
                    f.control("start", "run-01")
                    native_before = self.native_bytes(f)
                    original_create, evidence = Coordinator.create, {}
                    def create(*args, **kwargs):
                        co = original_create(*args, **kwargs)
                        header = co.root / "run.json"
                        original = co.root / "original-header.json"
                        original.write_bytes(header.read_bytes())
                        evidence["original"] = file_ref(original)
                        if mutation == "content":
                            value = read_json(header)
                            value["boot_id"] = "changed-after-validation"
                            write_json(header, value)
                        else:
                            header.unlink()
                            header.symlink_to(original)
                        return co
                    with patch("orchestration_run.Coordinator.create", side_effect=create):
                        with self.assertRaises(ContractError):
                            r.start("run-01")
                    self.assertEqual(evidence["original"], file_ref(evidence["original"]["path"]))
                    self.assertEqual(native_before, self.native_bytes(f))
                    self.assertFalse((f.root / "runs/run-01/events").exists())

    def test_public_status_and_claim_recheck_same_size_changed_pins(self):
        for version in (2, 3):
            for action in ("status", "claim"):
                for target in ("controller", "adapter", "input"):
                    with self.subTest(version=version, action=action, target=target), tempfile.TemporaryDirectory() as directory:
                        f = CampaignFixture(directory, version=version)
                        r = Runner(f.ref, fixtures=True)
                        f.control("start", "run-01")
                        r.start("run-01")
                        paths = {"controller": Path(f.campaign["controller"]["path"]) / "SKILL.md",
                                 "adapter": Path(f.campaign["checker"]["path"]),
                                 "input": Path(f.campaign["inputs"][0]["path"])}
                        victim = paths[target]
                        raw = victim.read_bytes()
                        victim.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
                        before = self.native_bytes(f)
                        if action == "status":
                            call = lambda: r.status("run-01")
                        else:
                            call = lambda: r.claim("run-01", "local")
                        with self.assertRaises(ContractError):
                            call()
                        self.assertEqual(before, self.native_bytes(f))
                        root = f.root / "runs/run-01"
                        self.assertFalse((root / "claims").exists())
                        self.assertFalse((root / "events").exists())

    def test_retained_coordinator_claim_rechecks_before_creating_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            f = CampaignFixture(directory, version=3)
            co = f.start()
            victim = Path(f.campaign["controller"]["path"]) / "SKILL.md"
            raw = victim.read_bytes()
            victim.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            before = self.native_bytes(f)
            with self.assertRaises(ContractError):
                co.claim("local")
            self.assertFalse((co.root / "claims").exists())
            self.assertFalse((co.root / "events").exists())
            self.assertEqual(before, self.native_bytes(f))

    def test_transport_rechecks_before_harness_or_submit(self):
        for boundary in ("prepare", "dispatch"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                f = CampaignFixture(directory)
                co = f.start()
                co.claim("review")
                runtime = FakeRuntime(f, co)
                transport = Transport(co, "review", runtime=runtime)
                with patch("orchestration_transport.handshake_journal", return_value=runtime.handshake_root), \
                     patch("orchestration_transport.dispatch_journal", return_value=runtime.dispatch_root):
                    if boundary == "dispatch":
                        transport.prepare()
                    calls_before = list(runtime.calls)
                    natives_before = self.native_bytes(f)
                    events_before = {p.name: p.read_bytes() for p in (co.root / "events").glob("*.json")}
                    victim = Path(f.campaign["controller"]["path"]) / "SKILL.md"
                    raw = victim.read_bytes()
                    victim.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
                    with self.assertRaises(ContractError):
                        getattr(transport, boundary)()
                    self.assertEqual(runtime.calls, calls_before)
                    self.assertEqual(natives_before, self.native_bytes(f))
                    self.assertEqual(events_before, {p.name: p.read_bytes() for p in (co.root / "events").glob("*.json")})
                    self.assertFalse((transport.root / "dispatch-intent.json").exists())

    def test_pinned_runtime_rechecks_before_subprocess_and_module_import(self):
        for boundary in ("harness", "modules"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                f = CampaignFixture(directory)
                runtime = PinnedRuntime(f.campaign)
                victim = Path(f.campaign["controller"]["path"]) / "SKILL.md"
                raw = victim.read_bytes()
                victim.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
                with patch("orchestration_transport.subprocess.run") as external:
                    with self.assertRaises(ContractError):
                        if boundary == "harness":
                            runtime.harness(["never-execute"], f.root)
                        else:
                            with runtime.modules("never_import"):
                                self.fail("mutated controller allowed import")
                    external.assert_not_called()
                self.assertFalse((f.root / "stdout.txt").exists())

    def test_second_metadata_read_catches_mutation_outside_eof_fence_hashes(self):
        original_fixture = NativeFixture
        def padded_fixture(*args, **kwargs):
            client = original_fixture(*args, **kwargs)
            if client.provider == "codex":
                metadata = client.path.read_bytes()
                padding = canonical({"padding": "x" * 6000})
                client.path.write_bytes(padding + metadata + padding)
            return client
        with tempfile.TemporaryDirectory() as directory, \
             patch("orchestration_test_support.NativeFixture", side_effect=padded_fixture):
            f = CampaignFixture(directory, version=3)
            co = f.start()
            c = f.clients["codex"]
            inode_before = c.path.stat().st_ino
            from orchestration_coordinator import native_control as real_native_control
            mutations = []
            def mutate_after_control(*args, **kwargs):
                result = real_native_control(*args, **kwargs)
                raw = c.path.read_bytes()
                sid = c.session["session_id"].encode()
                replacement = b"0" * 8 + sid[8:]
                self.assertNotEqual(sid, replacement)
                start = raw.index(b'"id":"' + sid) + len(b'"id":"')
                self.assertGreater(start, 4096)
                self.assertLess(start + len(sid), co.header["native_start"]["codex"]["tail_at"])
                raw = raw[:start] + replacement + raw[start + len(sid):]
                c.path.write_bytes(raw)
                mutations.append(file_ref(c.path))
                # The EOF pin alone cannot see this middle-of-file metadata edit.
                c.reader.verify_pin(co.header["native_start"]["codex"])
                return result
            with patch("orchestration_coordinator.native_control", side_effect=mutate_after_control):
                with self.assertRaisesRegex(ContractError, "native metadata session mismatch"):
                    Coordinator(co.root, f.ref, fixtures=True)
            self.assertEqual(len(mutations), 1)
            self.assertEqual(inode_before, c.path.stat().st_ino)
            self.assertEqual(mutations[0], file_ref(c.path))

    def test_reused_reader_checks_growth_replacement_truncation_and_fence(self):
        for mutation in ("grow", "replace", "truncate", "fence"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                c = NativeFixture(directory, "codex")
                reader = NativeReader(c.session, fixtures=True)
                pin = reader.snapshot()
                if mutation == "grow":
                    c.append(c.message("user", "original session grows"))
                    reader.verify_metadata()
                    self.assertEqual(reader.verify_pin(pin), pin["offset"])
                    continue
                if mutation == "replace":
                    original = c.path.with_suffix(".preserved")
                    c.path.rename(original)
                    c.path.write_bytes(original.read_bytes())
                elif mutation == "truncate":
                    c.path.write_bytes(b"{}\n")
                else:
                    raw = c.path.read_bytes()
                    c.path.write_bytes(raw.replace(b"session_meta", b"session_metz", 1))
                with self.assertRaises(ContractError):
                    reader.verify_pin(pin)


if __name__ == "__main__":
    unittest.main()
