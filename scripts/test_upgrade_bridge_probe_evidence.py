"""Exercise generated probe evidence with real parsers and offline transport."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re
import types
import unittest
from unittest.mock import Mock

from orchestration_common import ContractError
from upgrade_live_patch import bridge_probe_evidence, transform_live


SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
TOKEN = "B1_01234567"


def definition(text, name):
    return next(n for n in ast.parse(text).body if getattr(n, "name", None) == name)


def screen(body="", status="✻ Brewed for 3m", footer_suffix=""):
    border = "─" * 64
    # Preserve non-ASCII spacing, ANSI, CRLF and surrounding blank rows exactly.
    return ("\n\x1b[0m" + status + "\r\n\r\n" + border + "\r\n❯\u00a0" + body
            + " \r\n" + border + "\r\n  ⏵⏵ bypass permissions on (shift+tab to cycle)"
            + footer_suffix + "\r\n")


def parser_namespace(text):
    functions = {"_claude_bordered_compose", "compose_block_text", "delivery_compose_text",
                 "compose_rendered_text", "compose_block_is_empty", "_queued_or_active_input",
                 "_clipped_prefix_pattern", "_claude_framed_prompt"}
    constants = {"_PROMPT_GLYPH_RE", "_ACTIVITY_RE", "_CLAUDE_BORDER_RE", "_CLAUDE_DURATION",
                 "_CLAUDE_TOOL", "_CLAUDE_COUNTS", "_CLAUDE_ACTIVE_TOOL_RE", "_CLAUDE_SUMMARY_RE",
                 "_CLAUDE_HUD_TOKENS", "_CLAUDE_CONTEXT_ROW_RE", "_COMPOSE_CHROME_RE",
                 "_PROVIDER_HINT_ROW_RE", "_CLAUDE_GOAL_DURATION", "_CLAUDE_GOAL_ROW_RE",
                 "_CLAUDE_BYPASS_ROW_RE"}
    nodes = [n for n in ast.parse(text).body
             if isinstance(n, ast.FunctionDef) and n.name in functions
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants
                                                 for t in n.targets)]
    namespace = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "generated-cmux-parser", "exec"), namespace)
    return {name: namespace[name] for name in functions if name in namespace}


@unittest.skipUnless(SOURCE.is_dir(), "requires fixed local MAC source")
class ProbeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (SOURCE / "scripts/mac_harness.py").read_text()
        root = Path("/offline/bridge-evidence")
        paths = {name: root / name for name in ("state", "controllers", "tmp", "config", "bin")}
        cls.generated, cls.changes = transform_live(
            "mac_harness.py", cls.original, paths, root / "controller", compatibility=True)
        bridge, _ = transform_live("cmux_bridge.py", (SOURCE / "scripts/cmux_bridge.py").read_text(),
                                   paths, root / "controller", compatibility=True)
        cls.parsers = parser_namespace(bridge)

    def probe(self, screens, *, original=False, delete_limit=None):
        transport = types.SimpleNamespace(**self.parsers, COMPOSE_OCCUPIED="COMPOSE_OCCUPIED",
                                          read_screen=Mock(side_effect=screens), send_text=Mock(),
                                          send_key=Mock(), focus_surface=Mock(),
                                          clear_known_compose_by_delete=Mock())
        namespace = {"cmux": transport, "hashlib": hashlib, "time": types.SimpleNamespace(sleep=Mock()),
                     "_info": Mock(), "_fail": Mock(), "_now": lambda: "offline-now",
                     "interrupted_probe": types.SimpleNamespace(
                         PROBE_ERRORS=(ValueError,),
                         InterruptedBashBoundary=Mock(side_effect=ValueError("offline-no-boundary")))}
        text = self.original if original else self.generated
        nodes = [n for n in ast.parse(text).body
                 if isinstance(n, ast.Assign) and any(
                     isinstance(t, ast.Name) and t.id.startswith("BRIDGE_TEST_CLEAR_") for t in n.targets)]
        nodes.append(definition(text, "_bridge_test_one_locked"))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "generated-probe", "exec"), namespace)
        if delete_limit is not None:
            namespace["BRIDGE_TEST_CLEAR_DELETE_COUNT"] = delete_limit
        check = Mock()
        evidence, ok = namespace["_bridge_test_one_locked"](
            types.SimpleNamespace(task_id="offline-probe", lines=200, force_compose=False),
            "offline-peer", TOKEN, 1, check)
        return evidence, ok, transport, check

    def assert_snapshot(self, observation, expected):
        self.assertEqual(observation["screen"], expected)
        self.assertEqual(observation["screen_sha256"], hashlib.sha256(expected.encode("utf-8")).hexdigest())

    def assert_original_behavior(self, screens, evidence, ok, transport, check, **kwargs):
        old, old_ok, old_transport, old_check = self.probe(screens, original=True, **kwargs)
        self.assertEqual(ok, old_ok)
        self.assertEqual(transport.send_key.call_args_list, old_transport.send_key.call_args_list)
        self.assertEqual(transport.send_text.call_args_list, old_transport.send_text.call_args_list)
        self.assertEqual(transport.read_screen.call_args_list, old_transport.read_screen.call_args_list)
        self.assertEqual(check.call_args_list, old_check.call_args_list)
        stripped = {k: v for k, v in evidence.items() if k not in ("pre_read_observation", "final_observation")}
        if "clear_observations" in stripped:
            stripped["clear_observations"] = [
                {k: v for k, v in item.items() if k not in ("screen", "body", "prefix_matches", "active_or_queued")}
                for item in stripped["clear_observations"]]
        self.assertEqual(stripped, old)

    def test_owned_token_is_cleaned_with_original_key_budget_and_exact_screen_sha(self):
        screens = [screen(), screen(TOKEN), screen()]
        ev, ok, transport, check = self.probe(screens)
        self.assertTrue(ok)
        self.assertEqual([c.args[1] for c in transport.send_key.call_args_list],
                         ["end"] + ["backspace"] * len(TOKEN))
        self.assert_snapshot(ev["pre_read_observation"], screens[0])
        self.assert_snapshot(ev["final_observation"], screens[-1])
        item = ev["clear_observations"][0]
        self.assert_snapshot(item, screens[1])
        self.assertEqual(item["body"], TOKEN)
        self.assertTrue(item["prefix_matches"])
        self.assertFalse(item["active_or_queued"])
        self.assertTrue(item["owned_token_prefix"])
        self.assert_original_behavior(screens, ev, ok, transport, check)

    def test_foreign_busy_and_unknown_cleanup_never_delete_or_submit(self):
        cases = ((screen("user draft"), "user draft", False, False),
                 (screen(TOKEN, "✻ Thinking…"), TOKEN, True, True),
                 ("transient unknown screen " + TOKEN, None, False, False))
        for rendered, body, prefix, busy in cases:
            with self.subTest(screen=rendered):
                screens = [screen()] + [rendered] * 4
                ev, ok, transport, check = self.probe(screens)
                self.assertFalse(ok)
                transport.send_text.assert_called_once_with("offline-peer", TOKEN)
                transport.send_key.assert_not_called()
                self.assertEqual(ev["clear_key_count"], 0)
                self.assertEqual(ev["clear_attempts"], 3)
                self.assert_snapshot(ev["pre_read_observation"], screens[0])
                self.assert_snapshot(ev["final_observation"], rendered)
                for item in ev["clear_observations"]:
                    self.assert_snapshot(item, rendered)
                    self.assertEqual(item["body"], body)
                    self.assertEqual(item["prefix_matches"], prefix)
                    self.assertEqual(item["active_or_queued"], busy)
                    self.assertFalse(item["owned_token_prefix"])
                self.assert_original_behavior(screens, ev, ok, transport, check)

    def test_occupied_busy_unknown_initial_screen_is_zero_input_and_preserved(self):
        for initial in (screen(TOKEN), screen("foreign draft"), screen("", "✻ Thinking…"),
                        "unknown initial screen"):
            with self.subTest(screen=initial):
                ev, ok, transport, check = self.probe([initial])
                self.assertFalse(ok)
                transport.send_text.assert_not_called()
                transport.send_key.assert_not_called()
                check.assert_not_called()
                self.assert_snapshot(ev["pre_read_observation"], initial)
                self.assert_snapshot(ev["final_observation"], initial)
                self.assert_original_behavior([initial], ev, ok, transport, check)

    def test_distinct_transient_screens_and_final_read_are_not_overwritten(self):
        screens = [screen(), screen(TOKEN), screen("foreign draft"), "unknown-layout", screen()]
        ev, ok, transport, check = self.probe(screens, delete_limit=2)
        self.assertTrue(ok)
        self.assertEqual([c.args[1] for c in transport.send_key.call_args_list],
                         ["end", "backspace", "backspace"])
        for item, expected in zip(ev["clear_observations"], screens[1:-1], strict=True):
            self.assert_snapshot(item, expected)
        self.assert_snapshot(ev["final_observation"], screens[-1])
        self.assert_original_behavior(screens, ev, ok, transport, check, delete_limit=2)

    def test_transformation_changes_only_probe_and_preserves_original_owned_and_key_branch(self):
        changed = bridge_probe_evidence(self.original)
        old_nodes, new_nodes = ast.parse(self.original).body, ast.parse(changed).body
        self.assertEqual(len(old_nodes), len(new_nodes))
        self.assertEqual([old.name for old, new in zip(old_nodes, new_nodes)
                          if ast.dump(old) != ast.dump(new)], ["_bridge_test_one_locked"])
        old, new = (definition(text, "_bridge_test_one_locked") for text in (self.original, changed))
        for tree in (old, new):
            owned = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "owned" for t in n.targets))
            branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                          and ast.unparse(n.test) == "not cleared(post_clear_screen) and owned")
            if tree is old:
                original_owned, original_branch = ast.dump(owned), ast.dump(branch)
            else:
                self.assertEqual(ast.dump(owned), original_owned)
                self.assertEqual(ast.dump(branch), original_branch)
        self.assertIn("exact_bridge_probe_screen_evidence", self.changes)

    def test_unknown_ambiguous_or_reapplied_source_shape_refuses(self):
        marker = '        clear_observations.append({\n            "attempt": attempt,\n'
        duplicate = ast.get_source_segment(self.original, definition(self.original, "_bridge_test_one_locked"))
        for source in (self.original.replace(marker, marker.replace('"attempt"', '"changed"')),
                       self.original + "\n\n" + duplicate + "\n",
                       self.original.replace("def _bridge_test_one_locked(", "def unknown_probe("),
                       self.original.replace("and not input_busy(post_clear_screen)", "and True"),
                       bridge_probe_evidence(self.original)):
            with self.subTest(length=len(source)), self.assertRaises(ContractError):
                bridge_probe_evidence(source)


if __name__ == "__main__":
    unittest.main()
