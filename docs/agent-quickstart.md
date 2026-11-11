# Agent quickstart / Agent 快速入門

[English overview](../README.md) · [繁體中文完整介紹](../README.zh-TW.md) · [Canonical agent instructions](../AGENTS.md)

Use this page with Codex, Claude Code, Gemini CLI, OpenCode or another file-reading assistant. It defines a shared reading and offline-planning workflow; it does not certify all native clients.

## Choose the supported scope

| Available capability | You can do | You cannot infer |
|---|---|---|
| Read files | Understand roles, four modes and five rules; prepare a handoff | Automatic skill loading, message delivery or model execution |
| Read files + shell + Python 3.10+ | Validate and schedule the supplied offline graph | Your own client is a supported native graph executor |
| Authorized macOS cmux environment + supported Claude/Codex controller | Follow that controller's identity and native evidence gates | Main has cross-workspace/Herdr support or proven efficiency |
| Gemini/OpenCode native session | Read rules and use offline tools if available | A Gemini/OpenCode transport adapter exists here |

State missing capabilities and continue authorized offline work without fabricating a session, ACK, callback or acceptance.

## Execute the offline example

From the repository root:

```sh
python3 -B scripts/orchestration_planner.py --help
python3 -B scripts/orchestration_planner.py validate --plan tests/fixtures/orchestration-plan.json
python3 -B scripts/orchestration_planner.py schedule --plan tests/fixtures/orchestration-plan.json
python3 -B scripts/orchestration_planner.py benchmark --plan tests/fixtures/orchestration-plan.json
```

Actual check on 2026-11-10, using Python 3.14 and the unchanged `4bedae2` runtime baseline: all three action commands exited 0. Validation returned `PASS` for 7 nodes. Scheduling returned 5 waves, with 2 parallel waves. The benchmark returned:

```json
{
  "evidence_class": "offline_schedule_estimate",
  "serial_seconds": 790.0,
  "scheduled_seconds": 600.0,
  "wall_time_reduction_percent": 24.051,
  "speedup": 1.317
}
```

These values come from assigned fixture durations. The output's `meets_fifteen_percent_target: true` applies only to that estimate, not the live acceptance gate. The commands do not contact models, submit prompts, install hooks or start agents. At the historical documentation/fixture checkpoint, the full offline suite passed all 1,290 tests on Python 3.14; that count is not the integrated runtime suite. `scripts/test_all.py --help` is **not** a help command: the script unconditionally starts its test suite.

繁體中文：三條動作命令已實際通過。7 個節點排成 5 批，其中 2 批並行；790 秒與 600 秒均來自測試資料預設時長，24.051% 不能寫成實際 Agent 提速。範例不需要原生會話或供應商帳號。

## Prepare an honest handoff

Record the objective, source revision/hash, dependencies, named owner, write scope, expected result, acceptance check and next action. A live handoff additionally needs actual session and controller evidence. Synthetic fixture IDs must stay synthetic.

Keep a single writer for each shared output. Parallelize independent deliverables; do not assign two agents to overwrite one file. The current example supports `claude`/`codex` executor labels and their matching native transport labels only. Another agent can inspect or invoke the planner, but it must not masquerade as a native executor.

For a four-chapter project, the target workflow is one Herdr team per cmux workspace: a supervisor owns each chapter's integration, and a senior supervisor owns cross-chapter reconciliation. Pure-cmux teams remain a valid workflow. Keep the generic MAC skill and thesis-refiner domain skill; a runtime or diagram does not replace their acceptance gates.

繁體中文：交接記錄目標、來源版本／雜湊、相依關係、負責人、寫入範圍、預期成果、驗收方式及下一步。只有實際採用原生通道時才填入已驗真的會話證據。每項共用成果只設一名寫者，章內與跨章整合各自有清楚責任。

## Check the evidence level before reporting

| Claim | Evidence required |
|---|---|
| Instructions were read | The intended file's complete relevant content was available to the agent. |
| Offline graph passed | Original command result and exit status for the actual graph. |
| Native notice arrived | A fresh user message containing the attempt's unique marker appears in the bound original receiver's native record. A file-reference notice proves receipt of that notice only. |
| Result was consumed | The receiver read the full result and recorded the corresponding source/hash. For a file-reference notice, verify the exact UTF-8 body against its declared byte count and SHA-256 before reading it. |
| Result was accepted | An authorized reviewer checked the task's acceptance criteria. |
| Live efficiency improved | Comparable real executions and correctness/interaction evidence, not a fixture estimate or CI. |

Receipt does not imply reading; reading does not imply acceptance. A SHA/path notice does not require the full body to appear as a native user message. Keep the notice receipt, body verification, full-body reading and acceptance as separate evidence. Reconcile uncertain outcomes against the original attempt. Review once, then only the reported issues, with a total budget of at most three rounds.

During an active authorized collaboration episode, publish progress at least every 60 seconds and notify on stop. Use the supported interrupt/queue route; do not invent recurring model prompts to simulate a heartbeat. Interrupted work resumes from original intent and evidence instead of replaying an unknown action.

繁體中文：原接收端新增含唯一標記的使用者訊息，只能證明通知收到。若通知以路徑和 SHA 指向正文，另核對完整 UTF-8 正文的位元組數與 SHA-256，再讀取正文；不要求正文另成一則原生使用者訊息。收到、驗真、讀完、接受及釋放資源分別記錄。進行中的已授權協作至少每分鐘回報，停止就通知；只有實際通道支援時才使用插話或排隊。結果不明只讀回原次；審查最多三輪，不因恢復重置預算。

## Runtime and publication boundaries

This source tree includes experimental startup, retained TASK owners and [inquiry/continuity capabilities](task-capability-extension.md) through [`baa2641`](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/tree/baa26418931cb488a99c0dad84ca8aa8261c7298), alongside the historical contract in [SKILL.md](../SKILL.md). Source integration does not establish the broader target architecture or authorize switching controllers. Formal acceptance is still **0/33 business runs and 0/15 pairs** at the 2026-11-11 checkpoint.

The installed MAC and thesis-refiner skills remain separately maintained. Do not run `manage_install.py install --apply` as part of this quickstart, change global hooks, restart sessions or relabel failed attempts. Consult the authorized current skill/controller before live operations. Older documents are provenance, not a fresh authorization or universal compatibility promise.

Use portable public examples and keep credentials/private transcripts out of commits. Read [SECURITY.md](../SECURITY.md). Passing this quickstart does not prove whole-history sanitization.
