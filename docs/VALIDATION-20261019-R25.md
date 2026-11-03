# r25：首轮超时，原次已停止

2026-10-19 13:22:47（Asia/Taipei）终态更新：原 driver 和独立 watchdog
均返回 HALTED；watchdog 发布 all_stopped=true，含义是停止后续调度，
不是销毁客户端或取消远端请求。原报告为 `/Users/lzhs/up/driver/final-report.json`，
SHA256 `4cf3e7354643b04c529111347799a259c364d022232faa86f27c2a4378c78017`。
最终为 **1 TIMEOUT、32 MISSING；pilot 0/3、业务 0/33、可比较配对 0/15**。

两个 Claude 的原始 `completion-callback-receipt.json` 均已生成；下文
13:07 的 pending 是历史状态。后续验收真实触发
`UPGRADE_MIXED_CONTROLLER_IMPORT:render_cmux_agent`，原冻结代码不能完成
候选验证。没有重发 callback，也没有热改原控制器。导入修复使用显式同目录
renderer 加载，保留控制器隔离校验；36 项定向回归通过，负对照复现原错误。
证据见 `maintenance/r25-callback-import-audit/REPORT.md`。

后续 r26 使用修复后的完整原矩阵，并注册 startup-v3 原生 Goal；仍以
本次原 **14:47:39** 为最晚截止，不额外获得两小时，不补样或伪称已通过。
以下为保留的运行中检查点，当前状态以本段和原 final-report 为准。

2026-10-19 13:07（Asia/Taipei）检查点：**三客户端已 ADOPTED，pilot-01 两路任务均 NATIVE_RECEIVED，双 Claude 已产出业务文件；整轮业务尚未验收，0/33。**
启动、工具与自动 hook 核验不等于持续 `/goal`、通信压力或协作提效通过。

## 本次执行

- 配置与计时起点：2026-10-19T04:47:39.605462Z。
- 整体固定截止：2026-10-19T06:47:39.605462Z，即台北 **14:47:39**。
- 原生配置：Codex `gpt-6-astra/xhigh`，Claude `fable/xhigh`，当前原生返回模型 `claude-opus-5-5`。
- 实验根 `/Users/lzhs/up`；原 wrapper、driver 和独立 watchdog 持续运行。
- 冻结实现提交 `4de3df97d8af58a72de5e1559fe52d81cb357dd6`，323 文件。
- 7 组、33 轮、15 对（12 warm、3 cold）、192 条独立普通通信。先连续通过 3 轮 pilot，再执行配对；缺失、拒绝、排队和失败不补样。
- 两小时是本次执行上限，不是保证通过的时间。单轮 1800 秒；失败保留原会话、原输入和全部分母，不重放、不重置时钟。

| 门槛 | 本检查点结果 |
| --- | --- |
| 七组准备与范围核验 | 7/7 |
| pilot 三个真实客户端、模型、隔离与自动 hook | ADOPTED |
| 首轮 bootstrap 与原生 supervisor | 已发布；原 supervisor 取得许可并 STARTED |
| 连续 pilot | 0/3 完成 |
| 完整业务 | 0/33 完成 |
| 配对 | 0/15 完成 |
| 普通通信压力 | 尚无完成结论，固定分母 192 |
| 持续 `/goal` 下双向通信 | 本次未注册启用/续开步骤，不能由 r25 通过推定 |
| 协作效率与成本 | INSUFFICIENT_EVIDENCE / UNKNOWN |

## 为什么使用 r25

r24 启动前发现 Codex 提供方和凭据变化，Claude 相关配置未变。
保留 r23、r24 未配置、未启动状态；按已有全面授权独立准备 r25。
相关配置比对只覆盖实际进入私有环境的提供方字段，冻结快照仍完整校验，
避免无关全局 UI、插件、hook 变化反复触发准备。未修改全局配置。

