# CLI-owned graph orchestration

This upgrade adds `scripts/orchestration_planner.py`. It borrows the useful
part of OmO's graph idea while keeping the existing collaboration contract in
charge of identity, delivery, ACK, callback and closeout.

The planner does not start an agent and does not make a model or HTTP call. A
Claude Code CLI or Codex CLI session authors a JSON graph, then the planner
validates it and emits bounded ready waves. Each node is assigned to exactly
one existing CLI transport and carries a write scope. The scheduler never puts
two nodes with the same write scope in one wave and defaults to one active node
per executor, matching the existing one-writer/session-reuse rule.

## Flow

```text
Claude Code or Codex CLI writes graph JSON
             |
             v
orchestration_planner.py validate/schedule
             |
             v
task-pack-seeds/<node>.json
             |
             v
mac_harness.py preflight -> task-pack -> finalize-pack
             |
             v
cmux_bridge.submit_task_pack / submit_completion_callback
```

The seed is not a dispatchable task pack. It is an explicit handoff between
the graph scheduler and the existing harness. A live identity gate, source
revision, callback nonce and report path must still be supplied by the normal
task-pack finalization path.

## Commands

```sh
python3 scripts/orchestration_planner.py validate \
  --plan /absolute/path/to/orchestration-plan.json

python3 scripts/orchestration_planner.py schedule \
  --plan /absolute/path/to/orchestration-plan.json

python3 scripts/orchestration_planner.py benchmark \
  --plan /absolute/path/to/orchestration-plan.json

python3 scripts/orchestration_planner.py materialize \
  --plan /absolute/path/to/orchestration-plan.json \
  --output-root /absolute/path/to/replay-artifacts
```

The benchmark is labelled `offline_schedule_estimate` unless an external
measurement process supplies real CLI replay evidence. Its percentage is a
scheduling estimate, not a claim about model quality or API latency.

The exact live procedure is in [`LIVE-REPLAY-RUNBOOK.md`](LIVE-REPLAY-RUNBOOK.md).

## Measured-gain gate

After the same graph has completed once serially and once as scheduled, run the
separate evidence gate:

```sh
python3 scripts/validate_replay_evidence.py \
  --plan /absolute/path/to/orchestration-plan.json \
  --evidence /absolute/path/to/measured-cli-replay.json
```

The evidence must contain both runs, exact task ids, existing-session bindings,
native receipt, ACK, callback, report hash, closeout and acceptance proof for
every node. The validator computes wall time from the run timestamps and returns
`PASS` only when correctness passes and the configured 15% reduction target is
met. Offline benchmark output cannot be promoted to `measured_cli_replay` by
changing a label.

## JEV boundary

The plan contract has a `decision_provider` field. The shipped values are
`rules` and `offline_mock`; the graph rejects `api_policy.third_party_api=true`
and every node must use `claude_code_cli` or `codex_cli` as its transport. This
keeps a future decision adapter replaceable without putting a TypeSafe API call
inside the Python harness. The current implementation does not install the
TypeSafe SDK, read `TYPESAFE_API_KEY`, invoke JEV, or claim a JEV performance
gain.
