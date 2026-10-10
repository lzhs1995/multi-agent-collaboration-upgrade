# Multi-Agent Collaboration

Evidence-bound collaboration for Codex and Claude Code in macOS cmux.
中文：复用完整上下文会话，明确分工，以真实报告和主动 callback 完成协作；
压缩故障及时干预，不以重开会话、假 ACK 或反复轮询代替恢复。

Current source: **0.4.7**. New messages use bounded single-line notices for pinned
bodies; formal packs and callbacks retain their dedicated task-bound routes.
Enter or Tab only requests submission. Reception requires the complete exact
payload in a new record of the bound receiver's native journal. See the
[delivery contract](references/verified-compose-delivery.md).

## Install

Requirements: Python 3.10+, macOS, cmux, and existing CLI agent sessions.
No provider credentials or agent subscriptions are included.

```sh
git clone https://github.com/lzhs1995/multi-agent-collaboration.git
cd multi-agent-collaboration
git checkout main
python3 scripts/manage_install.py install
python3 scripts/manage_install.py install --apply
python3 scripts/manage_install.py doctor
```

Install defaults to dry-run. It links this checkout into both clients' skill
directories and merges only this project's hooks. Existing foreign skill
directories are refused, never overwritten. Keep the checkout at its installed
path. Complete active work before installation or upgrading; restart/reload client
configuration only at a user-approved boundary, never replace an agent session.

```sh
python3 scripts/manage_install.py uninstall --apply
```

Uninstall removes only entries and links owned by this checkout. JSON backups
remain beside the original configs and may contain credentials: keep them private.
Other settings and hooks survive. Use `--home /absolute/sandbox` to test installation
without touching real client configuration.

## Use

Ask the supervisor to use `$multi-agent-collaboration`, then provide the objective
and designated existing executor. Both agents must read SKILL.md. The harness
binds their identity and task; scripts do not log in or start a replacement agent.

```sh
python3 scripts/mac_harness.py --help
python3 scripts/mac_harness.py preflight --task-id example-review \
  --artifact-root /absolute/path/to/example-review --executor-surface surface:7 --executor claude
```

Replace the example surface with a freshly verified binding. Do not paste task
prompts manually: use the finalized task-pack and callback entrypoints described
in [SKILL.md](SKILL.md). Public defaults preserve the current model and refuse
unknown compose text. Machine-specific preferences belong in local task settings.

## Verification And Boundaries

```sh
python3 scripts/test_all.py
```

Tests use isolated files and mocked agent surfaces, without sending live prompts.
Offline coverage is not certification of every cmux/client version. `doctor`
proves configured hooks can execute benign payloads; a real task still needs the
current-session handshake and current-task guards. Linux is an offline-test target,
not a live UI transport. There is no Windows/tmux or ACP adapter in this release.

See [lessons](references/lessons.md), [security](SECURITY.md), and
[version history](CHANGELOG.md). MIT; cmux, Codex and Claude remain separate products.

## Isolated orchestration upgrade

This public upgrade branch adds an experimental, CLI-owned graph adapter in
[`scripts/orchestration_planner.py`](scripts/orchestration_planner.py). Claude
Code or Codex can author a dependency graph; the adapter validates it, schedules
bounded ready waves, and emits task-pack seeds for the existing
`mac_harness.py`/`cmux_bridge` lifecycle. It never starts a replacement session
or calls a third-party model API. See [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md)
and [`RESEARCH-FINDINGS.md`](RESEARCH-FINDINGS.md) for the evidence review and
the limits of the offline benchmark.
