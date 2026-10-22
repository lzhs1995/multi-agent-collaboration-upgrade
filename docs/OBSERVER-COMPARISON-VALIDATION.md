# Observer configuration and cleanup validation — 2026-10-19

This candidate adds a common observer configuration contract and repairs temporary-file ownership. Local correctness results do not establish live Codex/Claude collaboration or an efficiency improvement. The latest native campaign remains r27, HALTED/TIMEOUT, with pilot 0/3, business 0/33, comparable pairs 0/15 and 192 missing communication slots.

## Configuration and measurement contract

New registrations use `upgrade-paired-registration-v3`, an explicit boolean `compact`, and one exact `response_observation` policy for all seven groups. The default is `{"mode":"disabled","sha256":null,"bytes":null}`. Enabled mode is `consumed-response-v1`, with a lowercase SHA256 and a positive integer byte count; boolean counts, extra fields and mixed disabled metadata are rejected.

Driver configuration, direct permit issue, driver tick, campaign freeze and private campaign validation check the policy against the already validated runtime/startup/controller chain. Configure checks all seven groups before publication; the other boundaries check their original group without introducing seven-group scans into the hot path. Enabled groups can have different private paths but must have identical observer source bytes.

V3 measurements use `registered_live_measurement_v2` and include `observer_configuration`. Report-v2 requires the original registration reference, campaign/runtime/startup chain, permit, complete original external closure measurement and pinned finish. Copying authentic observer pins cannot authenticate modified timings, status, interventions, costs or finish. Invalid rows cannot count as pilot/business passes or qualify pairs. Missing rows retain the full 33-run, 15-pair denominator. Legacy registrations remain readable and explicitly `legacy_unverified`; they cannot acquire the new qualification by changing an evidence-class label.

This verifies configuration and the external recorded measurement chain. It does not prove actual `LOADED`, active-client adoption, model completion, business acceptance or cryptographic resistance to a writer replacing the entire authoritative evidence store. Those are separate observations and trust boundaries.

## Cleanup repair

The observer tracks whether it created a temporary file and whether rename succeeded. Failed exclusive open never removes another writer's temporary file. Successful rename no longer triggers a redundant unlink. Write, close and rename failures still clean up the observer's own temporary file; cleanup-close failure preserves the original exception. Original ownership, mode, directory and inode checks remain in place.

Observer SHA256: `145414850a6cfeb039c61e13c159d1f487571fa3250fed7c0f96db9db847be17`.

## Correctness verification

- Focused Python suite: 120 tests executed. Six errors were subcases of one new configure-policy test, whose mock used a stale runtime reference list. The fixture was repaired and that test passed on its separate rerun. No other failures/errors were reported. The original failed result is retained; this is not described as a clean 120-test run.
- Independent comparison audit: 22/22 tests passed, then the additional forged-cost test passed separately, 1/1. The final unified 23-test file is included in the successful 2138-test full regression below. The fixtures use real on-disk pins, permits, closure and finish chains while mocking native campaign/runtime authority; they are offline tests.
- Node observer suite: 36/36 passed, including real filesystem fault cases and existing concurrent-stream checks. The final frozen-source rerun also passed 36/36 in 1.406 seconds, including 128 concurrent streams and 131,072 delta events.
- Complete frozen-source Python regression: **2138/2138 passed**, zero failures/errors, 943.394 seconds; 2026-10-19T07:50:35.556332+00:00 to 2026-10-19T08:06:18.968768+00:00. All monitored Python/CJS/shell source files were unchanged. Raw logs, source pins and the completed runner result are in `observer-symmetry-final/`.

## Bounded local pressure and performance

The old observer's separate cost attribution ran 2,048 local HTTP requests plus 256 parser replays, with 4,463/4,463 checks. There were five publications per request. Path checks cost 3.06–4.03 ms/request and atomic I/O 4.54–5.45 ms/request; combined they represented 39.9–46.8% of summed worker time, not controller elapsed time. Parser CPU (2.44–3.05 ms/payload) came from a separate replay and must not be added to HTTP timings. All 1,280 unlink calls in each phase returned ENOENT after successful rename.

