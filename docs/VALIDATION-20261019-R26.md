# r26：原生 Goal 全矩阵实测

2026-10-19 13:53（Asia/Taipei）终态：r26 driver 于 **05:43:39.490338Z**
HALTED / FAILED，watchdog 于 **05:43:40.491174Z** HALTED；原工具 78801
已退出（code 2）。业务 **0/33**、pilot **0/3**、可比较配对 **0/15**；
1 FAILED＋32 MISSING，192 条普通通信全部 MISSING。首轮两个握手在发送前
拒绝，没有正式业务派发，其余六组没有启动。未证明全量压力或协作提效。

`all_stopped=true` 仅证明停止后续调度，不能推出客户端停止、远端请求取消
或 lease 释放。旧客户端、原控制器、失败与完整分母全部保留。

## 本轮失败与修复证据

两个原 prepare 的拒绝均为 `READONLY_HANDSHAKE_CAPABILITY_REFUSED:
HANDSHAKE_CAPABILITY_DRAFT_OR_UNKNOWN_COMPOSER`，`dispatch_submitted_at=null`、
`executor_ack=false`。原失败分支没有保存 screen，**不能断言原次由 Goal
页脚误判引起**，也不能拿后来截屏补成原次证据。

05:45:40–41Z 读取原双 Claude UUID 的后来实屏，在原 control.sb 执行冻结
parser，均复现完整边框＋空 `❯ NBSP` 输入框被解析为 null。实际页脚是
`⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt    ◎ /goal active (13m)`，
旧白名单没有覆盖该完整组合。新修复只增加这个已观测结构的整行匹配；
正文和用户草稿不作为 chrome，未知后缀、截断或缺失边框仍拒绝。
Goal 页脚本身不证明任务活动或原生 Goal 连续续开。

本轮同时新增拒绝诊断：精确 screen／SHA256、原解析结果、读序号、拒绝阶段
和原异常；读取失败保存失败 slot，不补读、不合成屏幕。实际生成 harness
将诊断写入原拒绝 receipt，保留空 precondition、零 submit 和禁止重复执行。
成功 proof、nonce、root lock、原生 receipt fence 和单次发送不变。

独立实屏／变异负例审查：仓库 parser 和 pinned base 的实际变换结果各
28/28。相关原回归 34 项及新增生成 receipt 回归通过；新增测试首次因误把
reconciler 的可选字段当成原 receipt 必有字段而失败，修正测试断言后通过，
未修改运行时重试规则。全仓回归结果另存 `maintenance/r26-goal-repair`。
这些是本机验证，不计入正式压力分母。

唯一完整回归于 06:10:44Z 结束：**2,094/2,094，零失败、零错误**，
原进程 exit 0；05:52:27Z 开始，约 1,097 秒，所有监测源码前后 SHA 一致。
原工具 41948 已收取，不再重跑。证据 `maintenance/r26-goal-repair/full-regression.json`；
stdout SHA256 `dd7694119f18a98e905c5b40e29238716b03579b1939f6ca0752b18c9b1e4251`，
stderr SHA256 `0bbf5acd1978bef0a9d7601ba6a75f762050440370e6389bc656f48f7ed419bc`。
本段为测试结束后的结果文档，不追称文档文字本身已被该次测试覆盖。

原生日志还包含独立的 **502 upstream unavailable** 与 **429 group RPM limit**，
不是 401/403 或 key 无效。05:50:02Z 独立观察已核实错误后的间歇真实完成：
A 最近成功 05:48:56.215Z，B 最近成功 05:48:51.695Z；随后均在 05:48:56Z
再次 429，当时最新错误后尚无成功。部分恢复不证明持续容量。

