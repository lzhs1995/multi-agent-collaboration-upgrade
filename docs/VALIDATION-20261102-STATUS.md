# Upgrade and JEV: current evidence — 2026-11-02

Status: ACTIVE / NOT_ACCEPTED. Current implementation work is CODEX_SOLO;
review is `solo_self_review`. The original objective is usable Codex + two
Claude collaboration across the three availability states, with separate
upgrade-vs-MAC and JEV advisory-off/on efficiency measurements.

## Code verification

Candidate: `52efd3c908125c938914e64f540b8f5cff6f7cde`.
The original local full suite finished with 3,108 tests, zero failures/errors,
one skip, exit code 0, in 2,741.679 seconds. Four CI jobs reported 3,108 tests
with zero failures/errors:
<https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/41360493790>.
The 483-member frozen source snapshot matched the candidate. Documentation
updates after that candidate do not constitute a new tested code revision.

Local stdout SHA256: `95b77cc0d593acc73025368b223bcbfd98e9ce2c04fba6ae4954535c59b177d2`.
Local stderr SHA256: `3fe9170582f65a16ea104066c3cf0be0f38bc88a4739c0c13b15c808724c746b`.
CI log SHA256: `0b24cc936f0c19abea84ee2329b5ca7b802e92440778ada1ebd71d9d2ae78660`.

These are code checks. They establish no native task completion, automatic
client loading, continuous Goal behavior, or collaboration speedup.

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

ROOT changed no shared gateway settings and stopped no other task. A fresh
completed response on the fixed station route is needed before a new
independently registered native comparison. Existing workspace/panes and
context-bearing sessions remain the preferred continuation boundaries.

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
