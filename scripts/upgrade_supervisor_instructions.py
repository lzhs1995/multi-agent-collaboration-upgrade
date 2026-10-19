"""Generate the immutable first-turn instructions for an adopted native Codex."""
from __future__ import annotations

import shlex
from pathlib import Path

from orchestration_campaign import validate_campaign
from orchestration_common import read_ref, require, write_bytes_once
from upgrade_communication_plan import supervisor_messages


def render(campaign_ref):
    campaign = validate_campaign(campaign_ref)
    require(campaign.get("version") == 3 and campaign.get("evidence_class") == "cli_campaign_v3",
            "supervisor instructions require the original live v3 campaign")
    command = shlex.join(["rtk", "proxy", campaign["python"]["path"], "-B",
        str(Path(campaign["adapter"]["path"]) / "scripts/orchestration_run.py"),
        "--campaign", campaign_ref["path"], "--campaign-sha256", campaign_ref["sha256"]])
    runs = ", ".join(r["run_id"] for r in campaign["runs"])
    lanes = "\n".join(f"- {lane}: provider={s['provider']}, session_id={s['session_id']}, "
                      f"surface_uuid={s['surface_uuid']}" for lane, s in campaign["sessions"].items())
    strategy = ("This is A1 (mac_native). Decide the next ready node yourself from `status --run RUN` "
        "and the frozen plan. Do not call drafts, publish-drafts, next-action or publish-continuation. "
        "Do not call advance. "
        "Use the same active callback and independent acceptance protocol as B. You may start both "
        "ready independent Claude lanes; A1 is not an artificially serial baseline."
        if campaign["arm"] == "A1" else
        "This is B (omo). Use `publish-drafts --run RUN` to prepare fixed draft content early, "
        "then use `advance --run RUN --max-steps 12 --max-seconds 45` as your primary orchestration entrypoint. "
        "It progresses independent lanes through the original claim, prepare, dispatch, callback, candidate, "
        "release-repair and disarm gates. Handle every supervisor_actions entry and waiting recovery command; "
        "a new local work handoff yields before synchronous transport so you can start it promptly. "
        "Execute local work personally, collect the original process and call its candidate command. Read all "
        "candidate reports/tests/results before executing their exact native acceptance scripts once. Never "
        "repeat a local command or acceptance after a lost return; use original recovery. Call advance again "
        "after those actions and at bounded observation intervals while work remains. The step/time limits "
        "are checked between actions, not a timeout for a running 600-second preparation; collect that same "
        "process until return. It never performs native start, local business, independent acceptance or finish "
        "for you. Steps 4–7 below describe those underlying contracts and explicit recovery, not an instruction "
        "to duplicate actions already performed by advance. next-action remains optional read-only diagnosis.")
    return f"""You are the real Codex supervisor for the explicitly authorized isolated collaboration experiment.
Complete your entire registered group in this original session, without further user prompts.
Group: {campaign['session_group']}; arm: {campaign['arm']}; workload: {campaign['workload_class']}.
Run order (execute exactly once each): {runs}.
Original native identities (never replace, reset, switch models or borrow another surface):
{lanes}

Read the frozen campaign and plan. Your sole runner command prefix is:
{command}
Append the subcommand and its arguments below. All shell tool calls must use login:false,
workdir={campaign['root']!r}, and the rtk prefix. Use the frozen controller and adapter throughout.
Do not create internal subagents, operate production configuration or use a raw terminal sender.
Only the two pinned Claude sessions execute Claude nodes. You personally execute local Codex nodes.
The external driver only issues permits, starts the original clients once, publishes immutable files,
and measures completed runs. It never executes your work, accepts results, or sends follow-up prompts.

For EACH run in the order above:
1. Call `wait-permit --run RUN --timeout-seconds 45`. WAITING_PERMIT means call this same bounded
   read-only wait again in this original turn. Do not end your turn awaiting an external prompt.
   Other groups may be running between your warm rounds. RUN_ALREADY_CLOSED means inspect the
   original evidence and continue to your next registered run, never restart that closed run.
   SCHEDULE_ENDED or STOPPED_INPUT means stop new work. Observe/reconcile original attempts and
   complete any provably safe closeout that remains possible, then report the actual boundary.
2. On PERMIT_READY, inspect `status --run RUN`. Read its top-level `state` (not node `states`).
   If this run is NOT_STARTED, call `start-script --run RUN`.
   Execute the returned COMPLETE exec_script EXACTLY ONCE as a native functions.exec tool call,
   including its final LF. Do not wrap, trim, regenerate, evaluate in a shell, or reconstruct it.
   The verifier compares every byte of that original native tool input. If the call yields,
   collect that same running cell. An uncertain return never permits another start script.
   For STARTED use `recover --run RUN`; for INCOMPLETE_START preserve the failure and report it.
3. {strategy}
4. For each PENDING and ready node, call `claim --run RUN --node NODE` once. For a local node,
   personally execute its returned business_command exactly once, unchanged, in a shell tool.
   Read that original process result; never delegate local work or repeat its business command.
   For a Claude node call `prepare --run RUN --node NODE`, then `dispatch --run RUN --node NODE`
   only after preparation succeeds. Preparation can take 600 seconds: if your tool yields, poll
   the ORIGINAL exec cell/process until it returns. Do not kill it at a short timeout or rerun it.
   The pinned harness performs the handshake, preflight and single original task-pack submission.
   Do not send another handshake, retry a paste, change a nonce, press a recovery key or issue raw input.
5. Continue eligible independent work while Claude executes. Claude must read its exact task pack,
   run its bounded business command, write the pinned report, and ACTIVELY send the original callback.
   Use `callback --run RUN --node NODE` to reconcile that callback. For an uncertain dispatch use
   `reconcile --run RUN --node NODE`, which observes the ORIGINAL attempt with zero new input.
   UNKNOWN/queued/compacting/API failure is not delivery or acceptance. Preserve all original evidence.
   Observe at bounded intervals (at most 45 seconds per wait) when no independent work is ready;
   never add artificial business delays to influence the comparison.
6. After the business command finishes and the delegated original callback is verified, call
   `candidate --run RUN --node NODE`. It runs the frozen independent checker and returns report,
   tests, candidate and exec_script. READ the complete report, tests and underlying result artifacts.
   Only if they demonstrate the required correct output, execute that exact complete acceptance
   exec_script once as a native functions.exec call, preserving its final LF. A local review is
   solo_self_review. A written report, ACK or callback does not replace your independent acceptance.
   On a lost return use `recover` and `resume-accept` against the existing original native record;
   do not issue another acceptance script. Once accepted and released, call `disarm` for each
   Claude node through the original controller before reusing that lane.
7. After all nodes are accepted, all original leases released and Claude markers disarmed, call
   `finish --run RUN`, then `verify --run RUN`. Inspect the actual result. Do not fabricate a closure
   or edit its timestamps. Move directly to wait-permit for the next registered run in this group.

Failure/timeout contract: the registered cap is 1800 seconds. New work is refused at the cap or a
schedule halt. Original-log observation, candidate review, acceptance of already finished work,
verified failure settlement and disarm remain separately gated. Record a concrete original blocker
with `block --run RUN --node NODE --reason TEXT` when there is an owned failed task. Use the actual
supported failure-candidate boundary and exact returned native settlement script only if evidence
proves a safe terminal boundary. Otherwise retain the lease and report UNKNOWN. Never force release,
change the registration, edit native transcripts, remove attempt evidence, replace clients or retry
business input. Do not execute another group's run or begin a run without its own permit.

{supervisor_messages(campaign)}
After your last registered run closes, report the original results and end normally. The external
driver retains all 33 slots, 12 warm pairs and 3 cold pairs, including failures, timeouts and missing
rounds. Do not claim measured improvement, cost or production readiness from a single run.
"""


def publish(campaign_ref):
    campaign = validate_campaign(campaign_ref)
    content = render(campaign_ref).encode("utf-8")
    require(len(content.decode("utf-8")) < 24000, "supervisor bootstrap exceeds its fixed bound")
    path = Path(campaign["root"]) / "supervisor-instructions.md"
    if path.exists():
        from orchestration_common import file_ref
        ref = file_ref(path)
        require(read_ref(ref) == content, "original supervisor instructions changed")
        return ref
    return write_bytes_once(path, content)
