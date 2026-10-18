# Lessons Converted Into Controls

| Failure | Maintained rule | Regression surface |
| --- | --- | --- |
| Context was lost by restarting a peer | Reuse exact context-bearing session; never clear | test_cmux_agent_panel_guard.py |
| Screen DONE never reached supervisor | Report hash + nonce-bound active callback + Stop guard | test_r3_hardening.py |
| Prompt echoed its own ACK | Parse assistant blocks, not pasted challenge | test_mac_harness_receipts.py |
| Old surface ref addressed another panel | UUID/workspace binding, fresh identity | test_workspace_uuid_alignment.py |
| Idle placeholder blocked real work | Exact known virtual suggestions; unknown text remains occupied | test_r3_hardening.py |
| Callback delivered twice | Classify delivery; never blind-resend | test_r3_hardening.py |
| Compact stalled for 15 minutes | 60-second compact samples, two unchanged checks, bounded intervention | test_cmux_executor_sentinel.py |
| Billing errors caused endless retries | Unavailable/solo/handoff state machine | test_executor_availability.py |
| Guard existed but did not run | Both clients wired; benign execution and negative entrypoint tests | test_install.py, test_r3_hardening.py |
| File-edit prose was parsed as a shell launch | Check tool identity first; cover command and cmd payloads in real hook processes | test_cmux_agent_panel_guard.py |
| Later PASS hid earlier objections | Explicit resolution links; fresh artifact hashes | test_r3_hardening.py |
| Concurrent evidence heartbeat was lost | Atomic state and concurrency tests | test_heartbeat_concurrency.py |

## Probe Discipline

Use the producer's actual schema. Null, false, absent and zero are different
observations. A SHA of empty bytes is evidence of empty bytes, not proof of a
failed read: independently check the read outcome and byte count. Never reconstruct
hashes from prefixes. A generated report cannot contain timestamps produced only
after that immutable report was read; use a separate readback receipt.

Run positive and negative controls with measured branch/mutation hit counts.
If the harness itself fails, correct the probe and preserve the failed observation;
do not turn a zero-hit mutation or an exception into a product defect or a PASS.

## Avoid Process Inflation

See [efficiency and task closeout](efficiency-and-closeout.md) for phase budgets,
bounded callback settlement, authorized SOLO continuation and acceptance records.

Apply [bounded collaboration](bounded-collaboration.md): freeze the business
report, reconcile the original send without replay, and stop task-specific
callback diagnostics after verified receipt and marker disarm. That protocol is
an operator rule, not a claim that a new runtime hook has been installed. Track
accepted findings and coordination overhead separately; two occupied panels do
not establish a speedup.

Scope each phase to a concrete outcome. Keep operational cleanup, product fixes,
historical evidence and final admission separate. Do not reopen settled findings
without new evidence. A repeated unproductive review is not progress. Use the
formal consensus floor only when formal multi-agent consensus is the requested
workflow; do not invent peer review during a SOLO task.

The detailed incident archive predates this public packaging and contains local
observations. It is not normative for model choice, input ownership or present
provider capability. SKILL.md and the tested public defaults take precedence.

## Pressure and field experience

The r7 experiment reached real readiness and automatic hooks in all three
clients, but an inspector incorrectly compared a Codex outer exec call id with
the inner CommandExecution hook id. Reconcile only the exact command and its
successful output within the original thread, turn and call/result boundary.
Negative cases must reject crossed turns, ambiguous commands, failed output and
changed pins. Original-log revalidation never upgrades a failed pilot result.
See `test_upgrade_live_inspect.py`.

The first 24-process contention experiment admitted only 389 of 2400 independent
reservations despite passing safety checks. An immediate nonblocking registry
lock refusal was incorrectly counted like a genuine session/scope conflict.
Distinguish these outcomes. Registry reserve/release now opt into at most five
seconds of acquisition waiting; action/driver locks retain immediate refusal.
Acquire and validate the original path inode before reading or writing a lease.
Timeout, replaced/deleted lock paths and unknown or foreign terminal evidence
cannot release ownership. See `test_orchestration_lock.py`,
`test_orchestration_coordinator.py` and `test_orchestration_maintenance.py`.

