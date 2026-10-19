# R29 最终交付：Claude 303 已采用，真实提效未证明

2026-10-19。**本轮原生实验已结束，结论为 TIMEOUT / INSUFFICIENT_EVIDENCE。** 不再把它描述为持续进行的全量压力测试，也不以离线测试或启动成功替代真实业务完成。

## 与任务初衷的对应

保留 cmux 作为已有会话的终端承载，MAC 负责原身份、原生投递和回调证据；upgrade 负责依赖就绪、单写者占用、公平分派和批量推进。Jev/TypeSafe 当前仅有 `rules/offline_mock` 接缝，未接入在线服务。这是减少新增服务依赖的实施选择，不是已证明其他编排器无效，也不是永远排除 Jev。

`advance --run RUN --max-steps 12 --max-seconds 45` 已实现对 B 组的有界批量推进：核收原次回调，推进收尾，在独立 lane 之间公平选择，把就绪 local 业务优先交回主管，过滤过期 handoff。`drafts` 和 `next-action` 减少临时组包与反复定位。原生 START、业务执行、独立 ACCEPT 和 FINISH 仍需主管完成；空闲模型不会被该命令自动唤醒。45 秒在动作之间检查，不是强行中断同步操作的硬实时上限。

架构方向仍对应提效目标，但执行时间大量用于身份恢复、客户端兼容、服务故障和证据修复。**用户要求的“协作更快”尚未完成实证验收。** 没有有效配对数据，不能宣布至少 15% 提效、费用节省或生产可用。

## 原生压力矩阵的实际结果

| 指标 | 原始分母及终态 |
|---|---|
| 实验组 | 7 组完成准备；仅第 1 组实际启动 1 Codex＋2 Claude |
| 连续 pilot | 0/3 |
| 业务验证 | 0/33；1 TIMEOUT＋32 MISSING |
| 可比较配对 | 0/15；原计划 12 warm＋3 cold |
| 普通通信 | 192 个唯一 request_id，闭环可验证 0/192；不表示已发送 192 次 |
| 效率、成本 | 未证明；缺失时间、usage 和费用保持未知 |
| 控制器 | immutable v24，源码 b0ac022f9ec380a0873a17e67668a49ab1c1c64e |
| 原终态时间 | 2026-10-19T09:55:46.520524Z（台北 17:55:46） |

A1 也允许双 Claude 并行，不能人为改成串行基线。通过标准仍是正确性与干预合同先满足，再检验 warm 配对中位改善至少 15%、各任务类中位退化至多 5%。0/15 可比较配对不产生效率估计。

driver 与 watchdog 均 exit 2，无存活 driver 子进程。7 个停止输入标记的大小和 SHA 全部核对；`input_operations=0`、`terminate_clients=false`。原客户端保留，远端取消未证明，原生 Goal 结束仍为 UNVERIFIED。

整体 wrapper 耗时 929.762 秒；launch 至 halt 为 925.091 秒。900 秒启动预算采用 tick 检查，源码存在同步检查和默认 10 秒轮询，不能称严格 900 秒即时停止。缺少逐阶段计时，全部额外耗时的精确归因未知。

## Claude Code 升级与故障定位

官方 2.1.303 的 manifest、SHA256、版本及 strict codesign 已验证，三个 CLI 入口均实际输出 2.1.303。旧二进制、CCC launcher、旧会话与配置保留；采用官方原生安装，未迁移 Homebrew cask 元数据。详见 [版本验证](CLAUDE-VERSION-VALIDATION.md)。

R29 两路实际进程及原生日志均为 2.1.303，请求型号均为 `claude-opus-5-5`；每路记录 6 条主请求，均无 assistant。请求型号不证明后端实际完成型号。Codex probe 和自动 hook 已观察，双 Claude 未达到 ready。

- A 路原日志出现 `429 group requests-per-minute limit exceeded`，另有无 message_start/无 SSE 事件而自动切换 non-streaming 的记录。429 属于主请求还是 Goal hook 尚不能确定，空流的 HTTP 状态也未知。
- B 路底层失败原因 UNKNOWN，不能套用 A 的限流结论。
- 两路 Stop prompt hook 曾报 `No assistant message found`。B 在 09:48:50Z、A 在 09:49:21Z 出现 Goal 检查超时后自行暂停；不是主管通过暂停 Goal 取得握手窗口，没有再次发送唤醒输入。
- 两路响应观察器只有 LOADED，原生日志却有请求。缺观察记录不能说明没有请求，也不能据旧 R28 推断本轮的 HTTP 状态。
- settings rename EPERM 与上述错误同现，因果未证明。没有放松隔离以消除报错。

