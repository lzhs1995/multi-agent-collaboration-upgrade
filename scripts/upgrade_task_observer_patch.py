"""Source-shape checked observer consumers in newly rendered controllers."""
from upgrade_controller_patch import replace_once
from upgrade_task_bridge_patch import _entry, _replace_in_function


def task_observer_paths(name, text):
    if name == 'cmux_bridge.py':
        text = _entry(text, '_run',
            'from upgrade_task_observer_routes import tree_command\n'
            '    observed_tree = tree_command(args)\n'
            '    if observed_tree is not None:return observed_tree')
    elif name == 'cmux_workspace_guard.py':
        text = _entry(text, 'caller_snapshot',
            'from upgrade_task_observer_routes import caller_snapshot as observed_snapshot\n'
            '    observed = observed_snapshot()\n'
            '    if observed is not None:return observed')
    elif name == 'cmux_codex_input.py' and 'def _metadata_client(' in text:
        text = _entry(text, '_metadata_client',
            'from upgrade_task_observer_routes import metadata_client\n'
            '    observed_client = metadata_client()\n'
            '    if observed_client is not None:\n'
            '        yield observed_client\n'
            '        return')
        text = _replace_in_function(text, 'read_metadata', '    return metadata',
            '    from upgrade_task_observer_routes import annotate_metadata\n'
            '    return annotate_metadata(client, metadata)')
        text = replace_once(text, '        request = prepare(client, binding, text)\n',
            '        request = prepare(client, binding, text)\n'
            '        from upgrade_task_observer_routes import require_input_route\n'
            '        require_input_route(client, request)\n')
    return text
