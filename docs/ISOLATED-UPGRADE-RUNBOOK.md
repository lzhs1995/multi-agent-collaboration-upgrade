# Isolated native-client upgrade experiment

## Pressure and experience acceptance

Before freezing a repaired controller, run real multi-process filesystem
contention and the complete unchanged-source suite. The pressure matrix has
24 simultaneous callers, 100 rounds each of shared session, overlapping scope
and independent scopes. Require exactly one owner for each conflicting round
and all 24 owners for every independent round. Record semantic refusals and lock
busy/timeouts separately. Check retained original claims, rejection of UNCERTAIN
and foreign-owner release, and idempotent terminal release. Never label this
synthetic component test as a live model callback or a paired speed result.

The first matrix's safety PASS omitted independent admission: only 389/2400
succeeded. Preserve it and its correction. Registry transactions now explicitly
wait up to five seconds for the OS lock, then revalidate its original inode;
other action locks retain fail-fast semantics. This wait never retries dispatch.

Use fresh evidence directories for each validation. Stream each failure and
record source hashes before/after; if a complete suite times out, keep that
failure and diagnose the affected modules before rerunning the full suite.
Do not skip cases or change their internal timeouts to obtain a PASS.

Incorporate field lessons with their evidence class. A file inquiry, hook
exposure, complete-body reading, native receipt, callback and acceptance are
separate facts. Pending consultation does not stop independent work and does not
transfer shared runtime ownership. Current automatic workflow hooks are advisory;
historical closeout/reask state never creates a global session lock.

After verified source freeze, run the registered real Codex/dual-Claude pilot
three times on the same sessions, then all 12 warm and three cold pairs. Keep all
33 slots and failed denominators. Require correctness plus at least 15% median
warm improvement, at most 5% median degradation per workload, and disclose cost
growth above 10%. Until measured, improvement remains unproven.

This runbook operates the independent `multi-agent-collaboration-upgrade`.
Production MAC, global client configuration/hooks, existing sessions, CCC,
thesis-refiner and dissertation state stay outside the experiment's write scope.
Use only local macOS execution; never build on a VPS.

## Fixed experiment and evidence

`upgrade_benchmark.py` registers 33 slots before launch: three consecutive pilot
rounds with one original Codex supervisor and the same two original Claude
executors, followed by 12 warm pairs and 3 separately reported cold pairs.
The three workloads are lexical source indexing with an early successor, fan-in,
and overlapping write scopes. Each class/arm has a fixed original group.
The warm order is AB, BA, BA, AB. Cold order alternates by class. A1 uses native
supervisor scheduling (`mac_native`); B uses dependency advice/drafts/continuation
and the bounded `advance` entrypoint (`omo`). Both allow two independent Claude
lanes to run concurrently and use active callbacks and independent acceptance.
The B supervisor calls `advance --run RUN --max-steps 12 --max-seconds 45` with
the exact frozen runner/campaign arguments, then handles its native control and
review handoffs. It never duplicates actions already performed by advance.
Its action budget starts after the first complete fenced observation. Later
observations count against it; the next action checks it again. Record all four
returned timing fields: initialization, action, finalization and total elapsed
time. The original run/watchdog deadlines remain fixed. This budget is checked
between actions, not a timeout that cancels an in-flight preparation. Collect
the same original process after a yielded call.

New ACCEPT must run the independent checker exactly once. Historical accepted
state/status/recovery must run it zero times while revalidating original evidence
and source/input/output pins. Exercise changes to candidate and acceptance files
during the checker: no ACCEPTED event or lease release may be published. Native
authority reuse is confined to one pure verification; full exit validation,
session/journal fences, thread/task isolation and copied-context invalidation
are required. Do not cache an action gate or keep a scope over dispatch/append.

At most two Claude executors do business work at once. Only one global permit
is active. A later permit requires the previous original run's verified closure,
correct output and timing within 1800 seconds; B must have zero human prompts.
Failures, UNKNOWN, timeout and missing rounds stay in all 33 slots and 15 pairs.
The primary criterion is at least 15% median paired warm improvement, with no
class median regression over 5%. Disclose verified cost increases over 10%.
Unknown token boundaries or bills remain unknown; subscriptions are not per-run
cost evidence. The corpus's lexical workload does not guarantee uneven runtime.
Artificial sleeps to create an advantage are prohibited.

