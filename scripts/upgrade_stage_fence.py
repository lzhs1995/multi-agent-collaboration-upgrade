"""Fenced validation stage for the original clients; no input, model change or adoption.

A stage pins the original startup record unchanged and admits only evidence
after its own unique prompt. The original startup, its failures and the old
controller remain authoritative history; a stage never rewrites or replays them.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from orchestration_common import checked_path, digest, file_ref, json_ref, read_regular, require
from upgrade_startup_contract import readiness_command

SCHEMA = "upgrade-stage-fence-v1"
ADOPTION_SCHEMA = "upgrade-stage-adoption-v1"
FIELDS = {"schema", "stage_id", "model", "original_startup", "authority", "controller", "control", "prompts", "hooks"}
LANES = ("codex", "claude_a", "claude_b")
HOOK_PINS = ("codex/hooks.json", "claude_a/settings.json", "claude_b/settings.json")


def stage_prompt(stage_id, model, controller, python, rtk, lane):
    """A new, distinct readiness input; never the original startup prompt or its 900s wait."""
    command = readiness_command(controller, python, rtk, lane)
    invocation = command
    if lane == "codex":
        invocation = ("使用 functions.exec，完整 JavaScript 为：\ntext(await tools.exec_command(" +
            json.dumps(dict(cmd=command, login=False, max_output_tokens=1200),
                       ensure_ascii=False, separators=(",", ":")) + "));\n")
    return ("这是用户已授权的受限验证阶段 " + stage_id + "（模型 " + model + "），你的角色是 " + lane + "。"
            "原启动记录及其失败保留不变，不重做旧探针。先读取 " + str(Path(controller) / "SKILL.md") + "。"
            "仅通过一个正常工具调用执行以下完整命令：\n" + invocation +
            "\n成功后回复 STAGE_READY 并正常结束本轮。此命令只用于核验真实工具与自动 hook；"
            "不要手动运行 hook、伪造回执或发消息。正式任务须等待阶段 adoption 核验。保留本会话，不得新建替代会话。")


def control_directory(binding, stage_id):
    return Path(binding["evidence_root"]) / "live-control" / stage_id


def validate_stage(ref, binding, spec):
    """Return the validated stage; the binding names it, the record pins the original startup."""
    fence = binding.get("stage_fence")
    require(isinstance(fence, dict) and set(fence) == {"path", "stage_id", "model"}, "STAGE_BINDING_REQUIRED")
    stage = json_ref(ref, "validation stage")
    require(set(stage) == FIELDS and stage["schema"] == SCHEMA, "STAGE_FIELDS_CHANGED")
    stage_id, model = stage["stage_id"], stage["model"]
    require(isinstance(stage_id, str) and stage_id and "/" not in stage_id and not stage_id.startswith(".")
            and isinstance(model, str) and model
            and fence["stage_id"] == stage_id and fence["model"] == model, "STAGE_IDENTITY_CHANGED")
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
    require(stage["prompts"] == {lane: stage_prompt(stage_id, model, stage["controller"], python, rtk, lane)
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
    return stage


def stage_view(spec, stage):
    """The original startup with only the controller replaced, for readiness matching."""
    view = copy.copy(spec)
    view["controller"] = stage["controller"]
    return view
