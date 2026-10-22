# Supervisor Protocol

The supervisor owns clarity, scope control, and final audit. It should make the executor's job concrete enough that the executor cannot accidentally change production systems or solve a different problem.

## Pane-First Source Acquisition

When reviewing another agent's plan or result:

1. Read the visible executor pane.
2. Use the source path named in the pane.
3. Only then inspect repo files, plans, or transcripts.
4. If no source path is visible, answer with `SOURCE_NOT_FOUND` and ask the executor to name the artifact.

This prevents reviewing stale plans discovered by broad file search.

## Task Pack Fields

Every executor task pack should include:

- `TASK_ID`: stable short id.
- `ROLE`: usually `EXECUTOR`.
- `SUPERVISOR`: identity and pane id, for example `cc2:1.1 %45`.
- `EXECUTOR`: identity and pane id, for example `cc2:1.2 %46`.
- `ROLE_MAP`: path to `multi-agent-role-map.json` or an inline role map.
- `CONTEXT`: what triggered the task.
- `SOURCE`: plan or audit artifact to follow.
- `SCOPE`: files/systems the executor may touch.
- `FORBIDDEN`: files/systems the executor must not touch.
- `LOCAL_FIRST`: required local tests, mocks, Docker preview, or dry-run.
- `NEEDS_AUTH`: production/VPS/API-key operations requiring explicit user approval.
- `VERIFY`: exact verification expectations.
- `REQUIRED_SKILL`: absolute path to the skill the executor must read and obey
  before inspecting or changing source.
- `REPORT`: artifact path for the executor report.
- `CALLBACK`: exact callback string the executor must emit when done or blocked.
- `COMPLETION_CALLBACK`: separate terminal callback template containing the
  current task id, a fresh completion nonce, and the absolute report path. This
  is not the handshake `PREFLIGHT_ACK` template.
- `BRIDGE_TEST_EVIDENCE`: the preflight or bridge-test output proving the supervisor can read/reach the executor pane.

Generate a skeleton from the Mac harness when possible. The artifact root must be
an explicit absolute path; do not omit it or let the harness derive one from the
caller's cwd:

```bash
python3 <skill-checkout>/scripts/mac_harness.py \
  task-pack --task-id <task-id> \
  --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
python3 <skill-checkout>/scripts/mac_harness.py \
  finalize-pack --task-id <task-id> \
  --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
```

The scaffold is deliberately `draft=true`; finalization verifies scope, callback,
authorization provenance, and every extracted source path before setting
`draft=false`. Do not dispatch from a hand-written prompt or a draft pack.

The executor must read and obey the `multi-agent-collaboration` skill before
implementation. It writes the report artifact first, then actively sends the
task pack's completion callback to the bound supervisor surface. A report file,
visible `DONE` text, or sender exit code is not a callback. If the sender returns
`DISPATCH_UNCONFIRMED`, the supervisor waits for the same nonce within the
bounded late-ACK grace window only when the transport state is
`DELIVERY_UNVERIFIED_BY_DETECTOR` or `DELIVERY_QUEUED_AT_RECEIVER`. A protected
draft or an actual pre-input refusal preserves the original attempt. Ordinary
work in progress is not itself a refusal and no state authorizes a blind retry.

Dispatch the finalized executor task only through
`cmux_bridge.submit_task_pack(surface, prompt, task_pack_path, marker=nonce)`.
When the executor invokes the bridge from a shell, it must use an explicit CLI
subcommand such as `submit-task-pack` or `submit-completion-callback`; running
the module without a subcommand is only a diagnostic/self-test and is not
evidence of delivery.
The prompt must contain the absolute `TASK_PACK`, the canonical
`REQUIRED_SKILL`, `READ_AND_OBEY_REQUIRED_SKILL_FIRST`, `CALLBACK_TARGET`, and
the exact completion callback. Raw task prompts are blocked. Completion is sent
only through `cmux_bridge.submit_completion_callback(task_pack_path)`, which
writes the report-hash-bound completion receipt after confirmed delivery.

## Handshake Contract

