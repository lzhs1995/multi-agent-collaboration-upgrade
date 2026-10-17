"""Complete frozen startup contract shared by the launcher and adoption verifier.

Valid configuration is not proof of client adoption or native reception.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import shlex
import uuid

from orchestration_common import checked_path, file_ref, json_ref, read_regular, require
from upgrade_client_isolation import (LANES, NATIVE_POLICY, client_argv, control_socket,
                                      environment, private_native_roots, sandbox_profile, verify_client_temp)
from upgrade_live_integrity import verify_artifact
from upgrade_socket_boundary import SOCKET_POLICY, binding_boundary, extend_profile

FIELDS = {"schema", "context", "arm", "controller", "native_policy", "pins", "clients",
          "rpc", "work_root", "workspace_name", "protected_surface_uuids", "stop_path",
          "adoption_path", "authorization"}


def readiness_command(controller, python, rtk, lane):
    return shlex.join([str(rtk), "proxy", python, "-I", "-B",
                      str(Path(controller) / "scripts/mac_upgrade_client_probe.py"), "--lane", lane])


def readiness_prompt(controller, python, rtk, lane):
    command = readiness_command(controller, python, rtk, lane)
    invocation = command
    if lane == "codex":
        invocation = ("使用 functions.exec，完整 JavaScript 为：\ntext(await tools.exec_command(" +
            json.dumps(dict(cmd=command, login=False, max_output_tokens=1200),
                       ensure_ascii=False, separators=(",", ":")) + "));\n")
    continuation = "成功后回复 EXPERIMENT_READY 并正常结束本轮。"
    if lane == "codex":
        bootstrap = shlex.join([str(rtk), "proxy", python, "-I", "-B",
            str(Path(controller) / "scripts/mac_upgrade_bootstrap.py"), "--timeout-seconds", "900"])
        continuation = ("成功后继续在同一会话执行首次启动等待器，不要结束任务或等待用户催促。"
            "它只等待外部核验者发布固定实验指令，不发送消息、不代你验收。使用 functions.exec：\n"
            "text(await tools.exec_command(" + json.dumps(dict(cmd=bootstrap, login=False,
                yield_time_ms=1000, max_output_tokens=5000), ensure_ascii=False, separators=(",", ":"))
            + "));\n若返回 session_id，使用 write_stdin 收集结果，每次等待不超过 30 秒；"
            "若 functions.exec 返回 cell ID，使用 functions.wait。"
            "收到 BOOTSTRAP_READY 后读取其中固定的 campaign 和 instructions，立即执行至真实收口。"
            "超时或 stop 时保留本会话和所有证据，报告真实状态。")
    return ("这是用户已授权的独立多 agent 实验启动核验，你的角色是 " + lane + "。"
            "先读取 " + str(Path(controller) / "SKILL.md") + "。仅通过一个正常工具调用执行以下完整命令：\n"
            + invocation
            + "\n" + continuation
            + "此命令只用于核验真实工具与自动 hook；不要手动运行 hook、伪造回执或发消息。"
            "正式任务须等待独立 adoption 核验。保留本会话，不得新建替代会话。")


def client_environment(roots, paths, lane, rtk):
    result = environment(roots, paths, lane)
    # Frozen native binaries do not use PATH resolution. Tools still need a
    # bounded ordinary system PATH; no account launcher/CCC alias is imported.
    result["PATH"] = os.pathsep.join(dict.fromkeys(map(str, (
        paths["bin"], Path(rtk).parent, "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"))))
    return result


def validate_startup(ref, *, controller=None, verify_pins=True):
    spec = json_ref(ref, "startup")
    fields = FIELDS | ({"socket_boundary"} if spec.get("native_policy") == SOCKET_POLICY else set())
    require(set(spec) == fields and spec["schema"] == "upgrade-startup-v2", "STARTUP_FIELDS_CHANGED")
    source = checked_path(spec["controller"])
    if controller is not None:
        require(source == Path(controller).resolve(), "STARTUP_CONTROLLER_CHANGED")
    import json
    binding = json.loads(read_regular(source / "upgrade-binding.json"))
    require(binding.get("version") == 2 and binding.get("execution_mode") == "live-isolated-v2"
            and spec["context"] == binding["context"] and spec["arm"] == binding["arm"]
            and binding["controller_root"] == str(source), "STARTUP_SCOPE_CHANGED")
    context = json_ref(spec["context"], "experiment")
    require(context["account_home"] == str(Path.home().resolve())
            and os.environ.get("HOME") == context["account_home"]
            and context["account_uid"] == os.getuid(), "STARTUP_ACCOUNT_CHANGED")
    require(spec["arm"] in ("A1", "B") and context.get("production_adoption") is False
            and context.get("skill") == "multi-agent-collaboration-upgrade", "STARTUP_EXPERIMENT_CHANGED")
    root = checked_path(context["root"])
    names = ("state", "config", "controllers", "campaigns", "evidence", "tmp", "bin")
    require(context["paths"] == {n: str(root / n) for n in names}, "STARTUP_PATHS_CHANGED")
    paths = {n: checked_path(root / n / spec["arm"]) for n in names}
    roots = private_native_roots(paths)
    require(source == paths["controllers"] / "releases/private-live-v2/source"
            and Path(ref["path"]) == paths["config"] / "startup-v2.json", "STARTUP_LOCATION_CHANGED")
    for name in ("state", "config", "campaign", "evidence", "tmp"):
        key = "campaigns" if name == "campaign" else name
        require(binding[name + "_root"] == str(paths[key]), "STARTUP_BINDING_PATH_CHANGED")
    require(spec["native_policy"] == binding["native_policy"]
            and spec["native_policy"] in (NATIVE_POLICY, SOCKET_POLICY)
            and binding["native_roots"] == {k: str(v) for k, v in roots.items()}, "STARTUP_NATIVE_ROOT_CHANGED")
    require(spec["work_root"] == str(paths["state"] / "work")
            and spec["workspace_name"] == "upgrade-lab-" + context["experiment_id"] + "-" + spec["arm"],
            "STARTUP_WORKSPACE_CHANGED")
    require(spec["stop_path"] == str(paths["evidence"] / "live-control/stop.json")
            and spec["adoption_path"] == str(paths["evidence"] / "live-control/adoption.json"),
            "STARTUP_CONTROL_PATH_CHANGED")
    require(spec["authorization"] == {"source": "user: Implement the independent experiment plan",
            "dedicated_clients": True, "replace_failed_sessions": False, "production_adoption": False},
            "STARTUP_AUTHORIZATION_CHANGED")
    protected = spec["protected_surface_uuids"]
    require(isinstance(protected, list) and protected == sorted({str(uuid.UUID(x)) for x in protected}),
            "STARTUP_PROTECTED_ENDPOINTS_CHANGED")
    pins = spec["pins"]
    required = {"codex", "claude", "cmux", "rtk", "node", "python", "codex_original_wrapper", "claude_original_wrapper",
                "codex_wrapper", "claude_wrapper", "helper", "codex-hooks.json", "claude-settings.json",
                "work_AGENTS.md", "work_CLAUDE.md", "claude-startup-state.json",
                "control_sandbox", *[x + "_sandbox" for x in LANES]}
    local = {"helper": paths["bin"] / "cmux-agent"}
    for provider in ("codex", "claude"):
        local[provider + "_wrapper"] = paths["bin"] / ("cmux-" + provider + "-wrapper")
    for name in ("codex-hooks.json", "claude-settings.json", "claude-startup-state.json"):
        local[name] = paths["config"] / name
    for name in ("AGENTS.md", "CLAUDE.md"):
        local["work_" + name] = paths["state"] / "work" / name
    for lane in ("control", *LANES):
        local[lane + "_sandbox"] = paths["config"] / (lane + ".sb")
    for lane in LANES:
        for name in (("config.toml", "hooks.json", "AGENTS.md", "credential-observations/enabled-v1")
                     if lane == "codex" else ("settings.json", "CLAUDE.md")):
            required.add(lane + "/" + name)
            local[lane + "/" + name] = roots[lane] / name
    optional = {"codex/auth.json", "claude_a/.credentials.json", "claude_b/.credentials.json"}
    require(required <= set(pins) <= required | optional, "STARTUP_PIN_SET_CHANGED")
    require(pins["python"] == context["python"], "STARTUP_PYTHON_CHANGED")
    for key in set(pins) & optional:
        lane, name = key.split("/", 1)
        local[key] = roots[lane] / name
    for key, path in local.items():
        require(pins[key]["path"] == str(path), "STARTUP_PIN_LOCATION_CHANGED:" + key)
    if verify_pins:
        for pin in pins.values():
            verify_artifact(pin)
    boundary = binding_boundary(binding, binary_pin=pins["codex"])
    if boundary is not None:
        require(spec["socket_boundary"] == boundary, "STARTUP_SOCKET_BOUNDARY_CHANGED")
        # Rebuild the exact profile. A pinned but broader profile is not this
        # policy; only Codex and its inherited descendants get two literals.
        writable = [paths[n] for n in ("state", "campaigns", "evidence", "tmp")]
        for lane in ("control", *LANES):
            denied = list(binding["isolation"]["immutable"])
            if lane != "control":
                denied += [p for key, p in roots.items() if key != lane]
            profile = sandbox_profile(writable, denied)
            if lane == "codex":
                profile = extend_profile(profile, boundary)
            require(read_regular(pins[lane + "_sandbox"]["path"]) == profile,
                    "STARTUP_SOCKET_PROFILE_CHANGED:" + lane)
    socket = control_socket(paths, socket_boundary=boundary)
    require(spec["rpc"] == {"path": str(socket), "argv": [pins["codex"]["path"],
            "app-server", "--listen", "unix://" + str(socket)]}
            and binding["codex_control_socket"] == str(socket), "STARTUP_RPC_CHANGED")
    require(set(spec["clients"]) == set(LANES), "STARTUP_LANES_CHANGED")
    ids = set()
    for lane in LANES:
        client = spec["clients"][lane]
        verify_client_temp(paths, lane)
        provider = "codex" if lane == "codex" else "claude"
        require(set(client) == {"provider", "native_root", "session_id", "argv", "environment"}
                and client["provider"] == provider and client["native_root"] == str(roots[lane]),
                "STARTUP_CLIENT_CHANGED")
        if provider == "claude":
            sid = str(uuid.UUID(client["session_id"]))
            require(sid == client["session_id"] and sid not in ids, "STARTUP_SESSION_CHANGED")
            ids.add(sid)
        else:
            require(client["session_id"] is None, "STARTUP_CODEX_ID_INVENTED")
        argv = client_argv(provider, pins[provider + "_wrapper"]["path"], spec["work_root"], roots[lane],
                           session_id=client["session_id"], remote_socket=socket if provider == "codex" else None,
                           socket_boundary=boundary if provider == "codex" else None)
        argv.append(readiness_prompt(source, pins["python"]["path"], pins["rtk"]["path"], lane))
        require(client["argv"] == argv and client["environment"] ==
                client_environment(roots, paths, lane, pins["rtk"]["path"]), "STARTUP_ARGUMENTS_OR_ENV_CHANGED")
    return spec, binding
