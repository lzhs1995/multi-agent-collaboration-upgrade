# MAC 0.4.58 私有控制器兼容验证

2026-10-21。独立候选 `upgrade/mac058-20261021`，基于已推送 `10ccc1426f64b87231d203aa1f06bddece686136`。只为新授权实验生成私有控制器，不安装到共享 MAC、全局 hooks 或 thesis-refiner；旧客户端、输入原次、controller 与 R33 七个 STOP 不变。

固定完整来源 `/Users/lzhs/.local/share/multi-agent-collaboration/releases/0.4.58-cf3f6175840b/source`，SKILL SHA256 `96f54599a3d95b7296fe4c979dffa309ba928a361cd0c01ac3e1fb6177bb9188`。`scripts/fixtures/mac-0.4.58/manifest.json` 固定十个原字节 MIT 上游源文件和许可，用于 CI 可移植反例；夹具不是完整 controller，不能初始化或执行原生实验。

## 具体修复及保留语义

- 新 native inventory 的 `ps -ww` 与版本化 Claude 名称转换为既有私有 UID/TTY 内核 inventory；完整 executable、PID/birth、TTY、环境、session 与 transcript 检查仍在，不使用名称或新进程冒充身份。
- 新 embedded collector 接入私有 standalone 证明；managed `process`/ancestry 保持原始源码。public proof 记录真正不存在的 daemon 和真实 foreground，不制造 managed 身份。
- 新 bridge 已处理 Tip/Goal/无计时活动，该函数保持原始字节；只增加明确实测默认边框外 footer。composer 内正文、空白、未知 footer 不被当作 chrome 丢弃；native transport 保持原样。
- 新 callback deliver 签名保留 wait_seconds/recover_stranded；只有没有任何旧尝试的首次 callback 可以等待同一 pane inode 至多 15 秒。reconcile、resume_queue、recover_stranded、旧尝试保留 fail-fast。等待后重新核 pack/report pin、授权、身份、任务/目标/pane 锁和 journal，仍仅一个原提交。
- 新 preflight 已无破坏性 bridge-test。私有只读 capability 与原始 probe/diagnostic 兼容，不虚构 bridge_clear；契约错误保留原 sender journal，不推断 NO_INPUT。ACK 允许已发送 prefix，但验证完整 original gate、原 session/provider/pane；未发送成员仍 unstarted，不获得 ACK 或全体通过。
- 新 RPCError import 用完整 AST import 识别，避免旧前缀替换误移动到 input_gate。最终私有 Stop 检查位于 RPC 捕获区之外：原 NATIVE_INPUT_INTENT 后发生拒绝仍保留 consumed 原次，不执行 RPC、不转换 terminal、不重试，且诊断不伪装成 RPC outcome。

## 实际验证与失败留证

| 检查 | 结果与范围 |
|---|---|
| 完整当前源码内存转换/编译 | 227/227；不执行模块、不生成 runtime、不证明跨模块语义 |
| 原 0.4.34 focused | 75 测，9.424 秒，通过 |
| 首次 0.4.58 完整 materialized startup | 16 测，14 通过、2 失败；实际 import 检出 RPCError 错位，callback 旧样本缺少 nonce |
| 两个失败点修复后复测 | 2 测，11.462 秒，通过；live nonce gate 未放宽 |
| 当前实际完整 controller | 16 测，31.997 秒，通过；真实 Seatbelt/kernel、生成模块、回执围栏、私有 roots、启动 argv、body reader、callback 8 进程×10 轮锁竞争 |
| 可移植实际生成 entrypoint | 15 测，2.242 秒，通过；原 sender/observer/reconcile/deliver，RPC exact-once/no fallback、完整 gate pin、未发成员、未知变体、原 import/gate-pin excision反例 |
| 两版 materialized + 调度反例 + 可移植 | 94 测，97.827 秒，通过；新增 recover_stranded fail-fast 检查包括在内 |
| `c6a34dd` 完整回归 | 2322 测，0 failures、14 errors、0 skips，809.258 秒；三个其他测试组借用 materialization 构造器却未继承新增 `source_controller`。14 errors 含 1 条 checker 类级 setUpClass 失败，该类 5 项测试未运行、未计入 total；因此修复后的 2327 = 2322 + 5，并非新加 5 项。失败原样保留，不计为 PASS |
| 公共夹具兼容修复后 | 原三个报错组 + 旧/新 materialized controller 共 58 测，158.720 秒，通过；构造器对未继承调用者保留原 0.4.34 默认，对现代 subclass 保留显式 0.4.58 |
| `96a7b87` 修复提交完整回归 / CI | 冻结 detached snapshot 实跑 2327 测，0 failures/errors/skipped，1062.931 测试秒／1064.661817 wrapper 秒；308 个前后源码 pin 相同。对应 CI 39475536282 四矩阵均 SUCCESS；远端跳过范围不替代本机完整 MAC 验证 |
| 三态现场、自动 hook、配对提效 | 尚未完成；不由离线检查替代 |