Before plan consensus or executor dispatch, the supervisor must prove a current-task
handshake. Use the Mac harness with the same explicit artifact root:

```bash
python3 <skill-checkout>/scripts/mac_harness.py \
  handshake --task-id <task-id> --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
python3 <skill-checkout>/scripts/mac_harness.py \
  validate --task-id <task-id> --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
```

The first supervisor line must be equivalent to:

```text
HELLO_FROM_SUPERVISOR TASK_ID=<id> SUPERVISOR=<identity> EXECUTOR=<identity> ROLE_MAP=<path> CALLBACK=PREFLIGHT_ACK
```

The executor must answer with the nonce-bound compact ACK:

```text
PREFLIGHT_ACK|<id>|<agent:identity>|READY|INLINE|<nonce>
```

The compact machine ACK is also valid when all values match exactly:

```text
PREFLIGHT_ACK|<id>|<agent:identity>|READY|INLINE|<nonce>
```

The harness writes the expected nonce line into `handshake-receipt.json` and sends
only the challenge fields plus a format template. It does not send a fully
value-filled ACK line, so the harness cannot match its own prompt.
`handshake-receipt.json` is PASS only when the current executor pane shows the
complete task, provider, nonce, and `INLINE` marker in a genuine assistant response
block. Human-readable readiness, `HELLO_FROM_EXECUTOR`, tables, prompt echoes, or
report files without the callback line are not enough. Pane output alone is not
enough for final claims; cite `validation.json` or `receipt`.

## Acceptance Trio

Before dispatching any executor task pack, the supervisor must have current evidence for:

```bash
python3 <skill-checkout>/scripts/mac_harness.py setup-check
python3 <skill-checkout>/scripts/mac_harness.py preflight \
  --task-id <task-id> --executor-surface surface:<N> --executor claude \
  --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
python3 <skill-checkout>/scripts/mac_harness.py bridge-test \
  --task-id <task-id> --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
python3 <skill-checkout>/scripts/mac_harness.py handshake \
  --task-id <task-id> --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
python3 <skill-checkout>/scripts/mac_harness.py validate \
  --task-id <task-id> --artifact-root /absolute/path/handoff/multi-agent-artifacts/<task-id>
```

If any command fails, do not dispatch. `bridge-test` is intentionally non-submitting: it types a short token into the executor pane, clears the input, and does not press Enter. `observed_in_pane` is advisory because some TUIs do not expose unsubmitted input to `capture-pane`. Do not use bridge-test as a readiness signal; readiness is proven by `handshake-receipt.json.executor_ack=true`.

## Submission Confirmation (Mandatory)

Follow [verified native delivery](verified-compose-delivery.md) through the original
guarded bridge. Pin the exact receiver workspace/surface/process/session/transcript,
full payload and fresh EOF fence at the original input intent. Current v3 Codex
uses NATIVE_INPUT_INTENT and its authenticated control socket: one turn/steer
with the fresh expectedTurnId when active, or one turn/start when idle. Never
inspect its composer or send terminal input. Claude uses PASTE_INTENT, one paste,
two complete stable draft reads and one supported Enter.

Only a new complete native user after that fence, exactly equal to the payload,
establishes NATIVE_RECEIVED. Preserve whitespace and newlines. Screen activity,
ACK, exit zero, empty compose, queue text and Claude queued_command do not confirm
reception. Missing original binding/fence cannot be backfilled.

Unknown or queued delivery preserves the same attempt. Use its zero-input
reconciliation entrypoint. Current Codex native input has no recovery-key or
resend budget. Only an eligible historical terminal attempt under its original
controller shares at most one additional key across automatic and explicit
paths. Do not manually paste, clear compose,
press keys, switch controllers or start another dispatch.

Read frozen reports through authenticated active markers with bounded supervisor
PostToolUse report discovery (`scripts/cmux_supervisor_report_guard.py`). Discovery is not delivery,
acceptance or disarm. Independently review the original artifacts and keep missing
receipts visible. A bound executor can honestly stop in WAITING_SUPERVISOR; it need
not run a watcher or redo accepted work. Continue independent authorized work.

