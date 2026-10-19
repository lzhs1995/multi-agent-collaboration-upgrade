# 私有握手失败结算验证

2026-10-19。新生成的私有控制器在原次握手抛出 `TaskPackContractError` 时，复核原 root 后记录主管归属的 `FAIL/REJECTED`，避免异常留下陈旧 `PENDING` 后继续等待。本改动没有解决或重写 [R30](VALIDATION-20261019-R30.md) 的启动超时；真实双 Claude 提效仍未证明。

## 实现范围

- 在原 root-directory lock 内结算，写入异常类型、原 details 和时间；停止 ACK 轮询和后续 peer 派发。先前成功 peer 的记录保留，整组不能宣称成功。
- 仅固定发送器明确返回的绝对原 attempt 路径。双次稳定读取保存 SHA256、字节数及文件身份，不扫描目录、不修改原 journal。
- 缺失、不可读或变化的证据保留 `UNAVAILABLE`。`ORIGINAL_ATTEMPT_PINNED` 仅是诊断 pin；controller authentication 仍为 `UNVERIFIED`，不证明原生收件、`NO_INPUT`、业务接受或可重发。
- 原 `DispatchUnconfirmed` 分支保持不变。root 被替换或终态写入失败时继续抛错；写失败后原 receipt 可以仍为 `PENDING`，不能声称已持久化终态。
- 固定基底仍为 `0.4.34-d333557fb498`。它没有新版 shared `handshake_evidence` 集成；一些原错误本来就不附 details。本改动不是 [MAC PR55](https://github.com/lzhs1995/multi-agent-collaboration/pull/55) 的完整移植。
- PR55 于 `2026-10-19T11:23:09Z` 合并，head `b461edc5171b8d018338fe45655bb750c94cc3a6`，merge `ed8f7447bd7242901ec1bb76d3871ab36bbf45eb`。共享层继续由 surface:1000020040 维护。R30 已有版本目录 executable pins 与 kernel TTY/UID inventory，本次不引入共享 regex/ps 改动。

## 实际验证

| 执行 | 结果 | 证据边界 |
|---|---|---|
| 握手专项 | 29/29 PASS，11.081 秒，exit 0 | 原 stdout/stderr 未另存；执行记录保留实际工具摘要。五脚本 pins 为测试后采集。 |
| 首次四模块集成 | 42 项，41 PASS、1 ERROR、0 skip | 新 materialized settlement probe 超过 20 秒；原失败和前后源码 pins 保留。 |
| 有界栈诊断 | 定位外部 workspace identity 查询 | `_write → _touch_active_marker → _workspace_marker_paths → _workspace_key → caller_snapshot → subprocess.run`；不是 root-lock 死锁证据。 |
| 修复后 materialized 检查 | 2/2 PASS，10.043 秒，F0/E0/skip0 | 11:59:07.726932Z–11:59:17.773511Z，五脚本前后 pins 一致，20 秒上限未增加。 |

修复仅补齐离线 probe 的外部 workspace identity fixture，并捕获真实 writer 输出。完整生成 harness 的 writer、heartbeat scan、root 锁及原 receipt 重入拒绝仍真实运行。能力/传输与 workspace 查询为 fixture；零模型请求、零原生输入。两个复查用例之一与首次集成的已通过用例重合，不能把这些执行相加冒充一次新全套。

专项覆盖：输入前拒绝、PASTE_INTENT 后失败、无 details、缺失/不可读/变化证据、先成功 peer 后失败 peer、第三 peer 不再派发、原 attempt 不变、不 ACK/重发、receipt 写失败、root 替换、源码形状漂移及原不确定分支保留。独审结论为 `NO_SUBSTANTIVE_BLOCKER_IN_REVIEWED_FAILURE_SETTLEMENT`。

本报告随实现提交。新提交的 Ubuntu/macOS × Python 3.10/3.14 CI 在提交后核收；发布前不称该提交已完整回归。此前绿色 [CI 39241757006](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39241757006) 属于 `d8a158509680de53f9c8bdc8c541408c3054f605`，每格发现 2176 项、macOS skip96/Ubuntu skip106，不能移算给本改动。

## 原生验收保持开放

R30 的 pilot 0/3、业务 0/33（1 TIMEOUT＋32 MISSING）、有效配对 0/15、0/192 个计划普通通信闭环均不变。192 是计划 ID 数，不是实际发送次数。11:59:34 UTC 原次只读复查结论为 `NO_NEW_DUAL_CLAUDE_RECOVERY_EVIDENCE_IN_ORIGINAL_R30`。Claude 已升级并实际运行 2.1.303，但双路启动 probe、主任务有效输出及自动 Pre/PostToolUse 采用仍缺失；独立 `/loop` 长期验证也未完成。

本修复减少的是失败状态不明确造成的等待，尚无新增原生速度样本。不能用它解释 R30 无主输出、断言 key 无效或据此原样重开实验。原 hard deadline `2026-10-19T13:18:14.190806Z` 不延长。

## 本机原始证据

- [29 项执行记录](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/post-r30-pr55/verification/handshake-auditor-result.json)
- [首次 42 项集成](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/post-r30-pr55/verification/targeted-integration.json)
- [超时栈诊断](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/post-r30-pr55/verification/settlement-timeout-diagnostic.json)
- [修复后 2 项集成](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/post-r30-pr55/verification/settlement-repaired-materialized.json)
- [原 R30 恢复观察](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/post-r30-pr55/recovery-observation/REPORT.md)
