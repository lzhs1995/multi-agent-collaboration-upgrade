# 控制开销与跨调用公平调度验证

2026-10-19。R31 双 Claude 已真实启动，但本地控制操作耗时过长，第二执行通道的正式握手被延后。此次修改减少同一次观察的重复校验，并让单步调度保持通道公平性。它尚未在原生配对实验中验证提效；冻结 R31 原次不加载这些修改。

## 原次发现

每个私有 NativeReader 构造完整校验 29 个 startup pins，共 1,066,091,209 字节；每个 Coordinator 静态至少涉及 7 次该验证。这是源码推导的逻辑校验量，不是实测磁盘吞吐或独占 CPU 时间。原进程两秒采样见多个 worker 线程执行 SHA256。原 callback advance 的内部总时间为 248.0557 秒，期间没有新输入，不能把全部时间算成锁等待、模型推理或哈希。

原调度计数每次 advance 都重置。当控制开销使每次仅执行一步时，A 会连续 claim、prepare、dispatch，B 才得到 claim。该现象不表示 A 必须完成全部业务后 B 才能启动。

## 有界完整 pin 测量

13:22:27–13:22:29 UTC 直接调用原私有适配器的 `upgrade_live_integrity.verify_artifacts`，输入为原 startup-v2 全部29个 pins。连续三次均完整核对1,066,091,209逻辑字节，29/29通过，无错误：墙钟0.518210、0.472688、0.516498秒；进程CPU0.769678、0.667451、0.683424秒。原函数每次重新读取完整内容，没有跨次digest缓存；没有清空操作系统文件缓存。

这是当前本机负载/缓存条件下的组件测量，不能移算为原R31、完整Coordinator或新版本配对时间。**半秒量级的本次结果不足以解释此前129秒的控制延迟**；仍缺整条调用链实际次数、事件区间和资源争用证据，不能只凭逻辑字节量或SHA256栈采样归因。120秒总预算内完成，未发模型输入、重放实验或修改原状态。完整脚本、输入pins、原stdout和结果见[有界测量](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-final-publication/bounded-pin-measurement/REPORT.md)。

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

仍有六个观察用 NativeReader、action gates、callback 和最终刷新保留完整私有校验。现已取得上述原完整pin函数的有界组件测量，仍没有整条控制路径的同范围性能比较、原生新版本配对或15%提效结果。R31 的客户端、输入、时间、失败与样本分母保持原样。

## 精确提交 CI

修复提交 `ae2c3c645dabfa97be736fefff9609dadf4f715b` 的[四格 CI](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39257614544)于 13:08:22–13:08:26 UTC 实际触发一次，13:17:20 UTC 完成，结论为 FAILURE。原始每格均输出 Ran 2194 tests；未单独输出 discovered，以下通过数按总数减跳过、失败和错误推导。

| 环境 | 通过 | 跳过 | 失败 | 错误 | 测试用时 |
|---|---:|---:|---:|---:|---:|
| Ubuntu / Python 3.10 | 2079 | 114 | 0 | 1 | 467.153 秒 |
| Ubuntu / Python 3.14 | 2080 | 114 | 0 | 0 | 521.094 秒 |
| macOS / Python 3.10 | 2089 | 104 | 0 | 1 | 462.993 秒 |
| macOS / Python 3.14 | 2090 | 104 | 0 | 0 | 461.354 秒 |

两格 Python 3.10 都在新增测试 `test_lease_change_between_status_and_advice_is_refused` 的第120行报同一 `ValueError`：该版本的 `datetime.fromisoformat` 不接受原时间末尾的 `Z`。后续提交 `9e5adfdde29cbf7b8da9aadb2ad92f92695ab722` 仅先将它转换为 `+00:00`，保留加一秒的变更和原拒绝断言。定向8项在 Python 3.14 下通过，测试耗时9.094秒；完整stdout、测试前后相同源码SHA和执行记录保存在[兼容性修复证据](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-final-publication/python310-fixture-repair)。这不是本机Python 3.10验证。

`9e5adfdde29cbf7b8da9aadb2ad92f92695ab722` 的[新四格 CI](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39259307066)于13:20:24–13:20:27 UTC只触发一次，run.headSha精确匹配；13:30:23 UTC已是completed/SUCCESS。四格原日志均已收取，最后一份于13:31:09 UTC落盘。

| 环境 | Ran | 通过（推导） | 跳过 | 失败 | 错误 | 测试用时 |
|---|---:|---:|---:|---:|---:|---:|
| Ubuntu / Python 3.10 | 2194 | 2080 | 114 | 0 | 0 | 389.675 秒 |
| Ubuntu / Python 3.14 | 2194 | 2080 | 114 | 0 | 0 | 513.053 秒 |
| macOS / Python 3.10 | 2194 | 2090 | 104 | 0 | 0 | 539.829 秒 |
| macOS / Python 3.14 | 2194 | 2090 | 104 | 0 | 0 | 471.439 秒 |

每格原日志均未独立输出discovered；通过数为Ran减跳过、失败和错误，2194不是全部通过数。完整run元数据、分格计数和四份带SHA的原日志保存在[修复CI证据](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/ci-9e5adfd/results.json)。两格Python 3.10兼容性问题已在完整套件中关闭；此前 `7fa8e4e` 的通过结果与 `ae2c3c6` 的失败结果均保留、不移算。后续提交只更新文档，没有重新运行全套；该CI不是原生双Claude实验或提效验证。

最初的 `dispatch-intent.json` 写错上游仓库，未执行；原文件保留。`dispatch-intent-corrected.json` 与 `dispatch-result.json` 记录更正后的 upgrade 仓库以及唯一实际触发。该更正不是第二次 CI 执行，原始状态、日志和计数保存在 [CI 证据目录](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/ci-ae2c3c6)。

## 原始证据

- [控制开销审计](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-audit/REPORT.md)
- [测试执行报告](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/REPORT.md)
- [结构化执行记录及源码 SHA](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/results.json)
- [执行后归档的完整 JSON 比较脚本](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-fix/baseline-comparison.executed.py)
- [R31 原生进度与结果](VALIDATION-20261019-R31.md)
