# 2026-10-18 压力测试与真实协作验证

当前结论：**r16 修复完整回归 1,848/1,848、新进程压力 2,500/2,500、现存三客户端 TTY 压力 1,000/1,000 全部通过；真实协作提效仍未证明。**
本次 review_mode 为 `solo_self_review`。Jev/TypeSafe 仍为 advisory/mock，
真实 campaign 使用 rules。旧会话、失败分母、原控制器及原始证据均保留。
本文替代旧效率报告中的执行进度；平台架构分析仍可参考旧报告。

## r16 设备身份修复完整验收（2026-10-18T09:10:46.721169+00:00）

唯一 `tty-device-identity-full-01` 原进程已结束，exit 0：
**1,848/1,848，零失败、错误、跳过**；耗时
1583.642 秒，247 个监测文件测试前后 SHA 一致。
未放宽超时、未跳过或另启重复全测。完整日志 SHA256：
`fe897720645179c758b65d8fbe53534ca22d1f5424d0ced8e189cca5bcbd5200`。live_model_requests=0，属于本机回归验证。
本节是测试结束后的文档更新，由新 export 单独固定。

最终 inventory SHA 为 `4cf9d5081b7a9268dae8f03ef4ded6ff5a187ea389c3426ee43297d7190832da`。
TTY→UID→TTY 的完整集合闭合排除其他 UID 的 login 父进程；设备身份仅保留
path/dev/inode/rdev/mode/uid/gid。ctime 随正常终端输出更新，不能用于身份。
31 项定向全过，包含时间戳变化允许和六类设备／权限字段变更拒绝
（定向测试注入；真实 PTY chmod 另有因果实验）。
其余本 UID EPERM、未知、退出、身份漂移和严格 selected 原生校验保持。
离散读取不构成原子内核快照；target 唯一性仅针对本 UID 的绑定 TTY。

| 新压力 | 实际分母 | 耗时 | 独立审计 |
|---|---:|---:|---:|
| 七类进程／TTY 场景 | 2,500/2,500 | 68.257 秒 | 64,977 checks，0 errors |
| 现存 Codex＋双 Claude 三 TTY | 1,000/1,000 | 17.887 秒 | 21,198 checks，0 errors |

前者包含 4,000 个自有短命子进程，清理全部完成；unknown 的本次实际分支为
182 存活 fallback、2 path ESRCH、15 首 BSD absent、1 fallback BSD absent。
45 个 unknown fixture 最终 SIGKILL，具体系统原因未作归因，不能借用上轮
Gatekeeper 的 27 条证据。第一次审计器遇新增分支 TypeError，原脚本与失败
记录保留；v2 仅重新审计原 2,500 次数据，压力本身未重跑。

现存三 TTY 共 334/333/333 次全部返回正确目标，三个 root login PID 分别由
kernel UID 范围排除。Codex 扫描中实测 52 次 mtime/ctime 变化而身份稳定，
338 次无关 TTY 的 UID 集合变化未妨碍正确发现。只读核验，零模型输入，
原会话未更改；这不证明新客户端自动 hooks、业务派发、callback 或协作提效。

旧 r15 HALTED/FAILED、原实 TTY 01 的 0 次准备失败、02 的完整 1,000 次
失败结果和坏 JSON、中止的 `tty-uid-full-01` 全部保留，不能追认为通过。
新两组压力与最终全测均绑定本次相同源码；历史 7,200 组件压力及完整 hash
函数速度只在对应源码 pins 未变时复用，不充当真实协作效率证明。

沿已有全面授权，下一步冻结 v15、核验七组实际 controller，启动唯一 r16：
同三会话 pilot 连续 3/3，再执行 12 warm＋3 cold pairs，共 7 组／33 轮／15 对。
900/1800 秒 watchdog 和每次原生接收、产物、callback、验收、释放、续派
门槛保持；warm 中位改善需≥15%，各 workload 中位退化≤5%，缺 usage 为 UNKNOWN。
指定经验会话仍等待可验证回件；已吸收书面案例不等于双向交流完成。

## r15 启动终态与 r16 修复验证（08:41Z）

