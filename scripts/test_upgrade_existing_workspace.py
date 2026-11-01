"""Zero-UI tests for the existing-workspace held allocation helpers (R511)."""
import copy
import json
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
TARGETS = {"codex": "583F053A-F82A-4573-8332-A7DA39DD0643",
           "claude_a": "583F053A-F82A-4573-8332-A7DA39DD0643",
           "claude_b": "76ED6998-0B09-4C02-B4CB-3F746CDDC2F5"}


def target_tree():
    return {"windows": [{"workspaces": [{"id": WS, "panes": [
        {"id": "A3E7BC68-91F6-4F94-B72C-A86BDB74F113", "surfaces": [
            {"id": TARGETS["claude_b"], "type": "terminal"}]},
        {"id": "4BC763F6-E84E-4A22-90E8-BE1D8E090608", "surfaces": [
            {"id": TARGETS["codex"], "type": "terminal"}]}]}]}]}


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

    def test_explicit_target_uses_new_split_without_focus_selection(self):
        argv = ew.pane_argv("cmux", WS, "codex", "held codex", surface_uuid=TARGETS["codex"].lower())
        self.assertEqual(argv[4:6], ["new-split", "right"])
        self.assertEqual(argv[argv.index("--surface") + 1], TARGETS["codex"])
        self.assertEqual(argv[argv.index("--workspace") + 1], WS)
        self.assertNotIn("new-pane", argv)
        self.assertNotIn("new-workspace", argv)

    def test_split_targets_require_all_lanes_and_an_existing_workspace(self):
        expected = {lane: target.lower() for lane, target in TARGETS.items()}
        self.assertEqual(ew.normalize_split_targets(WS, TARGETS), expected)
        for workspace, targets in ((None, TARGETS), (WS, {}), (WS, dict(TARGETS, foreign=WS)),
                                   (WS, dict(TARGETS, codex="surface:7")), (WS, [])):
            with self.subTest(workspace=workspace, targets=targets), self.assertRaises(ValueError):
                ew.normalize_split_targets(workspace, targets)

    def test_split_targets_refuse_missing_foreign_browser_and_duplicate_uuid(self):
        self.assertEqual(ew.verify_split_targets(target_tree(), WS, TARGETS),
                         {lane: target.lower() for lane, target in TARGETS.items()})
        missing = target_tree()
        missing["windows"][0]["workspaces"][0]["panes"].pop()
        foreign = target_tree()
        workspace = foreign["windows"][0]["workspaces"][0]
        moved = workspace["panes"].pop()
        foreign["windows"][0]["workspaces"].append({"id": "44444444-4444-4444-8444-444444444444",
                                                  "panes": [moved]})
        browser = target_tree()
        browser["windows"][0]["workspaces"][0]["panes"][1]["surfaces"][0]["type"] = "browser"
        duplicate = target_tree()
        duplicate["windows"][0]["workspaces"][0]["panes"].append(
            copy.deepcopy(duplicate["windows"][0]["workspaces"][0]["panes"][1]))
        for tree in (missing, foreign, browser, duplicate):
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                ew.verify_split_targets(tree, WS, TARGETS)

    def test_tree_surfaces_are_read_by_uuid_not_by_title(self):
        self.assertEqual(ew.surfaces(TREE), OLD)

    def test_tab_targets_pin_existing_panes_without_splitting_the_layout(self):
        panes = ew.tab_target_panes(target_tree(), WS, TARGETS)
        self.assertEqual(panes['codex'], '4BC763F6-E84E-4A22-90E8-BE1D8E090608')
        self.assertEqual(panes['claude_a'], panes['codex'])
        argv = ew.tab_argv('cmux', WS, 'codex', 'held codex', panes['codex'])
        self.assertIn('new-surface', argv)
        self.assertEqual(argv[argv.index('--pane') + 1], panes['codex'])
        self.assertEqual(argv[argv.index('--workspace') + 1], WS)
        self.assertFalse({'new-split', 'new-pane', 'new-workspace'} & set(argv))

    def test_tab_targets_refuse_a_duplicate_pane_even_with_unique_surfaces(self):
        tree = target_tree()
        tree['windows'][0]['workspaces'][0]['panes'][0]['id'] = tree['windows'][0]['workspaces'][0]['panes'][1]['id']
        with self.assertRaises(ValueError):
            ew.tab_target_panes(tree, WS, TARGETS)

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

    def test_real_no_space_trigger_uses_pinned_targets_and_publishes_full_result(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import upgrade_live_entry as entry
        with tempfile.TemporaryDirectory(prefix="r644-split-") as temporary:
            control = Path(temporary).resolve()
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(argv)
                if "tree" in argv:
                    return Done(0, json.dumps(target_tree()))
                if "new-pane" in argv:  # The field failure: focused pane is too small.
                    return Done(1, "")
                lane = ew.LANES[len(calls) - 2]
                self.assertIn("new-split", argv)
                self.assertEqual(argv[argv.index("--surface") + 1], TARGETS[lane])
                return Done(0, json.dumps({"surface_id": sorted(NEW)[len(calls) - 2]}))

            spec = {"pins": {"cmux": {"path": "cmux"}}, "existing_split_targets": TARGETS}
            with patch.object(entry, "lane_command", side_effect=lambda ref, spec, lane: "held " + lane), \
                    patch.object(entry.subprocess, "run", side_effect=fake_run):
                result = entry._launch_existing({"path": "startup"}, spec, control, WS.lower(), None)
            published = json.loads((control / "result.json").read_text())
            self.assertEqual(result["returncode"], 0)
            self.assertEqual((published["returncode"], published["pane_creation_attempts"]), (0, 3))
            self.assertEqual(set(published["lanes"]), set(ew.LANES))
            self.assertTrue(all(lane["returncode"] == 0 for lane in published["lanes"].values()))
            self.assertEqual(len(calls), 4)
            self.assertEqual(json.loads((control / "intent.json").read_text())["argv"],
                             {lane: calls[i + 1] for i, lane in enumerate(ew.LANES)})

    def test_missing_live_split_target_refuses_before_creation_intent(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import upgrade_live_entry as entry
        from orchestration_common import ContractError
        tree = target_tree()
        tree["windows"][0]["workspaces"][0]["panes"].pop()
        with tempfile.TemporaryDirectory(prefix="r644-missing-") as temporary:
            control = Path(temporary).resolve()
            with patch.object(entry.subprocess, "run", return_value=Done(0, json.dumps(tree))) as runner:
                with self.assertRaisesRegex(ContractError, "EXISTING_SPLIT_TARGET_UNVERIFIED"):
                    entry._launch_existing({"path": "startup"},
                        {"pins": {"cmux": {"path": "cmux"}}, "existing_split_targets": TARGETS},
                        control, WS.lower(), None)
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(list(control.iterdir()), [])

    def test_three_tabs_publish_a_success_the_real_inspector_accepts_without_new_panes(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import upgrade_live_entry as entry
        from upgrade_live_inspect import confirmed_launch
        with tempfile.TemporaryDirectory(prefix='heldtabs-') as temporary:
            control = Path(temporary).resolve()
            calls = []
            before = target_tree()
            def fake_run(argv, **kwargs):
                calls.append(argv)
                if 'tree' in argv:
                    return Done(0, json.dumps(before))
                self.assertIn('new-surface', argv)
                self.assertFalse({'new-split', 'new-pane', 'new-workspace'} & set(argv))
                return Done(0, json.dumps({'surface_id': sorted(NEW)[len(calls) - 2]}))
            ref = {'path': 'startup', 'sha256': 'a' * 64, 'bytes': 1}
            spec = {'pins': {'cmux': {'path': 'cmux'}}, 'existing_workspace_uuid': WS.lower(),
                    'existing_tab_targets': TARGETS}
            with patch.object(entry, 'lane_command', side_effect=lambda ref, spec, lane: 'held ' + lane), \
                    patch.object(entry.subprocess, 'run', side_effect=fake_run):
                value = entry._launch_existing(ref, spec, control, WS.lower(), None)
            self.assertEqual(value['returncode'], 0)
            result = json.loads((control / 'result.json').read_text())
            intent = json.loads((control / 'intent.json').read_text())
            self.assertEqual((result['workspace_creation_attempts'], result['pane_creation_attempts'],
                              result['surface_creation_attempts']), (0, 0, 3))
            self.assertTrue(confirmed_launch(intent, result, spec, ref))
            self.assertEqual(before, target_tree())
            self.assertEqual(len(calls), 4)
            for field, value in [('returncode', 1), ('pane_creation_attempts', 3),
                                 ('surface_creation_attempts', 2), ('existing_workspace_uuid', WS)]:
                with self.subTest(field=field):
                    changed = dict(result, **{field: value})
                    self.assertFalse(confirmed_launch(intent, changed, spec, ref))
            self.assertFalse(confirmed_launch(intent, dict(result, returncode=False), spec, ref))

    def test_failed_tab_preserves_the_actual_surface_attempt_count_without_retry(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import upgrade_live_entry as entry
        from orchestration_common import ContractError
        with tempfile.TemporaryDirectory(prefix='heldtabfail-') as temporary:
            control = Path(temporary).resolve()
            replies = [Done(0, json.dumps(target_tree())), Done(0, 'first tab'), Done(1, 'tab refused')]
            with patch.object(entry, 'lane_command', return_value='held'), \
                    patch.object(entry.subprocess, 'run', side_effect=replies) as runner:
                with self.assertRaises(ContractError):
                    entry._launch_existing({'path': 'startup'},
                        {'pins': {'cmux': {'path': 'cmux'}}, 'existing_tab_targets': TARGETS},
                        control, WS.lower(), None)
            result = json.loads((control / 'result.json').read_text())
            self.assertEqual((result['returncode'], result['pane_creation_attempts'],
                              result['surface_creation_attempts']), (1, 0, 2))
            self.assertEqual(result['failed_lane'], 'claude_a')
            self.assertEqual(runner.call_count, 3)


if __name__ == "__main__":
    unittest.main()
