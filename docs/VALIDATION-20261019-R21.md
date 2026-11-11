# r21 终态与 UUID 握手修复

本报告记录 2026-10-19 台北时间的阶段交付。**真实协作效率尚未证明；PR 保持 Draft。**
1 Codex＋2 Claude 已真实启动并全部完成 ADOPTED，首轮在握手身份检查中失败。
本机修复与压力验证完成不改变原实验结果，不代表全量双 Claude 业务验收。

## 原实验结果

| 指标 | 终态 |
| --- | --- |
| 注册规模 | 7 组、33 槽、3 轮 pilot、15 对比较 |
| 完整业务 | **0/33**；1 FAILED＋32 MISSING |
| Pilot / 配对 | **0/3 / 0/15** |
| 注册普通消息 | 192；attempt 全部 MISSING，native/body_read 全部 UNKNOWN |
| 效率 / 成本 | INSUFFICIENT_EVIDENCE / UNKNOWN |
| Runner / driver / watchdog | 全部 exit 2；watchdog_failed=false |
| 七组停止状态 | 全部 STOPPED_INPUT；不证明远端取消、进程退出或 lease 释放 |

三客户端于 01:14:34 ADOPTED，原 Codex 收取 BOOTSTRAP_READY，
01:17:00.131 建立 pilot-01；01:21:43 原 driver 停止调度，01:21:46 最终报告生成。
原整体截止 03:03:55 未被延长。33 个槽和失败保留，不删除分母或把修复后结果填入旧实验。

终态通信观察器实际读取 native bytes 为 0；缺 attempt 无法区分尚未调用与输入前拒绝。
该 collector 尚未实现正文读取证明。单行通知的原生接收也不能代替 BODY_READ。
before/after 采样标签不能证明接收端 active、idle 或连续 `/goal` 状态。

## 故障与补丁

原 inventory preflight 报
`READONLY_HANDSHAKE_CAPABILITY_REFUSED: ACK_ORIGINAL_GATE_ALIAS_AMBIGUOUS`。
真实 `member.surface_ref` 已是小写 UUID，与 canonical `surface_uuid` 相同；
旧实现注册两遍后误判冲突。随后原 proof 内的 UI `target_surface_ref` 与 member UUID
字符串不等还会触发第二处误拒绝。旧测试仅使用 UI selector，漏掉真实 producer 形状。

`upgrade_handshake_identity.py` 现在接受数字 `surface:N` 或 UUID selector，
允许同一 canonical 成员的自别名。UI alias 仅来自原 gate、原 selector 键下的 proof，
且 proof 的 target UUID 必须匹配。跨成员冲突、错误 UUID、缺原 proof、旧 UI ref 漂移，
以及选定成员的 workspace/caller/pane/provider/session/order 漂移仍拒绝。
所有 member 都须具有原 proof；没有使用当前 inventory 补造历史映射。

修复只进入当前独立 upgrade 候选。旧 r21 的 v19 export、原 controller、receipt、
会话及失败文件保持原样。新能力检查仍只读，实际 challenge、nonce、ACK 和完整 native
receipt 保持原单次契约；不会为握手暂停 Claude `/goal`。

另外，01:24 的原日志有界观察识别 Claude A 25 条、B 15 条 HTTP 429，
均为 account/user concurrency limit。两侧真实 readiness Bash 曾成功。
这些记录可能包括后台请求，不能逐条归因业务，也不能称为 key 无效或 401/403。
UUID 补丁没有修复服务端并发容量问题；本阶段没有额外模型探测或换 key。

## 本机验证及边界

| 批次 | 实际结果 | 能证明的范围 |
| --- | --- | --- |
| UUID 受影响回归 | **66/66 PASS**，43.046 秒，零错误/失败/跳过，源码前后一致 | identity、capability、live startup、bridge evidence、live control 五模块 |
| UUID 独立压力 | **38,400/38,400 符合预期**，8 进程 ×100 轮 ×48 项；零意外失败 | 14 类合法 identity、30 类拒绝、4 类生成 capability |
| 压力分项 | 11,200 合法 identity＋24,000 预期拒绝＋3,200 capability | 含 UUID 大小写、双成员、原 UI alias、错序、跨成员冲突及原 gate 只读副本 |