r15 的唯一启动创建了真实 1 Codex＋2 Claude，但原 inspector 在读取客户端前，
对三个 TTY 的 UID 0 `/usr/bin/login` 父进程得到 EPERM。`PROC_TTY_ONLY`
包含其他 UID；此前全 UID 入口限定本账号，改为 TTY 入口时漏掉了该边界。
这是发现范围回归，不是 provider/key 失败。原轮于 08:32:44Z HALTED/FAILED，
driver exit 2；pilot 0/3、pairs 0/15、业务闭环 0/33，所有旧会话及分母保留。

下一修复将 kernel TTY PID 集合与 kernel UID PID 集合相交。每次 UID 枚举
两侧的完整 TTY 集合必须相等；读完所有本 UID 成员后，完整 TTY 集合及交集
都要再次闭合。本 UID EPERM、未知、退出、身份或 TTY 漂移继续拒绝，不能
捕获权限错误后跳过。离散观察不能证明两次采样间从未瞬时变化；选定客户端
仍执行完整原生身份和产物校验。daemon 发现保留原 full-UID 范围。

r16 第一版 UID 交集修复通过 29 项定向和 2,500/2,500 场景，但现存三 TTY
压力发现了另一处设备身份问题。实 TTY 01 准备失败（0/1,000）；02 完整
执行 1,000 次，双 Claude 666/666 返回正确，Codex 307 次正确返回却被 runner
跨整轮比较 ctime 错计，另 27 次产品拒绝设备变化。02 的 JSON 末尾因混合
键排序失败，部分原件和完整 attempts/traces 保留，重建报告另存。没有内部
逐次设备 pin，不能断言原 27 次拒绝均仅由 ctime 引起。

独立自有 PTY 实测发现，普通输出 20/20 次改变 mtime 和 ctime，其余设备
身份字段不变。另 6 组共 60 次因果实验：写 slave 仅改 mtime/ctime；读 slave
仅改 atime；读取 lstat、读 master、写 master 不改字段；chmod 只改变 mode。
因此设备 pin 应保留 path/dev/inode/rdev/mode/uid/gid，移除活动时间戳；不
放宽本 UID、精确 TTY、逐次 BSD 与 selected 原生身份核验。

唯一 `tty-uid-full-01` 在确认需改代码后精确中止自有测试子进程，207.348 秒、
returncode=-2、counts=null、247 个监测文件前后未变。原日志及中止意图保留，
不称完整回归通过；这是为避免耗完一轮后仍需重新验证。下一版完成新定向、
两类完整压力及新的完整回归后，再冻结独立版本并继续原定 7 组/33 轮/15 对。
下面 1,839 全测及历史压力只覆盖对应冻结字节。真实协作提效仍未证明，
既有全面授权继续有效。

## r15 修复完整验收与原定真实实验（历史）

唯一 `tty-integrity-full-01` 原进程已结束，exit 0：**1,839/1,839，
零失败、错误、跳过**。执行器耗时 1578.008 秒，
247 个监测文件的测试前后 SHA 完全一致，source_unchanged=true；
没有放宽子测试超时、跳过测试或重复启动。完整日志 SHA256：
`f9be1136f664b93bd87140301c638ff79cc53e30ae376380ba4dbe2e73c913bf`。
这是本机离线验证，live_model_requests=0；本文在结束后更新并单独固定。

native target 枚举现限定到原绑定 controlling TTY；daemon discovery 保留
全 UID。精确设备、逐次 BSD TTY、成员身份和最终 PID 集合均核对，任何同
TTY 退出、未知、权限错误或变化继续拒绝。此入口证明精确 TTY 内目标唯一，
不声称其他 TTY 上环境值全局唯一。选定客户端的完整进程、可执行文件、
private root、UUID、native session 和回执校验保持；basename matcher 每次
枚举读取一次配置，selected authority 校验不缓存。26 项定向测试包含真实
materialized Seatbelt/controlling PTY，现已纳入最终完整回归。

真实 TTY 压力第三次运行 **2,500/2,500 符合预注册安全谓词**，耗时
61.685 秒，独立逐条审计 **35,775 checks、errors=[]**。覆盖稳定 TTY
1,000 次、其他 TTY churn 500 次（4,000 个自有短命子进程），双候选、成员
新增、成员退出、unknown/unlinked 和 selected 身份错误各 200 次。
全部自有进程完成清理，owned_remaining/errors 为空，helper exit 0。
01 在 fixture 准备时失败、0 次开始；02 完成 2,500 次，其中 2,127 符合预期、
373 失败。全部原件和分母保留。03 仅改用本机 adhoc 签名的自有 fixture，
三次运行的五个产品文件一致；不把 03 通过追认为 01/02 通过。

