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

[原生启动快照](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-claude-version/r29-native-startup-checkpoint.json) 记录两路 Goal SET，以及 Goal 检查失败后的 1 分钟、5 分钟退避提示。两路 `api-request` 的 `querySource=repl_main_thread`、`params.model=claude-opus-5-5`，证明原生客户端所请求的型号；没有 assistant 响应，实际后端完成型号仍未证明。私有配置使用 `fable` 映射到 Opus 5.5；不能改成指向 Opus 5 的 `opus` 别名。

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

终态快照中两路各记录 6 条主请求，均无 assistant。A 的 client.log 第347、562行有 `429 group requests-per-minute limit exceeded`；第526–527行另有无 message_start、无 SSE 事件而转 non-streaming 的记录。429 对应主请求还是 Goal hook、空流 HTTP 状态均未确定。B 底层原因 UNKNOWN；两路 settings rename EPERM 的因果未证明。

两路 Stop prompt hook 曾报 `No assistant message found`；B 于09:48:50.124Z、A于09:49:21.077Z由客户端自身在 Goal 检查超时后暂停。主管没有暂停或再次唤醒它们。7组停止输入标记全部核验，客户端保留，远端取消和 Goal 结束仍未证明。总 wrapper 耗时929.762秒，launch至halt925.091秒；900秒是离散检查预算，超额时间无法全部精确归因。

[终态原生快照](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-claude-version/r29-native-final.json) 与 [独立终态审查](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r29-delivery-review/terminal-review.md) 支持以上结论。完整交付见 [R29最终报告](VALIDATION-20261019-R29.md)。本轮不再重开实验；升级采用完成，稳定协作、独立loop长期验证和提效验收仍未完成。
