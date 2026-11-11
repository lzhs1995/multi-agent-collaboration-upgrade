"""Bounded Python provenance for journal inspection; parsed code never runs.

Values are literals, symbolic imports, or unknown values retaining possible
sender origins. Rebinding and container mutation can remove literal authority,
but cannot turn a possible sender into evidence that no input happened.
"""
from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass

from orchestration_common import ContractError
from orchestration_input_syntax import LIMIT, UNKNOWN

BRIDGES = ("cmux_bridge", "cmux_agent", "cmux_agent_adapter", "successor_native_delivery", "successor_rebind_cli")
PROCESSES = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}
NAMESPACE_ORIGIN = "<python-namespace>"
NAMESPACE_READS = {"get", "keys", "items", "values", "copy", "__getitem__", "__contains__",
                   "__iter__", "__len__", "__reversed__"}


@dataclass(frozen=True)
class Symbol:
    path: str


@dataclass(frozen=True)
class Opaque:
    origins: frozenset


@dataclass(frozen=True)
class Namespace:
    value: object


def origins(value, depth=0):
    if depth > 24:
        raise ContractError("Python provenance nesting exceeds observation bound")
    if isinstance(value, Symbol):
        return {value.path}
    if isinstance(value, Opaque):
        return set(value.origins)
    if isinstance(value, Namespace):
        return {NAMESPACE_ORIGIN} | origins(value.value, depth + 1)
    if isinstance(value, (dict, list, tuple)):
        return set().union(*(origins(v, depth + 1) for v in (value.values() if isinstance(value, dict) else value)))
    return set()


def sensitive(path):
    root, _, member = path.partition(".")
    return (root in BRIDGES or root == "subprocess" and (not member or member.split(".")[0] in PROCESSES) or
            root == "os" and (not member or member == "system" or member.startswith(("exec", "spawn"))) or
            path in {"builtins.exec", "builtins.eval", "<dynamic-import>"} or root == "<module-registry>" or
            path == "sys.modules" or path.startswith("sys.modules."))


def opaque(*values):
    found = set().union(*(origins(value) for value in values))
    return Opaque(frozenset(found)) if found else UNKNOWN