unknown 细分为 **192 次存活 fallback、1 次 path ESRCH、7 次首 BSD absent**；
不能将按错误文案划分的 193/7 当作稳定 unknown 覆盖。最终 27 个 SIGKILL
fixture 与原时间窗的 27 条精确 PID Gatekeeper rejection 终止日志完全对应，
均在相应 kernel birth 后 0.123 秒以内。具体私有策略原因未知，也不能将此
结论推广到 02 或断言 unlink 必然导致终止。完整生命周期和 OS 日志审计
分别固定，有限多次读取不等于原子快照。

完整内容校验采用最多 3 个 worker，主线程检查声明的小文件；保持每次完整
SHA 与前后 fd/path 校验，无跨调用缓存。并行分支收齐全部检查及原异常，
小批次保持串行 fail-fast。集成测试 24/24 通过（50.440 秒）。实现后相同
29 pins、1,020,215,448 bytes 的 3 次串行与 3 次并行对照，中位
**2.962212→1.747980 秒，约减少 41.0%**。此为本机函数测量，受宿主负载
影响，不能替代真实协作提效结果。定向测试开始后只有 integrity docstring
变动；最终精确可执行源码验收以本次 1,839 项完整回归为准。

r14 原失败保持：07:44:54.401Z HALTED/FAILED，原 driver exit 2，
07:46:02.854Z 发布原最终报告。双 Claude 均有真实握手，source_index prepare
成功后 dispatch 因 kernel identity EPERM 拒绝；inventory prepare 因 executable
inventory changed 拒绝，原记录没有快照，具体变化字段未知。
**pilot 0/3、pairs 0/15、closures 0/33**，33 个注册轮次保留；
INSUFFICIENT_EVIDENCE、measured_runs=0、observed_runs=1、cost UNKNOWN。
原报告 `/Users/lzhs/ue/driver/final-report.json` SHA256：
`69cdff3a23e9b60f2ed150a79d53f146a22cfd1509b0b83f9cbdc6bea5d44301`。
原 r13 candidate EBADF 仍未归因；新增诊断不是原根因已修复的证明。

沿现有全面授权，下一步冻结独立 v14，核验七组实际 controller 并启动唯一
r15 实验；不重放旧会话启动或派发。完整合同保持同三会话 pilot 连续 3/3、
12 warm＋3 cold pairs、7 组、33 轮、15 对，同时最多双 Claude 执行业务。
指定经验会话仍未提供可验证的双向回件；已有 PR39 Tip/spinner 案例已实测
吸收，不能将公开记录或文件可用宣称为对方回复。

## r14 启动前最终回归（07:19Z）

唯一 `candidate-diagnostics-full-01` 原进程已收取，exit 0：
**1,826/1,826，零失败、错误、跳过**。执行器耗时 1,276.899 秒，
unittest 耗时 1,271.292 秒，246 个监测文件 SHA 前后一致；
live_model_requests=0，未修改子测试超时。日志 SHA256：
`30275141957b8072396edaac06042584632ec3ed50bddf8cf512f68985e92d32`。
两条 60 秒栈快照属于观察器诊断，相关用例随后完成，最终无测试超时失败。

此前 CLI 诊断定向验证 47/47 通过，覆盖真实 EBADF 注入、异常链、循环 cause、
PendingOperation 和成功输出兼容。原 r13 EBADF 根因仍未知；新增的异常类型、
errno 和栈帧记录是诊断改进，不追认原实验成功。

外部性能审计实测一次 continuation 验证构造 8 个 NativeReader，另加一次
state_root 验证，共 9 次 runtime 校验，耗时占 98.5%。29 个 pin 单次约
1.02 GB，9 次重复哈希约 9.18 GB 逻辑输入，不等于物理磁盘读取量。原 Codex
watchdog 前 status 仅 1 次、check 0 次，没有重复 status/check 的证据。
当前未实施缓存或降低校验门槛，后续用真实配对结果评估效率。

