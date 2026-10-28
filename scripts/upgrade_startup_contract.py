"""Complete frozen startup contract shared by the launcher and adoption verifier.

Valid configuration is not proof of client adoption or native reception.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import shlex
import stat
import uuid

from orchestration_common import checked_path, digest, file_ref, json_ref, read_regular, require
from upgrade_client_isolation import (LANES, NATIVE_POLICY, STATION_LAUNCHERS, claude_ps_wrapper, client_argv,
                                      control_socket, environment, private_native_roots, sandbox_profile,
                                      station_argv, station_config, station_server_argv, verify_client_temp)
from upgrade_live_integrity import verify_artifacts
from upgrade_socket_boundary import SOCKET_POLICY, binding_boundary, extend_profile
from upgrade_child_budget_protocol import (POLICY as CHILD_POLICY, child_socket, validate_registered_hooks)

FIELDS = {"schema", "context", "arm", "controller", "native_policy", "pins", "clients",
          "rpc", "work_root", "workspace_name", "protected_surface_uuids", "stop_path",
          "adoption_path", "authorization"}
GOAL_SCHEMA = "upgrade-startup-v3"
GOAL_LANES = {"claude_a", "claude_b"}
GOAL_FIELDS = {"goal_startup", "lane_instructions"}
MAX_INSTRUCTION_BYTES = 256 * 1024
RESPONSE_OBSERVATION = "consumed-response-v1"
RESPONSE_OBSERVER_FILE = "upgrade_claude_response_observer.cjs"
RESPONSE_OBSERVER_PIN = "claude_response_observer"
CONTINUITY_REGISTRATION = "private-current-v1"
CONTINUITY_CURRENT_PIN = "mac_upgrade_private_current"
STARTUP_CONTINUITY = "original-startup-failure-v1"
PRE_EXEC_HOLD = "held-uuid-release-v1"


def pre_exec_hold_enabled(binding):
    """Clients wait before any native exec until an exact release names them."""
    if "pre_exec_hold" not in binding:
        return False
    require(binding["pre_exec_hold"] == PRE_EXEC_HOLD, "STARTUP_PRE_EXEC_HOLD_CHANGED")
    return True


def startup_continuity_enabled(binding):
    if "startup_continuity" not in binding:
        return False
    require(binding["startup_continuity"] == STARTUP_CONTINUITY
            and continuity_registration_enabled(binding), "STARTUP_FAILURE_CONTRACT_CHANGED")
    return True


def child_budget_enabled(binding):
    if "child_budget" not in binding:
        return False
    require(binding["child_budget"] == CHILD_POLICY, "STARTUP_CHILD_BUDGET_CHANGED")
    return True


def continuity_registration_enabled(binding):
    if "continuity_registration" not in binding:
        return False
    require(binding["continuity_registration"] == CONTINUITY_REGISTRATION,
            "STARTUP_CONTINUITY_REGISTRATION_CHANGED")
    return True


def response_observation_enabled(binding):
    if "response_observation" not in binding:
        return False
    require(binding["response_observation"] == RESPONSE_OBSERVATION,
            "STARTUP_RESPONSE_OBSERVATION_CHANGED")
    return True


def station_launchers_enabled(binding):
    """The binding's station launchers, or None. Shape is fixed by materialize_live."""
    if "station_launchers" not in binding:
        return None
    station = binding["station_launchers"]
    require(isinstance(station, dict) and set(station) == {"schema", "codex", "claude"}
            and station["schema"] == STATION_LAUNCHERS
            and isinstance(station["codex"], dict) and set(station["codex"]) == {"station", "base_url"}
            and isinstance(station["claude"], dict) and set(station["claude"]) == {"station", "key"},
            "STARTUP_STATION_LAUNCHERS_CHANGED")
    return station


def station_client(station, lane, python, mg):
    if station is None:
        return None
    provider = "codex" if lane == "codex" else "claude"
    value = dict(python=str(python), mg=str(mg), station=station[provider]["station"])
    if provider == "claude":
        value["key"] = station["claude"]["key"]
    return value


