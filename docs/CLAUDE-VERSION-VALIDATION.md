# Claude Code 2.1.303：安装、实际采用与验证边界

2026-10-19。本轮已按用户授权从 2.1.170 升级到官方 2.1.303；旧会话、旧二进制、配置及原实验结果保留。版本滞后与本任务有关，但尚不能认定为启动失败的唯一原因。

## 官方变更为什么相关

| 版本 | 与本任务有关的官方变更 |
|---|---|
| 2.1.269 | Goal 在 API、网络、token 错误后不再静默停滞，加入退避或说明。 |
| 2.1.274 | Goal 压缩后 resume、上下文超限及 Stop prompt hook 的修复。 |
| 2.1.280 | 正式支持 `claude-opus-5-5`。 |
| 2.1.281 | 调度投递失败后的重复唤醒、退出问题。 |
| 2.1.288 | 对拒绝 structured outputs 的网关，调整 prompt hook 兼容行为。 |
| 2.1.290–2.1.299 | 多项 loop 的压缩、恢复、后台与取消修复；其中 290 的 cloud-container-restart 唤醒修复在 293 撤回，不能称 303 已包含全部修复。 |
| 2.1.303 | 一般可靠性修订，未对本次故障提供专项修复承诺。 |

来源：[官方 CHANGELOG](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)。本轮保存的 Git blob 为 `1da79e602ca805ee8f367b51d99c0c54269a6f74`；[正文与来源快照](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-claude-version/official-changelog.md)。发布说明不等于本机复现结果。

## 已完成的升级

当时 `brew` stable cask 是 2.1.292，`claude-code@latest` 是 2.1.303。为保留旧实验的二进制 pin，本次安装官方原生 303 二进制，未迁移 cask 元数据。

- 官方下载：<https://downloads.claude.ai/claude-code-releases/2.1.303/darwin-arm64/claude>。
- 固定二进制：`/Users/lzhs/.local/share/claude/versions/2.1.303`，267973136 bytes。
- SHA256：`d2da3b003dc08b88b7230469014fe3e1dc6fe94bc8f648e89b34175d30269370`；官方 manifest、版本输出和 strict codesign 均通过。
- `/opt/homebrew/bin/claude`、`/Users/lzhs/.local/bin/claude`、`/Users/lzhs/.nvm/versions/node/v24.16.0/bin/claude` 均已更新，分别实测输出 2.1.303；CCC launcher package 保留。
- [入口切换结果](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-claude-version/entrypoints-result.json) 是当前状态，较早 `native-install.json` 的未切换字段属于历史记录。

## R28 / R29 原生证据

R28 的旧 170 双 Claude 在 900 秒启动窗口内没有 assistant、启动 probe 或业务完成。原次于 09:33:19Z 终态 TIMEOUT，业务 0/33、配对 0/15，未替换、重放或改写结果。原响应观察器证明部分 HTTP 200 流没有 content block；这不构成模型有效输出，也没有据此证明 key 失效。

R29 第一组于 09:40:21Z 创建。两名 Claude 的原生版本记录与实际进程路径均已证明采用 303：

| Lane | 原 session | PID | 版本 |
|---|---|---|---|
| Claude A | `0d4f6ec4-dc7d-4e71-afd9-3adf863680f2` | 45057 | 2.1.303 |
| Claude B | `47912cd3-25b6-44af-9f36-42e3f83ed6c7` | 45097 | 2.1.303 |

[原生启动快照](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-claude-version/r29-native-startup-checkpoint.json) 记录两路 Goal SET，以及 Goal 检查失败后的 1 分钟、5 分钟退避提示。两路主 journal 的 `api-request` 为 `querySource=repl_main_thread`、`params.model=claude-opus-5-5`，证明原生客户端所请求的型号；主任务没有可见 assistant 输出，实际后端完成型号仍未证明。后续审计另发现 B 有成功的后台摘要，不能把主任务空输出扩展为所有请求均无输出。私有配置使用 `fable` 映射到 Opus 5.5；不能改成指向 Opus 5 的 `opus` 别名。

该快照时 Codex probe/自动 hook 已观察，双 Claude 仍未 ready，pilot 0/3。303 的响应观察目录只有 `LOADED`，没有请求记录；原生日志却有请求，因此不能用观察文件缺失推断“未请求”，也不能照抄 R28 的 HTTP 200 结论。新版退避行为有原生证据，成功执行协作尚无证据。

