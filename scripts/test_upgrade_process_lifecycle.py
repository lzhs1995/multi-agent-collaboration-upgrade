"""Original process lifecycle diagnostics, never API health or takeover.

Synthetic registered-controller fixtures and mocked libproc syscalls are
explicitly offline. No model, terminal input, child creation or resume.
"""
from contextlib import redirect_stderr, redirect_stdout
import ctypes
import errno
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import cmux_daemon_identity as identity
import upgrade_continuity_native as continuity
import upgrade_process_inventory as inventory
from orchestration_common import file_ref, json_ref, write_json
import test_upgrade_continuity_native as native_fixtures
from test_upgrade_live_startup import MODERN_SOURCE


@unittest.skipUnless(MODERN_SOURCE.is_dir(), "requires fixed original MAC0.4.58")
class RegisteredProcessLifecycleTests(unittest.TestCase):
    setUp = native_fixtures.RegisteredNativeContinuityTests.setUp
    source_controller = MODERN_SOURCE
    continuity_registration = True

    def test_stable_original_pid_absence_is_readonly_not_a_fresh_api_success(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        before_registration = json_ref(registration)
        before_native = {lane: journal.path.read_bytes() for lane, journal in self.journals.items()}
        calls = []

        def bsd(pid, kind, arg, pointer, size):
            calls.append((pid, kind))
            self.assertEqual(kind, 3)
            if pid == self.sessions["codex"]["process"]["pid"]:
                ctypes.set_errno(errno.ESRCH)
                return 0
            expected = next(session["process"] for session in self.sessions.values()
                            if session["process"]["pid"] == pid)
            row = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
            row.pid, row.uid, row.ppid, row.status = pid, inventory.os.getuid(), 7, 1
            row.start_sec, row.start_usec = expected["birth"]
            row.name = b"fixture-claude"
            ctypes.set_errno(0)
            return size

        def executable(pid, buffer, size):
            expected = next(session["process"] for session in self.sessions.values()
                            if session["process"]["pid"] == pid)
            buffer.value = expected["executable"].encode()
            ctypes.set_errno(0)
            return len(buffer.value) + 1

        library = types.SimpleNamespace(proc_pidinfo=bsd, proc_pidpath=executable)
        with patch.object(inventory, "_lib", return_value=library):
            result = continuity.observe_registered_processes(registration)

        self.assertEqual(result["status"], "ORIGINAL_PROCESS_OBSERVATION_VERIFIED")
        self.assertEqual(result["process_observations"]["codex"]["state"], "ORIGINAL_PID_ABSENT")
        for lane in ("claude_a", "claude_b"):
            self.assertEqual(result["process_observations"][lane]["state"], "ORIGINAL_PROCESS_PRESENT")
        codex = result["process_observations"]["codex"]
        self.assertEqual(codex["original_process"], self.sessions["codex"]["process"])
        self.assertEqual([read["errno"] for read in codex["kernel_reads"]], [errno.ESRCH, errno.ESRCH])
        self.assertEqual([read["count"] for read in codex["kernel_reads"]], [0, 0])
        self.assertEqual(len(calls), 6)
        self.assertNotIn("original", result)
        self.assertNotIn("plan", result)
        self.assertEqual(result["api_availability"], "NOT_EVALUATED")
        self.assertIs(result["takeover_authorized"], False)
        self.assertIs(result["child_budget_enforced"], False)
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(result["resource_releases"], 0)
        self.assertEqual(json_ref(registration), before_registration)
        self.assertEqual(before_native, {lane: journal.path.read_bytes() for lane, journal in self.journals.items()})
        saved = json_ref(result["evidence"])
        self.assertEqual(saved, {key: value for key, value in result.items() if key != "evidence"})
        self.assertEqual(Path(result["evidence"]["path"]).stat().st_mode & 0o777, 0o444)
        self.assertEqual(file_ref(registration["path"]), registration)


    def test_reused_pid_even_as_zombie_refuses_without_publishing_or_releasing(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        before = json_ref(registration)
        original = self.sessions["codex"]["process"]
        output = Path(before["output_root"]) / "process-observations"
        reads = []

        def reused(pid, kind, arg, pointer, size):
            reads.append(pid)
            row = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
            row.pid, row.uid, row.ppid, row.status = pid, inventory.os.getuid(), 7, 5
            row.start_sec, row.start_usec = original["birth"][0] + 1, original["birth"][1]
            row.name = b"codex"
            ctypes.set_errno(0)
            return size

        library = types.SimpleNamespace(proc_pidinfo=reused,
            proc_pidpath=lambda *args: self.fail("reused PID cannot inspect/authorize another executable"))
        with patch.object(inventory, "_lib", return_value=library):
            with self.assertRaisesRegex(continuity.ContractError, "ORIGINAL_PROCESS_IDENTITY_CHANGED"):
                continuity.observe_registered_processes(registration)

        self.assertEqual(reads, [original["pid"]])
        self.assertFalse(output.exists())
        self.assertEqual(json_ref(registration), before)


    def test_unknown_kernel_results_refuse_before_artifacts_instead_of_becoming_absence(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        before = json_ref(registration)
        output = Path(before["output_root"]) / "process-observations"
        for returned, number in ((0, errno.EPERM), (0, errno.EACCES), (0, 0), (3, 0),
                                 (-1, errno.EIO)):
            with self.subTest(returned=returned, errno=number):
                def refused(pid, kind, arg, pointer, size):
                    ctypes.set_errno(number)
                    return returned
                library = types.SimpleNamespace(proc_pidinfo=refused,
                    proc_pidpath=lambda *args: self.fail("unknown kernel result cannot use executable"))
                with patch.object(inventory, "_lib", return_value=library):
                    with self.assertRaisesRegex(continuity.ContractError,
                                                "ORIGINAL_PROCESS_OBSERVATION_UNVERIFIED"):
                        continuity.observe_registered_processes(registration)
                self.assertFalse(output.exists())
                self.assertEqual(json_ref(registration), before)


    def test_process_change_between_kernel_reads_never_publishes_exit_or_presence(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        before = json_ref(registration)
        output = Path(before["output_root"]) / "process-observations"
        original = self.sessions["codex"]["process"]
        for transition in ("absent-to-present", "present-to-absent", "birth-change", "name-change"):
            with self.subTest(transition=transition):
                readings = []
                def changing(pid, kind, arg, pointer, size):
                    index = len(readings)
                    readings.append(pid)
                    if ((transition == "absent-to-present" and index == 0)
                            or (transition == "present-to-absent" and index == 1)):
                        ctypes.set_errno(errno.ESRCH)
                        return 0
                    row = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
                    row.pid, row.uid, row.ppid, row.status = pid, inventory.os.getuid(), 7, 1
                    row.start_sec, row.start_usec = original["birth"]
                    row.name = b"codex"
                    if index == 1 and transition == "birth-change":
                        row.start_usec += 1
                    if index == 1 and transition == "name-change":
                        row.name = b"another"
                    ctypes.set_errno(0)
                    return size
                def executable(pid, buffer, size):
                    buffer.value = original["executable"].encode()
                    ctypes.set_errno(0)
                    return len(buffer.value) + 1
                library = types.SimpleNamespace(proc_pidinfo=changing, proc_pidpath=executable)
                with patch.object(inventory, "_lib", return_value=library):
                    with self.assertRaisesRegex(continuity.ContractError,
                            "ORIGINAL_PROCESS_(CHANGED_DURING_OBSERVATION|IDENTITY_CHANGED)"):
                        continuity.observe_registered_processes(registration)
                self.assertEqual(readings, [original["pid"], original["pid"]])
                self.assertFalse(output.exists())
                self.assertEqual(json_ref(registration), before)


    def test_stable_same_birth_zombie_is_diagnostic_and_never_uses_its_path(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        before = json_ref(registration)
        original = self.sessions["codex"]["process"]
        paths = []

        def bsd(pid, kind, arg, pointer, size):
            expected = next(session["process"] for session in self.sessions.values()
                            if session["process"]["pid"] == pid)
            row = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
            row.pid, row.uid, row.ppid = pid, inventory.os.getuid(), 7
            row.status = 5 if pid == original["pid"] else 1
            row.start_sec, row.start_usec = expected["birth"]
            row.name = b"fixture-client"
            ctypes.set_errno(0)
            return size

        def executable(pid, buffer, size):
            paths.append(pid)
            self.assertNotEqual(pid, original["pid"])
            expected = next(session["process"] for session in self.sessions.values()
                            if session["process"]["pid"] == pid)
            buffer.value = expected["executable"].encode()
            ctypes.set_errno(0)
            return len(buffer.value) + 1

        with patch.object(inventory, "_lib", return_value=types.SimpleNamespace(
                proc_pidinfo=bsd, proc_pidpath=executable)):
            result = continuity.observe_registered_processes(registration)

        codex = result["process_observations"]["codex"]
        self.assertEqual(codex["state"], "ORIGINAL_PROCESS_ZOMBIE")
        self.assertEqual(codex["original_process"], original)
        self.assertEqual(len(codex["kernel_reads"]), 2)
        self.assertTrue(all(read["snapshot"]["zombie"] for read in codex["kernel_reads"]))
        self.assertEqual(codex["executable_reads"], [])
        self.assertEqual(paths, [self.sessions[lane]["process"]["pid"]
                                for lane in ("claude_a", "claude_b") for _ in range(2)])
        self.assertEqual(result["api_availability"], "NOT_EVALUATED")
        self.assertIs(result["takeover_authorized"], False)
        self.assertEqual(result["resource_releases"], 0)
        self.assertEqual(json_ref(registration), before)


    def test_foreign_owner_or_unqualified_executable_refuses_without_output(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        before = json_ref(registration)
        original = self.sessions["codex"]["process"]
        output = Path(before["output_root"]) / "process-observations"
        for variant in ("foreign-owner", "bsd-errno", "different-path", "missing-path",
                        "path-permission", "path-error", "path-length", "path-second-read"):
            with self.subTest(variant=variant):
                paths = []
                def bsd(pid, kind, arg, pointer, size):
                    row = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
                    row.pid, row.uid, row.ppid, row.status = pid, inventory.os.getuid(), 7, 1
                    row.start_sec, row.start_usec = original["birth"]
                    row.name = b"codex"
                    if variant == "foreign-owner":
                        row.uid += 1
                    ctypes.set_errno(errno.EIO if variant == "bsd-errno" else 0)
                    return size
                def executable(pid, buffer, size):
                    paths.append(pid)
                    buffer.value = (b"/another/client" if variant == "different-path"
                                    else original["executable"].encode())
                    if variant == "missing-path":
                        ctypes.set_errno(errno.ESRCH)
                        return 0
                    if variant == "path-permission":
                        ctypes.set_errno(errno.EPERM)
                        return 0
                    if variant == "path-error":
                        ctypes.set_errno(errno.EIO)
                        return len(buffer.value) + 1
                    if variant == "path-length":
                        ctypes.set_errno(0)
                        return size + 1
                    if variant == "path-second-read" and len(paths) == 2:
                        buffer.value = b"/another/client"
                    ctypes.set_errno(0)
                    return len(buffer.value) + 1
                library = types.SimpleNamespace(proc_pidinfo=bsd, proc_pidpath=executable)
                with patch.object(inventory, "_lib", return_value=library):
                    with self.assertRaisesRegex(continuity.ContractError,
                            "ORIGINAL_PROCESS_(OBSERVATION_UNVERIFIED|KERNEL_STATUS_UNKNOWN|EXECUTABLE_CHANGED_OR_UNKNOWN)"):
                        continuity.observe_registered_processes(registration)
                if variant in ("foreign-owner", "bsd-errno"):
                    self.assertEqual(paths, [])
                self.assertFalse(output.exists())
                self.assertEqual(json_ref(registration), before)


    def test_process_cli_after_stop_preserves_native_bytes_and_rejects_caller_overrides(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        stop = write_json(self.spec["stop_path"], dict(status="STOPPED_INPUT", reason="offline test"),
                          exclusive=True)
        before = {lane: journal.path.read_bytes() for lane, journal in self.journals.items()}
        arguments = ["observe-processes", "--registration", registration["path"],
                     "--registration-sha256", registration["sha256"]]
        for extra in ("--fixtures", "--now", "--status", "--pid", "--model", "--max-records"):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as refused:
                    continuity.main(arguments + [extra])
                self.assertEqual(refused.exception.code, 2)
                self.assertIn("unrecognized arguments", error.getvalue())
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(continuity.main(arguments[:-1] + ["0" * 64]), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["status"], "REFUSED_EVIDENCE_PRESERVED")
        self.assertFalse((Path(json_ref(registration)["output_root"]) / "process-observations").exists())

        calls = []
        def absent(pid, kind, arg, pointer, size):
            calls.append(pid)
            ctypes.set_errno(errno.ESRCH)
            return 0
        library = types.SimpleNamespace(proc_pidinfo=absent,
            proc_pidpath=lambda *args: self.fail("absent PID cannot read a substitute path"))
        with patch.object(inventory, "_lib", return_value=library), \
                redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(continuity.main(arguments), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(result["status"], "ORIGINAL_PROCESS_OBSERVATION_VERIFIED")
        self.assertEqual(result["api_availability"], "NOT_EVALUATED")
        self.assertEqual(calls, [self.sessions[lane]["process"]["pid"]
                                 for lane in continuity.LANES for _ in range(2)])
        self.assertTrue(all(item["state"] == "ORIGINAL_PID_ABSENT"
                            for item in result["process_observations"].values()))
        self.assertEqual(result["input_operations"], 0)
        self.assertEqual(result["resource_releases"], 0)
        self.assertIs(result["takeover_authorized"], False)
        self.assertEqual(before, {lane: journal.path.read_bytes() for lane, journal in self.journals.items()})
        self.assertEqual(file_ref(self.spec["stop_path"]), stop)
        self.assertEqual(file_ref(registration["path"]), registration)


    def test_altered_original_registration_refuses_before_any_kernel_read(self):
        import copy
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        original = json_ref(registration)
        path = Path(registration["path"])
        saved = path.read_bytes()
        directory = Path(original["output_root"])
        mutations = [dict(original, fixture_mode=True), dict(original, task_id="different-task"),
                     dict(original, output_root=str(self.root))]
        substituted = copy.deepcopy(original)
        substituted["sessions"]["codex"]["process"]["pid"] += 1
        mutations.append(substituted)
        try:
            for index, value in enumerate(mutations):
                with self.subTest(index=index):
                    path.chmod(0o600)
                    forged = write_json(path, value)
                    path.chmod(0o444)
                    with patch.object(inventory, "_lib", side_effect=AssertionError(
                            "invalid original authority must not reach OS observation")):
                        with self.assertRaisesRegex(continuity.ContractError,
                                "original native continuity registration fields changed|native continuity registration scope changed|native continuity original session changed"):
                            continuity.observe_registered_processes(forged)
                    self.assertFalse((directory / "process-observations").exists())
        finally:
            path.chmod(0o600)
            path.write_bytes(saved)
            path.chmod(0o444)
        self.assertEqual(file_ref(path), registration)
        self.assertEqual(continuity.validate_native_registration(registration)[0], original)


    def test_unknown_kernel_process_status_is_not_present_or_recoverable(self):
        registration = continuity.register_native_continuity(self.runtime_ref, self.adoption,
            task_id=native_fixtures.TASK, native_authorizations=self.authorizations,
            automatic_fallback_authorized=True)
        before = json_ref(registration)
        output = Path(before["output_root"]) / "process-observations"
        def unknown(pid, kind, arg, pointer, size):
            expected = next(session["process"] for session in self.sessions.values()
                            if session["process"]["pid"] == pid)
            row = ctypes.cast(pointer, ctypes.POINTER(identity.BsdInfo)).contents
            row.pid, row.uid, row.ppid, row.status = pid, inventory.os.getuid(), 7, 0
            row.start_sec, row.start_usec = expected["birth"]
            row.name = b"fixture-client"
            ctypes.set_errno(0)
            return size
        def executable(pid, buffer, size):
            expected = next(session["process"] for session in self.sessions.values()
                            if session["process"]["pid"] == pid)
            buffer.value = expected["executable"].encode()
            ctypes.set_errno(0)
            return len(buffer.value) + 1
        library = types.SimpleNamespace(proc_pidinfo=unknown, proc_pidpath=executable)
        with patch.object(inventory, "_lib", return_value=library):
            with self.assertRaisesRegex(continuity.ContractError, "ORIGINAL_PROCESS_OBSERVATION_UNVERIFIED"):
                continuity.observe_registered_processes(registration)
        self.assertFalse(output.exists())
        self.assertEqual(json_ref(registration), before)


if __name__ == "__main__":
    unittest.main()
