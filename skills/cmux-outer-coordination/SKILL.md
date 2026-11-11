---
name: cmux-outer-coordination
description: 协调现有 cmux workspace 的章节主管和 senior 总主管，支持原任务继续及新协调范围的 mesh/star/hierarchical/ring 路由，保留精确内外终点及原任务 controller。
---

# cmux 外层协调

**Public candidate only.** 本分支仅交付可读工作流；正式业务仍 0/33、配对 0/15，不代表已安装或 live 验收。保留本仓库相对目录读取；不要只复制本 SKILL.md 而丢失共同资源。

先读[共同契约](../../references/nested-research/common-contract.md)，按需参考[四章示例](../../templates/nested-research/four-chapters.json)。本入口为新协调范围登记实际角色、路由和证据；已有任务沿原登记与 controller 继续，不改全局 CURRENT、不自动纳管旧任务，也不启动组、模型或独立调度器。

1. 按用户实际章节数和已指定角色复用原会话；各章专用 cmux workspace 承载其 Herdr 章节 session。章节 supervisor 负责本章，senior supervisor 统筹共享术语、跨章依赖、引用与最终整合。senior 的实际位置单独登记，可位于任一已授权精确终点；独立 cmux 原生角色的 Herdr 字段为 null。四章和 executor/reviewer 组合仅是示例，不据此增建 workspace、会话或角色。
2. 用 `cmux-agent self`、`cmux-agent list` 和本机只读命令查原生 caller/当前 UUID；外层 topology 是目录与路由，不是向 Herdr 外壳注入输入的许可。章节/角色名只作标签；endpoint fingerprint 才是接收身份。
3. 任务选择 mesh、star、hierarchical 或 ring，并列明合法边、步骤/跳数/失败/时间预算。默认示例外层 star、内层 hierarchical；软件不强制拓扑。mesh 是按需主管互通，不广播全文；ring 达到本轮末端即交接，不无限循环；预算耗尽仍未完成则报告未完成。
4. 普通独立 native CLI 终点沿其已 pin 的 cmux delivery controller：保留草稿、pane/target 输入锁、原生 SID/transcript fence；先持久化意图再输入。原 Codex 原生 steer/start 可用时用该受支持通道；只有输入意图之前确认原生通道不可用才可按原合同选择受保护 terminal fallback。身份不符不能降级。
5. 对在 Herdr 内的章主管，路由先确定外层 workspace，再按[共同契约的精确端点规则](../../references/nested-research/common-contract.md#精确端点与外部协调)选择已授权内部角色、当前 CLI 支持的显式 session/server selector 或已认证直达原 SID 的原生 API。外部精确调用需要用户授权覆盖该范围，并保留该任务原 MAC/controller；环境变量或 selector 本身不授予权限。不能向外层 cmux surface paste 后假定收到的是某个内 pane。普通 cmux 同 workspace 路径不因本技能自动获得跨 workspace 权限；只有原 controller 支持且现有用户授权覆盖的专用跨 workspace 路径可用，不支持则记 ROUTE_UNAVAILABLE。不得滥用 successor-rebind 伪装日常 dispatch。
6. 双向插话允许在工作、Goal/压缩期间提出具体新信息；原生 queued/steer/用户 turn 按真实形式记录。每次发送独立 marker 与 dedup key，UNKNOWN 只读核原次。上层已收件不证明内层已收件；转发是独立 hop，保留 parent_message_id 与相同业务 event_id，禁止通知回声。
7. 每 60 秒由活跃 task owner 刷新结构化进度；senior 只聚合真实章节记录。停止通知按事件去重，缺记录或超期标 STALE/UNKNOWN。本技能不唤醒空闲模型，不保证崩溃后的 60 秒检测；需要真正独立调度时另做登记、安装和运行验收。
8. senior 只整合已读、版本明确且本章已验收的产物；遇跨章冲突由相应主管裁决。任务需要审查时，一次基准全面审查、累计最多三轮；不因整合另启重复全面审查。共享主稿仅一个 writer，章任务使用各自输出路径/分支；Word/Zotero 等全局资源共用原租约。

按下一动作涉及的端点、路由与资源核实必需字段，并核对[能力表](../../references/nested-research/capabilities-and-gaps.md)；未涉端点、无关章节的缺项或 UNKNOWN 不阻断已核实的独立动作。章节地址、实际模型与 controller 不从示例推定；旧任务、旧 session、旧 UNKNOWN 和资源租约不会因读取本技能转移给 senior。
