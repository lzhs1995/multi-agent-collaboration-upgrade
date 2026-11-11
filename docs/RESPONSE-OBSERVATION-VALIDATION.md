# Private response observation after r27

r27 remains HALTED/TIMEOUT: pilot 0/3, business 0/33, comparable pairs 0/15,
ordinary-message slots 0/192. The missing response terminal evidence prevents
attributing its Claude stall to authentication, capacity, client consumption or
another cause. This change prepares bounded observation for a future authorized
materialization. It does not reopen r27 or load code into existing clients.

## Contract

`upgrade_live_materialize.py --observe-claude-responses` enables the optional
`consumed-response-v1` binding in startup-v2 or startup-v3. Default materialization
does not add a preload. The module is included in the immutable controller tree
and required startup pin; disabled contracts reject an added observer pin.
Each Claude lane gets its own 0700 directory and Bun-escaped preload path. Entry
sets the current exec-preserved PID. The installed observer removes its own
preload and enrollment variables before tools can inherit them. Codex receives
neither observer configuration nor output directory.

Literal backslash, CR/LF and NUL preload paths are refused before any arm artifact
is created. Spaces, tabs and quotes use Bun character escaping, not shell quotes.
APFS can return different path casing through Python and native Bun. Both the
requested and native-canonical directory must identify the same owned 0700 inode,
with no symlink ancestors; each publication rechecks both names. Preload cleanup
decodes only a single supported option and verifies the original module inode.

New paired registration v3 pins identical observation settings and observer
SHA/byte count for all seven groups; configure, permit issue, driver launch,
freeze and campaign validation now enforce that common policy. Enable the flag
at both registration and materialization from the same frozen source. Reports
verify original pins and the complete original external closure row. Actual
LOADED remains a separate observation; historical v1/v2 qualification is
`legacy_unverified`. See [the follow-up validation](OBSERVER-COMPARISON-VALIDATION.md).
No new live campaign or existing frozen-runtime mutation is part of this change.

The observer wraps the original fetch/response consumers without cloning,
teeing, draining or sending requests. It returns original promises, responses,
readers, chunks and rejection values. It adds promise handlers and synchronous
bounded telemetry; this can affect timing and unhandled-rejection behavior.
It is not a transparent network recorder.

Only HTTP(S) `/messages` requests are eligible, at most 512 per process. It
records status, redirect flag, request-body hash when directly available,
allowlisted SSE kinds/error types, observed usage and consumption state.
Prompts, credentials, model text and arbitrary remote error identifiers are not
stored. `request_type=UNKNOWN` is deliberate: body size does not identify a
main request versus a hook request.

Raw inspection is limited to 8 MiB. The separate frame limit is 262144 character
units: SSE counts non-newline Unicode code points per frame; JSON fallback uses
UTF-16 code units. It is not a 256 KiB byte limit. There are at most
16 errors and at most 128 progress plus 3 terminal snapshots per request.
Limits and malformed/incomplete data are explicit. A failing sink stops further
publication for that parser; directory identity and 0600 atomic snapshots are
checked. Cancellation intent is recorded before the original call so the
resulting pending-read `done:true` cannot be called natural EOF. Iterator return
with `preventCancel:true` records consumption stop without claiming cancellation.
Actual underlying cancellation takes precedence if the runtime still cancels.

Native Bun's iterator calls `body.cancel()` after its internal reader reaches
EOF. The observer records `readMany().done` without inspecting its prefetched
values, so early iterator return cannot inflate consumed bytes. Cancellation
after that reader EOF fact does not erase the later iterator completion. Failed
cancellation is recorded separately and does not end observation of a still
readable body. A pending iterator's successful cancellation without an observed
reader EOF retains `ITERATOR_DONE_CANCEL_ORIGIN_UNKNOWN`, never a guessed EOF.

`raw_bytes` records a hash only of the actually inspected prefix. `decoded_text`
has a separately named re-encoded prefix hash and bounded extra allocation.
`parsed_json` has no byte hash. Unsupported or never-consumed paths remain
unobserved. None of these summaries proves business correctness or releases a
provider slot. Actual LOADED/source-hash and response evidence must be checked
separately from configuration, native readiness probes and automatic hooks.

## Original fixed-source validation (before cleanup repair)

Observer SHA256:
`cf857020d46fc1b980496d25a88c648f16517cb7c5ca7ce5a8b35a9a807d1671`
(18,253 bytes). Evidence root:

`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/response-observer-local/`

| Check | Measured result | Evidence under that root |
| --- | --- | --- |
| Node consumer and parser regressions | 29/29 pass; includes 128 concurrent synthetic streams, 131,072 delta events and 24,400,640 bytes | `final-v2/node-final.json` |
| Python opt-in startup integration | 14/14 pass in 16.270 seconds | `integration-audit-final.json` |
| Native Bun cancellation boundaries | 6/6 pass, including failed cancellation followed by real reads and ambiguous iterator cancellation | `cancellation-boundaries-cf857020d46f-native-method-control.json` |
| Native Claude/Bun preload | 4/4 invocations LOADED using both `projects` and `Projects` paths | `case-native-repair-cf857020d46f.json` |
| Bun HTTP concurrency | Four workers × 64 concurrent root requests; 268 requests, 260 observed roots, 8 excluded children; 1,311/1,311 assertions | `loopback-repaired-cf857020d46f.json` |
| Concurrent atomic-publication reads | 5,261 JSON reads, zero parse errors; private permissions and no temporary leftovers | same loopback result |
| Four AB/BA overhead pairs | 2,048 HTTP requests; 3,130/3,130 correctness/count/hash assertions; material elapsed cost | `overhead-result-cf857020d46f.json` |
| Full Python repository regression | 2,108/2,108 pass, zero failures/errors; 1,022.890 seconds, source unchanged | `final-v2/full-regression.json` |

