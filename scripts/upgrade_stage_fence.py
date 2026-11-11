"""Fenced validation stage for the original clients; no replacement, model change or history rewrite.

A stage pins the original startup record unchanged and admits only evidence
after its own unique prompt. The original startup, its failures and the old
controller remain authoritative history; a stage never rewrites or replays them.
Input requires the stage's own adoption, published outside every client.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from orchestration_common import checked_path, digest, file_ref, json_ref, read_regular, require
from upgrade_startup_contract import readiness_command

SCHEMA = "upgrade-stage-fence-v1"
# A recovered stage: the same three original sessions, restored by the user's
# authority into one new workspace, with a few original files exactly re-pinned.
RECOVERED_SCHEMA = "upgrade-stage-fence-v2"
ADOPTION_SCHEMA = "upgrade-stage-adoption-v1"
RECOVERY_SCHEMA = "upgrade-stage-recovery-v1"
FIELDS = {"schema", "stage_id", "models", "original_startup", "authority", "controller", "control", "prompts", "hooks"}
LANES = ("codex", "claude_a", "claude_b")
HOOK_PINS = ("codex/hooks.json", "claude_a/settings.json", "claude_b/settings.json")
# Only these original pins may be superseded, each by one exact file reference
# frozen in the stage record. Never a tolerance for other configuration.
RECOVERABLE_PINS = ("codex/config.toml",)
RECOVERY_FIELDS = {"workspace_uuid", "sessions", "pins"}
RECOVERED_LAUNCH_SCHEMA = "upgrade-stage-recovered-launch-v1"
LAUNCH_FIELDS = {"schema", "lane", "provider", "native_root", "session_id", "pid", "birth", "surface_uuid",
                 "pane_uuid", "workspace_uuid", "tty", "startup"}


def stage_record_fields(stage):
    return FIELDS | ({"recovery"} if stage.get("schema") == RECOVERED_SCHEMA else set())


def recovered_pins(stage):
    """The exact replacements a recovered stage freezes; {} for an original stage."""
    if stage.get("schema") != RECOVERED_SCHEMA:
        return {}
    pins = stage["recovery"]["pins"]
    require(isinstance(pins, dict) and set(pins) <= set(RECOVERABLE_PINS)
            and all(isinstance(v, dict) and set(v) == {"path", "sha256", "bytes", "mode"} for v in pins.values()),
            "STAGE_RECOVERED_PIN_SET_CHANGED")
    return pins


def stage_pins_for_controller(controller):
    """Recovered pins named by a stage release's own binding, read without validating the stage."""
    binding = json.loads(read_regular(Path(controller) / "upgrade-binding.json"))
    fence = binding.get("stage_fence")
    require(isinstance(fence, dict), "STAGE_BINDING_REQUIRED")
    raw = read_regular(Path(fence["path"]))
    require(digest(raw) == fence["sha256"], "STAGE_RECORD_CHANGED")
    return recovered_pins(json.loads(raw))


# The shared MAC sender pastes only one line of at most 700 UTF-8 bytes; a
# longer or multiline text becomes a reference notice and could never equal
# this prompt in the native journal. The stage id doubles as receipt marker.
INLINE_MAX_BYTES = 700


def stage_prompt(stage_id, model, controller, python, rtk, lane):
    """A new, distinct, single-line readiness input; never the original prompt or its 900s wait."""
    command = readiness_command(controller, python, rtk, lane)
    invocation = command
    if lane == "codex":
        invocation = ("用 functions.exec 执行 JavaScript：text(await tools.exec_command(" +
            json.dumps(dict(cmd=command, login=False, max_output_tokens=1200),
                       ensure_ascii=False, separators=(",", ":")) + "));")
    text = ("受限验证阶段 " + stage_id + "（" + model + "，角色 " + lane + "）：原启动记录与失败保留，不重做旧探针。"
            "仅用一个正常工具调用执行：" + invocation +
            " 成功后回复 STAGE_READY 并结束本轮；不手动运行 hook、不发消息、不新建会话。")
    require(len(text.encode("utf-8")) <= INLINE_MAX_BYTES and not any(c in text for c in "\r\n\t"),
            "STAGE_PROMPT_NOT_SINGLE_INLINE_LINE")
    return text


def control_directory(binding, stage_id):
    return Path(binding["evidence_root"]) / "live-control" / stage_id


