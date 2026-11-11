# Availability and shared resources

Use one task identity through dual, solo and recovered-dual work. The user must
authorize automatic takeover; that authorization persists, so it need not be
requested again after every failure. A peer's assertion is not authorization.

## Retryable Claude API failures: evidence before takeover

For retryable failures such as a confirmed 502/504/524, retain the original
session and use a finite retry episode. Record its observation/attempt budget
before retrying; allow time for a completed observation at or beyond the
300-second minimum. Retry only after the preceding attempt has actually ended,
with at least 60 seconds between attempts, respecting a longer provider retry
delay. Internal client retry counters are not supervisor attempts. Never resend
an accepted or uncertain task nonce or add another call behind an in-flight one.

Start the consecutive-failure clock at the first actual API failure, not at task
dispatch, a silent screen or the start of a local wait. Bind the evidence to the
same task and executor: first/last failure times, last actual success time
(unknown if not observed), actual attempts and outcomes, request/error IDs when
available, and the original logs. Every observed API outcome in that episode
must be a retryable failure; any actual API success
resets the clock. Success here means an actual successful API response, not a
local helper's exit code, and does not by itself prove task completion.

Declare this executor temporarily unavailable for retryable API failure only
when a fresh failed attempt establishes `last_failure - first_failure >= 300s`,
no intervening API success exists, and the current attempt is terminal. Re-reading
an old error after five minutes does not qualify. A static screen, queued input,
unknown delivery/outcome, a short supervisor budget, or three retries cannot
establish continuous API failure. When an outcome or observation gap is unknown,
retain that uncertainty rather than count it as failure; resume a verifiable
failure episode from new evidence, without stitching short observations or
unknown periods together. Reaching a finite episode limit without the
required evidence leaves unavailability unproven, not permission to switch SOLO.
Continue independent authorized work while resolving the original attempt.

After the threshold is met, preserve the checkpoint, freeze executor dispatch
and writes, stop and verify the task sentinel, reconcile pending nonces and
shared operations, and verify the single-writer boundary. Only then use existing
user authorization for SOLO takeover through the availability lifecycle below.
Elapsed time grants neither shared-resource release nor a second writer; unknown
Word/NLM outcomes remain governed by their original resource receipts.

Authentication, billing, quota and no-account failures are separately classified
and permit no blind retry loop. A user explicitly stopping an executor or
withdrawing authorization is also a separate instruction, not evidence that
Claude crashed or met this retryable-failure threshold. Recovery preserves the
original session and requires a safe handoff plus a fresh actual handshake for
the next task; a successful API call alone cannot resume concurrent writes.
This minimum is an operating policy informed by limited recovery incidents,
not a statistically optimal outage detector.

This section changes operator policy only. It does not add a runtime timer or
new receipt fields to the existing availability CLI, install a hook, or migrate
an active task's pinned controller. Preserve the timing evidence as task artifacts
and verify the actual installed contract before any transition.

## Availability v2

`scripts/availability_contract.py` is the authority;
`scripts/executor_availability.py` is its compatibility CLI. `init --input`
accepts the keyword fields of `initial` after the CLI's `--task-id`: workspace/executor UUIDs,
current surface, TTL, protected paths, fallback policy and hashed user-authorization
record. Inspect `--help` for explicit state-file routing. Put that absolute state
path in the finalized pack's `availability_state`, set `availability_required=true`,
and register the artifact root. Dispatch, callback, round, handshake and sentinel
entrypoints must resolve that same state. Missing required state fails closed.

The diagnostic classifier recognizes numeric HTTP 402 independently of the
provider's message language. For an explicitly retryable 502/503/504/524,
`payload.retry_after_seconds` can carry the delay from the preceding original
response; the classifier waits for the greater of that delay and 60 seconds
after `last_attempt_at`. An explicit delay requires that original attempt time;
invalid, negative or nonfinite delays are refused.
This CLI calculation does not authenticate a supplied delay, issue a retry,
or replace the original native observer's evidence requirements. An exhausted
classifier count alone still does not establish the 300-second operating
policy above or grant takeover without the required original artifacts.

V1 is not silently interpreted as v2. `migrate --input` needs a hashed legacy
reference plus current identity/policy fields. It preserves the old bytes and
enters `EXECUTOR_DEGRADED` with dispatch disabled. Supply actual freeze/takeover
evidence or a safe handoff and fresh handshake before advancing. Migration is
not proof of availability.

Transitions require hashed artifacts, not prose alone:

- `ACTIVE -> EXECUTOR_DEGRADED|UNAVAILABLE_AUTH|UNAVAILABLE_BILLING|UNAVAILABLE_QUOTA`:
  retain the actual failure.
- `... -> SOLO_TAKEOVER`: current user authorization, checkpoint, original UUID,
  pending nonces, resume phase, protected paths, frozen executor, stopped sentinel
  and no executor writers. Solo reviews are `solo_self_review`.
- `SOLO_TAKEOVER -> HANDOFF_READY`: completed phase boundary and no writers.
  Direct unavailable-to-handoff is allowed after observed recovery when no solo
  work took place; do not invent a solo history.
- `HANDOFF_READY -> ACTIVE`: fresh same-task handshake after the handoff plus
  matching workspace/executor UUID. Surface numbering may be refreshed with a
  fresh identity observation, never by choosing an unrelated executor.

An expired observation requires `reobserve`: it refreshes identity/TTL but keeps
SOLO or unavailable state unchanged. Delivery queued at receiver, detector
uncertainty, late callbacks and a supervisor's insufficient budget are not
executor billing failures. Do not resend a possibly accepted nonce.

## Shared resources

`scripts/resource_broker.py` provides durable FIFO tickets and process-group
ownership backed by SQLite and real flock checks. All tasks sharing an account
must use the same root/account key and pinned external transport locks.
Different notebooks or browser targets do not create extra NotebookLM quota.
This implementation supports POSIX hosts; it does not claim a Windows lock backend.

Configure `word-zotero` once with the application's existing automation lock.
Use `request -> grant -> run -> release`, passing the returned ticket/token.
An expired ticket stays owned until reconciled; file existence, TTL expiry or a
dead coordinator does not grant ownership. Only the exact waiting owner may
`withdraw` an unused queue entry. Scope resource messages to the task and surface
UUID; do not borrow another task's executor session.

Word release requires a hashed, task-bound observation with integer
`documents=0`, integer `windows=0`, `modal=false`, `pending=false`,
`zotero_current_doc=false`, and `zotero_current_window=false`, plus empty owned
processes and actual free external locks. `-1743` is UNKNOWN, not zero; do not
reset permissions or close unrelated documents merely to satisfy the receipt.

NLM release instead requires `in_flight_requests=[]` and a hashed terminal
receipt bound to the exact request and lease, with remote terminal state and
empty process group. A no-invocation proof is allowed only when the broker itself
recorded zero invocations. Client exit alone cannot settle a browser/server request.
An uncertain request retains its account quarantine and exact lease until resolved.
Return the network window before doing offline adjudication.

NotebookLM task allocation, dedupe and quota models live in `thesis-refiner`;
this broker serializes resource ownership. Default collaboration batches are at
most two queries or ten minutes, then hand over after the active request settles.
Reserve 20% of measured budget for final review/recovery; unknown compute balance
must stay unknown. Neither rule is permission to bypass an account's service limits.
