# 2026-10-18 压力测试与真实协作验证

当前结论：**组件压力与本地完整回归通过；真实协作提效仍未证明。**
本次 review_mode 为 `solo_self_review`。Jev/TypeSafe 仍为 advisory/mock，
真实 campaign 使用 rules。旧会话、失败分母、原控制器及原始证据均保留。
本文替代旧效率报告中的执行进度；平台架构分析仍可参考旧报告。

## 已完成的验证

| 验证层 | 实际规模与结果 | 能证明的范围 |
| --- | --- | --- |
| 进程竞争压力 | 24 进程 × 100 轮 × 3 模式；7,200 次 reserve；PASS | 同会话、重叠范围、独立范围的文件系统租约竞争 |
| 独立任务吞吐 | 2,400/2,400 成功，registry busy/timeout 0 | 公共登记锁不再把这批独立任务错误拒绝 |
| 冲突与释放 | 每个冲突轮恰有 1/24 获权；31,200 次终态 release 检查；重复 owner、历史丢失、意外错误均 0 | 互斥、非法释放拒绝、幂等释放和历史保留 |
| 当前源码完整回归 | 1,743/1,743；0 failures/errors/skips；326.637 秒 | Python 3.14、本机离线 suite；239 个监测文件测试前后 SHA 相同 |
| 原生失败定向回归 | 25/25；2.032 秒 | 原生日志失败分类、增量观察、崩溃复用、watchdog、完整分母 |
| r8 原日志只读观察 | 一次 observe 找到原 turn 的终态错误；input_operations=0 | 新 detector 能识别已存在的真实失败，不等于重跑 r8 |
| r8 三客户端启动 | 1 Codex＋2 Claude 的 readiness、自动 Pre/Post hooks、adoption 全部通过 | 三个真实客户端启动并采用私有配置 |
| r8 业务实验 | HALTED/FAILED；pilot 0/3、pairs 0/15、closures 0/33，业务派发 0 | 提效、真实 callback 和业务验收尚未证明 |

压力测试耗时 821.931 秒，reserve 操作延迟中位数 0.04084 秒、P95 0.19112 秒。
该分位数来自组件样本，不是模型响应或端到端协作延迟。压力和本地回归均
`live_model_requests=0`，不能替代真实配对实验。报告落盘在完整回归之后；
最终源码复核应单列后续文档更新，不能声称这些新增文档参与了此前全测。

## 实测推动的修复

1. **登记锁竞争。** 首轮压力只检查安全性，独立任务仅 389/2,400 成功，
   2,011 次 registry busy；其 PASS 标记已另发更正，原件不改。
   现在仅 reserve/release 的短事务可等待 OS 锁最多 5 秒，获锁后复核原
   path/device/inode/mode/uid。action 锁仍立即拒绝；不会重试派发或 callback。
2. **Codex 自动 hook 关联。** r7 把外层 `functions.exec` call_id 与内层
   `CommandExecution` hook id 直接比较，导致已有真实 hooks 被拒绝。
   新代码只在同 thread/turn、唯一精确命令、原 call/result 区间和成功输出
   同时成立时接受关联；拒绝歧义、跨 turn、失败输出及重复调用。
3. **原生错误及时收口。** 新 driver 通过 `upgrade_failure_watch.py` 观察冻结
   后的原 Codex 日志，每 tick 最多 256 条。原生 `event_msg/task_complete`
   的非空 error 才触发 FAILED；旧错误、引用和工具错误不触发。
   未读完或末尾半条记录时禁止推进，原 1,800 秒 watchdog 仍有效。
   首份错误证据独占落盘并在崩溃后精确复用，保留已完成测量和全部失败分母。

r8 已经发布 freeze/bootstrap，原 Codex 已读取 bootstrap，随后在
`2026-10-17T19:48:21.105Z` 报 HTTP 400 `invalid_encrypted_content`。
此错误不能单独确定鉴权、服务负载或服务端根因。readiness 成功没有证明后续
多轮请求兼容。原冻结 v7 已据原生证据收口；未使用新 detector 重启、替换或
重放 r8。新修复尚无新的真实 campaign 使用证明。

## 经验会话与论文工作流