def validate_stage(ref, binding, spec):
    """Return the validated stage; the binding names it, the record pins the original startup."""
    fence = binding.get("stage_fence")
    require(isinstance(fence, dict) and set(fence) == {"path", "sha256", "stage_id", "models"},
            "STAGE_BINDING_REQUIRED")
    require(ref["sha256"] == fence["sha256"], "STAGE_RECORD_CHANGED")
    stage = json_ref(ref, "validation stage")
    require(stage.get("schema") in (SCHEMA, RECOVERED_SCHEMA) and set(stage) == stage_record_fields(stage),
            "STAGE_FIELDS_CHANGED")
    stage_id, models = stage["stage_id"], stage["models"]
    # Each lane keeps its own provider; a stage pins the one model per lane.
    require(isinstance(stage_id, str) and stage_id and "/" not in stage_id and not stage_id.startswith(".")
            and isinstance(models, dict) and set(models) == set(LANES)
            and all(isinstance(m, str) and m for m in models.values())
            and fence["stage_id"] == stage_id and fence["models"] == models, "STAGE_IDENTITY_CHANGED")
    control = control_directory(binding, stage_id)
    require(ref["path"] == fence["path"] == str(control / "stage.json")
            and stage["control"] == str(control), "STAGE_LOCATION_CHANGED")
    require(stage["controller"] == binding["controller_root"]
            and checked_path(stage["controller"]) == Path(binding["controller_root"]), "STAGE_CONTROLLER_CHANGED")
    require(stage["original_startup"] == file_ref(Path(binding["config_root"]) / "startup-v2.json")
            and stage["controller"] != spec["controller"], "STAGE_ORIGINAL_STARTUP_CHANGED")
    authority = stage["authority"]
    # The authority copy lives beside the stage, inside the lane-readable,
    # lane-immutable live-control tree; never an arbitrary outside path.
    require(isinstance(authority, dict) and set(authority) == {"path", "sha256"}
            and authority["path"] == str(control / "AUTHORITY.json")
            and digest(read_regular(Path(authority["path"]))) == authority["sha256"], "STAGE_AUTHORITY_CHANGED")
    python, rtk = spec["pins"]["python"]["path"], spec["pins"]["rtk"]["path"]
    require(stage["prompts"] == {lane: stage_prompt(stage_id, models[lane], stage["controller"], python, rtk, lane)
                                 for lane in LANES}, "STAGE_PROMPTS_CHANGED")
    # The original startup pins keep their old hook hashes (history). The stage
    # separately pins the rewritten files, which must name only this controller.
    hooks = stage["hooks"]
    require(isinstance(hooks, dict) and set(hooks) == set(HOOK_PINS), "STAGE_HOOK_PINS_CHANGED")
    for key in HOOK_PINS:
        require(hooks[key]["path"] == spec["pins"][key]["path"]
                and file_ref(Path(hooks[key]["path"])) == hooks[key], "STAGE_HOOK_FILE_CHANGED:" + key)
        text = read_regular(Path(hooks[key]["path"])).decode()
        require(str(Path(spec["controller"]) / "scripts") not in text
                and str(Path(stage["controller"]) / "scripts/cmux_workflow_advisory.py") in text,
                "STAGE_HOOK_CONTROLLER_CHANGED:" + key)
    if stage["schema"] == RECOVERED_SCHEMA:
        validate_recovery(stage, spec)
    return stage


def validate_recovery(stage, spec):
    """The restored identity: one workspace, the three original session IDs, exact re-pins."""
    import uuid
    recovery = stage["recovery"]
    require(isinstance(recovery, dict) and set(recovery) == RECOVERY_FIELDS, "STAGE_RECOVERY_FIELDS_CHANGED")
    canonical = lambda value: str(uuid.UUID(value))
    sessions = recovery["sessions"]
    require(recovery["workspace_uuid"] == canonical(recovery["workspace_uuid"])
            and isinstance(sessions, dict) and set(sessions) == set(LANES)
            and all(sessions[lane] == canonical(sessions[lane]) for lane in LANES)
            and len(set(sessions.values())) == len(LANES), "STAGE_RECOVERY_IDENTITY_CHANGED")
    # Claude IDs are frozen in the original startup; Codex's thread came from its launch.
    require(all(sessions[lane] == canonical(spec["clients"][lane]["session_id"]) for lane in ("claude_a", "claude_b")),
            "STAGE_RECOVERY_SESSION_NOT_ORIGINAL")
    for key, pin in recovered_pins(stage).items():
        require(pin["path"] == spec["pins"][key]["path"] and pin != {k: spec["pins"][key].get(k) for k in pin},
                "STAGE_RECOVERED_PIN_LOCATION_CHANGED:" + key)


