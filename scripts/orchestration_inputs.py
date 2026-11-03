"""Read actual sender invocations and exact message references without execution.

Report literals and tool results are data, not sender invocations. The literal
parser is loaded under an adapter-only name so it cannot shadow an immutable
controller module when PinnedRuntime imports its own transport.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import shlex
import uuid
from pathlib import Path

from orchestration_common import ContractError, contract_errors, digest, read_json, read_ref, read_regular, strict_json
from orchestration_input_syntax import StaticJS, UNKNOWN

_spec = importlib.util.spec_from_file_location(
    "orchestration_literal_inputs", Path(__file__).with_name("cmux_submission_inputs.py"))
_literal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_literal)
_RPC = {"terminal.paste", "surface.send_text", "turn/steer", "turn/start"}
_SUFFIX = ("Verify and read the exact UTF-8 body; follow its reply contract within existing authority. "
           "Native receipt proves this notice only; body reading and acceptance are separate.")


def require(condition, reason):
    if not condition:
        raise ContractError(reason)


@contract_errors("message reference")
def reference_body(text):
    """Follow only a complete exact wire, never a reference quoted in a report."""
    prefix = "STATUS: MESSAGE_REFERENCE_V2 "
    if text.startswith(prefix):
        require(len(text.encode()) <= 700 and not any(c in text for c in "\r\n\t"), "invalid body-reference wire")
        match = re.match(re.escape(prefix) + r"([A-Za-z0-9_.:-]{1,160}) BODY=", text)
        require(match is not None, "invalid body-reference marker")
        path, end = json.JSONDecoder().raw_decode(text[match.end():])
        rest = text[match.end() + end:]
        fields = re.fullmatch(r" SHA256=([0-9a-f]{64}) BYTES=([0-9]{1,7}) " + re.escape(_SUFFIX), rest)
        require(isinstance(path, str) and fields is not None, "invalid body-reference fields")
        sha, size = fields[1], int(fields[2])
        require(text == f'{prefix}{match[1]} BODY={json.dumps(path, ensure_ascii=False)} SHA256={sha} BYTES={size} {_SUFFIX}',
                "body-reference wire changed")
    elif text.startswith("STATUS: MESSAGE_REFERENCE_V1 "):
        suffix = ("Verify bytes before reading; handle only within existing authority. "
                  "Follow the body's reply contract. Native reception proves this notice only; "
                  "body reading and acceptance need separate evidence.")
        match = re.fullmatch(r"STATUS: MESSAGE_REFERENCE_V1 ([A-Za-z0-9_.:-]{1,160})\n"
                             r"Read UTF-8 body: ([^\n]+)\nSHA256=([0-9a-f]{64}) BYTES=([0-9]{1,7})\n" +
                             re.escape(suffix), text)
        require(match is not None and len(text.encode()) <= 700, "invalid historical body-reference wire")
        path, sha, size = match[2], match[3], int(match[4])
    else:
        return None
    require(0 < size <= 1024 * 1024 and Path(path).name == sha + ".txt", "invalid body-reference path/size")
    return read_ref(dict(path=path, sha256=sha, bytes=size), "original message body").decode("utf-8")


def input_texts(text):
    """Bounded exact reference expansion. No body is evaluated as code."""
    require(isinstance(text, str), "nonliteral peer payload")
    values = []
    for _ in range(5):
        require(text not in values, "cyclic body reference")
        values.append(text)
        body = reference_body(text)
        if body is None:
            return values
        text = body
    raise ContractError("body reference nesting exceeds observation bound")


def _options(args):
    values, positional = {}, []
    index = 0
    while index < len(args):
        item = args[index]
        if item.startswith("--"):
            key, sep, value = item.partition("=")
            require(key not in values, "duplicate sender option")
            if not sep and index + 1 < len(args) and not args[index + 1].startswith("--"):
                index += 1
                value = args[index]
            values[key] = value
        else:
            positional.append(item)
        index += 1
    return values, positional


@contract_errors("native RPC input")
def _rpc_call(method, params):
    require(isinstance(params, dict), "unresolved native sender parameters")
    selectors = [params[k] for k in ("surface", "surface_id", "surface_uuid", "target", "target_surface_uuid",
                                     "pane_id", "pane_uuid", "threadId", "session_id") if k in params]
    texts = [params[k] for k in ("text", "payload") if k in params]
    blocks = params.get("input", [])
    require(isinstance(blocks, list), "native input must be an array")
    for block in blocks:
        require(isinstance(block, dict) and block.get("type") in ("text", "input_text"), "unknown native input block")
        texts.append(block.get("text"))
    return dict(kind="rpc", selectors=selectors, texts=texts, method=method)


def unresolved(reason):
    return dict(kind="unresolved", reason=reason)


def _python_calls(source, depth=0):
    from orchestration_input_python import python_calls
    return python_calls(source, delivery_calls=delivery_calls, rpc_call=_rpc_call,
                        kinds=_literal._KINDS, rpc_methods=_RPC, depth=depth)


def _sender_hint(source):
    return bool(re.search(r"cmux[-_]|successor_rebind|submit_(?:text|task_pack|completion_callback)|terminal\.paste|surface\.send_text|turn/(?:steer|start)", source))


def _unwrap(args):
    """Peel documented command wrappers without executing shell expansion."""
    args = list(args)
    for _ in range(12):
        if not args:
            return []
        exe = Path(args[0]).name
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", args[0]):
            args.pop(0)
        elif exe in ("rtk", "proxy", "nohup", "exec", "command"):
            args.pop(0)
            while args and args[0] in ("--", "-p", "-a"):
                flag = args.pop(0)
                if flag == "-a" and args:
                    args.pop(0)
        elif exe == "env":
            args.pop(0)
            while args:
                if args[0] in ("-u", "--unset", "-C", "--chdir"):
                    args = args[2:]
                elif args[0].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", args[0]):
                    args.pop(0)
                else:
                    break
        elif exe in ("timeout", "gtimeout"):
            args.pop(0)
            while args and args[0].startswith("-"):
                flag = args.pop(0)
                if flag in ("-s", "--signal", "-k", "--kill-after"):
                    args = args[1:]
            args = args[1:]
        elif exe in ("nice", "stdbuf", "gstdbuf"):
            args.pop(0)
            while args and args[0].startswith("-"):
                flag = args.pop(0)
                if flag in ("-n", "--adjustment", "-i", "-o", "-e"):
                    args = args[1:]
        else:
            return args
    return None


def _python_entry(args):
    """Keep interpreter options separate from script arguments, without exec."""
    options, index = [], 1
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return options + [arg], "script", args[index + 1:]
        # CPython accepts -cSOURCE/-mMODULE and short-option clusters such as
        # -BIc SOURCE. W/X consume the rest as option data, even if it contains
        # a c/m or sender-looking text. Retain the original argv in the frame.
        short = re.fullmatch(r"-([bBdEhIiIOPqRsSuvVx?]*)([cmWX])(.*)", arg, re.S)
        if short and short[2] in ("c", "m"):
            prefix, kind, attached = short.groups()
            rest = ([attached] if attached else []) + args[index + 1:]
            return options + (["-" + prefix] if prefix else []), "inline" if kind == "c" else "module", rest
        if (short and short[2] in ("W", "X") and not short[3]) or arg == "--check-hash-based-pycs":
            if index + 1 == len(args):
                return options + [arg], "unknown", []
            options.extend(args[index:index + 2])
            index += 2
            continue
        if arg.startswith("-") and arg != "-":
            options.append(arg)
            index += 1
            continue
        return options, "script", args[index:]
    return options, "unknown", []


def _shell_frame(command, segment, argv, args, depth, dynamic):
    """Record the exact spelling as well as the parser's unwrapped view."""
    frame = dict(command=command, segment=segment, argv=list(argv),
                 wrappers=argv[:len(argv) - len(args)], executable=args[0] if args else None,
                 interpreter=None, interpreter_options=[], entrypoint_kind="executable",
                 entrypoint=args[0] if args else None, entrypoint_argv=args[1:],
                 depth=depth, literal=not dynamic)
    if args and Path(args[0]).name.startswith("python"):
        options, kind, rest = _python_entry(args)
        frame.update(interpreter=args[0], interpreter_options=options, entrypoint_kind=kind,
                     entrypoint=rest[0] if rest else None, entrypoint_argv=rest[1:])
    return frame


