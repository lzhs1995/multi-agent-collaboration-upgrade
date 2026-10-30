"""The advice worker ships no client and reads no ambient input at all."""
import ast
import unittest
from pathlib import Path

import jev_advice
import jev_advice_worker
from jev_advice_worker import JOURNAL_SCHEMA, SCHEMA, advise, build_request, journal_entry

# Every module E3 introduces. A file that does not exist yet is skipped rather
# than silently passing: the modules this commit ships are asserted to exist.
E3_MODULES = ("orchestration_binding", "upgrade_advice_chain", "upgrade_advice_publish",
              "jev_advice_worker", "jev_s2_checks", "jev_advice_control_harness")
# These take every input as an argument, so an ambient read is a defect in them.
ARGUMENT_ONLY = ("jev_advice_worker", "upgrade_advice_publish", "jev_s2_checks", "upgrade_advice_chain")
NETWORK_ROOTS = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "asyncio",
                 "subprocess", "requests", "httpx", "typesafe"}
AMBIENT_CALLS = {"environ", "getenv", "open", "read_text", "read_bytes", "load"}


def lane(kind, *, verified=True, idle=True, author=False):
    return dict(kind=kind, verified=verified, idle_write_domain=idle, author=author)


LANES = dict(codex=lane("codex"), claude_a=lane("claude", author=True), claude_b=lane("claude"))


def import_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                raise AssertionError("relative import in an E3 module")
            roots.add(node.module.split(".")[0])
    return roots


