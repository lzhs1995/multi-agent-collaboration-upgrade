"""Offline sender/provenance regressions. No command or peer is ever invoked."""
import copy
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from orchestration_common import ContractError, digest
from orchestration_inputs import input_texts, invocation_calls, require_unrelated
from test_orchestration_native import NativeFixture

RECEIVER = dict(provider="claude", session_id="42977dde-eb08-4900-aad1-1d20db13ff17",
                surface_uuid="1A38E155-605E-47B3-82F1-9E205FFD319E", surface_ref="surface:5996",
                pane_uuid="FDE776C4-E53E-4975-A580-5AFB133ED195")
SENDER = dict(provider="codex", session_id="01a13a5f-2412-7012-a872-501c8f75fda9",
              surface_uuid="FE97351D-9914-4E12-8EF8-953F28A819FA",
              workspace_uuid="6FB20312-9E66-43DB-AF09-92C3B18E1296",
              pane_uuid="02927867-37F5-42AA-833B-919B0E31DCD2")
OTHER = "A93448E1-E329-493D-9B45-568D5FC0DAD0"
TARGET = "ORIGINAL-BUSINESS-PACK"
COMMAND = shlex.join(["cmux-agent", "send", RECEIVER["surface_uuid"], TARGET])
SAFE = shlex.join(["cmux-agent", "send", OTHER, "independent maintenance"])