沿现有全面授权，下一步冻结 v13 源码并核验七组实际 controller，随后执行
唯一 r14 driver。规模仍为 7 组、33 轮、15 对，pilot 连续 3/3 后进入配对。
指定经验请求观察 45（07:04:22Z）尚无回复；没有追加输入或宣称双向沟通完成。
本节为全测完成后的结果文档更新，由导出记录单独固定。

## r13 终态、进程压力与 candidate 诊断（06:52Z）

r13 原 driver 已 exit 2，06:23:31.336Z HALTED/TIMEOUT；注册截止为
06:23:21.229Z，未延长。三客户端实际 ADOPTED，Claude B 已完成 source_index
业务产物，处理 48 个源码文件、4,817 行、194,785 字节。完整业务验收仍为
**pilot 0/3、pairs 0/15、closures 0/33**，产物和收到 callback 不等于整轮通过。
原最终报告 `/Users/lzhs/ud/driver/final-report.json` SHA256：
`116e6a8d628effb8dc893e05c0f12a3bf757073025b922e9536dbe72f6db3249`。

正式任务原生接收已确认，重试数为零。Claude callback 的完整 native user 于
06:20:43.951Z 到达原 Codex；正式 receipt 于 06:23:35.693Z 发布，间隔
171.742 秒，晚于 halt 4.357 秒。首次零输入结算的锁获取立即拒绝，不能将外层
工具耗时全归因于锁等待。原回执 confirmed=true、bridge_retries=0；所有旧次
证据和会话保留，不重放。详细时序见 `validation/r13-process-churn-pair-v2-01/R13-TIMING-AUDIT.md`。

随后原 Codex 的 candidate 于 06:33:37Z 返回 `[Errno 9] Bad file descriptor`。
当时 continuation 已发布，tests.json/candidate.json 未发布；旧 CLI 只保留异常
文字，缺少调用栈，因此根因仍未知。严格 Seatbelt 禁止旧根写入及网络的诊断中，
原 continuation 只读验证、output_refs 和真实 run_checker 子进程均通过；checker
返回 8 项正确性检查、exit 0。独立新目录的发布/锁文件描述符检查也未复现 EBADF。
这些结果不等于原 candidate 成功，也不证明原进程继承的文件描述符状态正常。
诊断器自身的 pipe FD 拒绝和嵌套 Seatbelt 失败均保留，未算入通过。
详见 `validation/r13-process-churn-pair-v2-01/R13-EBADF-AUDIT.md`。
当前补充结构化异常链和栈帧诊断，保留原拒绝和不重试语义；这是诊断改进，
不是已确定根因的修复。修改后的代码需要另外验证，不能借用前一轮全测成绩。

私有 Darwin inventory 修复保留每次 BSD 读取的 errno，并逐次核对 PID、UID、
birth、ppid 和完整 name。身份漂移、权限错误和未知/短读继续拒绝；仅确知无关的
进程返回精确 ESRCH/zombie 时排除。kernel 候选路径优先于 basename 排除，
仅 ENOENT 可使用稳定完整 BSD 名回退；选定客户端的 strict process/argv/TTY
认证不变。实际 materialized controller 的完整 helper 字节、双入口同模块调用
及 strict process AST 均纳入验证。

第三批真实进程压力已完成：200 次扫描、4,800 个自有子进程、818,746 次内核
操作，旧版和新版各 98/100 返回；sentinel 遗漏/漂移及遗留子进程均为零。四次
拒绝保留，分别为旧版两次初始 BSD EPERM、新版一次初始 BSD EPERM 和一次同
birth/ppid 下的 name 漂移。新版一条已知 `/bin/sleep` 的后续精确 ESRCH 已实测
排除；未实测到的内层初始第二 BSD ESRCH 不宣称覆盖。完整 trace 审计 PASS
不代表自然扫描 100% 成功，更不是业务提效证明。

定向回归 **124/124 PASS**。进程发现修复版完整回归
`process-discovery-pair-full-01` 已 exit 0：**1,822/1,822，零失败/错误/跳过**，
执行器 1,193.096 秒，246 个监测文件 SHA 前后一致，live_model_requests=0；
日志 SHA256 `43d38e5d08c420f1ae2c55d1613ce64abc25f8ce0224625a52dec4e207590a2e`。
旧 `process-discovery-full-01` 的 1,812/1,813 和一项失败保留，不用于新导出。
7,200 次 reserve 组件压力涉及的产品源码未变，不重复运行已通过的该项压力。

