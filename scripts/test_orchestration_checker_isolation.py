"""Real private audit/Seatbelt checker regressions; no model or cmux input.

The legacy stdin=PIPE reproduction must remain refused. The repaired checker
uses a private regular request file; the descriptor/path guard stays intact.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import sys
import unittest

from orchestration_common import file_ref, tree_manifest
import test_upgrade_live_startup as startup_fixture
from upgrade_client_isolation import SANDBOX


CHECKER = r'''
import hashlib, json, os, sys, time
from pathlib import Path
raw = sys.stdin.buffer.read()
assert sys.stdin.buffer.read(1) == b"", "checker did not receive EOF"
request = json.loads(raw)
assert raw == (json.dumps(request, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False) + "\n").encode()
with Path(request["trace"]).open("a") as trace:
    trace.write(str(os.getpid()) + "\n")
    trace.flush()
outside = Path(request["outside"])
try:
    outside.write_text("child escaped")
except PermissionError:
    pass
else:
    raise AssertionError("checker escaped inherited kernel write boundary")
sys.stderr.write("CHECKER_STDERR\n")
sys.stderr.flush()
if request["mode"] == "fail":
    sys.stderr.write("fixture-checker-error\n")
    raise SystemExit(7)
if request["mode"] == "timeout":
    time.sleep(60)
print(json.dumps(dict(checks=[dict(id="canonical-input-and-eof", passed=True)],
    request_bytes=len(raw), request_sha256=hashlib.sha256(raw).hexdigest(),
    eof=True, cwd=os.getcwd(), environment=dict(os.environ))))
'''


@unittest.skipUnless(
    sys.platform == "darwin" and SANDBOX.exists()
    and startup_fixture.SOURCE.is_dir() and startup_fixture.WRAPPERS.is_dir(),
    "requires macOS Seatbelt and fixed local materialization artifacts; no native clients",
)
class CheckerIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse materialization only, without inheriting or running startup tests.
        startup_fixture.MaterializedStartupTests.setUpClass.__func__(cls)
        if cls.source_controller != startup_fixture.SOURCE:
            raise AssertionError("borrowed checker fixture must preserve its fixed legacy source")

    @classmethod
    def tearDownClass(cls):
        startup_fixture.MaterializedStartupTests.tearDownClass.__func__(cls)

    def setUp(self):
        self.campaign_root = self.paths["campaigns"] / self._testMethodName
        self.campaign_root.mkdir()
        self.business = self.campaign_root / "business"
        self.business.mkdir()
        (self.business / "report.md").write_text("preserved business evidence\n")
        self.business_before = tree_manifest(self.business, kind="output")
        self.outside = self.root / (self._testMethodName + "-outside")
        self.outside.write_text("preserve\n")
        adapter = self.root / (self._testMethodName + "-adapter")
        adapter.mkdir()
        checker = adapter / "checker.py"
        checker.write_text(CHECKER)
        self.campaign = dict(
            root=str(self.campaign_root), python=file_ref(Path(sys.executable).resolve()),
            checker=file_ref(checker), adapter=tree_manifest(adapter),
            checker_timeout_seconds=5,
        )
        self.trace = self.campaign_root / "invocations.log"
        self.request = dict(mode="success", trace=str(self.trace), outside=str(self.outside),
                            payload="完整请求\n" * 20000)

    def run_guarded(self, body, *, pass_fds=()):
        # Load the current adapter, then install the genuine materialized guard.
        # Only checker scripts are synthetic; audit hooks and Seatbelt are real.
        prefix = f'''
import errno, hashlib, io, json, os, stat, subprocess, sys, traceback
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
from orchestration_common import ContractError, canonical, tree_manifest
import orchestration_evidence as evidence
sys.path.insert(0, {str(Path(self.spec["controller"]) / "scripts")!r})
import mac_upgrade_controller_scope as scope
scope.install_scope(live=True)
assert scope._installed
campaign = {self.campaign!r}
request = {self.request!r}
root = Path(campaign["root"])
trace = Path(request["trace"])
business = root / "business"
before = tree_manifest(business, kind="output")
'''
        result = subprocess.run(
            [str(SANDBOX), "-f", self.spec["pins"]["control_sandbox"]["path"],
             sys.executable, "-I", "-B", "-c", prefix + body],
            cwd=self.root, capture_output=True, text=True, timeout=30, pass_fds=pass_fds,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outside.read_text(), "preserve\n")
        self.assertEqual(tree_manifest(self.business, kind="output"), self.business_before)
        return result

    def test_original_pipe_stdin_reproduces_ebadf_before_checker_launch(self):
        result = self.run_guarded(r'''
try:
    subprocess.run([campaign["python"]["path"], "-I", "-B", campaign["checker"]["path"]],
        input=canonical(request), capture_output=True, timeout=5)
except OSError as error:
    assert error.errno == errno.EBADF, repr(error)
    frames = traceback.extract_tb(error.__traceback__)
    assert any(frame.name == "fd_path" and frame.filename == scope.__file__ for frame in frames)
else:
    raise AssertionError("original anonymous pipe write was unexpectedly permitted")
assert not trace.exists(), "checker started before original pipe refusal"
assert tree_manifest(business, kind="output") == before
print("ORIGINAL_PIPE_EBADF_CONFIRMED")
''')
        self.assertIn("ORIGINAL_PIPE_EBADF_CONFIRMED", result.stdout)
        self.assertFalse(self.trace.exists())

    def test_private_request_reaches_eof_and_captures_both_streams_once(self):
        result = self.run_guarded(r'''
real_run = evidence.subprocess.run
observed = []
def observe(*args, **kwargs):
    assert "input" not in kwargs
    incoming = kwargs["stdin"]
    assert incoming.readable() and not incoming.writable()
    source = Path(incoming.name)
    assert root in source.parents and business not in source.parents
    assert stat.S_ISREG(os.fstat(incoming.fileno()).st_mode)
    assert not source.exists(), "request pathname remains mutable at checker launch"
    assert os.pread(incoming.fileno(), len(canonical(request)) + 1, 0) == canonical(request)
    result = real_run(*args, **kwargs)
    observed.append((source, result))
    return result
evidence.subprocess.run = observe
value = evidence.run_checker(campaign, request)
assert len(observed) == 1
source, completed = observed[0]
assert completed.stderr == b"CHECKER_STDERR\n"
assert json.loads(completed.stdout) == value
assert value["eof"] is True and value["request_bytes"] == len(canonical(request))
assert value["request_sha256"] == hashlib.sha256(canonical(request)).hexdigest()
assert value["cwd"] == campaign["adapter"]["path"]
assert value["environment"]["PATH"] == "/usr/bin:/bin"
assert value["environment"]["LANG"] == "en_US.UTF-8"
assert len(trace.read_text().splitlines()) == 1
assert not source.exists() and not source.parent.exists()
assert set(p.name for p in root.iterdir()) == {"business", "invocations.log"}
assert tree_manifest(business, kind="output") == before
print("PRIVATE_CHECKER_STREAMS_PASS")
''')
        self.assertIn("PRIVATE_CHECKER_STREAMS_PASS", result.stdout)

    def test_checker_failure_keeps_stderr_and_does_not_retry_or_leave_scratch(self):
        self.request["mode"] = "fail"
        result = self.run_guarded(r'''
try:
    evidence.run_checker(campaign, request)
except ContractError as error:
    assert "trusted checker failed" in str(error)
    assert "CHECKER_STDERR" in str(error) and "fixture-checker-error" in str(error)
else:
    raise AssertionError("nonzero checker exit was accepted")
assert len(trace.read_text().splitlines()) == 1
assert set(p.name for p in root.iterdir()) == {"business", "invocations.log"}
assert tree_manifest(business, kind="output") == before
print("PRIVATE_CHECKER_FAILURE_ONCE")
''')
        self.assertIn("PRIVATE_CHECKER_FAILURE_ONCE", result.stdout)

    def test_checker_timeout_does_not_retry_or_leave_scratch(self):
        self.request["mode"] = "timeout"
        self.campaign["checker_timeout_seconds"] = 1
        result = self.run_guarded(r'''
try:
    evidence.run_checker(campaign, request)
except subprocess.TimeoutExpired as error:
    assert error.timeout == 1
    assert b"CHECKER_STDERR" in error.stderr
else:
    raise AssertionError("checker timeout was swallowed")
calls = trace.read_text().splitlines()
assert len(calls) == 1
try:
    os.kill(int(calls[0]), 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError("timed-out checker was not reaped")
assert set(p.name for p in root.iterdir()) == {"business", "invocations.log"}
assert tree_manifest(business, kind="output") == before
print("PRIVATE_CHECKER_TIMEOUT_ONCE")
''')
        self.assertIn("PRIVATE_CHECKER_TIMEOUT_ONCE", result.stdout)

    def test_external_descriptors_fifo_closed_fd_and_relative_escape_stay_refused(self):
        fifo = self.root / (self._testMethodName + "-fifo")
        os.mkfifo(fifo, 0o600)
        link = self.campaign_root / "outside-link"
        link.symlink_to(self.outside)
        with contextlib.ExitStack() as stack:
            regular = os.open(self.outside, os.O_WRONLY)
            stack.callback(os.close, regular)
            named_fifo = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
            stack.callback(os.close, named_fifo)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, directory)
            result = self.run_guarded(f'''
def refused(action):
    try:
        action()
    except PermissionError:
        return
    except OSError as error:
        # Darwin may report EBADF when F_GETPATH cannot resolve a FIFO.
        assert error.errno == errno.EBADF, repr(error)
        return
    raise AssertionError("unbound write was permitted")
refused(lambda: io.open({regular}, "wb", closefd=False))
refused(lambda: io.open({named_fifo}, "wb", closefd=False))
refused(lambda: Path({str(link)!r}).write_text("escape"))
refused(lambda: Path(request["outside"]).write_text("escape"))
refused(lambda: os.open({self.outside.name!r}, os.O_WRONLY, dir_fd={directory}))
refused(lambda: os.chmod({regular}, 0o666))
closed = os.open(root / "closed-fd", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(closed)
refused(lambda: io.open(closed, "wb", closefd=False))
assert Path(request["outside"]).read_text() == "preserve\\n"
assert tree_manifest(business, kind="output") == before
print("PRIVATE_DESCRIPTOR_BOUNDARIES_PRESERVED")
''', pass_fds=(regular, named_fifo, directory))
        self.assertIn("PRIVATE_DESCRIPTOR_BOUNDARIES_PRESERVED", result.stdout)
        self.assertFalse(self.trace.exists())


if __name__ == "__main__":
    unittest.main()