## Prepare and pin before launch

1. Run the focused tests for changed boundaries and one complete current-source
   suite with the approved local Python and `-B`. Preserve earlier failures.
   Historical test passes from another checkout do not validate current bytes.
2. Export a separate, source-only snapshot. Exclude `.git`, bytecode/cache and
   mutable handoff/evidence files. Freeze its complete tree manifest, the fixed
   research corpus manifest, source revision and test evidence. Use this same
   source/corpus snapshot for every group; never edit a campaign adapter in place.
3. Register a new, short private root using `upgrade_benchmark.py register
   --compact`. New v3 registrations use explicit `compact=true` to map the seven groups to `g/0` through `g/6`;
   semantic group names, all 33 slots, 15 pairs and their order are unchanged.
   Historical v1/v2 registrations remain readable and are never rewritten. Use each
   registration's exact `context_path` with `upgrade_experiment.py init` and then
   materialize that group's arm with `upgrade_live_materialize.py`.
   Registration also pins one response-observation policy for every group,
   disabled by default. For enabled observation pass `--observe-claude-responses`
   at registration and at all seven materializations from the same source.
   The enabled policy includes the complete observer SHA256 and byte count.

   The longest private `tmp/ARM/c.sock` path must fit in 103 bytes. Claude 2.1.170
   also requires the resolved `tmp/ARM/a/claude-UID` and `tmp/ARM/b/claude-UID`
   paths to fit in **44 UTF-8 bytes**; longer paths silently fall back to `/tmp`.
   For this account a dedicated root such as `/Users/lzhs/u3` fits. It must be new,
   real and privately owned. The compact registrar checks these limits before
   creating its root; materialization checks before creating any arm artifacts.
   It creates real owned `0700` directories and sets both `TMPDIR` and
   `CLAUDE_CODE_TMPDIR`. Symlinks and broad `/private/tmp` write permissions are
   not substitutes for these checks.

   Seven binary pins are required: `codex`, `claude`, `codex_wrapper`,
   `claude_wrapper`, `cmux`, `rtk`, **`node`** (CLI flag `--node`). The reviewed
   local Node is `/Users/lzhs/.nvm/versions/node/v24.16.0/bin/node`; the private
   cmux wrapper uses its absolute pinned path for the original settings merge.
   Keep model/provider settings consistent across groups. Copy only supported
   provider settings and required credentials into private roots. Keep real HOME
   and global environment intact.
4. Pin actual native user authorization records by transcript device/inode,
   exact byte offset/length/SHA and complete text SHA. An external generated
   startup instruction or a quoted old message is not native user authorization.
5. Create an immutable JSON driver request with fields:

   ```json
   {
     "schema": "upgrade-driver-request-v1",
     "registration": {"path": "ABSOLUTE", "sha256": "SHA", "bytes": 0},
     "runtimes": {"EVERY_REGISTERED_GROUP": {"path": "ABSOLUTE", "sha256": "SHA", "bytes": 0}},
     "source": {"COMPLETE_TREE_MANIFEST": "from tree_manifest(source_snapshot)"},
     "corpus": {"COMPLETE_TREE_MANIFEST": "from tree_manifest(fixed_corpus)"},
     "revision": "commit plus frozen source-tree evidence",
     "native_authorizations": [{"session": {}, "record": {}, "text_sha256": "SHA"}]
   }
   ```

   The displayed entries describe the schema; use actual full pins, not these
   placeholders. `orchestration_common.file_ref/tree_manifest` produce the pins.
   `upgrade_driver.py configure --request PATH --request-sha256 SHA` checks all
   runtimes and writes `REGISTRATION_ROOT/driver/config.json` before any permit.
   Every shell command uses `rtk proxy`, `login:false`, explicit workdir and the
   approved local Python. Do not invoke the inherited production installer.

## One original launch and automatic continuation