The frozen old/new cleanup comparison then ran four AB/BA pairs, each phase with four native Bun-preloaded processes ×64 concurrent requests. Both arms received the same 68,486-byte SSE payload. Across 2,048 HTTP requests, 4,197/4,197 correctness checks passed; all 32 preload records matched their arm SHA and all eight phases ended without temporary files.

| Pair | Order | Controller new/old | Request p95 new/old |
|---|---|---:|---:|
| 1 | AB | 0.7602 | 0.9195 |
| 2 | BA | 0.8491 | 0.7645 |
| 3 | AB | 1.7354 | 1.8018 |
| 4 | BA | 2.0699 | 2.1995 |

Ratios below 1 favor the repair. Two pairs improved and two regressed. Geometric means were 1.2340 for controller elapsed time and 1.2919 for request p95. **Stable speedup and absence of regression were not established.** The slower pairs remain in the result. Only four paired units support this comparison; the 2,048 requests are not independent performance trials. Parent focused Python tests were running on the same host during this comparison. This concurrent workload is a limitation, not an established explanation for the observed regressions. No additional pressure rounds were selected to seek a favorable result.

Earlier instrumented/uninstrumented ratios of 3.57–7.46× used the old observer source and remain historical negative evidence. Neither comparison measures native multi-agent collaboration. Observation stays disabled by default; any future enabled comparison must disclose configuration, actual loading and measured diagnostic overhead separately.

## Evidence and remaining live acceptance

Evidence root: `/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/`.

- `observer-symmetry-audit/DESIGN-AUDIT.md`: independent design review, recommendations distinguished from execution.
- `observer-symmetry-final/`: this delivery's frozen regression, source pins and final publication evidence.
- `response-observer-cost-analysis/`: old-source diagnostic attribution, result SHA256 `6626b53d2cfae30c2427860a63cc29fa2062b22536c019e73d6850c9ff4702f1`.
- `response-observer-cleanup-comparison/`: four-pair raw results, report, analysis and 2,158-entry manifest. Result SHA256 `3c6944e21b759501897e26530fbf47e5bdb0775b41401803d12bb387420c888e`; manifest SHA256 `d1fc1493ef88cad204988a48aab39b16415e571505f720c8dc5a51c6553f58bb`.

R27 retains its original startup timeout and expired overall cap; this work does not replay it, extend its deadline, replace its clients or infer remote request termination from `terminate_clients=false` stop files. No model requests were issued by this local follow-up. See [r27](VALIDATION-20261019-R27.md) and [response observation](RESPONSE-OBSERVATION-VALIDATION.md).

Live acceptance still requires actual probes and automatic hook adoption, three consecutive pilot rounds, all 33 business runs / 15 pairs / 192 communication slots, warm median improvement ≥15%, and no workload median regression >5%. Cost and efficiency remain UNKNOWN/null until measured. Shared MAC/thesis-refiner ownership remains with surface40; publishing a file-inbox notice does not prove it was read or merged.

## Final reconciliation

Both pressure manifests were independently rehashed in this delivery: 2,163 + 2,158 = 4,321 files, zero mismatches. All 47 local links in the four changed skill/runbook/validation documents resolve. Two new reports in the surface40 inbox were reviewed and pinned in `shared-owner-experience-review.json`: missing native control sockets must not trigger composer fallback; queued_command, body exposure and native receipt stay separate; missing journals do not prove NO_INPUT. These are source-report observations, not independent live reproductions. The shared maintainer owns any shared patch merge.

The overall `qualification_scope` label is conservative: it requires every registered row to pass the progression contract as well as its evidence checks. A genuine failed business row can therefore leave that overall label unverified even when its per-row configuration check passed. Inspect the explicit per-row checks to distinguish configuration evidence from business outcome.
