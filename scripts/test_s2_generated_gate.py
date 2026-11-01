"""R566 P2 (PARTIAL, R574): a REAL generated S2 campaign per arm, its fixture refusal and its generated steps.

Covered: the actual S2 registration (upgrade_jev_benchmark.register), init_experiment,
materialize_live(semantic_sender=True) and freeze_campaign as in ROOT's R549 check, per
arm; the frozen campaign carries semantic_input and sender duties; Coordinator.create
refuses it as fixtures (it opens only live, after its original START, R572); the
off-arm supervisor steps carry no registration, socket or key; the on arm has no steps
until the frozen advice index exists. Offline: synthetic local native sessions (R549).
NOT covered here: a coordinator, worker or index on this generated campaign; that
positive needs the permitted live run after P6. Off-zero/auth-STOP through the real
gate run on the fixture campaign in test_s2_real_gate.py.
"""
from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from orchestration_campaign import validate_campaign
from orchestration_common import ContractError, digest, file_ref, object_sha, tree_manifest, write_json
from orchestration_coordinator import Coordinator
import orchestration_freeze_v3 as freezer
from orchestration_native import NativeReader
import test_orchestration_freeze_v3 as freeze_fixtures  # a module alias, so its TestCase is not collected here
from test_orchestration_workload import CorpusFixture
import test_upgrade_live_control as control_fixtures
import test_upgrade_live_startup as startup_fixtures
from test_upgrade_jev_benchmark import PLAN, PLAN_SHA, factors
import upgrade_benchmark as v3
import upgrade_jev_benchmark as jb
from upgrade_experiment import arm_paths, init_experiment, validate_context
from upgrade_live_materialize import materialize_live
from upgrade_live_runtime import authenticated_sessions, validate_live_runtime
import upgrade_semantic_inputs as semantic
from upgrade_startup_contract import validate_startup


@unittest.skipUnless(startup_fixtures.SOURCE.is_dir() and startup_fixtures.WRAPPERS.is_dir()
                     and PLAN.is_file(), "requires pinned offline controller/wrappers and S2 plan")
