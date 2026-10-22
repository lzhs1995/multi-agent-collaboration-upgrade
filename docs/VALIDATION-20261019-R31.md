# R31 原生双 Claude 实验

R31 已于 **2026-10-19 13:04:49 UTC（台北21:04:49）超时停止**，原次终态独审截至13:09:05 UTC。双 Claude 均真实启动并执行工具；Claude A 已完成正式握手、任务收件、原次callback和独立ACCEPT，8/8检查通过。Claude B 已启动，但截至最后原生日志核查仍未收到正式握手。完整通过 **0/33**，可用配对 **0/15**，结论为 **INSUFFICIENT_EVIDENCE**。本轮暴露了本地控制开销和跨调用调度公平性问题；修复已推送，但没有新版本原生提效证据。

## 原次与固定边界

- 原次：`resume-20261019-r31-native-retry`，根 `/Users/lzhs/uv`；原外层工具 `92138`。只观察/收取这一原次，不重新prepare、configure、launch或补发消息。
- 冻结实现：`7fa8e4ee158d7fff82536ec153072c48183ab036`，`resume-20261019-live-v26/source`。私有MAC基底仍为 `0.4.34-d333557fb498`，不热加载共享发布。
- 固定矩阵：7组、33轮（3轮pilot＋30轮比较）、15对、192个计划普通消息槽。只有连续3轮pilot成功才进入配对；失败和缺失保留在分母。计划消息槽不等于实际发出消息。
- 实际不可延长截止：`2026-10-19T13:17:24.292814Z`（台北21:17:24）；另保留原外层授权截止 `13:18:14.190806Z`。独立watchdog与driver共同执行原停止边界；停止后续输入不等于远端取消或资源已释放。
- 原轮次另有1800秒上限；run.started_at为12:34:35.427Z，对应13:04:35.427Z。13:04:48.706895Z登记TIMEOUT，13:04:49.516584Z driver报告HALTED；没有为等待B而延长轮次或整体截止。
- R31采用ordinary startup-v2，两臂均关闭可选response observer。**没有启动原生 `/goal`，也没有验证独立 `/loop` 长时运行。** 旧R29/R30失败记录和全部旧会话保留。
- 外部driver只发许可、启动一次、发布文件和测量。协议要求START、本地业务、独立ACCEPT和FINISH由原生实验Codex执行，Claude节点仅由两个固定Claude会话执行；本轮完成START和A节点独立ACCEPT，本地merge与整轮FINISH未执行。

## 已核实进度

| 层次 | 结果 | 实际证据 |
|---|---|---|
| 双Claude启动 | 2/2 | 两端实际2.1.303，原生assistant模型字段 `claude-opus-5-5`，执行probe并返回 `EXPERIMENT_READY`。 |
| 三端采用 | 3/3 | 各端真实probe调用/结果、自动PreToolUse与PostToolUse、进程身份和隔离；不以配置落盘替代采用。 |
| pilot-01 START | 已执行 | 原Codex于12:34:35.427Z执行精确脚本；run于12:34:55.252Z创建。 |
| 草稿发布 | 4节点 | `publish-drafts`返回 `dispatch_authorized=false`；这一步没有派发任务。 |
| 首个正式握手 | Claude A PASS | 原native user、完整body读取、精确nonce ACK及原receipt全部核对。 |
| Claude B正式握手 | 未完成 | source_index于12:50:43.669116Z进入PREPARING；截至13:09:05Z，B仍373839字节、原device/inode匹配、原run fence后0字节，无晚到握手或正式任务。 |
| Claude A正式任务收件 | 已核实 | 12:45:18.003Z新增原生user与TASK_PACK_V2通知全文一致，task pack SHA匹配；12:46:37.683Z已完整读取任务包。 |
| Claude A业务执行 | 单节点已独立验收 | 12:49:13.303Z调用业务命令，12:49:20.804Z返回产物；report.md和result.json完整SHA已核，之后取得独立checker及正式ACCEPT。 |
| Claude A原次callback | 已确认，无重发 | 12:50:53.453Z精确DONE进入原Codex native user；12:53:23.781Z原reconcile-only成功，confirmed=true、reconciled_read_only=true、bridge_retries=0。 |
| 独立业务验收 | A checker 8/8；正式ACCEPTED | checker于12:58:50.643739Z完成；13:00:20.898Z调用ACCEPT，13:02:48.170221Z记录ACCEPTED，13:03:05.153Z原工具exit0。 |
| 合格pilot/业务/配对 | 0/3、0/33、0/15 | 33个注册槽全部保留：1 TIMEOUT＋32 MISSING。A单节点接受不构成完整轮次通过。 |
| 普通通信矩阵 | 0/192个计划槽取得合格闭环 | 未进入非pilot比较；计划槽不等于已发送消息，也不把启动、握手或callback算入这192槽。 |

