"""Measured FULL-read shapes for an advice body, with no matcher widening.

Every expectation here was measured against the unchanged shared matcher before
it was asserted (R62 `checks.equivalent`): one positive per accepted shape, and
a range read, a truncated output and a sha-only output as negatives. The
accepted argument key sets are asserted to be exactly today's, so a later
widening fails here instead of passing quietly.
"""
import json
import tempfile
import unittest

from orchestration_common import parse_time, utc_now
from orchestration_native import NativeReader
from orchestration_test_support import CampaignFixture
from upgrade_advice_chain import advice_read_evidence
from upgrade_body_read import read_result
from upgrade_body_read_evidence import RTK_NOTICE, valid_command

TERMINAL = "Chunk ID: abc\nWall time: 0.1 seconds\nProcess exited with code 0\nFinal output:\n"
TERMINAL_NO_CHUNK = "Wall time: 0.1 seconds\nProcess exited with code 0\nOutput:\n"


def suggesting_transport(request):
    return dict(answers=dict(work_kind="coding", work_kind_applies=0.9), http_status=200)


class AdviceReadShapeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = CampaignFixture(self.temp.name, strategies=("omo",), version=3, arm="B", unbound=True)
        self.co = self.f.start()
        self.advice = self.f.publish_fixture_advice(self.co, "unbound", transport=suggesting_transport,
                                                    budget=dict(max_model_requests=80))
        self.complete = json.dumps(read_result(self.advice["notice"]), ensure_ascii=False, sort_keys=True) + "\n"

    def observe(self, *, lane="codex", **kwargs):
        reader = NativeReader(self.f.campaign["sessions"][lane], fixtures=True)
        fence = reader.snapshot()
        self.f.fake_advice_read_records(self.advice, lane=lane, **kwargs)
        now = parse_time(utc_now()).timestamp()
        return advice_read_evidence(reader, fence=fence, until=reader.snapshot()["offset"],
                                    notice=self.advice["notice"], command=self.advice["command"],
                                    not_before=now - 30, not_after=now + 30)

    def test_three_measured_full_read_shapes_each_have_one_positive(self):
        for lane, wrapper in (("codex", True), ("codex", False), ("claude_a", False)):
            with self.subTest(lane=lane, wrapper=wrapper):
                self.setUp()
                found = self.observe(lane=lane, wrapper=wrapper)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["kind"], "BODY_READ_OBSERVED")
                self.assertIn("not_semantic_acceptance", found[0]["scope"])
                self.assertEqual(found[0]["reference"]["sha256"], self.advice["advice_sha256"])

    def test_codex_direct_terminal_envelope_positive(self):
        for header in (TERMINAL, TERMINAL_NO_CHUNK):
            with self.subTest(header=header.splitlines()[0]):
                self.setUp()
                self.assertEqual(len(self.observe(wrapper=False, raw_envelope=header + self.complete)), 1)

    def test_original_token_count_envelope_positive_and_truncated_negative(self):
        complete = json.dumps(dict(exit_code=0, output=self.complete, wall_time_seconds=0.1,
                                   original_token_count=900))
        self.assertEqual(len(self.observe(wrapper=False, raw_envelope=complete)), 1)
        self.setUp()
        cut = json.dumps(dict(exit_code=0, output=self.complete[: len(self.complete) // 2],
                              wall_time_seconds=0.1, original_token_count=900))
        self.assertEqual(self.observe(wrapper=False, raw_envelope=cut), [])

    def test_rtk_banner_positive_on_both_codex_envelopes_and_negative_before_the_header(self):
        self.assertEqual(len(self.observe(wrapper=False, output=RTK_NOTICE + self.complete)), 1)
        self.setUp()
        self.assertEqual(len(self.observe(wrapper=True, output=RTK_NOTICE + self.complete)), 1)
        self.setUp()
        self.assertEqual(len(self.observe(wrapper=False,
                                          raw_envelope=TERMINAL_NO_CHUNK + RTK_NOTICE + self.complete)), 1)
        self.setUp()
        # The completion header is matched exactly, so a banner before it is not
        # a recognised envelope at all - it stays UNKNOWN, not a proven read.
        banner_first = [dict(type="input_text", text=RTK_NOTICE + "Script completed\nWall time 0.1 seconds\nOutput:\n"),
                        dict(type="input_text", text=json.dumps(dict(exit_code=0, output=self.complete,
                                                                     wall_time_seconds=0.1)))]
        self.assertEqual(self.observe(wrapper=True, raw_envelope=banner_first), [])

    def test_claude_rtk_banner_positive(self):
        self.assertEqual(len(self.observe(lane="claude_a", output=RTK_NOTICE + self.complete)), 1)

    def test_range_read_is_not_a_full_read(self):
        for suffix in (" | sed -n '1,5p'", " | head -c 200"):
            with self.subTest(suffix=suffix):
                self.setUp()
                self.assertEqual(self.observe(command=self.advice["command"] + suffix), [])

    def test_truncated_output_is_not_a_full_read(self):
        self.assertEqual(self.observe(output=self.complete[:-12]), [])
        self.setUp()
        short = dict(json.loads(self.complete))
        short["body"] = short["body"][:-1]
        self.assertEqual(self.observe(output=json.dumps(short) + "\n"), [])

    def test_sha_only_output_is_not_a_full_read(self):
        self.assertEqual(self.observe(output=json.dumps(dict(sha256=self.advice["advice_sha256"])) + "\n"), [])

    def test_notice_only_keeps_the_read_unknown_even_though_the_file_is_readable(self):
        # The body is on disk and readable, and the advice exists. Absent
        # evidence is UNKNOWN, never a proven non-read.
        self.assertEqual(read_result(self.advice["notice"])["body"], self.advice["body"])
        self.assertEqual(self.observe(output="STATUS: read it yourself\n"), [])

    def test_prefixed_function_names_are_accepted_without_widening(self):
        self.assertEqual(len(self.observe(wrapper=True, name="functions.exec")), 1)
        self.setUp()
        self.assertEqual(len(self.observe(wrapper=False, name="functions.exec_command")), 1)
        self.setUp()
        # A bare name with the wrapper's JavaScript as its arguments is not a
        # direct invocation and stays refused.
        self.assertEqual(self.observe(wrapper=True, name="exec_command"), [])

    def test_whitelist_is_not_widened(self):
        command = self.advice["command"]
        every_exec_key = dict(cmd=command, login=False, workdir=self.f.campaign["root"],
                              yield_time_ms=10000, max_output_tokens=12000)
        every_bash_key = dict(command=command, description="read the advice", timeout=120000,
                              run_in_background=False)
        for name in ("exec_command", "functions.exec_command"):
            self.assertTrue(valid_command(name, json.dumps(dict(cmd=command, login=False)), command))
            self.assertTrue(valid_command(name, json.dumps(every_exec_key), command))
            self.assertFalse(valid_command(name, json.dumps(dict(cmd=command, login=False, tty=False)), command))
            self.assertFalse(valid_command(name, json.dumps(dict(cmd=command, login=True)), command))
            self.assertFalse(valid_command(name, json.dumps(dict(cmd=command)), command))
        self.assertTrue(valid_command("Bash", dict(command=command), command))
        self.assertTrue(valid_command("Bash", every_bash_key, command))
        self.assertFalse(valid_command("Bash", dict(command=command, dangerouslyDisableSandbox=True), command))
        self.assertFalse(valid_command("Bash", dict(command=command, run_in_background=True), command))
        for name in ("Read", "Write", "exec_commands", "shell", "functions.shell", "functions.Bash"):
            self.assertFalse(valid_command(name, dict(command=command, cmd=command), command))


if __name__ == "__main__":
    unittest.main()
