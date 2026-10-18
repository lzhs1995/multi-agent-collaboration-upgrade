# r22：正文读取证据与有界三客户端实验

状态记录于 2026-10-19 02:33（Asia/Taipei）。真实实验进行中，尚无完整业务通过。
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

## 已启动的真实实验

沿用用户明确全面授权，配置于 01:55:09 固定 7200 秒上限，截止 **03:55:09**。
独立 watchdog 在 01:55:21 记录 ready 后才启动原 driver。客户端启动子调用
returncode=0，原 driver 仍在运行；
三客户端已由原 controller 核验 ADOPTED；双 Claude 的原握手均 PASS/ACKED。
pilot-01 于 02:08:01.784 开始，单轮固定截止 02:38:01.784。截至 02:33，两节点
`inventory`、`source_index` 均 DISPATCHED；两份原 receipt 均记录 `NATIVE_RECEIVED`、
`retries=0`，包含对应接收者的原生 user record、offset/length/SHA。尚无完成回调、
独立业务验收或 run finish，仍为 0/33、pilot 0/3、pairs 0/15。
初次握手 capability 的 `active_or_queued=false`，不能据此宣称连续 `/goal` 验证通过。
早前 Claude A 的 429 是 concurrency/RPM 记录，之后 readiness 与握手通过；不归类为无效 key。

顺序保持连续 3 轮 pilot，之后 12 warm＋3 cold 配对，共 7 组、33 轮、15 对。
另注册 192 条普通消息，两臂各 96 条、四方向各 48 条。不暂停原生 `/goal`。
正确性通过后才评估 warm 中位改善至少 15%、各 workload 中位退化不超过 5%；
缺失 usage 或成本保留 UNKNOWN。当前提效结论仍为 **INSUFFICIENT_EVIDENCE**。

原调用不重放；失败不替换该 campaign 的客户端。到期停止后续调度和输入，
保留在途请求与原会话，不把 stop 标记解释为远端取消或 lease 释放。

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

配置：`/Users/lzhs/um/driver/config.json`，SHA256 `5a42d6d03a7b6f895ef0d44e32e6edac8ca37ae85e1733d49082cd06fafd4ccf`。

共享 MAC 和论文精炼助手仍由指定 surface40 统一管理。本实验仅修改 upgrade，
通过既有 maintenance 文件 inbox 交换实测发现；文件发布不证明对方已读。
