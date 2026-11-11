# Nested research skills / 巢狀科研協作技能

**Public candidate only — source and workflow documents, not a deployment.** The two named skills below are adapted from a six-member local workflow package. Formal business acceptance remains **0/33; paired comparisons 0/15**. Reading, installing or displaying a role pill does not establish live adoption or research acceptance.

**公開候選，尚非執行環境驗收。** cmux 負責跨終端／工作區的外部協調，Herdr 負責一個終端內的團隊協作；各章主管匯總章內成果，再由總主管整合全稿。兩者互補，既有 MAC 與論文精煉助手及純 cmux 用法均保留。

## Pick the layer that owns the action / 依動作選擇層次

| Layer / 層次 | Responsibility / 責任 | Read / 入口 |
|---|---|---|
| Official Herdr | Supported terminal/agent commands, IDs and caller restrictions / 原生命令及呼叫邊界 | Installed `herdr --skill`, `herdr --help`; [official project](https://github.com/herdrdev/herdr) |
| Third-party cmux-herdr | Optional topology/UI bridge / 可選的介面與拓撲橋接 | [RaviTharuma/cmux-herdr](https://github.com/RaviTharuma/cmux-herdr); separate from official Herdr |
| MAC transport profiles | Identity, delivery, intent, receipts and recovery on each actual communication edge / 每條通訊邊的原協議 | [Herdr profile at d652490](https://github.com/lzhs1995/multi-agent-collaboration/blob/d6524901844a104b478c22089a57e683fb880ed3/skills/herdr-collaboration/SKILL.md), [cmux profile at d652490](https://github.com/lzhs1995/multi-agent-collaboration/blob/d6524901844a104b478c22089a57e683fb880ed3/skills/cmux-collaboration/SKILL.md) |
| Nested research workflow | Chapter roles, bounded work, result consumption and cross-chapter integration / 章內分工與跨章整合 | [herdr-inner-collaboration](../skills/herdr-inner-collaboration/SKILL.md), [cmux-outer-coordination](../skills/cmux-outer-coordination/SKILL.md) |
| Thesis-refiner domain | Scientific evidence, faithful writing and applicable Word/PDF/NotebookLM acceptance / 科學與論文產物驗收 | [thesis-refiner at 653fd8c](https://github.com/lzhs1995/thesis-refiner/blob/653fd8c57afd3eaa643a68e4c304b34e7b973b3e/SKILL.md) |
| Upgrade runtime | Experimental TASK startup, retained owners, inquiry and continuity / 實驗性 TASK 執行碼 | [Runtime capability extension](task-capability-extension.md) |

Public version links are source references, not the controller binding for an existing task. Pure-cmux tasks use the applicable cmux profile with the actual domain rules; choosing cmux does not downgrade thesis-refiner. Existing tasks keep their registered source, sessions, controller, unknown attempts, callbacks, review budget and resource leases.

公開版本連結只供來源定位，不會改綁舊任務。新章節協作採用適用的領域規則；已有任務沿原登記繼續。UI 膠囊、pills 或狀態快取只能協助導航，不能當成身分、正文已讀、接單或科學成果驗收。

## Use the two skills / 使用兩套技能

Read directly from a complete checkout and keep the following relative layout. This candidate does not install or overwrite shared skills. If a client accepts an explicit skill path, point it at the relevant `SKILL.md`; otherwise ask it to read that file and the linked common contract. Automatic discovery varies by client and has not been verified here.

```text
skills/herdr-inner-collaboration/SKILL.md
skills/cmux-outer-coordination/SKILL.md
references/nested-research/common-contract.md
references/nested-research/capabilities-and-gaps.md
references/nested-research/sources.json
templates/nested-research/four-chapters.json
```

- For one chapter, read the [inner skill](../skills/herdr-inner-collaboration/SKILL.md). Executors submit a bounded artifact and its evidence to the chapter supervisor.
- For dependencies between chapters, read the [outer skill](../skills/cmux-outer-coordination/SKILL.md). Chapter supervisors submit accepted, versioned deltas to the senior supervisor.
- Both use the same [common contract](../references/nested-research/common-contract.md). The detailed four modes and five rules live there. The MAC task controller still owns transport and evidence; this package adds no second sender, identity store or ACK loop.
- For a pure-cmux team, keep the original MAC transport profile and thesis domain workflow. Do not create Herdr panes just to fit this example.

繁體中文：整份 checkout 保留相對目錄即可閱讀；此候選不安裝、不覆蓋共用 skill。章內執行者向章主管交付，章主管驗收後向總主管交版本化章增量。普通提問沿原通道處理，不為每次交談新增報告、身分庫或 ACK 循環。

## Prepare a chapter interface offline / 離線準備章節接口

Copy [four-chapters.json](../templates/nested-research/four-chapters.json) to a private task output directory. It is an editable workflow form, **not** input to `orchestration_planner.py` or a runnable scheduler. Keep only the chapters and roles actually assigned; update their edge lists together. Four chapters and separate reviewers are examples, not prerequisites.

Fill the objective, owner, authorized write paths, acceptance criteria and finite task budget from the existing assignment. For each chapter, use `accepted_delta_interface` to record artifact/source pins, definitions or methods shared with other chapters, actual checks, scientific limits, unresolved items, prior review rounds and resource status. A non-Word evidence package can be prepared without claiming final Word/PDF/NotebookLM completion. Fill live endpoints and controller pins only from real evidence when an authorized action needs them; nulls are intentional for this unregistered template.

```sh
# Parse the neutral template without contacting any agent or provider.
python3 -m json.tool templates/nested-research/four-chapters.json > /dev/null
```

The senior supervisor consumes each accepted chapter delta and reconciles cross-chapter claims. It reuses existing review evidence and the same three-round budget for the same acceptance scope. A handoff to senior does not start another full review. Scientific limits remain visible in the integrated manuscript.

繁體中文：在私有任務目錄複製模板，依原分派填寫章數、角色、寫入範圍及章增量接口。可先完成已授權的定義／證據包；只有真實發送或共享資源動作才核對相關端點與原控制器。不為補齊空欄創建會話，也不以別章缺項阻擋獨立工作。

## Runtime boundary / 執行邊界

The official Herdr skill defaults to a caller inside `HERDR_ENV=1`. Explicit user authorization to coordinate a designated existing Herdr environment takes precedence over that skill default. Read the installed CLI guidance and use an explicit session/server selector only when that version supports it; retain the selector on every call and verify the original server, terminal, native session and foreground process. Keep the actual environment unchanged. With no applicable authorization or supported exact route, use the already-bound internal participant or report `ROUTE_UNAVAILABLE`; never control whichever session happens to be focused.

A selector is an address, not an identity credential. An authorized ordinary contact does not satisfy an old controller's separate caller-authentication, formal-dispatch, resource or experiment gates, and does not migrate old tasks. Draft checks, input locks, intent, original native receipt and full-body consumption still apply. The shared [exact-endpoint rule](../references/nested-research/common-contract.md#精确端点与外部协调) governs both skills.

繁體中文：官方技能預設供 Herdr 內部呼叫者使用；使用者已明確指定外部協調範圍時，可沿已安裝 CLI 支援的精確 selector 操作該原有環境。保持真實環境值、原會話、草稿及任務控制器；不把地址當身分，也不把普通聯絡成功當作舊實驗門檻通過。

Do not paste a chapter message into the outer terminal hosting Herdr. Native receipt, full-body reading, task acceptance, scientific acceptance and resource release remain separate observations under the original controller. The two skills preserve the user's four modes — peer, moderated, hierarchical and finite ring — and five rules: traceable handoff, 60-second progress, two-way supported attention, one full review with at most three total rounds, and intent-first recovery without replay.

Read [capabilities and gaps](../references/nested-research/capabilities-and-gaps.md) and [source provenance](../references/nested-research/sources.json) for limits. Local predecessor installation/help checks are historical machine-specific facts; no private installation receipt or role registration is bundled. The source candidate has not launched agents, installed hooks, moved existing controllers or demonstrated a collaboration speedup.