The helper supports inventory/read-only inspection. Its direct ordinary-message
routes are allowed only through an absolute executable whose complete rendered bytes,
same-release adapter and Python pass the guard. It uses the sole guarded bridge;
formal task packs and callbacks retain their dedicated entrypoints. Shell/env
wrappers, redirection, nesting and raw senders remain refused.
The canonical native-proof contract also governs the diagnostic states below;
screen classifications never authorize a resend or establish native reception.

## Role Map Schema

The role map must be machine readable and contain at least:

```json
{
  "task_id": "string",
  "session": "cc2",
  "supervisor": {"identity": "cc2:1.1", "pane_id": "%45", "agent": "codex", "detected_agent": "codex", "agent_match": true},
  "executors": [{"identity": "cc2:1.2", "pane_id": "%46", "agent": "grok", "requested_agent": "grok", "detected_agent": "grok", "agent_match": true, "role": "executor"}],
  "panes": [{"identity": "cc2:1.1", "pane_id": "%45", "role": "supervisor", "path": "..."}],
  "updated_at": "ISO-8601"
}
```

## Production Gate

Use this default rule:

```text
No VPS, production DNS, production database, API key, model route, proxy, or account-pool change is allowed unless the user explicitly approves that production operation after preview/audit.
```

## Callback Format

Use one line so the supervisor can scan quickly. Standard messages:

```text
HELLO_FROM_SUPERVISOR TASK_ID=<id> SUPERVISOR=<identity> EXECUTOR=<identity>
PREFLIGHT_ACK|<id>|<agent:identity>|READY|INLINE|<nonce>
EXECUTOR REPORT | TASK_ID=<id> | STATUS=DONE|BLOCKED|NEEDS_AUTH | EVIDENCE=<path-or-url>
GROK_PLAN_REVIEW | TASK_ID=<id> | ROUND=<R1|R2|R3> | VERDICT=PASS|PASS_WITH_CHANGES|FAIL
```

The report file should contain changed files, commands run, results, risks, and next action.

## Transport State Attribution (Mandatory)

`DISPATCH_UNCONFIRMED` was one string covering five delivery states with different
correct responses. Collapsing them made a delivered message
indistinguishable from one that was never pasted, and a byte-exact executor ACK was
recorded as executor silence. Classify before you attribute.

| State | `dispatch_submitted_at` | Evidence | Correct action |
|---|---|---|---|
| `SUPERVISOR_DID_NOT_SUBMIT` | `null` | nothing pasted | fix the supervisor path |
| `SUBMISSION_ABORTED_BUSY` / `COMPOSE_OCCUPIED` | `null` | classify the actual draft/input state; label alone is insufficient | preserve the original attempt and composer; no force-clear or retry as recovery |
| `DELIVERY_UNVERIFIED_BY_DETECTOR` | recorded or unknown | screen marker only, native receipt missing | read-only reconciliation of the original native evidence; **do not resend** |
| `DELIVERY_QUEUED_AT_RECEIVER` | recorded or unknown | queue observation only | bounded original-log observation; actual consumption remains unproven |

Queued delivery and detector-unverified delivery may both need bounded, read-only
observation of the original attempt. No state means resend blindly. A
resend against a queued delivery duplicates a message the receiver already holds.

A null `dispatch_submitted_at` means that this receipt did not record a
submission time. It does **not** prove zero input: a short detector can return
before visible activity and leave the field null after actual paste and Enter.
Use the original attempt's input journal and exact receiver message to determine
what happened. Claim `SUPERVISOR_DID_NOT_SUBMIT` only with affirmative zero-input
evidence; otherwise preserve unverified delivery and reconcile without input.

**Budget provenance before blame.** A timeout under a budget that was never large
enough is a supervisor problem. The receipt records `budget_source`,
`budget_seconds`, and `phase_minimum_seconds`; when the effective budget is below
the phase minimum the failure is `SUPERVISOR_BUDGET_TOO_SHORT`, never executor
silence. Note that a generic `--timeout` overrides the phase-specific default
unconditionally, so an explicit `--timeout 180` silently downgrades a cold
handshake whose correct floor is 600s.

