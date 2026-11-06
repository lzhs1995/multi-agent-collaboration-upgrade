"""Explicit actor adapter entries in newly generated controllers only."""
import ast

from orchestration_common import require
from upgrade_controller_patch import edit_nodes,replace_once


def _entry(text,name,code):
    functions=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name==name]
    require(len(functions)==1,"TASK_BRIDGE_SOURCE_ENTRY_CHANGED:"+name)
    node=functions[0];body=node.body[1:] if ast.get_docstring(node) else node.body
    first=body[0]
    return edit_nodes(text,[(first,code+"\n    "+ast.get_source_segment(text,first))])


def _replace_in_function(text,name,old,new):
    functions=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name==name]
    require(len(functions)==1,"TASK_BRIDGE_SOURCE_ENTRY_CHANGED:"+name)
    node=functions[0]
    return edit_nodes(text,[(node,replace_once(ast.get_source_segment(text,node),old,new))])


def task_bridge_paths(name,text):
    if name=="cmux_bridge.py":
        text=_entry(text,"whoami","from upgrade_task_bridge_context import whoami as task_whoami\n"
            "    task_identity = task_whoami()\n    if task_identity is not None:return task_identity")
        text=_entry(text,"list_surfaces","from upgrade_task_bridge_context import list_surfaces as task_surfaces\n"
            "    task_rows = task_surfaces(workspace)\n    if task_rows is not None:return task_rows")
    elif name=="cmux_workspace_guard.py":
        text=replace_once(text,
            'def require_same_workspace(target, *, expected=None):\n'
            '    identity, tree, env, _proof = caller_snapshot()\n'
            '    binding = resolve_snapshot(identity, tree, target, env=env, expected=expected)',
            'def require_same_workspace(target, *, expected=None):\n'
            '    from upgrade_task_bridge_context import resolve as task_resolve\n'
            '    binding = task_resolve(target, expected=expected, resolver=resolve_snapshot)\n'
            '    if binding is None:\n'
            '        identity, tree, env, _proof = caller_snapshot()\n'
            '        binding = resolve_snapshot(identity, tree, target, env=env, expected=expected)')
    elif name=="cmux_native_delivery.py":
        text=_replace_in_function(text,'bind_target','    require_bound(bridge, surface, result, text)\n    return result',
            '    from upgrade_task_sender import bind as bind_task_sender\n'
            '    bind_task_sender(result, live)\n'
            '    require_bound(bridge, surface, result, text)\n    return result')
        text=_replace_in_function(text,'_validate_binding','    pin = b["transcript"]\n',
            '    from upgrade_task_sender import validate as validate_task_sender\n'
            '    validate_task_sender(b)\n'
            '    pin = b["transcript"]\n')
        text=_replace_in_function(text,'require_bound','    _validate_binding(b, text)\n',
            '    _validate_binding(b, text)\n'
            '    from upgrade_task_sender import check as check_task_sender\n'
            '    check_task_sender(b, read_only=read_only)\n')
    elif name=="cmux_hook_scope.py":
        text=_entry(text,"bind_participants","from upgrade_task_bridge_context import participants as task_participants\n"
            "    task_rows = task_participants(workspace, rows)\n    if task_rows is not None:return task_rows")
    elif name=="mac_harness.py":
        participants='    gate["native_participants"] = native_participants' in text
        text=replace_once(text,'def _workspace_key():',
            '_UPGRADE_TASK_BRIDGE_PARTICIPANTS = '+repr(participants)+'\n\ndef _workspace_key():')
        # Older supported controllers can still render/run their legacy path.
        # They cannot supply the participant contract required by a TASK actor.
        if not participants:return text
        text=_entry(text,"_workspace_key","from upgrade_task_bridge_context import workspace as task_workspace\n"
            "    task_ws = task_workspace()\n    if task_ws is not None:return task_ws")
        text=replace_once(text,'    gate["native_participants"] = native_participants',
            '    gate["native_participants"] = native_participants\n'
            '    if "task_actor" in me:\n'
            '        gate["task_actor"] = me["task_actor"]\n'
            '        gate["identity_source"] = me["identity_source"]')
        text=replace_once(text,'    from executor_reask_lifecycle import task_boundary_locked\n    try:',
            '    from upgrade_task_bridge_context import provenance as task_provenance\n'
            '    task_actor = task_provenance()\n'
            '    if task_actor is not None:\n'
            '        marker["task_actor"] = task_actor\n'
            '        marker["identity_source"] = "original_task_rpc_and_live_tree"\n'
            '    from executor_reask_lifecycle import task_boundary_locked\n    try:')
    return text
