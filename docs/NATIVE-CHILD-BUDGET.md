# Opt-in whole-task native child accounting

2026-10-21. This is private experimental code, not a shared MAC release or a
claim that an active native client has loaded the guard. The reviewed
`624d11a36c4a4ef5426802a88e25d074a114febe` continuity candidate is separate
from this subsequently modified working source.

## Scope and authority

Only a new explicitly authorized private experiment may opt in with
`upgrade_live_materialize.py --register-native-child-budget`. Actual service
registration also requires the original private continuity CURRENT contract,
the pinned runtime/startup, accepted original 1+2 adoption, complete source tree,
and original native user authorization records. Nothing is installed into an
existing client, global configuration, shared CURRENT or old campaign.

The whole task has one create-only external registration and one atomic ledger
shared by its two original Claude parents. At most two claims may be unconsumed
at once, including completed children whose reports are available but unread.
It is not two children per parent. Changing task ID cannot create a second
registration under the same runtime. Missing, replaced or corrupt authority,
ledger or lock is refusal, never an empty budget. There is no TTL, disappearance
release, automatic restart, cleanup reset or agent retry.

The service validates evidence and accounting only. It never calls a model,
creates/resumes a native session, dispatches a task, retries a provider request
or manages task processes. JEV/TypeSafe remains rules/offline_mock. Its Unix
packet accepts only schema, enrolled lane and native hook payload; a client
cannot submit an authority, process reader, fixture flag, clock or model.
Kernel peer PID/UID, exact automatic hook argv/shell ancestry and a freshly
checked original CLI process, birth, executable identity and native root must
match. Process labels or a manually invoked hook are not native authority.

## Creation, delivery and original-parent consumption

```
Original native Agent tool_use
  -> authenticated PreToolUse -> atomic RESERVED (occupies one slot)
  -> original native child -> full end_turn report + actual parent tool_result
  -> RESULT_READY (still occupies the slot)
  -> original parent full-file native Bash read
  -> actual original consume Bash call with the exact file SHA
  -> CONSUMED (releases once)
```

Reservation is persisted before native Agent execution, not at SubagentStart.
The hook preserves original fields and adds an exact claim preamble to the
assigned prompt; it never emits `permissionDecision=allow` or bypasses another
original guard. Repeated PreToolUse for an already issued claim is refused,
preserving the first reservation rather than permitting another creation.

Completion binds the unique original parent Agent result to the exact native
child file immediately under that parent. The child root must contain the
actual injected prompt, not a quoted marker. Conversation ancestry includes
attachment vertices without promoting their contents to reports. A unique
current `end_turn` text, no unresolved/duplicate/unrelated native tool results,
chronology, non-future timestamps and reread source pins are required. The
actual parent completed result must contain the full exact terminal text and
be no earlier than the original call or child terminal. A launch note, caller
status, API-error message or source-less file does not establish completion.

The service publishes an immutable full report with an original claim marker.
Its bounded hook notice supplies the exact full-file reader command, claim and
SHA consumption command; it does not duplicate a potentially long report in
hook context. The original parent must perform the source-bound complete read
before its actual consume call, by both byte offset and timestamp. A SHA alone,
later read, truncation, yielded result, error, sidechain, quote or ambiguous
duplicate call/result cannot release a slot. Reconsumption never releases twice.
The local consume CLI only reads the externally published receipt; the
authenticated automatic PreToolUse transaction performs the ledger change.

## Native hook compatibility and bounds

New private Claude PreToolUse groups match `Agent|Bash` and require exact
`onFailure:"block"`; explicit exit2 also refuses. Default command-hook startup
errors and timeouts otherwise fail open. The field requires Claude2.1.295 or
newer; configuration tests are not proof that a running client honors it.
Original hook groups and the existing75-second Stop guard are preserved.
Correct `hookSpecificOutput.hookEventName/additionalContext` envelopes are used.
See the primary [Claude hook failure contract](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails).

The external new-launch entrypoint and the actual generated original private
harness independently run the exact pinned native Claude `--version` before
writing launch intent. They recheck the complete program
pin before/after, rejects pre2.1.295, unknown, nonzero or extraneous output and
records the prerequisite in launch-request evidence. A read-only local check of
the fixed2.1.303 program passed; that is a version observation, not hook adoption.
Offline materialization still accepts explicitly declared ordinary executable
fixtures; those fixtures cannot satisfy this new-launch capability prerequisite.