Run the pinned snapshot's `scripts/upgrade_driver.py run --config PATH
--config-sha256 SHA`. The optional `--poll-seconds` is bounded to 1–45 seconds,
default 10; these are idle file observations, not business delays. The driver
prints progress JSON. `tick` performs one progression step for diagnostics.

The driver issues the first permitted run BEFORE launching its group. A launch
has both an external intent and the original private harness intent. No return,
timeout or publisher crash grants a second launch. Adoption only inspects the
same original processes, native journals, automatic hooks, isolation and input
capability. It does not infer live loading from configuration files.

Once adopted, the separate v3 freezer validates the original authorization,
session/model records and new native freeze fences, copies the complete adapter
and verifies the corpus. The supervisor instructions are generated from that
exact campaign and published atomically through the startup bootstrap within
900 seconds. The original Codex startup turn is already waiting on this file;
the external driver never sends a follow-up prompt or wakes another session.

The supervisor processes its group's entire registered run list in the same
native session. Between runs it repeatedly performs the read-only, at-most-45-
second `wait-permit`. Other groups may execute between its warm rounds. Every
warm round reuses its original three clients; no reset, replacement or model
switching is an error-recovery option.

Only the real Codex supervisor executes the returned `start-script` and candidate
acceptance `exec_script` as an EXACT native `functions.exec` input, including
the final LF. Reconstructing or wrapping the script changes the native proof.
It personally executes local nodes. Claude nodes use claim → prepare → dispatch,
the original real Claude report and active callback, independent checker/review,
native acceptance, lease release and disarm. Preparation may take 600 seconds;
collect the same yielded process instead of killing or rerunning it. A1 cannot
use B-only draft/advice/continuation APIs, but can run both eligible Claude lanes.

## Failure, publisher recovery and closeout

The driver records every non-repeatable action under `driver/actions/KEY` with
an intent and, when observed, the original result. After a crash, an existing
launch intent permits observation/adoption only. A complete original freeze or
bootstrap can be recognized by its exact existing pins. An incomplete freeze
does not get another attempt. Missing results do not reset task or input budgets.
The persistent publication lock is never deleted or replaced. CLI `run` and
`tick` also hold `runner.lock` for their entire invocation, including idle waits,
so a second driver cannot alternate publication steps with the original process.

`upgrade_schedule.py halt --registration PATH --registration-sha256 SHA
--classification TIMEOUT|FAILED|UNKNOWN|STOPPED --reason TEXT` publishes one
immutable halt. It retains the active permit, client identities, original
attempts and write leases. A halt blocks new permits, new run creation and new
work, and ends permit waits. It does not block original-log inspection or a
separately verified original acceptance/failure settlement/disarm. Do not use
runtime `stop` as a substitute: that endpoint also stops input and can prevent
closeout requiring the live gate. Never release a lease merely because time ran
out, create a synthetic finish, or erase failure evidence to advance.

When a verified finish appears, the external driver measures and closes that
original run. It recomputes the previous measurement before the next permit.
At completion or halt it writes `driver/final-report.json` with the full fixed
denominator. This snapshot remains immutable; later original cleanup can be
reported separately with `upgrade_schedule.py report --output PATH` beneath
the registered `reports` directory. Configuration, native receipt, callback,
automatic hook execution, business acceptance and measured improvement are
separate findings. No report here installs or promotes the upgrade to production.

## Startup repair verification

Optional response observation for new private clients is documented in
[response observation validation](RESPONSE-OBSERVATION-VALIDATION.md). Enable
it through `--observe-claude-responses` at registration and materialization. Require its
immutable pin, real LOADED/source-hash evidence and bounded consumer terminal
records; do not retrofit old runtime files. Run its Node/Bun consumer tests and
`test_upgrade_response_startup` alongside the startup checks below. Bun preload
arguments use Bun escaping rather than shell quoting.
For a new paired campaign, freeze the same enabled/disabled setting and exact
observer SHA/byte count across all seven groups before any client launch. Record
that comparison in preparation evidence. Registration v3 enforces its common
policy at driver configure, permit issue, driver launch, campaign freeze and
private campaign validation. Reports additionally require the original pinned
registration/runtime/startup and the complete original external closure row.
Missing or changed evidence cannot qualify timings, costs or passing counts;
all 33 slots remain in the denominator. No repeated seven-tree scan is added to
native-reader paths. This verifies configuration, not actual LOADED; retain
that separate live check. Legacy v1/v2 arithmetic remains readable with
`qualification_scope=legacy_unverified`, without retrospective qualification.
The fixed-source local AB/BA test measured 3.57–7.46× controller elapsed time
with observation enabled at 256 concurrent requests. Keep the diagnostic opt-in;
do not describe its overhead as negligible or its correctness checks as a speed
pass. This local ratio does not predict provider latency or collaboration speed.
The cleanup follow-up's four old/new pairs had mixed timing direction, so do not
claim the repair eliminates this cost. See
[configuration and cleanup validation](OBSERVER-COMPARISON-VALIDATION.md).

Run `test_upgrade_client_isolation`, `test_upgrade_live_startup` and
`test_upgrade_benchmark` after changing startup code, followed by the complete
current-source suite. These tests exercise real local Seatbelt descendants,
private temporary files, the actual cmux settings merge with pinned Node, and
the materialized Python audit guard with a benign Unix-socket server. The server
test verifies read-only `/dev/null` stdin while write-capable opens still fail.
It launches no model client and proves neither automatic hooks nor adoption.

The original r2 attempt is HALTED/FAILED and cannot be replayed. Its two Claude
processes inherited the old environment and sandbox; new source files do not
repair those running processes. Preserve their session IDs, journals, launch
intents and model selection. Complete repair validation before proposing any
recovery requiring additional authorization. Never silently stop or replace them
or count a subsequent experiment as a successful r2 run.

## r3 diagnosis and opt-in physical socket candidate

r3 is also HALTED/FAILED: both original Claude sessions completed real readiness,
but Codex failed before adoption. Pilot remains 0/3. Its authorization applies to
that original attempt; preserve `/Users/lzhs/u3`, the frozen v3 export and every
original session. Never rerun its preparation or driver to apply a repair.

The startup repairs use bounded libproc process inventory instead of `/bin/ps`.
The fixed Codex binary (SHA256
`56fe2ab47d01b97375f2d5f213562d53144e77dd03dd75a19bdf344ec1261921`,
646156856 bytes) maps the advertised private `c.sock` to a physical socket under
`/private/tmp/codex-daemon-UID/SHA256(advertised_path_bytes)`, with a sibling
`.lock`. The advertised path is a symlink. HOME/TMPDIR/CODEX_HOME cannot relocate
that fixed native directory. r3 failed on the lock's `open` with EPERM.

The default remains `private-cli-roots-v1`. Explicit materialization with
`--native-policy private-cli-roots-exact-codex-socket-v2` prepares a candidate
whose startup and binding pin the same exact socket boundary. Preparation is
not authorization to start new clients or write the live physical endpoints.

- Verify the fixed binary and the existing shared parent's device/inode/UID/0700
  mode. Refuse any occupied advertised/socket/lock path before launch intent.
  Do not create, chmod, move or clean the shared parent or another daemon's file.
- Only the Codex lane's profile gains two literal write rules. Its native server
  inherits them. Claude A/B and control receive neither rule. Rebuild and compare
  all four profiles during startup validation, even when artifact hashes match.
- Validate the advertised alias separately: exact target, current owner and real
  socket. Keep generic `checked_path` symlink refusal intact. Require socket and
  regular lock to be owned, 0600 and single-linked; reject symlink/hardlink locks.
- Query live kernel permissions only after endpoints exist. Darwin
  `sandbox_check` denies absent literal paths even when create/bind would succeed;
  an absent-path query does not prove creation is denied. Disposable real-syscall
  tests cover creation and neighboring-path denials. Adoption checks all three
  clients plus the server; every send rechecks participating clients and endpoint
  integrity. Missing or changed evidence refuses input.
- Python's audit write boundary stays private. Native children also inherit
  Seatbelt; the socket exception does not authorize Python writes to the shared
  parent or bypass identity, native reception, callback or acceptance gates.

Focused validation includes `test_upgrade_socket_boundary`,
`test_upgrade_socket_startup`, `test_upgrade_live_startup`,
`test_upgrade_client_isolation`, `test_upgrade_live_inspect`,
`test_upgrade_process_inventory` and `test_upgrade_benchmark`, then the full suite.
These tests launch no model client and write no actual daemon endpoint. They test
full private materialization, real socket lifecycle, ten neighboring/descendant
syscall denials, exact alias handling, occupied-path refusal and rehashed broader
profile refusal. Source/log pins belong in a new evidence directory. A subsequent
live candidate must pin its exact paths and obtain authorization for this changed
write scope and its new clients before the first launch. A passing test suite is
not native startup, automatic adoption, pilot acceptance or measured efficiency.

## r4 startup result and wrapper/workspace repair

r4 was explicitly authorized and launched once. Its physical Codex socket was
created, crossing the r3 failure boundary. Both original Claude clients completed
native readiness; a separate read-only diagnostic correlated their original
readiness calls with automatic PreToolUse and PostToolUse observations. This
diagnostic did not publish adoption or prove every live isolation/input gate.
The original Codex CLI exited before establishing a session because both the
startup argv and cmux hook emitter supplied the non-repeatable
`--dangerously-bypass-hook-trust` flag. The inspector also rejected the actual
creation response `OK workspace:1000020047` by requiring a UUID substring.

r4 is HALTED/FAILED: one permit, zero of 33 closures, pilot zero of three, no
business dispatch and no adoption. Preserve `/Users/lzhs/u4`, its sessions,
socket/lock, original driver result and frozen v4 source. Its authorization is
already received; it does not authorize replacement or replay after failure.

The repaired private Codex wrapper deduplicates only that boolean flag at the
final injected/session argument join. It retains one startup flag even if the
hook emitter is empty or fails, and preserves option values, other repeated
options, ordering and prompt text after `--`. The global wrapper is unchanged.
Workspace inspection parses the complete response and uniquely resolves each
ref/UUID selector against the same fresh tree used for native surface lookup.
Ambiguous, conflicting, duplicate and substring-only responses fail closed;
PID/birth, workspace, provider, native root, session, kernel and hook gates remain.

Run `test_upgrade_client_isolation`, `test_upgrade_live_startup`,
`test_upgrade_live_inspect` and `test_upgrade_benchmark`, then one complete suite
over unchanged source. The full-wrapper test executes real private materialization
inside Seatbelt with inert external emitter/model fixtures. Inspector-flow tests
cover real ref resolution and rejection before readiness on moved surfaces or
changed PID/birth; native processes, kernel and RPC are fixtures there. Neither
test is a model run or adoption proof. Freeze the tested repair separately and
prepare any successor without launch. Present its exact new-client/socket scope
only after validation; never silently rerun or rewrite r4.

## r5 native registration result and self-query repair

r5 started one real Codex and two real Claude clients. Codex established its
native session and direct-input capability, with the client/server socket and
kernel boundaries verified. Its readiness call was still unobserved during
provider high-demand reconnection. Both Claude processes started, but their
native session registries omitted `procStart`: Claude's original
`ps -o lstart= -p PID` invocation received `posix_spawn EPERM` from Seatbelt.
Darwin's system `/bin/ps` is setuid. The unchanged native reader correctly
refused `NATIVE_REGISTRATION_PROCESS_MISMATCH` before adoption.

r5 is HALTED/FAILED: one permit, zero of 33 closures, pilot zero of three,
no adoption and no business dispatch. Preserve `/Users/lzhs/u5`, all original
clients, controllers, native registries, socket/lock and driver evidence. Do not
fill in `procStart`, relax the reader, edit frozen PATH or restart the run.

New candidates materialize an immutable `bin/ARM/claude-tools/ps` wrapper and
`mac_upgrade_claude_ps.py` in the pinned controller. Only new Claude PATHs gain
this directory; the account PATH and Codex PATH remain unchanged. This helper
supports only the observed self-query argument shape, checks the direct parent
PID and same-user kernel birth before and after reading, and prints the actual
local start time. Unsupported queries, changed/reparented processes and kernel
errors fail without a timestamp. It writes no registry, session or journal.
Claude remains responsible for native registration; the independent native
reader retains its complete process/root/session/birth checks. Startup validates
the wrapper's exact rendered bytes, path, 0555 mode and ordinary artifact pin.
No additional writable path or socket exception is introduced.

Run `test_upgrade_claude_ps`, `test_upgrade_client_isolation`,
`test_upgrade_live_startup`, `test_upgrade_live_inspect`,
`test_upgrade_process_inventory` and `test_upgrade_benchmark`, then the full
current-source suite. The materialized Node subprocess test reproduces the exact
native self-query inside both Claude Seatbelt profiles and compares it with
system ps outside Seatbelt while the original fixture PID is alive. It also
checks the original system-ps denial and rejects writes to the helper and an
outside sentinel. These are local fixtures, not model clients or adoption proof.
Provider readiness remains a separate live condition. Any subsequent candidate
must be frozen and reviewed before requesting its new-client authorization.

## r8 native provider failure and bounded closeout

r8 launched one Codex and two Claude clients and verified all three native
readiness records, automatic hooks and adoption. The frozen campaign and its
bootstrap were published, and the original Codex read the bootstrap. Its next
native `task_complete` record reported HTTP 400 `invalid_encrypted_content`.
Preserve that exact provider error; neither startup success nor this error
establishes the upstream cause. In particular, do not label it missing peers or
bad credentials without evidence.

The original frozen v7 controller closed r8 as HALTED/FAILED using the pinned
terminal record. It retained one permit, zero business dispatches, zero of 33
closures, pilot 0/3 and pairs 0/15. `/Users/lzhs/u8/driver/final-report.json`
reports INSUFFICIENT_EVIDENCE. Keep its clients, original schedule, configuration,
native journals and frozen controller. Do not restart prepare/configure/driver
or revise the original result with a later detector.

Newly validated source includes `upgrade_failure_watch.py`. After original
bootstrap verification, the driver reads only the original frozen Codex session
and authenticates its file identity, freeze fence and prior cursor record. Only
native terminal error events qualify; each tick consumes at most 256 records.
Unread records or a partial trailing record hold closeout/next-permit progression,
while the original 1800-second watchdog still applies. Native failure evidence
is written exclusively, reused unchanged after a crash and passed to the
original schedule halt. Already finished measurements survive in the report;
later errors stop progression without erasing them. No model input, session
replacement, provider-setting change or business replay belongs to this observer.

Validate with `test_upgrade_failure_watch` and `test_upgrade_driver`, covering
real-format terminal events, pre-freeze and quoted negatives, partial appends,
cursor budget, changed file/pins, crash recovery and full-denominator reporting.
Then run the complete unchanged-source suite. Keep live original-log readback,
offline regressions and component contention results distinct. Passing all
three does not replace the still-unfinished live pilot and paired experiment.

## r17 concurrent run-state coordination

The original r17 inventory dispatch wrote its intent before trying the shared
coordinator lock. Contention refused it before submit-task-pack began, leaving
an observe-only attempt. Preserve that failed run and its intent; a corrected
controller belongs to a fresh experiment.

For v3, serialize shared-state writers with bounded acquisition of the original
coordinator lock inode. Claims also own their node runner lock so duplicate
actions fail immediately. New claim, PREPARED and DISPATCH_INTENT acquisition
uses the remaining original work budget and stops on Stop, halt, closure or
unknown clock. Recheck full authority and fresh state after obtaining the lock.
Historical transitions, acceptance, repair and closeout use a separate fixed
300-second acquisition cap without imposing a new-work gate. This cap bounds
acquisition, not the transaction or total closeout duration. v2 and first run
creation remain fail-fast, as do runner/transport/campaign action locks; the
registry keeps its distinct five-second bound.

Nested lock order is runner → transport → coordinator → registry. Validation
can itself consume time: recheck the original work budget immediately before
writing a new claim and before invoking external submission.

Create a new dispatch-intent only while holding the shared lock after a fresh
PREPARED check. Existing intents are observe-only, including after Stop. Keep
the gap between the intent and event writes explicit: serialization does not
make them one crash-atomic write. Never hold this lock while calling external
harness or submit; those retain their own original-operation journals.

Startup overhead may be reduced by reusing the freshly created Coordinator for
observational status and one Codex NativeReader within a constructor. Fresh
header, state, metadata and both EOF-pin reads remain required. Public action
and input gates still authenticate independently. Both benchmark arms use the
same optimization, with no clock change or digest cache. Regression results and
lock pressure do not themselves establish live collaboration improvement.

## r16 native interruption before bootstrap consumption

r16 adopted its three original clients at 2026-10-18T09:26:07Z. Its original
Codex journal then recorded `event_msg/turn_aborted`, reason `interrupted`, at
09:28:13.207Z. The private runtime log independently recorded an `Interrupt`
submission and abort of that same turn. The interruption source is unknown.
The original bootstrap command completed successfully at 09:28:13.765Z, after
the turn had stopped. This proves neither provider failure nor business start.

The frozen v15 watcher recognized only `task_complete` errors and therefore
missed the native interruption. The original driver reached its unchanged
1800-second cap and reported HALTED/TIMEOUT at 09:50:20Z: pilot 0/3, pairs 0/15,
closures 0/33. Preserve this report; later diagnosis does not rewrite its
classification. A later external continuation turn does not reopen the halted
campaign or authorize replay of its original business input.

The watcher must recognize an authenticated post-freeze native interruption,
retain its exact first record, and halt without new input. Quoted examples,
pre-freeze events, command completion and unrelated message shapes do not
qualify. Keep the same bounded cursor, native file identity and crash-recovery
checks. The r17 experiment reuses the previously validated, unchanged v15
export in new private directories; a separate read-only observer records
interruption events while the product correction is validated. Never hot-load
the correction into that frozen experiment or treat these diagnostics as
completed pilot rounds.

## r10 UUID case and unstarted status

r10 adopted the original Codex and both Claude clients, but its unique native
start failed before business dispatch. The frozen v3 identity uses lowercase
UUIDs; cmux tree and workspace proof returned uppercase. All three original
processes, native sessions, TTYs, transcript paths/device/inodes and private
configuration roots still matched. Read-only kernel/tree diagnosis under the
original control sandbox verified that only the three UUID fields differed.
That diagnostic authenticates targets, not the original foreground caller.

Canonicalize only the v3 live workspace/surface/pane UUID fields using UUID
parsing before comparing against the frozen campaign. Invalid UUIDs still fail;
different UUIDs, sessions, process birth, executable, file identity, TTY,
transcript or private roots still fail. Keep v2 compatibility behavior unchanged.
Never revise the original campaign/controller or repeat its unique start.

`status --run RUN` must work before the first start and remain read-only. A
missing run directory is `NOT_STARTED`; an existing directory without a header
is `INCOMPLETE_START` and cannot be restarted. Symlinks, non-directory roots and
malformed headers are errors, not permission for a new start. Supervisor
instructions refer explicitly to top-level `state`, distinct from node `states`.

Validate the complete collection-to-comparison path for all three lanes and
check real Coordinator states for both v2 and v3. Freeze a separately tested
source for the next authorized experiment. Startup uses the original 900-second
window; after a run permit, its original 1800-second watchdog applies even if
business start failed. Never extend it or reinterpret waiting as progress.

The same v3 UUID boundary applies to native handshake/dispatch/callback receipts,
pack `executor_uuid`, identity-gate participants and frozen disarm ownership.
Keep raw receipt-to-attempt identities, input fences and content hashes exact.
The pinned workspace guard emits uppercase caller UUIDs, so journal lookup must
use that producer spelling before hashing; scaffold argv selectors remain exact.
Freeze disarm from the actual harness workspace and original marker supervisor,
then retain their spelling for marker paths and case-sensitive reservation keys.
Do not migrate old paths or rewrite evidence to make comparisons succeed.

`test_orchestration_uuid` exercises uppercase native evidence against lowercase
campaigns in both directions, concurrent Claude lanes, callback acceptance,
release and dependent follow-up through a complete five-node offline lifecycle.
It also rejects changed identities, altered fences, marker substitutions and
changed non-UUID bindings. These fixtures do not prove live client completion.
