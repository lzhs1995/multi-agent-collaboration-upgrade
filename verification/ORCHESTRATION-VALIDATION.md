# Orchestration validation

The shipped adapter has two separate validation layers:

1. **Offline contract validation** runs without a cmux transport or model call.
   The focused suite has 9 tests and the repository suite has 1280 tests, with
   zero failures and zero errors. It rejects dependency cycles, unknown
   executors, mismatched transports, replacement sessions, third-party API
   flags, and concurrent writes to one scope.
2. **Offline scheduling estimate** reduces the seven-node fixture from 790
   serial seconds to 600 scheduled seconds (24.051%). This is the result in
   `orchestration-offline-benchmark.json`; it is an upper-bound estimate based
   on explicit node durations, not a live model measurement.

## Live replay gate

The implementation is not labelled as a measured efficiency improvement until
the same fixture is replayed through existing, already handshaken Claude Code
and Codex CLI sessions. The replay must preserve each node's task id, native
receipt, ACK, callback, report hash and closeout evidence, then compare the
serial baseline with the graph schedule. It must not install a provider SDK,
call TypeSafe/JEV, launch a replacement session or modify the upstream
`multi-agent-collaboration` checkout.

At this checkpoint the available Claude executor in the active cmux workspace
is occupied by another user task. Reusing or restarting it would violate the
session-reuse and scope rules, so no live result is claimed. The next safe
action is to run the replay at an approved idle/session boundary and append
the native evidence and measured metrics here.

When that evidence exists, promotion is mechanical and fail-closed:

```sh
python3 scripts/validate_replay_evidence.py \
  --plan tests/fixtures/orchestration-plan.json \
  --evidence /absolute/path/to/measured-cli-replay.json
```

The validator requires both serial and scheduled runs and rejects third-party
API calls, replacement sessions, missing native receipts, missing callbacks,
incorrect task ids, overlapping serial work, and reductions below 15%.
