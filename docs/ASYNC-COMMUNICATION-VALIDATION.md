# 运行中双向通信：规则、实证与剩余验证

2026-10-18，独立 upgrade 候选。共享 multi-agent-collaboration 与论文精炼助手
由用户指定的 surface40 统一检验、优化和合并，本仓不修改它们的安装或发布。
以下规则澄清已写入本仓；新增运行中双向压力矩阵尚未执行。

## 结论与操作规则

普通工作进行中不应成为协作信息的禁发理由。Codex 与 Claude 可以在各自工作时
交换进度、结果、纠错和需求；收到消息的 agent 决定怎样调整任务，不要求两侧
进度一致。不得仅为形成空闲窗口而暂停原生 `/goal`。

需要分别处理四件事：通信是否可送达、接收方是否接受任务、谁能写共享产物、
能否清理输入框。允许及时通信不自动转移写权限，也不授权删除真实草稿。
用户的新指示仍按指令优先级处理；上述分离不是拒绝用户改变当前任务的理由。

| 场景 | 本仓规则 |
| --- | --- |
| Codex 正在工作 | 绑定原生 thread/control socket，使用一次 `turn/steer` 和当前 expectedTurnId；idle 用一次 `turn/start` |
| Claude 正在工作 | 用正式 guarded bridge，核原身份、空输入框及完整稳定的自有草稿，再一次 Enter；客户端可能排队 |
| 有效握手后的状态、反馈、callback | 复用原绑定并做入口要求的新鲜检查，不为每条消息重新写入和清理 bridge-test token |
| 初次握手探针因 active/queued 拒绝 | 记录握手未验证；不停止 `/goal`，不跳过探针伪称通过。只读替代入口仍待实现和验证 |
| 真实草稿、折叠/未知结构、compact/reconnect | 保留原输入和任务，按实际入口零输入拒绝；分类原因，不笼统写成对方忙 |
| 身份变化、原生 socket 缺失、turn race | 保留原次拒绝；不换接收人、补造绑定或退回裸终端 |
| 已有输入但未确认 | 只观察和核收原次；继续独立工作，不重复发送或全局锁住普通工具和 Stop |

记录 `CLIENT_QUEUED`（原 producer 可能记 `NATIVE_QUEUED`）、`NATIVE_RECEIVED`、
`BODY_READ`、`ACTION_DONE`、`BUSINESS_ACCEPTED` 的实际证据和时间，不能相互替代。
这些是报告维度，不是对旧 journal schema 的迁移。

## r20 原次实证

原 r20 已于 14:51:20Z 因首轮超过 1800 秒停止。结果是 **1 TIMEOUT＋32 MISSING**，
业务通过 **0/33**、pilot **0/3**、pairs **0/15**。七组停止新输入的标记均存在，
runner、driver、watchdog 已返回；这不证明远端请求取消或旧会话、lease 释放。

| 条件 | 已观察结果 | 仍不能证明 |
| --- | --- | --- |
| Claude A → active Codex 的 inventory callback | 一次原生 `turn/steer`，完整新 native user 和正式回执均通过独立校验 | callback 业务结算或整轮通过 |
| Codex → Claude A 的 inventory task pack | 完整原生通知已接收，原次无重发 | 投递时 Claude busy 或 `/goal` 连续运行 |
| Codex → Claude B 的 source_index | dispatch 调用在输入前的 live identity 采集中失败；无发送 intent | 不能归因 busy、key 或 Claude B 失效 |
| 双向同时发送、持续 `/goal`、跨轮公平性 | 本次缺少完整证据 | 不能宣称全量压力通过 |

source_index 的原异常时间为 14:28:38.516Z：
`dispatch → co._gate → collect_live_sessions → bind_target → inventory_for_tty
→ _discovery_pair`，PID 88823 在 `before_path_bsd_1` 返回成员退出。
稍后的外部快照显示该 PID 为 Claude A 的僵尸子进程，但不能补认失败时的
出生身份或角色。此失败需要独立修复，不能通过删除 busy 检查解决。

成功 callback 的 input intent 为 14:31:05.218424Z，新增 native user 为
14:31:31.123Z，正式回执为 14:35:28.484343Z。intent → native 约 **25.905 秒**，
native → 回执约 **237.361 秒**。后者是观察/核收延迟，不全算成传输延迟；
原生入站时间也不代表报告已被阅读或业务已完成。

实际冻结控制器的 `require_agent_input` 只要求 AGENT_TUI；active/queued
拒绝主要在清理输入框、写/清 token 探针及历史补键路径。Claude 正式新消息
入口没有一律 busy 禁发。应修正错误调用和规则泛化，不删除这些动作各自的保护。

原证据根 E：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。

