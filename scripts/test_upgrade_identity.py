"""Original-gate identity regression: no process discovery or input operations."""
import copy
import unittest
from uuid import UUID

from upgrade_handshake_identity import bound_rows


def uid(n):
    return str(UUID(int=n))


def fixture():
    identity = dict(workspace_uuid=uid(1), caller_surface_uuid=uid(2),
                    target_surface_uuid=uid(3), target_pane_uuid=uid(4))
    binding = dict(identity=identity, provider="claude", session_id=uid(5))
    member = dict(surface_ref="surface:45", surface_uuid=uid(3), provider="claude",
                  workspace_ref="workspace:8", pane_ref="pane:20", ordinal=1)
    gate = dict(status="PASS", task_id="original", supervisor="codex",
                supervisor_surface_uuid=uid(2), workspace_uuid=uid(1), workspace_ref="workspace:8",
                native_participants=[dict(role="executor", surface_uuid=uid(3),
                                         native_session_id=uid(5), provider="claude")],
                workspace_proofs={"surface:45": dict(identity, caller_pane_uuid=uid(6),
                    workspace_ref="workspace:8", target_surface_ref="surface:45")})
    row = dict(executor="surface:45", ordinal=1, task_id="original", supervisor="codex",
               executor_provider="claude", workspace_ref="workspace:8")
    return [row], [member], gate, binding


def uuid_selector_fixture(uppercase=False):
    """r21 shape: UUID selector, original proof keyed by it, UI ref in proof."""
    rows, members, gate, binding = fixture()
    target = "e016648c-3409-4015-9fff-ac363b37e854"
    selector = target.upper() if uppercase else target
    rows[0]["executor"] = selector
    members[0].update(surface_ref=selector, surface_uuid=target.upper())
    proof = gate["workspace_proofs"].pop("surface:45")
    proof["target_surface_uuid"] = target.upper()
    gate["workspace_proofs"][selector] = proof
    gate["native_participants"][0]["surface_uuid"] = target.upper()
    binding["identity"].update(target_surface_uuid=target.upper(), caller_pane_uuid=uid(6),
                               target_pane_ref="pane:20")
    return rows, members, gate, binding


