# r22：真实首轮超时，候选修复与本机压力结果

原实验终态记录于 2026-10-19 02:39（Asia/Taipei），后续诊断见下文。原实验已终止：**1 TIMEOUT＋32 MISSING**，
业务 **0/33**、pilot **0/3**、pairs **0/15**；提效 **INSUFFICIENT_EVIDENCE**，成本 UNKNOWN。
本机优化及回归已提交，不构成全量真实压力通过。连续 `/goal` 通信仍未验证。
r21 保持原终态：1 FAILED＋32 MISSING，业务 0/33、pilot 0/3、pairs 0/15。
r22 使用独立根 `/Users/lzhs/um` 和冻结 v20；不修改 r21 控制器、原输入或失败结果。

## 本次变更

新私有客户端获得固定 `mac_upgrade_body_read.py --notice` 命令。它验证通知中的
路径、完整字节数和 SHA，返回原正文，不发送通知或 ACK。生成控制器先安装
私有运行范围再导入模块，读取指令写入工作目录及客户端策略文件。

独立 collector 验证原接收会话、journal 的设备/inode、原 input-intent fence，
以及唯一、完整、成功的原生工具 call/result 配对。Codex 的直接 exec_command、
已测量 functions.exec 包装和 Claude 前台 Bash 格式各自匹配；重复 ID、重复 JSON
键、非有限值、错误、后台/yield、截断或未支持格式均不产生正文读取成功。

`BODY_READ_OBSERVED` 只表示完整正文暴露在接收端工具上下文中。它不证明理解、
任务接受或 native notice 收取；排队通知与正文暴露可同时存在。collector 保留
192 条预注册通信槽及其缺失、拒绝、排队状态，不发送额外消息补样。
扫描默认每样本 8 MiB、总计 256 MiB，单记录 8 MiB、保留证据 32 MiB；覆盖不足
报告 UNKNOWN 和偏移。它不重验整棵 controller 依赖树，也不重新认证 live 进程，
不能独立代替运行完整性检查。收尾前复核支持判定的原始记录字节。

## 已完成的本机验证

| 验证 | 结果 | 范围 |
| --- | --- | --- |
| 最终受影响回归 | 109/109 PASS，42.96 秒，无失败/错误/跳过 | identity、握手、真实生成控制器与 Darwin sandbox、reader、通信计划及 observer |
| 实际 r22 冻结 reader 并发压力 | 8 workers、800 次 subprocess，20.73 秒 | 400 次完整正文读取、400 次篡改字节数预期拒绝 |
| 七组实际控制器核验 | 7/7 | 完整转换、reader 引导、客户端指令及 14 条 socket/lock 路径 |

以上均没有模型请求或 native 输入，不能计作三模型压力成绩。109 项覆盖此前
部分测试，不把重叠批次相加。独立审查未发现新增冻结阻断项。
800 次并发检查使用实际 pilot-B reader 的 8 份 49–105 字节正文重复执行；
这是隔离环境下的读/拒绝压力，不是大正文或真实模型吞吐测试。

## 原真实实验的终态

沿用用户明确全面授权，配置于 01:55:09 固定 7200 秒上限，截止 **03:55:09**。
独立 watchdog 在 01:55:21 记录 ready 后才启动原 driver。客户端启动子调用
returncode=0；原 driver 之后按单轮上限终止。
三客户端已由原 controller 核验 ADOPTED；双 Claude 的原握手均 PASS/ACKED。
pilot-01 于 02:08:01.784 开始，单轮固定截止 02:38:01.784。截至 02:33，两节点
`inventory`、`source_index` 均 DISPATCHED；两份原 receipt 均记录 `NATIVE_RECEIVED`、
`retries=0`，包含对应接收者的原生 user record、offset/length/SHA。尚无完成回调、
独立业务验收或 run finish。
初次握手 capability 的 `active_or_queued=false`，不能据此宣称连续 `/goal` 验证通过。
早前 Claude A 的 429 是 concurrency/RPM 记录，之后 readiness 与握手通过；不归类为无效 key。

顺序保持连续 3 轮 pilot，之后 12 warm＋3 cold 配对，共 7 组、33 轮、15 对。
另注册 192 条普通消息，两臂各 96 条、四方向各 48 条。不暂停原生 `/goal`。
正确性通过后才评估 warm 中位改善至少 15%、各 workload 中位退化不超过 5%；
缺失 usage 或成本保留 UNKNOWN。当前提效结论仍为 **INSUFFICIENT_EVIDENCE**。

