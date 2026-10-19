# R30 交付：实现与升级完成，原生提效未通过

2026-10-19，台北时间。**R30 已终态 TIMEOUT / INSUFFICIENT_EVIDENCE：pilot 0/3、业务 0/33、有效配对 0/15。不能声称完成了全量原生压力矩阵，也不能声称协作提效达到 15%。**

代码、组件压力验证、Claude Code 升级、社区逐项研究和维护者经验回件已形成可核查成果。本轮终态不再追加样本、替换客户端或重放原输入；全部旧结果保留。项目仍为实验技能，PR 保持 Draft。

## 是否回应最初的效率目标

保留 cmux 承载既有终端和会话，MAC 负责原生投递，upgrade 负责依赖就绪、执行通道分配、持久 callback 和批量推进。Jev/TypeSafe 目前只有本地规则与 `offline_mock`，没有新增在线 JEV 调用。这不是证明其他平台或在线编排器一定更慢，而是当前实现及可核验证据的范围。

实际改进包括：执行前用 `drafts` 提前组包；用 `next-action` 明确等待点；B/v3 的 `advance --run RUN --max-steps 12 --max-seconds 45` 在独立通道间公平推进原任务、核收原 callback、筛除过期交接，并及时交回就绪的本地工作。持久任务状态支持补查，写域维持单写者。普通忙态不阻断已就绪的信息交换，真实草稿与不确定原发送仍受保护。

这些功能减少的是主管反复查状态、组包和逐步调用的劳动。原生 START、本地业务、独立 ACCEPT、FINISH 仍由主管执行；显式 `advance` 不唤醒空闲模型，45 秒预算在同步动作之间检查。A1 基线同样允许双 Claude 并行，不能把人为串行基线当作收益。

**架构方向仍对应原目标，但执行中大量时间耗在启动、投递兼容和故障观测，实际提效验收明显滞后。** 修复这些缺陷不等于完成原任务。R30 的启动上限已执行，避免继续用同条件实验消耗时间；下一份效率结论必须来自真实接受成果和配对计时。

## R30 原次和终态

| 项目 | 核实结果 |
|---|---|
| 实验根 | `/Users/lzhs/uu` |
| 实验 ID | `resume-20261019-r30-fetchsession` |
| 受测源码 / 冻结控制器 | `d8a158509680de53f9c8bdc8c541408c3054f605` / v25 |
| 原 launch request | 11:10:26.545509 UTC |
| 900 秒启动截止 | 11:25:26.545509 UTC |
| schedule halt | 11:25:31.556429 UTC，TIMEOUT |
| driver 报告终态 | 11:25:32.083067 UTC，即台北 19:25:32 |
| wrapper 收取完成 | 11:25:33.089797 UTC，driver/watchdog 均 exit 2 |
| 准备 / 实际启动组数 | 7 / 1；仅 pilot-B 创建 1 Codex＋2 Claude |
| pilot / 业务 / 配对 | 0/3；0/33（1 TIMEOUT＋32 MISSING）；0/15 |
| 普通通信闭环 | 0/192 个唯一计划 ID；并非 192 条均已发送 |
| 业务正确性 / 提效 / 成本 | 未通过 / 无估计 / UNKNOWN |

356/356 导出文件匹配受测提交；export SHA256 为 `ba1ce3a1fc311a2234dbfef9b7c1710bb3c13cfa11b7e67736c033593db4433c`。R30 保持既定 7 组、33 业务轮、15 对（3 cold＋12 warm）、192 个普通通信样本；pilot 连续通过 3 轮才进入配对。缺失与失败留在分母中。

7 个停止输入标记于 11:25:32.163949–32.203425 UTC 发布，哈希核验全部通过。终态中的 `input_operations=0` 指停止/报告操作，不表示客户端从未发出模型请求。`terminate_clients=false`；独立观察确认 driver/watchdog 已退出，原 Codex UI/app-server 和双 Claude 仍保留。停止未来实验输入不证明远端请求取消、Goal 结束或资源释放。

wrapper 总耗时 911.127 秒；实际 schedule halt 比启动截止晚 5.011 秒。这是有界轮询和同步检查下的观测，不是严格实时截止保证。原配置总截止 13:17:24.649321 UTC、原生 Goal 截止 13:18:14.190806 UTC 均未延长；本次已提前因启动超时终止。

## Claude 303 的真实采用与失败层