Both entrypoints' refusal was tested through their actual public commands,
including the generated harness under its unchanged macOS kernel profile.
`child-harness-version-green2` passed10 tests,16.282seconds unittest/
16.768053seconds wrapper, ending `2026-10-21T02:51:24.891589Z`,334 pins unchanged;
2415B log SHA256
`76261e48bb27976c0b78da7ab077e5824866c8899853d62e67a6641e8327ab8d`.
The first harness counterexample and the subsequent read-only EOF descriptor
failure are retained. No audit, isolation or writable scope was loosened.

The first protocol supports only original non-fork foreground `general-purpose`
Claude children. Only opted-in new private Claude client environments pin
`CLAUDE_CODE_FORK_SUBAGENT=0` and `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`;
provider environment imports cannot override those values. Background requests
are refused before reservation. Fork, resume, model/team override, nested
children, Codex children and auto-mode SubagentHandback are unsupported.
SubagentStart/Stop observations cannot create or release capacity. See primary
[foreground/background behavior](https://code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background)
and [environment variables](https://code.claude.com/docs/en/env-vars).

Packets and responses are bounded to256KiB, native scans to65,536 records/32MiB,
the ledger to256 claims, service duration to1–3600seconds and service events
to256. Original native per-record limits still apply. The socket and service
intent are create-only. Timeout, operator Stop or lost response leaves occupied
capacity and evidence intact, never authorizing a replacement child. Capacity
and diagnostic status retain `native_adoption_proven=false` and model/input
operation counts zero; actual model adoption requires separate native evidence.

## R8 response ownership and long-history repair — 2026-10-21T17:48Z

The frozen2d3bafc baseline passed2433 local tests and four exact-commit CI
jobs. C29's600-second independent R8 increment review returned PARTIAL, not
whole-range acceptance. Its5791B report SHA256 is
`b25a14de203fea9f55a88561f11f9d2f8e811acd4a36d23a5a25e08fc732ae48`.
The original supervisor's exact native DONE record and dedicated callback
receipt were independently read and matched; only that review task was disarmed.
All previously closed R5–R7 findings and immutable failed runs are retained.

R8 found two creation liveness defects, now reproduced and repaired in the
subsequent working candidate, not silently attributed to frozen2d:

- One Claude API response can have several original JSONL content-block records
  sharing message.id. The current response is still selected with ordinary
  response deduplication; an additional nondeduplicated inspection qualifies the
  exact Agent block through the same original user ancestry, model and terminal
  rules. A message-id match alone is insufficient. A late block from a superseded
  response cannot make the old invocation current. Continuity's default API
  outcome counting is unchanged, and report/consume still authenticate their
  historical original invocation independently of this creation-only gate.
- The current-response scan no longer requires absolute transcript EOF below
  32MiB. It reads at most the latest32MiB, aligned to a whole-record boundary,
  without inventing an ancestry root. The original post-enrollment call interval
  still has its separate32MiB/65,536-record bound. A missing user root in the
  bounded tail, an overlong original episode, incomplete EOF or changed source
  remains a refusal with no reservation. This does not authorize rebinding an
  active original process, moving its enrollment fence, or unbounded rescanning.

Seven new service tests exercise text-then-Agent records, two split Agent blocks
sharing the whole-task two-slot budget, unlinked/truncated/nonterminal blocks,
large pre-enrollment history, a late superseded block, unchanged original
interval/partial EOF refusal, and missing-root refusal at the bounded edge.
All tests call the public allocator transaction with original-journal fixtures;
their process observations are fixtures, not live model-child adoption. The
initial split-response and long-history REDs and two insufficient intermediate
repairs' REDs remain under the evidence root. The final bounded-root positive
and negative pair passed2 tests at17:27:10.129100Z,0.588s unittest,
0.832197s wrapper,339 source pins unchanged;520B log SHA256
`58d7d29a3c00c45a80c25762bc83f4974fe61a84e582324b16cfa204dbe18bbd`.

A separate new generated-private-input test fills R8's unexamined partial-tail
boundary: both original Claude directions refuse `NATIVE_EOF_INCOMPLETE`, while
historical membership remains intact and no trio adoption is published. It
passed1 test at17:24:19.360857Z,7.636s unittest/8.0764s wrapper,339 pins unchanged;
364B log SHA256
`10293c11ea57259620775c1a9b403788703c8f650134a95bddd8689198039290`.
It runs the materialized adapter under its original Seatbelt profile, with
process/kernel-boundary observations explicitly mocked. Its first failed run
expected the wrong diagnostic; the log is retained and only that assertion was
corrected. The already refusing production gate was not changed for this test.

Broader working-source regression `child-r8-combined-v1` passed142 tests,
F0/E0/S0 at17:45:29.924128Z,349.484s unittest/350.203005s wrapper,
339 source pins unchanged;30754B log SHA256
`344e85671058381b3a0371d0b8eba0810dd184811f4080926f1b942fedad3501`.
It covers the original eight-module startup/continuity/observation/adapter/child
scope plus peer and fixed.58 compatibility. This paragraph is a later doc-only
update; that run records dirty2d HEAD, not a clean new commit. Clean new-commit
full regression and new CI are still pending. Neither frozen2d's green nor
these fixtures prove live children, native hook loading, whole-task production
enforcement or the user's actual three-mode collaboration.

## Historical validation state, not live acceptance

Working-source evidence is under
`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/execution-20261021/`.
All RED logs are retained. Latest small public-boundary run
`child-terminal-tools-green1` passed29 tests, zero errors/failures/skips,
1.151seconds unittest/1.613851seconds wrapper, ending
`2026-10-21T02:09:25.943241Z`, with333 source pins unchanged. Its6377B log SHA256
is `1e41c566dae386387e09611485cda4630c7e424d36ac528e363ef9e0dedcb8e0`.
Those pins precede this documentation addition; do not relabel it as a test of
the documented final source or as the frozen624d full result.

After the new-launch version prerequisite, `child-cli-version-green1` passed65
small child/identity/body-read tests, zero failures/errors/skips,2.602seconds
unittest/3.954891seconds wrapper, ending `2026-10-21T02:33:50.622404Z`, with334
pins unchanged;10776B log SHA256
`fb7c37d2cb65ecdc398d50265d754b46841d89e4a6b890201b64656253210f47`.
This includes30 child/discovery tests,24 existing daemon-identity tests and11
existing body-read tests. The later documentation update has separate pins.

Coverage includes an eight-process ordinary Python race allowing exactly two
claims, real Unix kernel peer observation, native ancestry/source boundaries,
complete report availability without release, before-consume reads, failure
and duplicate negatives, exact native completion chronology, tool-result
closure, bounded84KB report delivery and isolated test discovery. A separate
public `serve()` fixture actually bound a Unix socket for1second and preserved
two occupied claims on exit (`child-service-serve-green1`,1test). The complete
private-startup group then passed7 tests in `child-startup-current-v1`,
67.367seconds unittest/67.899546seconds wrapper, ending
`2026-10-21T02:43:49.494589Z`,334 pins unchanged;1747B log SHA256
`e944b1aa325c774c8dd16151ee16dca4d34f9e37323fe4d888a08f28009d5661`.
A subsequently added actual harness-version test is separate10-test evidence
above, not a retrospective eighth test in that original run. These are manually
invoked Python/offline adoption fixtures, not automatic native Agent execution.

At this historical validation checkpoint no real model child,
production registration/service, active-client hook
adoption, actual foreground completion flush, or live two-parent gate has been
proven. The latest combined eight-test private-startup group, frozen full/CI,
actual load-bearing failure/third-child gate, fresh original native parents,
two real60-second request/reply/full-read/SHA-consume/rearm cycles, three-state
continuity and the unchanged3pilot/33business/15pairs/192ordinary-slot benchmark
remained separate unfinished gates. Later complete local regression/CI are
recorded separately above and in HANDOFF; live collaboration gates remain
unfinished. Never inherit624d's2367-test/CI green for later code. The historical
low-disk hold was lifted only after actual filesystem headroom returned; every
new large full/native-workspace stage still requires a fresh10GB-floor check.

Read-only final-source transformation of the complete fixed MAC0.4.58 passed227
scripts with no refusal, no changed upstream pin, and no generated runtime in
`child-source-transform-v1`,13.420389seconds wrapper. Subsequent support/source
memory compilation passed274 local scripts and24 private Python support modules
(26 total support assets). These checks do not execute the transformed runtime.
Two additional manual compile-check command errors (relative path and treating a
support tuple as raw source) preceded the corrected check;they are not product
passes or erased failure evidence.
