# November 4 continuation: code verified, native experiment pending

The objective remains reliable one-Codex/two-Claude collaboration in all three
availability modes, followed by separate upgrade-vs-MAC and JEV off/on efficiency
comparisons. Current implementation is `CODEX_SOLO`, review `solo_self_review`.
The registered MAC 0.4.84 controller and all original attempts remain unchanged.

## Code and validation

Candidate `a6ea11abc6be699be83dec51812aa79e6fe1366c` includes:

- Numeric HTTP 402 classification regardless of the provider's message language.
- A retry delay of at least 60 seconds, respecting a longer supplied provider
  delay and requiring its original attempt timestamp. Invalid delays are refused.
- A module-local clock mock for the runner-lock test. The previous global
  `time.sleep` patch also intercepted subprocess polling on macOS, producing a
  second false scheduling observation. The test still proves that a competing
  runner is refused, the lock inode is unchanged, and no permit is issued.

Local affected suites: 76 availability/delivery checks and 48 driver checks,
both successful. No new local full suite is claimed. All four jobs in
[CI 41702862357](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/41702862357)
passed for the exact candidate. The preceding
[CI 41700573027](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/41700573027)
is retained: macOS/Python 3.14 failed the global-clock test; its other three
jobs passed. It reported 3,124 tests, one failure, zero errors and 294 skips
on that failing job. Skips are not passes.

The original `adc70616` full local/CI evidence and frozen source archive remain
attached to that candidate. A detached clean checkout of the new candidate is
`/Users/lzhs/projects/multi-agent-collaboration-upgrade-model55-20261104`.

## Explicit model amendment and bounded observations

At 2026-11-03T19:24:00.972Z, the original successor transcript records the user's
choice of `claude-opus-5-5` for both arms of a new independent experiment.
The prospective 15-pair factor table changes all 30 Claude model entries equally,
retaining the existing 1M context suffix, Codex model, inputs, policies, 33 business
slots, ordering and acceptance formulas. No old registration is rewritten.

S03, before that decision, had two complete alternate-model responses (38.31
and 51.52 seconds); it remains labeled an alternate-model diagnostic. S04,
after the decision, produced two HTTP 200 streams with EOF and `message_stop`
but empty text and `stop_reason=refusal`. It failed capacity acceptance.

S05 used a small dependency problem: A and C are initially ready, while B depends
on A. Both new requests returned the correct IDs, nonempty text, EOF and
`message_stop`, with no local, upstream or parse errors. Durations were 20.438
and 13.614 seconds; overlap was 13.613 seconds. There were two client requests
and zero client retries. This supports that bounded observation, not sustained
long-context capacity, native readiness, a formal paired sample or a speedup.
S04 and all earlier failed requests remain preserved.

## Preparation and remaining admission

The new model55 preparation script is read-only by default. Its explicitly
gated apply operation would create one fresh registration and derived files;
it does not start clients, issue a permit, or release a run. The script retains
the original admission schema and verifies the new model, candidate CI, native
user record, old j5 terminal records, two disk observations and current capacity.
Missing prerequisite evidence stops it before any registration or source write.

The private provider transformation was executed in isolation and verified to
produce `claude-opus-5-5[1M]` without changing the shared profile. Both derived
scripts compile. The old RTK 0.45.0 path no longer exists; the prospective private
recipe explicitly pins installed RTK 1.1.0, SHA256
`59325732b8081712d4844daf5c0bd89c347922e3f5b739c0a6d7935c67145416`.
This changes neither an old binary pin nor the original MAC controller.
All 434 original MAC source members and the other native binary pins matched.
The three specified experimental surface UUIDs still exist.

The old preparation's stopped-PID assertion cannot establish current ownership:
PID 30883 no longer exists, while the original Claude session is resumed in a
different terminal for a separate retrospective. New preparation is confined
to an exclusively created ROOT directory; it neither takes that executor nor
changes its task, process or controller. The old task files remain read-only.

Disk observations briefly reached 44.85/44.67 GiB at 19:08/19:09Z. Later actual
observations were 35.490 GiB at 19:26Z and 27.895 GiB at 19:37Z. No deletion or
compression was performed by this continuation; capacity movement is unattributed.
The earlier passing values cannot authorize a launch after the new shortfall.

S40's second maintenance notice was reconciled through its original MAC 0.4.84
controller to `NATIVE_RECEIVED`, with zero new input. Full-body consumption and
new admission remain unverified; its original session continues to display
remote-compaction 502 errors. Registration, capacity, notice receipt, body
consumption and business acceptance remain separate.

j6 is not registered or launched. Business remains **0/33**, formal JEV **0/15**.
Next: obtain fresh capacity and S40 admission, revalidate the prepared inputs,
then use the original held/CAS/watcher/one-release sequence for the first pilot.
The three-mode, inquiry-cycle, communication, child-parent and separate
upgrade-vs-MAC requirements remain open. The original missed deadline is retained.

## Local evidence

ROOT evidence directory:
`/Users/lzhs/uz/state/B/recovery-20261024/r89-u22-held-verification-v1`.

- `SUCCESSOR-01a1a1e3-20261104-CONTINUE-v2`: 76-check result, 48-check result,
  failed CI log, S02/S03 responses and original S40 send/reconcile evidence.
- `SUCCESSOR-01a1a1e3-20261104-MODEL55-v3`: exact native model authorization,
  factor changes, S04/S05 complete responses, CI metadata, source/binary/endpoint
  preflight, private preparation script and its read-only validation.
