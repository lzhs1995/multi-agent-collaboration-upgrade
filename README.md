# Multi-Agent Collaboration Upgrade

An independent, experimental orchestration and decision layer for the existing
Codex and Claude Code sessions in macOS cmux.

中文：在现有双 Agent 协作上增加规则决策、依赖调度、持久队列与证据验收；
复用原会话和共享协作控制器。它目前通过本地冻结的 CLI 适配器显式运行，
尚未作为全局服务或共享技能自动启用。离线测试通过不代表已证明实测收益。

This repository is separate from
[multi-agent-collaboration](https://github.com/lzhs1995/multi-agent-collaboration).
Its historical transport source is retained for reproducibility. New live work
uses a separately pinned installed immutable controller, not the old hooks in
this checkout. Shared runtime and skill changes have their own maintainer.

## What is implemented

- `DecisionProvider`: evidence-bound allow/deny/escalate decisions, hard
  authorization/identity/scope gates, low-confidence escalation and advisory
  fallback. Real campaigns use rules only; `offline_mock` is test-only.
- Three comparable scheduling policies: serial, dependency-ready `omo`, and
  durable per-session FIFO `codeg`, with one active task per original session.
- Frozen source/input/model/session/authorization pins; durable claims, leases,
  lifecycle events and original-controller transport journals.
- CLI-owned business work, an independent isolated checker, native receipt and
  callback verification, supervisor acceptance and scoped resource release.
- Nine-run counterbalanced validation. All runs must be correct and contract
  valid; promotion also needs at least 15% median improvement in an approved
  metric. No TypeSafe/Jev API call or replacement agent is involved.

## Get the upgrade and run offline checks

Requirements: Python 3.10+; real execution additionally needs macOS, cmux and the
two authorized existing CLI sessions. No credentials or subscriptions are bundled.

```sh
rtk proxy git clone https://github.com/lzhs1995/multi-agent-collaboration-upgrade.git
cd multi-agent-collaboration-upgrade
rtk proxy git checkout upgrade/recovery-v2-20261014
rtk proxy python3 -B scripts/test_all.py
```

The 2026-10-15 implementation checkpoint passed **1,403 offline tests**, with
zero failures/errors. Tests do not send live prompts. The legacy isolated
installer's real-process doctor failed; its sandbox was safely closed and the
failure is preserved. See the [validation record](verification/ORCHESTRATION-VALIDATION.md).

## Deploy and use the local adapter

Follow the [v2 live runbook](docs/LIVE-REPLAY-RUNBOOK.md): authenticate the original
pair, freeze the reviewed adapter and corpus into a private campaign directory,
then invoke the frozen `orchestration_run.py` with its trusted campaign SHA256.
The runner refuses the mutable checkout. It prepares tasks through the pinned
shared controller and verifies the original native logs and independent answers.

This does not register a daemon or make unrelated tasks use the new decisions.
Do not run this historical checkout's `manage_install.py install --apply` against
active client settings. Shared deployment requires the designated maintainer's
current release and separate evidence of active-client loading and live delivery.

Read [architecture and decisions](docs/ORCHESTRATION.md),
[source research](RESEARCH-FINDINGS.md), and
[measured validation status](verification/ORCHESTRATION-VALIDATION.md).
Historical `validate_replay_evidence.py` results cannot promote the upgrade.

MIT. cmux, Codex, Claude Code, OmO, codeg and TypeSafe are separate projects.
