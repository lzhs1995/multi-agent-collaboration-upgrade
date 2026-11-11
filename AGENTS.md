# Agent entry

This repository provides readable collaboration rules and a deterministic offline planner. Reading the rules does not authorize live dispatch, installation or session changes.

1. Read [README.md](README.md) or [README.zh-TW.md](README.zh-TW.md), then [docs/agent-quickstart.md](docs/agent-quickstart.md).
2. Start with the offline `validate`, `schedule` and `benchmark` example. Record the command, exit status and output. `offline_schedule_estimate` is not measured agent performance.
3. This source tree includes experimental runtime through `baa2641`. Keep source integration, offline tests and actual native adoption separate. Formal business acceptance remains 0/33 and paired comparisons 0/15 at the documented checkpoint.
4. Native graph transports are limited to `claude_code_cli` and `codex_cli`. Gemini/OpenCode may read rules and run offline commands; their native integration is unverified. Never spoof another provider label.
5. For explicitly authorized live work, read the pinned controller's actual contract. [SKILL.md](SKILL.md) is inherited historical MAC material; do not automatically install it over existing MAC/thesis-refiner skills or borrow another session's identity.
6. Preserve sessions, original attempts, write ownership and review budgets. Distinguish notification, full-body reading, result acceptance and resource release. Unknown side effects require readback, not replay.
7. Make public examples portable. Keep credentials, private transcripts and machine/account-specific state outside public commits. Do not claim a secret scan or full-history sanitization that was not performed.
8. For chapter research teamwork, read the [nested skill layer guide](docs/nested-research-skills.md) and the applicable inner/outer skill. These candidate workflow sources reuse MAC transport and thesis-refiner domain rules; they do not install skills or activate a team.

Claude/Gemini wrappers point here. OpenCode and other clients can read this file explicitly; automatic loading varies by client/version. No wrapper establishes runtime compatibility.