class SenderParsingTests(unittest.TestCase):
    def check(self, source, *, name="exec", reject=False):
        def run():
            calls = invocation_calls(name, source)
            for call in calls:
                require_unrelated(call, RECEIVER, SENDER, (TARGET,))
            return calls
        if reject:
            with self.assertRaises(ContractError):
                run()
        else:
            return run()

    def python(self, source, reject=True):
        return self.check(dict(cmd=shlex.join(["python3", "-B", "-c", source])),
                          name="exec_command", reject=reject)

    def test_real_concatenation_and_shell_quote_shapes(self):
        source = 'const p="cmux-agent send "; const who=' + json.dumps(OTHER) + ';'
        source += 'const note="maintenance; Codex alone \'quoted\'";'
        source += "const q = s => \"'\" + s.replace(/'/g, \"'\\\"'\\\"'\") + \"'\";"
        source += 'text(await tools.exec_command({cmd:p+who+" "+q(note),login:false}));'
        calls = self.check(source)
        self.assertEqual(calls[0]["text"], "maintenance; Codex alone 'quoted'")
        self.check(dict(cmd="cmux-agent send " + OTHER + " 'unterminated"), name="exec_command", reject=True)

    def test_report_literal_and_freeform_patch_are_data(self):
        source = 'text(' + json.dumps(COMMAND) + '); text(await tools.exec_command({cmd:' + json.dumps(SAFE) + '}));'
        self.check(source)
        self.assertEqual(self.check("*** Begin Patch\n+" + COMMAND, name="apply_patch"), [])
        self.python('import socket; s=socket.socket(); s.send(b"cmux-agent send report literal")', reject=False)

    def test_wrapped_commands_and_subprocess_are_senders(self):
        for command in [COMMAND, "rtk proxy env LANG=C timeout 3 nice -n 1 " + COMMAND,
                        shlex.join(["bash", "-lc", COMMAND])]:
            with self.subTest(command=command):
                self.check(dict(cmd=command), name="exec_command", reject=True)
        self.python('import subprocess as p; p.run(' + repr(shlex.split(COMMAND)) + ')')

    def test_immutable_map_and_object_shorthand_resolve(self):
        source = 'const rows=[[' + json.dumps(SAFE) + ',"x"]];'
        source += 'await Promise.all(rows.map(async ([cmd,label]) => tools.exec_command({cmd,login:false})));'
        self.assertEqual(self.check(source)[0]["surface"], OTHER)

    def test_destructured_argument_markers_remain_unresolved(self):
        readonly = "rtk read x.py"
        self.assertEqual(self.check('tools.exec_command({cmd:' + json.dumps(readonly) + '})'), [])
        for command in (readonly, COMMAND):
            for use in (
                'for (const [label,args] of rows){text({label,...await tools.exec_command(args)});}',
                'rows.slice(0).map(async ([label,args])=>tools.exec_command(args));',
            ):
                with self.subTest(command=command, use=use):
                    source = 'const rows=[["a",{cmd:' + json.dumps(command) + ',login:false}]];' + use
                    calls = invocation_calls("exec", source)
                    self.assertTrue(calls)
                    self.assertTrue(all(call["kind"] == "unresolved" for call in calls))
                    for call in calls:
                        with self.assertRaisesRegex(ContractError, "unresolved actual sender"):
                            require_unrelated(call, RECEIVER, SENDER, (TARGET,))

    def test_python_indirect_senders_never_disappear(self):
        args = repr(RECEIVER["surface_uuid"]) + ", " + repr(TARGET)
        examples = [
            'from cmux_bridge import submit_text as f; f=unknown; f(' + args + ')',
            'import cmux_bridge as b; f=b.submit_text; f=unknown; f(' + args + ')',
            'import cmux_bridge as b; fs=[b.submit_text]; fs[0](' + args + ')',
            'import cmux_bridge as b; fs={"send":b.submit_text}; fs["send"](' + args + ')',
            'import importlib; b=importlib.import_module("cmux_bridge"); b.submit_text(' + args + ')',
            'b=__import__("cmux_bridge"); getattr(b,"submit_text")(' + args + ')',
            'import cmux_bridge as b; vars(b)["submit_text"](' + args + ')',
            'import cmux_bridge as b; b.__dict__["submit_text"](' + args + ')',
            'import cmux_bridge as b; getattr(b, unknown)(' + args + ')',
        ]
        for source in examples:
            with self.subTest(source=source):
                self.python(source)

    def test_python_mutable_sender_arguments_and_process_aliases(self):
        source = 'import cmux_bridge as b; args=[' + repr(OTHER) + ',"safe"];'
        source += 'other=args; other[0]=' + repr(RECEIVER["surface_uuid"]) + '; other[1]=' + repr(TARGET) + '; b.submit_text(*args)'
        self.python(source)
        self.python('import subprocess as p; f=p.run; f=unknown; f(' + repr(COMMAND) + ',shell=True)')
        self.python('import os; getattr(os,"system")(' + repr(COMMAND) + ')')

    def test_joined_and_clustered_python_entry_options_keep_senders(self):
        source = 'import cmux_bridge as b; b.submit_text(' + repr(RECEIVER["surface_uuid"]) + ', ' + repr(TARGET) + ')'
        variants = [["-c" + source], ["-Bc", source], ["-BIc" + source],
                    ["-BW", "ignore", "-c" + source], ["-X", "dev", "-Sc", source],
                    ["-mcmux_bridge", "submit-text", "--surface", RECEIVER["surface_uuid"], "--text", TARGET],
                    ["-BIm", "cmux_bridge", "submit-text", "--surface", RECEIVER["surface_uuid"], "--text", TARGET]]
        for args in variants:
            with self.subTest(args=args):
                self.check(dict(cmd=shlex.join(["python3"] + args)), name="exec_command", reject=True)
        for args in (["-c" + 'print("cmux_bridge submit_text")'],
                     ["-BWcmux_bridge", "-c", 'print("ordinary report")'],
                     ["-IXcmux_bridge", "-c", 'print("ordinary report")']):
            with self.subTest(non_sender=args):
                self.assertEqual(self.check(dict(cmd=shlex.join(["python3"] + args)), name="exec_command"), [])

    def test_sender_provenance_preserves_exact_tool_wrapper_and_argv_boundaries(self):
        argv = ["rtk", "proxy", "/fixture/python3", "-BIc", 'import cmux_bridge as b; b.submit_text(' +
                repr(OTHER) + ', "independent maintenance")']
        args = dict(cmd=shlex.join(argv), login=False, workdir="/fixture", shell="/bin/zsh", tty=False)
        original = json.dumps(args)
        call = self.check(original, name="exec_command")[0]
        self.assertEqual(call["native_tool"], dict(name="exec_command", arguments=args, original_arguments=original))
        frame = call["shell"][0]
        self.assertEqual(frame["argv"], argv)
        self.assertEqual(frame["command"], args["cmd"])
        self.assertEqual(frame["wrappers"], ["rtk", "proxy"])
        self.assertEqual(frame["interpreter"], "/fixture/python3")
        self.assertEqual(frame["interpreter_options"], ["-BI"])
        self.assertEqual(frame["entrypoint_kind"], "inline")
        self.assertEqual(frame["entrypoint"], argv[-1])
        self.assertEqual(frame["entrypoint_argv"], [])

    def test_python_scope_and_module_namespaces_preserve_senders(self):
        args = repr(RECEIVER["surface_uuid"]) + ", " + repr(TARGET)
        for namespace in ("globals()", "locals()", "vars()"):
            for lookup in ('["submit_text"]', '[unknown]', '.get("submit_text")'):
                with self.subTest(namespace=namespace, lookup=lookup):
                    self.python('from cmux_bridge import submit_text; ' + namespace + lookup + '(' + args + ')')
            self.python('from cmux_bridge import submit_text; ns=' + namespace + '; ns["submit_text"](' + args + ')')
        for lookup in ('sys.modules["cmux_bridge"].submit_text', 'sys.modules[unknown].submit_text',
                       'sys.modules.get("cmux_bridge").submit_text'):
            with self.subTest(lookup=lookup):
                self.python('import sys; import cmux_bridge; ' + lookup + '(' + args + ')')
        code = 'import cmux_bridge as b; b.submit_text(' + args + ')'
        for lookup in ('__builtins__.__dict__["exec"]', '__builtins__["exec"]', 'getattr(__builtins__,"exec")'):
            with self.subTest(lookup=lookup):
                self.python(lookup + '(' + repr(code) + ')')
        self.python('from cmux_bridge import submit_text; globals()["f"]=submit_text; f(' + args + ')')
        self.python('x=3; print(globals()["x"]); print(locals()); print(vars())', reject=False)

    def test_python_namespace_method_mutations_cannot_hide_rebound_sender(self):
        sender = '__import__("cmux_bridge").submit_text'
        args = repr(RECEIVER["surface_uuid"]) + ", " + repr(TARGET)
        for namespace in ("globals()", "locals()", "vars()", 'vars(__import__("__main__"))'):
            for mutation in ('.update(s=' + sender + ')',
                             '.setdefault("s", ' + sender + ')',
                             '.__setitem__("s", ' + sender + ')'):
                with self.subTest(namespace=namespace, mutation=mutation):
                    self.python('s=print;' + namespace + mutation + ';s(' + args + ')')
                    self.python('s=print;ns=' + namespace + ';ns' + mutation + ';s(' + args + ')')
        self.python('s=print;mutate=globals().update;mutate(s=' + sender + ');s(' + args + ')')
        self.python('s=print;ns=globals();alias=ns;mutate=alias.update;mutate(s=' + sender + ');s(' + args + ')')
        # These mutate the namespace even without an explicit sender argument.
        # A missing original binding must not become proof of no input.
        for mutation in ('pop("s")', 'popitem()', 'clear()', '__delitem__("s")'):
            with self.subTest(mutation=mutation):
                self.python('s=print;globals().' + mutation + ';s(' + args + ')')
        self.python('x=3;ns=globals();print(ns.get("x"),ns.keys(),ns.items(),ns.values(),ns.copy())', reject=False)
        self.python('x=3;get=globals().get;print(get("x"))', reject=False)

    def test_python_intermediate_container_cannot_hide_namespace_sender(self):
        args = repr(RECEIVER["surface_uuid"]) + ", " + repr(TARGET)
        for mutation in ('dict.update(globals(), d)', 'globals().update(d)',
                         'ns=globals(); mutate=dict.update; mutate(ns, d)'):
            with self.subTest(mutation=mutation):
                self.python('import cmux_bridge as cb; d={"s":cb.submit_text};' + mutation + ';s(' + args + ')')
        self.python('d={"s":3}; print(d["s"])', reject=False)

    def test_python_unpack_walrus_reimport_and_executable_bindings(self):
        args = repr(RECEIVER["surface_uuid"]) + ", " + repr(TARGET)
        examples = [
            'import cmux_bridge as b; f, = [b.submit_text]; f(' + args + ')',
            'import cmux_bridge as b; (f := b.submit_text)(' + args + ')',
            'import cmux_bridge as b; import unrelated as b; b.submit_text(' + args + ')',
            'exec("from cmux_bridge import submit_text as f"); f(' + args + ')',
            'source="from cmux_bridge import submit_text as f"; e=exec; e(source); f(' + args + ')',
            'import cmux_bridge as b; f=eval("b.submit_text"); f(' + args + ')',
            'ns={}; exec("from cmux_bridge import submit_text as f", ns); ns["f"](' + args + ')',
        ]
        for source in examples:
            with self.subTest(source=source):
                self.python(source)

    def test_unknown_python_mutation_and_executable_reassignment(self):
        for mutation in ['unknown(args)', 'alias=args; unknown(alias)',
                         'exec(' + repr('args[0]=' + repr(RECEIVER["surface_uuid"])) + ')']:
            with self.subTest(mutation=mutation):
                self.python('import cmux_bridge as b; args=[' + repr(OTHER) + ',"safe"];' + mutation + ';b.submit_text(*args)')
        argv = repr(shlex.split(SAFE))
        self.python('import subprocess as p; args=' + argv + ';print(args);p.run(args)', reject=False)
        self.python('import cmux_bridge as b; d={"a":[' + repr(OTHER) + ',"safe"]};a=d["a"];unknown(a);b.submit_text(*d["a"])')

    def test_js_indirect_senders_never_disappear(self):
        args = '{cmd:' + json.dumps(COMMAND) + '}'
        for source in [
            '(tools.exec_command)(' + args + ')',
            'tools.exec_command?.(' + args + ')',
            'const f=tools.exec_command; Reflect.apply(f,null,[' + args + '])',
            'const f=tools.exec_command; Reflect.construct(f,[' + args + '])',
            'const f=tools.exec_command; f.call(null,' + args + ')',
            'const f=tools.exec_command; f.apply(null,[' + args + '])',
            'const f=tools.exec_command.bind(tools); f(' + args + ')',
            'let f=tools.exec_command; f=unknown; f(' + args + ')',
            'const f=tools.exec_command; unknown(f,' + args + ')',
        ]:
            with self.subTest(source=source):
                self.check(source, reject=True)

    def test_js_mutators_cannot_retain_stale_safe_arguments(self):
        for mutation in [
            'Object.defineProperty(a,"cmd",{value:' + json.dumps(COMMAND) + '});',
            'Reflect.set(a,"cmd",' + json.dumps(COMMAND) + ');',
            'Object.assign(a,{cmd:' + json.dumps(COMMAND) + '});',
            'const b=a; b.cmd=' + json.dumps(COMMAND) + ';',
            'unknownMutator(a);',
        ]:
            with self.subTest(mutation=mutation):
                self.check('const a={cmd:' + json.dumps(SAFE) + '};' + mutation + 'tools.exec_command(a)', reject=True)

    def test_exact_reference_body_and_invalid_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            raw = TARGET.encode()
            sha = digest(raw)
            path = Path(temp).resolve() / (sha + ".txt")
            path.write_bytes(raw)
            from orchestration_inputs import _SUFFIX
            wire = 'STATUS: MESSAGE_REFERENCE_V2 review BODY=' + json.dumps(str(path))
            wire += f' SHA256={sha} BYTES={len(raw)} {_SUFFIX}'
            self.assertEqual(input_texts(wire), [wire, TARGET])
            self.check(dict(cmd=shlex.join(["cmux-agent", "send", RECEIVER["surface_uuid"], wire])), name="exec_command", reject=True)
            path.write_bytes(b"changed")
            with self.assertRaises(ContractError):
                input_texts(wire)

    def test_malformed_rpc_and_lowercase_uuid_rejected(self):
        for params in [dict(surface=RECEIVER["surface_uuid"].lower(),text=TARGET),
                       dict(surface=RECEIVER["surface_uuid"],input="not-an-array")]:
            self.check(params, name="terminal.paste", reject=True)


class CrossCellProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = NativeFixture(self.temp.name, "codex")
        self.start = self.fixture.reader.snapshot()["offset"]

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, key, source, kind="custom_tool_call"):
        payload = dict(type=kind, name="functions.exec", call_id=key)
        payload["input" if kind == "custom_tool_call" else "arguments"] = source
        return self.fixture.append(dict(type="response_item", payload=payload))

    def result(self, key, *, header="Script completed", nested=None, kind="custom_tool_call_output"):
        output = [dict(type="input_text", text=header + "\nWall time 1.1 seconds\nOutput:\n")]
        if nested is not None:
            output.append(dict(type="input_text", text=json.dumps(nested)))
        return self.fixture.append(dict(type="response_item", payload=dict(type=kind, call_id=key, output=output)))

    def producer(self, key="p", command=SAFE):
        return self.invoke(key, 'store("command",' + json.dumps(command) + ');')

    def consumer(self, key="c"):
        return self.invoke(key, 'const cmd=load("command"); text(await tools.exec_command({cmd}));')

    def check(self, reject=False):
        def run():
            return self.fixture.reader.require_no_task_input(self.start, self.fixture.reader.snapshot()["offset"],
                task_id="test-task", pack=dict(path="/fixture/pack.json", sha256=TARGET), notice="task-notice",
                receiver=RECEIVER, sender=SENDER)
        if reject:
            with self.assertRaises(ContractError):
                run()
        else:
            return run()

    def test_successful_original_outer_result_commits_with_record_provenance(self):
        producer = self.producer()
        result = self.result("p", nested=dict(session_id=85805, output="nested process still running"))
        consumer = self.consumer()
        proof = self.check()
        self.assertEqual(proof["cached_reads"][0]["producer_call"], producer)
        self.assertEqual(proof["cached_reads"][0]["producer_result"], result)
        self.assertEqual(proof["cached_reads"][0]["consumer_call"], consumer)

    def test_nested_process_failure_does_not_make_outer_js_fail(self):
        self.producer()
        self.result("p", nested=dict(exit_code=2, output="Error: nested subprocess"))
        self.consumer()
        self.check()

    def test_failed_missing_duplicate_wrong_type_and_out_of_order_results(self):
        cases = ["missing", "failed", "pending", "duplicate_result", "duplicate_call", "wrong_type", "out_of_order"]
        for case in cases:
            with self.subTest(case=case):
                self.fixture.path.write_bytes(self.fixture.path.read_bytes()[:self.start])
                if case == "out_of_order":
                    self.result("p")
                self.producer()
                if case not in ("missing", "out_of_order"):
                    self.result("p", header="Script failed" if case == "failed" else
                                "Script running with cell ID cell1" if case == "pending" else "Script completed",
                                kind="function_call_output" if case == "wrong_type" else "custom_tool_call_output")
                if case == "duplicate_result":
                    self.result("p")
                if case == "duplicate_call":
                    self.producer()
                self.consumer()
                self.check(reject=True)

    def test_inflight_conflict_invalidates_both_producers(self):
        self.producer("a", COMMAND)
        self.producer("b", SAFE)
        self.result("a")
        self.result("b")
        self.consumer()
        self.check(reject=True)

    def test_failed_overwrite_invalidates_previously_committed_value(self):
        self.producer("a")
        self.result("a")
        self.producer("b", COMMAND)
        self.result("b", header="Script failed")
        self.consumer()
        self.check(reject=True)

    def test_alias_store_and_loaded_object_mutation_invalidate_cache(self):
        mutations = [
            'const s=store; s("command",' + json.dumps(COMMAND) + ');',
            'const v=load("command"); const alias=v; alias.cmd=' + json.dumps(COMMAND) + ';',
            'const v=load("command"); unknown(v);',
            'let v=load("command"); const a=v; a.cmd=' + json.dumps(COMMAND) + ';',
            'load("command").cmd=' + json.dumps(COMMAND) + ';',
            'Reflect.set(load("command"),"cmd",' + json.dumps(COMMAND) + ');',
            'store(unknownKey,{cmd:' + json.dumps(COMMAND) + '});',
            'function store(k,v){}; store("command",{cmd:' + json.dumps(SAFE) + '});',
            'const put=globalThis.store; put("command",{cmd:' + json.dumps(COMMAND) + '});',
        ]
        for source in mutations:
            with self.subTest(source=source):
                self.fixture.path.write_bytes(self.fixture.path.read_bytes()[:self.start])
                self.invoke("a", 'store("command",{cmd:' + json.dumps(SAFE) + '});')
                self.result("a")
                self.invoke("b", source)
                self.result("b", header="Script failed")
                self.invoke("c", 'tools.exec_command(load("command"))')
                self.check(reject=True)

    def test_mutation_after_store_does_not_export_the_earlier_literal(self):
        self.invoke("a", 'store("command",{cmd:' + json.dumps(SAFE) + '});load("command").cmd=' + json.dumps(COMMAND) + ';')
        self.result("a")
        self.invoke("c", 'tools.exec_command(load("command"))')
        self.check(reject=True)

    def test_transitive_cached_mutable_aliases_share_invalidation(self):
        self.invoke("a", 'store("command",{cmd:' + json.dumps(SAFE) + '});')
        self.result("a")
        self.invoke("b", 'const item=load("command");store("alias",item);')
        self.result("b")
        self.invoke("c", 'const item=load("alias");store("alias2",item);')
        self.result("c")
        self.invoke("d", 'load("alias2").cmd=' + json.dumps(COMMAND) + ';')
        self.result("d", header="Script failed")
        self.invoke("e", 'tools.exec_command(load("command"))')
        self.check(reject=True)

    def test_scalar_cache_ancestry_is_bound_to_each_completed_producer(self):
        self.producer()
        self.result("p")
        intermediate = self.invoke("p2", 'store("second",load("command"));')
        intermediate_result = self.result("p2")
        self.invoke("c", 'tools.exec_command({cmd:load("second")})')
        proof = self.check()
        self.assertEqual(proof["cached_reads"][-1]["producer_call"], intermediate)
        self.assertEqual(proof["cached_reads"][-1]["producer_result"], intermediate_result)
        self.assertEqual(len(proof["cached_reads"][-1]["ancestry"]), 1)

    def test_original_history_must_stay_identical_across_both_scans(self):
        self.producer()
        self.result("p")
        self.consumer()
        original = self.fixture.reader.records
        scans = 0
        def changing(start, end):
            nonlocal scans
            scans += 1
            if scans == 2:
                raw = self.fixture.path.read_bytes()
                self.fixture.path.write_bytes(raw.replace(b'independent maintenance', b'independent maintenancE'))
            yield from original(start, end)
        from unittest.mock import patch
        with patch.object(self.fixture.reader, "records", changing):
            self.check(reject=True)

    def test_cached_actual_business_input_stays_rejected(self):
        self.producer(command=COMMAND)
        self.result("p")
        self.consumer()
        self.check(reject=True)

    def test_report_strings_do_not_create_store_authority(self):
        self.invoke("p", 'text(' + json.dumps('store("command",' + json.dumps(SAFE) + ');') + ');')
        self.result("p")
        self.consumer()
        self.check(reject=True)


if __name__ == "__main__":
    unittest.main()