工作区UUID为 `1c78c085-b391-48c5-8724-ab5a9fe63d86`，固定客户端如下。

| 客户端 | 原生session | surface UUID | PID |
|---|---|---|---:|
| Claude A | `788a789d-f5e9-4773-bfc9-b9ea74be01dd` | `424ce152-475b-44d6-8541-131c140f9157` | 1141 |
| Claude B | `6293bb76-9e0b-492b-80b3-c64c188a7f14` | `dfe4e0dd-d730-4d49-b5f1-efc5a5c9673d` | 1146 |
| Codex | `01a15421-f580-7f01-aa5f-aca50d7785dc` | `0bc3b043-661f-42b6-93ad-93e918fcf373` | 1159 |

## 首个握手与控制耗时

| 原始事件 | UTC时间/耗时 |
|---|---|
| 首个advance原生调用 | 12:35:44.726Z |
| 首个advance返回，只完成inventory claim | 内部总计114.780秒；原生结果12:37:44.697Z收取 |
| 第二个advance原生调用 | 12:38:04.214Z |
| A preflight intent→result | 12:39:00.289476Z→12:41:37.642982Z，157.354秒 |
| 原握手dispatch_started→dispatch_submitted | 12:39:19.826216Z→12:40:08.022318Z，48.196秒 |
| A原生日志收到notice | 12:39:56.708Z |
| A校验body SHA/bytes成功 | 12:40:15.715Z |
| A读取1687字节完整body | 12:40:16.985Z |
| A原生assistant ACK | 12:41:30.473Z |
| 原receipt确认ACK | 12:41:32.902097Z |
| inventory PREPARED事件 | 12:42:11.038388Z |
| A原生user收到TASK_PACK_V2 | 12:45:18.003Z |
| A校验正式task pack SHA/55983 bytes成功 | 12:46:06.167Z |
| inventory DISPATCHED事件 | 12:46:06.477926Z |
| A完整读取正式task pack | 12:46:37.683Z |
| A调用业务命令→原结果返回 | 12:49:13.303Z→12:49:20.804Z，7.501秒 |
| A原callback RPC_ACCEPTED | 12:50:31.190060Z |
| 原callback helper首次返回 | 12:50:36.429Z，exit75 / callback not confirmed |
| B source_index PREPARING | 12:50:43.669116Z |
| Codex收到A原callback native user | 12:50:53.453Z |
| A原reconcile-only调用→成功返回 | 12:53:16.323Z→12:53:23.781Z |
| Codex原callback advance返回 | 12:55:48.370Z；内部248.0557秒，只导入callback，input_operations=0 |
| A独立checker执行 | 12:58:49.991552Z→12:58:50.643739Z，0.652187秒，8/8通过 |
| inventory EVIDENCE_READY | 12:59:32.270249Z |
| 原Codex ACCEPT调用 | 13:00:20.898Z；accepted_at记录的是该调用时间 |
| inventory ACCEPTED事件 | 13:02:48.170221Z；调用至事件147.272221秒 |
| 原生ACCEPT工具结果 | 13:02:48.231Z，status=ACCEPTED、checks=8、transport_retries=0、failed_api_attempts=0 |
| inventory租约释放 | 13:02:48.182604Z；不等于所有节点解除 |
| 原ACCEPT工具结束 | 13:03:05.153Z，exit0 |
| 原轮次终态 | 13:04:48.706895Z登记TIMEOUT；driver/watchdog退出码均2 |
| 最后原advance返回 | 13:05:54.069Z，内部129.182587秒，actions=[]；晚于停止标记，无新动作 |
| 原Codex native final | 13:07:30.218Z，明确三轮pilot未完成 |

该ACK匹配原任务 `corpus-v3-aa13dd5772c8aed3f18ec54e--pilot-01--inventory--a0001` 及原nonce `d7504996`。新增user全文SHA为 `0aec04fb12056cc3a3c6ebd2eeed2a3dd8c43dd47f7b7a2a02dbafb589a49ce5`，与原receipt一致；body SHA为 `bc4b5f0b77eceb22eeba276ff62971053a3dde8e98e944d4e489ea39546b6e2b`。