class GeneratedS2FreezeTests(unittest.TestCase):
    append = freeze_fixtures.V3FreezeTests.append

    def setUp(self):
        self.root = Path("/tmp").resolve() / ("g" + uuid.uuid4().hex[:6])
        self.input_root = Path("/tmp").resolve() / ("i" + uuid.uuid4().hex[:6])
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.input_root, True)
        self.corpus = CorpusFixture(self.input_root / "data")

    def generated_run(self, advisory):
        """Register, materialize and freeze one real S2 group of this arm; start its first run."""
        self.assertEqual(file_ref(PLAN)["sha256"], PLAN_SHA)
        tree = tree_manifest(self.corpus.inputs)
        bundle = dict(schema=semantic.INPUT_SCHEMA, scope=semantic.SCOPE,
            base_corpus=dict(path=str(self.corpus.inputs), files=len(tree["files"]), tree_object_sha256=object_sha(tree)),
            origin="R566 OFFLINE synthetic input; not HUMAN or native proof",
            messages_by_class={kind: [dict(id=kind + "-" + str(i), body=body) for i, body in enumerate(
                ("先记录后续文档建议。", "保留原输入及写域约束。", "请现在检查本轮既定依赖，不追加任务。"), 1)]
                for kind in semantic.CLASSES},
            delivery_specification=dict(per_run=3, source_artifact_is_native_proof=False))
        semantic_ref = write_json(self.input_root / "INPUT.json", bundle, exclusive=True)
        groups, pairs = jb.layout(self.root, file_ref(PLAN), compact=True)
        fs = factors(pairs)
        for pair in fs.values():
            for arm in pair.values():
                arm["input_sha256"] = semantic.input_factor(semantic_ref)
        opportunities, _ = semantic.possible_sites(semantic_ref, self.root)
        registration = jb.register(self.root, "r566-offline-s2", PLAN, factors=fs, opportunities=opportunities,
                                   compact=True, semantic_input=semantic_ref)
        name = jb.PILOT if advisory == "advisory-on" else next(
            n for n, g in groups.items() if g["advisory"] == "advisory-off")
        group = v3.validate_registration(registration)["groups"][name]
        context = init_experiment(Path(group["context_path"]).parent, "r566-offline-gate", startup_fixtures.SOURCE)
        binaries = {key: Path("/usr/bin/true") for key in ("codex", "claude", "cmux", "rtk", "node")}
        binaries.update({p + "_wrapper": startup_fixtures.WRAPPERS / ("cmux-" + p + "-wrapper") for p in ("codex", "claude")})
        runtime_ref = materialize_live(context, "B", binaries=binaries, provider_configuration=({}, {}), semantic_sender=True)
        # LiveControlTests.observation reads both: the actual ref and its validated runtime (R571).
        self.runtime_ref = runtime_ref
        _, self.runtime = validate_live_runtime(runtime_ref, context_ref=context)
        self.ref = self.runtime["startup"]
        self.spec, _ = validate_startup(self.ref)
        self.paths = arm_paths(validate_context(context), "B")
        _, _, proof = control_fixtures.LiveControlTests.observation(self)
        adoption = write_json(self.spec["adoption_path"], proof, exclusive=True)
        self.sessions = authenticated_sessions(runtime_ref, adoption)
        session = self.sessions["codex"]
        before = Path(session["transcript"]["path"]).stat().st_size
        text = "Implement the R566 offline generated-gate fixture plan; not a live authorization."
        self.append("codex", dict(type="response_item", payload=dict(type="message", role="user",
                                                                      content=[dict(type="input_text", text=text)])))
        reader = NativeReader(session, fixtures=True)
        record = list(reader.records(before, reader.snapshot()["offset"]))[0][0]
        source = self.root / "source"
        (source / "scripts").mkdir(parents=True)
        original, names = freeze_fixtures.source_files()
        for item in names:
            shutil.copyfile(original / item, source / item)
        (source / "SKILL.md").write_text("R566 offline adapter fixture\n")
        request = write_json(self.root / "request.json", dict(version=3, experiment=context, runtime=runtime_ref,
            adoption=adoption, registration=registration, session_group=name, source=str(source),
            corpus=str(self.corpus.inputs), revision="r566-offline",
            native_authorizations=[dict(session=session, record=record, text_sha256=digest(text.encode()))]),
            exclusive=True)
        campaign_ref = freezer.freeze_campaign(request)["campaign"]
        campaign = validate_campaign(campaign_ref)
        return campaign_ref, campaign, campaign["runs"][0], group

    def frozen(self, advisory):
        campaign_ref, campaign, spec, group = self.generated_run(advisory)
        self.assertIn("semantic_input", campaign)
        self.assertEqual(group["advisory"], advisory)
        self.assertTrue((Path(campaign["root"]) / semantic.SENDER_DUTIES_PATH).is_file())
        # R572: a real campaign opens only live, after its original START; fixture execution is refused.
        with self.assertRaisesRegex(ContractError, "fixtures are never live evidence"):
            Coordinator.create(Path(campaign["root"]) / "runs" / spec["run_id"], campaign_ref, spec, fixtures=True)
        return campaign_ref, campaign

    def test_generated_off_group_instructions_name_both_points_without_registration_or_socket(self):
        from upgrade_supervisor_instructions import jev_steps
        campaign_ref, campaign = self.frozen("advisory-off")
        text = jev_steps(campaign, campaign_ref)
        dp1 = text[text.index("DP1:"):text.index("DP2:")]
        self.assertIn("--advice-sha256 null and --action supervisor_review", dp1)
        for word in ("--registration", "--worker-socket", "--credential-fd", "read_command"):
            self.assertNotIn(word, text)
        self.assertIn("--semantic-disposition REQUEST_ID", text)

    def test_generated_on_group_needs_the_frozen_advice_index_before_instructions(self):
        from upgrade_supervisor_instructions import jev_steps
        campaign_ref, campaign = self.frozen("advisory-on")
        # The index is written only after ROOT's real per-node grants (R572 step 4); none exist here.
        with self.assertRaises((OSError, ContractError)):
            jev_steps(campaign, campaign_ref)


if __name__ == "__main__":
    unittest.main()
