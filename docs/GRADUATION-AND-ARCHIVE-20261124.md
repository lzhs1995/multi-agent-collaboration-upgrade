# Graduation and Archive Notice (2026-11-24)

`lzhs1995/multi-agent-collaboration-upgrade` is being retired as a separately maintained experimental sandbox. This consolidation decision does not mean that its unfinished business and efficiency evaluations passed.

To eliminate dual-repository maintenance overhead and duplicate skill definitions (`herdr-inner-collaboration` vs `herdr-collaboration`), the selected reusable capabilities below have been carried into the canonical repository [`lzhs1995/multi-agent-collaboration`](https://github.com/lzhs1995/multi-agent-collaboration). Canonical PRs #110 and #111 are merged; the consolidation work in PR #115 and this archive-notice PR #20 remain open as of 2026-11-27. The upgrade repository is not yet archived, and no v0.5.0 release has been published.

## Graduation Ledger

| Experimental Capability | Source in `upgrade` | Disposition in Canonical `multi-agent-collaboration` |
|---|---|---|
| Offline chapter delta validator | PR #15 / PR #16 (`scripts/chapter_delta_validator.py`) | **Ported in canonical PR #115** into `scripts/chapter_delta_validator.py` + `scripts/test_chapter_delta_validator.py`; offline validation is not chapter acceptance |
| Three-state continuity (`CODEX_DUAL_CLAUDE` / `CODEX_SINGLE_CLAUDE` / `CLAUDE_PEER`) | PR #19 (`feat/codex-claude-continuity`) | **Documented in canonical PR #115** in `references/collaboration-contract-core.md`; the three business scenarios still require end-to-end validation |
| Nested inward Herdr + outward cmux workflows & tab/pane binding checks | PR #14 / PR #17 (`skills/herdr-inner-collaboration`, `cmux-outer-coordination`) | **Consolidated in canonical sources** as `skills/herdr-collaboration` & `skills/cmux-collaboration`, with `scripts/herdr_roster.py` in PR #115; source availability, client loading and delivery acceptance are separate checks |
| Legacy pure-cmux 33-slot / 15-pair / 192-message scheduler campaign (R30–R34, `0/33`) | `upgrade/recovery-v2-20261014` | **Frozen, results retained** — the original `0/33` outcome, attempts and denominators are not reset or converted into passes. Herdr native states (`working/blocked/done/idle`) and `agent prompt/wait` provide runtime coordination, not proof of task acceptance or efficiency gains; any supplementary evaluation needs its own linked protocol and real results |
| TypeSafe JEV triage (`S0/S2/E3`) | `upgrade/jev-s0-20261025`, `upgrade/final-e3-causal-20261030` | **Discontinued (evaluation incomplete, no demonstrated gain)** — S0/S1 succeeded (`120/120` HTTP 200 on 2026-10-25; `R499` unlimited budget authorized 2026-10-31), 2026-10-26 shadow check showed `25/31` inbound items needed no model (`15` were `ccc` `FIXED_NUDGE`), and the 15-pair / 192-sample comparison finished `0/15` (`j2`/`j3` timed out, `j5` stopped; 2026-11-01 `3.09s` was a single sample) |

## Local Skill Entry Consolidation

- Active inward skill: `herdr-collaboration` (canonical `multi-agent-collaboration` release).
- Active outward skill: `cmux-collaboration` (canonical `multi-agent-collaboration` release).
- Legacy `herdr-inner-collaboration` local entry now forwards to `herdr-collaboration`; existing pinned task controllers remain untouched.

## Governance & Approval Record

- **User Archive Authorization (`2026-11-24T09:25:28Z` → `2026-11-24T09:31:46Z`)**:
  - Confirmation item #1 presented to user: *"1. 双仓收敛：是否同意将有效增量合入 `multi-agent-collaboration`（作为唯一生产主仓发布 `v0.5.0`），并将 `multi-agent-collaboration-upgrade` 转为实验归档仓，清理本机重复的 `herdr-inner-collaboration` 入口？"*
  - User reply: *"全部同意，开干。"*
  - Source: Antigravity conversation `a719d11f-93a4-4729-aef2-b247af11748e`, native transcript step 71 (proposal) and step 72 (`USER_EXPLICIT` reply); timestamps above are the original UTC `created_at` values. Authorization is recorded here; this document does not claim archival has executed.
- **Reversibility**: GitHub repository archival (`gh repo archive lzhs1995/multi-agent-collaboration-upgrade`) preserves all commits, branches, issues, and closed PR history read-only and can be reversed at any time via `gh repo unarchive`.
