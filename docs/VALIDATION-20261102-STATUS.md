# Upgrade and JEV: current evidence — updated 2026-11-03

Status: ACTIVE / NOT_ACCEPTED. Current implementation work is CODEX_SOLO;
review is `solo_self_review`. The original objective is usable Codex + two
Claude collaboration across the three availability states, with separate
upgrade-vs-MAC and JEV advisory-off/on efficiency measurements.

## Code verification

Candidate: `adc70616f443658273da1ca614667208d1148036`.
The original local full suite finished with 3,120 tests, zero failures/errors,
one skip, exit code 0, in 2,515.418 seconds. Four CI jobs reported 3,120 tests
with zero failures/errors:
<https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/41375226072>.
The 485-member frozen source snapshot matched the candidate. Documentation
updates after that candidate do not constitute a new tested code revision.

Local stdout SHA256: `c8d25e1711b900188d8a864bcbc4f7c8b13a44c9b6eeb3c479c3fec1016d2b58`.
Local stderr SHA256: `6522c519adb44dabe261c3a39e86437af5dbb6b8cd254f0c3774dea30388e821`.
CI log SHA256: `ea808284cdae6dd47c3c21ee434f426ce0ea37b29fea212d8329d34ac955f308`.
Source archive: 1,995,343 bytes,
SHA256 `ff8e6fb301ce49f9167ac1f9cbd3fd167d29f2a409dc3f01e12479ec550e3fb1`.

These are code checks. They establish no native task completion, automatic
client loading, continuous Goal behavior, or collaboration speedup.

The prospective Claude startup-failure repair passed 177 affected
regressions, zero failures/errors, rc0. It recognizes an original terminal API
failure before readiness and ends only that registered comparison promptly.
It is included in the 3,120-test candidate above. The earlier 3,108-test results
remain attached to `52efd3c908125c938914e64f540b8f5cff6f7cde`. See
[the repair and its limits](STARTUP-FAILURE-VALIDATION.md).

## Native attempt and actual blocker

The required station launch commands are:

```text
mg cx agentrouter-a
mg cc linxi -k linxi-s1
```

The original j5 CAS, one-shot prearm, permit ordinal 1 and release each ran
once. Codex startup probe/direct-input capability succeeded. Both original
Claude requests encountered service errors and failed readiness before the
unchanged startup deadline. The original Driver ended rc2 and the original
stop returned `STOPPED_INPUT`. Preserve clients, histories and all failed
samples; do not send more input to that stopped run.

Measured business runs: **0/33**. Formal JEV pairs: **0/15**. Native startup
argv and station selectors were verified; the observed failures do not support
a wrong-command finding for this attempt.

At 2026-11-02T00:37:33Z, two separately recorded 45-second diagnostic requests
used the original `linxi-s1` key/model/betas. Each returned HTTP 200 headers
and 24 bytes of keepalive comments, with no text, EOF or `message_stop` before
the local timeout. Gateway route records identify `linxi-paid-sub2`; the
recorded 499 is the diagnostic's cancellation, not an upstream 499 response.
Those diagnostic callers were classified `other`, so they do not certify
native Claude readiness. Original j5 native route records also show actual
503/429/502 attempts. Shared alternative routes were observed in credit,
quota or backoff states; no permanent account-health conclusion follows.

R655 then tested two new requests with a synthetic
`claude-cli/2.1.303 (external, cli)` User-Agent and a 150-second response bound,
keeping the original station key, model and betas. Both again returned only
keepalive comments, 108 bytes each, with no text, EOF or `message_stop`.
Exact gateway records 1793440761659 and 1793440761660 classified these probes
as `claude`; each selected `linxi-paid-sub2`, received an upstream 502 and
then recorded 499 when the local deadline canceled its subsequent attempt.
The observer made two client requests with no client retry; the gateway made
four upstream attempts. These remain synthetic transport diagnostics and
failed observations, with no native-client readiness or formal paired sample.

