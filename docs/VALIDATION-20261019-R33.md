# R33：缩短派发与核收等待，修复只读操作误判

2026-10-20 终态更新。运行源码固定为 `b94b5bd48a49a8511c75468070cc6a81016c3c5d`。本机完整回归和四组 CI 已通过。原生 R33 已因实验 Codex 配额 HTTP 401 终止；完整轮次和有效配对均为 0，不能宣称完整压力测试通过或达到 15% 提效。

## 已落实的修复

- 相同优先级的就绪节点按 PENDING → PREPARED → PREPARING 推进。先领取独立通道，已准备好的通道立即派发，再等待另一通道握手，减少握手之间的空档。
- 同一次 `advance` 已产生仍然有效的核收操作时，刷新其证据后返回，避免让该操作继续等待下一项同步传输。原来的本地业务操作优先级保留。
- 新注册的 metrics v4 使用 `output-effects-v1`，区分业务活动和潜在写入。成功、完整的 Claude 原生 Read 及任务包规定的纯 `ls` 可认定只读；失败、未完成和无法识别的调用仍为潜在写入。
- 修复 output_root 紧跟分号时漏计业务操作。Claude 在 callback 前完成输出自检，Codex 在 ACCEPT 前读完报告、测试和结果。任意 `functions.exec` 包装不自动获得只读豁免。

R32 旧控制器、metrics、终态及原生输入保持原样。两份已撤回的误发提案没有纳入本次修复。

## 本机验证与冻结

| 验证 | 结果 |
|---|---|
| 调度与核收顺序聚焦 | 42/42，0 失败、错误、跳过，34.720 秒 |
| 输出效果聚焦 | 45/45，0 失败、错误、跳过，84.711 秒 |
| 完整回归 | 2280/2280，0 失败、错误、跳过，914.098 秒 |
| 源码稳定性 | 完整回归前后 366 文件 SHA 一致；只读导出逐字匹配 |
| 资源竞争压力 | 复用未改源码的原 7200 次 reserve、31200 次 release 检查；本轮未重跑 |

聚焦用例包含于完整套件，不相加为独立测试数量。完整回归已经完成，不因随后更新文档而重新运行。

- [验证汇总](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-effects-and-scheduling/VALIDATION.json)，SHA256 `b14ef0324b994d0ddf942c04f1224a0db41d836ab2e4db83711e96f1d3528d0b`。
- [冻结导出](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/source-exports/resume-20261019-live-v28/export.json)，SHA256 `dee1e4422977ef6fba0260a6ac24e875025bbcc86421e6a78334715fe755d44f`。
- 精确源码提交的 [CI run 39280150950](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39280150950) 已完成，四格 success。macOS Python 3.10/3.14 各运行 2280 项、跳过 108；Ubuntu Python 3.10/3.14 各运行 2280 项、跳过 118。跳过项不算实际执行通过。最终元数据、四份原始日志及 SHA 已保存到 [CI 汇总](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-closeout-20261020/ci/summary.json)；首次 queued 快照作为历史保留，未重复触发。

## 原生 R33 终态

- 15:43:25 UTC：独立 watchdog ready；15:43:27 driver 发布首 permit；15:43:29 原客户端启动调用返回。
- 15:48:47：Codex 和双 Claude 已完成独立 adoption；15:49:16 已发布 bootstrap。
- 15:52 UTC：实验 Codex 已在原会话执行 pilot-01 START，并提前发布四节点草稿。
- 15:56:36.286：Claude A 的完整原生握手 ACK 已出现；15:58:36.182449 已记录 inventory 的 DISPATCH_INTENT，15:59:37.682278 已记录 DISPATCHED。ACK 原行 SHA、device/inode 已由根核对。该段为 119.896449 秒；R32 同名阶段为 335.442815 秒。这是跨轮次的补充观察，不能替代注册配对或宣称总体提效。
- B 的完整握手 ACK 在 16:03:38.940 UTC 出现；16:04:15.987648 到 PREPARED，之后未派发。双 Claude 均真实启动并握手。
- A 的原生 callback 在 16:04:15.106 UTC 到达，16:07:03.670660 到 EVIDENCE_READY，间隔 168.564660 秒；尚无成功 ACCEPT。
- Codex 在 16:10:20.104 UTC 记录 HTTP 401 配额耗尽，request id 为 `20261020001019818609739E0LOLeM1`；driver 16:10:22.262175 HALT，wrapper 16:10:26.137845 结束。七组 STOP 已保存，原客户端和原证据保留。STOP 文件不是远端操作已取消的证明。
- 完整 pilot 0/3、完整业务轮 0/33、有效配对 0/15。未启动轮保留在分母；不以替换或重放修补失败样本。
- Claude A/B 均为实际运行的 Claude Code 2.1.303，请求 Opus 5.5 / xhigh；Codex 为 gpt-6-astra / xhigh。三端保留原会话，响应 observer 关闭。
- 注册总计 33 业务轮（3 pilot + 30 配对业务）、15 对、192 普通通信槽，最多 7 组、同时 1 Codex + 2 Claude。这些是注册分母，不是已执行或通过数量。
- 本轮是 startup-v2；原生 `/goal` 持续续跑和独立 `/loop` 未计作通过。
- driver 原截止为 19:42:42.486100 UTC；wrapper 至多额外 60 秒；整体收口上限 19:45 UTC（台北 10 月 20 日 03:45）。在途时钟不延长，失败不替换或重放。