def station_rpc(station, socket, codex, python, mg, codex_root):
    """The private Codex server: its final native argv and, with stations, the mg launch of it."""
    argv = [str(codex), "app-server", "--listen", "unix://" + str(socket)]
    if station is None:
        return {"path": str(socket), "argv": argv}
    # mg-server.toml's launcher is [codex, "app-server"]; only the remaining arguments follow `--`.
    return {"path": str(socket), "argv": station_server_argv(argv, station["codex"]["base_url"]),
            "launch_argv": station_argv(python, mg, argv[1:], station=station["codex"]["station"]),
            "launch_environment": {"MG_CONFIG": str(Path(codex_root) / "mg-server.toml")}}


def response_observer_path(paths):
    return checked_path(paths["controllers"] / "releases/private-live-v2/source/scripts" /
                        RESPONSE_OBSERVER_FILE)


def bun_preload_option(path):
    # Native Bun parses BUN_OPTIONS itself; shell quoting can silently prevent
    # loading. Native trials reject literal backslashes in module filenames;
    # escape only the supported whitespace and quote argument delimiters.
    raw = str(path)
    require(not any(c in raw for c in "\\\n\r\x00"),
            "STARTUP_RESPONSE_OBSERVER_PATH_CHANGED")
    return "--preload " + "".join("\\" + c if c in " \t'\"" else c for c in raw)


def verify_response_observation_directory(native_root):
    directory = checked_path(Path(native_root) / "response-observations")
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            "STARTUP_RESPONSE_OBSERVATION_DIRECTORY_CHANGED")
    return directory


def require_goal_condition(condition):
    require(isinstance(condition, str) and condition == condition.strip() and bool(condition),
            "STARTUP_GOAL_CONDITION_REQUIRED")
    require(not any(ord(c) < 32 or ord(c) == 127 for c in condition), "STARTUP_GOAL_CONTROL_CHARACTER")
    require(condition.lower() not in {"clear", "stop", "off", "reset", "none", "cancel"},
            "STARTUP_GOAL_CLEAR_ALIAS")
    try:
        units = len(condition.encode("utf-16-le")) // 2
    except UnicodeError as exc:
        raise ValueError("STARTUP_GOAL_INVALID_UNICODE") from exc
    require(units <= 4000, "STARTUP_GOAL_TOO_LONG")
    return condition


def native_goal_condition(condition, instructions_path):
    require_goal_condition(condition)
    return require_goal_condition("Read and follow " + str(instructions_path)
                                  + ". Complete this registered goal: " + condition)


def pressure_lane_document(controller, python, rtk, lane, responsibilities):
    require(lane in LANES, "STARTUP_INSTRUCTION_LANE_UNKNOWN")
    require(isinstance(responsibilities, str) and bool(responsibilities.strip())
            and "\x00" not in responsibilities
            and len(responsibilities.encode()) <= MAX_INSTRUCTION_BYTES,
            "STARTUP_INSTRUCTIONS_INVALID")
    command = readiness_command(controller, python, rtk, lane)
    invocation = command
    if lane == "codex":
        invocation = ("使用 functions.exec，完整 JavaScript 为：\ntext(await tools.exec_command(" +
            json.dumps(dict(cmd=command, login=False, max_output_tokens=1200),
                       ensure_ascii=False, separators=(",", ":")) + "));\n")
    return ("# Pinned Goal startup responsibilities: " + lane + "\n\n"
            "Read " + str(Path(controller) / "SKILL.md") + ".\n"
            "First execute exactly one normal tool call for the real startup probe below. "
            "Do not manually run hooks, manufacture receipts, send messages, or claim adoption from the probe.\n"
            + invocation + "\nAfter the probe, follow the registered adoption-wait instructions below. "
            "Formal business and communication must wait for the external adoption gate. "
            "Keep this original session. Do not create replacements, replay attempts, clear a Goal to "
            "make input idle. Execute a bootstrap only when explicitly registered below. "
            "A missing gate is not permission to start work.\n\n"
            "## Registered lane responsibilities\n\n" + responsibilities + "\n")


