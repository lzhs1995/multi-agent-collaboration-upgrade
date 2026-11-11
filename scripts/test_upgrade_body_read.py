"""Receiver exposure evidence, including realistic native call/result shapes."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

from cmux_prompt_reference import plan, persist
from upgrade_body_read import read_command, read_result, reader_instructions
from upgrade_body_read_evidence import body_read_evidence, RTK_NOTICE


class BodyReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.body = "STATUS: original-marker\n信息与精确字节 ' $() ` echo\t\n"
        planned = plan(self.body, "original-marker", self.root)
        persist(planned["reference"], self.body)
        self.notice = planned["text"]
        self.command = read_command(sys.executable, self.root, self.notice, "/usr/local/bin/rtk")
        self.value = read_result(self.notice)
        self.output = json.dumps(self.value, ensure_ascii=False) + "\n"
        self.now = dt.datetime.now(dt.timezone.utc)

    def records(self, provider="codex", wrapper=True):
        call = {"timestamp": self.now.isoformat()}
        result = {"timestamp": (self.now + dt.timedelta(seconds=1)).isoformat()}
        if provider == "codex":
            args = dict(cmd=self.command, login=False, yield_time_ms=10000, max_output_tokens=12000)
            call.update(type="response_item", payload=dict(type="custom_tool_call" if wrapper else "function_call",
                call_id="one", name="exec" if wrapper else "exec_command",
                **({"input": "text(await tools.exec_command(" + json.dumps(args) + "));\n"} if wrapper
                   else {"arguments": json.dumps(args)})))
            output = json.dumps(dict(exit_code=0, output=self.output, wall_time_seconds=0.1))
            if wrapper:
                output = [dict(type="input_text", text="Script completed\nWall time 0.1 seconds\nOutput:\n"),
                          dict(type="input_text", text=output)]
            result.update(type="response_item", payload=dict(type="custom_tool_call_output" if wrapper else "function_call_output",
                          call_id="one", output=output))
        else:
            call.update(type="assistant", sessionId="original", message=dict(role="assistant", content=[
                dict(type="tool_use", id="one", name="Bash", input=dict(command=self.command))]))
            result.update(type="user", sessionId="original", message=dict(role="user", content=[
                dict(type="tool_result", tool_use_id="one", is_error=False, content=self.output)]))
        return self.pin([call, result])

    @staticmethod
    def pin(records):
        result, offset = [], 500
        for record in records:
            raw = json.dumps(record).encode() + b"\n"
            result.append((dict(offset=offset, length=len(raw), sha256=hashlib.sha256(raw).hexdigest()), record))
            offset += len(raw)
        return result

    def match(self, records, provider="codex", **kw):
        return body_read_evidence(records, provider=provider, session_id="original", notice=self.notice,
            command=self.command, not_before=self.now.timestamp() - 1,
            not_after=self.now.timestamp() + 5, **kw)

    def test_exact_pinned_utf8_body_and_shell_argument(self):
        self.assertEqual(self.value["body"], self.body)
        self.assertEqual(shlex.split(self.command)[-1], self.notice)
        self.assertIn("max_output_tokens", reader_instructions(sys.executable, self.root, "/rtk"))
        path = Path(self.value["reference"]["path"])
        path.chmod(0o600)
        with self.assertRaises(ValueError):
            read_result(self.notice)

    def test_three_supported_native_shapes(self):
        for provider, wrapper in (("codex", True), ("codex", False), ("claude", False)):
            with self.subTest(provider=provider, wrapper=wrapper):
                found = self.match(self.records(provider, wrapper), provider)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["kind"], "BODY_READ_OBSERVED")
                self.assertIn("not_semantic_acceptance", found[0]["scope"])

    def test_codex_direct_terminal_envelope(self):
        records = self.records(wrapper=False)
        records[1][1]["payload"]["output"] = "Chunk ID: abc\nWall time: 0.1 seconds\nProcess exited with code 0\nFinal output:\n" + self.output
        self.assertEqual(len(self.match(records)), 1)

    def test_claude_measured_rtk_warning(self):
        records = self.records("claude")
        records[1][1]["message"]["content"][0]["content"] = RTK_NOTICE + self.output
        self.assertEqual(len(self.match(records, "claude")), 1)

    def test_exact_pair_order_session_and_time(self):
        mutations = {
            "foreign_session": lambda c, r: r.update(sessionId="foreign"),
            "sidechain": lambda c, r: c.update(isSidechain=True),
            "future": lambda c, r: r.update(timestamp=(self.now + dt.timedelta(days=1)).isoformat()),
            "old": lambda c, r: c.update(timestamp=(self.now - dt.timedelta(days=1)).isoformat()),
            "no_timezone": lambda c, r: c.update(timestamp="2026-10-19T00:00:00"),
            "wrong_id": lambda c, r: r["message"]["content"][0].update(tool_use_id="other"),
            "error": lambda c, r: r["message"]["content"][0].update(is_error=True),
            "missing_error_flag": lambda c, r: r["message"]["content"][0].pop("is_error"),
            "reverse_time": lambda c, r: r.update(timestamp=(self.now - dt.timedelta(seconds=.5)).isoformat()),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                records = self.records("claude")
                mutate(records[0][1], records[1][1])
                self.assertEqual(self.match(records, "claude"), [])
        raw = [r for _, r in self.records()]
        for sequence in (raw[::-1], [raw[0], raw[0], raw[1]], [raw[0], raw[1], raw[1]], [raw[0]], [raw[1]]):
            self.assertEqual(self.match(self.pin(sequence)), [])

    def test_complete_output_required(self):
        for altered in (self.output[:-5], self.output + "extra", self.output.replace(self.body[:6], "WRONG:", 1),
                        self.output.replace('"schema":', '"schema":"bad","schema":'),
                        json.dumps(dict(self.value, body=self.body + "x")),
                        json.dumps(dict(self.value, notice=self.notice + "x")),
                        json.dumps(dict(self.value, body=None)), "NaN"):
            records = self.records("claude")
            records[1][1]["message"]["content"][0]["content"] = altered
            self.assertEqual(self.match(records, "claude"), [])

    def test_yielded_or_nonzero_codex_is_not_success(self):
        for value in (dict(output=self.output, session_id=42), dict(output=self.output, exit_code=1),
                      dict(output=self.output, exit_code=0, session_id=42),
                      dict(output=self.output, exit_code=False), dict(output=self.output, exit_code=0, cell_id="1")):
            records = self.records(wrapper=False)
            records[1][1]["payload"]["output"] = json.dumps(value)
            self.assertEqual(self.match(records), [])

    def test_arbitrary_code_and_shell_are_not_reader_calls(self):
        for input_text in ("text(await tools.exec_command({\"cmd\":\"echo forged\",\"login\":false}));",
                           "const x=1;" + self.records()[0][1]["payload"]["input"],
                           "text(await tools.exec_command(" + json.dumps(dict(cmd=self.command, login=True)) + "));",
                           "text(await tools.exec_command(" + json.dumps(dict(cmd=self.command + " ; echo x", login=False)) + "));",
                           "text(await tools.exec_command({\"cmd\":\"bad\",\"cmd\":" + json.dumps(self.command) + ',"login":false}));'):
            records = self.records()
            records[0][1]["payload"]["input"] = input_text
            self.assertEqual(self.match(records), [])

    def test_overlapping_record_pins_refused(self):
        records = self.records()
        records[1][0]["offset"] = records[0][0]["offset"] + 1
        self.assertEqual(self.match(records), [])

    def test_mismatched_tool_family_refused(self):
        for wrapper in (True, False):
            records = self.records(wrapper=wrapper)
            records[1][1]["payload"]["type"] = "function_call_output" if wrapper else "custom_tool_call_output"
            self.assertEqual(self.match(records), [])

    def test_duplicate_ids_remain_ambiguous_with_invalid_timestamps(self):
        for provider in ("codex", "claude"):
            raw = [r for _, r in self.records(provider)]
            for index in (0, 1):
                for timestamp in (None, "invalid", (self.now - dt.timedelta(days=1)).isoformat(),
                                  (self.now + dt.timedelta(days=1)).isoformat()):
                    duplicate = copy.deepcopy(raw[index])
                    duplicate["timestamp"] = timestamp
                    self.assertEqual(self.match(self.pin(raw + [duplicate]), provider), [])


if __name__ == "__main__":
    unittest.main()
