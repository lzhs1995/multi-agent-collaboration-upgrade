# 内外两层协作共同契约

本候选供明确登记的新协调范围使用，可复用原会话；已有任务沿原登记继续，不因读取本契约要求重新登记。用户授权范围优先，原在途任务保留其完整 immutable controller、原次 input intent/marker/fence、callback 和资源票据；文档参考基线、环境变量、技能安装、角色头衔或读到文件均不构成任务接管或实际 controller 绑定。不得重启、替换或清空原会话以修通信。任何真实恢复只用原 SID，在原任务下记录旧进程已结束及新 PID/birth 的独立恢复关系，不改写旧身份。

本文件是两入口唯一共同工作流契约。底层身份、发送、锁及回执格式继续复用原任务 MAC/controller，不创建第二套 sender、身份库或 ACK 协议。普通提问可直接沿原通道传递，不为每条消息新建报告或 fingerprint 文件；实质交接与副作用才按对应原协议核所需字段。

## 角色、拓扑和工作范围

每章 Herdr session 独占一个 cmux workspace；章节 supervisor 管内部 pane，senior supervisor 管跨章整合。新任务登记原上下文参与者、目标、章节/路径写权限、输入版本、owner/controller、deadline、topology、预算及完成条件。章节数量与角色按实际任务指定，不填满所有 pane，不从四章示例推定 reviewer。不同章节可并行；同一共享产物一个 writer，审查只读。senior 拥有整合责任，不自动获得全部章节文件或历史 callback 权限。

| 拓扑 | 合法路由与停止条件 | 典型使用 |
|---|---|---|
| mesh | 登记 peer 之间按需互通；唯一 event/marker，禁止全员回声；受 hop/step/时间预算限制 | 主管交叉核事实 |
| star | hub 派发与汇总；spoke 只沿登记边答复/求助 | 各章向 senior 汇总 |
| hierarchical | senior→章节主管→本章执行/审查；结果沿父关系返回，跨支线需登记边 | 章节分工与全稿整合 |
| ring | 固定 speaking_order，每站接收并处理一次版本化产物；一圈结束或预算耗尽即交主管 | 规格→实现→审查→整合 |

拓扑是每任务的路由约束，不是已安装 scheduler。它不改变用户输入优先权，不赋予 shell 执行、部署或资源占用的新权限。

## 章节交付与上行整合接口

执行者向本章 supervisor 返回版本化产物、输入/来源引用、实际检查结果与科学限制、未完项及下一步；supervisor 读取并按当前论文领域规则核收后，向 senior 提交本章 accepted delta。每个章增量包含 chapter_id、原 task/owner、artifact 路径与 SHA、证据清单、术语/方法依赖、验收范围和未决限制、已有 review baseline/轮次/结论、共享资源状态。senior 核跨章定义与边界并整合，沿用已审证据与原轮次，不因上行再开一次全面审查。

领域规则使用实际任务 pin 的 thesis-refiner，科学、Word/PDF/NotebookLM 门槛按本次产物是否适用；非 Word 的定义/证据包无需先完成整篇论文桌面验收。传输成功不证明论文正确。[四章模板](../../templates/nested-research/four-chapters.json)提供空接口；填表不是入组、启动或接管。

## 规则一：fingerprint 交接

实质交接复用原登记与证据，只补本次相关动作必需、尚缺的字段；涉及的终点字段可包括：机器标识；cmux workspace/pane/surface UUID；Herdr server selector/socket、server PID/birth/executable 与内 workspace/tab/pane/terminal；native agent kind、原 SID、PID/birth/PGID/TTY/executable；原 transcript 路径/device/inode、边界字节数与 SHA；采集时间和证据路径。非 Herdr 原生终点将内层明确记 null，此值合法，不填假值。refs 可导航，UUID/原 SID 才作绑定；数字 pane 名在不同 server 重复，不可全局寻址。

把完整 UTF-8 JSON 以 sorted keys、紧凑 separators、ensure_ascii=false 编码并 SHA256，记录算法及 fingerprint；接棒者阅读全文、核输入与产物 SHA、原授权及后续指令，再核当前现场身份。hash 只证明字节，不能取代原生核验。不得由 focus/title/newest mtime 猜目标。重启后 live process fingerprint 必然变化：保留旧 record，用原 SID 与历史边界证明有授权的恢复，再写新 binding；不能静默覆盖 PID/birth 或继承原 UNKNOWN 的发送预算。