def pressure_startup_prompt(lane, instructions, goals):
    path = instructions[lane]["path"]
    if lane in GOAL_LANES:
        condition = require_goal_condition(goals[lane])
        prefix = "Read and follow " + path + ". Complete this registered goal: "
        require(condition.startswith(prefix), "STARTUP_GOAL_INSTRUCTION_REFERENCE_CHANGED")
        require_goal_condition(condition[len(prefix):])
        return "/goal " + condition
    require(lane == "codex", "STARTUP_INSTRUCTION_LANE_UNKNOWN")
    return ("This is the authorized Goal startup experiment. Read and follow the complete pinned "
            "lane instructions at " + path + ". Execute their one real startup probe, then their external "
            "adoption wait and registered campaign plan in this same session. Execute any explicitly "
            "registered bootstrap as instructed. Do not create replacement clients or send input before adoption.")


def readiness_command(controller, python, rtk, lane):
    return shlex.join([str(rtk), "proxy", python, "-I", "-B",
                      str(Path(controller) / "scripts/mac_upgrade_client_probe.py"), "--lane", lane])


def bootstrap_continuation(controller, python, rtk):
    """Original bounded Codex continuation, reusable in pinned lane duties."""
    bootstrap = shlex.join([str(rtk), "proxy", python, "-I", "-B",
        str(Path(controller) / "scripts/mac_upgrade_bootstrap.py"), "--timeout-seconds", "900"])
    return ("成功后继续在同一会话执行首次启动等待器，不要结束任务或等待用户催促。"
        "它只等待外部核验者发布固定实验指令，不发送消息、不代你验收。使用 functions.exec：\n"
        "text(await tools.exec_command(" + json.dumps(dict(cmd=bootstrap, login=False,
            yield_time_ms=1000, max_output_tokens=5000), ensure_ascii=False, separators=(",", ":"))
        + "));\n若返回 session_id，使用 write_stdin 收集结果，每次等待不超过 30 秒；"
        "若 functions.exec 返回 cell ID，使用 functions.wait。"
        "收到 BOOTSTRAP_READY 后读取其中固定的 campaign 和 instructions，立即执行至真实收口。"
        "超时或 stop 时保留本会话和所有证据，报告真实状态。")


def readiness_prompt(controller, python, rtk, lane):
    command = readiness_command(controller, python, rtk, lane)
    invocation = command
    if lane == "codex":
        invocation = ("使用 functions.exec，完整 JavaScript 为：\ntext(await tools.exec_command(" +
            json.dumps(dict(cmd=command, login=False, max_output_tokens=1200),
                       ensure_ascii=False, separators=(",", ":")) + "));\n")
    continuation = "成功后回复 EXPERIMENT_READY 并正常结束本轮。"
    if lane == "codex":
        continuation = bootstrap_continuation(controller, python, rtk)
    return ("这是用户已授权的独立多 agent 实验启动核验，你的角色是 " + lane + "。"
            "先读取 " + str(Path(controller) / "SKILL.md") + "。仅通过一个正常工具调用执行以下完整命令：\n"
            + invocation
            + "\n" + continuation
            + "此命令只用于核验真实工具与自动 hook；不要手动运行 hook、伪造回执或发消息。"
            "正式任务须等待独立 adoption 核验。保留本会话，不得新建替代会话。")