[原驱动证据](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r33-original-driver/)保留原输出和进程身份。运行根为 `/Users/lzhs/ux`，日志观察不修改实验状态。

[终态核验](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-closeout-20261020/terminal-evidence.json) SHA256 `f54565dadb078c6d8d5aa369e09aee0dc4f70068356f53ff6589bb50aab2b88a`，核对原生 ACK、callback、失败记录及七份 STOP。后续修复在独立 [callback 候选](CALLBACK-DRAIN-VALIDATION.md) 进行，不重新解释 R33。

## D 计时证据的采用

新 D-TIMING-V1 与已撤回提案分开处理。两份来件 SHA 及 12 条原始引用已核对。R32 inventory 的 ACK → dispatch_intent 为 335.443 秒，callback 原生收件 → EVIDENCE_READY 为 248.233 秒；source_index 已收到 callback，到 HALT 尚未核收。

A 交叉索引所询问的 248.0557 秒是 R31 一次 advance 内部总耗时；248.233 秒是 R32 原生 callback 到 EVIDENCE_READY。二者原次和起止点不同，不能合并或互换。新 A/B/C/D 回件已逐项读完并发布答复。

解释时间字段时，原解析器的 `task_ready_at` 实际取 PREPARING，`report_frozen_at` 实际取 EVIDENCE_READY；native accept 调用不等于 ACCEPT 成功。DONE 前缀检索只作计时线索，送达和核收仍核完整正文及原始围栏。本轮进一步用这些分段比较准备、同步工具等待和核收延迟，不把整个空档归因于模型、锁或哈希。

独立补审已完成：[报告](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-effects-and-scheduling/d-timing-review/REPORT.md)、[完整性核验](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-effects-and-scheduling/d-timing-review/VALIDATION.json)。D 与 parser 的预期 SHA、12 条引用及两 callback 的 46 项合同检查全部通过；根已另核四份最终产物的字节数与 SHA。分析标记为 `supplementary/post-registration`，不修改 R32/R33、不补造事件、不代替配对结果。

| R32 区间（秒） | 全段 | 前台工具窗口 | 工具外仍有未结束会话观测 | 工具外退出时刻未知 | 无活动进程证据的主管间隙 |
|---|---:|---:|---:|---:|---:|
| inventory ACK → DISPATCH_INTENT | 335.443 | 191.914 | 80.594 | 43.622 | 19.313 |
| source_index ACK → DISPATCH_INTENT | 246.197 | 144.639 | 60.810 | 21.446 | 19.302 |
| inventory callback → EVIDENCE_READY | 248.233 | 110.439 | 89.937 | 23.354 | 24.503 |

四个分项互斥可加，但不是 CPU、模型思考、锁或磁盘耗时分类。两个原 callback 的超时记录与后来证实的原生收件同时保留。两份报告已在 14:44:30.326 UTC 的原工具输出中全文曝光，不等于理解或业务接受；source_index 未到 EVIDENCE_READY，相关时长仍为 null。优化优先减少串行准备和延迟核收，同时测量完整控制链，不能把这些空档全部归给模型。

## 交付边界

保留 cmux/MAC 作为原生协作层；upgrade 负责本地依赖调度、提前组包和持久 callback。JEV/TypeSafe 仍为 rules/offline_mock，不新增模型服务依赖。

[41 类问题台账](COLLABORATION-ISSUE-LEDGER.md)和[15 条社区矩阵](COMMUNITY-ADOPTION-20261019.md)已经形成，两份 surface40 本人经验回件已核读。41 类映射不等于全部修复；923 件补集未全文审读，视频/Reddit 正文及第三方运行仍有未证边界。共享 MAC 和 thesis-refiner 由 surface40 统一维护。当前 [PR #1](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/pull/1) 保持草稿，完整原生压测和提效结论待收口。
