"""Negative controls for the native self-query compatibility helper."""
import contextlib
import io
import unittest
from unittest.mock import patch

import upgrade_claude_ps as helper
from cmux_daemon_identity import IdentityError


class ClaudePsTests(unittest.TestCase):
    def test_unrelated_pid_and_unsupported_queries_never_read_kernel(self):
        with patch.object(helper.os, "getppid", return_value=42), patch.object(helper, "process") as process:
            for argv in ([], ["-ef"], ["-o", "lstart=", "-p", "41"],
                         ["-o", "lstart=", "-p", "042"], ["-o", "lstart=", "-p", "４２"],
                         ["-o", "lstart=", "-p", "42", "extra"]):
                with self.subTest(argv=argv), self.assertRaises(IdentityError):
                    helper.start_time(argv)
            process.assert_not_called()

    def test_reused_birth_reparenting_and_kernel_refusal_produce_no_timestamp(self):
        before = dict(pid=42, ppid=1, birth=[1700000000, 99])
        for after in (dict(before, birth=[1700000000, 100]), IdentityError("kernel EPERM")):
            with patch.object(helper.os, "getppid", return_value=42), \
                    patch.object(helper, "process", side_effect=[before, after]), \
                    contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(helper.main(["-o", "lstart=", "-p", "42"]), 2)
                self.assertEqual(out.getvalue(), "")
        with patch.object(helper.os, "getppid", side_effect=[42, 43]), \
                patch.object(helper, "process", return_value=before), self.assertRaisesRegex(IdentityError, "changed"):
            helper.start_time(["-o", "lstart=", "-p", "42"])


if __name__ == "__main__":
    unittest.main()
