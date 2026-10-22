# 原生投递、原次恢复与有界等待

本页是 prompt、任务包、callback、封口及 idle 等待的统一操作合同。
新的普通长消息及握手/复审提示按[短通知与固定正文](long-prompt-delivery.md)
先固定全文再投递短通知；原生回执只证明通知收到，不证明正文已读或任务已接受。
`SKILL.md` 和各客户端适配层引用本页；旧屏幕判据与独立 Enter/Tab
预算不再使用。用户明确授权的每60秒新 marker 求派保留在下述可选入口，
不与默认有界 persist 混为一谈。执行使用原任务固定的完整控制器；
文档更新不迁移在途任务，不证明已安装或已在真实客户端通过。

v3 双向普通通信不以对方 idle 为前提，不为握手或通信暂停原生 `/goal`。
信息就绪后使用绑定的受支持入口提交，由接收者协调任务；消息接收、任务接受、
写入权限和清草稿分别判断。有效握手不为每条消息重复写入/清除探针；初次握手
在冻结旧控制器受阻仍记未验证。新候选已实现绑定真实挑战的只读初次握手并通过本机回归/并发压力，真实持续goal握手仍未验证。实际边界与待测矩阵见
[运行中通信验证](../docs/ASYNC-COMMUNICATION-VALIDATION.md)。

## 只有原接收端新增的完整 user 记录能确认收到

输入前，原 controller 必须把以下信息固定到原 attempt，并在输入和核收时复核：

- 真实 caller 与接收方的同 workspace、surface/pane UUID、TTY，以及原生
  进程 PID、出生身份和可执行文件身份。焦点、标题、继承环境不代替身份。
- 接收方原生 session 与对应 transcript 的确定路径、device/inode、已有
  字节和边界哈希。只能从目标进程/会话绑定该文件，不能按日期、mtime
  或全局 marker 搜索选择一个看似匹配的会话。
- 完整 payload 及其 SHA、原 task/nonce、任务包和报告等适用 pins。
  Codex 的 `NATIVE_INPUT_INTENT` 或 Claude 的 `PASTE_INTENT` 必须在真实输入前
  持久化，并固定当时新鲜 EOF fence。

只有**同一 workspace/surface/process/session/transcript，在原
NATIVE_INPUT_INTENT / PASTE_INTENT 的新鲜 EOF fence 之后新增、全文完全相等的 native user**
才为 `NATIVE_RECEIVED`。保留空格、制表符、空行、换行和字面转义；
不得 trim、拼接多个记录、只查子串/nonce 或用引用替代完整入站记录。
assistant、tool、hook 输出和用户引用旧消息都不是本次入站证据。

| 证据 | 可以报告的事实 |
| --- | --- |
| 粘贴、Enter/Tab、exit 0、空 composer、屏幕活动或 ACK | 仅对应动作或观察；不能确认收到 |
| Claude `queued_command` 或 UI queue | `NATIVE_QUEUED`/pending；不能确认收到 |
| 原绑定及原 fence 后新增的完整 native user | `NATIVE_RECEIVED`；只证明接收 |
| 原 controller 核证据后发布正式 receipt | 原次传输已核收；不授业务接受 |
| 主管独立审读报告及原件并裁定 | 对应业务范围已接受；资源释放另验 |

缺绑定、旧 fence、文件被替换/截断、正文被改写或证据不可读，均保留未确认。
旧尝试缺少输入前 binding/fence 时，拒绝追补；不重发、不把当前 EOF
冒充过去的输入边界。报告被发现、被读取与消息被收到是不同事实。

## 当前 v3 Codex 使用原生输入

绑定原接收 thread 的控制 socket，查询当前 metadata 和 canAcceptDirectInput。
active/inProgress 使用一次 `turn/steer` 和新鲜 expectedTurnId；idle 使用一次
`turn/start`，不改变设置。先固定完整 request、身份和原输入意图，再执行输入。
未知状态、turn race、身份变化或 socket 不可用时拒绝；不操作 Codex composer、
不粘贴、不发 Enter/Tab，也不回退终端队列。RPC 接受不等于原生接收；错误、
超时或不确定返回只允许对原次零输入核收，没有重发或补键预算。

## Claude 首次终端输入只有一次粘贴和一次提交键

复用受保护 bridge。首次输入前确认 agent TUI 和空 composer；SHELL、
UNKNOWN、压缩、重连、外来草稿或未识别结构不得输入。原 attempt 只粘贴
一次，再有界只读等待**完整且稳定的原草稿**。等待耗尽就保留原次，不按键。