02:38:23.529 原 driver 发布 TIMEOUT：1800 秒内没有 complete closeout；02:38:24
watchdog 完成七组 `STOPPED_INPUT`，02:38:27 原进程报告终态。采样终止比单轮截止
晚约 22 秒，未重设时限。driver/watchdog 退出码均为 2，watchdog_failed=false；
这是实验超时，不是 watchdog 崩溃。33 条结果仅观察到首轮，0 measured/verified/passing。
原调用不重放；失败未替换客户端。后续调度与输入已停，保留在途请求与原会话，
stop 标记不证明远端取消、客户端退出或 lease 释放。

02:39 使用原冻结 v20 observer 收尾：192 个普通消息槽全部 `attempt=MISSING`，
native reception/body read 均 UNKNOWN，business acceptance 为 NOT_ASSESSED。
没有进入 warm 阶段；缺失消息不算 192 次通信失败或成功。observer 原生读取 0 字节，
无输入或 runtime 写入。新工作树的归因修复没有用于改写这些原结果。

原始日志归因到两条正式通知之后：Claude A、B 各有 10 条 API error 记录，包含
429 concurrency 限制。A 尚无正式通知后的工具调用，B 已多次调用读取工具。
整个采集前缀内 A 有 21、B 有 24 条 API error；这些是日志记录数，不是唯一上游请求数。
原 notice 时间 A 为 02:23:58.218、B 为 02:25:55.817，距 pilot 开始已分别约 16/18 分钟。
因此上游限流、初始准备/握手与控制器耗时需要分开；不能将完整超时都归因于 key、
provider 或 busy 门禁。此次没有 key 无效证据，也没有双向 continuous-goal 通过证据。

## 运行期间发现的后续优化

以下仅在工作树验证，不热改 r22 的冻结 v20 或原客户端。

只读 `validate_campaign` profiling 共 17.281 秒（含分析开销）：5 次
`validate_live_runtime` 累计 16.232 秒、20 次 `sandbox_profile` 累计 4.460 秒。
重复完整树核验与 startup 校验也有成本，不能把全部等待归因于 profile 生成。

新的 profile 生成器每次仍重新执行所有 `checked_path`，仅复用本次调用中的词法
父目录关系。28 份实际配置与 1,004 个随机/拒绝用例的新旧输出或拒绝结果完全一致；
实际配置的原始 profile 前缀也逐字节匹配，外部 socket 规则仍由独立边界追加。
交替执行 12 次，28 份配置的生成耗时中位数从 2.881 秒降至 0.281 秒，降低 90.24%。
这是本机配置生成性能，不是模型吞吐、完整控制器或端到端协作改善。

受影响内核/socket/启动回归 49 项中 48 项通过，1 项在实际全系统进程枚举中遇到
`EPERM`（pid 10076，`before_path_bsd_1`），原失败保留；之后该 PID 已不存在。
不放宽身份门禁。对同一失败测试，分别用冻结旧生成器与新生成器各执行一次，两次
均通过（含有/无 controlling TTY、真实 sandbox 和外部写拒绝）。这两个新观察不抹去
首轮失败，也不证明该环境错误以后不会出现。新增回归覆盖再次调用时路径变成
symlink 的拒绝，以及嵌套 writable 根、Unicode 路径和 immutable 父目录保护。

另发现冻结 v20 的 ordinary-message 归因缺口：预注册 `COMM_SAMPLE` 原生通知仍会
被 `orchestration_verify` 当成未分类输入，令 `human_interventions=null`，可能阻断
后续 warm schedule。另有真实生产格式差异：helper intent/正文头使用小写 UUID，
bridge journal/native binding 保留大写。修复需要保留原始哈希字节、按 UUID 语义
核对身份，并在新 metrics 版本中只豁免唯一原始记录；不能按文本前缀豁免全部输入。
修复已完成：新冻结默认 metrics version 3，旧 version 2 定义、输出结构及 r22 结论
不改。要求唯一 sender helper call/call ID、完整 intent/正文、原 receiver binding、
input fence 和完整 native 区间。大小写 UUID 比较只改变身份比较，不重写原字节；
若两个大小写派生目录同时存在则拒绝。任意混合大小写 journal 尚未支持，保持 UNKNOWN。

归因/observer 专项 39/39 PASS；生命周期及兼容集成 97/97 PASS（369.917 秒）。
新增 freeze 断言后，同一实际完整冻结测试再执行一次通过，确认新口径及模块复制；
不把这一重复 case 加入独立测试数。专项用真实 Adapter 和 native binding producer，
平台发现边界用 fixture；未在真实客户端执行。4 进程、512 样本、1,024 次归因校验
均符合预期（4.886 秒）：256 合法，重复通知、重复 call ID、空 binding、变化 intent
各 64；封口后追加不改变旧区间，扩大区间必须识别重复。均为零模型、零 live 输入。

## 原始延迟拆分与后续校验优化

只读核验原生 journal 设备/inode、固定前缀、唯一完整 prepare/dispatch 工具脚本和
正式通知原生记录后，得到以下原实验墙钟区间。单位为秒；列间不能相加为因果占比。

