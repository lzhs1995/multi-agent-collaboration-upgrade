# Orchestration v2 validation

Checkpoint: 2026-10-15. **Offline implementation passes; no live efficiency gain
or shared installation is certified by this checkpoint.**

| Layer | Observed result | What it establishes |
| --- | --- | --- |
| Current full repository suite | 1,532 tests; 0 failures/errors; 499.118 s | Offline coverage including the recorded observer and package previews. All 225 tracked/unignored files and resolved Python executable bytes/hash/mode matched before/after the run. The observer increment is `solo_self_review`. |
| Previous full repository suite | 1,514 tests; 0 failures/errors; 171.467 s | Historical regression coverage through the V20 parser repair. All 223 tracked/unignored files matched before/after that run and at supervisor verification before its documentation updates; ignored cache was not part of that inventory. |
| Recorded observer and package previews | 17 focused observer tests; runner 13 and adapter 12 tests passed | Read-only advice avoids checker reruns and transport construction; incomplete claims, original operations, concurrent control publication, lease release and clock gaps remain explicit. These suites are included in the current full repository result, not additional tests. |
| Dispatch-latency review (V23) | Original Claude accepted the scoped request; no frozen report collected | Provider authentication failure interrupts independent review. File-body reading and acceptance are recorded separately from native receipt. This does not certify the later observer implementation. |
| Independent maintenance entrypoint review (V19) | 9 tests and 7 subprocess runs across 4 probes met expectations | Fresh-process `main()` with disk manifests reached one real isolated lease release; replay and foreign-source/stale-reservation refusal preserved the lease. Campaign/native validation and state replay remained explicit fixtures. |
| Native control / coordinator / verifier regressions | Included in the full suite | Start, acceptance and settlement retain the original terminal LF; changed whitespace, markers and duplicate starts are refused. |
| Interrupted publication review (V11) | Eight subprocess crash injection points; no torn final result | Process-crash behavior, exclusive publication and preserved stages. No power-loss guarantee. |
| Source dependency and parser reviews | Fixes and regressions included in the full suite | Deferred adapter dependencies, private parser ownership, cache refusal and conservative sender provenance. Reviewed syntax coverage is not arbitrary-code analysis. |
| Independent deployment-document review (V21) | No blocker; three documentation findings accepted | Static review only, with no tests or documented commands executed. Follow-up documentation fixes specify the freezer/interpreter, claim-time decisions and mutating disarm. |
| Deployment-document follow-up (V22) | No blocker; F1–F3 verified against 19 pinned files | Static review only. The remaining wording fixes record the missing historical interpreter hash, PENDING-only decisions and pre-decision exit 2, and separate test and freeze-source directories. |
| Long original journal | 65 MiB fixture passed | Streaming preserves model history and tail actions while retaining file identity, EOF, record-boundary and per-record size checks. |
| Original corpus, isolated checker | 82 records, 48 Git sources, all eight checks passed | Offline verification of the actual research inputs, including the failed zero-byte fetch; no live business run. |
| Initial real freeze | Failed before any input | The validator rejected a legitimate GitHub organization repository list. The failed freeze is preserved; the corrected validator requires the exact original GitHub endpoint and CLI arguments and allows this route only for metadata. |
| Runner and maintenance boundaries | Included in the full suite | Incomplete preparation/uncertain input preserve leases; missing end timestamps are not zero-input proof. Locked recovery and version-3 disarm chronology are checked offline. |
| Legacy installer dry-run/install | Passed in a private `--home` | Private configuration/link operations only. |
| Legacy installer doctor | Failed | Empty input reached real active markers; legacy Stop guard returned `HOOK_CALLER_UNRESOLVED: managed hook lacks a valid session_id`. |
| Sandbox closeout | Passed using the absolute checkout entrypoint | Links retired with backups; foreign settings preserved; actual client config hashes unchanged during closeout. |
| Original sandbox uninstall refusal | Preserved | Relative invocation reported a foreign skill; absolute entrypoint did not reproduce it. The exact original cause remains unproven. |
| Original install's real-config before/after | Unmeasured | The interrupted script did not persist its original before snapshot; do not infer a retrospective pass. |
| Corrected real freeze | `FROZEN_NOT_MEASURED` | The original pair, source and corpus were successfully frozen with zero peer input or business execution. |
| First original native start | Rejected before run creation | The generated control script lacked the terminal LF present in the original native request (701 versus 702 bytes). That failed campaign is preserved; the generator now includes the LF, with exact matching and duplicate refusal retained. |
| Later real v4 campaign | Blocked at the first Claude preparation | The run and its original marker/reservation/lease remain preserved. No successful maintenance closeout or complete business comparison is claimed. |
| Original-history diagnostic v1 | `UNMATCHED`: `native tool arguments must be an object` | Preserved failure. V20 independently traced the sole parse exception among 767 same-turn calls to an internal symbolic tuple leaking from a legal JavaScript destructuring loop. |
| Original-history diagnostic v2 | `MATCHED_STATIC_ONLY` after the V20 repair | Real original call, command-execution and result pins matched over the same bounded record interval. No historical command was executed. Original startup, whole-process writes and invocation-time input identity remain unproved; settlement/retry remain unauthorized. |
| Nine live runs | Not yet measured | A successful freeze and offline checks cannot stand in for execution. |
| Shared 0.4.35 / automatic hooks | Not verified as published, installed or adopted | Last observed release is 0.4.34. The designated shared maintainer owns integration and active-client validation. |

