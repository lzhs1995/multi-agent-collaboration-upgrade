# R28：编排推进与原生压力验证

R28 已于 2026-10-19 09:33:19 UTC 终态 TIMEOUT / INSUFFICIENT_EVIDENCE，耗时 905.064 秒。第一组 1 Codex＋2 Claude 实际启动，Codex probe/自动 hook 已观察，双 Claude 2.1.170 仅有 Goal SET，没有 assistant/tool 或启动 probe。pilot 0/3、业务 0/33、配对 0/15，真实提效未证明；33轮、15对及192通信槽位全部保留。用户授权升级后，独立 R29 使用 2.1.303 和同一 v24 控制器继续有界验证，见[版本验证](CLAUDE-VERSION-VALIDATION.md)。R28 不重启。

## 目标和可比较条件

保留 cmux、MAC 原会话投递和独立业务验收；B 通过 `advance` 批量推进就绪节点，减少主管逐命令编排与无谓等待。A1 同样允许两路 Claude 并行，不能人为串行基线。Jev/TypeSafe 当前仍为规则或离线接缝，没有新增在线模型调用。

完整矩阵为 7 组、33 轮业务、15 对（12 warm、3 cold）、192 条普通通信；首先连续通过 3 轮 pilot。每组原生 1 Codex＋2 Claude；实现/独审的辅助 Codex 不计入实验客户端。普通通信不增加业务 ACK，握手 ACK 和正式 callback 保留。

验收要求先满足正确性、原次投递和人工干预合同，再判断 warm 配对中位改善是否至少 15%、每类中位退化是否至多 5%。B 不允许人工追加 prompt；模型服务失败、未知投递和未完成槽位保留原分母，缺失费用和时间不补成零。

## 固定时间与原次

- 本轮开始：2026-10-19T08:36:18Z。
- 最终交付上限：2026-10-19T17:36:18Z（台北 10 月 20 日 01:36:18）。
- 原生实验最多 14400 秒，且不能超过原交付上限；压缩或恢复不重置时限。
- 原次实际开始：2026-10-19T09:18:14.190806Z；实际截止：2026-10-19T13:18:14.190806Z（台北 21:18:14）。启动就绪预算 900 秒；原工具 session 91351，不重复启动。
- 旧会话和旧控制器保留；新实验使用七组私有 runtime 和精确 14 条 socket/lock 路径。失败不自动替换或重放。
- 独立 watchdog 发布停止输入；wrapper 到截止保留尚存原进程的 PID 并返回 UNKNOWN。停止文件不证明远端 API 取消或原生 Goal 终止。

## 已取得证据

24 个本地进程、100 轮、3 个锁场景完成：7200 次 reserve，31200 次 terminal release checks，独立成功 2400/2400；冲突双 owner、registry_busy、unexpected_errors、lost_history 均为 0。操作中位 0.0389424 秒，p95 0.174635 秒，总计 372.760 秒。这是组件压力，不能替代原生模型实验或跨版本提速比较。

启动 wrapper 的三个受控时钟反例通过：墙钟截止、墙钟回拨、启动即过期。执行原 AST 循环，验证不无限等待子进程、保留原 PID、以 code 2 收口；没有创建模型请求或真实子进程，也不证明操作系统硬实时性。

四个准备/启动脚本独审已完成；346 个 tracked 文件已导出为 immutable v24，固定 b0ac022f9ec380a0873a17e67668a49ab1c1c64e。七组私有 runtime 与 14 条路径均已准备；仅第一组已经启动，不能把配置准备算作其余六组实际运行。advance 的独立增量审查确认两项修复：local handoff 在同步通信前交回，末尾依状态与原生接受记录过滤过期指令。

完整回归原次于 09:00:39.846094Z 开始，09:14:58.148720Z 结束，858.286 秒；2168 项、0 failures、0 errors，源码前后哈希一致。CI 39227019973 的 Ubuntu/Python 3.10 失败，其余三组被 fail-fast 取消；不得据本地 3.14 结果声明跨平台通过。缺 tomllib 与 pathlib 崩溃注入差异的兼容补丁正在独审，后续修订不改变已运行 v24 控制器。

后续兼容补丁 c40b9ce 的四格 CI 39230455689 均通过，但本地完整回归出现 13 failures：生成的私有控制器缺少 `upgrade_toml` 依赖。CI 跳过了依赖本机原生二进制的路径，绿色状态不能证明交付包完整。现已补齐真实 materializer 的两项私有模块、许可和来源说明；原 13 个失败案例所在的 12 个测试方法全部通过，并新增不依赖原生客户端安装的隔离解释器打包回归，删除任一私有依赖都会失败。root 独审已接受，修复后的完整回归和 CI 尚待完成。修复不改变 R28/R29 的固定 v24。

R28 driver/watchdog 均 exit 2，无存活 driver 子进程；`clients_terminated=false`，原生 Goal 结束仍 UNVERIFIED。两路响应观察器确有 LOADED；首请求 HTTP 200、message_start/delta/stop/EOF，但 output_tokens=0、无 content_block。A 后续 ping 后 READ_ERROR，B 后续 output_tokens=1 仍无 content_block。请求分类 UNKNOWN，另有独立 Stop prompt hook 的 `No assistant message found`；这些不证明认证错误或版本单因果。

## 经验采用与范围

[问题台账](COLLABORATION-ISSUE-LEDGER.md) 重建 41 类，固定 1045 文件库存与 79 个分类来源；另对 966 个文件完成机器扫描，43 份报告全文复核，923 份未全文复核，不能声称逐件人工审阅完成。[社区矩阵](COMMUNITY-ADOPTION-20261019.md) 覆盖 15 条线索及四仓补证，区分固定源码、间接支持与正文/项目身份缺失。

surface40 继续独占共享 MAC 与论文精炼助手。本轮询问已发布到其维护 inbox，但两种直接输入入口被 WORKSPACE_SCOPE_DENIED 拒绝，没有实际消息输入。尚无本轮回复；文件发布不算收到、已读或合并。upgrade 私有实验不等待共享层变更。

## 原始证据

- [本轮状态](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-implementation/checkpoint.json)
- [完整回归](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-implementation/full-regression.json)
- [原生 driver 原日志](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-original-driver/stdout.jsonl)
- [R28 原次终态](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-original-driver/result.json)
- [固定导出](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/source-exports/resume-20261019-live-v24/export.json)
- [锁压力结果](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-implementation/multiprocess-lock-pressure/result.json)
- [wrapper 时钟反例](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r28-implementation/wrapper-deadline-validation.json)
- [准备与启动独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-plan-execution/script-review.md)
- [脚本固定清单](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r28-script-manifest.json)
