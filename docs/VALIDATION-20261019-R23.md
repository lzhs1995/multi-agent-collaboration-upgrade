# r23：准备完成，恢复后的双路容量核验仍未通过

截至 2026-10-19 10:55（Asia/Taipei），**r23 全量实验尚未启动**。
7 组、33 轮、15 对、192 条普通消息已准备并绑定既有全面授权。
连续 `/goal`、业务正确性与配对协作提效仍未通过；旧 r22 保持
**1 TIMEOUT＋32 MISSING，业务 0/33、pilot 0/3、pairs 0/15**。

## 已完成的准备

- 冻结提交 `4de3df97d8af58a72de5e1559fe52d81cb357dd6`，323 个文件；
  新根 `/Users/lzhs/un`。保留已核验私有基线及全部旧会话、控制器和失败。
- 七组实际生成控制器已核验，包含正文读取、metrics v3 原始消息归因、
  配置生成及重复校验优化；既有测试结果不计作本次新测试。
- 14 条限定 socket/lock 路径全部不存在，尚无 driver 配置、启动或输入。
  7200 秒整体时限未开始；已准备一次性 driver/watchdog 入口，未执行。
- 使用既定 Codex `gpt-6-astra/xhigh` 与 Claude `fable/xhigh`
  （当前映射 `claude-opus-5-5`）。未修改凭据、模型或全局 hooks。

## 04:22 原生恢复后的第一次并发观察

原 Claude A journal 新增了 `2026-10-18T20:04:04.172Z` 的真实模型
`end_turn` 记录，晚于旧双流拒绝。原 device/inode、完整旧前缀 SHA 与
新增记录 offset/length/SHA 均已核验，保留完整原始记录。
原 Claude B 最近成功仍在旧诊断之前。此事实足以支持一次新的有界观察，
不能证明两条 Claude 流可同时完成，也不改变已经终止的 r22。

04:21–04:22，分别注册两条并发流式请求，既定模型、xhigh、每路最多
4096 输出 token、同一固定 180 秒期限，**每路一次、零重试**。

| 路 | HTTP | 实际流内结果 | 耗时 | 完整响应 |
| --- | --- | --- | ---: | --- |
| A | 200 | account concurrency limit | 31.772 秒 | 否 |
| B | 200 | user concurrency limit | 31.025 秒 | 否 |

均无 `message_start` 或 `message_stop`，完整响应 **0/2**，双路容量
**NOT_ESTABLISHED**。不能据此判定 key 无效，不能称为两条 HTTP 429。
这两条提供方诊断不进入 33 轮、15 对或 192 条消息的分母。
没有新客户端或 native 输入；本次未重复源码回归测试。

## 10:51 第二次并发观察与配置变化

恢复执行时已距上次失败超过 6 小时。新的只读 usage 查询返回 HTTP 200；
相比旧 usage 快照，目标模型累计请求增加 617、输出 token 增加 510624。
这些累计数没有逐请求时间和并发信息，不能证明当前恢复。结合较长间隔，
在原授权下执行一次新的有界并发观察，另存 `r23-provider-dual-stream-02`，
未重跑原诊断、重放 native 输入或覆盖旧结果。

| 路 | HTTP | 保存的原始 JSON 正文 | 耗时 | 完整响应 |
| --- | --- | --- | ---: | --- |
| A | 429 | `rate_limit_error`: `Upstream rate limit exceeded, please retry later` | 12.521 秒 | 否 |
| B | 429 | 同上 | 12.738 秒 | 否 |

两路各一次、零重试，固定 180 秒上限，模型/effort 与第一次相同。
完整响应 **0/2**，无已观测的流重叠。两份原始正文均为 113 字节，SHA256
`21b7fdb5bc06827ba5304194bc1830a73cc327ce9dec24b0041d7e64bdbfd443`。
原读取器另记录 `OSError`；保存的正文已独立解析，错误正文与读取异常分列，
不覆盖原结果，不把它误判为无效 key 或只有本机读取失败。
本次新增 2 条模型诊断和 1 次 usage 查询；r23 两次并发诊断累计 4 条请求，
均不进入 33 轮、15 对或 192 条消息的实验分母。

同时发现 Codex 默认 `model`、`model_providers` 已变化，当前默认模型是
`gpt-5.6-sol`；提供方地址、凭据字段亦与冻结快照不同。Claude 提供方相关值
保持一致，默认 hooks 的变化没有被移入私有控制器。已保留逐字段比较，
未将这些变化静默应用到 r23。r23 继续固定 `gpt-6-astra/xhigh`。

本机重新核验 71 个引用文件、完整源码树和语料树；14 条端点路径均不存在，
driver 未配置或启动，磁盘可用约 19.0 GB。两份原 Claude journal 无新增记录，
surface40 原三份回复仍缺失。新报告为本次进展；真实协作验收仍未完成。

## 当前边界与后续执行

保留 r23 为已授权、已准备、未配置、未启动。当前容量拒绝下不原样重开；
等待可核验的并发恢复新证据，再重新核对依赖并在临近启动时 configure 一次。
独立 watchdog ready 后执行原 driver：先连续 3 轮 pilot，再 12 warm＋3 cold
配对。保持启动 900 秒、每轮 1800 秒、整体 7200 秒；不暂停 `/goal`、
切换模型、延长时限、替换失败客户端或补样。
两小时是启动后的整体上限，不能承诺提供方恢复时间或最终通过时间。

新事实已发布到 surface40 原维护文件 inbox；未重发原 native 通知，
文件发布不证明已读。共享 MAC/论文精炼助手由 surface40 管理，
其新版本不热换入冻结实验。原经验请求继续使用原回复路径。

## 证据

根目录：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。

- `authorization/r23-20261019/offline-readiness.json`：7 组准备及既有授权。
- `preparation/r23-candidate/REVIEW-AND-NEXT.md`：完整冻结计划及 14 条路径。
- `maintenance/r23-native-recovery-01/result.json`：新原生成功及旧前缀认证，
  SHA256 `d3d60aa607cf157ee7cf3ca611bc48875be1bc018f889de31411621b7c92fff2`。
- `maintenance/r23-provider-dual-stream-01/`：两路原请求、意图、完整响应及结果。
- `maintenance/r23-native-recovery-01/closeout-readback.json`：新诊断、66 个旧文件
  pin、配置不变、14 路径不存在、尚未启动的读回核验。
- `maintenance/r23-native-recovery-01/surface40-file-publication.json`：指定 inbox
  发布及完整读回；对方阅读仍未确认。
- `maintenance/r23-provider-capacity-02/`：10:48 只读 usage 查询及完整结果。
- `maintenance/r23-provider-dual-stream-02/`：10:51 新双路原请求、HTTP 429 正文和结果。
- `maintenance/r23-native-recovery-01/dual-stream-02-rationale.json`：新观察理由与限制。
- `maintenance/r23-native-recovery-01/provider-comparison-02.json`：默认配置变化与原快照比较。
- `maintenance/r23-native-recovery-01/resumed-closeout-02.json`：原正文解析、71 文件与树核验。

本报告为冻结后的文档增量，不修改 r23 原源码快照或其解释口径。