| 区间 | inventory / Claude A | source_index / Claude B |
| --- | ---: | ---: |
| run start → prepare 调用 | 222.808 | 296.624 |
| prepare 调用 → preflight intent | 40.163 | 38.130 |
| preflight | 379.784 | 328.508 |
| preflight 结束 → task-notice 生成结束 | 20.172 | 21.264 |
| task-notice 生成结束 → dispatch 调用 | 127.670 | 140.392 |
| dispatch 调用 → submit intent | 133.329 | 199.739 |
| submit intent → native notice | 32.509 | 49.377 |
| 完整 submit operation | 59.725 | 73.080 |
| run start → native notice | 956.434 | 1074.033 |

这些区间包含模型调度、控制验证、握手 ACK 等待和锁等待。原实验没有逐段 CPU/锁
分析，二者份额仍 UNKNOWN，不能把完整 16/18 分钟归因于一个校验函数或上游限流。
审计使用真实工具脚本的 `yield_time_ms=1000, max_output_tokens=8000`，没有拿默认
生成脚本代替已发生输入。读取上限 16 MiB，零 input/runtime 写入；历史 fixture
reader 仅用于核验原字节，不能作为新的 live 身份授权。

02:45:29–02:45:36，Claude B 的原 callback 首次尝试留下
`phase=NO_INPUT, events=[], error=EXPERIMENT_STOP_REQUESTED`。它发生在终止之后，
不是补发、收件或业务验收，r22 超时及全部分母不变。首个延迟审计失败也原样保留。

重复校验修复移除 `validate_private_campaign` 内第二次完整 runtime 扫描，立即使用
本次已验证 runtime 做新的真实 adoption 校验和纯 session 对照。公开 `live_gate_v2`
及三个 NativeReader 各自仍完整鉴权：完整 campaign 路径从五次扫描降为四次。
没有新增 skip 参数、跨动作缓存或移除 dispatch 的锁后/输入前门禁。减少一次扫描
也减少该时点对瞬态变更的观察，不承诺所有并发修改交错下的观察完全等价。

最终四项新增测试使用真实 register/materialize/注册验证器，**4/4 PASS，20.035 秒**；
同实现已有相关测试 **11/11 PASS**。此前包含 mock 注册的四项由此次替代，合计
15 项不同测试，不计作 19 项。fixture 只读文件改写及过长临时路径的早期失败已
记录，修复限于测试 fixture；真实 Claude 44 字节路径门槛、生产文件权限均保留。

同一真实注册 fixture、同一当前 renderer、old/new 严格交替四对，局部 helper
耗时中位 **0.264231 → 0.149669 秒**，配对节省中位 **0.114843 秒 / 41.9875%**；
八次返回值 SHA 完全相同。adoption 为合成记录，binary 为无模型 fixture，零模型
调用、零客户端启动；测量不含 readers、dispatch、网络或任务完成。四对观察不是
置信下界，也不能与旧 renderer 的 17.281 秒 profile 相加。当前 renderer 下另一次
对 r22 文件的 runtime profile 为 1.054682 秒（含 profiling 开销），也不是完整派发计时。

## Provider 容量核验

当前默认 Claude 端点 `https://k40.shengqainbang.cn` 的单次只读 `/v1/usage` 返回
HTTP 200、`isValid=true`。这是用量服务接受 key 的证据，不是两个模型流可同时完成
的证据。返回 `.usage.rpm=1` 是用量，不能解释为 RPM 上限；套餐名称不是权威到期
字段。返回的订阅 expiry 为 `2026-10-19T11:17:24.559722+08:00`。接口没有提供
并发上限或空闲槽数，因此当时实际并发容量仍 UNKNOWN。没有修改 key、渠道或设置。

03:00–03:01 又执行一次预先记录的真实并发容量核验：两个原始流式请求同时发出，
均用既定 `claude-opus-5-5`、xhigh，单请求固定上限 180 秒、4,096 输出 token，
零重试、零 native 输入、零会话/设置变更。两条 HTTP 状态都是 **200**，但响应只有
ping 后跟 error：A 在 58.250 秒返回 **account concurrency limit**，B 在 32.199 秒
返回 **user concurrency limit**。均没有 `message_start` 或 `message_stop`，完成
响应 **0/2**，双流容量 **NOT_ESTABLISHED**。这是流内 rate_limit_error，不能因为
HTTP 200 写成模型成功，也不能把它叫作两条 HTTP 429 或无效 key。

这两个独立诊断不计入 33 轮、15 对或 192 消息分母，不改变 r22 的终态。响应、事件
时间和原请求已落盘。当前明确容量拒绝下不原样开新实验；下一次全量验证仍须先有
可用并发的新证据，不能以不断重复请求或扩大时限代替解决容量问题。