压力运行使用实际新生成控制器和真实 Darwin sandbox，8 workers 全部 exit 0，
原 gate 哈希不变；共同执行重叠 0.629 秒，总执行 1.259 秒。
live 观察、原生 binding 和日志内容为 fixture；模型客户端启动、模型请求、native input
均为 0。这是高频本机边界压力，不是模型吞吐、原生投递或协作提效实验。
独立审计 30/30 通过，核对新控制器 manifest，以及原 7 个节点证据和 8 个旧控制器
文件的哈希未变；未重新运行完整 live identity gate producer。

早先已完成的 7000 真实进程检查、99 回归、9600 fixture 边界、1600 同根竞争
（100 次成功、1500 次预期拒绝、100 次模拟 submit）、58 项审计、66 通信集成和
8 collector fixture 没有机械重跑。批次存在覆盖重叠，不能把这些数字加成业务通过数。
启动前源码审计对应 v19 的 312 文件；当前 identity 及测试后续有修复，不能声称当前树
仍完全等于 v19，或最终整套测试已全部重新运行。

## 经验同步与未完成工作

surface40 继续统一管理共享 MAC 和论文精炼助手；本仓仅修改 upgrade。
其 r19 历史回件与 request/release/install 三组 pins 已核验并吸收。
本次 UUID 实证与终态经既有文件 inbox 补充；文件发布、对方读取和反馈分别记录，
不得把发布称为已读或充分双向交流。原 NO_INPUT 原生联络不重发。

本阶段完成修复、本机验证与 Draft PR 收尾，不自动启动下一 campaign。
真实目标仍有以下缺口，现有全面授权无需重复确认：

1. 在新冻结候选与明确时间预算内验证真实初次握手、完整三轮 pilot；保留本次失败，
   不热改旧客户端或回放旧输入。Provider 并发容量仍是需实际观察的独立约束。
2. 完成原 12 warm＋3 cold 配对业务与独立正确性验收；比较耗时、干预和成本，
   Jev/TypeSafe 仍为 advisory/mock，当前 live 使用 rules，不能声称测得 Jev 优势。
3. 执行已定义的 192 条四方向消息矩阵，补足绑定原生记录的 BODY_READ 采集，
   独立观察持续 `/goal`、同时回调、队列消费、延迟和公平性。拒绝、缺失不补样。
4. 核验 surface40 对新增实证的回件；共享技能的检验合并由其负责。

## 原始证据索引

证据根 E：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。
下列外部证据保留在本机，不随代码提交；原始会话和凭据不进入仓库。

| 路径 | SHA256 |
| --- | --- |
| `/Users/lzhs/ul/driver/final-report.json` | `5dea46987759adb3381502517a7c03ca0204da753490e9c77159d6ffc51ccb97` |
| `E/execution/r21-original-driver/result.json` | `ecfff2abb499535d0740687b462a680097473c4864d348a60af1aba79645c250` |
| `E/validation/r21-uuid-selector-regression-01/result.json` | `cb87612c7366fb8f3801eb1cf8aaf6695662952726591f3562261f694a09cec3` |
| `E/validation/r21-uuid-selector-pressure-01/result.json` | `647b279c850257a5d7eaa2114a3356ef65f8cf9565071f540b3da18436c860bd` |
| `E/validation/r21-uuid-selector-pressure-01/independent-review.json` | `819ce59114467d2469b8f0c9be4a73353b71021b73d541a1c698b04014cdec91` |
| `E/maintenance/r21-communication-observation/20261018T172434.126764Z-98182/observation.json` | `9bb548dcc2e43d751f7ffa34e89aa2d882ac1a7815603ace76f704a34516f4f5` |

其他原件：`E/maintenance/r21-delivery/terminal-readout.json`、
`E/maintenance/r21-delivery/READONLY-HANDSHAKE-ROOT-CAUSE-01.md`、
`E/maintenance/r21-provider-observations/20261018T172434.123423Z.json`、
`E/validation/r21-uuid-selector-pressure-01/result.json`。
surface40 发布回执与最终交付复核另存于 `E/maintenance/r21-delivery/`。