The Node and independent native/HTTP results record unchanged source hashes.
The targeted integration audit captured source hashes after its run; it does
not claim its own independent before/after hash check. The full-suite runner
separately monitors all Python, CJS and shell files in `scripts` before/after.

Native loading used Claude Code 2.1.170 `--help` with a private empty configuration
and denied network access; Bun was 1.3.14. Those checks prove preload execution,
not model responses or startup adoption. Combined HTTP count is **2,316 local
requests**, with **zero model requests**. Synthetic parser/cancellation tests
are not added to that HTTP count.

## Measured instrumentation cost

Each timing arm used four Bun workers with 64 concurrent HTTP requests each,
the same 68,486-byte SSE payload, 4,096-byte chunks and 1 ms pull delay. A disabled
observation; B enabled the actual root preload and atomic file publication.
The sequence was AB, BA, AB, BA, with no additional pairs or retries.

| Pair | Order | A controller ms | B controller ms | B/A elapsed | Request-median B/A |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | AB | 647.2 | 2,307.4 | 3.57× | 6.09× |
| 2 | BA | 651.9 | 2,659.5 | 4.08× | 4.92× |
| 3 | AB | 817.1 | 3,554.7 | 4.35× | 4.57× |
| 4 | BA | 397.4 | 2,963.0 | 7.46× | 9.82× |

This diagnostic has **material cost** in the measured workload. There was no
predeclared overhead threshold, and passing correctness does not make this a
performance pass. The benchmark combines parsing, bookkeeping, path validation
and synchronous writes; it does not identify their individual contribution.
Shared-host scheduling and filesystem caches were uncontrolled. The substantial
spread and local provider-free workload limit extrapolation to native model
latency. Equivalent observation settings and explicit calibration are required
for interpreting future collaboration timings; equality alone cannot establish
that instrumentation has no workload-dependent effect.

## Repaired failures and retained limits

The independent audit found cancellation/EOF races, delegating iterator double
counts, unguarded iterator patch failures, unbounded text encoding and snapshot
writes, and overly broad error-field logging. Those findings are covered by the
final tests. Native testing additionally exposed false cancellation at iterator
EOF, failed cancellation prematurely stopping observation, and APFS casing
refusing a valid preload. The final source passes the corresponding checks.

Earlier failed results remain intact. The old full regression was deliberately
stopped with return code -15 when new native defects were found; it is not PASS.
The first 14-test integration run had one exact-source fixture mismatch while
the CJS was concurrently edited. The frozen-source rerun passed all 14 without
weakening the assertion. Native Bun's actual `Symbol.asyncIterator` ignored the
tested `preventCancel` option; the corrected fixture follows its real public
cancel call instead of assuming Node behavior. The independent review records
the old casing test's expected-count labeling error and its actual zero records.

`FINAL-REVIEW-cf857020d46f.md` contains the independent review and failure history.
`FINAL-MANIFEST-cf857020d46f.json` pins its 1,387 evidence files; manifest SHA256:
`62e578c0e38ed63e937b077ad6202e59742facc838e8874adf48f8644994798a`.
This manifest was issued by the independent reviewer before the parent's full
regression finished and does not claim to pin that later result. The complete
Python suite ran from 2026-10-19 07:14:46Z to 07:31:49Z and exited 0; the parent's
`final-v2/release-evidence.json` pins the final logs and current repository files.
The parent independently rehashed all 1,387 entries with zero mismatches;
`final-v2/independent-manifest-verification.json` records that verification.

## Remaining live acceptance

The unresolved deliverable is the registered **1 Codex + 2 Claude** experiment,
not another replay of local HTTP traffic. Preserve the original r27 controller,
deadline, clients and failure evidence. A future bounded campaign must:

1. Freeze all seven groups before launch using v3's common observation policy.
   Require every runtime to match and retain separate actual LOADED checks.
   Keep the observer's measured cost visible in the report.
2. Establish actual loading separately from native Goal SET, completed response,
   readiness probe and automatic hook adoption. Record missing stages as missing.
   Only the native Claude process consuming an actual model response can validate
   the new observation path for that client workload.
3. Require three consecutive pilot passes before the remaining business rounds.
   Keep 33 business rounds, 15 comparable pairs and all 192 registered ordinary
   messages in their original denominators. Both comparison arms allow parallel
   Claude work; neither arm creates a forced idle Goal window for communication.
4. Accept collaboration correctness and efficiency only from completed native
   evidence. Retain the existing >=15% warm median improvement and <=5%
   per-workload median regression criteria, and report instrumentation as a
   possible interaction rather than silently subtracting local timing ratios.
5. End at the new campaign's own fixed deadline. Preserve failures, unknowns and
   unconsumed messages without replacement samples, replay or retroactive input
   proof. Publish measured lessons to the designated shared-skill owner.

This list identifies the remaining acceptance work; it does not start a campaign
or extend r27. The present change can be reviewed as an optional diagnostic and
cannot yet be promoted as a proven collaboration efficiency improvement.

No new Claude campaign, model request, historical replay or shared skill change
belongs to this local validation. r27 remains timed out with no completed pilot
or business rounds, and efficiency remains unproven. The designated surface40
owner continues to manage shared MAC and thesis-refiner integration; an inbox
publication alone does not prove its owner has read or merged these findings.