| 原生证据 | Codex | Claude A | Claude B |
|---|---|---|---|
| PID | 61228 / app-server 61357 | 61220 | 61210 |
| session | `01a153db-34c5-7151-8e20-fd985ae2579f` | `88e07a68-2129-497e-b15b-618a8feb4b11` | `feead08c-49e1-428d-a278-d9064ba4cce0` |
| 原生启动探针 | 1 次完成，整体 adoption=false | 0 | 0 |
| 自动 Pre/PostToolUse 记录 | 114 | 0 | 0 |
| Claude 主 journal assistant | 不适用 | 0 | 0 |
| Goal | create_goal 返回 active | SET 后检查超时暂停 | SET、退避后检查超时暂停 |

两路真实 executable 均为 `/Users/lzhs/.local/share/claude/versions/2.1.303`。私有配置为 `fable/xhigh`，主 journal 请求 `claude-opus-5-5`；这证明请求型号，不证明后端成功完成。Claude 的 Stop advisory 确有自动执行，但 `thread_scope=UNVERIFIED`，不能替代启动 probe 和完整采用门槛。

A 在 11:11:52.906 UTC、B 在 11:21:09.581 UTC 自行显示 Goal 检查超时暂停；对应 Stop prompt-hook 分别超时 30004 / 30018 毫秒。主管没有发送暂停或唤醒输入。Goal Stop 检查是独立模型请求，不等于普通任务输出，也不验证独立 `/loop` 长时运行。

修复后的观察器 SHA256 `f698017e8bd438074baacf584d65b0b8602705aee5c4d15921d61e9595453ba9` 已在两路真实加载，并实际拦截 `bun_fetch_session`。本次补齐了此前只有 LOADED、没有请求路径证据的缺口；不能回填 R29 历史路径。

| 原始响应观察 | A | B |
|---|---:|---:|
| RESPONSE 记录 | 3 | 13 |
| HTTP 200 / 429 | 3 / 0 | 11 / 2 |
| message_stop | 2 | 9 |
| 非空白文本 / tool-use 块 | 0 / 0 | 0 / 0 |
| 其他 | 1 READ_ERROR | 1 SSE rate_limit_error；1 FETCH_REJECTED |

HTTP 200、message_stop 或 usage 增长不足以证明有效输出。读流失败、未覆盖、截断及预先消费的响应保持未知，不能把局部零可见文本外推为完整原始响应为空。

debug 日志明确分类计数为主任务 A2/B8、Goal `hook_prompt` A1/B4、后台 `away_summary` A0/B2；它们包含重试/入口记录，不是唯一 wire request ID。观察器的单响应 `request_class` 仍为 UNKNOWN，缺一对一 request-ID 关联，不能按相邻时间把某个 429 断言为主任务失败。R29 的后台摘要成功属于另一原次，不移入 R30。

从 170 升级到 303 有官方 Goal 修复和 Opus 5.5 支持依据，且采用已证。此次结果说明**仅更新版本没有使本次协作成功**；也没有证明 key 无效、模型版本是唯一根因或某一方必须永久空闲。

## 已通过的源码与压力验证

[Actions 39241757006](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39241757006) 已对 d8a1585 完成四格 Python CI：

| 环境 | 总项数（含跳过） | 跳过 | failures / errors |
|---|---:|---:|---|
| macOS / Python 3.14 | 2176 | 96 | 0 / 0 |
| macOS / Python 3.10 | 2176 | 96 | 0 / 0 |
| Ubuntu / Python 3.14 | 2176 | 106 | 0 / 0 |
| Ubuntu / Python 3.10 | 2176 | 106 | 0 / 0 |

这是 2176 个发现用例在四个环境重复执行，不是 8704 个不同用例。跳过不算通过；没有声称新提交所有本机环境用例均已重跑。

- 历史本机完整回归属于 `9a09f32`：2171 项，零失败/错误/跳过，1226.361 秒。它不包含后增五项社区组合测试及新观察器专项。
- 24 进程组件压力：7200 reserve、31200 release 检查，2400/2400 独立成功，未观察到双 owner、registry_busy、异常或历史丢失；不是原生业务吞吐。
- 观察器独立验证：Node 47/47；实际 Claude 303 内嵌 Bun 的四轮各 256 并发 loopback 流通过。没有调用模型，不属于上述 Python CI。
- 五项社区组合测试使用项目实际持久状态 API、FakeRuntime 和合成 journal，覆盖历史断链、旧 owner/candidate、callback/local 同时就绪及积压发现。它们不是运行第三方编排器，也不是空闲模型唤醒证明。

此前 green CI 未捕获本机 13 个打包失败的记录保留，相关私有 TOML 依赖、许可、provenance 及 portable 测试已修复。后续交付提交仅修改文档，不重导出 v25 或改写受测代码。

## surface40 回件、完整问题台账与社区借鉴

