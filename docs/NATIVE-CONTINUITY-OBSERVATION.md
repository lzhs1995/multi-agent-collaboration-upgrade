# Source-authenticated native continuity observation

2026-10-21. `scripts/upgrade_continuity_native.py` is an external read-only
adapter to the complete original MAC `collaboration_continuity.py` evaluator.
It does not replace that evaluator, send input, call a provider, retry a turn,
spawn an agent, transfer a role, consume a reply or release a resource lease.
Its result is an advisory decision, not proof that those actions occurred.

## Native outcomes, not caller labels

One registered task retains the externally adopted original 1+2 identities,
their fixed native roots, device/inode, metadata, model evidence and EOF fences.
Registration is explicit and requires `--register-private-continuity` in the
original private runtime. The external publisher pins the original native user
authorization records and a single task-bound registration. Its fences cannot
be reset by registering the same task again. It does not change shared CURRENT,
hooks, original sessions, old campaigns or existing lease ownership.

Codex success requires its original `task_started` turn, enrolled model,
`final_answer` assistant message and matching `task_complete` final text.
Its terminal structured `error.codex_error_info.http_status_code` is an API
error. The original native CLI's externally tagged
`error.codex_error_info.http_connection_failed.http_status_code` is also
recognized, but only with the exact single outer and inner keys. Both forms
require a concrete integer in400–599. String/boolean/out-of-range statuses,
unrecognized or ambiguous tagged structures, string errors and canonical
aborts remain UNKNOWN; message text never supplies a status.
An internal error/retry notification is not another completed attempt.

Claude uses its original session's user/assistant/tool-result/system/attachment
parent chain. Native hook/deferred-tool/queued-command attachments inherit the
already authenticated parent; their contents never become API outcomes or user
authorization. Unknown links, foreign sessions and sidechains are not adopted.
Complete enrolled-model text or tool-use messages count once per native
message ID, not once per thinking/tool chunk. A final synthetic API-error
message uses its concrete `apiErrorStatus`; seven internal `system/api_error`
records still do not count as seven attempts. Missing/string status is UNKNOWN.
Sidechain, queued input, quoted error text and tool output cannot establish an
outage. A real model change is refused rather than preserving old availability.

Prior original records establish an in-flight turn or parent chain when
registration happens during work. Only terminal records after the task fence
are outcomes in the registered episode. There is no caller-supplied parser
state, health label or copied transcript promoted to native evidence.
An unclosed pre-fence Codex historical turn may be superseded by a fresh original
start only before any post-fence activity of that turn has been observed. The
old missing terminal is not an API failure. Post-fence overlapping turns remain
refused, and a turn that really continues across the fence retains its state.
Thread-level settings/goal events and session metadata do not mark an older
turn active. Native response items retain implicit open-turn ownership; context
and event records with the matching turn ID do mark post-fence activity.

## Bounded and append-safe

Each observation streams at most 65,536 historical records and the explicit
episode record budget (default 256, maximum 65,536), with a 32 MiB combined
byte ceiling per lane and at most 256 derived terminal outcomes overall.
Original native readers retain their independent per-record size limit.
Partial JSON appends or an exhausted scan return PENDING and cannot invoke the
evaluator. A larger explicit record budget can complete the same original
interval; it does not skip to a later cursor or erase an unseen outcome.
The byte/outcome ceilings do not have an automatic reset/archive bypass.

Every scan checks native record chronology, non-future timestamps, original
metadata, device/inode, fences and each record's exact bytes/SHA. Evaluation
independently rederives the complete pinned interval before and after invoking
the original module. A later legitimate append does not invalidate that
already fixed interval; a fresh observation includes the append. A changed,
omitted or caller-authored outcome is refused before creating result output.
These are interval observations, not a claim of current or sustained capacity.

## Original evaluator and private boundary

Production invocation validates the complete original source/controller tree,
Python pin, private startup, external adoption and task authorization. It runs
the actual generated `scripts/collaboration_continuity.py` in a fresh isolated
Python process under the pinned macOS control Seatbelt profile, with the
existing cleaned child environment. The generated module checks its actual
kernel boundary before running. The adapter neither imports a substitute
evaluator nor patches original modules in `sys.modules`.

The complete original evaluator retains its existing classifications: three
distinct retryable terminal errors separated by at least max(60, qualified
server Retry-After), success resetting that sequence, permanent 401/402/403,
and unknown availability. The adapter does not authorize or perform retries.

The currently supported native terminal grammar preserves HTTP status but does
not expose authoritative server retry advice. In installed Codex0.160.0, the
[pinned error mapping](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/protocol/src/error.rs)
does not place its captured RetryAfter in ErrorEvent; the
[retry handler](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/core/src/responses_retry.rs)
retains an exhausted turn's advice in ephemeral thread extension state. Missing
journal advice is not proof that the server supplied none. Arbitrary error text,
internal retry counters or caller-added `retry_after_seconds` cannot fill that
gap. The known Claude terminal grammar also supplies no qualified advice.

