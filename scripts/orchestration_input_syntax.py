"""Bounded static JavaScript values for native tool-call observation.

No eval, shell, imports or user functions are executed. Only immutable local
constants and a small string-expression grammar can produce a concrete value.
Everything else stays UNKNOWN, including reassignment and object spreads.
"""
from __future__ import annotations

import ast
import copy
import json
import re

UNKNOWN = object()
LIMIT = 1024 * 1024


def tokens(source):
    out, pos = [], 0
    while pos < len(source):
        char = source[pos]
        if char.isspace():
            pos += 1
        elif source.startswith("//", pos) or char == "#":
            end = source.find("\n", pos)
            pos = len(source) if end < 0 else end + 1
        elif source.startswith("/*", pos):
            end = source.find("*/", pos + 2)
            pos = len(source) if end < 0 else end + 2
        elif char in "\"'`":
            start, quote = pos, char
            pos += 1
            while pos < len(source):
                if source[pos] == "\\":
                    pos += 2
                elif source[pos] == quote:
                    pos += 1
                    break
                else:
                    pos += 1
            raw, value = source[start:pos], UNKNOWN
            try:
                if raw.endswith(quote) and len(raw) > 1:
                    if quote == '"':
                        value = json.loads(raw)
                    elif quote == "'":
                        # Reject escapes with different JS/Python meanings.
                        if not re.search(r"\\(?:[0-9]|N|U|a|e)", raw):
                            value = ast.literal_eval(raw)
                    elif "${" not in raw and "\\" not in raw:
                        value = raw[1:-1]
            except (ValueError, SyntaxError):
                pass
            out.append(("value", value))
        elif char == "/" and (not out or out[-1][1] in ("(", "=", ":", ",", "=>", "[")):
            start = pos = pos + 1
            escaped = False
            while pos < len(source):
                if source[pos] == "/" and not escaped:
                    break
                escaped = source[pos] == "\\" and not escaped
                pos += 1
            pattern = source[start:pos]
            pos += 1
            match = re.match(r"[a-z]*", source[pos:])
            flags = match[0] if match else ""
            pos += len(flags)
            out.append(("value", ("regex", pattern, flags)))
        else:
            match = re.match(r"[A-Za-z_$][\w$]*|[0-9]+(?:\.[0-9]+)?|=>|\.\.\.|\?\.|===|!==|==|!=|\+=|-=|\+\+|--", source[pos:])
            if match:
                raw = match[0]
                pos += len(raw)
                if raw[0].isdigit():
                    out.append(("value", float(raw) if "." in raw else int(raw)))
                else:
                    out.append(("name" if re.fullmatch(r"[A-Za-z_$][\w$]*", raw) else "punct", raw))
            else:
                out.append(("punct", char))
                pos += 1
    return out


def expression_end(items, start, stops):
    stack = []
    for pos in range(start, len(items)):
        kind, value = items[pos]
        if kind != "punct":
            continue
        if not stack and value in stops:
            return pos
        if value in ("(", "[", "{"):
            stack.append(value)
        elif value in (")", "]", "}"):
            if not stack or stack.pop() != {")": "(", "]": "[", "}": "{"}[value]:
                return pos
    return len(items)