R29 原次保持同一 v24 控制器、模型配置及 33 轮/15 对/192 条普通通信矩阵。实际配置截止 13:17:23Z，早于原固定上限 13:18:14Z；900 秒 startup 窗口不延长。两个版本按顺序运行，身份、路径、服务状态与剩余时限不同，不能当作严格的单变量版本因果实验。

## 纳入 upgrade 的经验

1. 分开记录官方能力、磁盘安装、实际进程版本、原生请求型号和业务完成；每层都不能替代下一层。
2. 版本更新不热换在途控制器，也不改变旧结果分母。经授权的新版本实验独立留证，仍服从原截止时间。
3. Goal SET、退避提示、自动 hook、连续更新、双向通信和业务正确性分别验收；`/goal` 矩阵不能冒充独立 `/loop` 长期运行验证。
4. 持续无法生成有效 assistant 输出时停止当前有界实验，交付失败层与证据，不能靠扩大重试轮数证明稳定。

当前结论：**升级完成且新进程已采用；协作稳定性、完整压力结果和 ≥15% 提效尚未通过。**


## R29 最终状态补记

原次于 2026-10-19T09:55:46.520524Z 结束，driver/watchdog 均 exit 2：TIMEOUT / INSUFFICIENT_EVIDENCE。pilot 0/3、业务 0/33（1 TIMEOUT＋32 MISSING）、可比较配对 0/15；192 个唯一普通通信 ID 的可验证闭环为 0/192，不表示 192 次均已发送。仅第一组启动，剩余六组为准备状态。原控制器为 b0ac022 的 v24，后续 9a09f32 打包修复未迁移到该原次。

后续[原生诊断](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r29-followthrough/native-diagnosis/report.md)确认：两路主任务各有三次原生 `resultLen=0` 回合结束，并各有三条原生 continuation 提示，原文为 `[Your previous response had no visible output. Please continue and produce a user-visible response.]`。这证明主客户端可见输出为空，但不重建原始 HTTP 响应。B 的后台 `away_summary` 曾收到 assistant 并以 `output=43` 结束；后台摘要不算主任务或业务完成。

| debug 请求入口类别 | A | B | 请求型号呈现 |
|---|---:|---:|---|
| `repl_main_thread` | 7 | 6 | `claude-opus-5-5[1m]` |
| Goal `hook_prompt` | 5 | 3 | `claude-opus-5-5` |
| `away_summary` | 2 | 3 | `claude-opus-5-5[1m]` |

这些是包含重试和 fallback 的 debug 入口计数，不是唯一服务 request ID。各主 journal 的六条 `api-request` 不等于全部 API 活动；请求字符串及 `[1m]` 不证明实际后端或有效上下文容量。A 的 client.log 第347、562行两次 `429 group requests-per-minute limit exceeded` 均位于 Goal-hook 时间区间内并随后出现 Goal 重试，时间顺序强烈支持 Goal-hook 归属；没有 request-ID 关联，不能称绝对 wire 归因。第525–529行还记录主请求等待30秒无初始chunk、无 message_start/事件及 non-streaming fallback，原始 HTTP 状态/正文仍未知。B 没有对应的明确429或空流诊断，底层原因 UNKNOWN；两路 settings rename EPERM 的因果未证明。

两路前两次 Goal 检查均报 `No assistant message found`。该错误来自独立的 `hook_prompt` schema 请求：无工具、thinking disabled、默认30000毫秒超时，并非仅在主 journal 中查找 assistant。B 于09:48:50.124Z、A于09:49:21.077Z由客户端自身在 Goal 检查超时后暂停。主管没有暂停或再次唤醒它们。7组停止输入标记全部核验，客户端保留；暂停不等于结束，远端取消和 Goal 结束仍未证明。总 wrapper 耗时929.762秒，launch至halt925.091秒；900秒是离散检查预算，超额时间无法全部精确归因。

[终态原生快照](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-claude-version/r29-native-final.json)、[独立终态审查](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r29-delivery-review/terminal-review.md)及后续原生诊断分别保存各阶段证据。完整交付见 [R29最终报告](VALIDATION-20261019-R29.md)。此前“不再重开”指 R29 原次收口，不是对已授权后续独立实验的永久禁令；本次文档补记接收任务时 R30 尚未启动。升级采用完成，稳定协作、独立loop长期验证和提效验收仍未完成。