def call_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute):
                names.add(function.attr)
            elif isinstance(function, ast.Name):
                names.add(function.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


class WorkerTests(unittest.TestCase):
    def forbidden_transport(self, request):
        raise AssertionError("the worker called a transport it must not have")

    def test_no_transport_and_no_budget_yield_not_configured(self):
        answer = advise(state_text="route this work", lanes=LANES, bound=False, budget=None)
        advice = answer["advice"]
        self.assertEqual(advice["status"], "NOT_CONFIGURED")
        self.assertEqual(advice["reason"], "JEV unavailable (NOT_CONFIGURED); rules decide")
        self.assertEqual((advice["fallback"], advice["candidates"]), ("rules", ()))
        self.assertEqual((answer["model_requests"], answer["request"]), (0, None))
        self.assertEqual([entry["model_requests"] for entry in answer["journal"]], [0])
        self.assertEqual(answer["journal"][0]["authority"], "WORKER_SELF_AUTHORED")
        self.assertEqual(answer["journal"][0]["schema"], JOURNAL_SCHEMA)

    def test_zero_budget_yields_not_configured_even_with_a_transport(self):
        for budget in (None, {}, dict(max_model_requests=0), dict(max_model_requests=-1),
                       dict(max_model_requests=True), dict(max_model_requests="80"), "80"):
            with self.subTest(budget=budget):
                answer = advise(state_text="route this work", lanes=LANES, bound=False,
                                budget=budget, transport=self.forbidden_transport, model="jev-1.14.0")
                self.assertEqual(answer["advice"]["status"], "NOT_CONFIGURED")
                self.assertEqual(answer["model_requests"], 0)

    def test_bound_node_is_never_asked(self):
        answer = advise(state_text="route this work", lanes=LANES, bound=True,
                        budget=dict(max_model_requests=80), transport=self.forbidden_transport,
                        model="jev-1.14.0")
        self.assertEqual(answer["advice"]["status"], "NOT_CALLED")
        self.assertEqual((answer["model_requests"], answer["request"]), (0, None))

    def test_request_is_built_verbatim_from_the_s0_questions(self):
        request = build_request("route this work", model="jev-1.14.0")
        self.assertEqual(request["schema"], SCHEMA)
        self.assertEqual(request["question"]["options"], ("coding", "debugging", "review", "literature"))
        self.assertEqual(request["question"], dict(id="work_kind", type="Choice",
                                                   options=jev_advice.WORK_KINDS))
        self.assertEqual(request["companion"]["id"], "work_kind_applies")
        self.assertEqual(request["companion"]["type"], "Noul")
        self.assertEqual(request["companion"]["statement"],
                         "`task` 描述的是一项需要分配给执行者的具体工作")
        self.assertEqual(request["companion"]["statement"],
                         jev_advice.QUESTIONS[0]["companion"]["statement"])
        for bad in ("", "   ", None, 7):
            with self.subTest(state=bad), self.assertRaises(ValueError):
                build_request(bad, model="jev-1.14.0")
        for bad in ("", None):
            with self.subTest(model=bad), self.assertRaises(ValueError):
                build_request("route this work", model=bad)

    def test_an_injected_transport_is_the_only_way_a_request_is_made(self):
        calls = []

        def transport(request):
            calls.append(request)
            return dict(answers=dict(work_kind="coding", work_kind_applies=0.9), http_status=200)

        clock = iter((10.0, 10.5))
        answer = advise(state_text="route this work", lanes=LANES, bound=False,
                        budget=dict(max_model_requests=80), transport=transport,
                        model="jev-1.14.0", now=lambda: next(clock))
        self.assertEqual(answer["advice"]["status"], "SUGGEST")
        self.assertEqual(answer["advice"]["candidates"], ("codex",))
        self.assertEqual((answer["model_requests"], len(calls)), (1, 1))
        self.assertEqual(answer["journal"][0]["http_status"], 200)
        self.assertAlmostEqual(answer["journal"][0]["elapsed_seconds"], 0.5)
        self.assertEqual(answer["journal"][0]["model"], "jev-1.14.0")

    def test_a_transport_error_is_passed_through_and_is_never_a_semantic_no(self):
        for response, status in ((dict(error="timeout"), "JEV_ERROR"),
                                 (dict(error="NOT_CONFIGURED"), "NOT_CONFIGURED"),
                                 (dict(answers=None), "JEV_ERROR"), ("not a dict", "JEV_ERROR")):
            with self.subTest(response=response):
                answer = advise(state_text="route this work", lanes=LANES, bound=False,
                                budget=dict(max_model_requests=80), transport=lambda _: response,
                                model="jev-1.14.0")
                self.assertEqual(answer["advice"]["status"], status)
                self.assertEqual(answer["advice"]["fallback"], "rules")
                self.assertEqual(answer["model_requests"], 1)
                self.assertIsNone(answer["journal"][0]["elapsed_seconds"])

    def test_journal_entry_refuses_an_invalid_request_count(self):
        for count in (-1, True, 1.0, None, "1"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                journal_entry(None, None, elapsed_seconds=None, http_status=None, model_requests=count)

    def test_not_configured_token_appears_exactly_once(self):
        source = Path(jev_advice_worker.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("NOT_CONFIGURED"), 1)
        self.assertEqual(source.count('error="NOT_CONFIGURED"'), 1)

    def test_module_imports_only_pure_dependencies(self):
        tree = ast.parse(Path(jev_advice_worker.__file__).read_text(encoding="utf-8"))
        self.assertLessEqual(import_roots(tree), {"jev_advice", "typing", "__future__"})
        self.assertFalse(call_names(tree) & AMBIENT_CALLS)

    def test_every_e3_module_is_free_of_network_and_ambient_input(self):
        directory = Path(jev_advice_worker.__file__).resolve().parent
        present = []
        for name in E3_MODULES:
            path = directory / (name + ".py")
            if not path.is_file():
                continue
            present.append(name)
            with self.subTest(module=name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                self.assertFalse(import_roots(tree) & NETWORK_ROOTS)
                if name in ARGUMENT_ONLY:
                    self.assertFalse(call_names(tree) & AMBIENT_CALLS)
        # The modules this commit ships must be measured, not skipped.
        self.assertLessEqual({"jev_advice_worker", "upgrade_advice_publish"}, set(present))


if __name__ == "__main__":
    unittest.main()