r14 外部准备脚本已静态审查，尚未导出或启动客户端。沿用户现有全面授权，
诊断改动验证后冻结独立源码，核对七组实际 controller，再执行唯一 driver。
原注册仍为同三会话 pilot 连续 3/3、12 warm＋3 cold pairs、7 组/33 轮/15 对；
900 秒启动及 1,800 秒单轮 watchdog 不变。下一轮属于有诊断记录的新实验，
不能描述为已修复旧 EBADF。指定经验观察 43（06:51:57Z）仍无双向回复。

## r12 已失败封存，v3 契约修复全测通过


r12 已于 05:15:29Z HALTED/FAILED，原 driver exit 2；原报告仍保留全部
33 个注册轮次，pilot 0/3、pairs 0/15、verified_runs=0、passing_runs=0。
`/Users/lzhs/uc/driver/final-report.json` SHA256：
`193257945d2355e2b87830389f10a0c1686413fe717a727c01685bb9ead66355`。

实际三客户端、自动 hooks、preflight 和 Claude ACK 均已观察到；失败发生在
五个准备阶段完成后的 `verify_prepared()`：真实 v3 sessions 没有 `surface_ref`，
旧验收代码却直接读取它。旧 fixture 含该字段，因此既有 1,804 项回归未覆盖此
真实格式。该回归成绩只证明上一版已测范围，不能证明当前修复或真实提效。

当前修复从固定的原 identity gate 认证 supervisor ref 到 UUID/workspace/pane/
native session/role 的对应关系；callback 绑定已验证 pack。已用 r12 原 gate
只读核对，成功解析原 `surface:1000021656`，input_operations=0；这不是重放或
重新验收旧实验。另修未映射 UI ref 被零输入检查误当无关接收者的漏洞，该漏洞
由离线审计发现，不声称 r12 现场触发。

修复版 `v3-contract-full-01` 原进程已收取，exit 0：**1,811/1,811 PASS**，
零失败、错误和跳过，981.601秒，246个监测文件SHA前后一致；没有模型请求，
没有延长子测试超时。日志SHA256为
`2257307bdd610afc5e6cbf79948a7da2a4bcd75725b0ce943f12a7ab262d494a`。
新增测试使用真实UUID-only会话形状，覆盖双lane生命周期、18种原gate篡改、
callback目标绑定及未映射ref零输入判定；v2原ref兼容保持。

另以r12原pack、原生握手回执、ACK和fence执行 `verify_prepared` 完整只读
兼容验证（`r13-original-prepared-01`）：fixtures=False、Seatbelt禁止网络和
全部写入，exit 0，7.140秒，原件不变。stdout SHA256为
`b0081ea3bc98d915bf4dff3738cc937bf575c7560fa2c81cadf50025c1044773`。
这项离线原证据验证不改变r12终态或已完成轮数。

r13外部准备脚本已独立审查；导出时另核对49个未纳入本轮监测的文件与已验证
v11原件SHA/bytes一致。已使用独立v12 export及私有根 `/Users/lzhs/ud`，
沿现有全面授权启动7组、33轮、15对的原注册实验。
05:49:16Z，pilot原1 Codex＋2 Claude全部ADOPTED；原生readiness、实际自动
hooks、内核隔离及输入能力分别核验。05:50:44Z发布bootstrap，原Codex已
从原等待进程取得BOOTSTRAP_READY。05:51:00Z只读观察仍为pilot 0/3、
pairs 0/15、closures 0/33；没有halt，尚不能宣称真实业务或提效通过。
以上为 r13 失败前的历史启动观察；其终态及后续诊断以上节为准。
本节为全测结束后的结果文档更新，不改变已测试的可执行源码。

指定经验会话最新零输入观察为 `request-observation-35.json`，05:51:20Z；
未收到双向回复。PR39 实景 parser 经验已复现吸收，不能据此宣称沟通全部完成。

## r12 历史启动记录（终态以上节为准）

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

以上为失败前的历史观察，原 driver 已结束；后续独立实验完整规模保持 3 轮 pilot、12 warm＋3
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

