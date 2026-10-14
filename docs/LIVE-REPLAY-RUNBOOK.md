# Original-session v2 live replay

Use the original Codex supervisor and the designated context-bearing Claude
session. This procedure sends real task packs; perform it only for an authorized
local replay. Do not start another agent, switch models, install a provider SDK,
or modify shared runtime. Keep private transcripts and authorization outside Git.

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

Run `rtk proxy python3 -B scripts/test_all.py` from the reviewed checkout. Check
the public validation record for the exact tested scope. The legacy installer is
not a prerequisite for installing the private adapter; its empty-input doctor
can reach real active markers even with `--home`. Do not install those old hooks
into active clients or suppress their observed failure.

## 2. Freeze before measurement

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
rtk proxy python3 -B scripts/orchestration_freeze.py \
  --request /absolute/private/freeze-request.json \
  --request-sha256 <separately-recorded-request-sha256>
```

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

1. `claim --run <run> --node <node>` reserves its original session/scope and
   writes the dependency-bound `work-request.json`.
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
5. Execute the returned acceptance `exec_script` verbatim. For a terminal Claude
   task, `disarm` verifies exact marker removal. After all nodes settle, use
   `finish --run <run>` and `verify --run <run>`.

Reception, ACK, callback settlement, acceptance, lease release and disarm are
separate facts. An uncertain operation allows original-log observation only;
use `reconcile` for the original dispatch, never another input. A failure retains
its lease until `failure-candidate` and the original native settlement prove a
safe boundary. Failed preparation without that proof remains blocked.

## 4. Finish the complete comparison

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
