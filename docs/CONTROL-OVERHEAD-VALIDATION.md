# 控制开销与跨调用公平调度验证

2026-10-19。R31 双 Claude 已真实启动，但本地控制操作耗时过长，第二执行通道的正式握手被延后。此次修改减少同一次观察的重复校验，并让单步调度保持通道公平性。它尚未在原生配对实验中验证提效；冻结 R31 原次不加载这些修改。

## 原次发现

每个私有 NativeReader 构造完整校验 29 个 startup pins，共 1,066,091,209 字节；每个 Coordinator 静态至少涉及 7 次该验证。这是源码推导的逻辑校验量，不是实测磁盘吞吐或独占 CPU 时间。原进程两秒采样见多个 worker 线程执行 SHA256。原 callback advance 的内部总时间为 248.0557 秒，期间没有新输入，不能把全部时间算成锁等待、模型推理或哈希。

原调度计数每次 advance 都重置。当控制开销使每次仅执行一步时，A 会连续 claim、prepare、dispatch，B 才得到 claim。该现象不表示 A 必须完成全部业务后 B 才能启动。

## 修改

- 初始 status/advice 在同一次无动作观察中共享一个新 Coordinator，状态只重放一次。前后验证 run header、控制文件、事件与租约；原生 snapshot 与已接受输出继续校验。
- 每个 action 的执行、实际 gate 和最终状态刷新仍新建 Coordinator；公开 status/next-action 各自新建。不跨动作复用摘要、文件 stat 或身份权限。
- 同 priority=1 的可执行候选依次按 PENDING、PREPARING、PREPARED 排序，确保新的单步 Runner 也能交错推进独立通道。保留收尾、review、reconcile 优先级，依赖与写域仍由原选择器及新 action gate 决定。

## 已执行验证

| 验证 | 结果 | 限制 |
|---|---|---|
| 六模块初次回归 | 106 项通过，131.576 秒 | 与后续公平性编辑部分重叠，不能视作最终源码的冻结全套。 |
| 初次公平性回归 | 31 项中 1 失败，60.382 秒 | dispatch 专用断言的 fixture 含第三个独立候选；保留原失败。 |
| 修正后的 advance＋reuse | 39 项通过，52.836 秒 | 保留 dispatch 专用断言，其他测试覆盖三通道及依赖未就绪情形。 |
| 最后加强的 reuse | 8 项通过，14.967 秒 | 覆盖观察中合法 claim、lease/event 变化及 accepted output 篡改。 |
| 原 HEAD／新路径完整 next-action JSON | pending/accepted/prepared/dispatched 四状态全部相等 | 没有删字段或归一化；不主张 advance 顺序未改变。 |

上述用例重叠，不能将 106＋39＋8 相加为独立测试总数。原始完整 stdout 未单独保存，逐 run 精确 UTC 和测试前 pins 未记录；证据报告保留真实工具 session/chunk、命令、摘要及首次失败。六个源码文件的 SHA 在测试后 12:55:56Z 采集，不能称为测试前冻结输入。

fixture 仪器化实测新 status 调用 state 一次；单步 local claim 创建三个独立 Coordinator，实际 gate 属于 action 的 Coordinator；四次公开观察创建四个独立 Coordinator。旧版两次 state／四次 Coordinator 仅来自源码分析，没有旧版同计数仪器化数据。

新的单步 Runner 序列为 A claim→B claim→A prepare→B prepare→A dispatch→B dispatch。不可领取的 pending 依赖不会阻塞已有 prepared 通道。并发变更测试要求原观察拒绝动作；实际动作仍需新 gate，这些离散前后检查不是原子锁。

## 未完成验证

仍有六个观察用 NativeReader、action gates、callback 和最终刷新保留完整私有校验。没有大 pin 性能基准、原生新版本配对或 15% 提效结果。新提交需要单独四格 CI；此前 `7fa8e4e` 的 CI 不能移算。R31 的客户端、输入、时间、失败与样本分母保持原样。

## 原始证据

- [控制开销审计](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-audit/REPORT.md)
- [测试执行报告](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/REPORT.md)
- [结构化执行记录及源码 SHA](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/results.json)
- [执行后归档的完整 JSON 比较脚本](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/baseline-comparison.executed.py)
- [R31 原生进度与结果](VALIDATION-20261019-R31.md)