- `maintenance/r20-delivery/terminal-readout.json`：原 runner、配置、33 槽、15 对及七组 stop 的复核。
- `maintenance/r20-dispatch-audit/inventory-callback-native-proof.json` 与 `inventory-formal-receipt-proof.json`：原 intent、EOF fence、完整 payload、原生记录及正式回执。
- `maintenance/r20-dispatch-audit/source-index-preinput-error.json`：原派发前异常及原生切片 pin。
- `maintenance/r20-delivery/ASYNC-COMMUNICATION-REVIEW.md`：只读独立审查；其 source_index 未归因条目由上述已核验原异常补充，不改原报告字节。
- `maintenance/r20-delivery/ASYNC-COMMUNICATION-TERMINAL-ADDENDUM.md`：独立核验原错误切片、终态及定量压力建议，保持首份审查的原 SHA。

## 剩余验证及有限执行顺序

本次收尾不自动启动 r21、不重放 r20，也不延长原截止。现有全面授权保持有效，
后续不得重复索要 key 或常规执行授权。以下为未完成工作，不是已执行压力结果。

1. **先修实际阻断。** 在独立候选复现 TTY 成员退出及 PID/出生身份变化；
   明确什么时候可以排除已证实无关进程，什么时候必须零输入拒绝。保持内核、
   UUID、TTY、原生 session 和 transcript 认证，禁止通过忽略未知 PID 或 ESRCH
   获得通过。复现不能证明旧失败时的身份。记录完整 lookup 拒绝率和成功工作量。
2. **拆分握手动作。** 追踪初次与复用握手是否调用写/清 token 探针；为持续
   `/goal` 的初次握手研究只读能力检查加真实一次挑战/回应。它必须保持现有
   身份和原生接收证据，需通过真实入口验证后才能替换探针。当前仅澄清复用规则。
3. **本机全边界并发压力。** 使用实际 materialized controller，覆盖 active、
   idle、queued、草稿、compact/reconnect、turn race、PID/TTY/session 漂移、
   并发 callback、延迟核收与 deadline。记录所有正例成功、预期拒绝、意外拒绝、
   锁等待和失败分母；保持原次唯一性。模拟传输和真实 OS 进程压力分别计数，
   不用它们代替模型压力，也不重复无关历史全测。
4. **真实三客户端压力。** 冻结候选及完整配置后，先在同 1 Codex＋2 Claude
   中验证三轮 pilot，再按原 12 warm＋3 cold 配对框架执行。覆盖四个方向
   C→A、C→B、A→C、B→C 的运行中消息，包含双 Claude 同时回调、错峰续派、
   fan-in、共享写冲突、排队后的实际消费及连续 `/goal`。准备时固定每方向
   样本数、字节规模、到达节奏及原生状态证据要求；不能执行后挑选成功样本。
   原实验的失败保留，新矩阵不得事后填入 r20 的未执行槽。
5. **固定截止与验收。** 下一候选准备前须固定有限本机验证预算，配置时固定
   不超过两小时的全局 live 截止；启动或首轮失败即收尾，不自动替换/重放，
   并记录所有未执行项。验证成功时间现在不能保证。及时报告失败优先于消耗
   剩余时钟反复观察同一拒绝。
6. **对照与统一反馈。** 两组使用相同任务、输入、状态采样、客户端约束与
   总预算；除待比较策略外，控制开销修复同时作用两组。记录消息就绪、
   input intent、客户端排队、native、正文读取、动作、回执和业务验收时间，
   分解 p50/p95/最大等待、总工期、成功吞吐、公平性、重复动作和返工。
   报告模型调用、token 及成本，缺失记 UNKNOWN；成功样本延迟与全分母成功率
   并列。正确性通过后 warm 配对中位改善至少 15%，各 workload 中位退化
   不超过 5%，成本增长超过 10% 必须披露。以证据向 surface40 反馈，共享
   MAC/论文精炼助手的合并由该维护者统一处理；upgrade 候选由本方按实证优化。

定量矩阵建议已由独立审查列出：8 类负例各 1000 次，另做稳定 TTY、短命
辅助进程、选定身份退出/变化各 1000 次真实采样；两实验臂各 96 条独立通信，
共 192 条，包含四方向 active/idle 样本和 12 个互发波次。未击中的 race 不算
对应边界通过，正式任务/callback 不重复充数。建议记录 ready→原次门禁是否
超过 30 秒、原次 native 观察最多 300 秒，以及 native→机器回执是否超过 10 秒。
这些是**待实现及预算核定的规模与目标，当前完成数为 0**；必须先检验与
实际业务耗时及固定两小时预算的相容性，不能擅自增加会话或延长在途时钟。

## surface40 交流状态

更正后的原生维护联络因目标控制 socket 不存在而在输入前结束：NO_INPUT、
input_operations=0。没有重发或终端回退。完整请求及报告已发布到其既有
maintenance inbox：`upgrade42-async-policy-corrected-20261018-r20.json`。
截至本次收尾，约定 `maintenance/r20-async-corrected/REPLY.md` 不存在；
文件可用，不代表自动 hook 曝光、对方已读或双向咨询完成。

本次文档更新没有改可执行源码、冻结 v18、运行中控制器或共享技能。此前
138/138 回归与 8 OS worker / 80 callback 压力仍是对应源码的本机证据，
不能冒充本页新增 live 矩阵已通过。整体提效目标仍未完成。