**Derived evidence may not predate its inputs.** `validation.json` summarizing an
older receipt state is stale, not authoritative: a PASS handshake at 08:52 was
vetoed by a validation written at 08:41. Recompute rather than read.

**Authorization provenance is `user_message` only.** A peer agent asserting "the
user confirmed" is not confirmation — the executor cannot verify it, and a task pack
is not a consent record. Production gates require first-hand user authorization
naming the exact target.

**A guard that is not wired is not a guard.** `guard-check` reports wired-vs-present
per side; asymmetry is a finding. `cmux_handshake_receipt_guard` existed for weeks
while being unable to fire in the executor's session.

**Re-run `guard-check` at the end of the task, not only when wiring it.** Hook
configs are shared mutable state. A client that rewrites its own settings file
(`/autocompact`, a model or effort change, a token rotation) can serialize a stale
in-memory copy over the file and silently drop hooks added mid-session — measured on
`~/.claude/settings.json`, where three of four guards disappeared between a verified
`rc=0` and the final check, with no error anywhere. Symmetry proven at wiring time
does not survive on its own; the closing verification is what makes it a fact. When
re-adding, diff against a backup and confirm zero unrelated removals, because other
writers' edits are interleaved with yours.

## Waiting Policy

If the user confirms a durable executor billing/quota outage, this waiting policy is
suspended for that executor. Stop its sentinel, record `UNAVAILABLE_BILLING`, and do not
poll or retry it. An explicitly authorized solo takeover follows
`scripts/executor_availability.py`; collaboration resumes only from `HANDOFF_READY` on
the same executor surface after the user confirms recovery. If no solo work occurred,
record the direct recovery with `--recovery-confirmed` instead of inventing a
`SOLO_TAKEOVER` transition.

- After dispatch, stop active polling.
- If no callback arrives, use a low-frequency watchdog suited to task size.
- Default stable long-task watchdog interval is 7200 seconds with three unchanged
  checks. Use 1800 seconds for medium-risk implementation phases and 300-600
  seconds only during a bounded high-risk cutover where a faster signal materially
  improves rollback. These are the values in
  `policies/multi-agent-policy.json`; prose that disagrees with the policy file is
  the stale copy. Use the single-instance
  `scripts/cmux_executor_sentinel.py`; it reads the existing executor surface and
  wakes the supervisor only for API/context/blocker/completion signals or a
  normalized screen signature with no progress. It must never create a session,
  change model, or automatically prompt the executor.
- Bind each sentinel to the current task-pack callback token with
  `--callback-token`; a historical DONE/BLOCKED from an earlier phase must not
  terminate the current watchdog.
- Match a screen terminal marker only when `DONE:` or `BLOCKED:` begins the line
  and the current callback token appears on that same line. Never combine a
  historical marker with a token found elsewhere in a supervisor prompt.
- Screen terminal text is a notification candidate, not an acceptance or stop
  condition. Keep the sentinel alive until the supervisor audits the report and
  explicitly stops it.
- A callback is not complete when printed only on the executor screen. Ordinary
  milestone messages use the pinned guarded helper after helper-parity passes;
  parity failure has no raw or alternate-controller fallback. Formal completion
  always uses the pack-bound submit_completion_callback entrypoint, after the
  report artifact is written and frozen.
- For implementation completion, the active callback is the task-pack-specific
  `DONE|<task-id>|<completion-nonce>|REPORT=<absolute-path>` (or matching
  `BLOCKED|...`) form. Do not substitute `PREFLIGHT_ACK`, a historical callback,
  or a report-only status. The supervisor binds the callback to the current
  nonce and report SHA before accepting completion.
- **The helper's exit status does not prove acceptance.** DISPATCH_UNCONFIRMED
  does not establish non-delivery; classify from the original intent, exact
  native evidence and receipt. A queue or detector label is not proof of native
  reception, and a missing timestamp is not proof of zero input. Preserve the
  same task/nonce/controller and reconcile read-only. No classification grants
  a fresh-nonce reissue. Completion additionally requires the task-bound callback
  receipt and independent business acceptance.