05:53:57–59Z 更新观察确认最新 429 后双方均有真实成功工具结果：
A 为 05:50:51.754Z，B 为 05:51:24.521Z；截至该边界没有更晚 API error。
A 于 05:51:41.638Z、B 于 05:51:43.591Z 明确识别 stop 并以原生
`stop_reason=end_turn` 结束当前 turn。仍无 Goal MET／CLEAR／FAILED，
因此模型 turn 结束与 Goal 原生终态分开记录，后者继续 UNKNOWN。
证据：`maintenance/r26-goal-audit/READOUT-0554.md`，及
`provider-completion-20261019T055359608229Z.json`（SHA256
`2e2ba891f88b92f1ba12a547c4be4049e9fcde5a304a2aa7fb7ac89528bbdf56`）。
这支持一次已授权的新独立观察，不能重放 r26 或证明持续容量。

原终态证据：`/Users/lzhs/uq/driver/final-report.json`，SHA256
`a722b6c8748ca949c59793ff663bf8a2bc66efd69781bbed958d09f6fb83ec92`；
`execution/r26-original-driver/result.json`，SHA256
`e937d1fe8657d58a12ff82deac52afa2a737bb31cf2e87890a13ccb0eec90888`。
后来实屏、供应端完成关联及独立结论分别见证据根下
`maintenance/r26-goal-audit/current-screen-20261019T054540490570Z/summary.json`、
`maintenance/r26-goal-audit/provider-completion-20261019T055002169757Z.json`、
`maintenance/r26-goal-audit/READOUT-0550.md`。
完整分母／阶段耗时审计见 `maintenance/r25-driver-goal-audit/R26-TERMINAL-AUDIT.md`。

## 固定范围与时限

- 原实验范围：7 组、33 轮、15 对（12 warm／3 cold）、192 条独立普通通信。
- 先连续通过 3 轮 pilot，再开始配对。A1 和 B 均允许双 Claude 并行。
- 用户已全面授权重试；旧会话、旧 controller、旧输入及失败证据全部保留。
- r26 根 `/Users/lzhs/uq`；05:31:35.737033Z 配置，05:31:52.294650Z 原 launch 返回。
- 原任务最晚截止为 06:47:39.605462Z；r26 实际不可变截止为
  **06:47:01.737033Z（台北 14:47:01）**，不增加两小时或重置计时。
- watchdog PID 5206，driver PID 5568；原 wrapper 工具句柄 78801。
- 缺失、排队、拒绝、失败保留原分母，不补样或重放。

## 已交付修复与启动核验

实现提交 `560598e`，冻结 `resume-20261019-live-v22`，330 个文件逐字节等于
提交。只推独立 upgrade 仓库，未修改共享 MAC／论文精炼助手或全局 hooks。

1. 修复 r25 原验收的 `UPGRADE_MIXED_CONTROLLER_IMPORT:render_cmux_agent`：
   以同目录显式路径和独立模块名加载纯 renderer，不修改 `sys.modules`，
   保留混用控制器拒绝。36 项回归通过，旧源码负对照复现原错误。
2. startup-v3 固定三 lane 职责和双 Claude 精确原生 `/goal` condition；
   adoption 要求唯一、准确的原生日志 SET、真实 probe 和自动 hooks。
   旧 startup-v2 行为和原 bootstrap／正式 ACK／callback 保留。
3. 两臂均可并行；Goal condition 覆盖所在组全部注册 run，不能因单项完成
   或暂时没有派发而提前结束。普通消息不增加 ACK，不影响正式任务 callback。

独立候选审查：7/7 runtime、21/21 职责 pins、14/14 Goal argv、42 个
controller 转换文件、14 条唯一且未占用的 socket/lock 全部通过。另一独立
审查验证每组 20 个私有 support 模块与冻结源码按原 materializer 转换一致。

Goal 启动回归共解决 63 个 distinct cases：扩展原始批次 62 通过、1 失败；
唯一失败是测试未在 Seatbelt 环境导入私有 bootstrap。修正测试运行方式后，
该单项在真实 control.sb 下通过，没有放宽生产门槛，也没有重新运行整批。
上述均是本机开发与启动准备证据，不纳入正式实测通过数。