首个114.780秒是只完成一次claim的 **advance内部总时间**，包含建议、身份和配置校验；还不能把它写成纯claim、锁等待或Claude耗时。原生收件到ACK为93.765秒，同样包含客户端工具与校验过程，不能全算作模型推理。`--max-seconds 45`只在同步动作之间检查，单次preparation允许600秒；其超出45秒不证明进程死锁。

[控制开销独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-audit/REPORT.md)已确认：每次私有NativeReader初始化都会重新完整校验29个startup pins，合计1,066,091,209字节；每次Coordinator构造静态至少包含7次这样的校验。此数字为逻辑校验字节量，不是实测磁盘吞吐；两秒进程采样显示多个worker线程执行SHA256，不是主线程比例。原preflight 157.354秒之外，进入前校验及后续准备/证明另有89.471秒，尚无更细的独占耗时归因。

后续有界测量直接调用同一原完整pin函数，29个文件连续三次全部通过，墙钟分别0.518210、0.472688、0.516498秒，没有清空系统缓存。这是当前条件的组件结果，不是原R31控制链复现；不足以解释原129秒advance，更不能把全部延迟归于哈希。详见[控制开销验证](CONTROL-OVERHEAD-VALIDATION.md)。

另已确认每次advance重新初始化公平计数。每次只容得下一步时，固定顺序先推进A的claim、prepare、dispatch，再轮到B的claim；这不等于A必须完成全部业务链后B才能启动。下一版改按已记录的生命周期阶段，在同优先级下让独立就绪lane依次claim、prepare、dispatch，保留收尾优先级和每步新门禁。

修复仅在同一次无动作的只读观察中复用协调器与已验证状态，保留动作前的新校验、原生快照验证和最终刷新。两项源码修复已完成root复核，并以 `ae2c3c645dabfa97be736fefff9609dadf4f715b` 推送至upgrade仓库；R31始终使用原冻结实现。定向执行分别为106项、修正后39项、最后加强8项通过，覆盖重叠且106项与后续编辑重叠，不能合计或称为一次冻结全套。原31项中的首次失败保留。详见[控制开销修复验证](CONTROL-OVERHEAD-VALIDATION.md)。完整实际字节校验保留，不引入跨动作digest/stat缓存，修复后原生速度和15%提效尚未证明。

## A业务产物和迟到回调

A原业务命令返回的report.md为167字节，SHA256 `cf1831e44c6312f83cb8dc278c19b65070dc177103c5c093ed65e79ebf33c2b6`；result.json为16365字节，SHA256 `cc194ba97c928308809629973bbe2288b35fd018a449803e16749fb3489b9870`。7.501秒为原生工具调用到原结果的时间，不能当作独占CPU耗时；产物写入也不等于验收。

原callback attempt-0001先获RPC_ACCEPTED，5秒扫描未见native记录后helper返回exit75。17.024秒后原Codex transcript追加完整DONE；原device/inode、fence之后offset1340440、length880及全文均已独审核对，未重新发送。A随后以同一pack执行原reconcile-only，将原receipt确认为成功。保留首次未确认和后续确认两个事实，不能把exit75解释为key失效、确定未送达或重发授权，也不能把迟到收件解释为独立ACCEPT。

## 终态、停止与未完成部分

原final-report于13:04:49.557056Z生成：33个注册槽为1 TIMEOUT＋32 MISSING，passing/verified/measured均0，available_pairs为0，提效与成本估计为null/UNKNOWN。外层原工具92138已收取，driver/watchdog均exit2；整次外层耗时2233.273075秒，包含启动，不能与单轮1800秒预算混用。报告的input_operations=0不能解释成原实验没有输入；本轮有已证明的启动、握手、正式任务和callback，完整轮次指标尚未形成。

7组原停止标记的字节和SHA均与watchdog引用匹配。all_stopped=true仅证明STOPPED_INPUT；terminate_clients=false，13:09:05Z核查时两个Claude、Codex和app-server原PID仍在。A租约已释放，但其disarm被EXPERIMENT_STOP_REQUESTED拒绝，没有disarmed.json；B保留PREPARING及原租约，另外两个后继节点未启动。不能声称全部任务解除、进程退出或远端取消。未通过轮次不补样，不恢复停止后的输入。

完整双路握手、三轮pilot、15对正确性/效率比较、192槽普通通信，以及独立Goal/loop验证均未闭环。已证实的控制开销与调度偏序可指导下一版，但不能凭本次单节点用时推导整体速度改善或把全部等待归因于哈希。

