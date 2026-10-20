# 三种可用状态下的任务接续与 upgrade 接入

2026-10-20。目标保持不变：减少 Codex 主管与双 Claude 的握手、派发和 callback 等待，在部分服务不可用时继续既定工作。cmux 承载原生会话；共享 MAC 负责通信与主动求派；upgrade 的本地图调度负责依赖、就绪工作、证据核收和提效度量。JEV/TypeSafe 仍为 rules/offline_mock，不增加第二套模型调用或进程管理器。

## 已核对的共享版本与维护范围

- 已读 MAC 0.4.56 的 `SKILL.md`、`references/task-continuity.md` 和 `scripts/collaboration_continuity.py`。随后核对 0.4.57：task-continuity 正文与 0.4.56 相同，新增自动 hook 审计回执修复。
- 当前此次核对的完整版本为 `/Users/lzhs/.local/share/multi-agent-collaboration/releases/0.4.57-e8685485a9c5/source`；SKILL SHA256 为 `d8c5cee987e9cdfc9dc182beb0c9449274b9121c36d931fea87d098cf0105bf2`。这是源码/安装观测，不能证明旧客户端已更新。
- surface40 / `A93448E1-E329-493D-9B45-568D5FC0DAD0` 维护共享 MAC、thesis-refiner、hook 和发布。surface43 / `7063B683-2147-4850-BF71-AF48C937BD37` 是用户原话“正在做upgrade”的参与者及已接受的独立只读审者，communication_only；这不授予唯一整合、merge或共享写权限。原 DFB70F77 的 callback 候选已冻结；当前 successor `28407F78-6591-4FAB-9712-D1DACC325F22` 仅在自己的独立 worktree 实施后续修复，不继承旧nonce/租约、不改共享配置。
- 两份撤回的 DFB 直发提案不恢复。修正版候选按新marker协调原surface43只读审查，若需共享写入或正式整合须有原授权及原owner核验；A/B/C/D 新来件逐项回件已发布，重复 B 通知只消费一次。

## 接续矩阵

| 状态 | 继续执行什么 | 必须保留什么 | 尚需证明的实际行为 |
|---|---|---|---|
| Codex + 双 Claude 可用 | 独立输出并行；准备就绪即派发；新 callback 优先登记、核收；Claude 主动求审和求派 | 原任务与身份、输入 SHA、写域、原 nonce；收件、阅读、接受分别记录 | 忙碌/Goal/压缩期间双向原生收件；两轮 request→reply→consume→rearm；完整业务和配对结果 |
| Codex + 单 Claude 可用 | 健康 Claude 继续原任务；Codex 接手已确定无人并发写入的缺失角色 | 故障原会话、在途未知输入、原成果和租约 | 实际故障分类、接手边界、健康通道不中断；自审标记 |
| 仅 Codex 可用 | 授权下从最后安全检查点 SOLO 推进，独立研究/修复/验证不停 | 旧任务不重放；旧执行器写权冻结或有明确交接；solo_self_review | 有证据的有限重试及接手、恢复原会话后增量独审 |
| Codex 不可用、双 Claude 可用 | 同任务的原 Claude 互认身份，先约定 CLAIM 和整合者，再并行完成原计划、交叉审查 | 各自输出和版本、共享写者、未结算资源；不冒充 Codex 验收 | 原生 peer 往返、交叉审查结果、父 agent 确认消费子任务结果、Codex 恢复后的核收 |
| Codex 不可用、仅单 Claude 可用 | 继续授权范围内独立工作，发布可复核检查点 | 同上；不把单方自审称独审 | 自审标记、原会话恢复交接 |
| 可用性未知 | 继续独立工作，核查原始结果 | 队列、无回复、工具成功不冒充 API 故障或成功 | 原生/API 结果与具体原次绑定 |

共享 0.4.56/57 的 evaluator 接受同任务、同 workspace 的 1+2 身份、固定授权和原结果证据。三个间隔至少 60 秒（或更长 Retry-After）的不同终态可重试 API 失败形成不可用判断；真正成功重置失败计数。401/402/403 单列诊断，不盲重试。该 evaluator 只输出决策建议，不验证自报标签的真实性、不执行角色转移、不解除资源。upgrade 必须继续检查证据和写权，不能将输出 mode 当作接管成功。

## 必须接入并验证的细节

1. **通信。** 新版本允许随时直发；不等待 turn 同步或暂停 Goal。仅原接收日志围栏后的含唯一 marker 的真实 user 记录证明收到。生成的压缩摘要、transcript-only 记录、queued_command、界面队列和 Enter 成功均不能证明收到。已有不确定输入只观察原次，不重贴。
2. **主动性。** 新授权 episode 登记 `--continuing --reservation-inquiries --proactive-hook`。Stop 要求原 Claude 继续有用工作或运行原 60 秒询问器，直到原回复全文读完并按 SHA consume。ANSWERED 不是已消费。重复 Stop 不能形成无工具的 API 紧循环；文件调度器只作保底，不能据此声称唤醒了模型。显式停止、撤权和任务完成结束该义务。
3. **自动采用。** 分别留证安装、实际进程版本、用户配置与 `--settings` 启动配置、自动 hook 执行、原生收到、正文读取、业务接受。0.4.57 修复审计结果漏记；老进程固定的启动配置可能仍执行旧 hook。不能只看 CURRENT 或磁盘配置。
4. **调度。** 前序运行中预组下一包；新 callback 优先核收；已有审查不饿死独立就绪通道。原业务失败后的短收取窗口只适用于注册实验，不是生产三态切换器。
5. **Claude 同行工作。** 先冻结输入和度量定义，CLAIM 载明写域、输出和整合者；至少一个独立审查用合适的不同阅读视角。同一整体任务同时最多两个子 agent，记录父子 ID、范围、结果 marker 和父端读取消费，不能各自各开两个。现有 evaluator 的数字 2 不等于跨进程预算已受约束，须验证实际共享分配机制。
6. **Git 与发布。** 每个写域一个作者；独立分支/候选目录，冻结被审提交与 SHA。整合后再执行相关测试。报告给 surface40 集中共享发布；upgrade候选与原surface43协调只读审查，正式整合另需明确写域／授权，不能由参与身份推定；不复制两套 runtime，不直接修改不可变版本。
7. **度量。** ACK→intent、native callback→continuation→EVIDENCE_READY→ACCEPT 分段；缺失保持 null。求派数、原生收到数、回复数、全文消费数分别计量；117:1 记录不能称送达率。保留版本、服务重试、介入、启动/冷暖和所有失败样本。

