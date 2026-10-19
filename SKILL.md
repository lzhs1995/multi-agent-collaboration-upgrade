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
For new B/v3 campaigns, use the pinned runner's `advance --run RUN --max-steps 12
--max-seconds 45` as the primary orchestration entrypoint. It batches original
gated actions across independent lanes; it does not perform native START,
local business, independent ACCEPT or FINISH. Handle its supervisor/recovery
handoffs, collecting existing original actions rather than repeating them.
Time is checked between actions, not during an in-flight synchronous operation.
A1 uses the manual baseline and can run both independent Claude lanes in parallel.
This explicit foreground batching does not wake an idle model or prove speedup.
For provider capacity, usage-service success and HTTP 200 response headers alone
do not establish model availability: a successful HTTP stream may contain only
pings followed by an account/user concurrency error. Record the complete bounded
stream outcome. Even two completed overlapping streams establish only that
observation, not sustained capacity or native collaboration acceptance. Preserve
these diagnostic requests separately from registered business/message samples;
do not launch an unchanged campaign into a just-observed capacity refusal.
`CCC_CLAUDE_REQUEST_OBSERVATIONS_DIR` selects an auxiliary output directory;
it does not load the CCC Bun observer. The private launcher removes inherited
preloads and pins the native binary. Verify observer activation separately before
interpreting missing files. The inspected CCC observer records request headers
and redirect credential scope, not complete response status, SSE or usage.
Neither its presence nor absence establishes model completion or failure.
New materializations can opt in with `--observe-claude-responses`. The private
response observer is a pinned controller artifact, scoped to the original root
PID and its private 0700 directory. It observes only data the original consumer
reads; it never clones, drains, retries or creates a request. Require an actual
`LOADED` record with the matching source hash before treating it as active.
Use Bun's character escaping for `BUN_OPTIONS`, not shell quoting; configuration
roundtrips and a successful `--version` alone do not prove preload activation.
Reject unsupported literal backslashes before materialization. APFS path casing
may differ across Python and Bun: verify the same nonsymlink directory and its
original inode through both names instead of requiring identical casing.
Preserve HTTP status, SSE errors, usage, cancellation and EOF separately.
Native Bun may call `body.cancel()` after iterator EOF. Observe `readMany().done`
without counting its prefetched values, then count only consumed iterator values.
Cancellation after that EOF fact does not erase completion. Failed cancellation
does not end observation of a still-readable body; record failure separately.
If a pending iterator cancels without an observed reader EOF, retain unknown
origin rather than inferring natural EOF or deliberate user cancellation.
Decoded text and parsed JSON cannot establish raw wire-byte hashes; truncated,
unread or unsupported paths remain incomplete. Request class stays UNKNOWN.
The observer has bounded request, parse and snapshot budgets, keeps error names
on a fixed allowlist and stops a failing telemetry sink. Promise/Response identity
and consumer values are preserved on supported paths, but observer callbacks
add overhead and rejection handlers; do not claim identical timing or process
`unhandledRejection` behavior. Its local pressure results are separate from
native model startup, task acceptance and paired efficiency measurements.
Use identical observer configuration and source bytes in both comparison arms;
local instrumentation overhead does not establish collaboration efficiency.
New paired registrations use `upgrade-paired-registration-v3`, including an
explicit `compact` boolean and one `response_observation` policy for all seven
groups. Observation defaults to disabled. To enable it, pass
`--observe-claude-responses` to both registration and each materialization from
the same frozen source. Configure, permit issue, driver launch, campaign freeze
and validation reject a different setting, SHA or byte count. Reports check
the original registration, runtime/startup and complete external closeout row;
copied pins cannot authenticate changed timings, costs or intervention counts.
These checks establish configuration only, never actual observer LOADED.
Historical v1/v2 registrations retain their interpretation and are labeled
`legacy_unverified`; do not rewrite them to obtain the new qualification.
Four local AB/BA pairs at 256 concurrent requests measured 3.57–7.46 times
the uninstrumented controller elapsed time. This optional diagnostic has material
cost in that workload; correctness passing is not a performance pass. Disclose
instrumentation and its calibration when reporting any future paired result.
The subsequent temporary-file cleanup repair avoids deleting a temp file after
a failed exclusive open and avoids redundant unlink after successful rename.
Four old/new pairs passed correctness but produced two speedups and two
slowdowns; stable improvement and absence of regression remain unproven.
See [response observation validation](docs/RESPONSE-OBSERVATION-VALIDATION.md).
See [configuration and cleanup validation](docs/OBSERVER-COMPARISON-VALIDATION.md).
Keep HTTP error status, saved response-body errors and local reader exceptions
separate. A saved JSON `rate_limit_error` with HTTP 429 remains rate-limit
evidence even if a subsequent EOF read raises `OSError`; preserve both facts.
Usage growth and elapsed time can justify a new bounded capacity observation,
but never establish recovery or justify replaying an existing native attempt.
Claude's default footer without a custom statusline is supported only when the
matching complete borders are followed by the exact permissions row. Empty
compose and idle turn remain separate checks; preserve drafts and reject unknown
or truncated footer structure. Complete Claude `⎿ Tip:` and goal chrome before
the composer cannot hide a live spinner, including measured timerless activity.
Skip only those hint rows; never skip arbitrary prose or composer content.
The measured default permissions footer may include `· esc to interrupt` and
the complete `◎ /goal active (duration)` annotation after the lower border.
Match the whole observed row; unknown suffixes and truncated annotations remain
unknown. This layout says nothing by itself about native Goal renewal or capacity.
On initial capability refusal, preserve the exact original screens, hashes,
parser results, read sequence, failure stage and error in the original receipt.
Failed reads retain a missing screen; do not reread to manufacture diagnostics.
A later successful parse cannot establish the cause of an earlier uncaptured
refusal. Diagnostic evidence grants no input, retry or successful capability.
The live materializer applies the same two measured parser changes to its pinned
base controller with exact source-shape checks. Preserve the base's native Codex
delivery and other status layouts; copying an older whole bridge is not a repair.
Validate the generated controller itself, including draft refusal and original
native receipt fences, before starting clients.
See the measured rules in
[verified delivery](references/verified-compose-delivery.md).
See [isolated experiment runbook](docs/ISOLATED-UPGRADE-RUNBOOK.md).
For new private campaigns, pin the actual Claude executable/version and requested
model; installation alone does not establish adoption by an existing process.
The 2.1.303 retry verified adoption but did not produce assistant output or a
successful native comparison. Preserve rate limits, empty streams and Goal
check failures separately; do not treat Goal pause as proven termination or as
permission to re-input. Goal checks do not validate independent long-running loop.
See [Claude version validation](docs/CLAUDE-VERSION-VALIDATION.md).
Current measured results and open live-validation gaps are recorded in the
[r29 final delivery](docs/VALIDATION-20261019-R29.md),
[r28 implementation and regression](docs/VALIDATION-20261019-R28.md),
[r27 recovery retry](docs/VALIDATION-20261019-R27.md),
[r26 native Goal experiment](docs/VALIDATION-20261019-R26.md),
[r25 native run checkpoint](docs/VALIDATION-20261019-R25.md),
[r23 preparation and provider observations](docs/VALIDATION-20261019-R23.md),
[r22 timeout and candidate improvements](docs/VALIDATION-20261019-R22.md) and
[r21 terminal report and UUID repair](docs/VALIDATION-20261019-R21.md), with
earlier batches in the [2026-10-18 validation report](docs/VALIDATION-20261018.md).

For explicitly authorized new Goal clients, use startup-v3 with pinned duties
for all three lanes and exact native `/goal` conditions for both Claude lanes.
Require native SET, the actual probe and automatic hooks before adoption.
Preserve legacy startup-v2 clients and attempts. A Goal prompt, configuration,
SET record or later model activity alone does not prove continuous operation,
message acceptance or efficiency; see [Goal evidence](docs/NATIVE-GOAL-OBSERVATION.md).
The r27 native clients recorded Goal SET and a first model-stream chunk without
any assistant record or startup probe. A later `No assistant message found`
error belonged to a separate Stop prompt-hook request; do not attribute that
error to the original request or call it an authentication failure. Preserve
request types and original logs, require actual probe/results for adoption,
and retain the original startup deadline. Coexisting configuration-write errors
do not establish causation or authorize weakening isolation; r26 completed both
probes after the same settings EPERM. Missing request-observation files leave
response semantics unknown. A stalled startup
remains missing business evidence, not a successful pressure sample.

Pin client versions separately from controller versions. Verify the actual
native process and journal version after an authorized upgrade; changing an
entrypoint does not update existing clients. A native request's model identifies
what was requested, not which backend successfully completed it. Revalidate
observer coverage across client versions: LOADED with no response records cannot
override native request evidence. Goal retry notices are not successful task
execution, and Goal testing is not independent loop validation. Preserve original
attempts and deadlines; see [Claude version validation](docs/CLAUDE-VERSION-VALIDATION.md).

Original handshake member selectors may be a numeric `surface:<N>` or a UUID.
Canonicalize UUID aliases and allow repeated aliases only for the same member;
reject a UUID selector belonging to another member or any cross-member collision.
Resolve a UI alias only from the original gate's proof under that member's exact
original selector key, with its matching target UUID. Keep legacy UI selectors
equal to their original proof and preserve selected workspace, caller, pane,
provider, session and row-order checks. Never repair an old proof using current
inventory. Include the producer's UUID-keyed proof with a UI target ref in tests;
UI-only fixtures missed the r21 refusal. Passing that repair locally does not
change r21's failed result or establish live handshake success.

For v3, ordinary work in progress is not a reason to delay an authorized
coordination message in either direction. Submit ready information through the
bound supported input route; the receiving agent decides how to integrate it.
Do not pause native `/goal` merely to create an idle communication window.
Communication, task acceptance, write ownership and composer clearing are
separate operations. Keep fresh identity, draft, original-attempt and native
receipt checks; an accepted queue entry is not proof of consumption.
Reuse a valid handshake instead of running a write-and-clear bridge probe for
every message. Newly materialized controllers check initial capability read-only
against the exact prepared challenge, then retain the original single send,
nonce, ACK and native receipt. Active Claude work is allowed with two complete,
stable empty-composer observations; Codex uses authenticated input metadata.
Hold the original root-directory lock through receipt writes, send and ACK;
concurrent or previously started handshakes are observe-only. Preserve root inode,
native process/session and original transcript-prefix checks. Legacy receipts
retain their original clear-proof requirement. Local regression and simulated
transport pressure passed; live continuous-goal handshake remains unverified.
New v3 nonpilot campaigns register 192 independent ordinary-message slots across
six groups and four warm rounds per group: two arms of 96, four directions of 48.
The bound native clients use their exact private helper once at each specified
before/after business boundary. Pilot and cold runs add no samples. Refused,
queued and missing slots remain in the denominator; do not replace samples,
request extra ACKs or count task notices/callbacks. A short helper body still
uses a pinned MESSAGE_REFERENCE_V2 notice because the helper adds its header.
Notice receipt does not prove body reading. Boundary labels do not establish
active/idle or continuous-goal state; require actual native observations.
New freezes use metrics version 3 to attribute registered ordinary messages.
Exclude only the exact original native record backed by its unique sender
helper call, complete pinned body, original receiver binding and input fence.
Duplicate, changed or missing evidence stays unclassified; never exempt a
message prefix. Preserve original UUID bytes and hashes while comparing UUID
identity semantically; ambiguous original journal locations remain UNKNOWN.
Older metrics versions and frozen campaigns keep their original interpretation.
New isolated clients receive a pinned `mac_upgrade_body_read.py` command that
returns the complete verified body in their native tool result. The separate
observer must authenticate the original receiver journal, input-intent fence,
exact reader invocation and its complete successful matching result before
reporting `BODY_READ_OBSERVED`. This means exposure in receiver context, not
understanding, task acceptance or a native notice receipt. Queued notice,
body exposure, subsequent action and business correctness remain separate.
Truncated, yielded, duplicate, stale or unsupported tool records remain UNKNOWN;
bounded scan coverage and original record offsets must be reported. Do not send
extra messages or ACKs to improve the 192-sample denominator.
See [active communication and its measured limits](docs/ASYNC-COMMUNICATION-VALIDATION.md).
Continuous Goal requires explicit native activation and renewal evidence; r25
did not register those steps. Use the separate read-only
`scripts/upgrade_goal_observer.py` to inspect original Claude `goal_status`
attachments, never prompt text or queue banners. A clear sentinel with `met:true`
is not goal completion, and later model activity does not prove wake causation.
Restoring a Goal can reset iteration/time/token baselines without a new SET
record. Report discrete observations and UNKNOWN intervals; do not infer
continuous operation, message acceptance or business correctness. The observer's
local tests are not native pressure results. See
[native Goal observation](docs/NATIVE-GOAL-OBSERVATION.md).
These instructions update this experimental skill only. The designated
surface40 owner manages shared MAC and thesis-refiner releases. Frozen campaigns
retain their original controllers; documentation does not hot-reload clients.

The r19 field audit found finalized packs referencing missing role-map and
surface-inventory inputs. New v3 preparation now generates both with the original
harness before task-pack finalization and verifies their pinned contents, task,
workspace, time and assigned native identities. Packs include the original
startup adoption proof and an explicit executor reading responsibility. Inventory
titles are not provider authority; retain fresh live identity checks. v2 keeps
its original preparation sequence. Report/callback outputs remain future outputs.
This candidate passed local regression and multiprocess pressure; live callback
and business acceptance remain unverified. Never backfill an already dispatched
pack to conceal a missing input.
See [task-pack field findings](references/lessons.md#r19-task-pack-input-completeness).

New v3 task scopes also provide the exact frozen-controller callback command,
including the original pack path, after the report-freeze instructions. Executors
collect that one original invocation instead of discovering a callback CLI.
An uncertain result or pane contention does not authorize another send. This
addresses observed task-navigation overhead, not measured end-to-end efficiency.
For a first callback with no prior attempt, the generated controller may wait up
to 15 seconds on the original pane-lock inode before input. Task/target locks and
recovery attempts retain fail-fast behavior. After acquisition, recheck the pack,
report, availability, identity, journal and all held lock identities before
PREPARED. Timeout or changed evidence produces no input and no extra attempt.
This is one original invocation, never a resend or a recovery-key budget.
Validate this transformation in the actual materialized controller, including
concurrent processes and the unchanged native input boundary. Live pilot
acceptance and end-to-end efficiency remain separate requirements.

New bounded campaigns pin an overall deadline once at configuration; neither
polling, a new slot nor process restart resets it. Run an independent watchdog
against that same configuration before the driver. At expiry it must first
publish each private runtime's stop marker, then halt scheduling and preserve
all registered slots in the final report. Clock/boot ambiguity refuses new work.
Do not depend solely on a driver tick that may be inside synchronous work.
Preserve in-flight requests, original clients and leases; stopping future input
does not prove remote cancellation or resource release. Report watchdog errors
and observed stop time, not an unsupported hard real-time guarantee.

Independent checkers receive canonical request bytes through a read-only file
descriptor from unique private campaign scratch, removed before child execution
and cleaned on exit. Do not use `subprocess.run(input=...)` inside the private
Darwin audit: its anonymous write pipe has no authenticated filesystem path.
Never bypass this by allowing arbitrary FIFOs or swallowing `EBADF`. Preserve
checker pins, timeout, output validation and one invocation per action; a repair
does not reopen a failed historical attempt. See
[checker input isolation](references/lessons.md#r19-checker-stdin-isolation).

For v3, automatic workflow hooks run through the pinned advisory wrapper.
Unresolved callbacks, reservation, reask or review state do not block ordinary
tools or Stop. Legacy gate descriptions below are explicit diagnostics, never a
global session lock. This does not relax the sender's identity, original-attempt,
native reception or input safeguards. Verify actual automatic invocation in
each live client separately from configuration and manual hook tests.

Pressure acceptance requires both safety and successful independent work.
Report every attempted reservation, semantic conflict and registry-lock timeout;
zero double owners alone is insufficient. Short registry transactions may wait
up to five seconds. The separate v3 run-state lock waits within the original
remaining work budget for new claims/preparation/dispatch; Stop, halt or unknown
clock refuses new work. Historical observation, acceptance and closeout have a
separate 300-second lock-acquisition cap, preserving their original gates.
Node action locks, v2 and initial creation remain fail-fast. Wait only for the
original lock inode; run the fresh action gate after acquiring it, then execute
the transaction at most once. No dispatch or callback is retried by this wait.
Nested acquisition follows runner → transport → coordinator → registry;
recheck the original work budget immediately before publishing a new claim.
Publish a new dispatch intent only after acquiring the run-state lock and
rechecking PREPARED; keep existing intents observe-only. The two evidence writes
are serialized, not crash-atomic. See [pressure and experience
controls](references/lessons.md#pressure-and-field-experience).

Private Darwin discovery retains paired kernel BSD snapshots and the original
errno at each read. A complete, untruncated BSD basename or absolute kernel path
may exclude a known unrelated row that then returns exact ESRCH/zombie. Candidate
paths override earlier basename exclusions. Permission errors, unknown or partial
reads, and PID/birth/parent/name drift refuse before considering a later exit;
selected native clients retain the original strict process/argv/TTY checks.
Stress every read boundary with real short-lived processes and preserve failed
scan counts. A traceback without its PID/path does not prove an unrelated row, and this discovery fix alone
does not establish successful live collaboration.

The private native target lookup intersects the bound controlling TTY's Darwin
`PROC_TTY_ONLY` inventory with the kernel `PROC_UID_ONLY` inventory. Each scope
sample brackets the UID read with equal complete TTY PID sets; the final sample
must preserve both the complete TTY set and its UID intersection. This excludes
foreign root login parents by kernel scope, never by ignoring a permission error.
Daemon client discovery retains its full-UID inventory. Pin the canonical `/dev`
character device's path, device/inode, rdev, mode and ownership before and after
enumeration. Darwin devfs updates ctime during ordinary terminal output; activity
timestamps are not device identity. Retain mode/owner checks for real metadata
changes. Verify every selected UID member's
BSD TTY, and close its original identity. Any such member exit, permission error,
unknown read, identity drift or observed set change refuses; there is no fallback
scan or automatic replay. These discrete reads are not an atomic kernel snapshot.
This establishes target uniqueness for this UID inside the exact TTY, not global
uniqueness of environment values on other terminals. Selected clients retain complete native process, executable,
private-root, session, UUID and transcript checks. Snapshot executable basenames
once per lookup; never cache the selected executable's authority checks.

On the measured Darwin 25.5.0/arm64 ABI, an initial TTY member whose first BSD
read returns ESRCH may be excluded only with the independent sysctl SZOMB proof:
fresh self calibration, four stable identity/status observations and complete
TTY/UID closure. Reaped, live-to-exit, unknown, permission or identity-drift cases
still refuse. This exception does not relax selected native-client authentication.

Startup content checks may use at most three workers for pins declaring large
files while the caller checks small files. Each invocation still hashes the
complete actual contents and checks before/after descriptor and path identity.
No digest or stat cache is permitted. Drain all parallel checks before raising,
preserving original exceptions; small batches retain serial fail-fast behavior.
Measure local verification speed separately from end-to-end collaboration gains.

Within one Coordinator construction, the Codex reader may be reused only with
its second original-file metadata read and both EOF-pin checks retained. Start
may form observational status from the Coordinator just created, with a fresh
header equality check and fresh state/ready reads. Independent status and every
later action retain fresh authentication; this is not permission to cache a
digest, skip an input gate or reuse authority across commands. Apply the same
control-overhead changes to both benchmark arms and keep their original clocks.

A private campaign may reuse the runtime it fully validated earlier in the same
validation invocation for its immediate adoption check and pure session binding
comparisons. Still authenticate the adoption proof afresh. Public live gates and
each NativeReader retain independent complete runtime/adoption validation; no
cross-action cache, caller-provided bypass or input gate is removed. Removing
the redundant intermediate scan removes one transient-change observation point,
so do not claim equivalence for every concurrent mutation interleaving. The
measured four-pair local helper improvement is not end-to-end collaboration
evidence; see the r22 report for method, failures and limits.

Preserve structured exception chains and traceback frame locations in runner
CLI refusals, without local variables or source lines. An unexplained filesystem
error is not a transient provider failure; record the last published artifact and
diagnose it before a new experiment. A passing read-only reproduction does not
retroactively repair the original attempt or authorize its replay.

Private bridge probes retain the exact pre-read/final screens and each cleanup
screen, parsed body, prefix match and screen-only busy classification. Keep the
original ownership check, interrupted-Bash boundary and key budget unchanged.
A later screen that parses successfully cannot explain an earlier hash-only
refusal; preserve the failed probe and distinguish diagnosis from a root-cause fix.

Native readiness is only a startup result. A later original Codex
`event_msg/task_complete` with an error or native `event_msg/turn_aborted`
must halt the experiment with its pinned native record, preserving sessions,
completed measurements and every registered slot. An interrupted turn may leave
a successful background command completion without any supervisor continuation.
That tool completion neither cancels the interruption nor proves business start.
Record the native reason without attributing its source to the user or provider.
The driver observes at most 256 records per tick; unread or partial records
hold advancement without extending the registered watchdog. A crash reuses the
first failure evidence, never retries input. See [native failure closeout](
docs/ISOLATED-UPGRADE-RUNBOOK.md#r8-native-provider-failure-and-bounded-closeout).

v3 adoption stores UUIDs without UI refs. The original C34 task pack still uses
a `surface:<N>` supervisor and callback target. Resolve that ref only from the
original pinned identity gate, checking both native roles/sessions and their
workspace, surface and pane UUIDs. Callback evidence must match the verified
pack target. Unmapped UI refs cannot establish that a sender targeted an
unrelated receiver. Regression fixtures must preserve the real UUID-only shape.

v3 live identity and campaign-to-native receipt checks compare parsed UUIDs:
cmux may return uppercase while frozen campaigns use lowercase. Journal lookup
uses the pinned controller's original uppercase caller; disarm retains original
marker paths and caller spelling. Never normalize stored evidence or reservation
hashes. This never relaxes exact process, native session, TTY, transcript, fence,
receipt-to-attempt or private configuration checks. Before
starting a permitted run, inspect the top-level `status --run RUN` state;
`NOT_STARTED` allows the original start, while `INCOMPLETE_START` preserves the
partial attempt and forbids replay. See [r10 startup findings](
docs/ISOLATED-UPGRADE-RUNBOOK.md#r10-uuid-case-and-unstarted-status).

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
   refresh a healthy handshake mid-command. Coordination messages may arrive
   during work; a new formal task still needs its own scope and reservation.
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
  process/session/transcript and a fresh EOF fence at the original
  NATIVE_INPUT_INTENT (Codex) or PASTE_INTENT (Claude).
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

执行[原生投递与有界等待](references/verified-compose-delivery.md)：原 NATIVE_INPUT_INTENT（Codex）或 PASTE_INTENT（Claude）新鲜 EOF fence 后的完整 native user 才确认收到；排队仍 pending。当前 Codex 原生输入无重发或补键预算；只有符合原控制器条件的历史终端尝试共用一次补键。封口保留 task-bound 诊断与零输入核收。默认 idle Stop 不要求求派；另行保留用户明确授权的每60秒新 marker 主动求派及其配套 hook，直到主管答复、新派发或 operator stop。

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
