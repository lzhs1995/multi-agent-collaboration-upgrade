import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orchestration_checker import check
from orchestration_common import ContractError, canonical, digest, file_ref, read_json, tree_manifest, write_json
from orchestration_workload import execute


class CorpusFixture:
    def __init__(self, base):
        self.root = Path(base).resolve()
        self.inputs = self.root / "corpus"
        (self.inputs / "fetched").mkdir(parents=True)
        commit = "a" * 40
        source = "demo@" + commit[:12] + "__code.py"
        values = {source: b"print(42)\n", "docs-typesafe__confidence.md": b"# Observed documentation\n", "tssdkpy-head.json": b""}
        rows = []
        for name, raw in values.items():
            (self.inputs / "fetched" / name).write_bytes(raw)
            if "@" in name:
                endpoint = "repos/example/demo/contents/code.py?ref=" + commit
                url, args = "https://api.github.com/" + endpoint, [endpoint]
            elif name.startswith("docs-"):
                url = "https://docs.typesafe.ai/confidence.md"
                args = ["CURL", url]
            else:
                endpoint = "repos/typesafe-ai/typesafe-sdk-python/commits/main"
                url, args = "https://api.github.com/" + endpoint, [endpoint]
            rows.append(dict(name=name, gh_api_args=args, url=url, fetched_at="2026-10-14T13:40:00+00:00",
                             exit_code=0 if raw else 1, bytes=len(raw), sha256=digest(raw), stderr="" if raw else "EOF\n"))
        (self.inputs / "MANIFEST.jsonl").write_bytes(b"".join(canonical(r) for r in rows))
        raw = values[source]
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        write_json(self.inputs / "BLOB-VERIFICATION.json", dict(checked=1, matched=1, rows=[dict(file=source,
            repo_path="code.py", bytes=len(raw), sha256=digest(raw), git_blob_sha1=blob, tree_blob_sha1=blob, match=True)]))

    def request(self, node, dependencies=None):
        output = self.root / "work" / node
        output.mkdir(parents=True)
        tree = tree_manifest(self.inputs, kind="output")
        value = dict(schema="orchestration-work-request-v2", task_id=f"corpus--run-01--{node}--a0001",
                     attempt_id=f"run-01:{node}:a0001", run_id="run-01", node_id=node, output_root=str(output),
                     inputs=[file_ref(self.inputs / n) for n in sorted(tree["files"])], input_tree=tree,
                     dependencies=[] if dependencies is None else dependencies)
        return write_json(self.root / (node + "-request.json"), value)

    def check_request(self, ref):
        request = read_json(ref["path"])
        request.update(schema="orchestration-check-request-v2", work_request=ref,
                       output_files=[file_ref(Path(request["output_root"]) / n) for n in ("report.md", "result.json")])
        return request

    def inventory_dependency(self):
        ref = self.request("inventory")
        request = read_json(ref["path"])
        execute(ref, request["output_root"])
        check(self.check_request(ref))
        tree = tree_manifest(request["output_root"], kind="output")
        outputs = [file_ref(Path(request["output_root"]) / n) for n in sorted(tree["files"])]
        accepted = write_json(self.root / "offline-inventory-acceptance.json", dict(
            task_id=request["task_id"], attempt_id=request["attempt_id"], node_id="inventory", run_id="run-01",
            acceptance_native={"offline_fixture": True}, output_tree=tree, output_files=outputs))
        return [dict(node_id="inventory", task_id=request["task_id"], attempt_id=request["attempt_id"],
                     accepted_evidence=accepted, output_tree=tree, output_files=outputs)]


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = CorpusFixture(self.tmp.name)

    def test_all_four_answers_and_independent_isolated_checker(self):
        dependency = self.fx.inventory_dependency()
        for node in ("independent_local", "verify_inputs", "dependent_local"):
            with self.subTest(node=node):
                ref = self.fx.request(node, dependency if node == "dependent_local" else None)
                request = read_json(ref["path"])
                execute(ref, request["output_root"])
                checked = self.fx.check_request(ref)
                result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name("orchestration_checker.py"))],
                    input=canonical(checked), capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["records"], 3)
                self.assertIn("tssdkpy-head.json", (Path(request["output_root"]) / "report.md").read_text())

    def test_rehashed_wrong_answer_is_not_a_pass(self):
        for node in ("inventory", "independent_local", "verify_inputs"):
            with self.subTest(node=node):
                ref = self.fx.request(node)
                request = read_json(ref["path"])
                execute(ref, request["output_root"])
                path = Path(request["output_root"]) / "result.json"
                result = read_json(path)
                result["data"]["invented_pass"] = True
                write_json(path, result)
                with self.assertRaisesRegex(ValueError, "independent calculation"):
                    check(self.fx.check_request(ref))

    def test_business_reentry_and_wrong_scope_do_not_overwrite(self):
        ref = self.fx.request("inventory")
        root = read_json(ref["path"])["output_root"]
        execute(ref, root)
        original = file_ref(Path(root) / "result.json")
        with self.assertRaises(ContractError):
            execute(ref, root)
        with self.assertRaises(ContractError):
            execute(ref, str(self.fx.root))
        self.assertEqual(original, file_ref(Path(root) / "result.json"))

    def test_extra_hidden_file_and_empty_directory_fail_input_tree(self):
        ref = self.fx.request("inventory")
        request = read_json(ref["path"])
        for kind in ("file", "directory"):
            p = self.fx.inputs / (".extra-" + kind)
            p.write_text("hidden") if kind == "file" else p.mkdir()
            with self.assertRaises(ContractError):
                execute(ref, request["output_root"])
            p.unlink() if kind == "file" else p.rmdir()

    def test_source_hash_changed_and_failure_deleted_are_rejected(self):
        ref = self.fx.request("inventory")
        request = read_json(ref["path"])
        p = self.fx.inputs / "fetched/tssdkpy-head.json"
        p.write_bytes(b"pretend success")
        with self.assertRaises(ContractError):
            execute(ref, request["output_root"])
        p.unlink()
        with self.assertRaises(ContractError):
            execute(ref, request["output_root"])

    def test_rehashed_source_with_unpinned_commit_is_rejected(self):
        p = self.fx.inputs / "MANIFEST.jsonl"
        rows = [json.loads(line) for line in p.read_text().splitlines()]
        rows[0]["url"] = rows[0]["url"].split("?ref=")[0] + "?ref=main"
        rows[0]["gh_api_args"] = [rows[0]["url"].removeprefix("https://api.github.com/")]
        p.write_bytes(b"".join(canonical(r) for r in rows))
        ref = self.fx.request("verify_inputs")
        with self.assertRaisesRegex(ContractError, "Git commit"):
            execute(ref, read_json(ref["path"])["output_root"])

    def test_other_run_dependency_is_rejected(self):
        dependency = self.fx.inventory_dependency()
        accepted = read_json(dependency[0]["accepted_evidence"]["path"])
        accepted["run_id"] = "another-run"
        dependency[0]["accepted_evidence"] = write_json(Path(dependency[0]["accepted_evidence"]["path"]), accepted)
        ref = self.fx.request("dependent_local", dependency)
        with self.assertRaisesRegex(ContractError, "matching original acceptance"):
            execute(ref, read_json(ref["path"])["output_root"])

    def test_checker_rejects_extra_output_and_report_failure_omission(self):
        ref = self.fx.request("inventory")
        root = Path(read_json(ref["path"])["output_root"])
        execute(ref, str(root))
        extra = root / ".hidden"
        extra.write_text("extra")
        with self.assertRaisesRegex(ValueError, "output set"):
            check(self.fx.check_request(ref))
        extra.unlink()
        report = root / "report.md"
        report.write_text(report.read_text().replace("tssdkpy-head.json", "none"))
        with self.assertRaisesRegex(ValueError, "report identity"):
            check(self.fx.check_request(ref))


if __name__ == "__main__":
    unittest.main()
