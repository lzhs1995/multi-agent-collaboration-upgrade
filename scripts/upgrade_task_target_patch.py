"""Shape-checked target wiring into a new complete native controller only."""
from upgrade_controller_patch import replace_once


def task_target_paths(name, text):
    if name == "cmux_native_delivery.py":
        text = replace_once(text, 'SCHEMA = "native-delivery-v1"',
            'import upgrade_task_target as task_target\n\nSCHEMA = "native-delivery-v1"')
        text = replace_once(text, '    session = _live_session(p, provider)\n'
            '    path = _transcript_path(provider, session, p)',
            '    selected_task = task_target.selected(p) if provider == "codex" else None\n'
            '    session = selected_task["session_id"] if selected_task is not None else _live_session(p, provider)\n'
            '    path = (Path(selected_task["journal"]["path"]) if selected_task is not None\n'
            '            else _transcript_path(provider, session, p))')
        text = replace_once(text, '    result = dict(schema=SCHEMA, identity=pins, tty=row["tty"], process=_public_process(p),',
            '    result = dict(schema=task_target.SCHEMA if selected_task is not None else SCHEMA,\n'
            '        identity=pins, tty=row["tty"], process=_public_process(p),')
        text = replace_once(text, '    require_bound(bridge, surface, result, text)\n    return result',
            '    if selected_task is not None:\n'
            '        result["task_target"] = selected_task["target"]\n'
            '    require_bound(bridge, surface, result, text)\n    return result')
        text = replace_once(text, '    if not isinstance(b, dict) or b.get("schema") != SCHEMA:',
            '    if not isinstance(b, dict) or b.get("schema") not in (SCHEMA, task_target.SCHEMA):')
        text = replace_once(text, '    if b.get("payload_sha256") != _hash(text.encode()):',
            '    if b["schema"] == task_target.SCHEMA:\n'
            '        task_target.validate_saved(b)\n'
            '    else:\n'
            '        task_target.require_legacy(b)\n'
            '    if b.get("payload_sha256") != _hash(text.encode()):')
        text = replace_once(text,
            '    if _provider(p) != b["provider"] or _live_session(p, b["provider"]) != b["session_id"]:\n'
            '        raise NativeDeliveryError("NATIVE_PROCESS_SESSION_CHANGED")',
            '    if _provider(p) != b["provider"]:\n'
            '        raise NativeDeliveryError("NATIVE_PROCESS_SESSION_CHANGED")\n'
            '    if b["schema"] == task_target.SCHEMA:\n'
            '        task_target.require_live(b, p)\n'
            '    elif _live_session(p, b["provider"]) != b["session_id"]:\n'
            '        raise NativeDeliveryError("NATIVE_PROCESS_SESSION_CHANGED")')
    elif name == "cmux_codex_input.py":
        text = replace_once(text, 'import cmux_native_delivery as native',
            'import cmux_native_delivery as native\nimport upgrade_task_target as task_target')
        text = replace_once(text, '    thread_id = binding["session_id"]\n',
            '    task_target.require_client(client, binding)\n    thread_id = binding["session_id"]\n')
        if "def read_metadata(" in text:
            text = replace_once(text, '    return metadata\n',
                '    task_target.require_client(client, binding)\n    return metadata\n')
        else:
            # Original .34 performs the same two reads directly in prepare.
            text = replace_once(text, '    return dict(transport=TRANSPORT, method=method, params=params,',
                '    task_target.require_client(client, binding)\n'
                '    return dict(transport=TRANSPORT, method=method, params=params,')
        # Newer original controllers also expose a bounded terminal-outcome
        # reader. Keep its same-connection checks without changing older shapes.
        if "def read_terminal_outcomes(" in text:
            text = replace_once(text, "    thread_id = binding['session_id']\n",
                "    task_target.require_client(client, binding)\n    thread_id = binding['session_id']\n")
            text = replace_once(text, "    return dict(source='codex-native-terminal-outcomes-v1'",
                "    task_target.require_client(client, binding)\n    return dict(source='codex-native-terminal-outcomes-v1'")
        text = replace_once(text, '    return hashlib.sha256(_canonical(value)).hexdigest()',
            '    task_target.require_request(value, binding)\n'
            '    return hashlib.sha256(_canonical(value)).hexdigest()')
        text = replace_once(text, '        try:\n            result = client.request(request["method"], request["params"])',
            '        task_target.require_client(client, binding)\n'
            '        try:\n            result = client.request(request["method"], request["params"])')
        if "    intended = False\n" in text:
            text = replace_once(text, '    intended = False\n',
                '    fallback = task_target.native_fallback(binding, fallback)\n    intended = False\n')
        elif 'def send_once(binding, text, *, recheck, emit, observe, wait_seconds=3):' not in text:
            raise ValueError("task target input fallback source shape changed")
        # Wrap the call itself so validation precedes indexing even thread/data
        # fields. Original .34/.58 have three calls; .84 adds two outcome reads.
        if text.count("client.request(") not in (3, 5):
            raise ValueError("task target response request source shape changed")
        text = text.replace("client.request(", "task_target.checked_request(client, binding, ")
    return text