## 原证据

证据根：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。

- `validation/r22-body-read-regression-01/result.json`，SHA256 `c4dd90f9fc35be6fa5651afd5ebb5d488fd22b46a50f8b4446ee2f190fe47234`。
- `validation/r22-generated-reader-pressure-01/result.json`，SHA256 `050bf0d0b5028eaa770f43de8525bd878f29dcce0913d7d954b613af21394586`。
- `source-exports/resume-20261019-live-v20/export.json`，SHA256 `6eaefdf56ef36311cbbba909c5ea416d33be3c1b86b8c3a1eab377d3150978b6`。
- `preparation/r22-candidate/candidate-review-pins.json` 与 `REVIEW-AND-NEXT.md`。
- `authorization/r22-20261019/configuration-result.json`。
- `execution/r22-original-driver/intent.json`、`watchdog-ready.json` 和原进程日志。
- `maintenance/r22-progress/observation-*.json`：有界只读观察，不能代替业务验收。
- `maintenance/r22-control-profile-01/`：原只读验证的性能分析。
- `validation/r22-profile-optimization-01/result.json`：新旧生成结果及交替测量。
- `validation/r22-profile-optimization-01/kernel-regression-attempt-01.json`：49 项原始批次结果（流式日志部分保存）。
- `validation/r22-profile-optimization-01/inventory-diagnostic-01/`：原失败项的新旧生成器对照原始日志。
- `maintenance/r22-development/communication-attribution-result.md` 与 `communication-attribution-source-pins.json`：新合同、生产格式兼容、回归与独立审查。
- `maintenance/r22-development/attribution-pressure-result.json`：本机多进程归因压力，非真实模型压力。
- `maintenance/r22-delivery/terminal-readout.json`：核验原 config、deadline、33 槽、15 对及七组 stop 的只读收尾。
- `maintenance/r22-provider-observations/observation-0002.json`：原日志区间 SHA、原正式通知记录及 API error 偏移。
- `maintenance/r22-communication/20261018T183910.850254Z-79776/observation.json`：原 v20 的 192 槽终态观察。
- `maintenance/r22-latency-audit-01/FAILED.md` 与 `maintenance/r22-latency-audit-02/observation.json`：原始脚本匹配失败及修正后的墙钟拆分、迟到 NO_INPUT。
- `maintenance/r22-control-profile-02/result.json`：当前 renderer 下的单次 runtime profile。
- `maintenance/r22-development/runtime-validation-dedup-20261019/RESULT.md`、`benchmark-results.json`、`source-pins.json`：真实注册测试、八次局部计时、36 个源码 pin。
- `maintenance/r22-provider-capacity-01/result.json`：单次只读用量接口结果，非模型并发证据。
- `maintenance/r22-provider-dual-stream-01/result.json`、`A-response.bin` 与 `B-response.bin`：两条 HTTP 200 内的实际并发拒绝，原请求、意图和逐事件时间分别保存。
- `maintenance/r22-development/surface40-latency-capacity-exchange-01/`：新实测请求、九项证据 pin、301 文件通信 release 核验，以及原生通知的零输入拒绝。

配置：`/Users/lzhs/um/driver/config.json`，SHA256 `5a42d6d03a7b6f895ef0d44e32e6edac8ca37ae85e1733d49082cd06fafd4ccf`。

共享 MAC 和论文精炼助手仍由指定 surface40 统一管理。本实验仅修改 upgrade，
通过既有 maintenance 文件 inbox 交换实测发现。归因/UUID/限流发现已发布为
`upgrade42-r22-attribution-provider-findings-20261019.json`；后续延迟、局部校验优化与
HTTP 200 流内拒绝证据已另行发布为 `upgrade42-r22-latency-capacity-findings-20261019.json`。
新通知沿 MAC 0.4.47 的授权同 pane 原生路径执行一次，在 metadata 阶段因
`/Users/lzhs/.codex/app-server-control/app-server-control.sock` 不存在而返回
`NO_INPUT`，input_operations=0。保留原次且不重发；文件发布不证明对方已读。

## 尚未完成的交付条件

候选提交 `ac79cf9` 修复普通消息归因并降低配置生成成本，旧实验已保留；用户的
完整测试授权仍有效。下一次实测须使用独立冻结的新合同，保留原模型、门槛、
失败分母与原会话。当前不能以同配置立即重开来宣称推进，也不能把首轮上限扩大。
必须先厘清双 Claude 的实际并发容量和准备/握手占用；再完成连续 3 pilot、
15 配对、192 消息及真实 continuous-goal 证据。若没有可用并发容量的证据，
无法承诺全量通过时间；本报告交付的是已验证代码改进与真实未通过结论。