def python_calls(source, *, delivery_calls, rpc_call, kinds, rpc_methods, depth=0, inherited=None):
    def unresolved(reason):
        return dict(kind="unresolved", reason=reason)

    if depth > 8 or len(source.encode()) > LIMIT:
        raise ContractError("Python source exceeds observation bound")
    try:
        tree = ast.parse(source)
        nodes = list(ast.walk(tree))
    except (SyntaxError, ValueError, RecursionError):
        return [unresolved("unparsed Python sender")] if re.search(
            r"cmux|successor|submit_|subprocess|exec|eval|os\.", source) else []
    if len(nodes) > 50000:
        raise ContractError("Python AST exceeds observation bound")
    definitions, invalid = {}, set()
    inherited = inherited or {}

    def bind(target, value):
        if isinstance(target, ast.Name):
            definitions.setdefault(target.id, []).append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for index, item in enumerate(target.elts):
                bind(item, ast.Subscript(value=value, slice=ast.Constant(value=index), ctx=ast.Load()))
        elif isinstance(target, (ast.Subscript, ast.Attribute)):
            base = target.value
            while isinstance(base, (ast.Subscript, ast.Attribute)):
                base = base.value
            if isinstance(base, ast.Name):
                invalid.add(base.id)

    def gather(new_nodes):
        for node in new_nodes:
            if isinstance(node, ast.Import):
                for item in node.names:
                    bind(ast.Name(id=item.asname or item.name.split(".")[0]), Symbol(item.name if item.asname else item.name.split(".")[0]))
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    if item.name == "*":
                        if node.module in BRIDGES:
                            for name in kinds:
                                bind(ast.Name(id=name), Symbol(node.module + "." + name))
                    else:
                        bind(ast.Name(id=item.asname or item.name), Symbol(node.module + "." + item.name))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    bind(target, node.value)
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr, ast.AugAssign)):
                bind(node.target, node.value)
                if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                    invalid.add(node.target.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bind(ast.Name(id=node.name), node)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse", "update", "setdefault", "__setitem__"}:
                bind(ast.Attribute(value=node.func.value, attr="<mutation>", ctx=ast.Store()), UNKNOWN)

    def propagate():
        # Also invalidate a parent whose mutable member escaped via an alias.
        changed = True
        while changed:
            before = set(invalid)
            for name, expressions in definitions.items():
                for expr in expressions:
                    base = expr
                    while isinstance(base, (ast.Subscript, ast.Attribute)):
                        base = base.value
                    if isinstance(base, ast.Name) and (name in invalid or base.id in invalid):
                        invalid.update((name, base.id))
            changed = before != invalid

    gather(nodes)
    propagate()

    def attribute(value, name):
        if isinstance(value, Namespace) and isinstance(name, str):
            return Opaque(frozenset(origins(value) | {NAMESPACE_ORIGIN + "." + name}))
        if isinstance(value, Symbol) and isinstance(name, str):
            if value.path == "sys" and name == "modules":
                return Namespace(Symbol("<module-registry>"))
            return Namespace(value) if name == "__dict__" else Symbol(value.path + "." + name)
        if isinstance(value, Opaque) and isinstance(name, str):
            return Opaque(value.origins | frozenset(path + "." + name for path in value.origins))
        return opaque(value)

    steps = 0

    def resolve(node, seen=frozenset(), level=0):
        nonlocal steps
        steps += 1
        if level > 32 or steps > 300000:
            raise ContractError("Python provenance work bound exceeded")
        if isinstance(node, (Symbol, Opaque, Namespace)) or node is UNKNOWN:
            return node
        if not isinstance(node, ast.AST):
            return node
        descend = lambda item: resolve(item, seen, level + 1)
        if isinstance(node, ast.Name):
            if node.id in seen:
                return UNKNOWN
            if node.id in definitions:
                values = [resolve(v, seen | {node.id}, level + 1) for v in definitions[node.id]]
                return values[0] if len(values) == 1 and node.id not in invalid else opaque(*values)
            return inherited.get(node.id, Symbol("builtins" if node.id == "__builtins__" else "builtins." + node.id))
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple)):
            return [descend(value) for value in node.elts]
        if isinstance(node, ast.Dict):
            keys = [descend(key) if key is not None else UNKNOWN for key in node.keys]
            values = [descend(value) for value in node.values]
            return dict(zip(keys, values)) if all(isinstance(k, (str, int)) for k in keys) and len(set(keys)) == len(keys) else opaque(*values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = descend(node.left), descend(node.right)
            if type(left) is type(right) and isinstance(left, (str, list)):
                value = left + right
                return value if len(value) <= LIMIT else UNKNOWN
            return opaque(left, right)
        if isinstance(node, ast.JoinedStr):
            parts = [descend(n.value) if isinstance(n, ast.FormattedValue) and n.conversion == -1 and n.format_spec is None else
                     n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else UNKNOWN for n in node.values]
            return "".join(parts) if all(isinstance(v, str) for v in parts) and sum(map(len, parts)) <= LIMIT else UNKNOWN
        if isinstance(node, ast.Attribute):
            return attribute(descend(node.value), node.attr)
        if isinstance(node, ast.Subscript):
            value, key = descend(node.value), descend(node.slice)
            if isinstance(value, Namespace):
                if isinstance(value.value, dict):
                    return value.value.get(key, UNKNOWN) if isinstance(key, str) else opaque(value)
                if value.value == Symbol("<module-registry>"):
                    return Symbol(key) if isinstance(key, str) else opaque(value)
                return attribute(value.value, key)
            if value == Symbol("builtins"):
                return attribute(value, key)
            if isinstance(value, dict) and isinstance(key, (str, int)):
                return value.get(key, UNKNOWN)
            if isinstance(value, list) and type(key) is int and -len(value) <= key < len(value):
                return value[key]
            return opaque(value)
        if isinstance(node, ast.NamedExpr):
            return descend(node.value)
        if isinstance(node, ast.IfExp):
            return opaque(descend(node.body), descend(node.orelse))
        if isinstance(node, ast.Call):
            function = descend(node.func)
            args = [descend(arg) for arg in node.args]
            path = function.path if isinstance(function, Symbol) else ""
            if path in {"importlib.import_module", "builtins.__import__"}:
                return Symbol(args[0]) if args and isinstance(args[0], str) else Opaque(frozenset({"<dynamic-import>"}))
            if path == "builtins.getattr" and len(args) >= 2:
                return attribute(args[0], args[1])
            if path == "builtins.vars" and len(args) == 1:
                return Namespace(args[0])
            if path in {"builtins.globals", "builtins.locals", "builtins.vars"} and not args and not node.keywords:
                # Capture possible bindings without evaluating this namespace.
                # `seen` prevents a namespace alias from recursively containing
                # itself. Ambiguous keys retain every possible sender origin.
                return Namespace({name: resolve(ast.Name(id=name), seen, level + 1)
                                  for name in definitions.keys() | inherited.keys() if name not in seen})
            if path == "builtins.eval" and args and isinstance(args[0], str):
                try:
                    expr = ast.parse(args[0], mode="eval").body
                except (SyntaxError, ValueError, RecursionError):
                    return opaque(function)
                return descend(expr)
            # A return value does not inherit the function's sender authority.
            return opaque(function) if isinstance(function, Opaque) or sensitive(path) and path.startswith("<") else UNKNOWN
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            values = [resolve(n, seen, level + 1) for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)]
            return opaque(*values)
        return UNKNOWN

    def arguments(node):
        args, kwargs, uncertain = [], {}, False
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                value = resolve(arg.value)
                if isinstance(value, list):
                    args.extend(value)
                else:
                    uncertain = True
            else:
                args.append(resolve(arg))
        for item in node.keywords:
            value = resolve(item.value)
            if item.arg is None:
                if isinstance(value, dict) and all(isinstance(k, str) for k in value) and not kwargs.keys() & value.keys():
                    kwargs.update(value)
                else:
                    uncertain = True
            elif item.arg in kwargs:
                uncertain = True
            else:
                kwargs[item.arg] = value
        return args, kwargs, uncertain

    # Literal executable source can introduce/rebind names used outside that
    # string. Merge its possible definitions conservatively; never execute it.
    expanded, source_bytes = set(), len(source.encode())
    for expansion in range(9):
        added = []
        for node in nodes:
            if not isinstance(node, ast.Call) or id(node) in expanded:
                continue
            function = resolve(node.func)
            if not isinstance(function, Symbol) or function.path not in {"builtins.exec", "builtins.eval"}:
                continue
            args, kw, uncertain = arguments(node)
            code = args[0] if args else kw.get("source", UNKNOWN)
            if not isinstance(code, str) or uncertain:
                continue
            expanded.add(id(node))
            source_bytes += len(code.encode())
            if expansion == 8 or source_bytes > 4 * LIMIT:
                raise ContractError("Python executable-source observation bound exceeded")
            try:
                added.extend(ast.walk(ast.parse(code)))
            except (SyntaxError, ValueError, RecursionError):
                continue
            # A supplied namespace may acquire any name from the source. It
            # cannot retain authority as the empty/safe original dictionary.
            for namespace in list(node.args[1:]) + [k.value for k in node.keywords if k.arg in {"globals", "locals"}]:
                if isinstance(namespace, ast.Name):
                    bind(namespace, Opaque(frozenset({"<dynamic-import>"})))
        if not added:
            break
        nodes.extend(added)
        if len(nodes) > 50000:
            raise ContractError("Python AST exceeds observation bound")
        gather(added)
        propagate()

    # Unknown functions may mutate the objects passed to them. Known read-only
    # helpers/process wrappers do not turn ordinary diagnostics into writes.
    pure = {"builtins." + n for n in ("print", "repr", "str", "bytes", "len", "bool", "int", "float", "tuple",
            "list", "dict", "set", "sorted", "enumerate", "zip", "isinstance", "issubclass", "callable", "getattr", "vars", "globals", "locals")}
    pure |= {"json.dumps", "json.loads", "shlex.join", "shlex.quote", "shlex.split", "importlib.import_module", "builtins.__import__"}
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        function = resolve(node.func)
        path = function.path if isinstance(function, Symbol) else ""
        module, _, member = path.rpartition(".")
        if (path in pure or module == "subprocess" and member in PROCESSES or
                path.split(".")[0] in BRIDGES and member in kinds):
            continue
        affected = list(node.args) + [k.value for k in node.keywords]
        if isinstance(node.func, ast.Attribute):
            affected.append(node.func.value)
        for expression in affected:
            if isinstance(expression, ast.Starred):
                expression = expression.value
            base = expression
            while isinstance(base, (ast.Subscript, ast.Attribute)):
                base = base.value
            if isinstance(base, ast.Name) and base.id in definitions:
                value = resolve(expression)
                if value is UNKNOWN or isinstance(value, (list, dict, Opaque, Namespace)):
                    invalid.add(base.id)
    propagate()

    calls = []
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and isinstance(resolve(target.value), Namespace):
                    calls.append(unresolved("Python namespace mutation changes binding provenance"))
        if not isinstance(node, ast.Call):
            continue
        function = resolve(node.func)
        # Namespace methods can replace a name whose original binding was
        # harmless. Preserve this fact through aliases and indirect method
        # calls; otherwise a stale `s = print` can hide globals().update(s=...).
        if any(origin.startswith(NAMESPACE_ORIGIN + ".") and
               origin[len(NAMESPACE_ORIGIN) + 1:] not in NAMESPACE_READS
               for origin in origins(function)):
            calls.append(unresolved("Python namespace mutation changes binding provenance"))
            continue
        if isinstance(function, Opaque):
            if any(sensitive(path) for path in function.origins):
                calls.append(unresolved("unresolved Python sender provenance"))
            continue
        path = function.path if isinstance(function, Symbol) else ""
        module, _, name = path.rpartition(".")
        bridge = path.split(".")[0] in BRIDGES
        args, kw, uncertain = arguments(node)
        if path in {"builtins.exec", "builtins.eval"}:
            code = args[0] if args else kw.get("source", UNKNOWN)
            if not isinstance(code, str) or uncertain or id(node) not in expanded:
                calls.append(unresolved("nonliteral executable Python source"))
        elif module == "subprocess" and name in PROCESSES or module == "os" and (name == "system" or name.startswith(("exec", "spawn"))):
            command = args[0] if args else kw.get("args", UNKNOWN)
            if path.startswith("os.exec"):
                command = args[1] if "v" in name and len(args) > 1 else args[1:]
            elif path.startswith("os.spawn"):
                command = args[2] if "v" in name and len(args) > 2 else args[2:]
            if isinstance(command, list) and all(isinstance(s, str) for s in command):
                command = shlex.join(command)
            calls.extend(delivery_calls(command, depth + 1) if isinstance(command, str) and not uncertain and "executable" not in kw else
                         [unresolved("nonliteral Python process command")])
        elif name in kinds and (bridge or module == "builtins" and name.startswith("submit_")):
            fields = (("task_pack_path",) if name == "submit_completion_callback" else
                      ("surface", "text", "task_pack_path", "marker") if name == "submit_task_pack" else
                      ("surface", "text", "marker"))
            if uncertain or set(fields[:len(args)]) & kw.keys():
                calls.append(unresolved("dynamic Python sender arguments"))
                continue
            kw.update(zip(fields, args))
            reconcile = kw.get("reconcile_only", False)
            calls.append(dict(kind=kinds[name], surface=kw.get("surface"), pack=kw.get("task_pack_path"),
                              text=kw.get("text"), reconcile=reconcile if type(reconcile) is bool else UNKNOWN))
        elif name in {"rpc", "call", "request", "_run", "_run_successor"} and (bridge or module == "builtins"):
            methods = [(i, v) for i, v in enumerate(args) if isinstance(v, str) and v in rpc_methods]
            for index, method in methods:
                params = args[index + 1] if len(args) > index + 1 else UNKNOWN
                if isinstance(params, str):
                    from orchestration_common import strict_json
                    params = strict_json(params)
                calls.append(rpc_call(method, params) if isinstance(params, dict) and not uncertain else unresolved("nonliteral Python RPC input"))
            if bridge and not methods:
                calls.append(unresolved("dynamic Python RPC method"))
        elif path not in {"builtins.getattr", "builtins.vars", "builtins.globals", "builtins.locals", "builtins.print", "builtins.repr", "builtins.str", "builtins.callable"}:
            if any(sensitive(origin) for value in args + list(kw.values()) for origin in origins(value)):
                calls.append(unresolved("Python sender passed to an unknown callable"))
    return calls