def _framed(calls, frame):
    return [dict(call, shell=[frame] + call.get("shell", [])) for call in calls]


def delivery_calls(command, depth=0):
    require(depth <= 8, "sender wrapper nesting exceeds observation bound")
    require(isinstance(command, str), "nonliteral shell command")
    heredoc = re.search(r"(^|\n)([^\n]*?)<<\s*(?P<quote>['\"]?)(?P<delimiter>\w+)(?P=quote)[^\n]*\n"
                       r"(?P<body>.*?)\n(?P=delimiter)(?:\n|$)", command, re.S)
    if heredoc:
        try:
            argv = shlex.split(heredoc[2])
            header = _unwrap(argv)
        except ValueError:
            return [unresolved("malformed heredoc command")]
        calls = _python_calls(heredoc["body"], depth) if header and Path(header[0]).name.startswith("python") else []
        if calls and not heredoc["quote"] and any(c in heredoc["body"] for c in "$`"):
            calls = [dict(kind="unresolved", reason="expanded Python sender")]
        frame = _shell_frame(command, heredoc[2], argv, header or [], depth, not bool(heredoc["quote"]))
        frame["heredoc"] = True
        return (delivery_calls(command[:heredoc.start()], depth + 1) + _framed(calls, frame) +
                delivery_calls(command[heredoc.end():], depth + 1))
    segments, dynamic = _literal._literal_shell_segments(command)
    calls = []
    for segment in segments:
        try:
            argv = shlex.split(segment, comments=False)
            args = _unwrap(argv)
        except ValueError:
            return [unresolved("malformed shell sender")] if _sender_hint(command) else []
        if args is None:
            return [unresolved("shell wrapper bound exceeded")]
        if not args:
            continue
        frame = _shell_frame(command, segment, argv, args, depth, dynamic)
        segment_calls = []
        exe = Path(args[0]).name
        if exe in ("sh", "bash", "zsh", "dash"):
            options = [i for i, arg in enumerate(args[1:], 1) if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg)]
            if options:
                index = options[0] + 1
                segment_calls = (delivery_calls(args[index], depth + 1) if index < len(args)
                                 else [unresolved("shell command missing")])
                calls.extend(_framed(segment_calls, frame))
            continue
        if exe.startswith("python"):
            options, kind, rest = _python_entry(args)
            if kind == "inline":
                segment_calls = (_python_calls(rest[0], depth + 1) if rest else [unresolved("Python source missing")])
                calls.extend(_framed(segment_calls, frame))
                continue
            args = list(rest)
            if not args:
                continue
            if kind == "module":
                args[0] = args[0].replace(".", "/").rsplit("/", 1)[-1]
            exe = Path(args[0]).name
        if exe in ("cmux-agent", "cmux_agent_adapter.py"):
            segment_calls.extend(_literal._helper_calls(args))
        elif exe in ("successor_rebind_cli.py", "successor_rebind_cli") and len(args) > 1 and args[1] == "submit":
            values, positional = _options(args[2:])
            require(not positional, "nonliteral successor arguments")
            segment_calls.append(dict(kind="successor", artifact=values.get("--artifact"), text_file=values.get("--text-file"),
                              marker=values.get("--marker")))
        elif exe in ("cmux-bridge-toolchain", "cmux_bridge.py", "cmux_bridge", "cmux-bridge", "cmux") and len(args) > 1:
            if args[1] == "rpc" and len(args) > 3 and args[2] in _RPC:
                segment_calls.append(_rpc_call(args[2], strict_json(args[3])))
            elif args[1].replace("-", "_") in _literal._KINDS:
                values, positional = _options(args[2:])
                segment_calls.append(dict(kind=_literal._KINDS[args[1].replace("-", "_")],
                    surface=_literal.target(values.get("--surface") or values.get("--target") or values.get("--target-surface")),
                    pack=values.get("--task-pack"), text=values.get("--text", " ".join(positional) or None),
                    text_file=values.get("--text-file"), reconcile=values.get("--reconcile-only") == ""))
        elif exe in ("xargs", "eval", "source", "."):
            segment_calls.append(unresolved("opaque shell execution"))
        elif dynamic and _sender_hint(segment) and exe not in ("printf", "echo", "cat", "rg", "grep", "sed"):
            segment_calls.append(unresolved("expanded sender command"))
        calls.extend(_framed(segment_calls, frame))
    return [dict(kind="unresolved", reason="nonliteral shell sender")] if calls and dynamic else calls