At 2026-11-02T01:43:17Z, two new fixed-route synthetic requests returned actual
HTTP429 JSON errors with `Retry-After` 40 and 28 seconds. The local station
reported the selected `linxi-paid` route's 10 requests/minute limit. This is a
rate-limit observation, not a completed response or a permanent account-failure
finding. A subsequent bounded observation starts only after both advertised
cooldown deadlines and remains separate from native/formal samples.

ROOT changed no shared gateway settings and stopped no other task. A fresh
completed response on the fixed station route is needed before a new
independently registered native comparison. Existing workspace/panes and
context-bearing sessions remain the preferred continuation boundaries.

## Successor observation — 2026-11-03

The user authorized session `01a1a1e3-349c-7f32-bd3a-5328502f6f4b` to continue
the original `01a159cf-6685-76b0-b779-b4572afab31b` task. Its current native
surface is `86272C3F-AB2B-42FB-BAEF-65A072A29F27`, in the original workspace
`6FB20312-9E66-43DB-AF09-92C3B18E1296`. Historical numeric surface references
were renumbered. Original endpoint UUIDs, controllers and attempts remain bound;
this continuation does not enroll the task in Herdr or reset j5.

At 2026-11-03T15:25:58Z, two new synthetic Claude-signature requests used the
same fixed station key, model and betas with a 60-second bound. Both returned
HTTP 200 SSE headers and 36 bytes of keepalive comments. Neither returned text,
EOF or `message_stop`; both reached the local timeout. Their overlap was
60.001 seconds, with two client requests and zero client retries. This does not
establish two usable native clients or sustained capacity. No new native input,
launch or formal sample was produced by these diagnostics.

The conservative local disk observations at 15:25Z and 15:31Z were 35.31 GiB
and 34.31 GiB. Both fall below the original 40 GiB prerequisite; no new S40
admission has been received. S40's original surface initially displayed a Hooks
review dialog and a draft; the successor preserved them. Once the original
session could be authenticated and its input was empty, the successor submitted
one protected maintenance notice through MAC 0.4.84. A fresh original user
record at 15:56:35.829Z proves notice reception. The original bridge observer
had already ended pending; independent readback verified the later record at
offset 1,862,291,175, SHA256
`e9d9b4d6d394fb7b1b9de83cbbbd8081df59f9066a0f9f8885535e7d3f3beb31`.
No repeat paste or recovery key was used. Full body consumption and a
substantive reply remain unverified; the original session displayed provider
502 errors. Owner43's original surface currently has no Claude process;
historical freeze evidence is not a current live handshake.

The proposed j6 preparation remains unexecuted. The original R663 preparation
script needs complete overlapping route responses and a fresh S40 admission
before it can register or materialize the next attempt. Do not write a passing
prerequisite record from these failed observations. See the
[ordered continuation plan](CONTINUATION-20261103.md).

## JEV implementation and measured limits

Existing code provides DP1 work-kind judgments and DP2 inbox judgments, a
one-attempt HTTP transport, per-attempt request ledger, separate advisory
registration, decision publishing and consumer/binding checks. Off-arm code
does not create a transport. Code owns identity, eligibility, write scope,
leases, delivery proof and acceptance.

The R651 real worker diagnostic returned HTTP 200 / `jev-1.14.0`, elapsed
2.8160695 seconds, input 556/output 72 tokens. ROOT's complete native tool
exposure and subsequent disposition were recorded. ROOT used the `debugging`
judgment to continue the already authorized solo investigation. Code reported
`NO_IDLE_LANE`; no lane binding or dispatch was manufactured. This establishes
one real request and one actual diagnostic consumer, not a campaign
`consumption_join`, a quality evaluation, or workflow efficiency.

Historical S1 recorded 120 requests: 119 retained wire responses and one
usage-only response. The five warm component pairs' 61.2% median time reduction
and the four-question batching result are component signals. They must not be
presented as upgrade-vs-MAC or JEV-off/on workflow gains. Separate independent
A1 contract-coverage probes found errors and undecided cases; their limitations
also remain part of JEV's evaluation, rather than being discarded.

