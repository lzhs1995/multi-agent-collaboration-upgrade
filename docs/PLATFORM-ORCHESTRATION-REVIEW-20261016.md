**Orca、cmux、Paseo、Conductor：面向 Codex 主管与多个 Claude 执行者的源码审计与实施计划**

审计日期：2026-10-16。GitHub 资料全部通过 gh CLI 获取；结论对应归档时的源码、发行版及 Issue/PR 状态。本轮完成研究与本地报告，没有安装候选软件、迁移会话或执行跨平台性能实验。

**若从四个项目中选一个作为任务编排的主要参考和候选底座，首选 Orca；Paseo 是第二选择，其主动 callback 尤其值得采用。** Orca 的优势在于持久任务依赖、权威 Dispatch、可重放邮箱和结算后复用；Paseo 的优势在于执行者结束后主动续起主管。对你现有系统，建议将这些机制接入 upgrade，继续让 MAC 承担原会话投递、thesis-refiner 承担学术验收、CCC 承担运行维护。cmux 可以继续承载终端，但它的终端体验不构成调度优势。

这是架构适配结论，尚不是“Orca 实测最快”或“安装后即可无改动接管现有会话”的结论。两份原分析都不足以支持这样的性能判断：内容一用工作台完整度和 stars 推出最佳编排器；内容二则把超时预算、业务任务时长和部分缺陷报告解释成必然更慢，过早排除了可借鉴的编排机制。

你的四个仓库应按职责评估，CCC 本身是 CLI 项目：

| 仓库 | 已核实的职责与状态 | 选型时应保留的要求 |
| --- | --- | --- |
| [multi-agent-collaboration][U-mac] | 审计基线 0.4.34；复用指定会话，Codex 原生输入区分运行中 steer 与空闲 start，保留原尝试证据 | 新增原生 user 记录与完整载荷逐字相等才能证明接收；RPC 成功、ACK、正文已读和业务验收分别记录 |
| [thesis-refiner][U-thesis] | 学术工作流消费领域工具的真实回执；文件调度不能自行唤醒空闲模型 | 数值、来源、报告哈希、语义与视觉验收不能由 agent 的 finished 状态代替 |
| [multi-agent-collaboration-upgrade][U-upgrade] | 已从早期 planner 扩展到规则决策、持久队列、claims/leases、原会话策略、drafts 与 next-action；当前仍是显式调用的冻结 CLI | 一会话一个活动任务、写范围隔离、原控制器与未知投递保留；下一步要补事件驱动的执行闭环 |
| [ccc][U-ccc] | 续跑、配置、监控、清理等运行维护；supervisor 表格是只读状态投影，其他显式控制可以影响会话 | 运行维护状态不等于任务依赖或业务完成；不能把 CCC 整个项目概括为纯只读 |

MAC 的[接收证明合同][U-delivery]比候选平台的通常发送回执严格，但这并不推出“候选项目的调度思想无用”。可以保留接收与验收合同，同时改善事件消费、任务准备、就绪选择和主管唤醒。真正需要比较的是这些环节，而不是谁最像当前终端。

**内容一的产品简介部分成立，优劣排序依据不充分。**

| 原说法 | 核查结果 |
| --- | --- |
| Orca 是支持多 CLI agent、worktree、桌面与移动端的 ADE | 与[仓库介绍][O-readme]方向一致；本次没有逐一执行全部 agent、移动端或远程功能，兼容数量不作为性能证据 |
| Orca 约 94k、cmux 约 28k、Paseo 约 21k stars | 本次快照分别为 [94,090][O-meta]、[28,207][C-meta]、[21,085][P-meta]；原文相近数字可视为不同时间快照。stars 不能证明分派延迟、可靠性或维护集中度；“细分领域最高”还需要明确候选全集 |
| Orca MIT、Paseo AGPL、cmux 开源核心加商业层 | Orca [MIT][O-license]正确；Paseo 实为 [Apache-2.0][P-license]。cmux [README][C-readme]列核心 GPL-3.0-or-later、服务端 BSL 1.1，不能以 API 的 NOASSERTION 代替许可证文件 |
| Orca 每项任务都在独立 worktree，cmux 主要靠 tabs 隔离 | worktree 是 Orca 的能力，不是硬性要求；[稳定版协议][O-guide]明确接受 folder workspace。cmux 终端也能运行外部 worktree 工具。应区分终端分屏、文件隔离与任务调度 |
| Conductor 无法与开源项目比较 stars | 应区分应用实现与周边仓库。官方组织有公开仓库和 Issues，但没有在本次发现可审计的应用实现源码，详见下文 |
| Orca 免费完整，所以最适合；Paseo 更轻、维护者更少 | 许可与功能简介不能证明运行总成本、上手成本或维护质量；本次没有支持这些比较的统一实测及贡献者集中度统计 |
| cmux Pro 约 40–50 美元/月，Max 约 160–200 美元/月 | [仓库计费配置][C-pricing]支持 Pro 月付 50/年付 480 美元，Max 月付 200/年付 1,920 美元；年付折算分别为 40、160。未验证实时 checkout、权益变化和税费 |