When any lane's latest original terminal is retryable429/500/502/503/504/524/529,
the production public observer returns `ORIGINAL_RETRY_DELAY_PENDING` with the
exact observations and `retry_delay_pending_lanes`. It does not invoke the
evaluator or create evaluator intent/result artifacts, and it changes no native
bytes, registration fences, membership or input/lease authority. Direct
production evaluator invocation also refuses this incomplete timing authority.
Earlier retryable failures remain in the evidence; a later same-original
success clears pending and resets the evaluator's failure count. A later
concrete permanent failure retains the existing classification. Explicit
fixture-only evaluator comparisons are still offline tests, not production
timing qualification. Retryable pre-readiness registration, provider advice
observability and the separate operational300s sustained-failure threshold
remain unfinished; this pending boundary does not implement takeover.

For fully qualified nonpending intervals, the adapter publishes intent, pinned
outcomes, original stdout/stderr/process evidence and an immutable result under
the private external `live-control/native-continuity/<task-hash>` directory.
Evidence remains readable after operator Stop; Stop never authorizes new input.

`advisory_only=true` and `child_budget_enforced=false` remain explicit. The
original evaluator's `max_subagents=2` is not a cross-parent atomic allocator.
Neither a mode nor this registration permits replay, replacement clients,
shared writes, lease takeover or final task acceptance.

## Separate original-process diagnostics

`observe-processes --registration ABSOLUTE_PATH --registration-sha256 SHA256`
is a separate read-only entrypoint, not an API observation or mode decision.
It first validates the same task's immutable original registration, private
runtime, source, authorization and native-file bindings, then repeats that
validation before publishing. It does not register or discover another client.

Each original PID is read twice through Darwin libproc. Present records require
the original PID/birth, current-user ownership, a known kernel status and, for
non-zombies, the original executable path read twice. Present snapshots must
agree across both reads. PID reuse, changed birth/name/owner/path, short or
permission-denied reads, unknown status and mid-read changes are refused before
creating output. Two matching ESRCH reads yield `ORIGINAL_PID_ABSENT`; a stable
same-birth BSD zombie yields `ORIGINAL_PROCESS_ZOMBIE` without trying to recover
its unavailable executable. A stable non-zombie yields
`ORIGINAL_PROCESS_PRESENT`, not proof that it can accept input or reach an API.

These are sequential, per-PID point observations, not an atomic three-client
snapshot. They do not authenticate live argv, terminal/session/environment or
provider reachability; existing input gates retain those independent checks.
ESRCH means the original PID was absent at the two reads, not that the session
history disappeared or that a server request failed. A reaped zombie may also
be indistinguishable from absence. No signal, process launch, exact-session
resume, retry or replacement is performed or authorized.

An immutable private `process-observations/<uuid>/result.json` retains the
original registration and kernel/executable read evidence. Its explicit
`api_availability=NOT_EVALUATED`, `advisory_only=true`,
`takeover_authorized=false`, `child_budget_enforced=false`, `input_operations=0`
and `resource_releases=0` prevent confusing diagnostics with an evaluator plan.
Stop does not prevent this read-only inspection or authorize new input. The
ordinary `observe` entrypoint and original evaluator are unchanged, and no
process label is converted into a synthetic native API outcome.

Public tests use complete generated MAC0.4.58 registration fixtures and mock
only the libproc syscall boundary. They cover absence, same-birth zombies,
PID reuse, permissions/short reads, changing snapshots, unknown kernel status,
executable errors, task/registration substitutions and the stopped CLI's
rejection of caller status/model/PID/time overrides. They do not establish a
live process-loss recovery, model availability or native collaboration result.

## Public entrypoints

`register --request ABSOLUTE_PATH --request-sha256 SHA256` accepts only a pinned
`upgrade-native-continuity-register-request-v1` object with `runtime`,
`adoption`, `task_id`, `native_authorizations` and explicit
`automatic_fallback_authorized=true` fields. `observe --registration
ABSOLUTE_PATH --registration-sha256 SHA256 [--max-records N]` uses the actual
wall clock. Neither CLI supports fixture, time, status, model or root overrides.
Synthetic fixtures are available only through explicit Python testing calls.

The tests exercise original native-file identity and record parsing, complete
original evaluator TRIO/CODEX_SOLO/CLAUDE_PEER decisions, bounded incomplete
scans, later appends, in-flight registration, unknown failures, queue/quote/tool
counterexamples, private generated-controller kernel execution, mutated scope
and authorization refusal, stopped read-only inspection, source-unobservable
retry delay pending, same-original success reset and direct-verifier denial.
Their synthetic
journals and adoption observations are not live automatic-hook adoption,
real provider fault takeover, two real 60-second inquiry cycles or a benchmark.
Those acceptance gates remain separate and unfinished.

## R5 real-native finding and repair

