# Multi-Agent Collaboration Upgrade

An experimental orchestration layer for Codex-supervisor and Claude Code
executors in cmux. It aims to shorten task preparation, dependency handoffs and
callback settlement while preserving native delivery evidence and independent
acceptance. **Native collaboration speedup has not been demonstrated.**

中文：保留 cmux 作为终端，MAC 作为原会话投递层，upgrade 负责本地依赖调度与批量推进。
当前是独立私有实验技能，尚未推广到生产共享技能；Jev/TypeSafe 没有接入在线服务。

## Implemented behavior

- Dependency-ready scheduling, per-session FIFO, durable claims and one writer
  per scope. Decisions use local rules; `offline_mock` is test-only.
- `drafts` previews forthcoming task packages; `next-action` explains recorded
  blockers without dispatching work or running business checkers.
- B/v3 `advance --run RUN --max-steps 12 --max-seconds 45` batches original gated
  actions, selects independent lanes fairly, imports original callbacks and
  filters obsolete handoffs. Ready local business is handed back promptly.
- Native START, local business, independent ACCEPT and FINISH remain explicit
  supervisor actions. `advance` does not wake idle models, and checks its time
  budget between synchronous actions.
- Frozen source, session, model, authorization and input pins; original native
  receipts, callback records and business acceptance stay separate.
- Materialized private controllers include their TOML dependencies, licenses
  and provenance in the verified manifest.

This repository is separate from
[multi-agent-collaboration](https://github.com/lzhs1995/multi-agent-collaboration).
New v3 experiments require an explicit private context and pinned complete
controller. Historical attempts retain their original controller and evidence.
Shared MAC/thesis-refiner integration has a separate maintainer.

## Current validation — 2026-10-19

Tested code: `9a09f32296f160b32bd3fb7d74ee93ad37715747`. Subsequent delivery edits
are documentation only.

本机 Python 3.14 完整回归原次于 2026-10-19T10:09:34.828919+00:00 结束：**2171 项，0 failures、0 errors、0 skipped，1226.361 秒**；被监测脚本前后 SHA256 一致，零模型请求。

- [CI: Ubuntu/macOS × Python 3.10/3.14](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39232997253)
  succeeded in all four jobs: 2,171 total tests each, zero failures/errors,
  with 106 Ubuntu and 96 macOS skips per job. Skipped tests are not passes.
- Component pressure: 24 processes, 7,200 reserves, 31,200 terminal-release
  checks and 2,400/2,400 independent successes; no observed double ownership,
  registry busy errors or lost history. This is not a native efficiency result.
- Claude Code was upgraded from experimental 2.1.170 to official 2.1.303.
  Three CLI entrypoints and both R29 native processes were verified on 303.
  Original sessions, binaries and failure evidence were retained.
- R29 ended **TIMEOUT / INSUFFICIENT_EVIDENCE**: pilot **0/3**, business **0/33**
  (1 TIMEOUT + 32 MISSING), comparable pairs **0/15**. The ordinary-message
  plan retains 192 unique IDs, with **0/192** verified closed loops; this does
  not mean that 192 messages were sent. Only the first of seven prepared groups
  actually launched its 1 Codex + 2 Claude clients.
- Both Claude lanes requested `claude-opus-5-5` but produced no assistant record.
  A logged RPM 429 and an empty stream; B's underlying cause remains unknown.
  Goal checks eventually paused themselves. Stable Goal/loop operation, costs
  and at least 15% collaboration speedup remain unproven.

The tested code and the native experiment are distinct: R29 used immutable v24
(`b0ac022`), not the later packaging repair. The prior green CI that missed 13
local packaging failures is retained in the validation history.

Read the [final R29 report](docs/VALIDATION-20261019-R29.md),
[Claude version evidence](docs/CLAUDE-VERSION-VALIDATION.md),
[41-category issue ledger](docs/COLLABORATION-ISSUE-LEDGER.md), and
[15-item community review](docs/COMMUNITY-ADOPTION-20261019.md).
The ledger is not an exhaustive manual review: 923 scanned files remain unread
in full, community mechanisms have explicit test gaps, and surface40's reply
and shared integration are still pending.

## Run offline checks

Python 3.10+ is required. Real private experiments additionally require macOS,
cmux, authorized native clients and their model service access.

```sh
rtk proxy git clone --branch upgrade/recovery-v2-20261014 https://github.com/lzhs1995/multi-agent-collaboration-upgrade.git
rtk proxy python3 -B multi-agent-collaboration-upgrade/scripts/test_all.py
```

Tests do not send live model prompts. For private v3 preparation, start with
the [isolated experiment runbook](docs/ISOLATED-UPGRADE-RUNBOOK.md) and
[SKILL.md](SKILL.md). Do not run the inherited production installer or replace
shared hooks. The [v2 runbook](docs/LIVE-REPLAY-RUNBOOK.md) and
[old validation](verification/ORCHESTRATION-VALIDATION.md) are historical;
the former nine-run benchmark is not the current acceptance matrix.

The current full native registration has seven groups, three consecutive pilot
passes, 33 business runs, 15 pairs and 192 ordinary communications. A1 may run
both independent Claude lanes in parallel. Correctness and intervention
requirements come first; efficiency acceptance additionally requires at least
15% warm paired median improvement and no more than 5% median regression in
each workload class. Failed and missing samples remain in the denominator.

MIT. cmux, Codex, Claude Code, OmO, codeg and TypeSafe are separate projects.
