"""R545 P1 on real generated startup artifacts: the semantic sender clause is pinned only when opted in."""
from pathlib import Path
import tempfile
import unittest

from orchestration_common import json_ref, read_ref
from upgrade_experiment import init_experiment
from upgrade_live_materialize import materialize_live

SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
WRAPPERS = Path("/Applications/cmux.app/Contents/Resources/bin")
CLAUSE = "semantic/sender-duties.json"


@unittest.skipUnless(SOURCE.exists() and WRAPPERS.exists(), "pinned MAC source or cmux wrappers missing")
class SemanticSenderArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="", dir="/tmp")
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name).resolve()
        binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
        binaries.update({p + "_wrapper": WRAPPERS / ("cmux-" + p + "-wrapper") for p in ("codex", "claude")})
        cls.pins = {}
        for short, enabled in (("f", False), ("t", True)):
            context = init_experiment(root / short, "semantic-" + short, SOURCE)
            runtime = json_ref(materialize_live(context, "B", binaries=binaries, provider_configuration=({}, {}),
                                                semantic_sender=enabled))
            cls.pins[enabled] = json_ref(runtime["startup"])["pins"]

    def policy(self, enabled, name):
        return read_ref(self.pins[enabled]["work_" + name], "frozen work policy").decode()

    def test_clause_is_frozen_in_both_work_policies_only_when_enabled(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(name=name):
                on, off = self.policy(True, name), self.policy(False, name)
                self.assertIn(CLAUSE, on)
                self.assertIn("only the original claude_a lane", on)
                self.assertIn("before any task claim", on)
                self.assertNotIn(CLAUSE, off)
                self.assertTrue(on.startswith(off.rstrip("\n").split("\n")[0]))

    def test_default_policy_bytes_do_not_change(self):
        self.assertNotIn("S2 semantic input", self.policy(False, "CLAUDE.md"))


MAC084 = Path("/Users/lzhs/Documents/cnm/tasks/02_Word_review/handoff/"
    "collaboration-maintenance-context-5994-20261011-v1/successor28-from6105-20261012-v1/"
    "continuation-01a13480-20261013-v1/maintenance0435-integration-20261015-v1/"
    "surface40-execution-20261021-v202/WORD0483-DISK-PREINPUT-v226/0.4.84-387ccf207a86/source")


@unittest.skipUnless(MAC084.exists() and WRAPPERS.exists(), "pinned MAC .84 source or cmux wrappers missing")
class Mac084MaterializeTests(unittest.TestCase):
    """R550 F2/R551: the complete P5 .84 source materializes once with the S2 sender clause."""

    def test_complete_084_source_materializes_without_ps_and_with_the_clause(self):
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as tmp:
            root = Path(tmp).resolve()
            binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
            binaries.update({p + "_wrapper": WRAPPERS / ("cmux-" + p + "-wrapper") for p in ("codex", "claude")})
            context = init_experiment(root / "m", "semantic-084", MAC084)
            runtime = json_ref(materialize_live(context, "B", binaries=binaries, provider_configuration=({}, {}),
                                                semantic_sender=True))
            delivery = Path(runtime["controller"]["path"]) / "scripts/cmux_native_delivery.py"
            self.assertNotIn("/bin/ps", delivery.read_text())
            self.assertIn("private_process.inventory_for_tty", delivery.read_text())
            pins = json_ref(runtime["startup"])["pins"]
            for name in ("AGENTS.md", "CLAUDE.md"):
                self.assertIn(CLAUSE, read_ref(pins["work_" + name], "frozen work policy").decode())


if __name__ == "__main__":
    unittest.main()