@contract_errors("actual native invocation")
def invocation_calls(name, arguments, *, static=None):
    require(isinstance(name, str), "native tool name missing")
    original_name, original_arguments = name, arguments
    rpc = next((method for method in _RPC if name == method or name.endswith("." + method) or name.endswith("__" + method)), None)
    if rpc:
        return [_rpc_call(rpc, strict_json(arguments) if isinstance(arguments, str) else arguments)]
    name = name.rsplit(".", 1)[-1]
    if name == "exec":
        require(isinstance(arguments, str), "native exec source missing")
        calls = []
        for tool, args in (static or StaticJS(arguments)).calls():
            if tool is UNKNOWN:
                calls.append(unresolved("dynamic native tool invocation"))
            elif args is UNKNOWN:
                if tool in ("exec_command", "write_stdin", "parallel") or tool in _RPC:
                    calls.append(unresolved("nonliteral native tool arguments"))
            else:
                calls.extend(invocation_calls(tool, args))
        return calls
    # Freeform patches, images and report data have no command-argument schema.
    # Filter by the actual tool name before attempting any JSON decoding.
    if name not in ("exec_command", "Bash", "bash", "run_terminal_cmd", "write_stdin", "parallel"):
        return []
    if isinstance(arguments, str):
        arguments = strict_json(arguments, "native tool arguments")
    require(isinstance(arguments, dict), "native tool arguments must be an object")
    if name in ("exec_command", "Bash", "bash", "run_terminal_cmd"):
        command = arguments.get("cmd", arguments.get("command"))
        calls = delivery_calls(command) if isinstance(command, str) else [unresolved("nonliteral native command")]
        tool = dict(name=original_name, arguments=copy.deepcopy(arguments),
                    original_arguments=copy.deepcopy(original_arguments))
        return [dict(call, native_tool=tool) for call in calls]
    if name == "write_stdin":
        chars = arguments.get("chars", "")
        if chars == "":
            return []
        # Without the original PTY command binding an arbitrary character is
        # not provably harmless (it may be a submit key for a prior draft).
        return [unresolved("nonempty PTY input requires original process binding")]
    if name == "parallel":
        uses = arguments.get("tool_uses")
        require(isinstance(uses, list), "parallel tool uses must be an array")
        calls = []
        for use in uses:
            require(isinstance(use, dict), "parallel tool use must be an object")
            calls.extend(invocation_calls(use["recipient_name"], use["parameters"]))
        return calls
    return []