The full suite uses private fixtures and real offline entrypoints. Coverage
includes authorization/identity gates, provider fallback and provenance,
dependency/output substitution, durable session FIFO, conflicting scopes,
crash/result-loss boundaries, complete tree/mode verification, native/model/clock
checks, callback/disarm ordering, isolated answer recomputation and nine-run
promotion. Fixture identity/process substitutions are explicitly test-only.

The current full-suite private result is frozen as
`full-suite-observer-v1/result.json`, 41,509 bytes, SHA256
`4d482fee52cbc8a7488d6c3b32a531841cfbc84fdb9b3f8e29611b4e5f785726`.
Its intent SHA256 is
`e39578518e60f1b978477d5130ac2b922c0077694d61f7610807a1a4ea1a3789`;
the 1,680-byte log SHA256 is
`817eba5f1e741d283aa6aad4569ddb4ef108ac71ed1eef17e3e77de0f99d608e`.
The run used Python 3.14.7 from the resolved Homebrew executable
`/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14`:
34,640 bytes, mode `0755`, SHA256
`87d4df53fd91304be5bac391fb204643c36b7df2023c04a0953bcbc7d4fdf634`.
The complete 225-file inventory and executable pin were identical before and
after the run. This pins the executable, not the entire interpreter environment
or standard library. The suite took 499.118 seconds; the evidence wrapper took
506.413 seconds. Later changes are limited to the README, architecture, live
runbook and this validation document. Tests were not repeated for those wording
updates. The increment remains `solo_self_review`, with no live delivery,
shared installation or measured efficiency certification.

The previous full-suite private result is frozen as
`full-suite-maintenance-v20-v3/result.json`, SHA256
`1de9b4bdb903ee9215bdc043df7024cc854c929c45c319c89213434209a7b233`;
its log SHA256 is
`2ed0e0d56bf9073a0f8e8a7dac96a1e7ee7b919f54b323af99ffeda9ab766c58`.
The result records the interpreter command path, but no resolved interpreter
bytes or SHA256. A current executable hash cannot establish that historical
identity. Reuse the run only for its recorded source and scope; rerun if the
interpreter identity is in question.

Public documentation updates followed that historical source-bound run. The earlier V19
checkpoint passed 1,513 tests in 276.423 s. Both durations are unittest timings,
excluding the outer evidence wrapper; the change in runtime is not a
collaboration speedup measurement. The preceding 1,507-test run with 14 errors
remains preserved as failed evidence; the older 1,410-test pass covers the
previous published revision only.

Both real history diagnostics' audited intervals recorded zero opens of the
original body/artifact and no process, network or write events. Imports and
separate evidence publication were outside that interval. Local/standard-library
bytecode was read, so this is not evidence of source-only historical execution.
The immutable v1 result SHA256 is
`322b3a4c73302c521eb965e53b181f85dab91a3a44c7c9e5ff74930c7d0e2e92`.
The v2 result SHA256 is
`65fddd47827d0392f2d20db7f83d3acaff4d856564d021ac7560364d3280a765`.
Both use the original byte interval `[75306780, 197532372)` with scan SHA256
`a1dca691605f0c7ced6601cf236082392e7d0068fb7244da8c311910833d2045`.
V20's minimal repair normalizes the parser's internal tuple to unresolved
arguments; it retains refusal of unresolved senders and the direct native
argument schema. The v2 static match still cannot prove the original process's
startup environment, whole-process writes or invocation-time input identity.
Settlement and input retry remain unauthorized by static diagnostics.

V19 and the other executor audits used scoped requests to the same original
Claude session through the authorized file channel. File publication, complete
body reading, scope acceptance, frozen DONE reports and supervisor verification
are separately recorded. They do not prove native reception of an ordinary
task notice or repair the blocked ordinary supervisor-to-Claude channel.

The doctor failure is an explicit legacy-hook diagnostic, not an automatic hook
invocation in either active client. `--home` limits configuration paths; it does
not isolate the legacy guard's activity directory. The old installer is not the
private frozen adapter's deployment entrypoint. Shared hooks were not changed.

## Real campaign acceptance

The fixed order is `serial, omo, codeg, omo, codeg, serial, codeg, serial, omo`.
Every run uses the same four-node graph and frozen corpus, original CLI
sessions/models, controller, Python and native user authorization. Keep each
original task pack, native reception/ACK, tool completions, callback, independent
checker result, supervisor acceptance, lease release and scoped disarm.

All nine runs must be correct and contract-valid. A candidate needs at least
15% improvement over serial in median elapsed seconds, human interventions or
retries to be promoted. Missing observations remain `null`; zero baselines are
ineligible for percentages. Provider automatic retries remain a separate native
counter. No unapproved absolute-count threshold is added. Publish all raw
per-run metric summaries and acknowledge the three samples per strategy and
original-session carryover limitations.

The v2 frozen runner's `verify-campaign` is the current gate. The legacy
`validate_replay_evidence.py` returns **HISTORICAL_ONLY**, exit 3, even for an old
passing pair. The seven-node 24.051% estimate and four-node 170/105/125 second
policy probe cannot be promoted by relabelling them as live evidence.

The original Claude's accepted research is closed, and the same session continues
independent recovery review while the live replay is blocked. Historical
handshake failures remain preserved. The checkout is managed in the independent
[upgrade repository](https://github.com/lzhs1995/multi-agent-collaboration-upgrade)
and [draft PR #1](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/pull/1).
That publication is separate from local execution, shared deployment and the
live comparison. See the [live runbook](../docs/LIVE-REPLAY-RUNBOOK.md).