def stage_view(spec, stage):
    """The original startup with the controller replaced and the stage's own file pins, for readiness matching."""
    view = copy.copy(spec)
    view["controller"] = stage["controller"]
    if stage.get("schema") == RECOVERED_SCHEMA:
        # The hook observer records the stage's hook pins; readiness compares the same reference.
        view["pins"] = dict(spec["pins"], **stage["hooks"], **recovered_pins(stage))
    return view


def load_stage(source):
    """Inside a stage release: the original startup plus this exact stage, or refuse.

    The stage binding is the original binding with only controller_root and
    stage_fence added/replaced. Every other original pin is verified unchanged.
    """
    from upgrade_startup_contract import validate_startup
    source = checked_path(source)
    binding = json.loads(read_regular(source / "upgrade-binding.json"))
    fence = binding.get("stage_fence")
    require(isinstance(fence, dict) and binding.get("controller_root") == str(source), "STAGE_BINDING_REQUIRED")
    startup_ref = file_ref(Path(binding["config_root"]) / "startup-v2.json")
    spec, original = validate_startup(startup_ref, stage_controller=source)
    require({k: v for k, v in binding.items() if k not in ("controller_root", "stage_fence")}
            == {k: v for k, v in original.items() if k != "controller_root"}
            and "stage_fence" not in original, "STAGE_BINDING_CHANGED")
    stage = validate_stage(file_ref(fence["path"]), binding, spec)
    return startup_ref, spec, binding, stage


def recovery_path(stage):
    return Path(stage["control"]) / "recovery.json"


def recovered_launches(stage, stage_ref, startup_ref):
    """The restored clients' launch identities, published once outside every client.

    None when the stage is not a recovered stage. Raises until the external
    publisher has frozen them; the original launch records are never rewritten.
    """
    if stage.get("schema") != RECOVERED_SCHEMA:
        return None, None
    ref = file_ref(recovery_path(stage))
    return ref, recovery_launches(stage, stage_ref, startup_ref, json_ref(ref, "stage recovery"))


def recovery_launches(stage, stage_ref, startup_ref, value):
    """The launches of one recovery record for this stage, or refuse; checked before and after publication."""
    sessions, workspace = stage["recovery"]["sessions"], stage["recovery"]["workspace_uuid"]
    require(value.get("schema") == RECOVERY_SCHEMA and value.get("stage") == stage_ref
            and value.get("startup") == startup_ref and value.get("input_operations") == 0
            and isinstance(value.get("launches"), dict) and set(value["launches"]) == set(LANES),
            "STAGE_RECOVERY_RECORD_CHANGED")
    for lane, launch in value["launches"].items():
        require(set(launch) == LAUNCH_FIELDS and launch["schema"] == RECOVERED_LAUNCH_SCHEMA
                and launch["lane"] == lane and launch["startup"] == startup_ref
                and launch["provider"] == ("codex" if lane == "codex" else "claude")
                and launch["session_id"] == sessions[lane] and launch["workspace_uuid"] == workspace,
                "STAGE_RECOVERY_LAUNCH_CHANGED:" + lane)
    require(len({launch["surface_uuid"] for launch in value["launches"].values()}) == len(LANES)
            and len({launch["pid"] for launch in value["launches"].values()}) == len(LANES),
            "STAGE_RECOVERY_LAUNCHES_NOT_DISTINCT")
    return value["launches"]


def stop_paths(binding):
    """Both the original experiment stop and this stage's own stop end input."""
    paths = [Path(binding["evidence_root"]) / "live-control/stop.json"]
    fence = binding.get("stage_fence")
    if isinstance(fence, dict):
        paths.append(Path(fence["path"]).parent / "stop.json")
    return paths


def stage_adoption(binding):
    """The input gate's stage branch: the externally published stage adoption."""
    fence = binding["stage_fence"]
    control = Path(fence["path"]).parent
    stage_ref = file_ref(fence["path"])
    require(stage_ref["sha256"] == fence["sha256"], "STAGE_RECORD_CHANGED")
    require(not any(map(os.path.lexists, stop_paths(binding))), "EXPERIMENT_STOP_REQUESTED")
    adoption = json.loads(read_regular(control / "adoption.json"))
    require(adoption.get("schema") == ADOPTION_SCHEMA and adoption.get("stage") == stage_ref
            and adoption.get("startup") == file_ref(Path(binding["config_root"]) / "startup-v2.json")
            and adoption.get("verified") is True and adoption.get("input_operations") == 0,
            "STAGE_ADOPTION_REQUIRED")
    return adoption