1. r15 已 HALTED/FAILED，旧会话、失败分母及原证据保留。TTY 发现遗漏
   本 UID 范围，真实 root login 父进程的 EPERM 暴露了回归；r16 先完成
   TTY∩UID 与 devfs 设备身份修复、现存三 TTY 只读压力、2,500 场景及新完整回归，再冻结
   独立 v15/r16 并沿现有全面授权执行，不重放旧次启动或派发。
   r13 原 candidate EBADF 仍未归因，诊断改进不等于已修复原根因。
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
| r15 唯一启动原终态 | `execution/r15-original-driver/` |
| r16 现存三 TTY 原压力与 devfs 因果诊断 | `validation/r16-existing-tty-uid-pressure-01/`、`validation/r16-existing-tty-uid-pressure-02/` |
| r16 第一版 TTY∩UID 2,500 场景压力 | `validation/r16-tty-uid-pressure-01/` |
| r16 已中止的过时字节完整回归 | `validation/r8-pressure-and-full/tty-uid-full-01/` |
| TTY 与完整 hash 并行修复最终全测 | `validation/r8-pressure-and-full/tty-integrity-full-01/` |
| 真实 TTY 压力及独立审计 | `validation/r15-tty-pressure-03/`（01/02 原失败另存） |
| 完整 hash 并行定向及基准 | `validation/r15-integrity-parallel-01/` |
| r14 启动前包含 CLI 诊断的完整回归 | `validation/r8-pressure-and-full/candidate-diagnostics-full-01/` |
| CLI 定向验证与只读性能审计 | `validation/r13-cli-error-diagnostics-02/`、`validation/r13-process-churn-pair-v2-01/R13-PERFORMANCE-BOUNDARIES.md` |
| 进程发现修复完整回归、逐项 events、源码前后 pins | `validation/r8-pressure-and-full/process-discovery-pair-full-01/` |
| 真实进程压力与原 callback/candidate 诊断 | `validation/r13-process-churn-pair-v2-01/` |
| 修复前完整回归 | `validation/r8-pressure-and-full/repair-full-04/` |
| 修复后完整压力及原压力更正 | `validation/r8-pressure-and-full/multiprocess-stress-02/result.json`、`validation/r8-pressure-and-full/multiprocess-stress-01-correction.json` |
| 压力执行脚本 | `validation/r8-pressure-and-full/stress_components_v2.py` |
| r8 原生错误与只读新 detector 验证 | `validation/r8-native-provider-failure/terminal-error-01.json`、`validation/r8-native-provider-failure/readonly-watch-02.json` |
| 定向回归 | `validation/r8-native-provider-failure/focused-02/` |
| 经验询问诊断 | `maintenance/standalone-caller-v1/hook-discovery-diagnosis-02.json` |
| 经验询问补充与最新观察 | `maintenance/standalone-caller-v1/diagnosis-file-supplement-02.json`、`maintenance/standalone-caller-v1/request-observation-35.json` |
| r13原driver与只读进度 | `execution/r13-original-driver/`、`maintenance/r13-progress/observation-0002.json` |

最近已结束实验的最终报告：`/Users/lzhs/uf/driver/final-report.json`；原 halt：
`/Users/lzhs/uf/schedule/halt.json`。r15已HALTED/FAILED，没有真实提效通过结论。
进程发现修复全测日志 SHA256：`43d38e5d08c420f1ae2c55d1613ce64abc25f8ce0224625a52dec4e207590a2e`。

历史失败也保留：原全测 1,500.37 秒超时、57E 且无最终 traceback，根因未确定；
失败模块复核 100/100 通过不抹除原失败。失败收口新增测试曾因 fixture 缺字段
出现 1E，修正 fixture 后 25/25 通过。r7 的原日志复核不改变其 pilot 0/3。
身份集成首轮曾因测试子进程没有进入 Seatbelt 而失败；修正测试启动器后纳入
本轮全测通过，`KERNEL_WRITE_BOUNDARY_UNVERIFIED` 检查未放宽。完整回归结束后
才更新本文的结果，后续文档变动单独记录，不声称参与了此前测试。

复现本地完整回归：`python -B scripts/test_all.py`。本次没有放宽测试内部超时、
跳过测试或安装共享 MAC。GitHub Actions 与真实活跃客户端采用分别报告。
