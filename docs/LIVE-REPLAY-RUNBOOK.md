# Original-session v2 live replay

Use the original Codex supervisor and the designated context-bearing Claude
session. This procedure sends real task packs; perform it only for an authorized
local replay. Do not start another agent, switch models, install a provider SDK,
or modify shared runtime. Keep private transcripts and authorization outside Git.

Current checkpoint (2026-10-15): 1,514 offline tests pass. The older real v4
campaign remains blocked at its first Claude preparation, with its original
marker and lease intact. Its first historical diagnostic is `UNMATCHED`; a
second, after the V20 parser repair and over the same original record interval,
is `MATCHED_STATIC_ONLY`. This does not authorize settlement or retry.
The nine-run comparison and shared 0.4.35 adoption are unverified. The steps
below describe gated operation; they do not authorize bypassing that blocker.

## 1. Verify the existing pair and source

Run `rtk cmux-agent self` and inspect the already designated Claude surface by
its UUID with `rtk cmux-agent read <UUID> 90`. The authenticated native caller is
authoritative. Raw cmux identity can inherit a daemon's old workspace; focus and
surface titles are not identity evidence. Do not rewrite environment variables
to force a match. An idle `WAITING_DEPENDENCY` executor needs a finalized new task
pack, not a replacement session or a repetition of accepted research.

Resolve the installed skill to its complete immutable release and read its
supervisor/delivery protocol. Use that same controller for the campaign. Existing
attempts keep their old controllers and evidence. The upgrade checkout and the
shared installation have separate owners.

Run `rtk proxy /absolute/pinned/python -B /absolute/reviewed-source/scripts/test_all.py`
with the reviewed source directory as the working directory. Check the public
validation record for the exact tested scope; reuse an already completed,
hash-bound result only when the source, resolved interpreter bytes and required
checks have not changed. The recorded local checkpoint used Python 3.14.
The legacy installer is not a prerequisite for installing the private adapter; its empty-input doctor
can reach real active markers even with `--home`. Do not install those old hooks
into active clients or suppress their observed failure.

## 2. Freeze before measurement

Use a fresh source export of the reviewed revision, with no `.pyc`, `.pyo` or
`__pycache__` anywhere under its `scripts/`. Do not delete the working checkout's
cache to change a failing validation result. `-B` prevents new bytecode writes,
not the execution of existing bytecode. The freezer verifies the complete tree
and its required/deferred adapter dependencies before and after copying it.
Keep the export's revision and complete tree manifest with the private evidence.

Create an exclusive private freeze-request JSON with these fields:

| Field | Required value |
| --- | --- |
| `version`, `campaign_id` | `1` and a unique campaign identifier. |
| `root` | New absolute private campaign directory. |
| `source`, `revision` | Reviewed complete adapter source and its revision. |
| `corpus` | Original corpus containing `MANIFEST.jsonl`, `BLOB-VERIFICATION.json`, `fetched/`. |
| `controller` | Complete resolved immutable collaboration release. |
| `lease_root` | Separate registry shared by all nine runs. |
| `sessions` | `codex` and `claude`: provider, original session ID, workspace/pane/surface UUIDs, surface ref, native model. |
| `native_authorizations` | Original user records: authenticated transcript/session pin, exact record offset/length/hash, full text SHA256. |

Read the exact native records when constructing these pins; never use a screen
model label or manufacture a user approval. Preserve the full approved plan and
its implementation authorization. The freezer verifies current kernel/process
identity and the latest real native model before copying anything.

```sh
rtk proxy /absolute/pinned/python -B \
  /absolute/fresh-export/scripts/orchestration_freeze.py \
  --request /absolute/private/freeze-request.json \
  --request-sha256 <separately-recorded-request-sha256>
```

Use the freezer from the same reviewed export named by the request's `source`.
The freezer does not enforce its own location, so invoking a different checkout's
freezer would not establish that the reviewed code performed the freeze. Choose
the interpreter explicitly: freeze pins the resolved `sys.executable` path and
bytes. Subsequent actions must use the exact `python.path` stored in
`campaign.json`, with unchanged bytes.

Success is `FROZEN_NOT_MEASURED`, with `campaign.json` and
`freeze-complete.json`. Freeze performs no messaging or business execution.
Inputs, adapter bytes/modes/directories, controller, Python, policy probe,
metrics, run order, models and authorization are pinned. Preserve a failed
freeze's intent and failure record; do not rewrite it as a successful freeze.

## 3. Execute each run through its original native control

The following common prefix always uses the frozen adapter, never the mutable
checkout. Substitute the absolute paths and independently recorded campaign SHA:

```sh
rtk proxy /absolute/pinned/python -B \
  /absolute/private/campaign/adapter/scripts/orchestration_run.py \
  --campaign /absolute/private/campaign/campaign.json \
  --campaign-sha256 <campaign-sha256> start-script --run run-01-serial
```

Execute the returned **complete `exec_script` verbatim as a `functions.exec`
call**, including its single terminal LF. Do not wrap, regenerate, trim, or
repeat it. The native verifier compares the complete original control request.
For lost returns, use `status`/`recover`;
`resume-accept` and `resume-settle` consume the existing native control record.