以上生成控制器测试不调用模型、不发送实际业务输入。macOS 和完整本地源码依赖的测试在远端 CI 会明确跳过；十文件可移植测试仍运行，不能将 CI 跳过解释为现场验证。

`c6a34dd` 原失败日志固定在 `/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/execution-20261021/mac058-full-v1/test-all.log`，16979 字节，SHA256 `fcc93237bb11ec195a581bd1a7740e052cb26f783f37dfe1a8394253e5144dd5`；308 个前后源码 pin 相同。新完整回归须另开输出目录，不覆盖它。

原 surface45 的 R2 独审回件全文已读并验 SHA256 `22b2fa50d5242fe317ac8caf0b7823eae508fa9326e94f6d5011cb89d631aa12`，5611 字节，结论仍 PARTIAL：callback 调度 1–4 项闭合，三项实跑反例通过；版本适配及夹具安全第 5 项 NOT_EXAMINED。R1 的证据优先级问题已修，v2 可达性及 failure guard 阻塞收取两项假设 REFUTED。需要下一轮覆盖当前适配、完整回归/CI和最终整合，不把范围外内容推断为独审通过。

`96a7b87` 完整原结果 `/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1/maintenance/execution-20261021/mac058-full-v2/result.json`，SHA256 `b647ba04138eec2c6c8c4b2e1dda211d6a78f24c46a1181121de1490c1eea063`；日志 SHA256 `c5e0864795aa833816a2d06a202c8f5ae737542e2e9a2c3ddf4cd5b010c1e745`。对应 [CI 39475536282](https://github.com/lzhs1995/multi-agent-collaboration-upgrade/actions/runs/39475536282) HEAD 为 `96a7b874f477d351e603980fa83b15de7e3a4d75`，新连续执行补丁不继承此绿色。

43 R1 独审回件 3539B／SHA256 `0ead7403d50d06c856617deffd311a8ef9b32b711b718aecf2205ee6f3e2dce3` 已全文读并核验。只闭合 c6→96 的夹具修复／计数；未跑测试、未独立读 CI，现代适配第1–3项仍 NOT_EXAMINED。D1 为上述类级失败／5项未运行的文档精度，D2 为另外两组借用者缺显式固定来源断言，D3 为历史“43整合”用语；后续独立 commit 补充，不修改被审快照。

43 R2 回件 4202B／SHA256 `8de391299e42285668bd2b67630475f99b094b23e7d8a94e28ab29993b6d6a73` 已全文读并核验；冻结 c6 的既有兼容 15 测独立通过（2.346 秒、HEAD 不变），仍为 PARTIAL。二次 live_gates／transform_live 被接受及 gate-pin 挖除判据过宽两项 P3 在后续独立候选修复；第2项真实入口与 inventory／footer 边界仍 NOT_EXAMINED。新原 consumer／private CURRENT／Stop 补充的范围及限制见 [私有接续验证](PRIVATE-CONTINUITY-VALIDATION.md)。

实际三个可用性场景、跨双 Claude 的共享最多两子 agent 预算、自动 60 秒 request→reply→全文 read→exact-SHA consume→rearm 两轮，及完整 3 pilot/33 business/15 pairs/192 ordinary 槽合同依旧是最终门槛。JEV 为 rules/offline_mock；R33 仍 INSUFFICIENT_EVIDENCE。共享维护由原 surface40集中；原surface43是用户点名的upgrade参与者及接受的独立只读审者，communication_only，不据“正在做upgrade”推出唯一整合、merge或共享写权限。