## 后续观察器修复：本地通过，R30 实际覆盖见下

冻结观察器只包裹 `globalThis.fetch`；实际303源码的 `p5n` 在当前global等于捕获值 `wn` 时可选择缓存的 `Bun.FetchSession.fetch`，绕过该包裹。源码及本地复现确认兼容缺陷，但 R29 未留分支遥测，不能追认每次历史请求必经该分支。LOADED只证明preload执行；原代码在transport前写REQUEST，因此“响应未读”本身不足以解释没有REQUEST。

[候选修复与验证](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r29-followthrough/observer-repair/report.md)保留原生session，为新构造实例覆盖fetch观察路径，并区分构造器已安装、实例安装和实际拦截。候选 SHA256 为 `f698017e8bd438074baacf584d65b0b8602705aee5c4d15921d61e9595453ba9`。**47/47 Node通过；实际303内嵌Bun 1.4.4完成global/session × reader/iterator四轮各256条并发loopback流**，合计1024流、1048576个delta、216189952字节。每轮使用新观察上下文，不是单个512请求寿命内观察1024请求。另以独立Bun验证global路径；该Bun无FetchSession。

这些均为本地验证，原生可执行文件只运行隔离的 `--version` 加loopback fixture；重建源码选择表达式，不执行原生业务 `p5n`。没有模型请求、业务样本或提效测量，旧2171项完整/CI也不含这次观察器验证。已存在的session/缓存构造器仍不覆盖，重复路径计数不是通用网络尝试计数；请求类别保持UNKNOWN，不从prompt猜测。未读、截断或预先消费的响应不能据text=false判空，观察开销未校准。新实验仍须独立证明实际加载及业务响应覆盖。

## R30 最终补记：303 与 FetchSession 均真实采用，业务未开始

R30 使用 d8a1585 冻结 v25，于 11:10:26.545509 UTC 发出原启动请求；900 秒窗口后，11:25:31.556429 UTC 发布 TIMEOUT，driver/watchdog 均 exit 2。pilot 0/3、业务 0/33（1 TIMEOUT＋32 MISSING）、有效配对 0/15、普通通信闭环 0/192。完整结果见 [R30 报告](VALIDATION-20261019-R30.md)。

| Lane | 原 session | PID / 实际版本 | 主 journal assistant / probe |
|---|---|---|---|
| A | `88e07a68-2129-497e-b15b-618a8feb4b11` | 61220 / 2.1.303 | 0 / 0 |
| B | `feead08c-49e1-428d-a278-d9064ba4cce0` | 61210 / 2.1.303 | 0 / 0 |

两路均真实加载 f698017e 观察器并拦截 `bun_fetch_session`，补齐新版本的实际观察路径证据。A 有 3 条 RESPONSE（HTTP200 3），B 有 13 条（HTTP200 11、429 2），另有一次 FETCH_REJECTED；非空白文本和 tool-use 块均未观察到。A 有 READ_ERROR；B 有 SSE rate_limit_error 和 HTTP429 rate_limit_exceeded。没有用不完整响应推断完整空正文，也没有据此认定 key 无效。

debug 显式 source 为主任务 A2/B8、Goal hook A1/B4、后台 A0/B2，包含入口/重试计数。单响应 `request_class=UNKNOWN`，缺 request-ID 关联，不能把全部 429 或空响应按时间归为主任务。HTTP200/message_stop/usage 与可见输出、probe、业务接受分别判断。R29 的后台成功不属于 R30。

A 于 11:11:52.906 UTC、B 于 11:21:09.581 UTC 因 Goal Stop 检查超时自行暂停；主管未发送暂停/唤醒。两路自动 Pre/PostToolUse 为 0；Stop advisory 已自动执行但 thread_scope 未认证，不能算完整采用。7 个停止输入标记已核验，原客户端保留；远端取消和 Goal 结束未证。

版本滞后已解决，独立 `/loop` 长期稳定、双路协作正确性及 ≥15% 提效仍未证明。不能将 R30 的失败归结为“仍在使用 170”，也不能将升级本身当成稳定性验收。