- Store watchdog state and its PID under
  `/tmp/multi-agent-collaboration/sentinels/<TASK_ID>.*`; allow only one watchdog
  per task. Stop it after a terminal callback or task cancellation. A
  `SENTINEL_STOP_REQUESTED` acknowledgement is not proof of termination: verify
  the recorded PID exited and the state no longer advances before declaring the
  watchdog stopped.
- On Mac, prefer supervising the long-lived sentinel with `launchctl`, but first
  require a real `cmux read-screen` probe from the launched process. Some cmux
  builds reject launchd children with `Access denied - only processes started
  inside cmux can connect`; a launchd job in that state is not a working
  sentinel. Stop the failed job instead of retrying it indefinitely.
- When launchd cannot access cmux, start the sentinel from an existing cmux
  descendant process (never by creating a replacement executor). A detached
  fallback is acceptable only when startup proof shows all three: a live PID,
  a freshly updated state file without `SENTINEL_ERROR`, and a successful real
  executor-screen classification. Record the launchd rejection in the task
  evidence. Continue to enforce one sentinel per task and stop it explicitly
  after the callback is audited.
- A status request from the user should be answered with current known state, then continue.
- Send authorized coordination information when ready, including while the
  executor is working; the same rule applies to executor-to-supervisor messages.
  Use the bound supported input route and let the receiver coordinate its work.
  Do not require an idle turn or pause native `/goal` just for communication.
  Preserve the actual composer and original attempt; receiving information does
  not transfer write ownership or accept a second formal task. See the
  [active communication contract](../docs/ASYNC-COMMUNICATION-VALIDATION.md).
- Reuse the existing context-bearing Claude Code session; session creation is not a retry strategy.
- Default Claude Code to `claude-opus-5`. Verify the bound session's visible model state before dispatch and, when needed, send the exact command `/model claude-opus-5` in that same session. Do not switch models to recover from an API error.
- Classify `API Error` before retrying. Explicit authentication, billing, quota or no-account failures permit no blind retries. Retryable Claude API failures use a finite same-session episode with at least 60 seconds after the previous attempt ends. Temporary unavailability requires at least 300 consecutive seconds from the first actual failure to a fresh failed attempt, with no intervening API success; any actual success resets the clock. Three retries, silence, queued input and unknown outcomes cannot substitute for that evidence. Follow [the failure-window rules](availability-and-resources.md#retryable-claude-api-failures-evidence-before-takeover), including unresolved outcomes and finite budgets. Continue independent work during recovery. After the threshold and current attempt's terminal state are verified, an already-authorized SOLO takeover still requires frozen executor writes, a verified stopped sentinel, preserved checkpoint and a safe single-writer boundary. A user's explicit stop or withdrawal is a separate instruction, not an API-outage diagnosis. Do not switch model, channel or session.
- Monitor automatic Claude compaction as a distinct state. Track its adjacent progress-bar percentage, start time, last-progress time, and unchanged checks; do not confuse it with the separate context percentage. Advancing compact progress is healthy even when slow. Two consecutive 300-second checks without compact progress emit `COMPACT_STALLED` for supervisor inspection.
- When the existing Claude Code context reaches 100% without active compaction, use the same-session compaction route within existing authority. An explicit compact failure ends executor retries and triggers the authorized solo path above. Preserve the original session and evidence; do not create a replacement. A successful compact still needs evidence that the original task resumed rather than merely returning to an idle prompt.
- Never use `/clear`. It discards the executor's accumulated task context and
  invalidates same-session continuity. A tool call that returns no output must be
  audited with a read-only postcondition check, not retried after clearing state.
- Other executor blockers require classification and preserved evidence. Continue independent authorized work; use the same controlled solo transition when applicable. A blocked executor is not an instruction to abandon the user's task. See [availability and shared resources](availability-and-resources.md).
- Apply the incident regressions in `sentinel-and-compaction-incidents.md`; in particular, a spinner-only Claude task line is ACTIVE, while the first observed idle prompt is immediately actionable.
