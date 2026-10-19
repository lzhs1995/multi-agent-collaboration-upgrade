# R31 原生双 Claude 实验

当前为运行中检查点，观测截至 **2026-10-19 13:02 UTC（台北21:02）**。双 Claude 已真实启动并执行工具；Claude A 已完成首个正式握手、任务收件、业务产物及原次callback核收，独立checker通过8/8检查。原Codex ACCEPT尚待原次返回，不能以accepted-evidence文件单独判定接受完成。Claude B 的正式握手与双路独立验收仍未通过。此文不能作为生产采用、提效或完成全量压测的证明。

## 原次与固定边界

- 原次：`resume-20261019-r31-native-retry`，根 `/Users/lzhs/uv`；原外层工具 `92138`。只观察/收取这一原次，不重新prepare、configure、launch或补发消息。
- 冻结实现：`7fa8e4ee158d7fff82536ec153072c48183ab036`，`resume-20261019-live-v26/source`。私有MAC基底仍为 `0.4.34-d333557fb498`，不热加载共享发布。
- 固定矩阵：7组、33轮（3轮pilot＋30轮比较）、15对、192个计划普通消息槽。只有连续3轮pilot成功才进入配对；失败和缺失保留在分母。计划消息槽不等于实际发出消息。
- 实际不可延长截止：`2026-10-19T13:17:24.292814Z`（台北21:17:24）；另保留原外层授权截止 `13:18:14.190806Z`。独立watchdog与driver共同执行原停止边界；停止后续输入不等于远端取消或资源已释放。
- R31采用ordinary startup-v2，两臂均关闭可选response observer。**没有启动原生 `/goal`，也没有验证独立 `/loop` 长时运行。** 旧R29/R30失败记录和全部旧会话保留。
- 外部driver只发许可、启动一次、发布文件和测量。原生实验Codex亲自执行START、本地业务、独立ACCEPT和FINISH，Claude节点仅由两个固定Claude会话执行。

## 已核实进度

| 层次 | 结果 | 实际证据 |
|---|---|---|
| 双Claude启动 | 2/2 | 两端实际2.1.303，原生assistant模型字段 `claude-opus-5-5`，执行probe并返回 `EXPERIMENT_READY`。 |
| 三端采用 | 3/3 | 各端真实probe调用/结果、自动PreToolUse与PostToolUse、进程身份和隔离；不以配置落盘替代采用。 |
| pilot-01 START | 已执行 | 原Codex于12:34:35.427Z执行精确脚本；run于12:34:55.252Z创建。 |
| 草稿发布 | 4节点 | `publish-drafts`返回 `dispatch_authorized=false`；这一步没有派发任务。 |
| 首个正式握手 | Claude A PASS | 原native user、完整body读取、精确nonce ACK及原receipt全部核对。 |
| Claude B正式握手 | 准备中 | source_index于12:50:43.669116Z进入PREPARING；截至13:02:47Z，B原启动fence后仍无新user记录。 |
| Claude A正式任务收件 | 已核实 | 12:45:18.003Z新增原生user与TASK_PACK_V2通知全文一致，task pack SHA匹配；12:46:37.683Z已完整读取任务包。 |
| Claude A业务执行 | 产物已生成，未独立验收 | 12:49:13.303Z调用业务命令，12:49:20.804Z返回WORK_WRITTEN_REQUIRES_INDEPENDENT_ACCEPTANCE；report.md和result.json完整SHA已核。 |
| Claude A原次callback | 已确认，无重发 | 12:50:53.453Z精确DONE进入原Codex native user；12:53:23.781Z原reconcile-only成功，confirmed=true、reconciled_read_only=true、bridge_retries=0。 |
| 独立业务验收 | checker 8/8；ACCEPT原次待收 | checker于12:58:50.643739Z完成；13:00:20.898Z原Codex调用ACCEPT。accepted-evidence已生成，最新事件仍EVIDENCE_READY。 |
| 合格pilot/业务/配对 | 0/3、0/33、0/15 | 运行中的未完成轮次不提前记为通过。 |

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
| 原Codex ACCEPT调用 | 13:00:20.898Z；原次内部工具13102，外部不代收 |

该ACK匹配原任务 `corpus-v3-aa13dd5772c8aed3f18ec54e--pilot-01--inventory--a0001` 及原nonce `d7504996`。新增user全文SHA为 `0aec04fb12056cc3a3c6ebd2eeed2a3dd8c43dd47f7b7a2a02dbafb589a49ce5`，与原receipt一致；body SHA为 `bc4b5f0b77eceb22eeba276ff62971053a3dde8e98e944d4e489ea39546b6e2b`。

首个114.780秒是只完成一次claim的 **advance内部总时间**，包含建议、身份和配置校验；还不能把它写成纯claim、锁等待或Claude耗时。原生收件到ACK为93.765秒，同样包含客户端工具与校验过程，不能全算作模型推理。`--max-seconds 45`只在同步动作之间检查，单次preparation允许600秒；其超出45秒不证明进程死锁。

