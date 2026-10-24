"""Invocation-local authority reuse never supplies action permission or stale pins."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from orchestration_common import ContractError
from orchestration_native import NativeReader, native_verification
import test_upgrade_live_control as control_fixtures
import test_upgrade_live_startup as startup_fixtures
import upgrade_live_runtime as runtime


class ObservationScopeTests(unittest.TestCase):
    def setUp(self):
        self.authority = {"runtime": {"path": "/fixture/runtime"},
                          "adoption": {"path": "/fixture/adoption"}}
        self.value = ({"context": 1}, {"runtime": 1}, {"sessions": {"claude_a": {"model": "one"}}})

    def read(self):
        return runtime._observed_native_authority(self.authority)

    def test_nested_verifications_share_only_this_operation_and_recheck_before_return(self):
        @native_verification
        def inner():
            return self.read()

        @native_verification
        def outer():
            self.read()
            inner()
            inner()

        with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
            outer()
            self.assertEqual(read.call_count, 2)
            outer()
            self.assertEqual(read.call_count, 4)
            self.read()
            self.read()
            self.assertEqual(read.call_count, 6)
        self.assertIsNone(runtime._native_authorities.get())

    def test_changed_authority_at_exit_refuses_result_and_drops_scope(self):
        changed = copy.deepcopy(self.value)
        changed[2]["sessions"]["claude_a"]["model"] = "two"
        with patch.object(runtime, "_read_native_authority", side_effect=[self.value, changed]):
            with self.assertRaisesRegex(ContractError, "CHANGED_DURING_VERIFICATION"):
                with runtime.native_authority_observation():
                    self.read()
        self.assertIsNone(runtime._native_authorities.get())

    def test_operation_and_final_recheck_exceptions_do_not_leak_cached_authority(self):
        for during_exit in (False, True):
            with self.subTest(during_exit=during_exit):
                outcomes = [self.value, ContractError("changed pin")] if during_exit else [self.value]
                with patch.object(runtime, "_read_native_authority", side_effect=outcomes):
                    with self.assertRaises(ContractError):
                        with runtime.native_authority_observation():
                            self.read()
                            if not during_exit:
                                raise ContractError("verification failed")
                self.assertIsNone(runtime._native_authorities.get())
                with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
                    with runtime.native_authority_observation():
                        self.read()
                    self.assertEqual(read.call_count, 2)

    def test_returned_value_cannot_mutate_authoritative_comparison_snapshot(self):
        with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
            with runtime.native_authority_observation():
                value = self.read()
                value[2]["sessions"]["claude_a"]["model"] = "forged"
                self.assertEqual(self.read(), self.value)
            self.assertEqual(read.call_count, 2)

    def test_each_distinct_authority_gets_full_entry_and_exit_validation(self):
        original = copy.deepcopy(self.authority)
        seen = []
        def observe(authority):
            seen.append(copy.deepcopy(authority))
            return self.value
        with patch.object(runtime, "_read_native_authority", side_effect=observe):
            with runtime.native_authority_observation():
                self.read()
                self.authority["adoption"]["path"] += "-different"
                self.read()
        self.assertEqual(seen, [original, self.authority, original, self.authority])

    def test_copied_context_does_not_share_authority_across_threads(self):
        def worker():
            with runtime.native_authority_observation():
                self.read()
                self.read()
        with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
            with runtime.native_authority_observation():
                self.read()
                context = copy_context()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(context.run, worker).result(timeout=10)
                self.read()
            self.assertEqual(read.call_count, 4)

    def test_saved_same_owner_context_cannot_reuse_an_ended_scope(self):
        for fail in (False, True):
            with self.subTest(exception_exit=fail):
                saved = None
                with patch.object(runtime, "_read_native_authority", return_value=self.value):
                    try:
                        with runtime.native_authority_observation():
                            self.read()
                            saved = copy_context()
                            if fail:
                                raise ContractError("original operation failed")
                    except ContractError:
                        self.assertTrue(fail)
                changed = copy.deepcopy(self.value)
                changed[2]["sessions"]["claude_a"]["model"] = "new-authority"
                @native_verification
                def reread():
                    return self.read()
                with patch.object(runtime, "_read_native_authority", return_value=changed) as read:
                    self.assertEqual(saved.run(self.read), changed)
                    self.assertEqual(read.call_count, 1)
                    self.assertEqual(saved.run(reread), changed)
                    self.assertEqual(read.call_count, 3)
                self.assertIsNone(runtime._native_authorities.get())

    def test_inherited_context_does_not_share_authority_across_async_tasks(self):
        async def worker():
            with runtime.native_authority_observation():
                self.read()
                self.read()
        async def parent():
            with runtime.native_authority_observation():
                self.read()
                await asyncio.create_task(worker())
                self.read()
        with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
            asyncio.run(parent())
            self.assertEqual(read.call_count, 4)

    def test_published_reverification_answers_later_reads_without_reading_again(self):
        # Live 2026-10-24: one private-root check took 2.8-6.7 s and an Agent
        # hook needed two, so a 6 s reply budget ran out before reserving.
        published = []
        with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
            with runtime.native_authority_observation(publish=published.append):
                self.read()
            self.assertEqual((read.call_count, len(published)), (2, 1))
            with runtime.verified_native_authority(published[0]):
                self.assertEqual(self.read(), self.value)
                self.read()
                with runtime.native_authority_observation():
                    self.read()
            self.assertEqual(read.call_count, 2)
            self.read()
            self.assertEqual(read.call_count, 3)
        self.assertIsNone(runtime._native_authorities.get())

    def test_verified_scope_refuses_an_authority_outside_its_reverification(self):
        published = []
        with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
            with runtime.native_authority_observation(publish=published.append):
                self.read()
            other = copy.deepcopy(self.authority)
            other["adoption"]["path"] += "-different"
            with runtime.verified_native_authority(published[0]):
                with self.assertRaisesRegex(ContractError, "NOT_REVERIFIED"):
                    runtime._observed_native_authority(other)
            self.assertEqual(read.call_count, 2)

    def test_only_a_completed_exit_recheck_can_be_published(self):
        changed = copy.deepcopy(self.value)
        changed[2]["sessions"]["claude_a"]["model"] = "two"
        published = []
        with patch.object(runtime, "_read_native_authority", side_effect=[self.value, changed]):
            with self.assertRaisesRegex(ContractError, "CHANGED_DURING_VERIFICATION"):
                with runtime.native_authority_observation(publish=published.append):
                    self.read()
        self.assertEqual(published, [])
        key = json.dumps(self.authority, sort_keys=True)
        for forged in ({}, {key: (self.authority, self.value)}):
            with self.subTest(forged=bool(forged)):
                with self.assertRaisesRegex(ContractError, "NOT_REVERIFIED"):
                    with runtime.verified_native_authority(forged):
                        pass
        with runtime.native_authority_observation():
            with self.assertRaisesRegex(ContractError, "PUBLISH_NESTED"):
                with runtime.native_authority_observation(publish=published.append):
                    pass
        self.assertEqual(published, [])

    def test_verified_snapshot_does_not_answer_another_thread_or_a_nested_install(self):
        published = []
        with patch.object(runtime, "_read_native_authority", return_value=self.value) as read:
            with runtime.native_authority_observation(publish=published.append):
                self.read()
            with runtime.verified_native_authority(published[0]):
                context = copy_context()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(context.run, self.read).result(timeout=10)
                self.assertEqual(read.call_count, 3)
                with self.assertRaisesRegex(ContractError, "SCOPE_NESTED"):
                    with runtime.verified_native_authority(published[0]):
                        pass
        self.assertIsNone(runtime._native_authorities.get())


@unittest.skipUnless(startup_fixtures.SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir(),
                     "requires fixed MAC and cmux wrappers")
class ObservationFileBoundaryTests(unittest.TestCase):
    observation = control_fixtures.LiveControlTests.observation
    publish_fixture = control_fixtures.LiveControlTests.publish_fixture

    def setUp(self):
        startup_fixtures.MaterializedStartupTests.setUpClass.__func__(self)
        self.addCleanup(self.tmp.cleanup)
        self.assertEqual(self.source_controller, startup_fixtures.SOURCE)
        self.proof_ref = self.publish_fixture()
        self.sessions = runtime.authenticated_sessions(self.runtime_ref, self.proof_ref)

    def test_multiple_readers_reuse_authority_but_each_reads_its_original_journal(self):
        with patch.object(runtime, "_read_native_authority", wraps=runtime._read_native_authority) as read:
            with runtime.native_authority_observation():
                for session in self.sessions.values():
                    before = NativeReader(session).snapshot()
                    with Path(session["transcript"]["path"]).open("a") as stream:
                        stream.write(json.dumps({"type": "offline_fixture_append"}) + "\n")
                    self.assertGreater(NativeReader(session).snapshot()["offset"], before["offset"])
                self.assertEqual(read.call_count, 1)
            self.assertEqual(read.call_count, 2)

    def test_original_runtime_startup_adoption_controller_and_client_pins_rechecked_at_exit(self):
        paths = [Path(self.runtime_ref["path"]), Path(self.ref["path"]), Path(self.proof_ref["path"]),
                 Path(self.spec["controller"]) / "scripts/mac_upgrade_live_inspect.py",
                 Path(self.spec["pins"]["claude_a/settings.json"]["path"])]
        for path in paths:
            with self.subTest(path=path.name):
                saved = path.read_bytes()
                mode = path.stat().st_mode & 0o777
                try:
                    with self.assertRaises((ContractError, ValueError)):
                        with runtime.native_authority_observation():
                            NativeReader(self.sessions["claude_a"])
                            # Deliberately corrupt only this test's private copy;
                            # restore permissions so the content pin is exercised.
                            path.chmod(mode | 0o200)
                            path.write_bytes(saved + b" ")
                            path.chmod(mode)
                finally:
                    path.chmod(mode | 0o200)
                    path.write_bytes(saved)
                    path.chmod(mode)
                self.assertIsNone(runtime._native_authorities.get())
                NativeReader(self.sessions["claude_a"])

    def test_new_session_identity_and_original_fence_are_never_reused_from_scope(self):
        original = self.sessions["claude_a"]
        with runtime.native_authority_observation():
            reader = NativeReader(original)
            changed = copy.deepcopy(original)
            changed["process"]["pid"] += 1
            with self.assertRaisesRegex(ContractError, "NOT_ENROLLED"):
                NativeReader(changed)
            pin = reader.snapshot()
            path = Path(original["transcript"]["path"])
            saved = path.read_bytes()
            try:
                path.write_bytes(b"{}\n")
                with self.assertRaises(ContractError):
                    NativeReader(original).verify_pin(pin)
            finally:
                path.write_bytes(saved)

    def test_stop_remains_a_fresh_input_gate_inside_readonly_scope(self):
        with runtime.native_authority_observation():
            NativeReader(self.sessions["claude_a"])
            Path(self.spec["stop_path"]).write_text("operator stop\n")
            with self.assertRaisesRegex(ContractError, "STOP_REQUESTED"):
                runtime.live_gate_v2(self.context_ref, self.runtime_ref, self.sessions, self.proof_ref)
            # Historic evidence remains inspectable after Stop, without permission to act.
            NativeReader(self.sessions["claude_b"])


if __name__ == "__main__":
    unittest.main()
