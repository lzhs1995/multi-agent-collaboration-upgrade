---
name: multi-agent-collaboration-upgrade
description: Independently test dependency scheduling and durable callbacks for existing Codex and Claude Code sessions in a dedicated cmux experiment. Requires an explicit private experiment context; never installs over production multi-agent-collaboration.
---

# Multi-Agent Collaboration Upgrade

This is an independent experimental skill. The installed
`multi-agent-collaboration` and its active sessions are separately owned.
Start with `scripts/upgrade_experiment.py`; every v3 campaign must pin its private
context, controller, configuration, state and exact permitted sessions. No shared
MAC path is a fallback. Do not run the inherited installer against a real account.
Do not edit global hooks, use production executors, migrate old attempts or change
HOME. The user has authorized private CODEX_HOME / CLAUDE_CONFIG_DIR values only
for newly created experimental clients. Existing processes and global environment
remain unchanged. Actual automatic hook adoption must be verified before live input.

The inherited transport contract below remains a compatibility reference. v2
campaigns retain their original controller and evidence; v3 is a separate format.
Offline estimates, replay and passing tests do not prove live efficiency. Report
the paired benchmark and correctness results before considering production use.
See [isolated experiment runbook](docs/ISOLATED-UPGRADE-RUNBOOK.md).
Current measured results and open live-validation gaps are recorded in the
[2026-10-18 validation report](docs/VALIDATION-20261018.md).

For v3, automatic workflow hooks run through the pinned advisory wrapper.
Unresolved callbacks, reservation, reask or review state do not block ordinary
tools or Stop. Legacy gate descriptions below are explicit diagnostics, never a
global session lock. This does not relax the sender's identity, original-attempt,
native reception or input safeguards. Verify actual automatic invocation in
each live client separately from configuration and manual hook tests.