## 实现、CI与任务初衷

受测提交的[四格CI](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39249352530)已经完成，不重复执行。每格发现2184项：macOS/Python3.10及3.14各通过2080、跳过104；Ubuntu两格各通过2070、跳过114，全部零失败/错误。本地29项专项和修复后2项检查分别记账，见[握手结算验证](HANDSHAKE-SETTLEMENT-VALIDATION.md)。这些结果不替代原生协作或速度验证。

修复提交 `ae2c3c6` 的[独立四格CI](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39257614544)已完成：Python3.14两格通过；Python3.10两格因新增测试直接解析末尾Z时间而各报1个ValueError。每格Ran2194项，原失败完整保留。单行兼容修复 `9e5adfdde29cbf7b8da9aadb2ad92f92695ab722` 已推送，定向8项通过；该精确提交的[新四格CI](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39259307066)于13:20:24–13:20:27Z只触发一次，13:30:23Z完成，四格全部SUCCESS、零失败/错误。每格Ran2194项：Ubuntu两格各通过2080、跳过114；macOS两格各通过2090、跳过104。通过数按Ran减跳过推导，discovered未独立输出。四份原日志均已收取，完整分格计数见[控制开销验证](CONTROL-OVERHEAD-VALIDATION.md)。后续仅为文档提交；旧提交CI不移算，也不把修复版CI通过当作R31或新版本原生协作通过。

继续保留cmux作为原生会话/终端载体，MAC负责绑定和可靠投递，upgrade负责依赖就绪、两路执行、下一任务草稿、持久回报、核收后接续和等待归因。Jev/TypeSafe仅为本地rules/offline mock，没有引入在线JEV依赖。能否提高效率由正确性成立后的配对结果判定；目前没有达到≥15%改善的原生证据，也不能断言其他平台一定更慢。

[41类协作问题台账](COLLABORATION-ISSUE-LEDGER.md)和[15条社区经验核查](COMMUNITY-ADOPTION-20261019.md)沿用已完成研究。surface:1000020040仍统一维护共享MAC/论文精炼助手；已核owner回件和PR55，不重复修改共享层。后续 `maintenance/post-r30-pr55/owner-followup-reply.md` 在本检查点仍缺失。R31终态、晚到callback和控制开销已沿[既有维护inbox](/Users/lzhs/Documents/cnm/tasks/06_Mintransition/verification/ccc-successor-claude-20261010/maintenance-inbox/upgrade42-to-surface1000020040-r31-terminal-20261019T1315Z.json)回传；这是文件发布，不宣称owner原生收件、正文阅读或接受。

## 原始证据

- [三端采用独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T123341Z-REPORT.md)：SHA256 `dbfd8d0e9ae0b037974742f0bf26afd8b5a9dc738ec128b4045fe3dbbdd84924`。
- [A握手增量独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T124147Z-pilot-01-increment.md)。
- [A正式任务收件独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T124518Z-pilot-01-five-minute.md)。
- [A迟到原生回调独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T125152Z-callback-late-native.md)及[原次只读核收增量](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T125352Z-native-increment.json)。
- [原adoption](/Users/lzhs/uv/g/0/evidence/B/live-control/adoption.json)：SHA256 `b794bdead73052dc7a29f418fa2463b8aec302464ee070c3d87ce1e96c4020d8`。
- [原run](/Users/lzhs/uv/g/0/campaigns/B/pilot-three-rounds/runs/pilot-01/run.json)、[A原握手receipt](/Users/lzhs/uv/g/0/campaigns/B/pilot-three-rounds/runs/pilot-01/nodes/inventory/preflight/handshake-receipt.json)。
- [外层原次日志](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r31-original-driver/stdout.jsonl)。
- [原终态报告](/Users/lzhs/uv/driver/final-report.json)：25253字节，SHA256 `7db80dc08044004b5d4de0fe8ecf0a8c3cc0ba6604e457381c58015cdb4066b5`。
- [外层原次结果](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r31-original-driver/result.json)：SHA256 `8f77839a420dc817b9bb89c43c22f0ecea7a99a2ed9447ee76a64221cc396765`。
- [终态独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T130905Z-terminal-audit.md)及[完整33槽、停止标记与原生证据](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T130905Z-terminal-audit.json)：JSON SHA256 `8cdd207845e4f9ef139e290141a577251731f9e30e96362c608d86e3627f8695`。

本轮结果为失败或证据不足；实现修复和CI结果单独交付，不宣称完成全量原生压力验证或达到提效目标。
