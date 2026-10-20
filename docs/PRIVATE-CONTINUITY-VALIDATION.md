# 私有主动询问登记与原入口验证

2026-10-21。本候选基于 `96a7b87`，只修改独立 upgrade worktree。没有安装共享 MAC／thesis-refiner，没有重启或修改既有客户端，没有迁移 R33 的 episode、nonce、租约或 STOP。所有来源均为完整固定 MAC `0.4.58-cf3f6175840b`。

## 接入合同

`upgrade_live_materialize.py --register-private-continuity` 是默认关闭的明确选项。它为新的私有控制器写入原 scheduler／guardian 接受的 `state/CURRENT.json`：仅包含该私有 source、固定 Python 路径和实际 MANIFEST SHA。CURRENT 本身为 0444、独立 pin，并受审计与 Seatbelt 的不可变路径保护；启动验证拒绝改变的内容、模式、位置或版本。登记不启动客户端、guardian job、scheduler 或 episode，不授予共享写权。

采用验证通过以后，原 executor 仍须用原 `executor_reask.py start --supervisor ORIGINAL_CODEX_UUID --task TASK_ID --overall-task OVERALL_TASK --authorization-file AUTHORIZATION_FILE --continuing --reservation-inquiries --proactive-hook --file-only` 及本任务的原授权登记 episode。上述占位值必须来自新实验的原固定身份与授权。原 `executor_reask.py run --max-seconds 60`、完整正文阅读、带实际正文 SHA 的 `consume` 才能推进；`ANSWERED`、Stop 提示及文件调度 sweep 都不替代模型阅读或消费。不要为旧 episode 新建 CURRENT 或改 controller。

原 identity／advisory 的 capture、check 与 consume 身份验证现在传递确切进程的私有配置根，不回退共享 `.claude`／`.codex`。捕获根、当前原进程环境、PID／birth／executable、TTY、surface／workspace／pane、session 和原 transcript pin 必须一致。Codex 接受原私有 standalone 证明，但仍要求同一真实 foreground 客户端，不制造 managed daemon。

Stop 配置保留原 `cmux_executor_idle_guard` 的 75 秒预算；安装器仅 AST 读取、不导入或执行。旧来源没有该预算时不添加。不同数字、不同 hook、重复／未知／注释类型赋值／增量赋值均在发布前拒绝。Stop 观测从原 advisory 入口记录原命令、实际父链和 scope_status；只有明确开启的私有登记才记录该 Stop。观测始终 `adoption_proven=false`，不向启动采用证明增加一个虚构事件。

## 已执行的行为与反例

- 实际原 consumer 在三个私有根中 capture／check／authenticate，拒绝跨 Claude lane、共享根、改变的进程根、PID／birth／foreground；保持原增长日志。完整 consumer 适配挖除后真实执行产生 TypeError，恢复后通过；原字节和语义不变注释对照保留。
- 真实生成完整 controller 在新的 Python 进程及其实际 Seatbelt 内调用原 guardian、scheduler、inquiry CLI 和 Stop 决策。原授权登记可验证，修改 CURRENT 拒绝启动，控制器对其写入被拒。
- 同一新 episode 两轮 sweep→完整正文 reply→ANSWERED→错误 SHA 拒绝→完整正文读取→原 CLI 精确 SHA consume→原 receipt→WAITING/rearm。每轮 hook 提示但不代模型消费；receipt 包含完整正文及原消费身份；terminal_sends／input_operations 均为零。这是隔离环境的原入口行为验证，时间和外部进程观测使用夹具，不是两个现场 60 秒自动周期。
- 真正原 advisory 的 Stop 审计入口产生观测；外部父链使用夹具。此测试不是原生客户端自动 hook 采用、收件或唤醒证明。
- 原 10 件 fixture／manifest 不变。新增 5 件原字节 MIT 源固定在独立 `continuity-manifest.json`，总 79348B，逐件 SHA 和 bytes 读取时核验。沿用原 MIT LICENSE；两套夹具均不是完整、可安装或可启动 controller。

与原 MAC 0.4.58 兼容反例合计 28 项通过（17.878 秒）。两项独审 P3 也已修复：原 RPC import 与重命名后的私有 gate import 都使二次补丁拒绝；gate-pin 挖除必须确切为 `NameError.name == gate_pin`，不能被无关缺失变量满足。第一轮新二次补丁测试失败 4 次，中间修复只挡原模块名、重命名路径仍失败 2 次，最终两入口及语义注释对照均通过，失败不抹除。

随后完整相关 focused 共 128 项通过、0 failures/errors/skips，172.547 测试秒／173.214431 wrapper 秒。涉及旧／现代完整 materialization、三个借用夹具、private CURRENT／consumer／Stop、采用检查与 benchmark 合同。319 个前后源及夹具 pin 一致；此轮是明确列出 dirty 修改的工作快照，不冒充已提交 HEAD。原结果 `/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/execution-20261021/continuity-focused-v1/result.json`，SHA256 `0a33215452d510d51b9a7d98aa82449f64f0078338adf138841f3d7049648f57`；日志 24125B，SHA256 `c5d2871b1955b72a0e0b14411695bd592da07660f718aac830cefe022bcba63b`。后续文档写入另留最终 full 快照。

## 独审与仍未闭合的门槛

43 R2 回件 4202B、SHA256 `8de391299e42285668bd2b67630475f99b094b23e7d8a94e28ab29993b6d6a73` 已整读核验；在冻结 c6 上既有 15 测独立通过，HEAD 不变，但 verdict 为 PARTIAL。第2项真正入口及 inventory／footer 边界仍 NOT_EXAMINED。这里的修复不自动获得该独审批准。

新候选需要独立 full、CI 和最终范围审查；旧 `96a7b87` 的 2327 全绿不覆盖这些改动。现场三个客户端采用、两轮自动 60 秒原生求派闭环、证据约束的三态接续、跨父共享最多两子 agent 与真实父端消费、3 pilot／33 business／15 pairs／192 ordinary 槽及至少 15% 提效仍未完成。结果继续汇报原 surface40 集中维护，不把原 surface43 的参与及只读审查推定为 merge／共享写授权。
