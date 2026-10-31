"""Read-only adopt precheck for reusing one existing workspace; fixtures only, no cmux call."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest

from orchestration_common import ContractError, file_ref, utc_now
from upgrade_client_isolation import LANES
from upgrade_live_entry import adopt, adopt_argv, adopt_lanes, adopt_precheck, created_surface

WS = "35BED1FD-1E0B-452D-8DC0-9FFD67300000"


def uid(n):
    return "00000000-0000-4000-8000-%012d" % n


class AdoptPrecheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        closeout = Path(self._tmp.name) / "closeout.json"
        self.expected = dict(owner="owner-fixture", task="task-fixture", session_ids=["s-1", "s-2", "s-3"])
        self.evidence = dict(lanes={lane: dict(pane_uuid=uid(10 + i), surface_uuid=uid(20 + i), pid=1000 + i,
                                               birth=[1793400000 + i, 0], draft="", observed_at=utc_now(),
                                               screen_sha256="a" * 64, transcript_sha256="b" * 64)
                                    for i, lane in enumerate(LANES)})
        self.content = dict(self.expected, status="CLOSED", workspace_uuid=WS,
                            lanes={lane: dict(surface_uuid=uid(20 + i), pid=1000 + i, birth=[1793400000 + i, 0])
                                   for i, lane in enumerate(LANES)})
        closeout.write_text(json.dumps(self.content))
        self.closeout = file_ref(closeout)
        self.tree = {WS: dict(id=WS, panes=[dict(id=uid(10 + i), surfaces=[dict(id=uid(20 + i), type="terminal")])
                                            for i in range(len(LANES))])}
        self.ccc = dict(claude_excluded_workspace_ids=[WS],
                        nested=dict(discovery_excluded_surface_ids=[uid(20 + i) for i in range(len(LANES))]))

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, **changes):
        values = dict(workspace_uuid=WS, evidence=self.evidence, tree=self.tree, process=lambda pid: None,
                      clients=lambda surface: [], ccc_config=self.ccc, protected=[], closeout_ref=self.closeout,
                      expected=self.expected, now=time.time())
        values.update(changes)
        return adopt_precheck(**values)

    def test_exact_exited_excluded_closed_workspace_passes(self):
        value = self.check()
        self.assertEqual(value["workspace_uuid"], WS)
        self.assertEqual({row["exited"] for row in value["lanes"].values()}, {"PID_ABSENT"})
        self.assertEqual(value["cmux_environment_for_new_surface"], "NOT_VERIFIED")

    def test_pid_reused_by_another_birth_counts_as_exited(self):
        value = self.check(process=lambda pid: dict(birth=[1, 1]))
        self.assertEqual({row["exited"] for row in value["lanes"].values()}, {"PID_REUSED_BY_ANOTHER_BIRTH"})

    def test_refusals(self):
        extra_tab = copy.deepcopy(self.tree)
        extra_tab[WS]["panes"][0]["surfaces"].append(dict(id=uid(99)))
        extra_pane = copy.deepcopy(self.tree)
        extra_pane[WS]["panes"].append(dict(id=uid(98), surfaces=[dict(id=uid(97))]))
        draft = copy.deepcopy(self.evidence)
        draft["lanes"][LANES[0]]["draft"] = "unsent text"
        no_draft = copy.deepcopy(self.evidence)
        del no_draft["lanes"][LANES[1]]["draft"]
        live = self.evidence["lanes"][LANES[0]]
        cases = dict(
            missing_workspace=(dict(tree={}), "ADOPT_WORKSPACE_NOT_IN_CMUX_TREE"),
            missing_lane=(dict(evidence=dict(lanes={LANES[0]: live})), "ADOPT_EVIDENCE_LANES_REQUIRED"),
            extra_pane=(dict(tree=extra_pane), "ADOPT_PANE_SET_CHANGED"),
            extra_tab=(dict(tree=extra_tab, ccc_config=dict(self.ccc, discovery_excluded_surface_ids=[uid(99)])),
                       "ADOPT_SURFACE_SET_CHANGED"),
            protected=(dict(protected=[uid(21)]), "PRODUCTION_SURFACE_REFUSED"),
            workspace_not_excluded=(dict(ccc_config=dict(self.ccc, claude_excluded_workspace_ids=[])),
                                    "ADOPT_WORKSPACE_NOT_CCC_EXCLUDED"),
            surface_not_excluded=(dict(ccc_config=dict(claude_excluded_workspace_ids=[WS])),
                                  "ADOPT_SURFACE_NOT_CCC_EXCLUDED"),
            closeout_missing=(dict(closeout_ref=None), "ADOPT_OWNER_CLOSEOUT_MISSING"),
            closeout_changed=(dict(closeout_ref=dict(self.closeout, sha256="0" * 64)), "ADOPT_OWNER_CLOSEOUT_MISSING"),
            draft_present=(dict(evidence=draft), "ADOPT_DRAFT_NOT_PROVEN_EMPTY"),
            draft_unobserved=(dict(evidence=no_draft), "ADOPT_DRAFT_NOT_PROVEN_EMPTY"),
            client_alive=(dict(process=lambda pid: dict(birth=next(r["birth"] for r in self.evidence["lanes"].values()
                                                                    if r["pid"] == pid))),
                          "ADOPT_LIVE_CLIENT_REFUSED"),
        )
        for name, (changes, error) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ContractError, error):
                    self.check(**changes)

    def closeout_with(self, **changes):
        path = Path(self._tmp.name) / ("closeout-%d.json" % len(list(Path(self._tmp.name).glob("closeout-*"))))
        path.write_text(json.dumps(dict(self.content, **changes)))
        return file_ref(path)

    def test_semantic_refusals(self):
        other_pid = copy.deepcopy(self.content["lanes"])
        other_pid[LANES[0]]["pid"] = 4242
        unpinned = copy.deepcopy(self.evidence)
        del unpinned["lanes"][LANES[2]]["screen_sha256"]
        browser = copy.deepcopy(self.tree)
        browser[WS]["panes"][1]["surfaces"][0]["type"] = "browser"
        duplicate = dict(self.tree, OTHER=dict(id="OTHER", panes=[dict(id=uid(77), surfaces=[dict(id=uid(21))])]))
        cases = dict(
            wrong_owner=(dict(closeout_ref=self.closeout_with(owner="someone-else")), "ADOPT_CLOSEOUT_SEMANTICS_UNPROVEN"),
            wrong_task=(dict(closeout_ref=self.closeout_with(task="another-task")), "ADOPT_CLOSEOUT_SEMANTICS_UNPROVEN"),
            not_closed=(dict(closeout_ref=self.closeout_with(status="RUNNING")), "ADOPT_CLOSEOUT_SEMANTICS_UNPROVEN"),
            other_workspace=(dict(closeout_ref=self.closeout_with(workspace_uuid=uid(5))),
                             "ADOPT_CLOSEOUT_SEMANTICS_UNPROVEN"),
            closeout_other_pid=(dict(closeout_ref=self.closeout_with(lanes=other_pid)),
                                "ADOPT_CLOSEOUT_SEMANTICS_UNPROVEN"),
            identity_unknown=(dict(expected=None), "ADOPT_CLOSEOUT_SEMANTICS_UNPROVEN"),
            identity_partial=(dict(expected=dict(self.expected, session_ids=[])), "ADOPT_CLOSEOUT_SEMANTICS_UNPROVEN"),
            stale_observation=(dict(now=time.time() + 600), "ADOPT_DRAFT_NOT_PROVEN_EMPTY"),
            future_observation=(dict(now=time.time() - 600), "ADOPT_DRAFT_NOT_PROVEN_EMPTY"),
            unpinned=(dict(evidence=unpinned), "ADOPT_DRAFT_NOT_PROVEN_EMPTY"),
            not_terminal=(dict(tree=browser), "ADOPT_OLD_TAB_NOT_TERMINAL"),
            duplicate_uuid=(dict(tree=duplicate), "ADOPT_UUID_NOT_UNIQUE"),
            tab_client_unknown=(dict(clients=lambda surface: None), "ADOPT_OLD_TAB_CLIENT_UNPROVEN"),
            tab_reused=(dict(clients=lambda surface: [dict(pid=7, birth=[1, 1])]), "ADOPT_OLD_TAB_CLIENT_UNPROVEN"),
        )
        for name, (changes, error) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ContractError, error):
                    self.check(**changes)

    def test_created_tab_is_allowed_only_in_its_own_pane(self):
        grown = copy.deepcopy(self.tree)
        grown[WS]["panes"][0]["surfaces"].append(dict(id=uid(60), type="terminal"))
        value = self.check(tree=grown, created={LANES[0]: uid(60)})
        self.assertEqual(value["created"], {LANES[0]: uid(60)})
        for created in ({}, {LANES[1]: uid(60)}):
            with self.subTest(created=created):
                with self.assertRaisesRegex(ContractError, "ADOPT_SURFACE_NOT_CCC_EXCLUDED|ADOPT_SURFACE_SET_CHANGED"):
                    self.check(tree=grown, created=created)
        with self.assertRaisesRegex(ContractError, "ADOPT_CREATED_LANE_UNKNOWN"):
            self.check(tree=grown, created={"elsewhere": uid(60)})

    def reply(self, lane_index, **changes):
        value = dict(surface_id=uid(60 + lane_index), workspace_id=WS, pane_id=uid(10 + lane_index))
        value.update(changes)
        return json.dumps(value)

    def test_created_surface_names_exactly_one_new_tab_in_the_original_pane(self):
        precheck = self.check()
        self.assertEqual(created_surface(precheck, LANES[0], self.reply(0), {}), uid(60))
        unparsed = ("ok", "[]", json.dumps(dict(surface_id=uid(60), workspace_id=WS)), self.reply(0, surface_id="x"))
        for stdout in unparsed:
            with self.subTest(stdout=stdout):
                with self.assertRaisesRegex(ContractError, "ADOPT_SURFACE_RESULT_UNPARSED"):
                    created_surface(precheck, LANES[0], stdout, {})
        outside = dict(other_workspace=(self.reply(0, workspace_id=uid(5)), {}),
                       other_pane=(self.reply(0, pane_id=uid(11)), {}),
                       old_tab=(self.reply(0, surface_id=uid(21)), {}),
                       already_created=(self.reply(0), {LANES[1]: uid(60)}))
        for name, (stdout, created) in outside.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ContractError, "ADOPT_SURFACE_OUTSIDE_ORIGINAL_SCOPE"):
                    created_surface(precheck, LANES[0], stdout, created)

    def test_adopt_lanes_rechecks_scope_and_never_retries(self):
        precheck = self.check()
        ok = lambda lane: SimpleNamespace(returncode=0, stdout=self.reply(LANES.index(lane)), stderr="")

        def timeout(lane):
            raise subprocess.TimeoutExpired("cmux", 45)

        def changed(created):
            return dict(precheck, created=created) if not created else "a foreign tab appeared"

        def refusing(created):
            raise ContractError("ADOPT_SURFACE_SET_CHANGED: " + LANES[0])
        cases = dict(
            scope_changed=(ok, changed, "ADOPT_SCOPE_CHANGED_BEFORE: " + LANES[1], 1),
            observe_refuses=(ok, refusing, "ADOPT_SCOPE_CHANGED_BEFORE: " + LANES[0], 0),
            timeout=(timeout, None, "ADOPT_SURFACE_CREATION_UNKNOWN", 1),
            nonzero=(lambda lane: SimpleNamespace(returncode=1, stdout="", stderr="no"), None,
                     "ADOPT_SURFACE_CREATION_UNCONFIRMED", 1),
            unparsed=(lambda lane: SimpleNamespace(returncode=0, stdout="ok", stderr=""), None,
                      "ADOPT_SURFACE_RESULT_UNPARSED", 1))
        for name, (run, observe, error, attempts) in cases.items():
            with self.subTest(name):
                calls, control = [], Path(self._tmp.name) / ("control-" + name)
                with self.assertRaisesRegex(ContractError, error):
                    adopt_lanes({}, precheck, control,
                                observe or (lambda created: dict(precheck, created=created)),
                                lambda lane: calls.append(lane) or run(lane))
                self.assertEqual(len(calls), attempts)
        calls = []
        created = adopt_lanes({}, precheck, Path(self._tmp.name) / "control-ok",
                              lambda created: dict(precheck, created=created),
                              lambda lane: calls.append(lane) or ok(lane))
        self.assertEqual(created, {lane: uid(60 + i) for i, lane in enumerate(LANES)})
        self.assertEqual(calls, list(LANES))

    def test_adopt_argv_pins_uuid_presentation_and_the_original_pane(self):
        precheck = self.check()
        for i, lane in enumerate(LANES):
            with self.subTest(lane=lane):
                argv = adopt_argv("/pinned/cmux", precheck, lane, "lane command")
                self.assertEqual(argv[:5], ["/pinned/cmux", "--json", "--id-format", "uuids", "new-surface"])
                self.assertEqual(argv[argv.index("--pane") + 1], uid(10 + i))
                self.assertEqual(argv[argv.index("--workspace") + 1], WS)
                self.assertEqual(argv[argv.index("--command") + 1], "lane command")
                self.assertEqual(argv[argv.index("--type") + 1], "terminal")
                self.assertEqual(argv[-2:], ["--focus", "false"])

    def test_adopt_refuses_without_both_spawn_flags_before_any_effect(self):
        for flags in (dict(), dict(spawn=True), dict(spawn_authorized=True)):
            with self.subTest(**flags):
                with self.assertRaisesRegex(ContractError, "SPAWN_REQUIRES_BOTH_FLAGS"):
                    adopt({}, {}, {}, workspace_uuid=WS, precheck={},
                          observe=lambda: self.fail("observe must not run"), **flags)


if __name__ == "__main__":
    unittest.main()
