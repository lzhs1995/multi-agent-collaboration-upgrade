# Official task actor: candidate integration, not business acceptance

This change connects the successful official-TUI-first diagnostic to an explicit
new startup path. Existing controllers and completed j6/j7/d5 attempts retain
their original evidence. The original user goal remains incomplete: 0/33 runs,
0/15 paired comparisons.

## Implemented candidate boundaries

- New materialization requires the complete official package, held launch,
  exact socket policy and original Claude Goal/hook setup. It freezes the task
  plan and includes every new module in the private controller closure.
- A real empty official TUI initializes the server first. A separately verified
  external controller creates the task with `threadSource=user`, records the
  first turn and pins its journal before asking the owned launcher to resume
  that exact task. The classification is metadata, never caller authority.
- The retained TaskRPC preserves the original record for callbacks buffered
  during a request. TaskActor checks that record and the actual peer, invokes
  an external enrollment gate, then records intent before one fixed handler.
  Missing results become UNKNOWN; recovery never reexecutes or resends.
- The pre-adoption actor supports only the readiness operation. Its random
  receipt is verified through the operation ledger, native completion, exact
  code-mode outer call/output and final answer. Later input, another tool,
  duplicate calls or ambiguous provenance cannot supply the nonce. The native
  AgentMessage mirror must match the final message ID, thread, turn and content.
- Explicit operations-v2 supports append-only turn scopes and mutually exclusive
  choices using one global ledger. A registered new turn cannot reset consumed
  operations. v1 stays fixed to its original turn. A turn grant must reference
  the original authority, pinned RPC exchange and previous terminal turn; these
  file checks still require external live enrollment before use.
- Explicit startup-plan-v2 now materializes and bootstraps that same operations-v2
  ledger with the actual server pin. Only `ready` is available in the initial
  turn; future route choices stay frozen in the same ledger. The pre-adoption
  actor has its own operation allowlist and no business handlers, even if a
  future operation uses the same kind. Old startup-plan-v1 remains unchanged.
- The task inspector separately requires original automatic PreToolUse and
  PostToolUse observations for the exact inner dynamic call, original server,
  configuration and command. Dynamic completion and nonce consumption alone
  cannot satisfy this requirement. The hook name follows the pinned official
  source's namespace concatenation; live automatic execution remains unverified.
- The task server profile denies self-connections and physical socket unlink.
  Its inspector uses that exact unlink policy; legacy writers keep their policy.
- An explicit `task_model_network=True` materialization choice adds outbound
  network isolation to the TASK app-server and both Claude lanes. Ports come
  from the frozen Codex station and Claude settings; startup validation rebuilds
  the contract and exact profile bytes. Only TCP to each lane's own localhost
  model port is allowed (IPv4 and IPv6). New connections to other TCP ports,
  Unix sockets and UDP, including UDP on the permitted model port, are refused
  in actual Darwin parent/descendant probes. The trusted controller and remote
  TUI keep their original profiles; older task candidates opt out.
  This is not inherited-FD, Mach/Apple Events, allowed-service authentication
  or a complete tool boundary. Required cooperation still needs a restricted
  channel; no full runtime/adoption claim follows from the network probe.
- New TASK terminal launchers require stdin/stdout/stderr to identify the same
  real terminal, close every other inherited descriptor before startup work,
  and repeat closure immediately before native exec. Initial closure is recorded
  in the original lane launch record. Actual PTY tests cover accepted/listening
  sockets, ordinary files, a high descriptor and each lane's launcher-to-exec
  path. Redirected output is refused before descriptor mutation. The app-server
  still uses its separate `Popen(close_fds=True)` with explicit input/log FDs.
  This addresses launcher inheritance; it does not prove every native client's
  later helper/tool path or establish the entire live adoption boundary.
- Public official configuration removes shell, apps/plugins and nested-agent
  tool entrypoints for explicit TASK launches; thread creation sets an empty
  environment list and later turn grants reject environment/policy overrides.
  This is a reduction, not a complete tool whitelist or broker/FD boundary.
- A pre-effect invocation producer binds the exact outer code-mode call, inner
  received RPC and one ledger intent. Campaign readers accept that explicit
  descriptor, keeping the original native offset fences. UNKNOWN remains an
  invocation observation, never completion or permission to execute again.