[控制开销独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/preparation/r31-candidate/control-overhead-audit/REPORT.md)已确认：每次私有NativeReader初始化都会重新完整校验29个startup pins，合计1,066,091,209字节；每次Coordinator构造静态至少包含7次这样的校验。此数字为逻辑校验字节量，不是实测磁盘吞吐；两秒进程采样显示多个worker线程执行SHA256，不是主线程比例。原preflight 157.354秒之外，进入前校验及后续准备/证明另有89.471秒，尚无更细的独占耗时归因。

另已确认每次advance重新初始化公平计数。每次只容得下一步时，固定顺序先推进A的claim、prepare、dispatch，再轮到B的claim；这不等于A必须完成全部业务链后B才能启动。下一版改按已记录的生命周期阶段，在同优先级下让独立就绪lane依次claim、prepare、dispatch，保留收尾优先级和每步新门禁。

下一版还仅在同一次无动作的只读观察中复用协调器与已验证状态，保留动作前的新校验、原生快照验证和最终刷新。两项源码修复已落地并完成root复核；定向执行分别为106项、修正后39项、最后加强8项通过，覆盖重叠且106项与后续编辑重叠，不能合计或称为一次冻结全套。原31项中的首次失败保留。详见[控制开销修复验证](CONTROL-OVERHEAD-VALIDATION.md)。不修改R31冻结源码、原任务、输入或截止。完整实际字节校验保留，不引入跨动作digest/stat缓存，修复后原生速度和15%提效尚未证明。

## A业务产物和迟到回调

A原业务命令返回的report.md为167字节，SHA256 `cf1831e44c6312f83cb8dc278c19b65070dc177103c5c093ed65e79ebf33c2b6`；result.json为16365字节，SHA256 `cc194ba97c928308809629973bbe2288b35fd018a449803e16749fb3489b9870`。7.501秒为原生工具调用到原结果的时间，不能当作独占CPU耗时；产物写入也不等于验收。

原callback attempt-0001先获RPC_ACCEPTED，5秒扫描未见native记录后helper返回exit75。17.024秒后原Codex transcript追加完整DONE；原device/inode、fence之后offset1340440、length880及全文均已独审核对，未重新发送。A随后以同一pack执行原reconcile-only，将原receipt确认为成功。保留首次未确认和后续确认两个事实，不能把exit75解释为key失效、确定未送达或重发授权，也不能把迟到收件解释为独立ACCEPT。

## 实现、CI与任务初衷

受测提交的[四格CI](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39249352530)已经完成，不重复执行。每格发现2184项：macOS/Python3.10及3.14各通过2080、跳过104；Ubuntu两格各通过2070、跳过114，全部零失败/错误。本地29项专项和修复后2项检查分别记账，见[握手结算验证](HANDSHAKE-SETTLEMENT-VALIDATION.md)。这些结果不替代原生协作或速度验证。

继续保留cmux作为原生会话/终端载体，MAC负责绑定和可靠投递，upgrade负责依赖就绪、两路执行、下一任务草稿、持久回报、核收后接续和等待归因。Jev/TypeSafe仅为本地rules/offline mock，没有引入在线JEV依赖。能否提高效率由正确性成立后的配对结果判定；目前没有达到≥15%改善的原生证据，也不能断言其他平台一定更慢。

[41类协作问题台账](COLLABORATION-ISSUE-LEDGER.md)和[15条社区经验核查](COMMUNITY-ADOPTION-20261019.md)沿用已完成研究。surface:1000020040仍统一维护共享MAC/论文精炼助手；已核owner回件和PR55，不重复修改共享层。后续 `maintenance/post-r30-pr55/owner-followup-reply.md` 在本检查点仍缺失，不因此阻止原实验。

## 原始证据

- [三端采用独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T123341Z-REPORT.md)：SHA256 `dbfd8d0e9ae0b037974742f0bf26afd8b5a9dc738ec128b4045fe3dbbdd84924`。
- [A握手增量独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T124147Z-pilot-01-increment.md)。
- [A正式任务收件独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T124518Z-pilot-01-five-minute.md)。
- [A迟到原生回调独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T125152Z-callback-late-native.md)及[原次只读核收增量](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r31-native-adoption-audit/20261019T125352Z-native-increment.json)。
- [原adoption](/Users/lzhs/uv/g/0/evidence/B/live-control/adoption.json)：SHA256 `b794bdead73052dc7a29f418fa2463b8aec302464ee070c3d87ce1e96c4020d8`。
- [原run](/Users/lzhs/uv/g/0/campaigns/B/pilot-three-rounds/runs/pilot-01/run.json)、[A原握手receipt](/Users/lzhs/uv/g/0/campaigns/B/pilot-three-rounds/runs/pilot-01/nodes/inventory/preflight/handshake-receipt.json)。
- [外层原次日志](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r31-original-driver/stdout.jsonl)。

最终收口应补齐全部注册轮次、原生投递与回报、失败原因、控制耗时和截止后的实际边界；不得用当前部分进度宣称完成全量实验。