交接包列已完成、未完成、拒绝/分歧、最后安全 checkpoint、精确文件/函数、下一可执行动作、消息待核收项与租约。继任者沿 pin controller；维护联络不转移旧写权或 callback。实际接手的交接包和被引用正文必须完整读，不能把 SHA 对上称为已理解；无关历史文档无需全量重读。

## 规则二：60 秒进度与停止通知

只对显式登记此契约的新任务启用 cadence。执行者/章节主管在持续工作的实际调度机会每 60 秒内更新一次 progress record：task/actor、observed_at、state、当前动作、已完成增量、待处理收件、阻断、下一步、artifact hashes、lease 状态。记录进度文件和 final 输出分开，不能覆写已冻结 review/final。主管给用户/上级简短状态；无业务变化也明确“仍等待什么”，不伪造百分比或 ETA。

这是一项 agent 执行责任，不是后台 timer 承诺。短读/等待每段≤30秒，正常工具工作避免超过60秒不回报；不可避免的长调用预告并在返回后记实际 gap。模型退出、阻塞工具、电脑断电期间没有保证本技能继续运行；last_update 超期标 STALE，未证明死亡就 UNKNOWN。旧 file scheduler 只处理其原登记任务，不自动覆盖本架构，也不唤醒模型。要真正无人值守刷新，需单独实现/登记并验证调度器的实际调用，本包未做。

状态进入 WAITING_INPUT、BLOCKED、FAILED、STOPPED、DELIVERABLE_READY 或 COMPLETE 时立即落盘对应 event。计划停止/正常 turn end 前写 STOP/WAIT 通知一次；异常退出由原主管在真实观察后补记 observed-stop/UNKNOWN，不能伪造对端自报。dedup key=(task_id, actor_id, episode_id, transition_seq, event_type)，各 hop 再加 recipient；同事件不因60秒计时重复发送。停止通知写明原因、未完项、原SID、租约、待核收消息和下一恢复动作。turn stop、process exit、operator cancel、业务完成是不同事件；idle/Done badge 不等于任务完成。STOPPED 不释放资源，不强制续轮，不杀进程。

### 执行者主动求派与求问

在明确分派且总任务仍有效的协作范围内，执行者交付或冻结后可承接下一项、没有分派工作，或遇到需要主管决定的具体问题时，主动向登记主管发一次具体请求：已交结果、未完项、可承接的有界下一项或具体问题。有相关交付通知就合并发送；正式产物沿原版本／SHA，普通问题不强制建报告文件。可独立推进的已授权工作继续。

请求投递未知时只核原次、不重发；仅收到回执不等于问题已答。主管实质答复（包括“暂无新任务”）结束本次请求，无需再 ACK。总任务完成、取消或停止后，该范围记为空闲已收口，不持续求派、不自行新增工作；只有相关新任务／指令、原授权内未解决依赖或实质新问题，或仍活跃总任务明确授权的节奏才能再问。时钟、一般里程碑及无关状态变化不触发重问。

同一状态的非紧急重复提醒，仅在已授权时且间隔至少 60 秒；满 60 秒本身不授予重问权限。新阻塞、撤回授权或即时写入风险立即报告，原 UNKNOWN 动作另行保留。这是 Herdr 向内与 cmux 向外的共同责任；旧 reask runner 仍沿原登记单独授权。安装、读取、SessionStart／Stop 提示不入组、不启计时器、不发消息、不阻止回合结束；其他独立任务、hook 与计划不因本条被修改。

## 规则三：双向插话与原生可见

双方 prompt 就绪即可在工作/Goal/compaction 期间发送；不等 idle，不暂停用户任务。保留用户草稿和审批对话框；同一真实输入终端使用原 controller 的输入锁，独立 pane 可以并行。先核实际接收身份、输入状态和 endpoint 能力；跨章只定位外层 workspace，实际输入落到精确内 pane 或原生 SID，禁止给 Herdr 外壳注入文本。

