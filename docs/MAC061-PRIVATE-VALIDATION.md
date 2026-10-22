# MAC 0.4.61 私有入口兼容验证

2026-10-22。这是原三态协作任务的有限实机前置修复，不是新增实验、共享安装或现场验收。原 surface40 仍是共享 MAC / thesis-refiner 唯一整合者；原会话、模型、控制器、输入原次和 R33 STOP 保留。

## 固定来源与最小变更

完整来源：`/Users/lzhs/.local/share/multi-agent-collaboration/releases/0.4.61-d14ae7c2b757/source`。351 件 release 成员的原字节与 executable mode 已逐件核验，tree SHA256 `d14ae7c2b757c30b9fab21bd4d9556b43f6015e2e481be4c91ea44a6e7486bb6`；SKILL SHA256 `b8ac049b559a1531dede6e2b6d1674c82d68b6cf439d515d10fb64e6750bc5ea`。

原 `cmux_native_delivery.py` 36048B，SHA256 `104a3242a3c188106dbcc8abe4d018bf43d12f8c62c21c0a1f40f636b601a955`。新独立 MIT fixture 保持原字节，旧 .58 fixtures 不变。fixture 不是完整 release，不能拿它初始化实验。

`upgrade_live_patch.py` 只接入新的 `matches, shells` 来源形状：

- 私有 UID/TTY 内核 inventory 可以发现 shell，仅用于返回 `NATIVE_TARGET_RETURNED_TO_SHELL` 诊断；shell 不进入 agent matches，也不触发自动选取、重启或输入。
- Agent 仍须通过冻结 executable、原私有 root、PID/birth/owner/TTY/workspace/surface、native session 和 transcript 围栏。诊断候选不授予身份权限。
- Shell 的 TTY 读取只允许在唯一原 `_target_process` 内转换；未知主体、重复函数或外部 shell 读取拒绝。旧来源形状和生产源码守卫保留。

## 实际检查与失败保留

证据目录：`/Users/lzhs/projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/execution-20261021`。以下检查使用 Python 3.14.7；没有模型请求或真实业务输入。

| 检查 | 结果 / 范围 |
| --- | --- |
| `c7577ec` 原 .61 全脚本转换 | 239 PASS / 1 REFUSED；旧 listing 形状不匹配，原结果不覆盖 |
| 公共 transform tracer RED | 1 ERROR，真实 `ContractError`；日志 SHA256 `22d043e4093176b9ebb0b332c333fc4b2d9759e5cb78d23c964a41efc794fc94` |
| 最小生产修复后 tracer GREEN | 1 PASS；日志 SHA256 `edb393f7d28a512a599e9354065ff611f94b5a62adc8c1f0fab503b127fa5fe7` |
| 完整固定 .61 转换 / 编译 | 240 PASS / 0 REFUSED，12.542956s；上游及 upgrade 前后源码均不变；未执行模块或启动 runtime |
| 完整 .61 私有生成公开绑定对照 | 合法冻结 agent 与诊断 shell 共存时绑定 agent；只有 shell 则拒绝且 input=0 |
| 最终 .58 + .61 focused | 21 PASS / F0 / E0 / S0，24.652s；.61 新增 5 项，其余是原 .58 16 项 |
| .61 / .58 / process / reask / native continuity affected | 116 PASS / F0 / E0 / S0，594.314s；345 件前后源码 pin 一致 |
| 新候选全量回归 / CI / 实际三态 | 尚待各自独立验收；不借 `c7577ec` 的 2458 测或 CI 绿色 |

完整 materialization 使用原公开 materializer 和生成模块，在真实 Darwin Seatbelt 内执行 `bind_target`。只模拟 libproc/sysctl 外部内核响应和只读 cmux tree；不 mock 私有 inventory、root 或 native-binding 实现。完整 release 依赖缺失时 3 项生成测试明确 SKIP，2 项固定来源可移植测试仍运行。生成测试中的 `/usr/bin/true`、进程 / 会话及 foreground 文件是隔离夹具，不是实际客户端或自动 hook 证据。

最初新增反例的两轮失败另存：`mac061-compatibility-controls-v1` 为 21/F2（测试目录复用和遗漏公开错误包装）；v2 为 21/F1（占位符误替换输出 marker）。只修测试夹具，未改变生产守卫；v3 为 21/F0/E0。不得将这些夹具错误当作新的 API 或生产身份故障。

240 转换结果 `mac061-transform-shell-compat-v1/result.json` SHA256 `70ddeb780c70092c473e7a388add0ca637ab9031b429f1c0fe80c0a65806d8c3`。最终 focused `mac061-compatibility-controls-v3/result.json` SHA256 `ec010a25beeccec0a70fec1bdc33beeace57650e8f92296cbf1152dc3c9e36a3`，原日志 SHA256 `34e0933c76e6b038ffc724986cf87559b01f0e46c576a2e55b2cae9e2dfba011`。这些 runs 的 HEAD 为 `c7577ec`，工作树含本修复；不是该旧提交的冻结全量结果。

Affected 结果 `mac061-affected-final-v1/result.json` SHA256 `b2dd17bef34ca46a5e6b361096549b1aedece81fe9edd36eb1145aa7aec073dc`；原日志 SHA256 `e780fa2925c4251ab990ef5556c21f5e39a17e1559bca0041648a5d3c8e00004`。它不替代完整回归。

## 接回原任务

原两 Claude 的最新已核 native 记录均为 403「API Key 所属分组已删除」，不是 SESSION_LOST。原会话或 provider 未改；不能用替代会话制造健康 trio。原40 R16 回件全文及 SHA 已核，明确无新模型 / 会话授权、无共享写转移。

另一个明确区分的原私有 UZ 实验，其三进程 PID/birth/path 于 05:04 核读仍 PRESENT，三个准确 UUID 仍属于原 workspace（列表 ref 已重编号）。05:13 用原冻结 controller 的只读 `experiment-inspect` 识别了两原 Claude 的 readiness 与自动 hooks、原 Codex direct-input 只读 capability，但 `ready=false`、adoption absent：同一 Codex native session 先以 `gpt-6-astra` 收到 402，后续两 turn_context 变成 `gpt-6.1-sol`；固定来源守卫报 `STARTUP_MODEL_UNCONFIRMED_OR_CHANGED`。这不是会话消失或合法新实验授权；ROOT 未更换模型、补造 adoption、发送输入或重启客户端。

仍需原会话在 fresh 健康边界完成真实握手、callback、每个 Claude 至少两轮 60 秒询问→原主管回复→FULL / exact-SHA consume→rearm；原 Codex 不可用时由同任务原两 Claude 协作、整体最多两件未消费 native child，以及原实证样本与提效门槛。历史 923 件补充审查暂停，不以扩大离线研究替代这些主线。

05:00 实测可用空间 5960228 KiB，低于不变的 10,000,000,000B 门槛。保留原件和原不确定上传；在恢复门槛前不启动本机全量测试或大体量实验。GitHub CI 可以独立检查冻结候选，但它的 SKIP 和离线结果不能替代本机完整或真实协作验收。
