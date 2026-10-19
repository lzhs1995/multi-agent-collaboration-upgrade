# r27：恢复重试在启动窗口超时

原 runner 已于 2026-10-19 **06:32:10.773952Z（台北 14:32:10）**结束，
退出码 **2**；driver 因首组 startup/bootstrap 超过原 900 秒窗口而
`HALTED / TIMEOUT`，总运行 909.432 秒。首组 1 Codex＋2 Claude 已启动，
Codex 完成真实 probe、model record 和自动 hooks；双 Claude 未完成
readiness probe，adoption 为 0，业务没有派发。
**完整真实压力测试未通过，协作效率提升仍未证明。**

独立只读审计重算与原始终态报告一致，差异为空：

| 验收项 | 实际终态 |
|---|---|
| 连续 pilot | 0/3 |
| 业务通过／已验证 | 0/33；1 TIMEOUT＋32 MISSING |
| 可比较配对 | 0/15 |
| 普通通信 | 192/192 attempt MISSING；两臂各 96 |
| 通信原生接收／正文读取 | UNKNOWN，业务 NOT_ASSESSED |
| 提效百分比／成本 | null／UNKNOWN |
| 业务结论 | INSUFFICIENT_EVIDENCE |

Driver、watchdog 和 runner 均退出 2。Watchdog 在 06:32:10.770961Z
报告 `all_stopped=true`，7/7 私有 runtime 停止标记已发布；标记均为
`terminate_clients=false`，旧客户端和 leases 保留。停止调度不证明
原生 Goal 已结束、模型请求已取消或资源已释放。

## 固定范围和实际启动

- 7 组、33 业务轮、3 个连续 pilot、15 对、192 条普通通信；两臂都允许双 Claude 并行。
- 原次配置：06:17:00.915788Z；LAUNCH_RETURNED：06:17:06.402552Z。
- 本轮实际 watchdog deadline：06:46:12.915788Z；总 CAP 仍为 06:47:01.737033Z。
- 启动和 bootstrap 原 900 秒上限于 06:32:09.766347Z 触发 TIMEOUT，未延长。
- 原 runner 工具 62236 已收取退出；保留全部旧会话、控制器、原输入、失败和缺失分母。
- 原配置根 `/Users/lzhs/ur`；未经原 adoption 核验，不发送业务或另起替代客户端。

候选使用提交 `b42923dd9e2d7447c1fb4e322ca4ed46ca92fc52`，冻结为
`resume-20261019-live-v23`。独立核查通过 334 份冻结源码、1869 份生成
控制器文件、7 组 runtime、21 份职责和 14 条 socket/lock 路径。
核查不包含模型执行，不计入业务压力分母。

## 原生 Goal 与请求事实

06:22:40Z 的原生日志仍分别停在原 inspector 的 EOF：A 27963 bytes，
B 27967 bytes。每端 Goal SET=1、assistant=0、tool_use=0，未见 native
API error、Goal MET 或 CLEAR。没有后续日志不能证明 Goal 连续运行或已终止。

原 runner 结束后的 06:37:25Z 独立终态核收仍为同一 EOF，两个 native
日志各新增 0 bytes；原 device/inode、冻结前缀及上一快照前缀均保持。
每端仍只有 Goal SET=1，assistant/tool/probe 为 0，未见 Goal MET、CLEAR
或 FAILED。原日志各自记录一次 `hook_non_blocking_error`，错误为
`No assistant message found`。两个 client.log 各新增 163 bytes，但未见
新增模型请求、stream 或错误事件，也没有停止后带时间戳的新记录。
新增内容仅为 06:27:19Z 的 retention cleanup 跳过提示。这项观察不能将
Goal 终态从 UNKNOWN 改为已结束。

client.log 显示两端确实发起 `repl_main_thread` 请求，模型为
`claude-opus-5-5[1m]`，分别在 06:17:35.683Z / 06:17:51.830Z 收到首个
stream chunk。随后独立 `hook_prompt` 请求也收到首块，并在
06:18:02.873Z / 06:18:04.694Z 报 `No assistant message found`。
该错误属于 Stop prompt hook，不能倒推为首次模型请求的错误。

现有 debug 片段没有完整 SSE 正文和 HTTP 状态，因此不能证明空响应、
key 无效、供应端已恢复或具体根因。两端同时存在 settings.json 的
rename/open EPERM；并存事实不构成因果证据。没有通过修改隔离规则来猜测修复。

