# r26：原生 Goal 全矩阵实测

2026-10-19 13:38（Asia/Taipei）检查点：七组准备与独立审查完成，pilot 的
1 Codex＋2 Claude 已全部 ADOPTED，双 Claude 真实原生 Goal SET 已出现；
原 bootstrap 已发布，正在进入首轮 pilot。业务 **0/33**，
pilot **0/3**，可比较配对 **0/15**；尚未证明全量压力通过或协作提效。

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
| 连续 Goal 续开 | 尚未验收；SET 不能替代续开证据 |
| 业务／pilot／配对 | 0/33；0/3；0/15 |
| 192 条普通通信 | 尚未验收；notice 接收、正文读取和业务接受分别统计 |
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