class IdentityTests(unittest.TestCase):
    def test_uuid_selector_original_ui_proof_and_case_return_original_objects(self):
        for uppercase in (False, True):
            args = uuid_selector_fixture(uppercase)
            before = copy.deepcopy(args)
            with self.subTest(uppercase=uppercase):
                resolved, row = bound_rows(*args)
                self.assertEqual(resolved[0][1], args[1][0]["surface_uuid"].lower())
                self.assertIs(row, args[0][0])
                self.assertIs(resolved[0][2], args[1][0])
                self.assertEqual(args, before)

    def test_uuid_selector_original_uuid_proof_is_canonicalized(self):
        args = uuid_selector_fixture()
        args[2]["workspace_proofs"][args[1][0]["surface_ref"]]["target_surface_ref"] = args[1][0]["surface_uuid"]
        self.assertIs(bound_rows(*args)[1], args[0][0])

    def test_uuid_selector_proof_identity_drift_stays_refused(self):
        for field in ("target_surface_uuid", "workspace_uuid", "caller_surface_uuid",
                      "caller_pane_uuid", "target_pane_uuid"):
            args = uuid_selector_fixture()
            args[2]["workspace_proofs"][args[1][0]["surface_ref"]][field] = uid(99)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "WORKSPACE_PROOF_CHANGED"):
                bound_rows(*args)
        for target in (uid(99), "unknown", "surface:", "surface:x", None):
            args = uuid_selector_fixture()
            args[2]["workspace_proofs"][args[1][0]["surface_ref"]]["target_surface_ref"] = target
            with self.subTest(target=target), self.assertRaises(ValueError):
                bound_rows(*args)

    def test_uuid_selector_cannot_use_differently_keyed_or_missing_proof(self):
        args = uuid_selector_fixture()
        proof = args[2]["workspace_proofs"].pop(args[1][0]["surface_ref"])
        args[2]["workspace_proofs"]["surface:45"] = proof
        with self.assertRaisesRegex(ValueError, "PROOF_REQUIRED"):
            bound_rows(*args)

    def test_uuid_selector_session_provider_pane_and_row_order_stay_bound(self):
        mutations = (
            lambda a: a[2]["native_participants"][0].update(native_session_id=uid(99)),
            lambda a: a[2]["native_participants"][0].update(provider="codex"),
            lambda a: a[2]["native_participants"][0].update(pane_uuid=uid(99)),
            lambda a: a[3]["identity"].update(target_pane_ref="pane:99"),
            lambda a: a[0][0].update(ordinal=2),
            lambda a: a[0][0].update(executor=uid(99)),
        )
        for index, mutate in enumerate(mutations):
            args = uuid_selector_fixture()
            mutate(args)
            with self.subTest(index=index), self.assertRaises(ValueError):
                bound_rows(*args)

    def test_cross_member_uuid_case_or_proof_alias_collision_refused(self):
        for collision in ("uuid-case", "proof-ref", "member-ref"):
            args = uuid_selector_fixture()
            second = dict(args[1][0], surface_ref="surface:99", surface_uuid=uid(99), ordinal=2)
            if collision == "uuid-case":
                second["surface_ref"] = args[1][0]["surface_uuid"]
            elif collision == "member-ref":
                second["surface_ref"] = "surface:45"
            proof = copy.deepcopy(args[2]["workspace_proofs"][args[1][0]["surface_ref"]])
            proof.update(target_surface_uuid=uid(99), target_surface_ref=second["surface_ref"])
            if collision == "proof-ref":
                second["surface_ref"] = uid(99)
                proof["target_surface_ref"] = "surface:45"
            args[2]["workspace_proofs"][second["surface_ref"]] = proof
            args[1].append(second)
            args[0].append(dict(args[0][0], executor=second["surface_ref"], ordinal=2))
            with self.subTest(collision=collision), self.assertRaisesRegex(ValueError, "ALIAS_AMBIGUOUS"):
                bound_rows(*args)

    def test_legacy_ref_proof_cannot_switch_to_another_ui_ref(self):
        args = fixture()
        args[2]["workspace_proofs"]["surface:45"]["target_surface_ref"] = "surface:99"
        with self.assertRaisesRegex(ValueError, "WORKSPACE_PROOF_CHANGED"):
            bound_rows(*args)

    def test_legacy_ref_returns_original_objects_without_mutation(self):
        args = fixture()
        before = copy.deepcopy(args)
        resolved, row = bound_rows(*args)
        self.assertIs(row, args[0][0])
        self.assertIs(resolved[0][2], args[1][0])
        self.assertEqual(args, before)

    def test_uuid_and_case_canonicalization(self):
        args = fixture()
        target = "abcdefab-cdef-4abc-8abc-abcdefabcdef"
        args[0][0]["executor"] = target.upper()
        args[1][0]["surface_uuid"] = target
        args[2]["native_participants"][0]["surface_uuid"] = target.upper()
        args[2]["workspace_proofs"]["surface:45"]["target_surface_uuid"] = target
        args[3]["identity"]["target_surface_uuid"] = target
        self.assertEqual(bound_rows(*args)[0][0][1], target)

    def test_changed_original_bindings_refused(self):
        for field, value in (("task_id", "another"), ("ordinal", True),
                             ("supervisor", "claude"), ("executor_provider", "codex"),
                             ("executor", "surface:99"), ("executor", uid(99)),
                             ("workspace_ref", "workspace:9")):
            with self.subTest(field=field, value=value):
                args = fixture()
                args[0][0][field] = value
                with self.assertRaises(ValueError):
                    bound_rows(*args)

    def test_original_pane_workspace_and_session_required(self):
        for field in ("target_pane_uuid", "caller_surface_uuid", "workspace_uuid", "target_surface_uuid"):
            with self.subTest(field=field):
                args = fixture()
                args[2]["workspace_proofs"]["surface:45"][field] = uid(99)
                with self.assertRaises(ValueError):
                    bound_rows(*args)
        args = fixture()
        args[2]["native_participants"][0]["native_session_id"] = uid(99)
        with self.assertRaises(ValueError):
            bound_rows(*args)

    def test_missing_proof_cannot_be_backfilled_from_live_refs(self):
        args = fixture()
        args[2]["workspace_proofs"] = {}
        with self.assertRaisesRegex(ValueError, "PROOF_REQUIRED"):
            bound_rows(*args)

    def test_duplicate_uuid_or_alias_refused(self):
        for collision in ("surface_uuid", "surface_ref"):
            args = fixture()
            second = dict(args[1][0], surface_uuid=uid(99), surface_ref="surface:99", ordinal=2)
            second[collision] = args[1][0][collision]
            args[1].append(second)
            args[0].append(dict(args[0][0], executor=second["surface_ref"], ordinal=2))
            with self.subTest(collision=collision), self.assertRaises(ValueError):
                bound_rows(*args)


if __name__ == "__main__":
    unittest.main()
