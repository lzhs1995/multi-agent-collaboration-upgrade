# R32：修复控制链后继续真实双 Claude 验证

2026-10-19。冻结代码为 `f6a3bb50fc0028d103be1b5ba22597577f7de730`。R32 于14:51:28 UTC停止：双Claude完成原生握手和callback，但旧校验器误判callback后的只读操作，首轮未通过；没有全量业务或提效结论。

## 最终结果与本次修复输入

- [原终态报告](/Users/lzhs/uw/driver/final-report.json)，SHA256 `65e23e5ca5171f5d413da50e3145ece4d3008bb7d367e041d189d7edfb13fd32`：0/33业务轮、0/15配对、pilot0/3；首轮FAILED，reason=EXPERIMENT_STOP_REQUESTED，其余未执行。driver/watchdog均HALTED，原runner exit2，七组停止后续输入。`input_operations=0`是报告器自身没有发送操作，不代表实验三端从未收发。
- 两端正式握手及native ACK通过；inventory于14:37:13Z、source_index于14:39:33Z派发。两项业务产物及callback均已出现，整轮验收仍失败。
- [精确只读审计](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r32-control-repair/callback-action-audit/audit.json)，SHA256 `a641c47689de9c208f664c0c3ffee0629256856817552c01d40763b1cc9f1577`：inventory callback后完成的两段`ls -la`被计为最后业务写入，相差48.338秒；report/result当前哈希与原两次快照一致。Read及reconcile不是本次触发项。另发现真实workload的output_root后紧接`;`而漏统计。
- 后续修复分离业务活动与潜在写入，以新注册metrics v4验证callback及终态后只读；45/45聚焦测试通过，84.831秒，零跳过，源码pins未变。保留旧metrics和原R32失败。新的lane优先级与handoff提前返回已通过42项聚焦测试，未用于该冻结实验。两条误发提案已被用户撤回，相关直发改造已取消，不改变R32或当前收件合同。

下方保留原时间点的准备、启动和观察记录。

## 已修复的问题

1. `advance` 首次完整观察单独计时，45秒动作预算从初始化之后开始；后续观察仍计入动作预算，绝对截止与每次新动作门禁保留。
2. 历史 candidate／ACCEPT 重放只验证原结果及完整输入、源码、测试与输出 pins，不重复运行 checker。新 candidate 与新 ACCEPT 仍各执行一次独立 checker。
3. ACCEPT checker返回后、写入验收事件及释放租约前，重新核对原文件 pins；拒绝校验期间被替换的证据。
4. 同次纯观察共享完整核验的 native authority，保留退出重验与各 reader 的会话、日志、EOF验证。缓存限定在线程／async task及活跃生命周期，异常退出与 copied Context 不得复活缓存；不跨发送、状态写入或租约变更。
5. R32外部准备流程修复路径顺序误判：保留十四条路径的唯一性和逐组归属，比较集合时不依赖JSON映射顺序；实验轮次顺序保持不变。

前四项已写入冻结代码及skill；第五项已在原准备流程成功恢复，并作为经验加入当前skill。R32始终使用原冻结控制器，现已终态；后续修复只用于新批次。

## 已完成的原次验证

| 验证 | 结果 | 证明范围 |
|---|---|---|
| 完整本机回归 | 2237/2237，0 failure/error/skip，1031.48秒 | 完整364文件测试前后SHA一致；不证明原生协作或提效。 |
| 24 OS进程×100轮×3模式 | 7200次reserve、31200次release检查PASS | 双owner、锁忙、意外错误、历史丢失均0；是文件状态／资源竞争验证。 |
| 冻结导出 | 364文件与本机实测源码逐字一致，只读 | 源码提交f6a3bb5；不代表活跃客户端自动采用。 |
| 精确f6a3bb5的四格CI | Ubuntu／macOS × Python3.10／3.14全部SUCCESS，0 failure/error | 每格Ran2237；Ubuntu各skip118，macOS各skip108。平台相关跳过与本机零跳过分列。 |

