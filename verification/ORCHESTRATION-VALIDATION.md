# Orchestration v2 validation

Checkpoint: 2026-10-15. **Offline implementation passes; no live efficiency gain
or shared installation is certified by this checkpoint.**

| Layer | Observed result | What it establishes |
| --- | --- | --- |
| Full repository suite | 1,403 tests; 0 failures/errors; 105.046 s | Current implementation's offline regression coverage. |
| Runner boundary suite | 10 tests; 4.070 s | Incomplete preparation/uncertain input preserve leases; missing end timestamps are not zero-input proof. Included in the full suite. |
| Legacy installer dry-run/install | Passed in a private `--home` | Private configuration/link operations only. |
| Legacy installer doctor | Failed | Empty input reached real active markers; legacy Stop guard returned `HOOK_CALLER_UNRESOLVED: managed hook lacks a valid session_id`. |
| Sandbox closeout | Passed using the absolute checkout entrypoint | Links retired with backups; foreign settings preserved; actual client config hashes unchanged during closeout. |
| Original sandbox uninstall refusal | Preserved | Relative invocation reported a foreign skill; absolute entrypoint did not reproduce it. The exact original cause remains unproven. |
| Original install's real-config before/after | Unmeasured | The interrupted script did not persist its original before snapshot; do not infer a retrospective pass. |
| Frozen adapter / nine live runs | Not yet measured | Offline results and an idle executor cannot stand in for execution. |
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