For each ready node:

1. `claim --run <run> --node <node>` requests a new rules decision. Only `allow`
   persists the decision gates and claim, reserves the original session/scope,
   and writes the dependency-bound `work-request.json`. `deny` or `escalate`
   returns exit 2 with a `REJECTED` reason on stderr before those writes; preserve
   that output separately because the campaign does not record the rejected claim.
2. For a local Codex node, the Codex CLI executes the returned `business_command`
   exactly once. For a Claude node, use `prepare`, then `dispatch`; the pinned
   harness performs preflight/ACK and finalizes the task pack. Claude reads the
   required skill and pack, executes the scoped command, writes the report and
   actively sends the original completion callback.
3. Continue eligible independent local work while Claude is active. `serial`
   cannot overlap nodes; `omo` uses dependency readiness; `codeg` obeys its
   persistent session FIFO. Do not add sleeps or fabricate timing differences.
4. Use `candidate` after completed business work and, for Claude, its original
   callback. This runs the independent isolated checker and freezes the complete
   result. Read the resulting report and verification artifacts.
5. Execute the returned acceptance `exec_script` verbatim. After terminal
   settlement and verified lease release, `disarm` removes the Claude task's
   scoped marker through the original controller, then verifies removal. This
   action mutates state. After all nodes settle, use `finish --run <run>` and
   `verify --run <run>`.

Reception, ACK, callback settlement, acceptance, lease release and disarm are
separate facts. An uncertain operation allows original-log observation only;
use `reconcile` for the original dispatch, never another input. A failure retains
its lease until `failure-candidate` and the original native settlement prove a
safe boundary. Failed preparation without that proof remains blocked.

## 4. Maintain an older blocked campaign

Use a separately reviewed source-only adapter; do not replace any file in the
original campaign or reinterpret its old attempts through a newer controller.
The maintenance entrypoint has no sender or lease override. Its `freeze` action
does write private evidence and may only succeed after original preparation
proof passes. It is not a read-only diagnostic command.

```sh
rtk proxy /absolute/pinned/python -B \
  /absolute/reviewed-source/scripts/orchestration_maintenance.py freeze \
  --campaign /absolute/private/original-campaign/campaign.json \
  --campaign-sha256 <original-campaign-sha256> \
  --run <original-run-id> --node <original-node-id>
```

If successful, retain the returned `manifest` path and SHA256. Its adapter is
copied under the original node's separate `maintenance/adapter/` directory; the
original adapter is unchanged. All subsequent actions use that **new frozen
maintenance entrypoint** and the same `--manifest` / `--manifest-sha256`:

| Action | Effect and required boundary |
| --- | --- |
| `candidate` | Revalidates the original failure proof and lease, freezes the candidate and returns its exact native `exec_script`. |
| `settle` | Only the returned `exec_script` supplies the exact marker. Execute it once, verbatim including the terminal LF, through the original supervisor's `functions.exec`. |
| `resume` | Looks up that original native settlement; it is not a new input or replacement control. |
| `repair-releases` | Repairs a proven terminal release while verifying the original lease identity; it does not authorize a pending failure. |
| `disarm` | After valid settlement and lease release, removes the task's scoped marker through the original controller, then verifies removal under the node locks. This action mutates state; bounded recovery preserves the original attempt. |
| `verify` | Reports validated terminal failure, release and any disarm proof; it does not perform the missing closeout. |

For example, after a successful maintenance freeze:

```sh
rtk proxy /absolute/pinned/python -B \
  /absolute/private/original-node/maintenance/adapter/scripts/orchestration_maintenance.py candidate \
  --manifest /absolute/private/original-node/maintenance/manifest.json \
  --manifest-sha256 <maintenance-manifest-sha256>
```

`REJECTED` (exit 2) or `PENDING_OR_BLOCKED` (exit 3) does not prove zero input.
When a historical refusal diagnostic is attached, preserve its original record.
Even `MATCHED_STATIC_ONLY` means `settlement_authorized=false` and
`input_retry_authorized=false`. Historical startup environment and whole-process
writes remain `UNKNOWN`; invocation-time body/artifact identity remains
`NOT_PROVEN`. Do not execute historical command strings to investigate them,
delete a marker, release a lease manually or start a replacement campaign to
sidestep an unresolved original reservation.

## 5. Finish the complete comparison

Run the frozen order: `serial, omo, codeg, omo, codeg, serial, codeg, serial, omo`.
Then invoke `verify-campaign` with the same frozen prefix (no `--run`). Keep the
complete per-run results, correctness/contract rates, elapsed and first-action
timing, transport/business retries, provider automatic retries and interventions.

All nine runs must be valid and correct. Promotion requires at least 15%
improvement in a candidate's median elapsed time, interventions or retries.
Missing data is `null`; a zero baseline is ineligible for a percentage. Three
samples per strategy in reused sessions have carryover limitations. A valid
campaign that misses the improvement threshold is not a promoted candidate.

`validate_replay_evidence.py` is v1 historical-only and exits 3. Old fixtures,
offline mocks, controller dry-runs, installed settings and manual hook tests
cannot be substituted for this real campaign or active-client adoption evidence.
