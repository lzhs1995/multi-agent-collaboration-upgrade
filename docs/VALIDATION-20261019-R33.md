# R33：缩短派发与核收等待，修复只读操作误判

2026-10-19。运行源码固定为 `b94b5bd48a49a8511c75468070cc6a81016c3c5d`。本机完整回归已通过，原生 R33 正在执行；目前不能宣称完整压力测试通过或达到 15% 提效。

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
- 精确源码提交的 [CI run 39280150950](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39280150950) 已触发一次。15:52 UTC 收取的记录中四格均 queued，尚未执行；这不是 CI 通过。原始快照见 [run-first-observed.json](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-effects-and-scheduling/ci-b94b5bd/run-first-observed.json)。

## 原生 R33 当前进度

- 15:43:25 UTC：独立 watchdog ready；15:43:27 driver 发布首 permit；15:43:29 原客户端启动调用返回。
- 15:48:47：Codex 和双 Claude 已完成独立 adoption；15:49:16 已发布 bootstrap。
- 15:52 UTC：实验 Codex 已在原会话执行 pilot-01 START，并提前发布四节点草稿。
- 15:56:36.286：Claude A 的完整原生握手 ACK 已出现；15:58:36.182449 已记录 inventory 的 DISPATCH_INTENT，15:59:37.682278 已记录 DISPATCHED。ACK 原行 SHA、device/inode 已由根核对。该段为 119.896449 秒；R32 同名阶段为 335.442815 秒。这是跨轮次的补充观察，不能替代注册配对或宣称总体提效。
- 16:01 UTC 观察中，Claude A 正核对任务包输入，Claude B 已启动；B 正式握手、callback、独立验收和完整轮次仍逐项核验中。完整轮次仍为 0，不能将启动、派发或局部分段计为通过。
- Claude A/B 均为实际运行的 Claude Code 2.1.303，请求 Opus 5.5 / xhigh；Codex 为 gpt-6-astra / xhigh。三端保留原会话，响应 observer 关闭。
- 注册总计 33 业务轮（3 pilot + 30 配对业务）、15 对、192 普通通信槽，最多 7 组、同时 1 Codex + 2 Claude。这些是注册分母，不是已执行或通过数量。
- 本轮是 startup-v2；原生 `/goal` 持续续跑和独立 `/loop` 未计作通过。
- driver 原截止为 19:42:42.486100 UTC；wrapper 至多额外 60 秒；整体收口上限 19:45 UTC（台北 10 月 20 日 03:45）。在途时钟不延长，失败不替换或重放。

[原驱动证据](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r33-original-driver/)保留原输出和进程身份。运行根为 `/Users/lzhs/ux`，日志观察不修改实验状态。

## D 计时证据的采用

新 D-TIMING-V1 与已撤回提案分开处理。两份来件 SHA 及 12 条原始引用已核对。R32 inventory 的 ACK → dispatch_intent 为 335.443 秒，callback 原生收件 → EVIDENCE_READY 为 248.233 秒；source_index 已收到 callback，到 HALT 尚未核收。

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
