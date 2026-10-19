# R28：编排推进与原生压力验证

截至 2026-10-19 09:00 UTC，候选实现及独审修复已冻结，完整回归已开始；本轮原生客户端尚未启动，真实提效未证明。此页随原始实验进展更新，R27 的失败结果保留。

## 目标和可比较条件

保留 cmux、MAC 原会话投递和独立业务验收；B 通过 `advance` 批量推进就绪节点，减少主管逐命令编排与无谓等待。A1 同样允许两路 Claude 并行，不能人为串行基线。Jev/TypeSafe 当前仍为规则或离线接缝，没有新增在线模型调用。

完整矩阵为 7 组、33 轮业务、15 对（12 warm、3 cold）、192 条普通通信；首先连续通过 3 轮 pilot。每组原生 1 Codex＋2 Claude；实现/独审的辅助 Codex 不计入实验客户端。普通通信不增加业务 ACK，握手 ACK 和正式 callback 保留。

验收要求先满足正确性、原次投递和人工干预合同，再判断 warm 配对中位改善是否至少 15%、每类中位退化是否至多 5%。B 不允许人工追加 prompt；模型服务失败、未知投递和未完成槽位保留原分母，缺失费用和时间不补成零。

## 固定时间与原次

- 本轮开始：2026-10-19T08:36:18Z。
- 最终交付上限：2026-10-19T17:36:18Z（台北 10 月 20 日 01:36:18）。
- 原生实验最多 14400 秒，且不能超过原交付上限；压缩或恢复不重置时限。
- 旧会话和旧控制器保留；新实验使用七组私有 runtime 和精确 14 条 socket/lock 路径。失败不自动替换或重放。
- 独立 watchdog 发布停止输入；wrapper 到截止保留尚存原进程的 PID 并返回 UNKNOWN。停止文件不证明远端 API 取消或原生 Goal 终止。

## 已取得证据

24 个本地进程、100 轮、3 个锁场景完成：7200 次 reserve，31200 次 terminal release checks，独立成功 2400/2400；冲突双 owner、registry_busy、unexpected_errors、lost_history 均为 0。操作中位 0.0389424 秒，p95 0.174635 秒，总计 372.760 秒。这是组件压力，不能替代原生模型实验或跨版本提速比较。

启动 wrapper 的三个受控时钟反例通过：墙钟截止、墙钟回拨、启动即过期。执行原 AST 循环，验证不无限等待子进程、保留原 PID、以 code 2 收口；没有创建模型请求或真实子进程，也不证明操作系统硬实时性。

四个准备/启动脚本独审已完成；实际导出、物化、启动与采纳须由后续原始证据证明。advance 的独立增量审查确认两项修复：local handoff 在同步通信前交回，末尾依状态与原生接受记录过滤过期指令。定向验证为修复版 29/29 加最后新增反例 1/1，不写成最终完整套件通过。本轮完整回归于 09:00:39Z 开始；旧 2138 Python / 36 Node 结果不冒充本轮结果。

## 经验采用与范围

[问题台账](COLLABORATION-ISSUE-LEDGER.md) 重建 41 类，固定 1045 文件库存与 79 个分类来源；966 文件未分类，不能声称逐件人工审阅完成。[社区矩阵](COMMUNITY-ADOPTION-20261019.md) 覆盖 15 条线索，区分固定源码、间接支持与正文/项目身份缺失。

surface40 继续独占共享 MAC 与论文精炼助手。本轮询问已发布到其维护 inbox，但两种直接输入入口被 WORKSPACE_SCOPE_DENIED 拒绝，没有实际消息输入。尚无本轮回复；文件发布不算收到、已读或合并。upgrade 私有实验不等待共享层变更。

## 原始证据

- [本轮状态](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-implementation/checkpoint.json)
- [锁压力结果](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-implementation/multiprocess-lock-pressure/result.json)
- [wrapper 时钟反例](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-implementation/wrapper-deadline-validation.json)
- [准备与启动独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-plan-execution/script-review.md)
- [脚本固定清单](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r28-script-manifest.json)
