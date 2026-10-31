"""Zero-UI tests for the existing-workspace held allocation helpers (R511)."""
import unittest

import upgrade_existing_workspace as ew

WS = "35BED1FD-1E0B-452D-8DC0-9FFD67387CB0"
TREE = """window window:1000020000 F8C434E5-0C03-4C25-895C-9DF4BFF6CF39 [current]
└── workspace workspace:1000020189 35BED1FD-1E0B-452D-8DC0-9FFD67387CB0 "upgrade-lab-u2078-trio-u25-20261030-v1-B"
    ├── pane pane:1000020323 A3E7BC68-91F6-4F94-B72C-A86BDB74F113 [focused]
    │   └── surface surface:1000025213 76ED6998-0B09-4C02-B4CB-3F746CDDC2F5 [terminal] "SUPERVISOR | codex | surface:1000025213"
    ├── pane pane:1000020324 4BC763F6-E84E-4A22-90E8-BE1D8E090608
    │   └── surface surface:1000025214 583F053A-F82A-4573-8332-A7DA39DD0643 [terminal] "EXECUTOR_1 | claude"
    └── pane pane:1000020049 EA827FE3-84DD-4687-B8F8-B0C8D2AD1B2D [dock:global]
        └── surface surface:1000021490 5E5D9FD6-E21D-4EB4-88B9-26A29A0DFBC1 [terminal] "Supervisor"
"""
OLD = {"76ED6998-0B09-4C02-B4CB-3F746CDDC2F5", "583F053A-F82A-4573-8332-A7DA39DD0643",
       "5E5D9FD6-E21D-4EB4-88B9-26A29A0DFBC1"}
NEW = {"11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
       "33333333-3333-4333-8333-333333333333"}


class ExistingWorkspaceTests(unittest.TestCase):
    def test_argv_targets_the_pinned_workspace_and_never_creates_one(self):
        argv = ew.pane_argv("/opt/homebrew/bin/cmux", WS.lower(), "codex", "held-entry --lane codex")
        self.assertNotIn("new-workspace", argv)
        self.assertEqual(argv[argv.index("--workspace") + 1], WS)
        self.assertEqual((argv[argv.index("--command") + 1], argv[argv.index("--focus") + 1]),
                         ("held-entry --lane codex", "false"))
        self.assertEqual(argv[4:7], ["new-pane", "--type", "terminal"])

    def test_bad_lane_command_or_workspace_is_refused(self):
        for args in (("x", "cmd"), ("codex", " "), ("codex", None)):
            with self.assertRaises(ValueError):
                ew.pane_argv("cmux", WS, *args)
        with self.assertRaises(ValueError):
            ew.pane_argv("cmux", "workspace:1000020189", "codex", "cmd")

    def test_tree_surfaces_are_read_by_uuid_not_by_title(self):
        self.assertEqual(ew.surfaces(TREE), OLD)

    def test_disjoint_trio_passes_and_every_violation_is_refused(self):
        self.assertEqual(ew.require_disjoint(OLD, OLD | NEW, NEW), sorted(NEW))
        lost = set(list(OLD)[1:])
        cases = [(OLD, lost | NEW, NEW), (OLD, OLD | NEW | {"44444444-4444-4444-8444-444444444444"}, NEW),
                 (OLD, OLD | NEW, NEW | {list(OLD)[0]}), (OLD, OLD, set()),
                 (OLD, OLD | NEW | {"44444444-4444-4444-8444-444444444444"},
                  NEW | {"44444444-4444-4444-8444-444444444444"})]
        for before, after, created in cases:
            with self.assertRaises(ValueError):
                ew.require_disjoint(before, after, created)


