# 2026-10-18 压力测试与真实协作验证

当前结论：**组件压力通过；生成控制器修复后的完整回归1,804/1,804通过；真实协作提效仍未证明。**
本次 review_mode 为 `solo_self_review`。Jev/TypeSafe 仍为 advisory/mock，
真实 campaign 使用 rules。旧会话、失败分母、原控制器及原始证据均保留。
本文替代旧效率报告中的执行进度；平台架构分析仍可参考旧报告。

## r12 原始真实实验运行中（2026-10-18 13:05 台北）

v11 已冻结 295 个文件，七组生成控制器已逐字自审；沿用完整回归通过的
`4b38553` 产品代码。独立 r12 授权配置已成功生成，原 driver 仅启动一次，
证据为 `execution/r12-original-driver/`，实验根 `/Users/lzhs/uc`。

- 04:53:39Z：原 1 Codex＋2 Claude 全部 ADOPTED，原生 readiness、实际自动
  hooks、内核隔离及输入能力分别核验。04:54:59Z 发布原 bootstrap。
- 04:58:53Z：原 Codex 发出唯一正式 `start --run pilot-01`；05:00:21Z 原生
  CommandExecution 记录 exit 0、STARTED，执行耗时 86.659 秒。
  05:04:18Z 原 Codex 收取同一个工具进程结果，未重放 start。
- 截至 05:04:55Z，原 driver 仍为 WAITING_ORIGINAL_SUPERVISOR；没有 halt。
  已确认成绩仍为 pilot **0/3**、pairs **0/15**、closures **0/33**。
  两只 Claude 尚无业务派发记录，不能把三客户端采用计为业务通过。
- 原 run.json 的正式时钟对应本轮截止点 **05:28:53Z / 台北 13:28:53**；
  这是单轮 watchdog 边界，不是最终交付时间。最终耗时尚不能可靠估计。

继续收取原 driver 和原三客户端证据；完整规模保持 3 轮 pilot、12 warm＋3
cold pairs。只读增量观察保存至 `maintenance/r12-progress/`；未向实验客户端
发送额外催促，未改冻结控制器、未重新运行已经通过且源码未变的组件压力。
本节属于全测完成后的文档更新，不声称参与此前 1,804 项测试。

## r11 默认 Claude 页脚误判与修复

`default-footer-tip-full-01` 原进程已正常结束：**1,799/1,799**，零失败、错误和
跳过；总耗时1,395.260秒，245个监测文件SHA不变。日志SHA为
`df42e3c62817a1a818bec145ce759e283009e50f0c974413ed7d8ceb69630d47`。

进一步检查发现，实际实验从固定MAC 0.4.34 base生成controller，单改工作仓库
bridge不会进入实际客户端。已在`upgrade_live_patch.py`为base的两个解析函数
增加精确source-shape转换；没有整文件覆盖，也没有丢失base的原生Codex投递。
新增转换测试验证仅两处函数变化、投递函数逐字保留、未知/重复形状拒绝；另用
真实Seatbelt子进程加载实际生成controller，检查两份原始实屏、Tip/spinner、
正文、草稿零输入、队列以及真实fixture日志原EOF fence后的精确回执。
这里的真实子进程和生成代码不代表模型会话或真实投递已经通过。

新增4项与原18项合计**22/22**定向通过，零失败、错误和跳过，14.899秒。
证据`validation/r12-materialized-chrome-focused-01/`。最终完整回归使用独立目录
`validation/r8-pressure-and-full/materialized-chrome-full-03/`，原进程已正常结束。
**1,804/1,804通过**，零失败、错误和跳过；执行器耗时1144.898秒，
246个监测文件测试前后SHA完全一致。未放宽内部超时，未跳过用例。
完整日志SHA256：`1afe54174535c7ed2b01d9c65ffcaeb8691d0ca9c9920ca2e4d02ae824ef5b87`。
本结果为离线回归，live_model_requests=0；本文在结束后更新并单独固定。
此前full-02因诊断器对StringIO调用fileno而零测试退出，exit1、6.432秒，
counts=null、246文件未变。原结果保留；新外部runner仅将栈诊断输出绑定到
独立真实文件描述符，不改产品源码、测试断言或测试内部超时。
候选审查将逐组检查生成bridge的转换记录及实际函数字节，不能只检查支持模块pin。