Repeat the 24-process, 100-round-per-mode matrix in a new evidence directory.
Every independent round must admit 24/24, and shared-session/shared-scope rounds
must admit exactly 1/24 with semantic refusals for the rest. Report latency,
registry busy/timeouts, retained history and invalid/idempotent release checks.
Keep failed reports immutable and publish corrections separately. Synthetic task
events used by component pressure tests are not live callback receipts.

The designated experience session's published maintenance notes and the thesis
workspace's existing lessons supply additional boundaries: file request creation,
hook exposure, full-body SHA consumption, native reception, callback settlement
and business acceptance are separate observations. Do not call a pending file
request a received message or an answered consultation. Keep the original
controller, task, nonce, reservation and UNKNOWN evidence. Current v3 automatic
hooks are advisory; inherited closeout gates are not session-wide tool locks.

Thesis experience also shows that a small Word/Zotero fixture can pass while a
full chapter times out. Keep realistic workload sizes, a single writer for
Word/Zotero, account-wide NotebookLM sharing and independent output scopes.
Do not use synthetic pressure, a tiny readiness request or offline scheduling
estimates as proof of model collaboration speed. Use the frozen pilot and paired
experiment, count failures, measure coordination overhead and verify correctness.

The r8 experiment passed native readiness and automatic hook adoption in all
three clients. After reading its bootstrap, the original Codex turn ended with
HTTP 400 `invalid_encrypted_content`. This was not a missing-Claude result, and
the native error alone does not identify an authentication, load or server root
cause. Pilot remained 0/3 and pairs 0/15. A small successful readiness request
cannot establish multi-turn provider compatibility or collaboration efficiency.

`upgrade_failure_watch.py` now observes the frozen original Codex journal. Only
a native `event_msg/task_complete` error after the original freeze fence can
trigger early FAILED closeout. Quoted examples, tool errors and pre-freeze errors
cannot. The observer verifies file identity and record hashes, advances a bounded
cursor, holds progression for unread/partial data and retains the ordinary
watchdog. First evidence survives a crash; a later append cannot hide a known
failure. Completed measurements and failed/missing denominator slots remain in
the report. Run `test_upgrade_failure_watch.py` and `test_upgrade_driver.py`;
read-only detection on the original r8 journal is separately recorded and does
not rerun or repair r8.

Automatic hook invocation and authenticated request discovery also differ. The
experience session's actual 0.4.34 hook audit shows a standalone Codex parent,
while that release's `collect_hook` returns no native proof without a managed
daemon and `authenticated_root` requires one. Preserve the pending request and
report this code-path incompatibility; do not manufacture a hook receipt or use
a simulated payload as proof that the target read it. A repair in an isolated
maintenance copy does not update already installed hooks or create a missing
native input control endpoint.

The r15 live startup exposed a discovery-scope regression: Darwin
`PROC_TTY_ONLY` includes foreign-UID login parents. All three actual client
terminals included a UID 0 `/usr/bin/login`; the first BSD read returned EPERM
before discovery reached the intended clients. The previous full-UID entrypoint
had excluded these parents. A scope optimization must preserve both dimensions.
Intersect kernel TTY and UID inventories; bracket each UID read with equal
complete TTY sets, and require the same complete TTY set and UID intersection
after inspecting every selected member. Exclude foreign users through kernel
scope, never by swallowing EPERM. Same-UID permission errors, unknown reads,
exits and identity drift still refuse, and daemon discovery keeps full-UID scope.
Discrete equal samples are not an atomic snapshot and cannot rule out transient
changes between reads. Validate real existing client TTYs as well as controlled
controlling-PTY fixtures: a clean synthetic shell tree does not cover a real
root login ancestor. Keep the original failed startup and its denominator;
passing earlier tests cannot certify changed discovery code or live efficiency.

Real macOS devfs timestamps are activity counters, not stable TTY identity.
Owned-PTY output changed mtime/ctime in 20/20 observations; separate grouped
experiments distinguished slave output, slave input reads, master I/O, lstat
and chmod. Keep path, device/inode, rdev, mode and ownership checks; ordinary
output must not invalidate a device solely through ctime. Preserve all raw
refusals: without the internal before/after pins, later causal reproduction
cannot prove that each earlier device-change refusal had the same cause.
Pressure runners also must separate per-scan invariants from whole-run activity,
and retain partial result serialization failures alongside complete raw attempts.