Claude 完整稳定的原草稿通过后，首次使用受支持的 `enter`。正常 active/queued
不是正式新消息的一律禁发条件；真实草稿、压缩、重连、未知结构继续受保护。
不另造 Ctrl+Enter 或裸键绕路。客户端排队仍是 pending，直到原生 user 证据成立。
旧 Codex Tab 分支仅说明历史控制器，不能用于当前 v3 新输入或恢复。

每次粘贴/按键前都重核身份，按键意图在动作前持久化。折叠粘贴摘要、残缺
首帧和未知 footer 不能证明完整草稿。已测显示等价只用于草稿保护，
不能降低原生全文比较的严格程度。未知状态不清空，不重复粘贴。

Claude 状态栏的识别只在完整匹配边框之外进行。已测可选计数行
`CLAUDE.md | 规则 | MCPs | 钩子` 中的规则数不能误算为草稿；相同文字
出现在输入框内仍是原文。未知字段或残缺边框保留 UNKNOWN，禁止清空绕过。
空输入与空闲回合分别核验，识别修复不改变完整草稿及原生接收门禁。
2026-10-18 两只隔离 Claude 的默认页脚没有自定义 model 状态行。只有完整
等宽边框后的唯一精确行 `⏵⏵ bypass permissions on (shift+tab to cycle)`
（可带精确后缀 ` · ← for agents`）可被识别；相同文字在框内仍为草稿。
额外、未知或截断页脚不通过。握手探针对活跃/排队保持零输入拒绝；普通消息
既有首次提交规则不变，排队仍不等于原生收到，不触发重发或补键。
指定经验会话发布的 [PR39](https://github.com/lzhs1995/multi-agent-collaboration/pull/39)
（固定 commit `41065d1ccd2c70fc82ef7d04fd810edc85f22d94`）提供了 spinner
后跟完整 `⎿ Tip:` 的案例。本地复现确认该提示会遮住活动状态。现在仅从
composer 前倒序跳过空行、完整 Tip 行和精确 goal 状态行；不越过任意正文。
无计时器 spinner 限定为活动符号＋单英文词或 `Compacting conversation/context`
＋省略号。已完成的 `Brewed for` 等状态仍为空闲；框内相同内容逐字保留。
这些是离线复现及入口验证，不是对方回复或真实投递通过的证明。
私有实验controller由固定base生成，必须在materialization中精确转换上述两个
解析函数；不能以工作仓库bridge已修复代替实际生成产物检查，也不能整文件覆盖
而丢失base的原生Codex输入。实际生成controller须通过草稿零输入、活动状态、
队列和原EOF fence后的精确回执测试，并逐字核对原投递函数保留。
完整边框外的同行时间字段可带精确提示 `new task? /clear to save N tokens`（N 仅为 ASCII 整数，或整数/一位小数加小写 `k`）；正文同字仍是草稿，未知提示及折叠草稿仍不授权输入。

## 历史终端原次恢复共用最多一次补键

本节仅适用于原控制器支持的历史终端尝试；当前 Codex 原生输入没有补键路径。

### Current status and nested hook identity

同一 hook evaluation 内，外层身份解析与内层 bridge 核收使用同一原生 caller
采集来源；每次边界仍重新核进程与 cmux 树，不缓存身份、不改写 daemon 环境。
否则外层识别正确、内层退回继承的 workspace 会错误阻止合法核收。

完整 Claude 边框成立时，只从最新顶格 activity 行判断当前压缩或重连；
完成报告及 recap 内引用的历史状态不参与。Codex 的 steer queue 标题不属于
当前状态行；独立 warnings footer 由已测规则识别。真正的 Compacting context、
Compacting conversation、Reconnecting 以及未知界面仍不准输入。
这些兼容规则不改变草稿逐字匹配和原 fence 后完整 native user 的验收标准。

先通过**原 sender/controller** 对原 attempt 做零输入核收，检查迟到的
完整 native user。已收到或已排队时不补键。只有同时满足下列条件，原
controller 支持的恢复入口才可在同一 attempt 内补一个现场支持的 Enter
或 Tab：

- 原报告、任务包、payload、nonce、双方身份及原生 binding/fence 不变；
- 当前完整未改草稿仍属于原次，结构稳定，原持久锁及 inode 可核；
- 原历史及当前观察中没有 UNKNOWN、compaction、queue、重连、结构变化
  或用户编辑等失去草稿所有权的证据；
- 原 controller 能证明这一个共享补键预算尚未消耗。

自动路径与显式恢复**共享最多一次补键**，不能分别建 Enter、Tab 预算。
补键意图落盘即消耗，动作失败、进程中断、重启或未确认均不重置。
诊断工具不能授予补键；手动见到草稿不构成授权。原版本不支持恢复时交主管，
不把新模块单独覆盖到旧 runtime。预算耗尽仍可有界零输入核收。

普通消息、任务派单和 callback 各用自己的原 journal 与原 controller。
`--reconcile-only` 是零输入核收。当前 CLI 的 `--recover-stranded`
只属于 `submit-text`：原次须为 `NATIVE_PENDING`、原记录为
`PASTE_INTENT`/`ENTER_SENT`，并通过上述全部门禁及未用补键预算。
`submit-task-pack` 和 `submit-completion-callback` 仅核原次，不能追加
该选项。callback 的旧 queue-only Python 接口不提供额外 Tab 配额。不能改 nonce、换会话、删 journal
或重写旧 attempt 来取得新发送槽。

## 封口、Stop 与主管报告发现

PostToolUse 成功结果须符合客户端官方 schema：仅通过
`hookSpecificOutput.hookEventName=PostToolUse` 与字符串 `additionalContext`
传回完整核验结果，不能把内部 `action/results` 直接放在 JSON 顶层。
Codex 对未知顶层字段拒绝整份输出；原生记录存在与自动 hook 输出被接受
分别核验。保留未确认时的 stderr/exit 2，普通无关工具保持静默。
离线核官方 schema 后，再取活跃客户端实际自动调用记录；手动调用不算。

报告完成后先冻结报告、task pack 和原 attempt 既有记录，再走原 callback。
封口阻止继续扩展测试、改报告、追加科研或重复发送；允许严格 task-bound
的只读诊断及原 controller 零输入 reconcile。核收如需追加观察或原子
发布 receipt，必须沿原控制器及持久锁，不改写历史。诊断入口是
`scripts/cmux_callback_diagnose.py --task-pack <原任务包绝对路径>`；
它不发键、不造回执、不清 marker，也不开放任意 shell/文件访问。

绑定报告与原回调终态成立后，Stop 可接受普通诚实说明，例如“报告已冻结，
回调尚未确认，等待主管核收”；不要求唯一精确 STATUS 模板。合法等待
返回 `WAITING_SUPERVISOR`、`continue:false`、`suppressOutput:true`，
保留 task/receipt 状态。明确声称 callback confirmed、共识或业务通过而
无对应证据时仍拒绝。缺失、漂移或在途证据不能被包装成合法等待。

Stop/SubagentStop 仅对严格布尔 `stop_hook_active is True` 处理重入；
字符串和数字不享该边界。重入处理不生成 receipt、不 disarm、不授验收，
下一正常回合仍核原证据。具体 hook 出口见
[Stop 生命周期](stop-hook-lifecycle-20261005.md)。

主管通过认证的 active markers 有界发现本 workspace、本 supervisor
所属的冻结报告；由 PostToolUse 入口 `scripts/cmux_supervisor_report_guard.py` 记录
`REPORT_DISCOVERED` 并提供报告上下文。不扫全盘，
不借 marker 文本越过身份核验。发现报告不是投递确认、业务接受或 disarm。
主管可先独立读固定报告推进工作，再按原证据分别核收通信、裁决业务及
定向解除原任务。执行者不因回执待核无限续跑；主线不依赖该回执的工作继续。

## 默认 idle 观察与 CCC 有明确等待期限

任务 disarm 后，idle Stop 只保存一次持久 idle 状态并允许结束；
没有求派记录、后台进程或主管答复都不能成为 Stop 阻断理由。
`cmux_executor_idle_guard.py` 不自动粘贴求派，不要求每 60 秒催派。

需要主动请求时，显式 `executor_ready.py ask`（旧拼写 `request`）
最多提交同一原请求一次。未确认保留同一 payload、marker、nonce 和原
attempt；后继只能核原次，不能周期性重贴。主管答复必须绑定原请求，
沿原 bridge 到达原 surface，或写到该请求给出的精确 mailbox。
mailbox 必须携带原 loop record 的 `episode_id`、`caller_surface_uuid`
和 `task_id`（即使 task_id 是空字符串也不能省略）。idle binding 通知的
独立 `episode_id` 不代替 loop record，也不证明已建立等待段。

显式 `executor_ready.py persist` 只读原请求、绑定 transcript 和 mailbox，
默认 60 秒、最长 300 秒；原期限与已用预算沿原 episode 保存，不因重启
延长。任务、主管或绑定 transcript 改变时保留原段并记
`UNRESOLVED_IDLE_EPISODE`，不自动另开一段；损坏的原请求/停止记录
保守终止，不自动重发。真实答复、新任务、
operator stop 或期限耗尽都结束观察。`WAITING_DEPENDENCY`、
`TIMED_OUT` 与收到答复分别记录；超时不是投递失败证明、业务完成或
下一轮自动催派许可。保持原状态，等待有实际授权的新事件。

CCC 在合法报告等待边界与 `WAITING_SUPERVISOR` 同步，并保留有限期限；
不得把等待改写为无限模型续轮。native Goal 是独立机制，不能从 CCC 配置、
本地 hook 放行或源码修改推断其实际状态。等待结束、工具可用性、任务接受
和新任务派发分别验收，不通过全局关闭保护来消除循环。

## 用户明确授权的每60秒主动求派

默认 executor_ready 的有界单次观察不撤销用户另行授权的主动求派。
可选 scripts/executor_reask.py 保留每60秒一个新 marker 的请求，直到主管答复、
新派发或 operator stop；不重贴旧请求、不重置旧 attempt 的按键预算。
配套 cmux_executor_reask_stop_guard.py 只约束已授权等待段。单次 run 有调用
时长上限，不能把“停止一次工具调用”说成“永久放弃任务”。

新求派使用同版 executor_ready、正文存储和 bridge。超过700 UTF-8字节或
带尾换行时固定完整正文再发送短通知，返回的通知 receipt 不证明正文已读。
不适合终端输入时使用已配置文件通道；每轮记录实际写入或错误，
空 channels、轮数增长和历史 marker 均不证明送达。历史 marker 无原次
receipt 时只保留 UNVERIFIED；真实队列由同版 bridge 的实际结构识别。

这个兼容更新不宣告其他未完成的 reask 生命周期方案已上线。安装须保存原
active episode、旧控制器与未决证据，不把默认 idle hook 全局改成无限续轮。

## helper 只保留一个受保护发送入口

普通消息的 helper 必须由 `scripts/render_cmux_agent.py` 从固定基线渲染。
只有绝对可执行路径、完整渲染字节、同一 release 的 adapter 与相同 Python
全部通过守卫，直接 `ask/send/broadcast/reconcile` 才准入；可加 `rtk` 或
`rtk proxy` 前缀。shell/env 包装、嵌套、重定向、裸 cmux 和替代发送器拒绝。
helper 仍调用唯一 guarded bridge；正式 task/callback 保留专用入口。
reconcile 须绑定原 intent，零输入；缺证明只保留 pending，不报收到。

## 一致版本与证据边界

发送器、journal reader、PostToolUse、Stop、idle、启动器和技能适配层
必须来自相容的完整固定版本。`cmux_native_delivery_guard.py` 只核当前
投递调用及其原 attempt；未确认不能报成功，普通工具不扫旧账。它不发键、
不造 receipt；不能关闭或改成 advisory 来绕过证据。旧屏幕/send-proof
hook 的退役由受支持安装器限定本包注册，保留其他任务及 foreign 配置。

配置落盘、完整版本安装、新进程导入、真实 hook 执行、当前客户端加载、
实际 native user 入站、Stop/CCC/Goal 等待及业务验收分别留证。真实验收
必须覆盖 Claude → supervisor；反方向成功不能替代。新任务解析当前
skill 的真实完整 release，在途原次仍保留原 controller。
离线测试不替代现场；一次成功也不能保证未来所有 UI 传输绝无失败。
能保证的合同是：未知不报成功、保留原次、恢复有界、证据不被重写。

## 新粘贴必须为单行（0.4.4）

普通消息及握手全文含任意 CR/LF/tab 或超过700 UTF-8字节时，保存完整只读正文，
再发单行 MESSAGE_REFERENCE_V2；保留全部字节和SHA。新任务使用专用
TASK_PACK_V2 单行通知并绑定完整任务包SHA；callback保持专用原文，不改成普通引用。
公共新粘贴门禁同时覆盖三条路径。旧V1四行通知仅沿原controller核收，不能重贴
或迁移原nonce。短通知原生收到只证明通知；完整正文另核读入，业务另行接受。

完整实机验收分别记录：原fence后完整 native user、真实callback原次及回执、
活跃客户端自动hook的实际调用。手动hook测试、配置存在和离线套件不能代替自动执行。
维护通过后接回用户原任务，沿原主管核未完边界，不擅自加派新研究或旧章节。