官方后续版本修过 Goal 恢复/Stop hook，2.1.280 正式支持 Opus 5.5，因此从 170 升级有依据。但某项 290 loop 修复已在 293 撤回；版本发布说明、Goal SET 或退避提示均不能证明 `/goal` 稳定性。本轮也不是独立 `/loop` 长期验证。两个版本顺序运行且服务状态等条件不同，不能得出单变量版本因果结论。

## 实现、组件和跨平台验证

受测代码为 `9a09f32296f160b32bd3fb7d74ee93ad37715747`。它补齐私有控制器的两项 TOML 依赖、许可和 provenance，并将依赖纳入 manifest 的 hash/mode 校验。原 13 个失败案例所在的 12 个方法通过；新增 3 项 portable 打包测试与实际 materializer validator 共 4/4 通过。后续文档提交不改变该受测代码。R29 保留旧 v24，不能称它实跑了此次打包修复。

本机 Python 3.14 完整回归原次于 2026-10-19T10:09:34.828919+00:00 结束：**2171 项，0 failures、0 errors、0 skipped，1226.361 秒**；被监测脚本前后 SHA256 一致，零模型请求。

[GitHub Actions 39232997253](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39232997253) 四组成功，受测 SHA 为 9a09f32：

| 平台 | Python | 总项数 | failures/errors | skipped | 秒 |
|---|---|---:|---|---:|---:|
| Ubuntu | 3.10 | 2171 | 0/0 | 106 | 452.208 |
| macOS | 3.10 | 2171 | 0/0 | 96 | 446.978 |
| Ubuntu | 3.14 | 2171 | 0/0 | 106 | 509.840 |
| macOS | 3.14 | 2171 | 0/0 | 96 | 397.084 |

保留旧失败：c40b9ce 四格 CI 绿色，但本地完整回归有 13 failures，原因是 CI 跳过原生安装依赖路径，掩盖私有包缺依赖。新 portable 用例覆盖该缺陷，不把 CI 绿色当成完整实机证明。

组件压力完成 24 进程、7200 次 reserve、31200 次 release 检查、2400/2400 独立成功；冲突双 owner、registry_busy、异常和历史丢失均为 0。该结果仅证明受测组件，不替代原生协作结果。

## surface40 与社区建议

[问题台账](COLLABORATION-ISSUE-LEDGER.md) 已列 41 类问题及来源、owner 处置、upgrade 落点和待测边界。库存 1045 个文件；补扫 966，其中 43 份全文复核、923 份未全文复核，不能称事无巨细的人工审核已经完成。

[社区矩阵](COMMUNITY-ADOPTION-20261019.md) 已对 15 条线索逐项区分源码支持、采用决定和缺失证据。采用任务四字段、依赖/尝试分层、持久状态补查、单写者/集成验收、有界独审等机制；不采信未经核验的成本节省，不把超时上限误读成最低等待。9 个机制的测试映射为 5 个离线限定、2 个部分覆盖、2 个精确场景未测；没有实际运行所有第三方工具。

surface40 继续统一维护共享 MAC 和论文精炼助手。本轮两份询问已写其维护 inbox，尚未取得 owner 回复；文件发布不代表收到、读取或合并。直接联络被自动审批/guard 以工作区边界拒绝，实际输入为零，未绕过该拒绝。因此“充分沟通并统一合并”仍未完成。

## 本轮收口与剩余工作

本轮结束并保留全部原次，不启动 R30，不重放或补样。草稿 PR 保持 Draft。剩余工作为：服务端能持续提供有效双路输出后再安排独立注册实验；核实 B 路原因及新版观察器覆盖缺口；完成原生业务/通信/效率配对及独立 loop 长期验证；取得 surface40 回复并补齐未测社区场景。现有失败不能被未来成功删除或改写。

原始证据根：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。

- `execution/r29-original-driver/result.json`，SHA256 `626fc92ebec785c889f3e02c585110d216481b9d2082121f77a6d4b87b8a9666`。
- `/Users/lzhs/ut/driver/final-report.json`，SHA256 `ec2b702f8c2d9070c510668d672fbbc71efa401596c95b443893f17c02950dea`。
- `maintenance/r29-delivery-review/terminal-review.md` 与 `terminal-review.json`：独立原终态审查。
- `maintenance/r28-claude-version/r29-native-final.json`，SHA256 `781096e209a09be77dc4f05d295a7d9a61ce4581831313663cac2050d6d9206e`。
- `maintenance/r28-ci-portability/application/private-support-repair/final-full/full-regression.json`：本机最终完整回归。
- `maintenance/r28-ci-portability/application/private-support-repair/final-ci/result.json`，SHA256 `4f5050d890a90dd2052a53e24d1b4ee02fd47d348adf228a7102077a785ca6ab`。
