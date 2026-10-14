# Evidence review and integration decision

Updated 2026-10-15 using the original Claude's accepted 2026-10-14 research.
The evidence corpus preserves 82 fetch records and 48 independently verified Git
blobs. Failed downloads, including a zero-byte SDK fetch, remain in the manifest;
an unsuccessful fetch is not source evidence. The private report was accepted
with recorded limitations, not as a live orchestration benchmark.

## Pinned sources and corrections

| Project | Revision | Verified conclusion |
| --- | --- | --- |
| Historical collaboration baseline | `788c007ae1150b1d379766367c810665698c487d` | Preserve identity, native delivery, callback and closeout semantics. Live replay separately pins its complete installed controller. |
| OmO | `562d7ca7eec13bd3c88fc970fa057365f3b7b6b2` | Task/dependency concepts are useful; its team sessions and delivery runtime are not imported. |
| codeg | `7c322eb1945a2159cd8445390062c74bfca5e88c` | Delegation state and queue/recovery behavior are design references; this revision does not include per-request overrides. |
| TypeSafe skills | `65a39f393687675ce170e6094757de20370365b9` | A coding-agent skill teaches API usage; installing it does not make Jev an API-free replacement for a coding model. |

OmO's pinned code treats a missing blocker as satisfied and includes
substring/requeue recovery behavior. This project deliberately rejects missing
dependencies and requires exact original native evidence; it does not copy those
behaviors. Its `omo` candidate borrows dependency-ready scheduling only.

codeg's chat queue is in memory and dequeues before sending. Its persistent task
queue is folder-oriented. It therefore does **not** establish a durable
per-session FIFO guarantee for chat delivery. The upgrade's persistent session
FIFO is its own implementation. Per-request overrides appeared in the then-open,
unmerged [PR #879](https://github.com/spacering-net/codeg/pull/879), not in the
pinned main revision. No codeg ACP/UI/runtime is embedded here.

TypeSafe confidence is advisory evidence, never authorization. The reviewed
Noul interface does not provide confidence; a future adapter cannot fabricate
one to fit the local type. Jev, TypeSafe SDKs and third-party model APIs are absent
from the real execution path. The current live provider is deterministic rules;
the offline mock is neither a TypeSafe integration nor a measurement of its value.

## Official source, proposals and local results

- The collaboration baseline's native receipt and callback fixes remain hard
  boundaries. A scheduler decides eligibility; it cannot declare a prompt
  received or a report accepted.
- OmO [PR #1726](https://github.com/code-yeongyu/oh-my-openagent/pull/1726) was a
  draft integration proposal at the snapshot. Its separate team/task/inbox
  runtime is not evidence of a compatible installed cmux integration.
- codeg PR #879's override design and recovery issue discussions are proposals
  or operational lessons unless the pinned source implements them.
- TypeSafe skills [issue #4](https://github.com/typesafe-ai/skills/issues/4)
  describes a noninteractive installation reporting success without files.
  [Issue #5](https://github.com/typesafe-ai/skills/issues/5) and
  [issue #11](https://github.com/typesafe-ai/skills/issues/11) are community
  confidence/cooperation proposals, not evidence of subscription entitlement or
  a deployed local decision engine. The retained corpus records the wider issue
  and SDK audit, including failed fetches, without upgrading proposals to facts.

## Local implementation and remaining evidence

`decision_provider.py`, the durable coordinator, original-controller transport,
runner, corpus workload, isolated checker, freeze and v2 verifier implement the
bounded integration. All model work belongs to the existing CLI sessions.
Source/interface failures remain distinct from provider outages and transport
uncertainty. An unknown callback or incomplete native tool result cannot release
an active task merely to make a replay continue.

The original research review's six implementation findings informed this work.
Its suggested additional absolute-count promotion threshold was not adopted;
the approved criterion remains at least 15% improvement in an eligible median
metric, after all nine correctness and contract checks pass.

The old seven-node 790→600 second estimate (24.051%) and the new four-node policy
probe (170 / 105 / 125 seconds) are both offline calculations. No artificial
delays are used to make live outcomes reproduce them. The latest completed full
suite is 1,404 offline tests. The nine-run real campaign and shared deployment
remain separate validation stages; see
[ORCHESTRATION-VALIDATION.md](verification/ORCHESTRATION-VALIDATION.md).

The designated original Claude is waiting for the finalized replay task after
its research was accepted and closed. This is `WAITING_DEPENDENCY`, not evidence
that it is occupied by another user's task or that a new handshake has failed.
Raw cmux focus/daemon identity does not override authenticated native caller
identity. Historical failed attempts remain preserved under their old controller.
