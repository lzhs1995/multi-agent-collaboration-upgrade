# Orchestration v2 validation

Checkpoint: 2026-10-15. **Offline implementation passes; no live efficiency gain
or shared installation is certified by this checkpoint.**

| Layer | Observed result | What it establishes |
| --- | --- | --- |
| Full repository suite | 1,410 tests; 0 failures/errors; 110.830 s | Current implementation's offline regression coverage, including original journals larger than 64 MiB, exact GitHub organization-list provenance and native control request bytes. |
| Native control / coordinator / verifier suite | 53 tests; 28.972 s | Start, acceptance and settlement retain the original terminal LF; changed whitespace, markers and duplicate starts are refused. Included in the full suite. |
| Long original journal | 65 MiB fixture passed | Streaming preserves model history and tail actions while retaining file identity, EOF, record-boundary and per-record size checks. |
| Original corpus, isolated checker | 82 records, 48 Git sources, all eight checks passed | Offline verification of the actual research inputs, including the failed zero-byte fetch; no live business run. |
| Initial real freeze | Failed before any input | The validator rejected a legitimate GitHub organization repository list. The failed freeze is preserved; the corrected validator requires the exact original GitHub endpoint and CLI arguments and allows this route only for metadata. |
| Runner boundary suite | 10 tests; 4.070 s | Incomplete preparation/uncertain input preserve leases; missing end timestamps are not zero-input proof. Included in the full suite. |
| Legacy installer dry-run/install | Passed in a private `--home` | Private configuration/link operations only. |
| Legacy installer doctor | Failed | Empty input reached real active markers; legacy Stop guard returned `HOOK_CALLER_UNRESOLVED: managed hook lacks a valid session_id`. |
| Sandbox closeout | Passed using the absolute checkout entrypoint | Links retired with backups; foreign settings preserved; actual client config hashes unchanged during closeout. |
| Original sandbox uninstall refusal | Preserved | Relative invocation reported a foreign skill; absolute entrypoint did not reproduce it. The exact original cause remains unproven. |
| Original install's real-config before/after | Unmeasured | The interrupted script did not persist its original before snapshot; do not infer a retrospective pass. |
| Corrected real freeze | `FROZEN_NOT_MEASURED` | The original pair, source and corpus were successfully frozen with zero peer input or business execution. |
| First original native start | Rejected before run creation | The generated control script lacked the terminal LF present in the original native request (701 versus 702 bytes). That failed campaign is preserved; the generator now includes the LF, with exact matching and duplicate refusal retained. |
| Nine live runs | Not yet measured | A successful freeze and offline checks cannot stand in for execution. |
| Shared installation / automatic hooks | Not established by this work | Owned by the designated shared maintainer and requires separate live evidence. |

The full suite uses private fixtures and real offline entrypoints. Coverage
includes authorization/identity gates, provider fallback and provenance,
dependency/output substitution, durable session FIFO, conflicting scopes,
crash/result-loss boundaries, complete tree/mode verification, native/model/clock
checks, callback/disarm ordering, isolated answer recomputation and nine-run
promotion. Fixture identity/process substitutions are explicitly test-only.

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

The original Claude's accepted research is closed; it is waiting for the next
formal replay pack. Current authenticated supervisor/peer identity matches the
designated pair. Historical handshake failures remain preserved, not converted
to successful current-task evidence. See the [live runbook](../docs/LIVE-REPLAY-RUNBOOK.md).