## 实施顺序与验收证据

1. 固定本 callback 候选，完成完整回归及独立审查，按新marker交原surface43只读核查及surface40集中共享协调；不改 R33 控制器或重放，不把收到候选等同merge授权。
2. 固定共享完整版本，验证 private materializer 对其源码的兼容性；保留严格的 source-shape 检查。若不匹配，应修具体补丁和夹具，不能关闭隔离或改用生产状态目录。三态 evaluator、主动询问及审计以该完整 MAC 版本为来源。
3. 在新的独立验证配置中登记主动询问和三态范围。先验证三个实际客户端、双 Claude 握手、双向收件/阅读、两轮主动求派闭环，再验证故障期间健康成员继续工作、子 agent 父端结果及恢复交接。
4. 故障注入和三态接续是独立实验轨，保留 33 业务轮/15 对/192 通信槽原比较合同。主比较的 Codex API 故障仍结束该原次，但本机修复和其他授权业务继续。不得把 SOLO 或 Claude 同行结果填进失败的 1+2 配对槽。
5. 注册配对全部具有正确性、原生回执、ACK、callback、报告哈希、收口和验收证据后，才能按原门槛判断至少 15% 提效。单位测试、单段变短和同行报告均不替代这个结论。

当前缺口：下述完整0.4.58兼容已验证，原0.4.57的首次拒绝记录不改；原reask consumer/advisory私有根及private CURRENT登记已完成具体实现和本机2340测、四矩阵CI，独立审查仍PARTIAL。consumer挖除测试的R3 P3已由原43的R4实跑及六种干扰反例CLOSED；余同名编译出处伪造为INFO，不升格为未修P3。三态真实接续、跨 Claude 两子 agent 预算、自动主动求派闭环和完整配对仍未完成。R33 的 Codex 配额失败及所有 STOP 均已保留。上述要求不能因本候选测试通过而标为完成。

2026-10-21 原生三态接入：新增独立只读[原生连续性观察入口](NATIVE-CONTINUITY-OBSERVATION.md)，由已采用原1+2会话的真实记录/turn/message/父链/固定fence推导终态，再在完整生成控制器的实际内核边界内调用原MAC evaluator。引用、队列、工具错误、内部重试、字符串HTTP状态和不完整窗口不冒充API故障。晚追加不改变原已固定窗口；下一次观察包含追加；进行中登记用原前序建立解析状态、不复用旧成功。原用户授权、私有runtime/adoption/root/Python/source均固定；无输入、第二SDK、角色转移或资源释放。该建议不代表whole-task子预算已受约束，也不替代两轮实机hook及配对验收；具体测试/冻结结果另行保存，不能继承438提交的full绿色。

2026-10-21 接入进展：在独立 `upgrade/mac058-20261021` 分支完成当前完整 MAC 0.4.58 的版本化私有适配。227 个上游 Python 文件内存转换/编译通过；16 项实际完整生成控制器验证通过，另 15 项可移植行为/反例通过；与旧控制器及调度反例合计 94 项通过。c6全量原14errors留存，夹具修复96全量2327项及四矩阵CI通过，见 [MAC 0.4.58 验证](MAC058-PRIVATE-VALIDATION.md)。这只关闭当前版本的具体兼容／夹具缺口，尚未证明三态角色接管、原客户端自动 hook、两轮求派闭环、共享两子 agent 分配和完整配对。共享发布/三态方向仍归原 surface40 集中；upgrade冻结候选交原surface43按communication_only独立只读审，不改旧 R33 或不可变共享版本，不推出唯一整合权。

首次对 0.4.57 全部 226 个顶层 Python 脚本执行了内存转换检查（不执行输出、不生成 runtime、不启客户端）：220 个转换成功，6 个拒绝。具体为 `cmux_bridge.py` 的旧 footer/activity 片段、`cmux_callback_journal.py` 的 deliver 签名、`cmux_daemon_identity.py` 的无 daemon 分支、`cmux_native_delivery.py` 的 ps 参数、`mac_harness.py` 的旧 preflight bridge-test 调用、`reconcile_handshake_ack.py` 的旧 bridge_clear 条件。部分旧补丁对应的行为已被上游改变，需核对语义后适配；不能删除 source-shape 检查或把转换成功当运行隔离成功。

原观察：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r33-closeout-20261020/mac057-transform-observation.json`，SHA256 `316f20308c8ecc59c2e6daf3e9f729afa21ce2b4ea867b3c5b0d9242043fec9f`。它只证明单文件转换结果，不证明完整 materialization、跨模块导入、自动 hook 或运行语义。
