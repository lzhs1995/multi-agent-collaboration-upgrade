---
name: herdr-inner-collaboration
description: 协调同一章节 Herdr 内的原有 agent pane，支持原任务继续和新协调范围登记；保持会话身份、双向插话、证据与写入归属。
---

# Herdr 内层协作

**Public candidate only.** 本分支仅交付可读工作流；正式业务仍 0/33、配对 0/15，不代表已安装或 live 验收。保留本仓库相对目录读取；不要只复制本 SKILL.md 而丢失共同资源。

先读[共同契约](../../references/nested-research/common-contract.md)；新协调范围可参考[四章示例](../../templates/nested-research/four-chapters.json)，按实际章节与已指定角色登记，示例不要求四章或固定 reviewer。已有任务沿原登记、controller、待核收消息及资源票据继续；新登记不要求新会话，安装本入口不接管旧任务。当前候选仅定义工作协议，未安装调度器、hook 或新 agent。

1. 章节主管负责范围、分工、接收、审查和本章验收；执行者只写获分配产物；审查者独立读取基准产物。角色按任务指定，不按 Claude/Codex/Grok/AGY 品牌推定。复用原有上下文会话，通信失败不清空或替换它。
2. 在 Herdr 内确认 HERDR_ENV=1，读本机 `herdr --skill`、`herdr --help`；通过当前 CLI 支持的显式 session/server selector 查询 status、agent/pane。控制语法以本机版本为准，不运行裸 herdr 作探测。当前官方 Herdr 指引要求调用者处于 HERDR_ENV=1 的 Herdr 环境；外部调用者仅可读离线文档，不调用 status/prompt/control，也不以显式 selector、环境变量伪造或 MAC profile 文字绕过此边界。需要跨界操作时，将明确工作交给已绑定、已获授权且位于该环境内的原角色。
3. 登记外层 cmux workspace/pane/surface UUID 与内层 server/socket、workspace/tab/pane/terminal、原 native SID、PID/birth/PGID/TTY；按共同契约核 fingerprint。`w1:p1`、名称、标题、环境变量和 idle 状态均不能单独认定身份。移动/重启后重新核现场绑定，保留旧绑定和恢复证据。
4. 同章工作通过官方 Herdr `agent prompt` 到已核实的内 pane。双方工作、Goal 或压缩期间可以插话，由客户端表达 steer/queue/独立 turn；禁止以“等 idle”作为通用门槛。实际 blocked 对话框、用户草稿或接收身份不明时保持原输入并记待处理；已有授权可回答的事项沿已有授权办理，不新增通用批准流程。
5. 每次输入先落意图和完整 payload/hash/marker，取得该内终端输入互斥，再复核身份与实际输入状态。不同内 pane 可并行；不可因共用外层 surface 把全章锁成单线程。查询与发送不是事务；失败或 UNKNOWN 只核原次，不补 Enter、不换通道重发。
6. 有界读取/等待单次不超过 30 秒并在任务期限内。Herdr host accepted、队列、原生可见消息、正文已读、产物和业务接受分别留证；idle/done 不能替代本任务完成。执行者按共同契约每 60 秒更新进度，状态变化立即报告；停止前落停止通知，异常退出由主管观察补记。
7. 任务需要审查时，同一基准产物 R1 全面审一次；有阻断才做最多 R2/R3 定点复核，三轮含初审。收到结果后本章主管核读、解决或保留分歧，再向总主管提交版本化章节增量。跨章协调读[外层入口](../cmux-outer-coordination/SKILL.md)，不能将消息粘贴到承载 Herdr 的外壳。

文档参考基线和现有能力边界见[来源](../../references/nested-research/sources.json)与[能力表](../../references/nested-research/capabilities-and-gaps.md)；参考基线不证明实际任务 controller，后者从原登记与证据核定。论文领域规则另行 pin，不由通信技能替代。Word/Zotero/共享数据库等资源沿原租约串行，不以任务超时、进程 idle 或协作停止自动释放。

角色胶囊、pills 或缓存只用于导航；本公开候选不包含 CCC 角色登记实现，也不证明任一机器已安装、入组、加载或接受任务。见[能力表](../../references/nested-research/capabilities-and-gaps.md)。