The independent 7426 review found attachment links in the original Claude
transcript, which the fixtures had omitted: its 6000-record diagnostic window
counted 0 successes, recovering 241 when attachments were relabeled as system.
The repair adds attachment ancestry and pre-fence incomplete Codex handling,
with separate failing-before/passing-after tests and negative boundaries.
A subsequent read-only diagnostic used the same pinned 6000 original records
for both parsers: old 0, candidate 267, original attachment-as-system control
267, candidate attachment-excised control 0, with identical counted ID sets in
each matched comparison. This is a new append-time window, not the reviewer's
old window or a claim that all eligible outcomes were recovered. It emits no
native text and grants no adoption/availability authority. Evidence is
`maintenance/execution-20261021/continuity-r5-real-native-v1/result.json` under
the task evidence root, with source, native device/inode/offsets and exact hashes.
The frozen 7426 local full run still has one original 5-second Stop subprocess
timeout among 2361 tests; its exact isolated rerun and all four CI jobs passed.
Neither result removes R5's findings or substitutes for live acceptance.

## R6 fence-boundary refinement

R6 independently closed attachment ancestry on frozen9819 (same tail:7426=0,
9819=282,attachment-excised=0,lost=0), but retained P3 because thread-level
events after a fence falsely marked a crashed historical Codex turn active.
The repair now distinguishes turn-owned activity from thread-level events.
Its public collector test first reproduced the original overlap refusal;
the repaired23 parser tests also retain a post-fence response-item overlap
negative control. A bounded, source-pinned diagnostic of the original2078
rollout simulated six removed historical terminals without altering the log:
9819 refused all six thread-event fences; the repair accepted all six. Both
accepted the six direct next-start fences. This is parser diagnosis, not a
native outage, replay, adoption, or task-continuity acceptance.

## Original startup402 diagnostic, 2026-10-21

The separate `/Users/lzhs/uz` continuity exercise launched its original
1Codex+2Claude clients once under frozen16f1 and the complete fixed MAC0.4.58
private controller. The Codex first turn ended with a structured402 budget-pool
error before executing its startup probe. Both original Claude probes and their
automatic PreToolUse/PostToolUse observations subsequently matched, but the
original all-three adoption gate remained unpublished. No live handshake,
inquiry/reply consumption, child creation or benchmark sample is accepted.

The exact native error used the externally tagged form above, absent from the
previous synthetic fixtures. A public collector test reproduced UNKNOWN on
the old parser, then passed as API_ERROR402 after the exact-form repair. A
separate eight-case negative test retains strings, booleans, invalid ranges,
unrecognized tags and ambiguous extra fields as UNKNOWN. The relevant focused
run passed57 tests without failures/errors/skips,93.698seconds unittest/
94.388359seconds wrapper, with334 source pins unchanged. Result1949B SHA256
`e5c2b30e54010b66a5020dc308be15dece1d6f7ab6b2b2797d5346bd4a9296a0`;
12252B log SHA256
`e8e6236499f6a1beecd12bef7d7ad3828dc15ca4ff21364acbcec09f55b43e96`.
That run used explicitly dirty working source, not frozen16f1. The initial
focused command's two incorrect module names and failed result are preserved.

A read-only comparison used the same original90800B/11-record prefix:
frozen16f1 returned UNKNOWN; the repaired parser returned exactly one402
API_ERROR with the unchanged original turn ID and terminal-record SHA. Its
4341B result SHA256 is
`3cd2d2e6a4600726c32e3eab3c65de4688c16258826fa817c1824f2b0a042b17`,
under `maintenance/execution-20261021/continuity-tagged-http-native-v1/`.
This pure-parser diagnosis grants neither adoption nor native-reader authority,
invokes no availability evaluator and performs no provider request or input.

Later native user continuations occurred in that same Codex session after a
model-context change fromgpt-6-astra togpt-6.1-sol. The original inspector
refused `STARTUP_MODEL_UNCONFIRMED_OR_CHANGED`; those later turns are not
recovery of the original fixed-model observation. No model switch, provider
write, frozen-controller hot patch or replacement client was performed here.
Startup-time failure also exposes an unfinished contract boundary: continuity
and child-service registration currently require full original1+2 adoption,
so healthy Claude startup alone cannot establish the permitted peer mode.
Implement and verify a separately scoped startup-failure contract before
claiming that path; do not bypass the existing adoption gate or fill missing
benchmark samples with fallback work.

## Frozen624d and independent R7 closeout

Frozen624d subsequently passed2367 local full tests with unchanged322 pins and
all four [CI jobs](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39515882294).
Original reviewer43 R7 independently closed the fence P3:old9819 refused six
thread-event fences/new624d accepted six;both accepted direct task-start fences
and refused the six same-turn response-item or same-turn-ID event negatives.
The reviewer read both new assertion bodies and ran27 native tests successfully.
Its3224B report SHA256 is
`a53e61031a96e5d19e8cce3c72de8f694b38a3d1f27d61fe4bd6f8d8f28f4319`.
Events without turn IDs were an explicitly unverified observation, not a new
confirmed finding. Earlier PARTIAL/NOT_EXAMINED items and failed7426 evidence
remain separate; this is not an all-items independent review.

The new [whole-task child budget](NATIVE-CHILD-BUDGET.md) implementation is
subsequent working source, not part of624d or this read-only evaluator. Its
offline tests do not inherit the frozen full/CI result or establish live hook
adoption, model availability, role takeover or benchmark acceptance.