class Done:
    def __init__(self, returncode, stdout=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


class CreatePanesTests(unittest.TestCase):
    def setUp(self):
        self.argvs = {lane: ew.pane_argv("cmux", WS, lane, "held " + lane) for lane in ew.LANES}
        self.intents, self.results, self.calls = [], [], []

    def run_with(self, codes, timeout_lane=None):
        def run(argv, timeout):
            lane = ew.LANES[len(self.calls)]
            self.calls.append(argv)
            if lane == timeout_lane:
                raise TimeoutError
            return Done(codes[lane], '{"surface_id":"x"}')
        return ew.create_panes(self.argvs, run, self.intents.append, self.results.append)

    def test_intent_first_then_one_call_per_lane_in_order(self):
        out = self.run_with(dict(codex=0, claude_a=0, claude_b=0))
        self.assertEqual((len(self.intents), len(self.calls), set(out)), (1, 3, set(ew.LANES)))
        self.assertEqual(self.calls, [self.argvs[lane] for lane in ew.LANES])
        self.assertEqual((self.results[-1]["status"], self.results[-1]["pane_creation_attempts"]), ("RETURNED", 3))

    def test_a_failed_lane_stops_before_the_next_and_is_recorded(self):
        with self.assertRaises(ValueError):
            self.run_with(dict(codex=0, claude_a=1, claude_b=0))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.results[-1]["failed_lane"], self.results[-1]["status"]), ("claude_a", "UNCONFIRMED"))

    def test_a_timeout_is_unknown_and_never_retried(self):
        with self.assertRaises(TimeoutError):
            self.run_with(dict(codex=0, claude_a=0, claude_b=0), timeout_lane="codex")
        self.assertEqual((len(self.calls), self.results[-1]["status"]), (1, "UNKNOWN"))

    def test_missing_lane_argv_is_refused_before_any_write(self):
        with self.assertRaises(ValueError):
            ew.create_panes({"codex": ["x"]}, lambda a, t: Done(0), self.intents.append, self.results.append)
        self.assertEqual((self.intents, self.results), ([], []))


class LaunchExistingTests(unittest.TestCase):
    """upgrade_live_entry._launch_existing wiring with a patched runner; no cmux call, no UI."""

    def test_three_new_pane_calls_in_the_pinned_workspace_with_intent_and_result(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import upgrade_live_entry as entry
        control = Path(os.path.realpath(tempfile.mkdtemp(prefix="r511-launch-existing-")))
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return Done(0, "{}")
        spec = {"pins": {"cmux": {"path": "/opt/homebrew/bin/cmux"}}}
        with patch.object(entry, "lane_command", side_effect=lambda ref, spec, lane: "held " + lane), \
                patch.object(entry.subprocess, "run", side_effect=fake_run):
            out = entry._launch_existing({"path": "startup"}, spec, control, WS.lower(), None)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all("new-workspace" not in a and a[a.index("--workspace") + 1] == WS for a in calls))
        self.assertEqual([a[a.index("--command") + 1] for a in calls], ["held " + lane for lane in ew.LANES])
        intent = json.loads((control / "intent.json").read_text())
        result = json.loads((control / "result.json").read_text())
        self.assertEqual((intent["existing_workspace_uuid"], intent["input_operations"]), (WS.lower(), 0))
        self.assertEqual((result["returncode"], result["workspace_creation_attempts"], result["pane_creation_attempts"]),
                         (0, 0, 3))
        self.assertEqual(out["returncode"], 0)

    def test_a_failed_pane_raises_and_leaves_the_unconfirmed_result(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import upgrade_live_entry as entry
        from orchestration_common import ContractError
        control = Path(os.path.realpath(tempfile.mkdtemp(prefix="r511-launch-existing-")))
        codes = iter([0, 2, 0])
        with patch.object(entry, "lane_command", side_effect=lambda ref, spec, lane: "held " + lane), \
                patch.object(entry.subprocess, "run", side_effect=lambda argv, **k: Done(next(codes))):
            with self.assertRaises(ContractError):
                entry._launch_existing({"path": "startup"}, {"pins": {"cmux": {"path": "cmux"}}}, control, WS.lower(), None)
        result = json.loads((control / "result.json").read_text())
        self.assertEqual((result["status"], result["failed_lane"], result["returncode"]), ("UNCONFIRMED", "claude_a", 1))


if __name__ == "__main__":
    unittest.main()