追加对照核查：r26 两端在相同 settings.json EPERM 之后，均成功执行真实
readiness probe 并返回 `UPGRADE_READINESS_TOOL_COMPLETED`。A 的错误在
05:32:04Z、成功结果在 05:35:28Z；B 分别在 05:32:04Z、05:33:52Z。
因此该错误本身不足以解释 r27 无助手输出。r27 原启动指定的两个
request-observations 目录均不存在；固定 client.log 也没有完整响应结束
事件、usage 或 HTTP 状态，响应语义及根因仍为 UNKNOWN。

## 已验证修复与经验边界

r26 后的修复支持实测完整 Goal footer，并保存原握手拒绝时的 screen、
SHA、parser、读序号、阶段和异常。原始读取失败不补读，原输入预算不变。
完整本机回归 **2,094/2,094**，零失败和错误，受监测源码前后相同。
这项结果证明本机回归通过，不能替代真实启动或协作压力验收。

新 Goal 明确将 runtime stop、campaign halt 或固定 deadline 中任一项
作为充分终止条件；正常工作期间允许双方及时沟通，不要求暂停 Goal。
Goal SET、流首块、后续助手输出、probe、自动 hooks、原生接收、正文暴露、
业务验收和资源释放分别记录；缺失不能用另一个阶段的成功替代。

## 原始证据

证据根 `/Users/lzhs/Projects/multi-agent-collaboration-upgrade-evidence/20261014-v1`：

- `preparation/r27-candidate/candidate.json`，SHA `16968f6889875a7c660254c9848284f35f8bed4e58bd8ab1fda9e803971474a0`。
- `maintenance/r25-driver-goal-audit/r27-actual-candidate-audit.json`，SHA `0db81b0c262dcf2bfe416a44bdb4686702b74cccb2a2c49e6a1e3c81fa71ff94`。
- `authorization/r27-20261019/configuration-result.json`，SHA `2a34923e917ba3d85a458b5b5e4c183285eb9a8296d449842d959da3398d6934`。
- `execution/r27-original-driver/intent.json` 和 `stdout.jsonl`：唯一原调用和逐步结果。
- `execution/r27-original-driver/result.json`，SHA `483c78a20c2822f734164afc5340280ef3c61d35e99fbfb1f61742f84e79f923`：原运行终态。
- `/Users/lzhs/ur/driver/final-report.json`，SHA `694c2014ec7356e0ec4a4e6a3db8bd59604c7fe8981ed094434d50c1ef8df97f`：完整原分母报告。
- `maintenance/r25-driver-goal-audit/r27-business-observation/20261019T063231.574745Z/checkpoint.json`，SHA `afa96787c4fdfc95314912674fdef1fea726cdc9c9e73d22d4d298dc79da0baa`：独立终态重算，input/runtime writes 均为 0。
- `maintenance/r27-goal-audit/STARTUP-DIAGNOSTIC-062240.json`，SHA `b19453e8e792ef30da17647a289acb250c54d826e1803a9aaeb8c33618820125`。
- `maintenance/r27-goal-audit/STARTUP-READOUT-062240.md`，SHA `ac9a942a4870fa71426a4089381b4ce952d8c2d010482bf2a1c6d94b64350f75`。
- `maintenance/r27-goal-audit/RESPONSE-COMPARISON-0629.json`，SHA `64c49a4f50cf74cfaf9f8d011a76831818f4d7dec6e8f4f635c5ec71eb1348d1`：r26 成功探针对照与 r27 响应观察缺失。
- `maintenance/r27-goal-audit/TERMINAL-OBSERVATION-063725.json`，SHA `afc2b3acbbe94696acf26568caf335aa43db244b38ccc46acf9ba2d6a4e94546`：原 runner 结束后的同身份、同前缀原生日志核收。
- `maintenance/r27-goal-audit/TERMINAL-DEBUG-TAIL-063725.json`，SHA `8bd310d0faf9806c421277456b333c7a48ea3b15fb640335a7df7325bba1c0fa`：同一固定 EOF 内的新增 debug 内容，未延长观察。
- `maintenance/r26-goal-repair/full-regression.json`：唯一完整回归结果。
- `maintenance/r27-goal-run/publication.json`：surface40 既有收件箱发布记录；发布不证明已读。
- `maintenance/r27-goal-run/terminal-publication.json`，SHA `456ce7dd9291350152ea84f99004a69a9ad545187ffefc6ee39a30e9aa97ada4`：06:38:57Z 向同一 surface40 收件箱发布终态；截至此时原约定回复文件仍不存在。

本轮已终结，不重放原输入、不补样，也不自动启动 r28。尚缺双 Claude
原生探针成功后的整套业务、通信、连续 Goal 及效率证据。现有日志不足以
定位原生请求为何没有助手输出；后续排障需要完整请求终态观测，不能靠
无限重启或放宽身份、草稿、隔离门禁来替代。PR 保持 Draft，不发布为已验证版本。