@contract_errors("original successor evidence")
def _successor(call, sender, state_root):
    """Resolve a successor through its original attempt, not a new artifact target."""
    marker = call["marker"]
    require(isinstance(marker, str) and marker, "unbound successor marker")
    caller = str(uuid.UUID(sender["surface_uuid"])).upper()
    key = digest(json.dumps([caller, marker], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    attempt = read_json(Path(state_root) / "successor-dispatch-v2" / key / "attempt.json")
    artifact = read_json(call["artifact"])
    sha = digest(json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    body = read_regular(call["text_file"]).decode("utf-8")
    binding = attempt["native_binding"]
    identity = binding["identity"]
    require(attempt["schema"] == "successor-attempt-v2" and attempt["marker"] == marker and
            attempt["artifact"] == artifact and attempt["artifact_sha256"] == sha == binding["artifact_sha256"] and
            attempt["body"] == body and attempt["body_sha256"] == digest(body.encode()) and
            attempt["wire_sha256"] == digest(attempt["wire"].encode()) == binding["payload_sha256"],
            "successor original attempt changed")
    require(identity["caller_surface_uuid"].upper() == caller == artifact["successor_surface_uuid"].upper() and
            identity["caller_workspace_uuid"].upper() == sender["workspace_uuid"].upper() ==
            artifact["successor_workspace_uuid"].upper() and
            identity["caller_pane_uuid"].upper() == sender["pane_uuid"].upper() ==
            artifact["successor_pane_uuid"].upper() and
            identity["target_surface_uuid"].upper() == artifact["target_executor_uuid"].upper() and
            identity["target_workspace_uuid"].upper() == artifact["target_workspace_uuid"].upper() and
            identity["target_pane_uuid"].upper() == artifact["target_pane_uuid"].upper(), "successor original recipient changed")
    expanded = input_texts(attempt["wire"])
    require(expanded[-1] == body, "successor original wire/body mismatch")
    return dict(kind="rpc", selectors=[identity["target_surface_uuid"], binding["session_id"]], texts=[body])


@contract_errors("actual sender evidence")
def require_unrelated(call, receiver, sender, targets, *, state_root=None):
    if call.get("reconcile") is True or call.get("operation") == "reconcile":
        return
    require(call.get("reconcile") in (None, False), "nonliteral reconcile-only flag")
    require(call["kind"] != "unresolved", "unresolved actual sender cannot prove no business input")
    if call["kind"] == "successor":
        call = _successor(call, sender, state_root or Path.home() / ".local/state/multi-agent-collaboration")
    selectors = call.get("selectors", [call.get("surface")])
    broadcast = call.get("operation") == "broadcast"
    require(broadcast or selectors and all(_literal.target(s) for s in selectors), "unresolved actual sender recipient")
    identities = {_literal.target(receiver.get(k)) for k in ("surface_uuid", "surface_ref", "pane_uuid", "session_id")}
    if not broadcast and not any(_literal.target(s) in identities for s in selectors):
        # UUID-only v3 adoption cannot prove an unbound UI ref is a different
        # receiver. A recycled or omitted ref must never establish zero input.
        require(not any(isinstance(s, str) and re.fullmatch(r"(?:surface|pane):[0-9]+", s)
                        for s in selectors), "unbound UI ref cannot prove unrelated recipient")
        return
    texts = list(call.get("texts", []))
    for key in ("text", "pack"):
        if call.get(key) is not None:
            texts.append(call[key])
    if call.get("text_file") is not None:
        texts.append(read_regular(call["text_file"]).decode("utf-8"))
    require(texts and all(isinstance(text, str) for text in texts), "unresolved original-executor payload")
    require(not any(token in value for text in texts for value in input_texts(text) for token in targets),
            "original business pack has an attempted native sender call")