- [合并验证与原始报告索引](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r32-control-repair/VALIDATION.json)，SHA256 `e7e7790857bf2ef907ce612b64322e53e5fb0b05b0db7ba4e745d723077fb003`。
- [冻结导出](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/source-exports/resume-20261019-live-v27/export.json)，SHA256 `471d8b7491dc5df213d6ffcb1fbbd9a28f6fc163c2c7e61a6c2c9a9b7893db47`。
- 精确提交的[四格CI](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39268319348)已只触发一次，于14:30:37 UTC全部完成。Ubuntu3.10／3.14各通过2119、跳过118；macOS3.10／3.14各通过2129、跳过108。通过数由原始Ran减skipped计算，发现数未另行输出。原[run.json](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r32-control-repair/ci-f6a3bb5/run.json) SHA256 `0028bdfb1887bd8e310dbbd25849596dcd5e415d17c53a1731c2d85ff776ce84`；[完整日志](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r32-control-repair/ci-f6a3bb5/jobs.log) SHA256 `eaa7e84363e4ee810004fd7dce43d19d65cac7cd7d10a5ed39d2fe98706798c0`。旧9e5adfd的CI不移算。

## 原准备流程恢复

原prepare已完成七组物化，最后因 `paths == predicted_paths` 将列表顺序不同误判为范围改变而退出。恢复脚本核对原prepare intent、原四脚本pins、七组完整配置、未启动状态、逐组路径归属、十四唯一路径未占用、当前provider配置和原绝对截止。只补齐未生成的元数据，未重新物化或重放输入。

- [原次恢复证明](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r32-candidate/preparation-recovery.json)，SHA256 `705e6082a1ecdc88dc7ae3396115a2f343211692d4b718fa5916867c4739c2cc`。
- [候选](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r32-candidate/candidate.json)，SHA256 `c07667484f5f7261d9455d23e109ce2134f4d3f612d08376f0063113ebbc7639`。
- [原驱动配置](/Users/lzhs/uw/driver/config.json)，SHA256 `5ed4bccbe4986cb6c80537fdfd591f382ae25b93a7dc652e4b9f85d3ebec56ff`。

## 原生验证历史过程（R32已于14:51:28 UTC终态）

独立watchdog先ready；driver于14:20:29 UTC启动。首组pilot-B的Codex和双Claude均已创建真实原进程。两Claude实际执行文件为2.1.303，界面显示Opus5.5／xhigh；Codex原生会话模型为gpt-6-astra。正式完成情况以原生回执、ACK、任务产物、callback与独立验收为准。

14:21:24与14:21:25 UTC，两Claude原日志记录HTTP429、`user requests-per-minute limit exceeded`。随后原请求自动重试；14:23:27的启动检查已取得Codex及Claude A的真实readiness证据，Claude B当时仍未完成。14:24:28三端已ADOPTED，14:24:53已BOOTSTRAP_PUBLISHED；实验Codex核对冻结计划后，取得pilot-01 permit并执行原生START。14:28:08和14:28:30分别记录inventory与source_index的claim，advance正在推进原始握手和派发。API错误保留在原次记录中；启动采用通过仍不是正式业务、callback或整轮通过。

注册仍为七组、33业务轮（含3轮pilot）、15配对，192普通通信槽单独统计；连续3轮pilot通过后推进30轮配对业务。A1允许双lane并行手工调度，B使用advance。两臂响应observer关闭，Goal／loop另验。原配置截止17:34:27.264659 UTC，总cap17:36:18 UTC，不因等待、重启或轮次切换延长。

14:31:15 UTC，inventory原始握手核收完成并转为PREPARED，完整native ACK、原握手通知回执及任务包pins均已进入原事件；source_index于14:32:09开始原次preflight。尚无完整业务、callback或整轮通过。观察到当前advance要求先prepare所有独立lane再dispatch，可能让已准备好的lane空等；正在独立审查更早派发的候选优先级，当前R32不变更实现或注册口径。

[原驱动证据目录](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r32-original-driver)保存完整输出与进程身份。R32原终态为0/33业务、0/15配对，未证明普通通信压力或提效；后续结果必须使用新批次独立报告。R31的0/33、0/15保持原样。