GitHub 的 open_issues_count 包含 PR，不能直接写成“开放 Issue 数”。公开 Issue 多也不能单独推出质量差，必须看故障是否覆盖目标 provider、是否可复现、是否已修复及是否进入发行版。

**内容二识别了接收与验收的区别，但数处关键推论需要修正。**

1. **Orca 的 10/15 分钟不是每次强制等待时间。** ask 默认 600,000 ms 是[超时预算][O-timeout]；check 示例 900,000 ms 也是最大等待。源码在消息到达时立即 resolve 匹配 waiter，没有 waiter 时通过 microtask 尝试推送提示，见[通知协调器][O-wake]。把这两个常量直接换算成正常 callback 延迟不成立。不过，[指南][O-wait]要求三次空等后巡检：如果每次都耗尽示例预算，故障发现可拖到约 45 分钟。这是异常路径的监测弱点，值得改进。

2. **Orca 可以复用仍存活、已结算的同一个 agent。** [coordinator-loop][O-reuse]明确使用 worker-start --terminal 接续任务；[稳定版指南][O-settlement]也把立即复用列为结算后的第一种选择。[Issue #14388][OI14388]要求的是关闭终端后恢复 provider session，不能反证活终端复用。[#16226][OI16226]针对未注册外部 checkout 的选择器限制，并列出注册仓库等 workaround；不能扩大成所有非 Orca 创建的目录都不可用。

3. **Orca 有任务依赖管理，但不能把它说成现成的自动 DAG 派发守护进程。** [task-store][O-tasks]持久保存依赖并根据依赖完成状态决定 pending/ready；[正式协议][O-guide]同时明确 Run 是命名空间与主管邮箱，不负责 schedule/place workers。支持的 [runCreate RPC][O-rpc]是登记与绑定。仓库中旧 coordinator 的循环代码不足以证明有可用入口，不能仅凭找到循环就认定自动后继调度已上线。

4. **Orca 的消息可靠性有可取之处，也有与你合同的差距。** send 明确只证明持久入队；check 返回 [FIFO Delivery，在 ACK 前重放该批消息][O-mailbox]。原生结构化会话有[主管提示发送路径][O-structured]，可向已经结束当前 turn 的主管发起新输入，但提示主要仍是去邮箱取正文的 pointer。[PR #28567][OP28567]要改为全文注入，归档时仍 OPEN，不能算已发布。有效 worker_done 会自动结算 Task/Dispatch；你的独立产物验收应保留为另一层，或建成明确的 review 节点，不能直接映射为业务已验收。

5. **Paseo 的 10–30 分钟是任务耗时描述，callback 也不只是 UI 通知。** [skill][P-skill]建议异步执行长任务；[稳定版 send_agent_prompt][P-tools]允许向已有 agent 发后续内容，agent-scoped 调用默认 background 与 notifyOnFinish，并在新 turn 路径等待 provider run-start acknowledgment。[完成通知源码][P-callback]实际调用 sendPromptToAgent(callerAgentId)，指定 activeTurnBehavior: steer、unarchive: false，因此能继续空闲主管或向活跃主管送入内容。仅用“多一个 daemon”推断更慢，没有测量依据。

6. **Paseo 真正需要重点改的是活动任务替换和通知持久性。** 普通 send_agent_prompt 没有指定 steer；[sendPromptToAgent][P-replace]默认 replaceRunning，[manager][P-cancel]会取消已有 run 后再启动。通知路径虽然选择 steer，[Claude adapter][P-compaction]在压缩期间返回 unavailable，manager 仍存在 replacement 路径；[#6806][PI6806]报告了相关中断。[通知订阅][P-subscription]保存在 WeakMap/内存中，终态先退订再发送，发送失败只见日志，未确认持久重试。适合借鉴主动唤醒，不宜原样接管你的活动 Claude 任务。

7. **cmux 的一个 gallery PR 不能代表整个消息系统。** [#21187][CP21187]确是 fixture-only UI，尚未接 scheduler/runtime；[#21149][CP21149]是 agent-chat 邮箱持久化改动。与此同时，[已存在的 agent-messages 文档][C-messages]及消息存储源码包含 queued/delivered/read、deferred claim/ack lease 与事件流。Claude asyncRewake 约每两秒检查并可唤醒 idle；同一文档明确这些 Codex hooks 不能唤醒 idle Codex。这一限制直接关系到你的主管停在空闲时能否接 callback。read 可由 turn 结束或人工 mark-read 产生，不是语义接受；[queued→read 的实现][C-read-state]与 OPEN [#16342][CP16342]仍须重视。

8. **Conductor 有第一方公开仓库和问题追踪，但应用源码可审计性有限。** 官方 [meltylabs 组织][D-org]名称为 Conductor，包含 [conductor-releases][D-repo]和 [Starter-Project][D-starter]。前者固定提交的 tree 只有 CHANGELOG.md；后者是模板。公共 [Issue #21][DI21]报告 0.73.x 重启后后台任务与计划唤醒丢失；[#34][DI34]是 workspace 状态，[#36][DI36]是用量显示。不能再写“没有第一方仓库/Issues”，也不能由这些用户报告推出当前版本的已验证调度缺陷。未发现应用实现源码，仍足以降低它作为可改造编排底座的优先级。

9. **对 upgrade 的描述停留在旧阶段。** 当前 [a882e51 的 README][U-upgrade]和[编排文档][U-orchestration]已有持久协调器、session FIFO、claims/leases、drafts、next-action 和验收流程，不再只是图校验与任务包种子。Jev/TypeSafe 仍是 advisory/mock 接缝，真实 campaign 使用 rules，没有上线 Jev 服务调用。与此同时，源码也明确持续派发器、共享 hook 采用和实测效率尚待完成；不能从功能实现或 1,532 项离线测试推出端到端收益。

缺陷证据需要带着适用范围阅读：

| 项目及证据 | 归档状态与适用范围 | 对本次选型的含义 |
| --- | --- | --- |
| Orca [#22652][OI22652]、[#26943][OI26943]、[#21445][OI21445] | OPEN；分别报告 1.4.209 Claude 空任务、1.4.223 前台识别失败、1.4.205 submitted 措辞歧义 | 与投递链路相关，应纳入验证；不能当成 1.4.227 的失败率 |
| Orca [PR #29008][OP29008] | OPEN；作者报告 worker 静默接管主管，并给出其数据库 53 条记录 | 与主管身份及 callback 路由高度相关；53 是作者证据，不是本地复现统计 |
| Orca [PR #27795][OP27795] | 已合并，且 [v1.4.227 release][O-release]明确列入 | 失败启动反复重试堆积 agent 的修复不能仍作为“尚未修复”论据 |
| Orca [#29107][OI29107] | OPEN；报告 1.4.226 每次 dispatch 约 700 词 preamble | 已有 MAC 合同时可能重复占上下文；这是用户报告，应测 token 与轮次成本 |
| Paseo [PR #5347][PP5347]、[#5386][PP5386] | 均已合并；稳定版工具源码已有超时后通知及 run-start 等待 | 不能拿已修复行为描述现版本常态；provider 接受仍不等于全文接收或完成 |
| Paseo [#6346][PI6346]、[#6806][PI6806] | OPEN；前者是 0.10.3 一次性通知漏掉后续自主 background turn，后者涉及 Claude 压缩 | 需验证每个 Dispatch 的通知登记、重启恢复及压缩边界 |
| Paseo [#7019][PI7019]、[#5610][PI5610]、[#4231][PI4231] | 分别涉及 Pi/Gotgenes、OpenCode 子 agent UI、OpenCode error/resume | 不能泛化为 Codex 主管→Claude 执行者的共同故障 |

因此，内容二的“拒绝把派发确认当 ACK/验收”合理；“四个都不适合，Orca 采用后必然更慢”不成立。它也低估了 Paseo 主动 callback 与 cmux 已有消息系统。

**将关键能力拆开后，Orca 的优先级来自可复用的编排结构。**

| 项目 | 依赖与任务状态 | 自动后继派发 | 空闲 Codex 主管唤醒 | 消息与完成可靠性 |
| --- | --- | --- | --- | --- |
| Orca | 持久 Task/依赖/权威 Dispatch，ready 视图 | 主管按协议选择与派发；Run 本身不自动调度 | 结构化会话支持提示发送；终端主管主要依赖 check waiter；路径须实测 | 持久邮箱、FIFO Delivery/ACK；提示尽力而为，worker_done 结算不等于独立业务验收 |
| Paseo | agent/turn 生命周期；有 schedules/heartbeat，未确认依赖完成驱动的通用 DAG 调度器 | 通知续起主管，再由主管发 prompt | callback 主动 sendPromptToAgent；比仅等下一次 hook 更贴近需求 | 完成订阅目前在内存；普通发送可替换活动 run；需补持久性与保守投递策略 |
| cmux | workspace/surface、进程与消息状态；[Python 示例][C-example]是固定 setup/build/test schema | 未确认可直接满足需求的通用依赖调度器 | 审计文档中的 Codex hooks 不支持 idle 唤醒 | 有真实持久消息和 lease；read 含义弱于你的接收/接受合同 |
| Conductor | 可确认 worktree 并行与 workspace 管理 | 应用实现未公开，无法核定 | 有相关后台唤醒用户报告，无法核定当前保证 | 可查官方 Issues，但无法沿应用源码核验持久通知、去重和会话复用合同 |
| 现有 upgrade | 持久协调器、claims/leases、规则及 FIFO 策略 | 仍需显式 CLI 调用 | MAC 支持原生输入；文件观察器本身不能唤醒模型 | 已有严格投递与独立验收路径；闭环采用和实跑仍缺证据 |

选择 Orca 的主要理由是任务依赖、执行尝试与消息消费能形成明确边界，最接近你要优化的“一个主管连续安排多路执行者”。Paseo 的唤醒路径更直接，但单靠主动通知无法替代任务依赖、写者约束与独立验收。两者的开源许可也允许在遵循各自条款的前提下研究和复用代码；直接复制实现时仍需逐文件确认来源与许可。

按你的用途，四者取舍如下：

- **Orca：主要参考／首个候选底座。** 适合借鉴 Task、Dispatch、持久 inbox、ACK 重放和结算后立即复用。代价是完整 ADE/runtime 接入、preamble 成本，以及现有 cmux 会话与其身份/投递模型的适配；本次未证明能够原样接管你的原会话。
- **Paseo：callback 机制首选参考，整体编排第二候选。** 已有 agentId 定址与主动主管续跑值得采用；需要防止活动任务被 replace，并补持久通知、依赖状态和业务验收。
- **cmux：继续作为终端与可观测性承载层。** 优点是与你原会话和 CCC 的操作面一致；不足是它并未为目标流程提供同等完整的通用编排闭环。保留它的理由是职责适配和接入成本，不是调度更强。
- **Conductor：可作为工作台体验候选，当前不列为可改造编排底座首选。** 缺少应用源码使本次无法审计你最关心的控制路径；这不等同于判定它的产品体验或性能差。

现阶段最合适的实施路线是 **upgrade 的现有协调器＋Orca 式任务/尝试/邮箱边界＋Paseo 式事件唤醒＋MAC 原会话投递**。如果以后考虑整套迁移，先以 Orca 为对照候选做相同工作负载实验，再决定是否让它承接运行时，不能只做界面试用就认定编排改善。

这次也核对了旧会话工作的完成边界：

| 原任务事项 | 当前证据状态 | 后续动作 |
| --- | --- | --- |
| 代码实现与发布 | 分支 upgrade/recovery-v2-20261014，HEAD a882e51c5afc2f5cd975a50bacb3dff84d210936；1,532 项离线测试通过，source export 与发布已完成；[PR #1](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/pull/1) 为 draft | 不重复测试、export 或发布；该增量为 solo_self_review，不能写成双 agent 已独立验收 |
| 原 Claude 的 V23 报告 | 本次续跑已确认冻结报告存在且哈希匹配；正式验收记录未写；报告覆盖旧分配快照 | 验收旧快照；新 observer 增量需另外完成独立审查，不重复分配 V23 |
| 共享维护通知 | 原 publication notice 仍 pending；原尝试及控制器保留 | 只处理原证据上的真实回执，不重发旧通知 |
| MAC 0.4.35 | 发布、部署及活跃客户端 adoption 未证明 | 分别核实，不能由仓库有代码推断在线客户端已采用 |
| 自动闭环与效率 | 连续自动派发器未上线；九次 serial/omo/codeg 内部实跑尚未完成 | 先补闭环和真实阶段证据，再执行既定比较；内部方案实验不能证明跨平台优越性 |

本报告继承的是 surface:11670、thread 01a13a5f-2412-7012-a872-501c8f75fda9 的业务记录与证据，不把历史端点身份视为当前调用者已经取得的发送权限。研究不依赖操作那些端点。

后续实施分六阶段推进；以下是详细计划，本轮未执行运行时改造。

1. **统一事件及度量，复用现有协调器。** 在 orchestration_coordinator.py、orchestration_adapter.py、orchestration_observe.py 的现有边界补齐事件映射：任务就绪、claim、输入 intent、原生接收、ACK/正文读取、工作开始/结束、callback、报告冻结/发现、独立验收、租约释放、下一任务输入。每条证据绑定 task/dispatch、原 session、controller、payload/report hash、事件序号和时钟来源；缺失时间保持 null。交付事件表和延迟分解，验证可从原事件重建状态，不能由一个 finished 推造后续证据。

2. **实现持久事件消费与增量就绪选择。** 在现有协调器上建立可恢复的消费 cursor/outbox 和单一派发所有者，原子关联状态提交与事件发布；消费者幂等处理重复、乱序和重启。只重算受变更影响的依赖节点，优先利用已释放的 executor 槽位。独立任务允许同时启动；某节点依赖已验收就立即成为候选，不必等同一波中无关的慢任务全部结束。交付可重放的消费入口，验证崩溃恢复后任务不丢、不会产生第二个派发所有者。

3. **把预览接到有界的原会话派发。** 当前任务运行时用 drafts 提前准备后继包；到就绪时才绑定真实依赖产物、claim 和授权。新任务统一调用现有 mac_harness/bridge，一会话一个活动节点、一写范围一个写者；scope 与健康身份允许时复用已有握手，避免每个节点重跑全部协商。缓存仅限不可变契约和内容哈希，最终输入前的会话身份、turn、草稿及所有权检查仍须新鲜。交付一个可显式启动、停止和限并发的派发入口；无任务时等待事件，不持续调用会重跑业务 checker 的 status/ready。

4. **补齐 callback 对主管的主动唤醒与独立验收。** 吸收 Paseo 的完成事件→主管输入路径，但新通知先持久登记。对原主管使用 MAC 支持的活动 turn/steer 或空闲 turn/start；结果不明只观察原尝试，不重发。同一时刻多个新事件可合并为一次待办提示以减少模型轮次，各原始 callback 与证明保持独立。主管读取完整报告并写入独立验收；只把已验收依赖交给后继。该阶段需由原共享运行时维护所有者集成，分别证明注册、客户端实际加载、事件发生、原生收到、正文已读和验收，文件存在不算链路完成。

5. **验证恢复、去重与异常边界。** 对断连、provider 403、压缩中、原会话消失、callback 乱序、通知发送后崩溃、验收失败和租约未释放进行定向验证。重放状态事件不等于重放终端输入；历史 pending 保留原 controller/nonce/fence，不能迁移成新发送。明确任务失败、API 不可用、未知投递与等待验收，只有受影响 lane 停止，其他无依赖工作继续。交付失败矩阵与恢复记录；验收要求零重复输入、零跨 session 偷换、零未经证明的依赖放行。

6. **执行对照实验并按门槛决定采用范围。** 先完成现有内部 serial/omo/codeg 的九次计划，证明本地闭环；再独立比较 Orca、Paseo 与当前栈，Conductor 若只能黑盒测试就单列。固定任务图、模型/provider 版本、并发数、机器、数据与产物验收，交错运行顺序；冷启动和原会话复用分组，不把冷启动 Orca 与热会话 MAC 混比。跨平台先导可按每场景每方案五次预登记，报告全部尝试和波动；样本不足不声称 P95 稳定或统计显著。推广门槛沿用至少 15% 的可比中位数改善，同时全部正确性与投递要求通过。

实验应覆盖短独立任务、带依赖的多阶段任务、混合长短任务及论文“分析→冻结产物→审查→修订”流程。只有所有方案都能执行并通过同一验收的任务才用于直接比较；无法接管指定会话属于适配限制，不能把它记成零耗时。

| 指标 | 起止证据／统计方式 | 用途 |
| --- | --- | --- |
| 握手与启动 | 首次授权绑定至健康可派发；冷启动与复用分开 | 量化上下文重建和启动成本 |
| 就绪到分派 | 依赖验收且槽位可用至原生输入 intent/接收 | 看调度器是否让可执行任务空等 |
| callback 与续派 | executor 完成→callback 收到→报告读取→验收→下一任务接收 | 将传输、主管唤醒、审查和调度分别归因 |
| 总墙钟 | 相同任务集从开始至最终业务验收，包含 provider 等待、恢复和人工介入 | 决定用户真正是否更快；provider 排队另列，但不从总耗时中删掉 |
| 正确性与成本 | 所有尝试的失败/重复率、人工次数、模型轮次、token、报告哈希与验收结果 | 防止以漏验收、丢消息或更多资源换取表面提速 |

15% 应在实验前明确针对哪个主指标；优先使用最终业务验收的总墙钟，同时报告调度空等分项。不能只展示成功样本的快速中位数而隐去失败率、失败恢复耗时或人工补救。原来的 790 秒与 600 秒是早期结构估算，九次内部策略比较也不包含候选平台，二者均不能作为“Orca/Paseo 更慢”的证明。

本次证据使用固定提交，避免将 main、未合并 PR 与稳定发行版混写：

| 项目 | 审计源码提交 | 对照稳定版 |
| --- | --- | --- |
| Orca | 5db05e8ed4f41e29786016cd1f236bc0a5fcf88f | v1.4.227 / f51e82c4c26745f6a15e215e4391f533327697e2 |
| Paseo | c1558d29eab78c898743adb613afa01ba4f0e780 | v0.11.4 / dc5b02f7d7c120baea380d76def8b968020d8823 |
| cmux | 6c23792198ce042b7ba2c018ca4e0b9d797a4235 | v0.65.2 / 9d01f956c51cd895e10e7d56b6dd7b5f6fcce625 |
| MAC | 29e4899d41d7c4f00f083ce092d20c84416f8e53 | 本次读取源码合同，不据此宣称客户端实际采用 |
| thesis-refiner | 18c6cc392df17d5b2219c773aa6cc44e08be3251 | 同上 |
| upgrade | a882e51c5afc2f5cd975a50bacb3dff84d210936 | PR 分支 HEAD，未用旧默认分支替代 |
| CCC | 79cc892e82d1f503ca36ea5e10d3e541dcdd8970 | 本次读取源码与说明 |
| Conductor releases / Starter | 0095de9b888f2a37413a3a3666638344028d2f46 / ff5caa01cf4502f9819553a193e4cc7ac4ec40f3 | [公开 changelog][D-changelog]顶部为 2025-12-18 的 0.27.0，不能当成当前 app 版本 |

已检查文件中，Orca 24/29、Paseo 6/15、cmux 19/19 与对应稳定版相同；这只是逐文件比较，不是整版二进制验证。尤其 Paseo v0.11.4 按 lifecycle idle 判完成，审计 main 的[通知实现][P-main-callback]改为检查 terminal events，不能把 main 的改进归入稳定版。Orca 的全文注入和主管身份修复 PR 在本次快照仍未合并。

证据归档共 166 次 gh 请求，163 成功、3 失败；98 份 raw 源码快照中 93 份通过固定 Git blob 校验。1 份 discovery README 未固定，4 份历史快照与源 blob 不符，均保留原件但排除引用；四份已取得校验通过的替代快照。没有未解决的 archive integrity/source-pin failure 或截断 tree。失败查询、HTTP 404 和查无结果均未被当成产品不存在的证据。

可复核文件：

- [完整证据清单](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/platform-review-20261016-v1/evidence-manifest.json)
- [本报告引用映射](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/platform-review-20261016-v1/report-citations.json)
- [报告验证结果](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/platform-review-20261016-v1/report-validation.json)
- [原任务续跑记录](/Users/lzhs/Projects/multi-agent-collaboration-upgrade/HANDOFF.md)

<!-- evidence-references -->

[U-mac]: https://github.com/lzhs1995/multi-agent-collaboration/blob/29e4899d41d7c4f00f083ce092d20c84416f8e53/SKILL.md
[U-thesis]: https://github.com/lzhs1995/thesis-refiner/blob/18c6cc392df17d5b2219c773aa6cc44e08be3251/SKILL.md
[U-upgrade]: https://github.com/lzhs1995/multi-agent-collaboration-upgrade/blob/a882e51c5afc2f5cd975a50bacb3dff84d210936/README.md#L12-L83
[U-ccc]: https://github.com/lzhs1995/ccc/blob/79cc892e82d1f503ca36ea5e10d3e541dcdd8970/README.md#L98-L145
[U-delivery]: https://github.com/lzhs1995/multi-agent-collaboration/blob/29e4899d41d7c4f00f083ce092d20c84416f8e53/references/verified-compose-delivery.md
[O-readme]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/README.md
[O-meta]: https://github.com/stablyai/orca
[C-meta]: https://github.com/manaflow-ai/cmux
[P-meta]: https://github.com/getpaseo/paseo
[O-license]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/LICENSE
[P-license]: https://github.com/getpaseo/paseo/blob/c1558d29eab78c898743adb613afa01ba4f0e780/LICENSE
[C-readme]: https://github.com/manaflow-ai/cmux/blob/6c23792198ce042b7ba2c018ca4e0b9d797a4235/README.md#L526-L533
[O-guide]: https://github.com/stablyai/orca/blob/f51e82c4c26745f6a15e215e4391f533327697e2/skill-guides/orchestration.md#L46-L71
[C-pricing]: https://github.com/manaflow-ai/cmux/blob/6c23792198ce042b7ba2c018ca4e0b9d797a4235/web/services/billing/plans.ts#L13-L72
[O-timeout]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/src/shared/orchestration-ask-timeout.ts#L1-L14
[O-wake]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/src/main/runtime/orchestration/mailbox-notification-coordinator.ts#L48-L75
[O-wait]: https://github.com/stablyai/orca/blob/f51e82c4c26745f6a15e215e4391f533327697e2/skill-guides/orchestration.md#L123-L145
[O-reuse]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/skill-guides/orchestration/references/coordinator-loop.md#L46-L58
[O-settlement]: https://github.com/stablyai/orca/blob/f51e82c4c26745f6a15e215e4391f533327697e2/skill-guides/orchestration.md#L161-L176
[OI14388]: https://github.com/stablyai/orca/issues/14388
[OI16226]: https://github.com/stablyai/orca/issues/16226
[O-tasks]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/src/main/runtime/orchestration/db/tasks/task-store.ts#L38-L66
[O-rpc]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/src/main/runtime/rpc/methods/orchestration/runs/runs.ts#L28-L55
[O-mailbox]: https://github.com/stablyai/orca/blob/f51e82c4c26745f6a15e215e4391f533327697e2/skill-guides/orchestration.md#L117-L127
[O-structured]: https://github.com/stablyai/orca/blob/5db05e8ed4f41e29786016cd1f236bc0a5fcf88f/src/main/runtime/orchestration/structured-mailbox-pointer-delivery.ts
[OP28567]: https://github.com/stablyai/orca/pull/28567
[P-skill]: https://github.com/getpaseo/paseo/blob/dc5b02f7d7c120baea380d76def8b968020d8823/skills/paseo/SKILL.md#L101-L125
[P-tools]: https://github.com/getpaseo/paseo/blob/dc5b02f7d7c120baea380d76def8b968020d8823/packages/server/src/server/agent/tools/paseo-tools.ts#L1895-L1989
[P-callback]: https://github.com/getpaseo/paseo/blob/dc5b02f7d7c120baea380d76def8b968020d8823/packages/server/src/server/agent/agent-prompt.ts#L475-L519
[P-replace]: https://github.com/getpaseo/paseo/blob/dc5b02f7d7c120baea380d76def8b968020d8823/packages/server/src/server/agent/agent-prompt.ts#L341-L350
[P-cancel]: https://github.com/getpaseo/paseo/blob/dc5b02f7d7c120baea380d76def8b968020d8823/packages/server/src/server/agent/agent-manager.ts#L2668-L2698
[P-compaction]: https://github.com/getpaseo/paseo/blob/dc5b02f7d7c120baea380d76def8b968020d8823/packages/server/src/server/agent/providers/claude/agent.ts#L2355-L2371
[PI6806]: https://github.com/getpaseo/paseo/issues/6806
[P-subscription]: https://github.com/getpaseo/paseo/blob/dc5b02f7d7c120baea380d76def8b968020d8823/packages/server/src/server/agent/agent-prompt.ts#L440-L519
[CP21187]: https://github.com/manaflow-ai/cmux/pull/21187
[CP21149]: https://github.com/manaflow-ai/cmux/pull/21149
[C-messages]: https://github.com/manaflow-ai/cmux/blob/6c23792198ce042b7ba2c018ca4e0b9d797a4235/docs/agent-messages.md#L23-L79
[C-read-state]: https://github.com/manaflow-ai/cmux/blob/6c23792198ce042b7ba2c018ca4e0b9d797a4235/Packages/macOS/CmuxAgentJournal/Sources/CmuxAgentJournal/AgentMessageStore.swift#L255-L280
[CP16342]: https://github.com/manaflow-ai/cmux/pull/16342
[D-org]: https://github.com/meltylabs
[D-repo]: https://github.com/meltylabs/conductor-releases/tree/0095de9b888f2a37413a3a3666638344028d2f46
[D-starter]: https://github.com/meltylabs/Starter-Project/blob/ff5caa01cf4502f9819553a193e4cc7ac4ec40f3/README.md#L1-L19
[DI21]: https://github.com/meltylabs/conductor-releases/issues/21
[DI34]: https://github.com/meltylabs/conductor-releases/issues/34
[DI36]: https://github.com/meltylabs/conductor-releases/issues/36
[U-orchestration]: https://github.com/lzhs1995/multi-agent-collaboration-upgrade/blob/a882e51c5afc2f5cd975a50bacb3dff84d210936/docs/ORCHESTRATION.md
[OI22652]: https://github.com/stablyai/orca/issues/22652
[OI26943]: https://github.com/stablyai/orca/issues/26943
[OI21445]: https://github.com/stablyai/orca/issues/21445
[OP29008]: https://github.com/stablyai/orca/pull/29008
[OP27795]: https://github.com/stablyai/orca/pull/27795
[O-release]: https://github.com/stablyai/orca/releases/tag/v1.4.227
[OI29107]: https://github.com/stablyai/orca/issues/29107
[PP5347]: https://github.com/getpaseo/paseo/pull/5347
[PP5386]: https://github.com/getpaseo/paseo/pull/5386
[PI6346]: https://github.com/getpaseo/paseo/issues/6346
[PI7019]: https://github.com/getpaseo/paseo/issues/7019
[PI5610]: https://github.com/getpaseo/paseo/issues/5610
[PI4231]: https://github.com/getpaseo/paseo/issues/4231
[C-example]: https://github.com/manaflow-ai/cmux/blob/6c23792198ce042b7ba2c018ca4e0b9d797a4235/cmux-tui/bindings/examples/python-dev-orchestrator/orchestrator.py
[D-changelog]: https://github.com/meltylabs/conductor-releases/blob/0095de9b888f2a37413a3a3666638344028d2f46/CHANGELOG.md#L1-L10
[P-main-callback]: https://github.com/getpaseo/paseo/blob/c1558d29eab78c898743adb613afa01ba4f0e780/packages/server/src/server/agent/agent-prompt.ts#L572-L617
