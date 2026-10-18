# r22：正文读取证据与有界三客户端实验

状态记录于 2026-10-19 01:56（Asia/Taipei）。真实实验进行中，尚无完整业务通过。
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
三客户端原进程已识别，自动 hook 和就绪记录仍由原 controller 核验。

顺序保持连续 3 轮 pilot，之后 12 warm＋3 cold 配对，共 7 组、33 轮、15 对。
另注册 192 条普通消息，两臂各 96 条、四方向各 48 条。不暂停原生 `/goal`。
正确性通过后才评估 warm 中位改善至少 15%、各 workload 中位退化不超过 5%；
缺失 usage 或成本保留 UNKNOWN。当前提效结论仍为 **INSUFFICIENT_EVIDENCE**。

原调用不重放；失败不替换该 campaign 的客户端。到期停止后续调度和输入，
保留在途请求与原会话，不把 stop 标记解释为远端取消或 lease 释放。

## 原证据

证据根：`/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`。

- `validation/r22-body-read-regression-01/result.json`，SHA256 `c4dd90f9fc35be6fa5651afd5ebb5d488fd22b46a50f8b4446ee2f190fe47234`。
- `validation/r22-generated-reader-pressure-01/result.json`，SHA256 `050bf0d0b5028eaa770f43de8525bd878f29dcce0913d7d954b613af21394586`。
- `source-exports/resume-20261019-live-v20/export.json`，SHA256 `6eaefdf56ef36311cbbba909c5ea416d33be3c1b86b8c3a1eab377d3150978b6`。
- `preparation/r22-candidate/candidate-review-pins.json` 与 `REVIEW-AND-NEXT.md`。
- `authorization/r22-20261019/configuration-result.json`。
- `execution/r22-original-driver/intent.json`、`watchdog-ready.json` 和原进程日志。
- `maintenance/r22-progress/observation-*.json`：有界只读观察，不能代替业务验收。

配置：`/Users/lzhs/um/driver/config.json`，SHA256 `5a42d6d03a7b6f895ef0d44e32e6edac8ca37ae85e1733d49082cd06fafd4ccf`。

共享 MAC 和论文精炼助手仍由指定 surface40 统一管理。本实验仅修改 upgrade，
通过既有 maintenance 文件 inbox 交换实测发现；文件发布不证明对方已读。