- Explicit campaign controls map frozen IDs to the existing Runner's start,
  claim, candidate, accept, block, failure-candidate, settle, decision-publish and bind entrypoints. The
  handler checks the original invocation before calling Runner and does not
  bypass any live identity/adoption/transport gates. Offline start/claim has
  passed through the actual actor, ledger, Runner and Coordinator.
- Typed decision-publish and bind-result readers require RPC completion plus
  the matching native inner event and exact outer JSON output inside the
  original fence. Returned bytes do not prove semantic model consumption.
  The business adapter remains in the orchestration source closure; private
  startup support does not acquire Runner dependencies.
- Explicit startup-plan-v3 and operations-v3 preserve the original operation
  table and add finite generated-artifact slots. The original SQLite ledger
  appends one binding per slot and records the resolved operation and binding
  with its intent. Reopening or moving to a later turn never renews its budget.
- Campaign-controls-v2 connects candidate-to-accept through the original
  TaskActor, Runner, Coordinator and typed native readers. The producer must
  have a successful ledger completion, matching original inner/outer native
  result, pinned receipt and original candidate verified by the business checker.
  The actor and coordinator live gates still run before binding. The descriptor
  carries the exact resolved payload; a model still supplies only its registered
  operation ID. It cannot select a different marker or source.
- An inner completion that precedes its native outer return leaves the slot
  unbound. `TaskActor.bind_completed` can observe that same original completion
  later without another effect or response. The bound operation reader
  rechecks the producer's original native result within its saved fence.
- Failure-to-settle now uses that same producer/reader chain. A frozen block
  operation names its concrete reason; a failure producer names one of the
  original four boundary types. Each node has one blocker, producer and settle
  operation budget. The original Runner creates the candidate, and only its
  successful native inner/outer return can bind the exact failure marker.
  Binding checks the original blocker, output tree and boundary with the
  business checker; settlement still verifies the actual terminal boundary
  before releasing resources. Local offline failure settlement passes through
  the real actor and ledger. False delegated/preparation boundaries, modified
  source, STOP and unknown responses preserve the blocker without replay.
- Run-level status, read-only recovery, finish and verification now have typed
  controls. Repeated observations require separate predeclared observation IDs
  in the same finite ledger; that field is forbidden on mutating operations.
  Finish calls the original full-run verifier and retains its lease/disarm/
  input-accounting requirements. A blocked node still refuses closeout.
  Offline failure closeout and independent rereading of the original finish
  artifact pass; these do not prove live input attribution or callback release.
- `TaskService` now drives that same actor and retained RPC connection in bounded
  pulses. Idle polls preserve the connection and budgets. Late native results
  bind before a queued dependent operation; a normal terminal event waits for
  outstanding original results. Interrupts, conflicts and partial-frame failures
  close the actor without reconnecting, repeating effects or sending more input.
  The original pending completion and deferred terminal records remain on disk.
- S40 found that the first service implementation could close a healthy idle
  connection after a complete WebSocket Ping. The new polling adapter tracks
  frame state explicitly: complete Ping/Pong permits idle, while partial
  headers/payloads and unfinished fragmented JSON still fail closed. Ping gets
  only its protocol Pong; no model response or operation is replayed. The
  original frozen RPC implementation remains unchanged.
- A production campaign service now constructs its own `TaskCampaignGate`.
  It requires full original adoption, the same ledger and retained server peer,
  current task/turn, exact TUI and both original Claude endpoints. Only a scoped
  in-memory actor context can select the new Runner identity branch; ordinary
  JSON or environment values cannot substitute. Claim production and replay
  have an explicit task-RPC identity type and bind the original enrollment,
  ledger and invocation to the exact run/node. Existing foreground claims keep
  their original meaning. Deployment checks in local tests are explicit mocks;
  full TASK adoption is still missing, so the production entry remains refused.
- The campaign service factory connects the actual Runner, ledger, invocation
  recorder and completion binder. Its public live entry still requires real
  adoption; TASK adoption remains unavailable, so this is not a deployed service.
  The private startup closure does not import the campaign adapter or Runner.