Pressure acceptance requires both safety and successful independent work.
Report every attempted reservation, semantic conflict and registry-lock timeout;
zero double owners alone is insufficient. Only short registry transactions may
wait up to five seconds for their lock. Action locks remain fail-fast, and no
dispatch or callback is retried by this wait. See [pressure and experience
controls](references/lessons.md#pressure-and-field-experience).

Native readiness is only a startup result. A later original Codex
`event_msg/task_complete` with an error must halt the experiment with its pinned
native record, preserving sessions, completed measurements and every registered
slot. The driver observes at most 256 records per tick; unread or partial records
hold advancement without extending the registered watchdog. A crash reuses the
first failure evidence, never retries input. See [native failure closeout](
docs/ISOLATED-UPGRADE-RUNBOOK.md#r8-native-provider-failure-and-bounded-closeout).

For zero-input identity refusals, read [native caller inventory failures](references/native-caller-inventory.md).
Repair discovery before retrying a handshake; retain kernel/UUID authentication
and distinguish local bridge failure from executor availability.

Hook reconciliation carries its authenticated native caller source through nested
bridge checks and recollects live process evidence at each boundary. It must not
fall back to the shared daemon's inherited workspace. UI readiness uses current
provider status chrome; quoted compaction/reconnection text in a completed report
does not describe the live turn. See the [runtime rules](references/verified-compose-delivery.md#current-status-and-nested-hook-identity).

Use this skill for an explicitly requested supervisor/executor workflow. Do not
start agents merely because this skill is being installed, edited, or reviewed.
Requires Python 3.10+ and macOS cmux for live coordination. Offline tests also run
on Linux. Other terminals and agent-native sessions are not certified transports.

## Core Contract

- Reuse the designated context-bearing session. Never `/clear`, restart, replace,
  or switch models to recover from an API error or compaction failure. If its
  surface disappears, exact-session recovery requires the history-pinned,
  one-time authorization supported by the panel guard.
- Discover the caller workspace with `cmux identify --json` before selecting
  peers. Bind UUIDs and current role-map evidence, not remembered surface numbers,
  titles, or another workspace's focused panel. Existing peers must be visible
  terminal side splits. A new peer requires explicit authorization plus both
  `--spawn --spawn-authorized`; never create an agent tab as a shortcut.
- The supervisor owns scope, authorization, final acceptance and evidence review.
  The executor owns the scoped implementation and its truthful result. An ACK
  proves channel possession, never agreement with the supervisor's conclusion.
- This skill grants no production, deletion, deployment, or session-reset
  authority. Preserve concurrent user changes and scope mutations to the task.
- No Claude/multi-agent participation claim without current-task strict identity,
  acknowledgement and validation artifacts. SOLO work must be labelled SOLO.

## Same-workspace handshake: mandatory transport boundary

Before discovery, naming, bridge-test, handshake, task dispatch, callback, paste
or key submission, resolve the **live caller** from `cmux identify --json` and
join it to `cmux tree --all --json --id-format both`. Compare nonempty workspace
UUIDs, not labels, titles, focused panes, remembered refs or inherited summaries.
Both participants must be terminal surfaces in different panes of that workspace.

A user-designated executor is an additional exact UUID constraint. Supply
`--expected-workspace-uuid` and repeatable `--expected-executor-uuid` to the harness.
If the designation conflicts with the live caller, refuse input immediately;
do not select an old peer, alter environment identity, move a surface, launch a
replacement, or claim a successful handshake. Existing sessions remain intact.

`scripts/cmux_workspace_guard.py` provides the shared fail-closed check.
`cmux_agent_panel_guard.py` invokes it before help/recovery exceptions. Raw
`cmux send/send-key` and terminal-write RPC paths are blocked. Direct
`ask/send/broadcast/reconcile` calls to an absolute executable helper are admitted
only when its complete rendered bytes, adapter release and Python match this guard.
Optional `rtk`/`rtk proxy` prefixes are supported; shell/env wrappers, nesting,
redirection and alternate senders are refused. The helper routes through the same
`cmux_bridge`, which rechecks every paste/key and addresses both endpoints by UUID. The harness rechecks saved gate UUIDs before
later phases. Missing identity, ambiguous refs, stale UUIDs, target movement,
unknown cmux state and a changed designated peer deny before input. No force,
message-prefix, timeout, recovery-authority or environment bypass exists.

An optional caller-scoped file at
`~/.local/state/multi-agent-collaboration/workspace-scope/<CALLER_UUID>.json`
preserves an explicit user designation across invocations. Schema:
`{"version":1,"caller_surface_uuid":"UUID","workspace_uuid":"UUID","target_surface_uuids":["UUID"],"authorization_source":"user instruction"}`.
Only a new explicit user designation may replace that intent; do not clear it
because a target is unavailable. The scope narrows access and cannot permit
cross-workspace input. Resource coordination across workspaces uses existing
file/queue receipts, not executor-handshake transport or a STATUS-prefix exemption.

The production MAC installer registers the guard for both clients. This upgrade
does not use that installation route; it materializes a pinned private controller.
Run the installed hook with a cross-workspace/raw-input negative case and verify
exit 2 and zero input before claiming enforcement. Client settings registration,
active hook execution and imported Python modules are separate states; a process
that already imported an older transport has not magically hot-reloaded.
There is no atomic cmux snapshot-plus-send API: UUID addressing closes ref reuse,
and a fresh check precedes each input, but this is a supported-transport guard,
not an OS sandbox against arbitrary self-written socket clients.

## Workflow

1. Read [supervisor protocol](references/supervisor-protocol.md) when coordinating,
   [executor protocol](references/executor-protocol.md) when receiving work, and
   [review standard](references/consensus-audit-standard.md) when conducting review.
   This entrypoint overrides historical personal defaults in those references.
2. Run `scripts/mac_harness.py preflight` with an explicit task id, absolute
   artifact root, and the existing executor surface. This covers setup, identity,
   visible naming, non-submitting bridge proof, handshake and validation. Do not
   refresh a healthy handshake mid-command or stack another task behind work.
3. For formal plan consensus, record at least three non-blocking review rounds
   and require `consensus-check` PASS. Each round needs its own real artifact and
   hash. Earlier objections must be explicitly resolved, not hidden by a later
   PASS. Never pre-fill the executor's verdict. Do not impose consensus on a
   user-authorized solo task or call solo review multi-agent consensus.
4. Finalize a durable task pack with objective, allowed changes, prohibitions,
   input identities, verification, authorization source and completion template.
   Require `required_skill` to resolve to this installation's SKILL.md.
   Send only through `cmux_bridge.submit_task_pack`, explicitly including
   `READ_AND_OBEY_REQUIRED_SKILL_FIRST`. The executor must read this skill.
5. Completion is report-first, callback-second. The executor must actively call
   `cmux_bridge.submit_completion_callback` to send the pack-bound task id,
   nonce and report hash. A written report, screen DONE, prompt echo, or helper
   exit code is not completion. Missing/mismatched receipts remain unconfirmed.
   After the original callback returns, use the bounded closeout path below;
   do not turn receipt reconciliation into unlimited executor work.
6. Independently read raw artifacts before accepting a result. Check current
   pins, actual counts, negative controls, incomplete work, and postconditions.
   Keep failures immutable; publish corrections separately. Update handoff and
   disarm only at the verified terminal boundary.

## Delivery And Monitoring

The current contract for both directions, original-attempt recovery, Stop and idle
waiting is [verified native delivery](references/verified-compose-delivery.md).
Historical screen confirmation and separate Enter/Tab retry budgets are superseded.
New ordinary messages exceeding 700 UTF-8 bytes or containing any CR, LF or tab use the shared
[short notice and exact body](references/long-prompt-delivery.md) route. A receipt
for the notice proves neither body reading nor task acceptance. Formal packs and
callbacks keep their task-bound entrypoints. The explicitly user-authorized
60-second new-marker reask remains separate from default bounded idle observation.

For new long ordinary messages and handshake/review challenges, use
[short notices with pinned bodies](references/long-prompt-delivery.md).
The shared helper and harness persist exact UTF-8 bytes before input; the journal
and native hook retain the original body pin. Reception confirms the short notice
only. New formal packs use a dedicated single-line TASK_PACK_V2 notice with the
full pack SHA; callbacks keep their exact task-bound line. Existing attempts keep
their original controller and bytes, including historical multiline notices.

Use [efficiency and task closeout](references/efficiency-and-closeout.md) to
choose zero, one or two executors, apply phase-specific handshake budgets,
attribute delivery failures, and close accepted work without repeated reviews.

The legacy [executor closeout diagnostic](references/executor-closeout-enforcement.md)
freezes the report, task pack and original attempt after the
bound callback returns. Strict task-bound read-only diagnostics and original-controller
reconciliation remain available. In v3 its automatic advisory wrapper permits
ordinary tools and an honest waiting turn-end; do not apply the legacy
continue:false output as a conversation gate. Waiting does not confirm reception,
release resources or establish consensus.

After a proven sole `attempt-0001` with `NO_INPUT` and `events=[]`, closeout also
admits the exact synchronous original callback CLI once. The frozen pack chooses
that controller; the original journal retains its two-attempt limit and all live
gates. A receipt, legacy pending state, changed pins or any recorded input denies
this exception. It does not require retrying and never resends a queued callback.

After verified acceptance/disarm, the supervisor owns
[closeout feedback and the next dependency](references/efficiency-and-closeout.md#核收后把结论和下一步交回执行者).
A normal task boundary is not an API failure or a permanent session stop. Use
current evidence for authorized follow-up; never ask the user to relay to a bound peer.

### Fast handshake and verified delivery in both directions

- Bridge cleanup must observe an empty compose, not merely the absence of the
  full probe. Short per-executor tokens reduce guarded key count; unknown input
  is reread, never blindly deleted. ACK waits remain `AWAITING_EXECUTOR_ACK`,
  not terminal failures. See the executable postconditions in
  [efficiency and closeout](references/efficiency-and-closeout.md).

- Give the executor the absolute pending handshake receipt path in the initial
  challenge. It reads that file and the bound skill, then returns the exact ACK;
  no registry search, task audit or formal consensus is part of a handshake.
  A healthy current-task handshake is reused, not repeated for each status turn.
- After original native reception has been proven, reconcile the existing receipt before waiting again. Preserve the failed receipt, actual reception time and original task/provider/nonce. An earlier observation timeout does not prohibit the executor from returning its genuine ACK; do not generate a replacement nonce for this case.
- Match the observation budget to the harness phase minimum. A shorter local
  observation is a supervisor budget limitation, not executor silence. Recover a
  late genuine ACK against the original task/provider/nonce without resending.
- **Keys and queue entries do not prove reception.** Bind the exact receiver
  process/session/transcript and a fresh EOF fence at the original PASTE_INTENT.
  Only a new whole native user record after that fence, exactly equal to the
  payload including whitespace, yields NATIVE_RECEIVED. Claude queued_command
  remains pending. For Codex, use its bound native control socket once with the
  current expected turn identity; never inspect its composer, paste or press keys.
  For Claude, paste once, verify the full stable draft, then submit once with Enter.
  An uncertain native input permits original-log observation only. Historical
  terminal recovery retains only its original controller's single-key budget.
- `submit_completion_callback` writes an exclusive attempt journal before
  input, binding task pack SHA, report SHA, nonce and live workspace identities.
  A submitted attempt refuses repeat delivery; `--reconcile-only` is the explicit
  no-input recovery entrypoint. Changed report/pack/peer bindings fail closed. Keep an uncertain
  attempt; do not delete its journal to retry. Older attempts without a journal
  require their original delivery evidence and receiver history; do not create a
  retroactive send intent or manufacture a confirmed receipt.
- A pending intent is not a completion receipt. A returned, bound original attempt
  may satisfy only the honest handoff exception, never a delivery/consensus claim.
  If zero-input refusal is proven, preserve that evidence and fix the input
  classifier before authorizing a successor attempt. Do not treat a classifier
  error as executor unavailability or bypass workspace/compose guards.
- Two user-authorized existing executors are useful for independent bounded
  tasks. Give each a separate task id, nonce, artifact root and write scope;
  shared repositories have one writer. Two tabs in one executor pane are not
  two independent UI channels: serialize input and recheck exact surface UUIDs.
  Supervisors continue independent work while callbacks are pending. Persistent
  executor failures use the authorized solo takeover boundary below.

Supervisors use authenticated active markers for bounded discovery of frozen
reports through the PostToolUse `scripts/cmux_supervisor_report_guard.py`
(REPORT_DISCOVERED). Discovery is not delivery,
acceptance or disarm. Idle Stop records one durable notice and allows termination;
explicit `executor_ready.py persist` is read-only and bounded to 300 seconds.
CCC waits have a deadline; native Goal behavior requires separate evidence.

Follow [bounded handshakes, delivery and closeout](references/bounded-collaboration.md)
for each dispatch. Keep business acceptance, callback confirmation and resource
release separate. Once the original callback is verified and its task marker is
disarmed, stop that task's callback probes; transport maintenance is a separate
scope. Use a second executor only for an independent unresolved deliverable.

Read [receiver input and delivery](references/receiver-input-and-delivery.md)
when a surface may have returned to a shell or a send is unconfirmed. Current
input type, submission, consumption, agreement and accepted output are separate
facts. The bridge refuses SHELL/UNKNOWN before sending and preserves user drafts.

- Normal supervision is callback-first: one sentinel per task, 7200-second stable
  cadence, 1800 seconds for medium-risk work, 300-600 only for bounded high-risk
  windows. Do not spend tokens polling a healthy peer every few seconds.
- Active compaction is different: sample at 60 seconds, act after two unchanged
  samples; explicit thrashing is immediate failure. Freshly read the same surface,
  interrupt the failed compact once with Esc, preserve/disarm work, then permit
  one narrow same-session compact only within operator authority. Never clear.
- Retryable Claude API failures: use bounded same-session retries, at least
  60 seconds after the prior attempt ends. Temporary unavailability requires
  at least 300 consecutive seconds of evidenced API failures, starting at the
  first actual failure, plus a fresh failed attempt at or beyond that threshold.
  Any actual API success resets the failure clock; a static screen, queued or
  unknown delivery, and retry counts do not establish that failure interval.
  Billing/authentication/quota failures permit no blind retries and are classified
  separately, as is a user's explicit stop or withdrawal of authorization.
  Continue independent work while waiting. Freeze the executor, stop its sentinel
  and verify no concurrent writers before an already-authorized SOLO takeover;
  recover only in the original session at a safe handoff and fresh handshake.
  Read [availability and shared resources](references/availability-and-resources.md)
  for the evidence rule, v2 initialization, v1 migration and resource leases.
- On uncertain delivery, inspect the original native evidence without resending.
  Missing original binding/fence cannot be backfilled. Screen activity, ACK,
  empty compose and marker absence never substitute for exact native reception.
- Claude footer fields are recognized only below matching full composer borders.
  The observed optional numeric `规则` field is accepted only between `CLAUDE.md`
  and `MCPs`; footer-like draft text and unknown layouts remain protected.
  An empty composer does not authorize clearing an active turn or prove delivery.
- Confirmed product virtual suggestions are not actual compose input. The bridge
  tests exact known shapes in both directions. Unknown text stays occupied.
  Public default is **no force-compose**. Only explicit operator permission may
  enable `--force-compose`; active/queued work still cannot be overwritten.
- Preserve the current model. An optional `MULTI_AGENT_EXECUTOR_MODEL` records
  the task's requested model; it does not silently issue a model-switch command.
- `scripts/render_cmux_agent.py` renders the helper from a pinned baseline.
  Installation must verify the whole helper and same-release adapter with
  `helper-parity`; a matching path or a successful exit is insufficient. Ordinary
  messages use its sole guarded bridge route. Formal task packs and completion
  callbacks retain their dedicated task-bound bridge entrypoints.

## Enforcement And Evidence

PostToolUse success must use the client-supported `hookSpecificOutput` envelope
with `hookEventName: PostToolUse` and the verified proof in `additionalContext`.
Internal `action/results` objects are not valid top-level Codex hook output.
Validate the wire format against the official schema and then observe automatic
execution in the active client; a manual script exit is a separate offline check.

- Materialize the pinned controller and both client configurations through
  `scripts/upgrade_live_materialize.py` inside the explicit private context.
  Do not run the inherited production installer. Configuration checks are separate
  from active-client adoption: verify actual automatic hooks, kernel isolation,
  original session identities and native input capability before business input.
- Require semantic positive and negative tests through real hook entrypoints,
  not only helper definitions or source-string counts. Missing/null/boolean-as-
  count, stale evidence, changed pins and malformed receipts must fail closed.
- Evidence roots are explicit or registry-bound, never guessed from cwd. A
  missing read is unmeasured, not zero. Parse structured data and check producer
  semantics before declaring a defect. Record mutation hit counts in test probes.
- Cross-workspace coordination uses file/queue receipts for resources, never another task's executor or raw terminal input.
  Word/Zotero share one serial resource; NotebookLM sharing is account-wide.
  A lease, an actual OS lock and a task-bound drain receipt are distinct facts.
  Unknown Word document counts and unknown remote query outcomes prevent release.
- Domain-specific Stata experiments do not belong in this generic contract.
  See [lessons and test mapping](references/lessons.md) for reusable findings;
  the [incident archive](references/sentinel-and-compaction-incidents.md) records
  historical observations, not current model availability or deployment proof.
- `schemas/` contains versioned contracts; active-marker version 1 is unchanged.
  Capability data is observation-only: no ACP interoperability claim until two
  endpoints have actually been measured. Unknown capabilities remain null.

## 双向投递与高效协作维护

执行[原生投递与有界等待](references/verified-compose-delivery.md)：原 PASTE_INTENT 新鲜 EOF fence 后的完整 native user 才确认收到；排队仍 pending。原次恢复共用一次补键，封口保留 task-bound 诊断与零输入核收。默认 idle Stop 不要求求派；另行保留用户明确授权的每60秒新 marker 主动求派及其配套 hook，直到主管答复、新派发或 operator stop。

### Stop hook reentry

Stop/SubagentStop with boolean `stop_hook_active=true` exits successfully before task gates to prevent recursion. This does not confirm callbacks, disarm tasks, or bypass checks on the next normal turn. See [Stop hook lifecycle](references/stop-hook-lifecycle-20261005.md).

### Read-only confirmation

For an existing callback journal with an exact native receiver user record,
use the explicit original-lock settlement described below. Preserve the original
task/report/attempt and authenticate the live receiver; never resend to repair a
missing receipt. Evidence validation alone is not receipt publication, and a
published receipt does not prove a later Stop hook or whole-task acceptance.

Use [read-only confirmation and native receipts](references/read-only-delivery-confirmation.md) to distinguish compose, queue, consumption, and report acceptance. Preserve the original sender journal and runtime; the new checker does not authorize retries or silently migrate old attempts.


## 归档双执行者现场经验（2026-10-05）

见[归档回调与独立收尾](references/archive-callback-boundaries-20261005.md)。区分原生入站、正式回执与候选补丁实效；仅文档增量，不替换在途控制器。


## Shared managed-daemon callers

Read [caller identity and bounded callback closeout](references/shared-daemon-caller.md). The live guard resolves the original native client when a managed daemon inherits another terminal environment; it never changes process environment or relaxes UUID checks. Harness marker ownership uses the same resolution.

The unique live native client's UUID selects the caller; unrelated recycled TTY
rows cannot veto it. The selected UUID row must still match its actual TTY and
workspace. Report caller-resolution failures as such, never as Claude identity
failures. Keep the bridge test bounded to the test token and verify compose is
clear; keep prompt/callback Enter checks and original-attempt recovery intact.

For an ordinary-terminal Hook stopped by a root login permission denial, use the
[narrow login boundary](references/root-login-permission-boundary.md). Preserve
the original session and task; a source fix or process probe is not proof that
the original executor has resumed.

### Single-line new input

All fresh pastes pass `cmux_prompt_reference.require_inline`: at most 700 UTF-8
bytes with no CR, LF or tab. Ordinary and handshake bodies exceeding that
constraint use the single-line MESSAGE_REFERENCE_V2 notice. Formal dispatch
uses `task_pack_notice` through `submit_task_pack`, validating the entire frozen
pack before input; it cannot be hidden in an ordinary reference. The callback
retains its dedicated exact line and refuses an oversized or multiline value.
Legacy V1 four-line notices are readable only for original-attempt reconciliation.
No new representation can be used to replay a previous attempt.

Claude may fold a multiline paste or expand a tab even when Enter returns success.
Keep full stable-draft matching and exact new native user proof; never infer
delivery from keys, partial text, queue banners or composer clearing. Live
acceptance records the callback and automatic hook invocation in the actual
client; running a hook manually proves only the invoked test.
