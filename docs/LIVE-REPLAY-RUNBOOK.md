# Live replay runbook

This runbook is the final step after the offline planner and tests pass. It is
deliberately operational: it uses the installed `multi-agent-collaboration`
release and the existing Claude Code/Codex sessions. It does not create a
replacement session or call a provider API.

## 1. Bind an idle pair

Run these read-only checks from the supervisor surface:

```sh
cmux identify --json
cmux tree --all --json --id-format both
cmux-agent list
```

Choose only two existing terminal surfaces in the caller's current workspace:
one Claude executor and one Codex supervisor/executor. Verify that both are at
a safe idle boundary and are not compacting, reconnecting, waiting on a user
task, or running a tool. Resolve the full installed skill release and use its
`scripts/mac_harness.py`; do not use a stale checkout copy.

## 2. Materialize the graph

```sh
python3 scripts/orchestration_planner.py materialize \
  --plan /absolute/path/to/tests/fixtures/orchestration-plan.json \
  --output-root /absolute/path/to/replay-artifacts
```

The materialized seeds are planning inputs. For each node, create the normal
task-pack through the installed harness, merge the seed's context/scope/source
and verification fields, then run `finalize-pack`. A seed is never itself a
dispatchable pack.

## 3. Run the serial baseline

Use the current workspace UUID and the exact executor UUIDs in every command:

```sh
python3 /absolute/path/to/installed/scripts/mac_harness.py preflight \
  --task-id orchestration-replay-v1-serial \
  --supervisor surface:<supervisor> \
  --executor-surface surface:<claude> \
  --executor claude \
  --expected-workspace-uuid <workspace-uuid> \
  --expected-executor-uuid <claude-uuid> \
  --artifact-root /absolute/path/to/replay-artifacts/serial
```

Dispatch the seven nodes in plan order, waiting for the original task-pack
callback and closeout before dispatching the next node. Preserve each node's
native receipt, ACK, callback receipt, report SHA and acceptance record.

## 4. Run the scheduled graph

Repeat preflight with a new run id and a separate artifact root, keeping the
same existing session identities. Dispatch only the planner's ready waves:
waves 2 and 3 contain the independent Claude/Codex pair and may run together;
the one-writer and one-active-node-per-executor limits remain in force. Every
node still follows the normal task-pack, native delivery and callback path.

## 5. Promote only measured evidence

Assemble the two runs in the evidence schema documented by
`scripts/validate_replay_evidence.py`, including the expected wave number for
each scheduled node, then run:

```sh
python3 scripts/validate_replay_evidence.py \
  --plan /absolute/path/to/tests/fixtures/orchestration-plan.json \
  --evidence /absolute/path/to/measured-cli-replay.json
```

Only a `PASS` from this command permits the result to be called a measured
collaboration gain. A missing receipt, unknown delivery, replacement session,
third-party API call, incorrect wave or reduction below 15% keeps the result
unverified.