## 验收项目

| 项目 | 当前证据 |
| --- | --- |
| 七组候选与隔离源码核验 | PASS 7/7 |
| pilot 三个原客户端启动 | launch 返回成功 |
| 原生 Goal SET | 双 Claude 2/2，原 session／offset／fence 已独立核收 |
| probe／自动 hooks | 05:35:30Z 三客户端全部 ADOPTED；双 Claude 独立日志核收通过 |
| 原 bootstrap | 05:36:06Z 发布 |
| 连续 Goal 续开 | 各 SET 1、UNMET 0；连续性与终态 UNKNOWN |
| 业务／pilot／配对 | 0/33；0/3；0/15 |
| 192 条普通通信 | 全部 MISSING；notice 接收、正文读取和业务接受分别统计 |
| 提效与成本 | INSUFFICIENT_EVIDENCE／UNKNOWN |

surface40 继续统一管理共享技能。已向既有文件收件箱发布 r26 新进展，沿用
原 Goal 经验回复约定；尚未收到回复，发布和读回文件不能证明 owner 已读。
该沟通与实测并行，不另设停工门槛。

原生日志初步结果：A 于 05:32:04.714Z、B 于 05:32:05.252Z 写入 SET，
两者原始 offset 均为 4476。B 于 05:33:48.097Z 执行 exact probe，
05:33:52.484Z 成功。A 后续一次 `Read {}` 在 05:34:51.548Z 返回
`InputValidationError: required file_path missing`，随后自行完成 probe 并通过 adoption。
这是模型工具参数错误，不能归因于 key 或原生 Goal 激活失败。
13:35 初步观察时尚无 CLEAR／MET／UNMET 记录，不能声称已验证持续续开。
两个 Claude 均成功读取私有 SKILL；kernel.denied 检测的是禁止写，不是禁止读。

05:36:35Z 独立 Goal 观察重新验证了原生 SET、exact probe call/result、
model record 和自动 Pre/Post hooks。A 的区间包含 61 records／182203 bytes，
B 包含 84 records／235451 bytes，均完成该区间扫描；各 SET 1、UNMET 0。
`continuous_goal_proven=false`，`state_at_interval_end=UNKNOWN`。
该状态观察没有验收通信或业务，其余六组尚未启动。

## 原始证据

证据根 `/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`：

- `preparation/r26-candidate/candidate.json`，SHA256
  `2275998d0339ea64e7fb0d8c4c92f89897b67d56df00ce6fd1fd1057a637a5ae`。
- `authorization/r26-20261019/driver-request.json`，SHA256
  `47c84cf5b713e2fd6faccca1b6020453b839e590faea4cd986ff69d276023c7f`。
- `authorization/r26-20261019/configuration-result.json`：唯一配置及真实 deadline。
- `execution/r26-original-driver/intent.json`、`watchdog-ready.json`、`stdout.jsonl`。
- `maintenance/r25-driver-goal-audit/r26-independent-pins-review.json`。
- `maintenance/r26-goal-audit/startup-audit.json`。
- `maintenance/r26-goal-audit/pre-adoption-053506729604.json`：真实 Goal SET、
  probe 和 A 的工具参数错误，未伪造 adoption。
- `maintenance/r26-goal-audit/observation-20261019T053635541777Z/summary.json`：
  双 Claude 原生 Goal／probe／自动 hooks 独立核收。
- `maintenance/r26-goal-run/publication.json`：surface40 文件发布，非已读回执。
- `maintenance/r25-independent-audit/goal-startup-implementation-validation.json`。
- `source-exports/resume-20261019-live-v22/export.json`、`observer-regression.json`。

终态必须更新本报告并保留本次所有失败和缺失；不由配置就绪、本机回归或
Goal SET 推断全量压力或效率通过。