class StaticJS:
    def __init__(self, source, *, stored=None):
        self.items = tokens(source)
        self.stored = stored or {}
        self.bindings, self.declarations, self.invalid = {}, {}, set()
        self.raw_bindings = {}
        self.store_sites = []
        self.cache_mutations = set()
        for pos, item in enumerate(self.items):
            if item[0] != "name" or pos + 1 >= len(self.items):
                continue
            next_item = self.items[pos + 1]
            if next_item in [("punct", op) for op in ("=", "+=", "-=", "++", "--")]:
                end = expression_end(self.items, pos + 2, {";", ","})
                self.raw_bindings.setdefault(item[1], []).append(self.items[pos + 2:end])
                if pos and self.items[pos - 1] == ("name", "const") and next_item == ("punct", "="):
                    if item[1] in self.bindings:
                        self.invalid.add(item[1])
                    end = expression_end(self.items, pos + 2, {";", ","})
                    self.bindings[item[1]] = self.items[pos + 2:end]
                    self.declarations[item[1]] = pos
                else:
                    self.invalid.add(item[1])
            end = self._member_end(pos)
            if end > pos + 1 and end < len(self.items) and self.items[end] in [
                    ("punct", op) for op in ("=", "+=", "-=", "++", "--")]:
                self.invalid.add(item[1])
        self.tainted = {"tools"}
        # Track provenance even through mutable aliases: unknown is a refusal,
        # never evidence that an indirect tool invocation did not happen.
        changed = True
        while changed:
            before = set(self.tainted)
            for name, expressions in self.raw_bindings.items():
                if any(kind == "name" and token in self.tainted
                       for expr in expressions for kind, token in expr):
                    self.tainted.add(name)
            changed = before != self.tainted
        for pos, item in enumerate(self.items):
            if item not in (("name", "const"), ("name", "let"), ("name", "var")):
                continue
            if pos + 1 < len(self.items) and self.items[pos + 1] in (("punct", "{"), ("punct", "[")):
                end = expression_end(self.items, pos + 1, {"="})
                rhs_end = expression_end(self.items, end + 1, {";"})
                if any(k == "name" and v in self.tainted for k, v in self.items[end + 1:rhs_end]):
                    for k, v in self.items[pos + 1:end]:
                        if k == "name":
                            self.tainted.add(v)
                            self.invalid.add(v)
        # A reference to the store/load capability outside a direct builtin
        # call may alias or shadow it. Never export a value from such a cell.
        self.opaque_store = False
        for pos, (kind, name) in enumerate(self.items):
            if kind != "name" or name not in {"store", "load"}:
                continue
            if pos and self.items[pos - 1] in (("punct", "."), ("punct", "?.")):
                if pos >= 2 and self.items[pos - 2] == ("name", "globalThis"):
                    self.opaque_store = True
                continue
            after = self.items[pos + 1:pos + 2]
            if after == [("punct", ":")]:
                continue  # an object key is not a reference to the builtin
            if (name in self.raw_bindings or after != [("punct", "(")] or
                    pos and self.items[pos - 1] == ("name", "function")):
                self.opaque_store = True
        for pos, end, close in self._sites():
            if self.items[pos:end] == [("name", "store")]:
                split = expression_end(self.items, end + 1, {","})
                self.store_sites.append((pos, self.items[end + 1:split], self.items[split + 1:close],
                                         self._top_statement(pos)))
        # Object aliases share mutation risk. Unknown callees may mutate an
        # object argument; primitive strings/numbers cannot be mutated by them.
        containers = set()
        for name, expressions in self.raw_bindings.items():
            if any(isinstance(self.value(rhs), (dict, list)) or ("name", "load") in rhs for rhs in expressions):
                containers.add(name)
        self.mutated_expressions = []
        changed = True
        while changed:
            before = (set(self.invalid), set(containers))
            for name, expressions in self.raw_bindings.items():
                for rhs in expressions:
                    value = self.value(rhs)
                    if value is not UNKNOWN and not isinstance(value, (dict, list)):
                        continue  # immutable scalar reads are not object aliases
                    aliases = {v for k, v in rhs if k == "name" and v in self.raw_bindings}
                    if name in containers or aliases & containers:
                        containers.update(aliases | {name})
                        if name in self.invalid or aliases & self.invalid:
                            self.invalid.update(aliases | {name})
            for pos, end, close in self._sites():
                path = self.items[pos:end]
                args = self._argument_expressions(end, close)
                # A direct loaded-object assignment/method has no local binding.
                suffix = self._suffix_end(close + 1)
                if suffix > close + 1 and self.items[suffix:suffix + 1] in [
                        [("punct", op)] for op in ("=", "+=", "-=", "++", "--", "(")]:
                    expr = self.items[pos:suffix]
                    if expr not in self.mutated_expressions:
                        self.mutated_expressions.append(expr)
                if len(path) >= 3 and path[1] == ("punct", ".") and path[2][1] in {
                        "push", "pop", "shift", "unshift", "splice", "sort", "reverse",
                        "fill", "copyWithin", "set", "delete", "clear"}:
                    self.invalid.add(path[0][1])
                known_mutator = len(path) == 3 and path[0][1] in {"Object", "Reflect"} and path[2][1] in {
                    "assign", "defineProperty", "defineProperties", "set", "deleteProperty", "setPrototypeOf"}
                if known_mutator or not self._pure_site(pos, end):
                    affected = args[:1] if known_mutator else args
                    for expr in affected:
                        self.invalid.update(v for k, v in expr if k == "name" and v in containers)
                        if expr not in self.mutated_expressions:
                            self.mutated_expressions.append(expr)
            changed = before != (self.invalid, containers)
        self.effects()

    def _member_end(self, pos):
        return self._suffix_end(pos + 1)

    def _suffix_end(self, end):
        while end < len(self.items):
            if self.items[end] in (("punct", "."), ("punct", "?.")) and end + 1 < len(self.items) and self.items[end + 1][0] == "name":
                end += 2
            elif self.items[end] == ("punct", "["):
                end = expression_end(self.items, end + 1, {"]"}) + 1
            else:
                break
        return end

    def _sites(self):
        for pos, item in enumerate(self.items):
            if item[0] != "name" or pos and self.items[pos - 1] in (("punct", "."), ("punct", "?.")):
                continue
            end = self._member_end(pos)
            while pos and self.items[pos - 1] == ("punct", "(") and self.items[end:end + 1] == [("punct", ")")]:
                pos -= 1
                end += 1
            if self.items[end:end + 2] == [("punct", "?."), ("punct", "(")]:
                end += 1
            if end < len(self.items) and self.items[end] == ("punct", "("):
                yield pos, end, expression_end(self.items, end + 1, {")"})

    def _argument_expressions(self, end, close):
        args, start = [], end + 1
        while start < close:
            stop = expression_end(self.items, start, {",", ")"})
            args.append(self.items[start:min(stop, close)])
            start = stop + 1
        return args

    def _function(self, pos, end, context=None):
        expr = self.items[pos:end]
        if expr[-1:] == [("punct", "?.")]:
            expr = expr[:-1]
        return self.value(expr, locals_=context, at=pos)

    def _pure_site(self, pos, end):
        path = self.items[pos:end]
        function = self._function(pos, end)
        if isinstance(function, tuple) and function[0] in {"tool", "indirect-tool", "arrow", "replace", "stringify", "load"}:
            return True
        if len(path) == 1 and path[0][1] in {"store", "text", "image", "audio", "generatedImage", "notify"}:
            return path[0][1] not in self.raw_bindings
        return (path[:2] == [("name", "Promise"), ("punct", ".")] and "Promise" not in self.raw_bindings or
                path[-2:] == [("punct", "."), ("name", "map")])

    def effects(self):
        """Possible cache reads/writes; None is a wildcard tombstone.

        Mutating a loaded object affects its original key even through local
        aliases, and even if the outer cell later fails. Unknown builtin
        provenance disables all cache authority in this cell.
        """
        if self.opaque_store:
            self.cache_mutations = None
            return dict(reads=None, writes=None)
        reads, writes = set(), set()
        for pos, end, close in self._sites():
            path = self.items[pos:end]
            if path not in ([('name', 'load')], [('name', 'store')]):
                continue
            args = self._argument_expressions(end, close)
            key = self.value(args[0], at=pos) if args else UNKNOWN
            target = reads if path[0][1] == 'load' else writes
            if not isinstance(key, str):
                self.cache_mutations = None
                return dict(reads=None, writes=None)
            target.add(key)

        def loaded(expr, seen=frozenset()):
            found = set()
            if len(seen) > 16:
                return None
            for i, token in enumerate(expr):
                if token == ('name', 'load') and expr[i + 1:i + 2] == [('punct', '(')]:
                    close = expression_end(expr, i + 2, {')'})
                    key = self.value(expr[i + 2:close])
                    if not isinstance(key, str):
                        return None
                    found.add(key)
                elif token[0] == 'name' and token[1] in self.raw_bindings and token[1] not in seen:
                    for rhs in self.raw_bindings[token[1]]:
                        nested = loaded(rhs, seen | {token[1]})
                        if nested is None:
                            return None
                        found.update(nested)
            return found

        expressions = list(self.mutated_expressions)
        expressions.extend(rhs for n in self.invalid for rhs in self.raw_bindings.get(n, []))
        mutations = set()
        for expr in expressions:
            keys = loaded(expr)
            if keys is None:
                self.cache_mutations = None
                return dict(reads=None, writes=None)
            mutations.update(keys)
        self.cache_mutations = mutations
        writes.update(mutations)
        return dict(reads=reads, writes=writes)

    def _top_statement(self, pos):
        if pos and self.items[pos - 1] != ("punct", ";"):
            return False
        depth = 0
        for kind, value in self.items[:pos]:
            if kind == "punct":
                depth += value in ("(", "[", "{")
                depth -= value in (")", "]", "}")
        return depth == 0 and not any(k == "name" and v in {
            "if", "switch", "for", "while", "do", "return", "throw", "exit",
            "try", "catch", "function"} for k, v in self.items[:pos])

    def _stored_value(self, key, at, seen):
        if (self.opaque_store or not isinstance(key, str) or
                self.cache_mutations is None or key in self.cache_mutations):
            return UNKNOWN
        value = self.stored.get(key, UNKNOWN)
        for pos, key_expr, value_expr, straight in self.store_sites:
            if pos >= at:
                break
            candidate = self.value(key_expr, seen, at=pos)
            if candidate is UNKNOWN:
                value = UNKNOWN
            elif candidate == key:
                value = self.value(value_expr, seen, at=pos) if straight else UNKNOWN
        return value

    def exports(self):
        """Candidates only; a matching successful outer result must commit them."""
        if self.opaque_store or self.cache_mutations is None:
            return None
        values = {}
        for pos, key_expr, value_expr, straight in self.store_sites:
            key = self.value(key_expr, at=pos)
            if not isinstance(key, str):
                return None  # an unknown key can replace any stored value
            value = self.value(value_expr, at=pos) if straight and key not in self.cache_mutations else UNKNOWN
            if serializable(value):
                values[key] = copy.deepcopy(value)
            else:
                values[key] = UNKNOWN
        return values

    def value(self, expression, seen=frozenset(), locals_=None, *, at=None):
        if len(seen) > 16 or len(expression) > 100000:
            return UNKNOWN
        locals_ = locals_ or {}
        at = len(self.items) if at is None else at
        pos = 0

        def take(value):
            nonlocal pos
            if pos < len(expression) and expression[pos] == ("punct", value):
                pos += 1
                return True
            return False

        def arguments(close):
            values = []
            if take(close):
                return values
            while pos < len(expression):
                before = pos
                values.append(addition())
                if take(close):
                    return values
                if pos == before or not take(","):
                    break
                if take(close):
                    return values
            return UNKNOWN

        def primary():
            nonlocal pos
            if pos >= len(expression):
                return UNKNOWN
            kind, name = expression[pos]
            pos += 1
            if kind == "value":
                value = name
            elif kind == "name":
                if name in self.invalid:
                    value = ("tool", UNKNOWN) if name in self.tainted else UNKNOWN
                elif name in locals_:
                    value = locals_[name]
                elif name in {"true", "false", "null"}:
                    value = {"true": True, "false": False, "null": None}[name]
                elif name == "tools" and name not in self.invalid and name not in self.bindings:
                    value = ("tools",)
                elif name == "JSON" and name not in self.invalid and name not in self.bindings:
                    value = ("json",)
                elif name == "load" and name not in self.bindings:
                    value = ("load",)
                elif name in self.bindings and name not in self.invalid and name not in seen:
                    if self.declarations[name] >= at:
                        return UNKNOWN
                    rhs = self.bindings[name]
                    if len(rhs) > 2 and rhs[0][0] == "name" and rhs[1] == ("punct", "=>"):
                        value = ("arrow", rhs[0][1], rhs[2:], seen | {name})
                    else:
                        value = self.value(rhs, seen | {name}, locals_, at=self.declarations[name])
                    if value is UNKNOWN and name in self.tainted:
                        value = ("indirect-tool", UNKNOWN)
                elif name in self.tainted:
                    value = ("tool", UNKNOWN)
                else:
                    value = UNKNOWN
            elif name == "(":
                value = addition()
                if not take(")"):
                    return UNKNOWN
            elif name == "[":
                value = arguments("]")
            elif name == "{":
                value = {}
                if not take("}"):
                    while pos < len(expression):
                        field_kind, field = expression[pos]
                        pos += 1
                        if field_kind not in ("name", "value") or not isinstance(field, str) or field in value:
                            return UNKNOWN
                        if take(":"):
                            start = pos
                            end = expression_end(expression, start, {",", "}"})
                            value[field] = self.value(expression[start:end], seen, locals_, at=at)
                            pos = end
                        else:
                            value[field] = self.value([(field_kind, field)], seen, locals_, at=at)
                        if take("}"):
                            break
                        if not take(","):
                            return UNKNOWN
                        if take("}"):
                            break
            else:
                return UNKNOWN
            while pos < len(expression):
                field = None
                if take(".") or take("?."):
                    if pos >= len(expression) or expression[pos][0] != "name":
                        return UNKNOWN
                    field = expression[pos][1]
                    pos += 1
                elif take("["):
                    field = addition()
                    if not take("]"):
                        return UNKNOWN
                elif take("("):
                    args = arguments(")")
                    value = apply(value, args)
                    continue
                else:
                    break
                if value == ("tools",):
                    value = ("tool", field) if isinstance(field, str) else ("tool", UNKNOWN)
                elif isinstance(value, tuple) and value[0] in ("tool", "indirect-tool"):
                    value = ("indirect-tool", value[1])
                elif value == ("json",) and field == "stringify":
                    value = ("stringify",)
                elif isinstance(value, str) and field in ("replace", "replaceAll"):
                    value = ("replace", value, field)
                elif isinstance(value, dict) and isinstance(field, str):
                    value = value.get(field, UNKNOWN)
                elif isinstance(value, list) and type(field) is int and 0 <= field < len(value):
                    value = value[field]
                else:
                    value = UNKNOWN
            return value

        def apply(function, args):
            if not isinstance(function, tuple) or args is UNKNOWN:
                return UNKNOWN
            if function[0] == "arrow" and len(args) == 1 and args[0] is not UNKNOWN:
                return self.value(function[2], function[3], {**locals_, function[1]: args[0]}, at=at)
            if function == ("load",) and len(args) == 1:
                return self._stored_value(args[0], at, seen)
            if function[0] == "replace" and len(args) == 2 and isinstance(args[1], str):
                old, replacement = args
                if "$" in replacement:  # JS replacement expansion is not Python replace.
                    return UNKNOWN
                all_ = function[2] == "replaceAll"
                if isinstance(old, tuple) and old == ("regex", "'", "g"):
                    old, all_ = "'", True
                if not isinstance(old, str) or not old:
                    return UNKNOWN
                result = function[1].replace(old, replacement, -1 if all_ else 1)
                return result if len(result) <= LIMIT else UNKNOWN
            if function == ("stringify",) and len(args) == 1:
                try:
                    return json.dumps(args[0], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                except (TypeError, ValueError):
                    return UNKNOWN
            return UNKNOWN

        def addition():
            value = primary()
            while take("+"):
                other = primary()
                value = value + other if isinstance(value, str) and isinstance(other, str) else UNKNOWN
                if isinstance(value, str) and len(value) > LIMIT:
                    return UNKNOWN
            return value

        try:
            value = addition()
            return value if pos == len(expression) else UNKNOWN
        except (TypeError, ValueError, RecursionError):
            return UNKNOWN

    def _locals_at(self, position):
        contexts = [{}]
        items = self.items
        for start, end, close in self._sites():
            if not (end < position < close) or items[end - 2:end] != [
                    ("punct", "."), ("name", "map")]:
                continue
            arrow = end + 1
            if items[arrow] == ("name", "async"):
                arrow += 1
            if items[arrow] != ("punct", "("):
                continue
            param_end = expression_end(items, arrow + 1, {")"})
            if items[param_end + 1:param_end + 2] != [("punct", "=>")]:
                continue
            if position <= param_end:
                continue
            params = items[arrow + 1:param_end]
            destructure = params[:1] == [("punct", "[")] and params[-1:] == [("punct", "]")]
            if destructure:
                params = params[1:-1]
            names = [v for k, v in params if k == "name"]
            if not names or any(k != "name" and (k, v) != ("punct", ",") for k, v in params):
                return [{}]
            next_contexts = []
            for context in contexts:
                rows = self.value(items[start:end - 2], locals_=context, at=start)
                if not isinstance(rows, list) or len(rows) > 256:
                    return [{}]
                for index, row in enumerate(rows):
                    values = row if destructure and isinstance(row, list) else [row, index]
                    if destructure and (not isinstance(row, list) or len(values) < len(names)):
                        return [{}]
                    next_contexts.append({**context, **dict(zip(names, values))})
            contexts = next_contexts
            if len(contexts) > 256:
                return [{}]
        return contexts

    def calls(self):
        """Observe literal/immutable map calls; never execute their callbacks."""
        for pos, end, close in self._sites():
            for context in self._locals_at(pos):
                function = self._function(pos, end, context)
                if not isinstance(function, tuple) or function[0] not in ("tool", "indirect-tool"):
                    if not self._pure_site(pos, end):
                        # Passing a sender to Reflect.apply, a callback or an
                        # unknown wrapper loses the original invocation shape.
                        args = [self.value(expr, locals_=context, at=pos)
                                for expr in self._argument_expressions(end, close)]
                        if any(has_tool(value) for value in args):
                            yield UNKNOWN, UNKNOWN
                    continue
                args = self.value(self.items[end + 1:close], locals_=context, at=pos)
                # Tuples are internal symbolic values, never literal JavaScript
                # arguments. Keep unresolved destructuring fail-closed without
                # leaking a tool marker into the native argument schema.
                if isinstance(args, tuple):
                    args = UNKNOWN
                yield function[1], args if function[0] == "tool" else UNKNOWN


def has_tool(value):
    if isinstance(value, tuple):
        return value[0] in {"tools", "tool", "indirect-tool"}
    if isinstance(value, (list, dict)):
        return any(has_tool(v) for v in (value.values() if isinstance(value, dict) else value))
    return False


def serializable(value):
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) <= LIMIT
    except (TypeError, ValueError, RecursionError):
        return False