- The result observer can explicitly pin the last complete native record while
  a writer appends a partial next line. The default reader remains strict. Tail
  scanning is bounded and starts with 4 KiB; an oversized partial tail refuses.
  Consumer order is checked against the producer's actual result record, rather
  than a later observation fence that may also contain the consumer's call.
- New TASK Codex hook observations have an explicit schema and a directory keyed
  by the exact startup pin, session and inner call. The producer and readiness
  reader share that key. Unrelated history no longer consumes the 4096-file
  bound; the bound still applies to the exact call. Directory selection grants
  no proof: original process ancestry, configuration, call arguments, session,
  startup, event order and nonce-source checks remain necessary. Existing
  non-TASK and Claude observations keep their original layout and schema.
- Offline candidate generation, deferred binding, model-shaped operation choice,
  acceptance and original ledger evidence now run through the actual components.
  This fixture does not prove a real model reviewed the candidate. Concurrent
  binding attempts, UNKNOWN, STOP, changed sources and mutually exclusive
  choices preserve the original budgets and refuse new effects.

## Deliberately unclaimed capabilities

The actual same-surface startup sequence has not been run for this candidate.
The production campaign factory owns its enrollment gate; standalone TaskActor
remains a component whose owner must supply one. Full TASK adoption is absent. The
campaign controls and the publish/bind result legs have typed evidence branches;
the advice-body producer/reader, transport, input attribution and callback legs
remain incomplete. Operations-v2 is selected by explicit v2 startup plans;
later turn enrollment still needs the live business gate. Broker/remote-exec/MCP/inherited-FD and both Claude
boundaries remain incomplete. No adoption or exclusive-responder claim follows
from these modules.

Accordingly the new task schema refuses formal `launch` and `hold`, its inspector
reports `ready=false`, and `startup_ready` is separate. Do not remove that refusal
until the complete business chain and deployment boundary are independently
verified. Never bypass it by invoking the underlying harness directly.

Operations-v1/v2 remain fixed. The new v3 binding path integrates
candidate-to-accept and failure-candidate-to-settle. Generated advice hashes have
a structural resolver definition but the campaign adapter explicitly rejects it
until its complete producer/reader chain is connected. Real enrollment, deployment
of the runtime service and actual model review remain unverified. Neither
copying the ledger nor changing its original registration is supported.
Binding artifact publication and SQLite insertion are separate operations. The
same closed actor can now reconcile a complete original artifact through its
live gate, original completion, source, native fence and business checker. It
appends the missing SQLite binding once, or observes an already committed one;
it never recreates an absent artifact, sends a response or reopens the actor.
Recovery after losing that in-memory owner and enrollment for a later turn
remain unimplemented. Creating a second service or recreating its exclusive
directories is not recovery.
Typed `advance` and supervisor instructions remain unconnected. Status and
read-only recovery have typed observations; finish and verify use the original
full-run checker. Process-owner recovery and broader transport are still
explicit pending work, not a complete campaign registration interface.

## Remaining execution order

1. Original S40 accepted bounded generated bindings, scoped hook discovery,
   service Ping correction round 1 and same-closed-actor recovery. ROOT verified
   the respective 188/179/187 artifact references. Original S40 has also returned
   bounded PASS for the explicit campaign identity increment; deployment tests
   remain mocks. Failure binding and typed closeout have also received bounded
   PASS with their original references checked. Review only new network changes,
   preserving earlier accepted scopes and recovery limits.
2. Add explicit business actor enrollment and the same typed provenance to both
   producers and readers listed in S40's original integration map. Preserve
   original Claude callback and exact-SHA reask consumers.
3. Connect append-only turn registration to that gate; prove task-wide budgets,
   STOP/expiry, target scope, cancellation and original-result-only recovery.
4. Close all client/broker/FD boundaries and run a new independently registered
   real startup, adoption and complete handshake/pack/callback/settlement unit.
5. Complete three-state, 60-second reask and takeover tests, three pilots plus
   fifteen pairs, then Herdr chapter teams, pure cmux, cross-workspace coordination
   and real Word/NotebookLM thesis artifacts with the original efficiency criteria.

Tests are evidence about these local components, not completed model runs.
