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


if __name__ == "__main__":
    unittest.main()