12:38 的两路 Claude 诊断均完整返回 HTTP 200 与 `message_stop`，耗时
44.503/46.039 秒，流重叠 20.226 秒。它们仅是当时的容量观察，不纳入正式
业务和消息分母。r25 的真实三客户端 adoption 是独立后续证据。

## 经验反馈与解释边界

独立审计核对了 7 组注册、15 对、192 个普通消息槽和 323 个冻结文件。
原次两路正式任务派发均有原生接收记录；13:04 后双 Claude 已生成各自的
`result.json` 和 `report.md`。source_index 的原 callback 已执行一次
`turn/steer`，返回 RPC_ACCEPTED，但当次扫描仍为 NATIVE_PENDING；这是
待核收状态，不是 callback 已确认，更不能重发。inventory 后续状态需继续观察。

该审计同时发现持续 Goal 覆盖缺口：冻结脚本有 19 处相关文本，涉及装饰解析、
测试和不暂停 Goal 的提示，没有原生 Goal 启用/续开验收流程。33 轮即使全部
通过，也不构成持续 Goal 验证。192 个普通消息槽有独立观察报告，但没有合入
配对时间统计的整体通过 gate；成本字段仍为 UNKNOWN。

配置到首次 native start 用时 295.442 秒。7200 秒除以 33 轮仅为每轮
218.182 秒，且未扣 7 组启动开销；首轮实际已明显超过该均摊时间，因此不能
承诺本次截止前全部完成，也不能重置或延长原时钟。

surface40 管理共享 MAC 和论文精炼助手。r25 启动事实已发布到其既有文件
收件箱，约定回复尚未出现；文件读回不代表 owner 已读。

已读取其共享 0.4.51 发布说明：自动 reply advisory 曾误导执行者等待 idle
preflight，新说明要求正常工作或 Goal 中及时通信。该发现针对实际 hook
输出文案，不能直接推论所有旧发送入口都阻止 active 接收者。upgrade 已要求
不为通信暂停 Goal；本次保持冻结控制器，后续按真实运行证据核验具体入口。

另已发布新的具体 Goal 格式咨询到 surface40 既有文件收件箱；截至本检查点
尚无 owner 已读确认。只读二进制分析确定 Claude 2.1.170 的
`attachment.goal_status` 和独立的 `active_goal` transport 格式。
候选新增 `scripts/upgrade_goal_observer.py`，仅认证原生日志、记录离散 Goal
状态和 unmet check 后的模型记录；18 项本地语义测试通过。这些属于开发验证，
不能算真实 Goal 压测，亦未热改原 r25 客户端。详见
[原生 Goal 观察边界](NATIVE-GOAL-OBSERVATION.md)。

## 原始证据

证据根：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。

- `preparation/r25-candidate/REVIEW-AND-NEXT.md`：范围、门槛和 14 条 socket/lock 路径。
- `authorization/r25-20261019/configuration-result.json`：唯一配置及固定截止。
- `execution/r25-original-driver/intent.json`、`watchdog-ready.json`：原启动与独立 watchdog。
- `execution/r25-original-driver/stdout.jsonl`：原 driver 观察；运行中持续追加。
- `/Users/lzhs/up/g/0/evidence/B/live-control/adoption.json`：三客户端 adoption。
- `/Users/lzhs/up/g/0/campaigns/B/pilot-three-rounds/runs/pilot-01/run.json`：原首轮开始。
- `maintenance/r25-native-recovery-01/surface40-launch-publication.json`：文件发布读回。
- `maintenance/r25-independent-audit/REPORT.md`：独立范围与覆盖审计。
- `maintenance/r25-goal-native-format-01/binary-format-evidence.json`：静态二进制格式依据。
- `maintenance/r25-goal-native-format-01/publication.json`：新的 Goal 经验咨询文件发布，非已读回执。

本文是运行检查点；终态必须以原报告和独立核收更新，不把未完成项目写成通过。