用户指定的经验会话为 workspace
`6FB20312-9E66-43DB-AF09-92C3B18E1296`、surface
`A93448E1-E329-493D-9B45-568D5FC0DAD0`。
已创建询问 `UPGRADE_EXPERIENCE_20261018_R8_01`，并在其既有 maintenance inbox
提供诊断补充。原生发送尝试因缺少控制 socket 在输入前结束；未粘贴或补键。
截至本报告，三个指定回件路径均不存在，也未观察到其原生日志消费 marker/SHA。
**文件可用、对方已读、对方回复是三件事；双向咨询尚未完成。**

实际自动 hook audit 显示该独立 Codex 没有 managed-daemon 父进程；现役
0.4.34 的 `collect_hook` 因此不产生 native proof，而 inbox discovery 需要该
proof。这是实际父链、hook audit 与源码路径诊断，不是对方阅读回执。
隔离维护副本的修复未安装，没有改变共享 hooks 或原会话。

从该会话已公开的维护记录与论文工作区既有经验吸收并写入 skill/runbook：

- 文件请求、hook 曝光、正文 SHA 消费、原生接收、callback 与业务验收分别留证。
- Word/Zotero 保持单写者；NotebookLM 共享按账号判断；锁和租约不是验收回执。
- 小 Word fixture 成功不能替代整章规模测试；保留超时、未测项与失败分母。
- 自动 workflow hooks 只观察，不以历史 callback/reask 状态锁死普通工具或 Stop。

这些规则已落实到 upgrade 文档和相关竞争/收口检查；本次没有操作论文文件、
Word/Zotero 或 NotebookLM，也未声称已经完成这些领域的真实负载测试。

## 未完成项及继续条件

1. 定位并解决原生请求兼容性失败，保留原 r8 终态和请求证据；不自动替换失败
   会话或重放原 campaign，不把一次轻量请求成功当作解决证明。
2. 在可用且授权的真实实验中，同三会话 pilot 连续 3/3，然后完成 12 warm＋3
   cold pairs；最多 7 组、33 slots、同时最多两个 Claude 执行业务。
3. 逐轮验证原生投递、真实 callback、独立验收、释放及自动续派。正确性通过后，
   warm 配对改善中位数需至少 15%，各 workload 中位退化不超过 5%；成本增长
   超过 10% 披露，缺失 usage 记 UNKNOWN。不得用组件提速代替这些门槛。
4. 获取指定经验会话的实际回复，核对其案例、失败条件和证据，再做相应实地验证。
   当前消息入口不可用不应阻止独立修复，也不允许声称沟通已经完成。

## 可复核证据

本地证据根：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。
以下相对路径均基于该根；原生记录与凭据不纳入公开仓库。

| 证据 | 路径 |
| --- | --- |
| 完整回归、逐项 events、源码前后 pins | `validation/r8-pressure-and-full/repair-full-04/` |
| 修复后完整压力及原压力更正 | `validation/r8-pressure-and-full/multiprocess-stress-02/result.json`、`validation/r8-pressure-and-full/multiprocess-stress-01-correction.json` |
| 压力执行脚本 | `validation/r8-pressure-and-full/stress_components_v2.py` |
| r8 原生错误与只读新 detector 验证 | `validation/r8-native-provider-failure/terminal-error-01.json`、`validation/r8-native-provider-failure/readonly-watch-02.json` |
| 定向回归 | `validation/r8-native-provider-failure/focused-02/` |
| 经验询问诊断 | `maintenance/standalone-caller-v1/hook-discovery-diagnosis-02.json` |
| 经验询问补充与最新观察 | `maintenance/standalone-caller-v1/diagnosis-file-supplement-02.json`、`maintenance/standalone-caller-v1/request-observation-04.json` |

原实验最终报告：`/Users/lzhs/u8/driver/final-report.json`，结论
`INSUFFICIENT_EVIDENCE`；原 halt：`/Users/lzhs/u8/schedule/halt.json`。
全测日志 SHA256：`4652b0eff12c10e26e2192ca18fca10beabf0d00fba41296017e49b06116458e`。

历史失败也保留：原全测 1,500.37 秒超时、57E 且无最终 traceback，根因未确定；
失败模块复核 100/100 通过不抹除原失败。失败收口新增测试曾因 fixture 缺字段
出现 1E，修正 fixture 后 25/25 通过。r7 的原日志复核不改变其 pilot 0/3。

复现本地完整回归：`python -B scripts/test_all.py`。本次没有放宽测试内部超时、
跳过测试或安装共享 MAC。GitHub Actions 与真实活跃客户端采用分别报告。