已收取并全文读取两份询问的[共同答复](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r28-owner-reply.md)和[详细经验](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/standalone-caller-v1/EXPERIENCE-REPLY.md)，两请求 SHA 与附件 SHA 均独立核对。此前“回件未取得”已过时。普通 helper 的历史拒绝仍保留为零输入，没有绕过；指定文件回件是经验交流，不冒充原生投递或业务验收。

[41 类问题台账](COLLABORATION-ISSUE-LEDGER.md)逐项记录事实、出处、归属、实现落点和未测范围，涵盖任务四字段、忙态通信、Goal/loop、身份/进程/会话绑定、草稿/折叠、原生回执、callback、占用与租约、文件回件、hook 采用、版本升级、隔离、截止与计量、共享应用单写、成本/返工等。1045 文件库存、966 文件补扫中 43 份全文审读、923 份未全文审读，不能声称穷尽人工审计；owner 也未重新穷尽全部 41 类。

本轮吸收的具体经验：普通忙碌不阻断就绪消息；发布→自动曝光→全文→mailbox 回件→原执行者读取→exact consume→rearm→RELEASE 分层；升级文件不等于进程采用；PID 更换后旧 episode 不能继承成功；不把询问次数当吞吐，不把 81 MB state 当每次写量，不把锁失败跨度当持锁时间。论文独立核验/草拟可并行，Word/Zotero 等真实共享资源按租约单写。owner 报告的完整 reservation 往返仍为 0/2。

[共享 MAC PR55](https://github.com/lzhs1995/multi-agent-collaboration/pull/55) 在本轮审计时 OPEN、非 Draft，head `b461edc5171b8d018338fe45655bb750c94cc3a6`。独审实际 diff 确认版本目录 executable 严格识别，以及握手 `TaskPackContractError` 归责 supervisor、写 FAIL/REJECTED、保留原 paste intent 并停止 ACK 轮询。其 [PR CI](https://github.com/lzhs1995/multi-agent-collaboration/actions/runs/39243112331) 四格各 2445 项（skip 1、F0/E0），另有 schema 6 项通过。这不证明旧进程加载、真实草稿恢复或在线接收；这些共享源码修复未移植进冻结 R30，后续隔离整合须先比对等价私有路径。共享代码仍由 surface40 管理。

[15 条社区逐项分析](COMMUNITY-ADOPTION-20261019.md)已更新源码 pin、Issue/PR、采用决定与缺证。采用四字段任务单、写域隔离、集成后验收、有界独审、持久 callback、复用已有 agent 和依赖图；不照搬成本节省、模型排行或第三方默认等待。Orca 的 600 秒是超时上限，不是最低等待。13 条推文来自第三方镜像而非 X 第一方认证；视频 LOGIN_REQUIRED/无字幕、Reddit 官方 403 保持未验证。未实现的 ring cursor、generation watcher、统一 async select 不由相似本地用例替代验证。

## 剩余门槛和停止条件

真实双 Claude 必须先产生启动 probe、自动 hook 和有效主任务输出，才能进入连续三轮 pilot、3 cold＋12 warm 配对及 192 普通通信矩阵。正确性、回执、独立业务接受和干预合同通过后，才检验 warm 配对中位提效至少 15%、各工作类中位退化不超过 5%。独立 `/loop` 长期稳定性另验，不能以 `/goal` 代替。

目前这些门槛没有通过，服务输出/限流/读流问题的精确归因也不完整。因此没有可负责承诺的“提效通过时间”。R30 原次已关闭，本轮交付按台北 19:45 前完成；不得把后续代码研究或静态检查描述为仍在进行全量原生压力测试。任何后续新实验保留原失败，并以实际条件变化和独立登记为前提，不扩大本轮截止或补样。

## 原始证据

- [原 runner 结果](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/execution/r30-original-driver/result.json)：SHA256 `a0bb407e3f96084c52a8dcb1de2eab2dc79f06d003a180f19ff93381ecd57817`。
- [原配对终态](/Users/lzhs/uu/driver/final-report.json)：SHA256 `7ca3a200132efa79119252f3dd714fbe1371fb9e79647bb37e05aa56a8c39fc2`。
- [独立原生终态审计](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r30-followthrough/native-adoption-audit/FINAL-INDEPENDENT-AUDIT.md)：SHA256 `427291e63e92aa394261c9a8a38458a5468a89b983241f0233b2e1e5ed02fe85`。
- [CI 与 v25 对照审计](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r30-followthrough/ci-closure/REPORT.md)。
- [surface40 回件与 PR55 独审](/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/r30-followthrough/owner-update-audit/REPORT.md)：SHA256 `7588343f6a55d8a9e51eb34d954e3b4343502bc09c09c172aa46ef32a403fec2`。
- [Claude 升级与采用](CLAUDE-VERSION-VALIDATION.md)；[R29 历史终态](VALIDATION-20261019-R29.md)。