The current public TypeSafe OpenAPI describes only `/v1/models` and
`/v1/systemone`; response usage includes input/output token counts, without an
account billing or request-ledger endpoint. Client attempt counts and response
usage are observable; authoritative account counts and actual charges remain
unverified. No such endpoint should be invented to fill that gap.

Official design references, accessed with current `gh` 2.103.3:

- [TypeSafe integration skill](https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md), MIT: atomic judgments, explicit null-case Noul, shared-state question batching and code decisions.
- [Official Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python), MIT: typed schemas and explicit retry configuration; it is not installed into the frozen experiment merely by reading it.
- [Official examples](https://github.com/typesafe-ai/typesafe-public-examples), MIT: confidence-based classification, skill suggestion, function dispatch and citation checks. Example data may have separate licensing.

The user's unlimited JEV authorization remains effective. It removes the need
to ask again about funding; it neither changes old attempt budgets nor turns
failed requests into semantic answers.

## Remaining acceptance

Required evidence remains three successful pilots, the registered 33 business
slots / 15 pairs / 192 ordinary communication slots, full quality and cost
accounting, three-state continuation, both Claude two-cycle 60-second inquiry
loops, and whole-task child count at most two with parent consumption. Warm
paired median improvement must be at least 15%, with no workload-class median
regression greater than 5%. Failed/missing samples retain their denominators.

Historical u25 handshake/callback/inquiry successes and the failed child-parent
end-to-end result remain separate. JEV S2 does not substitute for the distinct
upgrade-vs-MAC comparison. No whole-task efficiency result or production
deployment is accepted at this boundary.

The original nine-hour deadline, 2026-10-31T04:27:35Z, was missed and is not
reset. Continue useful authorized local work while diagnosing the service
dependency; do not keep launching unchanged comparisons into failed capacity.

## Local original evidence

ROOT evidence root:
`/Users/lzhs/uz/state/B/recovery-20261024/r89-u22-held-verification-v1`.

- `R646-ROOT-EXISTING-PANES-LIVE-ENTRY-v1`: frozen source and full local/CI logs.
- `R647-ROOT-J5-EXACT-S40-CAS-v1`: original exact CAS and complete receipt, independently checked.
- `R648-ROOT-J5-ONE-SHOT-PILOT-v1`: original prearm, permit/release and Driver output.
- `R649-ROOT-J5-TERMINAL-AND-JEV-CONTINUATION-v1`: native failures and original stop.
- `R651-ROOT-JEV-PRODUCTION-TRANSPORT-AND-CONSUMER-v1`: real JEV transport/ledger/native exposure/disposition.
- `R653-ROOT-BOUNDED-CAPACITY-CONTINUATION-v1`: unchanged bounded observer and both new response records.
- `R654-ROOT-LINXI-ROUTING-AND-CAPACITY-FINDING-v1`: original route record offsets/hashes and current configuration observations.
- `R655-ROOT-FIXED-LINXI-CLIENT-SIGNATURE-CAPACITY-v1`: both 150-second synthetic probes and exact gateway records.
- `R656-ROOT-J5-RECEIPT-AND-CURRENT-DELIVERY-v1`: verified existing receipt/log/consumer references and the current ROOT disposition.
- `R660-ROOT-STARTUP-CANDIDATE-FULL-v1`: original 3,120-test local/CI results, 485-member source archive and final candidate record.
- `R663-ROOT-PREREQUISITE-READBACK-AND-PREP-PREVIEW-v1`: gated j6 preparation script, still unexecuted.
- `R666-INCOMING-V291-v1`: latest original S40 full-body readback and retained prerequisite failures.
- `SUCCESSOR-01a1a1e3-20261103-v1`: user authorization, native identity, current capacity and two new bounded route observations.
