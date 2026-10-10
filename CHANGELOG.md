# 0.4.7 — 2026-10-09

- Emit verified PostToolUse results in the official
  `hookSpecificOutput` / `hookEventName: PostToolUse` / `additionalContext`
  envelope. Keep internal `action/results` out of the top-level client output.
- Validate success output against the Codex schema. Preserve silent unrelated
  calls and the existing failure behavior for unconfirmed delivery.
- Document configuration installation, automatic client invocation, exact native
  reception, callback settlement and task acceptance as separate checks.

# 0.4.6 — 2026-10-09

- Permit one synchronous original callback successor only when the sole prior
  attempt proves `NO_INPUT` with no events, receipt or pending legacy state, and
  the original task, report, attempt and controller pins still match.
- Keep the original controller's two-attempt limit and all live identity, draft
  and native-reception gates. Queued, uncertain or already received callbacks
  cannot use this exception; in-flight tasks are never migrated or replayed.
- Recognize the optional numeric rules-count field only outside matching Claude
  composer borders. Empty input and idle execution remain separate conditions.

# 0.4.5

- Accept exact-width Claude word wrapping and the observed clear-hint footer outside composer borders. Complete, stable, exact payload and native receiver evidence remain required.
- Preserve recovered original messages and settle genuine late ACKs against the original task/provider/nonce; the observation budget is not proof of executor failure.

# 0.4.4 — single-line fresh input and full native verification

- Preserve 0.4.3 nested native caller identity and current-status layout fixes.
- New ordinary/handshake messages containing CR/LF/tab or exceeding 700 UTF-8
  bytes use SHA-pinned single-line V2 references; old V1 attempts remain read-only.
- Formal packs use dedicated TASK_PACK_V2; callbacks retain exact dedicated text.
- Apply one shared single-line limit before every fresh paste.
- Update behavioral fixtures for task early rejection, literal heredocs, and
  historical multiline recovery without weakening live stable-draft/native gates.

## 0.4.3 — 2026-10-09

- Keep the hook's native caller collector through nested bridge reconciliation,
  retaining fresh process/tree checks instead of inheriting the daemon workspace.
- Recognize current Claude activity separately from quoted status in completed
  reports, and recognize Codex steer headers and the separate warnings footer.
- Add regression coverage for nested identity drift, real report layouts and
  active compaction/reconnection states without weakening native delivery proof.

## 0.4.0 — 2026-10-09

- Preserve literal escapes, tabs and newlines with guarded terminal.paste and
  submit_key=none; never fall back to the escape-decoding CLI send path.
- Confirm delivery only from a new, exact native user record after the original
  PASTE_INTENT EOF fence in the bound receiver process, session and transcript.
  Queued input, key success and a cleared composer remain unconfirmed.
- Use one guarded submission route with a stable complete draft, a shared bounded
  recovery budget and read-only reconciliation. Formal task/callback recovery
  remains read-only; the verified helper delegates ordinary messages to this route.
- Allow honest WAITING_SUPERVISOR closeout, task-bound diagnostics and authenticated
  supervisor report discovery. Idle requests and continuation waits are finite.
- Migrate only owned client hooks and preserve foreign configuration. Retire the
  obsolete screen-only and send-proof Stop guards; install the same full release
  for sender, reader, helper and hooks.

Migration: resolve the full installed release for new tasks. Preserve the original
controller, journal and evidence of in-flight tasks; never backfill a missing
pre-input binding or fence. Installation, live client loading, Claude-to-supervisor
reception and business acceptance require separate evidence.

## 0.1.1 — 2026-09-26

Check current agent input before sending, preserve user drafts and queued work, and distinguish unmarked submission from confirmed consumption. Preserve original sessions and late-delivery evidence.

# Changelog

## 0.1.0 - 2026-09-08

- Initial public extraction of the macOS/cmux collaboration harness and tests.
- Portable canonical skill path, current-model preservation and opt-in compose override.
- Session continuity, strict ACKs, task/report-bound callbacks, evidence and lease guards.
- Callback-first sentinel, bounded compact/error recovery and explicit solo handoff.
- Non-destructive dual-client installer, doctor and uninstall with regression tests.
- Shell payload recognition excludes file-edit content and covers both command/cmd fields.
- Active-marker schema remains version 1. No claim of new live agent consensus.

Versioning: SemVer for this distribution; breaking public schema changes require
a major version after 1.0. Before 1.0, breaking changes require a minor bump and
explicit migration notes. Published tags and release assets are never replaced.
## Unreleased — isolated orchestration adapter

- Add a validated CLI-owned dependency graph and bounded scheduler that keeps
  one writer per scope and routes work through existing Claude Code/Codex CLI
  sessions.
- Emit hash-bound task-pack seeds without bypassing identity-gate, native
  delivery, ACK, callback or closeout checks.
- Add deterministic offline benchmark/tests and document why TypeSafe/JEV stays
  behind a local decision seam under the no-third-party-API policy.