分别记录 INTENT、HOST_ACCEPTED/SUBMITTED、QUEUED、NATIVE_VISIBLE、BODY_READ、TASK_ACCEPTED、ARTIFACT_READY、SUPERVISOR_ACCEPTED、RESOURCE_RELEASED。cmux 原 native-user-marker 合同只接受原 transcript 新 user marker；Herdr 中 queued-command/system reminder、steer 附着和独立 user turn 据实分类。队列不等于收件，收件不等于正文已读，已读不等于接单。短通知引向 SHA 正文时必须留完整正文读取证据。不得再发一次来人为制造期望格式，也无 ACK-of-ACK。

文件 mailbox 可作明确备用；真实原生回执不可得就记 UNCONFIRMED，文件出现不冒充原生可见。对方实质回复可以另证已读/结果，不抹掉原通知未证实状态。接收/接受需要任务匹配，不用旧回合 settled 状态代替。所有旁路/转发都绑定原 endpoint 与事件，不能绕过 input UNKNOWN。

## 规则四：一次全面审查，累计最多三轮

任务需要审查时，冻结 baseline 产物与输入集合/hash、审查范围、reviewer、期限、预算；单纯启动协作或传递规格不因此新增审查角色。R1 对该基准做唯一一次全面独立审查；作者对 findings 逐项 accept/reject/defer 并附证据。有阻断才允许 R2、R3：只复核对应修复及受影响接口/测试，不重审已接受且未变化的材料。总轮数≤3，包含 R1；不把初审后另加三轮、不为 reviewer 换名/换版重置计数。

每个 finding 带 ID、严重性、确切位置、输入版本、支持证据、处置人和状态。没有真实风险的低影响改字不自动加审查。第3轮仍未解决交该层主管裁决：修复、缩小范围、保留分歧/阻断或在已有权限内接受已披露限制；只有需要用户决策的取舍才问用户，不阻其他独立工作。达到轮数上限不变 PASS。若目标/范围实质变更，开关联新任务和新 baseline，保留旧轮数及原因，禁止借重命名规避上限。任务完成必须满足原验收条件、实际产物已读/集成及独立资源释放状态；审查通过不自动授权 merge/deploy。

## 规则五：意图先落盘，UNKNOWN 只读核回

每个可能输入/变更动作之前，原 controller create-only 持久化 task/attempt/event/marker、完整正文与hash、sender/receiver fingerprints、原 transcript EOF fence、授权及操作预算；必要 fsync 后才调用主机。实际返回、queued/submitted、结果和后续原生证据各自落盘。发送者单一，不能由两层主管抢写相同 attempt。

超时、断连、进程切换、未解析结果或写入后确认失败均保存 UNKNOWN。沿原 controller 对原 endpoint、nonce、payload 和 fence 只读 reconciliation；不能重贴、盲 Enter、换通道重发、换新版 controller、删 journal 或以新 marker 绕过旧次。已证实零输入也不自动重置旧预算；新动作须由既有合同与原证据明确允许。独立新信息可以另建新事件，但不能是未知业务动作的伪装重复。

single-writer 与 resource lease 独立于通信：shared manuscript/branch/db/Word/Zotero/account 记录 resource ID、owner task+SID、原 controller/ticket、generation、范围与状态。超时/lease TTL 只触发核查，不证明旧 writer 停止；UNKNOWN/PENDING 仍隔离，释放须原协议真实证据。禁止双重写入、抢占活跃桌面应用、擅杀进程；禁止任何 VPS 构建，构建只在本机或 GitHub Actions。保持原授权边界，不引入无关批准环节。

[能力与缺口](capabilities-and-gaps.md)区分现有命令、技能人工执行责任和未实现 runtime；[来源](sources.json)保存文档参考基线，实际 controller 从原任务登记与证据核定。按下一动作局部核验：输入核发送/接收端点、授权、原 controller、路由、正文、锁和 fence；写产物另核对应写权与租约。缺项只限制相关动作，无关章节缺项或旧 UNKNOWN 不阻断已绑定且不依赖它们的新事件；仍不得重发或绕过原 UNKNOWN。示例的未用角色与不适用字段不必补齐，不猜章节与 live endpoint 映射。