首次生成控制器完整回归 `materialized-chrome-full-01` 完成987项后停在
`test_eventual_acquisition_runs_body_once`。系统采样显示主线程等待工作线程退出，
工作线程阻塞于Python RLock；SIGINT取得原traceback，确认3秒争用信号断言
失败后，ThreadPoolExecutor清理仍在等待。随后仅终止原测试子进程：returncode=-15，
1203.776秒，counts=null，246个监测文件SHA不变。本轮不是PASS。
在独立诊断子进程中，显式持有unittest.mock全局RLock可复现同一等待；它证明
测试观察器依赖Mock锁，不足以证明原suite为何留下该锁。测试观察器改为直接
替换普通函数，仍调用真实flock并保持全部争用、inode和截止时间断言。新增
回归确保观察器不依赖Mock调用记录锁。未修改产品锁实现或内部超时。
诊断、原系统采样及前后回归保存在`validation/r12-test-observer-lock-01/`
及原完整回归相邻证据；最终完整回归现包含1804项。

指定经验会话的 [PR39](https://github.com/lzhs1995/multi-agent-collaboration/pull/39)
固定 commit `41065d1ccd2c70fc82ef7d04fd810edc85f22d94` 提供 spinner 后跟
完整 `⎿ Tip:` 的案例。本地实际复现三种活动状态被误判为空闲，证据保存在
`validation/experience-0436-review-01/upgrade-before.json`。修复仅在 composer
之前略过完整 Tip/goal 提示，并窄匹配无计时器 spinner；正文和草稿不被过滤。
经验材料已用于本地复现与修复；指定原询问仍无双向回复。

默认页脚的 14 项定向回归通过后，旧 `default-footer-full-01` 完整回归因发现
上述新缺陷而主动停止：原子进程 SIGINT，returncode=-2，300.365 秒，245 个
监测文件未变，counts=null。它是部分回归，不能记为 PASS 或测试内部超时。
新增四项经验回归首轮 17/18 通过；唯一失败是测试误把清理前置检查当成
拒绝全部草稿的提交入口，改为实际 `submit_text` 零输入检查，产品规则未放宽。
纠正后 **18/18 定向回归通过，1.384 秒**。最新完整回归使用独立目录
`validation/r8-pressure-and-full/default-footer-tip-full-01/`，已收取通过，详见本节开头。

r11 三客户端均已 ADOPTED，原 Codex 唯一 start 成功，UUID 大小写修复通过
真实启动。双 Claude 在默认状态栏布局下被旧 parser 误判为 COMPOSE_OCCUPIED，
两项 preflight 均失败：token_sent=false、clear_attempts=0、clear_key_count=0。
旧 parser 要求 model 状态行，而实际完整边框后只有默认权限提示。原 driver
于 03:17:16Z 生成 HALTED/FAILED 最终报告并 exit 2；pilot 0/3、pairs 0/15、
closures 0/33，原三会话、原控制器、失败及全部注册分母保留。

只读捕获的两份原始屏幕位于 `validation/r11-default-footer-live-01/`，
03:15:47Z，input_operations=0；对应 SHA 为
`e512b4961023f38ec5c8f2330699ba50dfbbd69cdb588d903fbc464ca366e983`、
`3231def4788525f486985c5d2ed4e612db59d27af69c3af5abcdcf6992f946c7`。
工作源码仅增加完整匹配边框后唯一精确默认权限提示的解析分支。草稿、未知/
截断页脚、排队和活跃回合仍由原门禁处理，未热改冻结 controller。

新增回归覆盖原始实屏、派生草稿/活动/队列/边框负例、正式 bridge-test 和
submit_text 入口，以及精确原生回执和缺失/改写/队列回执。首轮两个失败是
测试把普通消息允许首次排队的行为错当成握手探针禁止输入；已改为核验实际
握手入口，产品发送规则未修改。修复后的最终回归结果另行记载，旧版本
1,781 项全测不能直接作为这次修复后的通过证据。

## r10 身份大小写与完整生命周期修复

r10 三客户端已于 01:43:07Z 全部 ADOPTED；01:48:08Z 原 Codex 的唯一
start 因 `identity/process/transcript changed` 被拒绝，业务未派发。
仍为 pilot 0/3、pairs 0/15、closures 0/33，不计通过。
三客户端真实内核/原生日志只读诊断证明：进程、会话、TTY、日志文件身份及
私有配置根均相同，仅 workspace/surface/pane UUID 大小写不同。
证据：`validation/r10-identity-live-01/result.json`，input_operations=0。

r10 已于 02:10:47Z 按原 1,800 秒单轮 watchdog HALTED/TIMEOUT，原 driver
exit 2，02:11:17Z 生成最终报告；原会话、controller 和唯一 start 保留。

首批 live UUID 和单轮未启动状态修复已通过 **1,774/1,774** 完整回归，
0 failures/errors/skips，732.507 秒，243 个监测文件未变。随后自审继续修复
campaign 与 native handshake/dispatch/callback、pack/gate/scaffold、disarm
之间的 UUID 比较。日志使用原 guard 大写 caller 定位；释放保留原 marker 路径
和 caller 拼写，不改原证据、fence、内容哈希或大小写敏感的 reservation key。
v2 保持原严格身份比较；v3 的非 UUID 字段仍严格匹配。

本轮 **69/69 定向回归通过，158.494 秒**，包含两个 Claude 并行派发、真实
producer 形态的原生证据、回调验收、释放和续派的五节点离线生命周期，以及
身份替换、原 fence 改动、marker 替换、非 UUID 字段改变等负例。首轮新测试
fixture 使用固定 nonce 导致日志重叠的失败保留，已改为每任务独立 nonce。
本轮新增边界修复的完整回归已通过 **1,781/1,781**，0 failures/errors/skips，
执行器总耗时 1,157.603 秒（suite 1,152.986 秒），244 个监测文件测试前后
SHA 完全一致。原工具进程 exit 0，未修改内部超时，未重复启动测试。
证据：`validation/r8-pressure-and-full/uuid-lifecycle-full-01/result.json`；
完整日志 SHA256 为 `f9d7062284bc7c7169ee8d0bea09a3dbe8968164b2fb37f74d2fa107adc1f1b0`。
以上均为离线验证，live_model_requests=0；本段实际结果在测试结束后更新，
作为单独记录的文档变动，不声称它参与了此前全测。
用户的全面授权持续有效；完整回归后继续独立冻结实验，无须重复确认。

## 新版 Claude 注册兼容修复（08:58 更新）

r9 已按原 900 秒启动 watchdog 收口，保留三个原会话及全部结果：pilot 0/3、
pairs 0/15、closures 0/33。两只 Claude 已执行 readiness；阻断原因是
Claude Code 2.1.170 的原生注册提供 `peerProtocol=1/startedAt`，不再提供
旧 `procStart`，原控制器因此返回 `NATIVE_REGISTRATION_PROCESS_MISMATCH`。
这不是 key 不可用或没有找到双 Claude。

修复同时进入原生读取器和私有 controller materialization。保留 legacy
`procStart` 的严格匹配；新格式必须满足整数 PID、协议、入口、毫秒时间戳及
真实内核进程生命周期，并重新读取进程以拒绝 PID 重用或身份变化。无效旧字段
不能通过新字段回退。没有改写原生注册、在途 controller 或共享 MAC。

- 完整回归：**1,768/1,768，零 failures/errors/skips，771.515 秒**；243 个
  监测文件 SHA 不变，未放宽内部超时。新增 5 项原生正反例和 1 项私有
  materialized controller 集成均包含在内。
- 两只实际 Claude（PID 3230/3209）的原生注册和内核身份通过修复 helper
  的只读验证，零输入、无 fixtures。该验证仅证明注册兼容，不冒充完整 adoption。
- 组件竞争压力涉及的两个文件未变，保留已通过的 7,200 reserve 压力结果。
- 集成测试首轮 fixture 缺少 executable/HOME 的失败保留；只修正 fixture，
  没有放宽产品身份检查。

证据：`validation/r8-pressure-and-full/claude-registration-full-01/`、
`validation/claude-registration-live-01/result.json`、
`execution/r9-original-driver/result.json`。r9 原报告为
`/Users/lzhs/u9/driver/final-report.json`；源码修复后的完整业务压力尚未完成。
本节在全测完成后更新，文档变化与经过测试的可执行源码分别固定。

用户已全面授权继续全量测试并要求不重复询问。修复后的独立实验沿用该明确授权，
保留旧会话、旧控制器、失败分母和原授权；精确固定新路径后执行同三会话
pilot 3/3，再运行 12 warm＋3 cold pairs。不将旧 r9 的失败替换为新成绩。

## 已完成的验证

| 验证层 | 实际规模与结果 | 能证明的范围 |
| --- | --- | --- |
| 进程竞争压力 | 24 进程 × 100 轮 × 3 模式；7,200 次 reserve；PASS | 同会话、重叠范围、独立范围的文件系统租约竞争 |
| 独立任务吞吐 | 2,400/2,400 成功，registry busy/timeout 0 | 公共登记锁不再把这批独立任务错误拒绝 |
| 冲突与释放 | 每个冲突轮恰有 1/24 获权；31,200 次终态 release 检查；重复 owner、历史丢失、意外错误均 0 | 互斥、非法释放拒绝、幂等释放和历史保留 |
| 身份修复后完整回归 | 1,762/1,762；0 failures/errors/skips；767.351 秒 | Python 3.14、本机离线 suite；242 个监测文件测试前后 SHA 相同 |
| 身份修复前基线回归 | 1,743/1,743；0 failures/errors/skips；326.637 秒 | 保留原结果；不能代替身份修复后的验证 |
| 独立 Codex 身份覆盖 | 18 项正反例＋1 项真实 materialized controller 集成，均包含在 1,762 项中 | collector、hook、public proof、resolve；内核/foreground 使用 fixtures，不是活跃客户端采用证明 |
| 原生失败定向回归 | 25/25；2.032 秒 | 原生日志失败分类、增量观察、崩溃复用、watchdog、完整分母 |
| r8 原日志只读观察 | 一次 observe 找到原 turn 的终态错误；input_operations=0 | 新 detector 能识别已存在的真实失败，不等于重跑 r8 |
| r8 三客户端启动 | 1 Codex＋2 Claude 的 readiness、自动 Pre/Post hooks、adoption 全部通过 | 三个真实客户端启动并采用私有配置 |
| r8 业务实验 | HALTED/FAILED；pilot 0/3、pairs 0/15、closures 0/33，业务派发 0 | 提效、真实 callback 和业务验收尚未证明 |

压力测试耗时 821.931 秒，reserve 操作延迟中位数 0.04084 秒、P95 0.19112 秒。
该分位数来自组件样本，不是模型响应或端到端协作延迟。压力和本地回归均
`live_model_requests=0`，不能替代真实配对实验。报告落盘在完整回归之后；
最终源码复核应单列后续文档更新，不能声称这些新增文档参与了此前全测。

## 实测推动的修复

1. **登记锁竞争。** 首轮压力只检查安全性，独立任务仅 389/2,400 成功，
   2,011 次 registry busy；其 PASS 标记已另发更正，原件不改。
   现在仅 reserve/release 的短事务可等待 OS 锁最多 5 秒，获锁后复核原
   path/device/inode/mode/uid。action 锁仍立即拒绝；不会重试派发或 callback。
2. **Codex 自动 hook 关联。** r7 把外层 `functions.exec` call_id 与内层
   `CommandExecution` hook id 直接比较，导致已有真实 hooks 被拒绝。
   新代码只在同 thread/turn、唯一精确命令、原 call/result 区间和成功输出
   同时成立时接受关联；拒绝歧义、跨 turn、失败输出及重复调用。
3. **原生错误及时收口。** 新 driver 通过 `upgrade_failure_watch.py` 观察冻结
   后的原 Codex 日志，每 tick 最多 256 条。原生 `event_msg/task_complete`
   的非空 error 才触发 FAILED；旧错误、引用和工具错误不触发。
   未读完或末尾半条记录时禁止推进，原 1,800 秒 watchdog 仍有效。
   首份错误证据独占落盘并在崩溃后精确复用，保留已完成测量和全部失败分母。
4. **独立 Codex 身份。** 新私有 materialization 在没有 managed daemon 时，
   认证唯一真实原生 Codex 祖先；交叉核验当前 PID/parent、内核 selector、
   workspace/surface UUID、TTY 和原生 foreground，并重读进程链及 foreground。
   缺少 foreground 不回退到 resume argv；子线程不能借用 root inbox。
   `collect`、`collect_hook`、public proof 和 `resolve` 均接入该路径，managed
   daemon 原分支保留。修复不会改写旧 controller、旧 hooks/config 或共享 MAC。

r8 已经发布 freeze/bootstrap，原 Codex 已读取 bootstrap，随后在
`2026-10-17T19:48:21.105Z` 报 HTTP 400 `invalid_encrypted_content`。
此错误不能单独确定鉴权、服务负载或服务端根因。readiness 成功没有证明后续
多轮请求兼容。原冻结 v7 已据原生证据收口；未使用新 detector 重启、替换或
重放 r8。新修复尚无新的真实 campaign 使用证明。

后续只读核实纠正了当前阻断：同一原 Codex 在收到用户“请继续”后，于
`2026-10-17T20:01:02.433Z` 完成新 turn
`01a14b6f-018d-79f1-a8f8-f0299c59d086`，终态没有 error。该回合实际执行
`start --run pilot-01` 时被拒绝：`current native supervisor process proof is unresolved`。
实测父链为工具代理 → 非 managed app-server → 原生 Codex → shell/login。
原控制器只支持 managed-daemon 身份，因此返回 None。本地缺陷已修复并完成全测；
不再把旧 400 当作原会话仍无法工作的证据。旧 r8 HALTED 和全部分母保持不变。
旧进程仍固定加载原 runtime/config/native roots；bootstrap 不允许替换为不同内容。
当前没有已验证的同会话切换到新 controller 的路径，不能以新源码或离线集成
冒充原进程采用。新候选准备与客户端启动分开；不扩大原 r8 的一次实验授权。

## 经验会话与论文工作流

用户指定的经验会话为 workspace
`6FB20312-9E66-43DB-AF09-92C3B18E1296`、surface
`A93448E1-E329-493D-9B45-568D5FC0DAD0`。
已创建询问 `UPGRADE_EXPERIENCE_20261018_R8_01`，并在其既有 maintenance inbox
提供诊断补充。原生发送尝试因缺少控制 socket 在输入前结束；未粘贴或补键。
截至本报告，三个指定回件路径均不存在，也未观察到其原生日志消费 marker/SHA。
**文件可用、对方已读、对方回复是三件事；双向咨询尚未完成。**

实际自动 hook audit 显示该独立 Codex 没有 managed-daemon 父进程；现役
0.4.34 的 `collect_hook` 因此不产生 native proof，而 inbox discovery 需要该
proof。这是实际父链、hook audit 与源码路径诊断，不是对方阅读回执。
隔离维护副本的修复未安装，没有改变共享 hooks 或原会话。

从该会话已公开的维护记录与论文工作区既有经验吸收并写入 skill/runbook：

- 文件请求、hook 曝光、正文 SHA 消费、原生接收、callback 与业务验收分别留证。
- Word/Zotero 保持单写者；NotebookLM 共享按账号判断；锁和租约不是验收回执。
- 小 Word fixture 成功不能替代整章规模测试；保留超时、未测项与失败分母。
- 自动 workflow hooks 只观察，不以历史 callback/reask 状态锁死普通工具或 Stop。

这些规则已落实到 upgrade 文档和相关竞争/收口检查；本次没有操作论文文件、
Word/Zotero 或 NotebookLM，也未声称已经完成这些领域的真实负载测试。

## 未完成项及继续条件

1. r11因默认Claude页脚误判而FAILED/HALTED，旧失败分母保留。修复已通过
   1,804项完整回归，v11已冻结，r12真实三客户端全部ADOPTED，pilot-01
   唯一正式start已成功。继续原driver；不重复授权、不重放启动或派发。
2. 在可用且授权的真实实验中，同三会话 pilot 连续 3/3，然后完成 12 warm＋3
   cold pairs；最多 7 组、33 slots、同时最多两个 Claude 执行业务。
3. 逐轮验证原生投递、真实 callback、独立验收、释放及自动续派。正确性通过后，
   warm 配对改善中位数需至少 15%，各 workload 中位退化不超过 5%；成本增长
   超过 10% 披露，缺失 usage 记 UNKNOWN。不得用组件提速代替这些门槛。
4. 获取指定经验会话的实际回复，核对其案例、失败条件和证据，再做相应实地验证。
   当前消息入口不可用不应阻止独立修复，也不允许声称沟通已经完成。

## 可复核证据

本地证据根：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。
以下相对路径均基于该根；原生记录与凭据不纳入公开仓库。

| 证据 | 路径 |
| --- | --- |
| 最新完整回归、逐项 events、源码前后 pins | `validation/r8-pressure-and-full/materialized-chrome-full-03/` |
| 修复前完整回归 | `validation/r8-pressure-and-full/repair-full-04/` |
| 修复后完整压力及原压力更正 | `validation/r8-pressure-and-full/multiprocess-stress-02/result.json`、`validation/r8-pressure-and-full/multiprocess-stress-01-correction.json` |
| 压力执行脚本 | `validation/r8-pressure-and-full/stress_components_v2.py` |
| r8 原生错误与只读新 detector 验证 | `validation/r8-native-provider-failure/terminal-error-01.json`、`validation/r8-native-provider-failure/readonly-watch-02.json` |
| 定向回归 | `validation/r8-native-provider-failure/focused-02/` |
| 经验询问诊断 | `maintenance/standalone-caller-v1/hook-discovery-diagnosis-02.json` |
| 经验询问补充与最新观察 | `maintenance/standalone-caller-v1/diagnosis-file-supplement-02.json`、`maintenance/standalone-caller-v1/request-observation-26.json` |

最近已结束实验的最终报告：`/Users/lzhs/ub/driver/final-report.json`；原 halt：
`/Users/lzhs/ub/schedule/halt.json`。r11已FAILED/HALTED；没有真实提效通过结论。
最新全测日志 SHA256：`1afe54174535c7ed2b01d9c65ffcaeb8691d0ca9c9920ca2e4d02ae824ef5b87`。

历史失败也保留：原全测 1,500.37 秒超时、57E 且无最终 traceback，根因未确定；
失败模块复核 100/100 通过不抹除原失败。失败收口新增测试曾因 fixture 缺字段
出现 1E，修正 fixture 后 25/25 通过。r7 的原日志复核不改变其 pilot 0/3。
身份集成首轮曾因测试子进程没有进入 Seatbelt 而失败；修正测试启动器后纳入
本轮全测通过，`KERNEL_WRITE_BOUNDARY_UNVERIFIED` 检查未放宽。完整回归结束后
才更新本文的结果，后续文档变动单独记录，不声称参与了此前测试。

复现本地完整回归：`python -B scripts/test_all.py`。本次没有放宽测试内部超时、
跳过测试或安装共享 MAC。GitHub Actions 与真实活跃客户端采用分别报告。
