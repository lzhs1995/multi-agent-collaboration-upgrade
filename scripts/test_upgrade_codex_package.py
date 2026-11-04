"""Package loss must refuse before any experimental client can be launched."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from orchestration_common import ContractError, file_ref, json_ref, write_json
from upgrade_codex_package import package_contract, verify_package_contract
from upgrade_live_integrity import artifact_ref


def fixture(root):
    package = root / "package"
    (package / "bin").mkdir(parents=True)
    (package / "codex-resources").mkdir()
    (package / "codex-path").mkdir()
    (package / "codex-resources/data").write_bytes(b"resource")
    binary = package / "codex-aarch64-apple-darwin"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    binary.chmod(0o555)
    helper = package / "bin/codex-code-mode-host"
    helper.write_bytes(b"#!/bin/sh\nexit 1\n")
    helper.chmod(0o555)
    (package / "bin/codex").symlink_to("../" + binary.name)
    (package / "codex-code-mode-host").symlink_to("bin/codex-code-mode-host")
    (package / "codex-package.json").write_text(json.dumps({
        "layoutVersion": 1, "version": "0.164.0", "variant": "codex",
        "entrypoint": "bin/codex", "resourcesDir": "codex-resources", "pathDir": "codex-path"}))
    return package, binary, helper


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.root = Path(self.tmp.name).resolve()
        self.package, self.binary, self.helper = fixture(self.root)
        self.pin = artifact_ref(self.binary)

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_tree_and_original_aliases_are_rechecked(self):
        contract = package_contract(self.package, self.pin)
        verify_package_contract(contract, self.pin)
        self.assertEqual(contract["helper"]["path"], str(self.helper))
        self.assertIn("codex-resources/data", contract["members"])

    def test_missing_helper_refuses_even_with_valid_main(self):
        self.helper.unlink()
        with self.assertRaises((ContractError, OSError)):
            package_contract(self.package, self.pin)
        self.assertEqual(artifact_ref(self.binary), self.pin)

    def test_packaged_host_without_adjacent_resolution_is_not_enough(self):
        (self.package / "codex-code-mode-host").unlink()
        with self.assertRaises((ContractError, OSError)):
            package_contract(self.package, self.pin)

    def test_outside_link_and_directory_link_are_rejected(self):
        link = self.package / "other"
        for target in ("/usr/bin/true", "codex-resources"):
            link.symlink_to(target)
            with self.assertRaisesRegex(ContractError, "LINK_ESCAPES_OR_IS_NOT_FILE"):
                package_contract(self.package, self.pin)
            link.unlink()

    def test_fifo_is_never_opened_or_hashed(self):
        os.mkfifo(self.package / "unexpected-fifo")
        with self.assertRaisesRegex(ContractError, "NOT_REGULAR"):
            package_contract(self.package, self.pin)

    def test_same_size_helper_change_is_detected(self):
        contract = package_contract(self.package, self.pin)
        self.helper.chmod(0o755)
        self.helper.write_bytes(b"#!/bin/sh\nexit 2\n")
        self.helper.chmod(0o555)
        with self.assertRaisesRegex(ContractError, "CONTENT_CHANGED"):
            verify_package_contract(contract, self.pin)

    def test_resource_removal_and_extra_file_are_detected(self):
        contract = package_contract(self.package, self.pin)
        (self.package / "codex-resources/data").unlink()
        with self.assertRaisesRegex(ContractError, "CONTENT_CHANGED"):
            verify_package_contract(contract, self.pin)
        (self.package / "codex-resources/data").write_bytes(b"resource")
        (self.package / "extra").write_bytes(b"later")
        with self.assertRaisesRegex(ContractError, "CONTENT_CHANGED"):
            verify_package_contract(contract, self.pin)

    def test_nonexecutable_helper_and_changed_entrypoint_refuse(self):
        self.helper.chmod(0o444)
        with self.assertRaisesRegex(ContractError, "EXECUTABLE_MISSING"):
            package_contract(self.package, self.pin)
        self.helper.chmod(0o555)
        entry = self.package / "bin/codex"
        entry.unlink()
        entry.symlink_to("codex-code-mode-host")
        with self.assertRaisesRegex(ContractError, "ENTRYPOINT_MISMATCH"):
            package_contract(self.package, self.pin)

    def test_wrong_main_pin_and_modified_contract_refuse(self):
        bad = dict(self.pin, sha256="0" * 64)
        with self.assertRaisesRegex(ContractError, "BINARY_PIN_CHANGED"):
            package_contract(self.package, bad)
        contract = package_contract(self.package, self.pin)
        bad = copy.deepcopy(contract)
        del bad["members"]["codex-resources/data"]
        with self.assertRaisesRegex(ContractError, "CONTENT_CHANGED"):
            verify_package_contract(bad, self.pin)

    def test_invalid_metadata_refuses_as_a_contract_error(self):
        for value in ([], None, "metadata", {"layoutVersion": True}):
            with self.subTest(value=value):
                (self.package / "codex-package.json").write_text(json.dumps(value))
                with self.assertRaisesRegex(ContractError, "LAYOUT_UNSUPPORTED"):
                    package_contract(self.package, self.pin)


class HeldPackageTests(unittest.TestCase):
    def test_package_loss_or_change_during_hold_refuses_before_any_native_start(self):
        import upgrade_live_entry as entry
        from upgrade_client_isolation import LANES
        from upgrade_startup_contract import PRE_EXEC_HOLD
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp).resolve()
            for lane in LANES:
                for change in ("none", "missing", "changed"):
                    with self.subTest(lane=lane, change=change):
                        base = root / (lane + "-" + change)
                        package, binary, helper = fixture(base)
                        pin = artifact_ref(binary)
                        contract = package_contract(package, pin)
                        control = base / "state/startup"
                        control.mkdir(parents=True)
                        (base / "work").mkdir()
                        (base / "startup.json").write_text("{}")
                        ref = file_ref(base / "startup.json")
                        spec = dict(stop_path=str(base / "STOP"), work_root=str(base / "work"),
                                    protected_surface_uuids=[], pins={"codex": pin},
                                    clients={lane: dict(environment={}, argv=["/usr/bin/true"],
                                                       native_root=str(base / lane), provider=lane)})
                        binding = dict(state_root=str(base / "state"), pre_exec_hold=PRE_EXEC_HOLD,
                                       codex_package=contract)
                        write_json(control / "intent.json", dict(startup=ref, spawn=True, spawn_authorized=True))
                        workspace, surface = str(uuid.uuid4()), str(uuid.uuid4())

                        def on_sleep(_):
                            if change == "missing":
                                helper.unlink()
                            elif change == "changed":
                                helper.chmod(0o755)
                                helper.write_bytes(b"#!/bin/sh\nexit 2\n")
                                helper.chmod(0o555)
                            held = file_ref(control / (lane + "-held.json"))
                            write_json(control / "release.json", dict(schema="upgrade-client-release-v1",
                                       startup=ref, workspace_uuid=workspace, held={lane: held}), exclusive=True)

                        with patch.dict(os.environ, {"CMUX_SURFACE_ID": surface, "CMUX_WORKSPACE_ID": workspace}), \
                                patch.object(entry.os, "chdir"), patch.object(entry.os, "umask"), \
                                patch("cmux_daemon_identity.process", return_value={"pid": 123, "birth": [1, 2]}), \
                                patch.object(entry, "start_server") as server, patch.object(entry.os, "execve") as execute, \
                                patch.object(entry.time, "sleep", side_effect=on_sleep) as slept:
                            if change == "none":
                                entry.start_client(ref, spec, binding, lane)
                                execute.assert_called_once()
                                self.assertEqual(server.call_count, 1 if lane == "codex" else 0)
                            else:
                                with self.assertRaises((ContractError, OSError)):
                                    entry.start_client(ref, spec, binding, lane)
                                server.assert_not_called()
                                execute.assert_not_called()
                            slept.assert_called_once()


SOURCE = Path.home() / ".local/share/multi-agent-collaboration/releases/0.4.34-d333557fb498/source"
WRAPPERS = Path("/Applications/cmux.app/Contents/Resources/bin")


@unittest.skipUnless(SOURCE.is_dir() and WRAPPERS.is_dir(), "fixed MAC fixture required")
class MaterializedPackageTests(unittest.TestCase):
    def test_new_frozen_controller_retains_check_before_launch_and_old_api_works(self):
        from upgrade_experiment import arm_paths, init_experiment, validate_context
        from upgrade_live_materialize import materialize_live
        from upgrade_startup_contract import validate_startup
        with tempfile.TemporaryDirectory(prefix="", dir="/tmp") as temp:
            root = Path(temp).resolve()
            package, binary, helper = fixture(root)
            context = init_experiment(root / "l", "package-gate", SOURCE)
            binaries = {k: Path("/usr/bin/true") for k in ("codex", "claude", "cmux", "rtk", "node")}
            binaries["codex"] = binary
            binaries.update({p + "_wrapper": WRAPPERS / ("cmux-" + p + "-wrapper")
                             for p in ("codex", "claude")})
            helper.unlink()
            with self.assertRaises((ContractError, OSError)):
                materialize_live(context, "B", binaries=binaries, provider_configuration=({}, {}),
                                 codex_package=package)
            self.assertFalse(any(p.exists() for p in arm_paths(validate_context(context), "B").values()))
            helper.write_bytes(b"#!/bin/sh\nexit 1\n")
            helper.chmod(0o555)
            runtime = json_ref(materialize_live(context, "B", binaries=binaries,
                               provider_configuration=({}, {}), codex_package=package))
            spec, binding = validate_startup(runtime["startup"])
            self.assertEqual(binding["codex_package"]["root"], str(package))
            rendered = Path(spec["controller"]) / "scripts/mac_upgrade_startup_contract.py"
            self.assertIn("from mac_upgrade_codex_package import", rendered.read_text())
            frozen_check = [sys.executable, "-B", "-c",
                            "import json,sys; from mac_upgrade_startup_contract import validate_startup; "
                            "validate_startup(json.loads(sys.argv[1])); print('FROZEN_PACKAGE_PASS')",
                            json.dumps(runtime["startup"])]
            frozen = subprocess.run(frozen_check, cwd=rendered.parent, capture_output=True, text=True,
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=120)
            self.assertEqual(frozen.returncode, 0, frozen.stdout + frozen.stderr)
            self.assertIn("FROZEN_PACKAGE_PASS", frozen.stdout)
            self.assertFalse((Path(runtime["state_root"]) / "startup/intent.json").exists())
            helper.unlink()
            with self.assertRaises((ContractError, OSError)):
                validate_startup(runtime["startup"])
            frozen = subprocess.run(frozen_check, cwd=rendered.parent, capture_output=True, text=True,
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=120)
            self.assertNotEqual(frozen.returncode, 0)
            self.assertIn("codex-code-mode-host", frozen.stderr)
            self.assertNotIn("ModuleNotFoundError", frozen.stderr)
            # Existing bindings do not silently acquire the new package policy.
            legacy = json_ref(materialize_live(context, "A1", binaries=binaries,
                              provider_configuration=({}, {})))
            _, legacy_binding = validate_startup(legacy["startup"])
            self.assertNotIn("codex_package", legacy_binding)


if __name__ == "__main__":
    unittest.main()
