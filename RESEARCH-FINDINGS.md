# Evidence review and integration decision

Snapshot date: 2026-10-10. All links below were read through `gh` CLI. The
source revisions are pinned so later upstream changes do not silently change
the conclusions.

## Recommendation

The most compatible source of ideas is **OmO's graph/task layer**, adapted as a
plan validator and scheduler around the existing cmux protocol. OmO's runtime
must not be embedded as a second transport: it owns its own team/session
runtime and would conflict with the current native receipt and callback
contract. The upgrade therefore implements only the graph boundary and routes
every node back through `mac_harness.py` and `cmux_bridge`.

codeg remains useful as a design reference for delegation state, resumable
session identity and per-request overrides. Its ACP/UI/workspace runtime is a
separate product and is not imported into this repository.

JEV is documented as an optional future decision provider. TypeSafe's own
coding-agent page says Jev is not a coding-agent LLM and that the skill helps an
agent write code using the TypeSafe API. A skill installation therefore does
not remove the third-party API call. Under this project's restriction, JEV is
not used in the online path; only deterministic rules and an offline mock are
shipped.

## Pinned evidence

| Project | Revision | Relevant evidence | Integration result |
|---|---|---|---|
| `multi-agent-collaboration` | `788c007ae1150b1d379766367c810665698c487d` | `SKILL.md`, `scripts/mac_harness.py`, `scripts/cmux_task_journal.py`, task-pack schema, native delivery and bounded closeout references | Keep as the transport and acceptance boundary. |
| OmO | `562d7ca7eec13bd3c88fc970fa057365f3b7b6b2` (`dev`, v5.1.29) | `packages/omo-opencode/src/features/team-mode/tools/tasks.ts` has explicit task states and blocked dependencies; live delivery reserves a message before sending; team mode has separate member sessions and cleanup. | Reuse graph/dependency concepts only; preserve cmux's stronger evidence-bound delivery. |
| codeg | `7c322eb1945a2159cd8445390062c74bfca5e88c` (`main`, v0.34.0) | `src-tauri/src/acp/delegation/types.rs` separates running/completed/failed/canceled/unknown and supports bounded per-request overrides; the app also persists parent/child session identity. | Borrow typed lifecycle and override ideas as future metadata; do not add ACP or a second workspace. |
| TypeSafe skills | `65a39f393687675ce170e6094757de20370365b9` | `skills/typesafe-ai/SKILL.md` and official docs state that Jev returns typed judgments, while the coding-agent page says it is not a replacement for Claude/Codex. | Keep a provider seam only; no online TypeSafe call under current policy. |

## Issue and PR lessons

- OmO PR #1726 (`feat: native agent-teams & optional MCB integration`) is a
  large, draft, conflict-prone feature. Its own triage describes a config-gated
  integration and separate team task/inbox/runtime layers. This supports using
  the small graph idea, not copying its runtime.
- OmO's team task code uses explicit `pending`, `claimed`, `in_progress`,
  `completed` and `deleted` states plus `blockedBy`; these map naturally to a
  graph scheduler but do not prove native delivery.
- codeg PR #879 adds per-request mode/config overrides while preserving default
  behavior and unifying fresh spawn/resume precedence. The upgrade records
  executor and session policy per node instead of adding a provider override.
- codeg issues/PRs repeatedly address ACP reconnects, stale history, bounded
  payloads and task requeue semantics. Those are useful failure categories, but
  they also show that an ACP workspace is another operational surface to
  maintain.
- TypeSafe skills issue #4 reports that non-interactive `npx skills add` can
  report success while writing nothing. Any future optional installation must
  verify files and registration; this upgrade does not install it.
- TypeSafe skills issues #5 and #11 propose confidence-gated command approval
  and Jev/LLM cooperation. They are community proposals, not evidence that JEV
  is available through Claude/Codex subscriptions. The adapter therefore
  treats them as design input only.

## What is implemented

`scripts/orchestration_planner.py` validates the API policy, executor transport,
session reuse, absolute source pins, dependency graph and write scopes. It
builds bounded waves, writes hash-bound task-pack seeds and calculates an
offline schedule estimate. It never starts an agent, calls a provider, or
dispatches terminal input.

The fixture in `tests/fixtures/orchestration-plan.json` is intentionally marked
`offline_schedule_estimate`. Its 790 seconds of serial work become 600 seconds
of bounded waves (24.051% schedule reduction). This is an architecture test,
not a measured model or live cmux result. A real gain claim requires a replay
with the existing Claude/Codex sessions and preserved native evidence.
