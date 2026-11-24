# Graduation and Archive Notice (2026-11-24)

`lzhs1995/multi-agent-collaboration-upgrade` has completed its mission as the experimental sandbox for task continuity, nested chapter handoffs, and JEV feasibility evaluation.

To eliminate dual-repository maintenance overhead and duplicate skill definitions (`herdr-inner-collaboration` vs `herdr-collaboration`), **all production-ready capabilities have been graduated into the single canonical repository [`lzhs1995/multi-agent-collaboration`](https://github.com/lzhs1995/multi-agent-collaboration)**.

## Graduation Ledger

| Experimental Capability | Source in `upgrade` | Disposition in Canonical `multi-agent-collaboration` |
|---|---|---|
| Offline chapter delta validator | PR #15 / PR #16 (`scripts/chapter_delta_validator.py`) | **Graduated** into `scripts/chapter_delta_validator.py` + `scripts/test_chapter_delta_validator.py` |
| Three-state continuity (`CODEX_DUAL_CLAUDE` / `CODEX_SINGLE_CLAUDE` / `CLAUDE_PEER`) | PR #19 (`feat/codex-claude-continuity`) | **Graduated** into `references/collaboration-contract-core.md` |
| Nested inward Herdr + outward cmux workflows & tab/pane binding checks | PR #14 / PR #17 (`skills/herdr-inner-collaboration`, `cmux-outer-coordination`) | **Unified** into canonical `skills/herdr-collaboration` & `skills/cmux-collaboration` + `scripts/herdr_roster.py` |
| Legacy pure-cmux 33-slot / 15-pair / 192-message scheduler campaign (R30–R34, `0/33`) | `upgrade/recovery-v2-20261014` | **Frozen / Archived** — superseded by Herdr 0.9.5 native server PTY states (`working/blocked/done/idle`) and `agent prompt/wait` |
| TypeSafe JEV triage (`S0/S2/E3`) | `upgrade/jev-s0-20261025`, `upgrade/final-e3-causal-20261030` | **Archived offline** — deterministic local checks (5ms, 100% exact) govern protocol identity, SHA, locks, and completion; no online JEV API dependency is adopted |

## Local Skill Entry Consolidation

- Active inward skill: `herdr-collaboration` (canonical `multi-agent-collaboration` release).
- Active outward skill: `cmux-collaboration` (canonical `multi-agent-collaboration` release).
- Legacy `herdr-inner-collaboration` local entry now forwards to `herdr-collaboration`; existing pinned task controllers remain untouched.
