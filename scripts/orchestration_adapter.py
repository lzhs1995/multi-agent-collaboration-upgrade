"""Check a frozen adapter's executable dependency closure without importing it.

This checks the supported flat scripts layout, including deferred imports and
the isolated literal parser. Dynamic controller imports remain in the separately
pinned controller; they do not turn controller modules into adapter dependencies.
It is a completeness check, not a general Python side-effect sandbox.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from orchestration_common import ContractError, contract_errors, read_ref, verify_tree


MAINTENANCE_MODULES = frozenset({
    "orchestration_adapter", "orchestration_maintenance", "orchestration_campaign",
    "orchestration_common", "orchestration_coordinator", "orchestration_evidence",
    "orchestration_freeze", "orchestration_native", "orchestration_preparation",
    "orchestration_transport", "orchestration_disarm", "orchestration_verify",
    "orchestration_planner", "orchestration_workload", "orchestration_inputs",
    "orchestration_input_history", "orchestration_input_python",
    "orchestration_input_syntax", "orchestration_refusal", "decision_provider", "cmux_submission_inputs",
})
CAMPAIGN_MODULES = MAINTENANCE_MODULES | {"orchestration_run", "orchestration_checker"}
# These two functions verify and load the separate immutable controller at run
# time. No other nonliteral import_module call is admitted in the adapter.
CONTROLLER_IMPORTS = {
    ("orchestration_campaign", ("controller_modules",)),
    ("orchestration_transport", ("PinnedRuntime", "modules")),
}
IMPORT_FUNCTIONS = {"import_module", "__import__", "spec_from_file_location"}


def _walk(node, scope=()):
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        scope += (node.name,)
    yield node, scope
    for child in ast.iter_child_nodes(node):
        yield from _walk(child, scope)


def _sibling(call):
    """Resolve the supported Path(__file__).with_name('sibling.py') loader."""
    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "with_name" and len(call.args) == 1 and not call.keywords
            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
        base = call.func.value
        name = call.args[0].value
        if (isinstance(base, ast.Call) and isinstance(base.func, ast.Name) and base.func.id == "Path"
                and len(base.args) == 1 and isinstance(base.args[0], ast.Name)
                and base.args[0].id == "__file__" and not base.keywords
                and Path(name).name == name and name.endswith(".py") and name[:-3].isidentifier()):
            return name[:-3]
    raise ContractError("unresolved adapter file import")


def _loader_aliases(tree):
    """Resolve import and single-name assignment aliases, including deferred code.

    Tuple/default/attribute aliases, cross-module reexports and arbitrary code
    loaders are outside this trusted-source check; it is not an execution sandbox.
    """
    aliases = {name: name for name in IMPORT_FUNCTIONS}
    nodes = list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in IMPORT_FUNCTIONS:
                    if alias.asname in aliases and aliases[alias.asname] != alias.name:
                        raise ContractError("ambiguous dynamic adapter import alias")
                    aliases[alias.asname or alias.name] = alias.name
    while True:
        before = dict(aliases)
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                name = value.attr if isinstance(value, ast.Attribute) else (
                    aliases.get(value.id) if isinstance(value, ast.Name) else None)
                if name in IMPORT_FUNCTIONS:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            if target.id in aliases and aliases[target.id] != name:
                                raise ContractError("ambiguous dynamic adapter import alias")
                            aliases[target.id] = name
        if aliases == before:
            return aliases


@contract_errors("adapter dependencies")
def validate_adapter(manifest, *, purpose="maintenance"):
    """Verify complete bytes and every required/transitive source, without exec."""
    if purpose not in {"maintenance", "campaign"}:
        raise ContractError("unknown adapter purpose")
    verify_tree(manifest)
    required = MAINTENANCE_MODULES if purpose == "maintenance" else CAMPAIGN_MODULES
    pending, seen = set(required), set()
    files = manifest["files"]
    for key in list(files) + manifest["directories"]:
        path = Path(key)
        if path.parts[0] == "scripts" and (
                "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}):
            # -B stops new caches, but CPython still executes existing unchecked
            # hash bytecode. A source-only closure must exclude all such inputs.
            raise ContractError("adapter bytecode/cache is unsupported: " + key)

    def dependency(name):
        if not name or name.startswith("."):
            raise ContractError("relative adapter import is unsupported: " + name)
        root = name.split(".")[0]
        local = "scripts/" + root + ".py"
        if any(key.startswith("scripts/" + root + "/") for key in files):
            raise ContractError("adapter local packages are unsupported: " + name)
        if local in files or root not in sys.stdlib_module_names:
            if name != root:
                raise ContractError("adapter requires a flat local module: " + name)
            pending.add(root)

    while pending - seen:
        name = min(pending - seen)
        key = "scripts/" + name + ".py"
        if key not in files:
            raise ContractError("complete adapter dependency missing: " + key)
        entry = files[key]
        source = read_ref(dict(path=str(Path(manifest["path"]) / key),
                               bytes=entry["bytes"], sha256=entry["sha256"]), "adapter dependency")
        tree = ast.parse(source, filename=key)
        aliases = _loader_aliases(tree)
        seen.add(name)
        for item, scope in _walk(tree):
            if isinstance(item, ast.Import):
                for alias in item.names:
                    dependency(alias.name)
            elif isinstance(item, ast.ImportFrom):
                if item.level or not item.module:
                    raise ContractError("relative adapter import is unsupported: " + key)
                dependency(item.module)
            elif isinstance(item, ast.Call):
                function = item.func.attr if isinstance(item.func, ast.Attribute) else (
                    aliases.get(item.func.id) if isinstance(item.func, ast.Name) else None)
                if function == "spec_from_file_location":
                    if len(item.args) != 2 or item.keywords:
                        raise ContractError("unresolved adapter file import: " + key)
                    pending.add(_sibling(item.args[1]))
                elif function in {"import_module", "__import__"}:
                    if item.args and isinstance(item.args[0], ast.Constant) and isinstance(item.args[0].value, str):
                        dependency(item.args[0].value)
                    elif (name, scope) not in CONTROLLER_IMPORTS:
                        raise ContractError("unresolved dynamic adapter import: " + key)
    # Catch additions, removals or mode changes during source traversal as well.
    verify_tree(manifest)
    return sorted("scripts/" + name + ".py" for name in seen)