def client_environment(roots, paths, lane, rtk, *, response_observation=False, child_budget=False, station=False):
    require(type(response_observation) is bool, "STARTUP_RESPONSE_OBSERVATION_CHANGED")
    require(type(child_budget) is bool, "STARTUP_CHILD_BUDGET_CHANGED")
    require(type(station) is bool, "STARTUP_STATION_LAUNCHERS_CHANGED")
    result = environment(roots, paths, lane, station=station)
    # Frozen native binaries do not use PATH resolution. Tools still need a
    # bounded ordinary system PATH; no account launcher/CCC alias is imported.
    result["PATH"] = os.pathsep.join(dict.fromkeys(map(str, (
        paths["bin"], Path(rtk).parent, "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"))))
    if lane != "codex":
        result["PATH"] = str(paths["bin"] / "claude-tools") + os.pathsep + result["PATH"]
        if child_budget:
            # This first opt-in protocol binds foreground non-fork native
            # completion only. Do not silently accept current default forks or
            # background handback/launch notes as an original complete report.
            result["CLAUDE_CODE_FORK_SUBAGENT"] = "0"
            result["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
        if response_observation:
            result["BUN_OPTIONS"] = bun_preload_option(response_observer_path(paths))
            result["UPGRADE_RESPONSE_OBSERVATIONS_DIR"] = str(roots[lane] / "response-observations")
    return result


def validate_startup(ref, *, controller=None, verify_pins=True, stage_controller=None, recovered=None):
    """stage_controller: a fenced stage's own release. Its three rewritten lane
    hook files are checked against it and pinned by the stage, not by this
    historical startup; every other original pin is still verified unchanged.

    recovered: exact file references that supersede named original pins. By
    default a recovered stage's own frozen pins; a builder passes the pins it is
    about to freeze. Each must name the original path and match exactly."""
    spec = json_ref(ref, "startup")
    goal_mode = spec.get("schema") == GOAL_SCHEMA
    fields = FIELDS | ({"socket_boundary"} if spec.get("native_policy") == SOCKET_POLICY else set())
    if goal_mode:
        fields |= GOAL_FIELDS
    require(set(spec) == fields and spec["schema"] in ("upgrade-startup-v2", GOAL_SCHEMA),
            "STARTUP_FIELDS_CHANGED")
    source = checked_path(spec["controller"])
    if controller is not None:
        require(source == Path(controller).resolve(), "STARTUP_CONTROLLER_CHANGED")
    import json
    binding = json.loads(read_regular(source / "upgrade-binding.json"))
    response_observation = response_observation_enabled(binding)
    continuity_registration = continuity_registration_enabled(binding)
    startup_continuity_enabled(binding)
    child_budget = child_budget_enabled(binding)
    station = station_launchers_enabled(binding)
    if goal_mode:
        require(isinstance(spec["goal_startup"], dict) and set(spec["goal_startup"]) == GOAL_LANES
                and isinstance(spec["lane_instructions"], dict)
                and set(spec["lane_instructions"]) == set(LANES), "STARTUP_GOAL_LANES_CHANGED")
        require(binding.get("startup_contract") == {key: spec[key] for key in ("schema", *GOAL_FIELDS)},
                "STARTUP_GOAL_BINDING_CHANGED")
    else:
        require("startup_contract" not in binding, "STARTUP_GOAL_DOWNGRADE_REFUSED")
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
                "codex_wrapper", "claude_wrapper", "claude_ps", "helper", "codex-hooks.json", "claude-settings.json",
                "work_AGENTS.md", "work_CLAUDE.md", "claude-startup-state.json",
                "control_sandbox", *[x + "_sandbox" for x in LANES]}
    local = {"helper": paths["bin"] / "cmux-agent", "claude_ps": paths["bin"] / "claude-tools/ps"}
    if continuity_registration:
        required.add(CONTINUITY_CURRENT_PIN)
        current_path = paths["state"] / "CURRENT.json"
        local[CONTINUITY_CURRENT_PIN] = current_path
        require(pins.get(CONTINUITY_CURRENT_PIN, {}).get("mode") == 0o444
                and str(current_path) in binding["isolation"]["immutable"],
                "STARTUP_CONTINUITY_CURRENT_MODE_CHANGED")
        current = json.loads(read_regular(current_path))
        require(current == {"source": str(source), "python": pins["python"]["path"],
                "manifest_sha256": digest(read_regular(source.parent / "MANIFEST.json"))},
                "STARTUP_CONTINUITY_CURRENT_CHANGED")
    if response_observation:
        required.add(RESPONSE_OBSERVER_PIN)
        local[RESPONSE_OBSERVER_PIN] = response_observer_path(paths)
        require(pins.get(RESPONSE_OBSERVER_PIN, {}).get("mode") == 0o444,
                "STARTUP_RESPONSE_OBSERVER_MODE_CHANGED")
    if goal_mode:
        for lane in LANES:
            key = lane + "_instructions"
            required.add(key)
            local[key] = paths["config"] / (lane + "-instructions.md")
            require(pins.get(key) == spec["lane_instructions"][lane]
                    and pins[key].get("mode") == 0o444, "STARTUP_INSTRUCTION_PIN_CHANGED")
            # Also verifies that the exact condition references this lane's document.
            pressure_startup_prompt(lane, spec["lane_instructions"], spec["goal_startup"])
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
    if station is not None:
        required |= {"mg", "magpie"}
        for lane in LANES:
            for name in (("mg.toml", "mg-server.toml") if lane == "codex"
                         else ("mg.toml", "mg-profiles/" + station["claude"]["key"] + ".json")):
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
    if child_budget:
        require(str(child_socket(paths)) in binding["isolation"]["immutable"],
                "STARTUP_CHILD_SOCKET_IMMUTABILITY_CHANGED")
    lane_hooks = ("codex/hooks.json", "claude_a/settings.json", "claude_b/settings.json")
    require(stage_controller is None or (controller is None
            and checked_path(stage_controller) != source), "STARTUP_STAGE_CONTROLLER_CHANGED")
    for name in ("codex-hooks.json", "claude-settings.json", *lane_hooks):
        document = json.loads(read_regular(pins[name]["path"]))
        try:
            validate_registered_hooks(document, pins["python"]["path"],
                Path(stage_controller) if stage_controller is not None and name in lane_hooks else source,
                provider="codex" if name.startswith("codex") else "claude", enabled=child_budget)
        except (ValueError, KeyError, TypeError) as exc:
            require(False, "STARTUP_CHILD_HOOK_CHANGED:" + name)
    if recovered is None and stage_controller is not None:
        from upgrade_stage_fence import stage_pins_for_controller
        recovered = stage_pins_for_controller(stage_controller)
    recovered = recovered or {}
    from upgrade_stage_fence import RECOVERABLE_PINS
    require(set(recovered) <= set(RECOVERABLE_PINS)
            and all(recovered[k]["path"] == pins[k]["path"] for k in recovered), "STARTUP_RECOVERED_PIN_CHANGED")
    if verify_pins:
        verify_artifacts(v for k, v in pins.items()
                         if (stage_controller is None or k not in lane_hooks) and k not in recovered)
        # A recovered file is verified as exactly as the pin it supersedes.
        verify_artifacts(recovered.values())
    require(pins["claude_ps"]["mode"] == 0o555
            and read_regular(pins["claude_ps"]["path"]) == claude_ps_wrapper(source, pins["python"]["path"]),
            "STARTUP_CLAUDE_PS_CHANGED")
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
    mg = pins["mg"]["path"] if station is not None else None
    require(spec["rpc"] == station_rpc(station, socket, pins["codex"]["path"], pins["python"]["path"], mg,
                                       roots["codex"])
            and binding["codex_control_socket"] == str(socket), "STARTUP_RPC_CHANGED")
    if station is not None:
        # The station launcher reads the pinned profile; the hook readers read settings.json.
        profile = "mg-profiles/" + station["claude"]["key"] + ".json"
        require(all(pins[lane + "/" + profile]["sha256"] == pins[lane + "/settings.json"]["sha256"]
                    for lane in GOAL_LANES)
                and read_regular(pins["codex/mg-server.toml"]["path"]) == station_config(
                    "codex", [pins["codex"]["path"], "app-server"], pins["magpie"]["path"], roots["codex"])
                and read_regular(pins["codex/mg.toml"]["path"]) == station_config(
                    "codex", pins["codex_wrapper"]["path"], pins["magpie"]["path"], roots["codex"])
                and all(read_regular(pins[lane + "/mg.toml"]["path"]) == station_config(
                    "claude", pins["claude_wrapper"]["path"], pins["magpie"]["path"], roots[lane],
                    roots[lane] / "mg-profiles") for lane in GOAL_LANES), "STARTUP_STATION_LAUNCHERS_CHANGED")
    require(set(spec["clients"]) == set(LANES), "STARTUP_LANES_CHANGED")
    ids = set()
    for lane in LANES:
        client = spec["clients"][lane]
        verify_client_temp(paths, lane)
        if response_observation and lane != "codex":
            verify_response_observation_directory(roots[lane])
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
                           socket_boundary=boundary if provider == "codex" else None,
                           station=station_client(station, lane, pins["python"]["path"], mg))
        argv.append(pressure_startup_prompt(lane, spec["lane_instructions"], spec["goal_startup"])
                    if goal_mode else
                    readiness_prompt(source, pins["python"]["path"], pins["rtk"]["path"], lane))
        require(client["argv"] == argv and client["environment"] ==
                client_environment(roots, paths, lane, pins["rtk"]["path"],
                                   response_observation=response_observation, child_budget=child_budget,
                                   station=station is not None),
                "STARTUP_ARGUMENTS_OR_ENV_CHANGED")
    return spec, binding
