# 新 callback 优先核收与失败后原成果收取

2026-10-20。候选分支 `upgrade/callback-drain-20261020`，基于 `2efca96`，与正在使用的源码和 R33 冻结实验分离。本修复落实 A/B/C/D 中的 P3；尚无采用本候选的原生配对结果。

## 触发问题与行为

R32 中 inventory callback 原生到达后 248.233 秒才进入 EVIDENCE_READY；source_index 的 callback 在 driver HALT 前 441 秒未被核收。R33 中 inventory 这段为 168.564660 秒，source_index 只到 PREPARED，随后实验 Codex 因配额 HTTP 401 终止。这些是不同原次的补充观察，不能计算注册配对提效。

候选修改三处：

1. `orchestration_advance.py` 在下一次推进时先处理新 callback 和未形成 EVIDENCE_READY 的候选，再领取、准备和派发其他工作。已提出的 EVIDENCE_READY 审查不会持续抢占新就绪通道。
2. `orchestration_coordinator.py` 保留原 HALT、截止和 Stop 检查优先级；一个节点失败后，阻止该注册轮的新 claim、prepare、dispatch，仍允许原 callback、candidate 和原主管独立 ACCEPT。检查不会撤销在途操作，不转移文件写权。
3. `upgrade_driver.py` 新配置可选择 `--node-failure-drain-seconds N`，N 为 1–300 秒，且必须固定整体截止。配置变为 `upgrade-driver-config-v3`，要求 coordinator 和 advance 与 driver 的源码匹配。窗口从首个失败事件计时；进程重启或晚发现不会重置。待收取节点全部离开在途/待验收状态、窗口到期、原截止到期或原生主管失败时，保留失败并停止该实验。

默认值 0、历史 v1/v2 配置、原输入和旧 HALT 均不变。该窗口只支持已经执行的成果收取，不承诺窗口内必能完成 RELEASE，也不唤醒模型、代验收或开启下一轮。整体任务在服务故障后的接续遵循[三态接续要求](TASK-CONTINUITY-INTEGRATION.md)，不能通过修改旧实验口径冒充完成。

callback 的来源继续使用原 `continuation.json` 中的 `callback_native`、`callback_received_at`、`published_at` 和完整任务合同。DONE 前缀、文件发现、通知入队都不能直接生成业务接受。

## 验证记录

| 阶段 | 实际结果 |
|---|---|
| 首次针对性验证 | 85 项，1 项失败：重复返回已有候选会饿死另一就绪通道；已修复优先级 |
| 修复后针对性验证 | 129 项，102.313 秒，通过；包含 advance、driver、watchdog、transport、continuation |
| 首次完整回归 | 2292 项，1236.181 秒，1 失败、0 错误；原 schedule HALT 应先于新事件检查，已调整检查顺序 |
| 调整后针对性验证 | 102 项，58.904 秒，通过；schedule、advance、driver |
| 调整后完整回归 | 2292 项，1216.625 秒，0 失败、错误、跳过；运行期间 259 个 Python 源文件 SHA 未变化 |
| 原生采用和配对提效 | 未验证 |

测试覆盖新 callback 抢先核收、已有审查不阻塞另一通道、另一节点 BLOCKED 后原 callback/候选/独立验收、新派发拒绝、原配置立即停止、有限窗口不可重置、未知时钟、篡改证据、不匹配源码、原生主管错误和原截止优先。

完整首次日志、结果及运行前后源码 SHA 保存在：

`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-closeout-20261020/callback-drain-validation/`

其中 `result.json` 保留首次完整失败；`recheck/result.json` 和 `recheck/test-all.log` 保存最终通过结果。最终日志 SHA256 为 `e248a7278ac52bc74a7052b5e30bd657de1eaa1b790fe26d72b9bad7d3bb1812`，`source_changed=[]` 覆盖 259 个 Python 源文件。首次失败日志不覆盖。当前审查为 `solo_self_review`；由 surface43 整合前须独立核读最终提交。
